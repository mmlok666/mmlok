@echo off
chcp 65001 >nul
title 🎤 家庭KTV点歌系统
echo ========================================
echo       🎤 家庭KTV点歌系统 v1.0
echo       纯Python - 零依赖 - 双击启动
echo ========================================
echo.
python --version >nul 2>&1 || (echo 未找到Python & pause & exit /b)
where ffmpeg >nul 2>&1 || (echo 未找到ffmpeg & pause & exit /b)
if not exist "H:\KTVSong\song_V3.2.3.db" echo 数据库不存在
echo.
echo 启动服务...
cd /d "%~dp0"
start http://localhost:3456/tv
python ktv-server.py
pause