"""
YouTube Studio Analytics (owner-only stats)
===========================================
Live owner-only stats for a video on your own channel, read from YouTube Studio:
impressions, click-through rate, views, watch time, and subscribers, as lifetime totals
and day by day.

The public APIs lag two to three days behind, so this asks Studio itself. It opens the
video's analytics page in the logged-in Chrome on port 9222 (the p9222 profile), copies
the auth headers and context from Studio's first API request, and makes one get_cards
call for exactly the metrics it needs. Totals are live; daily rows cover finished days.

The logged-in channel's ID is cached in cache/my_channel.json, so videos on other
channels are recognized without opening Chrome.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

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


async def _scrape(video_id: str, video_channel_id: str, port: int) -> tuple[str, Optional[dict]]:
    """
    Return (logged-in channel ID, raw get_cards response), with no response when the
    video belongs to another channel.
    """
    async with async_playwright() as p:
        context = await get_cdp_browser_context(p, profile_path=PROFILE_DIR, port=port)
        page = await get_clean_page(context)
        try:
            try:
                async with page.expect_request(_is_studio_api_request, timeout=REQUEST_TIMEOUT_MS) as info:
                    await page.goto(ANALYTICS_URL.format(video_id=video_id), wait_until="commit")
                request = await info.value
            except PlaywrightTimeoutError:
                if "accounts.google.com" in page.url:
                    raise StudioError("the p9222 Chrome profile isn't logged into YouTube Studio.")
                raise StudioError(f"Studio didn't load in {REQUEST_TIMEOUT_MS // 1000}s ({page.url}).")

            studio_context = json.loads(request.post_data_buffer)["context"]
            channel_id = studio_context["user"]["delegationContext"]["externalChannelId"]
            if channel_id != video_channel_id:
                return channel_id, None
            headers = {
                name: value for name, value in (await request.all_headers()).items()
                if not name.startswith((":", "sec-")) and name not in BROWSER_HEADERS
            }
            body = json.dumps(_build_cards_request(video_id, studio_context))
            status, text = await page.evaluate(FETCH_JS, [GET_CARDS_PATH, headers, body])
        finally:
            await page.close()

    if status != 200:
        try:
            message = json.loads(text)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = text[:200]
        raise StudioError(f"Studio returned HTTP {status}: {message}")
    return channel_id, json.loads(text)


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

def fetch_studio_analytics(video_id: str, video_channel_id: str, port: int = CDP_PORT) -> Optional[dict]:
    """
    Live totals plus one row per finished day for a video on the logged-in channel, or None
    when the video belongs to another channel. CTR is a percentage (4.2 means 4.2%).
    """
    channel_id, response = asyncio.run(_scrape(video_id, video_channel_id, port))
    _cache_channel_id(channel_id)
    if response is None:
        return None

    totals, daily = _parse_cards(response)
    if not totals:
        raise StudioError("Studio's response had no analytics for this video.")
    return {"totals": totals, "daily": daily}
