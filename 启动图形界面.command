#!/bin/zsh
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "${SCRIPT_DIR}" || exit 1
exec /bin/zsh "${SCRIPT_DIR}/scripts/launch_gui.sh"
