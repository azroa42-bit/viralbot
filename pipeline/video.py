"""
Creates YouTube Shorts (1080x1920) from a script.

Caption system: edge-tts WordBoundary events give exact word timestamps.
Words are grouped into caption blocks (~6 words each) and displayed in
sync with the voice — nothing is ever cut off.

Visual pipeline:
  1. Fetch portrait images from Pexels matching topic keywords
  2. Images cycle every IMAGE_CYCLE_SECS seconds
  3. Each image gets a slow Ken Burns zoom (1.0 ↔ 1.08x, alternating)
  4. Dark vignette over bottom half for caption readability
  5. Timed captions drawn at exact word-boundary moments
  6. Fade in/out transitions at image boundaries
  7. Gradient fallback if no Pexels key set

Audio:
  - JennyNeural (+10% rate) — warm, natural, energetic Shorts pace
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
from moviepy import AudioFileClip, VideoClip

from config import config

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
WIDTH, HEIGHT = 1080, 1920
FPS = 24

VOICE       = "en-US-JennyNeural"
VOICE_RATE  = "+10%"
VOICE_PITCH = "-4Hz"

ZOOM_START = 1.0
ZOOM_END   = 1.08
FADE_DUR   = 0.30          # seconds to fade in/out at image boundaries

IMAGE_CYCLE_SECS = 6.0     # switch background image every N seconds

WORDS_PER_BLOCK = 6        # caption words shown at once
CAPTION_Y_BOTTOM = 160     # pixels from bottom edge
CAPTION_FONT_SIZE = 68
CAPTION_LINE_WIDTH = 20    # chars per wrapped line

TEXT_WHITE  = (255, 255, 255)
TEXT_SHADOW = (0,   0,   0)
ACCENT      = (80,  160, 255)

GRAD_PALETTES = [
    ((15, 20, 50),  (5,  5,  25)),
    ((40, 10, 50),  (10, 5,  25)),
    ((10, 35, 50),  (5,  15, 25)),
    ((50, 25, 10),  (25, 10,  5)),
]


# ── Helpers ────────────────────────────────────────────────────────────────────
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


def _gradient_image(palette_idx: int = 0) -> np.ndarray:
    top, bot = GRAD_PALETTES[palette_idx % len(GRAD_PALETTES)]
    img = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / HEIGHT
        r = int(top[0] + t * (bot[0] - top[0]))
        g = int(top[1] + t * (bot[1] - top[1]))
        b = int(top[2] + t * (bot[2] - top[2]))
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))
    draw.ellipse([WIDTH//2-320, HEIGHT//2-320, WIDTH//2+320, HEIGHT//2+320],
                 outline=(*ACCENT, 35), width=2)
    return np.array(img)


def _fetch_images(keywords: list[str], count: int = 6) -> list[np.ndarray]:
    """Fetch portrait images from Pexels. Returns [] if key not set."""
    key = getattr(config, "pexels_api_key", "")
    if not key or "PASTE" in key:
        return []
    query = " ".join(keywords[:4]) if keywords else "nature"
    try:
        resp = requests.get(
            "https://api.pexels.com/v1/search",
            headers={"Authorization": key},
            params={"query": query, "per_page": count, "orientation": "portrait"},
            timeout=15,
        )
        resp.raise_for_status()
        images = []
        for photo in resp.json().get("photos", []):
            src = photo["src"]
            url = src.get("large2x") or src.get("portrait") or src.get("large")
            if not url:
                continue
            try:
                raw = requests.get(url, timeout=20).content
                img = Image.open(io.BytesIO(raw))
                img.load()
                img = img.convert("RGB")
                # Crop to 9:16
                ir = img.width / img.height
                tr = WIDTH / HEIGHT
                if ir > tr:
                    nw = int(img.height * tr)
                    ox = (img.width - nw) // 2
                    img = img.crop((ox, 0, ox + nw, img.height))
                else:
                    nh = int(img.width / tr)
                    oy = (img.height - nh) // 2
                    img = img.crop((0, oy, img.width, oy + nh))
                img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
                images.append(np.array(img))
            except Exception as e:
                logger.debug("Skipping Pexels image: %s", e)
        logger.info("Pexels: %d images for '%s'", len(images), query[:50])
        return images
    except Exception as e:
        logger.warning("Pexels fetch failed: %s", e)
        return []


def _apply_vignette(img_array: np.ndarray) -> np.ndarray:
    """Dark gradient over bottom 55% so captions are always readable."""
    img = Image.fromarray(img_array).convert("RGBA")
    ov  = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d   = ImageDraw.Draw(ov)
    start = int(HEIGHT * 0.45)
    for y in range(start, HEIGHT):
        a = int(210 * (y - start) / (HEIGHT - start))
        d.line([(0, y), (WIDTH, y)], fill=(0, 0, 0, a))
    return np.array(Image.alpha_composite(img, ov).convert("RGB"))


def _draw_caption(img_array: np.ndarray, text: str, title: str = "") -> np.ndarray:
    """Render caption text at bottom; title in small text at top."""
    if not text and not title:
        return img_array
    img  = Image.fromarray(img_array)
    draw = ImageDraw.Draw(img)
    cf   = _load_font(CAPTION_FONT_SIZE)
    sf   = _load_font(36)

    if text:
        lines = textwrap.wrap(text.strip(), width=CAPTION_LINE_WIDTH)
        y = HEIGHT - CAPTION_Y_BOTTOM
        for line in reversed(lines):
            bb = draw.textbbox((0, 0), line, font=cf)
            lw = bb[2] - bb[0]
            lh = bb[3] - bb[1]
            x  = (WIDTH - lw) // 2
            y -= (lh + 10)
            # Thick shadow for any background
            for dx, dy in [(-3,-3),(-3,3),(3,-3),(3,3),(0,4),(0,-4),(4,0),(-4,0)]:
                draw.text((x+dx, y+dy), line, font=cf, fill=TEXT_SHADOW)
            draw.text((x, y), line, font=cf, fill=TEXT_WHITE)

    if title:
        t = title[:55]
        bb = draw.textbbox((0, 0), t, font=sf)
        tw = bb[2] - bb[0]
        draw.text(((WIDTH-tw)//2+2, 82), t, font=sf, fill=TEXT_SHADOW)
        draw.text(((WIDTH-tw)//2,   80), t, font=sf, fill=ACCENT)
        draw.rectangle([(80, 128), (WIDTH-80, 132)], fill=ACCENT)

    return np.array(img)


# ── Word-timed captions ────────────────────────────────────────────────────────
async def _generate_audio_timed(script: str, audio_path: str) -> list[tuple]:
    """
    Generate TTS audio and collect word-level timestamps.
    Returns list of (word, start_sec, duration_sec).
    Offset/duration from edge-tts are in 100-nanosecond Windows units.
    """
    communicate = edge_tts.Communicate(script, VOICE, rate=VOICE_RATE, pitch=VOICE_PITCH,
                                       boundary="WordBoundary")
    word_timings = []
    with open(audio_path, "wb") as f:
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                f.write(chunk["data"])
            elif chunk["type"] == "WordBoundary":
                start_s    = chunk["offset"]   / 10_000_000
                dur_s      = chunk["duration"] / 10_000_000
                word_timings.append((chunk["text"], start_s, dur_s))
    return word_timings


def _build_caption_blocks(word_timings: list[tuple]) -> list[tuple]:
    """
    Group word timings into caption blocks of WORDS_PER_BLOCK words.
    Each block: (text, start_sec, end_sec)
    """
    blocks = []
    for i in range(0, len(word_timings), WORDS_PER_BLOCK):
        group = word_timings[i : i + WORDS_PER_BLOCK]
        text  = " ".join(w[0] for w in group)
        start = group[0][1]
        end   = group[-1][1] + group[-1][2]
        blocks.append((text, start, end))
    return blocks


def _caption_at(blocks: list[tuple], t: float) -> str:
    """Return the caption text that should be visible at time t."""
    # Keep showing a block for 0.15s after it ends (avoids blank flashes)
    for text, start, end in blocks:
        if start <= t < end + 0.15:
            return text
    return ""


# ── Ken Burns frame maker ──────────────────────────────────────────────────────
def _make_frame_fn(images: list[np.ndarray], caption_blocks: list[tuple],
                   total_duration: float, title: str):
    """
    Returns a make_frame(t) closure suitable for moviepy VideoClip.

    Images cycle every IMAGE_CYCLE_SECS seconds; each gets its own
    Ken Burns zoom direction (alternating in/out for visual variety).
    """
    # Pre-apply vignette to every image once
    vig_images = [_apply_vignette(img) for img in images]
    n_images   = len(vig_images)
    h, w       = vig_images[0].shape[:2]

    def make_frame(t: float) -> np.ndarray:
        # ── Which image slot are we in? ───────────────────────────
        slot       = int(t / IMAGE_CYCLE_SECS)
        slot_start = slot * IMAGE_CYCLE_SECS
        slot_end   = slot_start + IMAGE_CYCLE_SECS
        slot_dur   = min(slot_end, total_duration) - slot_start
        t_in_slot  = t - slot_start
        progress   = t_in_slot / max(slot_dur, 1e-6)

        img = vig_images[slot % n_images]

        # ── Ken Burns: alternate zoom in / zoom out ───────────────
        if slot % 2 == 0:
            zoom = ZOOM_START + (ZOOM_END - ZOOM_START) * progress
        else:
            zoom = ZOOM_END - (ZOOM_END - ZOOM_START) * progress

        crop_h = int(h / zoom)
        crop_w = int(w / zoom)
        y0 = (h - crop_h) // 2
        x0 = (w - crop_w) // 2
        cropped = img[y0:y0+crop_h, x0:x0+crop_w]
        frame = np.array(Image.fromarray(cropped).resize((w, h), Image.LANCZOS))

        # ── Caption (exact word timing) ───────────────────────────
        caption = _caption_at(caption_blocks, t)
        # Show title only during first 1.5 seconds
        show_title = title if t < 1.5 else ""
        frame = _draw_caption(frame, caption, title=show_title)

        # ── Fade in at slot boundary ──────────────────────────────
        alpha = 1.0
        if t_in_slot < FADE_DUR:
            alpha = t_in_slot / FADE_DUR
        # Fade out at very end of video
        remaining = total_duration - t
        if remaining < FADE_DUR:
            alpha = min(alpha, remaining / FADE_DUR)
        alpha = max(0.0, min(1.0, alpha))

        if alpha < 1.0:
            return (frame.astype(float) * alpha).astype(np.uint8)
        return frame

    return make_frame


# ── Main entry point ───────────────────────────────────────────────────────────
def create_short(title: str, script: str, output_name: str = None,
                 keywords: list[str] = None) -> Path | None:
    """
    Produce a 9:16 MP4 Short with word-synced captions and real background images.

    Args:
        title:       Video title (shown at top for first 1.5s)
        script:      Spoken narration text
        output_name: Filename stem for the output MP4
        keywords:    Topic keywords for Pexels image search

    Returns: Path to MP4 on success, None on failure.
    """
    output_name = output_name or str(uuid.uuid4())
    audio_path  = str(config.video_output_dir / f"{output_name}.mp3")
    video_path  = config.video_output_dir / f"{output_name}.mp4"

    try:
        # ── 1. TTS + word timings ─────────────────────────────────
        logger.info("Generating TTS: %s", title[:60])
        word_timings = asyncio.run(_generate_audio_timed(script, audio_path))
        logger.info("  Word timings: %d words", len(word_timings))

        audio_clip     = AudioFileClip(audio_path)
        total_duration = audio_clip.duration

        # ── 2. Build caption blocks from word timings ─────────────
        caption_blocks = _build_caption_blocks(word_timings)
        logger.info("  Caption blocks: %d (avg %.1fs each)",
                    len(caption_blocks),
                    total_duration / max(len(caption_blocks), 1))

        # ── 3. Background images ──────────────────────────────────
        kws    = keywords or title.lower().split()[:5]
        images = _fetch_images(kws, count=8)
        if not images:
            logger.info("  No Pexels images — using gradient fallback")
            n_slots = max(1, int(total_duration / IMAGE_CYCLE_SECS) + 1)
            images  = [_gradient_image(i) for i in range(n_slots)]

        # ── 4. Build VideoClip ────────────────────────────────────
        make_frame = _make_frame_fn(images, caption_blocks, total_duration, title)
        video = VideoClip(make_frame, duration=total_duration).with_fps(FPS)
        video = video.with_audio(audio_clip)

        # ── 5. Render ─────────────────────────────────────────────
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
        logger.info("Video done: %s (%.1fs, %d words, images=%s)",
                    video_path, total_duration, len(word_timings), len(images) > 0)
        return video_path

    except Exception as e:
        logger.error("Video creation failed: %s", e)
        Path(audio_path).unlink(missing_ok=True)
        return None
