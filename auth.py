"""
Google OAuth2 authentication.
Supports both local flow and web-based flow (for Render/server deployments).
"""

import os
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow, Flow
from googleapiclient.discovery import build

CREDENTIALS_FILE = Path("credentials.json")
TOKEN_FILE = Path("token.json")

SCOPES = [
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
]


def get_credentials() -> Credentials:
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            with open(TOKEN_FILE, "w") as f:
                f.write(creds.to_json())
        else:
            raise RuntimeError(
                "No valid token found. Visit /auth to authorize the app."
            )

    return creds


def is_authorized() -> bool:
    """Check if a valid token exists."""
    try:
        get_credentials()
        return True
    except Exception:
        return False


def build_web_flow(redirect_uri: str) -> Flow:
    """Build an OAuth flow for web-based authorization."""
    if not CREDENTIALS_FILE.exists():
        raise FileNotFoundError("credentials.json not found. Add it as a secret file in Render.")
    flow = Flow.from_client_secrets_file(
        str(CREDENTIALS_FILE),
        scopes=SCOPES,
        redirect_uri=redirect_uri,
    )
    return flow


def save_token_from_flow(flow: Flow, code: str):
    """Exchange auth code for token and save to token.json."""
    flow.fetch_token(code=code)
    creds = flow.credentials
    with open(TOKEN_FILE, "w") as f:
        f.write(creds.to_json())
    return creds


def get_gmail_service():
    return build("gmail", "v1", credentials=get_credentials())


def get_calendar_service():
    return build("calendar", "v3", credentials=get_credentials())


def get_drive_service():
    return build("drive", "v3", credentials=get_credentials())
