"""
Topic-aware image and video frame fetcher.

Sources tried in priority order:
  1. Wikipedia REST API   — real photo of the named person / subject (no key)
  2. Wikimedia Commons    — additional real factual images (no key)
  3. Pexels Videos        — relevant video clip frames (same Pexels key)
  4. Pexels Photos        — stock images as contextual fill (same Pexels key)

ALL output is:
  - Cropped to 9:16 portrait (1080×1920)
  - Returned as RGB numpy arrays (H×W×3 uint8)
  - Ready to feed directly into the existing _make_frame_fn / _apply_vignette pipeline

Design note: video clip frames are interleaved with still images in the returned list.
When _make_frame_fn cycles through them, sequential clip frames produce smooth motion,
while still-photo slots add visual variety — no changes required to the render engine.

No new API keys are needed unless you add Google Custom Search (TMDb is optional too).
"""
import io
import logging
import tempfile
import urllib.parse
from pathlib import Path
from typing import Optional

import numpy as np
import requests
from PIL import Image

from config import config

logger = logging.getLogger(__name__)

# Target canvas size (must match pipeline/video.py constants)
WIDTH, HEIGHT = 1080, 1920

HEADERS = {
    "User-Agent": (
        "ViralBot/1.0 (python-requests; content automation; "
        "contact via github.com/azroa42-bit/viralbot)"
    )
}
REQUEST_TIMEOUT = 15
DOWNLOAD_TIMEOUT = 25


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _crop_to_portrait(img: Image.Image) -> np.ndarray:
    """Crop any aspect ratio to 9:16 (center crop) and resize to 1080×1920."""
    img = img.convert("RGB")
    iw, ih = img.size
    target_ratio = WIDTH / HEIGHT          # ≈ 0.5625
    current_ratio = iw / ih
    if current_ratio > target_ratio:       # landscape → crop sides
        nw = int(ih * target_ratio)
        ox = (iw - nw) // 2
        img = img.crop((ox, 0, ox + nw, ih))
    else:                                  # portrait/square → crop top-bottom
        nh = int(iw / target_ratio)
        oy = (ih - nh) // 2
        img = img.crop((0, oy, iw, oy + nh))
    img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
    return np.array(img)


def _download_image(url: str, timeout: int = DOWNLOAD_TIMEOUT) -> Optional[Image.Image]:
    """Download an image URL and return a PIL Image, or None on any failure."""
    try:
        r = requests.get(url, headers=HEADERS, timeout=timeout)
        r.raise_for_status()
        img = Image.open(io.BytesIO(r.content))
        img.load()
        return img.convert("RGB")
    except Exception as e:
        logger.debug("Image download failed (%s): %s", url[:80], e)
        return None


def _is_image_url(url: str) -> bool:
    """Quick check that a URL points to a raster image (not SVG, audio, etc.)."""
    lower = url.lower().split("?")[0]
    return any(lower.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp"))


# ── Source 1: Wikipedia ────────────────────────────────────────────────────────

def fetch_wikipedia_images(subject: str, max_images: int = 3) -> list[np.ndarray]:
    """
    Fetch the main Wikipedia photo(s) for a named subject.

    Tries direct article lookup first, then falls back to search.
    Returns up to max_images cropped 9:16 numpy arrays.
    """
    results: list[np.ndarray] = []
    seen_urls: set[str] = set()

    def _try_page(title: str):
        encoded = urllib.parse.quote(title.replace(" ", "_"), safe="")
        try:
            r = requests.get(
                f"https://en.wikipedia.org/api/rest_v1/page/summary/{encoded}",
                headers=HEADERS, timeout=REQUEST_TIMEOUT,
            )
            if not r.ok:
                return
            data = r.json()
            for key in ("originalimage", "thumbnail"):
                url = data.get(key, {}).get("source", "")
                if url and url not in seen_urls and _is_image_url(url):
                    seen_urls.add(url)
                    img = _download_image(url)
                    if img:
                        results.append(_crop_to_portrait(img))
                        logger.debug("Wikipedia image: %s", url[:80])
        except Exception as e:
            logger.debug("Wikipedia lookup failed for '%s': %s", title, e)

    # Direct lookup
    _try_page(subject)

    # If still short, do a search and try the top results
    if len(results) < max_images:
        try:
            r = requests.get(
                "https://en.wikipedia.org/w/api.php",
                params={
                    "action": "query", "list": "search",
                    "srsearch": subject, "srlimit": 3, "format": "json",
                },
                headers=HEADERS, timeout=REQUEST_TIMEOUT,
            )
            if r.ok:
                for item in r.json().get("query", {}).get("search", []):
                    if len(results) >= max_images:
                        break
                    _try_page(item["title"])
        except Exception as e:
            logger.debug("Wikipedia search failed for '%s': %s", subject, e)

    if results:
        logger.info("  Wikipedia: %d image(s) for '%s'", len(results), subject[:50])
    return results[:max_images]


# ── Source 2: Wikimedia Commons ───────────────────────────────────────────────

def fetch_wikimedia_images(query: str, count: int = 4) -> list[np.ndarray]:
    """
    Search Wikimedia Commons for topic-relevant real photos.
    Filters out SVGs, audio files, and other non-image types.
    """
    results: list[np.ndarray] = []
    try:
        # Step 1: search for matching File: pages
        r = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query", "list": "search",
                "srsearch": query, "srnamespace": "6",
                "srlimit": count * 3,     # over-fetch — many will be non-images
                "format": "json",
            },
            headers=HEADERS, timeout=REQUEST_TIMEOUT,
        )
        if not r.ok:
            return []

        titles = [item["title"] for item in r.json().get("query", {}).get("search", [])]
        if not titles:
            return []

        # Step 2: batch-resolve image URLs
        r2 = requests.get(
            "https://commons.wikimedia.org/w/api.php",
            params={
                "action": "query",
                "titles": "|".join(titles[: count * 2]),
                "prop": "imageinfo",
                "iiprop": "url|mediatype",
                "iiurlwidth": "1080",
                "format": "json",
            },
            headers=HEADERS, timeout=REQUEST_TIMEOUT,
        )
        if not r2.ok:
            return []

        pages = r2.json().get("query", {}).get("pages", {})
        for page in pages.values():
            if len(results) >= count:
                break
            for info in page.get("imageinfo", []):
                # Skip anything that isn't a raster image
                if info.get("mediatype", "") not in ("BITMAP", "DRAWING"):
                    continue
                url = info.get("thumburl") or info.get("url", "")
                if url and _is_image_url(url):
                    img = _download_image(url)
                    if img:
                        results.append(_crop_to_portrait(img))
                        if len(results) >= count:
                            break

        if results:
            logger.info("  Wikimedia Commons: %d image(s) for '%s'", len(results), query[:50])
    except Exception as e:
        logger.debug("Wikimedia search failed for '%s': %s", query, e)
    return results


# ── Source 3: Pexels Videos ───────────────────────────────────────────────────

def fetch_pexels_video_frames(
    query: str,
    target_duration: float = 30.0,
    max_clips: int = 2,
    fps_extract: float = 2.0,
) -> list[np.ndarray]:
    """
    Search Pexels Video for relevant clips, download them, and extract frames
    at fps_extract rate. Sequential frames produce smooth motion when cycled.

    Returns portrait-cropped numpy arrays. Empty list if key not configured.
    """
    key = getattr(config, "pexels_api_key", "")
    if not key or "PASTE" in key.upper():
        return []

    frames: list[np.ndarray] = []
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": key},
            params={"query": query, "per_page": max_clips + 3, "size": "small"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        videos = r.json().get("videos", [])
        if not videos:
            logger.debug("Pexels video: no results for '%s'", query)
            return []

        clips_done = 0
        for video in videos:
            if clips_done >= max_clips:
                break

            # Pick an SD MP4 file (avoid 4K downloads; prefer 720p)
            mp4_files = sorted(
                [f for f in video.get("video_files", [])
                 if f.get("file_type") == "video/mp4"],
                key=lambda f: f.get("width", 0),
            )
            chosen = None
            for vf in mp4_files:
                if 480 <= vf.get("width", 0) <= 1280:
                    chosen = vf
                    break
            if not chosen and mp4_files:
                chosen = mp4_files[0]
            if not chosen or not chosen.get("link"):
                continue

            # Download to a temp file
            try:
                dl = requests.get(chosen["link"], timeout=60, stream=True)
                dl.raise_for_status()
                with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
                    for chunk in dl.iter_content(chunk_size=512 * 1024):
                        tmp.write(chunk)
                    tmp_path = tmp.name

                # Extract frames with moviepy
                from moviepy import VideoFileClip
                vc = VideoFileClip(tmp_path)
                clip_dur = min(vc.duration, 20.0)   # cap at 20s per clip
                n = max(2, int(clip_dur * fps_extract))
                step = clip_dur / n

                for i in range(n):
                    t = min(i * step, clip_dur - 0.05)
                    raw = vc.get_frame(t)
                    frames.append(_crop_to_portrait(Image.fromarray(raw)))

                vc.close()
                Path(tmp_path).unlink(missing_ok=True)
                clips_done += 1
                logger.info(
                    "  Pexels clip: %d frames (%.1fs, %dp)",
                    n, clip_dur, chosen.get("width", 0),
                )
            except Exception as e:
                logger.debug("Pexels clip extraction failed: %s", e)
                Path(tmp_path).unlink(missing_ok=True) if "tmp_path" in dir() else None

    except Exception as e:
        logger.debug("Pexels video search failed for '%s': %s", query, e)

    return frames


# ── Source 4: Pexels Photos ───────────────────────────────────────────────────

def fetch_pexels_images(query: str, count: int = 6) -> list[np.ndarray]:
    """
    Pexels photo search — contextual stock images as fill.
    Same logic as the original _fetch_images in video.py, now centralised here.
    """
    key = getattr(config, "pexels_api_key", "")
    if not key or "PASTE" in key.upper():
        return []
    results: list[np.ndarray] = []
    try:
        r = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": key},
            params={"query": query, "per_page": count + 2, "orientation": "portrait"},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        for photo in r.json().get("photos", []):
            src = photo["src"]
            url = src.get("large2x") or src.get("portrait") or src.get("large")
            if not url:
                continue
            img = _download_image(url, timeout=DOWNLOAD_TIMEOUT)
            if img:
                results.append(_crop_to_portrait(img))
                if len(results) >= count:
                    break
    except Exception as e:
        logger.debug("Pexels photos failed for '%s': %s", query, e)
    if results:
        logger.info("  Pexels photos: %d image(s) for '%s'", len(results), query[:50])
    return results


# ── Orchestrator ───────────────────────────────────────────────────────────────

def get_topic_visuals(
    title: str,
    keywords: list[str] = None,
    total_needed: int = 12,
    target_duration: float = 30.0,
    include_video_frames: bool = True,
) -> list[np.ndarray]:
    """
    Fetch topic-relevant visuals from all available sources.

    Source priority:
      1. Wikipedia — real photos of the named subject  (best for people/places)
      2. Wikimedia Commons — additional factual images
      3. Pexels Video frames — dynamic clip backgrounds
      4. Pexels Photos — stock images as fill

    Args:
        title:               The video title / main subject (used for Wikipedia lookup)
        keywords:            Ranked keyword list from the analyzer; keywords[0] is the
                             primary entity name, rest are context
        total_needed:        Target number of visual frames to return
        target_duration:     Audio duration in seconds (informs how many clip frames to extract)
        include_video_frames: Set False to skip video clip download (faster, images only)

    Returns:
        List of (1080×1920) RGB numpy arrays ready for _make_frame_fn.
        Falls back to [] if all sources fail (caller should use gradient).
    """
    results: list[np.ndarray] = []

    # Build search queries:
    #   subject = the entity itself (most relevant for Wikipedia)
    #   broad   = entity + context keywords (better for Pexels/Commons)
    kws = keywords or title.lower().split()[:5]
    subject = kws[0] if kws else title
    broad   = " ".join([subject] + list(kws[1:3]))   # e.g. "Anthony Gordon football career"

    logger.info(
        "  Visuals: subject='%s'  broad='%s'  need=%d",
        subject[:50], broad[:60], total_needed,
    )

    # ── 1. Wikipedia ──────────────────────────────────────────────
    wiki = fetch_wikipedia_images(subject, max_images=3)
    results.extend(wiki)
    if len(results) >= total_needed:
        return results[:total_needed]

    # ── 2. Wikimedia Commons ─────────────────────────────────────
    still_need = total_needed - len(results)
    commons = fetch_wikimedia_images(broad, count=min(4, still_need + 2))
    results.extend(commons)
    if len(results) >= total_needed:
        return results[:total_needed]

    # ── 3. Pexels Video frames ────────────────────────────────────
    if include_video_frames:
        still_need = total_needed - len(results)
        # Estimate how many frames we need from clips
        clip_frames_needed = max(4, int(target_duration * 1.5))
        vid_frames = fetch_pexels_video_frames(
            broad,
            target_duration=target_duration,
            max_clips=2,
            fps_extract=2.0,
        )
        if vid_frames:
            results.extend(vid_frames[:clip_frames_needed])
        if len(results) >= total_needed:
            return results[:total_needed]

    # ── 4. Pexels Photos ─────────────────────────────────────────
    still_need = max(2, total_needed - len(results))
    pexels = fetch_pexels_images(broad, count=still_need + 2)
    results.extend(pexels)

    total = len(results)
    if total == 0:
        logger.info("  No visuals found from any source — gradient fallback applies")
    else:
        logger.info("  Total visuals assembled: %d frames", total)

    return results[:total_needed] if results else []
