#!/usr/bin/env python3
"""
Current Content Pipeline Summary Tool.
Scans ~/Videos/YT Projects (or a custom directory) and displays a summary of
video projects, their current stage in the content pipeline, and the next step required.

Stages:
  1. New             - New project folder created, needs raw video recordings.
  2. Recorded        - Raw footage present, needs Phantom processing (to-review.mp4).
  3. Processed       - Phantom processing complete (to-review.mp4), needs manual review (final.mp4).
  4. Reviewed        - Manual review complete (final.mp4), needs metadata & thumbnail.
  5. Added Metadata  - metadata.json created, needs thumbnail.
  6. Added Thumbnail - Thumbnail(s) created, needs metadata.json.
  7. Ready to Publish- final.mp4 + metadata.json + thumbnail(s) complete!
"""

import os
import sys
import json
import argparse
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Optional

# Set up module paths to reuse video-editing utils if available
pipeline_dir = Path(__file__).resolve().parent
repo_root = pipeline_dir.parent
video_editing_dir = repo_root / "video-editing"

if str(video_editing_dir) not in sys.path:
    sys.path.insert(0, str(video_editing_dir))

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
    raw_files: List[Path] = field(default_factory=list)
    to_review_file: Optional[Path] = None
    final_file: Optional[Path] = None
    metadata_file: Optional[Path] = None
    thumbnail_files: List[Path] = field(default_factory=list)
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
            "raw_files": [f.name for f in self.raw_files],
            "to_review_file": self.to_review_file.name if self.to_review_file else None,
            "final_file": self.final_file.name if self.final_file else None,
            "has_metadata": self.metadata_file is not None,
            "thumbnails": [f.name for f in self.thumbnail_files],
        }


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


def analyze_project(project_dir: Path) -> VideoProject:
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
        elif is_thumbnail_file(item):
            project.thumbnail_files.append(item)
        elif is_raw_video_file(item):
            project.raw_files.append(item)

    # Determine stage
    # Stage 7: Ready to Publish (final.mp4 + metadata.json + thumbnail(s))
    if project.final_file and project.metadata_file and project.thumbnail_files:
        project.stage_num = 7
        project.stage_name = "Ready to Publish"
        project.next_step = "Upload video and publish on YouTube! 🚀"
        project.status_color = COLOR_GREEN

    # Stage 6: Added Thumbnail (final.mp4 + thumbnail, missing metadata)
    elif project.final_file and project.thumbnail_files:
        project.stage_num = 6
        project.stage_name = "Added Thumbnail"
        project.next_step = "Create metadata.json file (title, description, tags)"
        project.status_color = COLOR_CYAN

    # Stage 5: Added Metadata (final.mp4 + metadata.json, missing thumbnail)
    elif project.final_file and project.metadata_file:
        project.stage_num = 5
        project.stage_name = "Added Metadata"
        project.next_step = "Design and add thumbnail (e.g. thumbnail.png)"
        project.status_color = COLOR_CYAN

    # Stage 4: Reviewed (final.mp4 exists, missing both metadata and thumbnail)
    elif project.final_file:
        project.stage_num = 4
        project.stage_name = "Reviewed"
        project.next_step = "Create metadata.json AND design thumbnail (e.g. thumbnail.png)"
        project.status_color = COLOR_BLUE

    # Stage 3: Processed (to-review.mp4 exists, final.mp4 does not)
    elif project.to_review_file:
        project.stage_num = 3
        project.stage_name = "Processed"
        project.next_step = "Manually review to-review.mp4 and create final.mp4"
        project.status_color = COLOR_YELLOW

    # Stage 2: Recorded (raw video files present, no to-review.mp4)
    elif len(project.raw_files) > 0:
        project.stage_num = 2
        project.stage_name = "Recorded"
        project.next_step = f"Process video using Phantom (`phantom process` or `process_video.py`)"
        project.status_color = COLOR_MAGENTA

    # Stage 1: New (No raw video files present)
    else:
        project.stage_num = 1
        project.stage_name = "New"
        project.next_step = "Record raw footage (webcam & screencast files)"
        project.status_color = COLOR_GRAY

    return project


def get_all_projects(projects_dir: Path) -> List[VideoProject]:
    """Find and analyze all project subdirectories in the projects directory."""
    if not projects_dir.exists() or not projects_dir.is_dir():
        return []

    projects = []
    for item in sorted(projects_dir.iterdir()):
        if item.is_dir() and not item.name.startswith("."):
            projects.append(analyze_project(item))
    
    return projects


def render_stage_bar(stage_num: int) -> str:
    """Render a visual step pipeline indicator."""
    steps = [
        ("1", "New"),
        ("2", "Rec"),
        ("3", "Proc"),
        ("4", "Rev"),
        ("5", "Meta"),
        ("6", "Thumb"),
        ("7", "Done"),
    ]
    parts = []
    for num_str, name in steps:
        idx = int(num_str)
        if idx == stage_num:
            parts.append(f"{COLOR_BOLD}{COLOR_GREEN}[{num_str}:{name}]{COLOR_RESET}")
        elif idx < stage_num:
            parts.append(f"{COLOR_GREEN}✓{num_str}{COLOR_RESET}")
        else:
            parts.append(f"{COLOR_GRAY}{num_str}:{name}{COLOR_RESET}")
    return " ➔ ".join(parts)


def print_terminal_summary(projects: List[VideoProject], projects_dir: Path, verbose: bool = False) -> None:
    """Print a clean, visually rich terminal summary of all video projects."""
    width = 76
    print(f"\n{COLOR_BOLD}{COLOR_CYAN}{'=' * width}{COLOR_RESET}")
    print(f"{COLOR_BOLD}{COLOR_WHITE} 🎬  YOUTUBE CONTENT PIPELINE SUMMARY{COLOR_RESET}")
    print(f"{COLOR_GRAY} Directory: {projects_dir}{COLOR_RESET}")
    print(f"{COLOR_BOLD}{COLOR_CYAN}{'=' * width}{COLOR_RESET}\n")

    if not projects:
        print(f"{COLOR_YELLOW}No project folders found in {projects_dir}{COLOR_RESET}\n")
        return

    stage_counts = {i: 0 for i in range(1, 8)}

    for idx, proj in enumerate(projects, 1):
        stage_counts[proj.stage_num] += 1
        color = proj.status_color
        
        print(f"{COLOR_BOLD}{idx}. {proj.name}{COLOR_RESET}")
        print(f"   Stage {proj.stage_num}/7: {color}{COLOR_BOLD}[{proj.stage_name}]{COLOR_RESET}")
        print(f"   Pipeline: {render_stage_bar(proj.stage_num)}")
        print(f"   {COLOR_BOLD}➜ Next Step:{COLOR_RESET} {proj.next_step}")

        # Checklists for files
        raw_info = f"{len(proj.raw_files)} raw file(s)" if proj.raw_files else "No raw files"
        to_rev_status = f"{COLOR_GREEN}✓ {proj.to_review_file.name}{COLOR_RESET}" if proj.to_review_file else f"{COLOR_GRAY}✗ to-review.mp4{COLOR_RESET}"
        final_status = f"{COLOR_GREEN}✓ {proj.final_file.name}{COLOR_RESET}" if proj.final_file else f"{COLOR_GRAY}✗ final.mp4{COLOR_RESET}"
        meta_status = f"{COLOR_GREEN}✓ metadata.json{COLOR_RESET}" if proj.metadata_file else f"{COLOR_GRAY}✗ metadata.json{COLOR_RESET}"
        
        if proj.thumbnail_files:
            thumb_names = ", ".join([f.name for f in proj.thumbnail_files])
            thumb_status = f"{COLOR_GREEN}✓ thumbnail ({thumb_names}){COLOR_RESET}"
        else:
            thumb_status = f"{COLOR_GRAY}✗ thumbnail{COLOR_RESET}"

        print(f"   Files: [{raw_info}] | [{to_rev_status}] | [{final_status}] | [{meta_status}] | [{thumb_status}]")

        if verbose and (proj.raw_files or proj.thumbnail_files):
            if proj.raw_files:
                raw_names = ", ".join([f.name for f in proj.raw_files])
                print(f"   {COLOR_GRAY}└── Raw footage: {raw_names}{COLOR_RESET}")

        print(f"{COLOR_GRAY}{'-' * width}{COLOR_RESET}")

    # Summary Statistics
    print(f"\n{COLOR_BOLD}{COLOR_WHITE}📊 PIPELINE OVERVIEW & STATS{COLOR_RESET}")
    print(f" Total Projects: {COLOR_BOLD}{len(projects)}{COLOR_RESET}")
    
    stat_line = (
        f" {COLOR_GRAY}New: {stage_counts[1]}{COLOR_RESET} | "
        f"{COLOR_MAGENTA}Recorded: {stage_counts[2]}{COLOR_RESET} | "
        f"{COLOR_YELLOW}Processed: {stage_counts[3]}{COLOR_RESET} | "
        f"{COLOR_BLUE}Reviewed: {stage_counts[4]}{COLOR_RESET} | "
        f"{COLOR_CYAN}Metadata: {stage_counts[5]}{COLOR_RESET} | "
        f"{COLOR_CYAN}Thumbnail: {stage_counts[6]}{COLOR_RESET} | "
        f"{COLOR_GREEN}Ready: {stage_counts[7]}{COLOR_RESET}"
    )
    print(stat_line)

    action_needed = stage_counts[3] + stage_counts[4] + stage_counts[5] + stage_counts[6]
    ready_to_pub = stage_counts[7]
    
    print(f"\n {COLOR_YELLOW}⚡ Action Required: {action_needed} video(s) in progress{COLOR_RESET}")
    if ready_to_pub > 0:
        print(f" {COLOR_GREEN}🎉 Ready to Upload: {ready_to_pub} video(s) fully complete!{COLOR_RESET}")

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
