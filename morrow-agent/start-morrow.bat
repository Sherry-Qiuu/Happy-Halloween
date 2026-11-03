@echo off
rem 一键启动 Morrow（本地预览）
rem 双击即可：启动服务 + 打开浏览器。关闭窗口不影响服务，停止服务请用 stop-morrow.bat。

setlocal
cd /d "%~dp0"

set PORT=8000
set MORROW_DEV_BYPASS_AUTH=1
set MAIL_TRANSPORT=console
set MORROW_REQUIRE_EMAIL=0
if "%MORROW_DATA_DIR%"=="" set MORROW_DATA_DIR=%~dp0..\server information

where python >nul 2>nul
if errorlevel 1 (
  echo [Morrow] 没找到 python，请先安装 Python 3.10+ 或把 python 加进 PATH。
  pause
  exit /b 1
)

echo [Morrow] 启动服务 http://127.0.0.1:%PORT% ...
start "" /min python server.py

rem 等服务起来再打开浏览器
for /l %%i in (1,1,20) do (
  timeout /t 1 /nobreak >nul
  curl -sf "http://127.0.0.1:%PORT%/api/health" >nul 2>nul && goto :open
)
echo [Morrow] 服务没有在 20 秒内起来，请检查端口 %PORT% 是否被占用。
pause
exit /b 1

:open
start "" "http://127.0.0.1:%PORT%/"
echo [Morrow] 已打开浏览器。要停止服务，运行 stop-morrow.bat。
timeout /t 3 /nobreak >nul
endlocal
