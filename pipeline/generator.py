"""
Content Generator — produces video scripts using Groq (Llama 3.3 70B).

Uniqueness enforced via:
1. Analyzer-supplied angle (not just the trending headline)
2. Explicit instruction to avoid the generic version
3. Hook type + target emotion injected per platform

One video is produced per trend and reused across YouTube, TikTok, and Instagram.
Platform-specific captions/hashtags are handled by pipeline/seo.py.
"""
import json
import logging
from openai import OpenAI
from config import config

logger = logging.getLogger(__name__)

_client = None

_SYSTEM = """You are an expert viral content creator. You create original, engaging content
that approaches trending topics from FRESH ANGLES — never just summarizing what already exists.

Rules:
- ALWAYS use the assigned angle. Never default to summarizing the trend.
- NEVER start with the trending headline — approach from your angle only.
- Hook must match the specified hook_type exactly.
- Evoke the specified target_emotion through specific details, not generic claims.
- Natural human voice — conversational, not corporate.
- Output valid JSON only. No markdown fences, no extra text outside the JSON."""


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=config.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
    return _client


def _parse_json(text: str) -> dict:
    """Parse JSON from LLM output. JSON mode is enabled so output is always valid."""
    text = text.strip()
    # Strip markdown fences in case model adds them despite instructions
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def _hook_instruction(hook_type: str) -> str:
    hooks = {
        "QUESTION":         "Open with a direct question the reader hasn't thought to ask yet.",
        "STAT":             "Open with one specific, surprising statistic or number.",
        "STORY":            "Open with a 1-2 sentence vivid micro-story or scenario.",
        "COUNTERINTUITIVE": "Open with a claim that directly contradicts common belief.",
        "CURIOSITY_GAP":    "Open with a statement that creates an information gap — makes the reader NEED to read more.",
    }
    return hooks.get(hook_type, hooks["CURIOSITY_GAP"])


def generate_youtube_script(trend: dict, analysis: dict = None, angle_idx: int = 1) -> dict | None:
    raw = json.dumps(trend.get("raw_data", {}), ensure_ascii=False)

    analysis_block = ""
    if analysis:
        angles = analysis.get("unique_angles", [])
        chosen_angle = angles[angle_idx % len(angles)] if angles else ""
        hook_instruction = _hook_instruction(analysis.get("hook_type", "CURIOSITY_GAP"))
        revenue_niche = analysis.get("revenue_niche", "MEDIUM")
        monetization_angle = analysis.get("monetization_angle", "")

        revenue_note = ""
        if revenue_niche == "HIGH":
            revenue_note = f"\nREVENUE NOTE: HIGH-CPM niche. Incorporate: {monetization_angle}"

        analysis_block = f"""
TREND ANALYSIS:
- Why it's viral: {analysis.get('why_viral', '')}
- Viewer wants: {analysis.get('audience_insight', '')}
- YOUR ASSIGNED ANGLE: {chosen_angle}
- Hook instruction: {hook_instruction}
- Target emotion: {analysis.get('target_emotion', 'curiosity')}
- AVOID THIS: {analysis.get('avoid', '')}
- Keywords: {', '.join(analysis.get('keywords', [])[:5])}{revenue_note}

CRITICAL: Do NOT narrate the trending content. Approach ONLY from your assigned angle."""

    prompt = f"""Write a YouTube Shorts script about this topic.

Trending topic: {trend['topic']}
Source: {trend['source']}
Context: {raw[:500]}
{analysis_block}

Length: 40-55 seconds spoken (~110-145 words).
Structure:
  [HOOK 0-4s] — execute the hook instruction immediately.
  [CONTENT 4-45s] — unique angle with 2-3 specific facts.
  [CTA 45-55s] — "Follow for more" + one teaser.

Natural spoken language — short sentences, contractions, no jargon.

SEO note: naturally weave the main keywords into the spoken script —
YouTube indexes transcripts, so spoken keywords boost search ranking.
Don't force them; make them flow in the narration organically.

Return JSON only (no markdown fences):
{{
  "title": "YouTube title: primary keyword first, under 65 chars",
  "description": "1 compelling sentence (max 120 chars) that includes the primary keyword — this is the search snippet. No hashtags here.",
  "script": "Full spoken script, no stage directions",
  "tags": ["primary keyword", "tag2", "tag3", "niche tag", "topic tag", "related keyword"]
}}"""

    try:
        resp = _get_client().chat.completions.create(
            model=config.groq_model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1200,
            temperature=0.8,
            response_format={"type": "json_object"},
        )
        result = _parse_json(resp.choices[0].message.content)
        logger.info("  YouTube script: '%s'", result.get("title", "")[:70])
        return result
    except Exception as e:
        logger.error("YouTube generation failed for '%s': %s", trend["topic"][:60], e)
        return None


def generate_clip_script(formula: dict) -> dict | None:
    """
    Generate a YouTube Shorts script using a viral formula extracted by
    transcript_analyzer. The script covers the remix_topic using the exact
    same structural formula — 100% original content, no copied material.

    Args:
        formula: dict from transcript_analyzer.analyze_transcript() containing
                 hook_type, content_structure, viral_formula,
                 remix_topic, remix_hook, pacing.

    Returns: same JSON shape as generate_youtube_script:
             {title, description, script, tags}
    """
    remix_topic   = formula.get("remix_topic", "")
    remix_hook    = formula.get("remix_hook", "")
    viral_formula = formula.get("viral_formula", "")
    structure     = formula.get("content_structure", "REVEAL")
    pacing        = formula.get("pacing", "FAST")
    hook_type     = formula.get("hook_type", "CURIOSITY_GAP")
    hook_instr    = _hook_instruction(hook_type)

    if not remix_topic:
        logger.warning("generate_clip_script: no remix_topic in formula")
        return None

    prompt = f"""Write a YouTube Shorts script for this topic using a proven viral formula.

TOPIC: {remix_topic}
OPENING LINE (use this verbatim as your first sentence): "{remix_hook}"

FORMULA TO FOLLOW EXACTLY:
{viral_formula}

Structure: {structure}
Pacing: {pacing}
Hook type: {hook_type}
Hook instruction: {hook_instr}

Length: 40-55 seconds spoken (~110-145 words).
Beat-by-beat:
  [HOOK 0-4s]    — use the provided opening line exactly, then expand immediately.
  [CONTENT 4-45s] — follow the formula with 2-3 specific concrete facts. Match the {pacing} pacing.
  [CTA 45-55s]   — "Follow for more" + one strong teaser.

Natural spoken language. Short sentences. Contractions. No jargon.
Weave the main keywords into the script naturally (YouTube indexes transcripts).

Return JSON only (no markdown fences):
{{
  "title": "YouTube title: primary keyword first, under 65 chars",
  "description": "1 compelling sentence (max 120 chars) with primary keyword — the search snippet",
  "script": "Full spoken script, no stage directions",
  "tags": ["primary keyword", "tag2", "tag3", "niche tag", "topic tag", "related keyword"]
}}"""

    try:
        resp = _get_client().chat.completions.create(
            model=config.groq_model,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=1200,
            temperature=0.8,
            response_format={"type": "json_object"},
        )
        result = _parse_json(resp.choices[0].message.content)
        logger.info("  Clip script: '%s'", result.get("title", "")[:70])
        return result
    except Exception as e:
        logger.error("Clip script generation failed for '%s': %s", remix_topic[:60], e)
        return None
