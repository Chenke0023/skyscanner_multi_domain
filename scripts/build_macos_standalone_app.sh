#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP_NAME="Skyscanner 多市场比价"
BUNDLE_ID="local.a16.skyscanner-gui"
ICON_PATH="${PROJECT_ROOT}/data/app_icon.icns"
DIST_DIR="${PROJECT_ROOT}/dist"
APP_BUNDLE="${DIST_DIR}/${APP_NAME}.app"
VERSION_FILE="${PROJECT_ROOT}/data/version.txt"

# ── Resolve version ──────────────────────────────────────────────
if [[ -f "${VERSION_FILE}" ]]; then
  VERSION="$(head -n1 "${VERSION_FILE}" | tr -d '[:space:]')"
else
  VERSION="$(date +%Y.%m.%d)"
fi
echo "Building ${APP_NAME} v${VERSION}"

# ── Record source root for runtime legacy profile lookup ─────────
python3 -c "
import json
from pathlib import Path
manifest = {'source_root': r'${PROJECT_ROOT}'}
Path('${PROJECT_ROOT}/data/build_manifest.json').write_text(
    json.dumps(manifest, ensure_ascii=False), encoding='utf-8'
)
print(f'Build manifest written: source_root={manifest[\"source_root\"]}')
"

# ── Prerequisites ────────────────────────────────────────────────
if ! python3 -c "import PyInstaller" >/dev/null 2>&1; then
  echo "PyInstaller 未安装，执行: python3 -m pip install pyinstaller"
  exit 1
fi

if [[ ! -f "${ICON_PATH}" ]]; then
  echo "图标文件缺失: ${ICON_PATH}"
  echo "请先运行: python3 scripts/generate_icon.py"
  exit 1
fi

# ── Build frontend assets ────────────────────────────────────────
"${PROJECT_ROOT}/scripts/build_web_ui.sh"

# ── Clean previous build ─────────────────────────────────────────
rm -rf "${DIST_DIR}/${APP_NAME}" \
       "${APP_BUNDLE}" \
       "${PROJECT_ROOT}/build/${APP_NAME}"

# ── PyInstaller build ────────────────────────────────────────────
cd "${PROJECT_ROOT}"

python3 -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "${APP_NAME}" \
  --icon "${ICON_PATH}" \
  --osx-bundle-identifier "${BUNDLE_ID}" \
  --add-data "data:data" \
  --add-data "webui/dist:webui/dist" \
  --hidden-import webview \
  --hidden-import webview.platforms.cocoa \
  --hidden-import aiohttp \
  --hidden-import curl_cffi \
  --hidden-import lxml \
  --hidden-import bs4 \
  --hidden-import scrapling \
  --hidden-import cli \
  --hidden-import desktop_logic \
  --hidden-import desktop_ui_service \
  --hidden-import skyscanner_neo \
  --hidden-import scan_orchestrator \
  --hidden-import transport_scrapling \
  --hidden-import transport_cdp \
  --hidden-import skyscanner_page_parser \
  --hidden-import skyscanner_regions \
  --hidden-import skyscanner_models \
  --hidden-import location_resolver \
  --hidden-import date_window \
  --hidden-import fx_rates \
  --hidden-import failure_replay \
  --hidden-import scan_history \
  --hidden-import captcha_solver \
  --hidden-import app_paths \
  --collect-data apify_fingerprint_datapoints \
  --exclude-module pytest \
  --exclude-module unittest \
  --exclude-module test_skyscanner_neo \
  --exclude-module test_cli \
  --exclude-module test_date_window \
  --exclude-module test_transport_cdp \
  --exclude-module test_transport_scrapling \
  --exclude-module test_desktop_ui_service \
  --exclude-module test_gui_features \
  --exclude-module test_gui_startup \
  --exclude-module test_location_resolver \
  --exclude-module test_scan_history \
  --exclude-module test_failure_replay \
  --exclude-module test_app_paths \
  --exclude-module test_transport_opencli \
  --strip \
  desktop_webview.py

# ── Enhance Info.plist ───────────────────────────────────────────
PLIST="${APP_BUNDLE}/Contents/Info.plist"

/usr/libexec/PlistBuddy -c "Delete :CFBundleDisplayName" "${PLIST}" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleDisplayName string ${APP_NAME}" "${PLIST}"
/usr/libexec/PlistBuddy -c "Delete :CFBundleDevelopmentRegion" "${PLIST}" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleDevelopmentRegion string zh_CN" "${PLIST}"
/usr/libexec/PlistBuddy -c "Delete :CFBundleShortVersionString" "${PLIST}" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleShortVersionString string ${VERSION}" "${PLIST}"
/usr/libexec/PlistBuddy -c "Delete :CFBundleVersion" "${PLIST}" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :CFBundleVersion string ${VERSION}" "${PLIST}"
/usr/libexec/PlistBuddy -c "Delete :LSMinimumSystemVersion" "${PLIST}" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :LSMinimumSystemVersion string 12.0" "${PLIST}"
/usr/libexec/PlistBuddy -c "Delete :NSHighResolutionCapable" "${PLIST}" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :NSHighResolutionCapable bool true" "${PLIST}"
/usr/libexec/PlistBuddy -c "Delete :LSApplicationCategoryType" "${PLIST}" 2>/dev/null || true
/usr/libexec/PlistBuddy -c "Add :LSApplicationCategoryType string public.app-category.travel" "${PLIST}"

# ── Verify bundle ────────────────────────────────────────────────
APP_EXEC="${APP_BUNDLE}/Contents/MacOS/${APP_NAME}"
if [[ ! -x "${APP_EXEC}" ]]; then
  echo "ERROR: 可执行文件缺失: ${APP_EXEC}"
  exit 1
fi

echo ""
echo "────────────────────────────────────────────"
echo "  Built: ${APP_BUNDLE}"
echo "  Version: ${VERSION}"
echo "  Size: $(du -sh "${APP_BUNDLE}" | cut -f1)"
echo "────────────────────────────────────────────"
