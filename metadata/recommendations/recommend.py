#!/usr/bin/env python3
"""
YouTube Video Recommendation Engine
===================================
Recommends 2-3 topically relevant or prerequisite/follow-up videos from the channel
to include in the video's metadata.

Workflow:
1. Loads current video metadata (title and AI-generated description).
2. Generates semantic vector embedding using Google's `gemini-embedding-2` model.
3. Computes cosine similarity against existing channel video embeddings (`my_videos_embeddings.npy`).
4. Shortlists the top 15 most semantically similar candidate videos.
5. Feeds candidates to Claude via BrowserLLM using `agentic/prompts/recommend_videos.md`.
6. Claude selects and ranks the top 2-3 recommendations with concise justifications.
7. Saves structured recommendations (rank, video_id, title, url, reason) to `metadata.json`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from dotenv import load_dotenv

# Ensure repository root is in sys.path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Load environment variables
load_dotenv(REPO_ROOT / ".env")
load_dotenv()

from agentic.ask_browserllm import ask_claude, load_prompt, parse_claude_json
from metadata import utils
from metadata.recommendations.embed_my_videos import (
    DEFAULT_INPUT_PATH as DEFAULT_VIDEOS_JSON,
    DEFAULT_OUTPUT_PATH as DEFAULT_EMBEDDINGS_NPY,
    MODEL_NAME as EMBEDDING_MODEL_NAME,
    build_embedding_text,
    fetch_embeddings_batch,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("phantom.metadata.recommend")

TOP_CANDIDATES_COUNT = 15


def extract_series_tag(title: str) -> Optional[str]:
    """Extracts series information like 'Automation Tip #5' or 'Part 2' from title if present."""
    patterns = [
        r"[-–—|]\s*([A-Za-z0-9\s]+#\d+)",
        r"((?:Automation\s+Tip|Tip|Part|Episode|Ep|Lesson|Chapter)\s*#?\s*\d+)",
    ]
    for pat in patterns:
        match = re.search(pat, title, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def get_current_video_info(
    target: Optional[str | Path] = None,
    metadata_path: Optional[Path] = None,
) -> Tuple[Path, Path, Dict[str, Any], str, str, Optional[str]]:
    """
    Resolves project paths and extracts current video's title, description/summary, and video_id.
    Returns (project_dir, save_meta_path, metadata_dict, title, description, video_id).
    """
    project_dir, default_meta_path, captions_path, default_title = utils.resolve_project_paths(target)
    save_meta_path = metadata_path or default_meta_path

    metadata: Dict[str, Any] = {}
    if save_meta_path.is_file():
        try:
            metadata = json.loads(save_meta_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Could not read existing metadata file (%s): %s", save_meta_path, e)
            metadata = {}

    title = str(metadata.get("title") or default_title).strip()
    description = str(metadata.get("description") or metadata.get("ai_summary") or "").strip()

    # If description is missing but captions exist, attempt to generate or warn
    if not description and captions_path and captions_path.is_file():
        logger.info("Description is missing in metadata.json. Generating from captions...")
        try:
            from metadata.auto_gen_desc import generate_description_for_project
            description = generate_description_for_project(captions_path, metadata_path=save_meta_path)
            metadata["description"] = description
        except Exception as e:
            logger.warning("Could not auto-generate description from captions: %s", e)

    video_url = str(metadata.get("url") or "")
    current_video_id: Optional[str] = None
    if video_url:
        match = re.search(r"(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_-]{11})", video_url)
        if match:
            current_video_id = match.group(1)

    return project_dir, save_meta_path, metadata, title, description, current_video_id


def compute_video_embedding(
    title: str,
    description: str,
    api_key: Optional[str] = None,
) -> np.ndarray:
    """Generates a semantic embedding vector for the current video using Gemini."""
    resolved_api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    if not resolved_api_key:
        raise ValueError(
            "GEMINI_API_KEY (or GOOGLE_API_KEY) is not set in environment or .env file."
        )

    embed_text = build_embedding_text({"title": title, "ai_summary": description})
    logger.info("Generating embedding for current video using '%s'...", EMBEDDING_MODEL_NAME)
    embeddings = fetch_embeddings_batch(
        texts=[embed_text],
        api_key=resolved_api_key,
        model=EMBEDDING_MODEL_NAME,
    )
    if not embeddings:
        raise RuntimeError("Gemini Embedding API returned empty embedding list.")

    return np.array(embeddings[0], dtype=np.float32)


def shortlist_candidate_videos(
    current_embedding: np.ndarray,
    current_video_id: Optional[str] = None,
    current_title: Optional[str] = None,
    videos_json_path: Path = DEFAULT_VIDEOS_JSON,
    embeddings_npy_path: Path = DEFAULT_EMBEDDINGS_NPY,
    top_n: int = TOP_CANDIDATES_COUNT,
) -> List[Dict[str, Any]]:
    """
    Computes cosine similarity of current embedding with all video embeddings in dataset,
    filters out the current video, and returns top_n candidate video dictionaries.
    """
    if not videos_json_path.exists():
        raise FileNotFoundError(f"Videos metadata file not found at: {videos_json_path}")

    logger.info("Loading existing video dataset from %s...", videos_json_path.name)
    with open(videos_json_path, "r", encoding="utf-8") as f:
        dataset_videos: List[Dict[str, Any]] = json.load(f)

    if not embeddings_npy_path.exists():
        logger.info("Embeddings matrix not found at %s. Generating...", embeddings_npy_path)
        from metadata.recommendations.embed_my_videos import generate_video_embeddings
        dataset_embeddings = generate_video_embeddings(input_path=videos_json_path, output_path=embeddings_npy_path)
    else:
        dataset_embeddings = np.load(embeddings_npy_path)
        if len(dataset_videos) != dataset_embeddings.shape[0]:
            logger.info(
                "Dataset count mismatch (%d videos in JSON vs %d rows in embeddings). Re-generating embeddings...",
                len(dataset_videos),
                dataset_embeddings.shape[0],
            )
            from metadata.recommendations.embed_my_videos import generate_video_embeddings
            dataset_embeddings = generate_video_embeddings(input_path=videos_json_path, output_path=embeddings_npy_path)

    # Compute Cosine Similarity
    # sim(u, v) = dot(u, v) / (norm(u) * norm(v))
    norm_current = np.linalg.norm(current_embedding)
    norm_dataset = np.linalg.norm(dataset_embeddings, axis=1)

    norm_current = norm_current if norm_current > 0 else 1e-9
    norm_dataset = np.where(norm_dataset > 0, norm_dataset, 1e-9)

    similarities = np.dot(dataset_embeddings, current_embedding) / (norm_dataset * norm_current)

    # Sort indices in descending order of similarity
    sorted_indices = np.argsort(similarities)[::-1]

    candidates: List[Dict[str, Any]] = []
    norm_title = current_title.strip().lower() if current_title else ""

    for idx in sorted_indices:
        video = dataset_videos[idx]
        vid_id = video.get("video_id")
        v_title = (video.get("title") or "").strip()

        # Filter out current video if matched
        if current_video_id and vid_id == current_video_id:
            continue
        if norm_title and v_title.lower() == norm_title:
            continue

        candidate_item = {
            "video_id": vid_id,
            "title": v_title,
            "summary": video.get("ai_summary") or "",
            "view_count": video.get("views", 0),
            "published_date": video.get("publish_date", ""),
            "url": video.get("url") or f"https://www.youtube.com/watch?v={vid_id}",
            "series_tag": extract_series_tag(v_title),
            "similarity_score": float(similarities[idx]),
        }
        candidates.append(candidate_item)

        if len(candidates) >= top_n:
            break

    logger.info("Found top %d candidate videos by cosine similarity.", len(candidates))
    return candidates


def format_candidates_for_prompt(candidates: List[Dict[str, Any]]) -> str:
    """Formats the shortlisted candidate videos into structured text for Claude."""
    blocks: List[str] = []
    for idx, c in enumerate(candidates, 1):
        series_info = f", series_tag: {c['series_tag']}" if c.get("series_tag") else ""
        block = (
            f"{idx}. video_id: {c['video_id']}\n"
            f"   title: {c['title']}\n"
            f"   summary: {c['summary']}\n"
            f"   view_count: {c['view_count']}\n"
            f"   published_date: {c['published_date']}{series_info}"
        )
        blocks.append(block)
    return "\n\n".join(blocks)


def select_recommendations_with_claude(
    current_title: str,
    current_summary: str,
    candidates: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Prompts Claude via BrowserLLM with the candidate shortlist to select and rank
    the top 2-3 recommendations.
    """
    candidate_list_text = format_candidates_for_prompt(candidates)
    prompt = load_prompt(
        "recommend_videos.md",
        current_title=current_title or "Untitled",
        current_summary=current_summary or "No summary available.",
        candidate_list=candidate_list_text,
    )

    logger.info("Querying Claude via BrowserLLM to select top recommendations...")
    raw_response = ask_claude(prompt)
    raw_text = str(raw_response).strip()

    parsed = parse_claude_json(raw_text)
    if not isinstance(parsed, list):
        raise ValueError(f"Claude did not return a JSON list. Response was: {raw_text[:200]}")

    # Build video lookup table
    candidate_map = {c["video_id"]: c for c in candidates}

    recommendations: List[Dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        vid = item.get("video_id")
        if not vid:
            continue

        candidate_info = candidate_map.get(vid)
        video_title = candidate_info.get("title", "") if candidate_info else ""
        video_url = (
            candidate_info.get("url")
            if candidate_info
            else f"https://www.youtube.com/watch?v={vid}"
        )

        rank_val = item.get("rank")
        try:
            rank_int = int(rank_val) if rank_val is not None else len(recommendations) + 1
        except (ValueError, TypeError):
            rank_int = len(recommendations) + 1

        rec = {
            "rank": rank_int,
            "video_id": vid,
            "title": video_title,
            "url": video_url,
            "reason": str(item.get("reason") or "").strip(),
        }
        recommendations.append(rec)

    # Sort by rank
    recommendations.sort(key=lambda r: r["rank"])
    return recommendations


def recommend_videos_for_project(
    target: Optional[str | Path] = None,
    metadata_path: Optional[Path] = None,
    top_n: int = TOP_CANDIDATES_COUNT,
    dry_run: bool = False,
) -> List[Dict[str, Any]]:
    """
    Main pipeline function:
    1. Reads project metadata.
    2. Embeds current title + summary.
    3. Finds top N similar videos from channel dataset.
    4. Uses Claude to select top 2-3 recommendations.
    5. Saves to metadata.json.
    """
    project_dir, save_meta_path, metadata, title, description, current_video_id = get_current_video_info(
        target=target,
        metadata_path=metadata_path,
    )

    print("\n" + "=" * 60)
    print("🎬 Current Video:")
    print(f"  Title:   {title}")
    print(f"  Summary: {description or '(Empty)'}")
    print(f"  Target:  {save_meta_path}")
    print("=" * 60)

    # 1. Compute embedding for current video
    current_embedding = compute_video_embedding(title=title, description=description)

    # 2. Shortlist top candidate matches
    candidates = shortlist_candidate_videos(
        current_embedding=current_embedding,
        current_video_id=current_video_id,
        current_title=title,
        top_n=top_n,
    )

    if not candidates:
        logger.warning("No candidate videos found in dataset.")
        return []

    # 3. Ask Claude to select and rank top 2-3 recommendations
    recommendations = select_recommendations_with_claude(
        current_title=title,
        current_summary=description,
        candidates=candidates,
    )

    if not recommendations:
        logger.warning("Claude did not select any recommendations.")
        return []

    # 4. Save recommendations to metadata.json
    metadata["recommendations"] = recommendations

    if not dry_run:
        save_meta_path.parent.mkdir(parents=True, exist_ok=True)
        with open(save_meta_path, "w", encoding="utf-8") as f:
            json.dump(metadata, f, indent=4, ensure_ascii=False)
            f.write("\n")
        logger.info("Saved %d recommendations to %s", len(recommendations), save_meta_path)
    else:
        logger.info("[Dry Run] Skipped writing to %s", save_meta_path)

    return recommendations


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recommend top 2-3 relevant channel videos for a project and update metadata.json."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Path to project directory, video file, or metadata.json (default: current directory).",
    )
    parser.add_argument(
        "--metadata", "-m",
        default=None,
        help="Path to metadata.json directly.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=TOP_CANDIDATES_COUNT,
        help=f"Number of semantic matches to shortlist for Claude (default: {TOP_CANDIDATES_COUNT}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and display recommendations without modifying metadata.json.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output recommendations exclusively as formatted JSON.",
    )

    args = parser.parse_args()
    custom_meta = Path(args.metadata).resolve() if args.metadata else None

    try:
        recommendations = recommend_videos_for_project(
            target=args.target,
            metadata_path=custom_meta,
            top_n=args.top_n,
            dry_run=args.dry_run,
        )

        if args.json:
            print(json.dumps(recommendations, indent=2, ensure_ascii=False))
            return

        print("\n" + "=" * 60)
        print("🎯 Recommended Videos for Next Watch:")
        print("=" * 60)
        for rec in recommendations:
            print(f"\n[{rec['rank']}] {rec['title']}")
            print(f"    🔗 URL:    {rec['url']}")
            print(f"    💡 Reason: {rec['reason']}")
        print("=" * 60)

    except Exception as e:
        logger.error("Failed to generate recommendations: %s", e)
        sys.exit(1)


if __name__ == "__main__":
    main()
