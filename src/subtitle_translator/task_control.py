"""可中断的任务子进程；停止后等待退出，再由调用方清理临时文件。"""
import subprocess
import threading
import os


class TaskCancelled(Exception):
    pass


class TaskControl:
    def __init__(self):
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._process = None

    def cancel(self):
        self.cancelled.set()
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                self._process.kill()

    def check(self):
        if self.cancelled.is_set():
            raise TaskCancelled()

    def lines(self, command, *, payload=None, env=None):
        self.check()
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        with self._lock:
            self.check()
            process = subprocess.Popen(command, stdin=subprocess.PIPE if payload is not None else subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                       text=True, encoding="utf-8", errors="replace",
                                       creationflags=flags, env=env)
            self._process = process
        try:
            if payload is not None:
                process.stdin.write(payload)
                process.stdin.close()
            for line in process.stdout:
                self.check()
                yield line
            code = process.wait()
            self.check()
            if code:
                raise RuntimeError(f"任务进程退出代码：{code}")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
            with self._lock:
                self._process = None
