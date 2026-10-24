"""从 GitHub Release 的 ZIP 更新 Windows 程序。"""

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path


APP_VERSION = "1.0.2"
GITHUB_REPOSITORY = "yujianvip/srt-Zh2En"
EXE_NAME = "字幕翻译器-v1.0.exe"
MAX_UPDATE_SIZE = 400 * 1024 * 1024


def _version_tuple(value):
    value = value.strip().lower().removeprefix("v")
    if not re.fullmatch(r"\d+(?:\.\d+){0,3}", value):
        raise ValueError(f"版本号格式不正确：{value}")
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * (4 - len(parts))


def _request_json(url):
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "SubtitleStudio-Updater",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            payload = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError("GitHub 仓库尚未发布公开 Release，或仓库地址不正确。") from exc
        raise RuntimeError(f"访问 GitHub 失败：HTTP {exc.code}") from exc
    if len(payload) > 2 * 1024 * 1024:
        raise RuntimeError("GitHub Release 信息过大。")
    return json.loads(payload.decode("utf-8"))


def check_latest_release(repository=GITHUB_REPOSITORY, current_version=APP_VERSION):
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository.strip()):
        raise ValueError("GitHub 仓库应为 owner/repository 格式。")
    release = _request_json(f"https://api.github.com/repos/{repository.strip()}/releases/latest")
    tag = release.get("tag_name", "")
    remote_version = _version_tuple(tag)
    current = _version_tuple(current_version)
    assets = release.get("assets", [])
    package_name = f"srt-Zh2En-{tag}-package.zip"
    asset = next((item for item in assets if item.get("name") == package_name), None)
    if remote_version > current and asset is None:
        raise RuntimeError(f"Release {tag} 缺少更新压缩包 {package_name}。")
    return {
        "tag": tag,
        "version": remote_version,
        "available": remote_version > current,
        "asset": asset,
        "notes": release.get("body", "").strip(),
        "html_url": release.get("html_url", ""),
    }


def download_release(release, progress=None):
    asset = release["asset"]
    url = asset.get("browser_download_url", "")
    if not url.startswith("https://github.com/"):
        raise RuntimeError("Release 下载地址不是受支持的 GitHub HTTPS 地址。")
    expected_size = int(asset.get("size", 0))
    if expected_size <= 0 or expected_size > MAX_UPDATE_SIZE:
        raise RuntimeError("更新文件大小异常，已停止下载。")
    package = Path(tempfile.gettempdir()) / f"subtitle-studio-{uuid.uuid4().hex}.zip"
    target = package.with_suffix(".exe")
    digest = hashlib.sha256()
    total = 0
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "SubtitleStudio-Updater"})
        with urllib.request.urlopen(request, timeout=30) as response, package.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_UPDATE_SIZE or total > expected_size:
                    raise RuntimeError("下载内容超过 Release 声明的文件大小。")
                output.write(chunk)
                digest.update(chunk)
                if progress:
                    progress(total, expected_size)
        if total != expected_size:
            raise RuntimeError("更新下载不完整，文件大小与 GitHub 声明不一致。")
        expected_digest = asset.get("digest")
        if expected_digest and expected_digest.lower() != f"sha256:{digest.hexdigest()}":
            raise RuntimeError("更新文件 SHA-256 校验失败。")
        with zipfile.ZipFile(package) as archive:
            try:
                entry = archive.getinfo(EXE_NAME)
            except KeyError as exc:
                raise RuntimeError(f"更新包缺少 {EXE_NAME}。") from exc
            if entry.file_size < 1024 * 1024 or entry.file_size > MAX_UPDATE_SIZE:
                raise RuntimeError("更新包内的 EXE 大小异常。")
            with archive.open(entry) as source, target.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
        with target.open("rb") as executable:
            valid_header = executable.read(2) == b"MZ"
        if target.stat().st_size != entry.file_size or not valid_header:
            raise RuntimeError("更新包内的 EXE 无效。")
        return target
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        package.unlink(missing_ok=True)


def launch_windows_installer(staged_exe, target_exe):
    if not getattr(sys, "frozen", False):
        raise RuntimeError("自动替换只支持已打包的 EXE。")
    staged = Path(staged_exe).resolve()
    target = Path(target_exe).resolve()
    if not staged.is_file() or staged.stat().st_size < 1024 * 1024:
        raise RuntimeError("下载的更新文件不存在或大小异常。")
    if target != Path(sys.executable).resolve():
        raise RuntimeError("更新目标与当前运行的 EXE 不一致。")

    def ps_quote(value):
        return "'" + str(value).replace("'", "''") + "'"

    backup = target.with_name(target.name + ".update-backup")
    script = Path(tempfile.gettempdir()) / f"subtitle-update-{uuid.uuid4().hex}.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$pidToWait = {os.getpid()}\n"
        f"$target = {ps_quote(target)}\n"
        f"$staged = {ps_quote(staged)}\n"
        f"$backup = {ps_quote(backup)}\n"
        "while (Get-Process -Id $pidToWait -ErrorAction SilentlyContinue) { Start-Sleep -Milliseconds 300 }\n"
        "try {\n"
        "  Move-Item -LiteralPath $target -Destination $backup -Force\n"
        "  Move-Item -LiteralPath $staged -Destination $target -Force\n"
        "  Start-Process -FilePath $target\n"
        "} catch {\n"
        "  if (Test-Path -LiteralPath $target) { Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue }\n"
        "  if (Test-Path -LiteralPath $backup) { Move-Item -LiteralPath $backup -Destination $target -Force; Start-Process -FilePath $target }\n"
        "  throw\n"
        "} finally { Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue; Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue; Remove-Item -LiteralPath $PSCommandPath -Force -ErrorAction SilentlyContinue }\n",
        encoding="utf-8-sig",
    )
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        subprocess.Popen([
            "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden", "-File", str(script),
        ], creationflags=flags, close_fds=True)
    except Exception:
        script.unlink(missing_ok=True)
        raise
