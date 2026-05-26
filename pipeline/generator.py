"""
Content Generator — produces platform-specific content using Groq (Llama 3.3 70B).

Uniqueness enforced via:
1. Analyzer-supplied angle (not just the trending headline)
2. Explicit instruction to avoid the generic version
3. Hook type + target emotion injected per platform
4. Reddit and YouTube always get different angles
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


def generate_reddit_content(trend: dict, analysis: dict = None, angle_idx: int = 0) -> dict | None:
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
            revenue_note = f"\nREVENUE NOTE: HIGH-CPM niche. Naturally weave in: {monetization_angle}"

        analysis_block = f"""
TREND ANALYSIS:
- Why it's viral: {analysis.get('why_viral', '')}
- Audience wants: {analysis.get('audience_insight', '')}
- YOUR ASSIGNED ANGLE: {chosen_angle}
- Hook instruction: {hook_instruction}
- Target emotion: {analysis.get('target_emotion', 'curiosity')}
- AVOID THIS: {analysis.get('avoid', '')}
- Keywords: {', '.join(analysis.get('keywords', [])[:5])}{revenue_note}

CRITICAL: Do NOT summarize the trend. Approach ONLY from the assigned angle."""

    prompt = f"""Write a Reddit post about this topic.

Trending topic: {trend['topic']}
Source: {trend['source']}
Context: {raw[:500]}
{analysis_block}

Return JSON only (no markdown fences):
{{
  "title": "Reddit post title from your unique angle — no clickbait, under 300 chars",
  "body": "3-4 paragraphs from your angle. P1: hook. P2-3: insight. P4: open question. Conversational, no bullets, no headers."
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
        logger.info("  Reddit content: '%s'", result.get("title", "")[:70])
        return result
    except Exception as e:
        logger.error("Reddit generation failed for '%s': %s", trend["topic"][:60], e)
        return None


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

Return JSON only (no markdown fences):
{{
  "title": "YouTube title using angle + main keyword (under 70 chars)",
  "description": "2 punchy sentences + #Shorts #[keyword] #viral",
  "script": "Full spoken script, no stage directions",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6"]
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
