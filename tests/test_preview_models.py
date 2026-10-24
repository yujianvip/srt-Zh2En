"""回归检查：试听音频与当前译文配对，忽略过期模型列表。"""

from pathlib import Path
import queue
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from subtitle_translator.app import SubtitleTranslator
from subtitle_translator.task_control import TaskControl


class Value:
    def __init__(self, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value

    def configure(self, **_kwargs):
        pass


class FakePlayer:
    duration_ms = 1000

    def load(self, path):
        return Path(path).name


class PreviewAndModelTests(unittest.TestCase):
    def test_companion_subtitle_must_match_current_english(self):
        app = SubtitleTranslator.__new__(SubtitleTranslator)
        app._stop_sentence_preview = lambda: None
        app._clear_preview_highlight = lambda: None
        app.audio_player = FakePlayer()
        app.audio_name = app.audio_position = app.audio_time_label = Value()
        app.audio_slider = app.audio_play_label = Value()
        app.items = [{"time": "00:00:00,000 --> 00:00:01,000", "text": "中文", "original_english": ""}]
        app.results = {"Ollama": ["New English"], "DeepL": None}
        app.active = "Ollama"
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            wav = folder / "take-英文配音-Ollama.wav"
            wav.write_bytes(b"unused by fake player")
            srt = folder / "take-中英双语字幕-Ollama.srt"
            srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n中文\nOld English\n", encoding="utf-8")
            app._load_audio(wav)
            self.assertEqual(app._preview_positions, [])

            srt.write_text("1\n00:00:00,000 --> 00:00:01,000\n中文\nNew English\n", encoding="utf-8")
            app._load_audio(wav)
            self.assertEqual(app._preview_positions, [{"start_ms": 0, "end_ms": 1000}])

    def test_old_model_response_cannot_replace_current_list(self):
        app = SubtitleTranslator.__new__(SubtitleTranslator)
        app.events = queue.Queue()
        app._task_control = TaskControl()
        app._model_refresh_serial = 2
        app.ollama_address = Value("http://new-host:11434")
        app.available_models = ["current"]
        app.model_name = Value("current")
        app.status = Value()
        app.root = type("Root", (), {"after": lambda _self, *_args: None})()
        app._refresh_model_options = lambda: None
        app.events.put(("MODELS", {"address": "http://old-host:11434", "serial": 1,
                                   "models": ["stale"]}, None))
        app._poll()
        self.assertEqual(app.available_models, ["current"])

        app.events.put(("MODELS", {"address": "http://new-host:11434", "serial": 2,
                                   "models": ["fresh"]}, None))
        app._poll()
        self.assertEqual(app.available_models, ["fresh"])


if __name__ == "__main__":
    unittest.main()
