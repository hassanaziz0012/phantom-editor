"""
Page navigation with the retry policy the Reddit CLI scrapers share.

Rate limits (HTTP 429) wait until Reddit's window resets when its headers say when;
other retryable statuses and transient network failures wait RETRY_DELAY seconds.
"""

import asyncio
import sys

from playwright.async_api import Page, Response

from reddit.reddit_json import seconds_until_reset

RETRY_STATUSES = {429, 502, 503, 504}
MAX_RETRIES = 3
RETRY_DELAY = 240
NAV_TIMEOUT_MS = 30000


def _log(message: str, quiet: bool) -> None:
    # With --json, stdout only carries the JSON, so progress goes to stderr
    print(message, file=sys.stderr if quiet else sys.stdout)


def _is_transient(error: Exception) -> bool:
    text = str(error)
    return "ERR_HTTP_RESPONSE_CODE_FAILURE" in text or "timeout" in text.lower() or "net::" in text


async def goto_with_retries(page: Page, url: str, quiet: bool = False) -> Response:
    """Navigate ``page`` to ``url`` and return the response, retrying per the policy above."""
    for attempt in range(1, MAX_RETRIES + 2):
        retries_left = attempt <= MAX_RETRIES
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
        except Exception as e:
            if not (retries_left and _is_transient(e)):
                raise
            _log(f"[!] Warning: Transient navigation failure ({str(e)[:60]}...). "
                 f"Sleeping {RETRY_DELAY}s before retry {attempt}/{MAX_RETRIES}...", quiet)
            await asyncio.sleep(RETRY_DELAY)
            continue

        if response is None or response.status not in RETRY_STATUSES:
            return response

        retry_after = response.headers.get("retry-after")
        attempt_desc = "initial attempt" if attempt == 1 else f"retry {attempt - 1}/{MAX_RETRIES}"
        _log(f"[!] Warning: Received HTTP {response.status} on {attempt_desc}. Retry-After: {retry_after or 'None'}", quiet)
        if not retries_left:
            raise RuntimeError(
                f"Failed to navigate to {url}: Received HTTP {response.status} "
                f"(Retry-After: {retry_after or 'None'}) after {MAX_RETRIES} retries."
            )

        reset_wait = seconds_until_reset(response.headers) if response.status == 429 else None
        wait = reset_wait if reset_wait is not None else RETRY_DELAY
        reason = "until Reddit's rate limit resets" if reset_wait is not None else "default delay"
        _log(f"[*] Sleeping {wait:.0f}s ({reason}) before retry {attempt}/{MAX_RETRIES}...", quiet)
        await asyncio.sleep(wait)

    raise AssertionError("unreachable")
