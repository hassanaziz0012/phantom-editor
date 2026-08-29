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
import contextlib
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv

from metadata.recommendations.embed_my_videos import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_DELAY_SECONDS,
    DEFAULT_INPUT_PATH as DEFAULT_VIDEOS_JSON,
    DEFAULT_OUTPUT_PATH as DEFAULT_EMBEDDINGS_NPY,
    generate_video_embeddings,
)
from metadata.recommendations.summarize_my_channel import (
    DEFAULT_OUTPUT_PATH as DEFAULT_SUMMARY_OUTPUT_PATH,
    load_existing_videos,
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


class IssueCaptureHandler(logging.Handler):
    """Captures warning, error, and critical log messages into a list."""

    def __init__(self, level: int = logging.WARNING) -> None:
        super().__init__(level)
        self.issues: List[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:
            msg = record.getMessage()
        if msg and msg not in self.issues:
            self.issues.append(msg)


@contextlib.contextmanager
def suppress_all_output():
    """Redirects file descriptors 1 (stdout) and 2 (stderr) and Python sys.stdout/stderr to /dev/null."""
    sys.stdout.flush()
    sys.stderr.flush()

    null_fd = None
    orig_stdout_fd = None
    orig_stderr_fd = None

    try:
        null_fd = os.open(os.devnull, os.O_RDWR)
        orig_stdout_fd = os.dup(1)
        orig_stderr_fd = os.dup(2)
        os.dup2(null_fd, 1)
        os.dup2(null_fd, 2)
    except Exception:
        if orig_stdout_fd is not None:
            try:
                os.close(orig_stdout_fd)
            except Exception:
                pass
            orig_stdout_fd = None
        if orig_stderr_fd is not None:
            try:
                os.close(orig_stderr_fd)
            except Exception:
                pass
            orig_stderr_fd = None
        if null_fd is not None:
            try:
                os.close(null_fd)
            except Exception:
                pass
            null_fd = None

    captured_out = io.StringIO()
    captured_err = io.StringIO()

    try:
        with contextlib.redirect_stdout(captured_out), contextlib.redirect_stderr(captured_err):
            yield captured_err
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        if null_fd is not None:
            if orig_stdout_fd is not None:
                try:
                    os.dup2(orig_stdout_fd, 1)
                    os.close(orig_stdout_fd)
                except Exception:
                    pass
            if orig_stderr_fd is not None:
                try:
                    os.dup2(orig_stderr_fd, 2)
                    os.close(orig_stderr_fd)
                except Exception:
                    pass
            try:
                os.close(null_fd)
            except Exception:
                pass


def prepare_recommendations(
    channel: Optional[str] = None,
    videos_json_path: Path = DEFAULT_VIDEOS_JSON,
    embeddings_npy_path: Path = DEFAULT_EMBEDDINGS_NPY,
    limit: Optional[int] = None,
    fresh: bool = False,
    force: bool = False,
    reverse: bool = False,
    batch_size: int = DEFAULT_BATCH_SIZE,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    quiet: bool = False,
) -> Dict[str, Any]:
    """Runs channel video summarization followed by semantic video embeddings generation.

    Returns a summary dictionary containing operational details, new videos added,
    embedding statistics, and any issues encountered.
    """
    start_time = time.time()
    issue_handler = IssueCaptureHandler(level=logging.WARNING)
    issue_handler.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    root_logger = logging.getLogger()
    saved_handlers = list(root_logger.handlers)

    if quiet:
        root_logger.handlers = [issue_handler]
        logging.getLogger("googleapiclient").setLevel(logging.ERROR)
        logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
        logging.getLogger("urllib3").setLevel(logging.ERROR)
    else:
        root_logger.addHandler(issue_handler)

    try:
        initial_existing_videos = load_existing_videos(videos_json_path)
        initial_id_map: Dict[str, Dict[str, Any]] = {
            r["video_id"]: r
            for r in initial_existing_videos
            if isinstance(r, dict) and "video_id" in r
        }
        initial_summarized_ids: Set[str] = {
            vid for vid, r in initial_id_map.items() if r.get("ai_summary")
        }

        if not quiet:
            logger.info("=" * 60)
            logger.info("Step 1/2: Summarizing YouTube Channel Videos")
            logger.info("=" * 60)

        summarized_records = summarize_channel(
            channel=channel,
            output_path=videos_json_path,
            limit=limit,
            fresh=fresh,
            force=force,
            reverse=reverse,
        )

        if not quiet:
            logger.info("")
            logger.info("=" * 60)
            logger.info("Step 2/2: Generating Vector Embeddings for Channel Videos")
            logger.info("=" * 60)

        embeddings_matrix = generate_video_embeddings(
            input_path=videos_json_path,
            output_path=embeddings_npy_path,
            batch_size=batch_size,
            delay_seconds=delay_seconds,
        )

        newly_added_videos: List[Dict[str, Any]] = []
        newly_summarized_videos: List[Dict[str, Any]] = []
        skipped_count = 0

        for r in summarized_records:
            vid = r.get("video_id")
            if not vid:
                continue
            if vid not in initial_id_map:
                newly_added_videos.append(r)
                if r.get("ai_summary"):
                    newly_summarized_videos.append(r)
            elif vid not in initial_summarized_ids and r.get("ai_summary"):
                newly_summarized_videos.append(r)
            elif force and r.get("ai_summary"):
                newly_summarized_videos.append(r)
            else:
                skipped_count += 1

        total_videos_count = len(summarized_records)
        embeddings_count = (
            int(embeddings_matrix.shape[0])
            if hasattr(embeddings_matrix, "shape") and len(embeddings_matrix.shape) > 0
            else 0
        )
        embeddings_dim = (
            int(embeddings_matrix.shape[1])
            if hasattr(embeddings_matrix, "ndim")
            and embeddings_matrix.ndim > 1
            and embeddings_matrix.shape[0] > 0
            else 0
        )
        duration_sec = round(time.time() - start_time, 2)

        new_count = len(newly_added_videos)
        summarized_count = len(newly_summarized_videos)

        if new_count > 0 or summarized_count > 0:
            summary_desc = (
                f"Prepared recommendations data: {new_count} new video(s) added, "
                f"{summarized_count} video(s) summarized, {embeddings_count} embeddings generated."
            )
        else:
            summary_desc = (
                f"Recommendations data up to date: 0 new videos added, "
                f"{skipped_count} video(s) skipped, {embeddings_count} embeddings generated."
            )

        if not quiet:
            logger.info("")
            logger.info("🎉 All recommendations data prepared successfully!")
            logger.info("   - Video summaries: %s", videos_json_path)
            logger.info("   - Embeddings:      %s", embeddings_npy_path)
            logger.info("   - New videos added: %d", new_count)
            logger.info("   - Videos summarized: %d", summarized_count)

        report: Dict[str, Any] = {
            "success": True,
            "status": "success",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "channel": channel or os.getenv("YOUTUBE_CHANNEL_ID"),
            "summary": summary_desc,
            "files": {
                "videos_json": str(videos_json_path),
                "embeddings_npy": str(embeddings_npy_path),
            },
            "stats": {
                "total_videos": total_videos_count,
                "existing_videos_before": len(initial_existing_videos),
                "new_videos_added": new_count,
                "newly_summarized": summarized_count,
                "skipped_count": skipped_count,
                "embeddings_count": embeddings_count,
                "embeddings_dimension": embeddings_dim,
            },
            "new_videos": newly_added_videos,
            "newly_summarized_videos": newly_summarized_videos,
            "issues": list(issue_handler.issues),
            "duration_seconds": duration_sec,
        }
        return report

    finally:
        root_logger.handlers = saved_handlers


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
    parser.add_argument(
        "--delay", "-d",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help=f"Delay in seconds between embedding batch requests (default: {DEFAULT_DELAY_SECONDS}s).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Suppress logs and output summary report exclusively as JSON.",
    )

    args = parser.parse_args()
    videos_json = Path(args.output_json).resolve()
    embeddings_npy = Path(args.output_embeddings).resolve()

    if args.json:
        start_time = time.time()
        report = None
        error_caught = None
        captured_err_text = ""

        try:
            with suppress_all_output() as err_stream:
                report = prepare_recommendations(
                    channel=args.channel,
                    videos_json_path=videos_json,
                    embeddings_npy_path=embeddings_npy,
                    limit=args.limit,
                    fresh=args.fresh,
                    force=args.force,
                    reverse=args.reverse,
                    batch_size=args.batch_size,
                    delay_seconds=args.delay,
                    quiet=True,
                )
                captured_err_text = err_stream.getvalue().strip() if err_stream else ""
        except Exception as e:
            error_caught = e

        if error_caught is not None:
            duration_sec = round(time.time() - start_time, 2)
            issues = [str(error_caught)]
            if captured_err_text and captured_err_text not in issues:
                issues.append(captured_err_text)

            error_report: Dict[str, Any] = {
                "success": False,
                "status": "error",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "channel": args.channel or os.getenv("YOUTUBE_CHANNEL_ID"),
                "summary": f"Preparation pipeline failed: {error_caught}",
                "error": str(error_caught),
                "files": {
                    "videos_json": str(videos_json),
                    "embeddings_npy": str(embeddings_npy),
                },
                "stats": {
                    "total_videos": 0,
                    "existing_videos_before": 0,
                    "new_videos_added": 0,
                    "newly_summarized": 0,
                    "skipped_count": 0,
                    "embeddings_count": 0,
                    "embeddings_dimension": 0,
                },
                "new_videos": [],
                "newly_summarized_videos": [],
                "issues": issues,
                "duration_seconds": duration_sec,
            }
            print(json.dumps(error_report, indent=2, ensure_ascii=False))
            sys.exit(1)

        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        try:
            prepare_recommendations(
                channel=args.channel,
                videos_json_path=videos_json,
                embeddings_npy_path=embeddings_npy,
                limit=args.limit,
                fresh=args.fresh,
                force=args.force,
                reverse=args.reverse,
                batch_size=args.batch_size,
                delay_seconds=args.delay,
                quiet=False,
            )
        except Exception as e:
            logger.error("Preparation pipeline failed: %s", e)
            sys.exit(1)


if __name__ == "__main__":
    main()
