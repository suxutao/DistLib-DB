@echo off
chcp 65001 >nul 2>&1
title DistLib-DB Start

setlocal
set "ROOT=%~dp0"
cd /d "%ROOT%"

echo.
echo ================================================
echo   DistLib-DB  One-Click Start
echo ================================================
echo.

where conda >nul 2>&1
if errorlevel 1 (
    echo [ERROR] conda not found
    pause
    exit /b 1
)

echo [1/4] Site A  Literature   8001 ...
start "Site A 8001" /d "%ROOT%" cmd /k "conda run -n libDB python -m backend.site.main --port 8001 --category 文学"

echo [2/4] Site B  Technology   8002 ...
start "Site B 8002" /d "%ROOT%" cmd /k "conda run -n libDB python -m backend.site.main --port 8002 --category 科技"

echo [3/4] Site C  Education   8003 ...
start "Site C 8003" /d "%ROOT%" cmd /k "conda run -n libDB python -m backend.site.main --port 8003 --category 教育"

echo [4/4] Site D  History     8004 ...
start "Site D 8004" /d "%ROOT%" cmd /k "conda run -n libDB python -m backend.site.main --port 8004 --category 历史"

timeout /t 3 /nobreak >nul

echo       Main Site           8000 ...
start "MainSite 8000" /d "%ROOT%" cmd /k "conda run -n libDB python -m backend.main_site.main"

where npm >nul 2>&1
if not errorlevel 1 goto :do_frontend
goto :done_frontend

:do_frontend
timeout /t 2 /nobreak >nul
echo       Frontend            5173 ...
start "Frontend 5173" /d "%ROOT%frontend" cmd /k "npm run dev"

:done_frontend
echo.
echo ================================================
echo   All launch commands sent.
echo.
echo   Frontend   http://localhost:5173
echo   Main       http://localhost:8000
echo   Site A     http://localhost:8001
echo   Site B     http://localhost:8002
echo   Site C     http://localhost:8003
echo   Site D     http://localhost:8004
echo ================================================
echo.
echo   This window will close in 5s.
echo   Run stop.bat to shut everything down.
timeout /t 5 /nobreak >nul
