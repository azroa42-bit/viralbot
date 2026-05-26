"""
Dry run — full pipeline without posting anything.

Scrape -> Aggregate -> Analyze -> Generate Reddit post + YouTube script -> Create video
Shows exactly what the bot would publish.
"""
import logging
import sys
logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger("dry_run")

import db
from scrapers import trends as trends_scraper
from scrapers import youtube as youtube_scraper
from pipeline import aggregator, analyzer, generator
from pipeline.video import create_short

db.init_db()

# ── 1. Scrape ────────────────────────────────────────────────────────────────
logger.info("Scraping trends...")
raw = trends_scraper.get_trending()
yt_raw = youtube_scraper.get_trending(max_per_category=5)
raw += yt_raw
logger.info("Total signals: %d", len(raw))

if not raw:
    logger.error("No trends scraped. Check internet connection.")
    sys.exit(1)

# ── 2. Aggregate ─────────────────────────────────────────────────────────────
logger.info("Aggregating top trends...")
top = aggregator.aggregate_and_store(raw, max_trends=1)  # just 1 for the test
if not top:
    logger.info("All trends already seen today — picking best unseen for dry run...")
    # Skip low-quality and already-seen, pick highest-scored remaining
    from pipeline.aggregator import _is_low_quality
    import db as _db
    candidates = [
        t for t in sorted(raw, key=lambda x: x["score"], reverse=True)
        if not _is_low_quality(t["topic"])
        and not _db.trend_seen_today(t["topic"], t["source"])
    ]
    if not candidates:
        # Last resort: just take the top raw trend regardless
        candidates = sorted(raw, key=lambda x: x["score"], reverse=True)
    best = candidates[0]
    top = [best]

trend = top[0]
logger.info("Selected trend: [%.0f] %s", trend["score"], trend["topic"])

# ── 3. Analyze ───────────────────────────────────────────────────────────────
logger.info("Analyzing virality...")
analysis = analyzer.analyze(trend)
if analysis:
    logger.info("  Driver:  %s", analysis["virality_driver"])
    logger.info("  Hook:    %s", analysis["hook_type"])
    logger.info("  Revenue: %s", analysis["revenue_niche"])
    logger.info("  Angle 1: %s", analysis["unique_angles"][0][:80])
else:
    logger.warning("Analysis failed — continuing without it")

# ── 4. Generate content ──────────────────────────────────────────────────────
logger.info("Generating Reddit post...")
reddit_content = generator.generate_reddit_content(trend, analysis, angle_idx=0)
if reddit_content:
    print("\n" + "="*65)
    print("REDDIT POST (would post to r/test)")
    print("="*65)
    print(f"TITLE: {reddit_content['title']}")
    print(f"\n{reddit_content['body']}")

logger.info("Generating YouTube script...")
yt_content = generator.generate_youtube_script(trend, analysis, angle_idx=1)
if yt_content:
    print("\n" + "="*65)
    print("YOUTUBE SHORT")
    print("="*65)
    print(f"TITLE: {yt_content['title']}")
    print(f"TAGS:  {', '.join(yt_content.get('tags', []))}")
    print(f"\nSCRIPT:\n{yt_content['script']}")

# ── 5. Create video ──────────────────────────────────────────────────────────
if yt_content:
    keywords = analysis.get("keywords", []) if analysis else []
    logger.info("Creating video (keywords: %s)...", keywords[:4])
    video_path = create_short(
        title=yt_content["title"],
        script=yt_content["script"],
        output_name="dry_run_test",
        keywords=keywords,
    )
    if video_path:
        print("\n" + "="*65)
        print(f"VIDEO CREATED: {video_path}")
        print(f"Size: {video_path.stat().st_size / 1024 / 1024:.1f} MB")
        print("="*65)
    else:
        logger.warning("Video creation failed")

logger.info("Dry run complete.")
