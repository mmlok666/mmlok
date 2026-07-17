@echo off
echo Starting KTV...
py --version >nul 2>&1 || (echo Python not found & pause & exit /b)
where ffmpeg >nul 2>&1 || (echo ffmpeg not found & pause & exit /b)
cd /d "%~dp0"
start http://localhost:3456/tv
python ktv-server.py
pause
