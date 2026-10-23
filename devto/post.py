"""
DEV Community (dev.to) Post Publisher using Playwright & Chrome DevTools Protocol (CDP).

Creates a new dev.to post from a Markdown file by driving the https://dev.to/new
editor in the logged-in p9222 Chrome profile: title, up to 4 tags, body, and
optionally a cover image, canonical URL, series and scheduled publish time.
Publishes immediately unless --draft is given, and prints the post's URL.

The Markdown file may start with a flat YAML-style front matter block; its
title / tags / cover_image / canonical_url / series / published keys are used
as defaults and CLI flags override them. Without any title, the file's first
"# Heading" becomes the title and is removed from the body.

Usage:
    uv run python devto/post.py post.md --title "My Post" --tags python webdev
    uv run python devto/post.py post.md --tags "python, ai" --cover ./cover.png
    uv run python devto/post.py post.md --canonical-url https://blog.example.com/my-post
    uv run python devto/post.py post.md --series "Automation Diaries"
    uv run python devto/post.py post.md --schedule "2026-11-01 09:00"
    uv run python devto/post.py post.md --draft
"""

import argparse
import asyncio
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from playwright.async_api import Page, async_playwright

# Ensure repo root is in python path
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from browser.cdp import CDP_PORT, get_cdp_browser_context, get_clean_page

# ==============================================================================
# Configuration & Constants
# ==============================================================================
PROFILE_DIR = Path.home() / "Desktop" / "browser-profiles" / "p9222"
NEW_POST_URL = "https://dev.to/new"
# localStorage key the editor autosaves the unsaved /new form under
DRAFT_STORAGE_KEY = "editor-v2-https://dev.to/new"
MAX_TAGS = 4
TAG_PATTERN = re.compile(r"^[a-z0-9]+$")


# ==============================================================================
# Markdown & Argument Parsing
# ==============================================================================
def split_front_matter(markdown: str) -> Tuple[Dict[str, str], str]:
    """Splits a leading '---' front matter block of flat 'key: value' lines off the body."""
    match = re.match(r"\A---[ \t]*\n(.*?)\n---[ \t]*(?:\n|\Z)", markdown, re.DOTALL)
    if not match:
        return {}, markdown

    meta: Dict[str, str] = {}
    for line in match.group(1).splitlines():
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split(":", 1)
        meta[key.strip().lower()] = value.strip().strip("\"'")
    return meta, markdown[match.end():]


def extract_h1_title(body: str) -> Tuple[Optional[str], str]:
    """Returns the first '# Heading' line outside code fences and the body without it."""
    in_fence = False
    lines = body.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            in_fence = not in_fence
        elif not in_fence and re.match(r"^# +\S", line):
            title = line[2:].strip()
            return title, "".join(lines[:i] + lines[i + 1:]).lstrip("\n")
    return None, body


def normalize_tags(raw_tags: List[str]) -> List[str]:
    """Flattens comma/space separated tags, strips '#', lowercases and validates them."""
    tags: List[str] = []
    for raw in raw_tags:
        for tag in re.split(r"[,\s]+", raw):
            tag = tag.strip().lstrip("#").lower()
            if tag and tag not in tags:
                tags.append(tag)

    invalid = [t for t in tags if not TAG_PATTERN.match(t)]
    if invalid:
        raise ValueError(f"dev.to tags may only contain letters and numbers: {', '.join(invalid)}")
    if len(tags) > MAX_TAGS:
        raise ValueError(f"dev.to allows at most {MAX_TAGS} tags, got {len(tags)}: {', '.join(tags)}")
    return tags


def parse_schedule(value: str) -> datetime:
    """Parses 'YYYY-MM-DD HH:MM' (local time of the browser) into a datetime."""
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        raise ValueError(f"Invalid schedule '{value}', expected 'YYYY-MM-DD HH:MM'")


# ==============================================================================
# Editor Interaction
# ==============================================================================
async def open_clean_editor(page: Page) -> None:
    """Opens dev.to/new logged in, with any autosaved unsaved form discarded."""
    print(f"Navigating to {NEW_POST_URL}...")
    await page.goto(NEW_POST_URL, wait_until="domcontentloaded")

    if "/enter" in page.url:
        raise RuntimeError("Not logged in to dev.to. Log in to the p9222 Chrome profile and retry.")

    # The title textarea is server-rendered and gets replaced when the editor app
    # hydrates, so wait for the app-rendered body field before touching anything
    body_box = page.locator("#article_body_markdown")
    await body_box.wait_for(state="visible", timeout=30000)

    # The editor restores the last unsaved /new form from localStorage, so drop it
    # and reload to start from an empty form instead of merging into old content
    had_draft = await page.evaluate(
        "(key) => { const had = localStorage.getItem(key) !== null; localStorage.removeItem(key); return had; }",
        DRAFT_STORAGE_KEY,
    )
    if had_draft:
        print("Discarding the editor's autosaved unsaved form...")
        await page.reload(wait_until="domcontentloaded")
        await body_box.wait_for(state="visible", timeout=30000)


async def add_tags(page: Page, tags: List[str]) -> None:
    """Types each tag into the tag autocomplete, committing it with a comma."""
    tag_input = page.locator("#tag-input")
    for tag in tags:
        await tag_input.click()
        await tag_input.press_sequentially(tag, delay=40)
        # A comma commits exactly the typed text, unlike Enter which takes the highlighted suggestion
        await tag_input.press(",")
        await page.wait_for_timeout(300)
    await page.keyboard.press("Escape")

    # Each committed tag renders as a chip group labelled with the tag name
    chips = page.locator("#tag-input-combo-selected [role=group]")
    selected = [label for label in [await c.get_attribute("aria-label") for c in await chips.all()] if label]
    missing = [t for t in tags if t not in selected]
    if missing:
        raise RuntimeError(f"These tags were not added in the editor: {', '.join(missing)}")


async def upload_cover_image(page: Page, cover: Path) -> None:
    """Uploads the cover image and waits for the editor to show it."""
    print(f"Uploading cover image: {cover.name}...")
    await page.locator("#cover-image-input").set_input_files(str(cover))
    await page.locator("img.crayons-article-form__cover__image").wait_for(state="visible", timeout=60000)


async def set_advanced_options(
    page: Page,
    canonical_url: Optional[str],
    series: Optional[str],
    schedule: Optional[datetime],
) -> None:
    """Fills the Advanced Post Options modal (canonical URL, schedule, series)."""
    await page.locator("#post-options-btn").click()
    modal = page.locator(".crayons-modal").filter(has_text="Advanced Post Options")
    await modal.wait_for(state="visible", timeout=10000)

    if canonical_url:
        await modal.locator("#canonicalUrl").fill(canonical_url)

    if schedule:
        await modal.locator("#publishedAtDate").fill(schedule.strftime("%Y-%m-%d"))
        await modal.locator("#publishedAtTime").fill(schedule.strftime("%H:%M"))

    if series:
        await select_series(page, modal, series)

    await modal.get_by_role("button", name="Done", exact=True).click()
    await modal.wait_for(state="hidden", timeout=10000)


async def select_series(page: Page, modal, series: str) -> None:
    """Picks an existing series with this name, or creates it."""
    # Existing series are listed as toggle cards showing the series name
    card = modal.locator(".series-selector__series-card").filter(
        has=page.locator(".series-selector__series-name", has_text=re.compile(rf"^\s*{re.escape(series)}\s*$"))
    )
    if await card.count() > 0:
        if await card.first.get_attribute("aria-pressed") != "true":
            await card.first.click()
        return

    await modal.locator(".series-selector__create-btn").click()
    await modal.locator("#new-series-name").fill(series)
    await modal.get_by_role("button", name="Create series").click()
    await page.wait_for_timeout(500)


async def submit_post(page: Page, draft: bool) -> str:
    """Clicks Publish (or Schedule / Save Draft) and returns the resulting post URL."""
    if draft:
        button = page.get_by_role("button", name=re.compile(r"^Save\s*Draft$"))
    else:
        button = page.get_by_role("button", name=re.compile(r"^(Publish|Schedule)$"))
    await button.first.click()

    try:
        await page.wait_for_url(lambda url: not url.rstrip("/").endswith("/new"), timeout=30000)
    except Exception:
        errors = await page.locator(".crayons-notice--danger, [role=alert]").all_inner_texts()
        detail = " | ".join(e.strip() for e in errors if e.strip()) or "no error message shown"
        raise RuntimeError(f"dev.to did not accept the post: {detail}")

    return page.url


# ==============================================================================
# Publishing Workflow
# ==============================================================================
async def post_devto_article(
    title: str,
    body_markdown: str,
    tags: Optional[List[str]] = None,
    cover_image: Optional[Path] = None,
    canonical_url: Optional[str] = None,
    series: Optional[str] = None,
    schedule: Optional[datetime] = None,
    draft: bool = False,
    headless: bool = False,
) -> str:
    """
    Creates a dev.to post and returns its URL.

    :param title: Post title.
    :param body_markdown: Post body in Markdown.
    :param tags: Up to 4 alphanumeric tags.
    :param cover_image: Optional local cover image file.
    :param canonical_url: Optional canonical URL if the post was first published elsewhere.
    :param series: Optional series name; created if it does not exist yet.
    :param schedule: Optional future publish time, in the browser's local timezone.
    :param draft: Save as a draft instead of publishing.
    :param headless: If starting a fresh Chrome instance, whether to run headless.
    """
    if not title.strip():
        raise ValueError("A post title is required.")
    if not body_markdown.strip():
        raise ValueError("The post body is empty.")
    tags = normalize_tags(tags or [])
    if cover_image and not cover_image.is_file():
        raise FileNotFoundError(f"Cover image not found: {cover_image}")
    if schedule and schedule <= datetime.now():
        raise ValueError(f"Scheduled time {schedule:%Y-%m-%d %H:%M} is not in the future.")
    if schedule and draft:
        raise ValueError("A post cannot be both scheduled and saved as a draft.")

    async with async_playwright() as playwright:
        # Attach to (or launch) Chrome on the p9222 profile via CDP.
        # Chrome is left running afterwards; only our tab is closed.
        context = await get_cdp_browser_context(playwright, profile_path=PROFILE_DIR, port=CDP_PORT, headless=headless)
        page = await get_clean_page(context)
        try:
            await open_clean_editor(page)

            print("Filling in title, tags and body...")
            await page.locator("#article-form-title").fill(title.strip())
            await add_tags(page, tags)
            await page.locator("#article_body_markdown").fill(body_markdown.strip() + "\n")

            if cover_image:
                await upload_cover_image(page, cover_image)

            if canonical_url or series or schedule:
                print("Setting advanced post options...")
                await set_advanced_options(page, canonical_url, series, schedule)

            action = "Saving draft" if draft else ("Scheduling post" if schedule else "Publishing post")
            print(f"{action}...")
            url = await submit_post(page, draft)
        finally:
            await page.close()

    print("\n=======================================================")
    print(f"🎉 Post {'saved as draft' if draft else 'scheduled' if schedule else 'published'}!")
    print(f"URL: {url}")
    print("=======================================================\n")
    return url


# ==============================================================================
# CLI Entry Point
# ==============================================================================
def main():
    parser = argparse.ArgumentParser(
        description="Create a dev.to post from a Markdown file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python devto/post.py post.md --title "My Post" --tags python webdev
  python devto/post.py post.md --tags "python, ai" --cover ./cover.png
  python devto/post.py post.md --canonical-url https://blog.example.com/my-post
  python devto/post.py post.md --series "Automation Diaries" --schedule "2026-11-01 09:00"
  python devto/post.py post.md --draft
        """,
    )
    parser.add_argument("markdown_file", help="Path to the Markdown file with the post body.")
    parser.add_argument("-t", "--title", help="Post title (default: front matter title, else the first '# Heading').")
    parser.add_argument("--tags", nargs="+", help="Up to 4 tags, space or comma separated.")
    parser.add_argument("-c", "--cover", help="Path to a cover image.")
    parser.add_argument("--canonical-url", help="Canonical URL if the post was first published elsewhere.")
    parser.add_argument("--series", help="Series to add the post to (created if it doesn't exist).")
    parser.add_argument("--schedule", help="Publish later, at 'YYYY-MM-DD HH:MM' local time.")
    parser.add_argument("--draft", action="store_true", help="Save as a draft instead of publishing.")
    parser.add_argument("--headless", action="store_true", help="Launch Chrome in headless mode if not already running.")
    args = parser.parse_args()

    markdown_path = Path(args.markdown_file).expanduser()
    if not markdown_path.is_file():
        print(f"Error: Markdown file not found: {markdown_path}", file=sys.stderr)
        sys.exit(1)

    meta, body = split_front_matter(markdown_path.read_text(encoding="utf-8"))
    title = args.title or meta.get("title")
    if not title:
        title, body = extract_h1_title(body)
    if not title:
        print("Error: No title given and the Markdown has no '# Heading' to use.", file=sys.stderr)
        sys.exit(1)

    # A relative front matter cover path is relative to the Markdown file
    cover = None
    if args.cover:
        cover = Path(args.cover).expanduser().resolve()
    elif meta.get("cover_image") and not meta["cover_image"].startswith(("http://", "https://")):
        cover = (markdown_path.parent / meta["cover_image"]).expanduser().resolve()

    draft = args.draft or meta.get("published", "").lower() == "false"

    try:
        asyncio.run(
            post_devto_article(
                title=title,
                body_markdown=body,
                tags=args.tags or ([meta["tags"]] if meta.get("tags") else []),
                cover_image=cover,
                canonical_url=args.canonical_url or meta.get("canonical_url"),
                series=args.series or meta.get("series"),
                schedule=parse_schedule(args.schedule) if args.schedule else None,
                draft=draft,
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
