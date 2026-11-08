# 🤖 Reddit Automation & Outreach

Automation tools for scraping Reddit posts and subreddit feeds via Playwright and Chrome DevTools Protocol (CDP), and analyzing post sentiment with Groq LLM.

---

## 🏗️ Architecture

All Reddit scraping workflows attach to a persistent native Google Chrome instance via CDP:
* **CDP Port**: `9222` (default).
* **Browser Profile**: `~/Desktop/browser-profiles/cdp` (preserves active sessions, logins, and cookies).
* **Anti-Bot Stealth**: Injects stealth scripts to mask `navigator.webdriver`, authenticates `window.chrome`, and strips automation signatures.
* **CLI Wrapper**: Main subcommands are integrated directly into the `phantom research reddit` CLI.

---

## 🛠️ Scripts & Tools

### 1. [scrape_reddit_post.py](../reddit/scrape_reddit_post.py)

Scrapes engagement metrics (upvote count, upvote ratio, view count, total comment count) and top comments from any Reddit post URL.

* **CLI Usage**:
  ```bash
  # Scrape post and print formatted summary
  phantom research reddit post "https://www.reddit.com/r/webdev/comments/..."

  # Output as JSON
  phantom research reddit post "https://www.reddit.com/r/webdev/comments/..." --json

  # Limit comments or run headless
  phantom research reddit post "https://www.reddit.com/r/..." --max-comments 15 --headless
  ```

* **Direct Python**:
  ```bash
  phantom research reddit post <url> [--headless] [--max-comments <int>] [--json] [--port <int>]
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
  phantom research reddit sub webdev

  # Scrape 'hot' or 'new' feeds
  phantom research reddit sub webdev --hot --limit 25
  phantom research reddit sub r/automation --new

  # Scrape 'top' feeds
  phantom research reddit sub webdev --top-weekly
  phantom research reddit sub webdev --top-daily --json
  ```

* **Direct Python**:
  ```bash
  phantom research reddit sub <subreddit> [feed_flag] [--limit <int>] [--headless] [--json]
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

Scrapes post results from a Reddit search (`https://www.reddit.com/search/?q=...&type=posts`). Search has no usable `.json` URL (www.reddit.com blocks it, and old.reddit.com's legacy search ranks results differently), so the scraper `fetch()`es the search page's HTML from a tab and parses it without rendering, then follows the 7-results-at-a-time fragments the page would load on scroll (`/svc/shreddit/search/?...&cursor=...`) until the limit is reached or results run out. Each result includes title, subreddit, author, relative and ISO post date, votes, comment count, thumbnail (if any), and URL.

* **CLI Usage**:
  ```bash
  # Search posts (relevance, all time)
  phantom research reddit search "automate video editing"

  # Unquoted multi-word queries also work
  phantom research reddit search automate video editing --limit 40

  # Sort and time window
  phantom research reddit search "automate video editing" --sort top --time month
  phantom research reddit search "automate video editing" --sort new --json
  ```

* **Direct Python**:
  ```bash
  phantom research reddit search <query> [--sort <sort>] [--time <time>] [--limit <int>] [--headless] [--json]
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
  phantom research reddit profile spez
  phantom research reddit profile "https://www.reddit.com/user/spez/" --posts 50 --comments 50

  # Top posts and comments of the past year, as JSON
  phantom research reddit profile u/spez --sort top --time year --json

  # Skip the per-item insights requests on your own profile
  phantom research reddit profile No_Nefariousness2052 --no-views
  ```

* **Direct Python**:
  ```bash
  phantom research reddit profile <username_or_url> [--posts <int>] [--comments <int>] [--sort <sort>] [--time <time>] [--no-views] [--no-removal-reasons] [--headless] [--port <int>] [--json]
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
  phantom research reddit analyze "https://www.reddit.com/r/..."

  # Run headless with JSON output
  phantom research reddit analyze "https://www.reddit.com/r/..." --headless --json
  ```

* **Direct Python**:
  ```bash
  phantom research reddit analyze <url> [--headless] [--json]
  ```

* **Output Analysis**:
  - **Rating**: Community reception score out of 10.0.
  - **Reception**: `positively received`, `negatively received`, `mixed`, or `neutral`.
  - **Verdict & Summary**: Synthesized analysis of upvotes, views, and comment sentiment.
  - **Key Comment Themes**: Recurring talking points, feedback, and humor.
  - **Positive & Critical Points**: Specific arguments, praise, counterarguments, and skepticism.
