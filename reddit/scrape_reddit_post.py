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
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

from playwright.async_api import async_playwright, BrowserContext, Page
from pydantic import BaseModel, Field

# Ensure repo root is in python path for module imports
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from reddit.reddit_json import check_post_data, decode_response, json_url, post_fields, seconds_until_reset

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
    """Always opens a dedicated new tab for the worker process to avoid tab collisions."""
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
            post_url = url
            if "/comments/" not in urlsplit(url).path:
                # Share links (/r/<sub>/s/<id>, redd.it/<id>) only redirect to the post, so follow
                # the redirect first and fetch the .json of the post it lands on
                await page.goto(url, wait_until="commit", timeout=30000)
                post_url = page.url
            target_url = json_url(post_url)
            if not quiet:
                print(f"[*] Navigating to: {target_url}")

            # Navigate with retry logic for rate limits (HTTP 429) & transient HTTP/network failures
            max_retries = 3
            retry_delay = 240  # seconds to sleep before each retry
            max_nav_attempts = 1 + max_retries
            nav_success = False
            last_err = None

            for attempt in range(1, max_nav_attempts + 1):
                try:
                    if not quiet and attempt > 1:
                        print(f"[*] Retry attempt {attempt - 1}/{max_retries} for {url}...")
                    response = await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
                    if response and response.status in (429, 502, 503, 504):
                        retry_after = response.headers.get("retry-after") if response else None
                        attempt_desc = "initial attempt" if attempt == 1 else f"retry {attempt - 1}/{max_retries}"
                        # On a rate limit, wait until Reddit says its window resets; otherwise use the fixed delay
                        reset_wait = seconds_until_reset(response.headers) if response.status == 429 else None
                        wait = reset_wait if reset_wait is not None else retry_delay
                        wait_reason = "until Reddit's rate limit resets" if reset_wait is not None else "default delay"
                        if not quiet:
                            print(f"[!] Warning: Received HTTP {response.status} on {attempt_desc}")
                            if retry_after:
                                print(f"[*] Reddit 'Retry-After' header: {retry_after}")
                            else:
                                print("[*] Reddit 'Retry-After' header: None (not present)")
                        else:
                            print(
                                f"[!] Warning: Received HTTP {response.status} on {attempt_desc}. "
                                f"Retry-After: {retry_after or 'None'}",
                                file=sys.stderr,
                            )

                        if attempt < max_nav_attempts:
                            if not quiet:
                                print(f"[*] Sleeping {wait:.0f}s ({wait_reason}) before retry {attempt}/{max_retries}...")
                            else:
                                print(f"[*] Sleeping {wait:.0f}s ({wait_reason}) before retry {attempt}/{max_retries}...", file=sys.stderr)
                            await asyncio.sleep(wait)
                            continue
                        else:
                            last_err = RuntimeError(
                                f"Failed to navigate to {url}: Received HTTP {response.status} "
                                f"(Retry-After: {retry_after or 'None'}) after {max_retries} retries."
                            )
                            break

                    nav_success = True
                    break
                except Exception as e:
                    last_err = e
                    err_str = str(e)
                    if "ERR_HTTP_RESPONSE_CODE_FAILURE" in err_str or "timeout" in err_str.lower() or "net::" in err_str:
                        if attempt < max_nav_attempts:
                            if not quiet:
                                print(f"[!] Warning: Transient navigation failure ({err_str[:60]}...). Sleeping {retry_delay}s before retry {attempt}/{max_retries}...")
                            else:
                                print(f"[!] Warning: Transient navigation failure ({err_str[:60]}...). Sleeping {retry_delay}s before retry {attempt}/{max_retries}...", file=sys.stderr)
                            await asyncio.sleep(retry_delay)
                            continue
                    raise e

            if not nav_success:
                if last_err:
                    raise last_err
                raise RuntimeError(f"Failed to navigate to {url}: Navigation was not successful.")

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
