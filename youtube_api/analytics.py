"""
YouTube Analytics (owner-only stats)
====================================
OAuth access to YouTube's owner-only stats for the authenticated channel: impressions,
click-through rate, views, and watch time, as lifetime totals and day by day.

These stats only exist for videos on your own channel. The OAuth token lives in its own
file (tokens/analytics_token.json) so it doesn't disturb the upload token's scopes.

Views and watch time come from the Analytics API, which doesn't expose impressions or CTR.
Those only come from the Reporting API's daily "channel_reach_basic_a1" CSVs (one row per
video per day). YouTube deletes those files after a few weeks, so every downloaded day is
kept in cache/reach_reports.json and history keeps growing past what the API still holds.
"""

import csv
import io
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# ── Configuration ─────────────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/youtube.readonly",       # Look up which channel is "mine"
    "https://www.googleapis.com/auth/yt-analytics.readonly",  # Impressions, CTR, views, watch time
]
TOKENS_DIR          = Path(__file__).resolve().parent / "tokens"
CLIENT_SECRETS_FILE = TOKENS_DIR / "client_secret.json"
TOKEN_FILE          = TOKENS_DIR / "analytics_token.json"
CACHE_DIR           = Path(__file__).resolve().parent / "cache"
CHANNEL_CACHE_FILE  = CACHE_DIR / "my_channel.json"
REACH_CACHE_FILE    = CACHE_DIR / "reach_reports.json"
REACH_REPORT_TYPE   = "channel_reach_basic_a1"
REACH_JOB_NAME      = "phantom reach"

# YouTube Analytics days are Pacific-time days, in both APIs.
ANALYTICS_TZ = ZoneInfo("America/Los_Angeles")

# Analytics API metric name -> key in our output
METRICS = {
    "views":                              "views",
    "estimatedMinutesWatched":            "minutes_watched",
    "averageViewDuration":                "avg_view_duration_seconds",
    "averageViewPercentage":              "avg_view_percentage",
    "likes":                              "likes",
    "subscribersGained":                  "subscribers_gained",
    "subscribersLost":                    "subscribers_lost",
}


class AnalyticsNotAuthorized(Exception):
    """Raised when no usable analytics token exists and login wasn't requested."""


# ── Auth ──────────────────────────────────────────────────────────────────────

def get_credentials(login: bool = False) -> Credentials:
    """
    Load the analytics token, refreshing it when expired. With login=True, run the
    browser consent flow if there's no usable token; otherwise raise AnalyticsNotAuthorized.
    """
    creds = None
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except (OSError, ValueError):
            creds = None

    if creds and not creds.valid and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_token(creds)
        except Exception:
            creds = None

    if creds and creds.valid:
        return creds

    if not login:
        raise AnalyticsNotAuthorized("No analytics token; run with --login once to authorize.")
    if not CLIENT_SECRETS_FILE.exists():
        raise FileNotFoundError(f"Google client_secret.json not found at {CLIENT_SECRETS_FILE}")

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS_FILE), SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    # A new login may be a different Google account, so forget the cached channel.
    CHANNEL_CACHE_FILE.unlink(missing_ok=True)
    return creds


def _save_token(creds: Credentials) -> None:
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(creds.to_json())


# ── Channel ownership ─────────────────────────────────────────────────────────

def get_my_channel_id(creds: Credentials) -> str:
    """The authenticated account's channel ID, cached since it never changes for a token."""
    try:
        with open(CHANNEL_CACHE_FILE, "r", encoding="utf-8") as f:
            cached = json.load(f).get("channel_id")
        if cached:
            return cached
    except (OSError, ValueError):
        pass

    youtube = build("youtube", "v3", credentials=creds)
    items = youtube.channels().list(part="id", mine=True).execute().get("items", [])
    if not items:
        raise RuntimeError("The authorized Google account has no YouTube channel.")
    channel_id = items[0]["id"]

    _write_json(CHANNEL_CACHE_FILE, {"channel_id": channel_id})
    return channel_id


def _write_json(path: Path, data) -> None:
    # Cache failures shouldn't break the command; the next run just refetches.
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = path.with_suffix(".json.tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp_file, path)
    except OSError:
        pass


# ── Analytics API (views, watch time) ────────────────────────────────────────

def _query(analytics, video_id: str, start: date, end: date, dimensions: Optional[str]) -> list[dict]:
    params = {
        "ids": "channel==MINE",
        "startDate": start.isoformat(),
        "endDate": end.isoformat(),
        "metrics": ",".join(METRICS),
        "filters": f"video=={video_id}",
    }
    if dimensions:
        params["dimensions"] = dimensions
        params["sort"] = dimensions
    response = analytics.reports().query(**params).execute()

    names = [h["name"] for h in response.get("columnHeaders", [])]
    return [
        {METRICS.get(name, name): value for name, value in zip(names, raw)}
        for raw in response.get("rows", [])
    ]


# ── Reporting API (impressions, CTR) ──────────────────────────────────────────

def _get_reach_job_id(reporting) -> tuple[str, bool]:
    """Return (job ID, newly_created) for the channel's reach report job, creating one if missing."""
    for job in reporting.jobs().list().execute().get("jobs", []):
        if job.get("reportTypeId") == REACH_REPORT_TYPE:
            return job["id"], False
    job = reporting.jobs().create(body={"reportTypeId": REACH_REPORT_TYPE, "name": REACH_JOB_NAME}).execute()
    return job["id"], True


def _parse_reach_csv(text: str, channel_id: str) -> dict[str, list]:
    """{video_id: [impressions, ctr]} for one day's report."""
    videos = {}
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("channel_id") != channel_id:
            continue
        impressions = int(row["video_thumbnail_impressions"] or 0)
        ctr = float(row["video_thumbnail_impressions_ctr"] or 0)
        # Fold any duplicate rows for a video into one, weighting CTR by impressions.
        prev_impr, prev_ctr = videos.get(row["video_id"], (0, 0.0))
        total = prev_impr + impressions
        videos[row["video_id"]] = [total, (prev_impr * prev_ctr + impressions * ctr) / total if total else 0.0]
    return videos


def sync_reach_reports(creds: Credentials, channel_id: str) -> tuple[dict, bool]:
    """
    Download any reach reports not cached yet and return (cached days, job_just_created).
    Cached days look like {"2026-10-17": {"created": "...", "videos": {video_id: [impressions, ctr]}}}.
    """
    try:
        with open(REACH_CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    except (OSError, ValueError):
        cache = {}
    days = cache.get(channel_id, {})

    reporting = build("youtubereporting", "v1", credentials=creds)
    job_id, created = _get_reach_job_id(reporting)

    reports, page_token = [], None
    while True:
        response = reporting.jobs().reports().list(jobId=job_id, pageToken=page_token).execute()
        reports.extend(response.get("reports", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    # YouTube can reissue a day's report with corrections; the newest one wins.
    latest = {}
    for report in reports:
        day = datetime.fromisoformat(report["startTime"]).astimezone(ANALYTICS_TZ).date().isoformat()
        if day not in latest or report["createTime"] > latest[day]["createTime"]:
            latest[day] = report
    missing = {
        day: report for day, report in latest.items()
        if day not in days or days[day]["created"] < report["createTime"]
    }

    if missing:
        session = AuthorizedSession(creds)

        def download(item):
            day, report = item
            response = session.get(report["downloadUrl"], timeout=60)
            response.raise_for_status()
            return day, report["createTime"], _parse_reach_csv(response.text, channel_id)

        with ThreadPoolExecutor(max_workers=8) as pool:
            for day, created_at, videos in pool.map(download, missing.items()):
                days[day] = {"created": created_at, "videos": videos}
        cache[channel_id] = days
        _write_json(REACH_CACHE_FILE, cache)

    return days, created


# ── Combined ──────────────────────────────────────────────────────────────────

def fetch_video_analytics(creds: Credentials, channel_id: str, video_id: str, published_at: datetime) -> dict:
    """
    Lifetime totals plus one row per day since publish for a video on the authorized channel.
    Both sources lag about two days behind, so the most recent days may be missing or partial.
    CTR and average view percentage are percentages (4.2 means 4.2%).
    """
    analytics = build("youtubeAnalytics", "v2", credentials=creds)
    start = published_at.astimezone(ANALYTICS_TZ).date()
    end = datetime.now(ANALYTICS_TZ).date()

    with ThreadPoolExecutor(max_workers=3) as pool:
        totals_job = pool.submit(_query, analytics, video_id, start, end, None)
        daily_job = pool.submit(_query, analytics, video_id, start, end, "day")
        reach_job = pool.submit(sync_reach_reports, creds, channel_id)
        totals = totals_job.result()
        daily = {row.pop("day"): row for row in daily_job.result()}
        reach_days, reach_job_created = reach_job.result()

    # Merge impressions/CTR into the matching Analytics day.
    total_impressions, total_clicks = 0, 0.0
    for day, entry in reach_days.items():
        if not start.isoformat() <= day <= end.isoformat():
            continue
        impressions, ctr = entry["videos"].get(video_id, (0, 0.0))
        row = daily.setdefault(day, {})
        row["impressions"] = impressions
        row["ctr"] = round(ctr * 100, 2)
        total_impressions += impressions
        total_clicks += impressions * ctr

    totals = totals[0] if totals else {}
    reach_covered = sorted(d for d in reach_days if d >= start.isoformat())
    if reach_covered:
        totals["impressions"] = total_impressions
        totals["ctr"] = round(total_clicks / total_impressions * 100, 2) if total_impressions else 0.0

    return {
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        # The days impressions/CTR cover; earlier days had expired before they were first cached.
        "reach_start_date": reach_covered[0] if reach_covered else None,
        "reach_end_date": reach_covered[-1] if reach_covered else None,
        "reach_job_created": reach_job_created,
        "totals": totals,
        "daily": [{"day": day, **daily[day]} for day in sorted(daily)],
    }
