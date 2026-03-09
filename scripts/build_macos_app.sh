#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Skyscanner 多市场比价"
APP_DIR="${PROJECT_ROOT}/${APP_NAME}.app"
CONTENTS_DIR="${APP_DIR}/Contents"
MACOS_DIR="${CONTENTS_DIR}/MacOS"
RESOURCES_DIR="${CONTENTS_DIR}/Resources"
PYTHON_BIN="$(command -v python3 || true)"
LOG_DIR="${PROJECT_ROOT}/logs"
LAUNCHER_PATH="${MACOS_DIR}/launch_gui"

if [[ -z "${PYTHON_BIN}" ]]; then
  echo "python3 not found"
  exit 1
fi

rm -rf "${APP_DIR}"
mkdir -p "${MACOS_DIR}" "${RESOURCES_DIR}" "${LOG_DIR}"

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
set -u

PROJECT_ROOT="__PROJECT_ROOT__"
PYTHON_BIN="__PYTHON_BIN__"
GUI_SCRIPT="${PROJECT_ROOT}/gui.py"
LOG_FILE="__LOG_DIR__/gui_app.log"

show_alert() {
  /usr/bin/osascript -e "display alert \"Skyscanner 多市场比价\" message \"$1\" as critical"
}

if [[ ! -x "${PYTHON_BIN}" ]]; then
  show_alert "没有找到 python3：${PYTHON_BIN}"
  exit 1
fi

if [[ ! -f "${GUI_SCRIPT}" ]]; then
  show_alert "没有找到 gui.py：${GUI_SCRIPT}"
  exit 1
fi

mkdir -p "__LOG_DIR__"
cd "${PROJECT_ROOT}" || exit 1

if [[ "${SKYSCANNER_GUI_SMOKE_TEST:-0}" == "1" ]]; then
  "${PYTHON_BIN}" - <<'PY'
import tkinter
import gui
print("smoke-ok")
PY
  exit $?
fi

"${PYTHON_BIN}" "${GUI_SCRIPT}" >> "${LOG_FILE}" 2>&1
status=$?

if [[ $status -ne 0 ]]; then
  show_alert "启动失败，日志见 __LOG_DIR__/gui_app.log"
fi

exit $status
SH

LAUNCHER_PATH_ENV="${LAUNCHER_PATH}" \
PROJECT_ROOT_ENV="${PROJECT_ROOT}" \
PYTHON_BIN_ENV="${PYTHON_BIN}" \
LOG_DIR_ENV="${LOG_DIR}" \
python3 - <<'PY'
import os
from pathlib import Path

path = Path(os.environ["LAUNCHER_PATH_ENV"])
text = path.read_text()
text = text.replace("__PROJECT_ROOT__", os.environ["PROJECT_ROOT_ENV"])
text = text.replace("__PYTHON_BIN__", os.environ["PYTHON_BIN_ENV"])
text = text.replace("__LOG_DIR__", os.environ["LOG_DIR_ENV"])
path.write_text(text)
PY

chmod +x "${LAUNCHER_PATH}"

/usr/bin/touch "${APP_DIR}"
echo "Built app: ${APP_DIR}"
