#!/usr/bin/env python3
"""
Google Sheets Utility Module
============================
Handles authentication and synchronization with Google Sheets.
"""

import json
import os
import sys
from datetime import datetime, timedelta, timezone
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
    scopes = scopes or SCOPES
    credentials = service_account.Credentials.from_service_account_info(info, scopes=scopes)
    _apply_cached_token(credentials, email, scopes)
    return credentials


# Fetching an access token costs about 1.5 s per run. Tokens last an hour, so the
# last one is kept here (owner-only) and reused while it has a few minutes left.
TOKEN_CACHE_FILE = Path.home() / ".cache" / "phantom-editor" / "sheets_token.json"
TOKEN_MIN_REMAINING = timedelta(minutes=5)


def _apply_cached_token(credentials, email: str, scopes: List[str]) -> None:
    """Load a still-valid cached token into ``credentials``, or fetch and cache a new one."""
    from google.auth.transport.requests import Request

    cache_key = f"{email} {' '.join(sorted(scopes))}"
    try:
        cached = json.loads(TOKEN_CACHE_FILE.read_text(encoding="utf-8"))
        # google-auth compares expiry against a naive UTC datetime
        expiry = datetime.fromisoformat(cached["expiry"])
        if cached["key"] == cache_key and expiry - datetime.now(timezone.utc).replace(tzinfo=None) > TOKEN_MIN_REMAINING:
            credentials.token = cached["token"]
            credentials.expiry = expiry
            return
    except (OSError, ValueError, KeyError, TypeError):
        pass

    credentials.refresh(Request())
    try:
        TOKEN_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(TOKEN_CACHE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"key": cache_key, "token": credentials.token, "expiry": credentials.expiry.isoformat()}, f)
    except OSError:
        pass  # Caching is only an optimisation; the next run fetches a fresh token.


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


def _project_row(project: Any, headers: List[str], col_map: Dict[str, int], platform: str) -> List[str]:
    """The sheet row for a local project; the local folder is the source of truth."""
    values = {
        "project": project.name,
        "title": project.title or "",
        "status": project.stage_name,
        "url": project.yt_url or "",
        "publish_date": getattr(project, "uploaded_date", None) or getattr(project, "scheduled_date", None) or "",
        "platform": platform,
    }
    row = [""] * len(headers)
    for key, value in values.items():
        if col_map[key] >= 0:
            row[col_map[key]] = value
    return row


# Formats the sheet may display a Publish Date in after USER_ENTERED parses "2026-10-11"
SHEET_DATE_FORMATS = ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%d/%m/%Y")


def _as_iso_date(value: str) -> str:
    for fmt in SHEET_DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return value


def _comparable(row: List[Any], col_map: Dict[str, int]) -> List[str]:
    """A row as the Sheets API returns it (strings, no trailing empty cells), dates as ISO."""
    cells = [str(c) for c in row]
    date_col = col_map["publish_date"]
    if 0 <= date_col < len(cells):
        cells[date_col] = _as_iso_date(cells[date_col])
    while cells and not cells[-1].strip():
        cells.pop()
    return cells


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

    Costs one read, plus one write only when something changed.
    Returns a dictionary summarizing changes: {"added": int, "updated": int, "removed": int, "total": int}
    """
    sheet_id = get_spreadsheet_id(spreadsheet_id)
    if service is None:
        service = get_sheets_service()

    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{sheet_name}!A1:Z"
    ).execute()
    values = result.get("values", [])

    has_headers = bool(values and values[0] and any(str(c).strip() for c in values[0]))
    headers = [str(c).strip() for c in values[0]] if has_headers else DEFAULT_HEADERS
    raw_rows = values[1:]
    col_map = parse_header_mapping(headers)

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
        row_url = get_val("url")
        row_plat = get_val("platform") or "YouTube"

        # Case 1: Row has a Project folder specified
        if row_proj:
            if row_proj not in local_projects:
                # Folder was deleted locally! Remove row from sheet
                stats["removed"] += 1
                continue
            p = local_projects[row_proj]
            matched_local_names.add(p.name)
            row_data = _project_row(p, headers, col_map, row_plat)
            if _comparable(row_data, col_map) != _comparable(row, col_map):
                stats["updated"] += 1
            new_rows.append(row_data)
            continue

        # Case 2: Row has NO Project folder (manual entry, Twitter post, etc.)
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
            new_rows.append(_project_row(matched_proj, headers, col_map, row_plat))
        else:
            # Preserve manual / Twitter / other row as is, padded to the header length
            padded_row = list(row) + [""] * max(0, len(headers) - len(row))
            new_rows.append(padded_row[:len(headers)])

    # Case 3: Add new local projects not yet in sheet
    for p_name, p in local_projects.items():
        if p_name not in matched_local_names:
            stats["added"] += 1
            new_rows.append(_project_row(p, headers, col_map, "YouTube"))

    unchanged = has_headers and [_comparable(r, col_map) for r in new_rows] == [_comparable(r, col_map) for r in raw_rows]
    if unchanged:
        return stats

    # One write: the header row if it was missing, the data from row 2, and blank
    # rows over any leftovers when the table shrank (blank values clear those cells).
    width = max([len(headers)] + [len(r) for r in raw_rows])
    leftover = max(0, len(raw_rows) - len(new_rows))
    data = [{"range": f"{sheet_name}!A2", "values": new_rows + [[""] * width] * leftover}]
    if not has_headers:
        data.append({"range": f"{sheet_name}!A1", "values": [DEFAULT_HEADERS]})
    if new_rows or leftover:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()

    return stats
