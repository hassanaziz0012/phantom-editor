#!/usr/bin/env python3
"""
YouTube Video Metadata Reader & Parser
=======================================
Provides a unified abstraction for discovering, reading, parsing, and modifying
`metadata.json` project files for YouTube video uploads.

Usage as a Python module:
    from youtube_api.read_metadata import read_metadata, VideoMetadata

    # Load metadata by passing video path, folder path, or metadata.json directly
    meta = read_metadata("/path/to/project/final.mp4")
    print(meta.title)
    print(meta.tags)

    # Modify and save back
    meta.url = "https://youtu.be/example123"
    meta.save()

Usage as a CLI script:
    python read_metadata.py /path/to/project/or/video.mp4
    phantom yt read-metadata /path/to/project
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterator, Optional, Union

if TYPE_CHECKING:
    from youtube_api.models import VideoSeed


# ---------------------------------------------------------------------------
# Helpers for terminal highlighting
# ---------------------------------------------------------------------------

def _highlight_json(json_str: str) -> str:
    """Highlights JSON string with ANSI color codes, using pygments if available."""
    if not sys.stdout.isatty():
        return json_str

    try:
        from pygments import highlight
        from pygments.formatters import TerminalFormatter
        from pygments.lexers import JsonLexer
        return highlight(json_str, JsonLexer(), TerminalFormatter()).rstrip()
    except ImportError:
        pass

    pattern = r'("(?:\\.|[^"\\])*")(\s*:)?|(\btrue\b|\bfalse\b|\bnull\b)|(-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'

    def colorize(match):
        string_val, colon, bool_val, num_val = match.groups()
        if string_val is not None:
            if colon:
                return f"\033[1;36m{string_val}\033[0m{colon}"
            return f"\033[32m{string_val}\033[0m"
        if bool_val is not None:
            return f"\033[1;35m{bool_val}\033[0m"
        if num_val is not None:
            return f"\033[33m{num_val}\033[0m"
        return match.group(0)

    return re.sub(pattern, colorize, json_str)


# ---------------------------------------------------------------------------
# Path Resolution
# ---------------------------------------------------------------------------

def find_metadata_file(target: Optional[Union[str, Path]] = None) -> Path:
    """
    Intelligently find the `metadata.json` file from various target formats:
    - Path directly to a metadata.json (or other .json file)
    - Path to a video file in the project folder (e.g. final.mp4, video.mp4)
    - Path to the project directory containing metadata.json
    - None (searches current working directory)
    """
    if target is None:
        target_path = Path.cwd()
    else:
        target_path = Path(target).expanduser().resolve()

    # 1. Exact file check
    if target_path.is_file():
        if target_path.name.lower() == "metadata.json" or target_path.suffix.lower() == ".json":
            return target_path
        # If pointing to a video or other file, check sibling metadata.json
        candidate = target_path.parent / "metadata.json"
        if candidate.exists() and candidate.is_file():
            return candidate

    # 2. Directory check
    if target_path.is_dir():
        candidate = target_path / "metadata.json"
        if candidate.exists() and candidate.is_file():
            return candidate

    # 3. Path might not exist as a directory/file yet or is a relative stem
    # Check if target_path with / metadata.json exists
    sibling_candidate = target_path.parent / "metadata.json"
    if sibling_candidate.exists() and sibling_candidate.is_file():
        return sibling_candidate

    direct_candidate = target_path / "metadata.json"
    if direct_candidate.exists() and direct_candidate.is_file():
        return direct_candidate

    raise FileNotFoundError(
        f"metadata.json not found for target '{target}'. "
        f"Searched: '{target_path}', '{target_path / 'metadata.json'}', and '{target_path.parent / 'metadata.json'}'"
    )


# ---------------------------------------------------------------------------
# VideoMetadata Model
# ---------------------------------------------------------------------------

@dataclass
class VideoMetadata:
    """
    Structured representation of YouTube video metadata parsed from `metadata.json`.
    Supports both object attribute access and dictionary-style access.
    """

    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    category_id: str = "28"
    privacy_status: str = "public"
    made_for_kids: bool = False
    tweet_template: str = "🎬 New video just dropped! {url}"
    url: Optional[str] = None
    publish_date: Optional[str] = None
    file_path: Optional[Path] = None
    raw_data: dict[str, Any] = field(default_factory=dict)

    # ── CamelCase Property Aliases for Compatibility ───────────────────────────

    @property
    def publishDate(self) -> Optional[str]:
        return self.publish_date

    @publishDate.setter
    def publishDate(self, value: Optional[str]) -> None:
        self.publish_date = str(value) if value is not None else None

    @property
    def categoryId(self) -> str:
        return self.category_id

    @categoryId.setter
    def categoryId(self, value: Any) -> None:
        self.category_id = str(value)

    @property
    def privacyStatus(self) -> str:
        return self.privacy_status

    @privacyStatus.setter
    def privacyStatus(self, value: str) -> None:
        self.privacy_status = value

    @property
    def madeForKids(self) -> bool:
        return self.made_for_kids

    @madeForKids.setter
    def madeForKids(self, value: bool) -> None:
        self.made_for_kids = bool(value)

    @property
    def tweetTemplate(self) -> str:
        return self.tweet_template

    @tweetTemplate.setter
    def tweetTemplate(self, value: str) -> None:
        self.tweet_template = value

    @property
    def video_id(self) -> Optional[str]:
        """Extract the YouTube video ID if URL is present."""
        if not self.url:
            return None
        match = re.search(r"(?:v=|youtu\.be/|shorts/)([a-zA-Z0-9_-]{11})", self.url)
        return match.group(1) if match else None

    # ── Dict-like Access Compatibility ────────────────────────────────────────

    def __getitem__(self, key: str) -> Any:
        mapping = {
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "categoryId": self.category_id,
            "category_id": self.category_id,
            "privacyStatus": self.privacy_status,
            "privacy_status": self.privacy_status,
            "madeForKids": self.made_for_kids,
            "made_for_kids": self.made_for_kids,
            "tweetTemplate": self.tweet_template,
            "tweet_template": self.tweet_template,
            "url": self.url,
            "publishDate": self.publish_date,
            "publish_date": self.publish_date,
        }
        if key in mapping:
            return mapping[key]
        if key in self.raw_data:
            return self.raw_data[key]
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key == "title":
            self.title = str(value)
        elif key == "description":
            self.description = str(value)
        elif key == "tags":
            self.tags = list(value) if isinstance(value, (list, tuple, set)) else [str(value)]
        elif key in ("categoryId", "category_id"):
            self.category_id = str(value)
        elif key in ("privacyStatus", "privacy_status"):
            self.privacy_status = str(value)
        elif key in ("madeForKids", "made_for_kids"):
            self.made_for_kids = bool(value)
        elif key in ("tweetTemplate", "tweet_template"):
            self.tweet_template = str(value)
        elif key == "url":
            self.url = str(value) if value is not None else None
        elif key in ("publishDate", "publish_date"):
            self.publish_date = str(value) if value is not None else None
        else:
            self.raw_data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        try:
            val = self[key]
            return default if val is None else val
        except KeyError:
            return default

    def __contains__(self, key: str) -> bool:
        standard_keys = {
            "title", "description", "tags", "categoryId", "category_id",
            "privacyStatus", "privacy_status", "madeForKids", "made_for_kids",
            "tweetTemplate", "tweet_template", "url", "publishDate", "publish_date"
        }
        return key in standard_keys or key in self.raw_data

    def keys(self) -> list[str]:
        return list(self.to_dict().keys())

    def values(self) -> list[Any]:
        return list(self.to_dict().values())

    def items(self) -> list[tuple[str, Any]]:
        return list(self.to_dict().items())

    # ── Conversions & Serialization ───────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Convert to a clean dictionary matching YouTube upload schema."""
        out: dict[str, Any] = {
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "categoryId": self.category_id,
            "privacyStatus": self.privacy_status,
            "madeForKids": self.made_for_kids,
            "tweetTemplate": self.tweet_template,
        }
        if self.url:
            out["url"] = self.url
        if self.publish_date:
            out["publishDate"] = self.publish_date

        # Include any extra keys originally present
        for k, v in self.raw_data.items():
            if k not in out:
                out[k] = v

        return out

    def to_json(self, indent: int = 4) -> str:
        """Format metadata as a JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    def to_seed(self) -> VideoSeed:
        """Convert into a VideoSeed instance for recommendation calculations."""
        # Import lazily to prevent circular dependencies
        from youtube_api.models import VideoSeed
        return VideoSeed(
            title=self.title,
            description=self.description,
            tags=list(self.tags),
            category_id=self.category_id,
        )

    def save(self, destination: Optional[Union[str, Path]] = None) -> Path:
        """
        Write the metadata back to disk.
        If destination is omitted, saves to `self.file_path`.
        """
        target = destination or self.file_path
        if not target:
            raise ValueError("No destination path specified and file_path is None.")

        save_path = Path(target).expanduser().resolve()
        save_path.parent.mkdir(parents=True, exist_ok=True)

        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=4, ensure_ascii=False)
            f.write("\n")

        self.file_path = save_path
        return save_path

    def __repr__(self) -> str:
        return (
            f"VideoMetadata(title={self.title!r}, tags_count={len(self.tags)}, "
            f"url={self.url!r}, path={str(self.file_path)!r})"
        )


# ---------------------------------------------------------------------------
# Parsing & Loading Functions
# ---------------------------------------------------------------------------

def parse_metadata_dict(data: dict[str, Any], file_path: Optional[Path] = None) -> VideoMetadata:
    """Parse a dictionary (from JSON) into a structured VideoMetadata instance."""
    title = str(data.get("title", ""))
    description = str(data.get("description", ""))

    raw_tags = data.get("tags", [])
    if isinstance(raw_tags, str):
        tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
    elif isinstance(raw_tags, (list, tuple, set)):
        tags = [str(t).strip() for t in raw_tags if str(t).strip()]
    else:
        tags = []

    category_id = str(data.get("categoryId") or data.get("category_id") or "28")
    privacy_status = str(data.get("privacyStatus") or data.get("privacy_status") or "public")

    made_for_kids_val = data.get("madeForKids") if "madeForKids" in data else data.get("made_for_kids", False)
    if isinstance(made_for_kids_val, str):
        made_for_kids = made_for_kids_val.strip().lower() in ("true", "1", "yes", "y")
    else:
        made_for_kids = bool(made_for_kids_val)

    tweet_template = str(
        data.get("tweetTemplate") or data.get("tweet_template") or "🎬 New video just dropped! {url}"
    )
    url = data.get("url")
    if url is not None:
        url = str(url).strip() or None

    publish_date = data.get("publishDate") or data.get("publish_date")
    if publish_date is not None:
        publish_date = str(publish_date).strip() or None

    standard_keys = {
        "title", "description", "tags", "categoryId", "category_id",
        "privacyStatus", "privacy_status", "madeForKids", "made_for_kids",
        "tweetTemplate", "tweet_template", "url", "publishDate", "publish_date"
    }
    raw_data = {k: v for k, v in data.items() if k not in standard_keys}

    return VideoMetadata(
        title=title,
        description=description,
        tags=tags,
        category_id=category_id,
        privacy_status=privacy_status,
        made_for_kids=made_for_kids,
        tweet_template=tweet_template,
        url=url,
        publish_date=publish_date,
        file_path=file_path,
        raw_data=raw_data,
    )


def read_metadata(target: Optional[Union[str, Path, dict, VideoMetadata]] = None) -> VideoMetadata:
    """
    Read and parse YouTube video metadata into a VideoMetadata object.

    Conveniently accepts:
    - A Path or str pointing to metadata.json
    - A Path or str pointing to a video file (e.g. /path/to/project/final.mp4)
    - A Path or str pointing to a project directory
    - None (looks in current working directory)
    - An existing dictionary or VideoMetadata object
    """
    if isinstance(target, VideoMetadata):
        return target

    if isinstance(target, dict):
        return parse_metadata_dict(target)

    file_path = find_metadata_file(target)
    with open(file_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    return parse_metadata_dict(data, file_path=file_path)


# Backward-compatible alias
load_metadata = read_metadata


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inspect and parse metadata.json for a YouTube video project."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=".",
        help="Path to metadata.json, video file (.mp4), or project folder (default: current dir).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output raw JSON instead of human-readable summary.",
    )
    args = parser.parse_args()

    try:
        metadata = read_metadata(args.target)
    except FileNotFoundError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"❌ Failed to parse metadata: {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(metadata.to_json())
        return

    print("=" * 60)
    print(f"📄 Metadata: {metadata.file_path}")
    print("=" * 60)
    print(f"  Title:          {metadata.title or '(None)'}")
    print(f"  Category ID:    {metadata.category_id}")
    print(f"  Privacy Status: {metadata.privacy_status}")
    print(f"  Made for Kids:  {metadata.made_for_kids}")
    print(f"  Tags ({len(metadata.tags)}):     {', '.join(metadata.tags) if metadata.tags else '(None)'}")
    print(f"  YouTube URL:    {metadata.url or '(Not uploaded yet)'}")
    print(f"  Tweet Template: {metadata.tweet_template}")
    print("-" * 60)
    print("  Description:")
    if metadata.description:
        for line in metadata.description.splitlines():
            print(f"    {line}")
    else:
        print("    (Empty)")
    print("=" * 60)


if __name__ == "__main__":
    main()
