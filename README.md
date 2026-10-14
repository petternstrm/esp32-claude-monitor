# Claude Usage Monitor

A Claude plan usage monitor for the **5-hour** and **weekly** limits. The macOS menu-bar app prefers server-provided percentages and reset times, with a read-only Claude Desktop usage-history fallback.

Two display options:

| | macOS menu bar app | ESP32 CYD display |
|---|---|---|
| **Platform** | macOS | Windows (proxy) + any WiFi |
| **Hardware** | None | ESP32-2432S028R (~$10) |
| **Dependencies** | None for end users | Python + Node.js + Arduino |
| **Setup time** | Drag into Applications | ~30 minutes |

---

## How It Works

**macOS app** — a bundled menu-bar app reads plan usage locally from Claude Desktop and optionally accesses the current Claude Desktop session via macOS Keychain for exact server-provided reset times. It does not store OAuth credentials or refresh tokens. The exact-data request sends a minimal Claude message about every five minutes and uses a small amount of your plan allowance.

**ESP32 setup** — a Windows proxy server decrypts the OAuth token (Windows DPAPI), serves data over HTTP on port 5000, and the ESP32 polls it over WiFi every 2 minutes.

The ESP32 proxy uses its own token and JSONL scanning implementation; its installation instructions are below.

---

## macOS Menu Bar App

### Install (Apple Silicon)

1. Install and sign in to Claude Desktop.
2. Download `Claude-Usage-arm64.dmg`, open it, and drag **Claude Usage.app** to **Applications**.
3. Open **Claude Usage** from Applications. Its percentage appears in the menu bar; click it for both limits and the reset times.
4. If macOS blocks the unnotarized download, try opening the app once, then open **System Settings → Privacy & Security → Open Anyway**. Confirm **Open**. This is a one-time graphical step; no Terminal is required.
5. If asked for access to **Claude Safe Storage** in Keychain, allow it to enable exact reset times. If you decline, the app uses Claude Desktop's local usage percentages, and labels any reset estimate.

Select **Launch at login** from the menu after installing to Applications. To uninstall, quit the app, turn off Launch at login if enabled, and move the app to Trash. The app does not copy credentials into its bundle or a local cache.

### Menu bar

```
◉ 72% | 4h 23m                   ← 5h plan usage + reset countdown
────────────────────────────────
5h: 72% · resets in 4h 23m (18:45)
7d: 45% · resets in 2d 14h (09:00)
────────────────────────────────
Server data · updated 14:23
────────────────────────────────
✓ Show timer in menu bar · Launch at login
Open Claude usage settings
Refresh now
────────────────────────────────
Quit
```

- The menu bar title updates every 30 seconds with a fresh countdown
- Local data is checked every minute; server data is requested at most once every 5 minutes
- **Show timer in menu bar** toggles the reset countdown on/off
- Local percentages are read from `~/Library/Application Support/Claude/plan-usage-history.json`; stale samples are not displayed. Local reset estimates are labelled `estimated` or prefixed `~`.

### Build a new macOS release (maintainers)

On an Apple Silicon Mac, create a Python environment, install `requirements_mac.txt` and PyInstaller, then run `bash build_mac.sh`. The output is `dist/Claude Usage.app` and `dist/Claude-Usage-arm64.dmg`. Test the DMG after downloading it on another Mac, as a locally built copy may not carry the downloaded-file quarantine flag. This build was produced on macOS 26; validate compatibility on older macOS versions separately. This release is ad-hoc signed, not notarized; the user must approve first launch in macOS settings.

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
