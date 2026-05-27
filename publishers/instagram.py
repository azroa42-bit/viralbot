"""
Instagram publisher — posts Reels via the Instagram Graph API (v19+).

Setup (one-time):
  1. Create a Facebook Developer App at developers.facebook.com
  2. Add "Instagram Graph API" product to the app
  3. Connect your Instagram Professional account (Business or Creator) to a Facebook Page
  4. Generate a User Access Token with these permissions:
       instagram_basic, instagram_content_publish, pages_read_engagement
     via: developers.facebook.com → Tools → Graph API Explorer
  5. Exchange for a long-lived token (valid 60 days, auto-refreshed by this module):
     GET https://graph.facebook.com/v19.0/oauth/access_token
         ?grant_type=fb_exchange_token
         &client_id={app_id}
         &client_secret={app_secret}
         &fb_exchange_token={short_lived_token}
  6. Get your Instagram User ID:
     GET https://graph.facebook.com/v19.0/me/accounts  → find your Page
     GET https://graph.facebook.com/v19.0/{page_id}?fields=instagram_business_account
  7. Set in .env:
       INSTAGRAM_ACCESS_TOKEN=...
       INSTAGRAM_USER_ID=...
       INSTAGRAM_APP_ID=...
       INSTAGRAM_APP_SECRET=...

Token auto-refresh: long-lived tokens can be refreshed before expiry.
This module refreshes automatically when the token is within 7 days of expiry.

Video requirements for Reels:
  - Format: MP4 (H.264 video, AAC audio)
  - Aspect ratio: 9:16 (vertical) — our videos are already 1080×1920
  - Duration: 3–90 seconds
  - Max size: 1 GB
"""
import json
import logging
import time
from pathlib import Path

import requests

from config import config

logger = logging.getLogger(__name__)

GRAPH_BASE   = "https://graph.facebook.com/v19.0"
TOKEN_REFRESH = f"{GRAPH_BASE}/oauth/access_token"


# ── Token management ───────────────────────────────────────────────────────────

def _load_token() -> dict | None:
    tf = Path(config.instagram_token_file)
    if tf.exists():
        try:
            return json.loads(tf.read_text())
        except Exception:
            pass
    return None


def _save_token(data: dict):
    Path(config.instagram_token_file).write_text(json.dumps(data))


def _refresh_long_lived_token(token: str) -> str | None:
    """Refresh a long-lived User Access Token (keeps it alive for another 60 days)."""
    resp = requests.get(
        f"{GRAPH_BASE}/oauth/access_token",
        params={
            "grant_type":        "fb_exchange_token",
            "client_id":         config.instagram_app_id,
            "client_secret":     config.instagram_app_secret,
            "fb_exchange_token": token,
        },
        timeout=15,
    )
    if resp.ok:
        data = resp.json()
        return data.get("access_token")
    logger.error("Instagram token refresh failed: %s", resp.text[:200])
    return None


def _get_access_token() -> str | None:
    """
    Returns a valid access token.
    Priority: stored token → refresh if near expiry → env var fallback.
    """
    # 1. Try stored token
    stored = _load_token()
    if stored and stored.get("access_token"):
        expires_at = stored.get("expires_at", 0)
        # Refresh if within 7 days of expiry
        if expires_at and time.time() > expires_at - (7 * 86400):
            new_token = _refresh_long_lived_token(stored["access_token"])
            if new_token:
                new_data = {
                    "access_token": new_token,
                    "expires_at":   time.time() + 60 * 86400,  # 60 days
                }
                _save_token(new_data)
                logger.info("Instagram token refreshed (new 60-day window)")
                return new_token
        elif expires_at and time.time() < expires_at:
            return stored["access_token"]

    # 2. Fall back to .env token (first run — no stored file yet)
    env_token = config.instagram_access_token
    if env_token:
        # Persist it so we can track expiry going forward
        _save_token({
            "access_token": env_token,
            "expires_at":   time.time() + 60 * 86400,
        })
        return env_token

    logger.warning("Instagram access token not set — skipping publish")
    return None


def _get_user_id() -> str | None:
    uid = config.instagram_user_id
    if not uid:
        logger.warning("INSTAGRAM_USER_ID not set in .env — skipping publish")
    return uid or None


# ── Upload ─────────────────────────────────────────────────────────────────────

def post_reel(video_path: str, caption: str) -> str | None:
    """
    Upload an MP4 as an Instagram Reel using the resumable upload flow.

    Returns the Instagram media ID (post ID) on success, None on failure.

    Flow:
      1. Init media container (get container_id + upload_uri)
      2. Upload video bytes to upload_uri
      3. Poll container status until FINISHED
      4. Publish the container → get permanent media_id
    """
    access_token = _get_access_token()
    ig_user_id   = _get_user_id()
    if not access_token or not ig_user_id:
        return None

    video_file = Path(video_path)
    if not video_file.exists():
        logger.error("Instagram: video file not found: %s", video_path)
        return None

    file_size = video_file.stat().st_size

    # ── Step 1: Create media container (resumable) ────────────────────────────
    init_resp = requests.post(
        f"{GRAPH_BASE}/{ig_user_id}/media",
        params={
            "media_type":   "REELS",
            "upload_type":  "resumable",
            "caption":      caption[:2200],
            "access_token": access_token,
        },
        timeout=30,
    )
    if not init_resp.ok:
        logger.error("Instagram container init failed: %s", init_resp.text[:300])
        return None

    init_data    = init_resp.json()
    container_id = init_data.get("id")
    upload_uri   = init_data.get("uri")

    if not container_id or not upload_uri:
        logger.error("Instagram init: missing id or uri in response: %s", init_data)
        return None

    logger.debug("Instagram container created: %s", container_id)

    # ── Step 2: Upload video bytes ────────────────────────────────────────────
    with open(video_file, "rb") as f:
        video_bytes = f.read()

    upload_resp = requests.post(
        upload_uri,
        headers={
            "Authorization":  f"OAuth {access_token}",
            "offset":         "0",
            "file_size":      str(file_size),
            "Content-Type":   "application/octet-stream",
        },
        data=video_bytes,
        timeout=300,
    )
    if not upload_resp.ok:
        logger.error("Instagram video upload failed: HTTP %d — %s",
                     upload_resp.status_code, upload_resp.text[:200])
        return None

    logger.debug("Instagram video bytes uploaded (%d bytes)", file_size)

    # ── Step 3: Poll container status ─────────────────────────────────────────
    for attempt in range(24):  # up to ~2 minutes
        time.sleep(5)
        status_resp = requests.get(
            f"{GRAPH_BASE}/{container_id}",
            params={
                "fields":       "status_code,status",
                "access_token": access_token,
            },
            timeout=15,
        )
        if status_resp.ok:
            s = status_resp.json()
            status_code = s.get("status_code", "")
            logger.debug("Instagram container status: %s (attempt %d)", status_code, attempt + 1)
            if status_code == "FINISHED":
                break
            if status_code == "ERROR":
                logger.error("Instagram container processing error: %s", s)
                return None
    else:
        logger.warning("Instagram container status polling timed out — proceeding anyway")

    # ── Step 4: Publish ───────────────────────────────────────────────────────
    pub_resp = requests.post(
        f"{GRAPH_BASE}/{ig_user_id}/media_publish",
        params={
            "creation_id":  container_id,
            "access_token": access_token,
        },
        timeout=30,
    )
    if not pub_resp.ok:
        logger.error("Instagram publish failed: %s", pub_resp.text[:300])
        return None

    media_id = pub_resp.json().get("id")
    logger.info("Instagram Reel published — media_id=%s", media_id)
    return media_id
