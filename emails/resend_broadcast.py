#!/usr/bin/env python3
"""
Script to send a YouTube video promotion broadcast to a segment using Resend.
"""

import os
import sys
import argparse
import re
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from dotenv import load_dotenv
import resend

# Load environment variables from .env if present
load_dotenv()

# ANSI escape codes for output styling
GREEN = "\033[92m"
RED = "\033[91m"
RESET = "\033[0m"

# Determine paths
SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = SCRIPT_DIR / "templates"
DEFAULT_TEMPLATE_PATH = TEMPLATES_DIR / "youtube_promotion.html"


def extract_youtube_id(url: str) -> str:
    """Extract YouTube 11-character video ID from a URL or raw ID string."""
    if not url:
        return ""
    url = url.strip()

    parsed = urlparse(url)
    if parsed.hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            return qs["v"][0]
        parts = [p for p in parsed.path.split("/") if p]
        if parts and parts[0] in ("embed", "v", "shorts") and len(parts) > 1:
            return parts[1]
    elif parsed.hostname == "youtu.be":
        parts = [p for p in parsed.path.split("/") if p]
        if parts:
            return parts[0]

    match = re.search(r"(?:v=|\/|embed\/|shorts\/|youtu\.be\/)([a-zA-Z0-9_-]{11})", url)
    if match:
        return match.group(1)

    if len(url) == 11 and re.match(r"^[a-zA-Z0-9_-]{11}$", url):
        return url

    return ""


def load_and_render_template(
    template_path: Path,
    video_title: str,
    video_description: str,
    video_url: str,
) -> str:
    """Load HTML template and substitute video details."""
    if not template_path.exists():
        raise FileNotFoundError(f"Template file not found at: {template_path}")

    with open(template_path, "r", encoding="utf-8") as f:
        content = f.read()

    video_id = extract_youtube_id(video_url)
    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg" if video_id else ""

    # Perform string replacements for placeholders
    replacements = {
        "${videoTitle}": video_title,
        "{videoTitle}": video_title,
        "${videoDescription}": video_description,
        "{videoDescription}": video_description,
        "${videoUrl}": video_url,
        "{videoUrl}": video_url,
        "${videoId}": video_id,
        "{videoId}": video_id,
        "${thumbnailUrl}": thumbnail_url,
        "{thumbnailUrl}": thumbnail_url,
        "${videoThumbnail}": thumbnail_url,
        "{videoThumbnail}": thumbnail_url,
    }

    for key, value in replacements.items():
        content = content.replace(key, value)

    return content


def get_default_segment_id(api_key: str) -> str:
    """Fetch the default or first segment ID from Resend using the official SDK."""
    resend.api_key = api_key
    try:
        segments = resend.Segments.list()
        data = segments.get("data", []) if isinstance(segments, dict) else getattr(segments, "data", [])
        if data:
            first = data[0]
            seg_id = first.get("id") if isinstance(first, dict) else getattr(first, "id", None)
            if seg_id:
                return seg_id
    except Exception as e:
        raise ValueError(
            f"Could not automatically retrieve Segment ID from Resend API: {e}"
        ) from e

    raise ValueError(
        "No Segments found in your Resend account. Please create a Segment at https://resend.com/segments"
    )


def send_broadcast(
    subject: str,
    html_content: str,
    name: str | None = None,
    api_key: str | None = None,
    segment_id: str | None = None,
    from_email: str | None = None,
):
    """Send a broadcast using the official Resend Python SDK."""
    api_key = api_key or os.getenv("RESEND_API_KEY")
    segment_id = segment_id or os.getenv("RESEND_SEGMENT_ID") or os.getenv("RESEND_AUDIENCE_ID")
    from_email = from_email or os.getenv("RESEND_FROM_EMAIL")

    if not api_key:
        raise ValueError("Missing RESEND_API_KEY environment variable.")
    if not segment_id:
        print("RESEND_SEGMENT_ID not specified in .env. Automatically fetching default segment from Resend...")
        segment_id = get_default_segment_id(api_key)
        print(f"Using Segment ID: {segment_id}")
    if not from_email:
        raise ValueError(
            "Missing RESEND_FROM_EMAIL environment variable (e.g., 'Hassan <newsletter@yourdomain.com>')."
        )

    resend.api_key = api_key

    params: resend.Broadcasts.CreateParams = {
        "name": name or subject,
        "segment_id": segment_id,
        "from": from_email,
        "subject": subject,
        "html": html_content,
    }

    broadcast = resend.Broadcasts.create(params)
    broadcast_id = broadcast.get("id") if isinstance(broadcast, dict) else getattr(broadcast, "id", None)

    if broadcast_id:
        send_params: resend.Broadcasts.SendParams = {
            "broadcast_id": broadcast_id,
        }
        resend.Broadcasts.send(send_params)

    return broadcast


def main():
    parser = argparse.ArgumentParser(
        description="Send a YouTube video promotion broadcast via Resend."
    )
    parser.add_argument(
        "--title",
        "-t",
        required=True,
        help="Title of the YouTube video",
    )
    parser.add_argument(
        "--description",
        "-d",
        required=True,
        help="Description / summary of the YouTube video",
    )
    parser.add_argument(
        "--url",
        "-u",
        required=True,
        help="URL link to watch on YouTube",
    )
    parser.add_argument(
        "--template",
        type=Path,
        default=DEFAULT_TEMPLATE_PATH,
        help="Path to HTML template file (default: emails/templates/youtube_promotion.html)",
    )
    parser.add_argument(
        "--segment-id",
        "--audience-id",
        "-s",
        "-a",
        dest="segment_id",
        type=str,
        default=None,
        help="Optional Resend segment ID (defaults to RESEND_SEGMENT_ID env var or default segment)",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Send broadcast to tester segment (RESEND_TESTER_SEGMENT_ID) instead of main segment",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Render template and print details without sending email",
    )

    args = parser.parse_args()

    subject = args.title
    try:
        html_content = load_and_render_template(
            template_path=args.template,
            video_title=args.title,
            video_description=args.description,
            video_url=args.url,
        )
    except Exception as err:
        print(f"{RED}Error loading template: {err}{RESET}", file=sys.stderr)
        sys.exit(1)

    print("=" * 50)
    print(f"Subject: {subject}")
    print("=" * 50)
    print("HTML Preview:\n")
    print(html_content)
    print("=" * 50)

    if args.test:
        target_segment_id = os.getenv("RESEND_TESTER_SEGMENT_ID") or os.getenv("RESEND_TESTER_AUDIENCE_ID")
        if not target_segment_id:
            print(
                f"{RED}Error: Missing RESEND_TESTER_SEGMENT_ID (or RESEND_TESTER_AUDIENCE_ID) environment variable.{RESET}",
                file=sys.stderr,
            )
            sys.exit(1)
    else:
        target_segment_id = args.segment_id

    if args.dry_run:
        if args.test:
            print(f"[DRY RUN] Test broadcast to segment ({target_segment_id}) not sent.")
        else:
            print("[DRY RUN] Broadcast not sent.")
        return

    if args.test:
        print(f"Sending test broadcast to segment ({target_segment_id}) via Resend...")
    else:
        print("Sending broadcast via Resend...")

    try:
        result = send_broadcast(
            subject=subject,
            name=subject,
            html_content=html_content,
            segment_id=target_segment_id,
        )
        if args.test:
            print(f"{GREEN}Test broadcast sent successfully!{RESET}")
        else:
            print(f"{GREEN}Broadcast sent successfully!{RESET}")
        print(f"Response: {result}")
    except Exception as err:
        if args.test:
            print(f"{RED}Error sending test broadcast: {err}{RESET}", file=sys.stderr)
        else:
            print(f"{RED}Error sending broadcast: {err}{RESET}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

