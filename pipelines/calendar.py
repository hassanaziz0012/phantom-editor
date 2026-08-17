#!/usr/bin/env python3
"""
Content Calendar CLI
====================
Interactive CLI integrated with the Phantom Editor pipeline for managing the
content calendar on Google Sheets.

Usage:
    phantom pipeline calendar [list] [--platform <youtube|twitter|all>]
    phantom pipeline calendar next-date [--platform <youtube|twitter>] [--json]
    phantom pipeline calendar add --title <title> [--url <url>] [--desc <desc>] [--date <date>] [--platform <youtube|twitter>]
    phantom pipeline calendar remove <row_index | --title <title>>
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import List, Optional, Set

# Ensure Python's standard library calendar is loaded into sys.modules to prevent shadowing
import sys
_orig_sys_path = sys.path[:]
sys.path = [p for p in sys.path if p not in ("", ".", str(Path(__file__).resolve().parent))]
import calendar as _stdlib_calendar
import _strptime
sys.path = _orig_sys_path

# Setup path imports
pipeline_dir = Path(__file__).resolve().parent
repo_root = pipeline_dir.parent
video_editing_dir = repo_root / "video-editing"

if str(video_editing_dir) not in sys.path:
    sys.path.insert(0, str(video_editing_dir))

if str(pipeline_dir) not in sys.path:
    sys.path.insert(0, str(pipeline_dir))

# Import Google Sheet Utils
try:
    from google_sheet_utils import (
        CalendarRecord,
        add_record,
        get_sheets_service,
        get_spreadsheet_id,
        list_records,
        remove_record,
        update_record,
    )
except ImportError:
    from pipelines.google_sheet_utils import (
        CalendarRecord,
        add_record,
        get_sheets_service,
        get_spreadsheet_id,
        list_records,
        remove_record,
        update_record,
    )

# Terminal Colors
COLOR_GREEN = "\033[92m"
COLOR_RED = "\033[91m"
COLOR_YELLOW = "\033[93m"
COLOR_BLUE = "\033[94m"
COLOR_CYAN = "\033[96m"
COLOR_MAGENTA = "\033[95m"
COLOR_GRAY = "\033[90m"
COLOR_WHITE = "\033[97m"
COLOR_BOLD = "\033[1m"
COLOR_DIM = "\033[2m"
COLOR_RESET = "\033[0m"

# Platform badge styles
PLATFORM_STYLES = {
    "youtube": f"{COLOR_RED}{COLOR_BOLD}[YouTube]{COLOR_RESET}",
    "yt": f"{COLOR_RED}{COLOR_BOLD}[YouTube]{COLOR_RESET}",
    "twitter": f"{COLOR_CYAN}{COLOR_BOLD}[Twitter]{COLOR_RESET}",
    "x": f"{COLOR_CYAN}{COLOR_BOLD}[Twitter]{COLOR_RESET}",
}


def print_banner(title: str):
    print(f"\n{COLOR_BOLD}{COLOR_BLUE}{'=' * 65}{COLOR_RESET}")
    print(f"{COLOR_BOLD}{COLOR_WHITE}  {title}{COLOR_RESET}")
    print(f"{COLOR_BOLD}{COLOR_BLUE}{'=' * 65}{COLOR_RESET}\n")


def format_platform_badge(platform: str) -> str:
    plat_key = platform.strip().lower()
    return PLATFORM_STYLES.get(plat_key, f"{COLOR_MAGENTA}[{platform.title()}]{COLOR_RESET}")


def normalize_platform(platform_str: str) -> str:
    p = platform_str.strip().lower()
    if p in ("youtube", "yt"):
        return "YouTube"
    elif p in ("twitter", "x", "tweet"):
        return "Twitter"
    return platform_str.strip().title()


def parse_date_tolerant(date_str: str) -> Optional[datetime]:
    """Tolerantly parses a date string into a datetime object."""
    if not date_str or not str(date_str).strip():
        return None

    raw = str(date_str).strip()

    # Regex search for standard YYYY-MM-DD
    iso_match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw)
    time_match = re.search(r"(\d{1,2}):(\d{2})(?:\s*([apAP][mM]))?", raw)

    # Specific format parsing
    formats = [
        "%Y-%m-%d %I:%M %p",
        "%Y-%m-%d %I:%M%p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d/%m/%Y %I:%M %p",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
        "%b %d, %Y %I:%M %p",
        "%B %d, %Y %I:%M %p",
        "%b %d, %Y",
        "%B %d, %Y",
        "%Y/%m/%d %H:%M",
        "%Y/%m/%d",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass

    if iso_match:
        try:
            year, month, day = int(iso_match.group(1)), int(iso_match.group(2)), int(iso_match.group(3))
            hour, minute = 22, 30
            if time_match:
                h = int(time_match.group(1))
                m = int(time_match.group(2))
                meridiem = time_match.group(3)
                if meridiem:
                    if meridiem.lower() == "pm" and h < 12:
                        h += 12
                    elif meridiem.lower() == "am" and h == 12:
                        h = 0
                hour, minute = h, m
            return datetime(year, month, day, hour, minute)
        except Exception:
            pass

    return None


def parse_input_date(date_input: Optional[str], default_time: time = time(22, 30)) -> datetime:
    """Parses user input date flags (e.g. today, tomorrow, YYYY-MM-DD)."""
    now = datetime.now()
    if not date_input or not date_input.strip():
        return datetime.combine(now.date(), default_time)

    val = date_input.strip()
    val_lower = val.lower()

    if val_lower in ("today", "tod"):
        return datetime.combine(now.date(), default_time)
    elif val_lower in ("tomorrow", "tom"):
        return datetime.combine(now.date() + timedelta(days=1), default_time)

    dt = parse_date_tolerant(val)
    if dt:
        # If user only specified a date without specific time (00:00:00), attach default_time
        if dt.hour == 0 and dt.minute == 0 and "%H" not in val and "%I" not in val and ":" not in val:
            dt = datetime.combine(dt.date(), default_time)
        return dt

    raise ValueError(
        f"Unable to parse date: '{date_input}'.\n"
        "Supported formats: 'YYYY-MM-DD', 'YYYY-MM-DD 10:30 PM', 'today', 'tomorrow', etc."
    )


def format_publish_datetime(dt: datetime) -> str:
    """Formats datetime into clean standard sheet format: YYYY-MM-DD 10:30 PM."""
    return dt.strftime("%Y-%m-%d %I:%M %p")


def format_human_date(dt: datetime) -> str:
    """Returns human-friendly string e.g. 'Wednesday, Aug 19, 2026 at 10:30 PM (Tomorrow)'."""
    now = datetime.now()
    today = now.date()
    target_date = dt.date()

    date_part = dt.strftime("%A, %b %d, %Y at %I:%M %p")

    delta_days = (target_date - today).days
    if delta_days == 0:
        relative = "Today"
    elif delta_days == 1:
        relative = "Tomorrow"
    elif delta_days == -1:
        relative = "Yesterday"
    elif delta_days > 1:
        relative = f"In {delta_days} days"
    else:
        relative = f"{abs(delta_days)} days ago"

    return f"{date_part} ({relative})"


def calculate_next_available_youtube_date(records: List[CalendarRecord]) -> datetime:
    """
    Computes the next available publish date for YouTube:
    Rule: 1 YouTube video every day at 10:30 PM (22:30).
    Finds the earliest date (starting today or tomorrow if past 10:30 PM)
    that is not already booked in the Google Sheet.
    """
    now = datetime.now()
    target_time = time(22, 30)

    # Determine starting candidate date
    if now.time() >= target_time:
        candidate_date = now.date() + timedelta(days=1)
    else:
        candidate_date = now.date()

    # Collect all scheduled dates for YouTube
    booked_dates: Set[date] = set()
    for rec in records:
        if normalize_platform(rec.platform) == "YouTube" and rec.publish_date:
            dt = parse_date_tolerant(rec.publish_date)
            if dt:
                booked_dates.add(dt.date())

    # Find earliest unoccupied day
    while candidate_date in booked_dates:
        candidate_date += timedelta(days=1)

    return datetime.combine(candidate_date, target_time)


def calculate_next_available_twitter_date(records: List[CalendarRecord]) -> datetime:
    """
    Computes next available date for Twitter.
    Defaults to next earliest day slot at 12:00 PM or next unoccupied slot.
    """
    now = datetime.now()
    target_time = time(12, 0)

    if now.time() >= target_time:
        candidate_date = now.date() + timedelta(days=1)
    else:
        candidate_date = now.date()

    booked_dates: Set[date] = set()
    for rec in records:
        if normalize_platform(rec.platform) == "Twitter" and rec.publish_date:
            dt = parse_date_tolerant(rec.publish_date)
            if dt:
                booked_dates.add(dt.date())

    while candidate_date in booked_dates:
        candidate_date += timedelta(days=1)

    return datetime.combine(candidate_date, target_time)


# ---------------------------------------------------------------------------
# CLI Command Handlers
# ---------------------------------------------------------------------------

def handle_list(args):
    """List all scheduled content in a clean terminal table."""
    try:
        records = list_records(spreadsheet_id=args.sheet_id)
    except Exception as e:
        print(f"{COLOR_RED}✗ Failed to fetch content calendar: {e}{COLOR_RESET}")
        sys.exit(1)

    # Filter platform if requested
    if args.platform and args.platform.lower() != "all":
        req_plat = normalize_platform(args.platform)
        records = [r for r in records if normalize_platform(r.platform) == req_plat]

    print_banner("📅 Phantom Content Calendar")

    if not records:
        print(f"{COLOR_YELLOW}No scheduled content found in the calendar.{COLOR_RESET}\n")
        print(f"To add a new record, run:")
        print(f"  {COLOR_BOLD}phantom pipeline calendar add --title \"My Video Title\" --platform youtube{COLOR_RESET}\n")
        return

    # Table headers
    col_idx = "#"
    col_plat = "Platform"
    col_date = "Publish Date"
    col_title = "Title"
    col_url = "URL"
    col_desc = "Description"

    max_title_len = min(35, max([len(r.title) for r in records] + [len(col_title)]))
    max_url_len = min(30, max([len(r.url) for r in records if r.url] + [len(col_url)]))
    max_desc_len = 25

    print(
        f"{COLOR_BOLD}{col_idx:<4} {col_plat:<12} {col_date:<22} {col_title:<{max_title_len}} {col_url:<{max_url_len}} {col_desc}{COLOR_RESET}"
    )
    print(f"{COLOR_GRAY}{'-' * 4} {'-' * 12} {'-' * 22} {'-' * max_title_len} {'-' * max_url_len} {'-' * max_desc_len}{COLOR_RESET}")

    for rec in records:
        badge = format_platform_badge(rec.platform)
        # Format date if possible
        dt = parse_date_tolerant(rec.publish_date)
        date_str = rec.publish_date or f"{COLOR_GRAY}Unscheduled{COLOR_RESET}"
        if dt:
            now = datetime.now()
            if dt < now:
                date_display = f"{COLOR_GRAY}{rec.publish_date}{COLOR_RESET}"
            else:
                date_display = f"{COLOR_GREEN}{rec.publish_date}{COLOR_RESET}"
        else:
            date_display = date_str

        # Truncate title, url, description for clean tabular output
        disp_title = rec.title if len(rec.title) <= max_title_len else rec.title[:max_title_len - 3] + "..."
        
        raw_url = rec.url.strip() if rec.url else ""
        if raw_url:
            disp_url = raw_url if len(raw_url) <= max_url_len else raw_url[:max_url_len - 3] + "..."
            url_display = f"{COLOR_CYAN}{disp_url:<{max_url_len}}{COLOR_RESET}"
        else:
            url_display = f"{COLOR_GRAY}{'-':<{max_url_len}}{COLOR_RESET}"

        disp_desc = rec.description.replace("\n", " ") if rec.description else ""
        if len(disp_desc) > max_desc_len:
            disp_desc = disp_desc[:max_desc_len - 3] + "..."

        row_num_str = f"{COLOR_DIM}{rec.row_index}{COLOR_RESET}"
        # Adjust spacing for badge with ANSI codes
        plat_plain = f"[{normalize_platform(rec.platform)}]"
        plat_pad = " " * max(0, 12 - len(plat_plain))

        print(
            f"{row_num_str:<12} {badge}{plat_pad} {date_display:<31} {COLOR_WHITE}{disp_title:<{max_title_len}}{COLOR_RESET} {url_display} {COLOR_DIM}{disp_desc}{COLOR_RESET}"
        )

    # Summary counts
    yt_count = len([r for r in records if normalize_platform(r.platform) == "YouTube"])
    tw_count = len([r for r in records if normalize_platform(r.platform) == "Twitter"])
    print(f"\n{COLOR_GRAY}Total: {len(records)} entries ({yt_count} YouTube, {tw_count} Twitter){COLOR_RESET}\n")


def handle_next_date(args):
    """Compute and display the next available publish date."""
    platform = normalize_platform(args.platform or "youtube")

    try:
        records = list_records(spreadsheet_id=args.sheet_id)
    except Exception as e:
        print(f"{COLOR_RED}✗ Error connecting to Google Sheet: {e}{COLOR_RESET}")
        sys.exit(1)

    if platform == "YouTube":
        next_dt = calculate_next_available_youtube_date(records)
    elif platform == "Twitter":
        next_dt = calculate_next_available_twitter_date(records)
    else:
        next_dt = calculate_next_available_youtube_date(records)

    formatted_sheet = format_publish_datetime(next_dt)
    formatted_human = format_human_date(next_dt)

    if getattr(args, "json", False):
        out = {
            "platform": platform,
            "next_publish_date": formatted_sheet,
            "human_readable": formatted_human,
            "iso": next_dt.isoformat(),
        }
        print(json.dumps(out, indent=2))
        return

    print_banner(f"🎯 Next Available Date: {platform}")
    print(f"Platform:              {format_platform_badge(platform)}")
    print(f"Schedule Strategy:     {COLOR_BOLD}1 video per day at 10:30 PM (22:30){COLOR_RESET}")
    print(f"Next Available Date:   {COLOR_GREEN}{COLOR_BOLD}{formatted_human}{COLOR_RESET}")
    print(f"Sheet Format:          {COLOR_CYAN}{formatted_sheet}{COLOR_RESET}\n")


def handle_add(args):
    """Add a new record to the content calendar."""
    if not args.title:
        print(f"{COLOR_RED}✗ Error: --title is required to add a calendar record.{COLOR_RESET}")
        sys.exit(1)

    platform = normalize_platform(args.platform or "youtube")

    try:
        records = list_records(spreadsheet_id=args.sheet_id)
    except Exception as e:
        print(f"{COLOR_RED}✗ Error connecting to Google Sheet: {e}{COLOR_RESET}")
        sys.exit(1)

    # Determine publish date
    if args.date:
        dt = parse_input_date(args.date)
        publish_date_str = format_publish_datetime(dt)
    else:
        # Auto-compute next available slot
        if platform == "YouTube":
            dt = calculate_next_available_youtube_date(records)
        else:
            dt = calculate_next_available_twitter_date(records)
        publish_date_str = format_publish_datetime(dt)
        print(f"{COLOR_BLUE}ℹ Auto-assigned next available date slot: {COLOR_BOLD}{publish_date_str}{COLOR_RESET}")

    url = (getattr(args, "url", None) or "").strip()
    description = (getattr(args, "description", None) or getattr(args, "desc", None) or "").strip()

    try:
        rec = add_record(
            title=args.title.strip(),
            description=description,
            url=url,
            publish_date=publish_date_str,
            platform=platform,
            spreadsheet_id=args.sheet_id,
        )
        print(f"\n{COLOR_GREEN}{COLOR_BOLD}✓ Successfully added to Content Calendar!{COLOR_RESET}")
        print(f"  {COLOR_BOLD}Row #{COLOR_RESET}         {rec.row_index if rec.row_index > 0 else 'Appended'}")
        print(f"  {COLOR_BOLD}Platform:{COLOR_RESET}      {format_platform_badge(platform)}")
        print(f"  {COLOR_BOLD}Title:{COLOR_RESET}         {rec.title}")
        if rec.url:
            print(f"  {COLOR_BOLD}URL:{COLOR_RESET}           {COLOR_CYAN}{rec.url}{COLOR_RESET}")
        print(f"  {COLOR_BOLD}Publish Date:{COLOR_RESET}  {COLOR_GREEN}{rec.publish_date}{COLOR_RESET}")
        if rec.description:
            print(f"  {COLOR_BOLD}Description:{COLOR_RESET}   {rec.description}")
        print()
    except Exception as e:
        print(f"{COLOR_RED}✗ Failed to add record: {e}{COLOR_RESET}")
        sys.exit(1)


def handle_remove(args):
    """Remove a record by row number or title match."""
    row_idx = args.row
    title_match = args.title

    if not row_idx and not title_match:
        if args.identifier:
            if args.identifier.isdigit():
                row_idx = int(args.identifier)
            else:
                title_match = args.identifier
        else:
            print(f"{COLOR_RED}✗ Error: Must specify a row number or --title to remove.{COLOR_RESET}")
            sys.exit(1)

    try:
        remove_record(
            row_index=row_idx,
            title=title_match,
            spreadsheet_id=args.sheet_id,
        )
        target_str = f"row #{row_idx}" if row_idx else f"title '{title_match}'"
        print(f"\n{COLOR_GREEN}{COLOR_BOLD}✓ Successfully removed {target_str} from Content Calendar.{COLOR_RESET}\n")
    except Exception as e:
        print(f"{COLOR_RED}✗ Failed to remove record: {e}{COLOR_RESET}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Argument Parsing & Main
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phantom Content Calendar CLI: Manage your content schedule and next available publish dates.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--sheet-id",
        default=None,
        help="Google Sheets ID (defaults to CONTENT_CALENDAR_SHEET_ID in .env).",
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Calendar subcommands")

    # list subcommand
    list_parser = subparsers.add_parser("list", help="List all scheduled content from the Google Sheet")
    list_parser.add_argument(
        "--platform", "-p",
        choices=["all", "youtube", "twitter", "yt", "x"],
        default="all",
        help="Filter by platform (default: all)",
    )

    # next-date subcommand
    next_parser = subparsers.add_parser(
        "next-date",
        aliases=["next", "slot"],
        help="Calculate the next available publish date slot"
    )
    next_parser.add_argument(
        "--platform", "-p",
        default="youtube",
        choices=["youtube", "twitter", "yt", "x"],
        help="Platform to calculate next date for (default: youtube)",
    )
    next_parser.add_argument(
        "--json",
        action="store_true",
        help="Output result as JSON",
    )

    # add subcommand
    add_parser = subparsers.add_parser("add", help="Add a new entry to the content calendar")
    add_parser.add_argument(
        "--title", "-t",
        required=True,
        help="Content title",
    )
    add_parser.add_argument(
        "--url", "-u",
        default="",
        help="Content URL or link (e.g. video URL, draft link, doc link)",
    )
    add_parser.add_argument(
        "--platform", "-p",
        default="youtube",
        choices=["youtube", "twitter", "yt", "x"],
        help="Target platform (default: youtube)",
    )
    add_parser.add_argument(
        "--date", "-d",
        default=None,
        help="Publish date (e.g. '2026-08-20', 'tomorrow', '2026-08-20 10:30 PM'). If omitted, next available slot is auto-assigned.",
    )
    add_parser.add_argument(
        "--desc", "--description",
        dest="description",
        default="",
        help="Content description or notes",
    )

    # remove subcommand
    remove_parser = subparsers.add_parser("remove", aliases=["delete", "rm"], help="Remove an entry from the calendar")
    remove_parser.add_argument(
        "identifier",
        nargs="?",
        default=None,
        help="Row number (e.g. 3) or title substring to remove",
    )
    remove_parser.add_argument(
        "--row", "-r",
        type=int,
        default=None,
        help="Exact row number in the Google Sheet to remove",
    )
    remove_parser.add_argument(
        "--title", "-t",
        default=None,
        help="Title of the video/content to remove",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Default action if no subcommand is provided is to list records
    if not args.subcommand or args.subcommand == "list":
        if not hasattr(args, "platform"):
            args.platform = "all"
        handle_list(args)
    elif args.subcommand in ("next-date", "next", "slot"):
        handle_next_date(args)
    elif args.subcommand == "add":
        handle_add(args)
    elif args.subcommand in ("remove", "delete", "rm"):
        handle_remove(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
