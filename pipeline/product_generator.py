"""
Product content generator — creates affiliate marketing content using Claude.

Fundamentally different from the trend generator:
- Trend content: educate/entertain to build audience
- Product content: convert viewers into buyers

The scripts follow proven affiliate video structures:
  PROBLEM → AGITATE → SOLUTION (PAS) for high-ticket items
  HOOK → PROOF → CTA for low-ticket impulse buys

Claude picks the right structure based on price point and category.
All content naturally embeds the affiliate link and TikTok Shop tag.
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


_SYSTEM = """You are an expert affiliate marketer and content creator. You create short-form
video scripts and social media posts that drive product sales without feeling like ads.

Your content rules:
- Lead with the PROBLEM or RESULT, never with "I'm reviewing X"
- Use specific numbers (ratings, prices, savings) — vague claims don't convert
- One clear CTA at the end — don't stack multiple asks
- Write in natural spoken language — contractions, short sentences, conversational
- Never say "affiliate link" or "sponsored" in the script — just "link in bio"
- Output valid JSON exactly matching the requested schema"""


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def generate_product_content(product: dict) -> dict | None:
    """
    Generate a complete content package for one affiliate product:
    - TikTok/YouTube Short script (PAS or HOOK→PROOF→CTA structure)
    - Reddit post title + body
    - SEO tags

    Returns None on failure.
    """
    price    = product.get("price", 0)
    rating   = product.get("rating", 0)
    reviews  = product.get("review_count", 0)
    comm_pct = product.get("commission_pct", 0)
    aff_url  = product.get("affiliate_url", "")
    source   = product.get("source", "")

    # TikTok Shop products get a slightly different CTA
    tiktok_cta = (
        "Check it out via TikTok Shop — link in bio."
        if source == "tiktok_shop"
        else "Link in bio — I'll leave the exact one there."
    )

    prompt = f"""Create affiliate marketing content for this product.

Product: {product['name']}
Category: {product.get('category', '')}
Price: ${price:.2f}
Commission: {comm_pct:.0f}%
Rating: {rating}/5 ({reviews:,} reviews)
Description: {product.get('description', '')[:400]}
Affiliate URL: {aff_url}
Source: {source}

Content rules:
- If price > $50: use Problem-Agitate-Solution structure (PAS)
- If price ≤ $50: use Hook-Proof-CTA structure (impulse buy format)
- Script should be 40-55 seconds spoken (110-145 words)
- Reddit post must feel like a genuine recommendation, not an ad
- Weave in the rating and review count as social proof
- TikTok CTA: "{tiktok_cta}"

Return JSON:
{{
  "video_title": "Short title for YouTube/TikTok (under 60 chars, benefit-led)",
  "video_description": "2 sentences + affiliate link + #Shorts #ad",
  "script": "Full spoken script, no stage directions, natural conversational English",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6"],
  "reddit_title": "Reddit post title — sounds like a genuine recommendation (under 200 chars)",
  "reddit_body": "3 paragraphs: 1) personal problem/context, 2) product discovery + specific details, 3) honest assessment + affiliate link. Conversational, ends with a question."
}}"""

    try:
        resp = _get_client().messages.create(
            model=config.claude_model,
            max_tokens=1400,
            system=[{"type": "text", "text": _SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": prompt}],
        )
        result = _parse_json(resp.content[0].text)
        logger.info("  Content generated for: %s", product["name"][:60])
        return result
    except Exception as e:
        logger.error("Product content generation failed for '%s': %s", product["name"][:60], e)
        return None
