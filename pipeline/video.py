"""
Creates YouTube Shorts (1080x1920) from a script.

Visual pipeline:
  1. Fetch portrait images from Pexels matching the topic keywords
  2. Each image segment gets a slow Ken Burns zoom (1.0 -> 1.08x)
  3. Bottom-half dark vignette overlay so captions are always readable
  4. Script split into caption segments, one per image
  5. Fade in/out transitions between segments
  6. Falls back to animated gradient if no Pexels key configured

Audio:
  - edge-tts JennyNeural voice (warm, natural) at slightly reduced
    rate and pitch for a soothing, human-sounding delivery
"""
import asyncio
import io
import logging
import textwrap
import uuid
from pathlib import Path

import edge_tts
import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont
from moviepy import AudioFileClip, VideoClip, concatenate_videoclips

from config import config

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
WIDTH, HEIGHT = 1080, 1920
FPS = 24

# Voice: Jenny is warm and natural; slight slowdown + lower pitch = soothing
VOICE       = "en-US-JennyNeural"
VOICE_RATE  = "+10%"     # slightly faster — energetic Shorts pacing
VOICE_PITCH = "-4Hz"     # slightly deeper = warmer feel

# Ken Burns zoom range per clip
ZOOM_START = 1.0
ZOOM_END   = 1.08

# Fade duration at clip edges (seconds)
FADE_DUR = 0.35

# Caption zone: bottom portion of the frame
CAPTION_BOTTOM_PAD = 140    # pixels from bottom edge
CAPTION_FONT_SIZE  = 66
CAPTION_LINE_WIDTH = 22     # chars per line before wrapping

# Colours
TEXT_WHITE  = (255, 255, 255)
TEXT_SHADOW = (0,   0,   0)
ACCENT      = (80,  160, 255)

# Gradient fallback palette (cycles through these hues per segment)
GRAD_PALETTES = [
    ((15, 20, 50),  (5, 5, 25)),
    ((40, 10, 50),  (10, 5, 25)),
    ((10, 35, 50),  (5, 15, 25)),
    ((50, 25, 10),  (25, 10, 5)),
]


# ── Font loader ────────────────────────────────────────────────────────────────
def _load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/Arial Bold.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


# ── Image fetching (Pexels) ────────────────────────────────────────────────────
def _fetch_images(keywords: list[str], count: int = 5) -> list[np.ndarray]:
    """
    Fetch portrait images from Pexels matching the topic keywords.
    Returns list of numpy arrays (HEIGHT x WIDTH x 3), or [] if unavailable.
    """
    if not getattr(config, "pexels_api_key", "") or "PASTE" in config.pexels_api_key:
        return []
    query = " ".join(keywords[:4]) if keywords else "nature"
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": config.pexels_api_key},
            params={"query": query, "per_page": count, "orientation": "portrait"},
            timeout=15,
        )
        resp.raise_for_status()
        photos = resp.json().get("photos", [])
        images = []
        for photo in photos:
            # Prefer large2x for quality, fall back down the chain
            src = photo["src"]
            url = src.get("large2x") or src.get("portrait") or src.get("large")
            if not url:
                continue
            try:
                img_bytes = requests.get(url, timeout=20).content
                img = Image.open(io.BytesIO(img_bytes))
                img.load()                      # force decode now so bad files raise here
                img = img.convert("RGB")
                # Crop to exact 9:16
                img_ratio = img.width / img.height
                target_ratio = WIDTH / HEIGHT
                if img_ratio > target_ratio:
                    new_w = int(img.height * target_ratio)
                    offset = (img.width - new_w) // 2
                    img = img.crop((offset, 0, offset + new_w, img.height))
                else:
                    new_h = int(img.width / target_ratio)
                    offset = (img.height - new_h) // 2
                    img = img.crop((0, offset, img.width, offset + new_h))
                img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
                images.append(np.array(img))
            except Exception as img_err:
                logger.debug("Skipping Pexels image (decode error): %s", img_err)
                continue
        logger.info("Pexels: fetched %d images for '%s'", len(images), query[:50])
        return images
    except Exception as e:
        logger.warning("Pexels image fetch failed: %s", e)
        return []


# ── Gradient fallback ──────────────────────────────────────────────────────────
def _gradient_image(palette_idx: int = 0) -> np.ndarray:
    """Animated-look gradient for when no images are available."""
    top, bot = GRAD_PALETTES[palette_idx % len(GRAD_PALETTES)]
    img = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / HEIGHT
        r = int(top[0] + t * (bot[0] - top[0]))
        g = int(top[1] + t * (bot[1] - top[1]))
        b = int(top[2] + t * (bot[2] - top[2]))
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))
    # Decorative circle accent
    draw.ellipse([WIDTH//2 - 300, HEIGHT//2 - 300, WIDTH//2 + 300, HEIGHT//2 + 300],
                 outline=(*ACCENT, 40), width=2)
    draw.ellipse([WIDTH//2 - 500, HEIGHT//2 - 500, WIDTH//2 + 500, HEIGHT//2 + 500],
                 outline=(*ACCENT, 20), width=1)
    return np.array(img)


# ── Vignette overlay ───────────────────────────────────────────────────────────
def _apply_vignette(img_array: np.ndarray) -> np.ndarray:
    """
    Apply a dark vignette to the bottom 55% of the image.
    This ensures caption text is always legible over any photo.
    """
    img = Image.fromarray(img_array).convert("RGBA")
    overlay = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    vignette_start = int(HEIGHT * 0.45)
    for y in range(vignette_start, HEIGHT):
        # Ramp from 0 to 200 alpha
        alpha = int(200 * (y - vignette_start) / (HEIGHT - vignette_start))
        draw.line([(0, y), (WIDTH, y)], fill=(0, 0, 0, alpha))
    composited = Image.alpha_composite(img, overlay).convert("RGB")
    return np.array(composited)


# ── Caption drawing ────────────────────────────────────────────────────────────
def _draw_caption(img_array: np.ndarray, text: str, title: str = "") -> np.ndarray:
    """Render caption text at the bottom of the frame."""
    img = Image.fromarray(img_array)
    draw = ImageDraw.Draw(img)

    caption_font = _load_font(CAPTION_FONT_SIZE)
    small_font   = _load_font(38)

    lines = textwrap.wrap(text, width=CAPTION_LINE_WIDTH)[-3:]  # max 3 lines

    # Draw from bottom up
    y = HEIGHT - CAPTION_BOTTOM_PAD
    for line in reversed(lines):
        bbox = draw.textbbox((0, 0), line, font=caption_font)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        x = (WIDTH - lw) // 2
        y -= (lh + 12)
        # Thick shadow for readability over any background
        for dx, dy in [(-3,-3),(-3,3),(3,-3),(3,3),(0,4),(0,-4),(4,0),(-4,0)]:
            draw.text((x+dx, y+dy), line, font=caption_font, fill=TEXT_SHADOW)
        draw.text((x, y), line, font=caption_font, fill=TEXT_WHITE)

    # Subtle title at top
    if title:
        title_short = title[:55]
        bbox = draw.textbbox((0, 0), title_short, font=small_font)
        tw = bbox[2] - bbox[0]
        draw.text(((WIDTH - tw)//2 + 2, 82), title_short, font=small_font, fill=TEXT_SHADOW)
        draw.text(((WIDTH - tw)//2,     80), title_short, font=small_font, fill=ACCENT)
        # Accent bar under title
        draw.rectangle([(80, 130), (WIDTH-80, 134)], fill=ACCENT)

    return np.array(img)


# ── Ken Burns clip ─────────────────────────────────────────────────────────────
def _ken_burns_clip(base_img: np.ndarray, caption: str,
                    duration: float, title: str = "",
                    zoom_start: float = ZOOM_START,
                    zoom_end: float = ZOOM_END) -> VideoClip:
    """
    Create a VideoClip with:
    - Slow Ken Burns zoom (zoom_start -> zoom_end over `duration` seconds)
    - Dark vignette for caption legibility
    - Caption text and title overlay
    - Fade in/out at edges
    """
    h, w = base_img.shape[:2]   # 1920, 1080

    # Pre-compute vignette once (doesn't change per frame)
    base_vignetted = _apply_vignette(base_img)

    def make_frame(t: float) -> np.ndarray:
        # ── Ken Burns zoom ────────────────────────────────────────
        progress = t / max(duration, 1e-6)
        zoom = zoom_start + (zoom_end - zoom_start) * progress

        crop_h = int(h / zoom)
        crop_w = int(w / zoom)
        y0 = (h - crop_h) // 2
        x0 = (w - crop_w) // 2
        cropped = base_vignetted[y0:y0+crop_h, x0:x0+crop_w]
        zoomed = np.array(
            Image.fromarray(cropped).resize((w, h), Image.LANCZOS)
        )

        # ── Caption ───────────────────────────────────────────────
        frame = _draw_caption(zoomed, caption, title=title if t < 1.0 else "")

        # ── Fade in/out ───────────────────────────────────────────
        alpha = 1.0
        if t < FADE_DUR:
            alpha = t / FADE_DUR
        elif t > duration - FADE_DUR:
            alpha = (duration - t) / FADE_DUR
        alpha = max(0.0, min(1.0, alpha))

        if alpha < 1.0:
            return (frame.astype(float) * alpha).astype(np.uint8)
        return frame

    return VideoClip(make_frame, duration=duration).with_fps(FPS)


# ── Script splitting ───────────────────────────────────────────────────────────
def _split_script(script: str, n_segments: int) -> list[str]:
    """Split script into n_segments roughly equal chunks."""
    # Try to split on sentence boundaries first
    import re
    sentences = re.split(r'(?<=[.!?])\s+', script.strip())
    if len(sentences) >= n_segments:
        # Group sentences into n_segments chunks
        per_group = max(1, len(sentences) // n_segments)
        groups = []
        for i in range(0, len(sentences), per_group):
            groups.append(" ".join(sentences[i:i+per_group]))
        return groups[:n_segments] or [script]
    # Fall back to word-count split
    words = script.split()
    size = max(1, len(words) // n_segments)
    return [" ".join(words[i:i+size]) for i in range(0, len(words), size)][:n_segments]


# ── Audio generation ───────────────────────────────────────────────────────────
async def _generate_audio(script: str, audio_path: str):
    communicate = edge_tts.Communicate(script, VOICE, rate=VOICE_RATE, pitch=VOICE_PITCH)
    await communicate.save(audio_path)


# ── Main entry point ───────────────────────────────────────────────────────────
def create_short(title: str, script: str, output_name: str = None,
                 keywords: list[str] = None) -> Path | None:
    """
    Produce a 9:16 MP4 Short.

    Args:
        title:      Video title (shown at top of each frame)
        script:     Spoken narration text
        output_name: Output filename stem
        keywords:   Topic keywords for Pexels image search

    Returns: Path to MP4 on success, None on failure.
    """
    output_name = output_name or str(uuid.uuid4())
    audio_path  = str(config.video_output_dir / f"{output_name}.mp3")
    video_path  = config.video_output_dir / f"{output_name}.mp4"

    try:
        # ── 1. TTS audio ──────────────────────────────────────────
        logger.info("Generating TTS: %s", title[:60])
        asyncio.run(_generate_audio(script, audio_path))
        audio_clip     = AudioFileClip(audio_path)
        total_duration = audio_clip.duration

        # ── 2. Fetch background images ────────────────────────────
        kws = keywords or title.lower().split()[:5]
        images = _fetch_images(kws, count=6)
        using_images = bool(images)
        if not using_images:
            logger.info("No Pexels images — using gradient fallback")

        # ── 3. Build segments ─────────────────────────────────────
        N = min(len(images), 5) if images else 4
        N = max(N, 3)
        segments  = _split_script(script, N)
        durations = [total_duration / len(segments)] * len(segments)
        # Slightly longer first segment (hook matters most)
        if len(segments) > 2:
            bonus = min(1.5, durations[0] * 0.25)
            durations[0] += bonus
            durations[-1] -= bonus

        # ── 4. Create clips ───────────────────────────────────────
        clips = []
        for i, (seg, dur) in enumerate(zip(segments, durations)):
            if using_images:
                img = images[i % len(images)]
            else:
                img = _gradient_image(palette_idx=i)

            clip = _ken_burns_clip(
                base_img=img,
                caption=seg,
                duration=dur,
                title=title,
                # Alternate zoom direction for variety
                zoom_start=ZOOM_START if i % 2 == 0 else ZOOM_END,
                zoom_end=ZOOM_END   if i % 2 == 0 else ZOOM_START,
            )
            clips.append(clip)

        # ── 5. Assemble + audio ───────────────────────────────────
        logger.info("Assembling %d segments (%.1fs total)", len(clips), total_duration)
        video = concatenate_videoclips(clips, method="compose")
        video = video.with_audio(audio_clip)

        # ── 6. Render ─────────────────────────────────────────────
        logger.info("Rendering -> %s", video_path)
        video.write_videofile(
            str(video_path),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=str(config.video_output_dir / f"{output_name}_tmp.m4a"),
            remove_temp=True,
            logger=None,
        )

        Path(audio_path).unlink(missing_ok=True)
        logger.info("Video done: %s (%.1fs, images=%s)", video_path, total_duration, using_images)
        return video_path

    except Exception as e:
        logger.error("Video creation failed: %s", e)
        Path(audio_path).unlink(missing_ok=True)
        return None
