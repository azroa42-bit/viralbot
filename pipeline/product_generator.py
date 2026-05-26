"""
Product content generator — creates affiliate marketing content using Gemini.

PAS (Problem-Agitate-Solution) for high-ticket items (>$50).
Hook-Proof-CTA for low-ticket impulse buys (≤$50).

Uses Google Gemini 2.0 Flash (free tier).
"""
import json
import logging
from google import genai
from google.genai import types
from config import config

logger = logging.getLogger(__name__)

_client = None

_SYSTEM = """You are an expert affiliate marketer and content creator. You create short-form
video scripts and social media posts that drive product sales without feeling like ads.

Your content rules:
- Lead with the PROBLEM or RESULT, never with "I'm reviewing X"
- Use specific numbers (ratings, prices, savings) — vague claims don't convert
- One clear CTA at the end — don't stack multiple asks
- Write in natural spoken language — contractions, short sentences, conversational
- Never say "affiliate link" or "sponsored" in the script — just "link in bio"
- Output valid JSON exactly matching the requested schema. No markdown fences."""


def _get_client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=config.gemini_api_key)
    return _client


def _parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text.startswith("json"):
            text = text[4:]
    return json.loads(text.strip())


def generate_product_content(product: dict) -> dict | None:
    price    = product.get("price", 0)
    rating   = product.get("rating", 0)
    reviews  = product.get("review_count", 0)
    comm_pct = product.get("commission_pct", 0)
    aff_url  = product.get("affiliate_url", "")
    source   = product.get("source", "")

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
- Price > $50: use Problem-Agitate-Solution (PAS) structure
- Price ≤ $50: use Hook-Proof-CTA structure (impulse buy)
- Script: 40-55 seconds spoken (110-145 words)
- Reddit post must feel like a genuine recommendation
- Use the rating and review count as social proof
- TikTok CTA: "{tiktok_cta}"

Return JSON (no markdown fences):
{{
  "video_title": "YouTube/TikTok title under 60 chars, benefit-led",
  "video_description": "2 sentences + affiliate link + #Shorts #ad",
  "script": "Full spoken script, no stage directions, natural conversational English",
  "tags": ["tag1", "tag2", "tag3", "tag4", "tag5", "tag6"],
  "reddit_title": "Genuine-sounding recommendation title under 200 chars",
  "reddit_body": "3 paragraphs: 1) personal problem/context, 2) product discovery + specific details, 3) honest assessment + affiliate link. Conversational, ends with a question."
}}"""

    try:
        resp = _get_client().models.generate_content(
            model=config.gemini_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM,
                max_output_tokens=1400,
                temperature=0.7,
            ),
        )
        result = _parse_json(resp.text)
        logger.info("  Content generated for: %s", product["name"][:60])
        return result
    except Exception as e:
        logger.error("Product content generation failed for '%s': %s", product["name"][:60], e)
        return None
