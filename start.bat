@echo off
cd /d "%~dp0"
echo ========================================
echo   铁路出行路径规划  Railway Route Planner
echo ========================================
echo.
echo Starting server on http://127.0.0.1:8000
echo Press Ctrl+C to stop
echo.
python src/main.py --gui --port 8000
pause
