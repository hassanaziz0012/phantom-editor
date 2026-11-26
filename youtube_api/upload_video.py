#!/usr/bin/env python3
"""
YouTube Video Uploader
Usage: phantom publish yt /path/to/video.mp4
Expects metadata.json and a thumbnail (optional, e.g. thumbnail.png) in the same folder as the video.

With a publishDate in metadata.json the video is uploaded privately and set to go public on that
date; the tweet, Substack note and email are then left for you to send on the day.
"""

import argparse
import io
import mimetypes
import shlex
import socket
import subprocess
import sys
import time as time_module
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Any, Optional, Union

from shared.commands import python_module
import config
from metadata.read_metadata import DEFAULT_CATEGORY_ID, DEFAULT_TWEET_TEMPLATE, read_metadata, VideoMetadata
from metadata.check_resource_links import blank_resources
from metadata.approve_title import format_candidates, resolve_choice
from projects.current_pipeline import is_thumbnail_file


import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from tqdm import tqdm


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

# "youtube" covers playlist management (phantom yt add-to-playlist, yt upload --playlist).
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]
CLIENT_SECRETS_FILE = Path(__file__).parent / "tokens/client_secret.json"
TOKEN_FILE = Path(__file__).parent / "tokens/token.json"
YOUTUBE_API_SERVICE = "youtube"
YOUTUBE_API_VERSION = "v3"
CHUNK_SIZE = 25 * 1024 * 1024  # 25 MB — balances throughput with progress granularity

# When a publishDate has no time of day, the video goes public at this local time
DEFAULT_PUBLISH_TIME = time(22, 30)

# Thumbnail types the YouTube API takes as they are; other image types Pillow can read are sent as PNG
THUMBNAIL_MAX_BYTES = 2 * 1024 * 1024  # YouTube rejects thumbnails over 2 MB
YOUTUBE_MAX_TITLE_CHARS = 100
YOUTUBE_MAX_DESCRIPTION_BYTES = 5000
UPLOAD_MAX_TRIES = 5
RETRYABLE_HTTP_STATUSES = {500, 502, 503, 504}
THUMBNAIL_MIME_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
# Preferred when a project has several thumbnails (the rest are usually alternatives)
DEFAULT_THUMBNAIL_NAMES = ("thumbnail.png", "thumbnail.jpg", "thumbnail.jpeg")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def get_authenticated_service():
    creds = None

    if TOKEN_FILE.exists():
        try:
            # Load with the token's own scopes so has_scopes() reflects what was actually granted.
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE))
        except Exception as e:
            print(f"⚠️ Error reading token file {TOKEN_FILE}: {e}. Re-authenticating...")

    if creds and not creds.has_scopes(SCOPES):
        print("⚠️ Saved YouTube token is missing required permissions. Re-authenticating...")
        creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as e:
                print(f"⚠️ Failed to refresh token: {e}. Re-running auth flow...")
                creds = None

        if not creds:
            if not CLIENT_SECRETS_FILE.exists():
                raise FileNotFoundError(
                    f"Google client_secret.json credentials file not found at {CLIENT_SECRETS_FILE}.\n"
                    f"Please verify client_secret.json exists."
                )

            TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            flow = InstalledAppFlow.from_client_secrets_file(
                str(CLIENT_SECRETS_FILE), SCOPES
            )
            creds = flow.run_local_server(port=0)

        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())

    return build(YOUTUBE_API_SERVICE, YOUTUBE_API_VERSION, credentials=creds)


# ---------------------------------------------------------------------------
# Scheduling
# ---------------------------------------------------------------------------

def parse_publish_date(value: str) -> datetime:
    """The moment a metadata.json publishDate goes public, as an aware datetime.

    Takes "2026-11-12" (DEFAULT_PUBLISH_TIME), "2026-11-12T17:00" or "2026-11-12 17:00"; a time
    without a UTC offset is local time.
    """
    text = value.strip()
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise ValueError(
            f"publishDate '{value}' in metadata.json isn't a date. "
            "Use YYYY-MM-DD, or YYYY-MM-DDTHH:MM for a specific time."
        ) from None
    if len(text) <= 10:
        moment = datetime.combine(moment.date(), DEFAULT_PUBLISH_TIME)
    return moment if moment.tzinfo else moment.astimezone()


def scheduled_publish_time(metadata: Union[VideoMetadata, dict]) -> Optional[datetime]:
    """When the video should go public, or None to publish it right away.

    Raises ValueError for a publishDate that can't be read or has already passed.
    """
    value = metadata.get("publishDate")
    if not value:
        return None
    publish_at = parse_publish_date(str(value))
    if publish_at <= datetime.now(timezone.utc):
        raise ValueError(
            f"publishDate {publish_at:%Y-%m-%d %H:%M %Z} in metadata.json has already passed.\n"
            "   Set a future date to schedule the video, or remove publishDate to publish it now."
        )
    return publish_at


# ---------------------------------------------------------------------------
# Timestamp & Description Formatting
# ---------------------------------------------------------------------------

def format_timestamps_for_description(raw_timestamps: Any) -> str:
    """Formats timestamps from metadata.json into a multiline string for the YouTube description."""
    if not raw_timestamps:
        return ""

    if isinstance(raw_timestamps, str):
        return raw_timestamps.strip()

    if isinstance(raw_timestamps, (list, tuple, set)):
        lines: list[str] = []
        for item in raw_timestamps:
            if isinstance(item, dict):
                ts = str(item.get("timestamp") or item.get("time") or "").strip()
                topic = str(item.get("topic") or item.get("title") or "").strip()
                if ts and topic:
                    lines.append(f"{ts} {topic}")
                elif ts or topic:
                    lines.append(f"{ts}{topic}".strip())
            elif isinstance(item, str) and item.strip():
                lines.append(item.strip())
        return "\n".join(lines).strip()

    return ""


def format_recommendations_for_description(raw_recommendations: Any) -> str:
    """Formats recommendations from metadata.json into a multiline string of URLs for the YouTube description."""
    if not raw_recommendations:
        return ""

    if isinstance(raw_recommendations, str):
        return raw_recommendations.strip()

    if isinstance(raw_recommendations, (list, tuple, set)):
        items = list(raw_recommendations)
        if all(isinstance(item, dict) for item in items):
            try:
                items.sort(key=lambda x: int(x.get("rank", 999)))
            except (ValueError, TypeError):
                pass

        lines: list[str] = []
        for item in items:
            if isinstance(item, dict):
                url = str(item.get("url") or item.get("link") or "").strip()
                if not url and item.get("video_id"):
                    url = f"https://www.youtube.com/watch?v={item['video_id']}"
                if url:
                    lines.append(url)
                elif (title := str(item.get("title") or item.get("topic") or item.get("name") or "").strip()):
                    lines.append(title)
            elif isinstance(item, str) and item.strip():
                lines.append(item.strip())
        return "\n".join(lines).strip()

    return ""


def format_resources_for_description(raw_resources: Any) -> str:
    """Formats metadata.json "resources" into the description's RESOURCES section ("" if there are none).

    The section ends with a blank line so it can sit right before the next heading in DESCRIPTION_TEMPLATE.
    """
    if not isinstance(raw_resources, list):
        return ""

    lines: list[str] = []
    for item in raw_resources:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        url = str(item.get("url") or "").strip()
        if key and url:
            lines.append(f"{key}: {url}")
        elif url:
            lines.append(url)

    if not lines:
        return ""
    return "RESOURCES\n" + "\n".join(lines) + "\n\n"


def check_youtube_text_limits(title: str, full_description: str) -> None:
    """Raises ValueError if YouTube would refuse the title or the full description."""
    problems = []
    if len(title) > YOUTUBE_MAX_TITLE_CHARS:
        problems.append(
            f"The title is {len(title)} characters; YouTube allows at most {YOUTUBE_MAX_TITLE_CHARS}.\n"
            f"     Title: {title}"
        )
    description_bytes = len(full_description.encode("utf-8"))
    if description_bytes > YOUTUBE_MAX_DESCRIPTION_BYTES:
        problems.append(
            f"The full description (with timestamps, recommendations and resources) is {description_bytes} bytes; "
            f"YouTube allows at most {YOUTUBE_MAX_DESCRIPTION_BYTES}.\n"
            "     Shorten 'description' (or trim timestamps/recommendations/resources) in metadata.json."
        )
    for label, text in (("title", title), ("description", full_description)):
        found = sorted({c for c in text if c in "<>"})
        if found:
            problems.append(
                f"The {label} contains {' and '.join(repr(c) for c in found)}; YouTube does not allow '<' or '>' "
                f"in titles or descriptions.\n     Remove or reword them in metadata.json."
            )
    if problems:
        raise ValueError("Metadata breaks YouTube's rules:\n   - " + "\n   - ".join(problems))


def validate_metadata_for_upload(
    metadata: Union[VideoMetadata, dict],
    require_description: bool = True,
    require_timestamps: bool = True,
    require_recommendations: bool = True,
) -> tuple[str, str, str, str]:
    """
    Validates metadata fields before uploading.
    Ensures 'description', 'timestamps', and 'recommendations' exist and are non-empty if required,
    and that every entry in 'resources' has a URL.
    Returns (raw_description, formatted_timestamps, formatted_recommendations, formatted_resources).
    """
    raw_description = str(metadata.get("description", "") or "").strip()

    if require_description and not raw_description:
        raise ValueError(
            "Missing or empty 'description' in metadata.json.\n"
            "   A video description is required before uploading to YouTube.\n"
            "   Please run `phantom meta desc <target>` or `phantom meta create <target>` to generate it."
        )

    raw_timestamps = metadata.get("timestamps")
    formatted_timestamps = format_timestamps_for_description(raw_timestamps)

    if require_timestamps and not formatted_timestamps:
        raise ValueError(
            "Missing or empty 'timestamps' in metadata.json.\n"
            "   Timestamps/chapters are required before uploading to YouTube.\n"
            "   Please run `phantom meta timestamps <target>` or `phantom meta create <target>` to generate them."
        )

    raw_recommendations = metadata.get("recommendations")
    formatted_recommendations = format_recommendations_for_description(raw_recommendations)

    if require_recommendations and not formatted_recommendations:
        raise ValueError(
            "Missing or empty 'recommendations' in metadata.json.\n"
            "   Video recommendations are required before uploading to YouTube.\n"
            "   Please run `phantom meta recommend <target>` or `phantom meta create <target>` to generate them."
        )

    raw_resources = metadata.get("resources")
    blank = blank_resources(raw_resources)
    if blank:
        raise ValueError(
            "Some 'resources' in metadata.json have no URL:\n"
            + "".join(f"     - {key}\n" for key in blank)
            + "   The video promises to link these. Fill in each one's 'url' in metadata.json\n"
            "   (or delete the entry if you don't want to link it)."
        )
    formatted_resources = format_resources_for_description(raw_resources)

    check_youtube_text_limits(
        str(metadata.get("title", "") or "").strip(),
        build_description(raw_description, formatted_timestamps, formatted_recommendations, formatted_resources),
    )

    return raw_description, formatted_timestamps, formatted_recommendations, formatted_resources


# ---------------------------------------------------------------------------
# Upload helpers
# ---------------------------------------------------------------------------

def build_description(
    raw_description: str,
    formatted_timestamps: str,
    formatted_recommendations: str,
    formatted_resources: str,
) -> str:
    """The full YouTube description: the parts validate_metadata_for_upload returns, put into the template."""
    return config.DESCRIPTION_TEMPLATE.format(
        video_description=raw_description,
        timestamps=formatted_timestamps,
        recommended=formatted_recommendations,
        resources=formatted_resources,
    )


def confirm_title(metadata: VideoMetadata, choice: Optional[str], keep: bool) -> None:
    """Make sure the title was approved before it goes public; saves the choice to metadata.json.

    The pipeline auto-picks the top-scored title and sets "titleReviewed": false. A --title
    choice or --keep-title settles it. Otherwise a terminal asks; without one (e.g. when an
    agent runs the upload) this exits with the candidates so the choice can be passed back in.
    """
    if choice is None and metadata.get("titleReviewed") is not False:
        return
    if choice is None and not keep:
        print("\n🏷️  The title is still the pipeline's auto-pick:")
        print(format_candidates(metadata))
        if not sys.stdin.isatty():
            print(
                "\n❌ Approve a title first: rerun with --title <rank or your own title>, "
                "or --keep-title to upload with the current one.",
                file=sys.stderr,
            )
            sys.exit(1)
        while True:
            answer = input("Rank number, your own title, or Enter to keep the current one: ").strip()
            if not answer:
                break
            try:
                choice = resolve_choice(metadata, answer)
                break
            except ValueError as e:
                print(f"   {e}")
    if choice is not None:
        try:
            metadata["title"] = resolve_choice(metadata, choice)
        except ValueError as e:
            print(f"\n❌ Error: {e}\n", file=sys.stderr)
            sys.exit(1)
    metadata["titleReviewed"] = True
    metadata.save()
    print(f"✅ Title: {metadata.get('title')}")


def is_transient_upload_error(error: Exception) -> bool:
    """Whether a failed upload chunk is worth retrying (5xx, dropped connection, socket/httplib2 trouble)."""
    if isinstance(error, HttpError):
        return error.resp.status in RETRYABLE_HTTP_STATUSES
    return isinstance(error, (ConnectionError, socket.error, httplib2.HttpLib2Error))


def next_chunk_with_retry(request):
    """request.next_chunk() that retries transient errors with exponential backoff.

    Calling next_chunk() again on the same request resumes the same resumable upload (the client asks
    YouTube how much it already has when the previous attempt failed mid-chunk).
    """
    for attempt in range(1, UPLOAD_MAX_TRIES + 1):
        try:
            return request.next_chunk()
        except Exception as e:
            if not is_transient_upload_error(e) or attempt == UPLOAD_MAX_TRIES:
                raise
            delay = 2 ** attempt
            print(f"\n⚠️  Upload hiccup ({type(e).__name__}: {e}); retrying in {delay}s "
                  f"(attempt {attempt + 1}/{UPLOAD_MAX_TRIES})…")
            time_module.sleep(delay)


def upload_video(
    youtube,
    video_path: Path,
    metadata: Union[VideoMetadata, dict],
    full_description: str,
    publish_at: Optional[datetime] = None,
) -> str:
    """Upload the video with ``full_description`` (see build_description) and return its YouTube video ID.

    With ``publish_at`` the video is uploaded private and YouTube makes it public at that moment.
    """

    body = {
        "snippet": {
            "title":       metadata.get("title", video_path.stem),
            "description": full_description,
            "tags":        metadata.get("tags", []),
            "categoryId":  str(metadata.get("categoryId", DEFAULT_CATEGORY_ID)),
        },
        "status": {
            "privacyStatus":           metadata.get("privacyStatus", "public"),
            "selfDeclaredMadeForKids": metadata.get("madeForKids", False),
        },
    }
    if publish_at:
        # YouTube only honours publishAt on private videos
        body["status"]["privacyStatus"] = "private"
        body["status"]["publishAt"] = publish_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    media = MediaFileUpload(
        str(video_path),
        mimetype=mimetypes.guess_type(video_path.name)[0] or "application/octet-stream",
        chunksize=CHUNK_SIZE,
        resumable=True,
    )

    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )

    file_size = video_path.stat().st_size
    uploaded = 0
    response = None

    print(f"\n📤 Uploading: {video_path.name}")
    with tqdm(total=file_size, unit="B", unit_scale=True, unit_divisor=1024,
              desc="Progress", ncols=80) as pbar:
        while response is None:
            status, response = next_chunk_with_retry(request)
            if status:
                new_uploaded = status.resumable_progress
                pbar.update(new_uploaded - uploaded)
                uploaded = new_uploaded

        # Final update to 100 %
        pbar.update(file_size - uploaded)

    video_id = response["id"]
    print(f"✅ Upload complete — video ID: {video_id}")
    return video_id


def is_uploadable_thumbnail(path: Path) -> bool:
    """Whether the thumbnail can be sent to YouTube, as it is or converted to PNG (not .svg)."""
    return path.suffix.lower() in THUMBNAIL_MIME_TYPES or path.suffix.lower() in {".webp", ".gif", ".bmp"}


def find_thumbnail(video_path: Path) -> Optional[Path]:
    """The project's thumbnail (the same thumbnail*.png/.jpg/.jpeg/.webp files `phantom projects
    status` counts), or None once the user agrees to go without one.

    With several, thumbnail.png (or .jpg) wins; otherwise the user picks one. Asked before
    uploading: aborting after the upload would leave a video whose URL never made it into
    metadata.json.
    """
    candidates = sorted(
        f for f in video_path.parent.iterdir()
        if f.is_file() and is_thumbnail_file(f) and is_uploadable_thumbnail(f)
    )
    if len(candidates) == 1:
        return candidates[0]
    for name in DEFAULT_THUMBNAIL_NAMES:
        match = next((f for f in candidates if f.name.lower() == name), None)
        if match:
            return match
    if candidates:
        print("🖼  Several thumbnails found:")
        for idx, f in enumerate(candidates, 1):
            print(f"   {idx}. {f.name}")
        while True:
            answer = input(f"   Which one should YouTube use? [1-{len(candidates)}] ").strip()
            if answer.isdigit() and 1 <= int(answer) <= len(candidates):
                return candidates[int(answer) - 1]

    print("⚠️  No thumbnail (thumbnail*.png, .jpg, .jpeg or .webp) found in the video folder.")
    answer = input("   Proceed without a custom thumbnail? YouTube will auto-select one. [y/N] ").strip().lower()
    if answer != "y":
        print("❌ Aborted. Re-run the script once you've added a thumbnail (e.g. thumbnail.png).")
        sys.exit(1)
    return None


def shrunk_thumbnail(thumbnail_file: Path) -> Path:
    """A JPEG copy of the thumbnail (next to the original) that fits under YouTube's 2 MB limit."""
    from PIL import Image

    target = thumbnail_file.with_name(thumbnail_file.stem + "-under2mb.jpg")
    with Image.open(thumbnail_file) as image:
        rgb = image.convert("RGB")
    scale = 1.0
    while True:
        candidate = rgb if scale == 1.0 else rgb.resize(
            (max(1, int(rgb.width * scale)), max(1, int(rgb.height * scale))), Image.LANCZOS
        )
        for quality in (95, 90, 85, 80, 70):
            candidate.save(target, format="JPEG", quality=quality, optimize=True)
            if target.stat().st_size <= THUMBNAIL_MAX_BYTES:
                print(f"🖼  Thumbnail was over 2 MB; using {target.name} ({target.stat().st_size // 1024} KB).")
                return target
        scale *= 0.85


def thumbnail_media(thumbnail_file: Path):
    """The thumbnail as an upload body: PNG/JPEG as they are (shrunk to JPEG if over 2 MB), other formats converted to PNG."""
    mimetype = THUMBNAIL_MIME_TYPES.get(thumbnail_file.suffix.lower())
    if mimetype:
        if thumbnail_file.stat().st_size > THUMBNAIL_MAX_BYTES:
            thumbnail_file = shrunk_thumbnail(thumbnail_file)
            mimetype = "image/jpeg"
        return MediaFileUpload(str(thumbnail_file), mimetype=mimetype)

    from PIL import Image

    buffer = io.BytesIO()
    with Image.open(thumbnail_file) as image:
        image.convert("RGB").save(buffer, format="PNG")
    if buffer.getbuffer().nbytes > THUMBNAIL_MAX_BYTES:
        return MediaFileUpload(str(shrunk_thumbnail(thumbnail_file)), mimetype="image/jpeg")
    buffer.seek(0)
    return MediaIoBaseUpload(buffer, mimetype="image/png")


def set_thumbnail(youtube, video_id: str, thumbnail_file: Optional[Path]) -> bool:
    """Set the thumbnail. Never raises: the video is already up, so a failure only warns. Returns success."""
    if thumbnail_file is None:
        print("⏭  Skipping thumbnail — YouTube will pick one automatically.")
        return True

    print(f"🖼  Setting thumbnail ({thumbnail_file.name})…")
    try:
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=thumbnail_media(thumbnail_file),
        ).execute()
    except Exception as e:
        print(f"⚠️  Failed to set the thumbnail: {e}")
        print(f"   Retry with: phantom publish yt {shlex.quote(str(thumbnail_file.parent))}/<video>.mp4 "
              f"--set-thumbnail-for {video_id}")
        return False
    print("✅ Thumbnail set.")
    return True


def get_share_url(video_id: str) -> str:
    return f"https://youtu.be/{video_id}"


def render_tweet(template: str, url: str, title: str) -> str:
    """Fill a tweetTemplate's {url} and {title}; other braces are kept as they are."""
    try:
        return template.format(url=url, title=title)
    except (KeyError, IndexError, ValueError):
        return template.replace("{url}", url).replace("{title}", title)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Upload a video to YouTube.")
    parser.add_argument("video_path", help="Path to the .mp4 file to upload.")
    parser.add_argument("--skip-socials", action="store_true", help="Skip sharing the video on social platforms.")
    parser.add_argument("--skip-email", action="store_true", help="Skip sending the email broadcast.")
    parser.add_argument(
        "--playlist",
        help="Add the video to this playlist (ID, URL, or title; a new title creates a public playlist).",
    )
    parser.add_argument(
        "--set-thumbnail-for",
        metavar="VIDEO_ID",
        help="Only set the project's thumbnail on this already-uploaded video (retry after a failed thumbnail), then exit.",
    )
    title_group = parser.add_mutually_exclusive_group()
    title_group.add_argument(
        "--title",
        help="Approve this title before uploading: a rank number from metadata.json's titles, or your own text.",
    )
    title_group.add_argument(
        "--keep-title", action="store_true", help="Upload with the current title even if it wasn't approved."
    )
    args = parser.parse_args()

    video_path = Path(args.video_path).resolve()
    if not video_path.exists():
        print(f"❌ File not found: {video_path}")
        sys.exit(1)

    # Load metadata
    print("📋 Loading metadata…")
    try:
        metadata = read_metadata(video_path)
    except FileNotFoundError as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.set_thumbnail_for:
        thumbnail_file = find_thumbnail(video_path)
        youtube = get_authenticated_service()
        sys.exit(0 if set_thumbnail(youtube, args.set_thumbnail_for, thumbnail_file) else 1)

    # Pre-check metadata requirements (description, timestamps & recommendations) before authentication or uploading
    print("🔍 Validating metadata...")
    try:
        raw_description, formatted_timestamps, formatted_recommendations, formatted_resources = validate_metadata_for_upload(metadata)
        print("✅ Found video description.")
        ts_count = len(formatted_timestamps.splitlines())
        rec_count = len(formatted_recommendations.splitlines())
        print(f"✅ Found {ts_count} timestamps for video chapters.")
        print(f"✅ Found {rec_count} recommended videos for 'WATCH THESE NEXT'.")
        if formatted_resources:
            res_count = len(formatted_resources.strip().splitlines()) - 1
            print(f"✅ Found {res_count} resource links for 'RESOURCES'.")
        elif "resources" not in metadata:
            print("⚠️  No 'resources' in metadata.json. Run `phantom meta resources` to check for promised links.")
    except ValueError as e:
        print(f"\n❌ Error: {e}\n", file=sys.stderr)
        sys.exit(1)

    try:
        publish_at = scheduled_publish_time(metadata)
    except ValueError as e:
        print(f"\n❌ Error: {e}\n", file=sys.stderr)
        sys.exit(1)
    if publish_at:
        print(f"📅 Will upload privately and go public on {publish_at:%a %b %d %Y, %H:%M %Z}.")

    confirm_title(metadata, args.title, args.keep_title)

    thumbnail_file = find_thumbnail(video_path)

    # Authenticate
    print("🔐 Authenticating with YouTube…")
    youtube = get_authenticated_service()

    # Resolve the playlist before uploading so a bad ID or ambiguous title stops us early
    playlist = None
    if args.playlist:
        # Imported here: addtoplaylist imports this module
        from youtube_api.addtoplaylist import find_playlist
        try:
            playlist = find_playlist(youtube, args.playlist)
        except ValueError as e:
            print(f"\n❌ Error: {e}\n", file=sys.stderr)
            sys.exit(1)
        if playlist:
            print(f"✅ Will add the video to playlist '{playlist['snippet']['title']}'.")
        else:
            print(f"✅ Will create playlist '{args.playlist.strip()}' and add the video to it.")

    # Upload video
    full_description = build_description(
        raw_description, formatted_timestamps, formatted_recommendations, formatted_resources
    )
    video_id = upload_video(youtube, video_path, metadata, full_description, publish_at)

    # Save URL and uploadedDate right away: the video is live, so this must not wait on later steps
    share_url = get_share_url(video_id)
    print(f"\n🔗 Share URL: {share_url}")
    metadata.url = share_url
    metadata.uploaded_date = datetime.now().strftime("%Y-%m-%d")
    try:
        metadata.save()
        print("✅ Saved YouTube URL and uploadedDate to metadata.json")
    except Exception as e:
        print(f"⚠️  Failed to save metadata to metadata.json: {e}")

    # Set thumbnail (a failure only warns)
    set_thumbnail(youtube, video_id, thumbnail_file)

    # Add to playlist. The video is already up, so a failure here only warns.
    if args.playlist:
        from youtube_api.addtoplaylist import add_video_to_playlist, create_playlist
        try:
            if playlist is None:
                playlist = create_playlist(youtube, args.playlist)
                add_video_to_playlist(youtube, video_id, playlist, check_existing=False)
            else:
                add_video_to_playlist(youtube, video_id, playlist)
        except Exception as e:
            print(f"⚠️  Failed to add the video to the playlist: {e}")
            print(f"   Retry with: phantom yt add-to-playlist {video_id} \"{args.playlist}\"")

    title = metadata.get("title", video_path.stem)
    post_content = render_tweet(metadata.get("tweetTemplate", DEFAULT_TWEET_TEMPLATE), share_url, title)
    steps = []
    if not args.skip_socials:
        steps += [
            ("🐦 Posting tweet", "Tweet posted", ["twitter.post_tweet", post_content],
             ["phantom", "publish", "x", post_content]),
            ("📝 Posting Substack note", "Substack note posted", ["substack.post_note", post_content],
             ["phantom", "publish", "substack", post_content]),
        ]
    if not args.skip_email:
        email_args = ["--title", title, "--description", metadata.get("description", ""), "--url", share_url]
        steps.append(
            (f"📧 Sending email broadcast for '{title}'", "Email broadcast sent", ["emails.resend_broadcast", *email_args],
             ["phantom", "publish", "email", *email_args])
        )

    if publish_at and steps:
        # The video isn't public yet, so posting now would share a link that doesn't work
        print(f"\n⏸  The video goes public on {publish_at:%b %d at %H:%M}. Send these then:")
        for _, _, _, command in steps:
            print(f"   {shlex.join(command)}")
    else:
        # The video is already up, so a failing step only warns and the rest still run
        for heading, done, module_args, command in steps:
            print(f"\n{heading}...")
            try:
                subprocess.run(python_module(*module_args), check=True)
                print(f"✅ {done}!")
            except Exception as e:
                print(f"⚠️  Step failed: {e}")
                print(f"   Retry with: {shlex.join(command)}")

    print("\n🎉 All done!")


if __name__ == "__main__":
    main()
