#!/usr/bin/env python3
"""
Threads Publisher
=================
Automates publishing single posts and multi-post threads with optional image attachments
to Threads (threads.com / threads.net) using Playwright over Chrome DevTools Protocol (CDP).

Adheres to:
- Persistent Chrome CDP Architecture (PLAYWRIGHT_CDP_GUIDE.md)
- Role & Accessibility-first element selectors (get_by_role, get_by_label, get_by_text)

Usage:
    # Single post from a text file:
    python threads/post_thread.py --posts post1.txt

    # Multi-post thread from multiple text files:
    python threads/post_thread.py --posts post1.txt post2.txt post3.txt

    # Single post or thread with image attachments:
    python threads/post_thread.py --posts post1.txt post2.txt --images img1.png img2.png
"""

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import List, Optional, Union
from playwright.async_api import async_playwright, BrowserContext, Page, TimeoutError as PlaywrightTimeoutError

# ==============================================================================
# Configuration & Constants
# ==============================================================================
PROFILE_DIR = Path.home() / "Desktop" / "browser-profiles" / "cdp"
CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
THREADS_URL = "https://www.threads.com"
MAX_CHARS_PER_POST = 500

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

# ==============================================================================
# Chrome CDP Lifecycle Helpers
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
    """Fetches WebSocket debugger URL to avoid Node.js DEP0169 url.parse warning."""
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

    subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

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
    return context


async def get_clean_page(context: BrowserContext) -> Page:
    """Reuses an existing blank page or opens a new tab to avoid collisions."""
    for page in context.pages:
        if page.url in ("about:blank", "chrome://newtab/", "chrome://new-tab-page/"):
            await page.add_init_script(STEALTH_INIT_SCRIPT)
            return page
    page = await context.new_page()
    await page.add_init_script(STEALTH_INIT_SCRIPT)
    return page


# ==============================================================================
# Content Validation Helper
# ==============================================================================
def read_and_validate_posts(post_file_paths: List[Union[str, Path]]) -> List[str]:
    """
    Reads text files from the provided paths and validates that none exceed
    the 500-character limit.

    :param post_file_paths: List of paths to post text documents.
    :return: List of validated post string contents.
    """
    if not post_file_paths:
        raise ValueError("At least one post file path must be provided via --posts.")

    posts_content: List[str] = []
    for idx, path_str in enumerate(post_file_paths):
        path = Path(path_str).resolve()
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(f"Post file not found: {path_str}")

        content = path.read_text(encoding="utf-8").strip()
        if not content:
            raise ValueError(f"Post file is empty: {path_str}")

        char_count = len(content)
        if char_count > MAX_CHARS_PER_POST:
            raise ValueError(
                f"Post {idx + 1} ({path.name}) exceeds the {MAX_CHARS_PER_POST}-character limit "
                f"({char_count} characters). Please shorten the content or split into another post."
            )
        posts_content.append(content)

    return posts_content


# ==============================================================================
# Threads Publishing Workflow
# ==============================================================================
async def post_thread(
    posts_text: List[str],
    image_paths: Optional[List[Union[str, Path]]] = None,
    headless: bool = False,
    page: Optional[Page] = None,
) -> str:
    """
    Publishes a single post or multi-part thread to Threads and returns the direct post URL.

    :param posts_text: List of validated strings (each <= 500 chars).
    :param image_paths: Optional list of file paths to local images to attach.
    :param headless: If starting a fresh Chrome instance, whether to run headless.
    :param page: Optional existing Playwright Page to use.
    :return: Direct URL of the published thread/post.
    """
    if not posts_text:
        raise ValueError("Cannot post an empty thread. Provide at least one post's text.")

    # Validate image files if provided
    resolved_images: List[Path] = []
    if image_paths:
        for img in image_paths:
            p = Path(img).resolve()
            if not p.exists() or not p.is_file():
                raise FileNotFoundError(f"Image file not found: {p}")
            resolved_images.append(p)

    close_page_when_done = False

    async with async_playwright() as playwright:
        if page is None:
            context = await get_cdp_browser_context(playwright, headless=headless)
            page = await get_clean_page(context)
            close_page_when_done = True
        else:
            context = page.context

        print(f"Navigating to {THREADS_URL}...")
        await page.goto(THREADS_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)

        # ----------------------------------------------------------------------
        # Step 1: Open Composer Modal
        # ----------------------------------------------------------------------
        print("Looking for composer prompt ('What\\'s new?')...")
        # Try accessibility label first, then text match, then role buttons
        prompt_btn = page.get_by_label("Empty text field. Type to compose a new post.")
        if await prompt_btn.count() == 0:
            prompt_btn = page.get_by_text("What's new?").first
        if await prompt_btn.count() == 0:
            prompt_btn = page.get_by_role("button", name="New thread")
        if await prompt_btn.count() == 0:
            prompt_btn = page.get_by_role("link", name="New thread")

        if await prompt_btn.count() == 0:
            raise RuntimeError(
                "Could not find the 'What\\'s new?' compose prompt. "
                "Ensure you are logged into Threads in the browser profile."
            )

        await prompt_btn.scroll_into_view_if_needed()
        await prompt_btn.click()
        await page.wait_for_timeout(1500)

        # ----------------------------------------------------------------------
        # Step 2: Locate Composer Modal Dialog
        # ----------------------------------------------------------------------
        dialog = page.get_by_role("dialog")
        await dialog.wait_for(state="visible", timeout=15000)
        print("Composer modal opened.")

        # ----------------------------------------------------------------------
        # Step 3: Populate Posts in the Thread
        # ----------------------------------------------------------------------
        total_posts = len(posts_text)
        print(f"Composing thread with {total_posts} post(s)...")

        for idx, post_content in enumerate(posts_text):
            if idx > 0:
                print(f"Adding post {idx + 1}/{total_posts} to thread...")
                # Click 'Add to thread'
                add_thread_btn = dialog.get_by_text("Add to thread").or_(
                    dialog.get_by_role("button", name="Add to thread")
                )
                if await add_thread_btn.count() == 0:
                    raise RuntimeError(f"Could not find 'Add to thread' button for post {idx + 1}.")
                await add_thread_btn.first.click()
                await page.wait_for_timeout(1000)

            # Locate the corresponding textbox
            textboxes = dialog.get_by_role("textbox")
            # Wait until the required textbox index is available
            for _ in range(20):
                if await textboxes.count() > idx:
                    break
                await page.wait_for_timeout(250)

            current_textbox = textboxes.nth(idx)
            await current_textbox.wait_for(state="visible", timeout=5000)
            await current_textbox.click()
            await current_textbox.fill(post_content)
            print(f"  Filled post {idx + 1}/{total_posts} ({len(post_content)} chars).")
            await page.wait_for_timeout(500)

        # ----------------------------------------------------------------------
        # Step 4: Handle Image Attachments (if provided)
        # ----------------------------------------------------------------------
        if resolved_images:
            print(f"Attaching {len(resolved_images)} image(s)...")
            file_input = dialog.locator('input[type="file"]').first
            if await file_input.count() > 0:
                await file_input.set_input_files([str(p) for p in resolved_images])
                print("Image file(s) attached.")
                # Give browser time to generate image previews
                await page.wait_for_timeout(2500)
            else:
                print("Warning: File input element not found in composer dialog.")

        # ----------------------------------------------------------------------
        # Step 5: Post the Thread & Wait for Upload Processing
        # ----------------------------------------------------------------------
        post_btn = dialog.get_by_role("button", name="Post", exact=True)
        if await post_btn.count() == 0:
            post_btn = dialog.locator('div[role="button"]').filter(has_text="Post").last

        print("Waiting for media processing & Post button to enable...")
        max_wait_seconds = 120
        start_time = time.time()
        is_ready = False

        while time.time() - start_time < max_wait_seconds:
            if await post_btn.count() > 0 and not await post_btn.is_disabled():
                is_ready = True
                break
            await page.wait_for_timeout(1000)

        if not is_ready:
            raise TimeoutError("Post button remained disabled. Check text length or image processing.")

        print("Publishing thread...")
        await post_btn.click()

        # ----------------------------------------------------------------------
        # Step 6: Extract Direct Post URL from the Toast Notification
        # ----------------------------------------------------------------------
        print("Waiting for toast notification and 'View' link...")
        post_url = ""

        try:
            # Look for the 'View' link inside the 'Posted' toast notification
            view_link = page.get_by_role("link", name="View", exact=True)
            if await view_link.count() == 0:
                view_link = page.locator('a').filter(has_text="View")
            if await view_link.count() == 0:
                view_link = page.get_by_text("View", exact=True)

            await view_link.first.wait_for(state="visible", timeout=20000)
            href = await view_link.first.get_attribute("href")
            if href:
                if href.startswith("/"):
                    post_url = f"https://www.threads.com{href}"
                elif not href.startswith("http"):
                    post_url = f"https://www.threads.com/{href.lstrip('/')}"
                else:
                    post_url = href
            else:
                # If href is not directly on the element, check parent anchor tag
                parent_href = await view_link.first.evaluate("el => el.closest('a') ? el.closest('a').getAttribute('href') : null")
                if parent_href:
                    if parent_href.startswith("/"):
                        post_url = f"https://www.threads.com{parent_href}"
                    else:
                        post_url = parent_href
        except Exception as toast_err:
            print(f"Note on toast detection: {toast_err}")

        # Wait for modal to dismiss
        try:
            await dialog.wait_for(state="hidden", timeout=10000)
        except Exception:
            pass

        if close_page_when_done:
            await page.close()

        if post_url:
            print("\n=======================================================")
            print("🎉 Thread posted successfully!")
            print(f"Direct Post URL: {post_url}")
            print("=======================================================\n")
            return post_url
        else:
            print("\nThread posted, but direct post link could not be captured automatically.")
            return "https://www.threads.com"


# ==============================================================================
# CLI Entry Point
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Publish single posts and multi-part threads to Threads automatically.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Publish a single post from text:
  python threads/post_thread.py "Hello Threads!"

  # Publish a single post from a text file:
  python threads/post_thread.py --posts post1.txt

  # Publish a multi-part thread from multiple text files:
  python threads/post_thread.py --posts post1.txt post2.txt post3.txt

  # Publish a thread with image attachment(s):
  python threads/post_thread.py --posts post1.txt post2.txt --images chart.png diagram.png
        """,
    )
    parser.add_argument(
        "text",
        nargs="?",
        default="",
        help="Text content of a single Threads post.",
    )
    parser.add_argument(
        "-t", "--text",
        dest="text_flag",
        help="Alternative flag for post text.",
    )
    parser.add_argument(
        "-p", "--posts",
        dest="posts",
        nargs="+",
        help="List of file paths to text documents (each <= 500 chars).",
    )
    parser.add_argument(
        "-i", "--image", "--images",
        dest="images",
        nargs="+",
        help="Optional path(s) to local image file(s) to attach.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Launch Chrome in headless mode if not already running.",
    )

    args = parser.parse_args()

    content = (args.text_flag or args.text or "").strip()
    if args.posts:
        try:
            validated_posts = read_and_validate_posts(args.posts)
        except Exception as err:
            print(f"\nValidation Error: {err}", file=sys.stderr)
            sys.exit(1)
    elif content:
        if len(content) > MAX_CHARS_PER_POST:
            print(
                f"\nValidation Error: Post exceeds the {MAX_CHARS_PER_POST}-character limit "
                f"({len(content)} characters).",
                file=sys.stderr,
            )
            sys.exit(1)
        validated_posts = [content]
    elif args.images:
        validated_posts = [""]
    else:
        parser.error("You must provide post text, post file(s) via --posts, or at least one image.")

    try:
        asyncio.run(
            post_thread(
                posts_text=validated_posts,
                image_paths=args.images,
                headless=args.headless,
            )
        )
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.", file=sys.stderr)
        sys.exit(130)
    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
