#!/usr/bin/env python3
"""
Reddit YouTube Video Promotion Outreach Engine
==============================================
Scrapes posts from target subreddits, filters out previously analyzed posts
using Google Sheets, calculates semantic similarity with channel video embeddings,
grades each post using Groq with structured outputs, and records results into
the 'Reddit YT promotion' tab of the Outreach Tracker Google Sheet.

Usage:
    uv run python reddit/promote_videos.py
    uv run python reddit/promote_videos.py --limit 25 --feed new
    uv run python reddit/promote_videos.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import numpy as np
from dotenv import load_dotenv
from pydantic import BaseModel, Field

# Ensure repository root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load environment variables
load_dotenv(REPO_ROOT / ".env", override=True)
load_dotenv(override=True)

from agentic.ask_groq import ask_groq
from metadata.recommendations.embed_my_videos import (
    MODEL_NAME as EMBEDDING_MODEL_NAME,
    fetch_embeddings_batch,
    resolve_api_key as resolve_gemini_key,
)
from pipelines.google_sheet_utils import get_sheets_service
from reddit.scrape_subreddit import SubredditPostItem, scrape_subreddit

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phantom.reddit.promote_videos")

# Default file paths
DEFAULT_SUBREDDITS_FILE = REPO_ROOT / "reddit" / "subreddits_for_yt_promotion.txt"
DEFAULT_PROMPT_FILE = REPO_ROOT / "agentic" / "prompts" / "grade_post_for_video_promotion.md"
DEFAULT_VIDEOS_JSON = REPO_ROOT / "metadata" / "recommendations" / "my_videos.json"
DEFAULT_EMBEDDINGS_NPY = REPO_ROOT / "metadata" / "recommendations" / "my_videos_embeddings.npy"

DEFAULT_SHEET_TAB = "Reddit YT promotion"
EXPECTED_HEADERS = [
    "Post ID",
    "Post Title",
    "Post URL",
    "Subreddit",
    "Video URL",
    "Match",
    "Score",
    "Reason",
    "Suggested Angle",
]


# ==============================================================================
# Pydantic Schema for Groq Structured Output
# ==============================================================================
class GradePostResult(BaseModel):
    match: bool = Field(
        description="Whether any candidate video is a genuine, helpful fit to share"
    )
    video_id: Optional[str] = Field(
        default=None,
        description="ID of best-fit video, or null if no match",
    )
    score: int = Field(
        description="Fit score between 1 and 10",
    )
    reason: str = Field(
        description="One sentence on why this is (or isn't) a genuine fit",
    )
    suggested_angle: Optional[str] = Field(
        default=None,
        description="One sentence on how the reply should frame the video, or null if no match",
    )


# ==============================================================================
# Helper Functions
# ==============================================================================
def extract_post_id(url: str, fallback_id: str = "") -> str:
    """Extracts Reddit post ID from URL (e.g. /comments/<id>/) or cleans fallback id."""
    match = re.search(r"/comments/([a-zA-Z0-9]+)", url)
    if match:
        return match.group(1)
    if fallback_id:
        # Strip t3_ prefix if present
        return fallback_id.replace("t3_", "").strip()
    return url.strip()


def load_subreddits_list(file_path: Path) -> List[str]:
    """Reads subreddits from text file, stripping comments and r/ prefixes."""
    if not file_path.exists():
        raise FileNotFoundError(f"Subreddits file not found: {file_path}")

    subreddits: List[str] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cleaned = re.sub(r"^/?r/", "", line).strip()
        if cleaned:
            subreddits.append(cleaned)
    return subreddits


def load_prompt_template(prompt_path: Path) -> Tuple[str, str]:
    """
    Loads prompt markdown and splits it into system guidelines and user template.
    """
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt template file not found: {prompt_path}")

    content = prompt_path.read_text(encoding="utf-8")

    # Split on "Subreddit: {subreddit}"
    split_marker = "Subreddit: {subreddit}"
    if split_marker in content:
        parts = content.split(split_marker, 1)
        system_instructions = parts[0].strip()
        user_template = (split_marker + parts[1]).strip()
    else:
        system_instructions = (
            "You are screening Reddit posts to find genuine opportunities to share a "
            "relevant YouTube video as a helpful reply — not to spam or self-promote."
        )
        user_template = content.strip()

    return system_instructions, user_template


# ==============================================================================
# Google Sheets Integration & Duplicate Prevention
# ==============================================================================
def get_tracker_sheet_id(sheet_id_arg: Optional[str] = None) -> str:
    """Resolves the Google Outreach Tracker Sheet ID."""
    sid = sheet_id_arg or os.getenv("GOOGLE_OUTREACH_TRACKER_SHEET_ID")
    if not sid:
        raise ValueError(
            "GOOGLE_OUTREACH_TRACKER_SHEET_ID is not set in environment or .env file. "
            "Please configure GOOGLE_OUTREACH_TRACKER_SHEET_ID or pass --sheet-id."
        )
    return sid.strip()


def fetch_already_analyzed_posts(
    service,
    spreadsheet_id: str,
    sheet_name: str = DEFAULT_SHEET_TAB,
) -> Tuple[Set[str], Set[str]]:
    """
    Fetches existing records from the sheet and returns sets of (post_ids, post_urls)
    to prevent re-evaluating posts.
    """
    logger.info("Checking Google Sheet '%s' for previously analyzed posts...", sheet_name)
    try:
        res = service.spreadsheets().values().get(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A2:C",
        ).execute()
        rows = res.get("values", [])
    except Exception as e:
        logger.warning("Could not read existing rows from '%s': %s", sheet_name, e)
        return set(), set()

    existing_ids: Set[str] = set()
    existing_urls: Set[str] = set()

    for row in rows:
        if not row:
            continue
        p_id = str(row[0]).strip() if len(row) > 0 else ""
        p_url = str(row[2]).strip() if len(row) > 2 else ""

        if p_id:
            existing_ids.add(p_id)
        if p_url:
            existing_urls.add(p_url)
            # Also extract ID from URL in case of ID format variance
            extracted = extract_post_id(p_url)
            if extracted:
                existing_ids.add(extracted)

    logger.info(
        "Found %d previously analyzed post IDs (%d URLs) in Google Sheet.",
        len(existing_ids),
        len(existing_urls),
    )
    return existing_ids, existing_urls


def ensure_tab_headers(
    service,
    spreadsheet_id: str,
    sheet_name: str = DEFAULT_SHEET_TAB,
) -> None:
    """Ensures that the tab exists and contains the expected header columns."""
    # Check if sheet tab exists
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheet_titles = [s["properties"]["title"] for s in meta.get("sheets", [])]

    if sheet_name not in sheet_titles:
        logger.info("Tab '%s' not found. Creating new tab...", sheet_name)
        service.spreadsheets().batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{"addSheet": {"properties": {"title": sheet_name}}}]},
        ).execute()

    # Check header row
    res = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1:I1",
    ).execute()
    header_rows = res.get("values", [])

    if not header_rows or not header_rows[0] or not any(str(c).strip() for c in header_rows[0]):
        logger.info("Initializing header row in tab '%s'...", sheet_name)
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="USER_ENTERED",
            body={"values": [EXPECTED_HEADERS]},
        ).execute()


def append_evaluation_to_sheet(
    service,
    spreadsheet_id: str,
    row_values: List[Any],
    sheet_name: str = DEFAULT_SHEET_TAB,
) -> None:
    """Appends a single analyzed post row to the Google Sheet."""
    service.spreadsheets().values().append(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A:I",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row_values]},
    ).execute()


# ==============================================================================
# Semantic Similarity & Top 5 Matching
# ==============================================================================
class VideoCatalog:
    """Encapsulates the pre-generated video embeddings and catalog metadata."""

    def __init__(
        self,
        videos_json_path: Path = DEFAULT_VIDEOS_JSON,
        embeddings_npy_path: Path = DEFAULT_EMBEDDINGS_NPY,
    ):
        if not videos_json_path.exists():
            raise FileNotFoundError(f"Videos catalog file missing: {videos_json_path}")
        if not embeddings_npy_path.exists():
            raise FileNotFoundError(f"Embeddings matrix missing: {embeddings_npy_path}")

        logger.info("Loading channel video catalog from %s...", videos_json_path.name)
        with open(videos_json_path, "r", encoding="utf-8") as f:
            self.videos: List[Dict[str, Any]] = json.load(f)

        logger.info("Loading pre-computed video embeddings from %s...", embeddings_npy_path.name)
        self.embeddings: np.ndarray = np.load(embeddings_npy_path)

        if len(self.videos) != self.embeddings.shape[0]:
            raise ValueError(
                f"Count mismatch: {len(self.videos)} videos vs {self.embeddings.shape[0]} embeddings"
            )

        self.video_lookup: Dict[str, Dict[str, Any]] = {
            v.get("video_id"): v for v in self.videos if v.get("video_id")
        }

        # Pre-compute L2 norms for cosine similarity
        norm_v = np.linalg.norm(self.embeddings, axis=1)
        self.norm_embeddings = np.where(norm_v > 0, norm_v, 1e-9)

    def find_top_matches(
        self,
        post_title: str,
        post_body: str,
        top_k: int = 5,
    ) -> List[Dict[str, Any]]:
        """
        Computes cosine similarity between post (title + body) and all channel videos,
        returning the top_k most similar videos.
        """
        combined_text = f"Title: {post_title.strip()}"
        if post_body.strip():
            combined_text += f"\nBody: {post_body.strip()}"

        # Generate embedding vector for the post using Gemini
        embeddings = fetch_embeddings_batch(
            texts=[combined_text],
            model=EMBEDDING_MODEL_NAME,
        )
        if not embeddings:
            raise RuntimeError("Gemini Embedding API returned empty response for post.")

        post_vector = np.array(embeddings[0], dtype=np.float32)
        norm_p = np.linalg.norm(post_vector)
        norm_p = norm_p if norm_p > 0 else 1e-9

        # Cosine similarity: (A . B) / (||A|| * ||B||)
        similarities = np.dot(self.embeddings, post_vector) / (self.norm_embeddings * norm_p)

        # Get top_k indices sorted descending
        top_indices = np.argsort(similarities)[::-1][:top_k]

        candidates = []
        for idx in top_indices:
            v = self.videos[idx]
            candidates.append({
                "video_id": v.get("video_id"),
                "title": v.get("title", "").strip(),
                "summary": (v.get("ai_summary") or "").replace("\n", " ").strip(),
                "url": v.get("url") or f"https://www.youtube.com/watch?v={v.get('video_id')}",
                "score": float(similarities[idx]),
            })

        return candidates


def format_candidate_videos_text(candidates: List[Dict[str, Any]]) -> str:
    """
    Formats candidate videos matching the required prompt structure:
    id | title | one-line summary
    """
    lines: List[str] = []
    for c in candidates:
        v_id = c.get("video_id") or "unknown"
        title = c.get("title") or "Untitled"
        summary = c.get("summary") or "No summary available."
        lines.append(f"{v_id} | {title} | {summary}")
    return "\n".join(lines)


# ==============================================================================
# LLM Evaluation via Groq
# ==============================================================================
def grade_post_with_groq(
    system_instructions: str,
    user_template: str,
    subreddit: str,
    post_title: str,
    post_body: str,
    candidate_videos: List[Dict[str, Any]],
    model: str = "openai/gpt-oss-120b",
) -> GradePostResult:
    """
    Evaluates a post against top candidate videos using Groq's structured outputs.
    """
    candidate_text = format_candidate_videos_text(candidate_videos)

    user_prompt = (
        user_template.replace("{subreddit}", f"r/{subreddit.replace('r/', '')}")
        .replace("{title}", post_title)
        .replace("{body}", post_body if post_body.strip() else "[No body text]")
        .replace("{video_candidates}", candidate_text)
    )

    result = ask_groq(
        system_prompt=system_instructions,
        user_prompt=user_prompt,
        schema=GradePostResult,
        model=model,
        schema_name="grade_post_result",
        temperature=0.2,
        strict=True,
    )
    return result


# ==============================================================================
# Main Outreach Pipeline
# ==============================================================================
async def run_promotions_pipeline(
    subreddits_file: Path = DEFAULT_SUBREDDITS_FILE,
    prompt_file: Path = DEFAULT_PROMPT_FILE,
    limit_per_sub: int = 25,
    feed_type: str = "new",
    headless: bool = False,
    sheet_id: Optional[str] = None,
    sheet_name: str = DEFAULT_SHEET_TAB,
    dry_run: bool = False,
) -> None:
    """Executes the full Reddit video promotion grading and logging workflow."""
    print("=" * 80)
    print("      🚀 REDDIT YOUTUBE PROMOTION PIPELINE")
    print("=" * 80)
    print(f"Subreddits file:   {subreddits_file}")
    print(f"Feed type:         {feed_type}")
    print(f"Limit per sub:     {limit_per_sub}")
    print(f"Dry run mode:      {dry_run}")
    print("=" * 80)

    # 1. Load Subreddits list
    subreddits = load_subreddits_list(subreddits_file)
    logger.info("Loaded %d subreddit(s) to monitor: %s", len(subreddits), ", ".join(f"r/{s}" for s in subreddits))

    # 2. Load Prompt Template
    system_instructions, user_template = load_prompt_template(prompt_file)

    # 3. Initialize Video Catalog and Embeddings
    catalog = VideoCatalog()

    # 4. Connect to Google Sheets & Fetch Existing Analyzed Posts
    existing_ids: Set[str] = set()
    existing_urls: Set[str] = set()
    sheets_service = None
    target_sheet_id = ""

    if not dry_run:
        target_sheet_id = get_tracker_sheet_id(sheet_id)
        sheets_service = get_sheets_service()
        ensure_tab_headers(sheets_service, target_sheet_id, sheet_name=sheet_name)
        existing_ids, existing_urls = fetch_already_analyzed_posts(
            sheets_service,
            spreadsheet_id=target_sheet_id,
            sheet_name=sheet_name,
        )
    else:
        logger.info("[Dry Run] Skipping Google Sheets connection and deduplication check.")

    total_scraped = 0
    total_skipped = 0
    total_analyzed = 0
    total_matched = 0

    # 5. Process each subreddit
    for sub_idx, sub in enumerate(subreddits, start=1):
        print("\n" + "-" * 80)
        logger.info("[%d/%d] Scraping r/%s (%s feed, limit: %d)...", sub_idx, len(subreddits), sub, feed_type, limit_per_sub)
        print("-" * 80)

        try:
            scraped_data = await scrape_subreddit(
                subreddit_input=sub,
                feed_type=feed_type,
                limit=limit_per_sub,
                headless=headless,
                quiet=True,
            )
            posts = scraped_data.posts
            logger.info("Scraped %d posts from r/%s.", len(posts), sub)
        except Exception as e:
            logger.error("Failed to scrape r/%s: %s", sub, e)
            continue

        for p_idx, post in enumerate(posts, start=1):
            total_scraped += 1
            post_id = extract_post_id(post.url)

            # Check if post has already been evaluated
            if post_id in existing_ids or post.url in existing_urls:
                total_skipped += 1
                logger.info(
                    "  [#%d/%d] SKIP: Post '%s' (ID: %s) already in Google Sheet.",
                    p_idx,
                    len(posts),
                    post.title[:45],
                    post_id,
                )
                continue

            logger.info(
                "  [#%d/%d] ANALYZING: \"%s\" (ID: %s)",
                p_idx,
                len(posts),
                post.title[:55],
                post_id,
            )

            # Step 5a: Find Top 5 semantically matching videos
            try:
                candidates = catalog.find_top_matches(
                    post_title=post.title,
                    post_body=post.body_snippet,
                    top_k=5,
                )
            except Exception as e:
                logger.error("  Failed to compute cosine similarity for post '%s': %s", post_id, e)
                continue

            # Step 5b: Grade Post with Groq structured outputs
            try:
                grade = grade_post_with_groq(
                    system_instructions=system_instructions,
                    user_template=user_template,
                    subreddit=sub,
                    post_title=post.title,
                    post_body=post.body_snippet,
                    candidate_videos=candidates,
                )
                total_analyzed += 1
            except Exception as e:
                logger.error("  Failed Groq grading for post '%s': %s", post_id, e)
                continue

            # Resolve matched video URL
            video_url = ""
            if grade.video_id:
                matched_vid = catalog.video_lookup.get(grade.video_id)
                video_url = (
                    matched_vid.get("url")
                    if matched_vid
                    else f"https://www.youtube.com/watch?v={grade.video_id}"
                )

            is_match = bool(grade.match)
            if is_match:
                total_matched += 1
                match_icon = "🟢 MATCH"
            else:
                match_icon = "⚪ NO MATCH"

            logger.info(
                "    -> %s | Score: %d/10 | Best Video: %s",
                match_icon,
                grade.score,
                grade.video_id or "None",
            )
            logger.info("    -> Reason: %s", grade.reason)
            if grade.suggested_angle:
                logger.info("    -> Angle:  %s", grade.suggested_angle)

            # Step 5c: Record in Google Sheet
            row_data = [
                post_id,
                post.title,
                post.url,
                f"r/{sub}",
                video_url,
                "TRUE" if is_match else "FALSE",
                grade.score,
                grade.reason,
                grade.suggested_angle or "",
            ]

            if not dry_run and sheets_service:
                try:
                    append_evaluation_to_sheet(
                        service=sheets_service,
                        spreadsheet_id=target_sheet_id,
                        row_values=row_data,
                        sheet_name=sheet_name,
                    )
                    existing_ids.add(post_id)
                    existing_urls.add(post.url)
                    logger.info("    ✓ Appended to Google Sheet.")
                except Exception as e:
                    logger.error("    Failed to append row to Google Sheet: %s", e)
            elif dry_run:
                logger.info("    [Dry Run] Would append row: %s", row_data[:6])

    # Pipeline summary report
    print("\n" + "=" * 80)
    print("                    PIPELINE SUMMARY")
    print("=" * 80)
    print(f"Total Posts Scraped:   {total_scraped}")
    print(f"Previously Analyzed:   {total_skipped} (skipped)")
    print(f"Newly Evaluated:       {total_analyzed}")
    print(f"Genuine Video Matches: {total_matched}")
    print("=" * 80)


# ==============================================================================
# CLI Entry Point
# ==============================================================================
def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Scrape subreddits, grade outreach opportunities, and log to Google Sheets."
    )
    parser.add_argument(
        "--subreddits-file", "-f",
        default=str(DEFAULT_SUBREDDITS_FILE),
        help=f"Path to file listing subreddits (default: {DEFAULT_SUBREDDITS_FILE}).",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=25,
        help="Maximum posts to scrape per subreddit (default: 25).",
    )
    parser.add_argument(
        "--feed",
        default="new",
        choices=["new", "hot", "best", "rising", "top-daily", "top-weekly"],
        help="Subreddit feed category (default: 'new').",
    )
    parser.add_argument(
        "--sheet-id",
        default=None,
        help="Google Sheets ID (defaults to GOOGLE_OUTREACH_TRACKER_SHEET_ID).",
    )
    parser.add_argument(
        "--sheet-name",
        default=DEFAULT_SHEET_TAB,
        help=f"Target worksheet tab name (default: '{DEFAULT_SHEET_TAB}').",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run Chrome in headless mode.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Evaluate posts without writing to Google Sheets.",
    )
    return parser.parse_args()


def main():
    args = parse_arguments()

    try:
        asyncio.run(
            run_promotions_pipeline(
                subreddits_file=Path(args.subreddits_file).resolve(),
                limit_per_sub=args.limit,
                feed_type=args.feed,
                headless=args.headless,
                sheet_id=args.sheet_id,
                sheet_name=args.sheet_name,
                dry_run=args.dry_run,
            )
        )
    except KeyboardInterrupt:
        logger.info("\nPipeline interrupted by user.")
    except Exception as e:
        logger.error("Fatal pipeline error: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
