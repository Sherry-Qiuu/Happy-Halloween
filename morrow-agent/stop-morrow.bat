@echo off
rem 停止本地 Morrow 预览服务（只结束监听 8000 端口的 python 进程）

setlocal
set PORT=8000

set FOUND=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:"LISTENING" ^| findstr /c:":%PORT% "') do (
  echo [Morrow] 停止进程 %%p
  taskkill /pid %%p /f >nul 2>nul
  set FOUND=1
)

if "%FOUND%"=="0" echo [Morrow] 端口 %PORT% 上没有在跑的服务。
endlocal
