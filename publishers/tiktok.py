"""
TikTok publisher — uploads videos via the TikTok v2 Content Posting API.

Setup:
  1. Register at https://developers.tiktok.com/ and create an app
  2. Request access to the "Content Posting API" scope
  3. Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET in .env
  4. First upload opens a browser for OAuth consent → saves tiktok_token.json
  5. Subsequent uploads reuse the stored token (auto-refreshed)

Privacy default: SELF_ONLY (drafts) on first use — change to PUBLIC_TO_EVERYONE
once you've confirmed the output looks right.
"""
import hashlib
import json
import logging
import os
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Thread
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from config import config

logger = logging.getLogger(__name__)

AUTH_URL    = "https://www.tiktok.com/v2/auth/authorize/"
TOKEN_URL   = "https://open.tiktokapis.com/v2/oauth/token/"
REFRESH_URL = "https://open.tiktokapis.com/v2/oauth/token/"
INIT_URL    = "https://open.tiktokapis.com/v2/post/publish/video/init/"
STATUS_URL  = "https://open.tiktokapis.com/v2/post/publish/status/fetch/"
REDIRECT    = "http://localhost:8182/callback"
SCOPES      = "video.upload,user.info.basic"
CHUNK_SIZE  = 10 * 1024 * 1024   # 10 MB chunks


# ── OAuth ─────────────────────────────────────────────────────────────────────

class _CallbackHandler(BaseHTTPRequestHandler):
    code = None

    def do_GET(self):
        params = parse_qs(urlparse(self.path).query)
        _CallbackHandler.code = params.get("code", [None])[0]
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"<h2>TikTok auth complete. You can close this tab.</h2>")

    def log_message(self, *args):
        pass   # silence HTTP server log noise


def _run_local_server(timeout: int = 120) -> str | None:
    server = HTTPServer(("localhost", 8182), _CallbackHandler)
    server.timeout = timeout
    server.handle_request()
    return _CallbackHandler.code


def _load_token() -> dict | None:
    tf = Path(config.tiktok_token_file)
    if tf.exists():
        try:
            return json.loads(tf.read_text())
        except Exception:
            pass
    return None


def _save_token(data: dict):
    Path(config.tiktok_token_file).write_text(json.dumps(data))


def _refresh_access_token(refresh_token: str) -> dict | None:
    resp = requests.post(TOKEN_URL, data={
        "client_key":     config.tiktok_client_key,
        "client_secret":  config.tiktok_client_secret,
        "grant_type":     "refresh_token",
        "refresh_token":  refresh_token,
    }, timeout=15)
    if resp.ok:
        return resp.json()
    logger.error("TikTok token refresh failed: %s", resp.text[:200])
    return None


def _get_access_token() -> str | None:
    if not (config.tiktok_client_key and config.tiktok_client_secret):
        logger.warning("TikTok client key/secret not set — skipping")
        return None

    token = _load_token()

    # Use stored token if still valid (with 60s buffer)
    if token and token.get("access_token"):
        expires_at = token.get("expires_at", 0)
        if time.time() < expires_at - 60:
            return token["access_token"]
        # Try refresh
        refreshed = _refresh_access_token(token.get("refresh_token", ""))
        if refreshed and refreshed.get("access_token"):
            refreshed["expires_at"] = time.time() + refreshed.get("expires_in", 86400)
            _save_token(refreshed)
            return refreshed["access_token"]

    # Full OAuth flow
    state = secrets.token_urlsafe(16)
    params = {
        "client_key":    config.tiktok_client_key,
        "scope":         SCOPES,
        "response_type": "code",
        "redirect_uri":  REDIRECT,
        "state":         state,
    }
    url = f"{AUTH_URL}?{urlencode(params)}"
    logger.info("Opening browser for TikTok OAuth...")
    webbrowser.open(url)

    code = _run_local_server(timeout=120)
    if not code:
        logger.error("TikTok OAuth timed out — no code received")
        return None

    resp = requests.post(TOKEN_URL, data={
        "client_key":     config.tiktok_client_key,
        "client_secret":  config.tiktok_client_secret,
        "code":           code,
        "grant_type":     "authorization_code",
        "redirect_uri":   REDIRECT,
    }, timeout=15)

    if not resp.ok:
        logger.error("TikTok token exchange failed: %s", resp.text[:200])
        return None

    data = resp.json()
    data["expires_at"] = time.time() + data.get("expires_in", 86400)
    _save_token(data)
    logger.info("TikTok OAuth complete — token saved")
    return data.get("access_token")


# ── Upload ────────────────────────────────────────────────────────────────────

def upload_video(video_path: str, title: str, description: str,
                 privacy: str = "SELF_ONLY") -> str | None:
    """
    Upload a video to TikTok.

    privacy:
      SELF_ONLY         — saved as draft (safe for testing)
      PUBLIC_TO_EVERYONE — posted publicly
      FRIENDS_ONLY
      MUTUAL_ONLY

    Returns the publish_id on success, None on failure.
    """
    access_token = _get_access_token()
    if not access_token:
        return None

    video_file = Path(video_path)
    if not video_file.exists():
        logger.error("Video file not found: %s", video_path)
        return None

    file_size = video_file.stat().st_size
    chunk_count = max(1, (file_size + CHUNK_SIZE - 1) // CHUNK_SIZE)

    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type":  "application/json; charset=UTF-8",
    }

    # ── Step 1: Init upload ───────────────────────────────────────────────────
    init_body = {
        "post_info": {
            "title":                    title[:2200],
            "privacy_level":            privacy,
            "disable_duet":             False,
            "disable_stitch":           False,
            "disable_comment":          False,
            "video_cover_timestamp_ms": 1000,
        },
        "source_info": {
            "source":            "FILE_UPLOAD",
            "video_size":        file_size,
            "chunk_size":        CHUNK_SIZE,
            "total_chunk_count": chunk_count,
        },
    }
    resp = requests.post(INIT_URL, headers=headers, json=init_body, timeout=30)
    if not resp.ok:
        logger.error("TikTok init failed: %s", resp.text[:300])
        return None

    data       = resp.json().get("data", {})
    publish_id = data.get("publish_id")
    upload_url = data.get("upload_url")
    if not publish_id or not upload_url:
        logger.error("TikTok init: missing publish_id or upload_url in response")
        return None

    # ── Step 2: Upload chunks ─────────────────────────────────────────────────
    with open(video_file, "rb") as f:
        for chunk_idx in range(chunk_count):
            chunk_data   = f.read(CHUNK_SIZE)
            start_byte   = chunk_idx * CHUNK_SIZE
            end_byte     = start_byte + len(chunk_data) - 1
            chunk_headers = {
                "Content-Type":  "video/mp4",
                "Content-Range": f"bytes {start_byte}-{end_byte}/{file_size}",
                "Content-Length": str(len(chunk_data)),
            }
            put_resp = requests.put(upload_url, data=chunk_data,
                                    headers=chunk_headers, timeout=120)
            if not put_resp.ok:
                logger.error("TikTok chunk %d upload failed: HTTP %d",
                             chunk_idx, put_resp.status_code)
                return None
            logger.debug("TikTok chunk %d/%d uploaded", chunk_idx + 1, chunk_count)

    # ── Step 3: Poll status ───────────────────────────────────────────────────
    for attempt in range(12):   # up to ~60s
        time.sleep(5)
        status_resp = requests.post(
            STATUS_URL,
            headers=headers,
            json={"publish_id": publish_id},
            timeout=15,
        )
        if status_resp.ok:
            status_data = status_resp.json().get("data", {})
            state = status_data.get("status", "")
            if state == "PUBLISH_COMPLETE":
                logger.info("TikTok upload complete — publish_id=%s", publish_id)
                return publish_id
            if state in ("FAILED", "CANCELLED"):
                logger.error("TikTok publish failed — state=%s reason=%s",
                             state, status_data.get("fail_reason", ""))
                return None
            logger.debug("TikTok status: %s (attempt %d)", state, attempt + 1)

    logger.warning("TikTok status polling timed out — publish_id=%s", publish_id)
    return publish_id   # return ID anyway; post may still go through
