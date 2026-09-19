@echo off
chcp 65001 >nul 2>&1
title DistLib-DB Stop

echo.
echo ================================================
echo   DistLib-DB  Stopping all services...
echo ================================================
echo.

REM ---- Kill processes by well-known ports ----
for %%P in (8000 8001 8002 8003 8004 5173) do (
    for /f "tokens=5" %%A in ('netstat -ano ^| findstr /r "%%P" ^| findstr LISTENING') do (
        echo   kill PID %%A  port %%P
        taskkill /F /PID %%A >nul 2>&1
    )
)

REM ---- Extra fallback: close windows by title ----
for %%T in ("Site A 8001" "Site B 8002" "Site C 8003" "Site D 8004" "MainSite 8000" "Frontend 5173") do (
    taskkill /F /FI "WINDOWTITLE eq %%~T" >nul 2>&1
)

timeout /t 2 /nobreak >nul
echo.
echo   All services stopped.
echo.
