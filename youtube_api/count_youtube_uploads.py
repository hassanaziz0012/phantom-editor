#!/usr/bin/env python3
"""
YouTube Uploads Counter
========================
Calculates the number of videos uploaded on a YouTube channel over today,
last 7 days, last 14 days, and last 30 days. Displays results in a beautiful
ASCII table format.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

# Add project root to sys.path to allow package imports
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from youtube_api.fetch_videos import fetch_channel_videos
from youtube_api.utils import (
    get_youtube_client,
    resolve_channel_id,
    supports_color,
)

# Colors for terminal output
COLOR_HEADER = "\033[95m"  # Magenta
COLOR_BLUE = "\033[94m"    # Blue
COLOR_GREEN = "\033[92m"   # Green
COLOR_CYAN = "\033[96m"    # Cyan
COLOR_YELLOW = "\033[93m"  # Yellow
COLOR_BOLD = "\033[1m"
COLOR_RESET = "\033[0m"


def parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Count YouTube channel video uploads for today, last 7 days, last 14 days, and last 30 days."
    )
    parser.add_argument(
        "channel",
        nargs="?",
        default=None,
        help="YouTube channel ID, URL, or handle (@channel). Falls back to YOUTUBE_CHANNEL_ID in .env."
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Force a fresh fetch from the YouTube API, bypassing and updating the local JSON cache."
    )
    return parser.parse_args()


def main() -> None:
    # 1. Initialize environment & determine color support
    load_dotenv()
    args = parse_args()
    
    use_color = supports_color()
    c_header = COLOR_HEADER if use_color else ""
    c_blue = COLOR_BLUE if use_color else ""
    c_green = COLOR_GREEN if use_color else ""
    c_cyan = COLOR_CYAN if use_color else ""
    c_yellow = COLOR_YELLOW if use_color else ""
    c_bold = COLOR_BOLD if use_color else ""
    c_reset = COLOR_RESET if use_color else ""

    api_key = os.getenv("YOUTUBE_API_KEY")
    channel_env = os.getenv("YOUTUBE_CHANNEL_ID")
    
    if not api_key:
        print(f"{c_bold}{COLOR_HEADER}Error:{c_reset} YOUTUBE_API_KEY must be set in your .env file or environment.", file=sys.stderr)
        sys.exit(1)

    target_channel = args.channel or channel_env
    if not target_channel:
        print(
            f"{c_bold}{COLOR_HEADER}Error:{c_reset} A YouTube channel must be specified (either as a command-line argument or in YOUTUBE_CHANNEL_ID in your .env file).",
            file=sys.stderr
        )
        sys.exit(1)

    # 2. Authenticate and resolve channel ID
    try:
        youtube = get_youtube_client(api_key)
        resolved_channel_id = resolve_channel_id(youtube, target_channel)
    except Exception as e:
        print(f"{c_bold}{COLOR_HEADER}Error resolving channel ID:{c_reset} {e}", file=sys.stderr)
        sys.exit(1)

    # 3. Fetch channel videos using the cached fetcher
    print(f"{c_blue}Fetching upload statistics for channel: {c_bold}{target_channel}{c_reset}...")
    try:
        videos = fetch_channel_videos(api_key, resolved_channel_id, fresh=args.fresh)
    except Exception as e:
        print(f"{c_bold}{COLOR_HEADER}Error fetching videos:{c_reset} {e}", file=sys.stderr)
        sys.exit(1)

    channel_title = videos[0].channel_title if videos else target_channel

    # 4. Calculate date ranges in user's local timezone
    local_now = datetime.now().astimezone()
    local_tz = local_now.tzinfo
    today_date = local_now.date()

    count_today = 0
    count_7 = 0
    count_14 = 0
    count_30 = 0

    for v in videos:
        # Convert video published_at to system local timezone
        pub_local = v.published_at.astimezone(local_tz)
        pub_date = pub_local.date()

        diff_days = (today_date - pub_date).days
        if diff_days < 0:
            # Skip videos published in the future
            continue

        if diff_days == 0:
            count_today += 1
        if diff_days < 7:
            count_7 += 1
        if diff_days < 14:
            count_14 += 1
        if diff_days < 30:
            count_30 += 1

    # Format localized date strings for date ranges
    today_str = today_date.strftime("%Y-%m-%d")
    day_minus_6 = (today_date - timedelta(days=6)).strftime("%Y-%m-%d")
    day_minus_13 = (today_date - timedelta(days=13)).strftime("%Y-%m-%d")
    day_minus_29 = (today_date - timedelta(days=29)).strftime("%Y-%m-%d")

    range_today = today_str
    range_7 = f"{day_minus_6} to {today_str}"
    range_14 = f"{day_minus_13} to {today_str}"
    range_30 = f"{day_minus_29} to {today_str}"

    # 5. Display statistics in a premium ASCII table format
    # Column sizes (excluding spaces around content):
    # Col 1: Period (20 chars)
    # Col 2: Video Count (11 chars)
    # Col 3: Date Range (24 chars)
    
    print()
    print(f"{c_bold}{c_header}┌─────────────────────────────────────────────────────────────┐{c_reset}")
    title_str = f"YOUTUBE UPLOAD STATISTICS - {channel_title}"
    # Truncate if it's too long to fit centered in 59 characters
    if len(title_str) > 57:
        title_str = title_str[:54] + "..."
    centered_title = title_str.center(59)
    print(f"{c_bold}{c_header}│  {c_bold}{c_yellow}{centered_title}{c_reset}{c_bold}{c_header}  │{c_reset}")
    print(f"{c_bold}{c_header}├──────────────────────┬─────────────┬────────────────────────┤{c_reset}")
    
    # Header Row
    h_col1 = f"{'Period':<20}"
    h_col2 = f"{'Video Count':>11}"
    h_col3 = f"{'Date Range (Local)':<24}"
    print(f"{c_bold}{c_header}│{c_reset} {c_bold}{c_blue}{h_col1}{c_reset} {c_bold}{c_header}│{c_reset} {c_bold}{c_blue}{h_col2}{c_reset} {c_bold}{c_header}│{c_reset} {c_bold}{c_blue}{h_col3}{c_reset} {c_bold}{c_header}│{c_reset}")
    print(f"{c_bold}{c_header}├──────────────────────┼─────────────┼────────────────────────┤{c_reset}")

    # Data Rows function to prevent color formatting string length distortion
    def format_row(period: str, count: int, date_range: str) -> str:
        p_part = f"{c_bold}{c_cyan}{period:<20}{c_reset}"
        c_part = f"{c_bold}{c_green}{count:>11,}{c_reset}"
        d_part = f"{date_range:<24}"
        return f"{c_bold}{c_header}│{c_reset} {p_part} {c_bold}{c_header}│{c_reset} {c_part} {c_bold}{c_header}│{c_reset} {d_part} {c_bold}{c_header}│{c_reset}"

    # Print rows
    print(format_row("Today", count_today, range_today))
    print(format_row("Last 7 Days", count_7, range_7))
    print(format_row("Last 14 Days", count_14, range_14))
    print(format_row("Last 30 Days", count_30, range_30))
    
    print(f"{c_bold}{c_header}└──────────────────────┴─────────────┴────────────────────────┘{c_reset}")
    print()


if __name__ == "__main__":
    main()
