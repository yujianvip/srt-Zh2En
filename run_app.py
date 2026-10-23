"""PyInstaller 的独立入口；包内模块使用绝对导入。"""

from tkinterdnd2 import TkinterDnD

from subtitle_translator.app import SubtitleTranslator


def main():
    root = TkinterDnD.Tk()
    SubtitleTranslator(root)
    root.mainloop()


if __name__ == "__main__":
    main()
