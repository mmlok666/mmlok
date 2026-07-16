@echo off
python --version >nul 2>&1 || (echo Python not found & pause & exit /b)
where ffmpeg >nul 2>&1 || (echo ffmpeg not found & pause & exit /b)
if not exist "H:\KTVSong\song_V3.2.3.db" echo Warning: database not found
echo Starting KTV server...
cd /d "%~dp0"
start http://localhost:3456/tv
python ktv-server.py
pause
