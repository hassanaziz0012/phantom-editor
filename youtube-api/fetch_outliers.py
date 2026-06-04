#!/usr/bin/env python3
"""
Fetch YouTube Channel Outliers
==============================
Fetches all videos of a given YouTube channel and calculates outlier scores
based on views and likes ratios compared to the channel averages.
Includes options to boost scores for recently uploaded videos.
"""

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

# Add the project root directory to sys.path to allow absolute package imports
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

# Reuse Data models and helper functions from package
from youtube_api.models import Video
from youtube_api.fetch_videos import fetch_channel_videos
from youtube_api.utils import resolve_channel_id, get_youtube_client, supports_color

# Color constants for elegant terminal formatting
COLOR_HEADER = "\033[95m"
COLOR_BLUE = "\033[94m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_BOLD = "\033[1m"
COLOR_RESET = "\033[0m"


def format_count_diff(diff: float, unit: str) -> str:
    """Format a count difference cleanly with a plus/minus sign."""
    if diff >= 0:
        return f"+{int(diff):,} {unit}"
    else:
        return f"{int(diff):,} {unit}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Identify outlier videos on a YouTube channel based on views and likes ratios."
    )
    parser.add_argument(
        "channel",
        help="Channel URL, handle (e.g. @channel), or channel ID (UC...).",
    )
    parser.add_argument(
        "--days",
        type=float,
        help="Give a 10%% boost multiplier to the outlier score of videos uploaded within this many days ago.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit the number of displayed outlier videos.",
    )
    parser.add_argument(
        "--api-key",
        help="YouTube Data API key (overrides YOUTUBE_API_KEY env var).",
    )
    return parser.parse_args()


def main() -> None:
    load_dotenv()
    args = parse_args()

    use_color = supports_color()
    c_red = COLOR_RED if use_color else ""
    c_reset = COLOR_RESET if use_color else ""

    if args.limit is not None and args.limit <= 0:
        print(f"{c_red}Error: --limit must be a positive integer.{c_reset}", file=sys.stderr)
        sys.exit(1)

    api_key = args.api_key or os.getenv("YOUTUBE_API_KEY")

    # 1. Authenticate and Build API Client
    try:
        youtube = get_youtube_client(args.api_key)
    except Exception as e:
        print(f"{c_red}Error authenticating API key: {e}{c_reset}", file=sys.stderr)
        sys.exit(1)

    # 2. Resolve Channel ID using robust utility
    try:
        channel_id = resolve_channel_id(youtube, args.channel)
    except Exception as e:
        print(f"{c_red}Error resolving channel ID: {e}{c_reset}", file=sys.stderr)
        sys.exit(1)

    # 3. Fetch all channel videos using fetch_channel_videos
    try:
        videos = fetch_channel_videos(api_key, channel_id)
    except Exception as e:
        print(f"{c_red}Error fetching channel videos: {e}{c_reset}", file=sys.stderr)
        sys.exit(1)

    if not videos:
        print("No videos found or channel has no uploads.")
        return

    # 4. Calculate average view count and average like count across all videos
    valid_views_videos = [v for v in videos if v.view_count is not None]
    valid_likes_videos = [v for v in videos if v.like_count is not None]

    avg_views = sum(v.view_count for v in valid_views_videos) / len(valid_views_videos) if valid_views_videos else 0.0
    avg_likes = sum(v.like_count for v in valid_likes_videos) / len(valid_likes_videos) if valid_likes_videos else 0.0

    # 5. Filter for outliers: higher-than-average views OR likes
    outlier_candidates = []
    now = datetime.now(timezone.utc)

    for v in videos:
        # Determine if either metric exceeds average
        has_higher_views = v.view_count is not None and v.view_count > avg_views
        has_higher_likes = v.like_count is not None and v.like_count > avg_likes

        if has_higher_views or has_higher_likes:
            # Calculate ratios
            view_ratio = v.view_count / avg_views if (v.view_count is not None and avg_views > 0) else 0.0
            like_ratio = v.like_count / avg_likes if (v.like_count is not None and avg_likes > 0) else 0.0

            # Gather valid ratios to calculate average ratio
            ratios = []
            if v.view_count is not None and avg_views > 0:
                ratios.append(view_ratio)
            if v.like_count is not None and avg_likes > 0:
                ratios.append(like_ratio)

            base_score = sum(ratios) / len(ratios) if ratios else 0.0

            # Boost logic: Check if video was uploaded within X days ago
            is_boosted = False
            age_in_days = (now - v.published_at).total_seconds() / 86400.0

            score = base_score
            if args.days is not None:
                if age_in_days <= args.days:
                    score = base_score * 1.10
                    is_boosted = True

            outlier_candidates.append({
                "video": v,
                "score": score,
                "base_score": base_score,
                "view_ratio": view_ratio,
                "like_ratio": like_ratio,
                "view_diff": v.view_count - avg_views if v.view_count is not None else 0,
                "like_diff": v.like_count - avg_likes if v.like_count is not None else 0,
                "age_in_days": age_in_days,
                "is_boosted": is_boosted
            })

    # Sort outliers by final score in descending order
    outlier_candidates.sort(key=lambda item: item["score"], reverse=True)

    # 6. Output results cleanly and beautifully
    c_header = COLOR_HEADER if use_color else ""
    c_blue = COLOR_BLUE if use_color else ""
    c_green = COLOR_GREEN if use_color else ""
    c_yellow = COLOR_YELLOW if use_color else ""
    c_bold = COLOR_BOLD if use_color else ""

    channel_name = videos[0].channel_title if videos else "Target Channel"
    print(f"\n{c_bold}{c_header}⭐ YOUTUBE CHANNEL OUTLIER REPORT ⭐{c_reset}")
    print(f"{c_blue}Channel:       {c_bold}{channel_name} ({channel_id}){c_reset}")
    print(f"{c_blue}Total Videos:  {c_bold}{len(videos)}{c_reset}")
    print(f"{c_blue}Average Views: {c_bold}{int(avg_views):,}{c_reset}")
    print(f"{c_blue}Average Likes: {c_bold}{int(avg_likes):,}{c_reset}")
    if args.days is not None:
        print(f"{c_yellow}Boost Info:    10% score boost applied to videos uploaded within the last {args.days} days{c_reset}")
    if args.limit is not None:
        print(f"{c_blue}Showing:       {c_bold}Top {args.limit} outliers (out of {len(outlier_candidates)} total outliers){c_reset}")
    print("-" * 80)

    if not outlier_candidates:
        print("No outlier videos detected (no videos have higher-than-average views or likes).")
        return

    displayed_candidates = outlier_candidates
    if args.limit is not None:
        displayed_candidates = outlier_candidates[:args.limit]

    for index, item in enumerate(displayed_candidates, start=1):
        v = item["video"]
        score_str = f"{item['score']:.2f}x"
        view_ratio_str = f"{item['view_ratio']:.2f}x"
        like_ratio_str = f"{item['like_ratio']:.2f}x"

        view_diff_str = format_count_diff(item["view_diff"], "views")
        like_diff_str = format_count_diff(item["like_diff"], "likes")

        # Color-code score for premium experience (higher score = more distinct color)
        c_score = c_red if item["score"] >= 3.0 else (c_yellow if item["score"] >= 1.5 else c_green)

        # Construct title with boost / emoji indicators
        boost_indicator = f" {c_yellow}🔥 [BOOSTED 10%]{c_reset}" if item["is_boosted"] else ""
        
        # Age presentation
        age_str = f"{item['age_in_days']:.1f} days ago" if item['age_in_days'] < 365 else f"{v.published_at.strftime('%Y-%m-%d')}"

        print(
            f"{c_bold}{index}. {v.title}{c_reset}"
        )
        print(
            f"   Score: {c_score}{c_bold}{score_str}{c_reset} "
            f"(Views: {c_blue}{view_ratio_str}{c_reset}, Likes: {c_blue}{like_ratio_str}{c_reset}) "
            f"({view_diff_str}, {like_diff_str}){boost_indicator}"
        )
        print(f"   Published: {age_str} | URL: {c_blue}{v.url}{c_reset}")
        print()


if __name__ == "__main__":
    main()
