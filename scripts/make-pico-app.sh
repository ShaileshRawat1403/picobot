#!/usr/bin/env bash
# Build the double-clickable Pico.app (macOS). No Terminal window appears.
#
#   ./scripts/make-pico-app.sh [output-path]   # default: Pico.app at repo root
#
# The app bundles the same launcher as scripts/pico-web-launcher, so double-
# clicking Pico.app starts the web workbench (detached) and opens the browser.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${1:-$REPO_ROOT/Pico.app}"
APP_DIR="$OUT/Contents"

mkdir -p "$APP_DIR/MacOS" "$APP_DIR/Resources"
cp "$REPO_ROOT/scripts/pico-web-launcher" "$APP_DIR/MacOS/pico-web-launcher"
chmod +x "$APP_DIR/MacOS/pico-web-launcher"

cat >"$APP_DIR/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleExecutable</key>
  <string>pico-web-launcher</string>
  <key>CFBundleIdentifier</key>
  <string>com.picobot.web</string>
  <key>CFBundleName</key>
  <string>Pico</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSUIElement</key>
  <true/>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

echo "Built $OUT"
echo "  Double-click Pico.app to start Pico and open the browser."
echo "  Optional login-start (keeps Pico running even when closed):"
echo "    $REPO_ROOT/scripts/pico-web-launcher --install-agent"
echo "  Remove login-start: $REPO_ROOT/scripts/pico-web-launcher --uninstall-agent"
echo "  Check agent state:  $REPO_ROOT/scripts/pico-web-launcher --agent-status"
