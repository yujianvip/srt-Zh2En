import json
import os
import queue
import re
import subprocess
import shutil
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
from .task_control import TaskControl, TaskCancelled
from .sentence_preview import preview_sentence as speak_sentence
from .voice_model import missing_model_files
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
        self._close_after_stop = False
        self._pending_update_path = None
        self._task_control = TaskControl()
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
        self._model_refresh_serial = 0
        self.output_dir = tk.StringVar(value=self.settings.get("output_dir", str(project_dir / "输出")))
        self.github_repo = tk.StringVar(value=self.settings.get("github_repo", GITHUB_REPOSITORY))
        self.progress_value = tk.DoubleVar(value=0)
        voice_runtime = project_dir / ".venv-tts" / "Scripts" / "python.exe"
        self.tts_python = tk.StringVar(value=str(voice_runtime))
        self.reference_audio = tk.StringVar(value=self.settings.get("reference_audio", ""))
        self.reference_audio.trace_add("write", self._persist_path_settings)
        self.output_dir.trace_add("write", self._persist_path_settings)
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
        self.auto_voice_countdown = tk.StringVar(value="")
        self._build()
        self.ollama_address.trace_add("write", self._ollama_address_changed)
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
            "reference_audio": self.reference_audio.get().strip(),
            "github_repo": self.github_repo.get().strip(),
        }
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        self.settings_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _persist_path_settings(self, *_args):
        try:
            self._save_settings()
        except OSError as exc:
            self.status.set(f"保存配音设置失败：{exc}")

    def _close(self):
        if self.busy:
            if not messagebox.askyesno("任务执行中", "有执行中任务，是否强制停止并关闭？", parent=self.root):
                return
            self._close_after_stop = True
            self.stop_task()
            return
        if hasattr(self, "_ui_confirm_edits") and not self._ui_confirm_edits():
            return
        self._finish_close()

    def _finish_close(self):
        self._cancel_auto_voice()
        self._stop_sentence_preview()
        self.audio_player.close()
        if self._pending_update_path is not None:
            try:
                Path(self._pending_update_path).unlink(missing_ok=True)
            except OSError:
                pass
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
        self.status.trace_add("write", self._update_stage_indicator)
        self._update_stage_indicator()

    def _update_stage_indicator(self, *_args):
        """把长状态消息映射为稳定、易扫读的阶段标签。"""
        if not hasattr(self, "stage_label"):
            return
        message = self.status.get()
        if "停止" in message and ("正在" in message or "清理" in message):
            stage, color = "停止中", "#A3322D"
        elif "失败" in message or "无法" in message or "错误" in message:
            stage, color = "需处理", "#A3322D"
        elif "已输出" in message or "已载入" in message or "已保存" in message or "已完成" in message:
            stage, color = "已完成", "#26734D"
        elif "翻译" in message or "DeepL 正在" in message or "Ollama 正在" in message:
            stage, color = "翻译中", "#16648F"
        elif "配音" in message or "VoxCPM2" in message or "自动生成" in message:
            stage, color = "配音中", "#16648F"
        elif "试听" in message or "播放" in message:
            stage, color = "试听中", "#16648F"
        else:
            stage, color = "就绪", "#16648F"
        self.stage_label.configure(text=stage, fg=color)

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
            self._clear_loaded_audio()
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
        dialog = tk.Toplevel(self.root)
        dialog.title("获取 DeepL API Key")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        body = ttk.Frame(dialog, padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(
            body,
            text="1. 注册或登录 DeepL API 计划。\n"
                 "2. 打开“账户”→“API Keys & Limits”。\n"
                 "3. 复制现有 Key，或点“Create key”新建。\n"
                 "4. 粘贴到本软件的 API Key 输入框。",
            justify="left",
        ).pack(anchor="w", pady=(0, 14))
        actions = ttk.Frame(body)
        actions.pack(fill="x")
        ttk.Button(actions, text="取消", command=dialog.destroy).pack(side="left")

        def open_deepl_page():
            webbrowser.open(DEEPL_HELP_URL, new=2)
            dialog.destroy()

        ttk.Button(actions, text="打开浏览器获取 API Key", command=open_deepl_page).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.grab_set()

    def refresh_models(self, silent=False):
        if self.busy:
            return
        address = self.ollama_address.get().strip()
        self._model_refresh_serial += 1
        serial = self._model_refresh_serial
        self.status.set("正在读取 Ollama 模型列表……")
        threading.Thread(target=self._models_worker, args=(address, silent, serial), daemon=True).start()

    def _ollama_address_changed(self, *_args):
        self._model_refresh_serial += 1
        self.available_models = []
        self._refresh_model_options()
        self.status.set("Ollama 地址已更改，请刷新模型列表。")

    def prompt_start_ollama(self, error):
        """Offer to launch the installed Ollama app when its service is unavailable."""
        dialog = tk.Toplevel(self.root)
        dialog.title("Ollama 未启用")
        dialog.transient(self.root)
        dialog.resizable(False, False)
        dialog.configure(bg="#F6F8F9")
        tk.Label(dialog, text="当前无法连接 Ollama 服务。\n启用 Ollama 后再刷新模型列表？",
                 bg="#F6F8F9", fg="#1E2C35", font=("Microsoft YaHei UI", 11),
                 justify="left", padx=24, pady=20).pack(fill="x")
        actions = tk.Frame(dialog, bg="#F6F8F9", padx=16, pady=12)
        actions.pack(fill="x")
        def launch():
            dialog.destroy()
            try:
                self.launch_ollama()
            except OSError as exc:
                messagebox.showerror("无法启动 Ollama", str(exc))
                return
            self.status.set("已启动 Ollama，正在等待服务就绪并刷新模型……")
            self.root.after(1800, self.refresh_models)
        tk.Button(actions, text="启用 Ollama", command=launch).pack(side="left", padx=(0, 8))
        tk.Button(actions, text="取消", command=dialog.destroy).pack(side="right")
        dialog.update_idletasks()
        x = self.root.winfo_rootx() + (self.root.winfo_width() - dialog.winfo_width()) // 2
        y = self.root.winfo_rooty() + (self.root.winfo_height() - dialog.winfo_height()) // 2
        dialog.geometry(f"+{max(0, x)}+{max(0, y)}")
        dialog.grab_set()

    @staticmethod
    def launch_ollama():
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        user_local = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "AppData" / "Local"
        program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        for path in (local / "Programs" / "Ollama" / "ollama app.exe",
                     user_local / "Programs" / "Ollama" / "ollama app.exe",
                     local / "Programs" / "Ollama" / "Ollama.exe",
                     user_local / "Programs" / "Ollama" / "Ollama.exe",
                     program_files / "Ollama" / "ollama app.exe",
                     program_files / "Ollama" / "Ollama.exe"):
            if path.is_file():
                os.startfile(str(path))
                return
        # Some installations only expose the CLI; in that case start its service.
        found = shutil.which("ollama")
        if found:
            subprocess.Popen([found, "serve"], cwd=str(Path(found).parent),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) |
                             getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return
        raise OSError("未找到 Ollama 桌面程序或命令行程序。请先安装 Ollama，或确认安装路径可用。")

    def _models_worker(self, address, silent, serial):
        event_type = "MODELS_AUTO" if silent else "MODELS"
        try:
            self.events.put((event_type, {"address": address, "serial": serial,
                                          "models": list_models(address)}, None))
        except Exception as exc:
            self.events.put((event_type, {"address": address, "serial": serial}, str(exc)))

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

    def open_preview_folder(self):
        """打开当前试听 WAV 所在目录；没有载入 WAV 时回退到输出目录。"""
        audio_path = self.audio_player.path
        if audio_path is not None:
            self.open_output_folder(Path(audio_path).parent)
        else:
            self.open_output_folder()

    def check_updates(self):
        if self.busy:
            messagebox.showinfo("正在处理", "请等当前任务完成后再检查更新。")
            return
        if self._pending_update_path is not None:
            if messagebox.askyesno("更新已下载", "更新文件已下载。现在安装并重启程序吗？"):
                self._install_downloaded_update(self._pending_update_path)
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

    def _install_downloaded_update(self, path):
        try:
            launch_windows_installer(path, sys.executable)
        except Exception as exc:
            self.update_button.configure(state="normal")
            self.status.set("更新安装启动失败。")
            messagebox.showerror("更新失败", str(exc))
            return False
        self._pending_update_path = None
        self.status.set("更新文件已校验，正在重启并安装……")
        self.audio_player.close()
        self.root.destroy()
        return True

    def show_completion_dialog(self, folder, wav_path, positions):
        dialog = tk.Toplevel(self.root)
        dialog.withdraw()
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
        def load_to_preview():
            try:
                self._load_audio(wav_path, positions=positions)
                self._refresh_audio_player()
            except Exception as exc:
                messagebox.showerror("载入试听失败", str(exc), parent=dialog)
                return
            dialog.destroy()

        ttk.Button(actions, text="载入到试听",
                   command=load_to_preview).pack(side="left", padx=(8, 0))
        ttk.Button(actions, text="关闭", command=dialog.destroy).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", dialog.destroy)
        dialog.update_idletasks()
        width = dialog.winfo_reqwidth()
        height = dialog.winfo_reqheight()
        self.root.update_idletasks()
        parent_x = self.root.winfo_rootx()
        parent_y = self.root.winfo_rooty()
        parent_width = self.root.winfo_width()
        parent_height = self.root.winfo_height()
        screen_width = dialog.winfo_screenwidth()
        screen_height = dialog.winfo_screenheight()
        if parent_width <= 1 or parent_height <= 1:
            parent_x = parent_y = 0
            parent_width, parent_height = screen_width, screen_height
        x = max(0, min(parent_x + (parent_width - width) // 2, screen_width - width))
        y = max(0, min(parent_y + (parent_height - height) // 2, screen_height - height))
        dialog.geometry(f"{width}x{height}+{x}+{y}")
        dialog.deiconify()
        # 等 Windows 标题栏和边框完成布局后再按实际窗口尺寸校准位置。
        dialog.after_idle(lambda: self._recenter_completion_dialog(dialog))
        dialog.grab_set()

    def _recenter_completion_dialog(self, dialog):
        if not dialog.winfo_exists():
            return
        dialog.update_idletasks()
        self.root.update_idletasks()
        width, height = dialog.winfo_width(), dialog.winfo_height()
        parent_x, parent_y = self.root.winfo_rootx(), self.root.winfo_rooty()
        parent_width, parent_height = self.root.winfo_width(), self.root.winfo_height()
        screen_width, screen_height = dialog.winfo_screenwidth(), dialog.winfo_screenheight()
        if parent_width <= 1 or parent_height <= 1:
            parent_x = parent_y = 0
            parent_width, parent_height = screen_width, screen_height
        x = max(0, min(parent_x + (parent_width - width) // 2, screen_width - width))
        y = max(0, min(parent_y + (parent_height - height) // 2, screen_height - height))
        dialog.geometry(f"+{x}+{y}")

    def _set_progress(self, percent):
        self.progress_value.set(percent)
        self.progress_label.configure(text=f"{percent:.0f}%")

    @staticmethod
    def _format_audio_time(milliseconds):
        seconds = max(0, int(milliseconds // 1000))
        return f"{seconds // 60:02d}:{seconds % 60:02d}"

    def _stop_sentence_preview(self):
        control = getattr(self, "_sentence_control", None)
        if control is not None:
            control.cancel()
        self._sentence_control = None
        if getattr(self, "_segment_preview_end_ms", None) is not None:
            try:
                self.audio_player.pause()
            except RuntimeError:
                pass
        self._segment_preview_end_ms = None
        self._segment_preview_row = None
        self._sentence_index = None
        self._sentence_token = getattr(self, "_sentence_token", 0) + 1
        self._clear_preview_highlight()
        for button in getattr(self, "sentence_preview_buttons", []):
            if button is not None:
                button.configure(text="试听")

    def _clear_preview_highlight(self):
        if not hasattr(self, "table"):
            return
        for row in self.table.get_children():
            tags = tuple(tag for tag in self.table.item(row, "tags") if tag != "playing")
            self.table.item(row, tags=tags)
        self._preview_last_row = None

    def preview_sentence(self, index):
        if index != 2 or self.busy or not self.items or not self.table.selection():
            return
        if self.audio_player.path is None:
            messagebox.showinfo("需要试听音频", "请先打开或生成英文配音 WAV，再试听当前字幕对应的语音。", parent=self.root)
            return
        row = int(self.table.selection()[0])
        if (getattr(self, "_sentence_index", None) == index
                and getattr(self, "_segment_preview_row", None) == row):
            self._stop_sentence_preview()
            self.audio_play_label.set("播放")
            self.status.set("当前字幕试听已停止。")
            return
        positions = getattr(self, "_preview_positions", [])
        if row >= len(positions):
            messagebox.showinfo("缺少字幕时间", "当前 WAV 没有对应的字幕时间，无法截取这一句试听。", parent=self.root)
            return
        self._stop_sentence_preview()
        self._cancel_auto_voice()
        timing = positions[row]
        start_ms = int(timing["start_ms"])
        end_ms = int(timing.get("end_ms", positions[row + 1]["start_ms"] if row + 1 < len(positions) else self.audio_duration_ms))
        end_ms = min(end_ms, self.audio_duration_ms)
        if end_ms <= start_ms:
            messagebox.showinfo("字幕时间无效", "当前字幕的结束时间不晚于开始时间，无法试听。", parent=self.root)
            return
        set_selection = getattr(self, "_ui_set_selection", None)
        if set_selection is not None:
            set_selection(str(row))
        self.table.focus(str(row))
        self._clear_preview_highlight()
        self.audio_player.seek(start_ms)
        self.audio_player.play()
        self._segment_preview_end_ms = end_ms
        self._segment_preview_row = row
        self._preview_follow_enabled = False
        self._sentence_index = index
        self.sentence_preview_buttons[index].configure(text="停止")
        self.audio_play_label.set("暂停")
        self.status.set(f"正在试听第 {row + 1} 条字幕对应的配音片段。")

    def _sentence_worker(self, text, language, control, token):
        error = None
        try:
            speak_sentence(text, language, control)
        except Exception as exc:
            if not control.cancelled.is_set():
                error = str(exc)
        self.events.put(("SENTENCE_PREVIEW", {"token": token}, error))

    def _load_audio(self, path, positions=None):
        self._stop_sentence_preview()
        self._clear_preview_highlight()
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
                current_english = self.results.get(self.active)
                matched = (
                    current_english is not None
                    and len(current_english) == len(self.items) == len(blocks)
                    and all(
                        block["text"] == item["text"] + "\n" + english
                        for block, item, english in zip(blocks, self.items, current_english)
                    )
                )
                positions = [{
                    "start_ms": self._parse_preview_time(block["time"].split("-->")[0]),
                    "end_ms": self._parse_preview_time(block["time"].split("-->")[1]),
                } for block in blocks] if matched else []
            except (OSError, ValueError, KeyError):
                positions = []
        self._preview_positions = positions
        self._preview_items = self.items
        self._preview_last_row = None
        self._preview_last_position = None
        if hasattr(self, "_ui_refresh_edit_state"):
            self._ui_refresh_edit_state()

    def _clear_loaded_audio(self):
        self._stop_sentence_preview()
        self.audio_player.close()
        self.audio_name.set("生成配音后可在这里试听")
        self.audio_play_label.set("播放")
        self.audio_duration_ms = 0
        self.audio_position.set(0)
        self.audio_time_label.set("00:00 / 00:00")
        self.audio_slider.configure(to=1)
        self._preview_positions = []
        self._preview_items = None
        self._preview_follow_enabled = True
        self._clear_preview_highlight()
        if hasattr(self, "_ui_refresh_edit_state"):
            self._ui_refresh_edit_state()

    @staticmethod
    def _parse_preview_time(value):
        hours, minutes, seconds = value.strip().replace(",", ".").split(":")
        return round((int(hours) * 3600 + int(minutes) * 60 + float(seconds)) * 1000)

    def _follow_preview(self, position, mode):
        if not getattr(self, "_preview_follow_enabled", True):
            return
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
        self._clear_preview_highlight()
        self.table.item(row, tags=("playing",) + tuple(tag for tag in self.table.item(row, "tags") if tag != "playing"))
        self.table.see(row)
        self._preview_last_row = row
        # 有未保存编辑时只移动播放标记，不切换编辑框或弹出保存提示。
        if not getattr(self, "_ui_has_unsaved_edits", lambda: False)():
            set_selection = getattr(self, "_ui_set_selection", None)
            if set_selection is not None:
                set_selection(row)
            else:
                self.table.selection_remove(*self.table.selection())
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
                if not self._preview_positions:
                    self.status.set("已打开 WAV；未找到与当前译稿匹配的配套字幕，逐句试听不可用。")
                else:
                    self.status.set("已打开配音和匹配的双语字幕，可逐句试听。")
            except Exception as exc:
                messagebox.showerror("无法打开音频", str(exc))

    def toggle_audio_preview(self):
        self._stop_sentence_preview()
        self._preview_follow_enabled = True
        try:
            if self.audio_player.mode() == "playing":
                self.audio_player.pause()
            else:
                self.audio_player.play()
        except Exception as exc:
            messagebox.showerror("试听失败", str(exc))
        self._refresh_audio_player()

    def stop_audio_preview(self):
        self._stop_sentence_preview()
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
        self._preview_follow_enabled = True
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
            segment_end = getattr(self, "_segment_preview_end_ms", None)
            if segment_end is not None and (mode != "playing" or position >= segment_end):
                if mode == "playing":
                    self.audio_player.pause()
                self._segment_preview_end_ms = None
                self._segment_preview_row = None
                self._sentence_index = None
                self.sentence_preview_buttons[2].configure(text="试听")
                self._clear_preview_highlight()
                mode = self.audio_player.mode()
            self.audio_play_label.set("暂停" if mode == "playing" else "播放")
            if not self.audio_seeking:
                self.audio_position.set(position)
                if getattr(self, "_sentence_index", None) is None:
                    self._follow_preview(position, mode)
            self.audio_time_label.set(
                f"{self._format_audio_time(position)} / "
                f"{self._format_audio_time(self.audio_duration_ms)}"
            )

    def stop_task(self):
        if not self.busy or self._task_control.cancelled.is_set():
            return
        self._cancel_auto_voice()
        self.stop_button.configure(state="disabled", text="正在停止…")
        self.status.set("正在停止任务并清理临时文件……")
        self._task_control.cancel()

    def _set_busy(self, busy):
        if busy:
            self._stop_sentence_preview()
            self._task_control = TaskControl()
        self.busy = busy
        self.stop_button.configure(state="normal" if busy else "disabled", text="停止")
        state = "disabled" if busy else "normal"
        self.translate_button.configure(state=state)
        self.generate_button.configure(state=state)
        self.voice_model_button.configure(state=state)
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
        self._clear_loaded_audio()
        self.status.set(f"正在生成 {provider} 版本，请稍候……")
        self._set_progress(0)
        self._set_busy(True)
        api_key = self.key.get().strip()
        model_name = self.model_name.get().strip()
        address = self.ollama_address.get().strip()
        threading.Thread(target=self._translate_worker, args=(provider, api_key, model_name, address), daemon=True).start()

    def _translate_worker(self, provider, api_key, model_name, address):
        control = self._task_control
        try:
            command = [sys.executable, "--translation-worker"] if getattr(sys, "frozen", False) else [
                sys.executable, "-u", "-c",
                "import sys; sys.path.insert(0, sys.argv[1]); from subtitle_translator.translation_worker import main; main()",
                str(self.project_dir / "src"),
            ]
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(self.project_dir / "src") + os.pathsep + environment.get("PYTHONPATH", "")
            payload = json.dumps({"provider": provider, "api_key": api_key, "model": model_name,
                                  "address": address, "items": self.items}, ensure_ascii=True)
            values = None
            for line in control.lines(command, payload=payload, env=environment):
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if "progress" in event:
                    self.events.put(("TRANSLATE_PROGRESS", {"done": event["progress"], "total": event["total"], "provider": provider}, None))
                if "error" in event:
                    raise RuntimeError(event["error"])
                if "result" in event:
                    values = event["result"]
            control.check()
            if not isinstance(values, list) or len(values) != len(self.items):
                raise ValueError("翻译进程没有返回完整结果。")
            self.events.put((provider, values, None))
        except Exception as exc:
            self.events.put((provider, None, str(exc)))

    def _schedule_auto_voice(self, provider):
        self._cancel_auto_voice()
        self.auto_voice_pending = True
        self.auto_voice_started = time.monotonic()
        self.auto_voice_countdown.set(
            f"剩余时间：{AUTO_VOICE_IDLE_SECONDS} 秒　｜　连续无操作后开始生成英文配音和双语字幕"
        )
        self.auto_voice_banner.grid()
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
        if hasattr(self, "auto_voice_banner"):
            self.auto_voice_banner.grid_remove()

    def cancel_auto_voice_countdown(self):
        if not self.auto_voice_pending:
            return
        self._cancel_auto_voice()
        self.status.set("已取消自动生成；需要时可手动点击“生成双语字幕与英文配音”。")

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
            self._cancel_auto_voice()
            if not self.items or self.results[self.active] is None:
                self.status.set("自动配音已取消：当前翻译方式没有可用译稿。")
                return
            self.status.set("倒计时结束，正在自动生成英文配音和中英双语字幕……")
            self.generate_bundle()
            return
        seconds_left = max(1, int(remaining + 0.999))
        self.auto_voice_countdown.set(
            f"剩余时间：{seconds_left} 秒　｜　连续无操作后开始生成英文配音和双语字幕"
        )
        self.status.set(
            f"字幕已生成；连续无操作倒计时 {seconds_left} 秒。可在醒目提示栏取消。"
        )
        self.auto_voice_job = self.root.after(1000, self._check_auto_voice)

    def _poll(self):
        try:
            while True:
                provider, values, error = self.events.get_nowait()
                if provider == "SENTENCE_PREVIEW":
                    if values["token"] == getattr(self, "_sentence_token", None):
                        self._stop_sentence_preview()
                        self.status.set("单句试听失败。" if error else "单句试听结束。")
                        if error:
                            messagebox.showerror("单句试听失败", error, parent=self.root)
                    continue
                if self._task_control.cancelled.is_set() and provider in ("DeepL", "Ollama", "TRANSLATE_PROGRESS", "TTS_PROGRESS", "TTS_MODEL_PROGRESS", "TTS_MODEL_READY", "TTS_MODEL_ERROR", "TTS_MODEL_CANCELLED", "TTS_COMPLETE", "TTS_ERROR"):
                    if provider in ("TRANSLATE_PROGRESS", "TTS_PROGRESS", "TTS_MODEL_PROGRESS"):
                        continue
                    cleanup_error = None
                    if provider == "TTS_COMPLETE":
                        try:
                            Path(values["staged_wav"]).unlink(missing_ok=True)
                        except OSError as exc:
                            cleanup_error = str(exc)
                    if provider == "TTS_ERROR" and error and error.startswith("清理失败"):
                        cleanup_error = error
                    self._set_busy(False)
                    self._set_progress(0)
                    self.status.set("任务已停止，未完成任务的临时文件已清理。" if not cleanup_error else f"任务已停止，但部分临时文件无法删除：{cleanup_error}")
                    if self._close_after_stop:
                        self._finish_close()
                        return
                    continue
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
                    if self.busy:
                        self._pending_update_path = values["path"]
                        self.update_button.configure(state="normal", text="安装已下载更新")
                        self.status.set("更新已下载；当前任务完成后，点击“安装已下载更新”继续。")
                        continue
                    if self._install_downloaded_update(values["path"]):
                        return
                    continue
                if provider == "UPDATE_ERROR":
                    self.update_button.configure(state="normal")
                    self.status.set("检查或下载更新失败。")
                    messagebox.showerror("更新失败", error)
                    continue
                if provider in ("MODELS", "MODELS_AUTO"):
                    if (values["serial"] != self._model_refresh_serial or
                            values["address"] != self.ollama_address.get().strip()):
                        continue
                    if error:
                        if provider == "MODELS":
                            self.status.set("读取 Ollama 模型失败。")
                            if "urlopen error" in error.lower() or "connection refused" in error.lower():
                                self.prompt_start_ollama(error)
                            else:
                                messagebox.showerror("读取 Ollama 模型失败", error)
                    else:
                        models = values["models"]
                        self.available_models = models
                        if models and self.model_name.get() not in models:
                            preferred = next((name for name in models if name == "qwen3.5:9b"), models[0])
                            self.model_name.set(preferred)
                        self._refresh_model_options()
                        self.status.set(f"已从 Ollama 读取 {len(models)} 个模型。")
                    continue
                if provider == "TRANSLATE_PROGRESS":
                    self._set_progress(100 * values["done"] / values["total"])
                    self.status.set(f"{values['provider']} 正在翻译：{values['done']}/{values['total']} 条")
                    continue
                if provider == "TTS_PROGRESS":
                    self._set_progress(100 * values["done"] / values["total"])
                    self.status.set(f"VoxCPM2 正在生成英文配音：{values['done']}/{values['total']} 组")
                    continue
                if provider == "TTS_MODEL_PROGRESS":
                    total = values["total"]
                    percent = 100 * values["done"] / total if total else 0
                    self._set_progress(percent)
                    self.status.set(
                        f"正在下载 VoxCPM2 配音模型：{values['filename']} "
                        f"{values['done'] / 1024**2:.1f}/{total / 1024**2:.1f} MB（{percent:.0f}%）"
                    )
                    continue
                if provider == "TTS_MODEL_READY":
                    self._set_progress(100)
                    self._set_busy(False)
                    self.status.set("VoxCPM2 配音模型下载完成。")
                    messagebox.showinfo("配音模型", "VoxCPM2 配音模型已下载，可以开始生成英文配音。")
                    continue
                if provider == "TTS_MODEL_ERROR":
                    self._set_busy(False)
                    self.status.set("VoxCPM2 配音模型下载失败。")
                    messagebox.showerror("配音模型下载失败", error)
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
                    self.show_completion_dialog(values["folder"], wav_path, values["positions"])
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
        if self.active != self.provider.get():
            self._clear_loaded_audio()
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

    def choose_reference(self):
        path = filedialog.askopenfilename(title="选择自己的声音参考片段", filetypes=[("WAV 音频", "*.wav"), ("所有文件", "*.*")])
        if path:
            self.reference_audio.set(path)

    def open_reference_audio(self):
        raw_path = self.reference_audio.get().strip()
        if not raw_path:
            messagebox.showinfo("尚未选择参考声音", "请先填写或浏览选择 WAV 音频文件。")
            return
        path = Path(raw_path).expanduser()
        if not path.is_file():
            messagebox.showerror("参考声音不存在", f"找不到文件：\n{path}")
            return
        try:
            os.startfile(str(path))
        except OSError as exc:
            messagebox.showerror("无法打开参考声音", f"{path}\n\n{exc}")

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

    def check_voice_model(self):
        if self.busy:
            return
        python_path = Path(self.tts_python.get().strip())
        if not python_path.is_file():
            messagebox.showerror("尚未安装配音环境", "请先运行软件同目录的 setup_voxcpm.bat，安装完成后再检测配音模型。")
            return
        missing = missing_model_files(self.project_dir / ".model-cache")
        if not missing:
            self.status.set("VoxCPM2 配音模型已下载。")
            messagebox.showinfo("配音模型检测", "VoxCPM2 配音模型已下载。")
            return
        if not messagebox.askyesno(
            "配音模型检测",
            "VoxCPM2 配音模型尚未下载完整。\n\n是否现在开始下载？",
        ):
            self.status.set("已取消配音模型下载。")
            return
        self._cancel_auto_voice()
        self._set_progress(0)
        self._set_busy(True)
        self.status.set("正在下载 VoxCPM2 配音模型……")
        threading.Thread(target=self._download_voice_model_worker, args=(python_path,), daemon=True).start()

    def _download_voice_model_worker(self, python_path):
        worker = (Path(sys._MEIPASS) / "tts_worker.py") if getattr(sys, "frozen", False) else Path(__file__).with_name("tts_worker.py")
        command = [str(python_path), "-u", str(worker), "--download-model"]
        environment = os.environ.copy()
        environment["HF_HOME"] = str(self.project_dir / ".model-cache")
        ready = False
        recent = []
        try:
            for line in self._task_control.lines(command, env=environment):
                recent.append(line.strip())
                recent = recent[-12:]
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if "model_download" in event:
                    self.events.put(("TTS_MODEL_PROGRESS", event["model_download"], None))
                elif event.get("model_ready"):
                    ready = True
                elif "error" in event:
                    raise RuntimeError(event["error"])
            if not ready or missing_model_files(self.project_dir / ".model-cache"):
                raise RuntimeError("配音模型仍不完整。" + "\n".join(recent))
            self.events.put(("TTS_MODEL_READY", None, None))
        except TaskCancelled:
            self.events.put(("TTS_MODEL_CANCELLED", None, None))
        except Exception as exc:
            self.events.put(("TTS_MODEL_ERROR", None, str(exc)))

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
            messagebox.showerror("尚未安装配音环境", "请先运行软件同目录的 setup_voxcpm.bat，安装完成后再生成英文配音。")
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
        self._clear_loaded_audio()
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
        control = self._task_control
        staged_wav = Path(folder) / f".英文配音-{version}-{uuid.uuid4().hex}.wav"
        result = None
        failure = None
        temp_dir = None
        try:
            task_cache = self.project_dir / ".task-cache"
            task_cache.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix="subtitle_voice_", dir=task_cache) as temp_dir:
                manifest = {"items": items, "reference_wav": reference,
                            "wav_path": str(staged_wav), "task_temp_dir": temp_dir,
                            "timing_path": str(Path(temp_dir) / "timing.json")}
                manifest_path = Path(temp_dir) / "manifest.json"
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
                environment = os.environ.copy()
                environment["HF_HOME"] = str(self.project_dir / ".model-cache")
                environment["TMP"] = environment["TEMP"] = temp_dir
                recent = []
                command = [python_path, "-u", str(worker), str(manifest_path)]
                for line in control.lines(command, env=environment):
                    recent.append(line.strip())
                    recent = recent[-12:]
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue
                    if "error" in event:
                        failure = event["error"]
                    if "progress" in event:
                        self.events.put(("TTS_PROGRESS", {"done": event["progress"], "total": event["total"]}, None))
                    if "model_download" in event:
                        self.events.put(("TTS_MODEL_PROGRESS", event["model_download"], None))
                control.check()
                timing_path = Path(manifest["timing_path"])
                if not timing_path.is_file():
                    raise RuntimeError(failure or "\n".join(recent) or "没有生成配音时间码。")
                positions = json.loads(timing_path.read_text(encoding="utf-8"))["positions"]
                if len(positions) != len(items):
                    raise ValueError("生成的字幕时间数量与原字幕不一致。")
                if not staged_wav.is_file() or staged_wav.stat().st_size <= 44:
                    raise ValueError("语音文件未成功生成。")
                result = {"folder": folder, "version": version, "positions": positions,
                          "staged_wav": str(staged_wav), "timestamp": timestamp,
                          "english": [x["english"] for x in items]}
            control.check()
        except Exception as exc:
            result = None
            failure = failure or str(exc)
        finally:
            if temp_dir is not None and Path(temp_dir).exists():
                result = None
                failure = f"清理失败：任务临时目录仍被占用：{temp_dir}"
            # 强制退出不会执行子进程 finally；由父进程删除本任务的残留。
            targets = [staged_wav.with_suffix(".partial.wav")]
            if result is None or control.cancelled.is_set():
                targets.append(staged_wav)
                result = None
            for target in targets:
                try:
                    target.unlink(missing_ok=True)
                except OSError as exc:
                    result = None
                    failure = f"清理失败：{target}：{exc}"
        if result is not None:
            self.events.put(("TTS_COMPLETE", result, None))
        else:
            self.events.put(("TTS_ERROR", None, failure))


if __name__ == "__main__":
    window = TkinterDnD.Tk()
    app = SubtitleTranslator(window)
    window.mainloop()
