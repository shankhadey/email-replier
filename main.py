"""
FastAPI backend for Gmail Replier.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

from database import (
    init_db, get_pending_queue, get_all_queue, get_queue_item,
    update_queue_item, update_draft_reply, mark_processed,
)
from gmail_client import send_reply, create_reply_draft
from config import load_config, save_config
from scheduler import start_scheduler, stop_scheduler, run_now, get_status, reschedule
from auth import build_web_flow, save_token_from_flow, is_authorized

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# In-memory OAuth flow store (single-user app, this is fine)
_oauth_flow = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Gmail Replier", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve frontend
app.mount("/static", StaticFiles(directory="frontend"), name="static")


@app.get("/")
def index():
    return FileResponse("frontend/index.html")


# ── Auth ─────────────────────────────────────────────────────────────────────

@app.get("/auth")
def start_auth(request: Request):
    """Start the Google OAuth2 flow. Visit this URL in your browser to authorize."""
    global _oauth_flow
    redirect_uri = str(request.base_url) + "auth/callback"
    _oauth_flow = build_web_flow(redirect_uri)
    auth_url, _ = _oauth_flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return RedirectResponse(auth_url)


@app.get("/auth/callback")
def auth_callback(request: Request, code: str = None, error: str = None):
    """Google redirects here after user authorizes."""
    global _oauth_flow
    if error:
        return HTMLResponse(f"<h2>Authorization failed: {error}</h2>", status_code=400)
    if not code:
        return HTMLResponse("<h2>No authorization code received.</h2>", status_code=400)
    if not _oauth_flow:
        return HTMLResponse(
            "<h2>OAuth flow expired. Please visit <a href='/auth'>/auth</a> again.</h2>",
            status_code=400,
        )
    try:
        redirect_uri = str(request.base_url) + "auth/callback"
        _oauth_flow.redirect_uri = redirect_uri
        save_token_from_flow(_oauth_flow, code)
        _oauth_flow = None
        return HTMLResponse("""
            <h2 style='font-family:monospace;color:green'>Authorization successful.</h2>
            <p style='font-family:monospace'>token.json saved.
            <a href='/'>Go to the app</a></p>
        """)
    except Exception as e:
        logger.error(f"Auth callback error: {e}")
        return HTMLResponse(f"<h2>Error saving token: {e}</h2>", status_code=500)


@app.get("/auth/status")
def auth_status():
    return {"authorized": is_authorized()}


# ── Config ──────────────────────────────────────────────────────────────────

@app.get("/api/config")
def get_config():
    return load_config()


class ConfigUpdate(BaseModel):
    poll_interval_minutes: Optional[int] = None
    poll_start_hour: Optional[int] = None
    poll_end_hour: Optional[int] = None
    autonomy_level: Optional[int] = None
    low_confidence_threshold: Optional[float] = None


@app.patch("/api/config")
def update_config(body: ConfigUpdate):
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    if not updates:
        raise HTTPException(400, "No updates provided")
    config = save_config(updates)
    if "poll_interval_minutes" in updates:
        reschedule(updates["poll_interval_minutes"])
    return config


# ── Queue ────────────────────────────────────────────────────────────────────

@app.get("/api/queue")
def queue(pending_only: bool = False):
    if pending_only:
        return get_pending_queue()
    return get_all_queue()


@app.get("/api/queue/{item_id}")
def queue_item(item_id: int):
    item = get_queue_item(item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    return item


class DraftUpdate(BaseModel):
    draft_reply: str


@app.put("/api/queue/{item_id}/draft")
def update_draft(item_id: int, body: DraftUpdate):
    item = get_queue_item(item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    update_draft_reply(item_id, body.draft_reply)
    return {"ok": True}


class ApproveAction(BaseModel):
    action: str  # "send", "draft", or "discard"


@app.post("/api/queue/{item_id}/action")
def take_action(item_id: int, body: ApproveAction):
    item = get_queue_item(item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    if item["status"] != "pending":
        raise HTTPException(400, f"Item already actioned: {item['status']}")

    if body.action == "send":
        sender_email = _extract_email(item["sender"])
        subject = item["subject"]
        reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        success = send_reply(
            thread_id=item["thread_id"],
            to=sender_email,
            subject=reply_subject,
            body=item["draft_reply"],
        )
        if not success:
            raise HTTPException(500, "Failed to send email")
        update_queue_item(item_id, "sent", "sent by user")

    elif body.action == "draft":
        sender_email = _extract_email(item["sender"])
        subject = item["subject"]
        reply_subject = subject if subject.lower().startswith("re:") else f"Re: {subject}"
        create_reply_draft(
            thread_id=item["thread_id"],
            to=sender_email,
            subject=reply_subject,
            body=item["draft_reply"],
        )
        update_queue_item(item_id, "drafted", "saved as Gmail draft")

    elif body.action == "discard":
        update_queue_item(item_id, "discarded", "discarded by user")

    else:
        raise HTTPException(400, f"Unknown action: {body.action}")

    return {"ok": True, "action": body.action}


# ── Scheduler ────────────────────────────────────────────────────────────────

@app.get("/api/scheduler/status")
def scheduler_status():
    return get_status()


@app.post("/api/scheduler/run-now")
def trigger_poll():
    results = run_now()
    return {"processed": len(results), "results": results}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _extract_email(sender: str) -> str:
    import re
    match = re.search(r"<([^>]+)>", sender)
    return match.group(1) if match else sender
