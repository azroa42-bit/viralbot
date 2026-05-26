"""
Trend Aggregator

Merges trends from all scrapers, then:
1. Deduplicates by topic similarity within this batch
2. Boosts score when the same topic appears across multiple platforms
   (Reddit + YouTube + Google Trends on the same subject = stronger signal)
3. Filters out topics already seen today
4. Returns the top N trends sorted by score
"""
import logging
import re
from db import trend_seen_today, insert_trend

logger = logging.getLogger(__name__)

# Common stop-words to strip before comparing topic similarity
_STOP = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "was", "are", "were", "be", "been",
    "has", "have", "had", "that", "this", "it", "its", "as", "up", "how",
    "what", "when", "who", "why", "new", "more", "just", "about", "after",
}


def _keyword_set(topic: str) -> set[str]:
    """Extract meaningful words from a topic for similarity comparison."""
    words = re.findall(r"[a-z]+", topic.lower())
    return {w for w in words if len(w) > 3 and w not in _STOP}


def _topics_overlap(a: str, b: str, threshold: float = 0.35) -> bool:
    """True if topics share enough keywords to be considered the same subject."""
    ka, kb = _keyword_set(a), _keyword_set(b)
    if not ka or not kb:
        return False
    overlap = len(ka & kb) / min(len(ka), len(kb))
    return overlap >= threshold


def aggregate_and_store(raw_trends: list[dict], max_trends: int) -> list[dict]:
    """
    Merge, boost cross-platform signals, deduplicate, store, and return top N.
    """
    if not raw_trends:
        return []

    # ── Step 1: Within-batch dedup by topic similarity ──────────────
    # Keep the highest-score representative per cluster; accumulate cross-source bonus.
    clusters: list[dict] = []  # list of cluster dicts

    for trend in raw_trends:
        matched = False
        for cluster in clusters:
            if _topics_overlap(trend["topic"], cluster["topic"]):
                # Merge: keep higher-scoring topic text, sum scores
                cluster["score"] += trend["score"] * 0.5   # additive but discounted
                cluster["sources"].add(trend["source"])
                if trend["score"] > cluster["_best_score"]:
                    cluster["topic"] = trend["topic"]
                    cluster["raw_data"] = trend["raw_data"]
                    cluster["_best_score"] = trend["score"]
                matched = True
                break
        if not matched:
            clusters.append({
                **trend,
                "sources": {trend["source"]},
                "_best_score": trend["score"],
            })

    # ── Step 2: Cross-platform score multiplier ──────────────────────
    # A topic trending on 2+ distinct platforms gets a 1.5× boost per extra platform.
    for cluster in clusters:
        distinct_platforms = len({s.split("/")[0] for s in cluster["sources"]})
        if distinct_platforms > 1:
            cluster["score"] *= 1.0 + 0.5 * (distinct_platforms - 1)
            logger.debug(
                "Cross-platform boost ×%.1f for '%s' (sources: %s)",
                1.0 + 0.5 * (distinct_platforms - 1),
                cluster["topic"][:60],
                ", ".join(cluster["sources"]),
            )

    clusters.sort(key=lambda x: x["score"], reverse=True)

    # ── Step 3: Filter already-seen, insert new trends ───────────────
    stored = []
    for cluster in clusters:
        if trend_seen_today(cluster["topic"], cluster["source"]):
            logger.debug("Skipping already-seen: %s", cluster["topic"][:60])
            continue

        trend_id = insert_trend(
            topic=cluster["topic"],
            source=cluster["source"],
            score=cluster["score"],
            raw_data={
                **cluster["raw_data"],
                "all_sources": list(cluster["sources"]),
            },
        )
        stored.append({**cluster, "id": trend_id})

        if len(stored) >= max_trends:
            break

    logger.info(
        "Aggregator: %d new trends stored from %d raw (cross-platform merging applied)",
        len(stored),
        len(raw_trends),
    )
    return stored
