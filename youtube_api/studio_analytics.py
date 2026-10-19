"""
YouTube Studio Analytics (owner-only stats)
===========================================
Live owner-only stats for a video on your own channel, read from YouTube Studio:
impressions, click-through rate, views, watch time, and subscribers, as lifetime totals
and day by day. Also the channel's long-form totals over the last 28 days, the same
numbers as the Videos card on Studio's Content tab.

The public APIs lag two to three days behind, so this asks Studio itself. It opens the
video's analytics page in the logged-in Chrome on port 9222 (the p9222 profile), copies
the auth headers and context from Studio's first API request, and makes one get_cards
call for exactly the metrics it needs. Totals are live; daily rows cover finished days.
Several videos and the channel totals can share one Studio session, one get_cards call each.

The logged-in channel's ID is cached in cache/my_channel.json, so videos on other
channels are recognized without opening Chrome.
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from playwright.async_api import TimeoutError as PlaywrightTimeoutError, async_playwright

# Add project root to sys.path to allow package imports
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from browser.cdp import CDP_PORT, get_cdp_browser_context, get_clean_page

# ── Configuration ─────────────────────────────────────────────────────────────

# The Chrome profile on port 9222 that's logged into the channel; only used if Chrome isn't running yet
PROFILE_DIR        = Path.home() / "Desktop" / "browser-profiles" / "p9222"
CHANNEL_CACHE_FILE = Path(__file__).resolve().parent / "cache" / "my_channel.json"
ANALYTICS_URL      = "https://studio.youtube.com/video/{video_id}/analytics/tab-reach_viewers/period-default"
CHANNEL_URL        = "https://studio.youtube.com/channel/{channel_id}/analytics/tab-content/period-default"
GET_CARDS_PATH     = "/youtubei/v1/yta_web/get_cards?alt=json"
REQUEST_TIMEOUT_MS = 30000
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

# Request headers the browser sets itself; fetch() refuses or ignores them
BROWSER_HEADERS = {"cookie", "content-length", "accept-encoding", "user-agent", "origin", "referer", "priority"}

FETCH_JS = """async ([path, headers, body]) => {
    const response = await fetch(path, {method: "POST", headers, body, credentials: "include"});
    return [response.status, await response.text()];
}"""


class StudioError(Exception):
    """Raised when Studio can't be reached or doesn't return analytics."""


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

def _is_studio_api_request(request) -> bool:
    # Studio's first authorized API call carries the context get_cards needs (client, delegation)
    if "/youtubei/v1/" not in request.url or "authorization" not in request.headers:
        return False
    body = request.post_data_buffer
    return body is not None and b"delegationContext" in body


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


async def _scrape(
    landing_url: str, channel_id: str, requests: dict[str, Callable[[dict], dict]], port: int,
) -> tuple[str, dict[str, dict]]:
    """
    Open Studio at landing_url, then make one get_cards call per entry in requests (each
    builds its body from Studio's context). Returns (logged-in channel ID, raw response
    per key), with no responses when channel_id isn't the logged-in channel.
    """
    async with async_playwright() as p:
        context = await get_cdp_browser_context(p, profile_path=PROFILE_DIR, port=port)
        page = await get_clean_page(context)
        try:
            try:
                async with page.expect_request(_is_studio_api_request, timeout=REQUEST_TIMEOUT_MS) as info:
                    await page.goto(landing_url, wait_until="commit")
                request = await info.value
            except PlaywrightTimeoutError:
                if "accounts.google.com" in page.url:
                    raise StudioError("the p9222 Chrome profile isn't logged into YouTube Studio.")
                raise StudioError(f"Studio didn't load in {REQUEST_TIMEOUT_MS // 1000}s ({page.url}).")

            studio_context = json.loads(request.post_data_buffer)["context"]
            logged_in_channel_id = studio_context["user"]["delegationContext"]["externalChannelId"]
            if logged_in_channel_id != channel_id:
                return logged_in_channel_id, {}
            headers = {
                name: value for name, value in (await request.all_headers()).items()
                if not name.startswith((":", "sec-")) and name not in BROWSER_HEADERS
            }
            results = {}
            for key, build in requests.items():
                body = json.dumps(build(studio_context))
                results[key] = await page.evaluate(FETCH_JS, [GET_CARDS_PATH, headers, body])
        finally:
            await page.close()

    responses = {}
    for key, (status, text) in results.items():
        if status != 200:
            try:
                message = json.loads(text)["error"]["message"]
            except (ValueError, KeyError, TypeError):
                message = text[:200]
            raise StudioError(f"Studio returned HTTP {status} for {key}: {message}")
        responses[key] = json.loads(text)
    return logged_in_channel_id, responses


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


def fetch_studio(
    channel_id: str, video_ids: list[str] = (), channel_totals: bool = False, port: int = CDP_PORT,
) -> Optional[dict]:
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

    landing_url = (
        ANALYTICS_URL.format(video_id=video_ids[0]) if video_ids else CHANNEL_URL.format(channel_id=channel_id)
    )
    logged_in_channel_id, responses = asyncio.run(_scrape(landing_url, channel_id, requests, port))
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


def fetch_studio_analytics(video_id: str, video_channel_id: str, port: int = CDP_PORT) -> Optional[dict]:
    """
    Live totals plus one row per finished day for a video on the logged-in channel, or None
    when the video belongs to another channel. CTR is a percentage (4.2 means 4.2%).
    """
    result = fetch_studio(video_channel_id, [video_id], port=port)
    return None if result is None else result["videos"][video_id]
