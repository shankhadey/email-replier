"""
Google OAuth2 authentication.
Place your credentials.json (downloaded from Google Cloud Console) in the project root.
On first run, a browser window will open for authorization. Token is saved to token.json.
"""

import os
from pathlib import Path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
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
        else:
            if not CREDENTIALS_FILE.exists():
                raise FileNotFoundError(
                    "credentials.json not found. Download it from Google Cloud Console "
                    "(APIs & Services > Credentials > OAuth 2.0 Client IDs) and place it "
                    "in the project root."
                )
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CREDENTIALS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return creds


def get_gmail_service():
    return build("gmail", "v1", credentials=get_credentials())


def get_calendar_service():
    return build("calendar", "v3", credentials=get_credentials())


def get_drive_service():
    return build("drive", "v3", credentials=get_credentials())
