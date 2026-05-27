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
    analysis    TEXT,
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
    angle_used       INTEGER DEFAULT 0,
    platform_post_id TEXT,
    status           TEXT DEFAULT 'pending',
    error            TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    posted_at        TIMESTAMP,
    UNIQUE(trend_id, platform)
);

CREATE TABLE IF NOT EXISTS products (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id     TEXT NOT NULL,
    source         TEXT NOT NULL,   -- amazon | clickbank | tiktok_shop | manual
    name           TEXT NOT NULL,
    description    TEXT,
    price          REAL DEFAULT 0,
    commission_pct REAL DEFAULT 0,
    affiliate_url  TEXT,
    image_url      TEXT,
    category       TEXT,
    rating         REAL DEFAULT 0,
    review_count   INTEGER DEFAULT 0,
    score          REAL DEFAULT 0,
    scraped_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(product_id, source)
);

CREATE TABLE IF NOT EXISTS product_posts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    product_db_id    INTEGER REFERENCES products(id),
    platform         TEXT NOT NULL,   -- youtube | tiktok | instagram
    title            TEXT,
    content          TEXT,
    video_path       TEXT,
    platform_post_id TEXT,
    status           TEXT DEFAULT 'pending',
    error            TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    posted_at        TIMESTAMP,
    UNIQUE(product_db_id, platform)
);

CREATE TABLE IF NOT EXISTS video_clips (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id     TEXT NOT NULL UNIQUE,      -- YouTube video ID
    platform     TEXT NOT NULL DEFAULT 'youtube',
    url          TEXT,
    title        TEXT,
    channel      TEXT,
    views        INTEGER DEFAULT 0,
    likes        INTEGER DEFAULT 0,
    duration_sec INTEGER DEFAULT 0,
    transcript   TEXT,                       -- JSON list[{text,start,duration}]
    formula      TEXT,                       -- JSON from transcript_analyzer
    clip_path    TEXT,                       -- path to downloaded clip file
    analyzed_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS clip_posts (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_db_id       INTEGER REFERENCES video_clips(id),
    platform         TEXT NOT NULL,
    title            TEXT,
    content          TEXT,
    video_path       TEXT,
    platform_post_id TEXT,
    status           TEXT DEFAULT 'pending',
    error            TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    posted_at        TIMESTAMP,
    UNIQUE(clip_db_id, platform)
);
"""

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
    conn = get_conn()
    conn.execute("UPDATE trends SET analysis=? WHERE id=?", (json.dumps(analysis), trend_id))
    conn.commit()
    conn.close()


def load_analysis(trend_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute("SELECT analysis FROM trends WHERE id=?", (trend_id,)).fetchone()
    conn.close()
    if row and row["analysis"]:
        return json.loads(row["analysis"])
    return None


def get_unposted_trends(platform: str, limit: int = 10) -> list:
    conn = get_conn()
    rows = conn.execute(
        """SELECT t.id, t.topic, t.source, t.score, t.raw_data, t.analysis
           FROM trends t
           WHERE DATE(t.scraped_at) = DATE('now')
             AND NOT EXISTS (SELECT 1 FROM posts p WHERE p.trend_id=t.id AND p.platform=?)
           ORDER BY t.score DESC LIMIT ?""",
        (platform, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def next_unused_angle(trend_id: int) -> int:
    conn = get_conn()
    used = {r[0] for r in conn.execute(
        "SELECT angle_used FROM posts WHERE trend_id=?", (trend_id,)
    ).fetchall()}
    conn.close()
    for i in range(3):
        if i not in used:
            return i
    return 0


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


# ── Products ──────────────────────────────────────────────────────────────────

def upsert_product(product_id: str, source: str, name: str, description: str,
                   price: float, commission_pct: float, affiliate_url: str,
                   image_url: str, category: str, rating: float,
                   review_count: int, score: float) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO products
             (product_id, source, name, description, price, commission_pct,
              affiliate_url, image_url, category, rating, review_count, score)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(product_id, source) DO UPDATE SET
             price=excluded.price, score=excluded.score,
             rating=excluded.rating, review_count=excluded.review_count,
             scraped_at=CURRENT_TIMESTAMP""",
        (product_id, source, name, description, price, commission_pct,
         affiliate_url, image_url, category, rating, review_count, score),
    )
    conn.commit()
    db_id = cur.lastrowid or conn.execute(
        "SELECT id FROM products WHERE product_id=? AND source=?", (product_id, source)
    ).fetchone()["id"]
    conn.close()
    return db_id


def get_unposted_products(platform: str, limit: int = 10) -> list:
    """Products not yet posted to `platform` — ordered by score desc."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT p.*
           FROM products p
           WHERE NOT EXISTS (
               SELECT 1 FROM product_posts pp
               WHERE pp.product_db_id=p.id AND pp.platform=?
           )
           ORDER BY p.score DESC LIMIT ?""",
        (platform, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Product posts ─────────────────────────────────────────────────────────────

def create_product_post(product_db_id: int, platform: str, title: str,
                        content: str, video_path: str = None) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT OR IGNORE INTO product_posts
           (product_db_id, platform, title, content, video_path)
           VALUES (?,?,?,?,?)""",
        (product_db_id, platform, title, content, video_path),
    )
    conn.commit()
    post_id = cur.lastrowid
    conn.close()
    return post_id


def mark_product_post(post_id: int, status: str,
                      platform_post_id: str = None, error: str = None):
    conn = get_conn()
    conn.execute(
        """UPDATE product_posts SET status=?, platform_post_id=?, error=?,
           posted_at=CASE WHEN ?='posted' THEN CURRENT_TIMESTAMP ELSE posted_at END
           WHERE id=?""",
        (status, platform_post_id, error, status, post_id),
    )
    conn.commit()
    conn.close()


# ── Video clips ───────────────────────────────────────────────────────────────

def clip_seen(video_id: str) -> bool:
    """Return True if this YouTube video_id has already been analyzed."""
    conn = get_conn()
    row = conn.execute(
        "SELECT id FROM video_clips WHERE video_id=?", (video_id,)
    ).fetchone()
    conn.close()
    return row is not None


def save_video_clip(
    video_id: str, url: str, title: str, channel: str,
    views: int, likes: int, duration_sec: int,
    transcript: list, formula: dict,
    clip_path: str = None,
) -> int:
    """Insert or update a video clip record. Returns the DB row id."""
    conn = get_conn()
    cur = conn.execute(
        """INSERT INTO video_clips
             (video_id, url, title, channel, views, likes, duration_sec,
              transcript, formula, clip_path)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(video_id) DO UPDATE SET
             transcript=excluded.transcript,
             formula=excluded.formula,
             clip_path=COALESCE(excluded.clip_path, clip_path),
             analyzed_at=CURRENT_TIMESTAMP""",
        (
            video_id, url, title, channel, views, likes, duration_sec,
            json.dumps(transcript), json.dumps(formula) if formula else None,
            clip_path,
        ),
    )
    conn.commit()
    db_id = cur.lastrowid or conn.execute(
        "SELECT id FROM video_clips WHERE video_id=?", (video_id,)
    ).fetchone()["id"]
    conn.close()
    return db_id


def get_unposted_clips(platform: str, limit: int = 5) -> list:
    """Return analyzed clips not yet posted to `platform`, newest first."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT vc.*
           FROM video_clips vc
           WHERE vc.formula IS NOT NULL
             AND NOT EXISTS (
               SELECT 1 FROM clip_posts cp
               WHERE cp.clip_db_id = vc.id AND cp.platform = ?
             )
           ORDER BY vc.views DESC
           LIMIT ?""",
        (platform, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_clip_post(
    clip_db_id: int, platform: str,
    title: str, content: str, video_path: str = None,
) -> int:
    conn = get_conn()
    cur = conn.execute(
        """INSERT OR IGNORE INTO clip_posts
           (clip_db_id, platform, title, content, video_path)
           VALUES (?,?,?,?,?)""",
        (clip_db_id, platform, title, content, video_path),
    )
    conn.commit()
    post_id = cur.lastrowid
    conn.close()
    return post_id


def mark_clip_post(post_id: int, status: str,
                   platform_post_id: str = None, error: str = None):
    conn = get_conn()
    conn.execute(
        """UPDATE clip_posts SET status=?, platform_post_id=?, error=?,
           posted_at=CASE WHEN ?='posted' THEN CURRENT_TIMESTAMP ELSE posted_at END
           WHERE id=?""",
        (status, platform_post_id, error, status, post_id),
    )
    conn.commit()
    conn.close()
