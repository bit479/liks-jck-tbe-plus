@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title TBe+ 新闻资讯自动点赞 - 命令行

call "%~dp0_python.bat"

if not defined PY (
  echo.
  echo   [错误] 没有找到可用的 Python。
  echo   请先到 https://www.python.org/downloads/ 安装 Python 3.10 以上版本，
  echo   安装时勾选 "Add python.exe to PATH"。
  echo.
  pause
  exit /b 1
)

echo 使用的 Python：%PY%
echo.
echo   可用命令示例：
echo     cli.py --check          检查客户端是否已接管
echo     cli.py --attach         重启并接管客户端
echo     cli.py --list           只列出板块和篇数
echo     cli.py --dry-run        试运行（只看不点）
echo     cli.py                  正式点赞三个板块
echo.
cmd /k
endlocal
