#!/usr/bin/env bash
# 在qqvps-cd以ubuntu运行；发布器负责预检、切流健康与失败自动回滚。
set -euo pipefail
if [[ $# -ne 1 ]]; then
  echo "用法: $0 <git-ref或SHA>" >&2
  exit 1
fi
exec python3 "$(dirname -- "${BASH_SOURCE[0]}")/publish.py" "$1"
