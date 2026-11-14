#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# it6721-control MCP launcher (Linux / macOS)
# Auto-locates a Python interpreter (prefer WorkBuddy bundled, then system PATH),
# then starts the MCP server. No username / Python version path is hardcoded.
# Usage: chmod +x this file, then point the connector "command" at its absolute path.
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
if [ -z "$PY" ]; then echo "[it6721-control] ERROR: no Python interpreter found." >&2; exit 1; fi
exec "$PY" "$(dirname "$0")/it6721_mcp.py"
