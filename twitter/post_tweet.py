import asyncio
import os
import sys
import argparse
import tempfile

from playwright.async_api import async_playwright

# Get the absolute path to the data/auth.json file relative to this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH_FILE_PATH = os.path.join(SCRIPT_DIR, "data", "auth.json")

async def post_tweet(content: str, use_image: bool = False):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(storage_state=AUTH_FILE_PATH)
        page = await context.new_page()

        await page.goto("https://x.com/intent/post")

        # 1. Type the content
        tweet_box = page.locator('div[data-testid="tweetTextarea_0"]').first
        await tweet_box.wait_for(state="visible")
        await tweet_box.click()
        await page.keyboard.type(content, delay=50)

        # 2. Handle image from clipboard
        temp_image_path = None
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
        await page.locator('button[data-testid="tweetButton"]').first.click()

        print(f"Tweet posted: {content}")
        await page.wait_for_timeout(3000)
        await browser.close()
        
        if temp_image_path and os.path.exists(temp_image_path):
            os.remove(temp_image_path)

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