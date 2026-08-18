"""
Reddit Auto-Login & Session Synchronizer for Playwright

Extracts Reddit authentication cookies, localStorage, and sessionStorage from your
regular Google Chrome browser profile, decrypts and parses them, and saves them
into a dedicated Playwright persistent user directory located at `reddit/.reddit_user`.

Usage:
    uv run python reddit/autologin.py
    or
    python reddit/autologin.py
"""

import asyncio
import glob
import hashlib
import json
import os
import platform
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from playwright.async_api import async_playwright

# Directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PLAYWRIGHT_USER_DIR = os.path.join(BASE_DIR, ".reddit_user")
STORAGE_STATE_FILE = os.path.join(PLAYWRIGHT_USER_DIR, "storage_state.json")

# Target domains / origins
REDDIT_DOMAINS = [".reddit.com", "www.reddit.com", "reddit.com", "sh.reddit.com", "oauth.reddit.com"]
REDDIT_ORIGINS = ["https://www.reddit.com", "https://reddit.com", "https://old.reddit.com", "https://sh.reddit.com"]


# =====================================================================
# 1. Chrome Encryption Key Recovery
# =====================================================================

def get_linux_chrome_key() -> str:
    """Retrieves Chrome Safe Storage password from Secret Service / Keyring on Linux."""
    # 1. Try secret-tool CLI
    for app in ["chrome", "chromium", "google-chrome"]:
        try:
            res = subprocess.run(
                ["secret-tool", "lookup", "application", app],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout.strip()
        except Exception:
            pass

    # 2. Try Secret Service via system python3 PyGObject
    try:
        cmd = [
            "python3",
            "-c",
            """
import gi
gi.require_version('Secret', '1')
from gi.repository import Secret
for schema_name, app_name in [
    ('chrome_libsecret_os_crypt_password_v2', 'chrome'),
    ('chrome_libsecret_os_crypt_password', 'chrome'),
    ('chromium_libsecret_os_crypt_password', 'chromium')
]:
    schema = Secret.Schema.new(schema_name, Secret.SchemaFlags.NONE, {'application': Secret.SchemaAttributeType.STRING})
    pw = Secret.password_lookup_sync(schema, {'application': app_name}, None)
    if pw:
        print(pw)
        break
""",
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        if res.returncode == 0 and res.stdout.strip():
            return res.stdout.strip()
    except Exception:
        pass

    # Fallback to standard hardcoded Linux password
    return "peassword"


def decrypt_chrome_value(enc_val: bytes, password: str) -> str:
    """Decrypts AES-128-CBC encrypted cookie values from Linux Chrome database."""
    if not enc_val:
        return ""

    # Remove version header (e.g. 'v10' or 'v11')
    if enc_val.startswith(b"v10") or enc_val.startswith(b"v11"):
        enc_val = enc_val[3:]

    try:
        # PBKDF2 HMAC-SHA1 key derivation
        key = hashlib.pbkdf2_hmac("sha1", password.encode("utf-8"), b"saltysalt", 1, 16)
        iv = b" " * 16
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
        decryptor = cipher.decryptor()
        decrypted = decryptor.update(enc_val) + decryptor.finalize()

        # Remove PKCS7 padding
        pad_len = decrypted[-1]
        if isinstance(pad_len, int) and 0 < pad_len <= 16:
            decrypted = decrypted[:-pad_len]

        # In modern Chromium (v114+), the decrypted payload has a 32-byte SHA-256 header
        if len(decrypted) > 32:
            try:
                return decrypted[32:].decode("utf-8")
            except Exception:
                pass

        return decrypted.decode("utf-8", errors="ignore")
    except Exception:
        return ""


# =====================================================================
# 2. Chrome Path Detection
# =====================================================================

def find_chrome_profile_dir() -> Optional[str]:
    """Finds the active Google Chrome Default profile directory."""
    system = platform.system()
    candidates = []

    if system == "Linux":
        candidates = [
            os.path.expanduser("~/.config/google-chrome/Default"),
            os.path.expanduser("~/.config/chromium/Default"),
            os.path.expanduser("~/.config/google-chrome-beta/Default"),
        ]
    elif system == "Darwin":
        candidates = [
            os.path.expanduser("~/Library/Application Support/Google/Chrome/Default"),
            os.path.expanduser("~/Library/Application Support/Chromium/Default"),
        ]
    elif system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            candidates = [
                os.path.join(local_app_data, r"Google\Chrome\User Data\Default"),
                os.path.join(local_app_data, r"Chromium\User Data\Default"),
            ]

    for path in candidates:
        if os.path.exists(path):
            return path
    return None


# =====================================================================
# 3. Cookie Extraction & Decryption
# =====================================================================

def extract_reddit_cookies(profile_dir: str) -> List[Dict[str, Any]]:
    """Extracts and decrypts all Reddit cookies from Chrome SQLite DB."""
    cookies_db = os.path.join(profile_dir, "Cookies")
    if not os.path.exists(cookies_db):
        cookies_db = os.path.join(profile_dir, "Network", "Cookies")
    if not os.path.exists(cookies_db):
        print(f"[!] Cookies database not found in {profile_dir}")
        return []

    print(f"[*] Reading Chrome cookies from: {cookies_db}")
    password = get_linux_chrome_key()

    # Safely copy to temp file to avoid lock conflicts with running browser
    tmp_file = tempfile.NamedTemporaryFile(delete=False)
    tmp_path = tmp_file.name
    tmp_file.close()

    try:
        shutil.copy2(cookies_db, tmp_path)
        conn = sqlite3.connect(tmp_path)
        cursor = conn.cursor()

        query = """
            SELECT name, value, host_key, path, is_secure, is_httponly, expires_utc, samesite, encrypted_value
            FROM cookies
            WHERE host_key LIKE '%reddit.com%'
        """
        cursor.execute(query)
        rows = cursor.fetchall()
        conn.close()

        cookies = []
        samesite_map = {-1: "None", 0: "None", 1: "Lax", 2: "Strict"}
        now = time.time()

        for name, value, host, path, is_sec, is_http, exp, same, enc_val in rows:
            if not value and enc_val:
                value = decrypt_chrome_value(enc_val, password)

            if not value:
                continue

            # Convert Chrome WebKit microseconds timestamp (since 1601) to Unix epoch (seconds)
            exp_unix = (exp / 1_000_000.0) - 11644473600 if exp > 0 else -1

            cookie_entry: Dict[str, Any] = {
                "name": name,
                "value": value,
                "domain": host,
                "path": path or "/",
                "secure": bool(is_sec),
                "httpOnly": bool(is_http),
                "sameSite": samesite_map.get(same, "None"),
            }
            if exp_unix > now:
                cookie_entry["expires"] = exp_unix

            cookies.append(cookie_entry)

        return cookies
    except Exception as e:
        print(f"[!] Error extracting cookies: {e}")
        return []
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# =====================================================================
# 4. Pure-Python LevelDB Reader for LocalStorage & SessionStorage
# =====================================================================

def snappy_decompress(data: bytes) -> bytes:
    """Pure-Python Snappy decompressor for LevelDB SSTable data blocks."""
    pos = 0
    uncompressed_len = 0
    shift = 0
    while True:
        if pos >= len(data):
            break
        b = data[pos]
        pos += 1
        uncompressed_len |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7

    out = bytearray()
    while pos < len(data) and len(out) < uncompressed_len:
        b = data[pos]
        pos += 1
        tag = b & 0x03
        if tag == 0:
            lit_len = (b >> 2) + 1
            if lit_len > 60:
                extra_bytes = lit_len - 60
                lit_len = int.from_bytes(data[pos : pos + extra_bytes], "little") + 1
                pos += extra_bytes
            out.extend(data[pos : pos + lit_len])
            pos += lit_len
        elif tag == 1:
            copy_len = ((b >> 2) & 0x07) + 4
            offset = ((b & 0xE0) << 3) | data[pos]
            pos += 1
            start = len(out) - offset
            for i in range(copy_len):
                out.append(out[start + i])
        elif tag == 2:
            copy_len = (b >> 2) + 1
            offset = struct.unpack_from("<H", data, pos)[0]
            pos += 2
            start = len(out) - offset
            for i in range(copy_len):
                out.append(out[start + i])
        elif tag == 3:
            copy_len = (b >> 2) + 1
            offset = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            start = len(out) - offset
            for i in range(copy_len):
                out.append(out[start + i])
    return bytes(out)


def read_varint(data: bytes, offset: int) -> Tuple[Optional[int], int]:
    """Reads a protobuf/LevelDB varint from bytes."""
    res = 0
    shift = 0
    while True:
        if offset >= len(data):
            return None, offset
        b = data[offset]
        offset += 1
        res |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return res, offset


def parse_leveldb_folder(folder: str) -> Dict[bytes, Optional[bytes]]:
    """Parses LevelDB SSTable (.ldb) and WAL (.log) files into a key-value dictionary."""
    kvs: Dict[bytes, Optional[bytes]] = {}
    if not os.path.exists(folder):
        return kvs

    # 1. Parse .ldb (SSTable) files
    for lf in sorted(glob.glob(os.path.join(folder, "*.ldb"))):
        try:
            with open(lf, "rb") as f:
                content = f.read()
            if len(content) < 48 or content[-8:] != b"\x57\xfb\x80\x8b\x24\x75\x47\xdb":
                continue
            footer = content[-48:]
            meta_offset, off = read_varint(footer, 0)
            meta_size, off = read_varint(footer, off)
            index_offset, off = read_varint(footer, off)
            index_size, off = read_varint(footer, off)
            if index_offset is None or index_size is None:
                continue

            idx_raw = content[index_offset : index_offset + index_size]
            if content[index_offset + index_size] == 1:
                idx_raw = snappy_decompress(idx_raw)

            restarts_count = struct.unpack_from("<I", idx_raw, len(idx_raw) - 4)[0]
            restarts_offset = len(idx_raw) - 4 - (restarts_count * 4)
            p = 0
            last_key = bytearray()
            data_handles = []

            while p < restarts_offset:
                shared, p = read_varint(idx_raw, p)
                unshared, p = read_varint(idx_raw, p)
                vlen, p = read_varint(idx_raw, p)
                if p is None or unshared is None or vlen is None:
                    break
                key = last_key[:shared] + idx_raw[p : p + unshared]
                p += unshared
                val = idx_raw[p : p + vlen]
                p += vlen
                last_key = key
                b_off, _ = read_varint(val, 0)
                b_sz, _ = read_varint(val, _)
                if b_off is not None and b_sz is not None:
                    data_handles.append((b_off, b_sz))

            for b_off, b_sz in data_handles:
                b_data = content[b_off : b_off + b_sz]
                if content[b_off + b_sz] == 1:
                    b_data = snappy_decompress(b_data)

                b_restarts = struct.unpack_from("<I", b_data, len(b_data) - 4)[0]
                b_restarts_off = len(b_data) - 4 - (b_restarts * 4)
                bp = 0
                b_last_key = bytearray()

                while bp < b_restarts_off:
                    shared, bp = read_varint(b_data, bp)
                    unshared, bp = read_varint(b_data, bp)
                    vlen, bp = read_varint(b_data, bp)
                    if bp is None or unshared is None or vlen is None:
                        break
                    key = b_last_key[:shared] + b_data[bp : bp + unshared]
                    bp += unshared
                    val = b_data[bp : bp + vlen]
                    bp += vlen
                    b_last_key = key

                    if len(key) >= 8:
                        user_key = bytes(key[:-8])
                        k_type = key[-8]
                        if k_type == 1:  # Put/Value
                            kvs[user_key] = bytes(val)
                        elif k_type == 0:  # Delete
                            kvs.pop(user_key, None)
        except Exception:
            pass

    # 2. Parse .log (WAL) files
    for lf in sorted(glob.glob(os.path.join(folder, "*.log"))):
        try:
            with open(lf, "rb") as f:
                content = f.read()
            pos = 0
            while pos < len(content):
                block_pos = pos % 32768
                if block_pos > 32768 - 7:
                    pos += 32768 - block_pos
                    continue
                if pos + 7 > len(content):
                    break
                crc, length, r_type = struct.unpack_from("<IHB", content, pos)
                pos += 7
                if pos + length > len(content):
                    break
                payload = content[pos : pos + length]
                pos += length
                if len(payload) < 8:
                    continue
                seq, count = struct.unpack_from("<QI", payload, 0)
                p_offset = 8
                for _ in range(count):
                    if p_offset >= len(payload):
                        break
                    tag = payload[p_offset]
                    p_offset += 1
                    key_len, p_offset = read_varint(payload, p_offset)
                    if key_len is None or p_offset + key_len > len(payload):
                        break
                    key = payload[p_offset : p_offset + key_len]
                    p_offset += key_len
                    if tag == 1:  # Put
                        val_len, p_offset = read_varint(payload, p_offset)
                        if val_len is None or p_offset + val_len > len(payload):
                            break
                        val = payload[p_offset : p_offset + val_len]
                        p_offset += val_len
                        kvs[key] = val
                    elif tag == 0:  # Delete
                        kvs.pop(key, None)
        except Exception:
            pass

    return kvs


def extract_origin_storage(kvs: Dict[bytes, Optional[bytes]], origins: List[str]) -> Dict[str, Dict[str, str]]:
    """Extracts decoded key-value storage grouped by origin."""
    result: Dict[str, Dict[str, str]] = {}
    for origin in origins:
        result[origin] = {}
        p_utf8 = f"_{origin}\x00\x01".encode("utf-8")
        p_utf16 = f"_{origin}\x00\x00".encode("utf-8")

        for k, v in kvs.items():
            if not v:
                continue
            key_str = None
            if k.startswith(p_utf8):
                key_str = k[len(p_utf8) :].decode("utf-8", errors="ignore")
            elif k.startswith(p_utf16):
                key_str = k[len(p_utf16) :].decode("utf-16le", errors="ignore")

            if key_str is not None:
                if v.startswith(b"\x01"):
                    val_str = v[1:].decode("utf-8", errors="ignore")
                elif v.startswith(b"\x00"):
                    val_str = v[1:].decode("utf-16le", errors="ignore")
                else:
                    val_str = v.decode("utf-8", errors="ignore")
                result[origin][key_str] = val_str

    return {orig: data for orig, data in result.items() if data}


# =====================================================================
# 5. Playwright Session Synchronization
# =====================================================================

async def sync_session_to_playwright(
    cookies: List[Dict[str, Any]],
    local_storage_by_origin: Dict[str, Dict[str, str]],
    session_storage_by_origin: Dict[str, Dict[str, str]],
    user_data_dir: str = PLAYWRIGHT_USER_DIR,
) -> bool:
    """
    Launches Playwright persistent context targeting `user_data_dir`,
    injects all cookies, localStorage, and sessionStorage, and persists state.
    """
    os.makedirs(user_data_dir, exist_ok=True)
    print(f"[*] Initializing Playwright persistent user directory at: {user_data_dir}")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            channel="chrome",
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            ignore_default_args=["--enable-automation"],
        )

        # 1. Add cookies to the browser context
        if cookies:
            print(f"[*] Injecting {len(cookies)} Reddit cookies into Playwright context...")
            await context.add_cookies(cookies)

        page = context.pages[0] if context.pages else await context.new_page()

        # 2. Inject localStorage and sessionStorage for each origin
        all_origins = set(local_storage_by_origin.keys()) | set(session_storage_by_origin.keys())
        if not all_origins:
            all_origins = {"https://www.reddit.com"}

        for origin in all_origins:
            ls_data = local_storage_by_origin.get(origin, {})
            ss_data = session_storage_by_origin.get(origin, {})
            print(f"[*] Navigating to {origin} to sync {len(ls_data)} localStorage & {len(ss_data)} sessionStorage items...")
            try:
                # 'commit' returns as soon as the response headers are received and origin is set
                await page.goto(origin, wait_until="commit", timeout=8000)
                await page.evaluate(
                    """
                    ([ls, ss]) => {
                        if (ls) {
                            for (const [k, v] of Object.entries(ls)) {
                                try { localStorage.setItem(k, v); } catch(e) {}
                            }
                        }
                        if (ss) {
                            for (const [k, v] of Object.entries(ss)) {
                                try { sessionStorage.setItem(k, v); } catch(e) {}
                            }
                        }
                    }
                    """,
                    [ls_data, ss_data],
                )
            except Exception as e:
                print(f"[!] Warning: Unable to populate storage for {origin}: {e}")

        # 3. Export Playwright storage_state.json for fast reuse
        await context.storage_state(path=STORAGE_STATE_FILE)
        print(f"[+] Storage state saved to: {STORAGE_STATE_FILE}")

        # 4. Verify login state on Reddit
        try:
            print("[*] Verifying Reddit authentication state...")
            await page.goto("https://www.reddit.com/", wait_until="commit", timeout=8000)
            has_session = any(c["name"] == "reddit_session" for c in cookies)
            print(f"[+] Reddit session active: {has_session}")
        except Exception as e:
            print(f"[!] Verification warning: {e}")

        await context.close()
        return True


# =====================================================================
# Main CLI Entry Point
# =====================================================================

async def main():
    print("=" * 60)
    print("Reddit Auto-Login & Session Synchronizer")
    print("=" * 60)

    profile_dir = find_chrome_profile_dir()
    if not profile_dir:
        print("[!] Error: Could not locate Chrome profile directory.")
        sys.exit(1)

    print(f"[+] Found Chrome profile: {profile_dir}")

    # 1. Cookies
    cookies = extract_reddit_cookies(profile_dir)
    print(f"[+] Successfully extracted {len(cookies)} Reddit cookies.")
    for c in cookies:
        print(f"    - {c['name']} ({c['domain']})")

    # 2. Local Storage
    ls_dir = os.path.join(profile_dir, "Local Storage", "leveldb")
    print(f"[*] Parsing Chrome LocalStorage LevelDB at: {ls_dir}")
    ls_kvs = parse_leveldb_folder(ls_dir)
    local_storage = extract_origin_storage(ls_kvs, REDDIT_ORIGINS)
    total_ls = sum(len(v) for v in local_storage.values())
    print(f"[+] Extracted {total_ls} Reddit LocalStorage entries across {len(local_storage)} origins.")

    # 3. Session Storage
    ss_dir = os.path.join(profile_dir, "Session Storage")
    print(f"[*] Parsing Chrome SessionStorage LevelDB at: {ss_dir}")
    ss_kvs = parse_leveldb_folder(ss_dir)
    session_storage = extract_origin_storage(ss_kvs, REDDIT_ORIGINS)
    total_ss = sum(len(v) for v in session_storage.values())
    print(f"[+] Extracted {total_ss} Reddit SessionStorage entries across {len(session_storage)} origins.")

    # 4. Sync to Playwright
    print("\n[*] Synchronizing data to Playwright persistent user directory...")
    success = await sync_session_to_playwright(
        cookies=cookies,
        local_storage_by_origin=local_storage,
        session_storage_by_origin=session_storage,
        user_data_dir=PLAYWRIGHT_USER_DIR,
    )

    if success:
        print("\n" + "=" * 60)
        print(" Successfully synced Reddit session to Playwright!")
        print(f" Persistent User Directory: {PLAYWRIGHT_USER_DIR}")
        print(f" Storage State JSON File:   {STORAGE_STATE_FILE}")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
