"""
Reddit User Profile Scraper using Playwright & Chrome DevTools Protocol (CDP).

Scrapes a Reddit user's profile (https://www.reddit.com/user/<name>/) by attaching to the persistent,
logged-in Chrome instance via CDP. Like serve.py, the tab sits on a plain-text reddit.com page and
gets everything with fetch(), so requests carry the login without Chrome running Reddit's web app.

Extracts:
- account details: karma (post/comment/awarder/awardee), cake day & account age, avatar, bio,
  employee/mod/premium/verified flags, suspension, followers, contributions, contributor status
- trophies and the communities the user moderates
- posts: score, upvote ratio, comments, crossposts, awards, flair, type, body/link, NSFW/spoiler/
  locked/archived/pinned/edited flags, and removal status (removed by moderators, Reddit's filters,
  AutoModerator, admins, ...) with the mod's removal comment where there is one
- comments: score, post they're on, reply/top-level, OP replies, controversial, edited, removed
- views and insights (top countries, shares, reposts) for posts and comments; Reddit only shows
  these to the author, so they're filled in when the profile is the logged-in account's own
- a summary: removal rate, average/median scores, top post & comment, most active subreddits,
  most active hour and weekday

Usage:
    uv run python reddit/scrape_profile.py spez
    uv run python reddit/scrape_profile.py "https://www.reddit.com/user/spez/" --posts 50 --comments 50
    uv run python reddit/scrape_profile.py u/spez --sort top --time year --json
"""

import argparse
import asyncio
import json
import os
import re
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright
from pydantic import BaseModel, Field

# Ensure repo root is in python path for module imports
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from reddit.reddit_json import (
    MAX_LISTING_PAGE,
    decode_response,
    flair_text,
    html_text_blocks,
    iso_timestamp,
    json_url,
    listing_data,
    media_body,
    post_type,
    relative_age,
)
from browser.cdp import CDP_PORT, get_cdp_browser_context, get_clean_page
from reddit.serve import HOME_URL, RedditFetcher

# ==============================================================================
# Configuration & Constants
# ==============================================================================
# The Chrome profile on port 9222 that has the logged-in Reddit account; only used if Chrome isn't running yet
PROFILE_DIR = Path.home() / "Desktop" / "browser-profiles" / "p9222"

REDDIT = "https://www.reddit.com"
SORT_OPTIONS = ["new", "hot", "top", "controversial"]
TIME_OPTIONS = ["all", "year", "month", "week", "day", "hour"]

# How many insights pages and removal-reason lookups run at once
CONCURRENCY = 4

# Reddit stops showing a comment's insights this many days after it was posted
COMMENT_INSIGHTS_DAYS = 90

USERNAME_RE = re.compile(r"^[A-Za-z0-9_-]{3,20}$")

# What each removed_by_category value means, worded the way the post page says it
REMOVAL_LABELS = {
    "moderator": "Removed by the moderators of r/{subreddit}",
    "automod_filtered": "Filtered by AutoModerator (held for mod review)",
    "reddit": "Removed by Reddit's filters",
    "anti_evil_ops": "Removed by Reddit's admins (Anti-Evil Operations)",
    "community_ops": "Removed by Reddit's Community team",
    "legal_operations": "Removed by Reddit's legal team",
    "content_takedown": "Removed by a content takedown",
    "copyright_takedown": "Removed after a copyright notice",
    "deleted": "Deleted by the author",
    "author": "Deleted by the author",
}

# Removals a moderator usually explains in a comment on the post
MOD_EXPLAINED_REMOVALS = {"moderator", "automod_filtered"}

# Parses a sidebar or insights page's HTML without rendering it. Sidebar stats are cells of a value
# <p> and a label <p> ("8,610" / "Karma"); the karma cell's value also holds a tooltip, so take its trigger
PROFILE_SIDEBAR_JS = """(html) => {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const text = el => el ? el.textContent.replace(/\\s+/g, " ").trim() : null;
    const stats = {};
    const karma = doc.querySelector('[data-testid="karma-number"]');
    const grid = karma && karma.closest("p") ? karma.closest("p").parentElement.parentElement : null;
    for (const cell of grid ? grid.children : []) {
        const ps = cell.querySelectorAll("p");
        if (ps.length < 2) continue;
        const value = ps[0].querySelector('[slot="trigger"]') || ps[0];
        stats[text(ps[ps.length - 1]).replace(/\\s*>$/, "")] = text(value);
    }
    const cakeDay = doc.querySelector('[data-testid="cake-day"]');
    return {
        stats,
        followers: text(doc.querySelector('[data-testid="profile-followers-widget"]')),
        cake_day: cakeDay ? cakeDay.getAttribute("datetime") : null,
    };
}"""

# A modal's content sits in a <template>, which DOMParser keeps out of the body's text
PAGE_TEXT_JS = """(html) => {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const parts = [];
    const walk = root => {
        for (const el of root.querySelectorAll("script, style, svg")) el.remove();
        parts.push(root.textContent);
        for (const t of root.querySelectorAll("template")) walk(t.content);
    };
    walk(doc.body);
    return parts.join(" ").replace(/\\s+/g, " ");
}"""

# Each insights number has an aria-label like "390 views", "12K views" or "66.7% upvote ratio"; the first
# of each kind is the post's or comment's own (a later "1 upvote" belongs to the top comment shown below it)
INSIGHTS_JS = """(html) => {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const metrics = {};
    for (const el of doc.querySelectorAll("[aria-label]")) {
        const m = el.getAttribute("aria-label").match(/^([\\d.,]+[KM]?%?) (views?|upvotes?|upvote ratio|comments?|replies|reply|shares?|reposts?|awards?)$/);
        if (m && !(m[2] in metrics)) metrics[m[2]] = m[1];
    }
    const texts = [];
    const heading = [...doc.querySelectorAll("p")].find(p => p.textContent.trim() === "Top countries by views");
    for (let el = heading && heading.nextElementSibling; el; el = el.nextElementSibling) {
        texts.push(...[...el.querySelectorAll("p")].map(p => p.textContent.trim()));
    }
    const countries = [];
    for (let i = 0; i + 1 < texts.length; i += 2) countries.push({country: texts[i], share: texts[i + 1]});
    return {metrics, countries};
}"""

# aria-label words from INSIGHTS_JS -> Insights field
INSIGHT_FIELDS = {
    "view": "views", "views": "views",
    "upvote": "upvotes", "upvotes": "upvotes",
    "upvote ratio": "upvote_ratio",
    "comment": "comments", "comments": "comments",
    "reply": "replies", "replies": "replies",
    "share": "shares", "shares": "shares",
    "repost": "reposts", "reposts": "reposts",
    "award": "awards", "awards": "awards",
}


# ==============================================================================
# Pydantic Schemas for Scraped Profile Data
# ==============================================================================
class Trophy(BaseModel):
    name: str
    description: Optional[str] = None
    granted_at: Optional[str] = Field(default=None, description="ISO date the trophy was awarded, if Reddit says")


class ModeratedSubreddit(BaseModel):
    name: str = Field(description="e.g. 'r/announcements'")
    title: Optional[str] = None
    subscribers: Optional[int] = None
    subreddit_type: Optional[str] = Field(default=None, description="public, private, restricted, archived, ...")
    nsfw: bool = False


class CountryShare(BaseModel):
    country: str
    share: str = Field(description="Share of views, e.g. '22.8%'")


class Insights(BaseModel):
    """What Reddit's /poststats or /commentstats page shows the author."""
    views: Optional[int] = None
    upvotes: Optional[int] = None
    upvote_ratio: Optional[str] = None
    comments: Optional[int] = None
    replies: Optional[int] = None
    shares: Optional[int] = None
    reposts: Optional[int] = None
    awards: Optional[int] = None
    top_countries: List[CountryShare] = []
    rounded: List[str] = Field(default=[], description="Fields Reddit only showed abbreviated (e.g. '12K'), so they're approximate")


class RemovalNote(BaseModel):
    author: str = Field(description="Moderator or bot that commented, e.g. 'AutoModerator'")
    text: str


class ProfileAccount(BaseModel):
    username: str
    user_id: Optional[str] = Field(default=None, description="Reddit thing id, e.g. 't2_1w72'")
    profile_url: str
    is_logged_in_account: bool = Field(description="Whether this is the account logged in to the scraping Chrome")
    display_name: Optional[str] = None
    description: Optional[str] = None
    avatar_url: Optional[str] = None
    banner_url: Optional[str] = None
    created_timestamp: Optional[str] = None
    cake_day: Optional[str] = None
    account_age_days: Optional[int] = None
    total_karma: Optional[int] = None
    post_karma: Optional[int] = None
    comment_karma: Optional[int] = None
    awarder_karma: Optional[int] = None
    awardee_karma: Optional[int] = None
    karma_per_day: Optional[float] = None
    followers: Optional[int] = Field(default=None, description="Only shown when the user allows it")
    contributions: Optional[int] = Field(default=None, description="Posts + comments, as the sidebar counts them")
    total_posts: Optional[int] = Field(default=None, description="From the contributions breakdown (updated daily)")
    total_comments: Optional[int] = Field(default=None, description="From the contributions breakdown (updated daily)")
    contributor_status: Optional[str] = None
    gold_earned: Optional[str] = None
    active_in: Optional[str] = None
    is_employee: bool = False
    is_mod: bool = False
    is_premium: bool = False
    verified: bool = False
    has_verified_email: Optional[bool] = None
    is_suspended: bool = False
    profile_nsfw: bool = False
    accepts_followers: Optional[bool] = None
    accepts_chats: Optional[bool] = None
    accepts_pms: Optional[bool] = None
    trophies: List[Trophy] = []
    moderated_subreddits: List[ModeratedSubreddit] = []


class ProfilePost(BaseModel):
    post_id: str
    title: str
    subreddit: str
    url: str
    created_timestamp: str
    date_posted: str = Field(description="Relative age, e.g. '7h' or '3mo'")
    post_type: str
    flair: Optional[str] = None
    body: str = Field(description="Post text, or '[Image: ...]'-style link for media posts")
    domain: Optional[str] = None
    score: int
    upvote_ratio: Optional[float] = None
    num_comments: int
    num_crossposts: int = 0
    awards: int = 0
    views: Optional[int] = Field(default=None, description="Only visible to the post's author")
    nsfw: bool = False
    spoiler: bool = False
    locked: bool = False
    archived: bool = False
    pinned_to_profile: bool = False
    stickied_in_subreddit: bool = False
    original_content: bool = False
    distinguished: Optional[str] = None
    edited_timestamp: Optional[str] = None
    crosspost_of: Optional[str] = Field(default=None, description="Permalink of the original post, for crossposts")
    removed: bool = False
    removed_by_category: Optional[str] = Field(default=None, description="Reddit's raw value, e.g. 'moderator', 'reddit'")
    removal_status: Optional[str] = Field(default=None, description="e.g. 'Removed by Reddit's filters'")
    removal_note: Optional[RemovalNote] = Field(default=None, description="The mod/bot comment explaining a removal")
    insights: Optional[Insights] = None


class ProfileComment(BaseModel):
    comment_id: str
    body: str
    subreddit: str
    post_title: str
    post_author: Optional[str] = None
    post_url: str
    url: str
    created_timestamp: str
    date_posted: str
    score: int
    score_hidden: bool = False
    is_reply: bool = Field(description="True if it replies to another comment, False if top-level on the post")
    on_own_post: bool = Field(description="Whether the user is the post's author (OP)")
    controversial: bool = False
    awards: int = 0
    views: Optional[int] = Field(default=None, description="Only visible to the comment's author")
    nsfw: bool = False
    locked: bool = False
    stickied: bool = False
    distinguished: Optional[str] = None
    edited_timestamp: Optional[str] = None
    collapsed_reason: Optional[str] = None
    removed: bool = False
    removal_status: Optional[str] = None
    insights: Optional[Insights] = None


class SubredditActivity(BaseModel):
    subreddit: str
    posts: int
    comments: int


class ProfileSummary(BaseModel):
    posts_scraped: int
    comments_scraped: int
    removed_posts: int
    removed_posts_by_status: Dict[str, int]
    removed_comments: int
    avg_post_score: Optional[float] = None
    median_post_score: Optional[float] = None
    avg_comment_score: Optional[float] = None
    median_comment_score: Optional[float] = None
    top_post: Optional[Dict[str, Any]] = None
    top_comment: Optional[Dict[str, Any]] = None
    total_post_views: Optional[int] = None
    total_comment_views: Optional[int] = None
    nsfw_posts: int = 0
    locked_posts: int = 0
    edited_posts: int = 0
    edited_comments: int = 0
    controversial_comments: int = 0
    comments_on_own_posts: int = 0
    top_subreddits: List[SubredditActivity] = []
    most_active_hour_utc: Optional[int] = None
    most_active_weekday: Optional[str] = None
    first_activity: Optional[str] = None
    last_activity: Optional[str] = None
    notes: List[str] = []


class ScrapedProfileData(BaseModel):
    profile_url: str
    sort: str
    time_filter: str
    account: ProfileAccount
    summary: ProfileSummary
    posts: List[ProfilePost]
    comments: List[ProfileComment]


# ==============================================================================
# Parsing Helpers
# ==============================================================================
def parse_username(user_input: str) -> str:
    """The username in a profile URL (reddit.com/user/<name>/..., /u/<name>), 'u/<name>', or a bare name."""
    text = user_input.strip()
    match = re.search(r"(?:^|/)(?:u|user)/([A-Za-z0-9_-]+)", text)
    name = match.group(1) if match else text.lstrip("@")
    if not USERNAME_RE.match(name):
        raise ValueError(f"Not a Reddit username or profile URL: {user_input}")
    return name


def to_int(text: Optional[str]) -> Optional[int]:
    """'8,610' -> 8610; None if there's no number in it."""
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def to_count(text: str) -> Optional[int]:
    """An insights number: '390' -> 390, '12K' -> 12000, '2.1M' -> 2100000."""
    match = re.fullmatch(r"([\d.,]+)([KM]?)", text.strip())
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    return round(number * {"": 1, "K": 1_000, "M": 1_000_000}[match.group(2)])


def edited_at(value: Any) -> Optional[str]:
    """Reddit's "edited" is False or the edit's Unix time."""
    return iso_timestamp(value) if value and not isinstance(value, bool) else None


def removal_status(category: Optional[str], subreddit: str) -> Optional[str]:
    if not category:
        return None
    return REMOVAL_LABELS.get(category, f"Removed ({category})").format(subreddit=subreddit)


def to_insights(raw: Dict[str, Any]) -> Optional[Insights]:
    """An Insights from INSIGHTS_JS's result, or None if the page had none (e.g. 'Unauthorized access')."""
    if not raw["metrics"]:
        return None
    fields: Dict[str, Any] = {"rounded": []}
    for word, value in raw["metrics"].items():
        field = INSIGHT_FIELDS[word]
        fields[field] = value if field == "upvote_ratio" else to_count(value)
        if value[-1] in "KM":
            fields["rounded"].append(field)
    return Insights(**fields, top_countries=[CountryShare(**c) for c in raw["countries"]])


def to_profile_post(post: Dict[str, Any]) -> ProfilePost:
    kind = post_type(post)
    body = "\n\n".join(html_text_blocks(post.get("selftext_html"))) or media_body(post, kind)
    crosspost = post.get("crosspost_parent_list")
    category = post.get("removed_by_category")
    return ProfilePost(
        post_id=post["name"],
        title=post.get("title") or "Untitled Post",
        subreddit=post.get("subreddit_name_prefixed") or f"r/{post.get('subreddit')}",
        url=f"{REDDIT}{post['permalink']}",
        created_timestamp=iso_timestamp(post["created_utc"]),
        date_posted=relative_age(post["created_utc"]),
        post_type=kind,
        flair=flair_text(post),
        body=body,
        domain=post.get("domain"),
        score=post.get("score", 0),
        upvote_ratio=post.get("upvote_ratio"),
        num_comments=post.get("num_comments", 0),
        num_crossposts=post.get("num_crossposts") or 0,
        awards=post.get("total_awards_received") or 0,
        views=post.get("view_count"),
        nsfw=bool(post.get("over_18")),
        spoiler=bool(post.get("spoiler")),
        locked=bool(post.get("locked")),
        archived=bool(post.get("archived")),
        pinned_to_profile=bool(post.get("pinned")),
        stickied_in_subreddit=bool(post.get("stickied")),
        original_content=bool(post.get("is_original_content")),
        distinguished=post.get("distinguished"),
        edited_timestamp=edited_at(post.get("edited")),
        crosspost_of=f"{REDDIT}{crosspost[0]['permalink']}" if crosspost else None,
        removed=bool(category),
        removed_by_category=category,
        removal_status=removal_status(category, post.get("subreddit") or ""),
    )


def to_profile_comment(comment: Dict[str, Any], username: str) -> ProfileComment:
    body = "\n\n".join(html_text_blocks(comment.get("body_html"))) or comment.get("body") or ""
    # Profile listings don't say why a comment is gone; only its body does
    gone = comment.get("body") if comment.get("body") in ("[removed]", "[deleted]") else None
    return ProfileComment(
        comment_id=comment["name"],
        body=body,
        subreddit=comment.get("subreddit_name_prefixed") or f"r/{comment.get('subreddit')}",
        post_title=comment.get("link_title") or "",
        post_author=comment.get("link_author"),
        post_url=comment.get("link_permalink") or "",
        url=f"{REDDIT}{comment['permalink']}",
        created_timestamp=iso_timestamp(comment["created_utc"]),
        date_posted=relative_age(comment["created_utc"]),
        score=comment.get("score", 0),
        score_hidden=bool(comment.get("score_hidden")),
        is_reply=str(comment.get("parent_id", "")).startswith("t1_"),
        on_own_post=(comment.get("link_author") or "").lower() == username.lower(),
        controversial=bool(comment.get("controversiality")),
        awards=comment.get("total_awards_received") or 0,
        nsfw=bool(comment.get("over_18")),
        locked=bool(comment.get("locked")),
        stickied=bool(comment.get("stickied")),
        distinguished=comment.get("distinguished"),
        edited_timestamp=edited_at(comment.get("edited")),
        collapsed_reason=comment.get("collapsed_reason") or comment.get("collapsed_reason_code"),
        removed=bool(gone),
        removal_status={"[removed]": "Removed", "[deleted]": "Deleted by the author"}.get(gone or ""),
    )


def to_account(username: str, about: Dict[str, Any], is_self: bool) -> ProfileAccount:
    """The account fields from /user/<name>/about.json. Only public fields are kept: for the logged-in
    account Reddit also returns private settings and a modhash, which must not end up in the output."""
    sub = about.get("subreddit") or {}
    created = about.get("created_utc")
    age_days = int((time.time() - created) // 86400) if created else None
    total_karma = about.get("total_karma")
    return ProfileAccount(
        username=about.get("name") or username,
        user_id=f"t2_{about['id']}" if about.get("id") else None,
        profile_url=f"{REDDIT}/user/{about.get('name') or username}/",
        is_logged_in_account=is_self,
        display_name=sub.get("title") or None,
        description=sub.get("public_description") or None,
        avatar_url=(about.get("snoovatar_img") if about.get("pref_show_snoovatar") else None) or about.get("icon_img") or None,
        banner_url=(sub.get("banner_img") or "").split("?")[0] or None,
        created_timestamp=iso_timestamp(created) if created else None,
        account_age_days=age_days,
        total_karma=total_karma,
        post_karma=about.get("link_karma"),
        comment_karma=about.get("comment_karma"),
        awarder_karma=about.get("awarder_karma"),
        awardee_karma=about.get("awardee_karma"),
        karma_per_day=round(total_karma / age_days, 2) if total_karma is not None and age_days else None,
        is_employee=bool(about.get("is_employee")),
        is_mod=bool(about.get("is_mod")),
        is_premium=bool(about.get("is_gold")),
        verified=bool(about.get("verified")),
        has_verified_email=about.get("has_verified_email"),
        is_suspended=bool(about.get("is_suspended")),
        profile_nsfw=bool(sub.get("over_18")),
        accepts_followers=about.get("accept_followers"),
        accepts_chats=about.get("accept_chats"),
        accepts_pms=about.get("accept_pms"),
    )


def insights_available(comment: ProfileComment) -> bool:
    posted = datetime.strptime(comment.created_timestamp, "%Y-%m-%dT%H:%M:%S.%f%z")
    return (datetime.now(timezone.utc) - posted).days < COMMENT_INSIGHTS_DAYS


def summarize(account: ProfileAccount, posts: List[ProfilePost], comments: List[ProfileComment]) -> ProfileSummary:
    post_scores = [p.score for p in posts]
    comment_scores = [c.score for c in comments if not c.score_hidden]
    removed = [p for p in posts if p.removed]

    activity: Dict[str, List[int]] = {}
    for p in posts:
        activity.setdefault(p.subreddit, [0, 0])[0] += 1
    for c in comments:
        activity.setdefault(c.subreddit, [0, 0])[1] += 1
    top_subs = sorted(activity.items(), key=lambda kv: -(kv[1][0] + kv[1][1]))[:10]

    times = [datetime.strptime(x.created_timestamp, "%Y-%m-%dT%H:%M:%S.%f%z") for x in [*posts, *comments]]
    hours = Counter(t.hour for t in times)
    weekdays = Counter(t.strftime("%A") for t in times)

    best_post = max(posts, key=lambda p: p.score, default=None)
    best_comment = max(comments, key=lambda c: c.score, default=None)
    post_views = [p.views for p in posts if p.views is not None]
    comment_views = [c.views for c in comments if c.views is not None]

    notes: List[str] = []
    if account.is_suspended:
        notes.append("This account is suspended; Reddit doesn't list its posts or comments.")
    if not posts and not comments and (account.contributions or 0) > 0:
        notes.append(
            f"Reddit counts {account.contributions} contributions but the profile lists none; "
            "the user has probably hidden their activity from their profile."
        )
    if not account.is_logged_in_account:
        notes.append("Views and insights are only visible to the author, so they're not available for this profile.")
    if removed:
        notes.append(f"{len(removed)} of {len(posts)} scraped posts ({len(removed) / len(posts):.0%}) are removed.")
        if account.is_logged_in_account and any(p.views is None for p in removed):
            notes.append("Reddit doesn't show insights for removed posts, so they have no view counts.")
    if account.is_logged_in_account:
        expired = sum(not insights_available(c) for c in comments)
        if expired:
            notes.append(
                f"{expired} comments are older than {COMMENT_INSIGHTS_DAYS} days; "
                "Reddit no longer shows their insights, so they have no view counts."
            )
    rounded = sum("views" in x.insights.rounded for x in [*posts, *comments] if x.insights)
    if rounded:
        notes.append(f"Reddit abbreviates large view counts (e.g. '12K'), so {rounded} of them are approximate.")

    return ProfileSummary(
        posts_scraped=len(posts),
        comments_scraped=len(comments),
        removed_posts=len(removed),
        removed_posts_by_status=dict(Counter(p.removal_status for p in removed)),
        removed_comments=sum(c.removed for c in comments),
        avg_post_score=round(statistics.mean(post_scores), 1) if post_scores else None,
        median_post_score=statistics.median(post_scores) if post_scores else None,
        avg_comment_score=round(statistics.mean(comment_scores), 1) if comment_scores else None,
        median_comment_score=statistics.median(comment_scores) if comment_scores else None,
        top_post={"title": best_post.title, "score": best_post.score, "url": best_post.url} if best_post else None,
        top_comment={"text": " ".join(best_comment.body.split())[:200], "score": best_comment.score, "url": best_comment.url} if best_comment else None,
        total_post_views=sum(post_views) if post_views else None,
        total_comment_views=sum(comment_views) if comment_views else None,
        nsfw_posts=sum(p.nsfw for p in posts),
        locked_posts=sum(p.locked for p in posts),
        edited_posts=sum(p.edited_timestamp is not None for p in posts),
        edited_comments=sum(c.edited_timestamp is not None for c in comments),
        controversial_comments=sum(c.controversial for c in comments),
        comments_on_own_posts=sum(c.on_own_post for c in comments),
        top_subreddits=[SubredditActivity(subreddit=s, posts=n[0], comments=n[1]) for s, n in top_subs],
        most_active_hour_utc=hours.most_common(1)[0][0] if hours else None,
        most_active_weekday=weekdays.most_common(1)[0][0] if weekdays else None,
        first_activity=min(times).isoformat() if times else None,
        last_activity=max(times).isoformat() if times else None,
        notes=notes,
    )


# ==============================================================================
# Scraper Implementation
# ==============================================================================
def log(message: str, quiet: bool) -> None:
    print(message, file=sys.stderr if quiet else sys.stdout)


async def collect_listing(
    fetcher: RedditFetcher, url: str, limit: int, sort: str, time_filter: str
) -> List[Dict[str, Any]]:
    """The first `limit` items of a profile listing (submitted or comments), following its pages."""
    items: List[Dict[str, Any]] = []
    after: Optional[str] = None
    while len(items) < limit:
        params: Dict[str, Any] = {"limit": min(MAX_LISTING_PAGE, limit), "sort": sort}
        if sort in ("top", "controversial"):
            params["t"] = time_filter
        if after:
            params["after"] = after
        listing = listing_data(url, await fetcher.get_json(json_url(url, **params)))
        items.extend(child["data"] for child in listing["children"][: limit - len(items)])
        after = listing.get("after")
        if not after or not listing["children"]:
            break
    return items


async def fetch_optional(fetcher: RedditFetcher, url: str, what: str, quiet: bool) -> Optional[Any]:
    """A .json URL's data, or None with a warning if Reddit won't give it (it isn't worth failing the scrape)."""
    try:
        return await fetcher.get_json(url)
    except Exception as e:
        log(f"[!] Warning: couldn't get {what}: {e}", quiet)
        return None


async def fetch_sidebar(fetcher: RedditFetcher, username: str, quiet: bool) -> Dict[str, Any]:
    """Followers, contributions and the other sidebar stats, which only the profile page shows."""
    status, html = await fetcher.get(f"{REDDIT}/user/{username}/")
    if status != 200:
        log(f"[!] Warning: profile page returned HTTP {status}; skipping sidebar stats", quiet)
        return {"stats": {}, "followers": None, "cake_day": None}
    sidebar = await fetcher.page.evaluate(PROFILE_SIDEBAR_JS, html)

    status, html = await fetcher.get(f"{REDDIT}/svc/shreddit/profiles/{username}/profile-contributions-modal")
    match = re.search(r"([\d,]+) Posts ([\d,]+) Comments", await fetcher.page.evaluate(PAGE_TEXT_JS, html)) if status == 200 else None
    sidebar["total_posts"], sidebar["total_comments"] = (to_int(match.group(1)), to_int(match.group(2))) if match else (None, None)
    return sidebar


async def fetch_insights(fetcher: RedditFetcher, url: str) -> Optional[Insights]:
    """The insights page's numbers. Reddit sometimes serves the page without them, so that gets one
    more try; an "Unauthorized access" page (e.g. for a removed post) or an "insights are only available
    for 90 days" one won't change, so it doesn't."""
    for _ in range(2):
        status, html = await fetcher.get(url)
        if status != 200:
            return None
        insights = to_insights(await fetcher.page.evaluate(INSIGHTS_JS, html))
        if insights or "Unauthorized access" in html or "insights are only available" in html:
            return insights
    return None


async def fetch_removal_note(fetcher: RedditFetcher, post: ProfilePost) -> Optional[RemovalNote]:
    """The moderator's (or AutoModerator's) comment on a removed post, which usually says why."""
    data = await fetcher.get_json(json_url(post.url, limit=25))
    for child in data[1]["data"]["children"]:
        comment = child.get("data", {})
        if child.get("kind") != "t1":
            continue
        if comment.get("distinguished") == "moderator" or comment.get("author") == "AutoModerator":
            text = "\n\n".join(html_text_blocks(comment.get("body_html"))) or comment.get("body") or ""
            return RemovalNote(author=comment.get("author") or "moderator", text=text)
    return None


async def scrape_profile(
    user: str,
    max_posts: int = 25,
    max_comments: int = 25,
    sort: str = "new",
    time_filter: str = "all",
    views: bool = True,
    removal_reasons: bool = True,
    headless: bool = False,
    port: int = CDP_PORT,
    profile_dir: Optional[Path] = None,
    quiet: bool = False,
) -> ScrapedProfileData:
    """
    Scrapes a Reddit user's profile using Playwright attached to Chrome via CDP.

    Args:
        user: Username, 'u/name', or any reddit.com/user/<name> URL.
        max_posts: Maximum number of posts to scrape (default: 25).
        max_comments: Maximum number of comments to scrape (default: 25).
        sort: Listing order ('new', 'hot', 'top', 'controversial').
        time_filter: Time window for 'top' and 'controversial' ('all', 'year', ..., 'hour').
        views: Fetch views/insights for each post and comment, when the profile is the logged-in account's.
        removal_reasons: Look up the mod's comment on posts removed by moderators.
        headless: Whether to start Chrome headless if launching a new instance.
        port: Chrome CDP port (default: 9222).
        profile_dir: Optional custom Chrome user profile directory.
        quiet: If True, sends progress output to stderr so stdout only carries the JSON.

    Returns:
        ScrapedProfileData with the account, summary, posts and comments.
    """
    username = parse_username(user)
    profile_url = f"{REDDIT}/user/{username}/"
    profile_path = profile_dir or PROFILE_DIR

    log(f"[*] Profile: {profile_url}", quiet)
    log(f"[*] Connecting to Chrome via CDP on port {port} (profile: {profile_path})...", quiet)

    async with async_playwright() as p:
        context = await get_cdp_browser_context(playwright=p, profile_path=profile_path, port=port, headless=headless)
        page = await get_clean_page(context)
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            fetcher = RedditFetcher(page)

            # The account lookup and "who am I" don't depend on each other, so they go out together
            about_url = json_url(f"{REDDIT}/user/{username}/about")
            (status, text), me = await asyncio.gather(
                fetcher.get(about_url),
                fetch_optional(fetcher, json_url(f"{REDDIT}/api/me"), "the logged-in account", quiet),
            )
            if status == 404:
                raise RuntimeError(f"u/{username} not found: the account is deleted, shadowbanned, or never existed")
            about = decode_response(about_url, status, text)["data"]
            username = about.get("name") or username

            me = me or {}
            me_name = (me.get("data") or me).get("name") or ""
            is_self = me_name.lower() == username.lower()
            if not me_name:
                log("[!] Warning: Chrome isn't logged in to Reddit; some data may be missing", quiet)

            account = to_account(username, about, is_self)
            log(f"[*] Found u/{account.username} ({account.total_karma} karma){' — your own account' if is_self else ''}", quiet)

            raw_posts: List[Dict[str, Any]] = []
            raw_comments: List[Dict[str, Any]] = []
            if not account.is_suspended:
                if max_posts > 0:
                    log(f"[*] Fetching up to {max_posts} posts ({sort})...", quiet)
                if max_comments > 0:
                    log(f"[*] Fetching up to {max_comments} comments ({sort})...", quiet)

                async def no_items() -> List[Dict[str, Any]]:
                    return []

                # Everything else about the account is independent too: fetch it all at once
                trophies, moderated, sidebar, raw_posts, raw_comments = await asyncio.gather(
                    fetch_optional(fetcher, json_url(f"{REDDIT}/user/{username}/trophies"), "trophies", quiet),
                    fetch_optional(fetcher, json_url(f"{REDDIT}/user/{username}/moderated_subreddits"), "moderated subreddits", quiet),
                    fetch_sidebar(fetcher, username, quiet),
                    collect_listing(fetcher, f"{REDDIT}/user/{username}/submitted", max_posts, sort, time_filter)
                    if max_posts > 0 else no_items(),
                    collect_listing(fetcher, f"{REDDIT}/user/{username}/comments", max_comments, sort, time_filter)
                    if max_comments > 0 else no_items(),
                )

                for t in (trophies or {}).get("data", {}).get("trophies", []):
                    d = t["data"]
                    granted = d.get("granted_at")
                    account.trophies.append(Trophy(
                        name=d.get("name") or "",
                        description=d.get("description"),
                        granted_at=datetime.fromtimestamp(granted, timezone.utc).date().isoformat() if granted else None,
                    ))

                for s in (moderated or {}).get("data") or []:
                    account.moderated_subreddits.append(ModeratedSubreddit(
                        name=s.get("sr_display_name_prefixed") or f"r/{s.get('sr')}",
                        title=s.get("title"),
                        subscribers=s.get("subscribers"),
                        subreddit_type=s.get("subreddit_type"),
                        nsfw=bool(s.get("over_18")),
                    ))

                stats = sidebar["stats"]
                account.followers = to_int(sidebar["followers"])
                account.contributions = to_int(stats.get("Contributions"))
                account.total_posts = sidebar["total_posts"]
                account.total_comments = sidebar["total_comments"]
                account.contributor_status = stats.get("Contributor Status")
                account.gold_earned = stats.get("Gold earned")
                account.active_in = stats.get("Active in")
                account.cake_day = (sidebar["cake_day"] or "")[:10] or None

            posts = [to_profile_post(post) for post in raw_posts]
            comments = [to_profile_comment(comment, username) for comment in raw_comments]

            limiter = asyncio.Semaphore(CONCURRENCY)

            async def limited(coro):
                async with limiter:
                    return await coro

            # Removal reasons and insights share the CONCURRENCY limit and go out as one batch
            explained = [post for post in posts if post.removed_by_category in MOD_EXPLAINED_REMOVALS] if removal_reasons else []
            if explained:
                log(f"[*] Looking up removal reasons for {len(explained)} mod-removed posts...", quiet)

            recent_comments = [comment for comment in comments if insights_available(comment)]
            with_insights = [*posts, *recent_comments] if views and is_self else []
            if with_insights:
                log(f"[*] Fetching views & insights for {len(posts)} posts and {len(recent_comments)} comments...", quiet)
            insight_urls = [f"{REDDIT}/poststats/{post.post_id[3:]}/" for post in posts] if with_insights else []
            insight_urls += [f"{REDDIT}/commentstats/{comment.comment_id}" for comment in recent_comments] if with_insights else []

            results = await asyncio.gather(
                *(limited(fetch_removal_note(fetcher, post)) for post in explained),
                *(limited(fetch_insights(fetcher, url)) for url in insight_urls),
                return_exceptions=True,
            )
            notes, insights_results = results[: len(explained)], results[len(explained):]

            for post, note in zip(explained, notes):
                if isinstance(note, Exception):
                    log(f"[!] Warning: couldn't get removal reason for {post.url}: {note}", quiet)
                else:
                    post.removal_note = note

            for item, insights in zip(with_insights, insights_results):
                if isinstance(insights, Exception):
                    log(f"[!] Warning: couldn't get insights for {item.url}: {insights}", quiet)
                elif insights:
                    item.insights = insights
                    item.views = insights.views

            summary = summarize(account, posts, comments)
            log(f"[✓] Scraped u/{account.username}: {len(posts)} posts, {len(comments)} comments.", quiet)

            return ScrapedProfileData(
                profile_url=account.profile_url,
                sort=sort,
                time_filter=time_filter,
                account=account,
                summary=summary,
                posts=posts,
                comments=comments,
            )
        finally:
            log("[*] Closing scraper tab...", quiet)
            await page.close()


# ==============================================================================
# Output Formatting
# ==============================================================================
def yes(flag: bool, label: str) -> List[str]:
    return [label] if flag else []


def print_scraped_profile_data(data: ScrapedProfileData):
    """
    Prints a formatted summary of the scraped profile.
    """
    a, s = data.account, data.summary
    print("\n" + "=" * 80)
    print(f"                    REDDIT PROFILE: u/{a.username}")
    print(f" URL: {a.profile_url}")
    print("=" * 80)
    if a.display_name and a.display_name != a.username:
        print(f"Display name:   {a.display_name}")
    if a.description:
        print(f"Bio:            {a.description}")
    flags = yes(a.is_suspended, "SUSPENDED") + yes(a.is_employee, "Reddit employee") + yes(a.is_mod, "moderator") \
        + yes(a.is_premium, "premium") + yes(a.verified, "verified") + yes(a.profile_nsfw, "NSFW profile") \
        + yes(a.is_logged_in_account, "your account")
    if flags:
        print(f"Flags:          {', '.join(flags)}")
    if a.created_timestamp:
        print(f"Joined:         {a.cake_day or a.created_timestamp[:10]} ({a.account_age_days} days ago)")
    if a.total_karma is not None:
        print(f"Karma:          {a.total_karma:,} (post {a.post_karma:,}, comment {a.comment_karma:,}, "
              f"awarder {a.awarder_karma or 0:,}, awardee {a.awardee_karma or 0:,}) — {a.karma_per_day}/day")
    if a.contributions is not None:
        split = f" (Reddit counts {a.total_posts:,} posts, {a.total_comments:,} comments)" if a.total_posts is not None else ""
        print(f"Contributions:  {a.contributions:,}{split}")
    if a.followers is not None:
        print(f"Followers:      {a.followers:,}")
    if a.contributor_status:
        print(f"Contributor:    {a.contributor_status}")
    if a.gold_earned:
        print(f"Gold earned:    {a.gold_earned}")
    if a.moderated_subreddits:
        mods = ", ".join(m.name for m in a.moderated_subreddits[:10])
        more = f" (+{len(a.moderated_subreddits) - 10} more)" if len(a.moderated_subreddits) > 10 else ""
        print(f"Moderates:      {mods}{more}")
    if a.trophies:
        names = ", ".join(t.name for t in a.trophies[:8])
        more = f" (+{len(a.trophies) - 8} more)" if len(a.trophies) > 8 else ""
        print(f"Trophies:       {names}{more}")

    print("-" * 80)
    print("SUMMARY")
    print(f"  Scraped {s.posts_scraped} posts and {s.comments_scraped} comments ({data.sort}"
          + (f", {data.time_filter}" if data.sort in ("top", "controversial") else "") + ")")
    if s.posts_scraped:
        print(f"  Post score:     avg {s.avg_post_score}, median {s.median_post_score}")
    if s.comments_scraped:
        print(f"  Comment score:  avg {s.avg_comment_score}, median {s.median_comment_score}")
    if s.total_post_views is not None or s.total_comment_views is not None:
        print(f"  Views:          {s.total_post_views or 0:,} on posts, {s.total_comment_views or 0:,} on comments")
    for status, count in s.removed_posts_by_status.items():
        print(f"  Removed posts:  {count} — {status}")
    if s.removed_comments:
        print(f"  Removed comments: {s.removed_comments}")
    if s.top_post:
        print(f"  Top post:       ▲ {s.top_post['score']} — {s.top_post['title']}")
    if s.top_comment:
        print(f"  Top comment:    ▲ {s.top_comment['score']} — {s.top_comment['text'][:100]}")
    if s.top_subreddits:
        print("  Most active in: " + ", ".join(f"{x.subreddit} ({x.posts}p/{x.comments}c)" for x in s.top_subreddits[:6]))
    if s.most_active_hour_utc is not None:
        print(f"  Most active:    {s.most_active_weekday}s, around {s.most_active_hour_utc:02d}:00 UTC")
    extras = [f"{n} {label}" for n, label in [
        (s.nsfw_posts, "NSFW posts"), (s.locked_posts, "locked posts"), (s.edited_posts, "edited posts"),
        (s.edited_comments, "edited comments"), (s.controversial_comments, "controversial comments"),
        (s.comments_on_own_posts, "comments on own posts"),
    ] if n]
    if extras:
        print(f"  Also:           {', '.join(extras)}")
    for note in s.notes:
        print(f"  Note:           {note}")

    for idx, post in enumerate(data.posts, start=1):
        print(f"\n--- Post #{idx} " + "-" * 66)
        print(f"Title:        {post.title}")
        print(f"Subreddit:    {post.subreddit}" + (f"  [{post.flair}]" if post.flair else ""))
        print(f"Posted:       {post.date_posted} ago ({post.created_timestamp[:16].replace('T', ' ')} UTC)"
              + (f", edited {post.edited_timestamp[:10]}" if post.edited_timestamp else ""))
        ratio = f" ({post.upvote_ratio:.0%} upvoted)" if post.upvote_ratio is not None else ""
        views = f" | 👁 {'~' if post.insights and 'views' in post.insights.rounded else ''}{post.views:,} views" if post.views is not None else ""
        print(f"Engagement:   ▲ {post.score}{ratio} | 💬 {post.num_comments} | 🔁 {post.num_crossposts} crossposts{views}")
        if post.insights and (post.insights.shares or post.insights.reposts or post.insights.top_countries):
            countries = ", ".join(f"{c.country} {c.share}" for c in post.insights.top_countries)
            print(f"Insights:     {post.insights.shares or 0} shares, {post.insights.reposts or 0} reposts"
                  + (f" | top countries: {countries}" if countries else ""))
        flags = yes(post.nsfw, "NSFW") + yes(post.spoiler, "spoiler") + yes(post.locked, "locked") \
            + yes(post.archived, "archived") + yes(post.pinned_to_profile, "pinned to profile") \
            + yes(post.stickied_in_subreddit, "stickied") + yes(post.original_content, "OC") \
            + yes(bool(post.crosspost_of), "crosspost") + yes(bool(post.distinguished), f"distinguished ({post.distinguished})")
        print(f"Type:         {post.post_type}" + (f" | {', '.join(flags)}" if flags else ""))
        if post.removal_status:
            print(f"Status:       ⛔ {post.removal_status}")
        if post.removal_note:
            print(f"Mod note:     u/{post.removal_note.author}: {post.removal_note.text[:300]}")
        if post.body:
            body = post.body if len(post.body) <= 300 else post.body[:300].rstrip() + "…"
            print("Body:         " + body.replace("\n", "\n              "))
        print(f"URL:          {post.url}")

    for idx, comment in enumerate(data.comments, start=1):
        print(f"\n--- Comment #{idx} " + "-" * 63)
        print(f"On:           {comment.post_title} ({comment.subreddit}"
              + (", your own post" if comment.on_own_post else f", by u/{comment.post_author}") + ")")
        score = "hidden" if comment.score_hidden else comment.score
        views = f" | 👁 {'~' if comment.insights and 'views' in comment.insights.rounded else ''}{comment.views:,} views" if comment.views is not None else ""
        print(f"Posted:       {comment.date_posted} ago | ▲ {score}{views}"
              + (f" | edited {comment.edited_timestamp[:10]}" if comment.edited_timestamp else ""))
        flags = yes(comment.is_reply, "reply") + yes(not comment.is_reply, "top-level") \
            + yes(comment.controversial, "controversial") + yes(comment.locked, "locked") \
            + yes(comment.stickied, "stickied") + yes(bool(comment.distinguished), f"distinguished ({comment.distinguished})") \
            + yes(bool(comment.collapsed_reason), f"collapsed ({comment.collapsed_reason})")
        print(f"Flags:        {', '.join(flags)}")
        if comment.removal_status:
            print(f"Status:       ⛔ {comment.removal_status}")
        body = comment.body if len(comment.body) <= 300 else comment.body[:300].rstrip() + "…"
        print("Text:         " + body.replace("\n", "\n              "))
        print(f"URL:          {comment.url}")

    print("\n" + "=" * 80)


# ==============================================================================
# CLI Argument Parser
# ==============================================================================
def parse_arguments():
    parser = argparse.ArgumentParser(
        description="Scrape a Reddit user's profile (account details, posts, comments, removals, views) via Chrome CDP."
    )
    parser.add_argument("user", help="Username, 'u/name', or a reddit.com/user/<name> URL.")
    parser.add_argument("--posts", type=int, default=25, help="Maximum number of posts to scrape (default: 25, 0 to skip).")
    parser.add_argument("--comments", type=int, default=25, help="Maximum number of comments to scrape (default: 25, 0 to skip).")
    parser.add_argument("--sort", choices=SORT_OPTIONS, default="new", help="Order of posts and comments (default: new).")
    parser.add_argument(
        "--time", dest="time_filter", choices=TIME_OPTIONS, default="all",
        help="Time window for --sort top/controversial (default: all).",
    )
    parser.add_argument(
        "--no-views", dest="views", action="store_false",
        help="Skip fetching views & insights (only available on your own profile; one request per post/comment).",
    )
    parser.add_argument(
        "--no-removal-reasons", dest="removal_reasons", action="store_false",
        help="Skip looking up the mod's comment on posts removed by moderators.",
    )
    parser.add_argument("--headless", action="store_true", default=False, help="Run Chrome in headless mode if starting instance.")
    parser.add_argument("--port", type=int, default=CDP_PORT, help=f"Chrome CDP debugging port (default: {CDP_PORT}).")
    parser.add_argument("--json", action="store_true", default=False, help="Output raw JSON to stdout.")
    return parser.parse_args()


# ==============================================================================
# Main Entry Point
# ==============================================================================
async def main():
    args = parse_arguments()

    try:
        scraped_data = await scrape_profile(
            user=args.user,
            max_posts=args.posts,
            max_comments=args.comments,
            sort=args.sort,
            time_filter=args.time_filter,
            views=args.views,
            removal_reasons=args.removal_reasons,
            headless=args.headless,
            port=args.port,
            quiet=args.json,
        )

        if args.json:
            print(json.dumps(scraped_data.model_dump(), indent=2, ensure_ascii=False))
        else:
            print_scraped_profile_data(scraped_data)
    except Exception as e:
        if args.json:
            print(json.dumps({"error": f"Failed to scrape profile: {e}", "user": args.user}))
            sys.exit(0)
        else:
            print(f"[!] Error scraping profile: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
