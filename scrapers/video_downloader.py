"""
Video downloader and transcript fetcher.

Provides two capabilities:
  1. YouTube transcripts via youtube-transcript-api (free, no API key needed)
  2. Video/clip download via yt-dlp (supports 1000+ sites, YouTube + TikTok)

Downloaded clips are stored under config.video_output_dir / "clips/".
Transcripts return list[dict] with keys: text, start, duration.

Install dependencies:
  pip install yt-dlp youtube-transcript-api
"""
import json
import logging
import subprocess
from pathlib import Path
from typing import Optional

from config import config

logger = logging.getLogger(__name__)

CLIPS_DIR = config.video_output_dir / "clips"
CLIPS_DIR.mkdir(parents=True, exist_ok=True)


# ── Transcripts ───────────────────────────────────────────────────────────────

def get_transcript(video_id: str, languages: list[str] = None) -> list[dict]:
    """
    Fetch YouTube captions via youtube-transcript-api.

    Tries manual captions first, then auto-generated.
    Returns list of {text, start, duration} dicts; empty list on failure.
    """
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        langs = languages or ["en", "en-US", "en-GB"]
        try:
            segments = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
        except Exception:
            # Fall back to any available language (auto-generated)
            segments = YouTubeTranscriptApi.get_transcript(video_id)
        logger.debug("Transcript: %d segments for video %s", len(segments), video_id)
        return segments
    except Exception as e:
        logger.debug("No transcript for %s: %s", video_id, e)
        return []


# ── Video download ────────────────────────────────────────────────────────────

def download_clip(
    url: str,
    output_name: str,
    start_sec: Optional[float] = None,
    end_sec: Optional[float] = None,
    max_duration_secs: int = 60,
) -> Optional[Path]:
    """
    Download a video clip via yt-dlp.

    Args:
        url:               YouTube/TikTok/any yt-dlp-supported URL.
        output_name:       Filename stem (no extension). Stored in clips/.
        start_sec:         Clip start in seconds. None = start from 0.
        end_sec:           Clip end in seconds. None = use max_duration_secs.
        max_duration_secs: Hard cap on clip length (default 60s for Shorts).

    Returns: Path to the downloaded MP4, or None on failure.
    Idempotent: returns existing file if already downloaded.
    """
    out_path = CLIPS_DIR / f"{output_name}.mp4"
    if out_path.exists() and out_path.stat().st_size > 50_000:
        logger.info("Clip already cached: %s", out_path.name)
        return out_path

    s = start_sec or 0.0
    e = end_sec if end_sec is not None else (s + max_duration_secs)

    cmd = [
        "yt-dlp",
        # Prefer H.264 MP4 so moviepy can read it directly
        "--format",
        "bestvideo[ext=mp4][height<=1080]+bestaudio[ext=m4a]"
        "/bestvideo[height<=1080]+bestaudio"
        "/best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "--output", str(out_path),
        "--no-playlist",
        "--quiet",
        "--no-warnings",
        # Time-range cut
        "--download-sections", f"*{s}-{e}",
        "--force-keyframes-at-cuts",
        url,
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        if result.returncode == 0 and out_path.exists() and out_path.stat().st_size > 10_000:
            size_mb = out_path.stat().st_size / (1024 * 1024)
            logger.info("Downloaded clip: %s (%.1f MB)", out_path.name, size_mb)
            return out_path
        err = (result.stderr or result.stdout or "unknown error")[:300]
        logger.error("yt-dlp failed (exit %d): %s", result.returncode, err)
        return None
    except subprocess.TimeoutExpired:
        logger.error("yt-dlp timed out downloading %s", url)
        return None
    except FileNotFoundError:
        logger.warning("yt-dlp not found — install with: pip install yt-dlp")
        return None
    except Exception as e:
        logger.error("Clip download error for %s: %s", url, e)
        return None


def yt_url(video_id: str) -> str:
    """Build a standard YouTube watch URL from a video ID."""
    return f"https://www.youtube.com/watch?v={video_id}"
