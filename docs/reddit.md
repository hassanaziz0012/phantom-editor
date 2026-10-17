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

### 3. [scrape_search.py](../reddit/scrape_search.py)

Scrapes post results from a Reddit search (`https://www.reddit.com/search/?q=...&type=posts`), scrolling to lazy-load more results until the limit is reached or results run out. Each result includes title, subreddit, author, relative and ISO post date, votes, comment count, thumbnail (if any), and URL.

* **CLI Usage**:
  ```bash
  # Search posts (relevance, all time)
  phantom reddit scrapesearch "automate video editing"

  # Unquoted multi-word queries also work
  phantom reddit scrapesearch automate video editing --limit 40

  # Sort and time window
  phantom reddit scrapesearch "automate video editing" --sort top --time month
  phantom reddit scrapesearch "automate video editing" --sort new --json
  ```

* **Direct Python**:
  ```bash
  uv run python reddit/scrape_search.py <query> [--sort <sort>] [--time <time>] [--limit <int>] [--headless] [--json]
  ```

* **Key Options**:
  - `query`: Search query (required).
  - `--sort`: `relevance` (default), `hot`, `top`, `new`, `comments`.
  - `--time`: `all` (default), `year`, `month`, `week`, `day`, `hour`.
  - `--limit`: Target number of posts to scrape (default: `20`).
  - `--headless`: Run Chrome in headless mode.
  - `--json`: Output raw JSON to stdout.

---

### 4. [scrape_profile.py](../reddit/scrape_profile.py)

Scrapes a Reddit user's profile (`https://www.reddit.com/user/<name>/`) from a username, `u/name`, or any profile URL. Uses the logged-in Chrome profile `~/Desktop/browser-profiles/p9222` on port `9222`, and like `serve.py` reads Reddit's `.json` endpoints with `fetch()` from a reddit.com tab instead of rendering the profile.

* **Account**: karma breakdown (post/comment/awarder/awardee, karma per day), cake day and account age, bio, avatar, banner, followers, contributions (with Reddit's posts/comments split), contributor status, gold earned, employee/mod/premium/verified/suspended flags, trophies, and moderated communities.
* **Posts**: score, upvote ratio, comments, crossposts, awards, flair, type, body or media link, NSFW/spoiler/locked/archived/pinned/stickied/OC/edited flags, and removal status (moderators, AutoModerator filter, Reddit's filters, admins, legal, copyright, deleted). For mod removals it also fetches the mod's or AutoModerator's removal comment.
* **Comments**: score, the post they're on and its author, reply vs. top-level, comments on own posts, controversial, edited, collapsed, and removed/deleted.
* **Views & insights**: views, shares, reposts, and top countries from `/poststats` and `/commentstats`. Reddit only shows these to the author, so they're only filled in when the profile belongs to the logged-in account. Reddit doesn't show insights for removed posts or for comments older than 90 days, and shows large view counts rounded (`12K`); those fields are listed in `insights.rounded`.
* **Summary**: removal rate by status, average/median scores, top post and comment, total views, most active subreddits, most active weekday and hour (UTC), and notes explaining any missing data.

* **CLI Usage**:
  ```bash
  # Username, u/name, or full profile URL
  phantom reddit profile spez
  phantom reddit profile "https://www.reddit.com/user/spez/" --posts 50 --comments 50

  # Top posts and comments of the past year, as JSON
  phantom reddit profile u/spez --sort top --time year --json

  # Skip the per-item insights requests on your own profile
  phantom reddit profile No_Nefariousness2052 --no-views
  ```

* **Direct Python**:
  ```bash
  uv run python reddit/scrape_profile.py <username_or_url> [--posts <int>] [--comments <int>] [--sort <sort>] [--time <time>] [--no-views] [--no-removal-reasons] [--headless] [--port <int>] [--json]
  ```

* **Key Options**:
  - `user`: Username, `u/name`, or a `reddit.com/user/<name>` URL (required).
  - `--posts`: Maximum number of posts to scrape (default: `25`, `0` to skip).
  - `--comments`: Maximum number of comments to scrape (default: `25`, `0` to skip).
  - `--sort`: `new` (default), `hot`, `top`, `controversial`.
  - `--time`: `all` (default), `year`, `month`, `week`, `day`, `hour` (for `top` and `controversial`).
  - `--no-views`: Skip fetching views & insights (one request per post/comment on your own profile).
  - `--no-removal-reasons`: Skip looking up the mod's comment on mod-removed posts.
  - `--headless`: Run Chrome in headless mode if starting a new instance.
  - `--port`: Chrome CDP port (default: `9222`).
  - `--json`: Output raw JSON to stdout.

---

### 5. [analyze_post.py](../reddit/analyze_post.py)

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

### 6. [promote_videos.py](../reddit/promote_videos.py)

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
