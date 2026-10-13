#!/usr/bin/env python3
"""
Re-export written_content_sheet_utils from content package for convenience across pipelines.
"""

from content.written_content_sheet_utils import (
    WRITTEN_SHEET_NAME,
    DEFAULT_WRITTEN_HEADERS,
    PLATFORM_CANONICAL,
    VALID_STATUSES,
    DEFAULT_STATUS,
    WrittenPostRecord,
    format_platform,
    ensure_written_sheet_headers,
    parse_written_header_mapping,
    append_written_post,
    list_written_posts,
    update_written_post,
    main,
)

if __name__ == "__main__":
    main()
