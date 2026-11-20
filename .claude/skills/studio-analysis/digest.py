#!/usr/bin/env python3
"""
Condense a `phantom studio dump` folder (~100 JSON files, a few MB) into one markdown digest
(~30 KB) that an analyst can read in full: titles joined to stats, videos compared at the same
age, Studio's typical ranges, traffic, search terms, audience, research, comments.

    python3 .claude/skills/studio-analysis/digest.py [DUMP_DIR] [--videos N]

DUMP_DIR defaults to the newest folder in data/studio/. Writes DUMP_DIR/digest.md and prints its path.
"""
import argparse
import json
import statistics
from datetime import date, datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DUMPS = REPO / "data" / "studio"


def load(d: Path, name: str):
    p = d / f"{name}.json"
    return json.loads(p.read_text()) if p.exists() else None


def fmt(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.1f}" if abs(v) >= 10 else f"{v:.2f}"
    return str(v)


def table(rows: list[dict], cols: list[tuple[str, str]]) -> str:
    """cols: (key, header). Values are formatted with fmt()."""
    if not rows:
        return "_(no rows)_\n"
    out = ["| " + " | ".join(h for _, h in cols) + " |", "|" + "---|" * len(cols)]
    for r in rows:
        out.append("| " + " | ".join(fmt(r.get(k)).replace("|", "/") for k, _ in cols) + " |")
    return "\n".join(out) + "\n"


def median(values):
    values = [v for v in values if isinstance(v, (int, float))]
    return round(statistics.median(values), 2) if values else None


def mins(sec):
    return None if sec is None else round(sec / 60, 1)


def short(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", nargs="?", help="dump folder (default: newest in data/studio/)")
    ap.add_argument("--videos", type=int, default=40, help="newest long-form videos to list (default 40)")
    args = ap.parse_args()

    d = Path(args.dump) if args.dump else max((p for p in DUMPS.iterdir() if p.is_dir()), key=lambda p: p.name)
    meta = load(d, "_meta") or {}
    saved = datetime.fromisoformat(meta["saved"]) if meta.get("saved") else datetime.fromtimestamp(d.stat().st_mtime)
    today = saved.date()

    content = load(d, "content_all") or []
    titles = {v["video_id"]: v["title"] for v in content}
    by_id = {v["video_id"]: v for v in content}
    L = []  # output lines
    add = L.append

    add(f"# Studio digest: {d.name}\n")
    add(f"Saved {saved:%Y-%m-%d %H:%M}. Periods in dump: {', '.join(meta.get('periods', []))}. "
        f"Failed parts: {', '.join(meta.get('errors', {})) or 'none'}.\n")
    add("Units: `_sec` = seconds, `min` = minutes, percentages 0–100, CTR = impressions click-through rate. "
        "`-` = withheld by Studio (too few viewers) or not applicable, not zero.\n")
    counts = {}
    for v in content:
        counts[v["type"]] = counts.get(v["type"], 0) + 1
    add(f"Content: {counts}.\n")

    # ── Channel vs typical ────────────────────────────────────────────────
    add("## Channel: last 28 days vs Studio's typical range and the previous 28 days\n")
    t28 = load(d, "typical_28d") or []
    add(table(t28, [("metric", "metric"), ("total", "28d"), ("typical_low", "typical low"),
                    ("typical_high", "typical high"), ("vs_typical", "vs typical"), ("previous_period", "previous 28d")]))
    for period in ("28d", "lifetime"):
        ov = load(d, f"report_overview_{period}")
        if ov:
            add(f"Overview {period}: " + ", ".join(f"{k}={fmt(v)}" for k, v in ov[0].items()) + "\n")
    for period in ("28d", "lifetime"):
        ct = load(d, f"report_content-type_{period}")
        if ct:
            add(f"\nContent type, {period}:\n")
            add(table(ct, [("creator_content_type", "type"), ("external_views", "views"),
                           ("average_watch_percentage", "avg %"), ("subscribers_net_change", "subs")]))

    # ── Trend ─────────────────────────────────────────────────────────────
    add("## Trend\n")
    monthly = (load(d, "report_monthly_lifetime") or [])
    monthly = sorted(monthly, key=lambda r: r["month"])[-13:]
    for r in monthly:
        r["month"] = r["month"][:7]
        r["watch_h"] = round((r.get("external_watch_time_sec") or 0) / 3600, 1)
    add("Monthly, last 13 months (current month is partial):\n")
    add(table(monthly, [("month", "month"), ("external_views", "views"), ("video_thumbnail_impressions", "impr"),
                        ("video_thumbnail_impressions_vtr", "CTR"), ("watch_h", "watch h"),
                        ("average_watch_percentage", "avg %"), ("subscribers_net_change", "subs")]))
    weekly = sorted(load(d, "report_weekly_lifetime") or [], key=lambda r: r["week"])[-10:]
    add("\nWeekly, last 10 weeks (current week is partial):\n")
    add(table(weekly, [("week", "week"), ("external_views", "views"), ("video_thumbnail_impressions", "impr"),
                       ("video_thumbnail_impressions_vtr", "CTR"), ("average_watch_percentage", "avg %"),
                       ("subscribers_net_change", "subs")]))

    # ── Long-form videos at the same age ──────────────────────────────────
    daily = {}
    for r in load(d, "video_daily_lifetime") or []:
        daily.setdefault(r["video"], []).append((r["day"], r.get("external_views") or 0))
    stats = {r["video"]: r for r in load(d, "report_videos_lifetime") or []}
    ret = {v["video_id"]: v for v in (load(d, "retention_channel_lifetime") or {}).get("videos", [])}

    def views_by_day(vid, pub: date, n: int):
        if (today - pub).days < n:
            return None  # too young to compare
        last = (pub + timedelta(days=n - 1)).isoformat()
        return sum(v for day, v in daily.get(vid, []) if day <= last)

    longform = [v for v in content if v["type"] == "video_on_demand" and v["privacy"] == "public" and v.get("published")]
    longform.sort(key=lambda v: v["published"], reverse=True)
    rows = []
    for v in longform[: args.videos]:
        vid, pub = v["video_id"], datetime.fromisoformat(v["published"]).date()
        s, r = stats.get(vid, {}), ret.get(vid, {})
        rows.append({
            "id": vid, "title": short(v["title"], 70), "published": pub.isoformat(), "age_d": (today - pub).days,
            "len_min": mins(v.get("length_sec")), "views": v.get("views"),
            "d2": views_by_day(vid, pub, 2), "d7": views_by_day(vid, pub, 7), "d28": views_by_day(vid, pub, 28),
            "impr": s.get("video_thumbnail_impressions"), "ctr": s.get("video_thumbnail_impressions_vtr"),
            "avg_pct": s.get("average_watch_percentage"), "avg_min": mins(s.get("average_watch_time_sec")),
            "ret30": r.get("retention_at_30s"), "subs": s.get("subscribers_net_change"),
            "likes": s.get("ratings_likes"), "comments": s.get("comments"),
            "test": (v.get("experiment") or {}).get("state"),
        })
    add(f"## Newest {len(rows)} public long-form videos (lifetime stats)\n")
    add("`d2`/`d7`/`d28` = views in the first 2/7/28 days since publish (`-` = video is younger than that), "
        "the fair way to compare videos of different ages. `ret30` = % still watching at 0:30 "
        "(only for videos Studio includes in its retention summary). `test` = Test & Compare state.\n")
    add(table(rows, [("published", "published"), ("title", "title"), ("id", "id"), ("len_min", "min"),
                     ("views", "views"), ("d2", "d2"), ("d7", "d7"), ("d28", "d28"), ("impr", "impr"),
                     ("ctr", "CTR"), ("avg_pct", "avg %"), ("avg_min", "avg min"), ("ret30", "ret30"),
                     ("subs", "subs"), ("likes", "likes"), ("comments", "cmts"), ("test", "test")]))
    for label, part in (("all listed", rows), ("newest 10", rows[:10]), ("previous 10 (11–20)", rows[10:20])):
        add(f"Median, {label}: " + ", ".join(f"{k}={fmt(median(r[k] for r in part))}"
                                            for k in ("d2", "d7", "d28", "ctr", "avg_pct", "avg_min", "ret30", "subs")) + "\n")

    best = sorted((r for r in stats.values() if by_id.get(r["video"], {}).get("type") == "video_on_demand"),
                  key=lambda r: r.get("external_views") or 0, reverse=True)[:15]
    for r in best:
        v = by_id.get(r["video"], {})
        r["title"] = short(v.get("title", r["video"]), 70)
        r["published"] = (v.get("published") or "")[:10]
        r["avg_min"] = mins(r.get("average_watch_time_sec"))
    add("\n## All-time top 15 long-form videos by views\n")
    add(table(best, [("published", "published"), ("title", "title"), ("external_views", "views"),
                     ("video_thumbnail_impressions_vtr", "CTR"), ("average_watch_percentage", "avg %"),
                     ("avg_min", "avg min"), ("subscribers_net_change", "subs")]))
    subv = load(d, "report_sub-videos_28d") or []
    for r in subv:
        r["title"] = short(titles.get(r["video"], r["video"]), 70)
    add("\nVideos that gained subscribers, last 28 days:\n")
    add(table(subv[:10], [("title", "title"), ("subscribers_net_change", "net"), ("subscribers_gained", "gained"),
                          ("external_views", "views")]))

    shorts = load(d, "report_shorts_lifetime") or []
    if shorts:
        for r in shorts:
            r["title"] = short(titles.get(r["video"], r["video"]), 60)
        add("\n## Shorts (lifetime)\n")
        add(table(shorts[:10], [("title", "title"), ("external_views", "views"), ("shorts_feed_impressions", "feed impr"),
                                ("shorts_feed_impressions_vtr", "swipe-through %"), ("average_watch_percentage", "avg %"),
                                ("subscribers_gained", "subs")]))

    # ── Newest videos in depth ────────────────────────────────────────────
    add("\n## Newest videos in depth (since publish)\n")
    deep = [f.stem.removeprefix("retention_") for f in d.glob("retention_*.json") if not f.stem.startswith("retention_channel")]
    deep.sort(key=lambda vid: by_id.get(vid, {}).get("published") or "", reverse=True)
    for vid in deep:
        f = d / f"retention_{vid}.json"
        rt = json.loads(f.read_text())
        v = by_id.get(vid, {})
        add(f"### {v.get('title', vid)} ({vid}, published {(v.get('published') or '')[:10]}, {mins(v.get('length_sec'))} min)\n")
        add(f"Retention: avg {fmt(rt.get('avg_pct_watched'))}% watched, avg view {fmt(mins(rt.get('avg_view_duration_sec')))} min, "
            f"at 0:30 {fmt(rt.get('retention_at_30s'))}% (typical {rt.get('typical_retention_at_30s')}), "
            f"curve points vs typical {rt.get('points_vs_typical')}.\n")
        moments = [f"{m['type']} {m['start_sec']:.0f}–{m['end_sec']:.0f}s"
                   + (f" ({m['vs_your_average']})" if m.get("vs_your_average") else "")
                   for m in rt.get("key_moments") or []]
        if moments:
            add("Key moments: " + "; ".join(moments) + "\n")
        if rt.get("chapters"):
            add("Chapters: " + "; ".join(f"{c['start_sec']}s {short(c['title'], 50)}" for c in rt["chapters"]) + "\n")
        typ = load(d, f"typical_{vid}") or []
        if typ:
            add("Vs comparable videos at the same age: " + "; ".join(
                f"{t['metric']} {fmt(t['total'])} (typical {fmt(t['typical_low'])}–{fmt(t['typical_high'])}, {t['vs_typical'] or '-'})"
                for t in typ if t.get("typical_low") is not None) + "\n")
        rank = load(d, f"rank_{vid}")
        if rank:
            add(f"Rank by {rank.get('ranked_by')}: {rank.get('rank')} of {rank.get('out_of')} recent uploads.\n")
        fun = load(d, f"funnel_{vid}") or []
        if fun:
            add("Funnel by surface: " + "; ".join(
                f"{r['surface']}: {fmt(r.get('video_thumbnail_impressions'))} impr, CTR {fmt(r.get('video_thumbnail_impressions_vtr'))}"
                for r in fun) + "\n")
        add("")

    # ── Discovery ─────────────────────────────────────────────────────────
    add("## Discovery\n")
    fun = load(d, "funnel_28d") or []
    add("Channel funnel, last 28 days:\n")
    add(table(fun, [("surface", "surface"), ("video_thumbnail_impressions", "impr"), ("video_thumbnail_impressions_vtr", "CTR"),
                    ("video_thumbnail_impressed_views", "views from impr")]))
    for period in ("28d", "lifetime"):
        tr = load(d, f"report_traffic_{period}") or []
        total = sum(r.get("external_views") or 0 for r in tr) or 1
        for r in tr:
            r["share"] = round(100 * (r.get("external_views") or 0) / total, 1)
        add(f"\nTraffic sources, {period}:\n")
        add(table(tr, [("traffic_source_type", "source"), ("external_views", "views"), ("share", "share %"),
                       ("video_thumbnail_impressions", "impr"), ("video_thumbnail_impressions_vtr", "CTR"),
                       ("average_watch_percentage", "avg %")]))
    for period, n in (("28d", 20), ("lifetime", 30)):
        st = load(d, f"report_search-terms_{period}") or []
        add(f"\nTop YouTube search terms, {period} (term: views):\n")
        add(", ".join(f"{r['traffic_source_detail']}: {r['external_views']}" for r in st[:n]) + "\n")
    sug = load(d, "report_suggested_28d") or []
    add("\nTop suggesting videos, last 28 days (own videos show a title, others only an ID):\n")
    add(", ".join(f"{titles.get(r['traffic_source_detail'], r['traffic_source_detail'])}: {r['external_views']}" for r in sug[:12]) + "\n")
    ext = load(d, "report_external_28d") or []
    add("\nExternal sites, last 28 days: " + ", ".join(f"{r['traffic_source_detail']}: {r['external_views']}" for r in ext[:10]) + "\n")

    # ── Audience ──────────────────────────────────────────────────────────
    add("\n## Audience, last 28 days\n")
    add("Rows Studio withholds for privacy are missing, so a lone row at 100% only means the others were too small to show.\n")
    for name, key in (("subscribed", "subscribed_to_uploader_state"), ("loyalty", "audience_loyalty_segment"),
                      ("sub-sources", "subscription_source_type"), ("countries", "country"), ("age", "viewer_age"),
                      ("gender", "viewer_gender"), ("devices", "device_platform_type")):
        rows_ = load(d, f"report_{name}_28d") or []
        if not rows_:
            continue
        val = next((k for k in ("external_views", "external_views_pct_of_total", "subscribers_net_change") if k in rows_[0]), None)
        extra = "average_watch_percentage" if "average_watch_percentage" in rows_[0] else None
        add(f"{name}: " + ", ".join(
            f"{r.get(key)} {fmt(r.get(val))}" + ("% of views" if val.endswith("_pct") or "pct" in val else "")
            + (f" (avg {fmt(r.get(extra))}% watched)" if extra else "") for r in rows_[:10]) + "\n")
    aud = load(d, "audience")
    if aud:
        add(f"When viewers are on YouTube / what else they watch: {json.dumps({k: v for k, v in aud.items() if k != 'period'})[:600]}\n")

    # ── Research, tests, comments ─────────────────────────────────────────
    res = load(d, "research") or {}
    add("\n## Research tab (what this audience searches for on YouTube)\n")
    add("Searches (volume; GAP = content gap, few good videos exist): " + "; ".join(
        f"{q['query']} ({q.get('volume')}{', GAP' if q.get('content_gap') else ''}{', SHORTS GAP' if q.get('shorts_gap') else ''})"
        for q in res.get("search_queries", [])) + "\n")
    add("\nRelevant videos from other channels: " + "; ".join(
        f"{short(v['title'], 80)} ({v.get('channel')}, {v.get('views')} views, {v.get('published')})"
        for v in res.get("videos", [])[:15]) + "\n")

    tests = [v for v in content if v.get("experiment")]
    if tests:
        add("\n## Test & Compare (thumbnail/title tests)\n")
        for v in tests[:6]:
            e = v["experiment"]
            add(f"- {v['title']}: {e.get('state')}, result {e.get('result')}, winner {e.get('winner')}, "
                f"started {e.get('started')}" + (f", variants {e['variants']}" if any(e.get("variants") or []) else "") + "\n")
        results = {}
        for v in tests:
            r = v["experiment"].get("result")
            results[r] = results.get(r, 0) + 1
        add(f"All {len(tests)} tests by result: {results}\n")

    comments = load(d, "comments_recent") or []
    add(f"\n## Newest comments ({min(len(comments), 30)} of {len(comments)})\n")
    for c in comments[:30]:
        add(f"- [{short(titles.get(c.get('video_id'), c.get('video_id') or ''), 40)}] {c.get('published')}: "
            f"{short(c.get('text'), 200)}" + (" (owner)" if c.get("by_owner") else "") + (" (replied)" if c.get("replies") else ""))

    out = d / "digest.md"
    out.write_text("\n".join(L) + "\n")
    print(f"{out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
