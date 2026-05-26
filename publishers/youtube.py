"""
YouTube publisher — uploads an MP4 as a Short.

First run: opens a browser for OAuth consent. Stores credentials in token.json
for all subsequent runs (no browser needed again).

Requires:
  - client_secrets.json downloaded from Google Cloud Console
    (OAuth 2.0 Desktop client)
  - YouTube Data API v3 enabled in the same project
"""
import logging
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from config import config

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _get_credentials() -> Credentials | None:
    secrets = Path(config.youtube_client_secrets_file)
    token = Path(config.youtube_token_file)

    if not secrets.exists():
        logger.warning("YouTube client_secrets.json not found at %s", secrets)
        return None

    creds = None
    if token.exists():
        creds = Credentials.from_authorized_user_file(str(token), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(str(secrets), SCOPES)
            creds = flow.run_local_server(port=0)
        token.write_text(creds.to_json())

    return creds


def upload_short(video_path: str, title: str, description: str,
                 tags: list[str] = None) -> str | None:
    """
    Upload an MP4 as a YouTube Short.
    Returns the video ID on success, None on failure.
    """
    creds = _get_credentials()
    if not creds:
        return None

    video_file = Path(video_path)
    if not video_file.exists():
        logger.error("Video file not found: %s", video_path)
        return None

    try:
        yt = build("youtube", "v3", credentials=creds)

        # Ensure #Shorts in description for YouTube to classify it
        desc = description if "#Shorts" in description else f"{description}\n\n#Shorts"

        body = {
            "snippet": {
                "title": title[:100],
                "description": desc[:5000],
                "tags": (tags or []) + ["Shorts"],
                "categoryId": "22",  # People & Blogs
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(str(video_file), mimetype="video/mp4", resumable=True)
        request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.debug("Upload progress: %.0f%%", status.progress() * 100)

        video_id = response["id"]
        logger.info("YouTube upload complete: https://youtube.com/shorts/%s", video_id)
        return video_id

    except Exception as e:
        logger.error("YouTube upload failed: %s", e)
        return None
