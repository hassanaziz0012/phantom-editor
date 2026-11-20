"""
Studio's own comparisons and panels, read from its analytics cards (yta_web/get_cards):

* retention()          a video's retention curve with the typical band for comparable
                       videos, key moments (intro, dips, spikes) and chapters
* channel_retention()  the same summary for every recent video, plus channel averages
* typical()            totals vs "typical" (Studio's usual range) and the previous period
* funnel()             impressions -> views -> watch time, overall and per surface
* ranking()            how a video ranks against your recent uploads, metric by metric
* audience()           when your viewers are on YouTube, what else they watch
* research()           Studio's Research/Inspiration feed: search queries (with volume and
                       content gaps) and relevant videos from other channels

Studio hides some panels below a minimum audience size; those come back empty with
"not_enough_data", exactly as Studio shows "Not enough data".
"""

from datetime import datetime
from typing import Optional

from youtube_api.studio.analytics import parse_table
from youtube_api.studio.session import Studio, StudioError

# --period -> Studio's card time periods (cards don't take arbitrary ranges)
CARD_PERIODS = {
    "7d": "ANALYTICS_TIME_PERIOD_TYPE_WEEK",
    "28d": "ANALYTICS_TIME_PERIOD_TYPE_FOUR_WEEKS",
    "60d": "ANALYTICS_TIME_PERIOD_TYPE_LAST_SIXTY_DAYS",
    "90d": "ANALYTICS_TIME_PERIOD_TYPE_QUARTER",
    "365d": "ANALYTICS_TIME_PERIOD_TYPE_YEAR",
    "lifetime": "ANALYTICS_TIME_PERIOD_TYPE_LIFETIME",
}
TYPICAL_METRICS = [
    "EXTERNAL_VIEWS", "EXTERNAL_WATCH_TIME", "AVERAGE_WATCH_TIME", "AVERAGE_WATCH_PERCENTAGE",
    "VIDEO_THUMBNAIL_IMPRESSIONS", "VIDEO_THUMBNAIL_IMPRESSIONS_VTR", "SUBSCRIBERS_NET_CHANGE",
]
DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


# ── Card requests ─────────────────────────────────────────────────────────────

def _scope(studio: Studio, video_id: Optional[str], period: Optional[str]) -> tuple[dict, dict]:
    if video_id:
        if period in (None, "lifetime", "since-publish"):
            return {"videoId": video_id}, {
                "timePeriodType": "ANALYTICS_TIME_PERIOD_TYPE_SINCE_PUBLISH",
                "referencePoint": "TIME_PERIOD_REFERENCE_POINT_SINCE_PUBLISH",
                "entity": {"videoId": video_id},
            }
        entity = {"videoId": video_id}
    else:
        entity = {"channelId": studio.channel_id}
    key = period or "28d"
    if key not in CARD_PERIODS:
        raise ValueError(f"this command takes --period {', '.join(CARD_PERIODS)}")
    return entity, {"timePeriodType": CARD_PERIODS[key]}


def card(studio: Studio, config_name: str, config: Optional[dict] = None, *,
         video_id: Optional[str] = None, period: Optional[str] = None) -> dict:
    """One analytics card's response (the card dict, minus Studio's bookkeeping)."""
    entity, time_period = _scope(studio, video_id, period)
    response = studio.call("yta_web/get_cards", {
        "screenConfig": {
            "entity": entity, "timePeriod": time_period, "currency": "USD",
            "timeZoneOffsetSecs": int(datetime.now().astimezone().utcoffset().total_seconds()),
        },
        "cardConfigs": [{"failureMode": "ANALYTICS_CARD_FAILURE_MODE_FAIL_PAGE", config_name: config or {}}],
    })
    result = (response.get("cards") or [{}])[0]
    if result.get("isFailed"):
        raise StudioError(f"Studio couldn't build the {config_name} card ({result.get('statusCode', 'no status')}).")
    return result


def _ms(value) -> Optional[float]:
    return None if value is None else round(int(value) / 1000, 1)


# ── Retention ─────────────────────────────────────────────────────────────────

def _moment(h: dict) -> dict:
    r = h.get("range", {})
    return {
        "type": h.get("type", "").replace("HIGHLIGHT_TYPE_", "").lower(),
        "start_sec": round(r.get("startSec", 0), 1),
        "end_sec": round(r["endSec"], 1) if "endSec" in r else None,
        "score": round(h["score"], 2) if "score" in h else None,
        "vs_your_average": (h.get("scoreComparison") or "").replace("HIGHLIGHT_SCORE_COMPARISON_", "").lower() or None,
    }


def _retention_entry(v: dict, length: Optional[int] = None) -> dict:
    """One video's entry from the retention highlights card, summarized and with its curve."""
    values = v.get("retentionValues") or []
    typical = v.get("typicalPerformance") or {}
    low, high = typical.get("lowerBoundaryValues") or [], typical.get("upperBoundaryValues") or []
    totals = v.get("metricTotals") or {}
    if not length and v.get("chapters"):
        length = v["chapters"][-1].get("endSec")
    if not length and totals.get("avgPercentageWatched") and totals.get("avgViewDurationMillis"):
        # Average view duration is that share of the video
        length = round(int(totals["avgViewDurationMillis"]) / 1000 / totals["avgPercentageWatched"])

    curve = []
    for i, value in enumerate(values):
        point = {"position_pct": i}
        if length:
            point["position_sec"] = round(length * i / 100, 1)
        point["retention_pct"] = value
        if low:
            point["typical_low"], point["typical_high"] = round(low[i], 2), round(high[i], 2)
            point["vs_typical"] = "above" if value > high[i] else "below" if value < low[i] else "within"
        curve.append(point)

    summary = {
        "video_id": v.get("videoId"),
        "length_sec": length,
        "views": totals.get("views"),
        "avg_view_duration_sec": _ms(totals.get("avgViewDurationMillis")),
        "avg_pct_watched": round(totals["avgPercentageWatched"] * 100, 2) if "avgPercentageWatched" in totals else None,
        # "Still watching at 0:30" — Studio's intro metric
        "retention_at_30s": None,
        "has_typical": bool(low),
    }
    if length and values:
        summary["retention_at_30s"] = values[min(99, int(30 / length * 100))]
    if low:
        counts = {k: sum(1 for p in curve if p["vs_typical"] == k) for k in ("above", "within", "below")}
        summary["points_vs_typical"] = counts
        i = min(99, int(30 / length * 100)) if length else None
        summary["typical_retention_at_30s"] = [round(low[i], 2), round(high[i], 2)] if length else None
    moments = [_moment(h) for h in v.get("highlights", [])]
    summary["key_moments"] = sorted(moments, key=lambda m: m["start_sec"])
    summary["chapters"] = [{"start_sec": c.get("startSec", 0), "end_sec": c.get("endSec"), "title": c.get("title")}
                           for c in v.get("chapters", [])] or None
    summary["curve"] = curve
    return summary


def retention(studio: Studio, video_id: str) -> dict:
    """Lifetime retention for one video, with the typical band, key moments and chapters."""
    data = card(studio, "audienceRetentionHighlightsCardConfig", video_id=video_id).get("audienceRetentionHighlightsCardData")
    videos = (data or {}).get("videosData") or []
    entry = next((v for v in videos if v.get("videoId") == video_id), None)
    if not entry or not entry.get("retentionValues"):
        raise StudioError(f"no retention data for {video_id} (too few views, or not your video).")
    return _retention_entry(entry)


def channel_retention(studio: Studio, period: str = "28d", curves: bool = False) -> dict:
    """Retention for the channel's recent long-form videos, and channel-wide averages."""
    data = card(studio, "audienceRetentionHighlightsCardConfig", period=period).get("audienceRetentionHighlightsCardData") or {}
    videos = [_retention_entry(v) for v in data.get("videosData", []) if v.get("retentionValues")]
    if not curves:
        for v in videos:
            v.pop("curve")

    def avg(key):
        values = [v[key] for v in videos if v.get(key) is not None]
        return round(sum(values) / len(values), 2) if values else None

    with_typical = [v for v in videos if v.get("points_vs_typical")]
    return {
        "period": period,
        "videos_count": len(videos),
        "avg_pct_watched": avg("avg_pct_watched"),
        "avg_view_duration_sec": avg("avg_view_duration_sec"),
        "avg_retention_at_30s": avg("retention_at_30s"),
        "videos_mostly_above_typical": sum(1 for v in with_typical if v["points_vs_typical"]["above"] > v["points_vs_typical"]["below"]),
        "videos_mostly_below_typical": sum(1 for v in with_typical if v["points_vs_typical"]["below"] > v["points_vs_typical"]["above"]),
        "videos": videos,
    }


# ── Typical vs actual ─────────────────────────────────────────────────────────

def typical(studio: Studio, video_id: Optional[str] = None, period: Optional[str] = None, series: bool = False) -> list[dict]:
    """
    Each metric's total next to Studio's "typical" value and range for it (how your
    comparable videos or periods usually do) and the previous period's total.
    Studio only computes typical for some metrics; the rest have None there.
    """
    result = card(studio, "keyMetricCardConfig", {
        "timePeriod": {}, "metricTabConfigs": [{"metric": m} for m in TYPICAL_METRICS],
    }, video_id=video_id, period=period)
    rows = []
    for tab in result.get("keyMetricCardData", {}).get("keyMetricTabs", []):
        c = tab.get("primaryContent", {})
        metric = c.get("metric", "")
        ms = metric in ("AVERAGE_WATCH_TIME", "EXTERNAL_WATCH_TIME")
        conv = (lambda x: _ms(x)) if ms else (lambda x: x)
        t = c.get("typicalPerformanceTotal") or {}
        rng = t.get("typicalRange") or {}
        total = conv(c.get("total"))
        # Studio leaves out zero values (protobuf defaults), so a range with one bound has 0 for the other
        low, high = (conv(rng.get("lowerBound", 0)), conv(rng.get("upperBound", 0))) if rng else (None, None)
        row = {
            "metric": metric.lower() + ("_sec" if ms else ""),
            "total": total,
            "typical": conv(t.get("typicalValue", 0)) if t else None,
            "typical_low": low,
            "typical_high": high,
            "vs_typical": None if low is None or total is None else "above" if total > high else "below" if total < low else "within",
            "previous_period": conv(c.get("previousTotal")),
        }
        if series and c.get("typicalPerformanceSeries"):
            actual = {p["x"]: p.get("y") for p in (c.get("mainSeries") or {}).get("datums", [])}
            row["typical_series"] = [
                {"time": datetime.fromtimestamp(int(d["x"]) / 1000).isoformat(timespec="minutes"),
                 "actual": conv(actual.get(d["x"])), "low": conv(d.get("lowerY")), "high": conv(d.get("upperY"))}
                for d in c["typicalPerformanceSeries"].get("datums", [])
            ]
        rows.append(row)
    return rows


# ── Funnel, ranking ───────────────────────────────────────────────────────────

def funnel(studio: Studio, video_id: Optional[str] = None, period: Optional[str] = None) -> list[dict]:
    """Impressions, CTR, views and watch time from impressions: overall and per surface."""
    data = card(studio, "funnelCardConfig", video_id=video_id, period=period).get("funnelCardData") or {}
    rows = []
    for key, label in (("totalData", "all"), ("homePageTrafficData", "home"), ("watchPageTrafficData", "suggested (watch page)"), ("trendingTrafficData", "trending")):
        if key in data:
            row = (parse_table(data[key]) or [{}])[0]
            rows.append({"surface": label, **row})
    return rows


def ranking(studio: Studio, video_id: str) -> dict:
    """How the video ranks among your recent uploads (Studio's "Ranks x of 10"), and its typical ranges."""
    data = card(studio, "entitySnapshotCardConfig", video_id=video_id).get("entitySnapshotCardData") or {}
    entities = (data.get("ranking") or {}).get("entities", [])
    metrics = []
    for row in (data.get("metricsTable") or {}).get("metricRows", []):
        metric = row.get("metric", {}).get("type", "")
        # Same units as everywhere else: seconds for durations, 0-100 for rates
        if metric in ("AVERAGE_WATCH_TIME", "EXTERNAL_WATCH_TIME"):
            name, conv = metric.lower() + "_sec", _ms
        elif metric.endswith("_VTR"):
            name, conv = metric.lower(), (lambda x: None if x is None else round(x * 100, 2))
        else:
            name, conv = metric.lower(), (lambda x: x)
        entry = {"metric": name, "value": conv((row.get("value") or {}).get("double")), "typical_low": None, "typical_high": None}
        for k, v in row.items():
            if k.startswith("typical") and isinstance(v, dict):
                rng = v.get("typicalRange") or v
                if "lowerBound" in rng or "upperBound" in rng:
                    entry["typical_low"], entry["typical_high"] = conv(rng.get("lowerBound", 0)), conv(rng.get("upperBound", 0))
        metrics.append(entry)
    return {
        "video_id": video_id,
        "rank": next((e["rank"] for e in entities if e.get("entity", {}).get("videoId") == video_id), None),
        "out_of": len(entities),
        "ranked_by": (entities[0].get("metric", {}).get("type", "").lower() if entities else None),
        "recent_videos": [{"rank": e.get("rank"), "video_id": e.get("entity", {}).get("videoId"),
                           "value": (e.get("value") or {}).get("double")} for e in entities],
        "metrics": metrics,
    }


# ── Audience ──────────────────────────────────────────────────────────────────

def audience(studio: Studio, period: str = "28d") -> dict:
    """When your viewers are on YouTube (local time) and what else your audience watches."""
    online = (card(studio, "audienceOnlineCardConfig", period=period).get("audienceOnlineCardData") or {}).get("hourlyUsersOnline") or []
    when = None
    if any(online):
        # 168 values: one per hour of the week, in your local time
        when = [{"day": DAYS[i // 24], "hour": i % 24, "viewers": v} for i, v in enumerate(online)]
        when.sort(key=lambda r: -r["viewers"])

    interests = (card(studio, "audienceInterestsCardConfig", period=period).get("audienceInterestsCardData") or {}).get("tables") or []
    watches = {}
    for table in interests:
        rows = {k: v for k, v in table.items() if k != "contentType"}
        if rows:
            watches[table["contentType"].replace("CONTENT_ANALYSIS_TYPE_", "").lower()] = rows

    return {
        "period": period,
        "when_viewers_are_on_youtube": when or "not_enough_data",
        "what_your_audience_watches": watches or "not_enough_data",
    }


# ── Research ──────────────────────────────────────────────────────────────────

def research(studio: Studio) -> dict:
    """Studio's Research/Inspiration feed: searches your audience makes and relevant videos (other channels)."""
    data = card(studio, "contentInspirationFeedCardConfig").get("contentInspirationFeedCardData") or {}
    out = {"search_queries": [], "videos": []}
    for shelf in data.get("shelves", []):
        kind = shelf.get("shelfType", "")
        if "SEARCH_QUERIES" in kind:
            for q in (shelf.get("searchQueries") or {}).get("searchQueries", []):
                out["search_queries"].append({
                    "query": q.get("query"),
                    "volume": (q.get("volume") or "").replace("QUERY_VOLUME_", "").lower() or None,
                    "content_gap": bool(q.get("isContentGap") or q.get("isShortsContentGap")),
                    "shorts_gap": bool(q.get("isShortsContentGap")),
                    "language": q.get("languageCode"),
                })
        elif "VIDEOS" in kind:
            for v in (shelf.get("videos") or {}).get("videos", []):
                published = v.get("timePublishedSeconds")
                out["videos"].append({
                    "video_id": v.get("externalVideoId"),
                    "title": v.get("title"),
                    "channel": v.get("uploaderChannelName"),
                    "views": int(v["viewCount"]) if str(v.get("viewCount", "")).isdigit() else v.get("viewCount"),
                    "published": datetime.fromtimestamp(int(published)).date().isoformat() if published else None,
                })
        else:
            out.setdefault("other_shelves", []).append(kind)
    return out
