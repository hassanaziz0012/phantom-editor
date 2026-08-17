#!/usr/bin/env python3
"""
Current Content Pipeline Summary Tool.
Scans ~/Videos/YT Projects (or a custom directory) and displays a summary of
video projects, their current stage in the content pipeline, and the next step required.

Stages:
  1. New               - New project folder created, needs video script (.excalidraw).
  2. Scripted          - Script created (.excalidraw), needs raw video recordings.
  3. Recorded          - Raw footage present, needs Phantom processing (to-review.mp4).
  4. Processed         - Phantom processing complete (to-review.mp4), needs manual review (final.mp4).
  5. Reviewed          - Manual review complete (final.mp4), needs metadata & thumbnail.
  6. Added Metadata    - metadata.json created, needs thumbnail.
  7. Added Thumbnail   - Thumbnail(s) created, needs metadata.json.
  8. Ready to Schedule - final.mp4 + metadata.json + thumbnail(s) complete, needs calendar scheduling.
  9. Scheduled         - Video scheduled in content calendar, needs YouTube upload.
 10. Uploaded          - Video uploaded to YouTube (metadata.json has valid YT URL). Complete!
"""

import os
import sys
import json
import re
import argparse
from pathlib import Path

# Ensure Python's standard library calendar is loaded into sys.modules to prevent shadowing
pipeline_dir = Path(__file__).resolve().parent
repo_root = pipeline_dir.parent
video_editing_dir = repo_root / "video-editing"

_orig_sys_path = sys.path[:]
sys.path = [p for p in sys.path if p not in ("", ".", str(pipeline_dir))]
import calendar as _stdlib_calendar
import _strptime
sys.path = _orig_sys_path

if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

if str(video_editing_dir) not in sys.path:
    sys.path.append(str(video_editing_dir))

from dataclasses import dataclass, field
from typing import List, Optional, Any


from youtube_api.read_metadata import read_metadata

try:
    from pipelines.pipeline_status import is_valid_yt_url
except ImportError:
    from pipeline_status import is_valid_yt_url

try:
    from pipelines.google_sheet_utils import list_records
except ImportError:
    try:
        from google_sheet_utils import list_records
    except ImportError:
        list_records = None

try:
    from utils import (
        COLOR_GREEN, COLOR_RED, COLOR_YELLOW, COLOR_BLUE, COLOR_RESET, COLOR_BOLD
    )
except ImportError:
    COLOR_GREEN = "\033[92m"
    COLOR_RED = "\033[91m"
    COLOR_YELLOW = "\033[93m"
    COLOR_BLUE = "\033[94m"
    COLOR_RESET = "\033[0m"
    COLOR_BOLD = "\033[1m"

COLOR_CYAN = "\033[96m"
COLOR_MAGENTA = "\033[95m"
COLOR_GRAY = "\033[90m"
COLOR_WHITE = "\033[97m"

VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
GENERATED_PREFIXES = ("to-review", "final", "after-", "temp", "preview", "processed-audio")


@dataclass
class VideoProject:
    name: str
    path: Path
    script_files: List[Path] = field(default_factory=list)
    raw_files: List[Path] = field(default_factory=list)
    to_review_file: Optional[Path] = None
    final_file: Optional[Path] = None
    metadata_file: Optional[Path] = None
    thumbnail_files: List[Path] = field(default_factory=list)
    yt_url: Optional[str] = None
    title: Optional[str] = None
    scheduled_date: Optional[str] = None
    stage_num: int = 1
    stage_name: str = "New"
    next_step: str = ""
    status_color: str = COLOR_RESET

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "stage_num": self.stage_num,
            "stage_name": self.stage_name,
            "next_step": self.next_step,
            "has_script": len(self.script_files) > 0,
            "script_files": [f.name for f in self.script_files],
            "raw_files": [f.name for f in self.raw_files],
            "to_review_file": self.to_review_file.name if self.to_review_file else None,
            "final_file": self.final_file.name if self.final_file else None,
            "has_metadata": self.metadata_file is not None,
            "title": self.title,
            "thumbnails": [f.name for f in self.thumbnail_files],
            "yt_url": self.yt_url,
            "scheduled_date": self.scheduled_date,
        }


def is_script_file(file_path: Path) -> bool:
    """Check if a file is an Excalidraw script / presentation file."""
    if file_path.suffix.lower() == ".excalidraw":
        return True
    name_lower = file_path.name.lower()
    if name_lower.endswith(".excalidraw") or name_lower.endswith(".excalidraw.json") or name_lower.endswith(".excalidraw.svg"):
        return True
    return False


def is_raw_video_file(file_path: Path) -> bool:
    """Check if a file is a raw recording video rather than an edited/generated file."""
    if file_path.suffix.lower() not in VIDEO_EXTENSIONS:
        return False
    name_lower = file_path.stem.lower()
    for prefix in GENERATED_PREFIXES:
        if name_lower.startswith(prefix):
            return False
    return True


def is_thumbnail_file(file_path: Path) -> bool:
    """Check if a file is a designed thumbnail image."""
    if file_path.suffix.lower() not in IMAGE_EXTENSIONS:
        return False
    name_lower = file_path.stem.lower()
    return name_lower.startswith("thumbnail") or name_lower.startswith("thumb")


def get_calendar_records() -> list:
    """Fetch calendar records safely from Google Sheets."""
    if list_records is None:
        return []
    try:
        return list_records()
    except Exception:
        return []


def find_calendar_record_match(
    project_title: Optional[str],
    yt_url: Optional[str],
    project_name: str,
    records: list
) -> Optional[Any]:
    """Find a matching calendar record for a video project by URL, title, or project name."""
    if not records:
        return None

    def extract_id(url_val: Optional[str]) -> Optional[str]:
        if not url_val:
            return None
        m = re.search(r"(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_-]{11})", url_val)
        return m.group(1) if m else None

    yt_id = extract_id(yt_url)

    # 1. Match by YouTube Video ID
    if yt_id:
        for r in records:
            r_url = getattr(r, "url", "") or ""
            if yt_id in r_url:
                return r

    # 2. Match by exact metadata title
    if project_title:
        clean_title = project_title.strip().lower()
        for r in records:
            r_title = getattr(r, "title", "") or ""
            if r_title.strip().lower() == clean_title:
                return r

    # 3. Match by project folder name
    clean_name = project_name.strip().lower()
    for r in records:
        r_title = getattr(r, "title", "") or ""
        if r_title.strip().lower() == clean_name:
            return r

    return None


def analyze_project(project_dir: Path, calendar_records: Optional[list] = None) -> VideoProject:
    """Scan a project directory and evaluate its pipeline stage."""
    project = VideoProject(name=project_dir.name, path=project_dir)

    if not project_dir.is_dir():
        return project

    for item in project_dir.iterdir():
        if item.is_dir():
            continue

        stem_lower = item.stem.lower()
        ext_lower = item.suffix.lower()

        # Check output / milestone files
        if stem_lower == "to-review" and ext_lower in VIDEO_EXTENSIONS:
            project.to_review_file = item
        elif stem_lower == "final" and ext_lower in VIDEO_EXTENSIONS:
            project.final_file = item
        elif item.name.lower() == "metadata.json":
            project.metadata_file = item
            try:
                meta = read_metadata(item)
                if meta.title:
                    project.title = meta.title
                if meta.url and is_valid_yt_url(meta.url):
                    project.yt_url = meta.url
            except Exception:
                pass
        elif is_script_file(item):
            project.script_files.append(item)
        elif is_thumbnail_file(item):
            project.thumbnail_files.append(item)
        elif is_raw_video_file(item):
            project.raw_files.append(item)

    # Check for calendar scheduling match
    matched_record = None
    if calendar_records is not None:
        matched_record = find_calendar_record_match(project.title, project.yt_url, project.name, calendar_records)
    elif project.yt_url or project.title:
        matched_record = find_calendar_record_match(project.title, project.yt_url, project.name, get_calendar_records())

    if matched_record:
        project.scheduled_date = getattr(matched_record, "publish_date", None) or "Scheduled"

    # Determine stage
    # Stage 10: Uploaded (metadata.json contains a valid YouTube URL)
    if project.yt_url:
        project.stage_num = 10
        project.stage_name = "Uploaded"
        date_info = f" (Scheduled: {project.scheduled_date})" if project.scheduled_date and project.scheduled_date != "Scheduled" else ""
        project.next_step = f"Video uploaded to YouTube{date_info}! All pipeline stages complete! 🚀"
        project.status_color = COLOR_GREEN

    # Stage 9: Scheduled (in content calendar, awaiting YouTube upload)
    elif matched_record:
        project.stage_num = 9
        project.stage_name = "Scheduled"
        date_info = f" ({project.scheduled_date})" if project.scheduled_date and project.scheduled_date != "Scheduled" else ""
        project.next_step = f"Upload video to YouTube{date_info}! 🚀 (`phantom yt upload` or add URL to metadata.json)"
        project.status_color = COLOR_YELLOW

    # Stage 8: Ready to Schedule (final.mp4 + metadata.json + thumbnail(s))
    elif project.final_file and project.metadata_file and project.thumbnail_files:
        project.stage_num = 8
        project.stage_name = "Ready to Schedule"
        project.next_step = "Schedule video release in content calendar (`phantom pipeline calendar add`)"
        project.status_color = COLOR_CYAN

    # Stage 7: Added Thumbnail (final.mp4 + thumbnail, missing metadata)
    elif project.final_file and project.thumbnail_files:
        project.stage_num = 7
        project.stage_name = "Added Thumbnail"
        project.next_step = "Create metadata.json file (title, description, tags)"
        project.status_color = COLOR_CYAN

    # Stage 6: Added Metadata (final.mp4 + metadata.json, missing thumbnail)
    elif project.final_file and project.metadata_file:
        project.stage_num = 6
        project.stage_name = "Added Metadata"
        project.next_step = "Design and add thumbnail (e.g. thumbnail.png)"
        project.status_color = COLOR_CYAN

    # Stage 5: Reviewed (final.mp4 exists, missing both metadata and thumbnail)
    elif project.final_file:
        project.stage_num = 5
        project.stage_name = "Reviewed"
        project.next_step = "Create metadata.json AND design thumbnail (e.g. thumbnail.png)"
        project.status_color = COLOR_BLUE

    # Stage 4: Processed (to-review.mp4 exists, final.mp4 does not)
    elif project.to_review_file:
        project.stage_num = 4
        project.stage_name = "Processed"
        project.next_step = "Manually review to-review.mp4 and create final.mp4"
        project.status_color = COLOR_YELLOW

    # Stage 3: Recorded (raw video files present, no to-review.mp4)
    elif len(project.raw_files) > 0:
        project.stage_num = 3
        project.stage_name = "Recorded"
        project.next_step = "Process video using Phantom (`phantom process` or `process_video.py`)"
        project.status_color = COLOR_MAGENTA

    # Stage 2: Scripted (.excalidraw file present, no raw video files)
    elif len(project.script_files) > 0:
        project.stage_num = 2
        project.stage_name = "Scripted"
        project.next_step = "Record raw footage (webcam & screencast files)"
        project.status_color = COLOR_BLUE

    # Stage 1: New (No .excalidraw script or raw video files present)
    else:
        project.stage_num = 1
        project.stage_name = "New"
        project.next_step = "Script the video (create .excalidraw presentation with notes & diagrams)"
        project.status_color = COLOR_GRAY

    return project


def get_all_projects(projects_dir: Path) -> List[VideoProject]:
    """Find and analyze all project subdirectories in the projects directory."""
    if not projects_dir.exists() or not projects_dir.is_dir():
        return []

    calendar_records = get_calendar_records()

    projects = []
    for item in sorted(projects_dir.iterdir()):
        if item.is_dir() and not item.name.startswith("."):
            projects.append(analyze_project(item, calendar_records=calendar_records))
    
    return projects


def render_stage_bar(proj: VideoProject) -> str:
    """Render a visual step pipeline indicator checking actual completion of each step."""
    steps = [
        ("1", "New"),
        ("2", "Script"),
        ("3", "Rec"),
        ("4", "Proc"),
        ("5", "Rev"),
        ("6", "Meta"),
        ("7", "Thumb"),
        ("8", "Ready"),
        ("9", "Sched"),
        ("10", "Upload"),
    ]
    parts = []
    for num_str, name in steps:
        idx = int(num_str)
        if idx == proj.stage_num:
            parts.append(f"{COLOR_BOLD}{COLOR_GREEN}[{num_str}:{name}]{COLOR_RESET}")
        else:
            is_complete = False
            if idx == 1:
                is_complete = (
                    proj.stage_num > 1
                    or len(proj.script_files) > 0
                    or len(proj.raw_files) > 0
                    or proj.to_review_file is not None
                    or proj.final_file is not None
                )
            elif idx == 2:
                is_complete = len(proj.script_files) > 0 or proj.stage_num > 2
            elif idx == 3:
                is_complete = (
                    proj.stage_num > 3
                    or len(proj.raw_files) > 0
                    or proj.to_review_file is not None
                    or proj.final_file is not None
                )
            elif idx == 4:
                is_complete = proj.stage_num > 4 or proj.to_review_file is not None or proj.final_file is not None
            elif idx == 5:
                is_complete = proj.final_file is not None and proj.stage_num > 5
            elif idx == 6:
                is_complete = proj.metadata_file is not None
            elif idx == 7:
                is_complete = bool(proj.thumbnail_files)
            elif idx == 8:
                is_complete = proj.stage_num > 8 or (
                    proj.final_file is not None
                    and proj.metadata_file is not None
                    and bool(proj.thumbnail_files)
                )
            elif idx == 9:
                is_complete = proj.stage_num > 9 or bool(proj.yt_url) or bool(proj.scheduled_date)
            elif idx == 10:
                is_complete = proj.stage_num == 10 or bool(proj.yt_url)

            if is_complete:
                parts.append(f"{COLOR_GREEN}✓{num_str}{COLOR_RESET}")
            else:
                parts.append(f"{COLOR_GRAY}{num_str}:{name}{COLOR_RESET}")
    return " ➔ ".join(parts)


def print_terminal_summary(projects: List[VideoProject], projects_dir: Path, verbose: bool = False) -> None:
    """Print a clean, visually rich terminal summary of all video projects."""
    width = 78
    print(f"\n{COLOR_BOLD}{COLOR_CYAN}{'=' * width}{COLOR_RESET}")
    print(f"{COLOR_BOLD}{COLOR_WHITE} 🎬  YOUTUBE CONTENT PIPELINE SUMMARY{COLOR_RESET}")
    print(f"{COLOR_GRAY} Directory: {projects_dir}{COLOR_RESET}")
    print(f"{COLOR_BOLD}{COLOR_CYAN}{'=' * width}{COLOR_RESET}\n")

    if not projects:
        print(f"{COLOR_YELLOW}No project folders found in {projects_dir}{COLOR_RESET}\n")
        return

    stage_counts = {i: 0 for i in range(1, 11)}

    for idx, proj in enumerate(projects, 1):
        stage_counts[proj.stage_num] += 1
        color = proj.status_color
        
        print(f"{COLOR_BOLD}{idx}. {proj.name}{COLOR_RESET}")
        print(f"   Stage {proj.stage_num}/10: {color}{COLOR_BOLD}[{proj.stage_name}]{COLOR_RESET}")
        print(f"   Pipeline: {render_stage_bar(proj)}")
        print(f"   {COLOR_BOLD}➜ Next Step:{COLOR_RESET} {proj.next_step}")

        # Checklists for files
        if proj.script_files:
            script_names = ", ".join([f.name for f in proj.script_files])
            script_status = f"{COLOR_GREEN}✓ script ({script_names}){COLOR_RESET}"
        else:
            script_status = f"{COLOR_GRAY}✗ script (.excalidraw){COLOR_RESET}"

        raw_info = f"{len(proj.raw_files)} raw file(s)" if proj.raw_files else "No raw files"
        to_rev_status = f"{COLOR_GREEN}✓ {proj.to_review_file.name}{COLOR_RESET}" if proj.to_review_file else f"{COLOR_GRAY}✗ to-review.mp4{COLOR_RESET}"
        final_status = f"{COLOR_GREEN}✓ {proj.final_file.name}{COLOR_RESET}" if proj.final_file else f"{COLOR_GRAY}✗ final.mp4{COLOR_RESET}"
        
        if proj.metadata_file:
            if proj.yt_url:
                meta_status = f"{COLOR_GREEN}✓ metadata.json (URL set){COLOR_RESET}"
            elif proj.scheduled_date:
                meta_status = f"{COLOR_GREEN}✓ metadata.json (Scheduled: {proj.scheduled_date}){COLOR_RESET}"
            else:
                meta_status = f"{COLOR_GREEN}✓ metadata.json{COLOR_RESET}"
        else:
            meta_status = f"{COLOR_GRAY}✗ metadata.json{COLOR_RESET}"
        
        if proj.thumbnail_files:
            thumb_names = ", ".join([f.name for f in proj.thumbnail_files])
            thumb_status = f"{COLOR_GREEN}✓ thumbnail ({thumb_names}){COLOR_RESET}"
        else:
            thumb_status = f"{COLOR_GRAY}✗ thumbnail{COLOR_RESET}"

        print(f"   Files: [{script_status}] | [{raw_info}] | [{to_rev_status}] | [{final_status}] | [{meta_status}] | [{thumb_status}]")

        if verbose and (proj.script_files or proj.raw_files or proj.thumbnail_files or proj.title):
            if proj.title:
                print(f"   {COLOR_GRAY}└── Video title: {proj.title}{COLOR_RESET}")
            if proj.script_files:
                script_names = ", ".join([f.name for f in proj.script_files])
                print(f"   {COLOR_GRAY}└── Script / Presentation: {script_names}{COLOR_RESET}")
            if proj.raw_files:
                raw_names = ", ".join([f.name for f in proj.raw_files])
                print(f"   {COLOR_GRAY}└── Raw footage: {raw_names}{COLOR_RESET}")

        print(f"{COLOR_GRAY}{'-' * width}{COLOR_RESET}")

    # Summary Statistics
    print(f"\n{COLOR_BOLD}{COLOR_WHITE}📊 PIPELINE OVERVIEW & STATS{COLOR_RESET}")
    print(f" Total Projects: {COLOR_BOLD}{len(projects)}{COLOR_RESET}")
    
    stat_line = (
        f" {COLOR_GRAY}New: {stage_counts[1]}{COLOR_RESET} | "
        f"{COLOR_BLUE}Scripted: {stage_counts[2]}{COLOR_RESET} | "
        f"{COLOR_MAGENTA}Recorded: {stage_counts[3]}{COLOR_RESET} | "
        f"{COLOR_YELLOW}Processed: {stage_counts[4]}{COLOR_RESET} | "
        f"{COLOR_BLUE}Reviewed: {stage_counts[5]}{COLOR_RESET} | "
        f"{COLOR_CYAN}Metadata: {stage_counts[6]}{COLOR_RESET} | "
        f"{COLOR_CYAN}Thumbnail: {stage_counts[7]}{COLOR_RESET} | "
        f"{COLOR_CYAN}Ready: {stage_counts[8]}{COLOR_RESET} | "
        f"{COLOR_YELLOW}Scheduled: {stage_counts[9]}{COLOR_RESET} | "
        f"{COLOR_GREEN}Uploaded: {stage_counts[10]}{COLOR_RESET}"
    )
    print(stat_line)

    action_needed = (
        stage_counts[2] + stage_counts[3] + stage_counts[4] +
        stage_counts[5] + stage_counts[6] + stage_counts[7] +
        stage_counts[8] + stage_counts[9]
    )
    ready_to_sched = stage_counts[8]
    scheduled_cnt = stage_counts[9]
    uploaded_cnt = stage_counts[10]
    
    if action_needed > 0:
        print(f"\n {COLOR_YELLOW}⚡ Action Required: {action_needed} video(s) in progress / awaiting next steps{COLOR_RESET}")
    if ready_to_sched > 0:
        print(f" {COLOR_CYAN}📋 Ready to Schedule: {ready_to_sched} video(s) fully reviewed with metadata & thumbnail!{COLOR_RESET}")
    if scheduled_cnt > 0:
        print(f" {COLOR_YELLOW}📅 Scheduled (Needs Upload): {scheduled_cnt} video(s) on calendar awaiting YouTube upload{COLOR_RESET}")
    if uploaded_cnt > 0:
        print(f" {COLOR_GREEN}🎉 Uploaded: {uploaded_cnt} video(s) published on YouTube!{COLOR_RESET}")

    print(f"\n{COLOR_BOLD}{COLOR_CYAN}{'=' * width}{COLOR_RESET}\n")


def main():
    parser = argparse.ArgumentParser(
        description="Display summary of current YouTube video projects and pipeline stage."
    )
    parser.add_argument(
        "--dir", "-d",
        default=os.path.expanduser("~/Videos/YT Projects"),
        help="Path to YouTube projects directory (default: ~/Videos/YT Projects)."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output summary in JSON format."
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed file list for each project."
    )
    args = parser.parse_args()

    projects_dir = Path(args.dir).expanduser().resolve()
    projects = get_all_projects(projects_dir)

    if args.json:
        data = {
            "projects_dir": str(projects_dir),
            "total_projects": len(projects),
            "projects": [p.to_dict() for p in projects]
        }
        print(json.dumps(data, indent=2))
    else:
        print_terminal_summary(projects, projects_dir, verbose=args.verbose)


if __name__ == "__main__":
    main()
