# Claude Usage Monitor for macOS

A small Apple Silicon menu-bar app showing how much of your Claude plan's **five-hour** and **weekly** limits you have used, and when they reset. Usage is shared across Claude Desktop, claude.ai, and Claude Code when they use the same subscription account. API-key-billed usage is separate. The percentages describe plan usage, not raw token counts.

## Install

1. Install Claude Desktop and sign in to your subscription account.
2. [Download the latest Apple Silicon DMG](https://github.com/petternstrm/esp32-claude-monitor/releases/latest), open it, and drag **Claude Usage.app** into **Applications**.
3. Open **Claude Usage** from Applications, then click its menu-bar percentage to see both limits. **Launch at login** is available in the menu.

The internal release is ad-hoc signed but not notarized. If macOS blocks the downloaded app, try opening it once, then go to **System Settings → Privacy & Security → Open Anyway** and confirm. This one-time step does not require Terminal. Installation instructions are also inside the DMG.

For exact server-provided percentages and reset times, allow macOS Keychain access to **Claude Safe Storage** when asked. If access or the server request is unavailable, the app uses fresh local usage data from Claude Desktop and marks any reset estimate with `~` or `estimated`. It does not store a separate copy of your OAuth credentials. A manual **Refresh now** also attempts a server request and may trigger a Keychain prompt.

## How it refreshes

- Checks Claude Desktop's local usage history every minute; samples older than 30 minutes are not displayed.
- Requests server-provided usage and reset times about every five minutes by sending a minimal Claude message. This uses a small amount of your plan allowance.
- Updates the menu-bar countdown every 30 seconds without another server request.
- **Refresh now** checks both sources immediately.

Local usage is read from `~/Library/Application Support/Claude/plan-usage-history.json`. That file belongs to Claude Desktop and its format may change. Local percentages reflect the last time Desktop updated the file, so activity in Claude Code or the web may appear there with a delay.

To uninstall, quit the app, turn off **Launch at login** if enabled, and move **Claude Usage.app** to Trash.

## Build from source

Build on an Apple Silicon Mac with Python 3 and macOS command-line tools:

```bash
python3 -m venv venv
venv/bin/python -m pip install -r requirements_mac.txt 'pyinstaller>=6.15,<7'
bash build_mac.sh
```

The build produces `dist/Claude Usage.app` and `dist/Claude-Usage-arm64.dmg`. Run the parser tests with `venv/bin/python -m unittest -v test_claude_usage`. The published v1.0.0 build was produced on macOS 26; compatibility with older macOS versions has not been validated.
