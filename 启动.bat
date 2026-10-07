@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
cd /d "%~dp0"
title TBe+ 新闻资讯自动点赞

set "PY="
if exist "%~dp0.venv\Scripts\python.exe" set "PY=%~dp0.venv\Scripts\python.exe"
if not defined PY (
  if exist "%~dp0..\.venv\Scripts\python.exe" set "PY=%~dp0..\.venv\Scripts\python.exe"
)
if not defined PY (
  where py >nul 2>nul && set "PY=py -3"
)
if not defined PY (
  where python >nul 2>nul && set "PY=python"
)

if not defined PY (
  echo.
  echo [错误] 没有找到 Python。
  echo 请先安装 Python 3.10 以上版本：https://www.python.org/downloads/
  echo 安装时记得勾选 "Add Python to PATH"。
  echo.
  pause
  exit /b 1
)

%PY% -c "import websockets" >nul 2>nul
if errorlevel 1 (
  echo 首次运行，正在安装依赖 websockets …
  %PY% -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo.
    echo [错误] 依赖安装失败，请检查网络后重试。
    pause
    exit /b 1
  )
)

%PY% app.py
if errorlevel 1 (
  echo.
  echo 程序异常退出。
  pause
)
endlocal
