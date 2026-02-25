"""
Google Drive: search for relevant documents and return them as email attachments.
Only called when classifier detects a document request (resume, proposal, etc).
"""

import io
import logging
from typing import Optional

from auth import get_drive_service

logger = logging.getLogger(__name__)

# MIME types we can handle as attachments
EXPORTABLE_MIME = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
}


def search_and_attach(query: str) -> list[dict]:
    """
    Search Drive for files matching the query, return the best match(es)
    as attachment dicts: {filename, data (bytes), mime_type}.
    Tries name-based search first (more precise), falls back to full-text search.
    Returns empty list on failure or no results.
    """
    service = get_drive_service()
    safe_q = _sanitize(query)
    try:
        files = []
        for q in [
            f"name contains '{safe_q}' and trashed=false",
            f"fullText contains '{safe_q}' and trashed=false",
        ]:
            results = service.files().list(
                q=q,
                spaces="drive",
                fields="files(id, name, mimeType, modifiedTime)",
                orderBy="modifiedTime desc",
                pageSize=3,
            ).execute()
            files = results.get("files", [])
            if files:
                break  # found by name — don't fall through to content search

        if not files:
            logger.info(f"No Drive files found for query: {query}")
            return []

        attachments = []
        for f in files[:1]:  # attach only the best match
            att = _download_file(service, f)
            if att:
                attachments.append(att)

        return attachments

    except Exception as e:
        logger.error(f"Drive search error: {e}")
        return []


def _download_file(service, file_meta: dict) -> Optional[dict]:
    file_id = file_meta["id"]
    name = file_meta["name"]
    mime = file_meta["mimeType"]

    try:
        if mime in EXPORTABLE_MIME:
            export_mime, ext = EXPORTABLE_MIME[mime]
            data = service.files().export_media(
                fileId=file_id, mimeType=export_mime
            ).execute()
            filename = name + ext
        else:
            data = service.files().get_media(fileId=file_id).execute()
            filename = name

        return {
            "filename": filename,
            "data": data,
            "mime_type": mime,
        }
    except Exception as e:
        logger.error(f"Error downloading file {name}: {e}")
        return None


def _sanitize(query: str) -> str:
    """Escape single quotes for Drive API query."""
    return query.replace("'", "\\'")


def get_attachment_names(query: str) -> list[str]:
    """
    Quick search to get just filenames (for drafter context without downloading).
    Tries name-based search first, falls back to full-text search.
    """
    service = get_drive_service()
    safe_q = _sanitize(query)
    try:
        for q in [
            f"name contains '{safe_q}' and trashed=false",
            f"fullText contains '{safe_q}' and trashed=false",
        ]:
            results = service.files().list(
                q=q,
                spaces="drive",
                fields="files(id, name, mimeType)",
                orderBy="modifiedTime desc",
                pageSize=3,
            ).execute()
            files = results.get("files", [])
            if files:
                return [f["name"] for f in files][:1]
        return []
    except Exception:
        return []
