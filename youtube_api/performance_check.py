"""
Early Performance Check
=======================
Once an upload is 48 hours old, compares its click-through rate in its first days with
the channel's long-form CTR over the last 28 days (the Videos card on Studio's Content
tab, see channel_stats.py). A lower CTR means it needs repackaging (a new title and
thumbnail). Average view duration is stored alongside for reference only. Studio's
average isn't watch time divided by views, so the video's figure is Studio's own
since-publish average at check time rather than one computed for the window.

The video's side covers its first WINDOW_DAYS Pacific-time days since publish (the
publish day plus the day after). Both of those days have finished 48 hours after
publish, whatever time the video went out, so every video is judged on the same window.
The video's and the channel's numbers come live from YouTube Studio in one Chrome
session; the public API only supplies publish times.

Used by `phantom projects status`, which stores each result in the project's
metadata.json under "performanceCheck". Each check also refreshes the channel stats
saved by channel_stats.py.

check_repackaged() then watches videos that need repackaging: once the title or
thumbnail on YouTube no longer matches the project's metadata.json title and local
thumbnail files, the video has been repackaged. YouTube re-encodes thumbnails, so
they're compared by perceptual hash rather than by bytes.
"""

import io
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from PIL import Image

# Add project root to sys.path to allow package imports
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from youtube_api import channel_stats, studio_analytics
from youtube_api.fetch_videos import fetch_video_details
from youtube_api.models import Video
from youtube_api.utils import get_youtube_client, is_short as _is_short, parse_iso8601_duration
from youtube_api.video_info import build_payload, compute_outlier, get_channel_samples, owner_analytics_result

# ── Configuration ─────────────────────────────────────────────────────────────

CHECK_AFTER          = timedelta(hours=48)
WINDOW_DAYS          = 2        # Publish day + the next day, both finished by CHECK_AFTER
THUMBNAIL_MATCH_BITS = 10       # Max differing dHash bits (of 64) for two thumbnails to be the same image

# Studio's daily rows are Pacific-time days
ANALYTICS_TZ = ZoneInfo("America/Los_Angeles")


# ── Window stats ──────────────────────────────────────────────────────────────

def window_stats(published_at: datetime, daily: list[dict]) -> Optional[dict]:
    """
    Impressions, views, and CTR over a video's first WINDOW_DAYS days, or None when
    Studio doesn't have that many finished days yet. CTR is a percentage, weighted by
    each day's impressions.
    """
    publish_day = published_at.astimezone(ANALYTICS_TZ).date().isoformat()
    rows = [d for d in daily if d["day"] >= publish_day][:WINDOW_DAYS]
    if len(rows) < WINDOW_DAYS:
        return None

    impressions = sum(r.get("impressions") or 0 for r in rows)
    clicks = sum((r.get("impressions") or 0) * (r.get("ctr") or 0) / 100 for r in rows)
    return {
        "impressions": impressions,
        "views": sum(r.get("views") or 0 for r in rows),
        "ctr": round(clicks / impressions * 100, 2) if impressions else 0.0,
        "days": [r["day"] for r in rows],
    }


# ── Check ─────────────────────────────────────────────────────────────────────

def check_videos(video_ids: list[str]) -> dict[str, dict]:
    """
    Run the performance check for each video on your channel. Returns a result per video ID:
      {"status": "waiting", "ready_at": iso}   - under CHECK_AFTER since publish
      {"status": "error", "reason": str}       - couldn't be checked this time
      {"status": "ok", "result": dict}         - the snapshot to store in metadata.json
    """
    youtube = get_youtube_client()
    videos = {v.video_id: v for v in fetch_video_details(youtube, video_ids)}
    now = datetime.now(UTC)

    results: dict[str, dict] = {}
    ready: list[Video] = []
    for video_id in video_ids:
        video = videos.get(video_id)
        if video is None:
            results[video_id] = {"status": "error", "reason": "Video not found on YouTube (private or not yet public)."}
        elif _is_short(parse_iso8601_duration(video.duration)):
            results[video_id] = {"status": "error", "reason": "Shorts aren't checked; the channel stats cover long-form videos."}
        elif now - video.published_at < CHECK_AFTER:
            results[video_id] = {"status": "waiting", "ready_at": (video.published_at + CHECK_AFTER).isoformat()}
        else:
            ready.append(video)
    if not ready:
        return results

    # Every project is on the same channel, so one Studio session covers them all.
    channel_id = ready[0].channel_id
    for video in ready:
        if video.channel_id != channel_id:
            results[video.video_id] = {"status": "error", "reason": "Video isn't on your channel."}
    ready = [v for v in ready if v.channel_id == channel_id]

    studio = studio_analytics.fetch_studio(channel_id, [v.video_id for v in ready], channel_totals=True)
    if studio is None:
        for video in ready:
            results[video.video_id] = {"status": "error", "reason": "Video isn't on the channel logged into Studio."}
        return results
    channel = channel_stats.record_channel_stats(channel_id, studio["channel"])

    # Only for the stored yt info payload's outlier score
    samples, baseline_cached = get_channel_samples(youtube, channel_id)

    for video in ready:
        analytics = studio["videos"][video.video_id]
        own_window = window_stats(video.published_at, analytics["daily"])
        if own_window is None:
            results[video.video_id] = {"status": "error", "reason": "Studio doesn't have the first days' analytics yet."}
            continue

        info = build_payload(
            video,
            compute_outlier(video, samples),
            baseline_cached,
            owner_analytics_result(video, analytics),
        )
        results[video.video_id] = {"status": "ok", "result": {
            "checked_at": now.isoformat(),
            "window_days": WINDOW_DAYS,
            "video": {
                **own_window,
                # For reference: Studio's since-publish average, comparable with the channel's
                "avg_view_duration_since_publish_ms": analytics["totals"].get("avg_view_duration_ms"),
            },
            "channel_28d": channel,
            "needs_repackaging": own_window["ctr"] < channel["ctr"],
            "yt_info": info,
        }}
    return results


# ── Repackaging ───────────────────────────────────────────────────────────────

def thumbnail_hash(image: Image.Image) -> int:
    """64-bit difference hash: survives YouTube's re-encoding and resizing, changes with the image."""
    width, height = image.size
    if abs(width / height - 4 / 3) < 0.01:
        # Lower-quality YouTube thumbnails are 4:3 with black bars around the 16:9 frame
        frame_height = width * 9 // 16
        image = image.crop((0, (height - frame_height) // 2, width, (height + frame_height) // 2))
    pixels = image.convert("L").resize((9, 8), Image.LANCZOS).tobytes()
    bits = (pixels[row * 9 + col] > pixels[row * 9 + col + 1] for row in range(8) for col in range(8))
    return sum(1 << i for i, bit in enumerate(bits) if bit)


def _download_image(url: str) -> Image.Image:
    with urllib.request.urlopen(url, timeout=15) as response:
        return Image.open(io.BytesIO(response.read()))


def check_repackaged(projects: dict[str, dict]) -> dict[str, dict]:
    """
    Compare each video's live title and thumbnail with its project's originals. Takes
    {video_id: {"title": metadata.json title, "thumbnails": [local thumbnail paths]}} and
    returns per video ID either {"status": "error", "reason": str} or
      {"status": "ok", "repackaged": bool, "title_changed": bool,
       "thumbnail_changed": bool or None (no local thumbnail to compare), "youtube_title": str}
    """
    videos = {v.video_id: v for v in fetch_video_details(get_youtube_client(), list(projects))}

    results = {}
    for video_id, original in projects.items():
        video = videos.get(video_id)
        if video is None:
            results[video_id] = {"status": "error", "reason": "Video not found on YouTube."}
            continue

        title_changed = video.title.strip() != (original["title"] or "").strip()
        thumbnail_changed = None
        local_hashes = []
        for path in original["thumbnails"]:
            try:
                local_hashes.append(thumbnail_hash(Image.open(path)))
            except OSError:
                pass
        if local_hashes:
            try:
                live_hash = thumbnail_hash(_download_image(video.thumbnail_url))
            except OSError as e:
                results[video_id] = {"status": "error", "reason": f"Couldn't download the YouTube thumbnail: {e}"}
                continue
            # Any of the project's thumbnails may be the one that was uploaded
            thumbnail_changed = all(bin(live_hash ^ h).count("1") > THUMBNAIL_MATCH_BITS for h in local_hashes)

        results[video_id] = {
            "status": "ok",
            "repackaged": title_changed or bool(thumbnail_changed),
            "title_changed": title_changed,
            "thumbnail_changed": thumbnail_changed,
            "youtube_title": video.title,
        }
    return results
