"""
YouTube Studio Analytics (owner-only stats)
===========================================
Live owner-only stats for a video on your own channel, read from YouTube Studio:
impressions, click-through rate, views, watch time, and subscribers, as lifetime totals
and day by day. Also the channel's long-form totals over the last 28 days, the same
numbers as the Videos card on Studio's Content tab.

The public APIs lag two to three days behind, so this asks Studio itself: one get_cards
call per video (and one for the channel) for exactly the metrics it needs, over plain
HTTP through the saved Studio session (youtube_api/studio/session.py; `phantom studio
login` creates it, and it re-imports itself from the p9222 Chrome if it ever expires).
Totals are live; daily rows cover finished days.

The logged-in channel's ID is cached in cache/my_channel.json, so videos on other
channels are recognized without a request.
"""

import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from youtube_api.studio.session import Studio, StudioError

# ── Configuration ─────────────────────────────────────────────────────────────

CHANNEL_CACHE_FILE = Path(__file__).resolve().parent / "cache" / "my_channel.json"
MAX_WORKERS        = 6      # get_cards calls in parallel; Studio takes ~2s each
MAX_DAILY_ROWS     = 5000   # The day table is sorted by its first metric, so fetch every day and sort locally

# Studio metric -> key in our output, and how Studio reports its values
METRICS = {
    "VIDEO_THUMBNAIL_IMPRESSIONS":     "impressions",
    "VIDEO_THUMBNAIL_IMPRESSIONS_VTR": "ctr",                # Percentage (4.2 means 4.2%)
    "EXTERNAL_VIEWS":                  "views",
    "EXTERNAL_WATCH_TIME":             "watch_time_ms",
    "AVERAGE_WATCH_TIME":              "avg_view_duration_ms",
    "SUBSCRIBERS_GAINED":              "subscribers_gained",
    "SUBSCRIBERS_LOST":                "subscribers_lost",
    "ESTIMATED_UNIQUE_VIEWERS":        "unique_viewers",
}

# Channel totals: the Content tab's Videos card (long-form only) plus watch time and subscribers
CHANNEL_METRICS = {
    m: METRICS[m] for m in (
        "VIDEO_THUMBNAIL_IMPRESSIONS", "VIDEO_THUMBNAIL_IMPRESSIONS_VTR", "EXTERNAL_VIEWS",
        "AVERAGE_WATCH_TIME", "EXTERNAL_WATCH_TIME", "SUBSCRIBERS_GAINED", "SUBSCRIBERS_LOST",
    )
}

# StudioError (re-exported from youtube_api.studio.session) is raised when Studio can't be
# reached or doesn't return analytics.


# ── Channel ownership ─────────────────────────────────────────────────────────

def get_cached_channel_id() -> Optional[str]:
    """The logged-in Studio channel's ID from the last scrape, or None before the first one."""
    try:
        with open(CHANNEL_CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f).get("channel_id")
    except (OSError, ValueError):
        return None


def _cache_channel_id(channel_id: str) -> None:
    # Cache failures shouldn't break the command; the next scrape just writes it again.
    try:
        CHANNEL_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = CHANNEL_CACHE_FILE.with_suffix(".json.tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump({"channel_id": channel_id}, f)
        os.replace(tmp_file, CHANNEL_CACHE_FILE)
    except OSError:
        pass


# ── Studio request ────────────────────────────────────────────────────────────

def _build_cards_request(video_id: str, context: dict) -> dict:
    card = {"autoUpdateInterval": "ANALYTICS_AUTO_UPDATE_INTERVAL_NEVER",
            "failureMode": "ANALYTICS_CARD_FAILURE_MODE_FAIL_PAGE"}
    return {
        "context": context,
        "screenConfig": {
            "entity": {"videoId": video_id},
            "timePeriod": {
                "timePeriodType": "ANALYTICS_TIME_PERIOD_TYPE_SINCE_PUBLISH",
                "referencePoint": "TIME_PERIOD_REFERENCE_POINT_SINCE_PUBLISH",
                "entity": {"videoId": video_id},
            },
            "currency": "USD",
        },
        "cardConfigs": [
            # Live lifetime totals
            {**card, "keyMetricCardConfig": {
                "timePeriod": {},
                "metricTabConfigs": [{"metric": m} for m in METRICS],
            }},
            # One row per finished day (Pacific time, like YouTube's other reports)
            {**card, "tableCardConfig": {
                "dimension": "DAY",
                "metrics": list(METRICS),
                "order": "ANALYTICS_TABLE_CARD_ORDER_TOP_METRIC",
                "maxRowCount": MAX_DAILY_ROWS,
            }},
        ],
    }


def _build_channel_cards_request(channel_id: str, context: dict) -> dict:
    return {
        "context": context,
        "screenConfig": {
            "entity": {"channelId": channel_id},
            "timePeriod": {"timePeriodType": "ANALYTICS_TIME_PERIOD_TYPE_FOUR_WEEKS"},
            "currency": "USD",
            # Studio's "last 28 days" uses the viewer's local days
            "timeZoneOffsetSecs": int(datetime.now().astimezone().utcoffset().total_seconds()),
        },
        "cardConfigs": [{
            "autoUpdateInterval": "ANALYTICS_AUTO_UPDATE_INTERVAL_NEVER",
            "failureMode": "ANALYTICS_CARD_FAILURE_MODE_FAIL_PAGE",
            "contentAnalysisType": "CONTENT_ANALYSIS_TYPE_VIDEO",
            "keyMetricCardConfig": {"metricTabConfigs": [{"metric": m} for m in CHANNEL_METRICS]},
        }],
    }


def _scrape(channel_id: str, requests: dict[str, Callable[[dict], dict]]) -> tuple[str, dict[str, dict]]:
    """
    One get_cards call per entry in requests (each builds its body from Studio's context).
    Returns (logged-in channel ID, response per key), with no responses when channel_id
    isn't the logged-in channel.
    """
    studio = Studio()
    if studio.channel_id != channel_id:
        return studio.channel_id, {}
    context = studio.context()

    def fetch(build):
        body = build(context)
        body.pop("context", None)   # Studio.call adds its own
        return studio.call("yta_web/get_cards", body)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {key: pool.submit(fetch, build) for key, build in requests.items()}
    return studio.channel_id, {key: f.result() for key, f in futures.items()}


# ── Parsing ───────────────────────────────────────────────────────────────────

def _column_values(column: dict) -> list:
    """A metric column's values with undefined cells as None."""
    values = next(
        (v["values"] for key in ("counts", "percentages", "milliseconds") if (v := column.get(key))),
        [],
    )
    undefined = set(column.get("undefinedValueIndices", []))
    return [None if i in undefined else value for i, value in enumerate(values)]


def _parse_cards(response: dict) -> tuple[dict, list[dict]]:
    totals, daily = {}, []
    for card in response.get("cards", []):
        if "keyMetricCardData" in card:
            for tab in card["keyMetricCardData"].get("keyMetricTabs", []):
                content = tab.get("primaryContent", {})
                if content.get("metric") in METRICS:
                    totals[METRICS[content["metric"]]] = content.get("total")
        elif "tableCardData" in card:
            table = card["tableCardData"].get("mainTableData", {})
            dimension = (table.get("dimensionColumns") or [{}])[0]
            date_ids = dimension.get("dateIds", {}).get("values", [])
            rows = [{"day": f"{d // 10000}-{d // 100 % 100:02}-{d % 100:02}"} for d in date_ids]
            for column in table.get("metricColumns", []):
                key = METRICS.get(column.get("metric", {}).get("type"))
                if key:
                    for row, value in zip(rows, _column_values(column)):
                        row[key] = value
            daily = sorted(rows, key=lambda r: r["day"])
    return totals, daily


# ── Combined ──────────────────────────────────────────────────────────────────

CHANNEL_KEY = "channel"   # Never a video ID, which is always 11 characters


def fetch_studio(channel_id: str, video_ids: list[str] = (), channel_totals: bool = False) -> Optional[dict]:
    """
    One Studio session for any number of videos on channel_id plus, optionally, the
    channel's long-form totals over the last 28 days. Returns None when channel_id isn't
    the logged-in channel, otherwise:
      {"videos": {video_id: {"totals": {...}, "daily": [...]}}, "channel": {...} or None}
    CTR is a percentage (4.2 means 4.2%).
    """
    video_ids = list(dict.fromkeys(video_ids))
    requests = {vid: (lambda ctx, vid=vid: _build_cards_request(vid, ctx)) for vid in video_ids}
    if channel_totals:
        requests[CHANNEL_KEY] = lambda ctx: _build_channel_cards_request(channel_id, ctx)
    if not requests:
        return {"videos": {}, "channel": None}

    logged_in_channel_id, responses = _scrape(channel_id, requests)
    _cache_channel_id(logged_in_channel_id)
    if logged_in_channel_id != channel_id:
        return None

    videos = {}
    for video_id in video_ids:
        totals, daily = _parse_cards(responses[video_id])
        if not totals:
            raise StudioError(f"Studio's response had no analytics for {video_id}.")
        videos[video_id] = {"totals": totals, "daily": daily}

    channel = None
    if channel_totals:
        channel, _ = _parse_cards(responses[CHANNEL_KEY])
        if not channel:
            raise StudioError("Studio's response had no channel analytics.")
    return {"videos": videos, "channel": channel}


def fetch_studio_analytics(video_id: str, video_channel_id: str) -> Optional[dict]:
    """
    Live totals plus one row per finished day for a video on the logged-in channel, or None
    when the video belongs to another channel. CTR is a percentage (4.2 means 4.2%).
    """
    result = fetch_studio(video_channel_id, [video_id])
    return None if result is None else result["videos"][video_id]
