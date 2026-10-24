"""停止行为回归：使用本地假服务和假配音进程，不消耗 API 或运行模型。"""
import json
import os
from pathlib import Path
import queue
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from subtitle_translator.app import SubtitleTranslator
from subtitle_translator.task_control import TaskControl


class Value:
    def set(self, value):
        self.value = value
    def configure(self, **kwargs):
        self.options = kwargs


def app_stub(project=ROOT):
    app = SubtitleTranslator.__new__(SubtitleTranslator)
    app.project_dir = project
    app._task_control = TaskControl()
    app.events = queue.Queue()
    app.items = [{"time": "00:00:00,000 --> 00:00:01,000", "text": "你好"}]
    app.busy = True
    app._close_after_stop = False
    app.status = Value()
    app.stop_button = Value()
    app._cancel_auto_voice = lambda: None
    app._set_busy = lambda busy: setattr(app, "busy", busy)
    app._set_progress = lambda value: None
    app.root = SimpleNamespace(after=lambda *args: None)
    return app


class StopTests(unittest.TestCase):
    def test_tts_stop_removes_partial_and_private_cache(self):
        with tempfile.TemporaryDirectory() as folder:
            folder = Path(folder)
            output = folder / "out"
            output.mkdir()
            finished = output / "finished.wav"
            finished.write_bytes(b"completed audio")
            worker = folder / "fake_worker.py"
            worker.write_text('''import json, sys, time
from pathlib import Path
m=json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
Path(m["wav_path"]).with_suffix(".partial.wav").write_bytes(b"unfinished")
Path(m["task_temp_dir"], "default_english_voice.partial.wav").write_bytes(b"partial reference")
print(json.dumps({"progress":1,"total":10}),flush=True)
time.sleep(30)
''', encoding="utf-8")
            app = app_stub(folder)
            thread = threading.Thread(target=app._tts_worker, args=(sys.executable, str(worker), str(output), "Ollama", "", [{"english": "Hello", "time": app.items[0]["time"]}], "test"))
            thread.start()
            self.assertEqual(app.events.get(timeout=5)[0], "TTS_PROGRESS")
            app.stop_task()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            app._poll()
            self.assertFalse(app.busy)
            self.assertEqual(list(output.iterdir()), [finished])
            self.assertEqual(list((folder / ".task-cache").iterdir()), [])
            self.assertIn("已清理", app.status.value)

    def test_late_completion_is_discarded(self):
        with tempfile.TemporaryDirectory() as folder:
            staged = Path(folder) / "partial.wav"
            staged.write_bytes(b"uncommitted")
            app = app_stub()
            app.events.put(("TTS_COMPLETE", {"staged_wav": str(staged)}, None))
            app.stop_task()
            app._poll()
            self.assertFalse(staged.exists())
            self.assertFalse(app.busy)
        app = app_stub()
        app.results = {"Ollama": ["previous completed translation"]}
        app.events.put(("Ollama", ["late unwanted translation"], None))
        app.stop_task()
        app._poll()
        self.assertEqual(app.results["Ollama"], ["previous completed translation"])

    def test_translation_wait_can_be_stopped(self):
        arrived = threading.Event()
        release = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["Content-Length"]))
                arrived.set()
                release.wait(10)
                try:
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b'{"message":{"content":"{\\"translations\\":[\\"Hello\\"]}"}}')
                except OSError:
                    pass
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        serving = threading.Thread(target=server.serve_forever, daemon=True)
        serving.start()
        app = app_stub()
        worker = threading.Thread(target=app._translate_worker, args=("Ollama", "", "fake", f"http://127.0.0.1:{server.server_port}"))
        try:
            worker.start()
            self.assertTrue(arrived.wait(8), list(app.events.queue))
            app.stop_task()
            worker.join(5)
            self.assertFalse(worker.is_alive())
            app._poll()
            self.assertFalse(app.busy)
        finally:
            app._task_control.cancel()
            release.set()
            worker.join(5)
            server.shutdown()
            server.server_close()

    @unittest.skipUnless(os.environ.get("SUBTITLE_TEST_EXE"), "只在打包后验证无控制台子进程")
    def test_packaged_translation_worker_pipes(self):
        control = TaskControl()
        payload = json.dumps({"provider": "Ollama", "model": "fake", "address": "http://127.0.0.1:1", "items": []})
        lines = list(control.lines([os.environ["SUBTITLE_TEST_EXE"], "--translation-worker"], payload=payload))
        self.assertIn({"result": []}, [json.loads(line) for line in lines if line.startswith("{")])


if __name__ == "__main__":
    unittest.main()
