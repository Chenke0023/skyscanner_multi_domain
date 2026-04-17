#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Skyscanner 多市场比价"
APP_DIR="${PROJECT_ROOT}/${APP_NAME}.app"
CONTENTS_DIR="${APP_DIR}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
LAUNCHER_PATH="${MACOS_DIR}/launch_gui"

"${PROJECT_ROOT}/scripts/build_web_ui.sh"

rm -rf "${APP_DIR}"
mkdir -p "${MACOS_DIR}" "${RESOURCES_DIR}"

cat > "${CONTENTS_DIR}/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDevelopmentRegion</key>
  <string>zh_CN</string>
  <key>CFBundleDisplayName</key>
  <string>${APP_NAME}</string>
  <key>CFBundleExecutable</key>
  <string>launch_gui</string>
  <key>CFBundleIdentifier</key>
  <string>local.a16.skyscanner-gui</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>${APP_NAME}</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>1.0</string>
  <key>CFBundleVersion</key>
  <string>1</string>
  <key>LSMinimumSystemVersion</key>
  <string>12.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
</dict>
</plist>
PLIST

cat > "${LAUNCHER_PATH}" <<'SH'
#!/bin/zsh
set -euo pipefail

APP_MACOS_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${APP_MACOS_DIR}/../../.." && pwd)"

exec /bin/zsh "${PROJECT_ROOT}/scripts/launch_gui.sh"
SH

chmod +x "${LAUNCHER_PATH}"

/usr/bin/touch "${APP_DIR}"
echo "Built app: ${APP_DIR}"
