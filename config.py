import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Pexels (free — pexels.com/api → Get Started) ────────────────────────
    pexels_api_key: str = os.getenv("PEXELS_API_KEY", "")

    # ── Groq (free tier — console.groq.com) ─────────────────────────────────
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = "llama-3.3-70b-versatile"
    claude_model: str = "llama-3.3-70b-versatile"  # legacy alias used in logs
    # Legacy Gemini key kept in case user switches back
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")

    # ── Instagram ─────────────────────────────────────────────────────────────
    # Setup: developers.facebook.com → create app → Instagram Graph API product
    # Required permissions: instagram_basic, instagram_content_publish, pages_read_engagement
    # Generate token via Graph API Explorer, then exchange for long-lived token (60 days).
    # Get Instagram User ID: GET /me/accounts → {page_id} → GET /{page_id}?fields=instagram_business_account
    instagram_access_token: str = os.getenv("INSTAGRAM_ACCESS_TOKEN", "")
    instagram_user_id: str = os.getenv("INSTAGRAM_USER_ID", "")
    instagram_app_id: str = os.getenv("INSTAGRAM_APP_ID", "")
    instagram_app_secret: str = os.getenv("INSTAGRAM_APP_SECRET", "")
    instagram_token_file: str = "instagram_token.json"

    # ── YouTube ───────────────────────────────────────────────────────────────
    youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "")
    youtube_client_secrets_file: str = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")
    youtube_token_file: str = "token.json"

    # ── TikTok ────────────────────────────────────────────────────────────────
    # Register at: https://developers.tiktok.com/
    # Required scopes: video.upload, user.info.basic
    tiktok_client_key: str = os.getenv("TIKTOK_CLIENT_KEY", "")
    tiktok_client_secret: str = os.getenv("TIKTOK_CLIENT_SECRET", "")
    tiktok_token_file: str = "tiktok_token.json"
    # TikTok Shop affiliate: set to your creator affiliate ID
    tiktok_shop_affiliate_id: str = os.getenv("TIKTOK_SHOP_AFFILIATE_ID", "")

    # ── Amazon PA API ─────────────────────────────────────────────────────────
    # Register at: affiliate-program.amazon.com → get PA API access after 3 sales
    amazon_access_key: str = os.getenv("AMAZON_ACCESS_KEY", "")
    amazon_secret_key: str = os.getenv("AMAZON_SECRET_KEY", "")
    amazon_associate_tag: str = os.getenv("AMAZON_ASSOCIATE_TAG", "")
    amazon_country: str = os.getenv("AMAZON_COUNTRY", "US")

    # ── ClickBank ─────────────────────────────────────────────────────────────
    # Register at: clickbank.com → Settings → Edit Account → API Keys
    clickbank_api_key: str = os.getenv("CLICKBANK_API_KEY", "")
    clickbank_dev_key: str = os.getenv("CLICKBANK_DEV_KEY", "")
    clickbank_affiliate_id: str = os.getenv("CLICKBANK_AFFILIATE_ID", "")

    # ── DB & video ────────────────────────────────────────────────────────────
    db_path: Path = Path(os.getenv("DB_PATH", "viralbot.db"))
    video_output_dir: Path = Path(os.getenv("VIDEO_OUTPUT_DIR", "videos"))

    # ── Pipeline ──────────────────────────────────────────────────────────────
    run_interval_hours: int = int(os.getenv("RUN_INTERVAL_HOURS", "2"))
    max_trends_per_run: int = int(os.getenv("MAX_TRENDS_PER_RUN", "3"))
    product_interval_hours: int = int(os.getenv("PRODUCT_INTERVAL_HOURS", "6"))
    max_products_per_run: int = int(os.getenv("MAX_PRODUCTS_PER_RUN", "3"))

    # ── Affiliate links ───────────────────────────────────────────────────────
    # Appended to product post descriptions when topic matches a keyword
    # Format: "keyword:url,keyword:url"
    affiliate_links: dict = {
        k.strip(): v.strip()
        for pair in os.getenv("AFFILIATE_LINKS", "").split(",")
        if ":" in pair
        for k, v in [pair.split(":", 1)]
    }

    # ── Manual products ───────────────────────────────────────────────────────
    # Any affiliate product from any network. One product per line in .env:
    # MANUAL_PRODUCTS=Name|description|affiliate_url|image_url|price|commission_pct|category
    # Separate multiple with double-pipe: ||
    @property
    def manual_products(self) -> list[dict]:
        raw = os.getenv("MANUAL_PRODUCTS", "")
        if not raw.strip():
            return []
        products = []
        for entry in raw.split("||"):
            parts = [p.strip() for p in entry.split("|")]
            if len(parts) >= 3:
                products.append({
                    "name":           parts[0] if len(parts) > 0 else "",
                    "description":    parts[1] if len(parts) > 1 else "",
                    "affiliate_url":  parts[2] if len(parts) > 2 else "",
                    "image_url":      parts[3] if len(parts) > 3 else "",
                    "price":          float(parts[4]) if len(parts) > 4 else 0.0,
                    "commission_pct": float(parts[5]) if len(parts) > 5 else 0.0,
                    "category":       parts[6] if len(parts) > 6 else "general",
                })
        return products

    def __init__(self):
        self.video_output_dir.mkdir(exist_ok=True)


config = Config()
