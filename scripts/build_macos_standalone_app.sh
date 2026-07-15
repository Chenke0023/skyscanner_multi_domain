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
RELEASE_ZIP="${DIST_DIR}/skyscanner-multi-domain-v${VERSION}-macos-arm64.zip"

# ── Record source root for bundled runtime paths ────────────────
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
mkdir -p "${DIST_DIR}"
find "${DIST_DIR}" -maxdepth 1 -type f -name "*.zip" -delete
rm -rf "${DIST_DIR}/${APP_NAME}" \
       "${DIST_DIR}/${APP_NAME}-Standalone" \
       "${APP_BUNDLE}" \
       "${PROJECT_ROOT}/build/${APP_NAME}" \
       "${PROJECT_ROOT}/build/${APP_NAME}-Standalone"

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
  --hidden-import cli \
  --hidden-import desktop_logic \
  --hidden-import desktop_ui_service \
  --exclude-module opencli \
  --exclude-module scrapling \
  --exclude-module patchright \
  --exclude-module playwright \
  --exclude-module babel \
  --exclude-module apify_fingerprint_datapoints \
  --exclude-module numpy \
  --exclude-module mypy \
  --exclude-module neo \
  --exclude-module captcha_solver \
  --exclude-module failure_replay \
  --exclude-module bs4 \
  --exclude-module lxml \
  --exclude-module fastapi \
  --exclude-module uvicorn \
  --exclude-module httpx \
  --exclude-module pydantic \
  --exclude-module openai \
  --exclude-module PIL \
  --exclude-module pytest \
  --exclude-module unittest \
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

# Info.plist is changed after PyInstaller signs the bundle, so renew the
# ad-hoc signature before smoke testing and archiving the distributable.
/usr/bin/codesign --force --deep --sign - "${APP_BUNDLE}"
/usr/bin/codesign --verify --deep --strict "${APP_BUNDLE}"

# ── Verify bundle ────────────────────────────────────────────────
APP_EXEC="${APP_BUNDLE}/Contents/MacOS/${APP_NAME}"
if [[ ! -x "${APP_EXEC}" ]]; then
  echo "ERROR: 可执行文件缺失: ${APP_EXEC}"
  exit 1
fi


# ── Reject retired or development-only modules ───────────────────
FORBIDDEN_PATTERN='(^|/)(opencli|scrapling|patchright|playwright|babel|apify_fingerprint_datapoints|numpy|mypy|neo|captcha_solver|failure_replay)([./]|$)'
FORBIDDEN_PATHS="$(find "${APP_BUNDLE}" -print | sed "s#${APP_BUNDLE}/##" | grep -Ei "${FORBIDDEN_PATTERN}" || true)"
if [[ -n "${FORBIDDEN_PATHS}" ]]; then
  echo "ERROR: 应用包包含已退役或禁止模块:"
  echo "${FORBIDDEN_PATHS}"
  exit 1
fi

print_largest_components() {
  echo "应用包最大组件:"
  du -ak "${APP_BUNDLE}" | sort -nr | head -30
}

APP_SIZE_LIMIT=$((150 * 1024 * 1024))
ZIP_SIZE_LIMIT=$((60 * 1024 * 1024))
APP_SIZE_BYTES=$(( $(du -sk "${APP_BUNDLE}" | awk '{print $1}') * 1024 ))
if (( APP_SIZE_BYTES > APP_SIZE_LIMIT )); then
  echo "ERROR: .app 大小 ${APP_SIZE_BYTES} bytes，超过 150MB 门禁 ${APP_SIZE_LIMIT} bytes"
  print_largest_components
  exit 1
fi

# ── Runtime smoke from a read-only cwd ───────────────────────────
SMOKE_TMP="$(mktemp -d)"
SMOKE_READONLY="${SMOKE_TMP}/readonly"
SMOKE_HOME="${SMOKE_TMP}/app-home"
mkdir -p "${SMOKE_READONLY}" "${SMOKE_HOME}"
chmod 555 "${SMOKE_READONLY}"
set +e
SMOKE_OUTPUT="$(
  cd "${SMOKE_READONLY}" && \
  SKYSCANNER_APP_HOME="${SMOKE_HOME}" \
  SKYSCANNER_GUI_SMOKE_TEST=1 \
  "${APP_EXEC}" 2>&1
)"
SMOKE_STATUS=$?
set -e
chmod 755 "${SMOKE_READONLY}"
if [[ ${SMOKE_STATUS} -ne 0 || "${SMOKE_OUTPUT}" != *"smoke-ok"* ]]; then
  echo "ERROR: 打包 app smoke 失败"
  echo "${SMOKE_OUTPUT}"
  rm -rf "${SMOKE_TMP}"
  exit 1
fi
if [[ ! -d "${SMOKE_HOME}/traces" ]]; then
  echo "ERROR: 打包 app smoke 未创建 runtime traces 目录"
  rm -rf "${SMOKE_TMP}"
  exit 1
fi
if [[ -d "${SMOKE_READONLY}/traces" ]]; then
  echo "ERROR: 打包 app smoke 在只读 cwd 下创建了 traces"
  rm -rf "${SMOKE_TMP}"
  exit 1
fi
rm -rf "${SMOKE_TMP}"
echo "Bundle runtime smoke passed"

# PyInstaller leaves a redundant onedir tree next to the .app on some runs.
rm -rf "${DIST_DIR}/${APP_NAME}"

# Keep exactly one distributable archive in dist/.
/usr/bin/ditto -c -k --keepParent "${APP_BUNDLE}" "${RELEASE_ZIP}"

ZIP_SIZE_BYTES="$(stat -f%z "${RELEASE_ZIP}")"
if (( ZIP_SIZE_BYTES > ZIP_SIZE_LIMIT )); then
  echo "ERROR: 发布 ZIP 大小 ${ZIP_SIZE_BYTES} bytes，超过 60MB 门禁 ${ZIP_SIZE_LIMIT} bytes"
  print_largest_components
  exit 1
fi

# Build intermediates are large and fully reproducible.
rm -rf "${PROJECT_ROOT}/build/${APP_NAME}" \
       "${PROJECT_ROOT}/build/${APP_NAME}-Standalone"

echo ""
echo "────────────────────────────────────────────"
echo "  Built: ${APP_BUNDLE}"
echo "  Zip: ${RELEASE_ZIP}"
echo "  Version: ${VERSION}"
echo "  Size: $(du -sh "${APP_BUNDLE}" | cut -f1)"
echo "  Zip size: $(du -sh "${RELEASE_ZIP}" | cut -f1)"
echo "────────────────────────────────────────────"
