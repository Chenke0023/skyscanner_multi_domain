#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"
WEBVIEW_SCRIPT="${PROJECT_ROOT}/desktop_webview.py"
LOG_DIR="${PROJECT_ROOT}/logs"
LOG_FILE="${LOG_DIR}/gui_app.log"

show_alert() {
  /usr/bin/osascript -e "display alert \"Skyscanner 多市场比价\" message \"$1\" as critical"
}

if [[ -z "${PYTHON_BIN}" || ! -x "${PYTHON_BIN}" ]]; then
  show_alert "没有找到可用的 python3"
  exit 1
fi

if [[ ! -f "${WEBVIEW_SCRIPT}" ]]; then
  show_alert "没有找到 desktop_webview.py"
  exit 1
fi

mkdir -p "${LOG_DIR}"
cd "${PROJECT_ROOT}" || exit 1

if [[ "${SKYSCANNER_GUI_SMOKE_TEST:-0}" == "1" ]]; then
  "${PYTHON_BIN}" "${WEBVIEW_SCRIPT}" >/dev/null
  exit $?
fi

exec "${PYTHON_BIN}" "${WEBVIEW_SCRIPT}" >> "${LOG_FILE}" 2>&1
