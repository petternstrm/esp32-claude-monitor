#!/usr/bin/env python3
"""
Claude Code Usage Monitor — macOS menu bar app.

Displays Claude plan usage and reset timers in the macOS menu bar.
Reads Claude Desktop's local usage history and optionally obtains exact
reset times from Anthropic using the current Desktop session.

Requirements:
    pip install rumps cryptography

Usage:
    python claude_monitor_mac.py

To run at login, use a launchd plist (see README for instructions).
"""

import base64
import hashlib
import json
import plistlib
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import rumps
from PyObjCTools import AppHelper

# ── Constants ──────────────────────────────────────────────────────────────────

CLAUDE_DIR   = Path.home() / "Library" / "Application Support" / "Claude"
HISTORY_FILE = CLAUDE_DIR / "plan-usage-history.json"
LOGIN_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.internal.claude-usage-monitor.plist"
API_TTL = 300
HISTORY_TTL = 60
STALE_AFTER = 1800

# ── macOS Keychain + token decryption ─────────────────────────────────────────

def _keychain_password() -> Optional[bytes]:
    """Read the safeStorage encryption password from macOS Keychain."""
    candidates = [
        ("Claude Safe Storage",    "Claude Key"),  # current Claude desktop app
        ("Claude Safe Storage",    "Claude"),       # older Claude desktop app
        ("claude Safe Storage",    "claude"),
        ("Electron Safe Storage",  "Claude"),
    ]
    for service, account in candidates:
        try:
            r = subprocess.run(
                ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                capture_output=True, text=True, timeout=15,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip().encode()
        except Exception:
            pass
    return None


def _aes128_key() -> Optional[bytes]:
    """Derive AES-128 key from Keychain password (Chromium PBKDF2 standard)."""
    pw = _keychain_password()
    if pw is None:
        return None
    return hashlib.pbkdf2_hmac("sha1", pw, b"saltysalt", 1003, dklen=16)


def _decrypt_v10(buf: bytes, key: bytes) -> str:
    """Decrypt a Chromium v10 AES-128-CBC safeStorage value."""
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

    ct     = buf[3:]       # strip b"v10" prefix
    iv     = b" " * 16    # Chromium uses space-padded IV
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv), backend=default_backend())
    pt     = cipher.decryptor().update(ct)
    return pt[: -pt[-1]].decode()  # strip PKCS7 padding


def _decrypt_safe_storage(enc_b64: str) -> Optional[str]:
    """Decrypt a safeStorage-encrypted base64 string from Claude config.json."""
    try:
        buf = base64.b64decode(enc_b64)
        if buf[:3] == b"v10":
            key = _aes128_key()
            return _decrypt_v10(buf, key) if key else None
        # v20 (Windows app-bound) won't appear on macOS; anything else: give up
    except Exception:
        pass
    return None


# ── Desktop-app token loading ──────────────────────────────────────────────────

def _parse_token_cache(raw) -> Optional[dict]:
    """Extract the first valid OAuth token dict from a token cache object."""
    now_ms = time.time() * 1000
    cache  = json.loads(raw) if isinstance(raw, str) else raw

    if not isinstance(cache, dict):
        return None
    # Prefer a token scoped for claude_code
    for key, val in cache.items():
        if ("sessions:claude_code" in key
                and isinstance(val, dict) and val.get("expiresAt", 0) > now_ms):
            return {"accessToken": val["token"],
                    "refreshToken": val.get("refreshToken"),
                    "expiresAt": val["expiresAt"]}

    # Fall back to any valid token with a refresh token
    for val in cache.values():
        if (isinstance(val, dict) and val.get("expiresAt", 0) > now_ms
                and val.get("refreshToken")):
            return {"accessToken": val["token"],
                    "refreshToken": val.get("refreshToken"),
                    "expiresAt": val["expiresAt"]}
    return None


def _load_from_desktop() -> Optional[dict]:
    """Try to load OAuth tokens from the Claude desktop app's config.json."""
    config_path = CLAUDE_DIR / "config.json"
    if not config_path.exists():
        return None
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    raw = config.get("oauth:tokenCache")
    if not raw:
        return None

    # Decrypt if it's a base64-encoded safeStorage blob
    if isinstance(raw, str) and raw[:4] not in ("{", "["):
        raw = _decrypt_safe_storage(raw)
        if raw is None:
            return None

    try:
        tokens = _parse_token_cache(raw)
    except Exception:
        return None

    return tokens


def get_valid_token() -> str:
    """Use only the Desktop app's currently valid token; never persist it."""
    tokens = _load_from_desktop()
    if not tokens:
        raise RuntimeError("Exact reset unavailable; sign in to Claude Desktop and allow Keychain access")
    return tokens["accessToken"]


# ── Anthropic API call ─────────────────────────────────────────────────────────

def fetch_rate_limits() -> dict:
    """Make a minimal API call and return rate-limit utilisation from headers."""
    token = get_valid_token()
    body  = json.dumps({
        "model":     "claude-haiku-4-5-20251001",
        "max_tokens": 1,
        "messages":  [{"role": "user", "content": "."}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type":      "application/json",
            "Authorization":     f"Bearer {token}",
            "anthropic-version": "2023-06-01",
            "User-Agent":        "claude-code/1.0.34",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=15) as resp:
            resp.read()  # consume body
            h = resp.headers
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API error {e.code}: {e.read().decode()[:200]}")

    def hf(name): return h.get(name)
    if hf("anthropic-ratelimit-unified-5h-utilization") is None:
        raise RuntimeError("Exact reset unavailable: rate-limit headers missing")

    return {
        "ok":       True,
        "pct_5h":   float(hf("anthropic-ratelimit-unified-5h-utilization")),
        "reset_5h": int(hf("anthropic-ratelimit-unified-5h-reset") or 0),
        "pct_7d":   float(hf("anthropic-ratelimit-unified-7d-utilization")) if hf("anthropic-ratelimit-unified-7d-utilization") is not None else None,
        "reset_7d": int(hf("anthropic-ratelimit-unified-7d-reset") or 0),
    }


def parse_usage_history(raw: bytes, now: Optional[float] = None) -> dict:
    """Read Claude's undocumented, read-only usage history (percentages 0..100)."""
    now = time.time() if now is None else now
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("version") != 2:
        raise ValueError("Unsupported Claude usage-history format")
    samples = payload.get("samples", [])
    if not isinstance(samples, list):
        raise ValueError("Unsupported Claude usage-history format")
    valid = [s for s in samples if isinstance(s, dict) and
             isinstance(s.get("t"), (int, float)) and isinstance(s.get("u"), dict) and
             all(isinstance(s["u"].get(k), (int, float)) and 0 <= s["u"][k] <= 100
                 for k in ("fh", "sd"))]
    if not valid:
        raise ValueError("No Claude usage samples found")
    latest = max(valid, key=lambda s: s["t"])
    sampled_at = latest["t"] / 1000
    if sampled_at > now + 300 or now - sampled_at > STALE_AFTER:
        raise ValueError("Claude usage history is out of date")
    # Percentages are already whole percentages; API utilization is a fraction.
    same_org = sorted((s for s in valid if s.get("org") == latest.get("org") and
                       s["t"] <= latest["t"]), key=lambda s: s["t"], reverse=True)
    reset = 0
    if latest["u"]["fh"] > 0:
        start = same_org[0]["t"]
        for sample in same_org[1:]:
            if sample["u"]["fh"] == 0 or start - sample["t"] > 5 * 3600 * 1000:
                break
            start = sample["t"]
        estimated = int(start / 1000 + 5 * 3600)
        if estimated > now:
            reset = estimated
    return {"pct_5h": latest["u"]["fh"] / 100,
            "pct_7d": latest["u"]["sd"] / 100,
            "reset_5h": reset, "reset_7d": 0,
            "sampled_at": sampled_at, "source": "local"}


def load_local_usage() -> dict:
    return parse_usage_history(HISTORY_FILE.read_bytes())


# ── Formatting helpers ─────────────────────────────────────────────────────────

def fmt_delta(td: timedelta) -> str:
    if td.total_seconds() <= 0:
        return "now"
    m = int(td.total_seconds() / 60)
    d, h, mins = m // 1440, (m % 1440) // 60, m % 60
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {mins}m"
    return f"{mins}m" if mins else "< 1m"


# ── Menu bar app ───────────────────────────────────────────────────────────────

class ClaudeMonitorApp(rumps.App):

    def __init__(self):
        super().__init__("Claude Usage", quit_button=None)
        self._api_data: Optional[dict] = None
        self._api_fetched_at = 0.0
        self._local_data: Optional[dict] = None
        self._local_checked_at = 0.0
        self._show_timer = True
        self._last_error: Optional[str] = None
        self._lock = threading.Lock()
        self._refreshing = False
        self._item_5h = rumps.MenuItem("5h: —")
        self._item_7d = rumps.MenuItem("7d: —")
        self._item_updated = rumps.MenuItem("Loading…")
        self._item_toggle = rumps.MenuItem("Show timer in menu bar", callback=self._on_toggle_timer)
        self._item_toggle.state = True
        self._item_login = rumps.MenuItem("Launch at login", callback=self._on_toggle_login)
        self._item_login.state = LOGIN_AGENT.exists()
        self._item_settings = rumps.MenuItem("Open Claude usage settings", callback=lambda _: webbrowser.open("https://claude.ai/settings/usage"))
        self._item_refresh = rumps.MenuItem("Refresh now", callback=self._on_refresh)
        self._item_quit = rumps.MenuItem("Quit", callback=lambda _: rumps.quit_application())
        self.menu = [
            self._item_5h, self._item_7d, None, self._item_updated, None,
            self._item_toggle, self._item_login, self._item_settings,
            self._item_refresh, None, self._item_quit,
        ]
        self._start_refresh()
        self._data_poll = rumps.Timer(self._on_data_tick, 60)
        self._data_poll.start()
        self._countdown_tick = rumps.Timer(self._on_countdown_tick, 30)
        self._countdown_tick.start()

    def _on_data_tick(self, _sender):
        self._start_refresh()

    def _on_countdown_tick(self, _sender):
        self._redraw()

    def _on_refresh(self, _sender):
        with self._lock:
            self._api_fetched_at = 0.0
            self._local_checked_at = 0.0
        self._start_refresh()

    def _on_toggle_timer(self, sender):
        self._show_timer = not self._show_timer
        sender.state = self._show_timer
        self._redraw()

    def _on_toggle_login(self, sender):
        try:
            if LOGIN_AGENT.exists():
                LOGIN_AGENT.unlink()
                sender.state = False
            else:
                from AppKit import NSBundle
                bundle = str(NSBundle.mainBundle().bundlePath())
                if not bundle.endswith(".app"):
                    raise ValueError("Launch at login requires the installed app")
                LOGIN_AGENT.parent.mkdir(parents=True, exist_ok=True)
                agent = {"Label": "com.internal.claude-usage-monitor",
                         "ProgramArguments": ["/usr/bin/open", "-a", bundle],
                         "RunAtLoad": True}
                LOGIN_AGENT.write_bytes(plistlib.dumps(agent))
                sender.state = True
        except Exception as exc:
            rumps.alert("Launch at login", str(exc))

    def _start_refresh(self):
        with self._lock:
            if self._refreshing:
                return
            self._refreshing = True
        threading.Thread(target=self._do_refresh, daemon=True).start()

    def _do_refresh(self):
        try:
            now = time.time()
            with self._lock:
                needs_api = now - self._api_fetched_at >= API_TTL
                needs_local = now - self._local_checked_at >= HISTORY_TTL
            if needs_local:
                try:
                    data = load_local_usage()
                    with self._lock:
                        self._local_data = data
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    with self._lock:
                        self._local_data = None
                        self._last_error = f"Local usage unavailable: {exc}"
                with self._lock:
                    self._local_checked_at = time.time()
                AppHelper.callAfter(self._redraw)
            if needs_api:
                try:
                    data = fetch_rate_limits()
                    with self._lock:
                        self._api_data = data
                        self._api_fetched_at = time.time()
                        self._last_error = None
                except Exception as exc:
                    with self._lock:
                        self._api_data = None
                        self._api_fetched_at = time.time()
                        self._last_error = str(exc)
            AppHelper.callAfter(self._redraw)
        finally:
            with self._lock:
                self._refreshing = False

    def _redraw(self):
        with self._lock:
            api = self._api_data if time.time() - self._api_fetched_at < API_TTL + 60 else None
            local = self._local_data
            error = self._last_error
        data = api or local
        if data is None:
            self.title = "⚠ Claude"
            self._item_5h.title = "5h: —"
            self._item_7d.title = "7d: —"
            self._item_updated.title = (error or "Open Claude Desktop to see usage")[:110]
            return
        now_utc = datetime.now(timezone.utc)
        def window_line(label, pct_key, reset_key):
            pct = data.get(pct_key)
            pct_str = f"{round(pct * 100)}%" if pct is not None else "—"
            reset = data.get(reset_key)
            timing = "reset time unavailable"
            if reset and reset > time.time():
                date = datetime.fromtimestamp(reset, tz=timezone.utc)
                prefix = "estimated reset" if data is local else "resets"
                timing = f"{prefix} in {fmt_delta(date - now_utc)} ({date.astimezone():%H:%M})"
            return f"{label}: {pct_str}  ·  {timing}"
        self._item_5h.title = window_line("5h", "pct_5h", "reset_5h")
        self._item_7d.title = window_line("7d", "pct_7d", "reset_7d")
        if api:
            self._item_updated.title = f"Server data · updated {datetime.fromtimestamp(self._api_fetched_at):%H:%M}"
        else:
            self._item_updated.title = f"Local data · sampled {datetime.fromtimestamp(local['sampled_at']):%H:%M} · exact reset unavailable"
        reset = data.get("reset_5h")
        suffix = ""
        if self._show_timer and reset and reset > time.time():
            suffix = f" | {'~' if data is local else ''}{fmt_delta(datetime.fromtimestamp(reset, tz=timezone.utc) - now_utc)}"
        self.title = f"◉ {round(data['pct_5h'] * 100)}%{suffix}"


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--smoke-check" in sys.argv:
        from AppKit import NSBundle, NSStatusBar
        assert NSBundle.mainBundle().bundleIdentifier() == "com.internal.claude-usage-monitor"
        assert NSStatusBar.systemStatusBar() is not None
    else:
        ClaudeMonitorApp().run()
