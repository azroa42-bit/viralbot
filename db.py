import sqlite3
import json
import logging
from config import config

logger = logging.getLogger(__name__)

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS trends (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic       TEXT NOT NULL,
    source      TEXT NOT NULL,
    score       REAL DEFAULT 0,
    raw_data    TEXT,
    analysis    TEXT,                          -- cached JSON from pipeline/analyzer.py
    scraped_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS posts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    trend_id         INTEGER REFERENCES trends(id),
    platform         TEXT NOT NULL,
    content_type     TEXT NOT NULL,
    title            TEXT,
    content          TEXT,
    video_path       TEXT,
    angle_used       INTEGER DEFAULT 0,        -- which unique_angle index was used (0/1/2)
    platform_post_id TEXT,
    status           TEXT DEFAULT 'pending',
    error            TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    posted_at        TIMESTAMP,
    UNIQUE(trend_id, platform)
);
"""

# Columns added after initial release — applied via migration, harmless if they exist
_MIGRATIONS = [
    "ALTER TABLE trends ADD COLUMN analysis TEXT",
    "ALTER TABLE posts ADD COLUMN angle_used INTEGER DEFAULT 0",
]


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(config.db_path))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript(_SCHEMA)
    # Run migrations silently (fail = column already exists, which is fine)
    for sql in _MIGRATIONS:
        try:
            conn.execute(sql)
            conn.commit()
        except Exception:
            pass
    conn.close()
    logger.info("Database ready at %s", config.db_path)


# ── Trends ────────────────────────────────────────────────────────────────────

def trend_seen_today(topic: str, source: str) -> bool:
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM trends WHERE topic=? AND source=? AND DATE(scraped_at)=DATE('now')",
        (topic, source),
    ).fetchone()
    conn.close()
    return row is not None


def insert_trend(topic: str, source: str, score: float, raw_data: dict) -> int:
    conn = get_conn()
    cur = conn.execute(
        "INSERT INTO trends (topic, source, score, raw_data) VALUES (?,?,?,?)",
        (topic, source, score, json.dumps(raw_data)),
    )
    conn.commit()
    trend_id = cur.lastrowid
    conn.close()
    return trend_id


def save_analysis(trend_id: int, analysis: dict):
    """Cache the trend analysis so we don't re-analyze on the next run."""
    conn = get_conn()
    conn.execute(
        "UPDATE trends SET analysis=? WHERE id=?",
        (json.dumps(analysis), trend_id),
    )
    conn.commit()
    conn.close()


def load_analysis(trend_id: int) -> dict | None:
    """Return cached analysis for a trend, or None if not yet analyzed."""
    conn = get_conn()
    row = conn.execute("SELECT analysis FROM trends WHERE id=?", (trend_id,)).fetchone()
    conn.close()
    if row and row["analysis"]:
        return json.loads(row["analysis"])
    return None


def get_unposted_trends(platform: str, limit: int = 10) -> list:
    """Trends that haven't been posted to `platform` yet today."""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT t.id, t.topic, t.source, t.score, t.raw_data, t.analysis
        FROM trends t
        WHERE DATE(t.scraped_at) = DATE('now')
          AND NOT EXISTS (
              SELECT 1 FROM posts p
              WHERE p.trend_id = t.id AND p.platform = ?
          )
        ORDER BY t.score DESC
        LIMIT ?
        """,
        (platform, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def next_unused_angle(trend_id: int) -> int:
    """Return the lowest angle index (0/1/2) not yet used for this trend across all platforms."""
    conn = get_conn()
    used = {
        row[0]
        for row in conn.execute(
            "SELECT angle_used FROM posts WHERE trend_id=?", (trend_id,)
        ).fetchall()
    }
    conn.close()
    for i in range(3):
        if i not in used:
            return i
    return 0  # all angles used — cycle back


# ── Posts ─────────────────────────────────────────────────────────────────────

def create_post(trend_id: int, platform: str, content_type: str,
                title: str, content: str, video_path: str = None,
                angle_used: int = 0) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT OR IGNORE INTO posts
           (trend_id, platform, content_type, title, content, video_path, angle_used)
           VALUES (?,?,?,?,?,?,?)""",
        (trend_id, platform, content_type, title, content, video_path, angle_used),
    )
    conn.commit()
    post_id = cur.lastrowid
    conn.close()
    return post_id


def mark_post(post_id: int, status: str, platform_post_id: str = None, error: str = None):
    conn = get_conn()
    conn.execute(
        """UPDATE posts SET status=?, platform_post_id=?, error=?,
           posted_at=CASE WHEN ?='posted' THEN CURRENT_TIMESTAMP ELSE posted_at END
           WHERE id=?""",
        (status, platform_post_id, error, status, post_id),
    )
    conn.commit()
    conn.close()
