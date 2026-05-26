"""
ViralBot — Viral Content Creator
Pipeline per cycle:
  1. Scrape  — Reddit hot posts + YouTube trending + Google Trends
  2. Aggregate — deduplicate + cross-platform score boost, pick top N
  3. Enrich  — fetch top comments for selected trends (reveals WHY it's viral)
  4. Analyze — Claude identifies virality driver + 3 unique angles + what to avoid
  5. Generate — platform-specific content using the analysis (Reddit + YouTube)
  6. Produce — edge-tts voiceover + Pillow slides + moviepy → MP4 Short
  7. Publish — post to Reddit, upload to YouTube
  8. Log     — SQLite tracks everything, prevents duplicates

Run once:   python main.py --once
Scheduler:  python main.py
"""
import argparse
import json
import logging
import sys
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

import db
from config import config
from pipeline import aggregator
from pipeline import analyzer
from pipeline import generator
from pipeline.video import create_short
from publishers import reddit as reddit_pub
from publishers import youtube as youtube_pub
from scrapers import reddit as reddit_scraper
from scrapers import trends as trends_scraper
from scrapers import youtube as youtube_scraper

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


def main():
    parser = argparse.ArgumentParser(description="ViralBot — Viral Content Creator")
    parser.add_argument("--once", action="store_true", help="Run pipeline once and exit")
    args = parser.parse_args()

    db.init_db()
    logger.info(
        "ViralBot ready | interval=%dh | max_trends=%d | model=%s",
        config.run_interval_hours,
        config.max_trends_per_run,
        config.claude_model,
    )

    if args.once:
        run_pipeline()
        return

    scheduler = BlockingScheduler()
    scheduler.add_job(
        run_pipeline,
        trigger=IntervalTrigger(hours=config.run_interval_hours),
        next_run_time=datetime.now(),   # run immediately on first start
        max_instances=1,
        misfire_grace_time=300,
    )
    logger.info("Scheduler running — next cycle in %dh. Ctrl+C to stop.", config.run_interval_hours)
    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("ViralBot stopped.")


if __name__ == "__main__":
    main()
