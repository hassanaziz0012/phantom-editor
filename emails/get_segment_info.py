#!/usr/bin/env python3
"""
Script to list all contacts and segments in Resend account.
"""

import os
from dotenv import load_dotenv
import resend

load_dotenv()

resend.api_key = os.getenv("RESEND_API_KEY")

if not resend.api_key:
    raise ValueError("RESEND_API_KEY environment variable is not set.")

# Fetch all contacts across the account
contacts_resp = resend.Contacts.list()
contacts = (
    contacts_resp.get("data", [])
    if isinstance(contacts_resp, dict)
    else getattr(contacts_resp, "data", [])
)

print("=" * 50)
print(f"ALL CONTACTS (Total: {len(contacts)})")
print("=" * 50)
for c in contacts:
    email = c.get("email") if isinstance(c, dict) else getattr(c, "email")
    unsub = c.get("unsubscribed") if isinstance(c, dict) else getattr(c, "unsubscribed")
    created_at = c.get("created_at") if isinstance(c, dict) else getattr(c, "created_at")
    print(f"- Email: {email} | Unsubscribed: {unsub} | Created: {created_at}")

print("\n" + "=" * 50)
print("SEGMENTS BREAKDOWN")
print("=" * 50)

# Retrieve all segments
try:
    segments_resp = resend.Segments.list()
    segments = (
        segments_resp.get("data", [])
        if isinstance(segments_resp, dict)
        else getattr(segments_resp, "data", [])
    )
    for seg in segments:
        seg_id = seg.get("id") if isinstance(seg, dict) else getattr(seg, "id")
        seg_name = seg.get("name") if isinstance(seg, dict) else getattr(seg, "name")
        seg_contacts_resp = resend.Contacts.list(segment_id=seg_id)
        seg_contacts = (
            seg_contacts_resp.get("data", [])
            if isinstance(seg_contacts_resp, dict)
            else getattr(seg_contacts_resp, "data", [])
        )
        print(f"Segment Name: {seg_name} | ID: {seg_id} | Contacts: {len(seg_contacts)}")
except Exception as e:
    print(f"Segments info unavailable: {e}")
