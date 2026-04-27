import asyncio
import os
import sys
from playwright.async_api import async_playwright

# Get the absolute path to the data/auth.json file relative to this script
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AUTH_FILE_PATH = os.path.join(SCRIPT_DIR, "data", "auth.json")

async def post_tweet(content: str):
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

        # 2. Click Post immediately
        await page.locator('button[data-testid="tweetButton"]').first.click()

        print(f"Tweet posted: {content}")
        await page.wait_for_timeout(3000)
        await browser.close()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        tweet_content = sys.argv[1]
        if len(tweet_content) > 280:
            print("Error: Tweet is longer than 280 characters.", file=sys.stderr)
            sys.exit(1)
    else:
        print("Usage: python post_tweet.py <tweet_content>")
        sys.exit(1)
        
    asyncio.run(post_tweet(tweet_content))