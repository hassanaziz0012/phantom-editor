# 🤖 Reddit Automation & Outreach

Automation tools for scraping Reddit posts and subreddit feeds via Playwright and Chrome DevTools Protocol (CDP), analyzing post sentiment with Groq LLM, and executing automated YouTube promotion outreach against semantic video embeddings.

---

## 🏗️ Architecture

All Reddit scraping workflows attach to a persistent native Google Chrome instance via CDP:
* **CDP Port**: `9222` (default).
* **Browser Profile**: `~/Desktop/browser-profiles/cdp` (preserves active sessions, logins, and cookies).
* **Anti-Bot Stealth**: Injects stealth scripts to mask `navigator.webdriver`, authenticates `window.chrome`, and strips automation signatures.
* **CLI Wrapper**: Main subcommands are integrated directly into the `phantom reddit` CLI.

---

## 🛠️ Scripts & Tools

### 1. [scrape_reddit_post.py](../reddit/scrape_reddit_post.py)

Scrapes engagement metrics (upvote count, upvote ratio, view count, total comment count) and top comments from any Reddit post URL.

* **CLI Usage**:
  ```bash
  # Scrape post and print formatted summary
  phantom reddit scrape "https://www.reddit.com/r/webdev/comments/..."

  # Output as JSON
  phantom reddit scrape "https://www.reddit.com/r/webdev/comments/..." --json

  # Limit comments or run headless
  phantom reddit scrape "https://www.reddit.com/r/..." --max-comments 15 --headless
  ```

* **Direct Python**:
  ```bash
  uv run python reddit/scrape_reddit_post.py <url> [--headless] [--max-comments <int>] [--json] [--port <int>]
  ```

* **Key Options**:
  - `url`: Reddit post URL (required).
  - `--max-comments`: Maximum number of top comments to extract (default: `10`).
  - `--headless`: Run Chrome in headless mode.
  - `--json`: Output raw JSON to stdout.

---

### 2. [scrape_subreddit.py](../reddit/scrape_subreddit.py)

Scrapes posts from any subreddit feed (`best`, `hot`, `new`, `rising`, or `top` filtered by hour, day, week, month, year, or all-time).

* **CLI Usage**:
  ```bash
  # Scrape default feed ('best')
  phantom reddit scrapesub webdev

  # Scrape 'hot' or 'new' feeds
  phantom reddit scrapesub webdev --hot --limit 25
  phantom reddit scrapesub r/automation --new

  # Scrape 'top' feeds
  phantom reddit scrapesub webdev --top-weekly
  phantom reddit scrapesub webdev --top-daily --json
  ```

* **Direct Python**:
  ```bash
  uv run python reddit/scrape_subreddit.py <subreddit> [feed_flag] [--limit <int>] [--headless] [--json]
  ```

* **Feed Flags**:
  - `--best` (default), `--hot`, `--new`, `--rising`
  - `--top-hourly`, `--top-daily`, `--top-weekly`, `--top-monthly`, `--top-yearly`, `--top-alltime`
* **Key Options**:
  - `subreddit`: Subreddit name, `r/name`, or full URL.
  - `--limit`: Target number of posts to scrape (default: `20`).
  - `--headless`: Run Chrome in headless mode.
  - `--json`: Output raw JSON to stdout.

---

### 3. [analyze_post.py](../reddit/analyze_post.py)

Scrapes a Reddit post via `scrape_reddit_post.py` and runs sentiment and community reception analysis using Groq (`openai/gpt-oss-120b`) with structured Pydantic outputs (`RedditPostAnalysis`).

* **CLI Usage**:
  ```bash
  # Run sentiment analysis on a post
  phantom reddit analyze-post "https://www.reddit.com/r/..."

  # Run headless with JSON output
  phantom reddit analyze-post "https://www.reddit.com/r/..." --headless --json
  ```

* **Direct Python**:
  ```bash
  uv run python reddit/analyze_post.py <url> [--headless] [--json]
  ```

* **Output Analysis**:
  - **Rating**: Community reception score out of 10.0.
  - **Reception**: `positively received`, `negatively received`, `mixed`, or `neutral`.
  - **Verdict & Summary**: Synthesized analysis of upvotes, views, and comment sentiment.
  - **Key Comment Themes**: Recurring talking points, feedback, and humor.
  - **Positive & Critical Points**: Specific arguments, praise, counterarguments, and skepticism.

---

### 4. [promote_videos.py](../reddit/promote_videos.py)

Outreach engine that identifies authentic video promotion opportunities across targeted subreddits:

1. **Subreddit Ingestion**: Reads target subreddits from `reddit/subreddits_for_yt_promotion.txt`.
2. **Deduplication**: Checks Google Sheet (`GOOGLE_OUTREACH_TRACKER_SHEET_ID`, tab `Reddit YT promotion`) to skip already evaluated posts.
3. **Semantic Matching**: Computes cosine similarity between post text and channel video embeddings (`metadata/recommendations/my_videos_embeddings.npy`).
4. **LLM Grading**: Evaluates top candidate videos using Groq structured outputs (`grade_post_for_video_promotion.md`) to score fit (1–10) and generate a natural reply angle.
5. **Google Sheets Logging**: Appends genuine matches, scores, explanations, and reply angles to the outreach tracking sheet.

* **Usage**:
  ```bash
  # Run standard outreach evaluation
  uv run python reddit/promote_videos.py

  # Custom limit and feed
  uv run python reddit/promote_videos.py --limit 15 --feed hot

  # Dry run (evaluates without writing to Google Sheets)
  uv run python reddit/promote_videos.py --dry-run
  ```

* **Options**:
  - `-f, --subreddits-file`: Path to subreddits list file (default: `reddit/subreddits_for_yt_promotion.txt`).
  - `-l, --limit`: Maximum posts to scrape per subreddit (default: `25`).
  - `--feed`: Subreddit feed (`new`, `hot`, `best`, `rising`, `top-daily`, `top-weekly`; default: `new`).
  - `--sheet-id`: Target Google Sheet ID (defaults to `GOOGLE_OUTREACH_TRACKER_SHEET_ID` from `.env`).
  - `--sheet-name`: Target worksheet tab (default: `Reddit YT promotion`).
  - `--headless`: Run Chrome in headless mode.
  - `--dry-run`: Evaluate posts without modifying Google Sheets.
