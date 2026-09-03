#!/usr/bin/env python3
"""
Embed Channel Videos with Gemini
===============================
Generates vector embeddings for videos in `my_videos.json` using Google's
`gemini-embedding-2` model. Specifically embeds combined video titles and AI summaries,
sends batched API requests (50 per batch), and saves the resulting embedding matrix
into a NumPy binary file (.npy).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import numpy as np
from dotenv import load_dotenv

# Add project root to sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load environment variables
load_dotenv(REPO_ROOT / ".env")
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phantom.metadata.embed_videos")

DEFAULT_INPUT_PATH = REPO_ROOT / "metadata" / "recommendations" / "my_videos.json"
DEFAULT_OUTPUT_PATH = REPO_ROOT / "metadata" / "recommendations" / "my_videos_embeddings.npy"
MODEL_NAME = "gemini-embedding-2"
DEFAULT_BATCH_SIZE = 50
DEFAULT_DELAY_SECONDS = 30.0
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_DELAY_SECONDS = 30.0


def mask_api_key(key: str) -> str:
    """Masks an API key for safe logging (e.g. '...ABCD')."""
    if not key:
        return "EMPTY"
    if len(key) <= 8:
        return "***"
    return f"...{key[-6:]}"


def resolve_api_key(api_key: Optional[str] = None) -> str:
    """
    Resolves and returns the Gemini API key.

    Sources checked in order:
    1. Explicit `api_key` argument.
    2. GEMINI_API_KEY_1 environment variable.
    3. GEMINI_API_KEY / GOOGLE_API_KEY fallback.
    """
    key = api_key or os.getenv("GEMINI_API_KEY_1") or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not key:
        raise ValueError(
            "GEMINI_API_KEY_1 is not set in environment or .env file."
        )
    return key.strip()


def resolve_api_keys(
    api_keys: Optional[Union[str, List[str]]] = None,
    api_key: Optional[Union[str, List[str]]] = None,
) -> List[str]:
    """Compatibility wrapper returning the single resolved Gemini API key in a list."""
    val = api_key or api_keys
    if isinstance(val, (list, tuple)) and val:
        val = val[0]
    return [resolve_api_key(val if isinstance(val, str) else None)]


def build_embedding_text(video: Dict[str, Any]) -> str:
    """Combines title and AI summary into a single formatted string for semantic embedding."""
    title = (video.get("title") or "").strip()
    summary = (video.get("ai_summary") or "").strip()

    if title and summary:
        return f"Title: {title}\nSummary: {summary}"
    elif summary:
        return f"Summary: {summary}"
    elif title:
        return f"Title: {title}"
    return "Untitled Video"


def fetch_embeddings_batch(
    texts: List[str],
    api_key: Optional[str] = None,
    model: str = MODEL_NAME,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_delay: float = DEFAULT_RETRY_DELAY_SECONDS,
) -> List[List[float]]:
    """
    Fetches text embeddings for a single batch using Google's Gemini API with retry logic.
    """
    resolved_key = resolve_api_key(api_key=api_key)
    masked_key = mask_api_key(resolved_key)

    requests_list = [
        {
            "model": f"models/{model}",
            "content": {"parts": [{"text": txt}]},
        }
        for txt in texts
    ]

    payload = {"requests": requests_list}
    req_data = json.dumps(payload).encode("utf-8")

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents?key={resolved_key}"
    req = urllib.request.Request(
        url,
        data=req_data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    for attempt in range(1, max_retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                if "embeddings" not in res_data:
                    raise KeyError(f"API response missing 'embeddings' field: {res_data}")

                embeddings: List[List[float]] = []
                for item in res_data["embeddings"]:
                    embeddings.append(item["values"])
                return embeddings

        except urllib.error.HTTPError as e:
            error_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
            logger.warning(
                "HTTP %d error on attempt %d/%d (API key %s): %s. Response: %s",
                e.code,
                attempt,
                max_retries,
                masked_key,
                e.reason,
                error_body[:200],
            )
            if attempt == max_retries:
                raise RuntimeError(
                    f"Gemini Embedding API failed with HTTP {e.code} ({e.reason}): {error_body}"
                ) from e
            logger.info(
                "Waiting %.1fs before retry attempt %d/%d...",
                retry_delay,
                attempt + 1,
                max_retries,
            )
            time.sleep(retry_delay)

        except Exception as e:
            logger.warning(
                "Error on attempt %d/%d (API key %s): %s",
                attempt,
                max_retries,
                masked_key,
                e,
            )
            if attempt == max_retries:
                raise
            logger.info(
                "Waiting %.1fs before retry attempt %d/%d...",
                retry_delay,
                attempt + 1,
                max_retries,
            )
            time.sleep(retry_delay)

    raise RuntimeError("Failed to retrieve embeddings after maximum retries.")


def generate_video_embeddings(
    input_path: Path = DEFAULT_INPUT_PATH,
    output_path: Path = DEFAULT_OUTPUT_PATH,
    batch_size: int = DEFAULT_BATCH_SIZE,
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
    api_key: Optional[str] = None,
) -> np.ndarray:
    """Loads video records, builds embedding texts, batches API requests with retry logic, and saves to .npy."""
    resolved_key = resolve_api_key(api_key=api_key)
    logger.info("Using Gemini API key (%s) for embeddings generation.", mask_api_key(resolved_key))

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    logger.info("Loading video metadata from %s...", input_path)
    with open(input_path, "r", encoding="utf-8") as f:
        videos: List[Dict[str, Any]] = json.load(f)

    if not videos:
        logger.warning("No videos found in %s.", input_path)
        empty_array = np.empty((0, 0), dtype=np.float32)
        return empty_array

    total_videos = len(videos)
    logger.info("Preparing embedding texts for %d videos...", total_videos)

    texts = [build_embedding_text(v) for v in videos]

    all_embeddings: List[List[float]] = []
    total_batches = (total_videos + batch_size - 1) // batch_size

    logger.info(
        "Requesting embeddings using '%s' in %d batch(es) of up to %d items...",
        MODEL_NAME,
        total_batches,
        batch_size,
    )

    for batch_idx in range(total_batches):
        if batch_idx > 0 and delay_seconds > 0:
            logger.info(
                "Waiting %.1fs before next batch to respect Gemini API rate limits...",
                delay_seconds,
            )
            time.sleep(delay_seconds)

        start_idx = batch_idx * batch_size
        end_idx = min(start_idx + batch_size, total_videos)
        batch_texts = texts[start_idx:end_idx]

        logger.info(
            "[%d/%d] Processing items %d-%d of %d...",
            batch_idx + 1,
            total_batches,
            start_idx + 1,
            end_idx,
            total_videos,
        )

        batch_results = fetch_embeddings_batch(
            texts=batch_texts,
            api_key=resolved_key,
            model=MODEL_NAME,
        )
        all_embeddings.extend(batch_results)

    # Convert to NumPy float32 matrix
    embeddings_matrix = np.array(all_embeddings, dtype=np.float32)

    # Ensure output directory exists and save
    output_path.parent.mkdir(parents=True, exist_ok=True)
    np.save(output_path, embeddings_matrix)

    file_size_kb = output_path.stat().st_size / 1024
    logger.info(
        "Successfully saved embeddings matrix %s (%.2f KB) to %s",
        embeddings_matrix.shape,
        file_size_kb,
        output_path,
    )

    return embeddings_matrix


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate gemini-embedding-2 embeddings for video summaries in my_videos.json and save to a .npy file."
    )
    parser.add_argument(
        "--input", "-i",
        default=str(DEFAULT_INPUT_PATH),
        help=f"Path to input videos JSON (default: {DEFAULT_INPUT_PATH}).",
    )
    parser.add_argument(
        "--output", "-o",
        default=str(DEFAULT_OUTPUT_PATH),
        help=f"Path to output .npy file (default: {DEFAULT_OUTPUT_PATH}).",
    )
    parser.add_argument(
        "--batch-size", "-b",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=f"Batch size for Gemini API requests (default: {DEFAULT_BATCH_SIZE}).",
    )
    parser.add_argument(
        "--delay", "-d",
        type=float,
        default=DEFAULT_DELAY_SECONDS,
        help=f"Delay in seconds between batch requests to respect rate limits (default: {DEFAULT_DELAY_SECONDS}s).",
    )
    parser.add_argument(
        "--api-key", "-k",
        default=None,
        help="Gemini API key (default: GEMINI_API_KEY_1 in .env).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview formatted texts without calling the Gemini API.",
    )

    args = parser.parse_args()
    input_file = Path(args.input).resolve()
    output_file = Path(args.output).resolve()

    if args.dry_run:
        with open(input_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(f"Loaded {len(data)} videos from {input_file}.\nSample formatted texts:")
        for idx, item in enumerate(data[:3], 1):
            print(f"\n--- Video #{idx} ({item.get('video_id')}) ---")
            print(build_embedding_text(item))
        return

    try:
        generate_video_embeddings(
            input_path=input_file,
            output_path=output_file,
            batch_size=args.batch_size,
            delay_seconds=args.delay,
            api_key=args.api_key,
        )
    except Exception as e:
        logger.error("Failed to generate and save video embeddings: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
