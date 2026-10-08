import base64
import os
import re
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from db import sb
from security import decrypt, is_safe_public_https_url

SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.modify",  # read, trash, send unsubscribe emails
]
HEADERS = ["From", "List-Unsubscribe", "List-Unsubscribe-Post"]
QUERY = "category:promotions OR category:updates OR category:forums OR unsubscribe"
BATCH_SIZE = 40  # Gmail allows ~250 quota units/sec/user; messages.get costs 5


def gmail_service(account: dict):
    creds = Credentials(
        token=None,
        refresh_token=decrypt(account["refresh_token_enc"]),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _set(account_id: str, **fields):
    sb.table("accounts").update(fields).eq("id", account_id).execute()


# ---------------------------------------------------------------- scan

def _run_batch(svc, ids):
    ok, failed = [], []

    def cb(request_id, response, exception):
        if exception is not None:
            failed.append(request_id)
        else:
            ok.append(response)

    batch = svc.new_batch_http_request(callback=cb)
    for mid in ids:
        batch.add(
            svc.users().messages().get(userId="me", id=mid, format="metadata", metadataHeaders=HEADERS),
            request_id=mid,
        )
    batch.execute()
    return ok, failed


def _fetch_metadata(svc, ids):
    ok, failed = _run_batch(svc, ids)
    if failed:  # one retry for rate-limited items
        time.sleep(2)
        more, _ = _run_batch(svc, failed)
        ok += more
    return ok


def _absorb(senders: dict, msg: dict):
    headers = {h["name"].lower(): h["value"] for h in msg.get("payload", {}).get("headers", [])}
    name, addr = parseaddr(headers.get("from", ""))
    addr = addr.strip().lower()
    if not addr:
        return
    s = senders.setdefault(
        addr,
        {
            "from_email": addr,
            "from_name": name or None,
            "msg_count": 0,
            "unread_count": 0,
            "last_received": 0,
            "list_unsubscribe": None,
            "one_click": False,
        },
    )
    s["msg_count"] += 1
    if "UNREAD" in msg.get("labelIds", []):
        s["unread_count"] += 1
    ts = int(msg.get("internalDate", 0))
    newest = ts >= s["last_received"]
    if newest:
        s["last_received"] = ts
        if name:
            s["from_name"] = name
    unsub = headers.get("list-unsubscribe")
    if unsub and (newest or not s["list_unsubscribe"]):
        s["list_unsubscribe"] = unsub
        s["one_click"] = "one-click" in headers.get("list-unsubscribe-post", "").lower()


def scan_account(account_id: str):
    max_messages = int(os.getenv("MAX_SCAN_MESSAGES", "5000"))
    try:
        account = sb.table("accounts").select("*").eq("id", account_id).single().execute().data
        _set(account_id, scan_status="scanning", scanned=0, total=0, scan_error=None)
        svc = gmail_service(account)

        ids, page = [], None
        while len(ids) < max_messages:
            res = svc.users().messages().list(
                userId="me", q=QUERY, maxResults=500, pageToken=page
            ).execute()
            ids += [m["id"] for m in res.get("messages", [])]
            page = res.get("nextPageToken")
            if not page:
                break
        ids = ids[:max_messages]
        _set(account_id, total=len(ids))

        senders: dict = {}
        for i in range(0, len(ids), BATCH_SIZE):
            for msg in _fetch_metadata(svc, ids[i : i + BATCH_SIZE]):
                _absorb(senders, msg)
            _set(account_id, scanned=min(i + BATCH_SIZE, len(ids)))
            time.sleep(0.3)

        rows = []
        for s in senders.values():
            row = dict(s, account_id=account_id)
            row["last_received"] = (
                datetime.fromtimestamp(s["last_received"] / 1000, tz=timezone.utc).isoformat()
                if s["last_received"]
                else None
            )
            rows.append(row)
        # `status` is not in the payload, so a re-scan never resets unsubscribed/kept senders.
        for i in range(0, len(rows), 500):
            sb.table("senders").upsert(rows[i : i + 500], on_conflict="account_id,from_email").execute()

        _set(
            account_id,
            scan_status="done",
            scanned=len(ids),
            last_scanned_at=datetime.now(timezone.utc).isoformat(),
        )
    except Exception as e:  # noqa: BLE001
        _set(account_id, scan_status="error", scan_error=str(e)[:300])


# ---------------------------------------------------------------- unsubscribe

def _send_mailto(svc, mailto: str) -> bool:
    parsed = urlparse(mailto)
    to = unquote(parsed.path)
    params = parse_qs(parsed.query)
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = unquote(params.get("subject", ["unsubscribe"])[0])
    msg.set_content(unquote(params.get("body", ["unsubscribe"])[0]))
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    svc.users().messages().send(userId="me", body={"raw": raw}).execute()
    return True


def unsubscribe(account: dict, sender: dict) -> dict:
    raw = sender.get("list_unsubscribe") or ""
    urls = re.findall(r"<([^>]+)>", raw)
    https = next((u for u in urls if u.lower().startswith("https://")), None)
    mailto = next((u for u in urls if u.lower().startswith("mailto:")), None)

    # 1) RFC 8058 one-click: a silent POST, no page for the user to click through.
    if https and sender.get("one_click") and is_safe_public_https_url(https):
        try:
            r = httpx.post(
                https,
                data={"List-Unsubscribe": "One-Click"},
                timeout=15,
                follow_redirects=False,
            )
            if r.status_code < 400:
                return {"result": "ok", "method": "one-click"}
        except httpx.HTTPError:
            pass

    # 2) mailto: sent from the user's own account.
    if mailto:
        try:
            _send_mailto(gmail_service(account), mailto)
            return {"result": "ok", "method": "email"}
        except Exception:  # noqa: BLE001
            pass

    # 3) Plain link: never fetched by the server, the user opens it themselves.
    if https:
        return {"result": "manual", "url": https}
    return {"result": "none"}


# ---------------------------------------------------------------- delete

def trash_sender_mail(account_id: str, sender_id: str):
    try:
        account = sb.table("accounts").select("*").eq("id", account_id).single().execute().data
        sender = sb.table("senders").select("*").eq("id", sender_id).single().execute().data
        svc = gmail_service(account)
        addr = sender["from_email"].replace('"', "")
        page = None
        while True:
            res = svc.users().messages().list(
                userId="me", q=f'from:"{addr}"', maxResults=500, pageToken=page
            ).execute()
            ids = [m["id"] for m in res.get("messages", [])]
            if ids:
                svc.users().messages().batchModify(
                    userId="me", body={"ids": ids, "addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]}
                ).execute()
            page = res.get("nextPageToken")
            if not page:
                break
        sb.table("senders").update({"status": "deleted", "msg_count": 0, "unread_count": 0}).eq(
            "id", sender_id
        ).execute()
    except Exception:  # noqa: BLE001
        sb.table("senders").update({"status": "active"}).eq("id", sender_id).execute()
