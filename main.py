"""
ViralBot — Viral Content Creator

Two parallel pipelines:

TREND PIPELINE  (every 2h)
  Scrape → Aggregate → Enrich → Analyze → Generate → Video → Post
  Platforms: Reddit, YouTube Shorts

PRODUCT PIPELINE  (every 6h)
  Scan affiliate markets → Score → Generate product content → Product video → Post
  Platforms: YouTube Shorts, TikTok, Reddit
  Sources:   Amazon PA API, ClickBank, TikTok Shop, manual products

Run once:         python main.py --once
Products only:    python main.py --products-only
Trends only:      python main.py --trends-only
Scheduler (both): python main.py
"""
import argparse
import logging
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

import db
from config import config
from pipeline import aggregator, analyzer, generator
from pipeline.video import create_short
from pipeline.product_generator import generate_product_content
from pipeline.product_video import create_product_short
from publishers import reddit as reddit_pub
from publishers import youtube as youtube_pub
from publishers import tiktok as tiktok_pub
from scrapers import reddit as reddit_scraper
from scrapers import trends as trends_scraper
from scrapers import youtube as youtube_scraper
from scrapers.products import get_products

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(name)s — %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("viralbot")


def _enrich_trend(trend: dict) -> dict:
    """
    Fetch top comments for a selected trend so the analyzer has richer context.
    Only called on the small set of trends that made it through aggregation.
    """
    source = trend.get("source", "")
    raw = trend.get("raw_data", {})

    if source.startswith("reddit/"):
        post_id = raw.get("id")
        if post_id:
            comments = reddit_scraper.enrich_with_comments(post_id)
            if comments:
                trend["raw_data"]["top_comments"] = comments
                logger.info("  Enriched Reddit post %s with %d comments", post_id, len(comments))

    elif source.startswith("youtube/"):
        video_id = raw.get("video_id")
        if video_id:
            comments = youtube_scraper.enrich_with_comments(video_id)
            if comments:
                trend["raw_data"]["top_comments"] = comments
                logger.info("  Enriched YouTube video %s with %d comments", video_id, len(comments))

    return trend


def run_pipeline():
    logger.info("=" * 65)
    logger.info("Pipeline run started — %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # ── 1. Scrape ────────────────────────────────────────────────────
    raw = []
    raw += reddit_scraper.get_trending(limit_per_sub=5)
    raw += youtube_scraper.get_trending(max_per_category=8)
    raw += trends_scraper.get_trending()

    if not raw:
        logger.warning("No trends scraped — check API credentials in .env")
        return

    logger.info("Scraped %d raw signals total", len(raw))

    # ── 2. Aggregate ─────────────────────────────────────────────────
    top_trends = aggregator.aggregate_and_store(raw, max_trends=config.max_trends_per_run)
    if not top_trends:
        logger.info("No new trends to process this cycle")
        return

    logger.info("Processing %d new trends", len(top_trends))

    for trend in top_trends:
        trend_id = trend["id"]
        topic_short = trend["topic"][:70]
        sources = ", ".join(trend.get("raw_data", {}).get("all_sources", [trend["source"]]))
        logger.info("── [%d] %s", trend_id, topic_short)
        logger.info("   score=%.0f  sources=%s", trend["score"], sources)

        # ── 3. Enrich with comments ───────────────────────────────────
        trend = _enrich_trend(trend)

        # ── 4. Analyze — understand WHY it's viral ───────────────────
        analysis = db.load_analysis(trend_id)  # use cache if available
        if not analysis:
            analysis = analyzer.analyze(trend)
            if analysis:
                db.save_analysis(trend_id, analysis)
        else:
            logger.info("  Using cached analysis (driver=%s)", analysis.get("virality_driver"))

        if analysis:
            logger.info(
                "  Analysis → driver=%-12s hook=%-16s emotion=%-10s revenue=%s",
                analysis.get("virality_driver", "?"),
                analysis.get("hook_type", "?"),
                analysis.get("target_emotion", "?"),
                analysis.get("revenue_niche", "?"),
            )
            logger.info("  Core topic: %s", analysis.get("core_topic", "?"))
            if analysis.get("revenue_niche") == "HIGH":
                logger.info("  Monetization angle: %s", analysis.get("monetization_angle", "?"))

        # ── 5a. Reddit — unique angle 0 ──────────────────────────────
        reddit_angle = db.next_unused_angle(trend_id)
        reddit_content = generator.generate_reddit_content(trend, analysis, angle_idx=reddit_angle)
        if reddit_content:
            post_id = db.create_post(
                trend_id=trend_id,
                platform="reddit",
                content_type="text",
                title=reddit_content["title"],
                content=reddit_content["body"],
                angle_used=reddit_angle,
            )
            post_ids = reddit_pub.post_to_all(reddit_content["title"], reddit_content["body"])
            platform_id = ",".join(post_ids) if post_ids else None
            status = "posted" if post_ids else "failed"
            error = None if post_ids else "Reddit publish returned no post IDs"
            db.mark_post(post_id, status=status, platform_post_id=platform_id, error=error)
            logger.info("  Reddit → %s  id=%s", status, platform_id or "—")
        else:
            logger.warning("  Reddit → content generation failed")

        # ── 5b. YouTube — unique angle 1 (different from Reddit) ─────
        yt_angle = (reddit_angle + 1) % 3
        yt_content = generator.generate_youtube_script(trend, analysis, angle_idx=yt_angle)
        if yt_content:
            script = yt_content.get("script", "")
            title = yt_content.get("title", topic_short)
            description = yt_content.get("description", "")
            tags = yt_content.get("tags", [])

            # ── 6. Produce video ──────────────────────────────────────
            video_path = create_short(
                title=title,
                script=script,
                output_name=f"trend_{trend_id}",
            )

            post_id = db.create_post(
                trend_id=trend_id,
                platform="youtube",
                content_type="video",
                title=title,
                content=script,
                video_path=str(video_path) if video_path else None,
                angle_used=yt_angle,
            )

            # ── 7. Publish to YouTube ─────────────────────────────────
            if video_path:
                video_id = youtube_pub.upload_short(
                    video_path=str(video_path),
                    title=title,
                    description=description,
                    tags=tags,
                )
                status = "posted" if video_id else "failed"
                error = None if video_id else "YouTube upload returned no video ID"
                db.mark_post(post_id, status=status, platform_post_id=video_id, error=error)
                logger.info("  YouTube → %s  id=%s", status, video_id or "—")
            else:
                db.mark_post(post_id, status="failed", error="Video creation failed")
                logger.warning("  YouTube → video creation failed, upload skipped")
        else:
            logger.warning("  YouTube → script generation failed")

    logger.info("Pipeline run complete.")
    logger.info("=" * 65)


def run_product_pipeline():
    """
    Affiliate product pipeline:
    1. Scan Amazon, ClickBank, TikTok Shop, and manual products
    2. Upsert into DB, pick unposted ones sorted by revenue score
    3. Generate product content (PAS or Hook-Proof-CTA script)
    4. Produce product video with image overlay
    5. Post to YouTube Shorts, TikTok, and Reddit
    """
    logger.info("=" * 65)
    logger.info("Product pipeline started — %s", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # ── 1. Scan ───────────────────────────────────────────────────────────────
    all_products = get_products()
    if not all_products:
        logger.warning("No products scraped — configure API keys or MANUAL_PRODUCTS in .env")
        return

    # ── 2. Upsert into DB and pick unposted ───────────────────────────────────
    for p in all_products:
        db.upsert_product(
            product_id=p["product_id"], source=p["source"],
            name=p["name"], description=p.get("description", ""),
            price=p.get("price", 0), commission_pct=p.get("commission_pct", 0),
            affiliate_url=p.get("affiliate_url", ""),
            image_url=p.get("image_url", ""),
            category=p.get("category", ""),
            rating=p.get("rating", 0), review_count=p.get("review_count", 0),
            score=p.get("score", 0),
        )

    # Get products not yet posted to YouTube (primary platform check)
    unposted = db.get_unposted_products("youtube", limit=config.max_products_per_run)
    if not unposted:
        logger.info("No new products to promote this cycle")
        return

    logger.info("Promoting %d products", len(unposted))

    for product in unposted:
        product_db_id = product["id"]
        logger.info("── Product: %s ($%.2f, %.0f%% commission)",
                    product["name"][:60], product.get("price", 0),
                    product.get("commission_pct", 0))

        # ── 3. Generate content ────────────────────────────────────────────────
        content = generate_product_content(product)
        if not content:
            logger.warning("  Content generation failed — skipping")
            continue

        script      = content.get("script", "")
        video_title = content.get("video_title", product["name"])
        video_desc  = content.get("video_description", "")
        tags        = content.get("tags", [])

        # ── 4. Produce video ───────────────────────────────────────────────────
        video_path = create_product_short(
            product=product,
            script=script,
            output_name=f"product_{product_db_id}",
        )

        # ── 5a. YouTube ────────────────────────────────────────────────────────
        yt_post_id = db.create_product_post(
            product_db_id=product_db_id, platform="youtube",
            title=video_title, content=script,
            video_path=str(video_path) if video_path else None,
        )
        if video_path:
            # Append affiliate link to description
            full_desc = f"{video_desc}\n\nGet it here: {product.get('affiliate_url', '')}"
            yt_id = youtube_pub.upload_short(
                video_path=str(video_path),
                title=video_title,
                description=full_desc,
                tags=tags,
            )
            status = "posted" if yt_id else "failed"
            db.mark_product_post(yt_post_id, status=status, platform_post_id=yt_id,
                                 error=None if yt_id else "YouTube upload failed")
            logger.info("  YouTube → %s  id=%s", status, yt_id or "—")
        else:
            db.mark_product_post(yt_post_id, status="failed", error="Video creation failed")

        # ── 5b. TikTok ─────────────────────────────────────────────────────────
        tk_post_id = db.create_product_post(
            product_db_id=product_db_id, platform="tiktok",
            title=video_title, content=script,
            video_path=str(video_path) if video_path else None,
        )
        if video_path:
            tiktok_desc = (
                f"{video_desc}\n\nGet it: {product.get('affiliate_url', '')}"
                f"\n\n#TikTokShop #affiliate #fyp"
            )
            tk_id = tiktok_pub.upload_video(
                video_path=str(video_path),
                title=video_title,
                description=tiktok_desc,
                privacy="SELF_ONLY",   # change to PUBLIC_TO_EVERYONE when ready
            )
            status = "posted" if tk_id else "failed"
            db.mark_product_post(tk_post_id, status=status, platform_post_id=tk_id,
                                 error=None if tk_id else "TikTok upload failed")
            logger.info("  TikTok → %s  id=%s", status, tk_id or "—")
        else:
            db.mark_product_post(tk_post_id, status="failed", error="Video creation failed")

        # ── 5c. Reddit ─────────────────────────────────────────────────────────
        reddit_title = content.get("reddit_title", video_title)
        reddit_body  = content.get("reddit_body", "")
        if reddit_body:
            rd_post_id = db.create_product_post(
                product_db_id=product_db_id, platform="reddit",
                title=reddit_title, content=reddit_body,
            )
            post_ids = reddit_pub.post_to_all(reddit_title, reddit_body)
            platform_id = ",".join(post_ids) if post_ids else None
            status = "posted" if post_ids else "failed"
            db.mark_product_post(rd_post_id, status=status, platform_post_id=platform_id,
                                 error=None if post_ids else "Reddit post failed")
            logger.info("  Reddit → %s  id=%s", status, platform_id or "—")

    logger.info("Product pipeline complete.")
    logger.info("=" * 65)


def main():
    parser = argparse.ArgumentParser(description="ViralBot — Viral Content Creator")
    parser.add_argument("--once",          action="store_true", help="Run both pipelines once and exit")
    parser.add_argument("--trends-only",   action="store_true", help="Run trend pipeline once and exit")
    parser.add_argument("--products-only", action="store_true", help="Run product pipeline once and exit")
    args = parser.parse_args()

    db.init_db()
    logger.info(
        "ViralBot ready | trends=%dh | products=%dh | model=%s",
        config.run_interval_hours,
        config.product_interval_hours,
        config.claude_model,
    )

    if args.trends_only:
        run_pipeline()
        return
    if args.products_only:
        run_product_pipeline()
        return
    if args.once:
        run_pipeline()
        run_product_pipeline()
        return

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_pipeline,
        trigger=IntervalTrigger(hours=config.run_interval_hours),
        next_run_time=datetime.now(),
        max_instances=1,
        misfire_grace_time=300,
        id="trends",
    )
    scheduler.add_job(
        run_product_pipeline,
        trigger=IntervalTrigger(hours=config.product_interval_hours),
        next_run_time=datetime.now(),
        max_instances=1,
        misfire_grace_time=300,
        id="products",
    )
    logger.info(
        "Both pipelines running — trends every %dh, products every %dh. Ctrl+C to stop.",
        config.run_interval_hours, config.product_interval_hours,
    )
    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("ViralBot stopped.")


if __name__ == "__main__":
    main()
