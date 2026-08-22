"""
Command-Line Interface for Database Management
===============================================
Responsible only for CLI argument parsing and terminal display output.
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

try:
    from .channels import get_all_channels
    from .config import (
        POSTGRES_CONTAINER_NAME,
        POSTGRES_DB,
        POSTGRES_HOST,
        POSTGRES_IMAGE,
        POSTGRES_PORT,
    )
    from .connection import get_db_connection
    from .docker import (
        ensure_postgres_container,
        get_container_status,
        stop_postgres_container,
    )
    from .queries import get_outlier_videos, get_top_videos
    from .schema import init_db
    from .scoring import update_video_scores
except ImportError:
    from ideas.outliers_db.channels import get_all_channels
    from ideas.outliers_db.config import (
        POSTGRES_CONTAINER_NAME,
        POSTGRES_DB,
        POSTGRES_HOST,
        POSTGRES_IMAGE,
        POSTGRES_PORT,
    )
    from ideas.outliers_db.connection import get_db_connection
    from ideas.outliers_db.docker import (
        ensure_postgres_container,
        get_container_status,
        stop_postgres_container,
    )
    from ideas.outliers_db.queries import get_outlier_videos, get_top_videos
    from ideas.outliers_db.schema import init_db
    from ideas.outliers_db.scoring import update_video_scores


def format_number(val: Any) -> str:
    """Formats large numbers nicely (e.g. 1.2M, 45.3K)."""
    if val is None:
        return "-"
    try:
        num = float(val)
        if num >= 1_000_000:
            return f"{num / 1_000_000:.1f}M"
        if num >= 1_000:
            return f"{num / 1_000:.1f}K"
        return f"{num:.0f}" if num.is_integer() else f"{num:.1f}"
    except (ValueError, TypeError):
        return str(val)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="phantom ideas outliers",
        description="PostgreSQL Database Manager for YouTube Channels & Outlier Analytics",
    )
    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # setup command
    subparsers.add_parser("setup", help="Ensure Docker container is running and initialize tables/indexes")

    # start / stop / status
    subparsers.add_parser("start", help="Start PostgreSQL Docker container")
    subparsers.add_parser("stop", help="Stop PostgreSQL Docker container")
    subparsers.add_parser("status", help="Check status of Docker container and database connection")

    # stats & scores
    calc_parser = subparsers.add_parser("calc", help="Calculate channel averages and video scores")
    calc_parser.add_argument("--channel", "-c", help="Specific channel ID to calculate")
    calc_parser.add_argument("--threshold", "-t", type=float, default=2.0, help="Outlier score multiplier threshold")

    # list outliers
    outliers_parser = subparsers.add_parser("outliers", help="List detected outlier videos")
    outliers_parser.add_argument("--channel", "-c", help="Filter by channel ID")
    outliers_parser.add_argument("--min-score", "-s", type=float, default=2.0, help="Minimum outlier score")
    outliers_parser.add_argument("--min-views", "-v", type=int, default=100, help="Minimum view count")
    outliers_parser.add_argument("--days", "-d", type=int, default=None, help="Filter videos published within the last N days")
    outliers_parser.add_argument("--no-shorts", action="store_true", help="Exclude YouTube Shorts from results")
    outliers_parser.add_argument("--limit", "-n", type=int, default=25, help="Number of results")

    # list channels
    subparsers.add_parser("channels", help="List all channels with average views and likes")

    # top videos
    top_parser = subparsers.add_parser("top", help="List top performing videos")
    top_parser.add_argument("--channel", "-c", help="Filter by channel ID")
    top_parser.add_argument("--sort", choices=["score", "view_score", "like_score", "views", "recent"], default="score")
    top_parser.add_argument("--days", "-d", type=int, default=None, help="Filter videos published within the last N days")
    top_parser.add_argument("--no-shorts", action="store_true", help="Exclude YouTube Shorts from results")
    top_parser.add_argument("--limit", "-n", type=int, default=20, help="Number of results")

    args = parser.parse_args()

    if not args.command or args.command == "setup":
        print("\n🔧 [Phantom DB] Setting up PostgreSQL Docker server & database...")
        success = ensure_postgres_container()
        if not success:
            sys.exit(1)
        init_db()
        print("✅ PostgreSQL container and database tables are ready!")

    elif args.command == "start":
        print(f"🚀 Starting container '{POSTGRES_CONTAINER_NAME}' ({POSTGRES_IMAGE})...")
        if ensure_postgres_container():
            print("✅ Container is up and healthy.")

    elif args.command == "stop":
        print(f"🛑 Stopping container '{POSTGRES_CONTAINER_NAME}'...")
        stop_postgres_container()

    elif args.command == "status":
        status = get_container_status()
        print(f"📦 Container Name: {POSTGRES_CONTAINER_NAME}")
        print(f"🖼️  Docker Image:   {POSTGRES_IMAGE}")
        print(f"📊 Container Status: {status or 'NOT CREATED'}")
        print(f"🔗 Host / Port:     {POSTGRES_HOST}:{POSTGRES_PORT}")
        print(f"🗄️  Database Name:   {POSTGRES_DB}")
        if status == "running":
            try:
                with get_db_connection(auto_start=False) as conn:
                    with conn.cursor() as cur:
                        cur.execute("SELECT COUNT(*) AS c FROM channels;")
                        channels_count = cur.fetchone()["c"]
                        cur.execute("SELECT COUNT(*) AS v, COUNT(CASE WHEN is_outlier THEN 1 END) AS o FROM videos;")
                        row = cur.fetchone()
                        videos_count = row["v"]
                        outliers_count = row["o"]
                print("🟢 Database Health: Connected")
                print(f"📈 Total Channels:  {channels_count}")
                print(f"🎬 Total Videos:    {videos_count} ({outliers_count} outliers)")
            except Exception as e:
                print(f"🔴 Database Health: Connection Failed ({e})")

    elif args.command == "calc":
        print("🧮 Calculating channel averages and video outlier scores...")
        count = update_video_scores(channel_id=args.channel, outlier_threshold=args.threshold)
        print(f"✅ Updated scores for {count} videos.")

    elif args.command == "channels":
        channels = get_all_channels()
        if not channels:
            print("No channels found in database.")
            return
        print(f"\n{'TITLE':<30} {'SUBSCRIBERS':<12} {'VIDEOS':<8} {'AVG VIEWS':<12} {'AVG LIKES':<12} {'OUTLIERS':<8}")
        print("-" * 88)
        for ch in channels:
            print(
                f"{ch['title'][:28]:<30} "
                f"{format_number(ch['subscriber_count']):<12} "
                f"{ch['stored_videos_count']:<8} "
                f"{format_number(ch['avg_views']):<12} "
                f"{format_number(ch['avg_likes']):<12} "
                f"{ch['outlier_videos_count']:<8}"
            )

    elif args.command == "outliers":
        outliers = get_outlier_videos(
            channel_id=args.channel,
            min_score=args.min_score,
            min_views=args.min_views,
            no_shorts=args.no_shorts,
            days=args.days,
            limit=args.limit,
        )
        if not outliers:
            print("No outlier videos found matching the criteria.")
            return
        print(f"\n{'TITLE':<40} {'CHANNEL':<20} {'VIEWS':<10} {'AVG VIEWS':<10} {'VIEW SCORE':<12} {'SCORE':<8}")
        print("-" * 106)
        for vid in outliers:
            print(
                f"{vid['title'][:38]:<40} "
                f"{vid['channel_title'][:18]:<20} "
                f"{format_number(vid['view_count']):<10} "
                f"{format_number(vid['channel_avg_views']):<10} "
                f"{vid['view_score']:<12.2f} "
                f"{vid['score']:<8.2f}"
            )

    elif args.command == "top":
        videos = get_top_videos(
            channel_id=args.channel,
            sort_by=args.sort,
            no_shorts=args.no_shorts,
            days=args.days,
            limit=args.limit,
        )
        if not videos:
            print("No videos found.")
            return
        print(f"\n{'TITLE':<40} {'CHANNEL':<20} {'VIEWS':<10} {'LIKES':<10} {'VIEW SCORE':<12} {'SCORE':<8}")
        print("-" * 106)
        for vid in videos:
            print(
                f"{vid['title'][:38]:<40} "
                f"{vid['channel_title'][:18]:<20} "
                f"{format_number(vid['view_count']):<10} "
                f"{format_number(vid['like_count']):<10} "
                f"{vid['view_score']:<12.2f} "
                f"{vid['score']:<8.2f}"
            )


if __name__ == "__main__":
    main()
