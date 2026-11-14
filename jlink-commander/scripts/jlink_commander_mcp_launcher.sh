#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Jlink-Commander MCP 启动器（Linux / macOS）
# 自动定位 Python 解释器（优先 WorkBuddy 自带，其次系统 PATH），再启动 MCP 服务。
# 用法：chmod +x 本文件后，把本文件绝对路径填到连接器配置的 command 即可。
# ---------------------------------------------------------------------------
set -euo pipefail
WB="$HOME/.workbuddy/binaries/python/versions"
PY=""
if [ -d "$WB" ]; then
  latest=$(ls -1 "$WB" 2>/dev/null | sort -r | head -1)
  if [ -n "$latest" ]; then
    for cand in python3 python; do
      if [ -x "$WB/$latest/bin/$cand" ]; then PY="$WB/$latest/bin/$cand"; break; fi
    done
  fi
fi
if [ -z "$PY" ] && command -v python3 >/dev/null 2>&1; then PY=python3; fi
if [ -z "$PY" ] && command -v python  >/dev/null 2>&1; then PY=python; fi
if [ -z "$PY" ]; then echo "[Jlink-Commander] ERROR: no Python interpreter found." >&2; exit 1; fi
exec "$PY" "$(dirname "$0")/jlink_commander_mcp.py"
