"""字幕翻译器的 Windows 桌面界面。翻译与配音逻辑位于 app.py。"""

import tkinter as tk
from tkinter import ttk, messagebox


INK = "#1E2C35"
INK_SOFT = "#245F84"
INK_MUTED = "#465862"
PAPER = "#F6F8F9"
SHEET = "#FFFFFF"
RAIL = "#ECF2F5"
LINE = "#CCD7DC"
TEXT = "#1E2C35"
MUTED = "#4C5D67"
ACCENT = "#16648F"
ACCENT_HOVER = "#0F5175"
MINT = "#DCECF6"

BODY_FONT = ("Microsoft YaHei UI", 11)
LABEL_FONT = ("Microsoft YaHei UI", 10)
SMALL_FONT = ("Microsoft YaHei UI", 9)
TITLE_FONT = ("Microsoft YaHei UI", 22, "bold")
MONO_FONT = ("Consolas", 10)


def _label(parent, text="", *, color=TEXT, bg=PAPER, font=BODY_FONT, **kwargs):
    return tk.Label(parent, text=text, bg=bg, fg=color, font=font, **kwargs)


def _section(parent, number, title):
    row = tk.Frame(parent, bg=RAIL)
    row.pack(fill="x", pady=(10, 4))
    _label(row, number, color=ACCENT, bg=RAIL, font=MONO_FONT).pack(side="left")
    _label(row, title, color=INK, bg=RAIL, font=("Microsoft YaHei UI", 11, "bold")).pack(side="left", padx=(10, 0))
    return row


def _divider(parent):
    tk.Frame(parent, height=1, bg=LINE).pack(fill="x", pady=(7, 0))


def _rail_button(parent, text, command, *, primary=False):
    color = ACCENT if primary else SHEET
    hover = ACCENT_HOVER if primary else MINT
    button = tk.Button(
        parent, text=text, command=command, bg=color, fg="#FFFFFF" if primary else INK,
        activebackground=hover, activeforeground="#FFFFFF" if primary else INK,
        relief="flat", bd=0, padx=15, pady=9,
        cursor="hand2", font=("Microsoft YaHei UI", 11, "bold"),
        highlightthickness=2, highlightbackground=RAIL, highlightcolor=ACCENT,
    )
    if primary:
        button.configure(disabledforeground="#FFFFFF")
    return button


def _subtle_button(parent, text, command, *, width=None, textvariable=None):
    return tk.Button(
        parent, text=text, textvariable=textvariable, command=command, width=width, bg=SHEET, fg=INK,
        activebackground=MINT, activeforeground=INK,
        relief="flat", bd=0, padx=10, pady=6, cursor="hand2",
        font=LABEL_FONT, highlightthickness=1, highlightbackground=LINE,
        highlightcolor=ACCENT,
    )


def _entry(parent, *, textvariable=None, show=None):
    entry = tk.Entry(
        parent, textvariable=textvariable, show=show, bg=SHEET, fg=INK,
        insertbackground=INK, relief="flat", bd=0, font=BODY_FONT,
        highlightthickness=2, highlightbackground=LINE, highlightcolor=ACCENT,
    )
    return entry


def _style(root):
    style = ttk.Style(root)
    style.theme_use("clam")
    style.configure("Studio.Treeview", background=SHEET, fieldbackground=SHEET,
                    foreground=TEXT, borderwidth=0, rowheight=44, font=BODY_FONT)
    style.configure("Studio.Treeview.Heading", background=RAIL, foreground=TEXT,
                    relief="flat", borderwidth=0, padding=(10, 11),
                    font=("Microsoft YaHei UI", 10, "bold"))
    style.map("Studio.Treeview", background=[("selected", MINT)],
              foreground=[("selected", INK)])
    style.configure("Studio.Vertical.TScrollbar", background="#AABBC4", troughcolor=SHEET,
                    borderwidth=0, arrowsize=14)
    style.configure("Studio.Horizontal.TProgressbar", background=ACCENT,
                    troughcolor=LINE, borderwidth=0, thickness=7)
    style.configure("Rail.TCombobox", padding=(10, 9), fieldbackground=SHEET,
                    background=SHEET, foreground=INK, arrowcolor=INK,
                    bordercolor=LINE, lightcolor=LINE, darkcolor=LINE,
                    font=("Microsoft YaHei UI", 10))
    style.map("Rail.TCombobox", fieldbackground=[("readonly", SHEET)],
              foreground=[("readonly", INK)])


def _center_dialog(dialog, root, width, height):
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    root.update_idletasks()
    x = root.winfo_rootx() + (root.winfo_width() - width) // 2
    y = root.winfo_rooty() + (root.winfo_height() - height) // 2
    x = max(0, min(x, root.winfo_screenwidth() - width))
    y = max(0, min(y, root.winfo_screenheight() - height))
    dialog.geometry(f"{width}x{height}+{x}+{y}")
    dialog.update_idletasks()
    # Windows 标题栏和边框会使客户端坐标偏离设定坐标，再按实际位置校正。
    dx = root.winfo_rootx() + root.winfo_width() // 2 - dialog.winfo_rootx() - dialog.winfo_width() // 2
    dy = root.winfo_rooty() + root.winfo_height() // 2 - dialog.winfo_rooty() - dialog.winfo_height() // 2
    dialog.geometry(f"+{dialog.winfo_x() + dx}+{dialog.winfo_y() + dy}")


def build_ui(app):
    root = app.root
    root.title("译声工作台 · Subtitle Studio")
    screen_height = root.winfo_screenheight()
    initial_height = min(960, max(820, screen_height - 80))
    root.geometry(f"1440x{initial_height}")
    # 五列字幕表格在 1050 px 下会压缩编辑列并让播放器变得难用；
    # 设定实际可用的下限，避免控件互相覆盖。
    root.minsize(1180, 760)
    root.configure(bg=PAPER)
    root.columnconfigure(0, minsize=360)
    root.columnconfigure(1, weight=1)
    root.rowconfigure(0, weight=1)
    _style(root)

    rail_host = tk.Frame(root, bg=RAIL, width=360)
    rail_host.grid(row=0, column=0, sticky="nsew")
    rail_host.grid_propagate(False)
    rail_host.rowconfigure(0, weight=1)
    rail_host.columnconfigure(0, weight=1)
    rail_canvas = tk.Canvas(rail_host, bg=RAIL, highlightthickness=0, width=340)
    rail_canvas.grid(row=0, column=0, sticky="nsew")
    rail_scroll = ttk.Scrollbar(rail_host, orient="vertical", command=rail_canvas.yview)
    rail_scroll.grid(row=0, column=1, sticky="ns")
    rail_scroll.grid_remove()
    rail_canvas.configure(yscrollcommand=rail_scroll.set)
    rail = tk.Frame(rail_canvas, bg=RAIL, padx=16, pady=10)
    rail_window = rail_canvas.create_window(0, 0, window=rail, anchor="nw")
    rail_canvas.bind("<Configure>", lambda e: (rail_canvas.itemconfigure(rail_window, width=e.width),
                                               rail_canvas.after_idle(refresh_rail_scroll)))

    def refresh_rail_scroll(_event=None):
        bounds = rail_canvas.bbox(rail_window)
        if bounds is None:
            return
        rail_canvas.configure(scrollregion=bounds)
        content_height = bounds[3] - bounds[1]
        if content_height > rail_canvas.winfo_height() + 1:
            rail_scroll.grid()
            rail_canvas.yview_moveto(0)
        else:
            rail_scroll.grid_remove()
            rail_canvas.yview_moveto(0)

    rail.bind("<Configure>", lambda _e: rail_canvas.after_idle(refresh_rail_scroll))
    work = tk.Frame(root, bg=PAPER, padx=20, pady=16)
    work.grid(row=0, column=1, sticky="nsew")

    _label(rail, "SUBTITLE  /  STUDIO", color=ACCENT, bg=RAIL,
           font=("Consolas", 10, "bold"), anchor="w").pack(fill="x")
    brand_row = tk.Frame(rail, bg=RAIL)
    brand_row.pack(fill="x", pady=(4, 1))
    _label(brand_row, "译声工作台", color=INK, bg=RAIL,
           font=("Microsoft YaHei UI", 18, "bold"), anchor="w").pack(side="left", fill="x", expand=True)
    app.update_button = _subtle_button(brand_row, "检查更新", app.check_updates)
    app.update_button.pack(side="right", padx=(4, 0))
    _divider(rail)

    _section(rail, "01", "导入素材")
    source = tk.Frame(rail, bg=SHEET, padx=12, pady=8,
                      highlightthickness=1, highlightbackground=LINE)
    source.pack(fill="x")
    source_text = _label(source, color=INK, bg=SHEET, font=LABEL_FONT,
                         textvariable=app.source_label, anchor="w", justify="left", wraplength=258)
    source_text.pack(fill="x")
    source_hint = _label(source, "拖入或选择中文、英文、双语 SRT", color=INK_MUTED,
                         bg=SHEET, font=SMALL_FONT, anchor="w", justify="left", wraplength=240)
    source_hint.pack(fill="x", pady=(3, 5))
    source_button = _subtle_button(source, "选择 SRT 文件", app.load)
    source_button.pack(fill="x")
    for drop_widget in (source, source_text, source_hint, source_button):
        app._register_drop(drop_widget, "srt")

    _section(rail, "02", "翻译引擎")
    toggle = tk.Frame(rail, bg=SHEET, padx=3, pady=3)
    toggle.pack(fill="x")
    engine_buttons = {}
    for value, label in (("Ollama", "本地模型"), ("DeepL", "DeepL API")):
        button = tk.Radiobutton(
            toggle, text=label, value=value, variable=app.provider, indicatoron=False,
            command=app.show_result,
            bg=SHEET, fg=INK, selectcolor=MINT,
            activebackground=MINT, activeforeground=INK,
            relief="flat", bd=0, padx=8, pady=6, font=LABEL_FONT,
            cursor="hand2", highlightthickness=0,
        )
        button.pack(side="left", fill="x", expand=True)
        engine_buttons[value] = button
    app._engine_buttons = engine_buttons

    engine_content = tk.Frame(rail, bg=RAIL, height=116)
    engine_content.pack(fill="x", pady=(3, 0))
    engine_content.pack_propagate(False)
    local = tk.Frame(engine_content, bg=RAIL)
    deepl = tk.Frame(engine_content, bg=RAIL)

    def open_model_settings():
        existing = getattr(app, "_model_dialog", None)
        if existing is not None and existing.winfo_exists():
            existing.lift()
            existing.focus_force()
            return
        dialog = tk.Toplevel(root)
        app._model_dialog = dialog
        dialog.title("Ollama 设置 · 译声工作台")
        dialog.minsize(550, 220)
        dialog.configure(bg=PAPER)
        dialog.transient(root)
        shell = tk.Frame(dialog, bg=PAPER, padx=26, pady=22)
        shell.pack(fill="both", expand=True)
        _label(shell, "Ollama 设置", bg=PAPER,
               font=("Microsoft YaHei UI", 18, "bold"), anchor="w").pack(fill="x")
        _label(shell, "填写 Ollama 服务地址，刷新后在主界面选择已安装的模型。",
               color=MUTED, bg=PAPER, font=LABEL_FONT, anchor="w").pack(fill="x", pady=(4, 14))
        _label(shell, "Ollama 地址", color=TEXT, bg=PAPER,
               font=LABEL_FONT, anchor="w").pack(fill="x", pady=(0, 5))
        path_row = tk.Frame(shell, bg=PAPER)
        path_row.pack(fill="x")
        _entry(path_row, textvariable=app.ollama_address).pack(side="left", fill="x", expand=True, ipady=8)
        tk.Button(path_row, text="刷新模型", command=app.refresh_models,
                  bg=INK_SOFT, fg="#FFFFFF", activebackground=INK,
                  activeforeground="#FFFFFF", bd=0, relief="flat", padx=13,
                  pady=8, cursor="hand2", font=LABEL_FONT).pack(side="left", padx=(8, 0))
        _label(shell, "刷新完成后，可在主界面点击“设为默认模型”。",
               color=MUTED, bg=PAPER, font=LABEL_FONT, anchor="w").pack(fill="x", pady=(14, 0))
        _center_dialog(dialog, root, 650, 240)

    _label(local, "用于翻译的 Ollama 模型", color=INK_MUTED, bg=RAIL,
           font=SMALL_FONT, anchor="w").pack(fill="x", pady=(8, 4))
    model_row = tk.Frame(local, bg=RAIL)
    model_row.pack(fill="x")
    app.model = ttk.Combobox(model_row, textvariable=app.model_display,
                             state="readonly", style="Rail.TCombobox", font=LABEL_FONT, height=9, width=24)
    app.model.pack(fill="x")
    app.model.bind("<<ComboboxSelected>>", app.select_model_display)
    local_actions = tk.Frame(local, bg=RAIL)
    local_actions.pack(fill="x", pady=(5, 0))
    _subtle_button(local_actions, "地址与刷新", open_model_settings).pack(side="left", fill="x", expand=True, padx=(0, 4))
    _subtle_button(local_actions, "设为默认模型", app.set_default_model).pack(side="left", fill="x", expand=True, padx=(4, 0))

    _label(deepl, "DeepL API Key", color=INK_MUTED, bg=RAIL,
           font=SMALL_FONT, anchor="w").pack(fill="x", pady=(9, 4))
    deepl_row = tk.Frame(deepl, bg=RAIL)
    deepl_row.pack(fill="x")
    app.key = _entry(deepl_row, show="*")
    app.key.pack(side="left", fill="x", expand=True, ipady=7)
    _subtle_button(deepl_row, "获取", app.show_deepl_help).pack(side="left", padx=(7, 0))
    _label(deepl, "密钥仅用于当前运行", color=INK_MUTED,
           bg=RAIL, font=SMALL_FONT, anchor="w").pack(fill="x", pady=(6, 0))

    app.translate_button = _rail_button(rail, "开始翻译", app.start, primary=True)
    app.translate_button.pack(fill="x", pady=(8, 0))
    _divider(rail)

    _section(rail, "03", "英文配音与导出")
    _label(rail, "参考声音（可选 WAV）", color=INK_MUTED,
           bg=RAIL, font=SMALL_FONT, anchor="w").pack(fill="x", pady=(0, 3))
    reference_row = tk.Frame(rail, bg=RAIL)
    reference_row.pack(fill="x")
    reference_row.columnconfigure(0, weight=1)
    app.reference_entry = _entry(reference_row, textvariable=app.reference_audio)
    app.reference_entry.grid(row=0, column=0, sticky="ew", ipady=7)
    _subtle_button(reference_row, "浏览", app.choose_reference, width=4).grid(
        row=0, column=1, sticky="ew", padx=(4, 0))
    _subtle_button(reference_row, "打开", app.open_reference_audio, width=4).grid(
        row=0, column=2, sticky="ew", padx=(4, 0))
    app._register_drop(app.reference_entry, "wav")

    _label(rail, "输出文件夹", color=INK_MUTED,
           bg=RAIL, font=SMALL_FONT, anchor="w").pack(fill="x", pady=(6, 3))
    output_row = tk.Frame(rail, bg=RAIL)
    output_row.pack(fill="x")
    output_row.columnconfigure(0, weight=1)
    app.output_entry = _entry(output_row, textvariable=app.output_dir)
    app.output_entry.grid(row=0, column=0, sticky="ew", ipady=7)
    _subtle_button(output_row, "浏览", app.choose_output, width=4).grid(
        row=0, column=1, sticky="ew", padx=(4, 0))
    _subtle_button(output_row, "打开", app.open_output_folder, width=4).grid(
        row=0, column=2, sticky="ew", padx=(4, 0))

    _label(rail, "参考声音留空时使用默认统一音色。", color=INK_MUTED,
           bg=RAIL, font=SMALL_FONT, anchor="w", justify="left", wraplength=290).pack(fill="x", pady=(4, 0))
    app.voice_model_button = _subtle_button(rail, "检测配音模型", app.check_voice_model)
    app.voice_model_button.pack(fill="x", pady=(6, 0))
    app.generate_button = _rail_button(rail, "生成双语字幕与英文配音", app.generate_bundle, primary=True)
    app.generate_button.pack(fill="x", pady=(6, 0))

    def refresh_provider():
        local.pack_forget()
        deepl.pack_forget()
        if app.provider.get() == "DeepL":
            deepl.pack(fill="x")
        else:
            local.pack(fill="x")
        for value, button in engine_buttons.items():
            selected = app.provider.get() == value
            button.configure(bg=MINT if selected else SHEET, fg=INK)
    app._ui_refresh_provider = refresh_provider
    refresh_provider()


    work.columnconfigure(0, weight=1)
    work.rowconfigure(2, weight=1, minsize=130)

    feedback = tk.Frame(work, bg=RAIL, padx=12, pady=8)
    feedback.grid(row=0, column=0, sticky="ew", pady=(0, 8))
    feedback.columnconfigure(1, weight=1)
    app.stage_label = _label(feedback, "就绪", bg=RAIL, color=ACCENT,
                             font=("Microsoft YaHei UI", 10, "bold"), width=7, anchor="w")
    app.stage_label.grid(row=0, column=0, sticky="w", padx=(0, 10))
    status_text = _label(feedback, textvariable=app.status, bg=RAIL, color=TEXT,
                         font=LABEL_FONT, anchor="w", justify="left", height=2)
    status_text.grid(row=0, column=1, sticky="ew")
    status_text.bind("<Configure>", lambda e: status_text.configure(wraplength=max(200, e.width-8)))
    app.progress_label = _label(feedback, "0%", bg=RAIL, color=ACCENT, font=("Consolas", 11, "bold"), width=5)
    app.progress_label.grid(row=0, column=2, padx=(12, 0))
    stop_slot = tk.Frame(feedback, bg=RAIL)
    stop_slot.grid(row=0, column=3, padx=(8, 0))
    app.stop_button = _subtle_button(stop_slot, "停止", app.stop_task)
    stop_slot.configure(width=app.stop_button.winfo_reqwidth() * 2,
                        height=app.stop_button.winfo_reqheight())
    stop_slot.pack_propagate(False)
    app.stop_button.pack(fill="both", expand=True)
    app.stop_button.configure(state="disabled", bg="#B83A32", fg="#FFFFFF",
                              activebackground="#922B25", activeforeground="#FFFFFF",
                              disabledforeground="#F2D7D5", highlightbackground="#B83A32",
                              highlightcolor="#922B25")
    app.auto_voice_banner = tk.Frame(feedback, bg="#FFF0D6", padx=10, pady=7,
                                     highlightthickness=1, highlightbackground="#E2A23A")
    app.auto_voice_banner.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(6, 0))
    app.auto_voice_banner.columnconfigure(0, weight=1)
    _label(app.auto_voice_banner, textvariable=app.auto_voice_countdown,
           bg="#FFF0D6", color="#805000",
           font=("Microsoft YaHei UI", 11, "bold"), anchor="w").grid(
               row=0, column=0, sticky="ew")
    app.auto_voice_cancel_button = tk.Button(
        app.auto_voice_banner, text="取消自动生成",
        command=app.cancel_auto_voice_countdown, bg="#B83A32", fg="#FFFFFF",
        activebackground="#922B25", activeforeground="#FFFFFF",
        relief="flat", bd=0, padx=12, pady=5, cursor="hand2",
        font=("Microsoft YaHei UI", 10, "bold"),
    )
    app.auto_voice_cancel_button.grid(row=0, column=1, padx=(10, 0))
    app.auto_voice_banner.grid_remove()
    app.progress = ttk.Progressbar(feedback, variable=app.progress_value, maximum=100,
                                  style="Studio.Horizontal.TProgressbar")
    app.progress.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(6, 0))

    list_header = tk.Frame(work, bg=PAPER)
    list_header.grid(row=1, column=0, sticky="ew", pady=(0, 6))
    _label(list_header, "01 / 逐句对照", font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
    app.rows_summary = tk.StringVar(value="等待导入字幕")
    _label(list_header, textvariable=app.rows_summary, color=MUTED, font=LABEL_FONT).pack(side="right")

    list_wrap = tk.Frame(work, bg=SHEET, highlightthickness=1, highlightbackground=LINE)
    list_wrap.grid(row=2, column=0, sticky="nsew")
    columns = ("number", "time", "chinese", "original_english", "english")
    app.table = ttk.Treeview(list_wrap, columns=columns, show="headings", height=5,
                            selectmode="browse", style="Studio.Treeview")
    for column, label in zip(columns, ("序号", "时间码", "中文原文", "英文原文", "英文译文")):
        app.table.heading(column, text=label, anchor="w" if column not in ("number", "time") else "center")
    app.table.column("number", width=58, minwidth=58, stretch=False, anchor="center")
    app.table.column("time", width=250, minwidth=250, stretch=False, anchor="center")
    for column in columns[2:]:
        app.table.column(column, width=230, minwidth=150, stretch=True)
    scrollbar = ttk.Scrollbar(list_wrap, orient="vertical", command=app.table.yview,
                              style="Studio.Vertical.TScrollbar")
    horizontal = ttk.Scrollbar(list_wrap, orient="horizontal", command=app.table.xview)
    app.table.configure(yscrollcommand=scrollbar.set, xscrollcommand=horizontal.set)
    list_wrap.rowconfigure(0, weight=1)
    list_wrap.columnconfigure(0, weight=1)
    app.table.grid(row=0, column=0, sticky="nsew")
    scrollbar.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    app.table.tag_configure("playing", background="#DCECF6", foreground=ACCENT)
    app.table.tag_configure("odd", background="#F7FAFB")
    app.table.tag_configure("even", background=SHEET)
    app.table.bind("<Double-1>", app.edit_cell)
    empty = _label(list_wrap, "导入 SRT，开始逐句校对\n\n原始字幕与生成译文将分别显示", color=MUTED, bg=SHEET, font=BODY_FONT)
    empty.place(relx=0.5, rely=0.5, anchor="center")

    def set_single_selection(row):
        selected = app.table.selection()
        if selected:
            app.table.selection_remove(*selected)
        if row is not None and app.table.exists(row):
            app.table.selection_set(row)

    app._ui_set_selection = set_single_selection

    detail_head = tk.Frame(work, bg=PAPER)
    detail_head.grid(row=3, column=0, sticky="ew", pady=(8, 6))
    _label(detail_head, "02 / 当前字幕", font=("Microsoft YaHei UI", 12, "bold")).pack(side="left")
    _label(detail_head, "可直接输入，点击保存生效。", color=MUTED,
           font=SMALL_FONT).pack(side="left", padx=(10, 0))
    app.detail_meta = tk.StringVar(value="选择一条字幕，查看完整内容")
    _label(detail_head, textvariable=app.detail_meta, font=MONO_FONT, color=MUTED).pack(side="left", padx=12)

    def move_row(step):
        rows = app.table.get_children()
        if not rows:
            return
        selection = app.table.selection()
        index = rows.index(selection[0]) if selection else 0
        row = rows[max(0, min(len(rows)-1, index+step))]
        set_single_selection(row)
        app.table.focus(row)
        app.table.see(row)
    _subtle_button(detail_head, "下一条", lambda: move_row(1)).pack(side="right", padx=(6, 0))
    _subtle_button(detail_head, "上一条", lambda: move_row(-1)).pack(side="right")

    detail = tk.Frame(work, bg=PAPER, height=142)
    detail.grid(row=4, column=0, sticky="ew")
    detail.grid_propagate(False)
    detail.rowconfigure(0, weight=1)
    for i in range(3):
        detail.columnconfigure(i, weight=1, uniform="detail")

    app.sentence_preview_buttons = []

    def text_panel(index, title, caption, editable=False):
        card = tk.Frame(detail, bg=SHEET, padx=10, pady=9,
                        highlightthickness=0, bd=0)
        card.grid(row=0, column=index, sticky="nsew", padx=(0 if index==0 else 4, 0 if index==2 else 4))
        card.columnconfigure(0, weight=1)
        panel_header = tk.Frame(card, bg=SHEET)
        panel_header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 5))
        _label(panel_header, title, bg=SHEET, color=ACCENT if editable else INK,
               font=("Microsoft YaHei UI", 11, "bold"), anchor="w").pack(side="left")
        preview_button = None
        if index == 2:
            preview_button = _subtle_button(panel_header, "试听配音", lambda: app.preview_sentence(index))
            preview_button.pack(side="right")
            preview_button.configure(state="disabled")
        app.sentence_preview_buttons.append(preview_button)
        content_row = 1
        if caption:
            _label(card, caption, bg=SHEET, color=MUTED, font=SMALL_FONT,
                   anchor="w").grid(row=1, column=0, columnspan=2, sticky="ew", pady=(2, 6))
            content_row = 2
        card.rowconfigure(content_row, weight=1)
        field = tk.Text(card, width=1, height=3, wrap="word", relief="flat", bd=0,
                        bg=SHEET, fg=TEXT, insertbackground=ACCENT, selectbackground=MINT,
                        font=BODY_FONT, spacing1=2, spacing3=4, undo=editable,
                        highlightthickness=1, highlightbackground=ACCENT, highlightcolor=ACCENT)
        field.grid(row=content_row, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(card, orient="vertical", command=field.yview)
        scroll.grid(row=content_row, column=1, sticky="ns")
        # 保留窄边距，防止滚动条显隐改变换行宽度而反复闪烁。
        card.columnconfigure(1, minsize=scroll.winfo_reqwidth())

        def update_scrollbar(first, last):
            scroll.set(first, last)
            if float(first) <= 0.000001 and float(last) >= 0.999999:
                scroll.grid_remove()
            else:
                scroll.grid()

        field.configure(yscrollcommand=update_scrollbar, state="disabled")
        return field

    chinese_view = text_panel(0, "中文原文", "", True)
    english_view = text_panel(1, "英文原文", "", True)
    editor = text_panel(2, "英文译文", "", True)
    app.detail_editor = editor

    player = tk.Frame(work, bg=RAIL, padx=12, pady=8)
    player.grid(row=5, column=0, sticky="ew", pady=(8, 0))
    player.columnconfigure(0, weight=1)
    player_top = tk.Frame(player, bg=RAIL)
    player_top.grid(row=0, column=0, sticky="ew")
    _label(player_top, "03 / 配音试听", bg=RAIL, font=("Microsoft YaHei UI", 11, "bold")).pack(side="left")
    _subtle_button(player_top, "打开配音", app.open_audio_preview).pack(side="left", padx=(12, 6))
    _subtle_button(player_top, "打开输出目录", app.open_preview_folder).pack(side="left")
    # 文件名占用剩余空间，不挤压文件操作按钮。
    _label(player_top, textvariable=app.audio_name, bg=RAIL, color=MUTED,
           font=SMALL_FONT, anchor="w", width=1).pack(side="left", fill="x", expand=True, padx=12)
    transport = tk.Frame(player, bg=RAIL)
    transport.grid(row=1, column=0, sticky="ew", pady=(6, 0))
    transport.columnconfigure(2, weight=1)
    _subtle_button(transport, "播放", app.toggle_audio_preview, width=4,
                   textvariable=app.audio_play_label).grid(row=0, column=0, padx=(0, 6))
    _subtle_button(transport, "停止", app.stop_audio_preview, width=4).grid(row=0, column=1)
    app.audio_slider = ttk.Scale(transport, from_=0, to=1, variable=app.audio_position, orient="horizontal")
    app.audio_slider.grid(row=0, column=2, sticky="ew", padx=12)
    app.audio_slider.bind("<ButtonPress-1>", app._audio_seek_start)
    app.audio_slider.bind("<ButtonRelease-1>", app._audio_seek_end)
    _label(transport, textvariable=app.audio_time_label, bg=RAIL,
           font=MONO_FONT, width=13, anchor="e").grid(row=0, column=3)

    fields = (chinese_view, english_view, editor)
    app.detail_fields = fields
    editing = {"item": None, "index": None, "provider": None, "snapshot": ("", "", ""), "loading": False}
    edit_status = tk.StringVar(value="未选择字幕")

    def contents():
        return tuple(field.get("1.0", "end-1c").strip() for field in fields)

    def dirty():
        return editing["item"] is not None and contents() != editing["snapshot"]

    app._ui_has_unsaved_edits = dirty

    def refresh_edit_state():
        enabled = editing["item"] is not None and not app.busy
        has_aligned_audio = (app.audio_player.path is not None and
                             len(getattr(app, "_preview_positions", [])) == len(app.items))
        for button in app.sentence_preview_buttons:
            if button is not None:
                button.configure(state="normal" if enabled and has_aligned_audio else "disabled")
        for field in fields:
            field.configure(state="normal" if enabled else "disabled")
        save_button.configure(state="normal" if enabled and dirty() else "disabled")

    def save_edits():
        if app.busy or editing["item"] is None:
            return False
        zh, original, translated = contents()
        changed = (zh, original, translated) != editing["snapshot"]
        item = editing["item"]
        index = editing["index"]
        provider = editing["provider"]
        source_changed = zh != editing["snapshot"][0]
        translation_changed = translated != editing["snapshot"][2]
        if source_changed:
            for other_provider, other_values in app.results.items():
                if other_provider != provider and other_values is not None:
                    other_values[index] = ""
            if not translation_changed:
                translated = ""
                editing["loading"] = True
                fields[2].delete("1.0", "end")
                editing["loading"] = False
        item["text"] = zh
        item["original_english"] = original
        values = app.results[provider]
        if values is not None or translation_changed:
            if values is None:
                values = [""] * len(app.items)
                app.results[provider] = values
            values[index] = translated
        if app.table.exists(str(index)):
            app.table.set(str(index), "chinese", zh.replace("\n", " "))
            app.table.set(str(index), "original_english", original.replace("\n", " "))
            if app.active == provider:
                app.table.set(str(index), "english", translated.replace("\n", " "))
        editing["snapshot"] = (zh, original, translated)
        if changed and app.audio_player.path is not None:
            app._clear_loaded_audio()
        edit_status.set("已保存到当前任务")
        if source_changed:
            app.status.set(f"第 {index+1} 条中文已修改；未同步修改的旧译文已清空，请重新翻译或补写英文。")
        else:
            app.status.set(f"第 {index+1} 条修改已保存到当前任务；后续翻译和配音使用保存后的内容。")
        refresh_edit_state()
        return True

    def confirm_edits():
        if not dirty():
            return True
        answer = messagebox.askyesnocancel("保存修改", "当前字幕有未保存的修改。是否保存到当前任务？\n\n是：保存修改；否：放弃修改；取消：继续编辑。", parent=root)
        if answer is None:
            return False
        if answer:
            return save_edits()
        editing["loading"] = True
        for field, value in zip(fields, editing["snapshot"]):
            field.delete("1.0", "end")
            field.insert("1.0", value)
            field.edit_modified(False)
        editing["loading"] = False
        edit_status.set("已放弃修改")
        refresh_edit_state()
        return True

    save_button = _subtle_button(detail_head, "保存修改", save_edits)
    save_button.pack(side="right", padx=(0, 8))
    _label(detail_head, textvariable=edit_status, color=ACCENT, font=SMALL_FONT).pack(side="right", padx=8)
    app.save_edits_button = save_button
    app._ui_confirm_edits = confirm_edits
    app._ui_refresh_edit_state = refresh_edit_state

    def select_row(_event=None):
        selection = app.table.selection()
        row = selection[0] if selection else None
        item = app.items[int(row)] if row is not None and app.items else None
        same = item is editing["item"] and app.active == editing["provider"]
        if not same:
            app._stop_sentence_preview()
        if dirty():
            if same:
                refresh_edit_state()
                return
            if not confirm_edits():
                old = str(editing["index"])
                if app.table.exists(old):
                    set_single_selection(old)
                    app.table.see(old)
                return
        editing["loading"] = True
        index = int(row) if item is not None else None
        values = app.results[app.active]
        data = (item["text"], item.get("original_english", ""), values[index] if values is not None else "") if item is not None else ("", "", "")
        for field, value in zip(fields, data):
            field.configure(state="normal")
            field.delete("1.0", "end")
            field.insert("1.0", value)
            field.edit_reset()
            field.edit_modified(False)
        editing.update(item=item, index=index, provider=app.active, snapshot=contents(), loading=False)
        app.detail_meta.set(f"{index+1} / {len(app.items)}" if item is not None else "选择字幕")
        edit_status.set("可编辑" if item is not None else "未选择字幕")
        refresh_edit_state()

    def edited(event=None):
        if editing["loading"] or app.busy:
            return
        if event is not None:
            if not event.widget.edit_modified():
                return
            event.widget.edit_modified(False)
        edit_status.set("未保存" if dirty() else "可编辑")
        if dirty() and getattr(app, "auto_voice_pending", False):
            app._cancel_auto_voice()
        refresh_edit_state()

    def focus_cell(event):
        column = app.table.identify_column(event.x)
        if column in ("#3", "#4", "#5") and app.table.identify("region", event.x, event.y) == "cell":
            fields[int(column[1:])-3].focus_set()
        return "break"
    app.table.bind("<Double-1>", focus_cell)
    root.bind("<Control-s>", lambda _event: save_edits())

    def update_empty():
        if app.items:
            empty.place_forget()
            if not app.table.selection():
                rows = app.table.get_children()
                if rows:
                    set_single_selection(rows[0])
                    app.table.focus(rows[0])
        else:
            empty.place(relx=0.5, rely=0.5, anchor="center")

    def bind_rail_wheel(widget):
        if not isinstance(widget, ttk.Combobox):
            widget.bind("<MouseWheel>", lambda e: rail_canvas.yview_scroll(-int(e.delta/120), "units"), add="+")
        for child in widget.winfo_children():
            bind_rail_wheel(child)
    bind_rail_wheel(rail)
    app.table.bind("<<TreeviewSelect>>", select_row)
    for field in fields:
        field.bind("<<Modified>>", edited)
    app._ui_select_row = select_row
    app._ui_update_empty = update_empty
    root.bind("<Control-o>", lambda _event: app.load())
    select_row()
