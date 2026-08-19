#!/usr/bin/env python3
"""
Google Sheets Utility Module for Content Calendar
=================================================
Handles authentication, reading, adding, updating, and deleting records
from the Google Sheets Content Calendar.
"""

import os
import sys
from dataclasses import dataclass, field
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
DEFAULT_SHEET_NAME = "Pipeline"
DEFAULT_HEADERS = ["Project", "Title", "Status", "URL", "Publish Date", "Platform"]

TOKEN_DIR = Path(__file__).resolve().parent / "tokens"
TOKEN_FILE = TOKEN_DIR / "sheets_token.json"


@dataclass
class CalendarRecord:
    row_index: int  # 1-based row index in Google Sheet
    project: str = ""
    title: str = ""
    status: str = "New"
    url: str = ""
    publish_date: str = ""
    platform: str = "YouTube"
    description: str = ""
    extra_fields: Dict[str, Any] = field(default_factory=dict)

    def to_row_values(self, headers: Optional[List[str]] = None) -> List[str]:
        if not headers:
            headers = DEFAULT_HEADERS

        values = []
        for h in headers:
            h_norm = h.strip().lower()
            if "proj" in h_norm:
                values.append(self.project)
            elif "title" in h_norm:
                values.append(self.title)
            elif "stat" in h_norm or "state" in h_norm:
                values.append(self.status)
            elif "url" in h_norm or "link" in h_norm:
                values.append(self.url)
            elif "date" in h_norm or "publish" in h_norm:
                values.append(self.publish_date)
            elif "plat" in h_norm or "channel" in h_norm:
                values.append(self.platform)
            elif "desc" in h_norm:
                values.append(self.description)
            else:
                values.append(self.extra_fields.get(h, ""))
        return values


def find_client_secrets() -> Path:
    """Locate client_secret.json across common project paths."""
    candidates = [
        Path(__file__).resolve().parent / "tokens/client_secret.json",
        repo_root / "youtube_api/tokens/client_secret.json",
        repo_root / "tokens/client_secret.json",
        repo_root / "video-editing/tokens/client_secret.json",
        repo_root / "client_secret.json",
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]


def get_spreadsheet_id(spreadsheet_id: Optional[str] = None) -> str:
    """Get spreadsheet ID from argument, environment, or raise error."""
    sheet_id = spreadsheet_id or os.getenv("CONTENT_CALENDAR_SHEET_ID")
    if not sheet_id:
        raise ValueError(
            "Google Sheets Content Calendar ID is missing. "
            "Set CONTENT_CALENDAR_SHEET_ID in .env or pass --sheet-id."
        )
    return sheet_id.strip()


def get_sheets_service(
    credentials_file: Optional[Path] = None,
    token_file: Optional[Path] = None
):
    """
    Authenticate and return an authorized Google Sheets API service resource.
    Refreshes credentials or prompts OAuth local server authentication as needed.
    """
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    if token_file is None:
        token_file = TOKEN_FILE
    if credentials_file is None:
        credentials_file = find_client_secrets()

    creds = None
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except Exception as e:
            print(f"⚠️ Warning: Error reading token file {token_file}: {e}. Re-authenticating...")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"⚠️ Warning: Failed to refresh token: {e}. Re-running auth flow...")
                creds = None

        if not creds:
            if not credentials_file.exists():
                raise FileNotFoundError(
                    f"Google client_secret.json credentials file not found.\n"
                    f"Searched paths:\n"
                    f" - {Path(__file__).resolve().parent / 'tokens/client_secret.json'}\n"
                    f" - {repo_root / 'youtube_api/tokens/client_secret.json'}\n"
                    f" - {repo_root / 'tokens/client_secret.json'}\n"
                    f"Please verify client_secret.json exists."
                )

            token_file.parent.mkdir(parents=True, exist_ok=True)
            flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
            creds = flow.run_local_server(port=0)

        with open(token_file, "w") as f:
            f.write(creds.to_json())

    return build("sheets", "v4", credentials=creds)


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


def list_records(
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = DEFAULT_SHEET_NAME,
    service=None
) -> List[CalendarRecord]:
    """
    Fetches all calendar records from Google Sheet.
    Returns a list of CalendarRecord objects.
    """
    sheet_id = get_spreadsheet_id(spreadsheet_id)
    if service is None:
        service = get_sheets_service()

    headers = ensure_sheet_headers(service, sheet_id, sheet_name)
    col_map = parse_header_mapping(headers)

    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{sheet_name}!A2:Z"
    ).execute()
    rows = result.get("values", [])

    records = []
    for idx, row in enumerate(rows, start=2):  # Row 1 is header, data starts at row 2
        # Skip completely empty rows
        if not row or not any(str(c).strip() for c in row):
            continue

        def get_col(col_idx: int) -> str:
            if 0 <= col_idx < len(row):
                return str(row[col_idx]).strip()
            return ""

        project = get_col(col_map["project"])
        title = get_col(col_map["title"])
        status = get_col(col_map["status"]) or "New"
        url = get_col(col_map["url"])
        publish_date = get_col(col_map["publish_date"])
        platform = get_col(col_map["platform"]) or "YouTube"
        description = get_col(col_map["description"])

        extra = {}
        for c_idx, h_name in enumerate(headers):
            if c_idx not in col_map.values() and c_idx < len(row):
                extra[h_name] = str(row[c_idx]).strip()

        records.append(
            CalendarRecord(
                row_index=idx,
                project=project,
                title=title,
                status=status,
                url=url,
                publish_date=publish_date,
                platform=platform,
                description=description,
                extra_fields=extra,
            )
        )

    return records


def add_record(
    title: str,
    project: str = "",
    status: str = "Scheduled",
    url: str = "",
    publish_date: str = "",
    platform: str = "YouTube",
    description: str = "",
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = DEFAULT_SHEET_NAME,
    service=None
) -> CalendarRecord:
    """
    Appends a new record to the Google Sheet.
    """
    sheet_id = get_spreadsheet_id(spreadsheet_id)
    if service is None:
        service = get_sheets_service()

    headers = ensure_sheet_headers(service, sheet_id, sheet_name)
    col_map = parse_header_mapping(headers)

    # Build the row array matching the header positions
    row_len = max(len(headers), max(col_map.values()) + 1 if col_map else len(DEFAULT_HEADERS))
    row_data = [""] * row_len

    if col_map["project"] >= 0:
        row_data[col_map["project"]] = project
    if col_map["title"] >= 0:
        row_data[col_map["title"]] = title
    if col_map["status"] >= 0:
        row_data[col_map["status"]] = status
    if col_map["url"] >= 0:
        row_data[col_map["url"]] = url
    if col_map["publish_date"] >= 0:
        row_data[col_map["publish_date"]] = publish_date
    if col_map["platform"] >= 0:
        row_data[col_map["platform"]] = platform
    if col_map["description"] >= 0:
        row_data[col_map["description"]] = description

    append_result = service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"{sheet_name}!A:A",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row_data]}
    ).execute()

    # Determine updated row index from updatedRange e.g. "Pipeline!A10:F10"
    updated_range = append_result.get("updates", {}).get("updatedRange", "")
    new_row_index = -1
    if "!" in updated_range:
        range_part = updated_range.split("!")[1]
        import re
        m = re.search(r'(\d+)', range_part)
        if m:
            new_row_index = int(m.group(1))

    return CalendarRecord(
        row_index=new_row_index,
        project=project,
        title=title,
        status=status,
        url=url,
        publish_date=publish_date,
        platform=platform,
        description=description,
    )


def remove_record(
    row_index: Optional[int] = None,
    title: Optional[str] = None,
    project: Optional[str] = None,
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = DEFAULT_SHEET_NAME,
    service=None
) -> bool:
    """
    Deletes a row from the Google Sheet either by exact row index, project name, or title.
    Returns True if successfully deleted.
    """
    sheet_id = get_spreadsheet_id(spreadsheet_id)
    if service is None:
        service = get_sheets_service()

    if row_index is None and title is None and project is None:
        raise ValueError("Must provide row_index, title, or project to remove_record.")

    if row_index is None:
        records = list_records(spreadsheet_id=sheet_id, sheet_name=sheet_name, service=service)
        target = None

        if project is not None:
            clean_proj = project.strip().lower()
            for r in records:
                if r.project.strip().lower() == clean_proj:
                    target = r
                    break

        if not target and title is not None:
            clean_t = title.strip().lower()
            for r in records:
                if r.title.strip().lower() == clean_t:
                    target = r
                    break
            if not target:
                for r in records:
                    if clean_t in r.title.strip().lower():
                        target = r
                        break

        if not target:
            identifier = project or title
            raise ValueError(f"No calendar record found matching: '{identifier}'")
        row_index = target.row_index

    if row_index < 2:
        raise ValueError(f"Cannot delete row {row_index} (row 1 is header row).")

    tab_props = get_sheet_tab_info(service, sheet_id, sheet_name)
    tab_id = tab_props.get("sheetId", 0)

    request_body = {
        "requests": [
            {
                "deleteDimension": {
                    "range": {
                        "sheetId": tab_id,
                        "dimension": "ROWS",
                        "startIndex": row_index - 1,
                        "endIndex": row_index,
                    }
                }
            }
        ]
    }

    service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body=request_body
    ).execute()

    return True


def update_record(
    row_index: int,
    updates: Dict[str, str],
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = DEFAULT_SHEET_NAME,
    service=None
) -> bool:
    """
    Updates specific fields for a given row in the Google Sheet.
    `updates` dict keys can be: project, title, status, url, publish_date, platform, description.
    """
    if row_index < 2:
        raise ValueError(f"Invalid row_index {row_index} for update.")

    sheet_id = get_spreadsheet_id(spreadsheet_id)
    if service is None:
        service = get_sheets_service()

    headers = ensure_sheet_headers(service, sheet_id, sheet_name)
    col_map = parse_header_mapping(headers)

    # Read current row values
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{sheet_name}!A{row_index}:Z{row_index}"
    ).execute()
    current_values = result.get("values", [[]])[0]
    while len(current_values) < len(headers):
        current_values.append("")

    for k, v in updates.items():
        k_norm = k.strip().lower()
        if k_norm in col_map and col_map[k_norm] >= 0:
            target_col = col_map[k_norm]
            while len(current_values) <= target_col:
                current_values.append("")
            current_values[target_col] = str(v)

    service.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range=f"{sheet_name}!A{row_index}:{row_index}",
        valueInputOption="USER_ENTERED",
        body={"values": [current_values]}
    ).execute()

    return True


def sync_projects_to_sheet(
    projects: List[Any],
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = DEFAULT_SHEET_NAME,
    service=None
) -> Dict[str, int]:
    """
    Synchronizes local video projects with the Google Sheet Content Calendar (one-way sync).
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
                updated_date = p.scheduled_date or ""

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
                row_data = [""] * len(headers)
                if col_map["project"] >= 0: row_data[col_map["project"]] = matched_proj.name
                if col_map["title"] >= 0: row_data[col_map["title"]] = matched_proj.title or ""
                if col_map["status"] >= 0: row_data[col_map["status"]] = matched_proj.stage_name
                if col_map["url"] >= 0: row_data[col_map["url"]] = matched_proj.yt_url or ""
                if col_map["publish_date"] >= 0: row_data[col_map["publish_date"]] = matched_proj.scheduled_date or ""
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
            row_data = [""] * len(headers)
            if col_map["project"] >= 0: row_data[col_map["project"]] = p.name
            if col_map["title"] >= 0: row_data[col_map["title"]] = p.title or ""
            if col_map["status"] >= 0: row_data[col_map["status"]] = p.stage_name
            if col_map["url"] >= 0: row_data[col_map["url"]] = p.yt_url or ""
            if col_map["publish_date"] >= 0: row_data[col_map["publish_date"]] = p.scheduled_date or ""
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

