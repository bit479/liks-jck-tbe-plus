@echo off
rem ---------------------------------------------------------------------------
rem 找一个真正能用的 Python，结果放到 PY 变量里（供调用方使用）。
rem
rem 重点：Windows 上 "where python" 经常会命中微软商店的占位符
rem （路径里带 WindowsApps），它根本没装 Python，跑什么都会失败。
rem 这里会把它过滤掉，并且真的执行一次 import sys 来验证。
rem ---------------------------------------------------------------------------
setlocal enabledelayedexpansion
set "FOUND="

rem 1) 项目自带的虚拟环境
call :probe "%~dp0.venv\Scripts\python.exe"
rem 2) 相邻目录的虚拟环境
call :probe "%~dp0..\.venv\Scripts\python.exe"

rem 3) py 启动器（python.org 的安装包会装）
if not defined FOUND (
  for /f "delims=" %%P in ('py -3 -c "import sys;print(sys.executable)" 2^>nul') do (
    call :probe "%%P"
  )
)

rem 4) PATH 里的 python（自动跳过微软商店占位符）
if not defined FOUND (
  for /f "delims=" %%P in ('where python 2^>nul') do call :probe "%%P"
)

rem 5) 注册表里登记过的 Python
if not defined FOUND call :probe_reg "HKCU\Software\Python\PythonCore"
if not defined FOUND call :probe_reg "HKLM\Software\Python\PythonCore"
if not defined FOUND call :probe_reg "HKLM\Software\WOW6432Node\Python\PythonCore"

rem 6) 常见安装目录
if not defined FOUND (
  for %%D in ("%LOCALAPPDATA%\Programs\Python" "%ProgramFiles%" "%ProgramFiles(x86)%" "C:\") do (
    for /d %%V in ("%%~D\Python3*") do call :probe "%%~V\python.exe"
  )
)

endlocal & set "PY=%FOUND%"
exit /b 0

:probe
rem 过滤微软商店占位符，并确认这个解释器真的能跑
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
