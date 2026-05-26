"""
Trend Analyzer — uses Groq (Llama 3.3 70B) to understand WHY a trend is viral.

Groq free tier: 14,400 requests/day, no billing required.
Get a key at: console.groq.com → API Keys → Create API Key
"""
import json
import logging
from openai import OpenAI
from config import config

logger = logging.getLogger(__name__)

_client = None

_ANALYSIS_SYSTEM = """You are a viral content analyst with deep expertise in social psychology,
platform algorithms, and what makes content spread. Your job is to understand WHY a piece of
content is going viral — the real psychological trigger underneath the surface topic — and then
identify original angles that haven't been covered yet.

Be brutally specific. "It's interesting" is not analysis. "It triggers cognitive dissonance
because people assumed X was safe but this reveals Y" is analysis.

Always output valid JSON exactly matching the requested schema. No markdown fences, no extra text."""


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=config.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
    return _client


def analyze(trend: dict) -> dict | None:
    """
    Deep-analyze a trend to understand its virality driver and surface unique angles.
    """
    raw_data = trend.get("raw_data", {})

    comment_ctx = ""
    top_comments = raw_data.get("top_comments", [])
    if top_comments:
        comment_ctx = "\n\nTop audience reactions:\n" + "\n".join(
            f'  [{c["score"]} pts] "{c["body"][:200]}"' for c in top_comments[:5]
        )

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
            f"{raw_data.get('comments', 0):,} comments"
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

Return JSON with this EXACT schema (no extra fields, no markdown fences, output JSON only):
{{
  "virality_driver": "SURPRISE",
  "core_topic": "the real underlying subject in plain language (10-15 words)",
  "why_viral": "2-3 sentences: the specific psychological mechanism",
  "audience_insight": "what the audience secretly wants from this content",
  "unique_angles": [
    "ANGLE_1: specific original perspective not in existing coverage",
    "ANGLE_2: counterintuitive or contrarian take",
    "ANGLE_3: personal/practical connection to daily life"
  ],
  "target_emotion": "curiosity",
  "hook_type": "CURIOSITY_GAP",
  "avoid": "what the generic, forgettable version of this content looks like",
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4", "keyword5"],
  "revenue_niche": "HIGH",
  "monetization_angle": "specific sub-topic that attracts high-CPM advertisers"
}}"""

    try:
        resp = _get_client().chat.completions.create(
            model=config.groq_model,
            messages=[
                {"role": "system", "content": _ANALYSIS_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=900,
            temperature=0.7,
        )
        text = resp.choices[0].message.content.strip()
        if text.startswith("```"):
            parts = text.split("```")
            text = parts[1] if len(parts) > 1 else text
            if text.startswith("json"):
                text = text[4:]
        result = json.loads(text.strip())
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
