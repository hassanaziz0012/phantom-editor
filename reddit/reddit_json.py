"""
Reddit .json helpers shared by scrape_reddit_post.py and scrape_subreddit.py.

Adding .json to a Reddit post or feed URL returns its data instead of the web app, so a scraping tab
only downloads JSON and Chrome never runs Reddit's page scripts, which is where nearly all of its CPU
time went. Reddit answers plain HTTP clients with a 403, so the scrapers still load these URLs in the
logged-in Chrome.

The functions here turn that JSON into the same fields the scrapers used to read off the rendered page,
written the way the page showed them (e.g. "u/name" links, "7h" ages, "[Image: ...]" bodies).
"""

import json
import math
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Awaitable, Callable, Dict, Iterator, List, Optional
from urllib.parse import urlencode, urlsplit

# Tags that start a new block of text, like a paragraph does on the page
BLOCK_TAGS = {"p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "pre", "blockquote", "ul", "ol", "table", "tr", "div", "hr"}

# Bodies Reddit gives comments that were removed or deleted; they have no text worth keeping
GONE_COMMENT_BODIES = {"[removed]", "[deleted]"}


def json_url(url: str, **params: Any) -> str:
    """The .json version of a reddit.com post or feed URL, keeping its query (e.g. ?t=week) and adding params."""
    parts = urlsplit(url)
    query = "&".join(q for q in (parts.query, urlencode({**params, "raw_json": 1})) if q)
    return f"https://www.reddit.com{parts.path.rstrip('/')}/.json?{query}"


class _TextBlocks(HTMLParser):
    """Splits the HTML Reddit renders from markdown into the text of each block, as the page shows it."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: List[str] = []
        self._buf: List[str] = []
        self._pre = 0
        self._in_link = 0

    def flush(self) -> None:
        text = "".join(self._buf)
        self._buf = []
        if not self._pre:
            text = re.sub(r"\n+", "\n", re.sub(r" *\n *", "\n", text)).strip(" \n")
        # A paragraph holding only a zero-width space is how Reddit's editor spaces out a post; the page drops it
        if text.replace("​", "").strip():
            self.blocks.append(text)

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "a":
            self._in_link += 1
        elif tag == "br":
            self._buf.append("\n")
        elif tag in BLOCK_TAGS:
            self.flush()
            if tag == "pre":
                self._pre += 1

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self._in_link = max(0, self._in_link - 1)
        elif tag in BLOCK_TAGS:
            self.flush()
            if tag == "pre":
                self._pre = max(0, self._pre - 1)

    def handle_data(self, data: str) -> None:
        if self._in_link:
            # The page shows "/u/name" and "/r/sub" links as "u/name" and "r/sub"
            data = re.sub(r"^/([ur]/)", r"\1", data)
        if not self._pre:
            # Line breaks inside a paragraph are kept: the renderer behind this HTML folds a list with no
            # blank line before it into the paragraph, while the page shows it as separate lines. Other
            # runs of ASCII whitespace collapse to one space; &nbsp; (\xa0) is kept, as on the page
            data = re.sub(r"[ \t\r]+", " ", data)
        self._buf.append(data)


def html_text_blocks(html: Optional[str]) -> List[str]:
    """The text of each paragraph, list item, heading, etc. in Reddit's rendered markdown HTML."""
    parser = _TextBlocks()
    parser.feed(html or "")
    parser.close()
    parser.flush()
    return parser.blocks


def post_type(post: Dict[str, Any]) -> str:
    """The page's post-type for a post: text, multi_media, image, gallery, video, crosspost or link."""
    if post.get("crosspost_parent_list"):
        return "crosspost"
    if post.get("is_gallery"):
        return "gallery"
    if post.get("is_video") or post.get("post_hint") in ("hosted:video", "rich:video"):
        return "video"
    if post.get("post_hint") == "image":
        return "image"
    if post.get("is_self"):
        # The page calls a text post "multi_media" exactly when the JSON gives it post_hint "self"
        return "multi_media" if post.get("post_hint") == "self" else "text"
    return "link"


def content_href(post: Dict[str, Any]) -> str:
    """Where the post points: its image, video, gallery or link URL, or a crosspost's original post."""
    if post.get("crosspost_parent_list"):
        return post["crosspost_parent_list"][0]["permalink"]
    return post.get("url_overridden_by_dest") or post.get("url") or ""


def media_body(post: Dict[str, Any], kind: str) -> str:
    """The "[Image: ...]"-style body the scrapers give a post with no text, or "" for a text post."""
    href = content_href(post)
    if kind == "crosspost":
        return f"[Crosspost: {href}]"
    permalink = post.get("permalink", "")
    if not href or href.endswith(permalink):
        return ""
    if kind in ("image", "gallery"):
        return f"[Image: {href}]"
    if kind == "video":
        return f"[Video: {href}]"
    return f"[Link: {href}]"


def flair_text(post: Dict[str, Any]) -> Optional[str]:
    """The flair's text, leaving out emoji, which the page shows as images."""
    rich = post.get("link_flair_richtext") or []
    if rich:
        text = "".join(part.get("t", "") for part in rich if part.get("e") == "text")
    else:
        text = post.get("link_flair_text") or ""
    return text.strip() or None


def relative_age(created_utc: float, now: Optional[float] = None) -> str:
    """The feed's relative age ("29m", "7h", "3d"), rounded to the nearest unit as the page does."""
    secs = max(0.0, (now or time.time()) - created_utc)
    if round(secs / 60) < 60:
        return f"{round(secs / 60)}m" if secs >= 30 else "now"
    if round(secs / 3600) < 24:
        return f"{round(secs / 3600)}h"
    days = round(secs / 86400)
    if days < 30:
        return f"{days}d"
    return f"{round(days / 30.44)}mo" if days < 365 else f"{round(days / 365.25)}y"


def iso_timestamp(created_utc: float) -> str:
    """The post's creation time in the page's created-timestamp format (the JSON only has whole seconds)."""
    return datetime.fromtimestamp(created_utc, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f%z")


def ratio_text(ratio: Optional[float]) -> Optional[str]:
    if ratio is None:
        return None
    return str(int(ratio)) if ratio == int(ratio) else str(ratio)


def flatten_comments(children: List[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
    """Comments in page order: each comment, then its replies, depth first. Skips "load more" stubs."""
    for child in children:
        if child.get("kind") != "t1":
            continue
        yield child["data"]
        replies = child["data"].get("replies")
        if isinstance(replies, dict):
            yield from flatten_comments(replies["data"]["children"])


def post_fields(url: str, data: List[Dict[str, Any]], max_comments: int) -> Dict[str, Any]:
    """The ScrapedPostData fields for a post page's .json response ([post listing, comment listing])."""
    post = data[0]["data"]["children"][0]["data"]
    body = "\n\n".join(html_text_blocks(post.get("selftext_html"))) or media_body(post, post_type(post))

    comments: List[Dict[str, str]] = []
    for comment in flatten_comments(data[1]["data"]["children"]):
        if len(comments) >= max_comments:
            break
        if comment.get("body") in GONE_COMMENT_BODIES:
            continue
        text = re.sub(r"\s+", " ", " ".join(html_text_blocks(comment.get("body_html")))).strip()
        if text:
            comments.append({"author": comment.get("author") or "anonymous", "score": str(comment.get("score", 0)), "text": text})

    return {
        "url": url,
        "title": post.get("title") or "Reddit Post",
        "body": body,
        "subreddit": post.get("subreddit") or "reddit",
        "author": post.get("author") or "unknown",
        "upvote_count": str(post.get("score", 0)),
        "upvote_ratio": ratio_text(post.get("upvote_ratio")),
        # Reddit only fills view_count in for the post's author, and often not even then
        "view_count": f"{post['view_count']} views" if post.get("view_count") else "Not publicly visible",
        "total_comments_count": str(post.get("num_comments", 0)),
        "top_comments": comments,
    }


def feed_item_fields(post: Dict[str, Any]) -> Dict[str, Any]:
    """The SubredditPostItem fields for one post in a feed's .json listing."""
    kind = post_type(post)
    # A feed card only shows body text for text posts; media posts show the media instead
    snippet = "\n".join(html_text_blocks(post.get("selftext_html"))) if kind in ("text", "multi_media") else ""
    if not snippet:
        # A crosspost's card links to the original post, so the feed calls it a link
        snippet = media_body(post, "link" if kind == "crosspost" else kind)
    author = post.get("author") or ""
    return {
        "title": post.get("title") or "Untitled Post",
        "author": f"u/{author}" if author else "u/anonymous",
        "date_posted": relative_age(post["created_utc"]),
        "created_timestamp": iso_timestamp(post["created_utc"]),
        "flair": flair_text(post),
        "body_snippet": snippet,
        "upvotes": str(post.get("score", 0)),
        "comments_n": str(post.get("num_comments", 0)),
        "post_type": kind,
        "url": f"https://www.reddit.com{post['permalink']}",
    }


def error_detail(text: str) -> str:
    """Reddit's reason for an error response, e.g. 'Not Found' or 'banned', or the start of the body."""
    match = re.search(r'"reason":\s*"([^"]+)"', text) or re.search(r'"message":\s*"([^"]+)"', text)
    return match.group(1) if match else text.strip()[:120]


def decode_response(url: str, status: int, text: str) -> Any:
    """The decoded JSON of a Reddit response, or a RuntimeError saying why there is none."""
    if status != 200:
        raise RuntimeError(f"Reddit returned HTTP {status} for {url}: {error_detail(text)}")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise RuntimeError(f"Reddit did not return JSON for {url} (got: {text.strip()[:80]!r})")


def check_post_data(url: str, data: Any) -> List[Dict[str, Any]]:
    """A post page's decoded .json ([post listing, comment listing]), or a RuntimeError if it isn't one."""
    if not isinstance(data, list) or len(data) < 2:
        raise RuntimeError(f"Not a Reddit post URL: {url}")
    return data


def listing_data(url: str, data: Any) -> Dict[str, Any]:
    """A feed listing's "data" object (posts under "children", next page's cursor under "after")."""
    if not isinstance(data, dict) or data.get("kind") != "Listing":
        raise RuntimeError(f"Reddit returned something other than a feed for {url}")
    return data["data"]


# Reddit's rate-limit window is 10 minutes; a longer reset than this isn't a value to trust
MAX_RESET_SECONDS = 900


def seconds_until_reset(headers: Dict[str, Optional[str]]) -> Optional[float]:
    """
    How long to wait for Reddit's rate-limit window to reset, from its x-ratelimit-reset header
    (or retry-after), plus a second of margin. None if neither header is there and readable, in
    which case callers fall back to their fixed retry delay.
    """
    for name in ("x-ratelimit-reset", "retry-after"):
        try:
            seconds = float(headers.get(name) or "")
        except ValueError:
            continue
        if math.isfinite(seconds) and 0 <= seconds <= MAX_RESET_SECONDS:
            return seconds + 1
    return None


def rate_limit_used_up(headers: Dict[str, Optional[str]]) -> bool:
    """True if Reddit's x-ratelimit-remaining header says no requests are left in this window."""
    try:
        return float(headers.get("x-ratelimit-remaining") or "") < 1
    except ValueError:
        return False


# Most posts Reddit returns in one page of a .json listing
MAX_LISTING_PAGE = 100


async def collect_feed_items(
    fetch_listing: Callable[[str], Awaitable[Dict[str, Any]]], feed_url: str, limit: int
) -> List[Dict[str, Any]]:
    """
    The SubredditPostItem fields of the first `limit` posts in a feed, fetching pages of its
    listing with fetch_listing(json_url) (which returns listing_data()) until there are enough.
    """
    items: List[Dict[str, Any]] = []
    after: Optional[str] = None
    while len(items) < limit:
        params = {"limit": min(MAX_LISTING_PAGE, limit), **({"after": after} if after else {})}
        listing = await fetch_listing(json_url(feed_url, **params))
        for child in listing["children"]:
            # Pinned posts aren't part of the page's feed either
            if child.get("kind") == "t3" and not child["data"].get("stickied") and len(items) < limit:
                items.append(feed_item_fields(child["data"]))
        after = listing.get("after")
        if not after:
            break
    return items
