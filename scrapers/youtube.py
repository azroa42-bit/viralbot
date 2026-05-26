import logging
from googleapiclient.discovery import build
from config import config

logger = logging.getLogger(__name__)

# Categories with CPM multipliers.
# A 10M-view finance video earns more than a 10M-view gaming video — the
# scoring formula reflects this so we surface the most revenue-relevant topics.
# Approximate real-world YouTube CPM tiers (USD per 1,000 views):
#   Finance/investing: $15-30  → mult 3.0
#   Tech/software:     $8-15   → mult 2.5
#   Education:         $5-12   → mult 2.2
#   Howto & Style:     $4-10   → mult 2.0
#   News/Politics:     $3-8    → mult 1.6
#   People & Blogs:    $2-5    → mult 1.3
#   Entertainment:     $2-4    → mult 1.0  (baseline)
CATEGORIES: dict[str, dict] = {
    "28": {"name": "Science & Technology", "cpm_mult": 2.5},
    "27": {"name": "Education",            "cpm_mult": 2.2},
    "26": {"name": "Howto & Style",        "cpm_mult": 2.0},
    "25": {"name": "News & Politics",      "cpm_mult": 1.6},
    "22": {"name": "People & Blogs",       "cpm_mult": 1.3},
    "24": {"name": "Entertainment",        "cpm_mult": 1.0},
}


def _engagement_rate(views: int, likes: int, comments: int) -> float:
    if views == 0:
        return 0.0
    return round((likes + comments) / views * 100, 3)


def get_trending(region_code: str = "US", max_per_category: int = 10) -> list[dict]:
    if not config.youtube_api_key:
        logger.warning("YouTube API key missing — skipping scraper")
        return []

    try:
        yt = build("youtube", "v3", developerKey=config.youtube_api_key)
        trends = []
        seen_ids = set()

        for cat_id, cat in CATEGORIES.items():
            cat_name = cat["name"]
            cpm_mult = cat["cpm_mult"]
            try:
                resp = yt.videos().list(
                    part="snippet,statistics,contentDetails",
                    chart="mostPopular",
                    regionCode=region_code,
                    videoCategoryId=cat_id,
                    maxResults=max_per_category,
                ).execute()

                for item in resp.get("items", []):
                    vid_id = item["id"]
                    if vid_id in seen_ids:
                        continue
                    seen_ids.add(vid_id)

                    snippet = item["snippet"]
                    stats = item.get("statistics", {})
                    views   = int(stats.get("viewCount",    0))
                    likes   = int(stats.get("likeCount",    0))
                    comments = int(stats.get("commentCount", 0))
                    eng_rate = _engagement_rate(views, likes, comments)

                    # Revenue-first scoring:
                    #   Primary driver  = raw view scale (10M views is 10M views regardless of eng%)
                    #   Secondary boost = actual engagement count (not rate — a 0.1% rate on 10M
                    #                     is still 10k likes, which is meaningful)
                    #   Multiplier      = CPM tier (finance/tech content earns 2-3× per view)
                    base_score = views / 1000 + likes * 1.5 + comments * 4
                    score = base_score * cpm_mult

                    trends.append({
                        "topic": snippet["title"][:200],
                        "source": f"youtube/{cat_name.lower().replace(' ', '_')}",
                        "score": score,
                        "raw_data": {
                            "video_id": vid_id,
                            "title": snippet["title"],
                            "description": snippet.get("description", "")[:800],
                            "channel": snippet.get("channelTitle", ""),
                            "category": cat_name,
                            "cpm_multiplier": cpm_mult,
                            "views": views,
                            "likes": likes,
                            "comments": comments,
                            "engagement_rate_pct": eng_rate,
                            "published_at": snippet.get("publishedAt", ""),
                            "duration": item.get("contentDetails", {}).get("duration", ""),
                            "top_comments": [],
                        },
                    })
            except Exception as e:
                logger.error("YouTube category %s scrape error: %s", cat_id, e)

        logger.info("YouTube: collected %d trending videos", len(trends))
        return trends

    except Exception as e:
        logger.error("YouTube scraper failed: %s", e)
        return []


def enrich_with_comments(video_id: str, max_comments: int = 5) -> list[dict]:
    """
    Fetch top comments for a video. Called only on selected trends to conserve API quota.
    YouTube commentThreads.list costs 1 unit per call.
    """
    if not config.youtube_api_key:
        return []
    try:
        yt = build("youtube", "v3", developerKey=config.youtube_api_key)
        resp = yt.commentThreads().list(
            part="snippet",
            videoId=video_id,
            order="relevance",
            maxResults=max_comments,
            textFormat="plainText",
        ).execute()

        comments = []
        for item in resp.get("items", []):
            top = item["snippet"]["topLevelComment"]["snippet"]
            text = top.get("textDisplay", "").strip()
            likes = top.get("likeCount", 0)
            if text:
                comments.append({"body": text[:250], "score": likes})
        logger.debug("Enriched YouTube %s with %d comments", video_id, len(comments))
        return comments
    except Exception as e:
        logger.error("YouTube comment fetch failed for %s: %s", video_id, e)
        return []
