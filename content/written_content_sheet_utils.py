#!/usr/bin/env python3
"""
Written Content Google Sheets Utility Module
============================================
Handles interaction with the 'Written' tab in the Google Sheets Content Calendar.

Columns:
  - Post: Text content of the generated post
  - Platform: Dropdown (LinkedIn, Substack, Twitter, Threads)
  - Status: Dropdown (Draft, Reviewed, Posted)
  - URL: Direct link to published post (empty initially)
  - Media: Image ideas or media descriptions for the post
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure repository root is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from dotenv import load_dotenv

load_dotenv(repo_root / ".env")

from pipelines.google_sheet_utils import get_sheets_service, get_spreadsheet_id

WRITTEN_SHEET_NAME = "Written"
DEFAULT_WRITTEN_HEADERS = ["Post", "Platform", "Status", "URL", "Media"]

# Standard canonical platform names matching dropdown data validation
PLATFORM_CANONICAL = {
    "linkedin": "LinkedIn",
    "substack": "Substack",
    "twitter": "Twitter",
    "threads": "Threads",
}

VALID_STATUSES = ["Draft", "Reviewed", "Posted"]
DEFAULT_STATUS = "Draft"


@dataclass
class WrittenPostRecord:
    row_index: int  # 1-based row index in Google Sheet
    post: str = ""
    platform: str = "LinkedIn"
    status: str = "Draft"
    url: str = ""
    media: str = ""
    extra_fields: Dict[str, Any] = field(default_factory=dict)

    def to_row(self, headers: Optional[List[str]] = None) -> List[str]:
        if not headers:
            headers = DEFAULT_WRITTEN_HEADERS

        row: List[str] = []
        for h in headers:
            h_norm = h.strip().lower()
            if h_norm == "post":
                row.append(self.post)
            elif h_norm == "platform":
                row.append(self.platform)
            elif h_norm == "status":
                row.append(self.status)
            elif h_norm == "url":
                row.append(self.url)
            elif h_norm == "media":
                row.append(self.media)
            else:
                row.append(str(self.extra_fields.get(h, "")))
        return row


def format_platform(platform: str) -> str:
    """
    Normalizes platform name to match the Google Sheet dropdown options:
    'LinkedIn', 'Substack', 'Twitter', 'Threads'.
    """
    clean = platform.strip().lower()
    if clean in PLATFORM_CANONICAL:
        return PLATFORM_CANONICAL[clean]

    # Check for prefix or substring match
    for k, v in PLATFORM_CANONICAL.items():
        if clean.startswith(k) or k in clean:
            return v

    return platform.capitalize()


def ensure_written_sheet_headers(
    service=None,
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = WRITTEN_SHEET_NAME,
) -> List[str]:
    """
    Checks if the Written sheet has a header row. If empty, initializes DEFAULT_WRITTEN_HEADERS.
    Returns the current headers list.
    """
    sheet_id = get_spreadsheet_id(spreadsheet_id)
    if service is None:
        service = get_sheets_service()

    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{sheet_name}!A1:Z1",
    ).execute()
    rows = result.get("values", [])

    if not rows or not rows[0] or not any(str(c).strip() for c in rows[0]):
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="USER_ENTERED",
            body={"values": [DEFAULT_WRITTEN_HEADERS]},
        ).execute()
        return DEFAULT_WRITTEN_HEADERS

    return [str(c).strip() for c in rows[0]]


def parse_written_header_mapping(headers: List[str]) -> Dict[str, int]:
    """Maps field names to column indexes in the Written sheet."""
    mapping = {
        "post": -1,
        "platform": -1,
        "status": -1,
        "url": -1,
        "media": -1,
    }
    for idx, h in enumerate(headers):
        h_norm = h.strip().lower()
        if h_norm == "post" and mapping["post"] == -1:
            mapping["post"] = idx
        elif h_norm == "platform" and mapping["platform"] == -1:
            mapping["platform"] = idx
        elif h_norm in ("status", "state") and mapping["status"] == -1:
            mapping["status"] = idx
        elif h_norm in ("url", "link") and mapping["url"] == -1:
            mapping["url"] = idx
        elif h_norm in ("media", "image", "images") and mapping["media"] == -1:
            mapping["media"] = idx

    # Fallback to default header indices if not identified
    if mapping["post"] == -1 and len(headers) > 0:
        mapping["post"] = 0
    if mapping["platform"] == -1 and len(headers) > 1:
        mapping["platform"] = 1
    if mapping["status"] == -1 and len(headers) > 2:
        mapping["status"] = 2
    if mapping["url"] == -1 and len(headers) > 3:
        mapping["url"] = 3
    if mapping["media"] == -1 and len(headers) > 4:
        mapping["media"] = 4

    return mapping


def append_written_post(
    post: str,
    platform: str,
    media: str = "",
    status: str = DEFAULT_STATUS,
    url: str = "",
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = WRITTEN_SHEET_NAME,
    service=None,
) -> WrittenPostRecord:
    """
    Appends a new written post to the 'Written' sheet tab.
    - Matches Platform dropdown capitalization (e.g. 'LinkedIn', 'Substack').
    - Status defaults to 'Draft'.
    - Media receives generated image ideas or media description.
    """
    sheet_id = get_spreadsheet_id(spreadsheet_id)
    if service is None:
        service = get_sheets_service()

    headers = ensure_written_sheet_headers(service, sheet_id, sheet_name)
    col_map = parse_written_header_mapping(headers)

    norm_platform = format_platform(platform)
    norm_status = status.strip().capitalize() if status else DEFAULT_STATUS
    if norm_status not in VALID_STATUSES:
        # If unknown status, default to Draft to satisfy dropdown validation
        norm_status = DEFAULT_STATUS

    row_len = max(len(headers), max(col_map.values()) + 1 if col_map else len(DEFAULT_WRITTEN_HEADERS))
    row_data = [""] * row_len

    if col_map["post"] >= 0:
        row_data[col_map["post"]] = post
    if col_map["platform"] >= 0:
        row_data[col_map["platform"]] = norm_platform
    if col_map["status"] >= 0:
        row_data[col_map["status"]] = norm_status
    if col_map["url"] >= 0:
        row_data[col_map["url"]] = url
    if col_map["media"] >= 0:
        row_data[col_map["media"]] = media

    # Find the next available empty row (starting from row 2) so we preserve
    # the user's existing pre-formatted dropdown smart chips and styles.
    res = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{sheet_name}!A2:A",
    ).execute()
    rows = res.get("values", [])

    target_row = 2
    for idx, r in enumerate(rows, start=2):
        if not r or not str(r[0]).strip():
            target_row = idx
            break
    else:
        target_row = len(rows) + 2

    # Update the target row in place using USER_ENTERED
    # This leaves existing data validations, smart chips, and colors intact.
    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{sheet_name}!A{target_row}:E{target_row}",
        valueInputOption="USER_ENTERED",
        body={"values": [row_data]},
    ).execute()

    return WrittenPostRecord(
        row_index=target_row,
        post=post,
        platform=norm_platform,
        status=norm_status,
        url=url,
        media=media,
    )


def list_written_posts(
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = WRITTEN_SHEET_NAME,
    status_filter: Optional[str] = None,
    platform_filter: Optional[str] = None,
    service=None,
) -> List[WrittenPostRecord]:
    """
    Fetches all posts from the 'Written' sheet tab.
    Optionally filter by status (e.g. 'Draft', 'Reviewed', 'Posted') or platform.
    """
    sheet_id = get_spreadsheet_id(spreadsheet_id)
    if service is None:
        service = get_sheets_service()

    headers = ensure_written_sheet_headers(service, sheet_id, sheet_name)
    col_map = parse_written_header_mapping(headers)

    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{sheet_name}!A2:Z",
    ).execute()
    rows = result.get("values", [])

    records: List[WrittenPostRecord] = []
    for idx, row in enumerate(rows, start=2):
        if not row or not any(str(c).strip() for c in row):
            continue

        def get_col(col_idx: int) -> str:
            if 0 <= col_idx < len(row):
                return str(row[col_idx]).strip()
            return ""

        post = get_col(col_map["post"])
        platform = get_col(col_map["platform"])
        status = get_col(col_map["status"]) or DEFAULT_STATUS
        url = get_col(col_map["url"])
        media = get_col(col_map["media"])

        if status_filter and status.lower() != status_filter.strip().lower():
            continue
        if platform_filter and platform.lower() != platform_filter.strip().lower():
            continue

        extra: Dict[str, Any] = {}
        for c_idx, h_name in enumerate(headers):
            if c_idx not in col_map.values() and c_idx < len(row):
                extra[h_name] = str(row[c_idx]).strip()

        records.append(
            WrittenPostRecord(
                row_index=idx,
                post=post,
                platform=platform,
                status=status,
                url=url,
                media=media,
                extra_fields=extra,
            )
        )

    return records


def update_written_post(
    row_index: int,
    updates: Dict[str, str],
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = WRITTEN_SHEET_NAME,
    service=None,
) -> bool:
    """
    Updates specific fields for a row in the 'Written' sheet.
    `updates` dict keys can include: post, platform, status, url, media.
    """
    if row_index < 2:
        raise ValueError(f"Invalid row_index {row_index} for update (row 1 is header).")

    sheet_id = get_spreadsheet_id(spreadsheet_id)
    if service is None:
        service = get_sheets_service()

    headers = ensure_written_sheet_headers(service, sheet_id, sheet_name)
    col_map = parse_written_header_mapping(headers)

    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{sheet_name}!A{row_index}:Z{row_index}",
    ).execute()
    current_values = result.get("values", [[]])[0]
    while len(current_values) < len(headers):
        current_values.append("")

    for k, v in updates.items():
        k_norm = k.strip().lower()
        if k_norm == "platform":
            v = format_platform(v)
        elif k_norm == "status":
            v = v.strip().capitalize()

        if k_norm in col_map and col_map[k_norm] >= 0:
            target_col = col_map[k_norm]
            while len(current_values) <= target_col:
                current_values.append("")
            current_values[target_col] = str(v)

    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{sheet_name}!A{row_index}:{row_index}",
        valueInputOption="USER_ENTERED",
        body={"values": [current_values]},
    ).execute()

    return True


def main():
    parser = argparse.ArgumentParser(description="Manage the Written Content Calendar sheet.")
    parser.add_argument("--list", action="store_true", help="List all posts in the Written sheet.")
    parser.add_argument("--status", type=str, help="Filter list by status (Draft, Reviewed, Posted).")
    parser.add_argument("--platform", type=str, help="Filter list by platform.")
    parser.add_argument("--sheet-id", type=str, default=None, help="Google Sheets ID override.")

    args = parser.parse_args()

    if args.list:
        posts = list_written_posts(
            spreadsheet_id=args.sheet_id,
            status_filter=args.status,
            platform_filter=args.platform,
        )
        if not posts:
            print("No written posts found matching criteria.")
            return

        print(f"Found {len(posts)} post(s) in 'Written' tab:\n")
        for p in posts:
            snippet = (p.post[:60] + "...") if len(p.post) > 60 else p.post
            snippet = snippet.replace("\n", " ")
            print(f"Row {p.row_index} | [{p.platform}] [{p.status}] | Post: {snippet}")
            if p.media:
                media_snippet = (p.media[:50] + "...") if len(p.media) > 50 else p.media
                media_snippet = media_snippet.replace("\n", " ")
                print(f"       Media: {media_snippet}")
            if p.url:
                print(f"       URL: {p.url}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
