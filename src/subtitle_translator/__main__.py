from tkinterdnd2 import TkinterDnD

from .app import SubtitleTranslator


def main():
    root = TkinterDnD.Tk()
    SubtitleTranslator(root)
    root.mainloop()


if __name__ == "__main__":
    main()
