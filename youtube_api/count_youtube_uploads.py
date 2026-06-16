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
    parse_iso8601_duration,
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

    videos_today = 0
    shorts_today = 0
    total_today = 0

    videos_7 = 0
    shorts_7 = 0
    total_7 = 0

    videos_14 = 0
    shorts_14 = 0
    total_14 = 0

    videos_30 = 0
    shorts_30 = 0
    total_30 = 0

    for v in videos:
        # Convert video published_at to system local timezone
        pub_local = v.published_at.astimezone(local_tz)
        pub_date = pub_local.date()

        diff_days = (today_date - pub_date).days
        if diff_days < 0:
            # Skip videos published in the future
            continue

        duration_sec = parse_iso8601_duration(v.duration)
        is_short = duration_sec is not None and duration_sec <= 60

        if diff_days == 0:
            if is_short:
                shorts_today += 1
            else:
                videos_today += 1
            total_today += 1
        if diff_days < 7:
            if is_short:
                shorts_7 += 1
            else:
                videos_7 += 1
            total_7 += 1
        if diff_days < 14:
            if is_short:
                shorts_14 += 1
            else:
                videos_14 += 1
            total_14 += 1
        if diff_days < 30:
            if is_short:
                shorts_30 += 1
            else:
                videos_30 += 1
            total_30 += 1

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
    # Col 1: Period (16 chars)
    # Col 2: Videos (10 chars)
    # Col 3: Shorts (10 chars)
    # Col 4: Total (10 chars)
    # Col 5: Date Range (24 chars)
    
    border_top    = f"{c_bold}{c_header}┌" + "─" * 84 + f"┐{c_reset}"
    border_middle = f"{c_bold}{c_header}├" + "─" * 18 + "┬" + "─" * 12 + "┬" + "─" * 12 + "┬" + "─" * 12 + "┬" + "─" * 26 + f"┤{c_reset}"
    border_data   = f"{c_bold}{c_header}├" + "─" * 18 + "┼" + "─" * 12 + "┼" + "─" * 12 + "┼" + "─" * 12 + "┼" + "─" * 26 + f"┤{c_reset}"
    border_bottom = f"{c_bold}{c_header}└" + "─" * 18 + "┴" + "─" * 12 + "┴" + "─" * 12 + "┴" + "─" * 12 + "┴" + "─" * 26 + f"┘{c_reset}"

    print()
    print(border_top)
    title_str = f"YOUTUBE UPLOAD STATISTICS - {channel_title}"
    # Truncate if it's too long to fit centered in 82 characters
    if len(title_str) > 80:
        title_str = title_str[:77] + "..."
    centered_title = title_str.center(82)
    print(f"{c_bold}{c_header}│{c_reset} {c_bold}{c_yellow}{centered_title}{c_reset} {c_bold}{c_header}│{c_reset}")
    print(border_middle)
    
    # Header Row
    h_col1 = f"{'Period':<16}"
    h_col2 = f"{'Videos':>10}"
    h_col3 = f"{'Shorts':>10}"
    h_col4 = f"{'Total':>10}"
    h_col5 = f"{'Date Range (Local)':<24}"
    print(f"{c_bold}{c_header}│{c_reset} {c_bold}{c_blue}{h_col1}{c_reset} {c_bold}{c_header}│{c_reset} {c_bold}{c_blue}{h_col2}{c_reset} {c_bold}{c_header}│{c_reset} {c_bold}{c_blue}{h_col3}{c_reset} {c_bold}{c_header}│{c_reset} {c_bold}{c_blue}{h_col4}{c_reset} {c_bold}{c_header}│{c_reset} {c_bold}{c_blue}{h_col5}{c_reset} {c_bold}{c_header}│{c_reset}")
    print(border_data)

    # Data Rows function to prevent color formatting string length distortion
    def format_row(period: str, videos: int, shorts: int, total: int, date_range: str) -> str:
        p_part = f"{c_bold}{c_cyan}{period:<16}{c_reset}"
        v_part = f"{c_bold}{c_green}{videos:>10,}{c_reset}"
        s_part = f"{c_bold}{c_green}{shorts:>10,}{c_reset}"
        t_part = f"{c_bold}{c_green}{total:>10,}{c_reset}"
        d_part = f"{date_range:<24}"
        return f"{c_bold}{c_header}│{c_reset} {p_part} {c_bold}{c_header}│{c_reset} {v_part} {c_bold}{c_header}│{c_reset} {s_part} {c_bold}{c_header}│{c_reset} {t_part} {c_bold}{c_header}│{c_reset} {d_part} {c_bold}{c_header}│{c_reset}"

    # Print rows
    print(format_row("Today", videos_today, shorts_today, total_today, range_today))
    print(format_row("Last 7 Days", videos_7, shorts_7, total_7, range_7))
    print(format_row("Last 14 Days", videos_14, shorts_14, total_14, range_14))
    print(format_row("Last 30 Days", videos_30, shorts_30, total_30, range_30))
    
    print(border_bottom)
    print()


if __name__ == "__main__":
    main()
