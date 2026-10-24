@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"

set "PY_LAUNCHER="
py -3.11 -c "import sys; assert sys.version_info[:2] == (3, 11)" >nul 2>&1
if not errorlevel 1 set "PY_LAUNCHER=py -3.11"
if not defined PY_LAUNCHER (
    python -c "import sys; assert sys.version_info[:2] == (3, 11)" >nul 2>&1
    if not errorlevel 1 set "PY_LAUNCHER=python"
)
if not defined PY_LAUNCHER (
    echo 未找到 Python 3.11。请先安装 Python 3.11，再重新运行此脚本。
    goto failed
)

if not exist ".venv-tts\Scripts\python.exe" (
    echo 正在创建独立的配音环境 .venv-tts ...
    %PY_LAUNCHER% -m venv .venv-tts
    if errorlevel 1 goto failed
)
".venv-tts\Scripts\python.exe" -c "import sys; assert sys.version_info[:2] == (3, 11)" >nul 2>&1
if errorlevel 1 (
    echo 现有 .venv-tts 不是 Python 3.11 环境，请检查后重新创建。
    goto failed
)

echo 正在安装 VoxCPM2 及依赖，首次安装需要下载较大的文件...
".venv-tts\Scripts\python.exe" -m pip install torch==2.9.1+cu128 torchaudio==2.9.1+cu128 --index-url https://download.pytorch.org/whl/cu128
if errorlevel 1 goto failed
".venv-tts\Scripts\python.exe" -m pip install -r requirements-tts.txt
if errorlevel 1 goto failed
".venv-tts\Scripts\python.exe" -c "import torch, torchaudio, voxcpm, modelscope; assert torch.version.cuda, 'CUDA 版 PyTorch 未安装'"
if errorlevel 1 goto failed

echo.
echo 配音环境安装完成。现在可以打开字幕翻译器，在软件中点击“检测配音模型”。
pause
exit /b 0

:failed
echo.
echo 配音环境安装失败。请检查上面的报错，修复后可重新运行本脚本。
pause
exit /b 1
