"""
Reddit Post Sentiment and Engagement Analyzer.

Uses Playwright (via `reddit.scrape_reddit_post`) to extract post engagement metrics
(upvote count, view count, top comments) from a Reddit post URL, and analyzes the community
sentiment and reception using Groq's openai/gpt-oss-120b model with structured outputs.

Usage:
    uv run python reddit/analyze_post.py "https://www.reddit.com/r/..."
    uv run python reddit/analyze_post.py --url "https://reddit.com/..." --json
"""

import argparse
import asyncio
import json
import os
import sys
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

# Ensure repo root is in python path for module imports
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from agentic.ask_groq import ask_groq
from reddit.scrape_reddit_post import CommentData, ScrapedPostData, scrape_reddit_post

PROMPT_PATH = os.path.join(REPO_ROOT, "agentic", "prompts", "analyse_reddit_post_sentiment.md")


# -----------------------------------------------------------------------------
# Pydantic Schemas for Sentiment Analysis Output
# -----------------------------------------------------------------------------
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
