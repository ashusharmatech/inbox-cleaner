# Inbox Cleaner

Connect Gmail → see every sender ranked by volume → unsubscribe or trash their mail in one click.

**Stack:** FastAPI (Render) · Supabase (Postgres) · React + Vite (Vercel)
**Privacy:** metadata headers only (`From`, `List-Unsubscribe*`), never bodies. Refresh tokens are Fernet-encrypted. Deleted mail goes to Gmail Trash. "Disconnect and erase" revokes the token and wipes your rows.

---

## 1. Supabase
1. Create a project.
2. SQL Editor → paste `supabase/schema.sql` → Run.
3. Project Settings → API → copy **Project URL** and the **service_role** key (backend only, never in the frontend).

## 2. Google Cloud
1. Create a project → **APIs & Services → Library → enable "Gmail API"**.
2. **OAuth consent screen** → External → fill app name/email → add scope `https://www.googleapis.com/auth/gmail.modify` → **Add your own Gmail as a Test user**. Leave it in *Testing*.
3. **Credentials → Create credentials → OAuth client ID → Web application.**
   Authorized redirect URIs (add both):
   - `http://localhost:8000/auth/google/callback`
   - `https://YOUR-API.onrender.com/auth/google/callback` (add after step 4)
4. Copy the Client ID and Client secret.

> Testing mode limits: up to 100 test users, an "unverified app" warning (click Advanced → Continue), and refresh tokens that expire after 7 days (just sign in again). Opening it to the public needs Google's OAuth verification plus a CASA security assessment because `gmail.modify` is a restricted scope.

## 3. Run locally
```bash
# backend
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # fill it in; generate the two secrets with the commands inside
uvicorn main:app --reload --port 8000

# frontend (new terminal)
cd frontend
cp .env.example .env
npm install && npm run dev      # http://localhost:5173
```

## 4. Deploy (this gives you the public URL)
**Backend → Render**
1. Push this folder to GitHub.
2. Render → New → Blueprint → pick the repo (it reads `render.yaml`).
3. Fill the env vars. `GOOGLE_REDIRECT_URI` = `https://YOUR-API.onrender.com/auth/google/callback`. Leave `FRONTEND_URL` for step 5.

**Frontend → Vercel**
1. Import the repo, set **Root Directory = `frontend`**.
2. Env var `VITE_API_URL` = `https://YOUR-API.onrender.com`. Deploy.
3. Your app URL is the Vercel domain.

**Wire them together**
5. Render → set `FRONTEND_URL` = your Vercel URL (no trailing slash) → redeploy.
6. Google Cloud → add `https://YOUR-API.onrender.com/auth/google/callback` to the OAuth client's redirect URIs.

Open the Vercel URL → **Connect Gmail**.

## How it works
- `POST /scan` lists up to `MAX_SCAN_MESSAGES` (default 5000) promo/update/"unsubscribe" messages, fetches headers in batches, and groups by sender.
- **Unsubscribe order:** RFC 8058 one-click POST → `mailto:` email sent from your account → otherwise the UI shows the link for you to open. The server only POSTs to public HTTPS hosts (SSRF-guarded) and never fetches plain links.
- **Delete** moves every message from that sender to Trash (undo from Gmail for 30 days).
- Re-scans never reset a sender you've already marked unsubscribed or kept.

## Files
```
backend/main.py        routes + Google OAuth
backend/gmail_ops.py   scan, unsubscribe, trash
backend/security.py    encryption, JWT, SSRF guard
supabase/schema.sql
frontend/src/App.jsx   landing + dashboard
render.yaml
```
