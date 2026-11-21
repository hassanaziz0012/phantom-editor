"""
Read the posts in your X home feed, with engagement numbers, and rank them by how fast they're taking off.
Used by `phantom ideas from-x` (ideas/from_x.py).

Attaches to the logged-in Chrome on port 9222 (see browser/cdp.py), opens x.com/home, scrolls, and
reads the HomeTimeline GraphQL responses the page fetches, so it gets exact counts instead of parsing
the page. Ads are skipped, reposts are counted as the original post, and duplicates are merged.
"""

from __future__ import annotations

import asyncio
import math
import sys
from datetime import datetime, timezone
from typing import Any, Optional

from playwright.async_api import async_playwright

from browser.cdp import get_cdp_browser_context, get_clean_page

HOME_URL = "https://x.com/home"
TIMELINE_OPS = ("/HomeTimeline", "/HomeLatestTimeline")
X_TIME_FORMAT = "%a %b %d %H:%M:%S %z %Y"


# ── Parsing ───────────────────────────────────────────────────────────────────

def _unwrap(result: Optional[dict]) -> Optional[dict]:
    """A tweet result as a plain Tweet dict (X wraps some in TweetWithVisibilityResults)."""
    if not result:
        return None
    if result.get("__typename") == "TweetWithVisibilityResults":
        result = result.get("tweet")
    if not result or "legacy" not in result:
        return None
    return result


def _text(tweet: dict) -> str:
    note = (((tweet.get("note_tweet") or {}).get("note_tweet_results") or {}).get("result") or {}).get("text")
    return note or tweet["legacy"].get("full_text", "")


def _author(tweet: dict) -> dict:
    user = ((tweet.get("core") or {}).get("user_results") or {}).get("result") or {}
    core, legacy = user.get("core") or {}, user.get("legacy") or {}
    followers = (user.get("relationship_counts") or {}).get("followers", legacy.get("followers_count"))
    return {
        "handle": core.get("screen_name") or legacy.get("screen_name") or "",
        "name": core.get("name") or legacy.get("name") or "",
        "followers": followers or 0,
        "verified": bool(user.get("is_blue_verified")),
    }


def parse_tweet(tweet: dict, now: datetime) -> dict:
    """The fields the ranking and the idea prompt need, from one Tweet result."""
    legacy = tweet["legacy"]
    author = _author(tweet)
    created = datetime.strptime(legacy["created_at"], X_TIME_FORMAT)
    age_hours = max((now - created).total_seconds() / 3600, 0.0)
    views = (tweet.get("views") or {}).get("count")
    entities = legacy.get("entities") or {}
    media = (legacy.get("extended_entities") or {}).get("media") or []

    post = {
        "id": tweet["rest_id"],
        "url": f"https://x.com/{author['handle']}/status/{tweet['rest_id']}",
        "author": author["handle"],
        "author_name": author["name"],
        "followers": author["followers"],
        "verified": author["verified"],
        "created_at": created.isoformat(),
        "age_hours": round(age_hours, 1),
        "text": _text(tweet),
        "likes": legacy.get("favorite_count", 0),
        "reposts": legacy.get("retweet_count", 0),
        "quotes": legacy.get("quote_count", 0),
        "replies": legacy.get("reply_count", 0),
        "bookmarks": legacy.get("bookmark_count", 0),
        "views": int(views) if views else None,
        "links": [u.get("expanded_url") for u in entities.get("urls", []) if u.get("expanded_url")],
        "media": sorted({m.get("type") for m in media if m.get("type")}),
        "lang": legacy.get("lang"),
    }
    quoted = _unwrap((tweet.get("quoted_status_result") or {}).get("result"))
    if quoted:
        q_author = _author(quoted)
        post["quoted"] = {
            "author": q_author["handle"],
            "text": _text(quoted),
            "likes": quoted["legacy"].get("favorite_count", 0),
            "url": f"https://x.com/{q_author['handle']}/status/{quoted['rest_id']}",
        }
    return score_post(post)


def score_post(post: dict) -> dict:
    """
    Adds engagement, engagement per hour and reach (engagement per follower), and a trend score
    that favours posts taking off fast and posts doing far better than their author usually would.
    """
    engagement = post["likes"] + 2 * (post["reposts"] + post["quotes"]) + post["replies"] + post["bookmarks"]
    per_hour = engagement / max(post["age_hours"], 1.0)
    reach = engagement / max(post["followers"], 500)
    post["engagement"] = engagement
    post["engagement_per_hour"] = round(per_hour, 1)
    post["reach"] = round(reach, 3)
    post["trend_score"] = round(math.log10(1 + per_hour) + 0.5 * math.log10(1 + 100 * reach), 3)
    return post


def tweets_from_timeline(data: dict) -> list[dict]:
    """Every organic Tweet result in one HomeTimeline response (ads skipped, reposts unwrapped)."""
    home = (data.get("data") or {}).get("home") or {}
    timeline = home.get("home_timeline_urt") or {}
    items: list[dict] = []
    for instruction in timeline.get("instructions", []):
        for entry in instruction.get("entries", []):
            if entry.get("entryId", "").startswith("promoted-"):
                continue
            content = entry.get("content") or {}
            if "itemContent" in content:
                items.append(content["itemContent"])
            for module_item in content.get("items", []):  # conversation threads
                item = (module_item.get("item") or {}).get("itemContent")
                if item:
                    items.append(item)

    tweets = []
    for item in items:
        if item.get("promotedMetadata"):
            continue
        tweet = _unwrap((item.get("tweet_results") or {}).get("result"))
        if not tweet:
            continue
        retweeted = _unwrap((tweet["legacy"].get("retweeted_status_result") or {}).get("result"))
        tweets.append(retweeted or tweet)
    return tweets


# ── Scraping ──────────────────────────────────────────────────────────────────

async def scrape_feed(target_posts: int = 200, following: bool = False, max_scrolls: int = 40) -> list[dict]:
    """Scrolls the home feed until about target_posts unique posts are seen. Newest data per post wins."""
    now = datetime.now(timezone.utc)
    posts: dict[str, dict] = {}
    pending: list[asyncio.Task] = []

    async with async_playwright() as p:
        context = await get_cdp_browser_context(p)
        page = await get_clean_page(context)

        async def read(response):
            try:
                for tweet in tweets_from_timeline(await response.json()):
                    post = parse_tweet(tweet, now)
                    posts[post["id"]] = post
            except Exception as e:  # one bad response shouldn't stop the scroll
                print(f"warning: skipped a timeline response ({e})", file=sys.stderr)

        def on_response(response):
            if "/graphql/" in response.url and any(op in response.url for op in TIMELINE_OPS):
                pending.append(asyncio.ensure_future(read(response)))

        page.on("response", on_response)
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded")
            await page.wait_for_selector("[data-testid='primaryColumn']", timeout=30000)
            if "/login" in page.url or "/i/flow" in page.url:
                raise RuntimeError("X isn't logged in on the p9222 Chrome profile. Log in there and retry.")
            if following:
                await page.get_by_role("tab", name="Following").click()
            await page.wait_for_timeout(4000)

            stalled = 0
            for _ in range(max_scrolls):
                if len(posts) >= target_posts:
                    break
                before = len(posts)
                await page.mouse.wheel(0, 5000)
                await page.wait_for_timeout(2200)
                await asyncio.gather(*pending)
                stalled = stalled + 1 if len(posts) == before else 0
                if stalled >= 4:
                    break
            await asyncio.gather(*pending)
        finally:
            await page.close()

    return list(posts.values())


def rank_posts(posts: list[dict], max_age_hours: Optional[float] = 72, min_likes: int = 0) -> list[dict]:
    kept = [
        p for p in posts
        if (max_age_hours is None or p["age_hours"] <= max_age_hours) and p["likes"] >= min_likes
    ]
    return sorted(kept, key=lambda p: p["trend_score"], reverse=True)
