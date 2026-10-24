"""PyInstaller 的独立入口；包内模块使用绝对导入。"""

from tkinterdnd2 import TkinterDnD

from subtitle_translator.app import SubtitleTranslator
import sys


def main():
    if "--translation-worker" in sys.argv:
        # PyInstaller windowed 模式将 sys 标准流设为 None，恢复继承的管道。
        if sys.stdin is None:
            sys.stdin = open(0, "r", encoding="utf-8", closefd=False)
        if sys.stdout is None:
            sys.stdout = open(1, "w", encoding="utf-8", closefd=False)
        from subtitle_translator.translation_worker import main as translate_main
        translate_main()
        return
    root = TkinterDnD.Tk()
    SubtitleTranslator(root)
    root.mainloop()


if __name__ == "__main__":
    main()
