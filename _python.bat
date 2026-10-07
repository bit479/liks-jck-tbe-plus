@echo off
rem ---------------------------------------------------------------------------
rem 找一个真正能用的 Python，结果放到 PY 变量里（供调用方使用）。
rem
rem 查找顺序：
rem   1. 项目自带的虚拟环境 .venv
rem   2. 相邻目录的 .venv
rem   3. py 启动器（python.org 的安装包会装）
rem   4. PATH 里的 python（会跳过微软商店占位符）
rem   5. 注册表里登记过的 Python
rem   6. 常见安装目录
rem   7. 都找不到就静默安装仓库里自带的安装包，然后再找一遍
rem
rem 重点：Windows 上 "where python" 经常命中微软商店的占位符
rem （路径里带 WindowsApps），它根本没装 Python，跑什么都会失败。
rem 这里会把它过滤掉，并且真的执行一次 import sys 来验证。
rem ---------------------------------------------------------------------------
setlocal enabledelayedexpansion
set "FOUND="

call :search_all

if not defined FOUND call :install_bundled
if not defined FOUND call :wait_after_install

endlocal & set "PY=%FOUND%"
exit /b 0


rem ===========================================================================
rem 查找
rem ===========================================================================
:search_all
call :probe "%~dp0.venv\Scripts\python.exe"
call :probe "%~dp0..\.venv\Scripts\python.exe"

if not defined FOUND (
  for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do (
    call :probe "%%P"
  )
)
if not defined FOUND (
  for /f "delims=" %%P in ('where python 2^>nul') do call :probe "%%P"
)
if not defined FOUND call :search_registry
if not defined FOUND call :search_dirs
goto :eof

:search_registry
if defined FOUND goto :eof
call :probe_reg "HKCU\Software\Python\PythonCore"
if not defined FOUND call :probe_reg "HKLM\Software\Python\PythonCore"
if not defined FOUND call :probe_reg "HKLM\Software\WOW6432Node\Python\PythonCore"
goto :eof

:search_dirs
if defined FOUND goto :eof
for %%D in ("%LOCALAPPDATA%\Programs\Python" "%ProgramFiles%" "%ProgramFiles(x86)%" "C:\") do (
  for /d %%V in ("%%~D\Python3*") do call :probe "%%~V\python.exe"
)
goto :eof


rem ===========================================================================
rem 自动安装（用仓库里自带的官方安装包）
rem ===========================================================================
:install_bundled
set "_setup="
for %%F in ("%~dp0installer\python-*-amd64.exe") do (
  if exist "%%~fF" set "_setup=%%~fF"
)
if not defined _setup goto :eof

echo.
echo   ================================================================
echo    没有找到 Python，正在自动安装（用仓库里自带的官方安装包）
echo   ================================================================
echo.
echo    安装包：%_setup%
echo    只装给当前用户，不需要管理员权限，大约需要 1-2 分钟。
echo    安装过程中请不要关闭这个窗口。
echo.

"%_setup%" /quiet InstallAllUsers=0 PrependPath=1 Include_launcher=1 ^
  Include_tcltk=1 Include_test=0 Include_doc=0 Shortcuts=0 AssociateFiles=0

echo    安装程序已返回，正在定位新装好的 Python …
goto :eof

:wait_after_install
echo.
echo    正在等待 Python 安装完成 …
for /l %%I in (1,1,90) do (
  if not defined FOUND (
    call :search_all
    if not defined FOUND ping -n 3 127.0.0.1 >nul 2>nul
  )
)
goto :eof


rem ===========================================================================
rem 校验单个候选
rem ===========================================================================
:probe
if defined FOUND goto :eof
set "_candidate=%~1"
if "%_candidate%"=="" goto :eof
echo "%_candidate%" | findstr /i /c:"WindowsApps" >nul && goto :eof
"%_candidate%" -c "import sys" >nul 2>nul || goto :eof
set "FOUND=%_candidate%"
goto :eof

:probe_reg
if defined FOUND goto :eof
for /f "tokens=2,*" %%A in ('reg query "%~1" /s /v ExecutablePath 2^>nul ^| findstr /i "ExecutablePath"') do (
  call :probe "%%B"
)
goto :eof
