"""
Transcript Analyzer — reverse-engineers the viral formula from trending videos.

Given a YouTube transcript (list of {text, start, duration}), this module:
  1. Extracts the hook (first ~8 seconds of speech)
  2. Identifies the content structure and pacing pattern
  3. Finds key moments with timestamps
  4. Produces a viral_formula description and a remix brief

The remix brief contains:
  - remix_topic: a COMPLETELY DIFFERENT topic using the EXACT SAME structure
  - remix_hook: the opening sentence for the remix (same hook_type, new subject)

This lets the clip pipeline generate 100% original content that copies the
structure/formula of a viral video — not its actual content.
"""
import json
import logging

from openai import OpenAI

from config import config

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=config.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
    return _client


# ── Helpers ───────────────────────────────────────────────────────────────────

def transcript_to_text(segments: list[dict], max_chars: int = 3000) -> str:
    """Flatten transcript segments into timestamped readable text for the LLM."""
    lines = []
    for seg in segments:
        start = seg.get("start", 0)
        text = seg.get("text", "").strip()
        if text:
            lines.append(f"[{int(start)}s] {text}")
    return "\n".join(lines)[:max_chars]


def extract_hook_text(segments: list[dict], duration_secs: float = 8.0) -> str:
    """Return all spoken text from the first `duration_secs` seconds."""
    return " ".join(
        seg.get("text", "").strip()
        for seg in segments
        if seg.get("start", 0) <= duration_secs
    ).strip()


def get_total_duration(segments: list[dict]) -> float:
    """Estimate total video duration from transcript segments."""
    if not segments:
        return 0.0
    last = segments[-1]
    return last.get("start", 0) + last.get("duration", 0)


# ── Main analysis ─────────────────────────────────────────────────────────────

def analyze_transcript(
    segments: list[dict],
    title: str,
    views: int = 0,
    likes: int = 0,
) -> dict | None:
    """
    LLM analysis of a video transcript to extract its viral formula and
    produce a remix brief for generating original content in the same style.

    Returns a dict with these keys:
      hook_type, hook_text, hook_why_works,
      content_structure, pacing,
      key_moments (list of {timestamp, description}),
      viral_formula,
      remix_topic, remix_hook,
      best_clip_start, best_clip_end
    Returns None on any failure.
    """
    if not segments:
        return None

    hook_text = extract_hook_text(segments, duration_secs=8.0)
    full_text = transcript_to_text(segments, max_chars=2500)
    total_dur = get_total_duration(segments)

    prompt = f"""You are a viral content strategist. Analyze this trending YouTube transcript and reverse-engineer its winning formula so we can create original content using the same structure.

VIDEO TITLE: "{title}"
PERFORMANCE: {views:,} views | {likes:,} likes
VIDEO LENGTH: ~{int(total_dur)}s
HOOK (first 8s): "{hook_text}"

FULL TRANSCRIPT:
{full_text}

Return JSON with this exact schema (no markdown fences):
{{
  "hook_type": "QUESTION|STAT|STORY|COUNTERINTUITIVE|CURIOSITY_GAP",
  "hook_text": "the exact first spoken sentence verbatim",
  "hook_why_works": "1 sentence: specific psychological mechanism this hook uses",
  "content_structure": "PROBLEM_SOLUTION|LISTICLE|STORY_ARC|TEACH_TRICK|REVELATION|HOT_TAKE|BEFORE_AFTER",
  "pacing": "FAST|MEDIUM|SLOW",
  "key_moments": [
    {{"timestamp": 0, "description": "what happens and why it keeps viewers watching"}},
    {{"timestamp": 15, "description": "tension or value delivery moment"}},
    {{"timestamp": 45, "description": "payoff, CTA, or resolution"}}
  ],
  "viral_formula": "2-3 sentences: the structural formula — exactly how this video is built, beat by beat",
  "remix_topic": "A SPECIFIC, completely different topic (not the same subject) that would work perfectly with this exact formula. Be concrete, not vague.",
  "remix_hook": "The exact opening sentence for the remix topic — same hook_type, different subject. Ready to use as the first line of a script.",
  "best_clip_start": 0,
  "best_clip_end": 8
}}

Critical rules:
- remix_topic must be a completely different subject than the original video
- remix_hook must be ready to speak — written in the same style as hook_text
- best_clip_start/end: the most visually compelling 5-15s segment of the original
- key_moments: pick the 3 most important structural beats"""

    try:
        resp = _get_client().chat.completions.create(
            model=config.groq_model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=900,
            temperature=0.65,
            response_format={"type": "json_object"},
        )
        result = json.loads(resp.choices[0].message.content)
        logger.info(
            "  Formula: hook=%-16s structure=%-14s remix='%s'",
            result.get("hook_type", "?"),
            result.get("content_structure", "?"),
            result.get("remix_topic", "?")[:60],
        )
        return result
    except Exception as e:
        logger.error("Transcript analysis failed for '%s': %s", title[:60], e)
        return None
