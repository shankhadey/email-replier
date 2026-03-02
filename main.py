"""
FastAPI backend for Gmail Replier — multi-user.
"""

import logging
import os
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from jose import JWTError, jwt
from pydantic import BaseModel, Field

import auth
import database as db
import scheduler
from gdrive_client import search_and_attach
from gmail_client import create_reply_draft, send_reply

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ── JWT config ─────────────────────────────────────────────────────────────────
JWT_SECRET = os.environ.get("JWT_SECRET_KEY", "dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_DAYS = 30
COOKIE_NAME = "session"

# ── In-flight OAuth flows: state -> (Flow, created_at unix ts) ─────────────────
_oauth_flows: dict[str, tuple] = {}


def _cleanup_stale_flows():
    cutoff = time.time() - 600  # 10 minutes
    stale = [s for s, (_, ts) in list(_oauth_flows.items()) if ts < cutoff]
    for s in stale:
        del _oauth_flows[s]


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    scheduler.init_scheduler()
    for uid in db.get_all_users_with_tokens():
        try:
            scheduler.add_user_job(uid)
        except Exception as e:
            logger.warning(f"Could not re-add job for {uid}: {e}")
    yield
    scheduler.shutdown_scheduler()


app = FastAPI(title="Gmail Replier", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
def index():
    return FileResponse("frontend/index.html")


# ── JWT helpers ────────────────────────────────────────────────────────────────

def create_session_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRE_DAYS)
    return jwt.encode(
        {"sub": user_id, "exp": expire},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )


def get_current_user(request: Request) -> str:
    """FastAPI dependency — returns user_id or raises 401."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        user_id: str = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_id
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")


# ── Auth endpoints ─────────────────────────────────────────────────────────────

@app.get("/auth/login")
@app.get("/auth")   # backward-compat alias
def start_auth():
    """Redirect to Google OAuth consent screen."""
    _cleanup_stale_flows()
    state = secrets.token_urlsafe(32)
    flow = auth.create_oauth_flow()
    _oauth_flows[state] = (flow, time.time())
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        state=state,
    )
    return RedirectResponse(auth_url)


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = None, error: str = None, state: str = None):
    """Google redirects here after the user authorises."""
    import asyncio

    if error:
        return HTMLResponse(f"<h2>Authorization failed: {error}</h2>", status_code=400)
    if not code:
        return HTMLResponse("<h2>No authorization code received.</h2>", status_code=400)

    entry = _oauth_flows.pop(state, None) if state else None
    if entry is None:
        return HTMLResponse(
            "<h2>OAuth state mismatch or expired. Please <a href='/auth/login'>try again</a>.</h2>",
            status_code=400,
        )
    flow, _ = entry

    try:
        flow.fetch_token(code=code)
    except Exception as e:
        logger.error(f"Token fetch error: {e}")
        return HTMLResponse(f"<h2>Token exchange failed: {e}</h2>", status_code=500)

    # Decode id_token to get user info — no extra HTTP call needed
    try:
        from google.auth.transport.requests import Request as GoogleRequest
        from google.oauth2 import id_token as google_id_token
        id_info = google_id_token.verify_oauth2_token(
            flow.credentials.id_token,
            GoogleRequest(),
            os.environ["GOOGLE_CLIENT_ID"],
        )
        user_id = id_info["sub"]
        email = id_info["email"]
        display_name = id_info.get("name", "")
    except Exception as e:
        logger.error(f"id_token decode error: {e}")
        return HTMLResponse(f"<h2>Could not decode user info: {e}</h2>", status_code=500)

    is_new = db.get_user(user_id) is None
    db.upsert_user(user_id, email, display_name)
    db.save_token(user_id, auth.credentials_to_dict(flow.credentials))
    if is_new:
        db.set_service_start_epoch(user_id, int(time.time()))

    scheduler.add_user_job(user_id)

    if is_new:
        try:
            import background_setup
            asyncio.create_task(background_setup.run_setup(user_id))
        except Exception as e:
            logger.warning(f"Could not start background setup for {user_id}: {e}")

    redirect = RedirectResponse(url="/", status_code=302)
    redirect.set_cookie(
        COOKIE_NAME,
        create_session_token(user_id),
        httponly=True,
        samesite="lax",
        secure=os.environ.get("APP_BASE_URL", "").startswith("https"),
        max_age=60 * 60 * 24 * JWT_EXPIRE_DAYS,
    )
    return redirect


@app.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@app.get("/auth/status")
def auth_status(request: Request):
    """Backward-compat: check if the current request is authenticated."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return {"authorized": False}
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return {"authorized": bool(payload.get("sub"))}
    except JWTError:
        return {"authorized": False}


# ── User info ──────────────────────────────────────────────────────────────────

@app.get("/api/me")
def get_me(user_id: str = Depends(get_current_user)):
    user = db.get_user(user_id)
    if not user:
        raise HTTPException(404, "User not found")
    return user


@app.get("/api/contacts")
def get_contacts(user_id: str = Depends(get_current_user)):
    return db.get_contacts(user_id)


class ContactCreate(BaseModel):
    email: str
    name: Optional[str] = None
    relationship_type: Optional[str] = None
    formality_level: Optional[str] = None


class ContactUpdate(BaseModel):
    name: Optional[str] = None
    relationship_type: Optional[str] = None
    formality_level: Optional[str] = None


@app.post("/api/contacts")
def add_contact(payload: ContactCreate, user_id: str = Depends(get_current_user)):
    db.upsert_contact(
        user_id,
        email=payload.email,
        name=payload.name,
        relationship_type=payload.relationship_type,
        formality_level=payload.formality_level,
    )
    return {"ok": True}


@app.put("/api/contacts/{contact_email:path}")
def update_contact(contact_email: str, payload: ContactUpdate, user_id: str = Depends(get_current_user)):
    db.update_contact_details(user_id, contact_email, payload.name, payload.relationship_type, payload.formality_level)
    return {"ok": True}


@app.delete("/api/contacts/{contact_email:path}")
def delete_contact_endpoint(contact_email: str, user_id: str = Depends(get_current_user)):
    db.delete_contact(user_id, contact_email)
    return {"ok": True}


@app.get("/api/profile")
def get_profile(user_id: str = Depends(get_current_user)):
    return db.load_user_params(user_id)


class ProfileUpdate(BaseModel):
    voice_profile: dict


@app.put("/api/profile")
def update_profile(payload: ProfileUpdate, user_id: str = Depends(get_current_user)):
    current = db.load_user_params(user_id)
    current["voice_profile"] = payload.voice_profile
    db.save_user_params(user_id, current)
    return {"ok": True}


# ── Config ─────────────────────────────────────────────────────────────────────

@app.get("/api/config")
def get_config(user_id: str = Depends(get_current_user)):
    return db.load_user_config(user_id)


class ConfigUpdate(BaseModel):
    poll_interval_minutes: Optional[int] = Field(None, ge=1, le=1440)
    poll_start_hour: Optional[int] = Field(None, ge=0, le=23)
    poll_end_hour: Optional[int] = Field(None, ge=0, le=23)
    autonomy_level: Optional[int] = Field(None, ge=1, le=3)
    low_confidence_threshold: Optional[float] = Field(None, ge=0.0, le=1.0)
    lookback_hours: Optional[int] = Field(None, ge=0)
    user_timezone: Optional[str] = None
    anthropic_model: Optional[str] = None


@app.patch("/api/config")
def update_config(body: ConfigUpdate, user_id: str = Depends(get_current_user)):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No updates provided")
    current = db.load_user_config(user_id)
    merged = {**current, **updates}
    db.save_user_config(user_id, merged)
    if "poll_interval_minutes" in updates:
        scheduler.add_user_job(user_id)  # reschedule with new interval
    return merged


# ── Queue ──────────────────────────────────────────────────────────────────────

@app.get("/api/queue")
def queue(pending_only: bool = False, user_id: str = Depends(get_current_user)):
    if pending_only:
        return db.get_pending_queue(user_id)
    return db.get_all_queue(user_id)


@app.get("/api/queue/{item_id}")
def queue_item(item_id: int, user_id: str = Depends(get_current_user)):
    item = db.get_queue_item(user_id, item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    return item


class DraftUpdate(BaseModel):
    draft_reply: str


@app.put("/api/queue/{item_id}/draft")
def update_draft(item_id: int, body: DraftUpdate, user_id: str = Depends(get_current_user)):
    item = db.get_queue_item(user_id, item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    db.update_draft_reply(user_id, item_id, body.draft_reply)
    return {"ok": True}


class ApproveAction(BaseModel):
    action: str  # "send", "draft", or "discard"


@app.post("/api/queue/{item_id}/action")
def take_action(item_id: int, body: ApproveAction, user_id: str = Depends(get_current_user)):
    item = db.get_queue_item(user_id, item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    if item["status"] != "pending":
        raise HTTPException(400, f"Item already actioned: {item['status']}")

    sender_email = _extract_email(item["sender"])
    subject = item["subject"]
    reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"

    # Re-fetch Drive attachment if needed (binary data is not persisted to DB)
    attachments = []
    cls = item.get("classification") or {}
    if cls.get("needs_gdrive") and cls.get("gdrive_query"):
        drive_service = auth.get_drive_service(user_id)
        attachments = search_and_attach(drive_service, cls["gdrive_query"])

    if body.action == "send":
        gmail_service = auth.get_gmail_service(user_id)
        success = send_reply(
            gmail_service,
            thread_id=item["thread_id"],
            to=sender_email,
            subject=reply_subject,
            body=item["draft_reply"],
            attachments=attachments or None,
        )
        if not success:
            raise HTTPException(500, "Failed to send email")
        db.update_queue_item(user_id, item_id, "sent", "sent by user")
        db.log_event(user_id, "user_sent", f"Sent: '{subject}' → {sender_email}")

    elif body.action == "draft":
        gmail_service = auth.get_gmail_service(user_id)
        create_reply_draft(
            gmail_service,
            thread_id=item["thread_id"],
            to=sender_email,
            subject=reply_subject,
            body=item["draft_reply"],
            attachments=attachments or None,
        )
        db.update_queue_item(user_id, item_id, "drafted", "saved as Gmail draft")
        db.log_event(user_id, "user_drafted", f"Saved to drafts: '{subject}'")

    elif body.action == "discard":
        db.update_queue_item(user_id, item_id, "discarded", "discarded by user")
        db.log_event(user_id, "user_discarded", f"Discarded: '{subject}'")

    else:
        raise HTTPException(400, f"Unknown action: {body.action}")

    return {"ok": True, "action": body.action}


# ── Scheduler ──────────────────────────────────────────────────────────────────

@app.get("/api/scheduler/status")
def scheduler_status(user_id: str = Depends(get_current_user)):
    return scheduler.get_user_status(user_id)


@app.post("/api/scheduler/run-now")
def trigger_poll(user_id: str = Depends(get_current_user)):
    results = scheduler.run_now(user_id)
    return {"processed": len(results), "results": results}


# ── Events ─────────────────────────────────────────────────────────────────────

@app.get("/api/events")
def get_events(
    limit: int = Query(50, ge=1, le=200),
    user_id: str = Depends(get_current_user),
):
    return db.get_recent_events(user_id, limit)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _extract_email(sender: str) -> str:
    match = re.search(r"<([^>]+)>", sender)
    return match.group(1) if match else sender
