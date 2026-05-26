import logging
import praw
from config import config

logger = logging.getLogger(__name__)


def _affiliate_footer(topic: str) -> str:
    """Return a footer with a relevant affiliate link if the topic matches a keyword."""
    if not config.affiliate_links:
        return ""
    topic_lower = topic.lower()
    for keyword, url in config.affiliate_links.items():
        if keyword.lower() in topic_lower:
            return f"\n\n---\n*Related resource: {url}*"
    return ""

def _make_client():
    return praw.Reddit(
        client_id=config.reddit_client_id,
        client_secret=config.reddit_client_secret,
        username=config.reddit_username,
        password=config.reddit_password,
        user_agent=config.reddit_user_agent,
    )


def post(title: str, body: str, subreddit: str = None) -> str | None:
    """
    Submit a self-post. Returns the new post's fullname (e.g. 't3_abc123')
    or None on failure.
    """
    if not all([config.reddit_client_id, config.reddit_client_secret,
                config.reddit_username, config.reddit_password]):
        logger.warning("Reddit credentials incomplete — skipping publish")
        return None

    target = subreddit or config.reddit_post_subreddits[0]
    try:
        reddit = _make_client()
        sub = reddit.subreddit(target)
        full_body = body + _affiliate_footer(title)
        submission = sub.submit(title=title, selftext=full_body)
        logger.info("Posted to r/%s: %s → %s", target, title[:60], submission.fullname)
        return submission.fullname
    except Exception as e:
        logger.error("Reddit post to r/%s failed: %s", target, e)
        return None


def post_to_all(title: str, body: str) -> list[str]:
    """Post to every configured subreddit, return list of post IDs."""
    results = []
    for sub in config.reddit_post_subreddits:
        post_id = post(title, body, subreddit=sub)
        if post_id:
            results.append(post_id)
    return results
