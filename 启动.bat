@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
title TBe+ 新闻资讯自动点赞

call "%~dp0_python.bat"
if not defined PY goto :no_python

echo 使用的 Python：%PY%
echo.
"%PY%" app.py

if errorlevel 1 (
  echo.
  echo   程序异常退出，请把上面的信息反馈一下。
  pause
  exit /b 1
)
endlocal
exit /b 0

:no_python
echo.
echo   ================================================================
echo    [错误] 这台电脑上没有找到可用的 Python
echo   ================================================================
echo.
echo    本程序需要 Python 3.10 或更高版本，但不需要安装任何第三方包
echo    （不需要 pip、不需要联网装依赖）。
echo.
echo    请到下面地址下载安装：
echo        https://www.python.org/downloads/
echo.
echo    安装时请务必勾选安装界面最下面的：
echo        [x] Add python.exe to PATH
echo.
echo    装完之后重新双击本文件即可。
echo.
echo    注意：如果你以前"装过"Python 却还是看到这个提示，多半是装成了
echo    微软商店版（Microsoft Store），那个版本不能直接跑脚本，
echo    请从上面网址下载官方安装包重新安装一次。
echo.
choice /c YN /n /m "  现在打开 Python 官方下载页吗？(Y/N) "
if errorlevel 2 goto :skip_download
start "" "https://www.python.org/downloads/"
:skip_download
echo.
pause
exit /b 1
