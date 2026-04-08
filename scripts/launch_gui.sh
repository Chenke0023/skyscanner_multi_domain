#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python3 || true)}"
GUI_SCRIPT="${PROJECT_ROOT}/gui.py"
LOG_DIR="${PROJECT_ROOT}/logs"
LOG_FILE="${LOG_DIR}/gui_app.log"

show_alert() {
  /usr/bin/osascript -e "display alert \"Skyscanner 多市场比价\" message \"$1\" as critical"
}

if [[ -z "${PYTHON_BIN}" || ! -x "${PYTHON_BIN}" ]]; then
  show_alert "没有找到可用的 python3"
  exit 1
fi

if [[ ! -f "${GUI_SCRIPT}" ]]; then
  show_alert "没有找到 gui.py：${GUI_SCRIPT}"
  exit 1
fi

mkdir -p "${LOG_DIR}"
cd "${PROJECT_ROOT}" || exit 1

if [[ "${SKYSCANNER_GUI_SMOKE_TEST:-0}" == "1" ]]; then
  "${PYTHON_BIN}" - <<'PY'
import tkinter
import gui
print("smoke-ok")
PY
  exit $?
fi

exec "${PYTHON_BIN}" "${GUI_SCRIPT}" >> "${LOG_FILE}" 2>&1
