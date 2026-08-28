"""
Reddit Post Scraper using Playwright & Chrome DevTools Protocol (CDP).

Scrapes post engagement metrics (title, author, subreddit, upvote count, upvote ratio,
view count, total comment count, and top comments) from any Reddit post URL by attaching
to a persistent native Google Chrome instance via CDP.

Usage:
    uv run python reddit/scrape_reddit_post.py "https://www.reddit.com/r/..."
    uv run python reddit/scrape_reddit_post.py "https://reddit.com/..." --json
    uv run python reddit/scrape_reddit_post.py "https://reddit.com/..." --headless --max-comments 15
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
from typing import Any, Dict, List, Optional

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
    """Reuses an existing blank page or opens a new tab to avoid collisions."""
    for page in context.pages:
        if page.url in ("about:blank", "chrome://newtab/", "chrome://new-tab-page/"):
            await page.add_init_script(STEALTH_INIT_SCRIPT)
            await page.add_init_script(EXTRA_STEALTH_JS)
            return page
    page = await context.new_page()
    await page.add_init_script(STEALTH_INIT_SCRIPT)
    await page.add_init_script(EXTRA_STEALTH_JS)
    return page


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
            if not quiet:
                print(f"[*] Navigating to: {url}")
            await page.goto(url, wait_until="domcontentloaded")

            # Wait for the main post component to be available
            try:
                await page.wait_for_selector("shreddit-post, article, [role='main']", timeout=15000)
            except Exception:
                if not quiet:
                    print("[!] Warning: Main post selector wait timed out, continuing with available DOM...")

            # Gentle scroll to trigger loading of top comments
            await asyncio.sleep(2)
            await page.evaluate("window.scrollBy(0, 600)")
            await asyncio.sleep(1.5)

            # -------------------------------------------------------------
            # 1. Extract Post Metrics (Upvotes, Views, Title, Subreddit)
            # -------------------------------------------------------------
            post_elem = page.locator("shreddit-post").first
            has_shreddit = await post_elem.count() > 0

            post_title = ""
            score_val = "0"
            upvote_ratio_val = None
            comment_count_val = "0"
            subreddit_val = "reddit"
            author_val = "unknown"

            if has_shreddit:
                post_title = await post_elem.get_attribute("post-title") or ""
                score_val = await post_elem.get_attribute("score") or "0"
                upvote_ratio_val = await post_elem.get_attribute("upvote-ratio")
                comment_count_val = await post_elem.get_attribute("comment-count") or "0"
                subreddit_val = await post_elem.get_attribute("subreddit-name") or "reddit"
                author_val = await post_elem.get_attribute("author") or "unknown"

            # Fallback for title using accessibility role 'heading'
            if not post_title:
                heading_loc = page.get_by_role("heading", level=1)
                if await heading_loc.count() > 0:
                    post_title = (await heading_loc.first.inner_text()).strip()
                else:
                    headings = await page.get_by_role("heading").all_inner_texts()
                    if headings:
                        post_title = headings[0].strip()

            # Fallback for upvote score
            if score_val == "0":
                upvote_btn = page.get_by_role("button", name=re.compile(r"upvote", re.I))
                if await upvote_btn.count() > 0:
                    score_text = await upvote_btn.first.inner_text()
                    digits = re.findall(r"\d+[\d,.]*[kKmM]?", score_text)
                    if digits:
                        score_val = digits[0]

            # -------------------------------------------------------------
            # Extract View Count (Post Insights)
            # -------------------------------------------------------------
            # Post views on Reddit are only visible to the author/moderator in the "Post Insights" bar
            # below the post buttons (e.g. '1.5K views  See More Insights').
            view_count_val = "Not publicly visible"

            # Wait briefly for post insights to render if available
            try:
                await page.wait_for_selector(
                    "shreddit-post-insights, [data-testid='post-insights'], faceplate-tracker[source='post_insights'], text=/views/i",
                    timeout=2500,
                )
            except Exception:
                pass

            # Strategy A: JavaScript evaluation for Post Insights element, nearby text, or eye icon container
            try:
                js_view = await page.evaluate("""() => {
                    // 1. Check dedicated post-insights custom element, testid, or faceplate tracker
                    const insightsEl = document.querySelector('shreddit-post-insights, [data-testid="post-insights"], faceplate-tracker[source="post_insights"]');
                    if (insightsEl) {
                        const text = insightsEl.innerText || insightsEl.textContent || '';
                        const m = text.match(/(\\d+[\\d,.]*\\s*[kKmMbB]?)\\s*(?:total\\s*)?views/i) ||
                                  text.match(/(?:total\\s*)?views[:\\s]*(\\d+[\\d,.]*\\s*[kKmMbB]?)/i);
                        if (m) return m[1].trim() + " views";
                    }

                    // 2. Check elements containing or adjacent to "See More Insights" or "Post Insights"
                    const insightLinks = Array.from(document.querySelectorAll('a, button, span, div, p')).filter(el =>
                        /see\\s+more\\s+insights|post\\s+insights/i.test(el.innerText || '')
                    );
                    for (const el of insightLinks) {
                        const container = el.closest('div, section, faceplate-tracker, shreddit-post-insights') || el.parentElement;
                        if (container) {
                            const text = container.innerText || container.textContent || '';
                            const m = text.match(/(\\d+[\\d,.]*\\s*[kKmMbB]?)\\s+views/i);
                            if (m) return m[1].trim() + " views";
                        }
                    }

                    // 3. Check inside shreddit-post or article
                    const post = document.querySelector('shreddit-post, article, [role="main"]');
                    if (post) {
                        const postText = post.innerText || '';
                        const m = postText.match(/\\b(\\d+[\\d,.]*\\s*[kKmMbB]?)\\s+views\\b/i);
                        if (m) return m[1].trim() + " views";
                    }

                    // 4. Global body text search
                    const bodyText = document.body ? document.body.innerText : '';
                    const m = bodyText.match(/\\b(\\d+[\\d,.]*\\s*[kKmMbB]?)\\s+views\\b/i);
                    if (m) return m[1].trim() + " views";

                    return null;
                }""")
                if js_view:
                    view_count_val = js_view
            except Exception:
                pass

            # Strategy B: Fallback locator text regex if Strategy A didn't find it
            if view_count_val == "Not publicly visible":
                # Search inside shreddit-post
                if has_shreddit:
                    post_text = await post_elem.inner_text()
                    view_match = re.search(r"\b(\d+[\d,.]*\s*[kKmMbB]?)\s+views\b", post_text, re.IGNORECASE)
                    if view_match:
                        view_count_val = f"{view_match.group(1).strip()} views"

                # Search inside entire page body
                if view_count_val == "Not publicly visible":
                    full_body_text = await page.locator("body").inner_text()
                    view_match = re.search(r"\b(\d+[\d,.]*\s*[kKmMbB]?)\s+views\b", full_body_text, re.IGNORECASE)
                    if view_match:
                        view_count_val = f"{view_match.group(1).strip()} views"

                # Search via get_by_text unanchored pattern
                if view_count_val == "Not publicly visible":
                    view_locator = page.get_by_text(re.compile(r"\b\d+[\d,.]*[kKmMbB]?\s+views\b", re.I))
                    if await view_locator.count() > 0:
                        raw_view_text = await view_locator.first.inner_text()
                        view_match = re.search(r"(\d+[\d,.]*\s*[kKmMbB]?)\s+views", raw_view_text, re.I)
                        if view_match:
                            view_count_val = f"{view_match.group(1).strip()} views"

            # -------------------------------------------------------------
            # 2. Extract Post Body (Text, Media, or External Link)
            # -------------------------------------------------------------
            post_body_val = ""
            if has_shreddit:
                # 1. Primary: Role-based paragraph extraction within post
                paragraphs = post_elem.get_by_role("paragraph")
                p_count = await paragraphs.count()
                if p_count > 0:
                    p_texts = []
                    for i in range(p_count):
                        txt = (await paragraphs.nth(i).inner_text()).strip()
                        if txt:
                            p_texts.append(txt)
                    if p_texts:
                        post_body_val = "\n\n".join(p_texts)

                # 2. Semantic slot fallback for text-body container
                if not post_body_val:
                    text_body_slot = post_elem.locator("[slot='text-body']")
                    if await text_body_slot.count() > 0:
                        post_body_val = (await text_body_slot.first.inner_text()).strip()

                # 3. Fallbacks for media (image, video) and external link posts
                if not post_body_val:
                    post_type = await post_elem.get_attribute("post-type") or ""
                    content_href = await post_elem.get_attribute("content-href") or ""
                    permalink = await post_elem.get_attribute("permalink") or ""

                    images = post_elem.get_by_role("img", name=re.compile(r"post|preview|image", re.I))
                    if await images.count() > 0:
                        img_src = await images.first.get_attribute("src")
                        if img_src:
                            post_body_val = f"[Image: {img_src}]"

                    if not post_body_val and content_href and content_href != permalink and not content_href.endswith(permalink):
                        if post_type in ["image", "gallery"]:
                            post_body_val = f"[Image: {content_href}]"
                        elif post_type in ["video", "crosspost"]:
                            post_body_val = f"[{post_type.capitalize()}: {content_href}]"
                        else:
                            post_body_val = f"[Link: {content_href}]"
            else:
                # Fallback for non-shreddit layout via article role
                article_elem = page.get_by_role("article").first
                if await article_elem.count() > 0:
                    paragraphs = article_elem.get_by_role("paragraph")
                    p_count = await paragraphs.count()
                    if p_count > 0:
                        p_texts = [(await paragraphs.nth(i).inner_text()).strip() for i in range(p_count)]
                        post_body_val = "\n\n".join([t for t in p_texts if t])

            # -------------------------------------------------------------
            # 3. Extract Top Comments
            # -------------------------------------------------------------
            if not quiet:
                print(f"[*] Extracting top comments (target: up to {max_comments})...")
            comments_list: List[CommentData] = []

            comment_elements = page.locator("shreddit-comment")
            c_count = await comment_elements.count()

            if c_count > 0:
                for i in range(min(c_count, max_comments + 5)):
                    if len(comments_list) >= max_comments:
                        break

                    c = comment_elements.nth(i)
                    c_author = await c.get_attribute("author") or "anonymous"
                    c_score = await c.get_attribute("score") or "0"

                    # Extract direct comment text, excluding nested child comments
                    direct_text = await c.evaluate("""el => {
                        const slot = el.querySelector('div[slot="comment"]');
                        if (slot) {
                            const clone = slot.cloneNode(true);
                            clone.querySelectorAll('shreddit-comment').forEach(nested => nested.remove());
                            return clone.innerText.trim();
                        }
                        const paragraphs = Array.from(el.querySelectorAll('p'));
                        if (paragraphs.length > 0) {
                            return paragraphs.map(p => p.innerText.trim()).join(' ');
                        }
                        return el.innerText.trim();
                    }""")

                    clean_text = re.sub(r"\s+", " ", direct_text).strip()
                    if clean_text:
                        comments_list.append(
                            CommentData(author=c_author, score=str(c_score), text=clean_text)
                        )

            # Fallback if shreddit-comment was not found: use role 'article' or comment testids
            if not comments_list:
                comment_articles = page.locator("article, [data-testid='comment']")
                art_count = await comment_articles.count()
                for i in range(min(art_count, max_comments)):
                    art = comment_articles.nth(i)
                    art_text = (await art.inner_text()).strip()
                    if art_text:
                        comments_list.append(
                            CommentData(author=f"commenter_{i+1}", score="N/A", text=art_text)
                        )

            if not quiet:
                print(f"[✓] Successfully scraped post metrics and {len(comments_list)} comments.")

            return ScrapedPostData(
                url=url,
                title=post_title or "Reddit Post",
                body=post_body_val,
                subreddit=subreddit_val,
                author=author_val,
                upvote_count=str(score_val),
                upvote_ratio=upvote_ratio_val,
                view_count=view_count_val,
                total_comments_count=str(comment_count_val),
                top_comments=comments_list,
            )

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


if __name__ == "__main__":
    asyncio.run(main())
