#!/usr/bin/env python3
"""
Script to sync all contacts in Resend account into a target segment (defaults to 'General').
"""

import os
import sys
import argparse
from dotenv import load_dotenv
import resend

load_dotenv()

# ANSI escape codes
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"


def get_segment_id_by_name(name: str) -> str | None:
    """Look up a segment ID by its name (case-insensitive)."""
    try:
        segments_resp = resend.Segments.list()
        segments = (
            segments_resp.get("data", [])
            if isinstance(segments_resp, dict)
            else getattr(segments_resp, "data", [])
        )
        for seg in segments:
            seg_name = seg.get("name") if isinstance(seg, dict) else getattr(seg, "name", "")
            seg_id = seg.get("id") if isinstance(seg, dict) else getattr(seg, "id", "")
            if seg_name.strip().lower() == name.strip().lower():
                return seg_id
    except Exception as e:
        print(f"{RED}Error fetching segments: {e}{RESET}", file=sys.stderr)
    return None


def sync_contacts_to_segment(segment_id: str):
    """Add all global contacts to the specified segment ID."""
    print("Fetching all global contacts...")
    contacts_resp = resend.Contacts.list()
    contacts = (
        contacts_resp.get("data", [])
        if isinstance(contacts_resp, dict)
        else getattr(contacts_resp, "data", [])
    )

    if not contacts:
        print("No contacts found in Resend account.")
        return

    print(f"Found {len(contacts)} contacts. Adding to Segment ID: {segment_id}...\n")
    success_count = 0

    for c in contacts:
        email = c.get("email") if isinstance(c, dict) else getattr(c, "email")
        first_name = c.get("first_name") if isinstance(c, dict) else getattr(c, "first_name", None)
        last_name = c.get("last_name") if isinstance(c, dict) else getattr(c, "last_name", None)
        unsubscribed = c.get("unsubscribed") if isinstance(c, dict) else getattr(c, "unsubscribed", False)

        # Note: Resend contacts API accepts audience_id / segment_id
        params = {
            "email": email,
            "audience_id": segment_id,
            "unsubscribed": unsubscribed,
        }
        if first_name:
            params["first_name"] = first_name
        if last_name:
            params["last_name"] = last_name

        try:
            resend.Contacts.create(params)
            print(f"[{GREEN}ADDED{RESET}] {email}")
            success_count += 1
        except Exception as err:
            print(f"[{RED}FAILED{RESET}] {email}: {err}")

    print(f"\n{GREEN}Successfully processed {success_count}/{len(contacts)} contacts into segment {segment_id}.{RESET}")


def main():
    parser = argparse.ArgumentParser(
        description="Sync all account contacts to a Resend segment."
    )
    parser.add_argument(
        "--segment-name",
        "-n",
        default="General",
        help="Name of the segment (default: 'General')",
    )
    parser.add_argument(
        "--segment-id",
        "-s",
        default=None,
        help="Direct segment ID (overrides --segment-name)",
    )

    args = parser.parse_args()

    api_key = os.getenv("RESEND_API_KEY")
    if not api_key:
        print(f"{RED}Error: RESEND_API_KEY environment variable is not set.{RESET}", file=sys.stderr)
        sys.exit(1)

    resend.api_key = api_key

    segment_id = args.segment_id
    if not segment_id:
        segment_id = get_segment_id_by_name(args.segment_name)
        if not segment_id:
            print(
                f"{RED}Error: Could not find segment with name '{args.segment_name}'.{RESET}",
                file=sys.stderr,
            )
            sys.exit(1)

    sync_contacts_to_segment(segment_id)


if __name__ == "__main__":
    main()
