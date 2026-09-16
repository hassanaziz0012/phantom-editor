#!/usr/bin/env python3
"""
Google Sheets Utility Module
============================
Handles authentication and synchronization with Google Sheets.
"""

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

# Ensure repo root is accessible
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

load_dotenv(repo_root / ".env")

# ---------------------------------------------------------------------------
# Constants & Scopes
# ---------------------------------------------------------------------------
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DEFAULT_SHEET_NAME = "YouTube"
DEFAULT_HEADERS = ["Project", "Title", "Status", "URL", "Publish Date", "Platform"]


def get_spreadsheet_id(spreadsheet_id: Optional[str] = None) -> str:
    """Get spreadsheet ID from argument, environment, or raise error."""
    sheet_id = spreadsheet_id or os.getenv("CONTENT_CALENDAR_SHEET_ID")
    if not sheet_id:
        raise ValueError(
            "Google Sheets Spreadsheet ID is missing. "
            "Set CONTENT_CALENDAR_SHEET_ID in .env or pass --sheet-id."
        )
    return sheet_id.strip()


def get_service_account_credentials(
    service_account_email: Optional[str] = None,
    private_key: Optional[str] = None,
    scopes: Optional[List[str]] = None,
):
    """
    Constructs Google Service Account Credentials from environment variables or arguments.
    """
    from google.oauth2 import service_account

    email = (service_account_email or os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL") or "").strip()
    key = private_key or os.getenv("GOOGLE_PRIVATE_KEY") or ""

    if not email:
        raise ValueError(
            "Google Service Account Email is missing. "
            "Set GOOGLE_SERVICE_ACCOUNT_EMAIL in .env."
        )
    if not key:
        raise ValueError(
            "Google Service Account Private Key is missing. "
            "Set GOOGLE_PRIVATE_KEY in .env."
        )

    # Format key properly (handle wrapping quotes and escaped newlines)
    key = key.strip()
    if (key.startswith('"') and key.endswith('"')) or (key.startswith("'") and key.endswith("'")):
        key = key[1:-1]
    key = key.replace("\\n", "\n")

    info = {
        "client_email": email,
        "private_key": key,
        "token_uri": "https://oauth2.googleapis.com/token",
    }
    return service_account.Credentials.from_service_account_info(
        info, scopes=scopes or SCOPES
    )


def get_sheets_service(
    service_account_email: Optional[str] = None,
    private_key: Optional[str] = None,
    credentials=None,
):
    """
    Authenticate and return an authorized Google Sheets API service resource
    using Google Cloud Service Account credentials.
    """
    from googleapiclient.discovery import build

    if credentials is None:
        credentials = get_service_account_credentials(
            service_account_email=service_account_email,
            private_key=private_key,
        )

    return build("sheets", "v4", credentials=credentials)


def get_sheet_tab_info(service, spreadsheet_id: str, sheet_name: str = DEFAULT_SHEET_NAME) -> Dict[str, Any]:
    """Retrieve metadata about the spreadsheet and specific tab."""
    metadata = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    sheets = metadata.get("sheets", [])
    if not sheets:
        raise ValueError(f"Spreadsheet {spreadsheet_id} contains no sheets.")

    for s in sheets:
        props = s.get("properties", {})
        if props.get("title", "").lower() == sheet_name.lower():
            return props

    # Default to first sheet if specified sheet_name is not found
    return sheets[0].get("properties", {})


def ensure_sheet_headers(
    service,
    spreadsheet_id: str,
    sheet_name: str = DEFAULT_SHEET_NAME
) -> List[str]:
    """
    Checks if sheet has header row. If sheet is empty, initializes DEFAULT_HEADERS.
    Returns the current headers list.
    """
    result = service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=f"{sheet_name}!A1:Z1"
    ).execute()
    rows = result.get("values", [])

    if not rows or not rows[0] or not any(str(c).strip() for c in rows[0]):
        # Write default headers
        service.spreadsheets().values().update(
            spreadsheetId=spreadsheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="USER_ENTERED",
            body={"values": [DEFAULT_HEADERS]}
        ).execute()
        return DEFAULT_HEADERS

    return [str(c).strip() for c in rows[0]]


def parse_header_mapping(headers: List[str]) -> Dict[str, int]:
    """Maps standard field names to their column index in the sheet."""
    mapping = {
        "project": -1,
        "title": -1,
        "status": -1,
        "url": -1,
        "publish_date": -1,
        "platform": -1,
        "description": -1,
    }
    for idx, h in enumerate(headers):
        h_norm = h.strip().lower()
        if "proj" in h_norm and mapping["project"] == -1:
            mapping["project"] = idx
        elif "title" in h_norm and mapping["title"] == -1:
            mapping["title"] = idx
        elif ("stat" in h_norm or "state" in h_norm) and mapping["status"] == -1:
            mapping["status"] = idx
        elif ("url" in h_norm or "link" in h_norm) and mapping["url"] == -1:
            mapping["url"] = idx
        elif ("date" in h_norm or "time" in h_norm or "publish" in h_norm) and mapping["publish_date"] == -1:
            mapping["publish_date"] = idx
        elif ("plat" in h_norm or "channel" in h_norm or "target" in h_norm) and mapping["platform"] == -1:
            mapping["platform"] = idx
        elif ("desc" in h_norm or "details" in h_norm) and mapping["description"] == -1:
            mapping["description"] = idx

    # If headers are missing or not mapped, fall back to default header order
    if mapping["project"] == -1 and len(headers) > 0 and "proj" in headers[0].lower():
        mapping["project"] = 0
    if mapping["title"] == -1 and len(headers) > 1:
        mapping["title"] = 1
    if mapping["status"] == -1 and len(headers) > 2:
        mapping["status"] = 2
    if mapping["url"] == -1 and len(headers) > 3:
        mapping["url"] = 3
    if mapping["publish_date"] == -1 and len(headers) > 4:
        mapping["publish_date"] = 4
    if mapping["platform"] == -1 and len(headers) > 5:
        mapping["platform"] = 5

    return mapping


def sync_projects_to_sheet(
    projects: List[Any],
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = DEFAULT_SHEET_NAME,
    service=None
) -> Dict[str, int]:
    """
    Synchronizes local video projects with Google Sheets (one-way sync).
    - Local project directory is the sole source of truth.
    - Updates Status, Title, URL, and Publish Date for existing projects to match local state.
    - Clears/unschedules Publish Date in sheet if removed locally.
    - Preserves rows without a Project name (manual/Twitter entries).
    - Rows with a Project name whose local folder was deleted are removed from the sheet.
    
    Returns a dictionary summarizing changes: {"added": int, "updated": int, "removed": int, "total": int}
    """
    sheet_id = get_spreadsheet_id(spreadsheet_id)
    if service is None:
        service = get_sheets_service()

    headers = ensure_sheet_headers(service, sheet_id, sheet_name)
    col_map = parse_header_mapping(headers)

    # Fetch all current rows
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{sheet_name}!A2:Z"
    ).execute()
    raw_rows = result.get("values", [])

    # Map of local projects by folder name
    local_projects = {p.name: p for p in projects}
    matched_local_names = set()

    stats = {"added": 0, "updated": 0, "removed": 0, "total": len(projects)}
    new_rows = []

    for row in raw_rows:
        if not row or not any(str(c).strip() for c in row):
            continue

        def get_val(key: str) -> str:
            col_idx = col_map.get(key, -1)
            if 0 <= col_idx < len(row):
                return str(row[col_idx]).strip()
            return ""

        row_proj = get_val("project")
        row_title = get_val("title")
        row_status = get_val("status")
        row_url = get_val("url")
        row_date = get_val("publish_date")
        row_plat = get_val("platform") or "YouTube"

        # Case 1: Row has a Project folder specified
        if row_proj:
            if row_proj in local_projects:
                p = local_projects[row_proj]
                matched_local_names.add(p.name)

                # Local project state is the sole source of truth
                updated_title = p.title or ""
                updated_url = p.yt_url or ""
                updated_status = p.stage_name
                updated_date = getattr(p, "uploaded_date", None) or getattr(p, "scheduled_date", None) or ""

                if (
                    updated_status != row_status
                    or updated_title != row_title
                    or updated_url != row_url
                    or updated_date != row_date
                ):
                    stats["updated"] += 1

                # Construct updated row
                row_data = [""] * len(headers)
                if col_map["project"] >= 0: row_data[col_map["project"]] = p.name
                if col_map["title"] >= 0: row_data[col_map["title"]] = updated_title
                if col_map["status"] >= 0: row_data[col_map["status"]] = updated_status
                if col_map["url"] >= 0: row_data[col_map["url"]] = updated_url
                if col_map["publish_date"] >= 0: row_data[col_map["publish_date"]] = updated_date
                if col_map["platform"] >= 0: row_data[col_map["platform"]] = row_plat
                new_rows.append(row_data)
            else:
                # Folder was deleted locally! Remove row from sheet
                stats["removed"] += 1
                continue

        # Case 2: Row has NO Project folder (manual entry, Twitter post, etc.)
        else:
            # Check if this manual entry matches an unmatched local project by title or url
            matched_proj = None
            for p_name, p in local_projects.items():
                if p_name in matched_local_names:
                    continue
                if p.yt_url and row_url and p.yt_url == row_url:
                    matched_proj = p
                    break
                if p.title and row_title and p.title.strip().lower() == row_title.strip().lower():
                    matched_proj = p
                    break

            if matched_proj:
                matched_local_names.add(matched_proj.name)
                stats["updated"] += 1
                matched_date = getattr(matched_proj, "uploaded_date", None) or getattr(matched_proj, "scheduled_date", None) or ""
                row_data = [""] * len(headers)
                if col_map["project"] >= 0: row_data[col_map["project"]] = matched_proj.name
                if col_map["title"] >= 0: row_data[col_map["title"]] = matched_proj.title or ""
                if col_map["status"] >= 0: row_data[col_map["status"]] = matched_proj.stage_name
                if col_map["url"] >= 0: row_data[col_map["url"]] = matched_proj.yt_url or ""
                if col_map["publish_date"] >= 0: row_data[col_map["publish_date"]] = matched_date
                if col_map["platform"] >= 0: row_data[col_map["platform"]] = row_plat
                new_rows.append(row_data)
            else:
                # Preserve manual / Twitter / other row as is
                # Pad to headers length if needed
                padded_row = list(row) + [""] * max(0, len(headers) - len(row))
                new_rows.append(padded_row[:len(headers)])

    # Case 3: Add new local projects not yet in sheet
    for p_name, p in local_projects.items():
        if p_name not in matched_local_names:
            stats["added"] += 1
            proj_date = getattr(p, "uploaded_date", None) or getattr(p, "scheduled_date", None) or ""
            row_data = [""] * len(headers)
            if col_map["project"] >= 0: row_data[col_map["project"]] = p.name
            if col_map["title"] >= 0: row_data[col_map["title"]] = p.title or ""
            if col_map["status"] >= 0: row_data[col_map["status"]] = p.stage_name
            if col_map["url"] >= 0: row_data[col_map["url"]] = p.yt_url or ""
            if col_map["publish_date"] >= 0: row_data[col_map["publish_date"]] = proj_date
            if col_map["platform"] >= 0: row_data[col_map["platform"]] = "YouTube"
            new_rows.append(row_data)

    # Perform batch update to the sheet
    # 1. Update the data values starting from row 2
    if new_rows:
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!A2",
            valueInputOption="USER_ENTERED",
            body={"values": new_rows}
        ).execute()

    # 2. If the new row count is less than the old row count, clear the leftover rows
    old_row_count = len(raw_rows)
    new_row_count = len(new_rows)
    if new_row_count < old_row_count:
        clear_start = new_row_count + 2
        clear_end = old_row_count + 2
        service.spreadsheets().values().clear(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!A{clear_start}:Z{clear_end}"
        ).execute()

    return stats

