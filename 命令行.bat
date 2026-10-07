@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"
title TBe+ 新闻资讯自动点赞 - 命令行

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)
if not defined PY (
  echo [错误] 没有找到 Python。
  pause
  exit /b 1
)

echo.
echo   可用命令示例：
echo     %PY% cli.py --check          检查客户端是否已接管
echo     %PY% cli.py --attach         重启并接管客户端
echo     %PY% cli.py --dry-run        试运行（只看不点）
echo     %PY% cli.py                  正式点赞三个板块
echo.
cmd /k
endlocal
