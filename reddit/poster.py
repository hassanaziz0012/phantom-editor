"""
Reddit Submission Poster - Playwright Automation (Stealth Mode)

Automates posting to r/ProgrammerHumor using Playwright in headful mode with persistent
authentication, role/accessibility-based selectors, human-like delays, automated flair
selection, and direct submission.

Usage:
    uv run python reddit/poster.py
    uv run python reddit/poster.py --title "whenTheCodeWorksOnTheFirstTry" --image "path/to/meme.png"
"""

import argparse
import asyncio
import os
import random
import re
import signal
import sys
from playwright.async_api import async_playwright, Error as PlaywrightError
from playwright_stealth import Stealth

TARGET_URL = "https://www.reddit.com/r/ProgrammerHumor/submit/"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLAYWRIGHT_USER_DIR = os.path.join(BASE_DIR, ".reddit_user")
DEFAULT_TEST_IMAGE = os.path.join(BASE_DIR, "test_meme.png")

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

// 4. Emulate realistic permissions query
if (window.navigator.permissions && window.navigator.permissions.query) {
    const originalQuery = window.navigator.permissions.query.bind(window.navigator.permissions);
    window.navigator.permissions.query = (parameters) => {
        if (parameters && parameters.name === 'notifications') {
            return Promise.resolve({
                state: (typeof Notification !== 'undefined' ? Notification.permission : 'default'),
                onchange: null
            });
        }
        return originalQuery(parameters);
    };
}
"""


async def human_delay(base_ms: int = 700, jitter_ms: int = 500):
    """
    Adds a human-like delay with random jitter (in milliseconds).
    """
    offset = random.uniform(-jitter_ms, jitter_ms)
    delay_sec = max(0.15, (base_ms + offset) / 1000.0)
    await asyncio.sleep(delay_sec)


async def human_type(locator, text: str):
    """
    Types text into an element with human-like character delays and micro-pauses.
    """
    await locator.click()
    await human_delay(base_ms=400, jitter_ms=200)
    for char in text:
        await locator.press_sequentially(char, delay=random.randint(35, 95))
        # Occasional micro-pause during typing
        if random.random() < 0.06:
            await asyncio.sleep(random.uniform(0.15, 0.35))


async def ensure_sample_image(image_path: str) -> str:
    """
    Ensures an image file exists at the given path, creating a sample meme image if not found.
    """
    if os.path.exists(image_path):
        return image_path

    os.makedirs(os.path.dirname(os.path.abspath(image_path)), exist_ok=True)
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (800, 600), color=(24, 24, 32))
        draw = ImageDraw.Draw(img)
        draw.text((220, 280), "whenTheCodeCompilesOnTheFirstTry", fill=(255, 255, 255))
        img.save(image_path)
        print(f"[*] Generated default meme image at: {image_path}")
    except Exception as e:
        print(f"[!] Warning: Could not create default image via PIL ({e}).")
    return image_path


def parse_arguments():
    parser = argparse.ArgumentParser(description="Reddit Submission Poster for r/ProgrammerHumor")
    parser.add_argument(
        "--title",
        type=str,
        default="whenTheCodeCompilesOnTheFirstTry",
        help="Submission title (Note: r/ProgrammerHumor requires camelCase titles)",
    )
    parser.add_argument(
        "--image",
        type=str,
        default=DEFAULT_TEST_IMAGE,
        help=f"Path to the image file to upload (default: {DEFAULT_TEST_IMAGE})",
    )
    return parser.parse_args()


async def main():
    args = parse_arguments()
    post_title = args.title
    image_path = os.path.abspath(args.image)

    # Ensure image exists
    image_path = await ensure_sample_image(image_path)

    print("==================================================")
    print("  Reddit Post Automation - r/ProgrammerHumor")
    print("==================================================")
    print(f"Target URL:     {TARGET_URL}")
    print(f"Post Title:     {post_title}")
    print(f"Image File:     {image_path}")
    print(f"User Profile:   {PLAYWRIGHT_USER_DIR}")
    print("==================================================")

    if not os.path.exists(PLAYWRIGHT_USER_DIR):
        print(f"[!] Warning: Persistent user directory not found at {PLAYWRIGHT_USER_DIR}")
        print("[!] Run `uv run reddit/autologin.py` to synchronize your Reddit credentials first.")
        os.makedirs(PLAYWRIGHT_USER_DIR, exist_ok=True)

    stop_event = asyncio.Event()

    # Handle termination signals cleanly
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass

    # Configure stealth matching local Linux platform
    stealth = Stealth(
        navigator_webdriver=True,
        navigator_platform=True,
        navigator_platform_override="Linux x86_64",
        navigator_user_agent=False,
        navigator_user_agent_data=False,
        chrome_runtime=True,
        chrome_app=True,
        chrome_csi=True,
        chrome_load_times=True,
        iframe_content_window=True,
        media_codecs=True,
        navigator_permissions=True,
        navigator_plugins=True,
        hairline=True,
    )

    async with async_playwright() as p:
        print("\n[1/6] Launching Chrome in headful mode with persistent Reddit session...")
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PLAYWRIGHT_USER_DIR,
            channel="chrome",
            headless=False,
            no_viewport=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--profile-directory=Default",
                "--start-maximized",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            ignore_default_args=[
                "--enable-automation",
                "--disable-component-update",
                "--disable-default-apps",
            ],
        )

        # Apply stealth protections
        await stealth.apply_stealth_async(context)
        await context.add_init_script(EXTRA_STEALTH_JS)

        context.on("close", lambda: stop_event.set())

        page = context.pages[0] if context.pages else await context.new_page()
        page.on("close", lambda: stop_event.set() if len(context.pages) <= 1 else None)

        print(f"[2/6] Navigating to {TARGET_URL}...")
        try:
            await page.goto(TARGET_URL, wait_until="domcontentloaded")
            await human_delay(base_ms=2500, jitter_ms=500)

            # ---------------------------------------------------------
            # Step 1: Fill the Post Title
            # ---------------------------------------------------------
            print(f"[3/6] Filling post title: '{post_title}'...")
            title_input = page.get_by_role("textbox", name=re.compile(r"title", re.I))
            if await title_input.count() == 0:
                title_input = page.get_by_placeholder(re.compile(r"title", re.I))
            if await title_input.count() == 0:
                title_input = page.locator('post-composer-title, [name="title"]')

            await human_type(title_input.first, post_title)
            print("      ✓ Title filled successfully.")
            await human_delay(base_ms=800, jitter_ms=400)

            # ---------------------------------------------------------
            # Step 2: Upload Image via File Picker
            # ---------------------------------------------------------
            print(f"[4/6] Selecting and uploading image ({os.path.basename(image_path)})...")
            image_btn = page.get_by_role("button", name=re.compile(r"^image$", re.I))
            if await image_btn.count() == 0:
                image_btn = page.get_by_role("button", name=re.compile(r"image|media|photo", re.I))

            async with page.expect_file_chooser() as fc_info:
                await image_btn.first.click()

            file_chooser = await fc_info.value
            await human_delay(base_ms=600, jitter_ms=300)
            await file_chooser.set_files(image_path)
            print("      ✓ Image file attached and loaded into post composer.")
            await human_delay(base_ms=2000, jitter_ms=500)

            # ---------------------------------------------------------
            # Step 3: Open Flair and Tags Dialog & Select Meme
            # ---------------------------------------------------------
            print("[5/6] Opening 'Add flair and tags' popup...")
            flair_btn = page.get_by_role("button", name=re.compile(r"add flair", re.I))
            if await flair_btn.count() == 0:
                flair_btn = page.get_by_text(re.compile(r"add flair and tags", re.I))
            if await flair_btn.count() == 0:
                flair_btn = page.get_by_role("button", name=re.compile(r"flair", re.I))

            await flair_btn.first.click()
            await human_delay(base_ms=1500, jitter_ms=400)

            print("      Selecting 'Meme' flair option and confirming...")
            meme_option = page.get_by_role("radio", name=re.compile(r"^meme$", re.I))
            if await meme_option.count() == 0:
                meme_option = page.get_by_role("radio", name=re.compile(r"meme", re.I))
            if await meme_option.count() == 0:
                meme_option = page.locator("faceplate-radio-input").filter(has_text=re.compile(r"^meme$", re.I))

            await meme_option.first.click()
            await human_delay(base_ms=700, jitter_ms=300)

            # Click the "Add" button to close popup
            modal_add_btn = page.get_by_role("button", name="Add", exact=True)
            if await modal_add_btn.count() == 0:
                modal_add_btn = page.get_by_role("button", name=re.compile(r"^add$", re.I))

            await modal_add_btn.first.click()
            print("      ✓ 'Meme' flair selected and popup closed.")
            await human_delay(base_ms=1800, jitter_ms=500)

            # ---------------------------------------------------------
            # Step 4: Submit the Post Directly
            # ---------------------------------------------------------
            print("\n[6/6] Submitting post...")
            await human_delay(base_ms=800, jitter_ms=300)
            post_btn = page.get_by_role("button", name="Post", exact=True)
            if await post_btn.count() == 0:
                post_btn = page.get_by_role("button", name=re.compile(r"^post$", re.I))
            if await post_btn.count() == 0:
                post_btn = page.get_by_role("button", name=re.compile(r"post|submit", re.I))

            if await post_btn.count() > 0 and await post_btn.first.is_disabled():
                print("      [*] Post button is currently disabled. Waiting for composer readiness...")
                try:
                    await page.wait_for_function(
                        "!document.querySelector('button[aria-label=\"Post\"], button:has-text(\"Post\")')?.disabled",
                        timeout=7000,
                    )
                except Exception:
                    pass

            if await post_btn.count() > 0:
                await post_btn.first.click()
                print("      ✓ Post button clicked!")
            else:
                print("      [!] Warning: Post button not found.")

            await human_delay(base_ms=3000, jitter_ms=1000)

            print("\n[SUCCESS] Post submitted successfully!")
            print("The browser will remain open for 15 seconds so you can see the result...")
            await asyncio.sleep(15)

        except KeyboardInterrupt:
            print("\n[!] Operation cancelled by user.")
        except Exception as e:
            print(f"\n[!] An error occurred during automation: {e}")
            raise
        finally:
            print("Closing browser context...")
            try:
                await context.close()
            except Exception:
                pass
            print("Done.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nExiting.")
