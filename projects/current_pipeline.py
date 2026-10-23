#!/usr/bin/env python3
"""
Current Content Pipeline Summary Tool.
Scans ~/Videos/YT Projects (or a custom directory) and displays a summary of
video projects, their current stage in the content pipeline, and the next step required.

Stages:
  1. New               - New project folder created, needs video script (.excalidraw) and outline.md.
  2. Scripted          - Script (.excalidraw) and outline.md (filled-in "# Hook" & "# Outline"
                         sections, plus a [LIKE / SUBSCRIBE] marker for the engagement CTA)
                         created, needs raw video recordings.
  3. Recorded          - Raw footage present, needs Phantom processing (to-review.mp4).
  4. Processed         - Phantom processing complete (to-review.mp4), needs manual review (final.mp4).
  5. Reviewed          - Manual review complete (final.mp4), needs metadata & thumbnail.
  6. Added Metadata    - metadata.json created, needs thumbnail.
  7. Added Thumbnail   - Thumbnail(s) created, needs metadata.json.
  8. Ready to Schedule - final.mp4 + metadata.json + thumbnail(s) complete, ready for release/upload.
  9. Scheduled         - Video has scheduled publish date, needs YouTube upload.
 10. Uploaded          - Video uploaded to YouTube (metadata.json has valid YT URL). Complete!
 11. Needs Repackaging - 48h after publish, its first days' CTR was below the channel's 28-day CTR.
                         Needs a new title & thumbnail; goes back to Uploaded once the title or
                         thumbnail on YouTube no longer matches metadata.json / the local thumbnails.

48 hours after publish, each uploaded video's analytics are read from YouTube Studio and stored in
its metadata.json under "performanceCheck" (see youtube_api/performance_check.py). That check runs
once per video; later runs use the stored result.
"""

import os
import sys
import json
import re
import argparse
from datetime import date, datetime, timedelta
from urllib.parse import urlparse, parse_qs
from pathlib import Path

pipeline_dir = Path(__file__).resolve().parent
repo_root = pipeline_dir.parent
video_editing_dir = repo_root / "video-editing"

if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

if str(video_editing_dir) not in sys.path:
    sys.path.append(str(video_editing_dir))

from dataclasses import dataclass, field
from typing import List, Optional, Any


try:
    from metadata.read_metadata import read_metadata
except ImportError:
    from read_metadata import read_metadata

PERFORMANCE_CHECK_KEY = "performanceCheck"

try:
    from projects.google_sheet_utils import sync_projects_to_sheet
except ImportError:
    try:
        from google_sheet_utils import sync_projects_to_sheet
    except ImportError:
        sync_projects_to_sheet = None

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
COLOR_PURPLE = "\033[35m"
COLOR_GRAY = "\033[90m"
COLOR_WHITE = "\033[97m"

VIDEO_EXTENSIONS = {".mp4", ".webm", ".mov", ".mkv", ".avi", ".m4v"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
GENERATED_PREFIXES = ("to-review", "final", "after-", "temp", "preview", "processed-audio")
OUTLINE_FILENAME = "outline.md"
OUTLINE_SECTIONS = ("Hook", "Outline")
# Marks where in the video to ask viewers to like & subscribe; must appear somewhere in outline.md.
OUTLINE_CTA_MARKER = "[LIKE / SUBSCRIBE]"
OUTLINE_CTA_PATTERN = re.compile(r"\[\s*LIKE\s*/\s*SUBSCRIBE\s*\]", re.IGNORECASE)


def is_valid_yt_url(url: str) -> bool:
    """Check if a URL string is a valid YouTube URL (fast, no HTTP requests)."""
    if not url or not isinstance(url, str):
        return False
    url = url.strip()
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https"):
            return False
        hostname = (parsed.hostname or "").lower()
        valid_hosts = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtu.be"}
        if hostname not in valid_hosts:
            return False

        if hostname in ("youtu.be", "www.youtu.be"):
            path = parsed.path.strip("/")
            return len(path) > 0
        else:
            if parsed.path == "/watch":
                qs = parse_qs(parsed.query)
                v_param = qs.get("v", [""])[0]
                return len(v_param) > 0
            elif parsed.path.startswith(("/embed/", "/v/", "/shorts/", "/live/")):
                parts = [p for p in parsed.path.split("/") if p]
                return len(parts) >= 2
            elif len(parsed.path.strip("/")) > 0:
                return True
        return False
    except Exception:
        return False


@dataclass
class VideoProject:
    name: str
    path: Path
    script_files: List[Path] = field(default_factory=list)
    outline_file: Optional[Path] = None
    outline_missing_sections: List[str] = field(default_factory=list)
    outline_has_cta: bool = False
    raw_files: List[Path] = field(default_factory=list)
    to_review_file: Optional[Path] = None
    final_file: Optional[Path] = None
    metadata_file: Optional[Path] = None
    thumbnail_files: List[Path] = field(default_factory=list)
    yt_url: Optional[str] = None
    title: Optional[str] = None
    scheduled_date: Optional[str] = None
    uploaded_date: Optional[str] = None
    performance_check: Optional[dict] = None
    stage_num: int = 1
    stage_name: str = "New"
    next_step: str = ""
    status_color: str = COLOR_RESET

    @property
    def has_outline(self) -> bool:
        return self.outline_file is not None and not self.outline_missing_sections and self.outline_has_cta

    @property
    def is_scripted(self) -> bool:
        return len(self.script_files) > 0 and self.has_outline

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": str(self.path),
            "stage_num": self.stage_num,
            "stage_name": self.stage_name,
            "next_step": self.next_step,
            "has_script": len(self.script_files) > 0,
            "script_files": [f.name for f in self.script_files],
            "has_outline": self.has_outline,
            "outline_missing_sections": self.outline_missing_sections,
            "outline_has_cta": self.outline_has_cta,
            "raw_files": [f.name for f in self.raw_files],
            "to_review_file": self.to_review_file.name if self.to_review_file else None,
            "final_file": self.final_file.name if self.final_file else None,
            "has_metadata": self.metadata_file is not None,
            "title": self.title,
            "thumbnails": [f.name for f in self.thumbnail_files],
            "yt_url": self.yt_url,
            "scheduled_date": self.scheduled_date,
            "uploaded_date": self.uploaded_date,
            "performance_check": self.performance_check,
        }


def is_script_file(file_path: Path) -> bool:
    """Check if a file is an Excalidraw script / presentation file."""
    if file_path.suffix.lower() == ".excalidraw":
        return True
    name_lower = file_path.name.lower()
    if name_lower.endswith(".excalidraw") or name_lower.endswith(".excalidraw.json") or name_lower.endswith(".excalidraw.svg"):
        return True
    return False


def missing_outline_sections(outline_file: Path) -> List[str]:
    """Return the OUTLINE_SECTIONS that outline.md lacks a "# <Section>" heading for, or that have no text.

    Only "# <Section>" headings for OUTLINE_SECTIONS start a new section; any other "# " heading
    (e.g. a title line inside the outline) counts as text of the current section.
    """
    section_names = {name.lower() for name in OUTLINE_SECTIONS}
    sections = {}
    current = None
    for line in outline_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("# ") and line[2:].strip().lower() in section_names:
            current = line[2:].strip().lower()
            sections.setdefault(current, False)
        # Blank lines, "---" separators and empty bullets don't count as text.
        elif current is not None and not re.fullmatch(r"[-*_\s]*", line):
            sections[current] = True
    return [name for name in OUTLINE_SECTIONS if not sections.get(name.lower())]


def has_outline_cta(outline_file: Path) -> bool:
    """Check if outline.md contains the OUTLINE_CTA_MARKER anywhere (case and spacing are ignored)."""
    return bool(OUTLINE_CTA_PATTERN.search(outline_file.read_text(encoding="utf-8", errors="replace")))


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


def describe_performance_check(check: dict) -> str:
    """One-line comparison of a stored performance check's CTR with the channel's 28-day CTR."""
    video, channel = check.get("video", {}), check.get("channel_28d", {})

    def pct(value) -> str:
        return f"{value:.2f}%" if isinstance(value, (int, float)) else "?"

    def duration(ms) -> str:
        if not isinstance(ms, (int, float)):
            return "?"
        minutes, seconds = divmod(round(ms / 1000), 60)
        return f"{minutes}:{seconds:02}"

    sign = "<" if check.get("needs_repackaging") else "≥"
    return (
        f"CTR {pct(video.get('ctr'))} in its first {check.get('window_days', '?')} days {sign} "
        f"channel's 28-day {pct(channel.get('ctr'))} "
        f"(avg view {duration(video.get('avg_view_duration_since_publish_ms'))} since publish "
        f"vs channel's {duration(channel.get('avg_view_duration_ms'))})"
    )


def is_performance_check_due(project: VideoProject) -> bool:
    """
    Whether an uploaded video still needs its performance check. Uses uploadedDate to skip
    videos that can't be 48h old yet; the exact publish time is confirmed from YouTube.
    """
    if not project.yt_url or project.performance_check or not project.metadata_file:
        return False
    if not project.uploaded_date:
        return True
    try:
        uploaded = date.fromisoformat(project.uploaded_date.strip()[:10])
    except ValueError:
        return True
    return uploaded + timedelta(days=2) <= date.today()


def run_performance_checks(projects: List[VideoProject], quiet: bool = False) -> List[VideoProject]:
    """
    Run the 48h performance check for every uploaded project that's due, store each result
    in its metadata.json, and return the projects re-analyzed with their new stage.
    """
    due = [p for p in projects if is_performance_check_due(p)]
    if not due:
        return projects

    # Imported here so status stays fast (no Playwright or API client) when nothing is due.
    from youtube_api.performance_check import check_videos
    from youtube_api.utils import extract_video_id

    by_video_id = {}
    for project in due:
        try:
            by_video_id[extract_video_id(project.yt_url)] = project
        except ValueError:
            pass
    if not by_video_id:
        return projects

    if not quiet:
        print(f"{COLOR_CYAN}📈 Running the 48h performance check for {len(by_video_id)} uploaded video(s)...{COLOR_RESET}")
    try:
        results = check_videos(list(by_video_id))
    except Exception as e:
        if not quiet:
            print(f"{COLOR_YELLOW}⚠️ Note: Could not run the performance check: {e}{COLOR_RESET}\n")
        return projects

    checked = set()
    for video_id, outcome in results.items():
        project = by_video_id[video_id]
        if outcome["status"] == "ok":
            try:
                meta = read_metadata(project.metadata_file)
                meta.raw_data[PERFORMANCE_CHECK_KEY] = outcome["result"]
                meta.save()
                checked.add(project.path)
                if not quiet:
                    verdict = "needs repackaging" if outcome["result"]["needs_repackaging"] else "passed"
                    print(f"{COLOR_GREEN}✓ {project.name}: {verdict}{COLOR_RESET}")
            except Exception as e:
                if not quiet:
                    print(f"{COLOR_YELLOW}⚠️ {project.name}: could not save the performance check: {e}{COLOR_RESET}")
        elif quiet:
            continue
        elif outcome["status"] == "waiting":
            ready_at = datetime.fromisoformat(outcome["ready_at"]).astimezone()
            print(f"{COLOR_GRAY}⏳ {project.name}: not 48h old yet, check runs after {ready_at:%b %d %H:%M}{COLOR_RESET}")
        else:
            print(f"{COLOR_YELLOW}⚠️ {project.name}: {outcome['reason']}{COLOR_RESET}")
    if not quiet:
        print()

    return [analyze_project(p.path) if p.path in checked else p for p in projects]


def run_repackaging_checks(projects: List[VideoProject], quiet: bool = False) -> List[VideoProject]:
    """
    For every project that needs repackaging, check whether its YouTube title or thumbnail
    has changed from the metadata.json title and local thumbnails. Once one has, it's
    recorded under performanceCheck.repackaged and the project goes back to Uploaded.
    """
    flagged = [p for p in projects if p.stage_num == 11]
    if not flagged:
        return projects

    # Imported here so status stays fast when nothing needs repackaging.
    from youtube_api.performance_check import check_repackaged
    from youtube_api.utils import extract_video_id

    by_video_id = {}
    for project in flagged:
        try:
            by_video_id[extract_video_id(project.yt_url)] = project
        except ValueError:
            pass
    try:
        results = check_repackaged({
            video_id: {"title": project.title, "thumbnails": project.thumbnail_files}
            for video_id, project in by_video_id.items()
        })
    except Exception as e:
        if not quiet:
            print(f"{COLOR_YELLOW}⚠️ Note: Could not check for repackaged videos: {e}{COLOR_RESET}\n")
        return projects

    repackaged = set()
    for video_id, outcome in results.items():
        project = by_video_id[video_id]
        if outcome["status"] != "ok":
            if not quiet:
                print(f"{COLOR_YELLOW}⚠️ {project.name}: {outcome['reason']}{COLOR_RESET}")
            continue
        if not outcome["repackaged"]:
            continue
        try:
            meta = read_metadata(project.metadata_file)
            meta.raw_data[PERFORMANCE_CHECK_KEY]["repackaged"] = {
                "detected_at": datetime.now().astimezone().isoformat(),
                "title_changed": outcome["title_changed"],
                "thumbnail_changed": outcome["thumbnail_changed"],
                "youtube_title": outcome["youtube_title"],
            }
            meta.save()
            repackaged.add(project.path)
            if not quiet:
                print(f"{COLOR_GREEN}✓ {project.name}: repackaged ({describe_repackaging(meta.raw_data[PERFORMANCE_CHECK_KEY]['repackaged'])}){COLOR_RESET}")
        except Exception as e:
            if not quiet:
                print(f"{COLOR_YELLOW}⚠️ {project.name}: could not save the repackaging: {e}{COLOR_RESET}")
    if repackaged and not quiet:
        print()

    return [analyze_project(p.path) if p.path in repackaged else p for p in projects]


def describe_repackaging(repackaged: dict) -> str:
    changed = [name for name, key in (("title", "title_changed"), ("thumbnail", "thumbnail_changed")) if repackaged.get(key)]
    return f"new {' & '.join(changed)}" if changed else "changed"


def stage_label(stage_num: int) -> str:
    # Stage 11 is a post-upload flag, so it isn't shown as part of the 10-step pipeline.
    return f"Stage {stage_num}/10" if stage_num <= 10 else f"Stage {stage_num}"


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
            try:
                meta = read_metadata(item)
                if meta.title:
                    project.title = meta.title
                if meta.url and is_valid_yt_url(meta.url):
                    project.yt_url = meta.url
                if meta.publish_date:
                    project.scheduled_date = meta.publish_date
                if meta.uploaded_date:
                    project.uploaded_date = meta.uploaded_date
                if isinstance(meta.raw_data.get(PERFORMANCE_CHECK_KEY), dict):
                    project.performance_check = meta.raw_data[PERFORMANCE_CHECK_KEY]
            except Exception:
                pass
        elif item.name.lower() == OUTLINE_FILENAME:
            project.outline_file = item
            try:
                project.outline_missing_sections = missing_outline_sections(item)
                project.outline_has_cta = has_outline_cta(item)
            except OSError:
                project.outline_missing_sections = list(OUTLINE_SECTIONS)
        elif is_script_file(item):
            project.script_files.append(item)
        elif is_thumbnail_file(item):
            project.thumbnail_files.append(item)
        elif is_raw_video_file(item):
            project.raw_files.append(item)

    has_scheduled_date = bool(project.scheduled_date and project.scheduled_date.strip())

    # Determine stage
    check = project.performance_check or {}

    # Stage 11: Needs Repackaging (uploaded, the 48h performance check found its CTR below the
    # channel's 28-day CTR, and its YouTube title & thumbnail still match the originals)
    if project.yt_url and check.get("needs_repackaging") and not check.get("repackaged"):
        project.stage_num = 11
        project.stage_name = "Needs Repackaging"
        project.next_step = f"Rework the title & thumbnail: {describe_performance_check(project.performance_check)}"
        project.status_color = COLOR_RED

    # Stage 10: Uploaded (metadata.json contains a valid YouTube URL)
    elif project.yt_url:
        project.stage_num = 10
        project.stage_name = "Uploaded"
        date_str = project.uploaded_date or project.scheduled_date
        date_info = f" (Published: {date_str})" if date_str else ""
        if check.get("repackaged"):
            check_info = (
                f" Repackaged ({describe_repackaging(check['repackaged'])}) after a below-average "
                f"performance check: {describe_performance_check(check)}"
            )
        elif check:
            check_info = f" Performance check passed: {describe_performance_check(check)}"
        else:
            check_info = " Performance check runs 48h after publish."
        project.next_step = f"Video uploaded to YouTube{date_info}! All pipeline stages complete! 🚀{check_info}"
        project.status_color = COLOR_GREEN

    # Stage 9: Scheduled (has scheduled publish date in local metadata.json, awaiting YouTube upload)
    elif has_scheduled_date:
        project.stage_num = 9
        project.stage_name = "Scheduled"
        date_info = f" ({project.scheduled_date})" if project.scheduled_date else ""
        project.next_step = f"Upload video to YouTube{date_info}! 🚀 (`phantom yt upload` or add URL to metadata.json)"
        project.status_color = COLOR_YELLOW

    # Stage 8: Ready to Schedule (final.mp4 + metadata.json + thumbnail(s))
    elif project.final_file and project.metadata_file and project.thumbnail_files:
        project.stage_num = 8
        project.stage_name = "Ready to Schedule"
        project.next_step = "Upload video to YouTube (`phantom yt upload`) or set publishDate in metadata.json"
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
        project.status_color = COLOR_PURPLE

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

    # Stage 2: Scripted (.excalidraw file and filled-in outline.md with the CTA marker present, no raw video files)
    elif project.is_scripted:
        project.stage_num = 2
        project.stage_name = "Scripted"
        project.next_step = "Record raw footage (webcam & screencast files)"
        project.status_color = COLOR_BLUE

    # Stage 1: New (No raw video files, and the .excalidraw script or a filled-in outline.md is missing)
    else:
        project.stage_num = 1
        project.stage_name = "New"
        todo = []
        if not project.script_files:
            todo.append("create .excalidraw presentation with notes & diagrams")
        if not project.outline_file:
            todo.append(
                f"write {OUTLINE_FILENAME} with {' & '.join(f'# {s}' for s in OUTLINE_SECTIONS)} sections"
                f" and a {OUTLINE_CTA_MARKER} marker"
            )
        else:
            if project.outline_missing_sections:
                sections = " & ".join(f"# {s}" for s in project.outline_missing_sections)
                todo.append(f"fill in the {sections} section(s) of {OUTLINE_FILENAME}")
            if not project.outline_has_cta:
                todo.append(f"add a {OUTLINE_CTA_MARKER} marker to {OUTLINE_FILENAME} where the viewer should be asked to engage")
        project.next_step = f"Script the video ({'; '.join(todo)})"
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
                    or proj.outline_file is not None
                    or len(proj.raw_files) > 0
                    or proj.to_review_file is not None
                    or proj.final_file is not None
                )
            elif idx == 2:
                is_complete = proj.is_scripted or proj.stage_num > 2
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
                is_complete = proj.stage_num > 9 or bool(proj.yt_url) or bool(proj.scheduled_date) or bool(proj.uploaded_date)
            elif idx == 10:
                is_complete = proj.stage_num == 10 or bool(proj.yt_url)

            if is_complete:
                parts.append(f"{COLOR_GREEN}✓{num_str}{COLOR_RESET}")
            else:
                parts.append(f"{COLOR_GRAY}{num_str}:{name}{COLOR_RESET}")
    if proj.stage_num == 11:
        parts.append(f"{COLOR_BOLD}{COLOR_RED}[11:Repack]{COLOR_RESET}")
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

    stage_counts = {i: 0 for i in range(1, 12)}

    for idx, proj in enumerate(projects, 1):
        stage_counts[proj.stage_num] += 1
        color = proj.status_color
        
        print(f"{COLOR_BOLD}{idx}. {proj.name}{COLOR_RESET}")
        print(f"   {stage_label(proj.stage_num)}: {color}{COLOR_BOLD}[{proj.stage_name}]{COLOR_RESET}")
        print(f"   Pipeline: {render_stage_bar(proj)}")
        print(f"   {COLOR_BOLD}➜ Next Step:{COLOR_RESET} {proj.next_step}")

        # Checklists for files
        if proj.script_files:
            script_names = ", ".join([f.name for f in proj.script_files])
            script_status = f"{COLOR_GREEN}✓ script ({script_names}){COLOR_RESET}"
        else:
            script_status = f"{COLOR_GRAY}✗ script (.excalidraw){COLOR_RESET}"

        if proj.has_outline:
            outline_status = f"{COLOR_GREEN}✓ {proj.outline_file.name}{COLOR_RESET}"
        elif proj.outline_file:
            missing = ", ".join([f"# {s}" for s in proj.outline_missing_sections]
                                + ([] if proj.outline_has_cta else [OUTLINE_CTA_MARKER]))
            outline_status = f"{COLOR_YELLOW}✗ {proj.outline_file.name} (missing/empty: {missing}){COLOR_RESET}"
        else:
            outline_status = f"{COLOR_GRAY}✗ {OUTLINE_FILENAME}{COLOR_RESET}"

        raw_info = f"{len(proj.raw_files)} raw file(s)" if proj.raw_files else "No raw files"
        to_rev_status = f"{COLOR_GREEN}✓ {proj.to_review_file.name}{COLOR_RESET}" if proj.to_review_file else f"{COLOR_GRAY}✗ to-review.mp4{COLOR_RESET}"
        final_status = f"{COLOR_GREEN}✓ {proj.final_file.name}{COLOR_RESET}" if proj.final_file else f"{COLOR_GRAY}✗ final.mp4{COLOR_RESET}"
        
        if proj.metadata_file:
            if proj.yt_url:
                date_str = proj.uploaded_date or proj.scheduled_date
                date_label = f" (Uploaded: {date_str})" if date_str else " (URL set)"
                meta_status = f"{COLOR_GREEN}✓ metadata.json{date_label}{COLOR_RESET}"
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

        print(f"   Files: [{script_status}] | [{outline_status}] | [{raw_info}] | [{to_rev_status}] | [{final_status}] | [{meta_status}] | [{thumb_status}]")

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
        f"{COLOR_PURPLE}Reviewed: {stage_counts[5]}{COLOR_RESET} | "
        f"{COLOR_CYAN}Metadata: {stage_counts[6]}{COLOR_RESET} | "
        f"{COLOR_CYAN}Thumbnail: {stage_counts[7]}{COLOR_RESET} | "
        f"{COLOR_CYAN}Ready: {stage_counts[8]}{COLOR_RESET} | "
        f"{COLOR_YELLOW}Scheduled: {stage_counts[9]}{COLOR_RESET} | "
        f"{COLOR_GREEN}Uploaded: {stage_counts[10]}{COLOR_RESET} | "
        f"{COLOR_RED}Repackage: {stage_counts[11]}{COLOR_RESET}"
    )
    print(stat_line)

    action_needed = (
        stage_counts[2] + stage_counts[3] + stage_counts[4] +
        stage_counts[5] + stage_counts[6] + stage_counts[7] +
        stage_counts[8] + stage_counts[9] + stage_counts[11]
    )
    ready_to_sched = stage_counts[8]
    scheduled_cnt = stage_counts[9]
    uploaded_cnt = stage_counts[10]
    repackage_cnt = stage_counts[11]
    
    if action_needed > 0:
        print(f"\n {COLOR_YELLOW}⚡ Action Required: {action_needed} video(s) in progress / awaiting next steps{COLOR_RESET}")
    if ready_to_sched > 0:
        print(f" {COLOR_CYAN}📋 Ready to Schedule: {ready_to_sched} video(s) fully reviewed with metadata & thumbnail!{COLOR_RESET}")
    if scheduled_cnt > 0:
        print(f" {COLOR_YELLOW}📅 Scheduled (Needs Upload): {scheduled_cnt} video(s) scheduled awaiting YouTube upload{COLOR_RESET}")
    if uploaded_cnt > 0:
        print(f" {COLOR_GREEN}🎉 Uploaded: {uploaded_cnt} video(s) published on YouTube!{COLOR_RESET}")
    if repackage_cnt > 0:
        print(f" {COLOR_RED}🔁 Needs Repackaging: {repackage_cnt} video(s) with CTR below the channel's 28-day CTR{COLOR_RESET}")

    print(f"\n{COLOR_BOLD}{COLOR_CYAN}{'=' * width}{COLOR_RESET}\n")


def print_concise_summary(projects: List[VideoProject]) -> None:
    """Print a concise one-line-per-project summary."""
    if not projects:
        print(f"{COLOR_YELLOW}No matching project folders found.{COLOR_RESET}")
        return
    for idx, proj in enumerate(projects, 1):
        color = proj.status_color
        print(f"{idx}. {proj.name} ({color}{stage_label(proj.stage_num)}: {proj.stage_name}{COLOR_RESET})")


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
        "--new",
        action="store_true",
        help="Display only new stage projects (Stage 1).",
    )
    parser.add_argument(
        "--scripted",
        action="store_true",
        help="Display only scripted stage projects (Stage 2).",
    )
    parser.add_argument(
        "--pending",
        action="store_true",
        help="Display in-progress stages (Stages 3-7: Recorded through Added Thumbnail).",
    )
    parser.add_argument(
        "--finished",
        action="store_true",
        help="Display ready to schedule, scheduled, uploaded, or needs-repackaging projects (Stages 8-11).",
    )
    parser.add_argument(
        "--repackage",
        action="store_true",
        help="Display only projects that need repackaging (Stage 11).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output summary in JSON format."
    )
    parser.add_argument(
        "--less", "-l",
        action="store_true",
        help="Display a concise one-line-per-project summary."
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Show detailed file list for each project."
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="Skip syncing pipeline status with Google Sheets.",
    )
    parser.add_argument(
        "--no-analytics",
        action="store_true",
        help="Skip the 48h performance check and repackaging check of uploaded videos "
             "(the performance check opens YouTube Studio in Chrome).",
    )
    args = parser.parse_args()

    projects_dir = Path(args.dir).expanduser().resolve()
    all_projects = get_all_projects(projects_dir)
    if not args.no_analytics:
        all_projects = run_performance_checks(all_projects, quiet=args.json)
        all_projects = run_repackaging_checks(all_projects, quiet=args.json)

    filter_stages = set()
    if args.new:
        filter_stages.add(1)
    if args.scripted:
        filter_stages.add(2)
    if args.pending:
        filter_stages.update([3, 4, 5, 6, 7])
    if args.finished:
        filter_stages.update([8, 9, 10, 11])
    if args.repackage:
        filter_stages.add(11)

    if filter_stages:
        projects = [p for p in all_projects if p.stage_num in filter_stages]
    else:
        projects = all_projects

    if args.json:
        data = {
            "projects_dir": str(projects_dir),
            "total_projects": len(projects),
            "projects": [p.to_dict() for p in projects]
        }
        print(json.dumps(data, indent=2))
    elif args.less:
        print_concise_summary(projects)
    else:
        print_terminal_summary(projects, projects_dir, verbose=args.verbose)

    # Automatically synchronize project statuses with Google Sheets
    if not args.no_sync and sync_projects_to_sheet is not None:
        if args.json:
            try:
                sync_projects_to_sheet(all_projects)
            except Exception:
                pass
        else:
            try:
                print(f"{COLOR_CYAN}🔄 Syncing video projects with Google Sheets...{COLOR_RESET}", end="", flush=True)
                stats = sync_projects_to_sheet(all_projects)
                details = []
                if stats.get("added"):
                    details.append(f"{stats['added']} added")
                if stats.get("updated"):
                    details.append(f"{stats['updated']} updated")
                if stats.get("removed"):
                    details.append(f"{stats['removed']} removed")
                detail_str = f" ({', '.join(details)})" if details else " (all up to date)"
                print(f"\r{COLOR_GREEN}✓ Synced {stats['total']} project(s) with Google Sheets{detail_str}.{COLOR_RESET}\n")
            except Exception as e:
                print(f"\r{COLOR_YELLOW}⚠️ Note: Could not sync with Google Sheets: {e}{COLOR_RESET}\n")


if __name__ == "__main__":
    main()
