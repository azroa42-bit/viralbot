"""
Product scraper — pulls affiliate products from three sources:

1. Amazon PA API  — bestselling physical/digital products in high-CPM categories
2. ClickBank API  — high-gravity digital products (courses, software, health)
3. Manual (.env)  — any affiliate product from any network

Each product is scored by estimated revenue per 1,000 impressions:
    score = (price × commission%) × social_proof_factor × 0.005

Social proof factor is based on rating and log-scaled review count, so
well-reviewed products score higher even at similar commission rates.
"""
import logging
import math
import requests
from config import config

logger = logging.getLogger(__name__)

# Amazon search indexes that overlap with high-CPM ad categories
AMAZON_CATEGORIES = [
    ("Electronics",      "Electronics"),
    ("Books",            "Books"),
    ("HealthPersonalCare", "Health & Personal Care"),
    ("SoftwareVideoGames", "Software"),
    ("Apparel",          "Fashion"),
]

# ClickBank categories with consistently high gravity products
CLICKBANK_CATEGORIES = [
    "health", "business", "computing", "education",
    "employment", "finance", "green-products",
]


# ── Scoring ───────────────────────────────────────────────────────────────────

def _score(price: float, commission_pct: float,
           rating: float, review_count: int) -> float:
    commission_value = price * commission_pct / 100
    # Social proof: well-rated + many reviews = higher conversion
    proof = min(math.log10(max(review_count, 1) + 1) / 5, 1.0) * (rating / 5.0)
    # Expected revenue per 1,000 impressions at ~0.5% click-to-purchase rate
    return commission_value * (0.5 + proof * 2) * 0.005 * 1000


# ── Amazon PA API ─────────────────────────────────────────────────────────────

def _get_amazon_products(max_per_category: int = 5) -> list[dict]:
    if not all([config.amazon_access_key, config.amazon_secret_key, config.amazon_associate_tag]):
        logger.debug("Amazon PA API keys not set — skipping")
        return []
    try:
        from amazon_paapi import AmazonApi
        api = AmazonApi(
            key=config.amazon_access_key,
            secret=config.amazon_secret_key,
            tag=config.amazon_associate_tag,
            country=config.amazon_country,
        )
        products = []
        for index, cat_name in AMAZON_CATEGORIES:
            try:
                results = api.search_items(
                    keywords="best seller",
                    search_index=index,
                    item_count=max_per_category,
                    sort_by="Relevance",
                )
                for item in results:
                    try:
                        title = item.item_info.title.display_value
                        url   = item.detail_page_url
                        asin  = item.asin

                        # Price
                        price = 0.0
                        try:
                            price = float(item.offers.listings[0].price.amount)
                        except Exception:
                            pass

                        # Rating & reviews
                        rating, reviews = 0.0, 0
                        try:
                            rating  = float(item.customer_reviews.star_rating.value)
                            reviews = int(item.customer_reviews.count)
                        except Exception:
                            pass

                        # Image
                        image_url = ""
                        try:
                            image_url = item.images.primary.large.url
                        except Exception:
                            pass

                        # Description from features
                        desc = ""
                        try:
                            feats = item.item_info.features.display_values
                            desc = " | ".join(feats[:3]) if feats else ""
                        except Exception:
                            pass

                        # Amazon standard commission: ~3-10% by category
                        commission = 4.0

                        products.append({
                            "product_id":     asin,
                            "source":         "amazon",
                            "name":           title,
                            "description":    desc,
                            "price":          price,
                            "commission_pct": commission,
                            "affiliate_url":  url,
                            "image_url":      image_url,
                            "category":       cat_name,
                            "rating":         rating,
                            "review_count":   reviews,
                            "score":          _score(price, commission, rating, reviews),
                        })
                    except Exception as e:
                        logger.debug("Amazon item parse error: %s", e)
            except Exception as e:
                logger.error("Amazon category %s error: %s", index, e)

        logger.info("Amazon: scraped %d products", len(products))
        return products
    except ImportError:
        logger.warning("python-amazon-paapi not installed — run: pip install python-amazon-paapi")
        return []
    except Exception as e:
        logger.error("Amazon scraper failed: %s", e)
        return []


# ── ClickBank API ─────────────────────────────────────────────────────────────

def _get_clickbank_products(max_per_category: int = 5) -> list[dict]:
    if not (config.clickbank_api_key and config.clickbank_dev_key):
        logger.debug("ClickBank API keys not set — skipping")
        return []
    try:
        products = []
        headers = {
            "Authorization": f"{config.clickbank_dev_key}:{config.clickbank_api_key}",
            "Accept": "application/json",
        }
        for category in CLICKBANK_CATEGORIES:
            try:
                resp = requests.get(
                    "https://api.clickbank.com/rest/1.3/products/list",
                    params={
                        "category":  category,
                        "sortField": "GRAVITY",
                        "sortOrder": "DESC",
                        "pageSize":  max_per_category,
                    },
                    headers=headers,
                    timeout=15,
                )
                if resp.status_code != 200:
                    logger.debug("ClickBank %s → HTTP %d", category, resp.status_code)
                    continue

                data = resp.json()
                for item in data.get("products", []):
                    vendor   = item.get("site", "")
                    name     = item.get("title", vendor)
                    gravity  = float(item.get("gravity", 0))
                    commission = float(item.get("commission", 75))  # ClickBank default is high
                    price    = float(item.get("price", 0))

                    # Build hoplink
                    aff_id = config.clickbank_affiliate_id or "cbaffiliate"
                    affiliate_url = f"https://{aff_id}.{vendor}.hop.clickbank.net/"

                    # ClickBank has no direct image API — use a placeholder
                    image_url = item.get("imageUrl", "")

                    # Gravity is the virality/popularity score (not reviews)
                    # Map gravity to a pseudo review_count for scoring
                    pseudo_reviews = int(gravity * 10)

                    products.append({
                        "product_id":     vendor,
                        "source":         "clickbank",
                        "name":           name,
                        "description":    item.get("description", "")[:500],
                        "price":          price,
                        "commission_pct": commission,
                        "affiliate_url":  affiliate_url,
                        "image_url":      image_url,
                        "category":       category,
                        "rating":         min(gravity / 20, 5.0),  # normalise gravity → rating
                        "review_count":   pseudo_reviews,
                        "score":          _score(price, commission, 4.0, pseudo_reviews),
                    })
            except Exception as e:
                logger.error("ClickBank category %s error: %s", category, e)

        logger.info("ClickBank: scraped %d products", len(products))
        return products
    except Exception as e:
        logger.error("ClickBank scraper failed: %s", e)
        return []


# ── TikTok Shop Affiliate ─────────────────────────────────────────────────────

def _get_tiktok_shop_products() -> list[dict]:
    """
    TikTok Shop affiliate products.
    Requires TikTok Shop Open API credentials (shop seller or affiliate account).
    Products are added manually via MANUAL_PRODUCTS in .env until API access is granted.
    """
    if not config.tiktok_shop_affiliate_id:
        return []
    # TikTok Shop Open API endpoint (requires approval)
    # When you have Shop API access, implement:
    #   GET https://open-api.tiktokglobalshop.com/products/search
    # For now, return empty — add products manually via MANUAL_PRODUCTS
    logger.debug("TikTok Shop API not yet configured — add products via MANUAL_PRODUCTS")
    return []


# ── Manual products ───────────────────────────────────────────────────────────

def _get_manual_products() -> list[dict]:
    products = []
    for p in config.manual_products:
        if not p.get("name") or not p.get("affiliate_url"):
            continue
        products.append({
            "product_id":     f"manual_{p['name'][:30].replace(' ', '_').lower()}",
            "source":         "manual",
            "name":           p["name"],
            "description":    p.get("description", ""),
            "price":          p.get("price", 0.0),
            "commission_pct": p.get("commission_pct", 0.0),
            "affiliate_url":  p["affiliate_url"],
            "image_url":      p.get("image_url", ""),
            "category":       p.get("category", "general"),
            "rating":         5.0,
            "review_count":   100,
            "score":          _score(
                p.get("price", 0),
                p.get("commission_pct", 0),
                5.0, 100
            ),
        })
    if products:
        logger.info("Manual: loaded %d products", len(products))
    return products


# ── Public interface ──────────────────────────────────────────────────────────

def get_products() -> list[dict]:
    """
    Fetch all affiliate products from all configured sources.
    Returns a flat list sorted by revenue score descending.
    """
    all_products = []
    all_products += _get_amazon_products()
    all_products += _get_clickbank_products()
    all_products += _get_tiktok_shop_products()
    all_products += _get_manual_products()
    all_products.sort(key=lambda p: p["score"], reverse=True)
    logger.info("Products total: %d from all sources", len(all_products))
    return all_products
