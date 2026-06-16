import asyncio
import os
import sys
import argparse
import tempfile

from playwright.async_api import async_playwright

# Get the absolute path to the data/auth.json file relative to this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH_FILE_PATH = os.path.join(SCRIPT_DIR, "data", "auth.json")

async def _run_post_tweet(content: str, use_image: bool = False, headless: bool = True, status: dict = None):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        temp_image_path = None
        try:
            if os.path.exists(AUTH_FILE_PATH):
                context = await browser.new_context(storage_state=AUTH_FILE_PATH)
            else:
                context = await browser.new_context()

            page = await context.new_page()
            await page.goto("https://x.com/intent/post")

            # Check if redirect to login page occurs
            await page.wait_for_timeout(2000)

            if "login" in page.url or "i/flow/login" in page.url:
                if headless:
                    raise Exception("Authentication required (redirected to login).")
                else:
                    print("\n" + "="*80)
                    print("🔑 MANUAL ACTION REQUIRED: Please log in in the opened browser window.")
                    print("Solve any CAPTCHAs, verification puzzles, or 2FA prompts.")
                    print("Once logged in, you should be redirected to the tweet composer automatically.")
                    print("="*80 + "\n")
                    
                    # Wait up to 5 minutes for user login and redirect
                    tweet_box = page.locator('div[data-testid="tweetTextarea_0"]').first
                    await tweet_box.wait_for(state="visible", timeout=300000)
            else:
                timeout = 10000 if headless else 30000
                tweet_box = page.locator('div[data-testid="tweetTextarea_0"]').first
                await tweet_box.wait_for(state="visible", timeout=timeout)

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
            if status is not None:
                status["posted"] = True

            print(f"Tweet posted: {content}")
            await page.wait_for_timeout(3000)

            # Save state on success
            os.makedirs(os.path.dirname(AUTH_FILE_PATH), exist_ok=True)
            await context.storage_state(path=AUTH_FILE_PATH)
            print(f"Session state saved to {AUTH_FILE_PATH}")
            
        finally:
            await browser.close()
            if temp_image_path and os.path.exists(temp_image_path):
                try:
                    os.remove(temp_image_path)
                except Exception:
                    pass

async def post_tweet(content: str, use_image: bool = False):
    status = {"posted": False}
    try:
        print("Launching browser in headless mode...")
        await _run_post_tweet(content, use_image, headless=True, status=status)
    except Exception as e:
        if status["posted"]:
            raise e
        print(f"Headless mode encountered an issue: {e}")
        print("Restarting browser in headed mode...")
        await _run_post_tweet(content, use_image, headless=False, status=status)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Post a tweet via Playwright.")
    parser.add_argument('--image', action='store_true', help="Attach an image from the clipboard")
    parser.add_argument('text', nargs='+', help="The tweet text content")
    
    args = parser.parse_args()
    tweet_content = " ".join(args.text)
    
    if len(tweet_content) > 280:
        print("Error: Tweet is longer than 280 characters.", file=sys.stderr)
        sys.exit(1)
        
    asyncio.run(post_tweet(tweet_content, use_image=args.image))