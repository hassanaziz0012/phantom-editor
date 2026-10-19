#!/usr/bin/env python3
"""
YouTube Video Info
==================
Shows quick details about any YouTube video: a direct thumbnail URL, basic stats,
and an outlier score (how its views compare to the channel's recent median).

For videos on your own channel it also shows live owner-only analytics (impressions,
CTR, views, watch time) as lifetime totals and day by day, read from YouTube Studio in
the logged-in Chrome on port 9222. Videos on other channels never open Chrome.

The channel baseline (its last 50 uploads) is cached in youtube_api/cache/baselines.json
for 24 hours, so repeat lookups on the same channel cost a single API call.

Usage:
    phantom yt info <video_url_or_id> [--fresh] [--json] [--days N]
"""

import argparse
import json
import os
import statistics
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Optional

# Add project root to sys.path to allow package imports
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from youtube_api import studio_analytics
from youtube_api.fetch_videos import fetch_video_details
from youtube_api.models import Video
from youtube_api.utils import (
    extract_video_id,
    get_youtube_client,
    is_short as _is_short,
    parse_iso8601_duration,
    supports_color,
)

# ── Configuration ─────────────────────────────────────────────────────────────

BASELINE_CACHE_FILE = Path(__file__).resolve().parent / "cache" / "baselines.json"
BASELINE_TTL        = timedelta(hours=24)
BASELINE_SAMPLE     = 50                  # Recent uploads fetched per channel (one API page)
MIN_VIDEO_AGE       = timedelta(days=14)  # Younger videos haven't settled, so they're left out of the baseline
MIN_SAMPLE_SIZE     = 5
DEFAULT_DAILY_ROWS  = 28                  # Days of the daily analytics table shown in text output


# ── Colors ────────────────────────────────────────────────────────────────────

USE_COLOR = supports_color()

def _c(code: str) -> str:
    return code if USE_COLOR else ""

BOLD   = _c("\033[1m")
DIM    = _c("\033[2m")
CYAN   = _c("\033[96m")
GREEN  = _c("\033[92m")
YELLOW = _c("\033[93m")
RED    = _c("\033[91m")
RESET  = _c("\033[0m")


# ── Helpers ───────────────────────────────────────────────────────────────────

def _format_age(published_at: datetime) -> str:
    days = (datetime.now(UTC) - published_at).days
    if days < 1:
        return "today"
    if days < 30:
        return f"{days}d ago"
    if days < 365:
        return f"{days // 30}mo ago"
    return f"{days // 365}y ago"


def _format_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "?"
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def _format_count(value: Optional[int]) -> str:
    return f"{value:,}" if value is not None else "hidden"


def _format_metric(value: Optional[int]) -> str:
    return f"{value:,}" if value is not None else "-"


def _format_ctr(value: Optional[float]) -> str:
    # Studio reports CTR as a percentage already (4.2 means 4.2%).
    return f"{value:.1f}%" if value is not None else "-"


def _format_hours(milliseconds: Optional[int]) -> str:
    return f"{milliseconds / 3_600_000:,.1f}h" if milliseconds is not None else "-"


def _format_seconds(milliseconds: Optional[int]) -> str:
    return _format_duration(round(milliseconds / 1000)) if milliseconds is not None else "?"


# ── Baseline cache ────────────────────────────────────────────────────────────

def _load_baseline_cache() -> dict:
    try:
        with open(BASELINE_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_baseline_cache(cache: dict) -> None:
    # Cache failures shouldn't break the command; the next run just refetches.
    try:
        BASELINE_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = BASELINE_CACHE_FILE.with_suffix(".json.tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2)
        os.replace(tmp_file, BASELINE_CACHE_FILE)
    except OSError:
        pass


def fetch_recent_uploads(youtube, channel_id: str) -> list[dict]:
    """
    Fetch the channel's most recent uploads as compact baseline samples.
    Costs 2 quota units: one playlistItems page + one videos.list batch.
    """
    # A channel's uploads playlist ID is its channel ID with the "UC" prefix swapped for "UU".
    uploads_playlist_id = "UU" + channel_id[2:]
    response = youtube.playlistItems().list(
        part="contentDetails",
        playlistId=uploads_playlist_id,
        maxResults=BASELINE_SAMPLE,
    ).execute()
    video_ids = [item["contentDetails"]["videoId"] for item in response.get("items", [])]

    return [
        {
            "video_id": v.video_id,
            "view_count": v.view_count,
            "published_at": v.published_at.isoformat(),
            "duration_seconds": parse_iso8601_duration(v.duration),
            "live_broadcast": v.live_broadcast,
        }
        for v in fetch_video_details(youtube, video_ids)
    ]


def get_channel_samples(youtube, channel_id: str, fresh: bool = False) -> tuple[list[dict], bool]:
    """Return (recent upload samples, served_from_cache) for a channel, refreshing if stale."""
    cache = _load_baseline_cache()
    entry = cache.get(channel_id)

    if not fresh and entry:
        try:
            fetched_at = datetime.fromisoformat(entry["fetched_at"])
            if datetime.now(UTC) - fetched_at < BASELINE_TTL:
                return entry["videos"], True
        except (KeyError, ValueError):
            pass

    samples = fetch_recent_uploads(youtube, channel_id)
    cache[channel_id] = {
        "fetched_at": datetime.now(UTC).isoformat(),
        "videos": samples,
    }
    _save_baseline_cache(cache)
    return samples, False


# ── Outlier score ─────────────────────────────────────────────────────────────

def compute_outlier(video: Video, samples: list[dict]) -> dict:
    """
    Compare the video's views to the median views of the channel's recent uploads
    of the same format (Shorts vs long-form), excluding the video itself, videos
    younger than MIN_VIDEO_AGE, and live/upcoming streams.
    """
    is_short = _is_short(parse_iso8601_duration(video.duration))
    now = datetime.now(UTC)
    result = {
        "score": None,
        "median_views": None,
        "sample_size": 0,
        "compared_against": "shorts" if is_short else "long-form",
        "too_early": now - video.published_at < MIN_VIDEO_AGE,
        "reason": None,
    }

    if video.view_count is None:
        result["reason"] = "View count is hidden for this video."
        return result

    baseline_views = [
        s["view_count"]
        for s in samples
        if s["video_id"] != video.video_id
        and s["view_count"] is not None
        and s.get("live_broadcast") in (None, "none")
        and _is_short(s["duration_seconds"]) == is_short
        and now - datetime.fromisoformat(s["published_at"]) >= MIN_VIDEO_AGE
    ]
    result["sample_size"] = len(baseline_views)

    if len(baseline_views) < MIN_SAMPLE_SIZE:
        result["reason"] = (
            f"Not enough comparable {result['compared_against']} videos on this channel "
            f"(found {len(baseline_views)}, need {MIN_SAMPLE_SIZE})."
        )
        return result

    median = statistics.median(baseline_views)
    result["median_views"] = round(median)
    if median <= 0:
        result["reason"] = "Channel's median views is zero."
        return result

    result["score"] = round(video.view_count / median, 2)
    return result


# ── Output ────────────────────────────────────────────────────────────────────

def build_payload(video: Video, outlier: dict, baseline_cached: Optional[bool], owner: dict) -> dict:
    duration_seconds = parse_iso8601_duration(video.duration)
    return {
        "video_id": video.video_id,
        "url": video.url,
        "title": video.title,
        "channel_id": video.channel_id,
        "channel_title": video.channel_title,
        "published_at": video.published_at.isoformat(),
        "duration_seconds": duration_seconds,
        "is_short": _is_short(duration_seconds),
        "thumbnail_url": video.thumbnail_url,
        "view_count": video.view_count,
        "like_count": video.like_count,
        "comment_count": video.comment_count,
        "outlier": {**outlier, "baseline_cached": baseline_cached},
        "owner_analytics": owner,
    }


def print_human(video: Video, outlier: dict, owner: dict, daily_rows: int) -> None:
    duration_seconds = parse_iso8601_duration(video.duration)
    kind = "Short" if _is_short(duration_seconds) else _format_duration(duration_seconds)

    def row(label: str, value: str) -> None:
        print(f"  {DIM}{label:<11}{RESET}{value}")

    print()
    print(f"  {BOLD}{video.title}{RESET}")
    print(f"  {DIM}{video.channel_title} · {_format_age(video.published_at)} · {kind}{RESET}")
    print()
    row("Views", f"{BOLD}{_format_count(video.view_count)}{RESET}")

    if outlier["score"] is not None:
        score = outlier["score"]
        color = GREEN if score >= 2 else YELLOW if score >= 1 else RED
        row("Outlier", (
            f"{BOLD}{color}{score:.2f}x{RESET} "
            f"{DIM}(median {outlier['median_views']:,} across {outlier['sample_size']} "
            f"recent {outlier['compared_against']} videos){RESET}"
        ))
        if outlier["too_early"]:
            row("", f"{YELLOW}Video is under {MIN_VIDEO_AGE.days} days old; score will likely rise.{RESET}")
    else:
        row("Outlier", f"{DIM}n/a: {outlier['reason']}{RESET}")

    row("Likes", _format_count(video.like_count))
    row("Comments", _format_count(video.comment_count))
    print()
    row("Thumbnail", f"{CYAN}{video.thumbnail_url}{RESET}")
    row("URL", f"{CYAN}{video.url}{RESET}")
    print()
    print_owner_analytics(owner, daily_rows)


def print_owner_analytics(owner: dict, daily_rows: int) -> None:
    status = owner["status"]
    if status == "not_owner":
        return
    if status != "ok":
        print(f"  {DIM}Owner analytics: {owner['reason']}{RESET}")
        print()
        return

    data = owner["data"]
    totals = data["totals"]

    def row(label: str, value: str) -> None:
        print(f"  {DIM}{label:<13}{RESET}{value}")

    print(f"  {BOLD}Your analytics{RESET} {DIM}(live from YouTube Studio){RESET}")
    row("Impressions", f"{BOLD}{_format_metric(totals.get('impressions'))}{RESET}")
    row("CTR", f"{BOLD}{_format_ctr(totals.get('ctr'))}{RESET}")
    row("Views", _format_metric(totals.get("views")))
    row("Unique", _format_metric(totals.get("unique_viewers")))
    row("Watch time", _format_hours(totals.get("watch_time_ms")))
    avg_percentage = totals.get("avg_view_percentage")
    row("Avg view", _format_seconds(totals.get("avg_view_duration_ms")) + (
        f" {DIM}({avg_percentage:.1f}% of the video){RESET}" if avg_percentage is not None else ""
    ))
    row("Subscribers", f"+{totals.get('subscribers_gained') or 0:,} / -{totals.get('subscribers_lost') or 0:,}")
    print()

    daily = data["daily"]
    if not daily:
        return
    shown = daily if daily_rows <= 0 else daily[-daily_rows:]
    print(f"  {DIM}{'Day':<12}{'Impressions':>12}{'CTR':>8}{'Views':>9}{'Watch':>9}{'Subs':>7}{RESET}")
    for d in shown:
        gained, lost = d.get("subscribers_gained"), d.get("subscribers_lost")
        net_subs = f"{(gained or 0) - (lost or 0):+}" if gained is not None or lost is not None else "-"
        print(
            f"  {d['day']:<12}"
            f"{_format_metric(d.get('impressions')):>12}"
            f"{_format_ctr(d.get('ctr')):>8}"
            f"{_format_metric(d.get('views')):>9}"
            f"{_format_hours(d.get('watch_time_ms')):>9}"
            f"{net_subs:>7}"
        )
    print(f"  {DIM}Days are finished Pacific-time days; the totals above also include today.{RESET}")
    if len(shown) < len(daily):
        print(f"  {DIM}Showing the last {len(shown)} of {len(daily)} days; --days 0 shows all.{RESET}")
    print()


# ── Owner analytics ───────────────────────────────────────────────────────────

def get_owner_analytics(video: Video) -> dict:
    """
    Live Studio analytics for the video, or a status explaining why there are none:
    "ok", "not_owner", or "error".
    """
    # Only a video on the cached channel (or the first run, before it's known) opens Chrome.
    my_channel_id = studio_analytics.get_cached_channel_id()
    if my_channel_id and my_channel_id != video.channel_id:
        return {"status": "not_owner"}

    try:
        data = studio_analytics.fetch_studio_analytics(video.video_id, video.channel_id)
    except Exception as e:
        return {"status": "error", "reason": f"Failed to read YouTube Studio: {e}"}
    if data is None:
        return {"status": "not_owner"}

    totals = data["totals"]
    duration_seconds = parse_iso8601_duration(video.duration)
    if totals.get("avg_view_duration_ms") is not None and duration_seconds:
        totals["avg_view_percentage"] = round(totals["avg_view_duration_ms"] / 10 / duration_seconds, 1)
    return {"status": "ok", "data": data}


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show quick details about a YouTube video: thumbnail URL, stats, and outlier score."
    )
    parser.add_argument(
        "video",
        help="YouTube video URL (watch, youtu.be, shorts, live) or 11-character video ID.",
    )
    parser.add_argument(
        "--fresh",
        action="store_true",
        help="Refetch the channel's baseline instead of using the 24h cache.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON instead of human-readable text.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DEFAULT_DAILY_ROWS,
        help=f"Days of your daily analytics to show (default {DEFAULT_DAILY_ROWS}, 0 for all since publish). "
             "JSON output always includes every day.",
    )
    args = parser.parse_args()

    def fail(message: str) -> None:
        if args.json:
            print(json.dumps({"error": message}, indent=2))
        else:
            print(f"{RED}Error:{RESET} {message}", file=sys.stderr)
        sys.exit(1)

    try:
        video_id = extract_video_id(args.video)
        youtube = get_youtube_client()
        videos = fetch_video_details(youtube, [video_id])
    except Exception as e:
        fail(str(e))

    if not videos:
        fail(f"Video not found (or private): {video_id}")
    video = videos[0]

    with ThreadPoolExecutor(max_workers=1) as pool:
        # Studio takes a few seconds in Chrome, so it runs while the baseline is fetched.
        owner_job = pool.submit(get_owner_analytics, video)

        # The score is a nice-to-have; an API error here shouldn't hide the video's details.
        baseline_cached = None
        try:
            samples, baseline_cached = get_channel_samples(youtube, video.channel_id, fresh=args.fresh)
            outlier = compute_outlier(video, samples)
        except Exception as e:
            outlier = compute_outlier(video, [])
            outlier["reason"] = f"Failed to fetch channel baseline: {e}"

        owner = owner_job.result()

    if args.json:
        print(json.dumps(build_payload(video, outlier, baseline_cached, owner), indent=2, ensure_ascii=False))
    else:
        print_human(video, outlier, owner, args.days)


if __name__ == "__main__":
    main()
