import asyncio
from playwright.async_api import async_playwright

async def post_tweet(content: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context(storage_state="data/auth.json")
        page = await context.new_page()

        await page.goto("https://x.com/intent/post")

        # 1. Type the content
        tweet_box = page.locator('div[data-testid="tweetTextarea_0"]').first
        await tweet_box.wait_for(state="visible")
        await tweet_box.click()
        await page.keyboard.type(content, delay=50)

        # 2. Click Post immediately
        await page.locator('button[data-testid="tweetButton"]').first.click()

        print("Tweet posted!")
        await page.wait_for_timeout(3000)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(post_tweet("am i a hypocrite for complaining about the X algo?"))