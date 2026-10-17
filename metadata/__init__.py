"""
Metadata generation and management tools for YouTube video projects.
"""

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

_READ_METADATA_NAMES = {"VideoMetadata", "find_metadata_file", "load_metadata", "parse_metadata_dict", "read_metadata"}


# Imported lazily: metadata.utils pulls in the BrowserLLM client (and pydantic),
# which callers that only read metadata.json (projects status, yt) don't need.
def __getattr__(name: str):
    if name in _READ_METADATA_NAMES:
        import importlib
        _rm = importlib.import_module("metadata.read_metadata")
        # Importing the submodule binds metadata.read_metadata to the module; rebind
        # these names to the functions, as the old eager re-export did.
        globals().update({n: getattr(_rm, n) for n in _READ_METADATA_NAMES})
        return globals()[name]
    if name in __all__:
        import metadata.utils as _u
        return getattr(_u, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
