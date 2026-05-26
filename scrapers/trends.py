"""
Google Trends scraper — uses the public RSS feed directly.

pytrends has repeated breakage with Google's API changes.
The RSS feed at trends.google.com/trends/trendingsearches/daily/rss
is stable, requires no API key, and returns the same data.
"""
import logging
import re
import xml.etree.ElementTree as ET

import requests

logger = logging.getLogger(__name__)

RSS_URL = "https://trends.google.com/trending/rss"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def _parse_traffic(traffic_str: str) -> int:
    """Convert '500K+' or '1M+' to an integer."""
    if not traffic_str:
        return 0
    s = traffic_str.replace("+", "").replace(",", "").strip()
    multipliers = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
    for suffix, mult in multipliers.items():
        if s.upper().endswith(suffix):
            try:
                return int(float(s[:-1]) * mult)
            except ValueError:
                return 0
    try:
        return int(s)
    except ValueError:
        return 0


def get_trending(geo: str = "US", max_results: int = 30) -> list[dict]:
    """Fetch daily trending searches from Google Trends RSS (no API key needed)."""
    try:
        resp = requests.get(RSS_URL, params={"geo": geo}, headers=HEADERS, timeout=20)
        resp.raise_for_status()

        root = ET.fromstring(resp.content)
        ns = {"ht": "https://trends.google.com/trending/rss"}

        trends = []
        channel = root.find("channel")
        if channel is None:
            logger.warning("Google Trends RSS: no channel element found")
            return []

        for rank, item in enumerate(channel.findall("item")):
            title_el = item.find("title")
            topic = title_el.text.strip() if title_el is not None and title_el.text else ""
            if not topic:
                continue

            # Approximate traffic from <ht:approx_traffic>
            traffic_el = item.find("ht:approx_traffic", ns)
            traffic = _parse_traffic(traffic_el.text if traffic_el is not None else "")

            # Score: traffic-weighted, then by rank
            score = traffic / 1000 if traffic else (max_results - rank) * 100

            trends.append({
                "topic": topic[:200],
                "source": "google_trends",
                "score": score,
                "raw_data": {
                    "rank": rank + 1,
                    "term": topic,
                    "geo": geo,
                    "approx_traffic": traffic,
                },
            })

            if len(trends) >= max_results:
                break

        logger.info("Google Trends: collected %d trending searches", len(trends))
        return trends

    except Exception as e:
        logger.error("Google Trends scraper failed: %s", e)
        return []
