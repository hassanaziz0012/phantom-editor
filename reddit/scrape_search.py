"""
Reddit Search Results Scraper using Playwright & Chrome DevTools Protocol (CDP).

Scrapes post results from a Reddit search (https://www.reddit.com/search/?q=...&type=posts)
by attaching to a persistent native Google Chrome instance via CDP.

Extracts:
- post title & post url
- subreddit
- author
- date posted (relative and ISO timestamp)
- upvotes (vote count)
- comments_n (number of comments)
- thumbnail url (if the result shows one)

Usage:
    uv run python reddit/scrape_search.py "automate video editing"
    uv run python reddit/scrape_search.py "automate video editing" --limit 40
    uv run python reddit/scrape_search.py "automate video editing" --sort top --time month
    uv run python reddit/scrape_search.py "automate video editing" --json
"""

import argparse
import asyncio
import json
import os
import sys
import urllib.parse
from pathlib import Path
from typing import List, Optional

from playwright.async_api import async_playwright, Page
from pydantic import BaseModel, Field

# Ensure repo root is in python path for module imports
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from reddit.scrape_subreddit import (
    CDP_PORT,
    PROFILE_DIR,
    get_cdp_browser_context,
    get_clean_page,
)

# ==============================================================================
# Configuration & Constants
# ==============================================================================
SEARCH_URL = "https://www.reddit.com/search/"
SORT_OPTIONS = ["relevance", "hot", "top", "new", "comments"]
TIME_OPTIONS = ["all", "year", "month", "week", "day", "hour"]

# Extracts every search result post currently in the DOM. Each result is a
# search-telemetry-tracker carrying a JSON tracking context with post/author/subreddit info.
EXTRACT_RESULTS_JS = """
() => {
    const results = [];
    for (const el of document.querySelectorAll('[data-testid="search-sdui-post"]')) {
        let ctx = {};
        try {
            ctx = JSON.parse(el.getAttribute('data-faceplate-tracking-context') || '{}');
        } catch (_) {}

        const titleLink = el.querySelector('a[data-testid="post-title-text"], a[data-testid="post-title"]');
        const timeago = el.querySelector('faceplate-timeago');
        const timeEl = el.querySelector('faceplate-timeago time, time');
        const counters = el.querySelectorAll('[data-testid="search-counter-row"] faceplate-number');
        const thumb = el.querySelector('[data-testid="search_post_thumbnail"]');

        results.push({
            post_id: el.getAttribute('data-thingid') || (ctx.post && ctx.post.id) || '',
            title: (ctx.post && ctx.post.title) || (titleLink ? titleLink.textContent.trim() : ''),
            nsfw: !!(ctx.post && ctx.post.nsfw),
            author: (ctx.profile && ctx.profile.name) || '',
            subreddit: (ctx.subreddit && ctx.subreddit.name) || '',
            permalink: titleLink ? titleLink.getAttribute('href') || '' : '',
            date_posted: timeEl ? timeEl.textContent.trim() : '',
            created_timestamp: timeago ? timeago.getAttribute('ts') || '' : '',
            upvotes: counters[0] ? counters[0].getAttribute('number') || '0' : '0',
            comments_n: counters[1] ? counters[1].getAttribute('number') || '0' : '0',
            thumbnail_url: thumb ? thumb.getAttribute('src') || '' : '',
        });
    }
    return results;
}
"""


def build_search_url(query: str, sort: str = "relevance", time_filter: str = "all") -> str:
    """Constructs the Reddit post search URL for a query, sort order and time window."""
    params = {"q": query.strip(), "type": "posts"}
    if sort != "relevance":
        params["sort"] = sort
    if time_filter != "all":
        params["t"] = time_filter
    return f"{SEARCH_URL}?{urllib.parse.urlencode(params)}"


# ==============================================================================
# Pydantic Schemas for Scraped Search Data
# ==============================================================================
class SearchPostItem(BaseModel):
    post_id: str = Field(description="Reddit thing id of the post, e.g. 't3_1x4cklm'")
    title: str = Field(description="Post title")
    subreddit: str = Field(description="Subreddit the post belongs to, e.g. 'r/VideoEditors'")
    author: str = Field(description="Username of the post author")
    date_posted: str = Field(description="Relative post age, e.g. '2d ago' or '3mo ago'")
    created_timestamp: Optional[str] = Field(default=None, description="ISO 8601 creation timestamp")
    upvotes: str = Field(description="Vote count of the post")
    comments_n: str = Field(description="Number of comments on the post")
    nsfw: bool = Field(default=False, description="Whether the post is marked NSFW")
    thumbnail_url: Optional[str] = Field(default=None, description="Thumbnail image URL, if shown")
    url: str = Field(description="Full URL / permalink to the post")


class ScrapedSearchData(BaseModel):
    query: str
    sort: str
    time_filter: str
    search_url: str
    total_scraped: int
    posts: List[SearchPostItem]


# ==============================================================================
# Scraper Implementation
# ==============================================================================
def to_search_post_item(raw: dict) -> SearchPostItem:
    """Converts a raw result dict from EXTRACT_RESULTS_JS into a SearchPostItem."""
    author = raw.get("author") or ""
    subreddit = raw.get("subreddit") or ""
    permalink = raw.get("permalink") or ""
    full_url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink

    return SearchPostItem(
        post_id=raw.get("post_id") or "",
        title=raw.get("title") or "Untitled Post",
        subreddit=f"r/{subreddit}" if subreddit else "Unknown subreddit",
        author=f"u/{author}" if author else "u/anonymous",
        date_posted=raw.get("date_posted") or "Unknown date",
        created_timestamp=raw.get("created_timestamp") or None,
        upvotes=str(raw.get("upvotes") or "0"),
        comments_n=str(raw.get("comments_n") or "0"),
        nsfw=bool(raw.get("nsfw")),
        thumbnail_url=raw.get("thumbnail_url") or None,
        url=full_url,
    )


async def navigate_with_retries(page: Page, url: str, quiet: bool = False) -> None:
    """
    Navigates to a URL, retrying on rate limits (HTTP 429) & transient HTTP/network failures.
    """
    max_retries = 3
    retry_delay = 240  # seconds to sleep before each retry
    max_nav_attempts = 1 + max_retries
    last_err = None

    for attempt in range(1, max_nav_attempts + 1):
        try:
            if not quiet and attempt > 1:
                print(f"[*] Retry attempt {attempt - 1}/{max_retries} for {url}...")
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            if response and response.status in (429, 502, 503, 504):
                retry_after = response.headers.get("retry-after")
                attempt_desc = "initial attempt" if attempt == 1 else f"retry {attempt - 1}/{max_retries}"
                if not quiet:
                    print(f"[!] Warning: Received HTTP {response.status} on {attempt_desc}")
                    print(f"[*] Reddit 'Retry-After' header: {retry_after or 'None (not present)'}")
                else:
                    print(
                        f"[!] Warning: Received HTTP {response.status} on {attempt_desc}. "
                        f"Retry-After: {retry_after or 'None'}",
                        file=sys.stderr,
                    )

                if attempt < max_nav_attempts:
                    print(
                        f"[*] Sleeping {retry_delay}s before retry {attempt}/{max_retries}...",
                        file=sys.stderr if quiet else sys.stdout,
                    )
                    await asyncio.sleep(retry_delay)
                    continue

                raise RuntimeError(
                    f"Failed to navigate to {url}: Received HTTP {response.status} "
                    f"(Retry-After: {retry_after or 'None'}) after {max_retries} retries."
                )

            return
        except RuntimeError:
            raise
        except Exception as e:
            last_err = e
            err_str = str(e)
            if "ERR_HTTP_RESPONSE_CODE_FAILURE" in err_str or "timeout" in err_str.lower() or "net::" in err_str:
                if attempt < max_nav_attempts:
                    print(
                        f"[!] Warning: Transient navigation failure ({err_str[:60]}...). "
                        f"Sleeping {retry_delay}s before retry {attempt}/{max_retries}...",
                        file=sys.stderr if quiet else sys.stdout,
                    )
                    await asyncio.sleep(retry_delay)
                    continue
            raise

    raise last_err or RuntimeError(f"Failed to navigate to {url}: Navigation was not successful.")


async def scrape_search(
    query: str,
    sort: str = "relevance",
    time_filter: str = "all",
    limit: int = 20,
    headless: bool = False,
    port: int = CDP_PORT,
    profile_dir: Optional[Path] = None,
    quiet: bool = False,
) -> ScrapedSearchData:
    """
    Scrapes post results for a Reddit search query using Playwright via Chrome CDP.

    Args:
        query: Search query, e.g. 'automate video editing'.
        sort: Result ordering ('relevance', 'hot', 'top', 'new', 'comments').
        time_filter: Time window ('all', 'year', 'month', 'week', 'day', 'hour').
        limit: Target maximum number of posts to scrape (default: 20).
        headless: Whether to start Chrome headless if launching a new instance.
        port: Chrome CDP port (default: 9222).
        profile_dir: Optional custom Chrome user profile directory.
        quiet: If True, suppresses informational progress output.

    Returns:
        ScrapedSearchData object containing list of SearchPostItem.
    """
    search_url = build_search_url(query, sort, time_filter)
    profile_path = profile_dir or PROFILE_DIR

    if not quiet:
        print(f"[*] Search Query: {query}")
        print(f"[*] Sort: {sort} | Time: {time_filter}")
        print(f"[*] Target URL: {search_url}")
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
                print(f"[*] Navigating to: {search_url}")

            await navigate_with_retries(page, search_url, quiet=quiet)

            # Wait for search results to attach
            try:
                await page.locator('[data-testid="search-sdui-post"]').first.wait_for(state="attached", timeout=15000)
            except Exception:
                if not quiet:
                    print("[!] Warning: search result selector wait timed out, continuing with available DOM...")

            await asyncio.sleep(2)

            scraped_items: List[SearchPostItem] = []
            seen_ids = set()

            # Stop once we hit the limit, or when several scrolls in a row load nothing new
            max_stale_scrolls = 3
            stale_scrolls = 0

            while len(scraped_items) < limit and stale_scrolls < max_stale_scrolls:
                before = len(scraped_items)

                for raw in await page.evaluate(EXTRACT_RESULTS_JS):
                    if len(scraped_items) >= limit:
                        break
                    key = raw.get("post_id") or raw.get("permalink")
                    if not key or key in seen_ids:
                        continue
                    seen_ids.add(key)
                    scraped_items.append(to_search_post_item(raw))

                if len(scraped_items) < limit:
                    stale_scrolls = stale_scrolls + 1 if len(scraped_items) == before else 0
                    # Scroll to the bottom to trigger lazy loading of additional results
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    await asyncio.sleep(2)

            if not quiet:
                print(f"[✓] Successfully scraped {len(scraped_items)} posts for search '{query}'.")

            return ScrapedSearchData(
                query=query,
                sort=sort,
                time_filter=time_filter,
                search_url=search_url,
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
def print_scraped_search_data(data: ScrapedSearchData):
    """
    Prints a clean, formatted summary of the scraped search results.
    """
    print("\n" + "=" * 80)
    print(f"               REDDIT SEARCH: '{data.query}' ({data.sort.upper()}, {data.time_filter.upper()})")
    print(f" URL: {data.search_url}")
    print(f" Total Posts Scraped: {data.total_scraped}")
    print("=" * 80)

    for idx, post in enumerate(data.posts, start=1):
        nsfw_str = " [NSFW]" if post.nsfw else ""
        print(f"\n--- Post #{idx} -------------------------------------------------------------")
        print(f"Title:        {post.title}{nsfw_str}")
        print(f"Subreddit:    {post.subreddit}")
        print(f"Author:       {post.author}")
        print(f"Posted:       {post.date_posted}" + (f" ({post.created_timestamp})" if post.created_timestamp else ""))
        print(f"Engagement:   ▲ {post.upvotes} votes | 💬 {post.comments_n} comments")
        print(f"URL:          {post.url}")

    print("\n" + "=" * 80)


# ==============================================================================
# CLI Argument Parser
# ==============================================================================
def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Scrape Reddit post search results using Playwright connected to Chrome via CDP."
    )
    parser.add_argument(
        "query",
        nargs="+",
        help="Search query (e.g. 'automate video editing'). Multiple words are joined with spaces.",
    )
    parser.add_argument(
        "--sort",
        choices=SORT_OPTIONS,
        default="relevance",
        help="Result ordering (default: relevance).",
    )
    parser.add_argument(
        "--time",
        dest="time_filter",
        choices=TIME_OPTIONS,
        default="all",
        help="Time window for results (default: all).",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Target number of posts to scrape from the results (default: 20).",
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
    query = " ".join(args.query)

    try:
        scraped_data = await scrape_search(
            query=query,
            sort=args.sort,
            time_filter=args.time_filter,
            limit=args.limit,
            headless=args.headless,
            port=args.port,
            quiet=args.json,
        )

        if args.json:
            print(json.dumps(scraped_data.model_dump(), indent=2))
        else:
            print_scraped_search_data(scraped_data)
    except Exception as e:
        if args.json:
            print(json.dumps({"error": f"Failed to scrape search results: {e}", "query": query, "posts": []}))
            sys.exit(0)
        else:
            print(f"[!] Error scraping search results: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
