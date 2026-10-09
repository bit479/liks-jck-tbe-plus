@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title TBe+ 新闻资讯自动点赞

call "%~dp0_python.bat"
if not defined PY goto :no_python

echo 使用的 Python：%PY%
echo.
rem --- GUI: prefer pythonw (same folder as python.exe), no console window ---
set "PYW=%PY:python.exe=pythonw.exe%"
if exist "%PYW%" (
  start "" "%PYW%" -X utf8 "%~dp0app.py"
  goto :run_ok
)
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw -X utf8 "%~dp0app.py"
  goto :run_ok
)
"%PY%" app.py

if errorlevel 1 (
  echo.
  echo   程序异常退出，请把上面的信息反馈一下。
  pause
  exit /b 1
)
:run_ok
endlocal
exit /b 0

:no_python
echo.
echo   ================================================================
echo    [错误] 没能准备好 Python
echo   ================================================================
echo.
echo    正常情况下，本程序会自动用仓库里自带的官方安装包
echo    （installer\python-*-amd64.exe）静默装好 Python，
echo    不需要你手动操作。现在这一步没有成功，可能是：
echo.
echo      1) 仓库里的 installer 目录被删掉了，或者安装包不完整；
echo      2) 安装被公司的系统策略 / 杀毒软件拦截了。
echo.
echo    你可以手动装一次，装完再双击本文件即可：
echo        https://www.python.org/downloads/
echo    安装时请务必勾选安装界面最下面的 [x] Add python.exe to PATH。
echo.
echo    注意：微软商店版（Microsoft Store）的 Python 跑不了脚本，
echo    一定要用上面网址的官方安装包。
echo.
choice /c YN /n /m "  现在打开 Python 官方下载页吗？(Y/N) "
if errorlevel 2 goto :skip_download
start "" "https://www.python.org/downloads/"
:skip_download
echo.
pause
exit /b 1
