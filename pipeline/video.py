"""
Creates YouTube Shorts (1080x1920) from a script using:
  - edge-tts   → free Microsoft TTS voiceover (MP3)
  - Pillow      → text-on-gradient frames (PNG)
  - moviepy     → assemble frames + audio → MP4

Requires ffmpeg on PATH. Download from https://ffmpeg.org/download.html
"""
import asyncio
import logging
import math
import textwrap
import uuid
from pathlib import Path

import edge_tts
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from moviepy.editor import AudioFileClip, ImageClip, concatenate_videoclips

from config import config

logger = logging.getLogger(__name__)

# Video dimensions for YouTube Shorts (9:16)
WIDTH, HEIGHT = 1080, 1920
FPS = 24
VOICE = "en-US-AriaNeural"

# Colour palette: deep blue gradient
BG_TOP = (10, 15, 40)
BG_BOT = (5, 5, 20)
TEXT_COLOR = (255, 255, 255)
ACCENT_COLOR = (80, 160, 255)


def _gradient_frame() -> np.ndarray:
    """Create a 1080x1920 dark-blue gradient as a numpy array."""
    img = Image.new("RGB", (WIDTH, HEIGHT))
    for y in range(HEIGHT):
        t = y / HEIGHT
        r = int(BG_TOP[0] + t * (BG_BOT[0] - BG_TOP[0]))
        g = int(BG_TOP[1] + t * (BG_BOT[1] - BG_TOP[1]))
        b = int(BG_TOP[2] + t * (BG_BOT[2] - BG_TOP[2]))
        ImageDraw.Draw(img).line([(0, y), (WIDTH, y)], fill=(r, g, b))
    return np.array(img)


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf",   # Windows bold Arial
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


def _text_slide(lines: list[str], font_size: int = 68, subtitle: str = "") -> np.ndarray:
    """Render a single slide with wrapped text on gradient background."""
    base = _gradient_frame()
    img = Image.fromarray(base)
    draw = ImageDraw.Draw(img)

    font = _load_font(font_size)
    small_font = _load_font(38)

    # Accent bar at top
    draw.rectangle([(60, 120), (WIDTH - 60, 128)], fill=ACCENT_COLOR)

    # Main text (centered vertically)
    full_text = "\n".join(lines)
    total_lines = len(lines)
    line_h = font_size + 20
    text_block_h = total_lines * line_h
    y_start = (HEIGHT - text_block_h) // 2 - 40

    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        x = (WIDTH - w) // 2
        y = y_start + i * line_h
        # Drop shadow
        draw.text((x + 3, y + 3), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=TEXT_COLOR)

    # Subtitle / source tag at bottom
    if subtitle:
        bbox = draw.textbbox((0, 0), subtitle, font=small_font)
        w = bbox[2] - bbox[0]
        draw.text(((WIDTH - w) // 2, HEIGHT - 140), subtitle, font=small_font, fill=ACCENT_COLOR)

    # Bottom accent bar
    draw.rectangle([(60, HEIGHT - 100), (WIDTH - 60, HEIGHT - 92)], fill=ACCENT_COLOR)

    return np.array(img)


def _split_script_to_slides(script: str, words_per_slide: int = 20) -> list[list[str]]:
    """Split script into slides of ~words_per_slide words, then wrap each."""
    words = script.split()
    slides = []
    for i in range(0, len(words), words_per_slide):
        chunk = " ".join(words[i : i + words_per_slide])
        wrapped = textwrap.wrap(chunk, width=22)  # ~22 chars per line at font 68
        slides.append(wrapped)
    return slides


async def _generate_audio(script: str, audio_path: str):
    communicate = edge_tts.Communicate(script, VOICE)
    await communicate.save(audio_path)


def create_short(title: str, script: str, output_name: str = None) -> Path | None:
    """
    Produce a 9:16 MP4 short from title + script.
    Returns the output Path on success, None on failure.
    """
    output_name = output_name or str(uuid.uuid4())
    audio_path = str(config.video_output_dir / f"{output_name}.mp3")
    video_path = config.video_output_dir / f"{output_name}.mp4"

    try:
        # 1. Generate TTS audio
        logger.info("Generating TTS audio for: %s", title[:60])
        asyncio.run(_generate_audio(script, audio_path))

        audio_clip = AudioFileClip(audio_path)
        total_duration = audio_clip.duration

        # 2. Build slides timed to audio
        slides = _split_script_to_slides(script)
        if not slides:
            logger.error("Script produced no slides")
            return None

        secs_per_slide = total_duration / len(slides)
        clips = []
        for slide_lines in slides:
            frame = _text_slide(slide_lines, subtitle=title[:50])
            clip = ImageClip(frame, duration=secs_per_slide)
            clips.append(clip)

        # 3. Concatenate slides, add audio
        video = concatenate_videoclips(clips, method="compose")
        video = video.set_audio(audio_clip)

        # 4. Export
        logger.info("Rendering video → %s", video_path)
        video.write_videofile(
            str(video_path),
            fps=FPS,
            codec="libx264",
            audio_codec="aac",
            temp_audiofile=str(config.video_output_dir / f"{output_name}_tmp.m4a"),
            remove_temp=True,
            logger=None,  # suppress moviepy progress bar in logs
        )

        # Cleanup temp audio
        Path(audio_path).unlink(missing_ok=True)
        logger.info("Video created: %s (%.1fs)", video_path, total_duration)
        return video_path

    except Exception as e:
        logger.error("Video creation failed: %s", e)
        Path(audio_path).unlink(missing_ok=True)
        return None
