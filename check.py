import db
import config as cfg

db.init_db()
print("DB init OK")
print("Config loaded OK")
print("Model:", cfg.config.groq_model)

# Google Trends needs no API key — test it now
from scrapers import trends as gt
results = gt.get_trending()
print(f"\nGoogle Trends: {len(results)} topics fetched")
for r in results[:5]:
    print(f"  [{r['score']:.0f}] {r['topic']}")

# Show what's missing
print("\n--- Credential status ---")
c = cfg.config
print("Groq:     ", "SET" if c.groq_api_key and "PASTE" not in c.groq_api_key else "MISSING")
print("Reddit:   ", "SET" if c.reddit_client_id and "PASTE" not in c.reddit_client_id else "MISSING")
print("YouTube:  ", "SET" if c.youtube_api_key else "not configured")
print("TikTok:   ", "SET" if c.tiktok_client_key else "not configured")
print("Amazon:   ", "SET" if c.amazon_access_key else "not configured")
print("ClickBank:", "SET" if c.clickbank_api_key else "not configured")
