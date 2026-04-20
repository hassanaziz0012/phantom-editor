"""
Runs once to log in and save the auth state.
Creates auth.json in the same directory.

usage: uv run twitter/login.py
"""

import asyncio
from playwright.async_api import async_playwright

async def run():
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=False,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-web-security",
                "--disable-infobars",
                "--disable-extensions",
                "--window-size=1280,720"
            ]
        )
        
        # Consistent User Agent to match your Windows 10 environment
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
        )
        
        page = await context.new_page()
        
        print("Opening X.com... Please log in.")
        await page.goto("https://x.com/i/flow/login")
        
        # Wait for you to finish login and reach the home feed/dashboard
        # This prevents saving a 'partial' session if it's still redirecting
        try:
            print("Waiting for home feed to load (sign-in detection)...")
            await page.wait_for_selector("[data-testid='AppTabBar_Home_Link']", timeout=300000) # 5 min timeout
            print("Login detected!")
        except Exception:
            print("Timeout or manual window closure. Attempting to save current state regardless...")

        # Save the authenticated state
        await context.storage_state(path="auth.json")
        print("Successfully saved auth.json. You can now use this with your agent.")
        
        await browser.close()

if __name__ == "__main__":
    asyncio.run(run())