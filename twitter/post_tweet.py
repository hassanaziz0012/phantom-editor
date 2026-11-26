import argparse
import asyncio
import os
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from playwright.async_api import Error as PlaywrightError, async_playwright

from browser.cdp import CDP_PORT, PROFILE_DIR, get_cdp_browser_context, get_clean_page
from substack.post_note import post_substack_note

CLIPBOARD = "clipboard"


def _clipboard_image_to_file() -> str:
    """Save the clipboard image to a temp PNG (wl-paste, xclip, or PowerShell on WSL) and return its path."""
    fd, path = tempfile.mkstemp(suffix=".png")
    os.close(fd)

    def ok() -> bool:
        return os.path.getsize(path) > 0

    try:
        if shutil.which("wl-paste") and os.environ.get("WAYLAND_DISPLAY"):
            with open(path, "wb") as f:
                subprocess.run(["wl-paste", "--type", "image/png"], stdout=f, stderr=subprocess.DEVNULL)
            if ok():
                return path
        if shutil.which("xclip"):
            with open(path, "wb") as f:
                subprocess.run(["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                               stdout=f, stderr=subprocess.DEVNULL)
            if ok():
                return path
        if shutil.which("wslpath") and shutil.which("powershell.exe"):  # WSL fallback
            win_path = subprocess.check_output(["wslpath", "-w", path]).decode().strip().replace("'", "''")
            ps = ("Add-Type -AssemblyName System.Windows.Forms\n"
                  "if ([System.Windows.Forms.Clipboard]::ContainsImage()) {"
                  f" [System.Windows.Forms.Clipboard]::GetImage().Save('{win_path}', [System.Drawing.Imaging.ImageFormat]::Png); exit 0 }} else {{ exit 1 }}")
            subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True)
            if ok():
                return path
    except Exception:
        os.remove(path)
        raise
    os.remove(path)
    raise RuntimeError("No image found in the clipboard (tried wl-paste, xclip). Copy an image first.")


async def _run_post_tweet(content: str, image: Optional[str] = None):
    async with async_playwright() as p:
        # Attach to (or launch) a headful Chrome on the shared profile via CDP.
        # Chrome is left running afterwards; only our tab is closed.
        context = await get_cdp_browser_context(p, profile_path=PROFILE_DIR, port=CDP_PORT, headless=False)
        page = await get_clean_page(context)
        temp_image_path = None
        try:
            await page.goto("https://x.com/intent/post")

            # Check if redirect to login page occurs
            await page.wait_for_timeout(2000)

            tweet_box = page.locator('div[data-testid="tweetTextarea_0"]').first
            if "login" in page.url or "i/flow/login" in page.url:
                print("\n" + "="*80)
                print("🔑 MANUAL ACTION REQUIRED: Please log in in the opened browser window.")
                print("Solve any CAPTCHAs, verification puzzles, or 2FA prompts.")
                print("Once logged in, you should be redirected to the tweet composer automatically.")
                print("="*80 + "\n")

                # Wait up to 5 minutes for user login and redirect
                await tweet_box.wait_for(state="visible", timeout=300000)
            else:
                await tweet_box.wait_for(state="visible", timeout=30000)

            # 1. Type the content
            await tweet_box.click()
            await page.keyboard.type(content, delay=50)

            # 2. Attach the image (clipboard or file) through the composer's file input
            if image:
                temp_image_path = None
                if image == CLIPBOARD:
                    image_path = temp_image_path = _clipboard_image_to_file()
                else:
                    image_path = str(Path(image).expanduser().resolve())
                    if not os.path.isfile(image_path):
                        raise FileNotFoundError(f"Image file not found: {image_path}")
                file_input = page.locator('input[data-testid="fileInput"], input[type="file"]').first
                await file_input.set_input_files(image_path)
                # Wait for the preview to show up and the upload to finish
                await page.locator('[data-testid="attachments"] img, [data-testid="attachments"] video').first.wait_for(
                    state="visible", timeout=30000)
                print("Attached image.")

            # 3. Click Post once it is enabled (disabled while an upload is in progress)
            post_button = page.locator('button[data-testid="tweetButton"]').first
            for _ in range(120):
                if await post_button.is_enabled():
                    break
                await page.wait_for_timeout(500)
            else:
                raise RuntimeError("The Post button stayed disabled (image upload not finished or text rejected).")
            await post_button.click()

            # 4. Confirm: the composer closes or a "sent" toast appears; an error toast is a failure
            deadline = 20
            for _ in range(deadline * 2):
                await page.wait_for_timeout(500)
                try:
                    toasts = await page.locator('[data-testid="toast"]').all_inner_texts()
                except PlaywrightError:
                    toasts = []  # page navigated away mid-query
                for t in toasts:
                    if "sent" in t.lower():
                        print(f"Tweet posted: {content}")
                        return
                    raise RuntimeError(f"X rejected the post: {t.strip()}")
                try:
                    composer_open = await tweet_box.is_visible()
                except PlaywrightError:
                    composer_open = False
                if not composer_open:
                    print(f"Tweet posted: {content}")
                    return
            raise RuntimeError("The tweet composer did not close after clicking Post; the tweet was probably not posted.")

        finally:
            await page.close()
            if temp_image_path and os.path.exists(temp_image_path):
                try:
                    os.remove(temp_image_path)
                except OSError:
                    pass

async def post_tweet(content: str, image: Optional[str] = None, substack: bool = False):
    await _run_post_tweet(content, image)

    if substack:
        note_url = await post_substack_note(text=content)
        if not note_url:
            print("Warning: Substack note was posted but its link could not be verified.", file=sys.stderr)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post a tweet via Playwright.")
    parser.add_argument('--image', action='store_true', help="Attach the image from the clipboard")
    parser.add_argument('--image-file', metavar='PATH', help="Attach an image from this file instead of the clipboard")
    parser.add_argument('--substack', action='store_true', help="Also post to Substack")
    # Threads posting was removed; the flag is still accepted so old callers keep working.
    parser.add_argument('--threads', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('text', nargs='+', help="The tweet text content")
    
    args = parser.parse_args()
    tweet_content = " ".join(args.text)
    
    if len(tweet_content) > 280:
        print("Error: Tweet is longer than 280 characters.", file=sys.stderr)
        sys.exit(1)
        
    if args.threads:
        print("Warning: --threads is no longer supported; skipping Threads.", file=sys.stderr)

    try:
        asyncio.run(post_tweet(tweet_content, image=args.image_file or (CLIPBOARD if args.image else None), substack=args.substack))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)