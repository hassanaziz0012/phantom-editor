"""
Studio analytics: any metric by any dimension, for the channel, videos or playlists.

query() calls yta_web/join, the engine behind Studio's Advanced Mode: a list of dimensions,
a list of metrics, filters ("restricts") and a date range, answered as a table. REPORTS
names the useful combinations so a command like `phantom studio report traffic` needs no
knowledge of the API. Retention, typical ranges and Studio's other panels are in insights.py.

Values come back as Studio reports them, except durations (milliseconds) which are turned
into seconds and get a _sec suffix. Percentages are 0-100 (CTR 4.2 means 4.2%).
"""

import re
from datetime import date, datetime, timedelta
from typing import Optional

from youtube_api.studio.session import Studio, StudioError

DEFAULT_PERIOD = "28d"
MAX_ROWS = 10000   # join ignores paging (any pageOffset is a 400), so one big page
TIME_DIMENSIONS = {"MINUTE", "HOUR", "DAY", "WEEK", "WEEK_MONDAY", "MONTH", "QUARTER", "YEAR"}
FIRST_DAY = date(2005, 4, 23)   # YouTube's first upload; "lifetime" over time starts here

# Core metric sets
VIEW_METRICS = ["EXTERNAL_VIEWS", "EXTERNAL_WATCH_TIME", "AVERAGE_WATCH_TIME", "AVERAGE_WATCH_PERCENTAGE"]
REACH_METRICS = ["VIDEO_THUMBNAIL_IMPRESSIONS", "VIDEO_THUMBNAIL_IMPRESSIONS_VTR"]
ENGAGEMENT_METRICS = ["RATINGS_LIKES", "RATINGS_DISLIKES", "COMMENTS", "SHARINGS", "SUBSCRIBERS_GAINED", "SUBSCRIBERS_LOST"]

# name -> (description, dimensions, metrics, extra filters, order metric)
REPORTS = {
    "overview":       ("Channel or video totals", [], REACH_METRICS + VIEW_METRICS + ["ESTIMATED_UNIQUE_VIEWERS"] + ENGAGEMENT_METRICS + ["SUBSCRIBERS_NET_CHANGE"], {}, None),
    "daily":          ("One row per day", ["DAY"], REACH_METRICS + VIEW_METRICS + ["SUBSCRIBERS_NET_CHANGE"], {}, None),
    "weekly":         ("One row per week", ["WEEK"], REACH_METRICS + VIEW_METRICS + ["SUBSCRIBERS_NET_CHANGE"], {}, None),
    "monthly":        ("One row per month", ["MONTH"], REACH_METRICS + VIEW_METRICS + ["SUBSCRIBERS_NET_CHANGE"], {}, None),
    "videos":         ("Every video, Short and live stream ranked by views", ["VIDEO"], REACH_METRICS + VIEW_METRICS + ENGAGEMENT_METRICS + ["SUBSCRIBERS_NET_CHANGE"], {}, "EXTERNAL_VIEWS"),
    "long-form":      ("Long-form videos ranked by views", ["VIDEO"], REACH_METRICS + VIEW_METRICS + ENGAGEMENT_METRICS + ["SUBSCRIBERS_NET_CHANGE"], {"CREATOR_CONTENT_TYPE": ["VIDEO_ON_DEMAND"]}, "EXTERNAL_VIEWS"),
    "shorts":         ("Shorts ranked by views, with Shorts feed swipe-through", ["VIDEO"], ["SHORTS_FEED_IMPRESSIONS", "SHORTS_FEED_IMPRESSIONS_VTR", "ENGAGED_VIEWS"] + VIEW_METRICS + ENGAGEMENT_METRICS, {"CREATOR_CONTENT_TYPE": ["SHORTS"]}, "EXTERNAL_VIEWS"),
    "live":           ("Live streams ranked by views", ["VIDEO"], VIEW_METRICS + ENGAGEMENT_METRICS, {"CREATOR_CONTENT_TYPE": ["LIVE_STREAM"]}, "EXTERNAL_VIEWS"),
    "content-type":   ("Long-form vs Shorts vs live vs posts", ["CREATOR_CONTENT_TYPE"], VIEW_METRICS + ["SUBSCRIBERS_NET_CHANGE"], {}, "EXTERNAL_VIEWS"),
    "traffic":        ("Traffic source types (browse, search, suggested...)", ["TRAFFIC_SOURCE_TYPE"], REACH_METRICS + VIEW_METRICS, {}, "EXTERNAL_VIEWS"),
    "search-terms":   ("YouTube search terms that led to views", ["TRAFFIC_SOURCE_DETAIL"], VIEW_METRICS, {"TRAFFIC_SOURCE_TYPE": ["YT_SEARCH"]}, "EXTERNAL_VIEWS"),
    "suggested":      ("Videos whose suggestions sent views", ["TRAFFIC_SOURCE_DETAIL"], VIEW_METRICS, {"TRAFFIC_SOURCE_TYPE": ["YT_RELATED"]}, "EXTERNAL_VIEWS"),
    "external":       ("External websites and apps that sent views", ["TRAFFIC_SOURCE_DETAIL"], VIEW_METRICS, {"TRAFFIC_SOURCE_TYPE": ["EXT_URL"]}, "EXTERNAL_VIEWS"),
    "playlists":      ("Playlists ranked by playlist views", ["PLAYLIST"], ["PLAYLIST_VIEWS", "PLAYLIST_STARTS", "PLAYLIST_WATCH_TIME_HOURS", "PLAYLIST_AVERAGE_VIEWS_PER_START", "PLAYLIST_SAVES_NET_CHANGE"], {}, "PLAYLIST_VIEWS"),
    "countries":      ("Views by country", ["COUNTRY"], VIEW_METRICS + ["SUBSCRIBERS_NET_CHANGE"], {}, "EXTERNAL_VIEWS"),
    "cities":         ("Views by city", ["CITY"], VIEW_METRICS, {}, "EXTERNAL_VIEWS"),
    "age":            ("Viewer age groups (share of views)", ["VIEWER_AGE"], ["EXTERNAL_VIEWS%", "EXTERNAL_WATCH_TIME%", "AVERAGE_WATCH_TIME", "AVERAGE_WATCH_PERCENTAGE"], {}, None),
    "gender":         ("Viewer gender (share of views)", ["VIEWER_GENDER"], ["EXTERNAL_VIEWS%", "EXTERNAL_WATCH_TIME%", "AVERAGE_WATCH_TIME", "AVERAGE_WATCH_PERCENTAGE"], {}, None),
    "devices":        ("Device types (mobile, computer, TV...)", ["DEVICE_PLATFORM_TYPE"], VIEW_METRICS, {}, "EXTERNAL_VIEWS"),
    "os":             ("Operating systems", ["DEVICE_OS_TYPE"], VIEW_METRICS, {}, "EXTERNAL_VIEWS"),
    "playback":       ("Where videos were watched (YouTube, embeds...)", ["PLAYBACK_LOCATION_TYPE"], VIEW_METRICS, {}, "EXTERNAL_VIEWS"),
    "subscribed":     ("Subscribed vs not subscribed viewers", ["SUBSCRIBED_TO_UPLOADER_STATE"], VIEW_METRICS, {}, "EXTERNAL_VIEWS"),
    "loyalty":        ("New, casual and regular viewers", ["AUDIENCE_LOYALTY_SEGMENT"], VIEW_METRICS, {}, "EXTERNAL_VIEWS"),
    "sub-sources":    ("Where subscribers were gained", ["SUBSCRIPTION_SOURCE_TYPE"], ["SUBSCRIBERS_NET_CHANGE", "SUBSCRIBERS_GAINED", "SUBSCRIBERS_LOST"], {}, "SUBSCRIBERS_NET_CHANGE"),
    "sub-videos":     ("Videos that gained the most subscribers", ["VIDEO"], ["SUBSCRIBERS_NET_CHANGE", "SUBSCRIBERS_GAINED", "SUBSCRIBERS_LOST", "EXTERNAL_VIEWS"], {}, "SUBSCRIBERS_NET_CHANGE"),
    "sharing":        ("Where viewers shared videos", ["SHARING_SERVICE"], ["SHARINGS"], {}, "SHARINGS"),
    "end-screens":    ("End screen element performance", ["ENDSCREEN_ELEMENT_TYPE"], ["ENDSCREEN_ELEMENT_IMPRESSIONS", "ENDSCREEN_ELEMENT_CLICKS", "ENDSCREEN_ELEMENT_CLICK_RATE"], {}, "ENDSCREEN_ELEMENT_IMPRESSIONS"),
    "cards":          ("Info card performance", ["INFO_CARD_TYPE"], ["INFO_CARD_TEASER_CLICKS", "INFO_CARD_TEASER_CLICK_RATE", "INFO_CARD_CLICKS", "INFO_CARD_CLICK_RATE"], {}, None),
    "captions":       ("Views by subtitle/CC language", ["CAPTION_LANGUAGE"], VIEW_METRICS, {}, "EXTERNAL_VIEWS"),
    "audio-tracks":   ("Views by audio track language", ["AUDIO_TRACK_LANGUAGE"], VIEW_METRICS, {}, "EXTERNAL_VIEWS"),
    "posts":          ("Community posts", ["POST"], ["POST_IMPRESSIONS", "POST_LIKES", "POST_VOTES", "POST_LIKES_PER_IMPRESSIONS"], {}, "POST_IMPRESSIONS"),
    "revenue":        ("Estimated revenue by day (monetized channels only)", ["DAY"], ["TOTAL_ESTIMATED_EARNINGS", "AD_GROSS_REVENUE", "CPM", "IMPRESSIONS_CPM"], {}, None),
    "realtime":       ("Views per hour over the last 48 hours", ["HOUR"], ["EXTERNAL_VIEWS"], {}, None),
    "realtime-videos": ("Top content over the last 48 hours", ["VIDEO"], ["EXTERNAL_VIEWS"], {}, "EXTERNAL_VIEWS"),
    "realtime-minutes": ("Views per minute over the last 60 minutes", ["MINUTE"], ["EXTERNAL_VIEWS"], {}, None),
}
REALTIME_PERIODS = {"realtime": "48h", "realtime-videos": "48h", "realtime-minutes": "60m"}


# ── Periods ───────────────────────────────────────────────────────────────────

def _date_id(d: date) -> int:
    return d.year * 10000 + d.month * 100 + d.day


def time_range(period: str) -> dict:
    """
    A join timeRange for: 7d/28d/90d/365d (whole days up to yesterday, like Studio's
    "Last 28 days"), 48h/60m (realtime, up to now), lifetime, a year (2026), a month
    (2026-11), or a range 2026-11-01:2026-11-19 (both days included).
    """
    p = period.strip().lower()
    today = date.today()
    if m := re.fullmatch(r"(\d+)d", p):
        return {"dateIdRange": {"inclusiveStart": _date_id(today - timedelta(days=int(m[1]))), "exclusiveEnd": _date_id(today)}}
    if m := re.fullmatch(r"(\d+)([hm])", p):
        # Studio only takes whole hours (or minutes); end after the current one so it's included
        unit = 3600 if m[2] == "h" else 60
        end = (int(datetime.now().timestamp()) // unit + 1) * unit
        return {"unixTimeRange": {"inclusiveStart": str(end - int(m[1]) * unit), "exclusiveEnd": str(end)}}
    if p in ("lifetime", "all"):
        return {"unboundedRange": {}}
    if m := re.fullmatch(r"(\d{4})", p):
        y = int(m[1])
        return {"dateIdRange": {"inclusiveStart": _date_id(date(y, 1, 1)), "exclusiveEnd": _date_id(date(y + 1, 1, 1))}}
    if m := re.fullmatch(r"(\d{4})-(\d{2})", p):
        start = date(int(m[1]), int(m[2]), 1)
        end = (start + timedelta(days=32)).replace(day=1)
        return {"dateIdRange": {"inclusiveStart": _date_id(start), "exclusiveEnd": _date_id(end)}}
    if m := re.fullmatch(r"(\d{4}-\d{2}-\d{2}):(\d{4}-\d{2}-\d{2})", p):
        start, end = date.fromisoformat(m[1]), date.fromisoformat(m[2])
        return {"dateIdRange": {"inclusiveStart": _date_id(start), "exclusiveEnd": _date_id(end + timedelta(days=1))}}
    raise ValueError(f"unknown period {period!r}; use 7d, 28d, 90d, 365d, 48h, 60m, lifetime, 2026, 2026-11 or 2026-11-01:2026-11-19")


# ── Tables ────────────────────────────────────────────────────────────────────

# Studio's internal TRAFFIC_SOURCE_TYPE values and the names its UI shows for them.
# SUBSCRIBER in particular is "Browse features" (home feed, subscriptions feed, Watch later),
# not "subscribers only". Rows keep the raw value for --filter and get a traffic_source column too.
TRAFFIC_SOURCE_NAMES = {
    "SUBSCRIBER":               "Browse features",
    "YT_RELATED":               "Suggested videos",
    "YT_SEARCH":                "YouTube search",
    "EXT_URL":                  "External",
    "UNKNOWN_MOBILE_OR_DIRECT": "Direct or unknown",
    "PLAYLIST":                 "Playlists",
    "YT_PLAYLIST_PAGE":         "Playlist page",
    "YT_CHANNEL":               "Channel pages",
    "YT_OTHER_PAGE":            "Other YouTube features",
    "NOTIFICATION":             "Notifications",
    "END_SCREEN":               "End screens",
    "ANNOTATION":               "Cards and annotations",
    "SHORTS":                   "Shorts feed",
    "NO_LINK_EMBEDDED":         "Embedded players",
    "ADVERTISING":              "YouTube advertising",
    "HASHTAGS":                 "Hashtag pages",
}

def _values(column: dict) -> tuple[str, list]:
    for kind, v in column.items():
        if isinstance(v, dict) and "values" in v:
            values = list(v["values"])
            for i in column.get("undefinedValueIndices", []):
                if i < len(values):
                    values[i] = None
            return kind, values
    return "", []


def _dimension_value(kind: str, value):
    if value is None:
        return None
    if kind == "dateIds":
        return f"{value // 10000}-{value // 100 % 100:02}-{value % 100:02}"
    if kind == "timestamps":
        return datetime.fromtimestamp(int(value) / 1000).isoformat(timespec="minutes")
    return value


def parse_table(table: dict) -> list[dict]:
    """A Studio result table as a list of rows (dicts), durations in seconds."""
    columns = []
    for col in table.get("dimensionColumns", []):
        kind, values = _values(col)
        dim = col["dimension"]["type"]
        parsed = [_dimension_value(kind, v) for v in values]
        if dim == "TRAFFIC_SOURCE_TYPE":
            columns.append(("traffic_source", [TRAFFIC_SOURCE_NAMES.get(v, v) for v in parsed]))
        columns.append((dim.lower(), parsed))
    for col in table.get("metricColumns", []):
        kind, values = _values(col)
        metric = col["metric"]
        name = metric["type"].lower() + ("_pct_of_total" if metric.get("asPercentagesOfTotal") else "")
        if kind == "milliseconds":
            name = name.replace("_hours", "") + "_sec"
            values = [None if v is None else round(int(v) / 1000, 1) for v in values]
        elif kind == "micros":
            values = [None if v is None else int(v) / 1e6 for v in values]
        columns.append((name, values))
    n = max((len(v) for _, v in columns), default=0)
    return [{name: (values[i] if i < len(values) else None) for name, values in columns} for i in range(n)]


# ── Queries ───────────────────────────────────────────────────────────────────

def _metric(name: str) -> dict:
    # A trailing % asks for the metric as a share of the total, like Studio's demographics
    if name.endswith("%"):
        return {"type": name[:-1], "asPercentagesOfTotal": True}
    return {"type": name}


def query(
    studio: Studio,
    dimensions: list[str],
    metrics: list[str],
    *,
    videos: list[str] = (),
    playlist: Optional[str] = None,
    filters: Optional[dict[str, list[str]]] = None,
    period: str = DEFAULT_PERIOD,
    order_by: Optional[str] = None,
    ascending: bool = False,
    limit: Optional[int] = 50,
) -> list[dict]:
    """
    Rows of metrics by dimensions for the channel (default), some videos or a playlist.
    filters maps a dimension to the values to keep, e.g. {"TRAFFIC_SOURCE_TYPE": ["YT_SEARCH"]}.
    limit=None fetches every row.
    """
    if videos:
        restricts = [{"dimension": {"type": "VIDEO"}, "inValues": list(videos)}]
    elif playlist:
        restricts = [{"dimension": {"type": "PLAYLIST"}, "inValues": [playlist]}]
    else:
        restricts = [{"dimension": {"type": "USER"}, "inValues": [studio.channel_id]}]
    for dim, values in (filters or {}).items():
        restricts.append({"dimension": {"type": dim}, "inValues": list(values)})

    orders = []
    if order_by:
        direction = "ANALYTICS_ORDER_DIRECTION_ASC" if ascending else "ANALYTICS_ORDER_DIRECTION_DESC"
        key = {"dimension": {"type": order_by}} if order_by in dimensions else {"metric": {"type": order_by.rstrip("%")}}
        orders.append({**key, "direction": direction})
    elif time_dims := [d for d in dimensions if d in TIME_DIMENSIONS]:
        orders.append({"dimension": {"type": time_dims[0]}, "direction": "ANALYTICS_ORDER_DIRECTION_ASC"})

    tr = time_range(period)
    if "unboundedRange" in tr and set(dimensions) & TIME_DIMENSIONS:
        # Studio refuses an open-ended range when rows are per day/week/month
        tr = {"dateIdRange": {"inclusiveStart": _date_id(FIRST_DAY), "exclusiveEnd": _date_id(date.today() + timedelta(days=1))}}

    q = {
        "dimensions": [{"type": d} for d in dimensions],
        "metrics": [_metric(m) for m in metrics],
        "restricts": restricts,
        "orders": orders,
        "timeRange": tr,
        "currency": "USD",
        "returnDataInNewFormat": True,
    }
    if dimensions:
        q["limit"] = {"pageSize": min(limit or MAX_ROWS, MAX_ROWS), "pageOffset": 0}
    response = studio.call("yta_web/join", {"nodes": [{"key": "q", "value": {"query": q}}]})
    try:
        table = response["results"][0]["value"]["resultTable"]
    except (KeyError, IndexError) as e:
        raise StudioError(f"unexpected join response: {str(response)[:300]}") from e
    return parse_table(table)


def report(studio: Studio, name: str, *, period: Optional[str] = None, **kwargs) -> list[dict]:
    """Run one of REPORTS. kwargs (videos, playlist, limit, filters) go to query()."""
    _, dims, metrics, filters, order = REPORTS[name]
    period = REALTIME_PERIODS.get(name) or period or DEFAULT_PERIOD
    merged = {**filters, **(kwargs.pop("filters", None) or {})}
    rows = query(studio, dims, metrics, filters=merged, period=period, order_by=order, **kwargs)
    if name in ("search-terms", "suggested", "external"):
        for row in rows:
            detail = row.get("traffic_source_detail")
            if isinstance(detail, str) and "." in detail:
                row["traffic_source_detail"] = detail.split(".", 1)[1]
    return rows
