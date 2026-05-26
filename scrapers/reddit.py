import logging
import praw
from config import config

logger = logging.getLogger(__name__)

# Subreddits to mine for viral trends (read-only, not posting here)
TREND_SUBREDDITS = [
    "worldnews", "technology", "science", "todayilearned",
    "AskReddit", "interestingasfuck", "mildlyinteresting",
    "Futurology", "dataisbeautiful", "explainlikeimfive",
]


def _make_client():
    return praw.Reddit(
        client_id=config.reddit_client_id,
        client_secret=config.reddit_client_secret,
        username=config.reddit_username,
        password=config.reddit_password,
        user_agent=config.reddit_user_agent,
    )


def _top_comments(post, limit: int = 5) -> list[dict]:
    """Fetch top-scored comments — reveals WHY the post is engaging."""
    try:
        post.comments.replace_more(limit=0)
        comments = []
        for c in sorted(post.comments.list(), key=lambda x: getattr(x, "score", 0), reverse=True)[:limit]:
            body = getattr(c, "body", "").strip()
            if body and body != "[deleted]" and body != "[removed]" and len(body) > 10:
                comments.append({"body": body[:250], "score": c.score})
        return comments
    except Exception as e:
        logger.debug("Comment fetch failed for %s: %s", post.id, e)
        return []


def get_trending(limit_per_sub: int = 5) -> list[dict]:
    if not (config.reddit_client_id and config.reddit_client_secret):
        logger.warning("Reddit credentials missing — skipping scraper")
        return []

    try:
        reddit = _make_client()
        trends = []
        for sub_name in TREND_SUBREDDITS:
            try:
                for post in reddit.subreddit(sub_name).hot(limit=limit_per_sub):
                    if post.stickied or not post.title:
                        continue

                    # Engagement velocity: newer posts with high scores signal faster spread
                    import time
                    post_age_hours = max(1, (time.time() - post.created_utc) / 3600)
                    velocity = (post.score + post.num_comments * 3) / post_age_hours

                    trends.append({
                        "topic": post.title[:200],
                        "source": f"reddit/{sub_name}",
                        "score": post.score + post.num_comments * 3,
                        "velocity": velocity,
                        "raw_data": {
                            "id": post.id,
                            "title": post.title,
                            "selftext": post.selftext[:600] if post.is_self else "",
                            "url": post.url,
                            "subreddit": sub_name,
                            "upvotes": post.score,
                            "comments": post.num_comments,
                            "upvote_ratio": post.upvote_ratio,
                            "age_hours": round(post_age_hours, 1),
                            "velocity": round(velocity, 1),
                            # top_comments fetched later (only for selected trends, saves API calls)
                            "top_comments": [],
                        },
                    })
            except Exception as e:
                logger.error("Reddit r/%s scrape error: %s", sub_name, e)

        logger.info("Reddit: collected %d posts", len(trends))
        return trends

    except Exception as e:
        logger.error("Reddit scraper failed: %s", e)
        return []


def enrich_with_comments(post_id: str) -> list[dict]:
    """
    Fetch top comments for a specific post ID.
    Called only on selected trends (not all scraped posts) to conserve API quota.
    """
    if not (config.reddit_client_id and config.reddit_client_secret):
        return []
    try:
        reddit = _make_client()
        submission = reddit.submission(id=post_id)
        comments = _top_comments(submission, limit=5)
        logger.debug("Enriched post %s with %d comments", post_id, len(comments))
        return comments
    except Exception as e:
        logger.error("Comment enrichment failed for %s: %s", post_id, e)
        return []
