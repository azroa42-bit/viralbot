"""
Content Generator — produces platform-specific content using trend analysis.

Uniqueness is enforced by:
1. Using the specific angle from analyzer.py (not just the trending headline)
2. Explicitly telling Claude what the generic version looks like and to avoid it
3. Injecting the hook_type and target_emotion so the format differs from existing content
4. Rotating through 3 angles (Reddit vs YouTube get different angles)
5. Never rephrasing the original — always approaching from a unique perspective
"""
import json
import logging
import anthropic
from config import config

logger = logging.getLogger(__name__)

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    return _client


# Cached system prompt (reduces API cost ~90% via prompt caching)
_SYSTEM = """You are an expert viral content creator. You create original, engaging content
that approaches trending topics from FRESH ANGLES — never just summarizing what already exists.

Your content rules:
- ALWAYS use the assigned angle. Never default to summarizing the trend.
- NEVER start with the trending headline — approach from your angle only.
- Hook must match the specified hook_type exactly.
- Evoke the specified target_emotion through specific details, not generic claims.
- Write in a natural, human voice — conversational, not corporate.
- Posts must feel like they were written by a real person who has a take, not a bot.
- Output valid JSON exactly matching the requested schema."""


def _parse_json(text: str) -> dict:
    """Strip markdown fences and parse JSON."""
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def _hook_instruction(hook_type: str) -> str:
    hooks = {
        "QUESTION":          "Open with a direct question the reader hasn't thought to ask yet.",
        "STAT":              "Open with one specific, surprising statistic or number.",
        "STORY":             "Open with a 1-2 sentence vivid micro-story or scenario.",
        "COUNTERINTUITIVE":  "Open with a claim that directly contradicts common belief.",
        "CURIOSITY_GAP":     "Open with a statement that creates an information gap — something that makes the reader NEED to read more.",
    }
    return hooks.get(hook_type, hooks["CURIOSITY_GAP"])


def generate_reddit_content(trend: dict, analysis: dict = None, angle_idx: int = 0) -> dict | None:
    """
    Generate a Reddit post using the trend analysis.
    angle_idx: 0=first unique angle, 1=second, 2=third (rotated per platform/run)
    """
    raw = json.dumps(trend.get("raw_data", {}), ensure_ascii=False)

    # Build analysis block — this is the core of uniqueness enforcement
    analysis_block = ""
    chosen_angle = ""
    if analysis:
        angles = analysis.get("unique_angles", [])
        chosen_angle = angles[angle_idx % len(angles)] if angles else ""
        hook_instruction = _hook_instruction(analysis.get("hook_type", "CURIOSITY_GAP"))
        revenue_niche = analysis.get("revenue_niche", "MEDIUM")
        monetization_angle = analysis.get("monetization_angle", "")

        # High-CPM topics get an extra instruction to weave in monetizable search terms
        revenue_note = ""
        if revenue_niche == "HIGH":
            revenue_note = (
                f"\nREVENUE NOTE: This topic sits in a HIGH-CPM niche. "
                f"Naturally weave in the following monetization angle to attract "
                f"high-value advertisers: {monetization_angle}"
            )

        analysis_block = f"""
TREND ANALYSIS (use this to guide everything):
- Why it's viral: {analysis.get('why_viral', '')}
- What the audience really wants: {analysis.get('audience_insight', '')}
- YOUR ASSIGNED ANGLE: {chosen_angle}
- Hook instruction: {hook_instruction}
- Target emotion to evoke: {analysis.get('target_emotion', 'curiosity')}
- AVOID THIS GENERIC VERSION: {analysis.get('avoid', '')}
- Keywords to weave in naturally: {', '.join(analysis.get('keywords', [])[:5])}{revenue_note}

CRITICAL: Do NOT summarize the trending content. Do NOT restate the headline.
You must approach this ONLY from the assigned angle above."""

    prompt = f"""Write a Reddit post about this topic.

Trending topic: {trend['topic']}
Source: {trend['source']}
Context: {raw[:500]}
{analysis_block}

Return JSON:
{{
  "title": "Reddit post title from your unique angle only — no clickbait, under 300 chars",
  "body": "3-4 paragraphs written from your assigned angle. Paragraph 1: hook (use the hook instruction). Paragraph 2-3: substance and insight. Paragraph 4: end with an open question to drive comments. Conversational tone, no bullet points, no headers."
}}"""

    try:
        resp = _get_client().messages.create(
            model=config.claude_model,
            max_tokens=1200,
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
        )
        result = _parse_json(resp.content[0].text)
        logger.info("  Reddit content: '%s'", result.get("title", "")[:70])
        return result
    except Exception as e:
        logger.error("Reddit generation failed for '%s': %s", trend["topic"][:60], e)
        return None


def generate_youtube_script(trend: dict, analysis: dict = None, angle_idx: int = 1) -> dict | None:
    """
    Generate a YouTube Shorts script using the trend analysis.
    Uses angle_idx=1 by default so Reddit and YouTube get different angles.
    """
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
            revenue_note = (
                f"\nREVENUE NOTE: HIGH-CPM niche. Naturally incorporate this monetization "
                f"angle so the video ranks for advertiser-valued search terms: {monetization_angle}"
            )

        analysis_block = f"""
TREND ANALYSIS:
- Why it's viral: {analysis.get('why_viral', '')}
- What viewers really want: {analysis.get('audience_insight', '')}
- YOUR ASSIGNED ANGLE: {chosen_angle}
- Hook instruction: {hook_instruction}
- Target emotion: {analysis.get('target_emotion', 'curiosity')}
- AVOID THIS GENERIC VERSION: {analysis.get('avoid', '')}
- Keywords: {', '.join(analysis.get('keywords', [])[:5])}{revenue_note}

CRITICAL: Do NOT narrate the trending video/post. Approach ONLY from your assigned angle."""

    prompt = f"""Write a YouTube Shorts script about this topic.

Trending topic: {trend['topic']}
Source: {trend['source']}
Context: {raw[:500]}
{analysis_block}

Script length: 40-55 seconds spoken (roughly 110-145 words).
Structure:
  [HOOK 0-4s]    — execute the hook instruction. Must grab attention immediately.
  [CONTENT 4-45s] — deliver the unique angle with 2-3 specific facts or insights.
  [CTA 45-55s]   — "Follow for more" + one teaser of what's coming next.

The script must sound natural when read aloud — short sentences, contractions, no jargon.

Return JSON:
{{
  "title": "YouTube title using the unique angle + main keyword (under 70 chars)",
  "description": "2 punchy sentences about the video from the unique angle. Add: #Shorts #[topic keyword] #viral",
  "script": "The full spoken script, no stage directions, natural spoken English only",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6"]
}}"""

    try:
        resp = _get_client().messages.create(
            model=config.claude_model,
            max_tokens=1200,
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
        )
        result = _parse_json(resp.content[0].text)
        logger.info("  YouTube script: '%s'", result.get("title", "")[:70])
        return result
    except Exception as e:
        logger.error("YouTube generation failed for '%s': %s", trend["topic"][:60], e)
        return None
