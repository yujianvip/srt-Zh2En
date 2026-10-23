import json
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import webbrowser
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
import tkinter as tk
import time
import winsound
import ctypes
from ctypes import wintypes
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
from tkinterdnd2 import DND_FILES, TkinterDnD
from .ollama_client import list_models, translate
from .audio_player import WavPlayer
from .ui import build_ui
from .updater import APP_VERSION, GITHUB_REPOSITORY, check_latest_release, download_release, launch_windows_installer


DEEPL_HELP_URL = "https://support.deepl.com/hc/en-us/articles/360020695820-API-key-for-DeepL-API"
AUTO_VOICE_IDLE_SECONDS = 30
MAX_DEEPL_RESPONSE = 4 * 1024 * 1024
MAX_SRT_SIZE = 20 * 1024 * 1024


class _LastInputInfo(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def _system_idle_seconds():
    info = _LastInputInfo()
    info.cbSize = ctypes.sizeof(info)
    if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
        raise OSError("Windows did not return the last-input time")
    get_tick_count = ctypes.windll.kernel32.GetTickCount
    get_tick_count.restype = ctypes.c_uint32
    now = get_tick_count()
    return ((now - info.dwTime) & 0xFFFFFFFF) / 1000


def _srt_milliseconds(value):
    match = re.fullmatch(r"(\d+):([0-5]\d):([0-5]\d),(\d{3})", value)
    if not match:
        raise ValueError(f"字幕时间码无效：{value}")
    hours, minutes, seconds, millis = map(int, match.groups())
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + millis


def read_srt(path):
    path = Path(path)
    if path.stat().st_size > MAX_SRT_SIZE:
        raise ValueError("SRT 文件超过 20 MB，请检查是否选错了文件。")
    raw = path.read_text(encoding="utf-8-sig")
    blocks = re.split(r"\r?\n\s*\r?\n", raw.strip())
    parsed = []
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3 or "-->" not in lines[1]:
            raise ValueError("SRT 格式无法识别，请检查字幕序号、时间码和正文。")
        start_text, end_text = (part.strip() for part in lines[1].split("-->", 1))
        if _srt_milliseconds(end_text) <= _srt_milliseconds(start_text):
            raise ValueError(f"字幕结束时间必须晚于开始时间：{lines[1]}")
        parsed.append({"time": lines[1].strip(), "text": "\n".join(lines[2:]).strip()})
    return parsed


def translate_deepl(items, api_key, progress=None):
    url = "https://api-free.deepl.com/v2/translate" if api_key.endswith(":fx") else "https://api.deepl.com/v2/translate"
    result = []
    for offset in range(0, len(items), 40):
        batch = items[offset:offset + 40]
        data = [("text", item["text"]) for item in batch]
        data.extend([("source_lang", "ZH"), ("target_lang", "EN-US")])
        request = urllib.request.Request(
            url,
            data=urllib.parse.urlencode(data).encode("utf-8"),
            headers={"Authorization": "DeepL-Auth-Key " + api_key.strip(),
                     "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read(MAX_DEEPL_RESPONSE + 1)
            if len(body) > MAX_DEEPL_RESPONSE:
                raise ValueError("DeepL 返回内容过大，请缩短字幕后重试。")
            payload = json.loads(body.decode("utf-8"))
        translations = payload.get("translations", [])
        if not isinstance(translations, list) or len(translations) != len(batch):
            raise ValueError("DeepL 返回的字幕条数与原文不一致。")
        values = [entry.get("text") if isinstance(entry, dict) else None for entry in translations]
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("DeepL 返回了空白或无效的英文字幕，请重试。")
        result.extend(value.strip() for value in values)
        if progress:
            progress(min(offset + len(batch), len(items)), len(items))
    return result


class SubtitleTranslator:
    def __init__(self, root):
        self.root = root
        self.root.title("双语字幕与英文配音")
        self.root.geometry("1100x850")
        self.items = []
        self.results = {"DeepL": None, "Ollama": None}
        self.active = "Ollama"
        self.events = queue.Queue()
        self.busy = False
        self.source_label = tk.StringVar(value="尚未载入 SRT")
        self.status = tk.StringVar(value="载入剪映导出的中文或中英双语 SRT；英文单语也可用于逐句查看。")
        if getattr(sys, "frozen", False):
            project_dir = Path(sys.executable).resolve().parent
        else:
            project_dir = Path(__file__).resolve().parents[2]
        self.project_dir = project_dir
        self.settings_path = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "字幕翻译器" / "settings.json"
        self.settings = self._read_settings()
        self.provider = tk.StringVar(value=self.settings.get("provider", "Ollama"))
        if self.provider.get() not in self.results:
            self.provider.set("Ollama")
        self.active = self.provider.get()
        self.ollama_address = tk.StringVar(value=self.settings.get("ollama_address", "http://localhost:11434"))
        self.model_name = tk.StringVar(value=self.settings.get("ollama_model", "qwen3.5:9b"))
        self.default_model_name = self.settings.get("ollama_model", "qwen3.5:9b")
        self.model_display = tk.StringVar()
        self.model_display_to_name = {}
        self.available_models = []
        self.output_dir = tk.StringVar(value=self.settings.get("output_dir", str(project_dir / "输出")))
        self.github_repo = tk.StringVar(value=self.settings.get("github_repo", GITHUB_REPOSITORY))
        self.progress_value = tk.DoubleVar(value=0)
        self.tts_python = tk.StringVar(value=str(project_dir / ".venv-tts" / "Scripts" / "python.exe"))
        self.reference_audio = tk.StringVar()
        self.audio_player = WavPlayer()
        self.audio_name = tk.StringVar(value="生成配音后可在这里试听")
        self.audio_position = tk.DoubleVar(value=0)
        self.audio_play_label = tk.StringVar(value="播放")
        self.audio_time_label = tk.StringVar(value="00:00 / 00:00")
        self.audio_seeking = False
        self.audio_duration_ms = 0
        self.auto_voice_pending = False
        self.auto_voice_job = None
        self.auto_voice_started = 0.0
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(150, self._poll)
        self.root.after(200, self._update_audio_player)
        self.root.after(350, lambda: self.refresh_models(silent=True))

    def _read_settings(self):
        try:
            data = json.loads(self.settings_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        data = {
            "provider": self.provider.get(),
            "ollama_address": self.ollama_address.get().strip(),
            "ollama_model": self.model_name.get().strip(),
            "output_dir": self.output_dir.get().strip(),
            "github_repo": self.github_repo.get().strip(),
        }
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _close(self):
        if self.busy:
            messagebox.showinfo("正在处理", "请等当前翻译或配音完成后再关闭窗口，避免留下未配套的输出文件。")
            return
        if hasattr(self, "_ui_confirm_edits") and not self._ui_confirm_edits():
            return
        self._cancel_auto_voice()
        self.audio_player.close()
        try:
            self._save_settings()
        except OSError:
            pass
        self.root.destroy()

    def _drop_path(self, event, kind):
        if self.busy:
            messagebox.showinfo("正在处理", "请等当前翻译或配音完成后再更换文件。")
            return
        paths = self.root.tk.splitlist(event.data)
        if len(paths) != 1:
            messagebox.showinfo("一次只能拖入一个文件", "请只拖入一个 SRT 或一个参考声音文件。")
            return
        path = Path(paths[0])
        if kind == "srt":
            if path.suffix.lower() != ".srt":
                messagebox.showerror("文件类型错误", "请拖入 .srt 字幕文件。")
            else:
                self.load_path(path)
        elif path.suffix.lower() != ".wav" or not path.is_file():
            messagebox.showerror("文件类型错误", "参考声音请使用 WAV 文件。")
        else:
            self.reference_audio.set(str(path))

    def _register_drop(self, widget, kind):
        widget.drop_target_register(DND_FILES)
        widget.dnd_bind("<<Drop>>", lambda event: self._drop_path(event, kind))

    def _build(self):
        build_ui(self)

    def load(self):
        path = filedialog.askopenfilename(title="选择字幕 SRT", filetypes=[("SRT 字幕", "*.srt"), ("所有文件", "*.*")])
        if path:
            self.load_path(path)

    def load_path(self, path):
        if self.busy:
            messagebox.showinfo("正在处理", "请等当前翻译或配音完成后再更换字幕。")
            return
        if hasattr(self, "_ui_confirm_edits") and not self._ui_confirm_edits():
            return
        self._cancel_auto_voice()
        try:
            blocks = read_srt(path)
            split = self.find_split(blocks)
            if split is not None:
                zh, en = blocks[:split], blocks[split:]
                if len(zh) != len(en):
                    raise ValueError("中英文条目数量不一致，无法可靠配对。")
                for i, (a, b) in enumerate(zip(zh, en), 1):
                    if a["time"] != b["time"]:
                        raise ValueError(f"第 {i} 对中英文时间码不一致，已停止以免错配。")
                items = [{"time": a["time"], "text": a["text"], "original_english": b["text"]} for a, b in zip(zh, en)]
            else:
                # Accept multi-line cues, with Chinese lines before English lines.
                items = []
                cue_types = set()
                for block in blocks:
                    lines = [line.strip() for line in block["text"].splitlines() if line.strip()]
                    if not lines:
                        raise ValueError("字幕正文为空，无法识别。")
                    chinese_lines = [bool(re.search(r"[\u4e00-\u9fff]", line)) for line in lines]
                    if any(chinese_lines) and not all(chinese_lines):
                        boundary = chinese_lines.index(False)
                        if boundary == 0 or any(chinese_lines[boundary:]):
                            raise ValueError("双语字幕应将所有中文行放在英文行之前。")
                        items.append({"time": block["time"], "text": "\n".join(lines[:boundary]),
                                      "original_english": "\n".join(lines[boundary:])})
                        cue_types.add("paired")
                    elif all(chinese_lines):
                        items.append({"time": block["time"], "text": "\n".join(lines), "original_english": ""})
                        cue_types.add("chinese")
                    elif not any(chinese_lines):
                        items.append({"time": block["time"], "text": "", "original_english": "\n".join(lines)})
                        cue_types.add("english")
                if len(cue_types) > 1:
                    raise ValueError("文件混合了单语和双语条目，无法可靠识别。请统一字幕格式后再载入。")
            self.items = items
            self.results = {"DeepL": None, "Ollama": None}
            self.active = self.provider.get()
            has_chinese = any(item["text"].strip() for item in items)
            self.source_label.set(f"{Path(path).name}（{len(items)} 条）")
            self.show_rows(None)
            self._set_progress(0)
            self.status.set("字幕已载入：英文列显示原有英文；点击“运行”后会生成新译文。" if has_chinese else "英文字幕已载入，可逐句查看；当前没有中文原文，运行翻译时会提示。")
        except Exception as exc:
            messagebox.showerror("载入失败", str(exc))

    @staticmethod
    def find_split(blocks):
        if len(blocks) % 2:
            return None
        middle = len(blocks) // 2
        if all(a["time"] == b["time"] for a, b in zip(blocks[:middle], blocks[middle:])):
            if any(re.search(r"[\u4e00-\u9fff]", x["text"]) for x in blocks[:middle]):
                return middle
        return None

    def show_deepl_help(self):
        messagebox.showinfo("获取 DeepL API Key", "1. 注册或登录 DeepL API 计划。\n2. 打开“账户”→“API Keys & Limits”。\n3. 复制现有 Key，或点“Create key”新建。\n4. 粘贴到本软件的 API Key 输入框。\n\n接下来会打开 DeepL 官方帮助页。")
        webbrowser.open(DEEPL_HELP_URL)

    def refresh_models(self, silent=False):
        if self.busy:
            return
        address = self.ollama_address.get().strip()
        self.status.set("正在读取 Ollama 模型列表……")
        threading.Thread(target=self._models_worker, args=(address, silent), daemon=True).start()

    def _models_worker(self, address, silent):
        event_type = "MODELS_AUTO" if silent else "MODELS"
        try:
            self.events.put((event_type, list_models(address), None))
        except Exception as exc:
            self.events.put((event_type, None, str(exc)))

    def set_default_model(self):
        if self.model_name.get().strip() not in self.available_models:
            messagebox.showinfo("未选择模型", "先刷新列表并选择一个已安装的 Ollama 模型。")
            return
        self.default_model_name = self.model_name.get().strip()
        try:
            self._save_settings()
        except OSError as exc:
            messagebox.showerror("保存设置失败", str(exc))
            return
        self._refresh_model_options()
        self.status.set(f"Ollama 地址与默认模型已保存：{self.default_model_name}")

    def _refresh_model_options(self):
        self.model_display_to_name = {}
        labels = []
        for name in self.available_models:
            label = f"{name}  ★ 默认" if name == self.default_model_name else name
            labels.append(label)
            self.model_display_to_name[label] = name
        self.model.configure(values=labels)
        selected = self.model_name.get().strip()
        self.model_display.set(next(
            (label for label, name in self.model_display_to_name.items() if name == selected),
            selected,
        ))

    def select_model_display(self, _event=None):
        self.model_name.set(self.model_display_to_name.get(
            self.model_display.get(), self.model_display.get()
        ))

    def choose_output(self):
        folder = filedialog.askdirectory(title="选择输出文件夹", initialdir=self.output_dir.get())
        if folder:
            self.output_dir.set(folder)
            self._save_settings()

    def open_output_folder(self, folder=None):
        raw_folder = str(folder) if folder is not None else self.output_dir.get().strip()
        if not raw_folder:
            messagebox.showinfo("未设置输出目录", "请先在英文配音设置中选择输出文件夹。")
            return
        folder = Path(raw_folder).expanduser()
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))
        except OSError as exc:
            messagebox.showerror("无法打开输出目录", f"{folder}\n\n{exc}")

    def check_updates(self):
        if self.busy:
            messagebox.showinfo("正在处理", "请等当前任务完成后再检查更新。")
            return
        repository = self.github_repo.get().strip()
        self.status.set("正在检查 GitHub 更新……")
        self.update_button.configure(state="disabled")
        threading.Thread(target=self._check_updates_worker, args=(repository,), daemon=True).start()

    def _check_updates_worker(self, repository):
        try:
            release = check_latest_release(repository, APP_VERSION)
            self.events.put(("UPDATE_CHECK", release, None))
        except Exception as exc:
            self.events.put(("UPDATE_ERROR", None, str(exc)))

    def _download_update_worker(self, release):
        try:
            path = download_release(
                release,
                progress=lambda done, total: self.events.put(("UPDATE_PROGRESS", {"done": done, "total": total}, None)),
            )
            self.events.put(("UPDATE_DOWNLOADED", {"path": str(path)}, None))
        except Exception as exc:
            self.events.put(("UPDATE_ERROR", None, str(exc)))

    def _begin_update_download(self, release):
        self.status.set(f"正在下载版本 {release['tag']}……")
        threading.Thread(target=self._download_update_worker, args=(release,), daemon=True).start()

    def show_completion_dialog(self, folder):
        dialog = tk.Toplevel(self.root)
        dialog.title("生成完成")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=20)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"中英双语字幕和英文配音已保存到：\n{folder}",
                  justify="left", wraplength=440).pack(anchor="w", pady=(0, 16))
        actions = ttk.Frame(body)
        actions.pack(fill="x")
        ttk.Button(actions, text="打开输出目录",
                   command=lambda: self.open_output_folder(folder)).pack(side="left")
        ttk.Button(actions, text="关闭", command=dialog.destroy).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.grab_set()

    def _set_progress(self, percent):
        self.progress_value.set(percent)
        self.progress_label.configure(text=f"{percent:.0f}%")

    @staticmethod
    def _format_audio_time(milliseconds):
        seconds = max(0, int(milliseconds // 1000))
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _load_audio(self, path, positions=None):
        name = self.audio_player.load(path)
        self.audio_duration_ms = self.audio_player.duration_ms
        self.audio_name.set(name)
        self.audio_position.set(0)
        self.audio_time_label.set(f"00:00 / {self._format_audio_time(self.audio_duration_ms)}")
        self.audio_slider.configure(to=max(1, self.audio_duration_ms))
        self.audio_play_label.set("播放")
        # 优先使用本次配音的实际时间；重新打开音频时读取配套字幕。
        if positions is None:
            companion = Path(path).with_name(Path(path).stem.replace("英文配音", "中英双语字幕") + ".srt")
            try:
                blocks = read_srt(companion) if companion.is_file() else []
                timings = blocks if len(blocks) == len(self.items) else self.items
                positions = [{"start_ms": self._parse_preview_time(item["time"].split("-->")[0])}
                             for item in timings]
            except (OSError, ValueError, KeyError):
                positions = []
        self._preview_positions = positions
        self._preview_items = self.items
        self._preview_last_row = None
        self._preview_last_position = None

    @staticmethod
    def _parse_preview_time(value):
        hours, minutes, seconds = value.strip().replace(",", ".").split(":")
        return round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)

    def _follow_preview(self, position, mode):
        positions = getattr(self, "_preview_positions", [])
        if not positions or getattr(self, "_preview_items", None) is not self.items:
            return
        if mode != "playing" and position == getattr(self, "_preview_last_position", None):
            return
        self._preview_last_position = position
        index = 0
        for i, timing in enumerate(positions):
            if timing["start_ms"] > position:
                break
            index = i
        row = str(index)
        if row == getattr(self, "_preview_last_row", None) or not self.table.exists(row):
            return
        previous = getattr(self, "_preview_last_row", None)
        if previous is not None and self.table.exists(previous):
            tags = tuple(tag for tag in self.table.item(previous, "tags") if tag != "playing")
            self.table.item(previous, tags=tags)
        self.table.item(row, tags=("playing",) + tuple(tag for tag in self.table.item(row, "tags") if tag != "playing"))
        self.table.see(row)
        self._preview_last_row = row
        # 有未保存编辑时只移动播放标记，不切换编辑框或弹出保存提示。
        if not getattr(self, "_ui_has_unsaved_edits", lambda: False)():
            self.table.selection_set(row)
            self.table.focus(row)

    def open_audio_preview(self):
        if self.busy:
            messagebox.showinfo("正在处理", "请等当前翻译或配音完成后再打开 WAV 文件。")
            return
        path = filedialog.askopenfilename(
            title="打开试听 WAV 文件", filetypes=[("WAV 音频", "*.wav")]
        )
        if path:
            try:
                self._load_audio(path)
            except Exception as exc:
                messagebox.showerror("无法打开音频", str(exc))

    def toggle_audio_preview(self):
        try:
            if self.audio_player.mode() == "playing":
                self.audio_player.pause()
            else:
                self.audio_player.play()
        except Exception as exc:
            messagebox.showerror("试听失败", str(exc))
        self._refresh_audio_player()

    def stop_audio_preview(self):
        try:
            self.audio_player.stop()
        except Exception as exc:
            messagebox.showerror("停止播放失败", str(exc))
        self._refresh_audio_player()

    def _audio_seek_start(self, _event=None):
        self.audio_seeking = True

    def _audio_seek_end(self, _event=None):
        if self.audio_duration_ms <= 0:
            self.audio_seeking = False
            return
        was_playing = self.audio_player.mode() == "playing"
        try:
            self.audio_player.seek(self.audio_position.get(), resume=was_playing)
        except Exception as exc:
            messagebox.showerror("定位失败", str(exc))
        self.audio_seeking = False
        self._refresh_audio_player()

    def _update_audio_player(self):
        self._refresh_audio_player()
        self.root.after(200, self._update_audio_player)

    def _refresh_audio_player(self):
        if self.audio_player.path is not None:
            mode = self.audio_player.mode()
            position = self.audio_player.position()
            self.audio_play_label.set("暂停" if mode == "playing" else "播放")
            if not self.audio_seeking:
                self.audio_position.set(position)
                self._follow_preview(position, mode)
            self.audio_time_label.set(
                f"{self._format_audio_time(position)} / "
                f"{self._format_audio_time(self.audio_duration_ms)}"
            )

    def _set_busy(self, busy):
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.translate_button.configure(state=state)
        self.generate_button.configure(state=state)
        if hasattr(self, "_engine_buttons"):
            for button in self._engine_buttons.values():
                button.configure(state=state)
        if hasattr(self, "detail_editor"):
            if busy:
                self.detail_editor.configure(state="disabled")
            elif hasattr(self, "_ui_select_row"):
                self._ui_select_row()
        if hasattr(self, "_ui_refresh_edit_state"):
            self._ui_refresh_edit_state()

    def start(self):
        provider = self.provider.get()
        if self.busy:
            return
        if hasattr(self, "_ui_confirm_edits") and not self._ui_confirm_edits():
            return
        if not self.items:
            messagebox.showinfo("需要字幕", "请先载入 SRT。")
            return
        if not any(item["text"].strip() for item in self.items):
            messagebox.showinfo("缺少中文原文", "当前 SRT 只有英文，可用于逐句查看；运行翻译前请载入含中文的 SRT。")
            return
        if provider == "DeepL" and not self.key.get().strip():
            messagebox.showinfo("需要 API Key", "请先填写 DeepL API Key。申请并使用 API 会受 DeepL 当前免费计划和额度限制。")
            return
        if provider == "Ollama" and self.model_name.get().strip() not in self.available_models:
            messagebox.showinfo("需要本地模型", "先连接 Ollama 并刷新模型列表。")
            return
        self._cancel_auto_voice()
        self.status.set(f"正在生成 {provider} 版本，请稍候……")
        self._set_progress(0)
        self._set_busy(True)
        api_key = self.key.get().strip()
        model_name = self.model_name.get().strip()
        address = self.ollama_address.get().strip()
        threading.Thread(target=self._translate_worker, args=(provider, api_key, model_name, address), daemon=True).start()

    def _translate_worker(self, provider, api_key, model_name, address):
        try:
            def progress(done, total):
                self.events.put(("TRANSLATE_PROGRESS", {"done": done, "total": total, "provider": provider}, None))
            if provider == "DeepL":
                values = translate_deepl(self.items, api_key, progress)
            else:
                values = translate(self.items, model_name, address, progress)
            self.events.put((provider, values, None))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:1000]
            self.events.put((provider, None, f"HTTP {exc.code}: {body}"))
        except Exception as exc:
            self.events.put((provider, None, str(exc)))

    def _schedule_auto_voice(self, provider):
        self._cancel_auto_voice()
        self.auto_voice_pending = True
        self.auto_voice_started = time.monotonic()
        try:
            winsound.MessageBeep(winsound.MB_OK)
        except RuntimeError:
            pass
        self.status.set(
            f"{provider} 翻译完成。电脑连续空闲 {AUTO_VOICE_IDLE_SECONDS} 秒后自动生成英文配音。"
        )
        self.auto_voice_job = self.root.after(1000, self._check_auto_voice)

    def _cancel_auto_voice(self):
        self.auto_voice_pending = False
        if self.auto_voice_job is not None:
            try:
                self.root.after_cancel(self.auto_voice_job)
            except tk.TclError:
                pass
            self.auto_voice_job = None

    def _check_auto_voice(self):
        self.auto_voice_job = None
        if not self.auto_voice_pending:
            return
        elapsed = time.monotonic() - self.auto_voice_started
        try:
            idle = _system_idle_seconds()
        except (AttributeError, OSError):
            idle = elapsed
        remaining = max(0, AUTO_VOICE_IDLE_SECONDS - elapsed,
                        AUTO_VOICE_IDLE_SECONDS - idle)
        if remaining <= 0:
            self.auto_voice_pending = False
            if not self.items or self.results[self.active] is None:
                self.status.set("自动配音已取消：当前翻译方式没有可用译稿。")
                return
            self.status.set("电脑已空闲 30 秒，正在自动生成英文配音……")
            self.generate_bundle()
            return
        self.status.set(
            f"翻译已完成；电脑空闲 {min(idle, AUTO_VOICE_IDLE_SECONDS):.0f}/"
            f"{AUTO_VOICE_IDLE_SECONDS} 秒后自动生成配音。"
        )
        self.auto_voice_job = self.root.after(1000, self._check_auto_voice)

    def _poll(self):
        try:
            while True:
                provider, values, error = self.events.get_nowait()
                if provider == "UPDATE_CHECK":
                    self.update_button.configure(state="normal")
                    if not values["available"]:
                        self.status.set(f"当前已是最新版本 v{APP_VERSION}。")
                        messagebox.showinfo("检查更新", f"当前已是最新版本 v{APP_VERSION}。")
                    else:
                        notes = values["notes"] or "此版本没有附带更新说明。"
                        if messagebox.askyesno(
                            "发现新版本",
                            f"发现版本 {values['tag']}（当前 v{APP_VERSION}）。\n\n{notes}\n\n现在下载并安装吗？",
                        ):
                            self._begin_update_download(values)
                    continue
                if provider == "UPDATE_PROGRESS":
                    percent = 100 * values["done"] / max(1, values["total"])
                    self.status.set(f"正在下载更新：{percent:.0f}%")
                    continue
                if provider == "UPDATE_DOWNLOADED":
                    try:
                        launch_windows_installer(values["path"], sys.executable)
                    except Exception as exc:
                        self.update_button.configure(state="normal")
                        self.status.set("更新安装启动失败。")
                        messagebox.showerror("更新失败", str(exc))
                    else:
                        self.status.set("更新文件已校验，正在重启并安装……")
                        self.audio_player.close()
                        self.root.destroy()
                        return
                    continue
                if provider == "UPDATE_ERROR":
                    self.update_button.configure(state="normal")
                    self.status.set("检查或下载更新失败。")
                    messagebox.showerror("更新失败", error)
                    continue
                if provider in ("MODELS", "MODELS_AUTO"):
                    if error:
                        if provider == "MODELS":
                            self.status.set("读取 Ollama 模型失败。")
                            messagebox.showerror("读取 Ollama 模型失败", error)
                    else:
                        self.available_models = values
                        if values and self.model_name.get() not in values:
                            preferred = next((name for name in values if name == "qwen3.5:9b"), values[0])
                            self.model_name.set(preferred)
                        self._refresh_model_options()
                        self.status.set(f"已从 Ollama 读取 {len(values)} 个模型。")
                    continue
                if provider == "TRANSLATE_PROGRESS":
                    self._set_progress(100 * values["done"] / values["total"])
                    self.status.set(f"{values['provider']} 正在翻译：{values['done']}/{values['total']} 条")
                    continue
                if provider == "TTS_PROGRESS":
                    self._set_progress(100 * values["done"] / values["total"])
                    self.status.set(f"VoxCPM2 正在生成英文配音：{values['done']}/{values['total']} 组")
                    continue
                if provider == "TTS_COMPLETE":
                    stamp = values["timestamp"]
                    serial = 1
                    while True:
                        suffix = "" if serial == 1 else f"-{serial}"
                        candidate_stamp = f"{stamp}{suffix}"
                        wav_path = Path(values["folder"]) / f"{candidate_stamp}-英文配音-{values['version']}.wav"
                        srt_path = Path(values["folder"]) / f"{candidate_stamp}-中英双语字幕-{values['version']}.srt"
                        if not wav_path.exists() and not srt_path.exists():
                            break
                        serial += 1
                    staged_wav = Path(values["staged_wav"])
                    staged_srt = srt_path.with_name(f".{srt_path.stem}-{uuid.uuid4().hex}.srt")
                    try:
                        self._export_files(values["folder"], values["version"], values["positions"],
                                           values["english"], output_path=staged_srt)
                        self._commit_bundle(((staged_wav, wav_path), (staged_srt, srt_path)))
                    except Exception as exc:
                        self._set_busy(False)
                        self.status.set("输出失败；已保留上一版成品。")
                        messagebox.showerror("输出失败", str(exc))
                        continue
                    finally:
                        for staged_file in (staged_wav, staged_srt):
                            try:
                                staged_file.unlink(missing_ok=True)
                            except OSError:
                                pass
                    try:
                        self._load_audio(wav_path, positions=values["positions"])
                        self._refresh_audio_player()
                    except Exception as exc:
                        messagebox.showwarning("配音已生成", f"文件已输出，但无法加载到试听区：\n{exc}")
                    self._set_progress(100)
                    self._set_busy(False)
                    self.status.set(f"已输出中英双语字幕和英文配音：{values['folder']}")
                    self.show_completion_dialog(values["folder"])
                    continue
                if provider == "TTS_ERROR":
                    self._set_busy(False)
                    self.status.set("VoxCPM2 生成失败。")
                    messagebox.showerror("VoxCPM2 生成失败", error)
                    continue
                if error:
                    self._set_busy(False)
                    self.status.set(f"{provider} 翻译失败。")
                    messagebox.showerror(f"{provider} 翻译失败", error)
                else:
                    self.results[provider] = values
                    self._set_progress(100)
                    self._set_busy(False)
                    if self.provider.get() == provider:
                        self.active = provider
                        self.show_rows(values)
                    self._schedule_auto_voice(provider)
        except queue.Empty:
            pass
        self.root.after(150, self._poll)

    def show_result(self, _event=None):
        if hasattr(self, "_ui_confirm_edits") and not self._ui_confirm_edits():
            self.provider.set(self.active)
            self._ui_refresh_provider()
            return
        self.active = self.provider.get()
        self.show_rows(self.results[self.active])
        if self.auto_voice_pending and self.results[self.active] is None:
            self._cancel_auto_voice()
            self.status.set("已切换到尚无译稿的方式，自动配音已取消。")
        if hasattr(self, "_ui_refresh_provider"):
            self._ui_refresh_provider()
        try:
            self._save_settings()
        except OSError:
            pass

    def show_rows(self, values):
        selection = self.table.selection()
        selected = selection[0] if selection else None
        for row in self.table.get_children():
            self.table.delete(row)
        for i, item in enumerate(self.items):
            english = values[i] if values is not None else ""
            original_english = item.get("original_english", "")
            self.table.insert("", "end", iid=str(i),
                              values=(i + 1, item["time"], item["text"], original_english.replace("\n", " "), english.replace("\n", " ")),
                              tags=("even" if i % 2 == 0 else "odd",))
        if selected is not None and self.table.exists(selected):
            self.table.selection_set(selected)
            self.table.see(selected)
        if hasattr(self, "rows_summary"):
            suffix = "已生成译稿" if values is not None else "等待翻译"
            self.rows_summary.set(f"{len(self.items)} 条字幕  ·  {suffix}" if self.items else "等待导入字幕")
        if hasattr(self, "_ui_update_empty"):
            self._ui_update_empty()
        if hasattr(self, "_ui_select_row"):
            self._ui_select_row()

    def edit_cell(self, event):
        if self.busy:
            return
        region = self.table.identify("region", event.x, event.y)
        column = self.table.identify_column(event.x)
        row = self.table.identify_row(event.y)
        if region != "cell" or column != "#5" or not row:
            return
        values = self.results[self.active]
        if values is None:
            return
        box = self.table.bbox(row, column)
        if not box:
            return
        x, y, width, height = box
        entry = ttk.Entry(self.table)
        entry.place(x=x, y=y, width=width, height=height)
        entry.insert(0, values[int(row)])
        entry.focus_set()
        saved = False
        def save(_event=None):
            nonlocal saved
            if saved:
                return
            saved = True
            values[int(row)] = entry.get().strip()
            entry.destroy()
            self.show_rows(values)
        entry.bind("<Return>", save)
        entry.bind("<FocusOut>", save)

    def choose_tts_python(self):
        path = filedialog.askopenfilename(title="选择 VoxCPM 虚拟环境的 python.exe", filetypes=[("Python", "python.exe")])
        if path:
            self.tts_python.set(path)

    def choose_reference(self):
        path = filedialog.askopenfilename(title="选择自己的声音参考片段", filetypes=[("WAV 音频", "*.wav"), ("所有文件", "*.*")])
        if path:
            self.reference_audio.set(path)

    @staticmethod
    def _timecode(milliseconds):
        milliseconds = max(0, int(milliseconds))
        hours, rest = divmod(milliseconds, 3600000)
        minutes, rest = divmod(rest, 60000)
        seconds, milli = divmod(rest, 1000)
        return f"{hours:02}:{minutes:02}:{seconds:02},{milli:03}"

    @staticmethod
    def _write_srt(path, blocks):
        data = "\r\n\r\n".join(blocks) + "\r\n"
        Path(path).write_bytes(b"\xef\xbb\xbf" + data.encode("utf-8"))

    @staticmethod
    def _commit_bundle(files):
        """Install WAV and SRT together, restoring existing files on failure."""
        token = uuid.uuid4().hex
        backups = []
        installed = []
        try:
            for _stage, target in files:
                if target.exists():
                    backup = target.with_name(f".{target.name}.{token}.backup")
                    os.replace(target, backup)
                    backups.append((target, backup))
            for stage, target in files:
                os.replace(stage, target)
                installed.append(target)
        except Exception:
            for target in installed:
                target.unlink(missing_ok=True)
            for target, backup in reversed(backups):
                os.replace(backup, target)
            raise
        for _target, backup in backups:
            try:
                backup.unlink()
            except OSError:
                pass

    def _export_files(self, folder, version, positions=None, english_snapshot=None, output_path=None):
        values = english_snapshot if english_snapshot is not None else self.results[version]
        if values is None or len(values) != len(self.items):
            raise ValueError("英文字幕条数与中文原文不一致，无法导出。")
        if positions is not None and len(positions) != len(self.items):
            raise ValueError("配音时间码数量与字幕不一致，无法导出。")
        bilingual_blocks = []
        for index, (item, translated) in enumerate(zip(self.items, values), 1):
            if positions:
                position = positions[index - 1]
                timing = f"{self._timecode(position['start_ms'])} --> {self._timecode(position['end_ms'])}"
            else:
                timing = item["time"]
            header = f"{index}\r\n{timing}\r\n"
            bilingual_blocks.append(header + item["text"] + "\r\n" + translated)
        target = Path(folder)
        target.mkdir(parents=True, exist_ok=True)
        self._write_srt(output_path or target / f"中英双语字幕-{version}.srt", bilingual_blocks)

    def generate_bundle(self):
        if self.busy:
            return
        if hasattr(self, "_ui_confirm_edits") and not self._ui_confirm_edits():
            return
        values = self.results[self.active]
        if values is None:
            messagebox.showinfo("尚无英文", "请先生成并校对要使用的英文版本。")
            return
        if any(not value.strip() for value in values):
            messagebox.showerror("英文缺失", "有空白英文条目，请先补全。")
            return
        python_path = Path(self.tts_python.get().strip())
        if not python_path.is_file():
            messagebox.showerror("缺少语音环境", "未找到 VoxCPM 专用 Python。请先运行 setup_voxcpm.bat，或选择正确的 python.exe。")
            return
        reference = self.reference_audio.get().strip()
        if reference and not Path(reference).is_file():
            messagebox.showerror("参考音频不存在", "请重新选择参考声音文件。")
            return
        folder = self.output_dir.get().strip()
        if not folder:
            messagebox.showinfo("需要输出目录", "请设置输出目录。")
            return
        try:
            Path(folder).mkdir(parents=True, exist_ok=True)
            self._save_settings()
        except OSError as exc:
            messagebox.showerror("输出目录不可用", str(exc))
            return
        version = self.active
        worker = (Path(sys._MEIPASS) / "tts_worker.py") if getattr(sys, "frozen", False) else Path(__file__).with_name("tts_worker.py")
        if not worker.is_file():
            messagebox.showerror("缺少语音模块", f"找不到语音工作程序：{worker}")
            return
        # Windows MCI keeps the preview WAV open and blocks replacing it.
        self.audio_player.close()
        self.audio_name.set("生成配音后可在这里试听")
        self.audio_play_label.set("播放")
        self.audio_duration_ms = 0
        self.audio_position.set(0)
        self.audio_time_label.set("00:00 / 00:00")
        self._cancel_auto_voice()
        self.status.set("正在启动 VoxCPM2；首次运行需要下载模型权重……")
        self._set_progress(0)
        self._set_busy(True)
        items = [{"time": item["time"], "english": value} for item, value in zip(self.items, values)]
        timestamp = datetime.now().strftime("%y-%m-%d %H-%M-%S")
        threading.Thread(
            target=self._tts_worker,
            args=(str(python_path), str(worker), folder, version, reference, items, timestamp),
            daemon=True,
        ).start()

    def _tts_worker(self, python_path, worker, folder, version, reference, items, timestamp):
        staged_wav = Path(folder) / f".英文配音-{version}-{uuid.uuid4().hex}.wav"
        try:
            with tempfile.TemporaryDirectory(prefix="subtitle_voice_") as temp_dir:
                manifest = {
                    "items": items,
                    "reference_wav": reference,
                    "wav_path": str(staged_wav),
                    "timing_path": str(Path(temp_dir) / "timing.json"),
                }
                manifest_path = Path(temp_dir) / "manifest.json"
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                environment = os.environ.copy()
                environment["HF_HOME"] = str(self.project_dir / ".model-cache")
                process = subprocess.Popen(
                    [python_path, "-u", worker, str(manifest_path)],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", creationflags=flags,
                    env=environment,
                )
                recent = []
                for line in process.stdout:
                    recent.append(line.strip())
                    recent = recent[-12:]
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "progress" in event:
                        self.events.put(("TTS_PROGRESS", {"done": event["progress"], "total": event["total"]}, None))
                code = process.wait()
                timing_path = Path(manifest["timing_path"])
                if code != 0 or not timing_path.is_file():
                    raise RuntimeError("\n".join(recent) or f"语音进程退出代码：{code}")
                positions = json.loads(timing_path.read_text(encoding="utf-8"))["positions"]
                if len(positions) != len(items):
                    raise ValueError("生成的字幕时间数量与原字幕不一致。")
                if not staged_wav.is_file() or staged_wav.stat().st_size <= 44:
                    raise ValueError("语音文件未成功生成。")
                self.events.put(("TTS_COMPLETE", {"folder": folder, "version": version,
                                                  "positions": positions, "staged_wav": str(staged_wav),
                                                  "timestamp": timestamp,
                                                  "english": [x["english"] for x in items]}, None))
        except Exception as exc:
            try:
                staged_wav.unlink(missing_ok=True)
            except OSError:
                pass
            self.events.put(("TTS_ERROR", None, str(exc)))


if __name__ == "__main__":
    window = TkinterDnD.Tk()
    app = SubtitleTranslator(window)
    window.mainloop()
