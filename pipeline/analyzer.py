"""
Trend Analyzer — uses Claude to understand WHY a trend is viral before generating content.

This is the most important layer for uniqueness:
- Identifies the psychological trigger driving engagement
- Surfaces 3 original angles that haven't been covered yet
- Specifies what the generic/boring version looks like (so we avoid it)
- Determines the optimal hook type and target emotion
"""
import json
import logging
import anthropic
from config import config

logger = logging.getLogger(__name__)

_ANALYSIS_SYSTEM = """You are a viral content analyst with deep expertise in social psychology,
platform algorithms, and what makes content spread. Your job is to understand WHY a piece of
content is going viral — the real psychological trigger underneath the surface topic — and then
identify original angles that haven't been covered yet.

Be brutally specific. "It's interesting" is not analysis. "It triggers cognitive dissonance
because people assumed X was safe but this reveals Y" is analysis.

Always output valid JSON exactly matching the requested schema."""


def analyze(trend: dict) -> dict | None:
    """
    Deep-analyze a trend to understand its virality driver and surface unique angles.

    Returns:
        {
          "virality_driver":  SURPRISE | CONTROVERSY | EDUCATION | EMOTION | HUMOR | FEAR | CURIOSITY
          "core_topic":       underlying subject in plain language (not the headline)
          "why_viral":        1-2 sentences — the actual psychological mechanism
          "audience_insight": what the audience REALLY wants to know / feel from this
          "unique_angles":    list of 3 specific original takes NOT in existing coverage
          "target_emotion":   awe | anger | curiosity | empathy | fear | joy | surprise
          "hook_type":        QUESTION | STAT | STORY | COUNTERINTUITIVE | CURIOSITY_GAP
          "avoid":            what the generic, expected, boring version of this content looks like
          "keywords":         5-7 SEO/search terms people use when looking for this topic
        }
    """
    raw_data = trend.get("raw_data", {})

    # Build comment context — top comments are gold: they show the actual reaction
    comment_ctx = ""
    top_comments = raw_data.get("top_comments", [])
    if top_comments:
        comment_ctx = "\n\nTop audience reactions (comments/replies):\n" + "\n".join(
            f'  [{c["score"]} pts] "{c["body"][:200]}"' for c in top_comments[:5]
        )

    # Engagement signals
    eng_signals = ""
    if "upvotes" in raw_data:
        eng_signals = (
            f"\nReddit: {raw_data['upvotes']:,} upvotes, "
            f"{raw_data.get('comments', 0):,} comments, "
            f"{raw_data.get('upvote_ratio', 0):.0%} upvote ratio"
            f" | velocity: {raw_data.get('velocity', 0):.0f} pts/hr"
        )
    elif "views" in raw_data:
        eng_signals = (
            f"\nYouTube: {raw_data['views']:,} views, "
            f"{raw_data.get('likes', 0):,} likes, "
            f"{raw_data.get('comments', 0):,} comments, "
            f"engagement rate: {raw_data.get('engagement_rate_pct', 0):.2f}%, "
            f"CPM tier multiplier: {raw_data.get('cpm_multiplier', 1.0):.1f}×"
        )

    body_ctx = ""
    if raw_data.get("selftext"):
        body_ctx = f"\n\nPost body:\n{raw_data['selftext'][:500]}"
    elif raw_data.get("description"):
        body_ctx = f"\n\nVideo description:\n{raw_data['description'][:500]}"

    prompt = f"""Analyze this trending content deeply.

Trending title: {trend['topic']}
Source: {trend['source']}
Virality score: {trend['score']:.0f}{eng_signals}{body_ctx}{comment_ctx}

Return JSON with this EXACT schema (no extra fields):
{{
  "virality_driver": "SURPRISE",
  "core_topic": "the real underlying subject in plain language (10-15 words)",
  "why_viral": "2-3 sentences: the specific psychological mechanism — WHY people can't scroll past this",
  "audience_insight": "what the audience secretly wants from this content: the question they need answered or feeling they want validated",
  "unique_angles": [
    "ANGLE_1: [specific original perspective] — explain the angle and why it hasn't been covered",
    "ANGLE_2: [counterintuitive or contrarian take] — name the counterintuitive claim specifically",
    "ANGLE_3: [personal/practical connection] — how this affects the reader's daily life specifically"
  ],
  "target_emotion": "curiosity",
  "hook_type": "CURIOSITY_GAP",
  "avoid": "1-2 sentences describing what the generic, forgettable version of this content looks like so we avoid it",
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
  "revenue_niche": "HIGH | MEDIUM | LOW — HIGH if topic overlaps with finance, investing, tech, software, career, health, education; MEDIUM for news/politics/howto; LOW for pure entertainment/gaming",
  "monetization_angle": "1 sentence: a specific sub-topic or search query within this trend that attracts high-CPM advertisers (e.g. investing, software tools, career skills, health products)"
}}"""

    try:
        client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        resp = client.messages.create(
            model=config.claude_model,
            max_tokens=900,
            system=[{
                "type": "text",
                "text": _ANALYSIS_SYSTEM,
                "cache_control": {"type": "ephemeral"},
            }],
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text)
        logger.info(
            "  Analysis: driver=%-12s hook=%-16s emotion=%-10s revenue=%s",
            result.get("virality_driver", "?"),
            result.get("hook_type", "?"),
            result.get("target_emotion", "?"),
            result.get("revenue_niche", "?"),
        )
        return result
    except Exception as e:
        logger.error("Trend analysis failed for '%s': %s", trend["topic"][:60], e)
        return None
