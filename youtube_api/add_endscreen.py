"""
YouTube End Screen
==================
Adds an end screen to one of your videos by applying a Studio template, since the
YouTube Data API has no end screen support. By default it applies "Import from latest
video", which copies the end screen of your most recent video that has one.

It drives the video's editor page in the logged-in Chrome on port 9222 (the p9222
profile): End screen → Add → Apply template → the template card → Save. Videos that
already have an end screen are left alone. The video has to finish processing first;
until then Studio's editor has no end screen option.

Usage:
    phantom yt endscreen <video_url_or_id>... [--template <name>] [--dry-run]
"""

import argparse
import asyncio
import sys

from playwright.async_api import Page, TimeoutError as PlaywrightTimeoutError, async_playwright


from browser.cdp import CDP_PORT, PROFILE_DIR, get_cdp_browser_context, get_clean_page
from youtube_api.utils import extract_video_id

# ── Configuration ─────────────────────────────────────────────────────────────

# The Chrome profile on port 9222 that's logged into the channel; only used if Chrome isn't running yet
EDITOR_URL       = "https://studio.youtube.com/video/{video_id}/editor"
DEFAULT_TEMPLATE = "Import from latest video"
LOAD_TIMEOUT_MS  = 30000
SAVE_TIMEOUT_MS  = 60000

# Studio's editor page; the end screen row shows an Add button when empty, Edit otherwise
ADD_BUTTON       = "#entrypoint-endscreen-row #add-endscreen-icon-button"
EDIT_BUTTON      = "#entrypoint-endscreen-row ytcp-button.edit-button"
APPLY_TEMPLATE   = "tp-yt-paper-item[test-id=APPLY_TEMPLATE]"
TEMPLATE_CARD    = "ytve-endscreen-template-picker .card"
ELEMENT_ROWS     = "ytve-endscreen-row #element"
SAVE_BUTTON      = "ytve-control-bar #save-button"


class EndscreenError(Exception):
    """Raised when Studio can't be reached or the end screen can't be applied."""


async def _element_labels(page: Page) -> list[str]:
    return [label.strip() for label in await page.locator(ELEMENT_ROWS).all_inner_texts()]


async def _open_editor(page: Page, video_id: str) -> bool:
    """Load the video's editor; True if it has no end screen yet."""
    await page.goto(EDITOR_URL.format(video_id=video_id), wait_until="domcontentloaded")
    try:
        await page.locator(f"{ADD_BUTTON}, {EDIT_BUTTON}").first.wait_for(timeout=LOAD_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        if "accounts.google.com" in page.url:
            raise EndscreenError("the p9222 Chrome profile isn't logged into YouTube Studio.")
        raise EndscreenError(
            f"the editor didn't show an end screen option in {LOAD_TIMEOUT_MS // 1000}s "
            f"({page.url}). Is the video still processing, or on another channel?"
        )
    return await page.locator(ADD_BUTTON).is_visible()


async def _apply_template(page: Page, template: str) -> list[str]:
    """Open the end screen editor, apply the template, and return the elements it added."""
    await page.locator(ADD_BUTTON).click()
    await page.locator(APPLY_TEMPLATE).click()
    card = page.locator(TEMPLATE_CARD).filter(has_text=template)
    try:
        await card.first.wait_for(timeout=LOAD_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        names = [name.strip() for name in await page.locator(TEMPLATE_CARD).all_inner_texts()]
        raise EndscreenError(f"no template named {template!r}. Studio offers: {', '.join(names) or 'none'}")
    await card.first.click()
    try:
        await page.locator(ELEMENT_ROWS).first.wait_for(timeout=LOAD_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        raise EndscreenError(f"applying {template!r} added no elements.")
    return await _element_labels(page)


async def _save(page: Page) -> None:
    save = page.locator(SAVE_BUTTON)
    await save.click()
    # Studio returns to the editor overview once the end screen is saved
    try:
        await page.locator(EDIT_BUTTON).wait_for(timeout=SAVE_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        raise EndscreenError(f"Studio didn't confirm the save in {SAVE_TIMEOUT_MS // 1000}s.")


async def add_endscreens(video_ids: list[str], template: str, dry_run: bool, port: int) -> int:
    """Apply the template to each video; returns how many failed."""
    failures = 0
    async with async_playwright() as p:
        context = await get_cdp_browser_context(p, profile_path=PROFILE_DIR, port=port)
        for video_id in video_ids:
            # A tab per video: closing it drops unsaved edits without a leave-page prompt
            page = await get_clean_page(context)
            try:
                if not await _open_editor(page, video_id):
                    print(f"⏭️  {video_id}: already has an end screen, skipped.")
                    continue
                labels = await _apply_template(page, template)
                if dry_run:
                    print(f"🔍 {video_id}: {template!r} would add {', '.join(labels)} (dry run, not saved).")
                    continue
                await _save(page)
                print(f"✅ {video_id}: end screen added ({', '.join(labels)}).")
            except EndscreenError as e:
                failures += 1
                print(f"❌ {video_id}: {e}", file=sys.stderr)
            finally:
                await page.close()
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Add an end screen to YouTube videos from a Studio template.")
    parser.add_argument("videos", nargs="+", help="Video URLs or IDs")
    parser.add_argument("--template", default=DEFAULT_TEMPLATE,
                        help=f"Studio template card to apply (default: {DEFAULT_TEMPLATE!r})")
    parser.add_argument("--dry-run", action="store_true", help="Apply the template but don't save")
    parser.add_argument("--port", type=int, default=CDP_PORT, help="Chrome remote debugging port")
    args = parser.parse_args()

    try:
        video_ids = list(dict.fromkeys(extract_video_id(v) for v in args.videos))
    except ValueError as e:
        sys.exit(f"❌ {e}")

    failures = asyncio.run(add_endscreens(video_ids, args.template, args.dry_run, args.port))
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
