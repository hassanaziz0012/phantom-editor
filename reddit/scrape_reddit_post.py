"""
Reddit Post Scraper using Playwright & Chrome DevTools Protocol (CDP).

Scrapes post engagement metrics (title, author, subreddit, upvote count, upvote ratio,
view count, total comment count, and top comments) from any Reddit post URL by attaching
to a persistent native Google Chrome instance via CDP. The tab loads the post's .json
URL rather than the page, so Chrome doesn't have to run Reddit's web app (see reddit_json.py).

Usage:
    uv run python reddit/scrape_reddit_post.py "https://www.reddit.com/r/..."
    uv run python reddit/scrape_reddit_post.py "https://reddit.com/..." --json
    uv run python reddit/scrape_reddit_post.py "https://reddit.com/..." --headless --max-comments 15
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import List, Optional
from urllib.parse import urlsplit

from playwright.async_api import async_playwright
from pydantic import BaseModel, Field

# Ensure repo root is in python path for module imports
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from browser.cdp import CDP_PORT, PROFILE_DIR, get_cdp_browser_context, get_clean_page
from reddit.navigate import goto_with_retries
from reddit.reddit_json import check_post_data, decode_response, json_url, post_fields

# -----------------------------------------------------------------------------
# Pydantic Schemas for Scraped Post Data
# -----------------------------------------------------------------------------
class CommentData(BaseModel):
    author: str = Field(description="Username of the commenter")
    score: str = Field(description="Upvote score of the comment")
    text: str = Field(description="Body content of the comment")


class ScrapedPostData(BaseModel):
    url: str
    title: str
    body: str = Field(description="Post body text, media link, or external URL")
    subreddit: str
    author: str
    upvote_count: str
    upvote_ratio: Optional[str] = None
    view_count: str
    total_comments_count: str
    top_comments: List[CommentData]


# -----------------------------------------------------------------------------
# Scraper Logic
# -----------------------------------------------------------------------------
async def scrape_reddit_post(
    url: str,
    headless: bool = False,
    max_comments: int = 10,
    port: int = CDP_PORT,
    profile_dir: Optional[Path] = None,
    quiet: bool = False,
) -> ScrapedPostData:
    """
    Scrapes post details and top comments from Reddit using Playwright attached to Chrome via CDP.
    Always uses role/accessibility-based selectors and semantic attributes.

    Args:
        url: Reddit post URL.
        headless: Whether to run Chrome in headless mode (if auto-spawned).
        max_comments: Maximum number of top comments to extract (default: 10).
        port: Chrome remote debugging CDP port (default: 9222).
        profile_dir: Optional custom path to Chrome profile directory.
        quiet: If True, suppresses informational progress output.

    Returns:
        ScrapedPostData object containing all extracted post information.
    """
    profile_path = profile_dir or PROFILE_DIR
    if not quiet:
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
            post_url = url
            if "/comments/" not in urlsplit(url).path:
                # Share links (/r/<sub>/s/<id>, redd.it/<id>) only redirect to the post, so follow
                # the redirect first and fetch the .json of the post it lands on
                await page.goto(url, wait_until="commit", timeout=30000)
                post_url = page.url
            target_url = json_url(post_url)
            if not quiet:
                print(f"[*] Navigating to: {target_url}")

            response = await goto_with_retries(page, target_url, quiet=quiet)

            data = check_post_data(url, decode_response(url, response.status, await response.text()))
            scraped = ScrapedPostData(**post_fields(url, data, max_comments))
            if not quiet:
                print(f"[✓] Successfully scraped post metrics and {len(scraped.top_comments)} comments.")
            return scraped

        finally:
            if not quiet:
                print("[*] Closing scraper tab...")
            await page.close()


def print_scraped_post_data(data: ScrapedPostData):
    """
    Prints a formatted summary of the scraped Reddit post data.
    """
    print("\n" + "=" * 70)
    print("                      SCRAPED REDDIT POST DATA")
    print("=" * 70)
    print(f"Title:          {data.title}")
    print(f"Subreddit:      r/{data.subreddit}")
    print(f"Author:         u/{data.author}")
    print(f"URL:            {data.url}")
    print(f"Upvotes:        {data.upvote_count} (Ratio: {data.upvote_ratio or 'N/A'})")
    print(f"Views:          {data.view_count}")
    print(f"Total Comments: {data.total_comments_count} (Scraped: {len(data.top_comments)})")
    print("-" * 70)
    print("Post Body:")
    if data.body:
        # Indent each line of body for clean display
        indented_body = "\n".join(f"  {line}" for line in data.body.splitlines())
        print(indented_body)
    else:
        print("  [Empty body]")
    print("-" * 70)

    if data.top_comments:
        print("\n[TOP COMMENTS]")
        for idx, c in enumerate(data.top_comments, start=1):
            print(f"\n  {idx}. u/{c.author} (Score: {c.score}):")
            print(f"     \"{c.text}\"")
    else:
        print("\nNo comments found.")

    print("\n" + "=" * 70)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Scrape Reddit post engagement metrics and top comments using Playwright with Chrome CDP."
    )
    parser.add_argument(
        "url",
        help="Reddit post URL to scrape.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run Playwright in headless mode if starting Chrome (default: headful mode).",
    )
    parser.add_argument(
        "--max-comments",
        type=int,
        default=10,
        help="Maximum number of top comments to extract (default: 10).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=CDP_PORT,
        help=f"Chrome DevTools Protocol port (default: {CDP_PORT}).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output the raw scraped post JSON to stdout.",
    )
    return parser.parse_args()


async def main():
    args = parse_arguments()

    try:
        scraped_data = await scrape_reddit_post(
            url=args.url,
            headless=args.headless,
            max_comments=args.max_comments,
            port=args.port,
            quiet=args.json,
        )

        if args.json:
            print(json.dumps(scraped_data.model_dump(), indent=2))
        else:
            print_scraped_post_data(scraped_data)
    except Exception as e:
        if args.json:
            print(json.dumps({"error": f"Failed to scrape post: {e}", "url": args.url}))
            sys.exit(0)
        else:
            print(f"[!] Error scraping post: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
