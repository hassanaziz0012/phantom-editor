import argparse
import asyncio
import os
import sys
import tempfile
from pathlib import Path

from playwright.async_api import async_playwright

# Setup import path for sibling modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from substack.post_note import post_substack_note, get_cdp_browser_context, get_clean_page
from threads.post_thread import post_thread

PROFILE_DIR = Path.home() / "Desktop" / "browser-profiles" / "p9222"
CDP_PORT = 9222

async def _run_post_tweet(content: str, use_image: bool = False):
    async with async_playwright() as p:
        # Attach to (or launch) a headful Chrome on the p9222 profile via CDP.
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

            # 2. Handle image from clipboard
            if use_image:
                import subprocess
                # Create a temp file
                temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
                temp_image_path = temp_file.name
                temp_file.close()

                try:
                    # Get the equivalent Windows path so powershell can write to it
                    win_path = subprocess.check_output(['wslpath', '-w', temp_image_path]).decode().strip()
                    win_path = win_path.replace("'", "''") # escape single quotes for powershell
                    
                    ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
if ([System.Windows.Forms.Clipboard]::ContainsImage()) {{
    $image = [System.Windows.Forms.Clipboard]::GetImage()
    $image.Save('{win_path}', [System.Drawing.Imaging.ImageFormat]::Png)
    exit 0
}} else {{
    exit 1
}}
"""
                    result = subprocess.run(['powershell.exe', '-NoProfile', '-Command', ps_script], capture_output=True)
                    
                    if result.returncode == 0 and os.path.getsize(temp_image_path) > 0:
                        # Upload using Playwright
                        file_input = page.locator('input[type="file"]').first
                        await file_input.set_input_files(temp_image_path)
                        print("Attached image from clipboard.")
                        
                        # Wait a moment for the image to upload/process
                        await page.wait_for_timeout(2000)
                    else:
                        print("Warning: No image found in clipboard.", file=sys.stderr)
                        os.remove(temp_image_path)
                        temp_image_path = None
                except Exception as e:
                    print(f"Error accessing clipboard: {e}", file=sys.stderr)
                    if os.path.exists(temp_image_path):
                        os.remove(temp_image_path)
                        temp_image_path = None

            # 3. Click Post immediately
            post_button = page.locator('button[data-testid="tweetButton"]').first
            await post_button.click()

            print(f"Tweet posted: {content}")
            await page.wait_for_timeout(3000)

        finally:
            await page.close()
            if temp_image_path and os.path.exists(temp_image_path):
                try:
                    os.remove(temp_image_path)
                except Exception:
                    pass

async def post_tweet(content: str, use_image: bool = False, substack: bool = False, threads: bool = False):
    await _run_post_tweet(content, use_image)

    if substack:
        await post_substack_note(text=content)

    if threads:
        await post_thread(posts_text=[content])

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post a tweet via Playwright.")
    parser.add_argument('--image', action='store_true', help="Attach an image from the clipboard")
    parser.add_argument('--substack', action='store_true', help="Also post to Substack")
    parser.add_argument('--threads', action='store_true', help="Also post to Threads")
    parser.add_argument('text', nargs='+', help="The tweet text content")
    
    args = parser.parse_args()
    tweet_content = " ".join(args.text)
    
    if len(tweet_content) > 280:
        print("Error: Tweet is longer than 280 characters.", file=sys.stderr)
        sys.exit(1)
        
    asyncio.run(post_tweet(tweet_content, use_image=args.image, substack=args.substack, threads=args.threads))