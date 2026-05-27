"""
SEO Enrichment — optimizes titles, descriptions, and hashtags for platform algorithms.

Called as a post-processing step after content generation. One Groq call per post.

YouTube Shorts rules enforced:
  - First 3 hashtags in description appear ABOVE the video title in the feed (prime real estate)
  - >15 hashtags → YouTube ignores ALL of them
  - Description first ~125 chars shown in search without "show more"
  - Metadata tags field (separate from hashtags) helps algorithm categorization; cap at 15
  - Title: 50-60 chars ideal, keyword-first

TikTok rules enforced:
  - Description hard limit: 2,200 chars; punchy ≤150 chars performs best
  - 3-5 hashtags (1 broad + 2-3 niche); more dilutes reach on new accounts
"""
import json
import logging

from openai import OpenAI

from config import config

logger = logging.getLogger(__name__)

_client = None

_SEO_SYSTEM = """You are an expert YouTube SEO and social media growth specialist.
You optimize content metadata to maximize organic reach, click-through rate, and watch time.

You know these platform rules cold:

YOUTUBE SHORTS:
- Title: keyword-first, 50-60 chars ideal (anything longer is truncated in search/feed)
- Description line 1 (first 125 chars): must contain primary keyword + curiosity hook — this is
  what shows in search snippets before the user clicks "more"
- Hashtags go at the END of description; first 3 hashtags appear ABOVE the video title in the
  YouTube feed — those 3 spots are prime real estate, choose them wisely
- #Shorts in position 0 is mandatory for Shorts algorithm classification
- Hard cap: 15 hashtags max. YouTube silently ignores ALL hashtags if you use more than 15.
- Metadata tags (the separate tag field, NOT in description): 10-15 single words or short phrases
  that help YouTube's classifier understand the video topic
- Never keyword-stuff or use spammy clickbait — YouTube's system demotes those

TIKTOK:
- Description: conversational, under 150 chars, include a hook question to drive comments
- Hashtags: 3-5 only. Mix: 1 broad (#fyp or #viral) + 2-3 niche-specific
- Niche hashtags outperform #fyp for discoverability on accounts under 10k followers

Output valid JSON only. No markdown fences, no extra text outside JSON."""


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=config.groq_api_key,
            base_url="https://api.groq.com/openai/v1",
        )
    return _client


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


# ── YouTube ────────────────────────────────────────────────────────────────────

def enrich_youtube(trend: dict, analysis: dict | None, content: dict) -> dict:
    """
    Post-process a generated YouTube script dict with SEO-optimal metadata.

    Input  (content keys): title, description, script, tags
    Output: same dict with title, description, tags, hashtags enriched.

    Falls back to original content on any error so the pipeline never breaks.
    """
    topic = trend.get("topic", "")
    keywords = analysis.get("keywords", []) if analysis else []
    revenue_niche = analysis.get("revenue_niche", "MEDIUM") if analysis else "MEDIUM"
    core_topic = analysis.get("core_topic", topic) if analysis else topic
    virality_driver = analysis.get("virality_driver", "") if analysis else ""

    original_title = content.get("title", topic[:70])
    original_desc = content.get("description", "")
    original_tags = content.get("tags", [])

    prompt = f"""Optimize this YouTube Shorts metadata for maximum organic discovery.

TOPIC: {topic}
CORE SUBJECT: {core_topic}
VIRALITY DRIVER: {virality_driver}
TOP KEYWORDS (ranked by relevance): {', '.join(keywords[:8])}
REVENUE NICHE: {revenue_niche}
CURRENT TITLE: {original_title}
CURRENT DESCRIPTION: {original_desc}
CURRENT TAGS: {', '.join(original_tags)}

Return JSON (no markdown fences):
{{
  "title": "SEO title: primary keyword first, 50-60 chars, curiosity-inducing — no emojis",
  "description_body": "Line 1 (≤125 chars): primary keyword + hook. Line 2: 1 punchy expansion sentence. Line 3: CTA e.g. 'Follow for daily [topic] content!' — NO hashtags in this field",
  "hashtags": ["#Shorts", "#NicheTag1", "#NicheTag2", "#viral", "#trending", "#KeywordFacts", "#LearnOnYouTube", "#[topic]"],
  "metadata_tags": ["keyword1", "keyword phrase", "tag3", "tag4", "tag5", "tag6", "tag7", "tag8", "tag9", "tag10", "tag11", "tag12"]
}}

Constraints (enforced):
- hashtags: 8-10 items total. #Shorts MUST be index 0. Indexes 1-2 = your 2 best niche tags
  (they appear above the video title in feed). Remaining = broad + long-tail.
- metadata_tags: 12 items, single words or short phrases only, no # prefix
- title: 50-60 chars, starts with the most-searched keyword for this topic
- description_body line 1: first 125 chars must contain primary keyword + hook"""

    try:
        resp = _get_client().chat.completions.create(
            model=config.groq_model,
            messages=[
                {"role": "system", "content": _SEO_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=700,
            temperature=0.4,
            response_format={"type": "json_object"},
        )

        seo = _parse_json(resp.choices[0].message.content)

        # Enforce #Shorts as first hashtag
        hashtags: list[str] = seo.get("hashtags", ["#Shorts"])
        if "#Shorts" not in hashtags:
            hashtags = ["#Shorts"] + hashtags
        hashtags = hashtags[:15]  # hard YouTube cap

        # Build final description: body block + blank line + hashtag string
        desc_body = seo.get("description_body", original_desc).strip()
        full_desc = f"{desc_body}\n\n{' '.join(hashtags)}"

        # Metadata tags — separate from hashtags, go into YouTube's tag field
        metadata_tags: list[str] = [
            t.lstrip("#") for t in seo.get("metadata_tags", original_tags)
        ][:15]

        enriched = {
            **content,
            "title": seo.get("title", original_title)[:100],
            "description": full_desc,
            "tags": metadata_tags,
            "hashtags": hashtags,  # stored for logging / TikTok reuse
        }

        logger.info(
            "  SEO enriched: title='%s...' | %d hashtags | %d metadata tags",
            enriched["title"][:55],
            len(hashtags),
            len(metadata_tags),
        )
        return enriched

    except Exception as e:
        logger.warning("YouTube SEO enrichment failed (using original content): %s", e)
        return content


# ── TikTok ─────────────────────────────────────────────────────────────────────

# ── Instagram ──────────────────────────────────────────────────────────────────

def enrich_instagram(trend: dict, analysis: dict | None, content: dict) -> dict:
    """
    Produces an Instagram Reels caption optimised for reach and saves.

    Instagram 2024 algorithm notes baked in:
    - First line of caption shown in feed before "more" — must hook immediately
    - 3-5 hashtags outperform 20-30 spam tags (algorithm change 2023+)
    - #Reels in position 0 for Reels discovery
    - Emojis increase engagement on Instagram (unlike YouTube)
    - Niche hashtags > broad ones for new accounts
    - CTA that drives saves ("Save this 🔖") boosts algorithmic reach more than likes

    Returns dict with 'caption' and 'hashtags' keys.
    Falls back to a basic caption on error.
    """
    topic = trend.get("topic", "")
    keywords = analysis.get("keywords", []) if analysis else []
    core_topic = analysis.get("core_topic", topic) if analysis else topic
    target_emotion = analysis.get("target_emotion", "curiosity") if analysis else "curiosity"

    original_title = content.get("title", topic[:70])
    original_desc = content.get("description", "")

    prompt = f"""Write an Instagram Reels caption optimised for maximum organic reach.

TOPIC: {topic}
CORE SUBJECT: {core_topic}
TARGET EMOTION: {target_emotion}
KEYWORDS: {', '.join(keywords[:6])}
VIDEO TITLE: {original_title}
VIDEO DESCRIPTION: {original_desc[:200]}

Return JSON (no markdown fences):
{{
  "caption": "Full Instagram caption. Line 1 (hook, ≤90 chars): bold statement or question that stops the scroll — include the primary keyword. Line 2-3: 2 punchy expansion sentences with emojis. Final line: save CTA like 'Save this for later 🔖' or 'Share with someone who needs this 💬'",
  "hashtags": ["#Reels", "#niche1", "#niche2", "#niche3", "#niche4"]
}}

Constraints:
- caption total: 150-300 chars (before hashtags)
- hashtags: exactly 4-5 items. #Reels MUST be index 0. Rest = niche-specific (not generic spam)
- Use 3-5 relevant emojis in the caption body (Instagram users expect them)
- First line must contain the primary keyword and create immediate curiosity
- The save CTA on the last line is important — saves signal quality content to the algorithm"""

    try:
        resp = _get_client().chat.completions.create(
            model=config.groq_model,
            messages=[
                {"role": "system", "content": _SEO_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=350,
            temperature=0.5,
            response_format={"type": "json_object"},
        )

        result = _parse_json(resp.choices[0].message.content)
        hashtags = result.get("hashtags", ["#Reels"])[:5]
        if "#Reels" not in hashtags:
            hashtags = ["#Reels"] + hashtags[:4]

        caption_body = result.get("caption", original_desc).strip()
        full_caption = f"{caption_body}\n\n{' '.join(hashtags)}"

        logger.info(
            "  Instagram SEO: %d chars | hashtags: %s",
            len(full_caption),
            " ".join(hashtags),
        )
        return {"caption": full_caption, "hashtags": hashtags}

    except Exception as e:
        logger.warning("Instagram SEO enrichment failed (using fallback): %s", e)
        fallback = f"{original_title}\n\n{' '.join(['#Reels', '#viral', '#trending', '#shorts', '#fyp'][:5])}"
        return {"caption": fallback, "hashtags": ["#Reels", "#viral", "#trending"]}


def enrich_tiktok(
    topic: str,
    analysis: dict | None,
    description: str,
    tags: list[str],
) -> dict:
    """
    Optimizes TikTok description and hashtag set.
    Returns {'description': str, 'hashtags': list[str]}
    Falls back gracefully on error.
    """
    keywords = analysis.get("keywords", []) if analysis else []
    core_topic = analysis.get("core_topic", topic) if analysis else topic

    prompt = f"""Optimize this TikTok post for maximum organic reach.

TOPIC: {topic}
CORE SUBJECT: {core_topic}
KEYWORDS: {', '.join(keywords[:5])}
CURRENT DESCRIPTION: {description}

Return JSON (no markdown fences):
{{
  "description": "Punchy hook under 150 chars. End with a question to drive comments.",
  "hashtags": ["#fyp", "#niche1", "#niche2", "#niche3", "#niche4"]
}}

Constraints:
- description: ≤150 chars, conversational, includes a hook question at the end
- hashtags: exactly 4-5. First = #fyp or #viral. Rest = niche tags relevant to the topic.
  Niche > broad for new accounts. No tag stuffing."""

    try:
        resp = _get_client().chat.completions.create(
            model=config.groq_model,
            messages=[
                {"role": "system", "content": _SEO_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            max_tokens=250,
            temperature=0.4,
            response_format={"type": "json_object"},
        )

        result = _parse_json(resp.choices[0].message.content)
        hashtags = result.get("hashtags", ["#fyp"])[:5]
        desc = result.get("description", description)[:150]
        full_desc = f"{desc}\n\n{' '.join(hashtags)}"

        logger.info(
            "  TikTok SEO: %d chars | hashtags: %s",
            len(full_desc),
            " ".join(hashtags),
        )
        return {"description": full_desc, "hashtags": hashtags}

    except Exception as e:
        logger.warning("TikTok SEO enrichment failed (using original): %s", e)
        fallback_desc = f"{description}\n\n{' '.join((tags or ['#fyp'])[:5])}"
        return {"description": fallback_desc, "hashtags": (tags or ["#fyp"])[:5]}
