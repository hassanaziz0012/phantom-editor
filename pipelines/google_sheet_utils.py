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
DEFAULT_SHEET_NAME = "Sheet1"
DEFAULT_HEADERS = ["Title", "Description", "URL", "Publish Date", "Platform"]

TOKEN_DIR = Path(__file__).resolve().parent / "tokens"
TOKEN_FILE = TOKEN_DIR / "sheets_token.json"


@dataclass
class CalendarRecord:
    row_index: int  # 1-based row index in Google Sheet
    title: str = ""
    description: str = ""
    url: str = ""
    publish_date: str = ""
    platform: str = "YouTube"
    status: str = "Scheduled"
    extra_fields: Dict[str, Any] = field(default_factory=dict)

    def to_row_values(self, headers: Optional[List[str]] = None) -> List[str]:
        if not headers:
            headers = DEFAULT_HEADERS

        values = []
        for h in headers:
            h_norm = h.strip().lower()
            if "title" in h_norm:
                values.append(self.title)
            elif "desc" in h_norm:
                values.append(self.description)
            elif "url" in h_norm or "link" in h_norm:
                values.append(self.url)
            elif "date" in h_norm:
                values.append(self.publish_date)
            elif "plat" in h_norm:
                values.append(self.platform)
            elif "stat" in h_norm:
                values.append(self.status)
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
        "title": -1,
        "description": -1,
        "url": -1,
        "publish_date": -1,
        "platform": -1,
        "status": -1,
    }
    for idx, h in enumerate(headers):
        h_norm = h.strip().lower()
        if "title" in h_norm and mapping["title"] == -1:
            mapping["title"] = idx
        elif ("desc" in h_norm or "details" in h_norm) and mapping["description"] == -1:
            mapping["description"] = idx
        elif ("url" in h_norm or "link" in h_norm) and mapping["url"] == -1:
            mapping["url"] = idx
        elif ("date" in h_norm or "time" in h_norm or "publish" in h_norm) and mapping["publish_date"] == -1:
            mapping["publish_date"] = idx
        elif ("plat" in h_norm or "channel" in h_norm or "target" in h_norm) and mapping["platform"] == -1:
            mapping["platform"] = idx
        elif ("stat" in h_norm or "state" in h_norm) and mapping["status"] == -1:
            mapping["status"] = idx

    # If headers are missing or not mapped, fall back to index-based defaults
    if mapping["title"] == -1 and len(headers) > 0:
        mapping["title"] = 0
    if mapping["description"] == -1 and len(headers) > 1:
        mapping["description"] = 1
    if mapping["url"] == -1 and len(headers) > 2 and ("url" in headers[2].lower() or "link" in headers[2].lower()):
        mapping["url"] = 2
    if mapping["publish_date"] == -1 and len(headers) > 3:
        mapping["publish_date"] = 3
    if mapping["platform"] == -1 and len(headers) > 4:
        mapping["platform"] = 4
    if mapping["status"] == -1 and len(headers) > 5:
        mapping["status"] = 5

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

        title = get_col(col_map["title"])
        description = get_col(col_map["description"])
        url = get_col(col_map["url"])
        publish_date = get_col(col_map["publish_date"])
        platform = get_col(col_map["platform"]) or "YouTube"
        status = get_col(col_map["status"]) or "Scheduled"

        extra = {}
        for c_idx, h_name in enumerate(headers):
            if c_idx not in col_map.values() and c_idx < len(row):
                extra[h_name] = str(row[c_idx]).strip()

        records.append(
            CalendarRecord(
                row_index=idx,
                title=title,
                description=description,
                url=url,
                publish_date=publish_date,
                platform=platform,
                status=status,
                extra_fields=extra,
            )
        )

    return records


def add_record(
    title: str,
    description: str = "",
    url: str = "",
    publish_date: str = "",
    platform: str = "YouTube",
    status: str = "Scheduled",
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

    if col_map["title"] >= 0:
        row_data[col_map["title"]] = title
    if col_map["description"] >= 0:
        row_data[col_map["description"]] = description
    if col_map["url"] >= 0:
        row_data[col_map["url"]] = url
    if col_map["publish_date"] >= 0:
        row_data[col_map["publish_date"]] = publish_date
    if col_map["platform"] >= 0:
        row_data[col_map["platform"]] = platform
    if col_map["status"] >= 0:
        row_data[col_map["status"]] = status

    append_result = service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"{sheet_name}!A:A",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [row_data]}
    ).execute()

    # Determine updated row index from updatedRange e.g. "Sheet1!A10:E10"
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
        title=title,
        description=description,
        url=url,
        publish_date=publish_date,
        platform=platform,
        status=status,
    )


def remove_record(
    row_index: Optional[int] = None,
    title: Optional[str] = None,
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = DEFAULT_SHEET_NAME,
    service=None
) -> bool:
    """
    Deletes a row from the Google Sheet either by exact row index or matching title.
    Returns True if successfully deleted.
    """
    sheet_id = get_spreadsheet_id(spreadsheet_id)
    if service is None:
        service = get_sheets_service()

    if row_index is None and title is None:
        raise ValueError("Must provide either row_index or title to remove_record.")

    # If title is provided, find corresponding row index
    if row_index is None and title is not None:
        records = list_records(spreadsheet_id=sheet_id, sheet_name=sheet_name, service=service)
        target = None
        for r in records:
            if r.title.strip().lower() == title.strip().lower():
                target = r
                break
        if not target:
            # Try substring match
            for r in records:
                if title.strip().lower() in r.title.strip().lower():
                    target = r
                    break
        if not target:
            raise ValueError(f"No calendar record found matching title: '{title}'")
        row_index = target.row_index

    if row_index < 2:
        raise ValueError(f"Cannot delete row {row_index} (row 1 is header row).")

    tab_props = get_sheet_tab_info(service, sheet_id, sheet_name)
    tab_id = tab_props.get("sheetId", 0)

    # Google Sheets API deleteDimension is 0-indexed [startIndex, endIndex)
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
    `updates` dict keys can be: title, description, publish_date, platform, status.
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
