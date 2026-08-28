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
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin, urlparse

from playwright.async_api import async_playwright, BrowserContext, Page
from pydantic import BaseModel, Field

# Ensure repo root is in python path
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
    """Fetches the active WebSocket debugger URL from the CDP endpoint."""
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

        page = await get_clean_page(context)
        try:
            if not quiet:
                print(f"[*] Navigating to: {base_url}")
            await page.goto(base_url, wait_until="domcontentloaded")

            # Wait briefly for post feed links to render
            await asyncio.sleep(2.0)

            collected_posts: List[PostItem] = []
            seen_urls: Set[str] = set()
            page_num = 1
            max_pages = max(50, (limit // 20) + 10)

            while len(collected_posts) < limit and page_num <= max_pages:
                if not quiet:
                    print(f"[*] Scraping Page {page_num} (Collected so far: {len(collected_posts)}/{limit})...")

                # Extract posts using role-based link locators
                links_loc = page.get_by_role("link")
                link_count = await links_loc.count()

                new_on_page = 0
                for idx in range(link_count):
                    if len(collected_posts) >= limit:
                        break

                    link_el = links_loc.nth(idx)
                    try:
                        href = await link_el.get_attribute("href")
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

                        # Extract title text via role / text content
                        raw_title = await link_el.inner_text()
                        title = raw_title.strip() if raw_title else ""

                        # Filter out empty texts or metadata labels (e.g. comment timestamps)
                        if not title or title.startswith("New comment") or title.startswith("Last comment") or title.startswith("View ") or title.isdigit():
                            continue

                        # Record unique post
                        seen_urls.add(canonical_post_url)
                        post_item = PostItem(title=title, url=canonical_post_url)
                        collected_posts.append(post_item)
                        new_on_page += 1

                        if not quiet:
                            print(f"    [{len(collected_posts):>3}] {title} -> {canonical_post_url}")

                    except Exception:
                        continue

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
                # Wait for feed DOM update
                await asyncio.sleep(2.0)

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
