"""
Check-in and account verification logic.

Auth modes:
  cookie   → session cookie + new-api-user header  (like the working standalone script)
  password → POST /api/user/login → get token from body → Authorization: Bearer <token>

new-api login response shape:
  { "success": true, "data": { "token": "...", "id": 123, ... } }
The token goes in the Authorization header, NOT as a cookie.
"""

import asyncio
import logging
import requests
from typing import Optional

log = logging.getLogger(__name__)

BASE_HEADERS = {
    "accept":             "application/json, text/plain, */*",
    "accept-language":    "en-US,en;q=0.5",
    "cache-control":      "no-store",
    "sec-fetch-dest":     "empty",
    "sec-fetch-mode":     "cors",
    "sec-fetch-site":     "same-origin",
    "user-agent":         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
}


# ── Session builders ───────────────────────────────────────────────────────

def _cookie_session(base_url: str, session_cookie: str, api_user: str) -> requests.Session:
    """Cookie + new-api-user header auth (exactly like the working standalone script)."""
    s = requests.Session()
    s.headers.update({
        **BASE_HEADERS,
        "new-api-user":       str(api_user),
        "origin":             base_url,
        "referer":            f"{base_url}/console/personal",
        "sec-ch-ua":          '"Chromium";v="148", "Brave";v="148", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-gpc":            "1",
        "priority":           "u=1, i",
    })
    s.cookies.set("session", session_cookie)
    return s


def _bearer_session(base_url: str, token: str, user_id: str) -> requests.Session:
    """Bearer token auth — used after password login."""
    s = requests.Session()
    s.headers.update({
        **BASE_HEADERS,
        "authorization":      f"Bearer {token}",
        "new-api-user":       str(user_id),
        "origin":             base_url,
        "referer":            f"{base_url}/console/personal",
        "sec-ch-ua":          '"Chromium";v="148", "Brave";v="148", "Not/A)Brand";v="99"',
        "sec-ch-ua-mobile":   "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-gpc":            "1",
        "priority":           "u=1, i",
    })
    return s


# ── Login ──────────────────────────────────────────────────────────────────

def _login_sync(site: dict) -> tuple[bool, Optional[str], Optional[str], str]:
    """
    POST /api/user/login with username+password.
    Returns (success, credential, user_id, auth_mode)
    auth_mode is "bearer" if token in body, "cookie" if session set via cookie.
    """
    base_url = site["url"].rstrip("/")
    try:
        s = requests.Session()
        r = s.post(
            f"{base_url}/api/user/login",
            json={"username": site["username"], "password": site["password"]},
            headers={
                **BASE_HEADERS,
                "content-type": "application/json",
                "origin":       base_url,
                "referer":      f"{base_url}/",
            },
            timeout=20,
        )
        data = r.json()
        log.debug(f"login [{site['name']}] HTTP {r.status_code}: {data}")

        if not data.get("success"):
            msg = data.get("message", "Login failed")
            log.warning(f"Login failed for {site['name']}: {msg}")
            return False, None, None, ""

        user_data = data.get("data", {})
        user_id   = str(user_data.get("id", ""))

        # Priority 1: token in response body → Bearer auth
        token = user_data.get("token") or user_data.get("access_token")
        if token:
            log.info(f"Login OK (bearer) for {site['name']} (user_id={user_id})")
            return True, token, user_id, "bearer"

        # Priority 2: session cookie set by server → cookie auth
        session_cookie = (
            r.cookies.get("session")
            or s.cookies.get("session")
        )
        if session_cookie:
            log.info(f"Login OK (cookie) for {site['name']} (user_id={user_id})")
            return True, session_cookie, user_id, "cookie"

        # Priority 3: check Set-Cookie header directly (some sites use different jar)
        set_cookie = r.headers.get("Set-Cookie", "")
        if "session=" in set_cookie:
            import re
            m = re.search(r"session=([^;]+)", set_cookie)
            if m:
                session_cookie = m.group(1)
                log.info(f"Login OK (cookie/header) for {site['name']} (user_id={user_id})")
                return True, session_cookie, user_id, "cookie"

        log.warning(f"Login succeeded but no usable credential for {site['name']}. "
                    f"Response data: {user_data} | Cookies: {dict(r.cookies)}")
        return False, None, None, ""

    except Exception as e:
        log.error(f"Login error for {site['name']}: {e}")
        return False, None, None, ""


# ── Build session from site ────────────────────────────────────────────────

def _session_for_site(site: dict) -> tuple[Optional[requests.Session], str]:
    """
    Returns (session, error_message).
    session is None on failure.
    """
    base_url = site["url"].rstrip("/")

    if site["auth_type"] == "cookie":
        cookie = site.get("session_cookie", "")
        uid    = site.get("api_user", "")
        if not cookie:
            return None, "No session cookie stored"
        return _cookie_session(base_url, cookie, uid), ""

    else:  # password
        ok, credential, user_id, auth_mode = _login_sync(site)
        if not ok:
            return None, "Login failed — check username/password"
        if auth_mode == "bearer":
            return _bearer_session(base_url, credential, user_id), ""
        else:  # cookie
            return _cookie_session(base_url, credential, user_id), ""


# ── Balance ────────────────────────────────────────────────────────────────

def _get_self(s: requests.Session, base_url: str) -> Optional[dict]:
    try:
        r = s.get(f"{base_url}/api/user/self", timeout=20)
        data = r.json()
        if data.get("success"):
            return data.get("data", {})
        log.debug(f"user/self failed: {data.get('message')}")
    except Exception as e:
        log.debug(f"user/self error: {e}")
    return None


def _fmt(v: int) -> str:
    return f"${v / 500000:.4f}"


# ── Verify ─────────────────────────────────────────────────────────────────

def _verify_sync(site: dict) -> tuple[bool, str]:
    base_url = site["url"].rstrip("/")
    s, err = _session_for_site(site)
    if s is None:
        return False, err

    try:
        r    = s.get(f"{base_url}/api/user/self", timeout=20)
        data = r.json()

        if r.status_code == 401 or not data.get("success"):
            msg = data.get("message", f"HTTP {r.status_code}")
            return False, f"Auth rejected: {msg}"

        d    = data["data"]
        name = d.get("display_name") or d.get("username") or "unknown"
        bal  = _fmt(d.get("quota", 0))
        used = _fmt(d.get("used_quota", 0))
        return True, f"👤 {name}  💰 {bal}  (used {used})"

    except requests.exceptions.ConnectionError:
        return False, "Cannot connect to site"
    except requests.exceptions.Timeout:
        return False, "Timed out"
    except Exception as e:
        return False, f"Error: {str(e)[:80]}"


# ── Check-in ───────────────────────────────────────────────────────────────

def _checkin_sync(site: dict) -> tuple[bool, str, Optional[str]]:
    base_url = site["url"].rstrip("/")
    s, err = _session_for_site(site)
    if s is None:
        return False, err, None

    try:
        # Balance before
        info_before = _get_self(s, base_url)
        bal_before  = None
        if info_before:
            bal_before = f"Balance {_fmt(info_before.get('quota', 0))} | Used {_fmt(info_before.get('used_quota', 0))}"

        # Check-in
        r = s.post(f"{base_url}/api/user/checkin", timeout=20)
        log.debug(f"checkin [{site['name']}] HTTP {r.status_code}: {r.text[:300]}")

        try:
            data = r.json()
        except Exception:
            return False, f"Non-JSON (HTTP {r.status_code}): {r.text[:80]}", None

        success = data.get("success", False)
        message = data.get("message", "")

        # Some forks use ret/code
        if not success and (data.get("ret") == 1 or data.get("code") == 0):
            success = True

        # Already checked in → success
        already_kw = ["already", "signed", "today", "重复", "已签"]
        if not success and any(kw in message.lower() for kw in already_kw):
            return True, f"Already checked in today ({message})", bal_before

        if not success:
            return False, message or f"Failed (HTTP {r.status_code})", None

        # Balance after + reward
        info_after = _get_self(s, base_url)
        bal_after  = bal_before
        if info_after:
            if info_before:
                reward = (
                    (info_after.get("quota", 0) + info_after.get("used_quota", 0))
                    - (info_before.get("quota", 0) + info_before.get("used_quota", 0))
                )
                reward_str = f"  (+{_fmt(int(reward))} reward)" if reward > 0 else ""
            else:
                reward_str = ""
            bal_after = f"Balance {_fmt(info_after.get('quota', 0))}{reward_str}"

        return True, message or "签到成功", bal_after

    except requests.exceptions.ConnectionError:
        return False, "Cannot connect to site", None
    except requests.exceptions.Timeout:
        return False, "Timed out", None
    except Exception as e:
        log.error(f"Checkin error [{site['name']}]: {e}")
        return False, f"Error: {str(e)[:80]}", None


# ── Async wrappers ─────────────────────────────────────────────────────────

async def verify_account(site: dict) -> tuple[bool, str]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _verify_sync, site)


async def do_checkin_site(site: dict) -> tuple[bool, str, Optional[str]]:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, _checkin_sync, site)
