#!/usr/bin/env python3
"""
Recommend related videos from the current YouTube channel inventory.

### How It Works (The Recommendation Algorithm)

The script calculates a "Similarity Score" (up to 1.0 or 100%) between a target "seed" 
video and every other candidate video in the channel. Videos with the highest scores 
are recommended.

The final score is built by adding up seven different matching factors. Each factor 
has a specific weight representing its importance:

1. Title Match (38% weight): 
   Calculates how many meaningful words are shared between both titles. It divides the 
   number of shared words by the total unique words across both titles (ignoring common 
   stop words like "the", "and"). This is known as Jaccard similarity.

2. Tags Match (28% weight): 
   Similar to the title match, it calculates the overlapping words in the tags of both videos.

3. Description Match (18% weight): 
   Calculates the overlapping words in the descriptions of both videos.

4. Category Match (8% weight): 
   Gives the full 8% boost if both videos belong to the exact same YouTube category, 
   otherwise it gives 0%.

5. Duration Match (5% weight): 
   Compares video lengths by dividing the shorter duration by the longer duration. 
   (For example, comparing a 5-minute video and a 10-minute video gives a 50% match, 
   which contributes 2.5% to the final score).

6. Recency Boost (2% weight): 
   A small mathematical boost (using a logarithmic curve) given to newer videos. It 
   slightly favors recent uploads without penalizing older evergreen videos too harshly.

7. Popularity Boost (1% weight): 
   A tiny boost based on the candidate video's view count. It scales logarithmically, 
   meaning the boost slowly maxes out as a video approaches 10 million views.

* Note: If the script evaluates the exact same video as the seed, it applies a -1.0 
score penalty to ensure a video never recommends itself.
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# Add the project root directory to sys.path to allow absolute package imports
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

# Import shared models and fetchers
from youtube_api.models import Video, VideoSeed, RankedVideo
from youtube_api.fetch_videos import fetch_channel_videos
from youtube_api.utils import (
    tokenize,
    normalize_tags,
    overlap_score,
    parse_iso8601_duration,
    duration_similarity,
    recency_score,
    popularity_score,
)

load_dotenv()


def build_seed_from_metadata(metadata: dict[str, Any]) -> VideoSeed:
    return VideoSeed(
        title=metadata.get("title", ""),
        description=metadata.get("description", ""),
        tags=list(metadata.get("tags", [])),
        category_id=str(metadata["categoryId"]) if metadata.get("categoryId") is not None else None,
    )


def build_seed_from_existing_video(video: Video) -> VideoSeed:
    return VideoSeed(
        title=video.title,
        description=video.description,
        tags=video.tags,
        category_id=video.category_id,
        duration_seconds=parse_iso8601_duration(video.duration),
        video_id=video.video_id,
    )


def score_video(seed: VideoSeed, candidate: Video) -> RankedVideo:
    title_tokens = tokenize(seed.title)
    description_tokens = tokenize(seed.description)
    tag_tokens = normalize_tags(seed.tags)

    candidate_title_tokens = tokenize(candidate.title)
    candidate_description_tokens = tokenize(candidate.description)
    candidate_tag_tokens = normalize_tags(candidate.tags)
    candidate_duration_seconds = parse_iso8601_duration(candidate.duration)

    reasons = {
        "title": overlap_score(title_tokens, candidate_title_tokens) * 0.38,
        "tags": overlap_score(tag_tokens, candidate_tag_tokens) * 0.28,
        "description": overlap_score(description_tokens, candidate_description_tokens) * 0.18,
        "category": 0.08 if seed.category_id and seed.category_id == candidate.category_id else 0.0,
        "duration": duration_similarity(seed.duration_seconds, candidate_duration_seconds) * 0.05,
        "recency": recency_score(candidate.published_at) * 0.02,
        "popularity": popularity_score(candidate.view_count) * 0.01,
    }

    if seed.video_id and seed.video_id == candidate.video_id:
        reasons["self_match_penalty"] = -1.0

    return RankedVideo(
        video=candidate,
        score=sum(reasons.values()),
        reasons=reasons,
    )


def rank_related_videos(seed: VideoSeed, videos: list[Video], limit: int) -> list[RankedVideo]:
    ranked = [score_video(seed, video) for video in videos if video.live_broadcast != "upcoming"]
    ranked = [item for item in ranked if item.score > 0]
    ranked.sort(key=lambda item: (item.score, item.video.view_count or 0), reverse=True)
    return ranked[:limit]


def load_metadata(path: Path) -> dict[str, Any]:
    with path.open() as handle:
        return json.load(handle)


def find_video_by_id(videos: list[Video], video_id: str) -> Video:
    for video in videos:
        if video.video_id == video_id:
            return video
    raise ValueError(f"Video ID not found in channel inventory: {video_id}")


def format_ranked_video(index: int, ranked: RankedVideo) -> str:
    video = ranked.video
    reasons = ", ".join(
        f"{name}={value:.3f}"
        for name, value in sorted(ranked.reasons.items(), key=lambda item: item[1], reverse=True)
        if value > 0
    )
    return (
        f"{index}. {video.title}\n"
        f"   score={ranked.score:.3f} | views={video.view_count or 0:,} | published={video.published_at.date()}\n"
        f"   {video.url}\n"
        f"   {reasons or 'no matching signals'}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Recommend related videos from your YouTube channel.")
    source_group = parser.add_mutually_exclusive_group(required=True)
    source_group.add_argument(
        "--metadata",
        type=Path,
        help="Path to a metadata.json file for the new upload.",
    )
    source_group.add_argument(
        "--video-id",
        help="Use an existing channel video as the source item.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="How many related videos to return.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of a text list.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    api_key = os.getenv("YOUTUBE_API_KEY")
    channel_id = os.getenv("YOUTUBE_CHANNEL_ID")
    if not api_key or not channel_id:
        print("Error: YOUTUBE_API_KEY and YOUTUBE_CHANNEL_ID must be set in .env", file=sys.stderr)
        sys.exit(1)

    try:
        videos = fetch_channel_videos(api_key, channel_id)
    except Exception as e:
        print(f"Error fetching channel videos: {e}", file=sys.stderr)
        sys.exit(1)

    if args.metadata:
        seed = build_seed_from_metadata(load_metadata(args.metadata))
    else:
        try:
            seed = build_seed_from_existing_video(find_video_by_id(videos, args.video_id))
        except Exception as e:
            print(f"Error finding seed video: {e}", file=sys.stderr)
            sys.exit(1)

    ranked = rank_related_videos(seed, videos, limit=args.limit)

    if args.json:
        payload = [
            {
                "video_id": item.video.video_id,
                "title": item.video.title,
                "url": item.video.url,
                "score": round(item.score, 4),
                "published_at": item.video.published_at.isoformat(),
                "view_count": item.video.view_count,
                "reasons": {key: round(value, 4) for key, value in item.reasons.items() if value > 0},
            }
            for item in ranked
        ]
        print(json.dumps(payload, indent=2))
        return

    for index, item in enumerate(ranked, start=1):
        print(format_ranked_video(index, item))
        print()


if __name__ == "__main__":
    main()
