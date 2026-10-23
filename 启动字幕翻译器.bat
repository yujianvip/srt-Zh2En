@echo off
cd /d "%~dp0"
if not exist "%~dp0字幕翻译器-v1.0.exe" (
    echo 尚未构建程序，请先双击 build.bat。
    pause
    exit /b 1
)
start "" "%~dp0字幕翻译器-v1.0.exe"
