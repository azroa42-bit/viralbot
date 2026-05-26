import logging
from pytrends.request import TrendReq

logger = logging.getLogger(__name__)


def get_trending(geo: str = "US") -> list[dict]:
    """Pull daily trending searches from Google Trends (no API key required)."""
    try:
        pytrends = TrendReq(hl="en-US", tz=0, timeout=(10, 25))
        df = pytrends.trending_searches(pn=geo.lower())
        trends = []

        for rank, (_, row) in enumerate(df.iterrows()):
            topic = str(row.iloc[0]).strip()
            if not topic:
                continue
            # Score by rank: #1 trend gets highest score
            score = (len(df) - rank) * 100
            trends.append({
                "topic": topic[:200],
                "source": "google_trends",
                "score": score,
                "raw_data": {"rank": rank + 1, "term": topic, "geo": geo},
            })

        logger.info("Google Trends: collected %d trending searches", len(trends))
        return trends

    except Exception as e:
        logger.error("Google Trends scraper failed: %s", e)
        return []
