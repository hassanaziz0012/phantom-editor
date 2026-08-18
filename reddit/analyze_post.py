"""
Reddit Post Scraper and Sentiment Analyzer.

Scrapes post engagement metrics (upvote count, view count, top 10 comments) from a Reddit post URL
using Playwright in headful mode with role/accessibility-based selectors, and analyzes the community
sentiment and reception using Groq's openai/gpt-oss-120b model with structured outputs.

Usage:
    uv run python reddit/analyze_post.py "https://www.reddit.com/r/..."
    uv run python reddit/analyze_post.py --url "https://reddit.com/..." --json
"""

import argparse
import asyncio
import json
import os
import re
import sys
from typing import Any, Dict, List, Literal, Optional

from playwright.async_api import async_playwright, BrowserContext, Page
from playwright_stealth import Stealth
from pydantic import BaseModel, Field

# Ensure repo root is in python path for module imports
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agentic.ask_groq import ask_groq

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLAYWRIGHT_USER_DIR = os.path.join(BASE_DIR, ".reddit_user")
PROMPT_PATH = os.path.join(REPO_ROOT, "agentic", "prompts", "analyse_reddit_post_sentiment.md")

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
"""


# -----------------------------------------------------------------------------
# Pydantic Schemas for Structured Output
# -----------------------------------------------------------------------------
class CommentData(BaseModel):
    author: str = Field(description="Username of the commenter")
    score: str = Field(description="Upvote score of the comment")
    text: str = Field(description="Body content of the comment")


class ScrapedPostData(BaseModel):
    url: str
    title: str
    subreddit: str
    author: str
    upvote_count: str
    upvote_ratio: Optional[str] = None
    view_count: str
    total_comments_count: str
    top_comments: List[CommentData]


class RedditPostAnalysis(BaseModel):
    rating: float = Field(
        description="Rating of the post out of 10.0 based on community reception and engagement"
    )
    reception: Literal["positively received", "negatively received", "mixed", "neutral"] = Field(
        description="Categorical verdict on whether the post was positively or negatively received"
    )
    verdict: str = Field(
        description="Concise summary headline of the community verdict"
    )
    summary: str = Field(
        description="Comprehensive analytical summary synthesizing upvotes, views, and comment feedback"
    )
    key_comment_themes: List[str] = Field(
        description="Key themes, talking points, or humor patterns observed in the top comments"
    )
    engagement_analysis: str = Field(
        description="Observations regarding upvote ratio, view count, and comment-to-view interaction"
    )
    top_positive_points: List[str] = Field(
        description="Positive sentiments, agreements, or shared humor expressed by the community"
    )
    top_critical_points: List[str] = Field(
        description="Critical remarks, counterarguments, skepticism, or negative sentiments raised"
    )


# -----------------------------------------------------------------------------
# Scraper Logic
# -----------------------------------------------------------------------------
async def scrape_reddit_post(url: str, headless: bool = False) -> ScrapedPostData:
    """
    Scrapes post details and top 10 comments from Reddit using Playwright.
    Always uses role/accessibility-based selectors and semantic attributes.
    """
    os.makedirs(PLAYWRIGHT_USER_DIR, exist_ok=True)

    stealth = Stealth(
        navigator_webdriver=True,
        navigator_platform=True,
        navigator_platform_override="Linux x86_64",
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

    print(f"[*] Launching Playwright (headful mode: {not headless})...")
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=PLAYWRIGHT_USER_DIR,
            channel="chrome",
            headless=headless,
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

        await stealth.apply_stealth_async(context)
        await context.add_init_script(EXTRA_STEALTH_JS)

        page = context.pages[0] if context.pages else await context.new_page()

        print(f"[*] Navigating to: {url}")
        await page.goto(url, wait_until="domcontentloaded")

        # Wait for the main post component to be available
        try:
            await page.wait_for_selector("shreddit-post, article, [role='main']", timeout=15000)
        except Exception:
            print("[!] Warning: Main post selector wait timed out, continuing with available DOM...")

        # Gentle scroll to trigger loading of top comments
        await asyncio.sleep(2)
        await page.evaluate("window.scrollBy(0, 600)")
        await asyncio.sleep(1.5)

        # -------------------------------------------------------------
        # 1. Extract Post Metrics (Upvotes, Views, Title, Subreddit)
        # -------------------------------------------------------------
        post_elem = page.locator("shreddit-post").first
        has_shreddit = await post_elem.count() > 0

        post_title = ""
        score_val = "0"
        upvote_ratio_val = None
        comment_count_val = "0"
        subreddit_val = "reddit"
        author_val = "unknown"

        if has_shreddit:
            post_title = await post_elem.get_attribute("post-title") or ""
            score_val = await post_elem.get_attribute("score") or "0"
            upvote_ratio_val = await post_elem.get_attribute("upvote-ratio")
            comment_count_val = await post_elem.get_attribute("comment-count") or "0"
            subreddit_val = await post_elem.get_attribute("subreddit-name") or "reddit"
            author_val = await post_elem.get_attribute("author") or "unknown"

        # Fallback for title using accessibility role 'heading'
        if not post_title:
            heading_loc = page.get_by_role("heading", level=1)
            if await heading_loc.count() > 0:
                post_title = (await heading_loc.first.inner_text()).strip()
            else:
                headings = await page.get_by_role("heading").all_inner_texts()
                if headings:
                    post_title = headings[0].strip()

        # Fallback for upvote score
        if score_val == "0":
            upvote_btn = page.get_by_role("button", name=re.compile(r"upvote", re.I))
            if await upvote_btn.count() > 0:
                score_text = await upvote_btn.first.inner_text()
                digits = re.findall(r"\d+[\d,.]*[kKmM]?", score_text)
                if digits:
                    score_val = digits[0]

        # Extract View Count
        # Search post inner text or accessibility text for patterns like '34K views'
        view_count_val = "Not publicly visible"
        post_text = ""
        if has_shreddit:
            post_text = await post_elem.inner_text()
        else:
            post_text = await page.locator("body").inner_text()

        view_match = re.search(r"([\d,.]+\s*[kKmMbB]?)\s*views?", post_text, re.IGNORECASE)
        if view_match:
            view_count_val = f"{view_match.group(1).strip()} views"
        else:
            # Check for view text element via accessibility locator
            view_locator = page.get_by_text(re.compile(r"\b\d+[\d,.]*[kKmMbB]?\s*views?\b", re.I))
            if await view_locator.count() > 0:
                view_count_val = (await view_locator.first.inner_text()).strip()

        # -------------------------------------------------------------
        # 2. Extract Top 10 Comments
        # -------------------------------------------------------------
        print("[*] Extracting top comments...")
        comments_list: List[CommentData] = []

        comment_elements = page.locator("shreddit-comment")
        c_count = await comment_elements.count()

        if c_count > 0:
            for i in range(min(c_count, 15)):
                if len(comments_list) >= 10:
                    break

                c = comment_elements.nth(i)
                c_author = await c.get_attribute("author") or "anonymous"
                c_score = await c.get_attribute("score") or "0"

                # Extract direct comment text, excluding nested child comments
                direct_text = await c.evaluate("""el => {
                    const slot = el.querySelector('div[slot="comment"]');
                    if (slot) {
                        const clone = slot.cloneNode(true);
                        clone.querySelectorAll('shreddit-comment').forEach(nested => nested.remove());
                        return clone.innerText.trim();
                    }
                    const paragraphs = Array.from(el.querySelectorAll('p'));
                    if (paragraphs.length > 0) {
                        return paragraphs.map(p => p.innerText.trim()).join(' ');
                    }
                    return el.innerText.trim();
                }""")

                clean_text = re.sub(r"\s+", " ", direct_text).strip()
                if clean_text:
                    comments_list.append(
                        CommentData(author=c_author, score=str(c_score), text=clean_text)
                    )

        # Fallback if shreddit-comment was not found: use role 'article' or comment testids
        if not comments_list:
            comment_articles = page.locator("article, [data-testid='comment']")
            art_count = await comment_articles.count()
            for i in range(min(art_count, 10)):
                art = comment_articles.nth(i)
                art_text = (await art.inner_text()).strip()
                if art_text:
                    comments_list.append(
                        CommentData(author=f"commenter_{i+1}", score="N/A", text=art_text)
                    )

        print(f"[✓] Successfully scraped post metrics and {len(comments_list)} comments.")

        await context.close()

        return ScrapedPostData(
            url=url,
            title=post_title or "Reddit Post",
            subreddit=subreddit_val,
            author=author_val,
            upvote_count=str(score_val),
            upvote_ratio=upvote_ratio_val,
            view_count=view_count_val,
            total_comments_count=str(comment_count_val),
            top_comments=comments_list,
        )


# -----------------------------------------------------------------------------
# Analysis Logic using Groq
# -----------------------------------------------------------------------------
def analyze_scraped_data(data: ScrapedPostData) -> RedditPostAnalysis:
    """
    Sends scraped Reddit post details to Groq with openai/gpt-oss-120b
    and returns a structured sentiment & rating analysis.
    """
    if not os.path.exists(PROMPT_PATH):
        raise FileNotFoundError(f"System prompt file not found at: {PROMPT_PATH}")

    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        system_prompt = f.read()

    # Format the top comments for the prompt
    comments_formatted = []
    for idx, c in enumerate(data.top_comments, start=1):
        comments_formatted.append(
            f"{idx}. [{c.author}] (Score: {c.score} upvotes):\n   \"{c.text}\""
        )
    comments_block = "\n\n".join(comments_formatted) if comments_formatted else "No comments found."

    user_prompt = f"""Please analyze the community reception and sentiment for this Reddit post:

### Post Details:
- **Title**: {data.title}
- **Subreddit**: r/{data.subreddit}
- **Author**: u/{data.author}
- **Post URL**: {data.url}

### Engagement Metrics:
- **Upvote Count (Score)**: {data.upvote_count}
- **Upvote Ratio**: {data.upvote_ratio or 'N/A'}
- **View Count**: {data.view_count}
- **Total Comments Count**: {data.total_comments_count}

### Top 10 Comments:
{comments_block}

Evaluate the sentiment, rate the post out of 10, and determine if it was positively or negatively received by the community.
"""

    print("\n[*] Sending scraped data to Groq (model: openai/gpt-oss-120b)...")
    analysis_result = ask_groq(
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema=RedditPostAnalysis,
        model="openai/gpt-oss-120b",
        schema_name="reddit_post_sentiment_analysis",
        temperature=0.2,
        strict=True,
    )

    return analysis_result


def print_analysis_report(data: ScrapedPostData, analysis: RedditPostAnalysis):
    """
    Prints a rich, formatted analysis report to standard output.
    """
    print("\n" + "=" * 70)
    print("                REDDIT POST SENTIMENT & RECEPTION REPORT")
    print("=" * 70)
    print(f"Title:         {data.title}")
    print(f"Subreddit:     r/{data.subreddit}")
    print(f"URL:           {data.url}")
    print(f"Upvotes:       {data.upvote_count} (Ratio: {data.upvote_ratio or 'N/A'})")
    print(f"Views:         {data.view_count}")
    print(f"Total Comments:{data.total_comments_count} (Scraped: {len(data.top_comments)})")
    print("-" * 70)

    # Reception badge / icon
    reception_icon = "🟢" if analysis.reception == "positively received" else (
        "🔴" if analysis.reception == "negatively received" else "🟡"
    )
    print(f"COMMUNITY RATING:   {analysis.rating:.1f} / 10.0")
    print(f"RECEPTION VERDICT:  {reception_icon} {analysis.reception.upper()}")
    print(f"HEADLINE:           {analysis.verdict}")
    print("-" * 70)

    print("\n[SUMMARY]")
    print(analysis.summary)

    print("\n[ENGAGEMENT ANALYSIS]")
    print(analysis.engagement_analysis)

    print("\n[KEY COMMENT THEMES]")
    for theme in analysis.key_comment_themes:
        print(f"  • {theme}")

    if analysis.top_positive_points:
        print("\n[POSITIVE HIGHLIGHTS & AGREEMENT]")
        for point in analysis.top_positive_points:
            print(f"  + {point}")

    if analysis.top_critical_points:
        print("\n[CRITICAL REMARKS & SKEPTICISM]")
        for point in analysis.top_critical_points:
            print(f"  - {point}")

    print("\n" + "=" * 70)


def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Scrape and analyze Reddit post sentiment using Playwright and Groq."
    )
    parser.add_argument(
        "url",
        nargs="?",
        default=None,
        help="Reddit post URL to scrape and analyze.",
    )
    parser.add_argument(
        "--url",
        dest="flag_url",
        type=str,
        default=None,
        help="Alternative flag to supply the Reddit post URL.",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run Playwright in headless mode (default: headful mode).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        default=False,
        help="Output the raw analysis JSON to stdout.",
    )
    args = parser.parse_args()

    if not (args.flag_url or args.url):
        parser.error("A Reddit post URL must be specified (either as a positional argument or via --url).")

    return args


async def main():
    args = parse_arguments()
    target_url = args.flag_url or args.url

    print("==================================================")
    print("   Reddit Post Sentiment Analyzer (Playwright + Groq)")
    print("==================================================")
    print(f"Target URL:     {target_url}")
    print(f"Headful Mode:   {not args.headless}")
    print(f"Groq Model:     openai/gpt-oss-120b")
    print("==================================================")

    # 1. Scrape post data with Playwright
    scraped_data = await scrape_reddit_post(url=target_url, headless=args.headless)

    # 2. Analyze with Groq
    analysis = analyze_scraped_data(scraped_data)

    # 3. Print report
    if args.json:
        output_payload = {
            "scraped_data": scraped_data.model_dump(),
            "analysis": analysis.model_dump(),
        }
        print(json.dumps(output_payload, indent=2))
    else:
        print_analysis_report(scraped_data, analysis)


if __name__ == "__main__":
    asyncio.run(main())
