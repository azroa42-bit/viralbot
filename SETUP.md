# ViralBot Setup Guide

Scans viral trends → generates original content → auto-posts to Reddit and YouTube.

---

## 1. Prerequisites

**Python 3.11+** and **ffmpeg** (required for video rendering).

Install ffmpeg on Windows:
```
winget install ffmpeg
```
Or download from https://ffmpeg.org/download.html and add to PATH.

---

## 2. Install dependencies

```bash
cd C:\Users\azroa\OneDrive\Desktop\ViralBot
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

---

## 3. Configure .env

```bash
copy .env.example .env
```

Edit `.env` and fill in each value. Details below.

### Claude API (required)
1. Go to https://console.anthropic.com/
2. Create an API key
3. Paste into `ANTHROPIC_API_KEY`

### Reddit (free)
1. Go to https://www.reddit.com/prefs/apps
2. Click **"create another app"**
3. Choose **"script"** type
4. Fill in any name and redirect URI (`http://localhost`)
5. Copy `client_id` (under the app name) and `client_secret`
6. Set `REDDIT_POST_SUBREDDITS=test` to start safely

### YouTube (free)
1. Go to https://console.cloud.google.com/
2. Create a new project
3. Enable **YouTube Data API v3**
4. Create credentials:
   - **API Key** → paste into `YOUTUBE_API_KEY`
   - **OAuth 2.0 Client ID** → type: Desktop app → download JSON
   - Rename downloaded file to `client_secrets.json` in the ViralBot folder
5. First YouTube upload will open a browser for OAuth consent (one-time only)

---

## 4. Run

**Test run (once, no loop):**
```bash
python main.py --once
```

**Continuous loop (every 2 hours):**
```bash
python main.py
```

---

## 5. Monetization path

| Platform | Requirement | Timeline |
|---|---|---|
| YouTube Shorts | 500 subs + 3,000 watch hours | 1-3 months |
| YouTube AdSense | 1,000 subs + 4,000 hours | 2-6 months |
| Reddit | Drive traffic to affiliate links in posts | Immediate |

**Tips:**
- Set `MAX_TRENDS_PER_RUN=5` and `RUN_INTERVAL_HOURS=3` for higher volume
- Add Amazon affiliate links to relevant Reddit posts for immediate income
- Once earning, add Twitter/X API ($100/mo) to expand reach 3x

---

## 6. Upgrade path (when generating revenue)

| Add-on | Cost | What it unlocks |
|---|---|---|
| Twitter/X API Basic | $100/mo | Auto-post threads + tap Creator Monetization |
| Runway Gen API | Pay-per-second | Real AI video instead of text slides |
| TikTok Creator API | Apply free | TikTok auto-posting |
