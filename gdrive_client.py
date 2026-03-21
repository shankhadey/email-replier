"""
Google Drive: search for relevant documents and return them as email attachments.
Only called when classifier detects a document request (resume, proposal, etc).
"""

import io
import logging
import re
from typing import Optional

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


def search_and_attach(service, query: str) -> list[dict]:
    """
    Search Drive for files matching the query, return the best match(es)
    as attachment dicts: {filename, data (bytes), mime_type}.
    Tries name-based search first (more precise), falls back to full-text search.
    Candidates are ranked by filename relevance to the query, not recency.
    For full-text fallback, only uses results whose filename overlaps the query.
    Returns empty list on failure or no results.
    """
    safe_q = _sanitize(query)
    try:
        best = None
        for is_fulltext, q in [
            (False, f"name contains '{safe_q}' and trashed=false"),
            (True,  f"fullText contains '{safe_q}' and trashed=false"),
        ]:
            results = service.files().list(
                q=q,
                spaces="drive",
                fields="files(id, name, mimeType, modifiedTime)",
                orderBy="modifiedTime desc",
                pageSize=10,
            ).execute()
            files = results.get("files", [])
            if not files:
                continue

            # Rank by filename relevance; for fullText results require some overlap
            scored = [
                (f, _score_match(query, f["name"]))
                for f in files
            ]
            scored.sort(key=lambda x: x[1], reverse=True)
            top_file, top_score = scored[0]

            if is_fulltext and top_score == 0.0:
                # fullText hit whose filename shares no words with the query —
                # this file merely mentions the search term, it's not what was asked for
                logger.info(
                    f"Drive fullText fallback skipped: best match '{top_file['name']}' "
                    f"has no filename overlap with query '{query}'"
                )
                continue

            best = top_file
            logger.info(
                f"Drive {'fullText' if is_fulltext else 'name'} search picked "
                f"'{best['name']}' (score={top_score:.2f}) for query '{query}'"
            )
            break

        if not best:
            logger.info(f"No Drive files found for query: {query}")
            return []

        att = _download_file(service, best)
        return [att] if att else []

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


def _score_match(query: str, filename: str) -> float:
    """
    Score how well a filename matches the query. Returns 0.0–1.0.
    Exact title match → 1.0; partial word overlap → proportional fraction.
    Ignores file extension and punctuation.
    """
    def tokenize(s: str) -> set[str]:
        # strip extension, lowercase, split on non-alphanumeric
        s = s.rsplit(".", 1)[0] if "." in s else s
        return set(t for t in re.split(r"[^a-z0-9]+", s.lower()) if t)

    q_words = tokenize(query)
    f_words = tokenize(filename)
    if not q_words:
        return 0.0
    # Exact match after tokenization
    if q_words == f_words:
        return 1.0
    overlap = q_words & f_words
    return len(overlap) / len(q_words)


def get_attachment_names(service, query: str) -> list[str]:
    """
    Quick search to get just filenames (for drafter context without downloading).
    Tries name-based search first, falls back to full-text search.
    Applies the same relevance scoring as search_and_attach.
    """
    safe_q = _sanitize(query)
    try:
        for is_fulltext, q in [
            (False, f"name contains '{safe_q}' and trashed=false"),
            (True,  f"fullText contains '{safe_q}' and trashed=false"),
        ]:
            results = service.files().list(
                q=q,
                spaces="drive",
                fields="files(id, name, mimeType)",
                orderBy="modifiedTime desc",
                pageSize=10,
            ).execute()
            files = results.get("files", [])
            if not files:
                continue
            scored = sorted(files, key=lambda f: _score_match(query, f["name"]), reverse=True)
            top = scored[0]
            if is_fulltext and _score_match(query, top["name"]) == 0.0:
                continue
            return [top["name"]]
        return []
    except Exception:
        return []
