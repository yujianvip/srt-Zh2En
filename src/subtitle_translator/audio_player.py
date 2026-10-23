"""Windows WAV preview controls powered by the built-in MCI audio API."""

import ctypes
import secrets
import shutil
import tempfile
from pathlib import Path


class WavPlayer:
    def __init__(self):
        self._winmm = ctypes.WinDLL("winmm")
        self._winmm.mciSendStringW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p
        ]
        self._winmm.mciSendStringW.restype = ctypes.c_uint
        self._winmm.mciGetErrorStringW.argtypes = [
            ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint
        ]
        self._winmm.mciGetErrorStringW.restype = ctypes.c_int
        self._alias = "subtitle_preview"
        self._path = None
        self._preview_path = None
        self.duration_ms = 0

    def _command(self, command, query=False):
        output = ctypes.create_unicode_buffer(256) if query else None
        result = self._winmm.mciSendStringW(
            command, output, len(output) if output is not None else 0, 0
        )
        if result:
            message = ctypes.create_unicode_buffer(256)
            self._winmm.mciGetErrorStringW(result, message, len(message))
            raise RuntimeError(message.value or f"MCI error {result}")
        return output.value if output is not None else None

    def load(self, path):
        path = Path(path).resolve()
        if not path.is_file() or path.suffix.lower() != ".wav":
            raise ValueError("试听文件必须是存在的 WAV 音频。")
        self.close()
        # The MCI waveaudio driver rejects long or non-ASCII WAV basenames.
        # A private short-named copy also keeps the user's output file unlocked.
        try:
            for _ in range(10):
                preview_path = Path(tempfile.gettempdir()) / f"{secrets.token_hex(4)}.wav"
                try:
                    preview_path.open("xb").close()
                    break
                except FileExistsError:
                    continue
            else:
                raise RuntimeError("无法创建试听临时文件。")
            self._preview_path = preview_path
            shutil.copyfile(path, preview_path)
            self._command(f'open "{preview_path}" type waveaudio alias {self._alias}')
            self._path = path
            self._command(f"set {self._alias} time format milliseconds")
            self.duration_ms = int(self._command(f"status {self._alias} length", query=True))
        except Exception:
            self.close()
            raise
        return path.name

    @property
    def path(self):
        return self._path

    def position(self):
        if self._path is None:
            return 0
        try:
            return int(self._command(f"status {self._alias} position", query=True))
        except (ValueError, RuntimeError):
            return 0

    def mode(self):
        if self._path is None:
            return "not_ready"
        try:
            return self._command(f"status {self._alias} mode", query=True)
        except RuntimeError:
            return "not_ready"

    def play(self):
        if self._path is None:
            raise RuntimeError("请先生成或打开一个 WAV 文件。")
        if self.mode() == "paused":
            self._command(f"resume {self._alias}")
            return
        position = self.position()
        if position >= self.duration_ms:
            position = 0
        self._command(f"play {self._alias} from {position}")

    def pause(self):
        if self._path is not None and self.mode() == "playing":
            self._command(f"pause {self._alias}")

    def stop(self):
        if self._path is not None:
            self._command(f"stop {self._alias}")
            self._command(f"seek {self._alias} to 0")

    def seek(self, position_ms, resume=False):
        if self._path is None:
            return
        position_ms = max(0, min(int(position_ms), self.duration_ms))
        self._command(f"stop {self._alias}")
        self._command(f"seek {self._alias} to {position_ms}")
        if resume and position_ms < self.duration_ms:
            self._command(f"play {self._alias} from {position_ms}")

    def close(self):
        if self._path is not None:
            try:
                self._command(f"close {self._alias}")
            except RuntimeError:
                pass
        self._path = None
        self.duration_ms = 0
        if self._preview_path is not None:
            self._preview_path.unlink(missing_ok=True)
            self._preview_path = None
