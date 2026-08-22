#!/usr/bin/env python3
"""
Export Ideas to Google Sheets
=============================
Utility functions to export generated content ideas to the 'Ideas' tab in the
Google Sheets Content Calendar (configured via CONTENT_CALENDAR_SHEET_ID).

Sheet Columns:
  - Idea: Explanation of the video/content idea
  - Source: Source comment or reference text along with URL
  - Source Type: e.g. "YT Comments", "Reddit", "Twitter", etc.
  - Confidence Score: Optional rating (e.g. 0-10)
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

# Ensure repository root is in sys.path
repo_root = Path(__file__).resolve().parent.parent
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from dotenv import load_dotenv

load_dotenv(repo_root / ".env")

from pipelines.google_sheet_utils import get_sheets_service, get_spreadsheet_id

logger = logging.getLogger("phantom.ideas.export")

IDEAS_SHEET_NAME = "Ideas"
DEFAULT_IDEAS_HEADERS = ["Idea", "Source", "Source Type", "Confidence Score"]


@dataclass
class ContentIdea:
    idea: str
    source: str = ""
    source_type: str = "YT Comments"
    confidence_score: Union[str, int, float] = ""

    def to_row(self, headers: Optional[List[str]] = None) -> List[str]:
        if not headers:
            headers = DEFAULT_IDEAS_HEADERS

        row: List[str] = []
        for h in headers:
            h_norm = h.strip().lower()
            if "idea" in h_norm or "title" in h_norm:
                row.append(str(self.idea or "").strip())
            elif "source type" in h_norm or "type" in h_norm:
                row.append(str(self.source_type or "YT Comments").strip())
            elif "source" in h_norm:
                row.append(str(self.source or "").strip())
            elif "confidence" in h_norm or "score" in h_norm:
                row.append(str(self.confidence_score if self.confidence_score is not None else "").strip())
            else:
                row.append("")
        return row


def ensure_ideas_sheet_headers(
    service=None,
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = IDEAS_SHEET_NAME,
) -> List[str]:
    """
    Ensures that the Ideas sheet exists and has the proper header row.
    Returns the header list.
    """
    sheet_id = get_spreadsheet_id(spreadsheet_id)
    if service is None:
        service = get_sheets_service()

    # Check if header row exists
    result = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{sheet_name}!A1:D1"
    ).execute()
    rows = result.get("values", [])

    if not rows or not rows[0] or not any(str(c).strip() for c in rows[0]):
        # Write default headers
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"{sheet_name}!A1",
            valueInputOption="USER_ENTERED",
            body={"values": [DEFAULT_IDEAS_HEADERS]}
        ).execute()
        return DEFAULT_IDEAS_HEADERS

    return [str(c).strip() for c in rows[0]]


def append_idea(
    idea: str,
    source: str = "",
    source_type: str = "YT Comments",
    confidence_score: Union[str, int, float] = "",
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = IDEAS_SHEET_NAME,
    service=None,
) -> Dict[str, Any]:
    """
    Appends a single content idea to the Google Sheet.
    """
    content_idea = ContentIdea(
        idea=idea,
        source=source,
        source_type=source_type,
        confidence_score=confidence_score,
    )
    return export_ideas_to_sheet(
        ideas=[content_idea],
        spreadsheet_id=spreadsheet_id,
        sheet_name=sheet_name,
        service=service,
    )


def export_ideas_to_sheet(
    ideas: List[Union[Dict[str, Any], ContentIdea]],
    spreadsheet_id: Optional[str] = None,
    sheet_name: str = IDEAS_SHEET_NAME,
    service=None,
) -> Dict[str, Any]:
    """
    Appends a list of content ideas to the 'Ideas' sheet in Google Sheets.
    Does NOT delete or overwrite any existing records.

    :param ideas: List of ContentIdea objects or dicts containing 'idea', 'source', etc.
    :param spreadsheet_id: Optional Google Spreadsheet ID (defaults to env CONTENT_CALENDAR_SHEET_ID)
    :param sheet_name: Target tab name (default: 'Ideas')
    :param service: Optional pre-authenticated Google Sheets service instance
    :return: Summary dict with count of appended rows and response details
    """
    if not ideas:
        return {"appended_count": 0, "updates": {}}

    sheet_id = get_spreadsheet_id(spreadsheet_id)
    if service is None:
        service = get_sheets_service()

    headers = ensure_ideas_sheet_headers(service, sheet_id, sheet_name)

    # Convert ideas to rows
    rows_to_append: List[List[str]] = []
    for item in ideas:
        if isinstance(item, ContentIdea):
            rows_to_append.append(item.to_row(headers))
        elif isinstance(item, dict):
            ci = ContentIdea(
                idea=item.get("idea", ""),
                source=item.get("source", ""),
                source_type=item.get("source_type", item.get("sourceType", "YT Comments")),
                confidence_score=item.get("confidence_score", item.get("confidenceScore", "")),
            )
            rows_to_append.append(ci.to_row(headers))

    if not rows_to_append:
        return {"appended_count": 0, "updates": {}}

    append_result = service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"{sheet_name}!A:D",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": rows_to_append},
    ).execute()

    return {
        "appended_count": len(rows_to_append),
        "updates": append_result.get("updates", {}),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Export content ideas to the Google Sheets Content Calendar."
    )
    parser.add_argument(
        "--file", "-f",
        type=str,
        help="Path to JSON file containing ideas array (each with idea, source, confidence_score).",
    )
    parser.add_argument(
        "--idea",
        type=str,
        help="Idea text (for quick single idea append).",
    )
    parser.add_argument(
        "--source",
        type=str,
        default="",
        help="Source comment and URL.",
    )
    parser.add_argument(
        "--source-type",
        type=str,
        default="YT Comments",
        help="Source type (default: 'YT Comments').",
    )
    parser.add_argument(
        "--confidence-score",
        type=str,
        default="",
        help="Confidence score (e.g. 0-10).",
    )
    parser.add_argument(
        "--sheet-id",
        type=str,
        default=None,
        help="Google Sheets ID (defaults to CONTENT_CALENDAR_SHEET_ID in .env).",
    )

    args = parser.parse_args()

    ideas: List[ContentIdea] = []
    if args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"Error: File not found: {file_path}", file=sys.stderr)
            sys.exit(1)
        data = json.loads(file_path.read_text(encoding="utf-8"))
        raw_list = data.get("ideas", []) if isinstance(data, dict) else data
        for item in raw_list:
            if isinstance(item, dict):
                ideas.append(
                    ContentIdea(
                        idea=item.get("idea", ""),
                        source=item.get("source", ""),
                        source_type=item.get("source_type", "YT Comments"),
                        confidence_score=item.get("confidence_score", ""),
                    )
                )
    elif args.idea:
        ideas.append(
            ContentIdea(
                idea=args.idea,
                source=args.source,
                source_type=args.source_type,
                confidence_score=args.confidence_score,
            )
        )
    else:
        print("Please provide --file with a JSON file or --idea with an idea string.", file=sys.stderr)
        sys.exit(1)

    print(f"Exporting {len(ideas)} idea(s) to Google Sheet (tab: '{IDEAS_SHEET_NAME}')...")
    res = export_ideas_to_sheet(ideas, spreadsheet_id=args.sheet_id)
    print(f"✓ Successfully appended {res['appended_count']} record(s) to Google Sheets.")


if __name__ == "__main__":
    main()
