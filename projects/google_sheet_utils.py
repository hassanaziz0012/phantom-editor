#!/usr/bin/env python3
"""
Google Sheets Utility Module
============================
Handles authentication and synchronization with Google Sheets.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Constants & Scopes
# ---------------------------------------------------------------------------
SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
DEFAULT_SHEET_NAME = "YouTube"
DEFAULT_HEADERS = ["Project", "Title", "Status", "URL", "Publish Date", "Platform"]
# Status given to a row whose project folder is gone (deleted, or archived by `phantom projects cleanup`)
ARCHIVED_STATUS = "Archived"


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

    # If headers are missing or not mapped, fall back to default header order,
    # using only column indexes no other field has already claimed
    taken = {i for i in mapping.values() if i >= 0}
    if mapping["project"] == -1 and len(headers) > 0 and "proj" in headers[0].lower() and 0 not in taken:
        mapping["project"] = 0
        taken.add(0)
    for key, idx in (("title", 1), ("status", 2), ("url", 3), ("publish_date", 4), ("platform", 5)):
        if mapping[key] == -1 and len(headers) > idx and idx not in taken:
            mapping[key] = idx
            taken.add(idx)

    return mapping


def _owned_values(project: Any, platform: str) -> Dict[str, str]:
    """The values for the columns the tool owns; the local folder is the source of truth.
    Every other column is never written."""
    return {
        "project": project.name,
        "title": project.title or "",
        "status": project.stage_name,
        "url": project.yt_url or "",
        "publish_date": getattr(project, "scheduled_date", None) or getattr(project, "uploaded_date", None) or "",
        "platform": platform,
    }


# Formats the sheet may display a Publish Date in after USER_ENTERED parses "2026-10-11"
SHEET_DATE_FORMATS = ("%Y-%m-%d", "%B %d, %Y", "%b %d, %Y", "%m/%d/%Y", "%d/%m/%Y")


def _as_iso_date(value: str) -> str:
    for fmt in SHEET_DATE_FORMATS:
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return value


def _escape_formula(value: Any) -> Any:
    """Stop USER_ENTERED from turning text that starts with = + - @ into a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return value


def _column_letter(idx: int) -> str:
    letters = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _cell_text(row: List[Any], idx: int) -> str:
    return str(row[idx]).strip() if 0 <= idx < len(row) else ""


def _changed_cells(row: List[Any], values: Dict[str, str], col_map: Dict[str, int]) -> Dict[int, str]:
    """The owned cells (column index -> new value) whose current value differs from ``values``."""
    changes = {}
    for key, value in values.items():
        idx = col_map.get(key, -1)
        if idx < 0:
            continue
        current = _cell_text(row, idx)
        if key == "publish_date":
            current = _as_iso_date(current)
        if current != str(value).strip():
            changes[idx] = value
    return changes


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
    - Rows are never deleted. A row whose project folder is gone keeps its values and gets
      the Status "Archived"; if the folder comes back, the row is updated as usual.

    Refuses to run with no projects: that almost always means the projects folder is missing
    or not mounted, and every row would be marked Archived.

    Costs one read, plus one write only when something changed.
    Returns a dictionary summarizing changes: {"added": int, "updated": int, "archived": int, "total": int}
    """
    if not projects:
        raise ValueError("No local projects found, so the sheet was left alone (is the projects folder mounted?).")
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

    stats = {"added": 0, "updated": 0, "archived": 0, "total": len(projects)}
    # sheet row number -> {column index: value}; only owned cells are ever written
    updates: Dict[int, Dict[int, str]] = {}

    for offset, row in enumerate(raw_rows):
        row_num = offset + 2
        if not row or not any(str(c).strip() for c in row):
            continue

        def get_val(key: str) -> str:
            return _cell_text(row, col_map.get(key, -1))

        row_proj = get_val("project")
        row_title = get_val("title")
        row_url = get_val("url")
        row_plat = get_val("platform") or "YouTube"

        # Case 1: Row has a Project folder specified
        if row_proj:
            if row_proj not in local_projects:
                # Folder is gone: keep the row, marked Archived
                status_col = col_map["status"]
                if status_col >= 0 and _cell_text(row, status_col) != ARCHIVED_STATUS:
                    updates[row_num] = {status_col: ARCHIVED_STATUS}
                    stats["archived"] += 1
                continue
            p = local_projects[row_proj]
            matched_local_names.add(p.name)
            changes = _changed_cells(row, _owned_values(p, row_plat), col_map)
            if changes:
                stats["updated"] += 1
                updates[row_num] = changes
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
            changes = _changed_cells(row, _owned_values(matched_proj, row_plat), col_map)
            if changes:
                updates[row_num] = changes
        # else: manual / Twitter / other row is left untouched

    # Case 3: Add new local projects not yet in sheet, below the last existing row
    next_row = len(raw_rows) + 2
    for p_name, p in local_projects.items():
        if p_name not in matched_local_names:
            stats["added"] += 1
            updates[next_row] = {
                col_map[key]: value for key, value in _owned_values(p, "YouTube").items() if col_map[key] >= 0
            }
            next_row += 1

    if has_headers and not updates:
        return stats

    # One write of just the changed owned cells (and the header row if it was missing).
    # The date column is left unescaped so USER_ENTERED still parses it as a date.
    data = []
    if not has_headers:
        data.append({"range": f"{sheet_name}!A1", "values": [DEFAULT_HEADERS]})
    date_col = col_map["publish_date"]
    for row_num, cells in sorted(updates.items()):
        for col, value in sorted(cells.items()):
            if col != date_col:
                value = _escape_formula(value)
            data.append({"range": f"{sheet_name}!{_column_letter(col)}{row_num}", "values": [[value]]})
    if data:
        service.spreadsheets().values().batchUpdate(
            spreadsheetId=sheet_id,
            body={"valueInputOption": "USER_ENTERED", "data": data},
        ).execute()

    return stats
