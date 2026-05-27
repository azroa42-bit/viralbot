"""
Clip-based Short creator.

Two background modes, set via CLIP_MODE in .env:

  inspired   (default — zero copyright risk)
    The trending clip is NOT used as visual background.
    Only its viral FORMULA drives content generation.
    Background: Pexels stock images (or gradient fallback).

  commentary (opt-in — transformative use)
    Downloads the actual trending clip, uses it as cycling background frames.
    New AI voiceover + word-synced captions = transformative work.
    Enable: CLIP_MODE=commentary in .env

Both produce a standard 1080×1920 MP4 ready for YouTube Shorts / TikTok / Reels.

Uses moviepy VideoFileClip for frame extraction — no OpenCV dependency needed.
"""
import asyncio
import logging
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image
from moviepy import AudioFileClip, VideoClip, VideoFileClip

from config import config
from pipeline.video import (
    _generate_audio_timed,
    _build_caption_blocks,
    _caption_at,
    _draw_caption,
    _apply_vignette,
    _fetch_images,
    _gradient_image,
    _make_frame_fn,
    WIDTH, HEIGHT, FPS, FADE_DUR, IMAGE_CYCLE_SECS,
)

logger = logging.getLogger(__name__)


# ── Frame extraction from a downloaded clip ───────────────────────────────────

def _extract_clip_frames(
    clip_path: Path,
    target_duration: float,
    fps_extract: float = 2.0,
) -> list[np.ndarray]:
    """
    Sample frames from a video file at fps_extract rate, crop to 9:16,
    and apply the bottom vignette so captions are readable.

    Returns a list of numpy arrays (H×W×3). Empty list on any failure.
    The caller falls back to stock images when this returns [].
    """
    try:
        video = VideoFileClip(str(clip_path))
        clip_dur = video.duration
        n_frames = max(2, int(target_duration * fps_extract))
        # Evenly space frame samples across the clip length
        step = clip_dur / n_frames

        frames = []
        for i in range(n_frames):
            t = min(i * step, clip_dur - 0.05)
            raw = video.get_frame(t)           # H×W×3 uint8

            # Crop landscape→portrait (9:16)
            img = Image.fromarray(raw).convert("RGB")
            iw, ih = img.size
            target_ratio = WIDTH / HEIGHT      # 1080/1920 ≈ 0.5625
            current_ratio = iw / ih
            if current_ratio > target_ratio:   # too wide → crop sides
                nw = int(ih * target_ratio)
                ox = (iw - nw) // 2
                img = img.crop((ox, 0, ox + nw, ih))
            else:                              # too tall → crop top/bottom
                nh = int(iw / target_ratio)
                oy = (ih - nh) // 2
                img = img.crop((0, oy, iw, oy + nh))

            img = img.resize((WIDTH, HEIGHT), Image.LANCZOS)
            frames.append(_apply_vignette(np.array(img)))

        video.close()
        logger.info(
            "  Clip frames: %d extracted (clip=%.1fs, target=%.1fs)",
            len(frames), clip_dur, target_duration,
        )
        return frames
    except Exception as e:
        logger.warning("Frame extraction failed (%s): %s", clip_path.name if clip_path else "?", e)
        return []


# ── Frame function for clip backgrounds ───────────────────────────────────────

def _make_clip_frame_fn(
    frames: list[np.ndarray],
    caption_blocks: list[tuple],
    total_duration: float,
    title: str,
    frame_cycle_secs: float = 4.0,
):
    """
    Returns a make_frame(t) closure that cycles through extracted video frames
    with word-synced captions and fade transitions at boundaries.
    """
    n = len(frames)

    def make_frame(t: float) -> np.ndarray:
        slot = int(t / frame_cycle_secs) % n
        t_in_slot = t - (int(t / frame_cycle_secs) * frame_cycle_secs)

        frame = frames[slot].copy()
        caption = _caption_at(caption_blocks, t)
        show_title = title if t < 1.5 else ""
        frame = _draw_caption(frame, caption, title=show_title)

        # Fade in at slot boundary + fade out at video end
        alpha = 1.0
        if t_in_slot < FADE_DUR:
            alpha = t_in_slot / FADE_DUR
        remaining = total_duration - t
        if remaining < FADE_DUR:
            alpha = min(alpha, remaining / FADE_DUR)
        alpha = max(0.0, min(1.0, alpha))

        if alpha < 1.0:
            return (frame.astype(float) * alpha).astype(np.uint8)
        return frame

    return make_frame


# ── Main entry point ──────────────────────────────────────────────────────────

def create_clip_short(
    title: str,
    script: str,
    output_name: str,
    clip_path: Optional[Path] = None,
    keywords: Optional[list[str]] = None,
) -> Optional[Path]:
    """
    Produce a 9:16 MP4 Short for the clip pipeline.

    Background selection priority:
      1. Extracted frames from clip_path  (only if CLIP_MODE=commentary)
      2. Pexels stock images              (if PEXELS_API_KEY configured)
      3. Animated gradient fallback

    Args:
        title:       Video title (shown at top for first 1.5s)
        script:      Spoken narration
        output_name: Output filename stem (no extension)
        clip_path:   Path to a downloaded video clip (commentary mode only)
        keywords:    Topic keywords for Pexels image search

    Returns: Path to rendered MP4, or None on failure.
    """
    audio_path = str(config.video_output_dir / f"{output_name}.mp3")
    video_path = config.video_output_dir / f"{output_name}.mp4"

    try:
        # ── 1. TTS + word timings ──────────────────────────────────
        logger.info("  TTS: %s", title[:60])
        word_timings = asyncio.run(_generate_audio_timed(script, audio_path))
        audio_clip = AudioFileClip(audio_path)
        total_duration = audio_clip.duration
        caption_blocks = _build_caption_blocks(word_timings)

        # ── 2. Choose background ────────────────────────────────────
        clip_mode = getattr(config, "clip_mode", "inspired")
        use_clip_bg = (
            clip_mode == "commentary"
            and clip_path is not None
            and clip_path.exists()
        )

        make_frame = None

        if use_clip_bg:
            frames = _extract_clip_frames(clip_path, total_duration)
            if frames:
                logger.info("  Background mode: clip frames")
                make_frame = _make_clip_frame_fn(
                    frames, caption_blocks, total_duration, title
                )

        if make_frame is None:
            # Inspired mode or clip extraction failed → real topic images + video frames
            from scrapers.image_fetcher import get_topic_visuals
            kws = keywords or title.lower().split()[:5]
            images = get_topic_visuals(
                title=title,
                keywords=kws,
                total_needed=14,
                target_duration=total_duration,
                include_video_frames=True,
            )
            if not images:
                n_slots = max(1, int(total_duration / IMAGE_CYCLE_SECS) + 1)
                images = [_gradient_image(i) for i in range(n_slots)]
                logger.info("  Background mode: gradient (%d slots)", n_slots)
            else:
                logger.info("  Background mode: %d topic visuals", len(images))
            make_frame = _make_frame_fn(images, caption_blocks, total_duration, title)

        # ── 3. Render ───────────────────────────────────────────────
        video = VideoClip(make_frame, duration=total_duration).with_fps(FPS)
        video = video.with_audio(audio_clip)
        logger.info("  Rendering → %s", video_path.name)
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
        logger.info("  Clip-short done: %s (%.1fs)", video_path.name, total_duration)
        return video_path

    except Exception as e:
        logger.error("Clip short creation failed: %s", e)
        Path(audio_path).unlink(missing_ok=True)
        return None
