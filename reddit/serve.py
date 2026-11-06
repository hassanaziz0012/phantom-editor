"""
Reddit scrape server: one long-running process that answers post and feed scrape requests.

`phantom reddit scrape` and `phantom reddit scrapesub` start a new uv -> Python -> Playwright chain
and a new Chrome tab for every call, and that startup now costs more CPU than the scrape itself.
This server starts once, keeps one tab open on reddit.com, and gets each post's or feed's .json
with fetch() inside that tab, so requests share the browser's login and run side by side.

Protocol: one JSON object per line on stdin, one reply per line on stdout, matched by "id".
Requests can run concurrently, so replies may come back in a different order.
    {"id": 1, "cmd": "post", "url": "https://www.reddit.com/r/.../comments/...", "max_comments": 10}
    {"id": 2, "cmd": "feed", "subreddit": "forhire", "feed": "new", "limit": 25}
    {"id": 3, "cmd": "info", "ids": ["1x6dqiz", "t3_1x5j7dl", ...]}
    -> {"id": 1, "result": {...same JSON as `phantom reddit scrape --json`...}}
    -> {"id": 2, "error": "Reddit returned HTTP 404 for ...: Not Found"}
    -> {"id": 3, "result": {"posts": [{"id", "url", "title", "author", "body", "removed_by_category"}, ...]}}
Post URLs must be full /comments/ permalinks; share links aren't followed.
"info" looks up any number of posts by ID, 100 per request, to check whether they are still up;
posts Reddit no longer knows are left out of its result.
If Reddit blocks .json, every request reads old.reddit.com pages instead (see FETCH_HTML_JS). "info" then
adds "undetectable_removals": ["reddit"], as those pages don't show posts removed by Reddit's own filters.
Logs go to stderr. The server exits once stdin closes and the requests in flight are answered.

Usage:
    uv run python reddit/serve.py [--port 9222] [--headless]
"""

import argparse
import asyncio
import json
import os
import sys
import time
from typing import Any, Dict, Optional, Set, Tuple

from playwright.async_api import Error as PlaywrightError, Page, async_playwright

# Ensure repo root is in python path for module imports
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from reddit.reddit_json import (
    MAX_INFO_IDS,
    check_post_data,
    collect_feed_items,
    decode_response,
    html_url,
    info_item_fields,
    info_url,
    is_blocked_page,
    json_url,
    listing_data,
    post_fields,
    rate_limit_used_up,
    seconds_until_reset,
)
from browser.cdp import CDP_PORT, PROFILE_DIR, get_cdp_browser_context
from reddit.scrape_reddit_post import ScrapedPostData
from reddit.scrape_subreddit import ScrapedSubredditData, SubredditPostItem, parse_subreddit_input

# A plain-text page on reddit.com, so the tab's fetch() calls are same-origin and carry the login
# cookies without Chrome running any of Reddit's web app
HOME_URL = "https://www.reddit.com/robots.txt"

# Same retry policy as the CLI scrapers, except that a rate limit pauses every request, not just one.
# A rate limit waits until Reddit's window resets, if its headers say when; everything else waits RETRY_DELAY
RETRY_STATUSES = {429, 502, 503, 504}
MAX_RETRIES = 3
RETRY_DELAY = 240
FETCH_TIMEOUT_MS = 30000

RATE_LIMIT_HEADERS = ["x-ratelimit-remaining", "x-ratelimit-reset", "retry-after"]

# No referrer: Reddit answers .json requests whose referrer is robots.txt with a 403 block page,
# while the same request with no referrer (as when the URL is typed into the address bar) gets the JSON.
# No cache: a retry must reach Reddit, not get back the cached 429 that it is retrying
FETCH_JS = """async ([url, timeout, headerNames]) => {
    const resp = await fetch(url, {
        credentials: "include", referrerPolicy: "no-referrer", cache: "no-store", signal: AbortSignal.timeout(timeout),
    });
    const headers = Object.fromEntries(headerNames.map(name => [name, resp.headers.get(name)]));
    return [resp.status, await resp.text(), headers];
}"""

# Page the fallback tab sits on, so its fetch() calls to old.reddit.com pages are same-origin
OLD_HOME_URL = "https://old.reddit.com/robots.txt"

# For when Reddit blocks .json (see reddit_json.is_blocked_page): fetches an old.reddit.com page from
# reddit_json.html_url() and parses it with DOMParser, which runs none of its scripts. A 200 page comes back as the
# JSON text its .json URL would have returned, holding just the fields reddit_json.py reads; anything else comes
# back as is. Two things the HTML doesn't have are missing: a feed post's text (only its post page shows it), and
# whether Reddit's own filters removed a post (/by_id/ still lists it as up). Mod removals show as a
# "[ Removed by moderator ]" title, and author deletions as a missing author
FETCH_HTML_JS = """async ([url, timeout, headerNames]) => {
    const resp = await fetch(url, {
        credentials: "include", referrerPolicy: "no-referrer", cache: "no-store", signal: AbortSignal.timeout(timeout),
    });
    const headers = Object.fromEntries(headerNames.map(name => [name, resp.headers.get(name)]));
    const text = await resp.text();
    const doc = new DOMParser().parseFromString(text, "text/html");
    const links = doc.querySelector("#siteTable");
    if (resp.status !== 200 || !links) return [resp.status, text, headers];

    const num = value => Number(value) || 0;
    const removedTitles = {"[ Removed by moderator ]": "moderator", "[ Removed by Reddit ]": "reddit"};
    const link = thing => {
        const permalink = thing.dataset.permalink;
        const title = (thing.querySelector(".entry a.title")?.textContent || "").trim();
        const flair = thing.querySelector(".entry .linkflairlabel");
        const body = thing.querySelector(".entry .expando .usertext-body .md");
        const domain = thing.dataset.domain || "";
        const url = thing.dataset.url || "";
        return {kind: "t3", data: {
            id: permalink.split("/comments/")[1].split("/")[0],
            title,
            // A deleted account's post has no data-author
            author: thing.dataset.author || "[deleted]",
            subreddit: thing.dataset.subreddit,
            permalink,
            url: url.startsWith("/") ? "https://www.reddit.com" + url : url,
            created_utc: num(thing.dataset.timestamp) / 1000,
            score: num(thing.dataset.score),
            num_comments: num(thing.dataset.commentsCount),
            is_self: thing.classList.contains("self"),
            is_gallery: thing.dataset.isGallery === "true",
            is_video: domain === "v.redd.it" || thing.dataset.kind === "video",
            post_hint: domain === "i.redd.it" || thing.dataset.kind === "image" ? "image" : null,
            stickied: thing.classList.contains("stickied"),
            link_flair_text: flair ? flair.getAttribute("title") || flair.textContent : null,
            selftext_html: body ? body.outerHTML : null,
            removed_by_category: removedTitles[title] || null,
        }};
    };
    const comment = thing => {
        const body = thing.querySelector(":scope > .entry .usertext-body");
        const replies = thing.querySelector(":scope > .child > .sitetable");
        return {kind: "t1", data: {
            author: thing.dataset.author || "[deleted]",
            score: num(thing.querySelector(":scope > .entry .score.unvoted")?.getAttribute("title")),
            body: (body?.textContent || "").trim(),
            body_html: body?.querySelector(".md")?.outerHTML || null,
            replies: replies ? {data: {children: comments(replies)}} : "",
        }};
    };
    const comments = table => [...table.querySelectorAll(":scope > .thing.comment")].map(comment);

    // Not just direct children: a video post page wraps its post in "pinnable" divs. Ads are listed as
    // promoted posts; Reddit's .json leaves them out
    const posts = [...links.querySelectorAll(".thing.link")]
        .filter(t => !t.parentElement.closest(".thing") && t.dataset.promoted !== "true")
        .map(link);
    let data;
    if (new URL(url).pathname.includes("/comments/")) {
        const post = posts[0];
        const ratio = doc.querySelector(".linkinfo .score")?.textContent.match(/\\((\\d+)% upvoted\\)/);
        if (post && ratio) post.data.upvote_ratio = num(ratio[1]) / 100;
        const table = doc.querySelector(".commentarea > .sitetable");
        data = [
            {kind: "Listing", data: {children: posts.slice(0, 1)}},
            {kind: "Listing", data: {children: table ? comments(table) : []}},
        ];
    } else {
        const next = doc.querySelector(".next-button a");
        data = {kind: "Listing", data: {children: posts, after: next ? new URL(next.href).searchParams.get("after") : null}};
    }
    return [200, JSON.stringify(data), headers];
}"""


def log(message: str) -> None:
    print(f"[serve] {message}", file=sys.stderr, flush=True)


class RedditFetcher:
    """
    Gets Reddit URLs with fetch() in one tab, retrying rate limits and network failures. Once Reddit
    blocks a .json URL, it reads old.reddit.com pages from a second tab instead, for the rest of its life.
    """

    def __init__(self, page: Page):
        self.page = page
        # While Reddit is rate limiting, no request goes out before this time.monotonic() value
        self.paused_until = 0.0
        self.json_blocked = False
        self._html_page: Optional[Page] = None
        self._html_page_lock = asyncio.Lock()

    def pause(self, seconds: float) -> None:
        self.paused_until = max(self.paused_until, time.monotonic() + seconds)

    async def html_page(self) -> Page:
        async with self._html_page_lock:
            if self._html_page is None:
                page = await self.page.context.new_page()
                await page.goto(OLD_HOME_URL, wait_until="domcontentloaded", timeout=30000)
                self._html_page = page
            return self._html_page

    async def close(self) -> None:
        if self._html_page is not None:
            await self._html_page.close()

    async def get(self, url: str, page: Optional[Page] = None, script: str = FETCH_JS) -> Tuple[int, str]:
        for attempt in range(1, MAX_RETRIES + 2):
            while (wait := self.paused_until - time.monotonic()) > 0:
                await asyncio.sleep(wait)
            delay, delay_reason = RETRY_DELAY, "default delay"
            try:
                status, text, headers = await (page or self.page).evaluate(script, [url, FETCH_TIMEOUT_MS, RATE_LIMIT_HEADERS])
            except PlaywrightError as e:
                # fetch() rejects on network failures and timeouts; anything else is a real error
                if "Failed to fetch" not in str(e) and "TimeoutError" not in str(e) and "NetworkError" not in str(e):
                    raise
                if attempt > MAX_RETRIES:
                    raise RuntimeError(f"Network failure fetching {url}: {e}") from e
                reason = f"network failure ({str(e).splitlines()[0][:80]})"
            else:
                reset = seconds_until_reset(headers)
                if status not in RETRY_STATUSES:
                    # That was the window's last request: hold the rest until it resets instead of collecting 429s
                    if rate_limit_used_up(headers) and reset is not None:
                        log(f"Reddit's rate limit is used up; pausing all requests {reset:.0f}s until it resets")
                        self.pause(reset)
                    return status, text
                if attempt > MAX_RETRIES:
                    return status, text
                reason = f"HTTP {status}"
                if status == 429 and reset is not None:
                    delay, delay_reason = reset, "until Reddit's rate limit resets"
            log(f"{reason} for {url}; pausing all requests {delay:.0f}s ({delay_reason}) before retry {attempt}/{MAX_RETRIES}")
            self.pause(delay)
        raise AssertionError("unreachable")

    async def get_json(self, url: str) -> Any:
        if not self.json_blocked:
            status, text = await self.get(url)
            if not is_blocked_page(status, text):
                return decode_response(url, status, text)
            if not self.json_blocked:
                log("Reddit is blocking .json requests; reading old.reddit.com pages instead from now on")
                self.json_blocked = True
        page_url = html_url(url)
        status, text = await self.get(page_url, await self.html_page(), FETCH_HTML_JS)
        return decode_response(page_url, status, text)


async def scrape_post(fetcher: RedditFetcher, request: Dict[str, Any]) -> Dict[str, Any]:
    url = request["url"]
    if "/comments/" not in url:
        raise RuntimeError(f"Not a Reddit post permalink: {url}")
    data = check_post_data(url, await fetcher.get_json(json_url(url)))
    return ScrapedPostData(**post_fields(url, data, int(request.get("max_comments", 10)))).model_dump()


async def scrape_feed(fetcher: RedditFetcher, request: Dict[str, Any]) -> Dict[str, Any]:
    feed_type = request.get("feed", "best")
    sub_name, feed_url = parse_subreddit_input(request["subreddit"], feed_type)

    async def fetch_listing(url: str) -> Dict[str, Any]:
        return listing_data(url, await fetcher.get_json(url))

    items = await collect_feed_items(fetch_listing, feed_url, int(request.get("limit", 20)))
    return ScrapedSubredditData(
        subreddit=sub_name,
        feed_type=feed_type,
        feed_url=feed_url,
        total_scraped=len(items),
        posts=[SubredditPostItem(**item) for item in items],
    ).model_dump()


async def scrape_info(fetcher: RedditFetcher, request: Dict[str, Any]) -> Dict[str, Any]:
    ids = list(request.get("ids") or [])
    posts = []
    for start in range(0, len(ids), MAX_INFO_IDS):
        url = info_url(ids[start:start + MAX_INFO_IDS])
        listing = listing_data(url, await fetcher.get_json(url))
        posts += [info_item_fields(child["data"]) for child in listing["children"] if child.get("kind") == "t3"]
    if fetcher.json_blocked:
        # old.reddit's /by_id/ still lists posts Reddit's own filters removed, so it can't tell those from posts that are up
        return {"posts": posts, "undetectable_removals": ["reddit"]}
    return {"posts": posts}


HANDLERS = {"post": scrape_post, "feed": scrape_feed, "info": scrape_info}


def reply(message: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


async def handle(fetcher: RedditFetcher, line: str) -> None:
    request_id = None
    try:
        request = json.loads(line)
        request_id = request.get("id")
        handler = HANDLERS.get(request.get("cmd"))
        if handler is None:
            raise RuntimeError(f"Unknown cmd {request.get('cmd')!r}; expected one of {sorted(HANDLERS)}")
        reply({"id": request_id, "result": await handler(fetcher, request)})
    except Exception as e:
        reply({"id": request_id, "error": str(e) or type(e).__name__})


async def serve(port: int, headless: bool) -> None:
    loop = asyncio.get_running_loop()
    stdin = asyncio.StreamReader(limit=16 * 1024 * 1024)
    await loop.connect_read_pipe(lambda: asyncio.StreamReaderProtocol(stdin), sys.stdin)

    async with async_playwright() as p:
        context = await get_cdp_browser_context(playwright=p, profile_path=PROFILE_DIR, port=port, headless=headless)
        page = await context.new_page()
        fetcher = RedditFetcher(page)
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            log(f"ready (Chrome on port {port})")

            tasks: Set[asyncio.Task] = set()
            while line := await stdin.readline():
                if line.strip():
                    task = asyncio.create_task(handle(fetcher, line.decode()))
                    tasks.add(task)
                    task.add_done_callback(tasks.discard)
            if tasks:
                await asyncio.gather(*tasks)
        finally:
            await fetcher.close()
            await page.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Answer Reddit post and feed scrape requests sent as JSON lines on stdin.")
    parser.add_argument("--port", type=int, default=CDP_PORT, help=f"Chrome DevTools Protocol port (default: {CDP_PORT})")
    parser.add_argument("--headless", action="store_true", help="Start Chrome headless if it isn't running yet")
    args = parser.parse_args()
    asyncio.run(serve(args.port, args.headless))


if __name__ == "__main__":
    main()
