"""
Reddit Subreddit Feed Scraper using Playwright & Chrome DevTools Protocol (CDP).

Scrapes posts from any subreddit feed (best, hot, new, rising, top hourly/daily/weekly/monthly/yearly/alltime)
by attaching to a persistent native Google Chrome instance via CDP. The tab loads the feed's
.json URL rather than the page, so Chrome doesn't have to run Reddit's web app (see reddit_json.py).

Extracts:
- author
- date posted (relative and ISO timestamp)
- flair (e.g. "Discussion", "Showoff Saturday", etc.)
- body_snippet (text body preview, or formatted media/link preview)
- upvotes (score count)
- comments_n (number of comments)
- post title & post url

Usage:
    uv run python reddit/scrape_subreddit.py webdev
    uv run python reddit/scrape_subreddit.py r/webdev --top-weekly
    uv run python reddit/scrape_subreddit.py "https://www.reddit.com/r/webdev" --hot --limit 25
    uv run python reddit/scrape_subreddit.py webdev --top-daily --json
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import async_playwright, Page
from pydantic import BaseModel, Field

# Ensure repo root is in python path for module imports
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from browser.cdp import CDP_PORT, PROFILE_DIR, get_cdp_browser_context, get_clean_page
from reddit.navigate import goto_with_retries
from reddit.reddit_json import collect_feed_items, decode_response, listing_data

# ==============================================================================
# Configuration & Constants
# ==============================================================================

FEED_URL_MAP = {
    "best": "https://www.reddit.com/r/{sub}/best/",
    "top-hourly": "https://www.reddit.com/r/{sub}/top/?t=hour",
    "top-daily": "https://www.reddit.com/r/{sub}/top/?t=day",
    "top-weekly": "https://www.reddit.com/r/{sub}/top/?t=week",
    "top-monthly": "https://www.reddit.com/r/{sub}/top/?t=month",
    "top-yearly": "https://www.reddit.com/r/{sub}/top/?t=year",
    "top-alltime": "https://www.reddit.com/r/{sub}/top/?t=all",
    "hot": "https://www.reddit.com/r/{sub}/hot/",
    "new": "https://www.reddit.com/r/{sub}/new/",
    "rising": "https://www.reddit.com/r/{sub}/rising/",
}


# ==============================================================================
# Subreddit URL Normalization
# ==============================================================================
def parse_subreddit_input(subreddit_input: str, feed_type: str = "best") -> Tuple[str, str]:
    """
    Normalizes any subreddit input format (URL, r/name, /r/name, or plain name)
    and constructs the corresponding feed URL.

    Args:
        subreddit_input: 'https://www.reddit.com/r/webdev', 'r/webdev', '/r/webdev', or 'webdev'
        feed_type: Feed category key from FEED_URL_MAP (default: 'best')

    Returns:
        Tuple of (normalized_subreddit_name, target_feed_url)
    """
    raw = subreddit_input.strip()

    # Match https://reddit.com/r/<name>, /r/<name>, r/<name>, or <name>
    match = re.search(r"(?:https?://(?:www\.)?reddit\.com/r/|^/?r/|^)([\w\d_]+)", raw, re.IGNORECASE)
    if not match:
        raise ValueError(f"Could not parse valid subreddit name from: {subreddit_input}")

    sub_name = match.group(1).lower()

    if feed_type not in FEED_URL_MAP:
        feed_type = "best"

    url = FEED_URL_MAP[feed_type].format(sub=sub_name)
    return sub_name, url


# ==============================================================================
# Pydantic Schemas for Scraped Subreddit Data
# ==============================================================================
class SubredditPostItem(BaseModel):
    title: str = Field(description="Post title")
    author: str = Field(description="Username of the post author")
    date_posted: str = Field(description="Relative post age, e.g. '11 hr. ago' or '1 day ago'")
    created_timestamp: Optional[str] = Field(default=None, description="ISO 8601 creation timestamp")
    flair: Optional[str] = Field(default=None, description="Post flair text or category")
    body_snippet: str = Field(description="Post body snippet or preview link/image indicator")
    upvotes: str = Field(description="Upvote / score count of the post")
    comments_n: str = Field(description="Number of comments on the post")
    post_type: str = Field(default="text", description="Type of post: text, link, image, gallery, video")
    url: str = Field(description="Full URL / permalink to the post")


class ScrapedSubredditData(BaseModel):
    subreddit: str
    feed_type: str
    feed_url: str
    total_scraped: int
    posts: List[SubredditPostItem]


# ==============================================================================
# Scraper Implementation
# ==============================================================================
async def fetch_listing(page: Page, url: str, quiet: bool = False) -> Dict[str, Any]:
    """
    Loads one page of a feed's .json listing in the tab and returns its "data" object
    (posts under "children", the next page's cursor under "after").
    """
    response = await goto_with_retries(page, url, quiet=quiet)
    return listing_data(url, decode_response(url, response.status, await response.text()))


async def scrape_subreddit(
    subreddit_input: str,
    feed_type: str = "best",
    limit: int = 20,
    headless: bool = False,
    port: int = CDP_PORT,
    profile_dir: Optional[Path] = None,
    quiet: bool = False,
) -> ScrapedSubredditData:
    """
    Scrapes posts from a specified subreddit and feed category using Playwright via Chrome CDP.

    Args:
        subreddit_input: Subreddit identifier ('webdev', 'r/webdev', 'https://reddit.com/r/webdev').
        feed_type: Feed sorting option ('best', 'hot', 'new', 'rising', 'top-hourly', etc.).
        limit: Target maximum number of posts to scrape (default: 20).
        headless: Whether to start Chrome headless if launching a new instance.
        port: Chrome CDP port (default: 9222).
        profile_dir: Optional custom Chrome user profile directory.
        quiet: If True, suppresses informational progress output.

    Returns:
        ScrapedSubredditData object containing list of SubredditPostItem.
    """
    sub_name, feed_url = parse_subreddit_input(subreddit_input, feed_type)
    profile_path = profile_dir or PROFILE_DIR

    if not quiet:
        print(f"[*] Subreddit: r/{sub_name}")
        print(f"[*] Feed Type: {feed_type}")
        print(f"[*] Target URL: {feed_url}")
        print(f"[*] Connecting to Chrome via CDP on port {port} (profile: {profile_path})...")

    async with async_playwright() as p:
        context = await get_cdp_browser_context(
            playwright=p,
            profile_path=profile_path,
            port=port,
            headless=headless,
        )

        page = await get_clean_page(context)
        try:
            if not quiet:
                print(f"[*] Navigating to: {feed_url}")

            items = await collect_feed_items(lambda u: fetch_listing(page, u, quiet), feed_url, limit)
            scraped_items = [SubredditPostItem(**item) for item in items]

            if not quiet:
                print(f"[✓] Successfully scraped {len(scraped_items)} posts from r/{sub_name}.")

            return ScrapedSubredditData(
                subreddit=sub_name,
                feed_type=feed_type,
                feed_url=feed_url,
                total_scraped=len(scraped_items),
                posts=scraped_items,
            )

        finally:
            if not quiet:
                print("[*] Closing scraper tab...")
            await page.close()


# ==============================================================================
# Output Formatting
# ==============================================================================
def print_scraped_subreddit_data(data: ScrapedSubredditData):
    """
    Prints a clean, formatted summary of the scraped subreddit feed.
    """
    print("\n" + "=" * 80)
    print(f"               REDDIT FEED: r/{data.subreddit.upper()} ({data.feed_type.upper()})")
    print(f" URL: {data.feed_url}")
    print(f" Total Posts Scraped: {data.total_scraped}")
    print("=" * 80)

    for idx, post in enumerate(data.posts, start=1):
        flair_str = f" [{post.flair}]" if post.flair else ""
        print(f"\n--- Post #{idx} -------------------------------------------------------------")
        print(f"Title:        {post.title}{flair_str}")
        print(f"Author:       {post.author}")
        print(f"Posted:       {post.date_posted}" + (f" ({post.created_timestamp})" if post.created_timestamp else ""))
        print(f"Engagement:   ▲ {post.upvotes} upvotes | 💬 {post.comments_n} comments | Type: {post.post_type}")
        print(f"URL:          {post.url}")
        print("Body Snippet:")
        if post.body_snippet:
            # Wrap / indent body snippet
            lines = post.body_snippet.splitlines()
            snippet_display = "\n".join(f"    {l}" for l in lines[:6])
            if len(lines) > 6:
                snippet_display += "\n    ..."
            print(snippet_display)
        else:
            print("    [No body snippet]")

    print("\n" + "=" * 80)


# ==============================================================================
# CLI Argument Parser
# ==============================================================================
def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Scrape Reddit subreddit feeds using Playwright connected to Chrome via CDP."
    )
    parser.add_argument(
        "subreddit",
        help="Subreddit to scrape (e.g. 'webdev', 'r/webdev', or 'https://www.reddit.com/r/webdev').",
    )

    # Feed sorting flag group (mutually exclusive)
    feed_group = parser.add_mutually_exclusive_group()
    feed_group.add_argument(
        "--best",
        action="store_const",
        dest="feed",
        const="best",
        help="Scrape 'Best' feed (default)",
    )
    feed_group.add_argument(
        "--top-hourly",
        action="store_const",
        dest="feed",
        const="top-hourly",
        help="Scrape 'Top' feed from the past hour",
    )
    feed_group.add_argument(
        "--top-daily",
        action="store_const",
        dest="feed",
        const="top-daily",
        help="Scrape 'Top' feed from the past 24 hours",
    )
    feed_group.add_argument(
        "--top-weekly",
        action="store_const",
        dest="feed",
        const="top-weekly",
        help="Scrape 'Top' feed from the past week",
    )
    feed_group.add_argument(
        "--top-monthly",
        action="store_const",
        dest="feed",
        const="top-monthly",
        help="Scrape 'Top' feed from the past month",
    )
    feed_group.add_argument(
        "--top-yearly",
        action="store_const",
        dest="feed",
        const="top-yearly",
        help="Scrape 'Top' feed from the past year",
    )
    feed_group.add_argument(
        "--top-alltime",
        action="store_const",
        dest="feed",
        const="top-alltime",
        help="Scrape 'Top' feed of all time",
    )
    feed_group.add_argument(
        "--hot",
        action="store_const",
        dest="feed",
        const="hot",
        help="Scrape 'Hot' feed",
    )
    feed_group.add_argument(
        "--new",
        action="store_const",
        dest="feed",
        const="new",
        help="Scrape 'New' feed",
    )
    feed_group.add_argument(
        "--rising",
        action="store_const",
        dest="feed",
        const="rising",
        help="Scrape 'Rising' feed",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Target number of posts to scrape from the feed (default: 20).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run Chrome in headless mode if starting instance.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=CDP_PORT,
        help=f"Chrome CDP debugging port (default: {CDP_PORT}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output raw JSON to stdout.",
    )

    return parser.parse_args()


# ==============================================================================
# Main Entry Point
# ==============================================================================
async def main():
    args = parse_arguments()
    feed_type = args.feed or "best"

    try:
        scraped_data = await scrape_subreddit(
            subreddit_input=args.subreddit,
            feed_type=feed_type,
            limit=args.limit,
            headless=args.headless,
            port=args.port,
            quiet=args.json,
        )

        if args.json:
            print(json.dumps(scraped_data.model_dump(), indent=2))
        else:
            print_scraped_subreddit_data(scraped_data)
    except Exception as e:
        if args.json:
            print(json.dumps({"error": f"Failed to scrape subreddit feed: {e}", "subreddit": args.subreddit, "posts": []}))
            sys.exit(0)
        else:
            print(f"[!] Error scraping subreddit feed: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
