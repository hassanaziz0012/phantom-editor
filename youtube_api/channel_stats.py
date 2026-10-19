#!/usr/bin/env python3
"""
Channel Stats
=============
Your channel's long-form performance over the last 28 days: impressions, click-through
rate, views, average view duration, watch time, and subscribers. These are the numbers
on the Videos card of YouTube Studio's Content tab, read live through the logged-in
Chrome on port 9222 (see studio_analytics.py).

Every fetch is saved to cache/channel_stats.json: the latest snapshot, plus one snapshot
per day as history, so any script can reuse the channel's averages and track how they
change without opening Chrome:

    from youtube_api.channel_stats import get_channel_stats, get_channel_history

    stats = get_channel_stats()      # Reuses a snapshot younger than CACHE_TTL, else asks Studio
    stats["ctr"]                     # Percentage (1.9 means 1.9%)
    stats["avg_view_duration_ms"]

Usage:
    phantom yt channel-stats [--fresh] [--json] [--history]
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

# Add project root to sys.path to allow package imports
root_dir = str(Path(__file__).resolve().parent.parent)
if root_dir not in sys.path:
    sys.path.insert(0, root_dir)

from youtube_api import studio_analytics
from youtube_api.utils import supports_color

# ── Configuration ─────────────────────────────────────────────────────────────

CACHE_FILE = Path(__file__).resolve().parent / "cache" / "channel_stats.json"
CACHE_TTL  = timedelta(hours=12)
PERIOD     = "last_28_days"
CONTENT    = "long-form"           # Studio's Videos card: no Shorts, live streams, or posts


# ── Store ─────────────────────────────────────────────────────────────────────

def _load_store() -> dict:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_store(store: dict) -> None:
    # Cache failures shouldn't break the caller; the next fetch just writes it again.
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_file = CACHE_FILE.with_suffix(".json.tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(store, f, indent=2)
        os.replace(tmp_file, CACHE_FILE)
    except OSError:
        pass


def record_channel_stats(channel_id: str, totals: dict) -> dict:
    """
    Save freshly fetched channel totals as the latest snapshot and as today's history
    entry (a later fetch the same day replaces it). Returns the snapshot.
    """
    now = datetime.now().astimezone()
    snapshot = {"fetched_at": now.isoformat(), "period": PERIOD, "content": CONTENT, **totals}
    store = _load_store()
    if store.get("channel_id") not in (None, channel_id):
        store = {}
    store["channel_id"] = channel_id
    store["latest"] = snapshot
    store.setdefault("history", {})[now.date().isoformat()] = snapshot
    _save_store(store)
    return snapshot


def get_cached_channel_stats(max_age: Optional[timedelta] = CACHE_TTL) -> Optional[dict]:
    """The latest saved snapshot if it's younger than max_age (None: any age), else None."""
    latest = _load_store().get("latest")
    if not latest:
        return None
    try:
        age = datetime.now().astimezone() - datetime.fromisoformat(latest["fetched_at"])
    except (KeyError, ValueError):
        return None
    return latest if max_age is None or age < max_age else None


def get_channel_stats(fresh: bool = False, max_age: timedelta = CACHE_TTL) -> dict:
    """Your channel's last-28-days long-form totals, from the cache or live from Studio."""
    if not fresh and (cached := get_cached_channel_stats(max_age)):
        return cached

    channel_id = studio_analytics.get_cached_channel_id()
    if not channel_id:
        raise studio_analytics.StudioError(
            "Your channel isn't known yet; run `phantom yt info` on one of your videos first."
        )
    result = studio_analytics.fetch_studio(channel_id, channel_totals=True)
    if result is None:
        raise studio_analytics.StudioError("The channel logged into Studio has changed; run `phantom yt info` on one of your videos.")
    return record_channel_stats(channel_id, result["channel"])


def get_channel_history() -> list[dict]:
    """Every saved daily snapshot, oldest first, each with its "date"."""
    history = _load_store().get("history", {})
    return [{"date": day, **history[day]} for day in sorted(history)]


# ── Output ────────────────────────────────────────────────────────────────────

USE_COLOR = supports_color()

def _c(code: str) -> str:
    return code if USE_COLOR else ""

BOLD  = _c("\033[1m")
DIM   = _c("\033[2m")
RED   = _c("\033[91m")
RESET = _c("\033[0m")


def _format_duration(milliseconds: Optional[int]) -> str:
    if milliseconds is None:
        return "-"
    minutes, seconds = divmod(round(milliseconds / 1000), 60)
    return f"{minutes}:{seconds:02}"


def _format_number(value) -> str:
    return f"{value:,}" if value is not None else "-"


def print_stats(stats: dict) -> None:
    def row(label: str, value: str) -> None:
        print(f"  {DIM}{label:<13}{RESET}{value}")

    fetched_at = datetime.fromisoformat(stats["fetched_at"])
    print()
    print(f"  {BOLD}Channel, last 28 days{RESET} {DIM}(long-form videos, from YouTube Studio){RESET}")
    row("Impressions", _format_number(stats.get("impressions")))
    row("CTR", f"{BOLD}{stats['ctr']:.2f}%{RESET}" if stats.get("ctr") is not None else "-")
    row("Views", _format_number(stats.get("views")))
    row("Avg view", f"{BOLD}{_format_duration(stats.get('avg_view_duration_ms'))}{RESET}")
    watch_time = stats.get("watch_time_ms")
    row("Watch time", f"{watch_time / 3_600_000:,.1f}h" if watch_time is not None else "-")
    row("Subscribers", f"+{stats.get('subscribers_gained') or 0:,} / -{stats.get('subscribers_lost') or 0:,}")
    print(f"  {DIM}Fetched {fetched_at:%b %d %H:%M}{RESET}")
    print()


def print_history(history: list[dict]) -> None:
    print()
    print(f"  {DIM}{'Date':<12}{'Impressions':>12}{'CTR':>8}{'Views':>8}{'Avg view':>10}{RESET}")
    for s in history:
        ctr = f"{s['ctr']:.2f}%" if s.get("ctr") is not None else "-"
        print(
            f"  {s['date']:<12}{_format_number(s.get('impressions')):>12}{ctr:>8}"
            f"{_format_number(s.get('views')):>8}{_format_duration(s.get('avg_view_duration_ms')):>10}"
        )
    print(f"  {DIM}Each row is the 28 days up to that date.{RESET}")
    print()


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Show your channel's long-form CTR, average view duration, and more over the last 28 days."
    )
    parser.add_argument("--fresh", action="store_true", help=f"Read Studio now instead of reusing a snapshot under {CACHE_TTL.seconds // 3600}h old.")
    parser.add_argument("--json", action="store_true", help="Output as JSON instead of human-readable text.")
    parser.add_argument("--history", action="store_true", help="Show every saved daily snapshot instead of the latest.")
    args = parser.parse_args()

    if args.history:
        history = get_channel_history()
        if args.json:
            print(json.dumps(history, indent=2))
        elif not history:
            print("No channel stats saved yet; run `phantom yt channel-stats`.")
        else:
            print_history(history)
        return

    try:
        stats = get_channel_stats(fresh=args.fresh)
    except Exception as e:
        if args.json:
            print(json.dumps({"error": str(e)}, indent=2))
        else:
            print(f"{RED}Error:{RESET} {e}", file=sys.stderr)
        sys.exit(1)

    if args.json:
        print(json.dumps(stats, indent=2))
    else:
        print_stats(stats)


if __name__ == "__main__":
    main()
