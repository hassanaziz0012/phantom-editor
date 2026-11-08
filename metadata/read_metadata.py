#!/usr/bin/env python3
"""
YouTube Video Metadata Reader & Parser
=======================================
Provides a unified abstraction for discovering, reading, parsing, and modifying
`metadata.json` project files for YouTube video uploads.

Usage as a Python module:
    from metadata.read_metadata import read_metadata, VideoMetadata

    # Load metadata by passing video path, folder path, or metadata.json directly
    meta = read_metadata("/path/to/project/final.mp4")
    print(meta.title)
    print(meta.tags)

    # Modify and save back
    meta.url = "https://youtu.be/example123"
    meta.save()

Usage as a CLI script:
    python metadata/read_metadata.py /path/to/project/or/video.mp4
    phantom meta read /path/to/project
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Optional, Union

# Ensure repository root is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

if TYPE_CHECKING:
    from youtube_api.models import VideoSeed


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
# Field table
# ---------------------------------------------------------------------------

def _optional_str(value: Any) -> Optional[str]:
    return (str(value).strip() or None) if value is not None else None


def _as_bool(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "y")
    return bool(value)


def _as_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(t).strip() for t in value if str(t).strip()]
    return []


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value] if value else []


# metadata.json key (camelCase) -> (VideoMetadata field, how a value is coerced)
FIELDS: dict[str, tuple[str, Callable[[Any], Any]]] = {
    "title": ("title", str),
    "description": ("description", str),
    "tags": ("tags", _as_tags),
    "categoryId": ("category_id", str),
    "privacyStatus": ("privacy_status", str),
    "madeForKids": ("made_for_kids", _as_bool),
    "tweetTemplate": ("tweet_template", str),
    "timestamps": ("timestamps", _as_list),
    "recommendations": ("recommendations", _as_list),
    "url": ("url", _optional_str),
    "publishDate": ("publish_date", _optional_str),
    "uploadedDate": ("uploaded_date", _optional_str),
}

# Every key the model reads, camelCase or snake_case, -> its field and coercion
KEY_TO_FIELD: dict[str, tuple[str, Callable[[Any], Any]]] = {
    **FIELDS,
    **{field_name: (field_name, coerce) for field_name, coerce in FIELDS.values()},
}

# Keys to_dict() always writes; the rest only when they have a value
ALWAYS_WRITTEN = ("title", "description", "tags", "categoryId", "privacyStatus", "madeForKids", "tweetTemplate")


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
    timestamps: list[Any] = field(default_factory=list)
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    url: Optional[str] = None
    publish_date: Optional[str] = None
    uploaded_date: Optional[str] = None
    file_path: Optional[Path] = None
    raw_data: dict[str, Any] = field(default_factory=dict)

    @property
    def video_id(self) -> Optional[str]:
        """Extract the YouTube video ID if URL is present."""
        # Imported here: the youtube_api package imports this module
        from youtube_api.video_id import video_id_or_none

        return video_id_or_none(self.url)

    # ── Dict-like Access Compatibility ────────────────────────────────────────

    def __getitem__(self, key: str) -> Any:
        if key in KEY_TO_FIELD:
            return getattr(self, KEY_TO_FIELD[key][0])
        if key in self.raw_data:
            return self.raw_data[key]
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key in KEY_TO_FIELD:
            field_name, coerce = KEY_TO_FIELD[key]
            setattr(self, field_name, coerce(value))
        else:
            self.raw_data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        try:
            val = self[key]
            return default if val is None else val
        except KeyError:
            return default

    def __contains__(self, key: str) -> bool:
        return key in KEY_TO_FIELD or key in self.raw_data

    def keys(self) -> list[str]:
        return list(self.to_dict().keys())

    def values(self) -> list[Any]:
        return list(self.to_dict().values())

    def items(self) -> list[tuple[str, Any]]:
        return list(self.to_dict().items())

    # ── Conversions & Serialization ───────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Convert to a clean dictionary matching YouTube upload schema."""
        out: dict[str, Any] = {}
        for key, (field_name, _) in FIELDS.items():
            value = getattr(self, field_name)
            if key in ALWAYS_WRITTEN or value:
                out[key] = value

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
    metadata = VideoMetadata(
        file_path=file_path,
        raw_data={k: v for k, v in data.items() if k not in KEY_TO_FIELD},
    )
    for key, (field_name, _) in FIELDS.items():
        value = data[key] if key in data else data.get(field_name)
        # Missing or empty values keep the dataclass default
        if value is not None and value != "":
            metadata[key] = value
    return metadata


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
    if metadata.uploaded_date:
        print(f"  Uploaded Date:  {metadata.uploaded_date}")
    if metadata.publish_date:
        print(f"  Publish Date:   {metadata.publish_date}")
    print(f"  Tweet Template: {metadata.tweet_template}")
    print("-" * 60)
    print("  Description:")
    if metadata.description:
        for line in metadata.description.splitlines():
            print(f"    {line}")
    else:
        print("    (Empty)")
    if metadata.timestamps:
        print("-" * 60)
        print(f"  Timestamps ({len(metadata.timestamps)}):")
        for item in metadata.timestamps:
            if isinstance(item, dict):
                ts = item.get("timestamp") or item.get("time") or ""
                topic = item.get("topic") or item.get("title") or ""
                print(f"    {ts:<8} {topic}")
            else:
                print(f"    {item}")
    print("=" * 60)


if __name__ == "__main__":
    main()
