#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
PYTHON="${PYTHON:-$ROOT/venv/bin/python}"
"$PYTHON" -m PyInstaller --noconfirm ClaudeUsage.spec
STAGING="$(mktemp -d)"
trap 'rm -rf "$STAGING"' EXIT
ditto "dist/Claude Usage.app" "$STAGING/Claude Usage.app"
ditto "INSTALL.txt" "$STAGING/INSTALL.txt"
ln -s /Applications "$STAGING/Applications"
hdiutil create -volname 'Claude Usage' -srcfolder "$STAGING" -ov -format UDZO 'dist/Claude-Usage-arm64.dmg'
shasum -a 256 'dist/Claude-Usage-arm64.dmg'
