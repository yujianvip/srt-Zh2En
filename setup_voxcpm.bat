@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv-tts\Scripts\python.exe" python -m venv .venv-tts
if errorlevel 1 exit /b 1
".venv-tts\Scripts\python.exe" -m pip install -r requirements-tts.txt
if errorlevel 1 exit /b 1
".venv-tts\Scripts\python.exe" -m pip install --no-deps torch==2.9.1+cu128 torchaudio==2.9.1+cu128 --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 exit /b 1
echo VoxCPM2 语音环境已安装。首次生成配音时还需下载模型权重。
pause
