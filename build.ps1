$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $root
$sourcePath = Join-Path $root 'src'
$workerPath = Join-Path $sourcePath 'subtitle_translator\tts_worker.py'

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '创建项目虚拟环境失败。' }
}

& $python -m pip install -r requirements-build.txt
if ($LASTEXITCODE -ne 0) { throw '安装构建依赖失败。' }

& $python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name '字幕翻译器-v1.0' --distpath . --workpath build --specpath build `
    --paths $sourcePath --collect-all tkinterdnd2 `
    --add-data "$workerPath;." run_app.py
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller 打包失败。' }

$exe = Get-Item -LiteralPath '.\字幕翻译器-v1.0.exe'
Write-Host ("构建完成：{0}（{1:N1} MB）" -f $exe.FullName, ($exe.Length / 1MB))
$buildDirectory = Join-Path $root 'build'
if (Test-Path -LiteralPath $buildDirectory) {
    Remove-Item -LiteralPath $buildDirectory -Recurse -Force
}
Read-Host '按回车关闭窗口'
