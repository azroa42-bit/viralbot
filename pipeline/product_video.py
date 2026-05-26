"""
Product video creator — 9:16 MP4 with actual product image overlaid.

Layout (1080×1920):
  ┌───────────────────┐
  │  HOOK TEXT        │  ← 420px — problem statement
  ├───────────────────┤
  │                   │
  │  [PRODUCT IMAGE]  │  ← 780px — downloaded from affiliate source
  │                   │
  ├───────────────────┤
  │  BENEFIT + PRICE  │  ← 420px — key detail + rating stars
  │  ★★★★★  4.8/5    │
  │  CTA: link in bio │
  └───────────────────┘

Falls back to text-only layout if product image is unavailable.
"""
import asyncio
import io
import logging
import math
import textwrap
import uuid
from pathlib import Path

import edge_tts
import numpy as np
import requests
from PIL import Image, ImageDraw, ImageFont
from moviepy import AudioFileClip, ImageClip, concatenate_videoclips

from config import config

logger = logging.getLogger(__name__)

WIDTH, HEIGHT = 1080, 1920
FPS = 24
VOICE = "en-US-AriaNeural"

# Layout zones
HOOK_H    = 420
PRODUCT_H = 780
INFO_H    = HEIGHT - HOOK_H - PRODUCT_H   # 300px

# Colours
BG_TOP       = (8, 12, 35)
BG_BOT       = (4, 4, 18)
TEXT_COLOR   = (255, 255, 255)
ACCENT_COLOR = (80, 160, 255)
STAR_COLOR   = (255, 210, 0)
PRICE_COLOR  = (100, 230, 100)
SHADOW       = (0, 0, 0)


def _gradient_bg() -> Image.Image:
    img = Image.new("RGB", (WIDTH, HEIGHT))
    draw = ImageDraw.Draw(img)
    for y in range(HEIGHT):
        t = y / HEIGHT
        r = int(BG_TOP[0] + t * (BG_BOT[0] - BG_TOP[0]))
        g = int(BG_TOP[1] + t * (BG_BOT[1] - BG_TOP[1]))
        b = int(BG_TOP[2] + t * (BG_BOT[2] - BG_TOP[2]))
        draw.line([(0, y), (WIDTH, y)], fill=(r, g, b))
    return img


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]:
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    return ImageFont.load_default()


def _fetch_product_image(url: str) -> Image.Image | None:
    if not url:
        return None
    try:
        resp = requests.get(url, timeout=10, headers={"User-Agent": "ViralBot/1.0"})
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGBA")
        return img
    except Exception as e:
        logger.warning("Could not download product image from %s: %s", url, e)
        return None


def _fit_image(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    img.thumbnail((max_w, max_h), Image.LANCZOS)
    return img


def _draw_stars(draw: ImageDraw.Draw, rating: float, x: int, y: int, font_size: int = 48):
    font = _load_font(font_size)
    full  = int(rating)
    half  = 1 if (rating - full) >= 0.5 else 0
    empty = 5 - full - half
    stars = "★" * full + "½" * half + "☆" * empty
    draw.text((x + 2, y + 2), stars, font=font, fill=SHADOW)
    draw.text((x, y), stars, font=font, fill=STAR_COLOR)


def _make_frame(product_img: Image.Image | None, hook_text: str,
                benefit_text: str, price: float, rating: float,
                slide_type: str = "content") -> np.ndarray:
    """Render one video frame."""
    base = _gradient_bg()
    draw = ImageDraw.Draw(base)

    # ── Accent line top ──────────────────────────────────────────────────────
    draw.rectangle([(50, 40), (WIDTH - 50, 48)], fill=ACCENT_COLOR)

    # ── Hook zone ────────────────────────────────────────────────────────────
    hook_font = _load_font(62)
    hook_lines = textwrap.wrap(hook_text, width=20)
    line_h = 72
    hook_y = 80
    for i, line in enumerate(hook_lines[:4]):
        bbox = draw.textbbox((0, 0), line, font=hook_font)
        lw = bbox[2] - bbox[0]
        x = (WIDTH - lw) // 2
        y = hook_y + i * line_h
        draw.text((x + 3, y + 3), line, font=hook_font, fill=SHADOW)
        draw.text((x, y), line, font=hook_font, fill=TEXT_COLOR)

    # ── Product image zone ────────────────────────────────────────────────────
    prod_top = HOOK_H
    if product_img:
        img_copy = product_img.copy()
        img_copy = _fit_image(img_copy, WIDTH - 80, PRODUCT_H - 40)
        # Center the image
        px = (WIDTH - img_copy.width) // 2
        py = prod_top + (PRODUCT_H - img_copy.height) // 2
        if img_copy.mode == "RGBA":
            base.paste(img_copy, (px, py), mask=img_copy)
        else:
            base.paste(img_copy, (px, py))
    else:
        # Placeholder box
        draw.rectangle(
            [(80, prod_top + 20), (WIDTH - 80, prod_top + PRODUCT_H - 20)],
            outline=ACCENT_COLOR, width=3
        )
        ph_font = _load_font(48)
        draw.text((WIDTH // 2 - 80, prod_top + PRODUCT_H // 2 - 30),
                  "📦 Product", font=ph_font, fill=ACCENT_COLOR)

    # ── Info zone ────────────────────────────────────────────────────────────
    info_top = HOOK_H + PRODUCT_H
    benefit_font = _load_font(46)
    price_font   = _load_font(54)
    cta_font     = _load_font(42)

    # Benefit text
    benefit_lines = textwrap.wrap(benefit_text, width=24)
    by = info_top + 20
    for line in benefit_lines[:2]:
        bbox = draw.textbbox((0, 0), line, font=benefit_font)
        lw = bbox[2] - bbox[0]
        draw.text(((WIDTH - lw) // 2 + 2, by + 2), line, font=benefit_font, fill=SHADOW)
        draw.text(((WIDTH - lw) // 2, by), line, font=benefit_font, fill=TEXT_COLOR)
        by += 56

    # Price + stars
    if price > 0:
        price_str = f"${price:.0f}"
        bbox = draw.textbbox((0, 0), price_str, font=price_font)
        pw = bbox[2] - bbox[0]
        draw.text((WIDTH // 2 - pw - 30 + 2, by + 2), price_str, font=price_font, fill=SHADOW)
        draw.text((WIDTH // 2 - pw - 30, by), price_str, font=price_font, fill=PRICE_COLOR)

    if rating > 0:
        _draw_stars(draw, rating, WIDTH // 2 + 10, by + 4, font_size=44)

    # CTA
    cta = "🔗 Link in bio"
    bbox = draw.textbbox((0, 0), cta, font=cta_font)
    cw = bbox[2] - bbox[0]
    draw.text(((WIDTH - cw) // 2, by + 70), cta, font=cta_font, fill=ACCENT_COLOR)

    # Bottom accent line
    draw.rectangle([(50, HEIGHT - 48), (WIDTH - 50, HEIGHT - 40)], fill=ACCENT_COLOR)

    return np.array(base)


async def _generate_audio(script: str, audio_path: str):
    communicate = edge_tts.Communicate(script, VOICE)
    await communicate.save(audio_path)


def _split_into_segments(script: str, n_segments: int = 5) -> list[str]:
    """Split script into roughly equal word-count segments."""
    words = script.split()
    size = max(1, math.ceil(len(words) / n_segments))
    return [" ".join(words[i:i + size]) for i in range(0, len(words), size)]


def create_product_short(product: dict, script: str,
                         output_name: str = None) -> Path | None:
    """
    Produce a 9:16 product showcase MP4.
    Downloads the product image, overlays it with benefit text + price + stars.
    Returns output Path or None on failure.
    """
    output_name = output_name or f"product_{uuid.uuid4().hex[:8]}"
    audio_path  = str(config.video_output_dir / f"{output_name}.mp3")
    video_path  = config.video_output_dir / f"{output_name}.mp4"

    try:
        # Download product image once (shared across frames)
        product_img = _fetch_product_image(product.get("image_url", ""))

        # Generate TTS
        logger.info("  TTS → %s", product["name"][:50])
        asyncio.run(_generate_audio(script, audio_path))

        audio_clip     = AudioFileClip(audio_path)
        total_duration = audio_clip.duration

        # Split script into 5 visual segments
        segments    = _split_into_segments(script, n_segments=5)
        secs_each   = total_duration / len(segments)

        price  = product.get("price", 0)
        rating = product.get("rating", 0)

        clips = []
        for i, seg in enumerate(segments):
            # Hook on slide 0, benefit snippet on the rest
            hook_text    = seg if i == 0 else product["name"][:40]
            benefit_text = seg if i > 0 else product.get("description", "")[:60]

            frame = _make_frame(
                product_img=product_img,
                hook_text=hook_text,
                benefit_text=benefit_text,
                price=price,
                rating=rating,
                slide_type="hook" if i == 0 else "content",
            )
            clips.append(ImageClip(frame, duration=secs_each))

        video = concatenate_videoclips(clips, method="compose")
        video = video.with_audio(audio_clip)

        logger.info("  Rendering product video → %s", video_path)
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
        logger.info("  Product video done: %s (%.1fs)", video_path, total_duration)
        return video_path

    except Exception as e:
        logger.error("Product video creation failed: %s", e)
        Path(audio_path).unlink(missing_ok=True)
        return None
