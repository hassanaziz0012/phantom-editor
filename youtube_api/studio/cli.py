"""
phantom studio: read everything in YouTube Studio from the command line, no browser.

    phantom studio login                      import the Studio session from the p9222 Chrome (once)
    phantom studio whoami                     check the saved session
    phantom studio videos [--type ...]        Content tab: videos, shorts, live or all
    phantom studio video <id>...              one video's full details (+ --raw for every field)
    phantom studio playlists [--podcasts]     playlists or podcasts
    phantom studio comments [--video <id>]    comments, newest first (--unanswered for the inbox)
    phantom studio report <name> [...]        a ready-made analytics report (list with `reports`)
    phantom studio query --dims ... --metrics ...   any analytics query (Advanced Mode)
    phantom studio retention <id>             retention curve vs typical, key moments, chapters
    phantom studio retention --channel        retention summary for every recent video
    phantom studio typical [--video <id>]     totals vs Studio's typical range and the previous period
    phantom studio funnel [--video <id>]      impressions -> views funnel, per surface
    phantom studio rank <id>                  how a video ranks against your recent uploads
    phantom studio audience                   when your viewers are online, what else they watch
    phantom studio research                   Research tab: audience searches, content gaps
    phantom studio vocab [metrics|dimensions] every metric and dimension name
    phantom studio dump [--out <dir>]         save all of the above as JSON files

Every command prints a table, or JSON with --json. See docs/studio.md.
"""

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from youtube_api.studio import analytics, content, insights, session, vocab
from youtube_api.studio.session import Studio, StudioError
from youtube_api.video_id import extract_video_id

DUMP_DIR = Path(__file__).resolve().parents[2] / "data" / "studio"


# ── Output ────────────────────────────────────────────────────────────────────

def _cell(value, width: int = 60) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        text = f"{value:,.2f}".rstrip("0").rstrip(".")
    elif isinstance(value, int) and not isinstance(value, bool):
        text = f"{value:,}"
    elif isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value).replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def print_table(rows: list[dict]) -> None:
    if not rows:
        print("(no rows)")
        return
    columns = list(dict.fromkeys(k for row in rows for k in row))
    cells = [[_cell(row.get(c)) for c in columns] for row in rows]
    widths = [max(len(c), *(len(r[i]) for r in cells)) for i, c in enumerate(columns)]
    numeric = [all(r[i] == "-" or r[i].replace(",", "").replace(".", "").lstrip("-").isdigit() for r in cells) for i in range(len(columns))]
    fmt = lambda values: "  ".join(v.rjust(w) if n else v.ljust(w) for v, w, n in zip(values, widths, numeric)).rstrip()
    print(fmt(columns))
    print("  ".join("-" * w for w in widths))
    for r in cells:
        print(fmt(r))


def emit(data, as_json: bool) -> None:
    if as_json:
        json.dump(data, sys.stdout, indent=1, ensure_ascii=False)
        print()
    elif isinstance(data, list) and all(isinstance(r, dict) for r in data):
        print_table(data)
    else:
        print(json.dumps(data, indent=1, ensure_ascii=False))


def emit_notes(metrics: list[str]) -> None:
    """Explain the metrics just printed. On stderr, so stdout stays pure JSON or table."""
    names = dict.fromkeys(m.rstrip("%") for m in metrics)
    notes = [f"  {n.lower()}: {vocab.METRIC_NOTES[n]}" for n in names if n in vocab.METRIC_NOTES]
    if notes:
        sys.stdout.flush()
        print("\nWhat the metrics mean:\n" + "\n".join(notes), file=sys.stderr)


def _csv(value: str) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()] if value else []


def _filters(items: list[str]) -> dict[str, list[str]]:
    out = {}
    for item in items or []:
        dim, sep, values = item.partition("=")
        if not sep:
            raise SystemExit(f"--filter takes DIMENSION=value[,value...], got {item!r}")
        out[dim.strip().upper()] = _csv(values)
    return out


def _video(value: str) -> str:
    return extract_video_id(value)


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_login(args):
    s = session.login()
    print(f"Logged in as channel {s['channel_id']} (Studio client {s['client_version']}).")
    print(f"Session saved to {session.SESSION_FILE}")


def cmd_whoami(args):
    info = session.session_info()
    if not info:
        raise StudioError("no saved session; run `phantom studio login`.")
    studio = Studio()
    rows = analytics.query(studio, [], ["EXTERNAL_VIEWS", "SUBSCRIBERS_NET_CHANGE"], period="28d")
    info.update(checked=datetime.fromtimestamp(studio.session["config_time"]).isoformat(timespec="minutes"),
                views_28d=rows[0].get("external_views") if rows else None)
    emit(info, args.json)


TABLE_HIDDEN = ("thumbnail", "created", "restrictions")   # Still in --json


def cmd_videos(args):
    videos = content.list_videos(Studio(), args.type, args.limit, raw=args.raw)
    if not (args.json or args.raw):
        videos = [{k: v for k, v in row.items() if k not in TABLE_HIDDEN} for row in videos]
    emit(videos, args.json or args.raw)


def cmd_video(args):
    studio = Studio()
    ids = [_video(v) for v in args.video]
    videos = content.get_videos(studio, ids, raw=args.raw)
    if args.analytics and not args.raw:
        for v in videos:
            totals = analytics.report(studio, "overview", videos=[v["video_id"]], period="lifetime")
            v["lifetime"] = totals[0] if totals else {}
    emit(videos if len(videos) > 1 else videos[0], True)


def cmd_playlists(args):
    emit(content.list_playlists(Studio(), podcasts=args.podcasts, raw=args.raw), args.json or args.raw)


def cmd_comments(args):
    rows = content.list_comments(Studio(), _video(args.video) if args.video else None,
                                 unanswered=args.unanswered, order=args.order, limit=args.limit)
    if args.json:
        emit(rows, True)
    else:
        emit([{k: v for k, v in r.items() if k != "replies"} | {"replies": len(r["replies"])} for r in rows], False)


def cmd_reports(args):
    emit([{"report": name, "description": spec[0], "dimensions": ",".join(spec[1]) or "-", "metrics": ",".join(spec[2])}
          for name, spec in analytics.REPORTS.items()], args.json)


def _scope(args) -> dict:
    return {
        "videos": [_video(v) for v in _csv(args.video)],
        "playlist": args.playlist,
        "filters": _filters(args.filter),
        "limit": None if args.limit == 0 else args.limit,
    }


def cmd_report(args):
    if args.name not in analytics.REPORTS:
        raise SystemExit(f"unknown report {args.name!r}; run `phantom studio reports` for the list")
    emit(analytics.report(Studio(), args.name, period=args.period, **_scope(args)), args.json)
    emit_notes(analytics.REPORTS[args.name][2])


def cmd_query(args):
    dims = [d.upper() for d in _csv(args.dims)]
    metrics = [m.upper() for m in _csv(args.metrics)]
    if not metrics:
        raise SystemExit("--metrics is required (see `phantom studio vocab metrics`)")
    emit(analytics.query(Studio(), dims, metrics, period=args.period or analytics.DEFAULT_PERIOD,
                         order_by=args.order.upper() if args.order else None, ascending=args.asc, **_scope(args)), args.json)
    emit_notes(metrics)


def cmd_retention(args):
    studio = Studio()
    if args.channel:
        data = insights.channel_retention(studio, args.period or "28d", curves=args.json)
        if args.json:
            emit(data, True)
            return
        videos = data.pop("videos")
        print(json.dumps(data, indent=1))
        emit([{k: (len(v) if k == "key_moments" else v) for k, v in row.items() if k not in ("chapters", "has_typical")} for row in videos], False)
        return
    if not args.video:
        raise SystemExit("give a video, or --channel")
    data = insights.retention(studio, _video(args.video))
    if args.json:
        emit(data, True)
        return
    curve = data.pop("curve")
    print(json.dumps(data, indent=1))
    print_table(curve)


def cmd_typical(args):
    rows = insights.typical(Studio(), _video(args.video) if args.video else None, args.period, series=args.series)
    emit(rows, args.json or args.series)


def cmd_funnel(args):
    emit(insights.funnel(Studio(), _video(args.video) if args.video else None, args.period), args.json)


def cmd_rank(args):
    emit(insights.ranking(Studio(), _video(args.video)), True)


def cmd_audience(args):
    data = insights.audience(Studio(), args.period or "28d")
    if not args.json and isinstance(data["when_viewers_are_on_youtube"], list):
        data["when_viewers_are_on_youtube"] = data["when_viewers_are_on_youtube"][:15]   # Busiest hours
    emit(data, True)


def cmd_research(args):
    data = insights.research(Studio())
    if args.json:
        emit(data, True)
        return
    print("Searches your audience makes:")
    print_table(data["search_queries"])
    print("\nRelevant videos (other channels):")
    print_table(data["videos"])


def cmd_vocab(args):
    if args.kind in (None, "metrics"):
        lines = [f"{m}  {vocab.METRIC_NOTES[m]}" if m in vocab.METRIC_NOTES else m for m in vocab.METRICS]
        print("\n".join(lines) if args.kind else "METRICS:\n  " + "\n  ".join(lines))
    if args.kind in (None, "dimensions"):
        print("\n".join(vocab.DIMENSIONS) if args.kind else "\nDIMENSIONS:\n  " + "\n  ".join(vocab.DIMENSIONS))


def cmd_dump(args):
    """Every list and report as JSON files in one folder, for analysis or history."""
    studio = Studio()
    out = Path(args.out) if args.out else DUMP_DIR / datetime.now().strftime("%Y-%m-%d_%H%M")
    out.mkdir(parents=True, exist_ok=True)
    errors = {}

    def save(name, fn):
        try:
            data = fn()
        except StudioError as e:
            errors[name] = str(e)
            print(f"  {name}: FAILED ({e})", file=sys.stderr)
            return None
        (out / f"{name}.json").write_text(json.dumps(data, indent=1, ensure_ascii=False))
        print(f"  {name}: {len(data) if isinstance(data, list) else 'ok'}", file=sys.stderr)
        return data

    def run_all(tasks: dict):
        # Studio takes ~2s per query, so run them side by side
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {name: pool.submit(save, name, fn) for name, fn in tasks.items()}
        return {name: f.result() for name, f in futures.items()}

    print(f"Saving Studio data to {out}", file=sys.stderr)
    tasks = {
        "content_all": lambda: content.list_videos(studio, "all"),
        "playlists": lambda: content.list_playlists(studio),
        "podcasts": lambda: content.list_playlists(studio, podcasts=True),
        "comments_recent": lambda: content.list_comments(studio, limit=args.comments),
        # Every video's views per day since publish, to compare videos at the same age (day 7, day 28).
        # Views only: adding metrics returns zero-view days too and hits Studio's 10,000-row cap.
        "video_daily_lifetime": lambda: analytics.query(studio, ["DAY", "VIDEO"], ["EXTERNAL_VIEWS"], period="lifetime", limit=None),
    }
    for period in args.periods:
        for name in analytics.REPORTS:
            if name not in analytics.REALTIME_PERIODS:
                tasks[f"report_{name}_{period}"] = lambda name=name, period=period: analytics.report(studio, name, period=period, limit=None)
    for name in analytics.REALTIME_PERIODS:
        tasks[f"report_{name}"] = lambda name=name: analytics.report(studio, name, limit=None)
    tasks.update({
        "research": lambda: insights.research(studio),
        "audience": lambda: insights.audience(studio),
    })
    for period in args.periods:
        card_period = period if period in insights.CARD_PERIODS else None
        if card_period:
            tasks[f"typical_{period}"] = lambda p=card_period: insights.typical(studio, period=p)
            tasks[f"funnel_{period}"] = lambda p=card_period: insights.funnel(studio, period=p)
            tasks[f"retention_channel_{period}"] = lambda p=card_period: insights.channel_retention(studio, p, curves=True)
    videos = run_all(tasks)["content_all"]

    if args.retention and videos:
        recent = [v["video_id"] for v in videos if v["type"] == "video_on_demand" and v["privacy"] == "public"][: args.retention]
        per_video = {}
        for vid in recent:
            per_video[f"retention_{vid}"] = lambda vid=vid: insights.retention(studio, vid)
            per_video[f"typical_{vid}"] = lambda vid=vid: insights.typical(studio, vid, series=True)
            per_video[f"funnel_{vid}"] = lambda vid=vid: insights.funnel(studio, vid)
            per_video[f"rank_{vid}"] = lambda vid=vid: insights.ranking(studio, vid)
        run_all(per_video)

    (out / "_meta.json").write_text(json.dumps({
        "channel_id": studio.channel_id, "saved": datetime.now().isoformat(timespec="seconds"),
        "periods": args.periods, "errors": errors,
    }, indent=1))
    print(f"Done: {out}" + (f" ({len(errors)} failed, see _meta.json)" if errors else ""), file=sys.stderr)


# ── Parser ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(prog="phantom studio", description="Read YouTube Studio over HTTP (no browser).")
    sub = parser.add_subparsers(dest="command", required=True)

    def add(name, fn, help_text, json_flag=True):
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.set_defaults(fn=fn)
        if json_flag:
            p.add_argument("--json", action="store_true", help="print JSON instead of a table")
        return p

    def scope(p, limit=50):
        p.add_argument("--period", "-p", help="7d, 28d (default), 90d, 365d, 48h, 60m, lifetime, 2026, 2026-11 or 2026-11-01:2026-11-19")
        p.add_argument("--video", "-v", help="only these videos (IDs or URLs, comma-separated)")
        p.add_argument("--playlist", help="only this playlist")
        p.add_argument("--filter", "-f", action="append", help="DIMENSION=value[,value] (repeatable), e.g. CREATOR_CONTENT_TYPE=SHORTS")
        p.add_argument("--limit", "-n", type=int, default=limit, help=f"max rows (default {limit}, 0 = all)")

    add("login", cmd_login, "import the Studio session from the logged-in p9222 Chrome", json_flag=False)
    add("whoami", cmd_whoami, "show the saved session and check it still works")

    p = add("videos", cmd_videos, "list content: videos, shorts, live or all")
    p.add_argument("--type", "-t", choices=list(content.KINDS), default="videos")
    p.add_argument("--limit", "-n", type=int)
    p.add_argument("--raw", action="store_true", help="every field Studio returns")

    p = add("video", cmd_video, "full details of one or more videos (always JSON)", json_flag=False)
    p.add_argument("video", nargs="+")
    p.add_argument("--analytics", "-a", action="store_true", help="add lifetime analytics totals")
    p.add_argument("--raw", action="store_true", help="every field Studio returns")

    p = add("playlists", cmd_playlists, "list playlists (or podcasts)")
    p.add_argument("--podcasts", action="store_true")
    p.add_argument("--raw", action="store_true")

    p = add("comments", cmd_comments, "list comments on the channel or one video")
    p.add_argument("--video", "-v")
    p.add_argument("--unanswered", action="store_true", help="only comments you haven't replied to")
    p.add_argument("--order", choices=["newest", "relevant"], default="newest")
    p.add_argument("--limit", "-n", type=int, default=100)

    add("reports", cmd_reports, "list the ready-made reports")

    p = add("report", cmd_report, "run a ready-made analytics report")
    p.add_argument("name")
    scope(p)

    p = add("query", cmd_query, "any analytics query: metrics by dimensions (Studio's Advanced Mode)")
    p.add_argument("--dims", "-d", default="", help="dimensions, comma-separated (none = totals)")
    p.add_argument("--metrics", "-m", default="", help="metrics, comma-separated; METRIC%% = share of total")
    p.add_argument("--order", "-o", help="metric or dimension to sort by (descending)")
    p.add_argument("--asc", action="store_true", help="sort ascending")
    scope(p)

    p = add("retention", cmd_retention, "retention vs typical, key moments and chapters (or --channel for every recent video)")
    p.add_argument("video", nargs="?")
    p.add_argument("--channel", action="store_true", help="summary for every recent long-form video, with channel averages")
    p.add_argument("--period", "-p", help="with --channel: 7d, 28d (default), 90d, 365d, lifetime")

    card_periods = ", ".join(insights.CARD_PERIODS)
    p = add("typical", cmd_typical, "totals vs Studio's typical range and the previous period")
    p.add_argument("--video", "-v", help="a video (since publish) instead of the channel")
    p.add_argument("--period", "-p", help=f"{card_periods} (default 28d; videos: since publish)")
    p.add_argument("--series", action="store_true", help="add the typical band over time (JSON)")

    p = add("funnel", cmd_funnel, "impressions -> views -> watch time, overall and per surface")
    p.add_argument("--video", "-v")
    p.add_argument("--period", "-p", help=card_periods)

    p = add("rank", cmd_rank, "how a video ranks against your recent uploads (always JSON)", json_flag=False)
    p.add_argument("video")

    p = add("audience", cmd_audience, "when your viewers are on YouTube and what else they watch (JSON)")
    p.add_argument("--period", "-p", help=card_periods)

    add("research", cmd_research, "Studio's Research tab: your audience's searches, content gaps, relevant videos")

    p = add("vocab", cmd_vocab, "list every metric and dimension name", json_flag=False)
    p.add_argument("kind", nargs="?", choices=["metrics", "dimensions"])

    p = add("dump", cmd_dump, "save every list and report as JSON files", json_flag=False)
    p.add_argument("--out", help=f"folder (default {DUMP_DIR}/<date_time>)")
    p.add_argument("--periods", nargs="+", default=["28d", "lifetime"], help="report periods (default 28d lifetime)")
    p.add_argument("--retention", type=int, default=10, help="retention, typical, funnel and rank for the N newest public videos (default 10)")
    p.add_argument("--comments", type=int, default=200, help="newest comments to save (default 200)")
    p.add_argument("--workers", type=int, default=8, help="requests in parallel (default 8)")

    args = parser.parse_args()
    try:
        args.fn(args)
    except (StudioError, ValueError) as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
