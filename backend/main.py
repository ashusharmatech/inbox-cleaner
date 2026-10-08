import os
from urllib.parse import urlencode

import httpx
import jwt
from dotenv import load_dotenv

load_dotenv()

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import RedirectResponse  # noqa: E402

from db import sb  # noqa: E402
from gmail_ops import SCOPES, scan_account, trash_sender_mail, unsubscribe  # noqa: E402
from security import (  # noqa: E402
    check_oauth_state,
    decrypt,
    encrypt,
    make_oauth_state,
    make_session_token,
    read_session_token,
)

FRONTEND_URL = os.environ["FRONTEND_URL"].rstrip("/")

app = FastAPI(title="Inbox Cleaner API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_URL],
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type"],
)


# ---------------------------------------------------------------- auth

def current_account(authorization: str | None = Header(default=None)) -> dict:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing token")
    try:
        account_id = read_session_token(authorization[7:])
    except jwt.PyJWTError:
        raise HTTPException(401, "Session expired, sign in again")
    res = sb.table("accounts").select("*").eq("id", account_id).limit(1).execute()
    if not res.data:
        raise HTTPException(401, "Account not found")
    return res.data[0]


@app.get("/health")
def health():
    return {"ok": True}


@app.get("/auth/google/start")
def google_start():
    params = {
        "client_id": os.environ["GOOGLE_CLIENT_ID"],
        "redirect_uri": os.environ["GOOGLE_REDIRECT_URI"],
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",  # get a refresh token
        "prompt": "consent",       # ...every time
        "state": make_oauth_state(),
    }
    return RedirectResponse("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(params))


@app.get("/auth/google/callback")
def google_callback(background: BackgroundTasks, code: str | None = None, state: str | None = None, error: str | None = None):
    if error or not code or not state or not check_oauth_state(state):
        return RedirectResponse(f"{FRONTEND_URL}/#error=auth_failed")

    tok = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": os.environ["GOOGLE_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_CLIENT_SECRET"],
            "redirect_uri": os.environ["GOOGLE_REDIRECT_URI"],
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    if tok.status_code != 200:
        return RedirectResponse(f"{FRONTEND_URL}/#error=token_exchange")
    tokens = tok.json()

    # The user must tick the Gmail checkbox on the consent screen.
    if "gmail.modify" not in tokens.get("scope", ""):
        return RedirectResponse(f"{FRONTEND_URL}/#error=gmail_permission")

    info = httpx.get(
        "https://openidconnect.googleapis.com/v1/userinfo",
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
        timeout=15,
    ).json()
    email = info["email"].lower()

    existing = sb.table("accounts").select("id").eq("email", email).limit(1).execute().data
    refresh = tokens.get("refresh_token")
    if existing:
        account_id = existing[0]["id"]
        if refresh:
            sb.table("accounts").update({"refresh_token_enc": encrypt(refresh)}).eq("id", account_id).execute()
    else:
        if not refresh:
            return RedirectResponse(f"{FRONTEND_URL}/#error=no_refresh_token")
        account_id = (
            sb.table("accounts")
            .insert({"email": email, "refresh_token_enc": encrypt(refresh), "scan_status": "scanning"})
            .execute()
            .data[0]["id"]
        )
        background.add_task(scan_account, account_id)  # first sign-in: scan straight away

    return RedirectResponse(f"{FRONTEND_URL}/#token={make_session_token(account_id)}")


# ---------------------------------------------------------------- API

@app.get("/me")
def me(account: dict = Depends(current_account)):
    return {k: account[k] for k in ("email", "scan_status", "scanned", "total", "scan_error", "last_scanned_at")}


@app.post("/scan")
def scan(background: BackgroundTasks, account: dict = Depends(current_account)):
    if account["scan_status"] == "scanning":
        return {"started": False}
    sb.table("accounts").update({"scan_status": "scanning", "scanned": 0, "total": 0}).eq("id", account["id"]).execute()
    background.add_task(scan_account, account["id"])
    return {"started": True}


@app.get("/senders")
def senders(account: dict = Depends(current_account)):
    rows = (
        sb.table("senders")
        .select("id,from_email,from_name,msg_count,unread_count,last_received,status,one_click,list_unsubscribe")
        .eq("account_id", account["id"])
        .order("msg_count", desc=True)
        .limit(3000)
        .execute()
        .data
    )
    for r in rows:  # the raw header stays on the server; the UI only needs to know if a method exists
        r["can_unsubscribe"] = bool(r.pop("list_unsubscribe"))
    return rows


def _own_sender(account: dict, sender_id: str) -> dict:
    res = sb.table("senders").select("*").eq("id", sender_id).eq("account_id", account["id"]).limit(1).execute().data
    if not res:
        raise HTTPException(404, "Sender not found")
    return res[0]


@app.post("/senders/{sender_id}/unsubscribe")
def unsubscribe_sender(sender_id: str, account: dict = Depends(current_account)):
    sender = _own_sender(account, sender_id)
    out = unsubscribe(account, sender)
    if out["result"] == "ok":
        sb.table("senders").update({"status": "unsubscribed"}).eq("id", sender_id).execute()
    return out


@app.post("/senders/{sender_id}/delete")
def delete_sender_mail(sender_id: str, background: BackgroundTasks, account: dict = Depends(current_account)):
    _own_sender(account, sender_id)
    sb.table("senders").update({"status": "deleting"}).eq("id", sender_id).execute()
    background.add_task(trash_sender_mail, account["id"], sender_id)
    return {"started": True}


@app.post("/senders/{sender_id}/keep")
def keep_sender(sender_id: str, account: dict = Depends(current_account)):
    _own_sender(account, sender_id)
    sb.table("senders").update({"status": "kept"}).eq("id", sender_id).execute()
    return {"ok": True}


@app.delete("/account")
def delete_account(account: dict = Depends(current_account)):
    """Revoke Google access and erase everything we store about the user."""
    try:
        httpx.post("https://oauth2.googleapis.com/revoke", data={"token": decrypt(account["refresh_token_enc"])}, timeout=10)
    except Exception:  # noqa: BLE001
        pass
    sb.table("accounts").delete().eq("id", account["id"]).execute()  # senders cascade
    return {"ok": True}
