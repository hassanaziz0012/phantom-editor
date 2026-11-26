#!/usr/bin/env python3
"""
Script to send a YouTube video promotion broadcast to a segment using Resend.
"""

import os
import sys
import argparse
import html
from pathlib import Path
import resend

from emails.resend_response import resend_field
from shared import colors
from youtube_api.video_id import video_id_or_none


# Determine paths
SCRIPT_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = SCRIPT_DIR / "templates"
DEFAULT_TEMPLATE_PATH = TEMPLATES_DIR / "youtube_promotion.html"


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

    video_id = video_id_or_none(video_url) or ""
    thumbnail_url = f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg" if video_id else ""

    # Escape every substituted value (text and attribute contexts) so characters
    # like & < > " in a title cannot break the HTML.
    video_title = html.escape(video_title, quote=True)
    video_description = html.escape(video_description, quote=True)
    video_url = html.escape(video_url, quote=True)
    video_id = html.escape(video_id, quote=True)
    thumbnail_url = html.escape(thumbnail_url, quote=True)

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
    segment_id = segment_id or os.getenv("RESEND_GENERAL_AUDIENCE_ID")
    from_email = from_email or os.getenv("RESEND_FROM_EMAIL")

    if not api_key:
        raise ValueError("Missing RESEND_API_KEY environment variable.")
    if not segment_id:
        raise ValueError(
            "No Resend segment specified. Set RESEND_GENERAL_AUDIENCE_ID or pass --segment-id; "
            "refusing to guess an audience."
        )
    if not from_email:
        raise ValueError(
            "Missing RESEND_FROM_EMAIL environment variable (e.g., 'Hassan <newsletter@yourdomain.com>')."
        )

    resend.api_key = api_key

    # Resend restricts `name` to a maximum of 70 characters
    broadcast_name = (name or subject or "").strip()[:70]

    params: resend.Broadcasts.CreateParams = {
        "name": broadcast_name,
        "segment_id": segment_id,
        "from": from_email,
        "subject": subject,
        "html": html_content,
    }

    broadcast = resend.Broadcasts.create(params)
    broadcast_id = resend_field(broadcast, "id")

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
        "--name",
        "-n",
        default=None,
        help="Optional internal name for the broadcast (defaults to title truncated to 70 chars)",
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
        help="Optional Resend segment ID (defaults to RESEND_GENERAL_AUDIENCE_ID env var; sending is refused if neither is set)",
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
        print(f"{colors.RED}Error loading template: {err}{colors.RESET}", file=sys.stderr)
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
                f"{colors.RED}Error: Missing RESEND_TESTER_SEGMENT_ID (or RESEND_TESTER_AUDIENCE_ID) environment variable.{colors.RESET}",
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
            name=args.name or subject,
            html_content=html_content,
            segment_id=target_segment_id,
        )
        if args.test:
            print(f"{colors.GREEN}Test broadcast sent successfully!{colors.RESET}")
        else:
            print(f"{colors.GREEN}Broadcast sent successfully!{colors.RESET}")
        print(f"Response: {result}")
    except Exception as err:
        if args.test:
            print(f"{colors.RED}Error sending test broadcast: {err}{colors.RESET}", file=sys.stderr)
        else:
            print(f"{colors.RED}Error sending broadcast: {err}{colors.RESET}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

