"""翻译子进程入口。API 密钥通过内存管道传入，不写入临时文件。"""
import json
import sys


def main():
    from .app import translate_deepl
    from .ollama_client import translate
    # Windows 无控制台 EXE 也通过父进程创建的标准管道通信。
    def emit(value):
        print(json.dumps(value, ensure_ascii=True), flush=True)
    try:
        data = json.loads(sys.stdin.read())
        progress = lambda done, total: emit({"progress": done, "total": total})
        if data["provider"] == "DeepL":
            values = translate_deepl(data["items"], data["api_key"], progress)
        else:
            values = translate(data["items"], data["model"], data["address"], progress)
        emit({"result": values})
    except Exception as exc:
        emit({"error": str(exc)})
