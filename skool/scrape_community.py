"""
Skool Community Scraper using Playwright & Chrome DevTools Protocol (CDP).

Scrapes post titles and canonical URLs from any Skool community page
(e.g., https://www.skool.com/maker-zero) with pagination support, attaching
to a persistent native Google Chrome instance via CDP.

Usage:
    uv run python skool/scrape_community.py "https://www.skool.com/maker-zero"
    uv run python skool/scrape_community.py "https://www.skool.com/maker-zero" --limit 50
    uv run python skool/scrape_community.py "https://www.skool.com/maker-zero" --json
    uv run python skool/scrape_community.py "https://www.skool.com/maker-zero" --output posts.json
"""

import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path
from typing import List, Optional, Set
from urllib.parse import urljoin, urlparse

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright
from pydantic import BaseModel, Field

# Ensure repo root is in python path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from browser.cdp import CDP_PORT, PROFILE_DIR, get_cdp_browser_context, get_clean_page

# ==============================================================================
# Configuration & Constants
# ==============================================================================

NON_POST_SLUGS = {
    "classroom",
    "about",
    "calendar",
    "leaderboards",
    "members",
    "settings",
    "search",
    "admin",
    "billing",
    "chat",
    "notifications",
    "rules",
    "affiliates",
}


# ==============================================================================
# Helper URL & Slug Functions
# ==============================================================================
def normalize_community_url(input_url: str) -> tuple[str, str]:
    """
    Parses a raw community URL or slug and returns:
        (canonical_community_base_url, community_slug)

    Examples:
        'https://www.skool.com/maker-zero' -> ('https://www.skool.com/maker-zero', 'maker-zero')
        'https://www.skool.com/maker-zero/about' -> ('https://www.skool.com/maker-zero', 'maker-zero')
        'maker-zero' -> ('https://www.skool.com/maker-zero', 'maker-zero')
    """
    cleaned = input_url.strip()
    if not cleaned.startswith("http://") and not cleaned.startswith("https://"):
        if "/" in cleaned:
            cleaned = f"https://www.skool.com/{cleaned.strip('/')}"
        else:
            cleaned = f"https://www.skool.com/{cleaned}"

    parsed = urlparse(cleaned)
    path_parts = [p for p in parsed.path.split("/") if p]
    if not path_parts:
        raise ValueError(f"Could not extract community slug from URL: {input_url}")

    slug = path_parts[0]
    base_url = f"https://www.skool.com/{slug}"
    return base_url, slug


# Every link Playwright's get_by_role("link") would match (an <a href> not hidden from the
# accessibility tree), as [{href, text}] in page order
VISIBLE_LINKS_JS = """() => [...document.querySelectorAll('a[href]')]
    .filter(a => !a.closest('[aria-hidden="true"]') && a.checkVisibility({visibilityProperty: true}))
    .map(a => ({href: a.getAttribute('href'), text: a.innerText}))"""


# The href of the first link to a post (/<community>/<post>) in the feed, or null
FIRST_POST_JS = """(slug) => {
    const prefix = "/" + slug.toLowerCase() + "/";
    for (const a of document.querySelectorAll('a[href]')) {
        const path = new URL(a.href, location.href).pathname.toLowerCase();
        if (path.startsWith(prefix) && path.split("/").filter(Boolean).length === 2 && a.innerText.trim()) return a.href;
    }
    return null;
}"""
FEED_WAIT_MS = 2000


async def wait_for_feed(page: Page, community_slug: str, previous: Optional[str]) -> None:
    """Wait until the feed lists posts, and a different first post than ``previous``, for at most FEED_WAIT_MS.

    The old fixed 2 s pause was the most this ever needed; usually the posts are already there.
    """
    try:
        await page.wait_for_function(
            f"([slug, previous]) => {{ const first = ({FIRST_POST_JS})(slug); return first && first !== previous; }}",
            arg=[community_slug, previous],
            timeout=FEED_WAIT_MS,
        )
    except PlaywrightTimeoutError:
        pass


# ==============================================================================
# Pydantic Schemas for Scraped Skool Community Posts
# ==============================================================================
class PostItem(BaseModel):
    title: str = Field(description="Title of the post")
    url: str = Field(description="Canonical URL of the post")


class ScrapedCommunityPosts(BaseModel):
    community_url: str = Field(description="Base URL of the community")
    community_slug: str = Field(description="Slug identifier of the community")
    total_scraped: int = Field(description="Total number of unique posts scraped")
    posts: List[PostItem] = Field(default_factory=list, description="List of scraped post titles and URLs")


# ==============================================================================
# Scraper Logic
# ==============================================================================
async def scrape_community(
    url: str,
    limit: int = 25,
    headless: bool = False,
    port: int = CDP_PORT,
    profile_dir: Optional[Path] = None,
    quiet: bool = False,
) -> ScrapedCommunityPosts:
    """
    Scrapes post titles and URLs from a Skool community page across pagination.
    Uses Playwright's role and accessibility-based locators to navigate pages
    and extract data reliably.

    Args:
        url: URL of the Skool community (e.g. 'https://www.skool.com/maker-zero').
        limit: Maximum number of posts to scrape (default: 25).
        headless: Whether to run Chrome headless (default False for headful).
        port: Chrome remote debugging port (default: 9222).
        profile_dir: Path to Chrome profile directory.
        quiet: If True, suppresses terminal logging (used with --json).

    Returns:
        ScrapedCommunityPosts instance containing all scraped posts.
    """
    base_url, community_slug = normalize_community_url(url)
    profile_path = profile_dir or PROFILE_DIR

    if not quiet:
        print(f"[*] Connecting to Chrome via CDP on port {port} (profile: {profile_path})...")
        print(f"[*] Target Community: {base_url} (Slug: {community_slug})")
        print(f"[*] Target Limit: {limit} posts")

    async with async_playwright() as p:
        context = await get_cdp_browser_context(
            playwright=p,
            profile_path=profile_path,
            port=port,
            headless=headless,
        )

        page = await get_clean_page(context, reuse_blank=True)
        try:
            if not quiet:
                print(f"[*] Navigating to: {base_url}")
            await page.goto(base_url, wait_until="domcontentloaded")

            # Wait (up to FEED_WAIT_MS) for post feed links to render
            await wait_for_feed(page, community_slug, previous=None)

            collected_posts: List[PostItem] = []
            seen_urls: Set[str] = set()
            page_num = 1
            max_pages = max(50, (limit // 20) + 10)

            while len(collected_posts) < limit and page_num <= max_pages:
                if not quiet:
                    print(f"[*] Scraping Page {page_num} (Collected so far: {len(collected_posts)}/{limit})...")

                # Read every visible link's href and text in one round trip; asking per
                # link cost two CDP calls each, and a feed page has well over 100 links
                links = await page.evaluate(VISIBLE_LINKS_JS)

                new_on_page = 0
                for link in links:
                    if len(collected_posts) >= limit:
                        break

                    href = link["href"]
                    if not href:
                        continue

                    # Resolve absolute URL
                    full_url = urljoin("https://www.skool.com", href)
                    parsed = urlparse(full_url)

                    # Must match /<community_slug>/<post_slug>
                    parts = [seg for seg in parsed.path.split("/") if seg]
                    if len(parts) != 2 or parts[0].lower() != community_slug.lower():
                        continue

                    slug = parts[1].lower()
                    if slug in NON_POST_SLUGS or slug.startswith("-") or slug.startswith("@"):
                        continue

                    # Exclude comment links (?p=...) or sidebar referral links (?utm_...)
                    if parsed.query:
                        continue

                    canonical_post_url = f"https://www.skool.com/{parts[0]}/{parts[1]}"
                    if canonical_post_url in seen_urls:
                        continue

                    title = (link["text"] or "").strip()

                    # Filter out empty texts or metadata labels (e.g. comment timestamps)
                    if not title or title.startswith("New comment") or title.startswith("Last comment") or title.startswith("View ") or title.isdigit():
                        continue

                    # Record unique post
                    seen_urls.add(canonical_post_url)
                    collected_posts.append(PostItem(title=title, url=canonical_post_url))
                    new_on_page += 1

                    if not quiet:
                        print(f"    [{len(collected_posts):>3}] {title} -> {canonical_post_url}")

                if not quiet:
                    print(f"[*] Added {new_on_page} posts from Page {page_num} (Total: {len(collected_posts)}/{limit})")

                if len(collected_posts) >= limit:
                    break

                # If no new posts were found on this page, stop to prevent infinite pagination
                if new_on_page == 0:
                    if not quiet:
                        print("[*] No new posts detected on this page. Reached end of feed.")
                    break

                # Locate Next pagination button using role-based selector
                next_button = page.get_by_role("button", name=re.compile(r"^Next", re.I))
                btn_count = await next_button.count()
                if btn_count == 0:
                    if not quiet:
                        print("[*] No 'Next' pagination button found. Reached last page.")
                    break

                first_post_url = await page.evaluate(FIRST_POST_JS, community_slug)
                first_next = next_button.first
                is_enabled = await first_next.is_enabled()
                is_visible = await first_next.is_visible()

                if not is_enabled or not is_visible:
                    if not quiet:
                        print("[*] 'Next' pagination button is disabled or not visible. Reached last page.")
                    break

                if not quiet:
                    print("[*] Navigating to next page...")

                try:
                    await first_next.scroll_into_view_if_needed()
                    await first_next.click(timeout=5000)
                except Exception as click_err:
                    if not quiet:
                        print(f"[!] Could not click Next button ({click_err}). Stopping pagination.")
                    break

                page_num += 1
                # Wait (up to FEED_WAIT_MS) for the feed to show the next page's posts
                await wait_for_feed(page, community_slug, previous=first_post_url)

            result = ScrapedCommunityPosts(
                community_url=base_url,
                community_slug=community_slug,
                total_scraped=len(collected_posts),
                posts=collected_posts,
            )

            if not quiet:
                print(f"\n[✓] Successfully scraped {len(result.posts)} posts from {base_url}.")

            return result

        finally:
            if not quiet:
                print("[*] Closing scraper page tab...")
            await page.close()


# ==============================================================================
# CLI Formatter & Main
# ==============================================================================
def print_formatted_results(data: ScrapedCommunityPosts):
    """Prints a clean human-readable summary table of the scraped community posts."""
    print("\n" + "=" * 80)
    print(f"             SCRAPED COMMUNITY POSTS: {data.community_slug.upper()}")
    print("=" * 80)
    print(f"Community URL:  {data.community_url}")
    print(f"Total Scraped:  {data.total_scraped}")
    print("-" * 80)
    print(f"{'#':<4} {'Title':<45} {'URL'}")
    print("-" * 80)

    for idx, post in enumerate(data.posts, 1):
        # Truncate title cleanly if very long for aligned display
        display_title = post.title if len(post.title) <= 43 else post.title[:40] + "..."
        print(f"[{idx:>2}] {display_title:<45} {post.url}")

    print("=" * 80 + "\n")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape post titles and URLs from a Skool community page using Playwright via CDP."
    )
    parser.add_argument(
        "url",
        help="Skool community URL or slug (e.g. 'https://www.skool.com/maker-zero' or 'maker-zero').",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=25,
        help="Maximum total posts to scrape (default: 25).",
    )
    parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Optional path to save scraped posts as a JSON file.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Suppress all logs and output raw scraped JSON to stdout.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run Chrome in headless mode (default: False / headful).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=CDP_PORT,
        help=f"Chrome remote debugging CDP port (default: {CDP_PORT}).",
    )
    return parser.parse_args()


async def main():
    args = parse_args()

    scraped = await scrape_community(
        url=args.url,
        limit=args.limit,
        headless=args.headless,
        port=args.port,
        quiet=args.json,
    )

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(scraped.model_dump(), indent=2, ensure_ascii=False))
        if not args.json:
            print(f"[✓] Saved {scraped.total_scraped} posts to {out_path.resolve()}")

    if args.json:
        print(json.dumps(scraped.model_dump(), indent=2, ensure_ascii=False))
    else:
        print_formatted_results(scraped)


if __name__ == "__main__":
    asyncio.run(main())
