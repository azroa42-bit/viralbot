import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Claude
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_model: str = "claude-haiku-4-5-20251001"

    # Reddit
    reddit_client_id: str = os.getenv("REDDIT_CLIENT_ID", "")
    reddit_client_secret: str = os.getenv("REDDIT_CLIENT_SECRET", "")
    reddit_username: str = os.getenv("REDDIT_USERNAME", "")
    reddit_password: str = os.getenv("REDDIT_PASSWORD", "")
    reddit_user_agent: str = os.getenv("REDDIT_USER_AGENT", "ViralBot/1.0")
    reddit_post_subreddits: list = [
        s.strip() for s in os.getenv("REDDIT_POST_SUBREDDITS", "test").split(",")
    ]

    # YouTube
    youtube_api_key: str = os.getenv("YOUTUBE_API_KEY", "")
    youtube_client_secrets_file: str = os.getenv("YOUTUBE_CLIENT_SECRETS_FILE", "client_secrets.json")
    youtube_token_file: str = "token.json"

    # DB & video
    db_path: Path = Path(os.getenv("DB_PATH", "viralbot.db"))
    video_output_dir: Path = Path(os.getenv("VIDEO_OUTPUT_DIR", "videos"))

    # Pipeline
    run_interval_hours: int = int(os.getenv("RUN_INTERVAL_HOURS", "2"))
    max_trends_per_run: int = int(os.getenv("MAX_TRENDS_PER_RUN", "3"))

    # Affiliate — appended to Reddit posts when topic matches a niche keyword
    # Format: "keyword:url,keyword:url"  (keyword is matched case-insensitively)
    # Example: "tech:https://amzn.to/abc,investing:https://amzn.to/xyz"
    affiliate_links: dict = {
        k.strip(): v.strip()
        for pair in os.getenv("AFFILIATE_LINKS", "").split(",")
        if ":" in pair
        for k, v in [pair.split(":", 1)]
    }

    def __init__(self):
        self.video_output_dir.mkdir(exist_ok=True)


config = Config()
