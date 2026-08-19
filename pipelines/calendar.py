#!/usr/bin/env python3
"""
Content Calendar CLI
====================
Interactive CLI integrated with the Phantom Editor pipeline for managing the
content calendar on Google Sheets.

Usage:
    phantom pipeline calendar [list] [--platform <youtube|twitter|all>]
    phantom pipeline calendar next-date [--platform <youtube|twitter>] [--json]
    phantom pipeline calendar add [<project>] [--title <title>] [--url <url>] [--desc <desc>] [--date <date>] [--platform <youtube|twitter>]
    phantom pipeline calendar remove [<project>] [--project <project>] [--title <title>]
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

# Import Google Sheet Utils & YouTube Metadata
try:
    from google_sheet_utils import (
        CalendarRecord,
        get_sheets_service,
        get_spreadsheet_id,
        list_records,
    )
except ImportError:
    from pipelines.google_sheet_utils import (
        CalendarRecord,
        get_sheets_service,
        get_spreadsheet_id,
        list_records,
    )

try:
    from youtube_api.read_metadata import VideoMetadata, read_metadata
except ImportError:
    from read_metadata import VideoMetadata, read_metadata

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


def find_project_dir(identifier: Optional[str], projects_dir: Path) -> Optional[Path]:
    """Locates a project directory from an identifier, folder name, title, or cwd."""
    # If no identifier provided, check if current directory is a project folder
    if not identifier or not identifier.strip():
        cwd = Path.cwd()
        if (cwd / "metadata.json").exists() or (cwd / "final.mp4").exists() or (cwd / "to-review.mp4").exists():
            return cwd
        return None

    raw = identifier.strip()
    raw_path = Path(raw).expanduser().resolve()
    if raw_path.is_dir():
        return raw_path

    if not projects_dir.exists() or not projects_dir.is_dir():
        return None

    # 1. Exact match in projects_dir
    direct_match = projects_dir / raw
    if direct_match.is_dir():
        return direct_match

    # 2. Case-insensitive exact match
    raw_lower = raw.lower()
    for item in projects_dir.iterdir():
        if item.is_dir() and item.name.lower() == raw_lower:
            return item

    # 3. Substring match on folder name
    for item in projects_dir.iterdir():
        if item.is_dir() and raw_lower in item.name.lower():
            return item

    # 4. Match metadata.json title
    for item in projects_dir.iterdir():
        if item.is_dir():
            meta_path = item / "metadata.json"
            if meta_path.exists():
                try:
                    meta = read_metadata(meta_path)
                    if meta.title and (raw_lower == meta.title.lower() or raw_lower in meta.title.lower()):
                        return item
                except Exception:
                    pass

    return None


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
        "%m/%d/%Y %I:%M:%S %p",
        "%m/%d/%Y %I:%M %p",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y",
        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y %I:%M %p",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y",
        "%b %d, %Y %I:%M %p",
        "%B %d, %Y %I:%M %p",
        "%b %d, %Y",
        "%B %d, %Y",
        "%Y/%m/%d %H:%M:%S",
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


def get_local_booked_dates(projects_dir: Optional[Path] = None) -> Set[date]:
    """Scan local project folders and collect all scheduled dates from metadata.json."""
    booked: Set[date] = set()
    if projects_dir is None:
        projects_dir = Path(os.path.expanduser("~/Videos/YT Projects")).resolve()
    if not projects_dir.exists() or not projects_dir.is_dir():
        return booked
    for item in projects_dir.iterdir():
        if item.is_dir() and not item.name.startswith("."):
            meta_file = item / "metadata.json"
            if meta_file.exists():
                try:
                    meta = read_metadata(meta_file)
                    if meta.publish_date:
                        dt = parse_date_tolerant(meta.publish_date)
                        if dt:
                            booked.add(dt.date())
                except Exception:
                    pass
    return booked


def calculate_next_available_youtube_date(
    records: Optional[List[CalendarRecord]] = None,
    projects_dir: Optional[Path] = None
) -> datetime:
    """
    Computes the next available publish date for YouTube:
    Rule: 1 YouTube video every day at 10:30 PM (22:30).
    Uses local project folders as the primary source of truth for booked dates.
    """
    now = datetime.now()
    target_time = time(22, 30)

    # Determine starting candidate date
    if now.time() >= target_time:
        candidate_date = now.date() + timedelta(days=1)
    else:
        candidate_date = now.date()

    # Collect scheduled dates from local projects
    booked_dates: Set[date] = get_local_booked_dates(projects_dir)

    # Also include any YouTube dates from calendar records if provided
    if records:
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
    col_proj = "Project"
    col_plat = "Platform"
    col_date = "Publish Date"
    col_stat = "Status"
    col_title = "Title"
    col_url = "URL"

    max_proj_len = min(25, max([len(r.project) for r in records if r.project] + [len(col_proj)]))
    max_title_len = min(35, max([len(r.title) for r in records if r.title] + [len(col_title)]))
    max_url_len = min(30, max([len(r.url) for r in records if r.url] + [len(col_url)]))
    max_stat_len = min(18, max([len(r.status) for r in records if r.status] + [len(col_stat)]))

    print(
        f"{COLOR_BOLD}{col_idx:<4} {col_proj:<{max_proj_len}} {col_plat:<12} {col_date:<22} {col_stat:<{max_stat_len}} {col_title:<{max_title_len}} {col_url}{COLOR_RESET}"
    )
    print(
        f"{COLOR_GRAY}{'-' * 4} {'-' * max_proj_len} {'-' * 12} {'-' * 22} {'-' * max_stat_len} {'-' * max_title_len} {'-' * max_url_len}{COLOR_RESET}"
    )

    for rec in records:
        badge = format_platform_badge(rec.platform)
        
        # Clean date string & color (pad before coloring)
        raw_date_str = rec.publish_date or "Unscheduled"
        dt = parse_date_tolerant(rec.publish_date)
        date_pad = f"{raw_date_str:<22}"
        if dt:
            now = datetime.now()
            if dt < now:
                date_display = f"{COLOR_GRAY}{date_pad}{COLOR_RESET}"
            else:
                date_display = f"{COLOR_GREEN}{date_pad}{COLOR_RESET}"
        else:
            date_display = f"{COLOR_GRAY}{date_pad}{COLOR_RESET}"

        # Project name
        disp_proj = rec.project if len(rec.project) <= max_proj_len else rec.project[:max_proj_len - 3] + "..."
        if not disp_proj:
            disp_proj = "-"
        proj_display = f"{COLOR_BOLD}{disp_proj:<{max_proj_len}}{COLOR_RESET}"

        # Status badge color
        stat_color = COLOR_WHITE
        if rec.status.lower() in ("uploaded", "published", "complete"):
            stat_color = COLOR_GREEN
        elif rec.status.lower() in ("scheduled", "ready to schedule"):
            stat_color = COLOR_YELLOW
        elif rec.status.lower() in ("recorded", "processed", "reviewed"):
            stat_color = COLOR_CYAN
        stat_plain = rec.status or "-"
        disp_stat = f"{stat_color}{stat_plain:<{max_stat_len}}{COLOR_RESET}"

        # Truncate title and url
        disp_title = rec.title if len(rec.title) <= max_title_len else rec.title[:max_title_len - 3] + "..."
        if not disp_title:
            disp_title = "-"
        
        raw_url = rec.url.strip() if rec.url else ""
        if raw_url:
            disp_url = raw_url if len(raw_url) <= max_url_len else raw_url[:max_url_len - 3] + "..."
            url_display = f"{COLOR_CYAN}{disp_url:<{max_url_len}}{COLOR_RESET}"
        else:
            url_display = f"{COLOR_GRAY}{'-':<{max_url_len}}{COLOR_RESET}"

        row_num_display = f"{COLOR_DIM}{rec.row_index:<4}{COLOR_RESET}"
        # Adjust spacing for badge with ANSI codes
        plat_plain = f"[{normalize_platform(rec.platform)}]"
        plat_pad = " " * max(0, 12 - len(plat_plain))

        print(
            f"{row_num_display} {proj_display} {badge}{plat_pad} {date_display} {disp_stat} {COLOR_WHITE}{disp_title:<{max_title_len}}{COLOR_RESET} {url_display}"
        )

    # Summary counts
    yt_count = len([r for r in records if normalize_platform(r.platform) == "YouTube"])
    tw_count = len([r for r in records if normalize_platform(r.platform) == "Twitter"])
    print(f"\n{COLOR_GRAY}Total: {len(records)} entries ({yt_count} YouTube, {tw_count} Twitter){COLOR_RESET}\n")


def handle_next_date(args):
    """Compute and display the next available publish date."""
    platform = normalize_platform(args.platform or "youtube")
    projects_dir = Path(getattr(args, "dir", "~/Videos/YT Projects")).expanduser().resolve()

    try:
        records = list_records(spreadsheet_id=args.sheet_id)
    except Exception:
        records = []

    if platform == "YouTube":
        next_dt = calculate_next_available_youtube_date(records, projects_dir=projects_dir)
    elif platform == "Twitter":
        next_dt = calculate_next_available_twitter_date(records)
    else:
        next_dt = calculate_next_available_youtube_date(records, projects_dir=projects_dir)

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
    """
    Schedule a video project by setting its `publishDate` in metadata.json.
    Does not write directly to Google Sheets; current_pipeline.py handles the sync.
    """
    project_input = (getattr(args, "project", None) or getattr(args, "target", None) or getattr(args, "title", None) or "").strip()
    projects_dir = Path(getattr(args, "dir", "~/Videos/YT Projects")).expanduser().resolve()

    target_dir = find_project_dir(project_input, projects_dir)
    if not target_dir:
        available_dirs = [d.name for d in sorted(projects_dir.iterdir()) if d.is_dir() and not d.name.startswith(".")] if projects_dir.exists() else []
        avail_str = "\n".join(f"  - {name}" for name in available_dirs[:10]) if available_dirs else "  (None found)"
        print(f"{COLOR_RED}✗ Error: Could not locate project directory for '{project_input or '.'}' in {projects_dir}.{COLOR_RESET}")
        print(f"\nAvailable projects in {projects_dir}:\n{avail_str}\n")
        sys.exit(1)

    platform = normalize_platform(args.platform or "youtube")

    try:
        records = list_records(spreadsheet_id=args.sheet_id)
    except Exception as e:
        records = []
        print(f"{COLOR_YELLOW}⚠️ Note: Could not fetch Google Sheet records ({e}). Using local fallback date.{COLOR_RESET}")

    # Determine publish date
    if args.date:
        dt = parse_input_date(args.date)
        publish_date_str = format_publish_datetime(dt)
    else:
        # Auto-compute next available slot
        if platform == "YouTube":
            dt = calculate_next_available_youtube_date(records, projects_dir=projects_dir)
        else:
            dt = calculate_next_available_twitter_date(records)
        publish_date_str = format_publish_datetime(dt)
        print(f"{COLOR_BLUE}ℹ Auto-assigned next available date slot: {COLOR_BOLD}{publish_date_str}{COLOR_RESET}")

    formatted_human = format_human_date(parse_date_tolerant(publish_date_str) or datetime.now())

    metadata_path = target_dir / "metadata.json"
    try:
        if metadata_path.exists():
            meta = read_metadata(metadata_path)
            meta.publish_date = publish_date_str
            if getattr(args, "title", None) and args.title.strip():
                meta.title = args.title.strip()
            if getattr(args, "url", None) and args.url.strip():
                meta.url = args.url.strip()
            meta.save()
        else:
            meta = VideoMetadata(
                title=getattr(args, "title", None) or target_dir.name,
                publish_date=publish_date_str,
                url=getattr(args, "url", None) or None,
            )
            meta.save(metadata_path)

        print(f"\n{COLOR_GREEN}{COLOR_BOLD}✓ Successfully scheduled project release date!{COLOR_RESET}")
        print(f"  {COLOR_BOLD}Project:{COLOR_RESET}       {target_dir.name}")
        print(f"  {COLOR_BOLD}Platform:{COLOR_RESET}      {format_platform_badge(platform)}")
        print(f"  {COLOR_BOLD}Publish Date:{COLOR_RESET}  {COLOR_GREEN}{publish_date_str}{COLOR_RESET} ({formatted_human})")
        if meta.title:
            print(f"  {COLOR_BOLD}Title:{COLOR_RESET}         {meta.title}")
        if meta.url:
            print(f"  {COLOR_BOLD}URL:{COLOR_RESET}           {COLOR_CYAN}{meta.url}{COLOR_RESET}")
        print(f"  {COLOR_BOLD}Metadata File:{COLOR_RESET} {metadata_path}")
        print(f"\n{COLOR_GRAY}Run '{COLOR_BOLD}phantom pipeline status{COLOR_RESET}{COLOR_GRAY}' to view the pipeline and sync this schedule to Google Sheets.{COLOR_RESET}\n")
    except Exception as e:
        print(f"{COLOR_RED}✗ Failed to save metadata: {e}{COLOR_RESET}")
        sys.exit(1)


def handle_remove(args):
    """
    Unschedule a video project by removing its `publishDate` from metadata.json.
    Does not write directly to Google Sheets; current_pipeline.py handles the sync.
    """
    project_input = (getattr(args, "project", None) or getattr(args, "target", None) or getattr(args, "identifier", None) or getattr(args, "title", None) or "").strip()
    row_idx = getattr(args, "row", None)
    projects_dir = Path(getattr(args, "dir", "~/Videos/YT Projects")).expanduser().resolve()

    # If row index was provided or identifier is purely numeric, look up corresponding project in Google Sheet
    if not project_input and row_idx:
        try:
            records = list_records(spreadsheet_id=args.sheet_id)
            match = next((r for r in records if r.row_index == row_idx), None)
            if match:
                project_input = match.project or match.title
            else:
                print(f"{COLOR_RED}✗ Error: No calendar record found at row #{row_idx} in Google Sheet.{COLOR_RESET}")
                sys.exit(1)
        except Exception as e:
            print(f"{COLOR_RED}✗ Failed to query Google Sheet for row #{row_idx}: {e}{COLOR_RESET}")
            sys.exit(1)
    elif project_input and project_input.isdigit():
        try:
            records = list_records(spreadsheet_id=args.sheet_id)
            match = next((r for r in records if r.row_index == int(project_input)), None)
            if match and (match.project or match.title):
                project_input = match.project or match.title
        except Exception:
            pass

    target_dir = find_project_dir(project_input, projects_dir)
    if not target_dir:
        available_dirs = [d.name for d in sorted(projects_dir.iterdir()) if d.is_dir() and not d.name.startswith(".")] if projects_dir.exists() else []
        avail_str = "\n".join(f"  - {name}" for name in available_dirs[:10]) if available_dirs else "  (None found)"
        print(f"{COLOR_RED}✗ Error: Could not locate project directory for '{project_input or '.'}' in {projects_dir}.{COLOR_RESET}")
        print(f"\nAvailable projects in {projects_dir}:\n{avail_str}\n")
        sys.exit(1)

    metadata_path = target_dir / "metadata.json"
    if not metadata_path.exists():
        print(f"{COLOR_RED}✗ Error: No metadata.json found in project directory '{target_dir.name}' ({metadata_path}).{COLOR_RESET}")
        sys.exit(1)

    try:
        meta = read_metadata(metadata_path)
        prev_date = meta.publish_date
        if not prev_date:
            print(f"\n{COLOR_YELLOW}ℹ Project '{target_dir.name}' does not have a scheduled publishDate in metadata.json.{COLOR_RESET}\n")
            return

        meta.publish_date = None
        if hasattr(meta, "raw_data") and isinstance(meta.raw_data, dict):
            meta.raw_data.pop("publishDate", None)
            meta.raw_data.pop("publish_date", None)
        meta.save()

        print(f"\n{COLOR_GREEN}{COLOR_BOLD}✓ Successfully removed publishDate from project metadata!{COLOR_RESET}")
        print(f"  {COLOR_BOLD}Project:{COLOR_RESET}        {target_dir.name}")
        print(f"  {COLOR_BOLD}Removed Date:{COLOR_RESET}   {COLOR_GRAY}{prev_date}{COLOR_RESET}")
        print(f"  {COLOR_BOLD}Metadata File:{COLOR_RESET}  {metadata_path}")
        print(f"\n{COLOR_GRAY}Run '{COLOR_BOLD}phantom pipeline status{COLOR_RESET}{COLOR_GRAY}' to view the pipeline and sync this update with Google Sheets.{COLOR_RESET}\n")
    except Exception as e:
        print(f"{COLOR_RED}✗ Failed to update metadata: {e}{COLOR_RESET}")
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
    next_parser.add_argument(
        "--dir", "-d",
        default=os.path.expanduser("~/Videos/YT Projects"),
        help="Base directory for YouTube projects",
    )

    # add subcommand
    add_parser = subparsers.add_parser("add", help="Schedule a video project by setting its publishDate in metadata.json")
    add_parser.add_argument(
        "target",
        nargs="?",
        default="",
        help="Project folder name or directory path",
    )
    add_parser.add_argument(
        "--project", "-P",
        dest="project",
        default="",
        help="Project folder name in YT Projects",
    )
    add_parser.add_argument(
        "--title", "-t",
        default="",
        help="Content title (optional override)",
    )
    add_parser.add_argument(
        "--dir", "-d",
        default=os.path.expanduser("~/Videos/YT Projects"),
        help="Base directory for YouTube projects",
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
        "--date",
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
    remove_parser = subparsers.add_parser(
        "remove",
        aliases=["delete", "rm", "unschedule"],
        help="Unschedule a video project by removing publishDate from metadata.json"
    )
    remove_parser.add_argument(
        "target",
        nargs="?",
        default="",
        help="Project folder name, directory path, or title substring to remove schedule for",
    )
    remove_parser.add_argument(
        "--project", "-P",
        dest="project",
        default="",
        help="Project folder name in YT Projects",
    )
    remove_parser.add_argument(
        "--title", "-t",
        default="",
        help="Title of the video/content to remove schedule for",
    )
    remove_parser.add_argument(
        "--dir", "-d",
        default=os.path.expanduser("~/Videos/YT Projects"),
        help="Base directory for YouTube projects",
    )
    remove_parser.add_argument(
        "--row", "-r",
        type=int,
        default=None,
        help="Row number in the Google Sheet to unschedule",
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
    elif args.subcommand in ("remove", "delete", "rm", "unschedule"):
        handle_remove(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
