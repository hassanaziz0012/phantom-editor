#!/usr/bin/env python3
"""
Prepare YouTube Recommendations Data
=====================================
Orchestration script that prepares channel video data for the recommendation engine.
Every time this script runs, it executes:
1. `summarize_my_channel.py` - Fetches new channel videos, transcribes them, generates AI summaries, and saves to `my_videos.json`.
2. `embed_my_videos.py` - Generates vector embeddings with Google Gemini for `my_videos.json` and saves to `my_videos_embeddings.npy`.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

from metadata.recommendations.embed_my_videos import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_INPUT_PATH as DEFAULT_VIDEOS_JSON,
    DEFAULT_OUTPUT_PATH as DEFAULT_EMBEDDINGS_NPY,
    generate_video_embeddings,
)
from metadata.recommendations.summarize_my_channel import (
    DEFAULT_OUTPUT_PATH as DEFAULT_SUMMARY_OUTPUT_PATH,
    summarize_channel,
)

load_dotenv(REPO_ROOT / ".env")
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phantom.metadata.prepare_recommendations")


def prepare_recommendations(
    channel: Optional[str] = None,
    videos_json_path: Path = DEFAULT_VIDEOS_JSON,
    embeddings_npy_path: Path = DEFAULT_EMBEDDINGS_NPY,
    limit: Optional[int] = None,
    fresh: bool = False,
    force: bool = False,
    reverse: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> None:
    """Runs channel video summarization followed by semantic video embeddings generation."""
    logger.info("=" * 60)
    logger.info("Step 1/2: Summarizing YouTube Channel Videos")
    logger.info("=" * 60)

    summarize_channel(
        channel=channel,
        output_path=videos_json_path,
        limit=limit,
        fresh=fresh,
        force=force,
        reverse=reverse,
    )

    logger.info("")
    logger.info("=" * 60)
    logger.info("Step 2/2: Generating Vector Embeddings for Channel Videos")
    logger.info("=" * 60)

    generate_video_embeddings(
        input_path=videos_json_path,
        output_path=embeddings_npy_path,
        batch_size=batch_size,
    )

    logger.info("")
    logger.info("🎉 All recommendations data prepared successfully!")
    logger.info("   - Video summaries: %s", videos_json_path)
    logger.info("   - Embeddings:      %s", embeddings_npy_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare recommendation data by running channel summarization followed by video embeddings generation."
    )
    parser.add_argument(
        "channel",
        nargs="?",
        default=None,
        help="YouTube channel ID, handle (@channel), or custom URL (default: YOUTUBE_CHANNEL_ID in .env).",
    )
    parser.add_argument(
        "--output-json", "-o",
        default=str(DEFAULT_VIDEOS_JSON),
        help=f"Path to save/read videos JSON (default: {DEFAULT_VIDEOS_JSON}).",
    )
    parser.add_argument(
        "--output-embeddings", "-e",
        default=str(DEFAULT_EMBEDDINGS_NPY),
        help=f"Path to save embeddings .npy file (default: {DEFAULT_EMBEDDINGS_NPY}).",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=None,
        help="Maximum number of videos to summarize.",
    )
    parser.add_argument(
        "--force", "-f",
        action="store_true",
        help="Force re-summarization of videos that already have an ai_summary.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Force fresh video list fetch from YouTube API, bypassing local cache.",
    )
    parser.add_argument(
        "--reverse",
        action="store_true",
        help="Process oldest videos first when summarizing.",
    )
    parser.add_argument(
        "--batch-size", "-b",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Batch size for Gemini embedding requests (default: {DEFAULT_BATCH_SIZE}).",
    )

    args = parser.parse_args()

    try:
        prepare_recommendations(
            channel=args.channel,
            videos_json_path=Path(args.output_json).resolve(),
            embeddings_npy_path=Path(args.output_embeddings).resolve(),
            limit=args.limit,
            fresh=args.fresh,
            force=args.force,
            reverse=args.reverse,
            batch_size=args.batch_size,
        )
    except Exception as e:
        logger.error("Preparation pipeline failed: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
