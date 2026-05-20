#!/usr/bin/env python3
"""
Claude Code Usage Monitor — macOS menu bar app.

Displays Claude API token usage and rate-limit reset timers in the macOS
menu bar. Reads OAuth tokens from the Claude desktop app via Keychain,
calls the Anthropic API every 5 minutes (~8 tokens), and scans local
Claude Code session files for raw token counts.

Requirements:
    pip install rumps cryptography

Usage:
    python claude_monitor_mac.py

To run at login, use a launchd plist (see README for instructions).
"""

import base64
import hashlib
import json
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import rumps

# ── Constants ──────────────────────────────────────────────────────────────────

CLIENT_ID   = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
SCOPES      = "user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"

TOKEN_CACHE  = Path.home() / ".claude" / "proxy_oauth_token.json"
PROJECTS_DIR = Path.home() / ".claude" / "projects"
CLAUDE_DIR   = Path.home() / "Library" / "Application Support" / "Claude"

API_TTL   = 300  # seconds between Anthropic API calls (~8 tokens each)
JSONL_TTL = 60   # seconds between JSONL scans

# ── macOS Keychain + token decryption ─────────────────────────────────────────

def _keychain_password() -> Optional[bytes]:
    """Read the safeStorage encryption password from macOS Keychain."""
    candidates = [
        ("Claude Safe Storage",    "Claude"),
        ("claude Safe Storage",    "claude"),
        ("Electron Safe Storage",  "Claude"),
    ]
    for service, account in candidates:
        try:
            r = subprocess.run(
                ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
                capture_output=True, text=True, timeout=5,
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

    # Prefer a token scoped for claude_code
    for key, val in cache.items():
        if (CLIENT_ID in key and "sessions:claude_code" in key
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

    if tokens:
        _save_tokens(tokens)
    return tokens


def _save_tokens(tokens: dict) -> None:
    TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(json.dumps(tokens))


def _load_cached() -> Optional[dict]:
    try:
        if TOKEN_CACHE.exists():
            return json.loads(TOKEN_CACHE.read_text())
    except Exception:
        pass
    return None


# ── OAuth token refresh ────────────────────────────────────────────────────────

def _refresh_access_token(refresh_tok: str) -> dict:
    body = json.dumps({
        "grant_type":    "refresh_token",
        "refresh_token": refresh_tok,
        "client_id":     CLIENT_ID,
        "scope":         SCOPES,
    }).encode()
    req = urllib.request.Request(
        "https://platform.claude.com/v1/oauth/token",
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "claude-code/1.0.34"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Token refresh failed: {e.code}")
    return {
        "accessToken":  data["access_token"],
        "refreshToken": data["refresh_token"],
        "expiresAt":    int(time.time() * 1000) + data["expires_in"] * 1000,
    }


def get_valid_token() -> str:
    """Return a valid Bearer token, loading and refreshing as needed."""
    tokens = _load_cached() or _load_from_desktop()
    if not tokens:
        raise RuntimeError(
            "No OAuth tokens found — open Claude desktop app and make sure you're logged in"
        )

    nearly_expired = tokens.get("expiresAt", 0) <= time.time() * 1000 + 60_000
    if nearly_expired:
        if tokens.get("refreshToken"):
            try:
                tokens = _refresh_access_token(tokens["refreshToken"])
                _save_tokens(tokens)
                return tokens["accessToken"]
            except Exception:
                # Refresh token stale; re-read from desktop app
                fresh = _load_from_desktop()
                if fresh and fresh.get("expiresAt", 0) > time.time() * 1000 + 60_000:
                    return fresh["accessToken"]
        raise RuntimeError("Token expired and could not be refreshed")

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

    return {
        "ok":       True,
        "pct_5h":   float(hf("anthropic-ratelimit-unified-5h-utilization") or 0),
        "reset_5h": int(hf("anthropic-ratelimit-unified-5h-reset") or 0),
        "pct_7d":   float(hf("anthropic-ratelimit-unified-7d-utilization") or 0),
        "reset_7d": int(hf("anthropic-ratelimit-unified-7d-reset") or 0),
    }


# ── JSONL token scanning ───────────────────────────────────────────────────────

def scan_jsonl_tokens() -> tuple:
    """Scan ~/.claude/projects JSONL files. Returns (tokens_5h, tokens_7d)."""
    now    = datetime.now(timezone.utc)
    cut_5h = now - timedelta(hours=5)
    cut_7d = now - timedelta(days=7)
    used_5h = used_7d = 0
    seen: set = set()

    for jsonl in PROJECTS_DIR.rglob("*.jsonl"):
        try:
            with open(jsonl, encoding="utf-8", errors="replace") as f:
                for raw in f:
                    raw = raw.strip()
                    if not raw:
                        continue
                    try:
                        entry = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("type") != "assistant":
                        continue
                    ts_str = entry.get("timestamp", "")
                    if not ts_str:
                        continue
                    try:
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    in_5h = ts >= cut_5h
                    in_7d = ts >= cut_7d
                    if not (in_5h or in_7d):
                        continue
                    req_id = entry.get("requestId")
                    if req_id:
                        if req_id in seen:
                            continue
                        seen.add(req_id)
                    usage = entry.get("message", {}).get("usage", {})
                    toks  = (usage.get("input_tokens", 0)
                             + usage.get("cache_creation_input_tokens", 0))
                    if in_5h:
                        used_5h += toks
                    if in_7d:
                        used_7d += toks
        except Exception:
            pass

    return used_5h, used_7d


# ── Formatting helpers ─────────────────────────────────────────────────────────

def fmt_tokens(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


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
        super().__init__("Claude", quit_button=None)

        self._api_data: Optional[dict] = None
        self._api_fetched_at: float    = 0.0
        self._jsonl_5h: int            = 0
        self._jsonl_7d: int            = 0
        self._jsonl_fetched_at: float  = 0.0
        self._show_timer: bool         = True
        self._last_error: Optional[str] = None
        self._lock = threading.Lock()

        self._item_5h      = rumps.MenuItem("5h window:  —")
        self._item_7d      = rumps.MenuItem("7d window:  —")
        self._item_updated = rumps.MenuItem("Loading…")
        self._item_toggle  = rumps.MenuItem(
            "Show timer in menu bar", callback=self._on_toggle_timer
        )
        self._item_toggle.state = True
        self._item_refresh = rumps.MenuItem("Refresh now", callback=self._on_refresh)
        self._item_quit    = rumps.MenuItem("Quit", callback=lambda _: rumps.quit_application())

        self.menu = [
            self._item_5h,
            self._item_7d,
            None,
            self._item_updated,
            None,
            self._item_toggle,
            self._item_refresh,
            None,
            self._item_quit,
        ]

        # Fetch data on startup (background so the app opens immediately)
        threading.Thread(target=self._do_refresh, daemon=True).start()

        # Poll for new data every 2 minutes
        self._data_poll = rumps.Timer(self._on_data_tick, 120)
        self._data_poll.start()

        # Redraw the countdown in the menu bar every 30 seconds (no network call)
        self._countdown_tick = rumps.Timer(self._on_countdown_tick, 30)
        self._countdown_tick.start()

    # ── Timer callbacks (main thread) ──────────────────────────────────────────

    def _on_data_tick(self, _sender):
        threading.Thread(target=self._do_refresh, daemon=True).start()

    def _on_countdown_tick(self, _sender):
        """Re-render the title from cached data — no network needed."""
        self._redraw()

    def _on_refresh(self, _sender):
        """Force a full API re-fetch when the user clicks Refresh now."""
        with self._lock:
            self._api_fetched_at = 0.0
        threading.Thread(target=self._do_refresh, daemon=True).start()

    def _on_toggle_timer(self, sender):
        self._show_timer = not self._show_timer
        sender.state = self._show_timer
        self._redraw()

    # ── Background worker ──────────────────────────────────────────────────────

    def _do_refresh(self):
        now = time.time()

        with self._lock:
            needs_api   = now - self._api_fetched_at   > API_TTL
            needs_jsonl = now - self._jsonl_fetched_at > JSONL_TTL

        if needs_api:
            try:
                data = fetch_rate_limits()
                with self._lock:
                    self._api_data      = data
                    self._api_fetched_at = time.time()
                    self._last_error     = None
            except Exception as exc:
                with self._lock:
                    self._last_error = str(exc)

        if needs_jsonl:
            try:
                h5, h7 = scan_jsonl_tokens()
                with self._lock:
                    self._jsonl_5h       = h5
                    self._jsonl_7d       = h7
                    self._jsonl_fetched_at = time.time()
            except Exception:
                pass

        self._redraw()

    # ── UI renderer (safe to call from any thread via rumps internals) ─────────

    def _redraw(self):
        with self._lock:
            api      = self._api_data
            h5_raw   = self._jsonl_5h
            h7_raw   = self._jsonl_7d
            error    = self._last_error

        # ── Error state ────────────────────────────────────────────────────────
        if error and api is None:
            self.title = "⚠ Claude"
            self._item_5h.title      = f"Error: {error[:72]}"
            self._item_7d.title      = "Make sure Claude desktop app is running"
            self._item_updated.title = f"Failed: {datetime.now().strftime('%H:%M:%S')}"
            return

        now_utc = datetime.now(timezone.utc)

        # ── Window rows ────────────────────────────────────────────────────────
        def window_line(label, raw_tokens, pct_key, reset_key):
            pct_f    = (api or {}).get(pct_key)
            reset_ts = (api or {}).get(reset_key)
            pct_str  = f"{round(pct_f * 100)}%" if pct_f is not None else "?%"
            tok_str  = fmt_tokens(raw_tokens)
            if reset_ts:
                reset_dt    = datetime.fromtimestamp(reset_ts, tz=timezone.utc)
                delta_str   = fmt_delta(reset_dt - now_utc)
                clock_str   = reset_dt.astimezone().strftime("%H:%M")
                timing = f"resets in {delta_str}  ({clock_str})"
            else:
                timing = "reset time unknown"
            return f"{label}   {pct_str}  ({tok_str})   →  {timing}"

        self._item_5h.title      = window_line("5h:", h5_raw, "pct_5h", "reset_5h")
        self._item_7d.title      = window_line("7d:", h7_raw, "pct_7d", "reset_7d")
        self._item_updated.title = f"Updated: {datetime.now().strftime('%H:%M:%S')}"

        # ── Menu bar title ─────────────────────────────────────────────────────
        if api is None:
            self.title = "◉ —"
            return

        pct_5h   = api.get("pct_5h", 0)
        reset_5h = api.get("reset_5h")
        pct_str  = f"{round(pct_5h * 100)}%"

        if self._show_timer and reset_5h:
            reset_dt  = datetime.fromtimestamp(reset_5h, tz=timezone.utc)
            timer_str = fmt_delta(reset_dt - now_utc)
            self.title = f"◉ {pct_str} | {timer_str}"
        else:
            self.title = f"◉ {pct_str}"


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ClaudeMonitorApp().run()
