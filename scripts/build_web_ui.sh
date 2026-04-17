#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
WEBUI_DIR="${PROJECT_ROOT}/webui"

if [[ ! -d "${WEBUI_DIR}" ]]; then
  echo "webui 目录不存在，跳过前端构建。"
  exit 0
fi

cd "${WEBUI_DIR}"

if [[ ! -d "${WEBUI_DIR}/node_modules" ]]; then
  npm install
fi

npm run build
