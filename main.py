"""
FastAPI backend for Gmail Replier.
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


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
    action: str  # "send" or "discard"


@app.post("/api/queue/{item_id}/action")
def take_action(item_id: int, body: ApproveAction):
    item = get_queue_item(item_id)
    if not item:
        raise HTTPException(404, "Item not found")
    if item["status"] != "pending":
        raise HTTPException(400, f"Item already actioned: {item['status']}")

    if body.action == "send":
        classification = item.get("classification", {})
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
