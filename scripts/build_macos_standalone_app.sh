#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Skyscanner 多市场比价"
BUNDLE_ID="local.a16.skyscanner-gui"

cd "${PROJECT_ROOT}"

if ! python3 -c "import PyInstaller" >/dev/null 2>&1; then
  echo "PyInstaller 未安装，请先执行: python3 -m pip install pyinstaller"
  exit 1
fi

"${PROJECT_ROOT}/scripts/build_web_ui.sh"

python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "${APP_NAME}" \
  --osx-bundle-identifier "${BUNDLE_ID}" \
  --collect-data "apify_fingerprint_datapoints" \
  --collect-submodules "webview" \
  --add-data "data:data" \
  --add-data "webui/dist:webui/dist" \
  desktop_webview.py

APP_PATH="${PROJECT_ROOT}/dist/${APP_NAME}.app/Contents/MacOS/${APP_NAME}"
if [[ -x "${APP_PATH}" ]]; then
  SKYSCANNER_GUI_SMOKE_TEST=1 "${APP_PATH}" >/dev/null
fi

echo "Built standalone app: ${PROJECT_ROOT}/dist/${APP_NAME}.app"
