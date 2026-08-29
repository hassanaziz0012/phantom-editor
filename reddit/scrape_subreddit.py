"""
Reddit Subreddit Feed Scraper using Playwright & Chrome DevTools Protocol (CDP).

Scrapes posts from any subreddit feed (best, hot, new, rising, top hourly/daily/weekly/monthly/yearly/alltime)
by attaching to a persistent native Google Chrome instance via CDP.

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
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from playwright.async_api import async_playwright, BrowserContext, Page
from pydantic import BaseModel, Field

# Ensure repo root is in python path for module imports
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# ==============================================================================
# Configuration & Constants
# ==============================================================================
PROFILE_DIR = Path.home() / "Desktop" / "browser-profiles" / "cdp"
CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"

STEALTH_INIT_SCRIPT = """
(() => {
    // 1. Mask navigator.webdriver
    try {
        if (Object.getOwnPropertyDescriptor(Navigator.prototype, 'webdriver')) {
            Object.defineProperty(Navigator.prototype, 'webdriver', {
                get: () => false,
                enumerable: true,
                configurable: true,
            });
        }
    } catch (_) {}
    try {
        if (Object.prototype.hasOwnProperty.call(navigator, 'webdriver')) {
            delete navigator.webdriver;
        }
    } catch (_) {}

    // 2. Ensure window.chrome structure is authentic
    if (!window.chrome) {
        window.chrome = {
            runtime: { id: undefined, connect: function() {}, sendMessage: function() {} },
            loadTimes: function() {},
            csi: function() {},
            app: { isInstalled: false, InstallState: {}, RunningState: {} },
        };
    }

    // 3. Fix permissions query for notifications
    if (navigator.permissions && navigator.permissions.query) {
        const originalQuery = navigator.permissions.query.bind(navigator.permissions);
        navigator.permissions.query = (parameters) => {
            if (parameters && parameters.name === 'notifications') {
                return Promise.resolve({
                    state: typeof Notification !== 'undefined' && Notification.permission === 'granted' ? 'granted' : 'prompt',
                    name: 'notifications',
                    onchange: null,
                    addEventListener: () => {},
                    removeEventListener: () => {},
                    dispatchEvent: () => false,
                });
            }
            return originalQuery(parameters);
        };
    }
})();
"""

EXTRA_STEALTH_JS = """
// 1. Mask navigator.webdriver
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
});

// 2. Clean up any CDC/CDP artifact signatures
for (const key of Object.getOwnPropertyNames(window)) {
    if (key.startsWith('cdc_') || key.startsWith('$cdc_')) {
        delete window[key];
    }
}

// 3. Ensure window.chrome object exists with standard properties
if (!window.chrome) {
    window.chrome = {};
}
if (!window.chrome.app) {
    window.chrome.app = {
        isInstalled: false,
        InstallState: { DISABLED: "disabled", INSTALLED: "installed", NOT_INSTALLED: "not_installed" },
        RunningState: { CANNOT_RUN: "cannot_run", READY_TO_RUN: "ready_to_run", RUNNING: "running" }
    };
}
if (!window.chrome.runtime) {
    window.chrome.runtime = {
        OnInstalledReason: { CHROME_UPDATE: "chrome_update", INSTALL: "install", SHARED_MODULE_UPDATE: "shared_module_update", UPDATE: "update" },
        OnRestartRequiredReason: { APP_UPDATE: "app_update", OS_UPDATE: "os_update", PERIODIC: "periodic" },
        PlatformArch: { ARM: "arm", ARM64: "arm64", MIPS: "mips", MIPS64: "mips64", X86_32: "x86-32", X86_64: "x86-64" },
        PlatformNaclArch: { ARM: "arm", MIPS: "mips", MIPS64: "mips64", X86_32: "x86-32", X86_64: "x86-64" },
        PlatformOs: { ANDROID: "android", CROS: "cros", LINUX: "linux", MAC: "mac", OPENBSD: "openbsd", WIN: "win" },
        RequestUpdateCheckStatus: { NO_UPDATE: "no_update", THROTTLED: "throttled", UPDATE_AVAILABLE: "update_available" }
    };
}
"""

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
# Helper Functions for CDP Chrome Lifecycle
# ==============================================================================
def find_chrome_executable() -> str:
    """Locates the system Google Chrome binary."""
    for binary in ("google-chrome-stable", "google-chrome", "chromium-browser", "chromium"):
        path = shutil.which(binary)
        if path:
            return path
    raise FileNotFoundError("Google Chrome executable not found on system PATH.")


def is_cdp_ready(port: int = CDP_PORT) -> bool:
    """Checks if the Chrome CDP endpoint is responding."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json/version",
            headers={"User-Agent": "Playwright-CDP-Client"},
        )
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            return resp.status == 200
    except Exception:
        return False


def get_cdp_ws_endpoint(port: int = CDP_PORT) -> str:
    """Fetches the active WebSocket debugger URL from the CDP endpoint to avoid Node's legacy url.parse()."""
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json/version",
            headers={"User-Agent": "Playwright-CDP-Client"},
        )
        with urllib.request.urlopen(req, timeout=2.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            return data.get("webSocketDebuggerUrl") or f"http://127.0.0.1:{port}"
    except Exception:
        return f"http://127.0.0.1:{port}"


def cleanup_stale_locks(profile_path: Path) -> None:
    """Removes dangling singleton locks if Chrome crashed or was killed."""
    for lock in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        target = profile_path / lock
        try:
            if target.is_symlink() or target.is_file():
                target.unlink(missing_ok=True)
            elif target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
        except Exception:
            pass


def ensure_chrome_cdp(
    profile_path: Path = PROFILE_DIR,
    port: int = CDP_PORT,
    headless: bool = False,
) -> str:
    """Ensures Chrome is running with CDP enabled. Auto-launches if not active."""
    cdp_url = f"http://127.0.0.1:{port}"
    if is_cdp_ready(port):
        return cdp_url

    profile_path = profile_path.resolve()
    profile_path.mkdir(parents=True, exist_ok=True)
    cleanup_stale_locks(profile_path)

    chrome_bin = find_chrome_executable()
    cmd = [
        chrome_bin,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_path}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
        "--password-store=basic",
    ]
    if headless:
        cmd.extend(["--headless=new", "--disable-gpu"])

    # Launch Chrome as an independent detached background process
    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    # Wait for the CDP endpoint to become ready
    for _ in range(40):
        time.sleep(0.25)
        if is_cdp_ready(port):
            return cdp_url

    raise RuntimeError(f"Failed to start Chrome with remote debugging on port {port}")


async def get_cdp_browser_context(
    playwright,
    profile_path: Path = PROFILE_DIR,
    port: int = CDP_PORT,
    headless: bool = False,
) -> BrowserContext:
    """Connects Playwright to the live Chrome CDP instance and applies stealth scripts."""
    ensure_chrome_cdp(profile_path=profile_path, port=port, headless=headless)
    ws_endpoint = get_cdp_ws_endpoint(port=port)
    browser = await playwright.chromium.connect_over_cdp(ws_endpoint)

    context = browser.contexts[0] if browser.contexts else await browser.new_context()
    await context.add_init_script(STEALTH_INIT_SCRIPT)
    await context.add_init_script(EXTRA_STEALTH_JS)
    return context


async def get_clean_page(context: BrowserContext) -> Page:
    """Always opens a dedicated new tab for the worker process to avoid tab collisions."""
    page = await context.new_page()
    await page.add_init_script(STEALTH_INIT_SCRIPT)
    await page.add_init_script(EXTRA_STEALTH_JS)
    return page


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
async def extract_single_post(post_elem, quiet: bool = False) -> Optional[SubredditPostItem]:
    """
    Extracts structured data from a single shreddit-post element using role and accessibility selectors.
    """
    try:
        # Attributes from shreddit-post
        post_title = await post_elem.get_attribute("post-title") or ""
        author = await post_elem.get_attribute("author") or ""
        score = await post_elem.get_attribute("score") or "0"
        comment_count = await post_elem.get_attribute("comment-count") or "0"
        created_timestamp = await post_elem.get_attribute("created-timestamp") or ""
        post_type = await post_elem.get_attribute("post-type") or "text"
        content_href = await post_elem.get_attribute("content-href") or ""
        permalink = await post_elem.get_attribute("permalink") or ""

        # 1. Fallback for Title via accessibility role 'heading'
        if not post_title:
            heading = post_elem.get_by_role("heading")
            if await heading.count() > 0:
                post_title = (await heading.first.inner_text()).strip()

        # 2. Fallback for Author via role 'link'
        if not author or author == "unknown":
            author_link = post_elem.get_by_role("link", name=re.compile(r"^u\/", re.I))
            if await author_link.count() > 0:
                raw_author = (await author_link.first.inner_text()).strip()
                author = raw_author.replace("u/", "").strip()

        if author and not author.startswith("u/"):
            display_author = f"u/{author}"
        else:
            display_author = author or "u/anonymous"

        # 3. Date Posted (relative age)
        date_posted = ""
        time_loc = post_elem.locator("faceplate-timeago, time, [slot='post-timestamp']")
        if await time_loc.count() > 0:
            date_posted = (await time_loc.first.inner_text()).strip()
        if not date_posted and created_timestamp:
            date_posted = created_timestamp

        # 4. Flair extraction (role / aria-label / slot)
        flair_val = None
        flair_loc = post_elem.locator("[aria-label^='Flair:'], shreddit-post-flair, [slot='post-flair']")
        if await flair_loc.count() > 0:
            raw_flair = (await flair_loc.first.inner_text()).strip()
            if not raw_flair:
                aria = await flair_loc.first.get_attribute("aria-label") or ""
                if "Flair:" in aria:
                    raw_flair = aria.split("Flair:", 1)[1].strip()
            if raw_flair:
                flair_val = raw_flair

        # 5. Body Snippet (text preview, media, or external link)
        body_snippet = ""
        # Check paragraphs first (accessibility role)
        paragraphs = post_elem.get_by_role("paragraph")
        p_count = await paragraphs.count()
        if p_count > 0:
            p_texts = []
            for idx in range(p_count):
                txt = (await paragraphs.nth(idx).inner_text()).strip()
                if txt:
                    p_texts.append(txt)
            if p_texts:
                body_snippet = "\n".join(p_texts)

        # Fallback to slot="text-body" or text preview element
        if not body_snippet:
            text_body = post_elem.locator("[slot='text-body'], shreddit-post-text-body")
            if await text_body.count() > 0:
                body_snippet = (await text_body.first.inner_text()).strip()

        # Fallbacks for non-text posts (image, video, link)
        if not body_snippet:
            if post_type in ["image", "gallery"]:
                img_loc = post_elem.get_by_role("img", name=re.compile(r"post|preview|image", re.I))
                if await img_loc.count() > 0:
                    img_src = await img_loc.first.get_attribute("src") or ""
                    body_snippet = f"[Image: {img_src}]" if img_src else "[Image Post]"
                elif content_href:
                    body_snippet = f"[Image: {content_href}]"
                else:
                    body_snippet = "[Image Post]"
            elif post_type in ["video", "gif"]:
                body_snippet = f"[Video: {content_href}]" if content_href else "[Video Post]"
            elif post_type == "link" or (content_href and permalink not in content_href):
                body_snippet = f"[Link: {content_href}]"
            elif post_type == "crosspost":
                body_snippet = f"[Crosspost: {content_href}]"

        # 6. Upvotes (score)
        upvotes = score
        if not upvotes or upvotes == "0":
            upvote_btn = post_elem.get_by_role("button", name=re.compile(r"upvote", re.I))
            if await upvote_btn.count() > 0:
                btn_txt = await upvote_btn.first.inner_text()
                digits = re.findall(r"\d+[\d,.]*[kKmM]?", btn_txt)
                if digits:
                    upvotes = digits[0]

        # 7. Comments Count
        comments_n = comment_count
        if not comments_n or comments_n == "0":
            comment_btn = post_elem.get_by_role("link", name=re.compile(r"comment", re.I))
            if await comment_btn.count() > 0:
                c_txt = await comment_btn.first.inner_text()
                digits = re.findall(r"\d+[\d,.]*[kKmM]?", c_txt)
                if digits:
                    comments_n = digits[0]

        full_url = f"https://www.reddit.com{permalink}" if permalink.startswith("/") else permalink

        return SubredditPostItem(
            title=post_title or "Untitled Post",
            author=display_author,
            date_posted=date_posted or "Unknown date",
            created_timestamp=created_timestamp or None,
            flair=flair_val,
            body_snippet=body_snippet,
            upvotes=str(upvotes),
            comments_n=str(comments_n),
            post_type=post_type,
            url=full_url,
        )
    except Exception as e:
        if not quiet:
            print(f"[!] Error extracting single post: {e}", file=sys.stderr)
        return None


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
            await page.goto(feed_url, wait_until="domcontentloaded")

            # Wait for posts container to attach
            try:
                await page.locator("shreddit-post").first.wait_for(state="attached", timeout=15000)
            except Exception:
                if not quiet:
                    print("[!] Warning: shreddit-post selector wait timed out, continuing with available DOM...")

            await asyncio.sleep(2)

            scraped_items: List[SubredditPostItem] = []
            seen_urls = set()

            scroll_attempts = 0
            max_scroll_attempts = max(limit // 5 + 3, 5)

            while len(scraped_items) < limit and scroll_attempts < max_scroll_attempts:
                posts = page.locator("shreddit-post")
                count = await posts.count()

                for i in range(count):
                    if len(scraped_items) >= limit:
                        break

                    post_elem = posts.nth(i)
                    permalink = await post_elem.get_attribute("permalink") or ""
                    post_id = await post_elem.get_attribute("id") or permalink

                    if post_id in seen_urls:
                        continue

                    seen_urls.add(post_id)
                    item = await extract_single_post(post_elem, quiet=quiet)
                    if item:
                        scraped_items.append(item)

                if len(scraped_items) < limit:
                    scroll_attempts += 1
                    # Scroll down to trigger lazy loading of additional feed items
                    await page.evaluate("window.scrollBy(0, 1500)")
                    await asyncio.sleep(1.5)

            if not quiet:
                print(f"[✓] Successfully scraped {len(scraped_items)} posts from r/{sub_name} ({feed_type} feed).")

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


if __name__ == "__main__":
    asyncio.run(main())
