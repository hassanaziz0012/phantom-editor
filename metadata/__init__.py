"""
Metadata generation and management tools for YouTube video projects.
"""

from metadata.utils import (
    extract_json_block,
    load_prompt,
    parse_claude_json,
    parse_srt_to_timestamped_transcript,
    query_claude,
    resolve_project_paths,
)

__all__ = [
    "extract_json_block",
    "load_prompt",
    "parse_claude_json",
    "parse_srt_to_timestamped_transcript",
    "query_claude",
    "resolve_project_paths",
]
