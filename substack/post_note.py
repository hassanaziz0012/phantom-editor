#!/usr/bin/env python3
"""
Substack Notes Publisher
========================
Automates creating and posting Substack Notes with text, image(s), and video attachments
using Playwright over Chrome DevTools Protocol (CDP).

Adheres to:
- Persistent Chrome CDP Architecture (PLAYWRIGHT_CDP_GUIDE.md)
- Role & Accessibility-first element selectors (get_by_role, get_by_text)

Usage:
    # Text-only note
    python substack/post_note.py "Just launched our new automation pipeline!"

    # Note with image(s)
    python substack/post_note.py "Check out our architecture diagram:" -i ./diagram.png

    # Note with multiple images
    python substack/post_note.py "Visual updates:" -i ./img1.png ./img2.png

    # Note with video
    python substack/post_note.py "Demo video below:" -v ./demo.mp4
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
from typing import List, Optional, Union
from playwright.async_api import async_playwright, BrowserContext, Page, TimeoutError as PlaywrightTimeoutError

# ==============================================================================
# Configuration & Constants
# ==============================================================================
PROFILE_DIR = Path.home() / "Desktop" / "browser-profiles" / "cdp"
CDP_PORT = 9222
CDP_URL = f"http://127.0.0.1:{CDP_PORT}"
SUBSTACK_URL = "https://substack.com/home"

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
# Substack Note Publishing Workflow
# ==============================================================================
async def post_substack_note(
    text: str,
    image_paths: Optional[List[Union[str, Path]]] = None,
    video_path: Optional[Union[str, Path]] = None,
    headless: bool = False,
    page: Optional[Page] = None,
) -> str:
    """
    Publishes a note to Substack and returns the direct link to the posted note.

    :param text: Body text content of the note.
    :param image_paths: Optional list of file paths to local images to attach.
    :param video_path: Optional file path to a local video to attach.
    :param headless: If starting a fresh Chrome instance, whether to run headless.
    :param page: Optional existing Playwright Page to use.
    :return: Direct URL of the published Substack note.
    """
    if not text and not image_paths and not video_path:
        raise ValueError("Cannot post an empty note. Provide text, images, or video.")

    # Validate image files if provided
    resolved_images: List[Path] = []
    if image_paths:
        for img in image_paths:
            p = Path(img).resolve()
            if not p.exists() or not p.is_file():
                raise FileNotFoundError(f"Image file not found: {p}")
            resolved_images.append(p)

    # Validate video file if provided
    resolved_video: Optional[Path] = None
    if video_path:
        p = Path(video_path).resolve()
        if not p.exists() or not p.is_file():
            raise FileNotFoundError(f"Video file not found: {p}")
        resolved_video = p

    close_page_when_done = False

    async with async_playwright() as playwright:
        if page is None:
            context = await get_cdp_browser_context(playwright, headless=headless)
            page = await get_clean_page(context)
            close_page_when_done = True
        else:
            context = page.context

        # Grant clipboard permissions on substack.com origin
        try:
            await context.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://substack.com")
        except Exception:
            pass

        print(f"Navigating to {SUBSTACK_URL}...")
        await page.goto(SUBSTACK_URL, wait_until="domcontentloaded")
        await page.wait_for_timeout(2500)

        # ----------------------------------------------------------------------
        # Step 1: Open Composer Modal
        # ----------------------------------------------------------------------
        print("Looking for 'What\\'s on your mind?' compose prompt...")
        # Role and accessibility-first selector for the inline composer prompt
        prompt_btn = page.get_by_role("button", name="New post").filter(has_text="What's on your mind?")
        if await prompt_btn.count() == 0:
            prompt_btn = page.get_by_role("button", name="What's on your mind?")
        if await prompt_btn.count() == 0:
            prompt_btn = page.get_by_text("What's on your mind?").first

        if await prompt_btn.count() == 0:
            raise RuntimeError("Could not find the 'What\\'s on your mind?' box. Ensure you are logged into Substack.")

        await prompt_btn.scroll_into_view_if_needed()
        await prompt_btn.click()
        await page.wait_for_timeout(1000)

        # ----------------------------------------------------------------------
        # Step 2: Locate Modal Dialog
        # ----------------------------------------------------------------------
        dialog = page.get_by_role("dialog")
        await dialog.wait_for(state="visible", timeout=10000)
        print("Composer modal opened.")

        # ----------------------------------------------------------------------
        # Step 3: Handle Media Attachments (Images & Video)
        # ----------------------------------------------------------------------
        if resolved_images:
            print(f"Attaching {len(resolved_images)} image(s)...")
            img_input = dialog.locator('input[type="file"][accept*="image"]').first
            if await img_input.count() == 0:
                img_input = page.locator('input[type="file"][accept*="image"]').first

            if await img_input.count() > 0:
                await img_input.set_input_files([str(img) for img in resolved_images])
                print("Image file(s) selected.")
            else:
                print("Warning: Image input element not found in modal.")

        if resolved_video:
            print(f"Attaching video: {resolved_video.name}...")
            vid_input = dialog.locator('input[type="file"][accept*="video"]').first
            if await vid_input.count() == 0:
                vid_input = page.locator('input[type="file"][accept*="video"]').first

            if await vid_input.count() > 0:
                await vid_input.set_input_files(str(resolved_video))
                print("Video file selected.")
            else:
                print("Warning: Video input element not found in modal.")

        # ----------------------------------------------------------------------
        # Step 4: Enter Note Text
        # ----------------------------------------------------------------------
        if text:
            print("Entering note text...")
            editor = dialog.get_by_role("textbox", name="What's on your mind?")
            if await editor.count() == 0:
                editor = dialog.get_by_role("textbox").first

            await editor.click()
            await editor.fill(text)
            await page.wait_for_timeout(500)

        # ----------------------------------------------------------------------
        # Step 5: Wait for Uploads & Post Button to Enable
        # ----------------------------------------------------------------------
        post_btn = dialog.get_by_role("button", name="Post")
        if await post_btn.count() == 0:
            post_btn = dialog.locator('button[data-testid="composer-post"]').first

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
            raise TimeoutError("Post button remained disabled after timeout. Check file sizes or media processing.")

        print("Post button is active. Publishing note...")
        await post_btn.click()

        # Wait for modal dialog to dismiss
        try:
            await dialog.wait_for(state="hidden", timeout=15000)
            print("Modal dismissed successfully.")
        except Exception:
            print("Note: Modal dismissal wait completed.")

        await page.wait_for_timeout(3000)

        # ----------------------------------------------------------------------
        # Step 6: Extract Direct Note URL via Share -> Copy Link
        # ----------------------------------------------------------------------
        print("Extracting note link from the top of the feed...")
        note_url = ""

        # Find the Share button on the topmost note
        share_btn = page.get_by_role("button", name="Share").first
        if await share_btn.count() > 0:
            await share_btn.scroll_into_view_if_needed()
            await share_btn.click()
            await page.wait_for_timeout(1000)

            # Click 'Copy link' in dropdown menu
            copy_link_btn = page.get_by_role("menuitem", name="Copy link")
            if await copy_link_btn.count() == 0:
                copy_link_btn = page.get_by_role("button", name="Copy link")
            if await copy_link_btn.count() == 0:
                copy_link_btn = page.get_by_text("Copy link").first

            if await copy_link_btn.count() > 0:
                await copy_link_btn.click()
                await page.wait_for_timeout(1000)

                # Read link from clipboard
                try:
                    copied_text = await page.evaluate("() => navigator.clipboard.readText()")
                    if copied_text and "substack.com" in copied_text:
                        note_url = copied_text.strip()
                except Exception as clip_err:
                    print(f"Note on clipboard reading: {clip_err}")

        # Clean tab
        if close_page_when_done:
            await page.close()

        if note_url:
            print("\n=======================================================")
            print("🎉 Note published successfully!")
            print(f"Direct Note URL: {note_url}")
            print("=======================================================\n")
            return note_url
        else:
            print("\nNote published, but direct link could not be copied automatically.")
            return "https://substack.com/home"


# ==============================================================================
# CLI Entry Point
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Publish Substack Notes with optional images and video.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python substack/post_note.py "Hello Substack Notes!"
  python substack/post_note.py "Screenshot from our newest release" -i ./screenshot.png
  python substack/post_note.py "Gallery of images" -i ./img1.png ./img2.png
  python substack/post_note.py "Quick product demo" -v ./demo.mp4
        """,
    )
    parser.add_argument("text", nargs="?", default="", help="Text content of the Substack note.")
    parser.add_argument("-t", "--text", dest="text_flag", help="Alternative flag for note text.")
    parser.add_argument("-i", "--image", "--images", dest="images", nargs="+", help="Path(s) to image file(s) to attach.")
    parser.add_argument("-v", "--video", dest="video", help="Path to a video file (.mp4, .mov, etc.) to attach.")
    parser.add_argument("--headless", action="store_true", help="Launch Chrome in headless mode if not already running.")

    args = parser.parse_args()

    content = args.text_flag or args.text
    if not content and not args.images and not args.video:
        parser.error("You must provide note text or at least one media file (-i / -v).")

    try:
        asyncio.run(
            post_substack_note(
                text=content,
                image_paths=args.images,
                video_path=args.video,
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
