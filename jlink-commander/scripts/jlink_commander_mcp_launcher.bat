@echo off
setlocal EnableDelayedExpansion
REM ---------------------------------------------------------------------------
REM Jlink-Commander MCP launcher (Windows)
REM Auto-locates a Python interpreter (prefer WorkBuddy bundled, then system PATH),
REM then starts the MCP server. This way the skill can be shared without hardcoding
REM any username / Python version path.
REM Usage: set the connector "command" to the absolute path of THIS file.
REM The MCP script path is derived from %~dp0, so only this file's location matters.
REM ---------------------------------------------------------------------------
set "WB=%USERPROFILE%\.workbuddy\binaries\python\versions"
set "PY="
if exist "%WB%" (
  for /f "delims=" %%d in ('dir /b /ad /o-n "%WB%" 2^>nul') do (
    if not defined PY if exist "%WB%\%%d\python.exe" set "PY=%WB%\%%d\python.exe"
  )
)
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY ( where py      >nul 2>nul && set "PY=py" )
if not defined PY (
  echo [Jlink-Commander] ERROR: no Python interpreter found. Install Python 3.10+ or use WorkBuddy. 1>&2
  exit /b 1
)
"%PY%" "%~dp0jlink_commander_mcp.py"
