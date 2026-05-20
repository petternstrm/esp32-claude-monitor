# Claude Usage Monitor

A real-time Claude API usage monitor that shows your **5-hour** and **weekly** token usage — percentage used, reset timer, and raw token count — fetched directly from Anthropic's servers using your Claude desktop app's OAuth token. No API key required.

Two display options:

| | macOS menu bar app | ESP32 CYD display |
|---|---|---|
| **Platform** | macOS | Windows (proxy) + any WiFi |
| **Hardware** | None | ESP32-2432S028R (~$10) |
| **Dependencies** | Python + 2 packages | Python + Node.js + Arduino |
| **Setup time** | ~2 minutes | ~30 minutes |

---

## How It Works

**macOS app** — a single Python process reads tokens from the Claude desktop app via macOS Keychain, calls the Anthropic API directly, and displays results in the menu bar.

**ESP32 setup** — a Windows proxy server decrypts the OAuth token (Windows DPAPI), serves data over HTTP on port 5000, and the ESP32 polls it over WiFi every 2 minutes.

Both options use the same approach: one minimal `claude-haiku` call every 5 minutes (~8 tokens) to read the `anthropic-ratelimit-unified-*` response headers, plus a scan of `~/.claude/projects/**/*.jsonl` for raw token counts.

---

## macOS Menu Bar App

### Prerequisites

- macOS with Claude desktop app installed and logged in
- Python 3.9+
- `pip install rumps cryptography`

### Setup

```bash
git clone https://github.com/yourname/esp32-claude-monitor.git
cd esp32-claude-monitor
pip install -r requirements_mac.txt
python claude_monitor_mac.py
```

The app appears in your menu bar immediately. On first run it reads the OAuth token from `~/Library/Application Support/Claude/config.json`, decrypts it using your Keychain, and caches it to `~/.claude/proxy_oauth_token.json` for subsequent launches.

### Menu bar

```
◉ 72% | 4h 23m                   ← 5h usage + reset countdown (toggleable)
────────────────────────────────
5h:   72%  (124.5K)  →  resets in 4h 23m  (18:45)
7d:   45%  (2.1M)    →  resets in 2d 14h  (Fri 09:00)
────────────────────────────────
Updated: 14:23:05
────────────────────────────────
✓ Show timer in menu bar
Refresh now
────────────────────────────────
Quit
```

- The menu bar title updates every 30 seconds with a fresh countdown
- Data is re-fetched from the API every 5 minutes (background thread)
- **Show timer in menu bar** toggles the reset countdown on/off

### Auto-start at login (optional)

Create a launchd plist at `~/Library/LaunchAgents/com.claude.monitor.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>       <string>com.claude.monitor</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/python3</string>
    <string>/path/to/esp32-claude-monitor/claude_monitor_mac.py</string>
  </array>
  <key>RunAtLoad</key>   <true/>
  <key>KeepAlive</key>   <true/>
</dict>
</plist>
```

Replace `/path/to/` with the actual repo path, then load it:

```bash
launchctl load ~/Library/LaunchAgents/com.claude.monitor.plist
```

### Troubleshooting (macOS)

| Symptom | Fix |
|---|---|
| `⚠ Claude` in menu bar | Claude desktop app not running or not logged in |
| No Keychain prompt / `Error: No OAuth tokens` | Open Claude desktop app and sign in, then retry |
| Keychain access denied | Run `python claude_monitor_mac.py` from Terminal once to grant access manually |
| `ModuleNotFoundError: rumps` | `pip install rumps cryptography` |

---

## ESP32 CYD Display (Windows)

### How It Works

```
Anthropic API
     ↑  (minimal haiku call, ~8 tokens, every 5 min)
rate_limit_fetcher.js   ← decrypts OAuth token from Claude desktop app (Windows DPAPI)
     ↑
proxy_server.py         ← Flask server on port 5000, also scans JSONL for raw counts
     ↑  (HTTP, every 2 min)
ESP32 CYD               ← displays bars, percentages, reset times
```

### Hardware

| Part | Details |
|---|---|
| Board | ESP32-2432S028R ("Cheap Yellow Display" / CYD) |
| Display | 2.4" ILI9341 TFT, 240×320 |
| Touch | XPT2046 resistive |
| Interface | USB micro-B (for flashing), WiFi (2.4 GHz only) |

Search AliExpress/Amazon for **"ESP32 Cheap Yellow Display"** or **"ESP32-2432S028R"**.

### Prerequisites

- **Windows** (OAuth token decryption uses Windows DPAPI via PowerShell)
- **Claude desktop app** installed and logged in
- **Node.js** — https://nodejs.org (install to default path `C:\Program Files\nodejs\`)
- **Python 3.9+** with Flask: `pip install flask`
- **Arduino IDE 2** or **arduino-cli**, with:
  - ESP32 board core `esp32:esp32` (board manager URL: `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`)
  - Libraries: **TFT_eSPI** by Bodmer, **ArduinoJson** by Benoit Blanchon

### Setup

#### 1. Clone the repo

```bash
git clone https://github.com/yourname/esp32-claude-monitor.git
cd esp32-claude-monitor
```

#### 2. Start the proxy server

```bash
pip install flask
python proxy_server.py
```

Verify — open http://localhost:5000/usage in your browser. You should see JSON with `"api_synced": true`.

> The Claude desktop app must be running and logged in on first use.

#### Auto-start at Windows login (optional)

1. Edit `start_proxy.vbs` — fill in your Python path and the path to `proxy_server.py`
2. Find your Python path: `where python` in cmd
3. Copy `start_proxy.vbs` to `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\`

#### 3. Configure the ESP32 sketch

```bash
cp esp32_claude_monitor/config.h.example esp32_claude_monitor/config.h
```

Edit `esp32_claude_monitor/config.h`:

```cpp
#define WIFI_SSID   "Your_WiFi_Name"   // 2.4 GHz only
#define WIFI_PASS   "your_wifi_password"
#define SERVER_HOST "192.168.1.X"      // your PC's local IP (ipconfig | findstr IPv4)
```

> `config.h` is gitignored — your credentials will never be committed.

#### 4. Configure TFT_eSPI

In your Arduino libraries folder, edit `TFT_eSPI/User_Setup.h`:

```cpp
#define ILI9341_2_DRIVER       // NOT ILI9341_DRIVER
#define TFT_MOSI  13
#define TFT_SCLK  14
#define TFT_MISO  12
#define TFT_CS    15
#define TFT_DC     2
#define TFT_BL    27           // backlight — NOT pin 21
#define TFT_INVERSION_ON
#define ESP32_DMA
#define SPI_FREQUENCY  65000000
#define TOUCH_CS  33
```

#### 5. Flash the ESP32

**Arduino IDE:** Open `esp32_claude_monitor/esp32_claude_monitor.ino`, select board **ESP32 Dev Module**, select the correct COM port, click Upload.

**arduino-cli:**
```bash
arduino-cli compile --fqbn esp32:esp32:esp32 esp32_claude_monitor
arduino-cli upload  --fqbn esp32:esp32:esp32 --port COM7 esp32_claude_monitor
```

### Display Layout

```
┌─────────────────────────┐
│     CLAUDE MONITOR      │
├─────────────────────────┤
│ 5H WINDOW               │
│ Resets: 14:30  (2h 15m) │
│ [████████░░░░░░░░] 48%  │
│ 183.9K tokens           │
├─────────────────────────┤
│ WEEKLY                  │
│ Resets: Tue 20:00 (4d)  │
│ [███░░░░░░░░░░░░░] 17%  │
│ 2.16M tokens            │
├─────────────────────────┤
│ Updated: 14:22:05       │
│      [ REFRESH ]        │
└─────────────────────────┘
```

- Bar turns **yellow** at 70%, **red** at 90%
- Tap **REFRESH** to fetch immediately

### Troubleshooting (ESP32)

| Symptom | Fix |
|---|---|
| ESP32 shows "Error: No connection" | Check `SERVER_HOST` in `config.h` matches your PC's current IP (`ipconfig`) |
| `api_synced: false` in `/usage` | Claude desktop app not running, or Node.js not found at `C:\Program Files\nodejs\node.exe` |
| COM port not appearing | USB cable is charge-only — use a data cable |
| Multiple proxy instances | Kill all `python` processes on port 5000 and restart |
| Token refresh errors | Restart Claude desktop app to refresh its session |

---

## Cost

Each rate limit fetch makes one minimal API call (`claude-haiku`, `max_tokens: 1`, single `.` message) costing roughly **8 input tokens**. With the default 5-minute cache, that's ~2304 tokens/day — negligible.

---

## License

MIT
