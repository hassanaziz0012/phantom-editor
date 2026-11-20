"""
A logged-in YouTube Studio session over plain HTTP, no browser.

Studio's web app talks to an internal API (studio.youtube.com/youtubei/v1/...). Those calls
only need the account's Google cookies plus a SAPISIDHASH Authorization header built from
one of them, so once the cookies are saved every command is an ordinary HTTPS request.

Cookies come from the logged-in Chrome profile (p9222) once, via `phantom studio login`,
and are saved outside the repo in ~/.config/phantom/studio_session.json (mode 600). Google
rotates some cookies; every response's Set-Cookie is written back, which keeps the session
alive. When Studio rejects the session anyway, the cookies are re-imported from Chrome
automatically (starting it headless if it isn't running) and the call is retried once.
"""

import asyncio
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Optional

STUDIO_ORIGIN = "https://studio.youtube.com"
SESSION_FILE  = Path(os.environ.get("PHANTOM_STUDIO_SESSION") or Path.home() / ".config" / "phantom" / "studio_session.json")
USER_AGENT    = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/157.0.0.0 Safari/537.36"
CLIENT_NAME   = 62          # WEB_CREATOR
CONFIG_MAX_AGE = 24 * 3600  # Re-read Studio's client version once a day
TIMEOUT       = 60
RETRY_STATUSES = {429, 500, 502, 503, 504}

# Header scheme -> cookie it hashes. Studio sends all three.
AUTH_COOKIES = (("SAPISIDHASH", "SAPISID"), ("SAPISID1PHASH", "__Secure-1PAPISID"), ("SAPISID3PHASH", "__Secure-3PAPISID"))


class StudioError(Exception):
    """Studio refused a request or returned something unusable."""


class StudioAuthError(StudioError):
    """The saved session isn't logged in (or there is none)."""


# ── Session file ──────────────────────────────────────────────────────────────

def _load() -> dict:
    try:
        return json.loads(SESSION_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _save(session: dict) -> None:
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SESSION_FILE.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(session, f, indent=1)
    os.replace(tmp, SESSION_FILE)


# ── Cookies from Chrome ───────────────────────────────────────────────────────

async def _chrome_cookies() -> dict:
    from playwright.async_api import async_playwright
    from browser.cdp import CDP_PORT, PROFILE_DIR, ensure_chrome_cdp, is_cdp_ready

    started = not is_cdp_ready(CDP_PORT)
    endpoint = ensure_chrome_cdp(PROFILE_DIR, CDP_PORT, headless=started)
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp(endpoint)
        try:
            context = browser.contexts[0]
            if started:
                # A freshly started Chrome hasn't refreshed its short-lived cookies yet
                page = await context.new_page()
                try:
                    await page.goto(STUDIO_ORIGIN, wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(3000)
                finally:
                    await page.close()
            cookies = await context.cookies([STUDIO_ORIGIN, "https://www.youtube.com"])
        finally:
            if started:
                # Only close a Chrome we started; the user's own stays open
                cdp = await browser.new_browser_cdp_session()
                await cdp.send("Browser.close")
    return {c["name"]: c["value"] for c in cookies}


def login() -> dict:
    """Import the cookies from the p9222 Chrome profile and check they're logged into Studio."""
    cookies = asyncio.run(_chrome_cookies())
    if "SAPISID" not in cookies:
        raise StudioAuthError("the p9222 Chrome profile isn't logged into YouTube. Log in there, then rerun `phantom studio login`.")
    session = {"cookies": cookies}
    _refresh_config(session)
    _save(session)
    return session


# ── Studio config (channel, client version) ──────────────────────────────────

def _cookie_header(cookies: dict) -> str:
    return "; ".join(f"{k}={v}" for k, v in cookies.items())


def _apply_set_cookies(session: dict, headers) -> bool:
    """Write Google's rotated cookies back into the session. True if anything changed."""
    changed = False
    for header in headers.get_all("Set-Cookie") or []:
        name, _, rest = header.partition("=")
        value, _, attrs = rest.partition(";")
        expired = False
        m = re.search(r"expires=([^;]+)", attrs, re.I)
        if m:
            try:
                expired = parsedate_to_datetime(m.group(1).strip()).timestamp() < time.time()
            except (TypeError, ValueError):
                pass
        if re.search(r"max-age=0\b", attrs, re.I):
            expired = True
        name = name.strip()
        if expired or not value:
            changed |= session["cookies"].pop(name, None) is not None
        elif session["cookies"].get(name) != value:
            session["cookies"][name] = value
            changed = True
    return changed


def _refresh_config(session: dict) -> None:
    """Read the channel ID and client version from Studio's page (and fail if logged out)."""
    req = urllib.request.Request(STUDIO_ORIGIN + "/", headers={"Cookie": _cookie_header(session["cookies"]), "User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            html = r.read().decode("utf-8", "replace")
            final_url = r.url
            _apply_set_cookies(session, r.headers)
    except urllib.error.URLError as e:
        raise StudioError(f"couldn't reach Studio: {e}") from e

    def cfg(key):
        m = re.search(r'"%s":\s*"?([^",}]+)' % key, html)
        return m and m.group(1)

    if "accounts.google.com" in final_url or cfg("LOGGED_IN") != "true" or not cfg("CHANNEL_ID"):
        raise StudioAuthError("the saved Studio session is logged out.")
    session.update(channel_id=cfg("CHANNEL_ID"), client_version=cfg("INNERTUBE_CLIENT_VERSION"), config_time=time.time())


# ── Requests ──────────────────────────────────────────────────────────────────

class Studio:
    """One Studio session. call() makes an internal API request and returns its JSON."""

    def __init__(self, auto_login: bool = True):
        self.auto_login = auto_login
        self._lock = threading.Lock()   # call() is safe from several threads at once
        self.session = _load()
        if not self.session.get("cookies"):
            if not auto_login:
                raise StudioAuthError("no saved Studio session; run `phantom studio login`.")
            self.session = login()
        elif time.time() - self.session.get("config_time", 0) > CONFIG_MAX_AGE:
            self._with_relogin(lambda: _refresh_config(self.session))
            _save(self.session)

    @property
    def channel_id(self) -> str:
        return self.session["channel_id"]

    def context(self) -> dict:
        return {
            "client": {"clientName": CLIENT_NAME, "clientVersion": self.session["client_version"], "hl": "en", "gl": "US"},
            "user": {"delegationContext": {
                "externalChannelId": self.channel_id,
                "roleType": {"channelRoleType": "CREATOR_CHANNEL_ROLE_TYPE_OWNER"},
            }},
        }

    def _headers(self) -> dict:
        cookies = self.session["cookies"]
        ts = str(int(time.time()))
        auth = " ".join(
            f"{scheme} {ts}_{hashlib.sha1(f'{ts} {cookies[name]} {STUDIO_ORIGIN}'.encode()).hexdigest()}"
            for scheme, name in AUTH_COOKIES if name in cookies
        )
        return {
            "Content-Type": "application/json",
            "Authorization": auth,
            "Cookie": _cookie_header(cookies),
            "Origin": STUDIO_ORIGIN,
            "X-Origin": STUDIO_ORIGIN,
            "Referer": STUDIO_ORIGIN + "/",
            "X-Goog-AuthUser": "0",
            "User-Agent": USER_AGENT,
        }

    def _with_relogin(self, fn):
        try:
            return fn()
        except StudioAuthError:
            if not self.auto_login:
                raise
            with self._lock:
                self.session = login()
            return fn()

    def _post(self, path: str, body: dict) -> dict:
        data = json.dumps({"context": self.context(), **body}).encode()
        for attempt in range(3):
            req = urllib.request.Request(f"{STUDIO_ORIGIN}/youtubei/v1/{path}?alt=json", data, self._headers())
            try:
                with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
                    payload = json.loads(r.read())
                    with self._lock:
                        if _apply_set_cookies(self.session, r.headers):
                            _save(self.session)
                    return payload
            except urllib.error.HTTPError as e:
                text = e.read().decode("utf-8", "replace")
                if e.code in (401, 403):
                    raise StudioAuthError(f"Studio rejected the session (HTTP {e.code}).") from e
                if e.code in RETRY_STATUSES and attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                try:
                    message = json.loads(text)["error"]["message"]
                except (ValueError, KeyError, TypeError):
                    message = text[:300]
                raise StudioError(f"{path}: HTTP {e.code}: {message}") from e
            except urllib.error.URLError as e:
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                raise StudioError(f"{path}: {e}") from e
        raise StudioError(f"{path}: no response")

    def call(self, path: str, body: dict) -> dict:
        """POST to Studio's internal API (path like "yta_web/join"); the context is added here."""
        return self._with_relogin(lambda: self._post(path, body))


def session_info() -> Optional[dict]:
    """The saved session's channel and age, without making a request."""
    s = _load()
    if not s.get("cookies"):
        return None
    return {"channel_id": s.get("channel_id"), "client_version": s.get("client_version"),
            "checked": s.get("config_time"), "file": str(SESSION_FILE)}
