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
    -> {"id": 1, "result": {...same JSON as `phantom reddit scrape --json`...}}
    -> {"id": 2, "error": "Reddit returned HTTP 404 for ...: Not Found"}
Post URLs must be full /comments/ permalinks; share links aren't followed.
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
from typing import Any, Dict, Set, Tuple

from playwright.async_api import Error as PlaywrightError, Page, async_playwright

# Ensure repo root is in python path for module imports
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from reddit.reddit_json import (
    check_post_data,
    collect_feed_items,
    decode_response,
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

FETCH_JS = """async ([url, timeout, headerNames]) => {
    const resp = await fetch(url, {credentials: "include", signal: AbortSignal.timeout(timeout)});
    const headers = Object.fromEntries(headerNames.map(name => [name, resp.headers.get(name)]));
    return [resp.status, await resp.text(), headers];
}"""


def log(message: str) -> None:
    print(f"[serve] {message}", file=sys.stderr, flush=True)


class RedditFetcher:
    """Gets Reddit URLs with fetch() in one tab, retrying rate limits and network failures."""

    def __init__(self, page: Page):
        self.page = page
        # While Reddit is rate limiting, no request goes out before this time.monotonic() value
        self.paused_until = 0.0

    def pause(self, seconds: float) -> None:
        self.paused_until = max(self.paused_until, time.monotonic() + seconds)

    async def get(self, url: str) -> Tuple[int, str]:
        for attempt in range(1, MAX_RETRIES + 2):
            while (wait := self.paused_until - time.monotonic()) > 0:
                await asyncio.sleep(wait)
            delay, delay_reason = RETRY_DELAY, "default delay"
            try:
                status, text, headers = await self.page.evaluate(FETCH_JS, [url, FETCH_TIMEOUT_MS, RATE_LIMIT_HEADERS])
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
        status, text = await self.get(url)
        return decode_response(url, status, text)


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


HANDLERS = {"post": scrape_post, "feed": scrape_feed}


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
        try:
            await page.goto(HOME_URL, wait_until="domcontentloaded", timeout=30000)
            fetcher = RedditFetcher(page)
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
            await page.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Answer Reddit post and feed scrape requests sent as JSON lines on stdin.")
    parser.add_argument("--port", type=int, default=CDP_PORT, help=f"Chrome DevTools Protocol port (default: {CDP_PORT})")
    parser.add_argument("--headless", action="store_true", help="Start Chrome headless if it isn't running yet")
    args = parser.parse_args()
    asyncio.run(serve(args.port, args.headless))


if __name__ == "__main__":
    main()
