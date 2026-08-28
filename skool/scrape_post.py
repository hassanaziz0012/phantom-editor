"""
Skool Post Scraper using Playwright & Chrome DevTools Protocol (CDP).

Scrapes post details (author, date posted, body, attached images, likes count,
comments count) and threaded comments/replies from any Skool post URL by attaching
to a persistent native Google Chrome instance via CDP.

Usage:
    uv run python skool/scrape_post.py "https://www.skool.com/maker-zero/0-to-15kmonth-in-8-months-thanks-nick"
    uv run python skool/scrape_post.py "https://www.skool.com/..." --json
    uv run python skool/scrape_post.py "https://www.skool.com/..." --max-comments 50
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
# Pydantic Schemas for Scraped Skool Post Data
# ==============================================================================
class CommentData(BaseModel):
    author: str = Field(description="Username/Name of the commenter")
    text: str = Field(description="Content text of the comment")
    date_posted: str = Field(description="Date/time when comment was posted")
    replies: List["CommentData"] = Field(default_factory=list, description="List of direct replies to this comment")


class ScrapedSkoolPost(BaseModel):
    url: str = Field(description="URL of the post")
    author: str = Field(description="Author of the post")
    date_posted: str = Field(description="Date or time ago the post was published")
    body: str = Field(description="Full text body of the post")
    attached_images: List[str] = Field(default_factory=list, description="List of image URLs attached to the post")
    likes_count: str = Field(description="Number of likes on the post")
    comments_count: str = Field(description="Total comments count according to post header")
    comments: List[CommentData] = Field(default_factory=list, description="List of scraped comments and replies")


# ==============================================================================
# Scraper Logic
# ==============================================================================
async def scrape_skool_post(
    url: str,
    headless: bool = False,
    max_comments: int = 100,
    port: int = CDP_PORT,
    profile_dir: Optional[Path] = None,
    quiet: bool = False,
) -> ScrapedSkoolPost:
    """
    Scrapes post details and threaded comments from a Skool post URL using Playwright via Chrome CDP.
    Uses role and accessibility-based locators to expand content and parse data reliably.

    Args:
        url: URL of the Skool post.
        headless: Whether to run Chrome headless (default False for headful).
        max_comments: Maximum number of comments (including replies) to extract (default: 100).
        port: Chrome remote debugging port (default: 9222).
        profile_dir: Path to Chrome profile directory.
        quiet: If True, suppresses all terminal logging (used with --json).

    Returns:
        ScrapedSkoolPost instance with all extracted data.
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

            # Wait for main post element to be visible
            try:
                await page.wait_for_selector("div[data-post-id], button:has-text('Like')", timeout=15000)
            except Exception:
                if not quiet:
                    print("[!] Warning: Timed out waiting for post selector, continuing with available DOM...")

            await asyncio.sleep(1.5)

            # ------------------------------------------------------------------
            # 1. Expand Post "See more" text if truncated
            # ------------------------------------------------------------------
            try:
                see_more_post = page.get_by_text("See more", exact=True)
                if await see_more_post.count() > 0:
                    await see_more_post.first.click(timeout=2000)
                    await asyncio.sleep(0.3)
            except Exception:
                pass

            # ------------------------------------------------------------------
            # 2. Scroll to Load Comments Up to max_comments
            # ------------------------------------------------------------------
            if not quiet:
                print(f"[*] Loading comments up to {max_comments}...")

            last_reply_btn_count = 0
            no_growth_count = 0
            while no_growth_count < 3:
                # Count current reply buttons on page
                curr_count = await page.get_by_role("button", name=re.compile(r"^Reply$", re.I)).count()
                if curr_count >= max_comments:
                    break

                if curr_count == last_reply_btn_count:
                    no_growth_count += 1
                else:
                    no_growth_count = 0
                last_reply_btn_count = curr_count

                # Scroll down
                await page.evaluate("window.scrollBy(0, 2500)")
                await asyncio.sleep(0.8)

            # ------------------------------------------------------------------
            # 3. Expand "View X more replies" buttons
            # ------------------------------------------------------------------
            if not quiet:
                print("[*] Expanding reply threads...")

            max_expansions = 25
            expansions = 0
            while expansions < max_expansions:
                view_replies_loc = page.get_by_text(re.compile(r"View\s+\d+\s+more\s+repl", re.I))
                b_count = await view_replies_loc.count()
                if b_count == 0:
                    break

                try:
                    btn = view_replies_loc.first
                    await btn.scroll_into_view_if_needed()
                    await btn.click(timeout=2000)
                    expansions += 1
                    await asyncio.sleep(0.6)
                except Exception:
                    break

            # ------------------------------------------------------------------
            # 4. Expand "See more" on all long comment texts via fast JS click
            # ------------------------------------------------------------------
            if not quiet:
                print("[*] Expanding comment texts...")

            await page.evaluate("""() => {
                const seeMores = Array.from(document.querySelectorAll('span, div, button')).filter(el => el.innerText && el.innerText.trim() === 'See more');
                for (const el of seeMores) {
                    try { el.click(); } catch (_) {}
                }
            }""")
            await asyncio.sleep(0.3)

            # ------------------------------------------------------------------
            # 5. Extract Structured Post & Comment Data
            # ------------------------------------------------------------------
            if not quiet:
                print("[*] Extracting structured post details and comments...")

            extracted = await page.evaluate("""(maxComments) => {
                const data = {};
                
                // --- 1. POST DETAILS ---
                const postRoot = document.querySelector('div[data-post-id]');
                if (!postRoot) return { error: "Post container not found on page" };

                // Author
                const postAuthorLink = Array.from(postRoot.querySelectorAll('a[href*="/@"]')).find(a => {
                    return a.innerText && /[a-zA-Z]/.test(a.innerText.trim()) && !a.innerText.trim().startsWith('@');
                });
                data.author = postAuthorLink ? postAuthorLink.innerText.trim().replace(/\\u00a0/g, ' ') : "Unknown";

                // Date Posted
                let postDate = "Unknown";
                if (postAuthorLink) {
                    const header = postAuthorLink.closest('div[class*="sc-58b51d96"]') || postAuthorLink.parentElement.parentElement.parentElement;
                    if (header) {
                        const m = header.innerText.match(/(\\d+[smhdwy]|yesterday|just now|\\d+\\s+(?:hours?|days?|months?|years?|minutes?)\\s+ago)/i);
                        if (m) postDate = m[1];
                    }
                }
                data.date_posted = postDate;

                // Title & Body
                const titleEl = postRoot.querySelector('div[class*="ckeDPS"], span[class*="iliBBR"]');
                const title = titleEl ? titleEl.innerText.trim() : "";
                
                const bodyEl = postRoot.querySelector('div[class*="gMFF"], div[class*="ProseMirror"]');
                let bodyText = bodyEl ? bodyEl.innerText.trim() : "";

                if (title && bodyText) {
                    data.body = title + "\\n\\n" + bodyText;
                } else if (title) {
                    data.body = title;
                } else {
                    data.body = bodyText;
                }

                // Attached Images
                const attachedImages = [];
                const attachmentsContainer = postRoot.querySelector('.skool-attachments-root, [class*="attachments"]');
                if (attachmentsContainer) {
                    const els = Array.from(attachmentsContainer.querySelectorAll('.skool-attachment, [class*="attachment"], div[style*="background-image"], img'));
                    for (const el of els) {
                        if (el.tagName === 'IMG' && el.src) {
                            attachedImages.push(el.src);
                        } else if (el.style && el.style.backgroundImage) {
                            const m = el.style.backgroundImage.match(/url\\(["']?(.*?)["']?\\)/);
                            if (m && m[1]) attachedImages.push(m[1]);
                        }
                    }
                }
                data.attached_images = Array.from(new Set(attachedImages));

                // Likes Count
                let likesCount = "0";
                const likeBtns = Array.from(postRoot.querySelectorAll('button')).filter(b => b.innerText && b.innerText.includes('Like'));
                if (likeBtns.length > 0 && likeBtns[0].parentElement) {
                    const txt = likeBtns[0].parentElement.innerText;
                    const m = txt.match(/Like\\s*\\n?\\s*(\\d+[kKmM]?)/i) || txt.match(/(\\d+[kKmM]?)/);
                    if (m) likesCount = m[1];
                }
                data.likes_count = likesCount;

                // Comments Count
                let commentsCount = "0";
                const commentCountEl = Array.from(postRoot.querySelectorAll('*')).find(el => el.innerText && /^\\d+\\s+comments$/i.test(el.innerText.trim()) && el.children.length === 0);
                if (commentCountEl) {
                    const m = commentCountEl.innerText.match(/(\\d+)/);
                    if (m) commentsCount = m[1];
                }
                data.comments_count = commentsCount;

                // --- 2. COMMENTS DETAILS ---
                const replyButtons = Array.from(document.querySelectorAll('button')).filter(b => b.innerText && b.innerText.trim() === 'Reply');
                if (replyButtons.length === 0) {
                    data.comments = [];
                    return data;
                }

                let el = replyButtons[0];
                while (el && el.children.length < 10) {
                    el = el.parentElement;
                }
                const commentsContainer = el;

                function parseSingleComment(node) {
                    const aLink = Array.from(node.querySelectorAll('a[href*="/@"]')).find(a => {
                        return a.innerText && /[a-zA-Z]/.test(a.innerText.trim()) && !a.innerText.trim().startsWith('@');
                    });
                    const cAuthor = aLink ? aLink.innerText.trim().replace(/\\u00a0/g, ' ') : "Unknown";

                    let cDate = "Unknown";
                    const allSpans = Array.from(node.querySelectorAll('span, div'));
                    for (const s of allSpans) {
                        if (s.children.length === 0 && s.innerText) {
                            const txt = s.innerText.trim();
                            const m = txt.match(/(?:•\\s*)?(\\d+[smhdwy]|yesterday|just now|\\d+\\s+(?:hours?|days?|months?|years?|minutes?)\\s+ago)/i);
                            if (m) {
                                cDate = m[1];
                                break;
                            }
                        }
                    }

                    let cTextContainer = node.querySelector('div[class*="gMFF"], div[class*="ProseMirror"]');
                    let cText = "";
                    if (cTextContainer) {
                        cText = cTextContainer.innerText.trim();
                    } else {
                        const textDivs = Array.from(node.querySelectorAll('div, span')).filter(d => {
                            return d.children.length === 0 && 
                                   !d.innerText.includes('Reply') && 
                                   !d.innerText.includes(cAuthor) && 
                                   !d.innerText.includes(cDate) &&
                                   !/^\\d+$/.test(d.innerText.trim());
                        });
                        cText = textDivs.map(d => d.innerText.trim()).filter(Boolean).join(' ');
                    }

                    return {
                        author: cAuthor,
                        text: cText,
                        date_posted: cDate,
                        replies: []
                    };
                }

                const commentsList = [];
                let totalCommentCount = 0;
                let currentTopLevel = null;

                for (let i = 0; i < commentsContainer.children.length; i++) {
                    if (totalCommentCount >= maxComments) break;

                    const child = commentsContainer.children[i];
                    const text = child.innerText.trim();
                    if (/view\\s+\\d+\\s+more\\s+repl/i.test(text)) continue;

                    const computed = window.getComputedStyle(child);
                    const paddingLeft = parseFloat(computed.paddingLeft) || 0;
                    const marginLeft = parseFloat(computed.marginLeft) || 0;
                    const isIndented = (paddingLeft > 20 || marginLeft > 20);

                    // Check if child is a wrapper containing multiple reply items
                    if (isIndented && child.children.length > 1 && !child.querySelector('button[innerText*="Reply"]')) {
                        for (let j = 0; j < child.children.length; j++) {
                            if (totalCommentCount >= maxComments) break;
                            const repChild = child.children[j];
                            const parsedRep = parseSingleComment(repChild);
                            if (parsedRep.author !== "Unknown" || parsedRep.text) {
                                if (currentTopLevel) {
                                    currentTopLevel.replies.push(parsedRep);
                                } else {
                                    commentsList.push(parsedRep);
                                }
                                totalCommentCount++;
                            }
                        }
                    } else if (isIndented) {
                        // Single indented reply
                        const parsedRep = parseSingleComment(child);
                        if (parsedRep.author !== "Unknown" || parsedRep.text) {
                            if (currentTopLevel) {
                                currentTopLevel.replies.push(parsedRep);
                            } else {
                                commentsList.push(parsedRep);
                            }
                            totalCommentCount++;
                        }
                    } else {
                        // Top level comment
                        const parsedTop = parseSingleComment(child);
                        if (parsedTop.author !== "Unknown" || parsedTop.text) {
                            currentTopLevel = parsedTop;
                            commentsList.push(currentTopLevel);
                            totalCommentCount++;
                        }
                    }
                }

                data.comments = commentsList;
                return data;
            }""", max_comments)

            if "error" in extracted:
                raise RuntimeError(extracted["error"])

            comments_data = [CommentData(**c) for c in extracted.get("comments", [])]

            post_obj = ScrapedSkoolPost(
                url=url,
                author=extracted.get("author", "Unknown"),
                date_posted=extracted.get("date_posted", "Unknown"),
                body=extracted.get("body", ""),
                attached_images=extracted.get("attached_images", []),
                likes_count=str(extracted.get("likes_count", "0")),
                comments_count=str(extracted.get("comments_count", "0")),
                comments=comments_data,
            )

            if not quiet:
                total_comments_extracted = sum(1 + len(c.replies) for c in post_obj.comments)
                print(f"[✓] Successfully scraped post by {post_obj.author} with {total_comments_extracted} comments/replies.")

            return post_obj

        finally:
            if not quiet:
                print("[*] Closing scraper page tab...")
            await page.close()


# ==============================================================================
# CLI Formatter & Main
# ==============================================================================
def print_formatted_post(data: ScrapedSkoolPost):
    """Prints a clean human-readable summary of the scraped post and comments."""
    total_comments_count = sum(1 + len(c.replies) for c in data.comments)
    print("\n" + "=" * 75)
    print("                           SCRAPED SKOOL POST")
    print("=" * 75)
    print(f"Author:          {data.author}")
    print(f"Date Posted:     {data.date_posted}")
    print(f"Likes:           {data.likes_count}")
    print(f"Total Comments:  {data.comments_count} (Scraped: {total_comments_count})")
    print(f"Attached Images: {len(data.attached_images)}")
    for idx, img in enumerate(data.attached_images, 1):
        print(f"  [{idx}] {img}")
    print(f"URL:             {data.url}")
    print("-" * 75)
    print("Post Body:")
    indented_body = "\n".join(f"  {line}" for line in data.body.splitlines())
    print(indented_body if indented_body.strip() else "  [Empty body]")
    print("-" * 75)

    if data.comments:
        print("\n[COMMENTS]")
        for c_idx, c in enumerate(data.comments, start=1):
            print(f"\n  ({c_idx}) {c.author} • {c.date_posted}")
            indented_c_text = "\n".join(f"      {line}" for line in c.text.splitlines())
            print(indented_c_text)

            if c.replies:
                for r_idx, r in enumerate(c.replies, start=1):
                    print(f"      └── [{c_idx}.{r_idx}] {r.author} • {r.date_posted}")
                    indented_r_text = "\n".join(f"            {line}" for line in r.text.splitlines())
                    print(indented_r_text)
    else:
        print("\nNo comments scraped.")

    print("\n" + "=" * 75)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape Skool post and threaded comments using Playwright via Chrome DevTools Protocol (CDP)."
    )
    parser.add_argument("url", help="Skool post URL to scrape.")
    parser.add_argument(
        "--max-comments",
        type=int,
        default=100,
        help="Maximum total comments to scrape including replies (default: 100).",
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

    scraped = await scrape_skool_post(
        url=args.url,
        headless=args.headless,
        max_comments=args.max_comments,
        port=args.port,
        quiet=args.json,
    )

    if args.json:
        print(json.dumps(scraped.model_dump(), indent=2, ensure_ascii=False))
    else:
        print_formatted_post(scraped)


if __name__ == "__main__":
    asyncio.run(main())
