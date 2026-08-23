"""
Metadata generation and management tools for YouTube video projects.
"""

from metadata.read_metadata import (
    VideoMetadata,
    find_metadata_file,
    load_metadata,
    parse_metadata_dict,
    read_metadata,
)
from metadata.utils import (
    extract_json_block,
    load_prompt,
    parse_claude_json,
    parse_srt_to_timestamped_transcript,
    query_claude,
    resolve_project_paths,
)

__all__ = [
    "VideoMetadata",
    "find_metadata_file",
    "load_metadata",
    "parse_metadata_dict",
    "read_metadata",
    "extract_json_block",
    "load_prompt",
    "parse_claude_json",
    "parse_srt_to_timestamped_transcript",
    "query_claude",
    "resolve_project_paths",
]
