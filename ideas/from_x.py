"""
Turn the posts taking off in your X home feed into 3-5 video ideas: the story, when to film, a take, a title.

Reads the feed through the logged-in Chrome (twitter/feed.py), sends the fastest-rising posts and your
best and latest videos from Studio to Claude via BrowserLLM, prints the ideas, saves them to
data/x/<timestamp>.md and adds them to the Ideas sheet.

Usage:
    phantom ideas from-x
    phantom ideas from-x --no-export
    phantom ideas from-x --profile p9223      # when BrowserLLM's p9222 hits a usage limit
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime
from typing import Optional

from shared.paths import REPO_ROOT

RUNS_DIR = REPO_ROOT / "data" / "x"
FEED_POSTS = 250     # posts to read from the feed
PROMPT_POSTS = 90    # fastest-rising posts sent to Claude
MAX_AGE_HOURS = 72


def channel_context() -> tuple[str, str]:
    """(best videos by home-feed reach, latest uploads) as prompt text, or a note if Studio is unreachable."""
    try:
        from youtube_api.studio import analytics, content
        from youtube_api.studio.session import Studio

        studio = Studio()
        public = {v["video_id"]: v for v in content.list_videos(studio, "videos", None) if v.get("privacy") == "public"}
        rows = analytics.query(
            studio, ["VIDEO"], ["VIDEO_THUMBNAIL_IMPRESSIONS", "VIDEO_THUMBNAIL_IMPRESSIONS_VTR"],
            filters={"TRAFFIC_SOURCE_TYPE": ["SUBSCRIBER"]}, period="365d", limit=None,
        )
        rows = [r for r in rows if r.get("video") in public and (r.get("video_thumbnail_impressions") or 0) >= 500]
        rows.sort(key=lambda r: r["video_thumbnail_impressions"] * (r.get("video_thumbnail_impressions_vtr") or 0), reverse=True)
        hits = "\n".join(
            f"- \"{public[r['video']]['title']}\": {r['video_thumbnail_impressions']:,} home-feed impressions "
            f"at {r.get('video_thumbnail_impressions_vtr') or 0}% CTR"
            for r in rows[:8]
        )
        latest = sorted(public.values(), key=lambda v: v.get("published") or "", reverse=True)[:15]
        recent = "\n".join(f"- \"{v['title']}\" ({(v.get('published') or '')[:10]})" for v in latest)
        return hits, recent
    except Exception as e:
        print(f"warning: couldn't read Studio ({e})", file=sys.stderr)
        return "(unavailable)", "(unavailable)"


def _num(n: Optional[int]) -> str:
    if n is None:
        return "?"
    return f"{n / 1e6:.1f}M" if n >= 1e6 else f"{n / 1e3:.1f}k" if n >= 1e3 else str(n)


def format_posts(posts: list[dict]) -> str:
    blocks = []
    for p in posts:
        lines = [
            f"score {p['trend_score']:.2f} | {p['age_hours']:.0f}h old | {_num(p['likes'])} likes | {_num(p['views'])} views | "
            f"@{p['author']} ({_num(p['followers'])} followers) | {p['url']}",
            " ".join(p["text"].split())[:700],
        ]
        if p.get("quoted"):
            q = p["quoted"]
            lines.append(f"    quoting @{q['author']} ({_num(q['likes'])} likes): {' '.join(q['text'].split())[:400]}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def to_markdown(ideas: list[dict]) -> str:
    return "\n\n".join(
        f"{i}. {idea.get('story', '')}  ({idea.get('timing', '')})\n"
        f"   Take: {idea.get('take', '')}\n"
        f"   Title: {idea.get('title', '')}\n"
        f"   {idea.get('url', '')}"
        for i, idea in enumerate(ideas, 1)
    )


def export_to_sheet(ideas: list[dict]) -> int:
    from ideas.export_ideas_to_sheet import export_ideas_to_sheet

    rows = [{
        "idea": f"{idea.get('story', '')}\nTitle: {idea.get('title', '')}\nTake: {idea.get('take', '')}",
        "source": idea.get("url", ""),
        "source_type": "X Feed",
        "best_formats": ["YT video"],
    } for idea in ideas]
    return export_ideas_to_sheet(rows).get("appended_count", len(rows))


def main() -> None:
    parser = argparse.ArgumentParser(prog="phantom ideas from-x", description=__doc__.split("\n\n")[0])
    parser.add_argument("--no-export", action="store_true", help="don't add the ideas to the Ideas sheet")
    parser.add_argument("--profile", help="BrowserLLM profile, e.g. p9223 when p9222 hits a usage limit")
    args = parser.parse_args()

    from agentic.ask_browserllm import ask_browserllm, load_prompt
    from ideas.common import extract_json_list
    from twitter.feed import rank_posts, scrape_feed

    print("Reading your X feed...", file=sys.stderr)
    posts = rank_posts(asyncio.run(scrape_feed(FEED_POSTS)), max_age_hours=MAX_AGE_HOURS)[:PROMPT_POSTS]

    hits, recent = channel_context()
    prompt = load_prompt("generate_ideas_from_x_feed", channel_hits=hits, recent_videos=recent, posts=format_posts(posts))

    print(f"Asking Claude about the top {len(posts)} posts...", file=sys.stderr)
    response = str(ask_browserllm(prompt, profile=args.profile, output_json=True))
    ideas = [i for i in (extract_json_list(response) or []) if isinstance(i, dict)]
    if not ideas:
        sys.exit(f"Couldn't read ideas from Claude's reply:\n{response[:1000]}")

    text = to_markdown(ideas)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNS_DIR / f"{datetime.now():%Y-%m-%d_%H%M%S}.md"
    path.write_text(text + "\n")
    print(text, flush=True)
    print(f"\nSaved to {path}", file=sys.stderr)

    if not args.no_export:
        try:
            print(f"Added {export_to_sheet(ideas)} ideas to the Ideas sheet.", file=sys.stderr)
        except Exception as e:
            print(f"warning: couldn't add the ideas to the sheet ({e})", file=sys.stderr)


if __name__ == "__main__":
    main()
