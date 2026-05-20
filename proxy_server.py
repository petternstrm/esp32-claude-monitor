"""
Claude Usage Proxy Server — LOCAL edition
Reads token usage from Claude Code session files on disk.
Also fetches accurate rate limit percentages and reset times from Anthropic API
using the OAuth token from the Claude desktop app.

pip install flask
python proxy_server.py

Open http://localhost:5000/usage to verify.
"""

import json
import time
import subprocess
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from flask import Flask, jsonify

app = Flask(__name__)

PROJECTS_DIR  = Path.home() / ".claude" / "projects"
FETCHER_JS    = Path(__file__).parent / "rate_limit_fetcher.js"

WINDOW_7D = timedelta(days=7)

# Cache for JSONL token counts (raw counts, for display only)
_jsonl_cache     = {"data": None, "fetched_at": 0.0}
_jsonl_cache_ttl = 60  # 1 minute

# Cache for API rate limit data (accurate percentages and reset times)
_api_cache     = {"data": None, "fetched_at": 0.0}
_api_cache_ttl = 300  # 5 minutes (costs ~8 tokens each call)

_lock = threading.Lock()


def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n/1_000_000:.2f}M"
    if n >= 1_000:
        return f"{n/1_000:.1f}K"
    return str(n)


def fmt_delta(td):
    if td.total_seconds() <= 0:
        return "now"
    total_min = int(td.total_seconds() / 60)
    days  = total_min // (60 * 24)
    hours = (total_min % (60 * 24)) // 60
    mins  = total_min % 60
    if days > 0:
        return f"{days}d {hours}h"
    if hours > 0:
        return f"{hours}h {mins}m"
    if total_min < 1:
        return "< 1m"
    return f"{total_min}m"


def fmt_reset_at(dt):
    local_dt  = dt.astimezone()
    local_now = datetime.now().astimezone()
    if local_dt.date() == local_now.date():
        return local_dt.strftime("%H:%M")
    return local_dt.strftime("%a %H:%M")


def parse_ts(ts_str):
    return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))


_WIN_NODE = r"C:\Program Files\nodejs\node.exe"

def fetch_api_rate_limits():
    """Call rate_limit_fetcher.js to get accurate data from Anthropic API."""
    try:
        # On Windows, prefer the full path so this works without a full PATH
        # (e.g. from Startup folder or Task Scheduler). On macOS/Linux, use node from PATH.
        node = _WIN_NODE if Path(_WIN_NODE).exists() else "node"
        result = subprocess.run(
            [node, str(FETCHER_JS)],
            capture_output=True, text=True, timeout=20
        )
        if result.returncode == 0:
            data = json.loads(result.stdout.strip())
            if data.get("ok"):
                return data
        print(f"[rate_limit] Fetcher error: {result.stderr[:200]}")
    except Exception as e:
        print(f"[rate_limit] Exception: {e}")
    return None


def scan_jsonl_tokens():
    """Scan JSONL files for raw token counts (used/weekly only)."""
    now    = datetime.now(timezone.utc)
    cut_7d = now - WINDOW_7D
    cut_5h = now - timedelta(hours=5)

    h5 = {"used": 0}
    w7 = {"used": 0}
    seen = set()

    for jsonl in PROJECTS_DIR.rglob("*.jsonl"):
        try:
            with open(jsonl, "r", encoding="utf-8", errors="replace") as f:
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
                        ts_dt = parse_ts(ts_str)
                    except ValueError:
                        continue

                    in_7d = ts_dt >= cut_7d
                    in_5h = ts_dt >= cut_5h
                    if not (in_7d or in_5h):
                        continue

                    req = entry.get("requestId")
                    if req:
                        if req in seen:
                            continue
                        seen.add(req)

                    usage  = entry.get("message", {}).get("usage", {})
                    tokens = (usage.get("input_tokens", 0) +
                              usage.get("cache_creation_input_tokens", 0))

                    if in_5h:
                        h5["used"] += tokens
                    if in_7d:
                        w7["used"] += tokens
        except Exception:
            pass

    return h5, w7


@app.route("/usage")
def usage():
    with _lock:
        now = time.time()

        # Refresh API rate limit data if stale
        if _api_cache["data"] is None or now - _api_cache["fetched_at"] > _api_cache_ttl:
            api_data = fetch_api_rate_limits()
            if api_data:
                _api_cache["data"] = api_data
                _api_cache["fetched_at"] = now
            elif _api_cache["data"] is None:
                _api_cache["data"] = {}  # signal that we tried

        # Refresh JSONL token counts if stale
        if _jsonl_cache["data"] is None or now - _jsonl_cache["fetched_at"] > _jsonl_cache_ttl:
            try:
                h5_raw, w7_raw = scan_jsonl_tokens()
                _jsonl_cache["data"] = {"h5": h5_raw, "w7": w7_raw}
                _jsonl_cache["fetched_at"] = now
            except Exception as exc:
                if _jsonl_cache["data"] is None:
                    return jsonify({"error": str(exc)[:200]}), 500

        api   = _api_cache["data"] or {}
        jsonl = _jsonl_cache["data"] or {}
        utc_now = datetime.now(timezone.utc)

        # Build response using API data for pct/reset, JSONL for raw counts
        def make_window(jsonl_key, api_pct_key, api_reset_key):
            raw     = jsonl.get(jsonl_key, {}).get("used", 0)
            pct_f   = api.get(api_pct_key)          # float 0.0-1.0 or None
            reset_ts = api.get(api_reset_key)        # Unix seconds or None

            if reset_ts:
                resets_dt = datetime.fromtimestamp(reset_ts, tz=timezone.utc)
                resets_at = fmt_reset_at(resets_dt)
                resets_in = fmt_delta(resets_dt - utc_now)
            else:
                resets_at = "?"
                resets_in = "?"

            pct = round(pct_f * 100) if pct_f is not None else -1

            return {
                "used":      raw,
                "used_fmt":  fmt_tokens(raw),
                "pct":       pct,           # -1 if unknown
                "resets_at": resets_at,
                "resets_in": resets_in,
            }

        result = {
            "h5":          make_window("h5", "pct_5h", "reset_5h"),
            "w7":          make_window("w7", "pct_7d", "reset_7d"),
            "updated_at":  datetime.now().astimezone().strftime("%H:%M:%S"),
            "api_synced":  bool(api.get("ok")),
        }
        return jsonify(result)


@app.route("/health")
def health():
    return jsonify({"ok": True})


if __name__ == "__main__":
    print(f"Claude Usage Proxy — scanning {PROJECTS_DIR}")
    print(f"Rate limit fetcher: {FETCHER_JS}")
    print("Open http://localhost:5000/usage to verify.")
    app.run(host="0.0.0.0", port=5000, debug=False)
