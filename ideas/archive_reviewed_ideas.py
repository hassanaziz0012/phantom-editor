#!/usr/bin/env python3
"""
Archive Reviewed Ideas
======================
Moves every row of the 'Ideas' tab that has a Decision (Yes / No / Maybe) to the
'Ideas Archive' tab, stamped with the review date, so the Ideas tab only holds
ideas that still need reviewing. The archive keeps the decisions and reasons,
which serve as labelled examples of what ideas get picked.

Usage:
    phantom ideas archive [--dry-run] [--sheet-id <id>]

The Ideas tab is rewritten in place (remaining rows moved up, blank rows dropped)
rather than having rows deleted, so its dropdowns stay on every row.
"""

from __future__ import annotations

import argparse
from datetime import date
from typing import List


from ideas.export_ideas_to_sheet import IDEAS_SHEET_NAME, column_letter, ensure_ideas_sheet_headers
from projects.google_sheet_utils import get_sheets_service, get_spreadsheet_id

ARCHIVE_SHEET_NAME = "Ideas Archive"


def ensure_archive_sheet(service, spreadsheet_id: str, headers: List[str]) -> None:
    """Creates the archive tab with the Ideas headers plus 'Reviewed' if it doesn't exist."""
    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id, fields="sheets.properties").execute()
    if any(s["properties"]["title"] == ARCHIVE_SHEET_NAME for s in meta["sheets"]):
        return
    service.spreadsheets().batchUpdate(
        spreadsheetId=spreadsheet_id,
        body={"requests": [{"addSheet": {"properties": {"title": ARCHIVE_SHEET_NAME}}}]},
    ).execute()
    service.spreadsheets().values().update(
        spreadsheetId=spreadsheet_id,
        range=f"'{ARCHIVE_SHEET_NAME}'!A1",
        valueInputOption="USER_ENTERED",
        body={"values": [headers + ["Reviewed"]]},
    ).execute()


def archive_reviewed_ideas(spreadsheet_id: str = None, dry_run: bool = False) -> dict:
    sheet_id = get_spreadsheet_id(spreadsheet_id)
    service = get_sheets_service()

    headers = ensure_ideas_sheet_headers(service, sheet_id, IDEAS_SHEET_NAME)
    decision_idx = next(i for i, h in enumerate(headers) if "decision" in h.lower())
    last_col = column_letter(len(headers) - 1)

    rows = service.spreadsheets().values().get(
        spreadsheetId=sheet_id,
        range=f"{IDEAS_SHEET_NAME}!A2:{last_col}",
    ).execute().get("values", [])
    rows = [r + [""] * (len(headers) - len(r)) for r in rows]

    reviewed = [r for r in rows if r[decision_idx].strip()]
    remaining = [r for r in rows if not r[decision_idx].strip() and any(c.strip() for c in r)]

    summary = {"archived": len(reviewed), "remaining": len(remaining)}
    if dry_run or not reviewed:
        return summary

    # Archive first: if the rewrite below fails, rows are duplicated rather than lost.
    ensure_archive_sheet(service, sheet_id, headers)
    today = date.today().isoformat()
    service.spreadsheets().values().append(
        spreadsheetId=sheet_id,
        range=f"'{ARCHIVE_SHEET_NAME}'!A:{column_letter(len(headers))}",
        valueInputOption="USER_ENTERED",
        insertDataOption="INSERT_ROWS",
        body={"values": [r + [today] for r in reviewed]},
    ).execute()

    service.spreadsheets().values().clear(
        spreadsheetId=sheet_id,
        range=f"{IDEAS_SHEET_NAME}!A2:{last_col}",
    ).execute()
    if remaining:
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=f"{IDEAS_SHEET_NAME}!A2",
            valueInputOption="USER_ENTERED",
            body={"values": remaining},
        ).execute()

    return summary


def main():
    parser = argparse.ArgumentParser(
        description=f"Move reviewed ideas (rows with a Decision) from '{IDEAS_SHEET_NAME}' to '{ARCHIVE_SHEET_NAME}'."
    )
    parser.add_argument("--dry-run", action="store_true", help="Only report what would be moved.")
    parser.add_argument(
        "--sheet-id",
        type=str,
        default=None,
        help="Google Sheets ID (defaults to CONTENT_CALENDAR_SHEET_ID in .env).",
    )
    args = parser.parse_args()

    res = archive_reviewed_ideas(spreadsheet_id=args.sheet_id, dry_run=args.dry_run)
    if args.dry_run:
        print(f"Would archive {res['archived']} reviewed idea(s); {res['remaining']} would remain in '{IDEAS_SHEET_NAME}'.")
    elif res["archived"]:
        print(f"✓ Archived {res['archived']} reviewed idea(s) to '{ARCHIVE_SHEET_NAME}'. {res['remaining']} left to review.")
    else:
        print(f"No reviewed ideas to archive. {res['remaining']} left to review.")


if __name__ == "__main__":
    main()
