# ESP32 Claude Usage Monitor

A real-time Claude API usage monitor running on an ESP32-2432S028R "Cheap Yellow Display" (CYD) with a 2.4" ILI9341 TFT screen.

Shows your **5-hour** and **weekly** token usage — percentage used, reset time, and raw token count — fetched directly from Anthropic's servers using your Claude desktop app's OAuth token. No API key required.

![Claude Monitor on CYD display](https://i.imgur.com/placeholder.png)

---

## How It Works

```
Anthropic API
     ↑  (minimal haiku call, ~8 tokens, every 5 min)
rate_limit_fetcher.js   ← decrypts OAuth token from Claude desktop app
     ↑
proxy_server.py         ← Flask server on port 5000, also scans JSONL for raw counts
     ↑  (HTTP, every 2 min)
ESP32 CYD               ← displays bars, percentages, reset times
```

- **`rate_limit_fetcher.js`** — Node.js script that decrypts the OAuth token stored by the Claude desktop app (Chrome os_crypt / Windows DPAPI), makes a minimal API call to `claude-haiku`, and reads the `anthropic-ratelimit-unified-5h-*` / `7d-*` response headers.
- **`proxy_server.py`** — Flask server that caches and serves the rate limit data over HTTP. Also scans `~/.claude/projects/**/*.jsonl` for raw token counts.
- **ESP32 sketch** — polls `/usage` over WiFi and renders the UI.

Rate limit percentages are **accurate** (straight from Anthropic's servers), not estimated from local logs.

---

## Hardware

| Part | Details |
|---|---|
| Board | ESP32-2432S028R ("Cheap Yellow Display" / CYD) |
| Display | 2.4" ILI9341 TFT, 240×320 |
| Touch | XPT2046 resistive |
| Interface | USB micro-B (for flashing), WiFi (2.4 GHz only) |

Search AliExpress/Amazon for **"ESP32 Cheap Yellow Display"** or **"ESP32-2432S028R"**.

---

## Prerequisites

### PC (proxy server)
- **Windows** (the OAuth token decryption uses Windows DPAPI via PowerShell)
- **Claude desktop app** installed and logged in
- **Node.js** — https://nodejs.org (install to default path `C:\Program Files\nodejs\`)
- **Python 3.9+** with Flask: `pip install flask`

### ESP32 (firmware)
- **Arduino IDE 2** or **arduino-cli**
- Arduino ESP32 core: `esp32:esp32` (add board manager URL `https://raw.githubusercontent.com/espressif/arduino-esp32/gh-pages/package_esp32_index.json`)
- Libraries (install via Library Manager):
  - **TFT_eSPI** by Bodmer
  - **ArduinoJson** by Benoit Blanchon

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/yourname/esp32-claude-monitor.git
cd esp32-claude-monitor
```

### 2. Start the proxy server

Install the Python dependency:

```bash
pip install flask
```

Run the server:

```bash
python proxy_server.py
```

Verify it works — open http://localhost:5000/usage in your browser. You should see JSON with `"api_synced": true`.

> **First run:** The script decrypts the OAuth token from your Claude desktop app automatically. The Claude desktop app must be running and logged in.

#### Auto-start at Windows login (optional)

1. Edit `start_proxy.vbs` — fill in your actual Python path and the path to `proxy_server.py`
2. Find your Python path: open cmd and run `where python`
3. Copy `start_proxy.vbs` to your Startup folder:
   ```
   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\
   ```
   From now on it launches silently at every login.

> **Important:** Make sure only one instance is running. Check Task Manager for multiple `python` processes on port 5000 if something seems wrong.

### 3. Configure the ESP32 sketch

Copy the example config and fill in your details:

```bash
cp esp32_claude_monitor/config.h.example esp32_claude_monitor/config.h
```

Edit `esp32_claude_monitor/config.h`:

```cpp
#define WIFI_SSID  "Your_WiFi_Name"    // 2.4 GHz network only
#define WIFI_PASS  "your_wifi_password"
#define SERVER_HOST "192.168.1.X"      // your PC's local IP (run: ipconfig | findstr IPv4)
```

> `config.h` is gitignored — your credentials will never be committed.

### 4. Configure TFT_eSPI

The CYD board needs a specific TFT_eSPI pin configuration. In your Arduino libraries folder, edit `TFT_eSPI/User_Setup.h` (or create a `User_Setup_Select.h`) with:

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

### 5. Flash the ESP32

**Arduino IDE:** Open `esp32_claude_monitor/esp32_claude_monitor.ino`, select board **ESP32 Dev Module**, select the correct COM port, click Upload.

**arduino-cli:**
```bash
arduino-cli compile --fqbn esp32:esp32:esp32 esp32_claude_monitor
arduino-cli upload  --fqbn esp32:esp32:esp32 --port COM7 esp32_claude_monitor
```

---

## Display Layout

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
- Auto-refreshes every 2 minutes

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| ESP32 shows "Error: No connection" | Check `SERVER_HOST` in `config.h` matches your PC's current IP (`ipconfig`) |
| `api_synced: false` in `/usage` | Claude desktop app not running, or Node.js not found at `C:\Program Files\nodejs\node.exe` |
| COM port not appearing | USB cable is charge-only — use a data cable |
| Multiple proxy instances | Kill all `python` processes and restart with `start_proxy.vbs` |
| Token refresh errors | Restart Claude desktop app to refresh its session |

---

## Cost

Each rate limit fetch makes one minimal API call (`claude-haiku`, `max_tokens: 1`, single `.` message) costing roughly **8 input tokens**. With the default 5-minute cache, that's ~2304 tokens/day — negligible.

---

## License

MIT
