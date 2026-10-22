"""Tk widgets are accessed exclusively by the main thread."""
from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from collections import Counter
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

import settings_manager
from extractors import load_book
from lmstudio import TRANSLATION_PROMPT
from settings_manager import Settings, PromptStore, load_settings, save_settings
from translator import Job, TranslationEngine, matching_saved_book, parse_chunk_selection, saved_books, should_skip
from quality_gui import QualityWindows
from novel_analysis import exclusion_count
from providers import create_client, provider_label


def queue_view(rows: list[dict], status: str = "All", review: str = "All",
               bookmarks_only: bool = False, query: str = "", sort: str = "#0",
               descending: bool = False) -> list[int]:
    """Return visible chunk IDs without changing translation or selection state."""
    query = query.strip().casefold()
    visible = [row for row in rows
               if (status == "All" or row["status"] == status)
               and (review == "All" or (row["check"] or "Not checked") == review)
               and (not bookmarks_only or row["bookmark"])
               and (not query or query in " ".join(str(value) for value in row.values()).casefold())]
    visible.sort(key=lambda row: row["index"])
    def key(row):
        if sort == "#0":
            return row["index"]
        if sort == "bookmark":
            return bool(row["bookmark"])
        return str(row[sort]).casefold()
    visible.sort(key=key, reverse=descending)
    return [row["index"] for row in visible]


class TranslatorGUI:
    def __init__(self, root: tk.Tk, data_directory: Path):
        self.root = root
        self.data_directory = data_directory
        self.settings_path = data_directory / "settings.json"
        self.events: queue.Queue = queue.Queue()
        self.job: Job | None = None
        self.engine: TranslationEngine | None = None
        self.busy = False
        self.closing = False
        self.statuses: dict[int, str] = {}
        self.details: dict[int, str] = {}
        self.check_details: dict[int, str] = {}
        self.analysis_excluded = 0
        self.previews: dict[int, str] = {}
        self.bookmarks: dict[int, str] = {}
        self.queue_sort = "#0"
        self.queue_descending = False
        self.queue_refresh_after = None
        self.quality_windows = QualityWindows(self)
        try:
            settings = load_settings(self.settings_path)
        except (OSError, ValueError, TypeError) as exc:
            logging.getLogger(__name__).exception("Settings could not be loaded")
            settings = Settings()
            messagebox.showwarning("Settings", f"Using defaults: {exc}", parent=root)
        self.values = {name: tk.StringVar(value=str(getattr(settings, name)))
                       for name in ("model", "input_lang", "output_lang", "max_workers",
                                    "retries", "timeout", "api_key")}
        self.start_lm_studio = tk.BooleanVar(value=False)
        self.lmstudio_path = settings.lmstudio_path
        self.proofread = tk.BooleanVar(value=settings.proofread)
        self.analyze_novel = tk.BooleanVar(value=settings.analyze_novel)
        self.active_provider = settings.provider
        self.provider = tk.StringVar(value=provider_label(settings.provider))
        self.server_urls = {"lmstudio":settings.lmstudio_url, "ollama":settings.ollama_url}
        self.server_url = tk.StringVar(value=self.server_urls[settings.provider])
        self.provider_models = {settings.provider:settings.model}
        self.provider_keys = {settings.provider:settings.api_key}
        self.discovery_generation = 0
        self.use_custom_prompt = tk.BooleanVar(value=settings.use_custom_prompt)
        self.custom_prompt = settings.custom_prompt
        prompt_path = data_directory / "prompts.json"
        had_library = prompt_path.exists()
        try:
            self.prompt_store = PromptStore(prompt_path, TRANSLATION_PROMPT)
            if not had_library and settings.use_custom_prompt and settings.custom_prompt.strip():
                self.prompt_store.create("Imported custom prompt", settings.custom_prompt)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showwarning("Prompt library", f"Saved prompts could not be loaded: {exc}\n"
                                   "The existing file was kept; the default remains available.", parent=root)
            self.prompt_store = PromptStore(prompt_path, TRANSLATION_PROMPT, read_only=True)
        self.prompt_selection = tk.StringVar()
        self.book_prompt_text = ""
        self.saved_selection = tk.StringVar()
        self.saved_choices = {}
        self.source = tk.StringVar(value=settings_manager.last_input_file)
        self.summary = tk.StringVar(value="Choose a book and a local model to begin.")
        self.connection = tk.StringVar(value=f"{provider_label(settings.provider)} · connecting")
        self.model_help = tk.StringVar(value="Looking for your local models…")
        self.model_selection = tk.StringVar(value=settings.model)
        self.model_choices: dict[str, str] = {}
        self.models_loading = False
        self._build()
        self._refresh_prompt_choices()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._poll)
        self.root.after(200, self.refresh_models)
        self.root.after(250, self.refresh_saved_books)

    def _style(self) -> None:
        palette = {"background": "#eef2f6", "surface": "#ffffff", "ink": "#17283b",
                   "muted": "#63758a", "border": "#d9e2eb", "accent": "#087f8c",
                   "navy": "#152b42", "selected": "#dceff1"}
        self.palette = palette
        style = ttk.Style(self.root)
        style.theme_use("clam")
        family = tkfont.nametofont("TkDefaultFont").actual("family")
        tkfont.nametofont("TkDefaultFont").configure(size=10)
        style.configure(".", font=(family, 10), foreground=palette["ink"],
                        background=palette["background"])
        style.configure("TFrame", background=palette["background"])
        style.configure("Card.TFrame", background=palette["surface"])
        style.configure("Header.TFrame", background=palette["navy"])
        style.configure("Icon.TLabel", background=palette["navy"])
        style.configure("TLabel", background=palette["background"])
        style.configure("Card.TLabel", background=palette["surface"])
        style.configure("Muted.TLabel", foreground=palette["muted"], background=palette["surface"])
        style.configure("Section.TLabel", font=(family, 11, "bold"), background=palette["surface"])
        style.configure("Title.TLabel", font=(family, 23, "bold"), foreground="#ffffff",
                        background=palette["navy"])
        style.configure("Subtitle.TLabel", foreground="#b9cbdc", background=palette["navy"])
        style.configure("Badge.TLabel", foreground="#87e0d4", background=palette["navy"],
                        padding=(14, 8))
        style.configure("Count.TLabel", font=(family, 19, "bold"), background=palette["surface"])
        style.configure("TButton", padding=(12, 9), background="#e7eef4", borderwidth=0)
        style.map("TButton", background=[("active", "#d7e4ed"), ("disabled", "#edf1f5")],
                  foreground=[("disabled", "#98a5b3")])
        style.configure("Primary.TButton", background=palette["accent"], foreground="#ffffff")
        style.map("Primary.TButton", background=[("disabled", "#b4cbd0"), ("active", "#066b76")],
                  foreground=[("disabled", "#f5f8fa")])
        style.configure("Danger.TButton", foreground="#a63f46")
        for name in ("TEntry", "TCombobox", "TSpinbox"):
            style.configure(name, fieldbackground="#f7f9fc", bordercolor=palette["border"],
                            lightcolor=palette["border"], darkcolor=palette["border"], padding=7)
            style.map(name, bordercolor=[("focus", palette["accent"])],
                      fieldbackground=[("disabled", "#edf1f5")])
        style.configure("TCheckbutton", background=palette["surface"], padding=(0, 4))
        style.map("TCheckbutton", background=[("active", palette["surface"])])
        style.configure("Treeview", background=palette["surface"], fieldbackground=palette["surface"],
                        rowheight=34, borderwidth=0, font=(family, 10))
        style.configure("Treeview.Heading", background="#eef3f7", foreground=palette["muted"],
                        font=(family, 9, "bold"), padding=(8, 9), relief="flat")
        style.map("Treeview", background=[("selected", palette["selected"])],
                  foreground=[("selected", palette["ink"])])
        style.configure("Horizontal.TProgressbar", background=palette["accent"],
                        troughcolor="#e5edf3", borderwidth=0, thickness=7)
        self.root.configure(background=palette["background"])
        self.root.option_add("*TCombobox*Listbox.font", (family, 10))
        self.root.option_add("*TCombobox*Listbox.background", palette["surface"])
        self.root.option_add("*TCombobox*Listbox.selectBackground", palette["accent"])

    def _build(self) -> None:
        self.root.title("Literary Translator | Local workspace")
        try:
            icon_path = Path(__file__).resolve().parent / "assets" / "app_icon_64.png"
            self.app_icon = tk.PhotoImage(file=str(icon_path))
            self.root.iconphoto(True, self.app_icon)
        except (OSError, tk.TclError):
            logging.getLogger(__name__).warning("Application icon could not be loaded", exc_info=True)
        self.root.geometry("1360x900")
        self.root.minsize(1100, 760)
        self._style()
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(24, 18))
        header.pack(fill="x")
        if hasattr(self, "app_icon"):
            ttk.Label(header, image=self.app_icon, style="Icon.TLabel").pack(side="left", padx=(0, 16))
        heading = ttk.Frame(header, style="Header.TFrame")
        heading.pack(side="left")
        ttk.Label(heading, text="Literary Translator", style="Title.TLabel").pack(anchor="w")
        ttk.Label(heading, text="Your books. Your model. An entirely local workflow.",
                  style="Subtitle.TLabel").pack(anchor="w", pady=(5, 0))
        ttk.Label(header, textvariable=self.connection, style="Badge.TLabel").pack(side="right")
        body = ttk.Frame(self.root, padding=18)
        body.pack(fill="both", expand=True)
        body.columnconfigure(1, weight=1)
        body.rowconfigure(0, weight=1)
        sidebar = ttk.Frame(body, style="Card.TFrame", width=330)
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        sidebar.grid_propagate(False)
        canvas = tk.Canvas(sidebar, background=self.palette["surface"],
                           highlightthickness=0, width=310)
        scroll = ttk.Scrollbar(sidebar, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        settings = ttk.Frame(canvas, style="Card.TFrame", padding=16)
        settings.columnconfigure(0, weight=1)
        window = canvas.create_window(0, 0, window=settings, anchor="nw")
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        settings.bind("<Configure>", lambda event: canvas.configure(scrollregion=canvas.bbox("all")))
        self._settings_panel(settings)
        self._bind_settings_scroll(settings, canvas)
        workspace = ttk.Frame(body)
        workspace.grid(row=0, column=1, sticky="nsew")
        self._workspace(workspace)
        self._controls(False)

    def _bind_settings_scroll(self, widget, canvas: tk.Canvas) -> None:
        def wheel(event):
            if canvas.yview() == (0.0, 1.0):
                return None
            direction = -1 if getattr(event, "num", None) == 4 or event.delta > 0 else 1
            canvas.yview_scroll(direction * 2, "units")
            return "break"
        # Bind just the settings widgets, not global wheel events; previews retain
        # their independent scrolling and no bindings survive this window.
        for sequence in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
            widget.bind(sequence, wheel, add="+")
        for child in widget.winfo_children():
            self._bind_settings_scroll(child, canvas)

    def _settings_panel(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="TRANSLATION SETUP", style="Section.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 18))
        ttk.Label(parent, text="Source book", style="Card.TLabel").grid(row=1, column=0, sticky="w")
        self.source_entry = ttk.Entry(parent, textvariable=self.source, width=25)
        self.source_entry.grid(row=2, column=0, sticky="ew", pady=(5, 7))
        self.browse = ttk.Button(parent, text="Choose EPUB, PDF, DOCX or TXT", command=self.choose_file)
        self.browse.grid(row=3, column=0, sticky="ew")
        self.open_button = ttk.Button(parent, text="Open book / recognize saved work", command=self.open_book)
        self.open_button.grid(row=4, column=0, sticky="ew", pady=(6, 12))
        ttk.Label(parent, text="Saved books", style="Card.TLabel").grid(row=5, column=0, sticky="w")
        self.saved_combo = ttk.Combobox(parent, textvariable=self.saved_selection, state="readonly", width=25)
        self.saved_combo.grid(row=6, column=0, sticky="ew", pady=(5, 6))
        self.open_saved_button = ttk.Button(parent, text="Open saved book", command=self.open_saved_book)
        self.open_saved_button.grid(row=7, column=0, sticky="ew")
        model_heading = ttk.Frame(parent, style="Card.TFrame")
        model_heading.grid(row=8, column=0, sticky="ew", pady=(20, 5))
        model_heading.columnconfigure(0, weight=1)
        ttk.Label(model_heading, text="AI provider", style="Card.TLabel").grid(row=0, column=0, sticky="w")
        self.provider_combo = ttk.Combobox(model_heading, textvariable=self.provider, values=("LM Studio", "Ollama"), state="readonly")
        self.provider_combo.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(5, 10))
        self.provider_combo.bind("<<ComboboxSelected>>", self._provider_selected)
        ttk.Label(model_heading, text="Server URL", style="Card.TLabel").grid(row=2, column=0, sticky="w")
        self.server_entry = ttk.Entry(model_heading, textvariable=self.server_url)
        self.server_entry.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(5, 10))
        self.server_entry.bind("<Return>", self._server_changed)
        self.server_entry.bind("<FocusOut>", self._server_changed)
        ttk.Label(model_heading, text="Model", style="Card.TLabel").grid(row=4, column=0, sticky="w")
        self.refresh_button = ttk.Button(model_heading, text="Refresh", command=self.refresh_models)
        self.refresh_button.grid(row=4, column=1, sticky="e")
        self.model_combo = ttk.Combobox(parent, textvariable=self.model_selection, width=25,
                                        height=12, state="normal")
        self.model_combo.grid(row=9, column=0, sticky="ew")
        self.model_combo.bind("<<ComboboxSelected>>", self._model_selected)
        launch_row = ttk.Frame(parent, style="Card.TFrame")
        launch_row.grid(row=23, column=0, sticky="ew", pady=(18, 0))
        self.launch_option = ttk.Checkbutton(launch_row, text="Start LM Studio on app launch",
                                             variable=self.start_lm_studio, command=self.save_launch_preferences)
        self.launch_option.pack_forget()
        self.launch_path_button = ttk.Button(launch_row, text="Set LM Studio path…", command=self.choose_studio_path)
        self.launch_path_button.pack_forget()
        ttk.Label(parent, textvariable=self.model_help, style="Muted.TLabel", wraplength=275).grid(
            row=10, column=0, sticky="w", pady=(7, 15))
        fields = ttk.Frame(parent, style="Card.TFrame")
        fields.grid(row=11, column=0, sticky="ew")
        self.option_widgets = [self.model_combo]
        for column, (name, label) in enumerate((("input_lang", "Source language"),
                                               ("output_lang", "Target language"))):
            fields.columnconfigure(column, weight=1, uniform="languages")
            ttk.Label(fields, text=label, style="Card.TLabel").grid(row=0, column=column, sticky="w")
            entry = ttk.Entry(fields, textvariable=self.values[name], width=10)
            entry.grid(row=1, column=column, sticky="ew", padx=(0, 8) if column == 0 else 0, pady=(5, 0))
            self.option_widgets.append(entry)
        ttk.Label(parent, text="Processing", style="Section.TLabel").grid(
            row=12, column=0, sticky="w", pady=(22, 8))
        processing = ttk.Frame(parent, style="Card.TFrame")
        processing.grid(row=13, column=0, sticky="ew")
        for column, name in enumerate(("max_workers", "retries", "timeout")):
            processing.columnconfigure(column, weight=1, uniform="processing")
            label = {"max_workers": "Workers", "retries": "Retries", "timeout": "Timeout (s)"}[name]
            ttk.Label(processing, text=label, style="Muted.TLabel").grid(row=0, column=column, sticky="w")
            entry = ttk.Entry(processing, textvariable=self.values[name], width=5)
            entry.grid(row=1, column=column, sticky="ew", pady=(5, 0), padx=(0, 6))
            self.option_widgets.append(entry)
        ttk.Label(parent, text="API key (optional)", style="Muted.TLabel").grid(
            row=14, column=0, sticky="w", pady=(14, 5))
        key = ttk.Entry(parent, textvariable=self.values["api_key"], show="•")
        key.grid(row=15, column=0, sticky="ew")
        self.option_widgets.append(key)
        checks = ttk.Frame(parent, style="Card.TFrame")
        checks.grid(row=16, column=0, sticky="ew", pady=(15, 10))
        self.analysis_option = ttk.Checkbutton(checks, text="Analyze novel before translation",
                                              variable=self.analyze_novel, command=self.save_analysis_preference)
        self.analysis_option.pack(anchor="w", pady=(0, 8))
        self.check_option = ttk.Checkbutton(checks, text="Proofread after translation", variable=self.proofread)
        self.check_option.pack(anchor="w")
        ttk.Separator(parent).grid(row=17, column=0, sticky="ew", pady=8)
        ttk.Label(parent, text="Chunks shorter than 4 characters and punctuation-only chunks are skipped automatically.",
                  style="Muted.TLabel", wraplength=270).grid(row=18, column=0, sticky="w", pady=(5, 0))

        ttk.Label(parent, text="Translation prompt", style="Section.TLabel").grid(
            row=19, column=0, sticky="w", pady=(18, 6))
        self.prompt_combo = ttk.Combobox(parent, textvariable=self.prompt_selection, state="readonly", width=25)
        self.prompt_combo.grid(row=20, column=0, sticky="ew")
        self.prompt_combo.bind("<<ComboboxSelected>>", self._prompt_selected)
        actions = ttk.Frame(parent, style="Card.TFrame")
        actions.grid(row=21, column=0, sticky="ew", pady=(8, 0))
        self.prompt_buttons = {}
        for name, command in (("New", lambda: self.edit_prompt(new=True)),
                              ("Edit", self.edit_prompt), ("Delete", self.delete_prompt)):
            button = ttk.Button(actions, text=name, command=command)
            button.pack(side="left", expand=True, fill="x", padx=(0, 4))
            self.prompt_buttons[name] = button
        ttk.Label(parent, text="Selected prompt is used for new and retranslated chunks. Incomplete or unclear text should return empty output.",
                  style="Muted.TLabel", wraplength=270).grid(row=22, column=0, sticky="w", pady=(8, 0))

    def _workspace(self, parent: ttk.Frame) -> None:
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", pady=(0, 12))
        self.buttons = {}
        actions = (("Start / load", "Start translation", self.start, "Primary.TButton"),
                   ("Pause", "Pause", self.pause, "TButton"),
                   ("Resume", "Resume", self.resume, "TButton"),
                   ("Cancel", "Cancel", self.cancel, "Danger.TButton"),
                   ("Retranslate chunk", "Retranslate selected", self.retranslate, "TButton"),
                   ("Export", "Export book", self.export, "TButton"))
        for name, label, command, style in actions:
            button = ttk.Button(toolbar, text=label, command=command, style=style)
            button.pack(side="left", padx=(0, 5))
            self.buttons[name] = button
        stats = ttk.Frame(parent, style="Card.TFrame", padding=(12, 10))
        stats.pack(fill="x", pady=(0, 12))
        self.count_vars = {}
        for column, name in enumerate(("Pending", "Completed", "Resumed", "Retranslated", "Error", "Retry", "Skipped")):
            stats.columnconfigure(column, weight=1, uniform="counts")
            self.count_vars[name] = tk.StringVar(value="0")
            ttk.Label(stats, textvariable=self.count_vars[name], style="Count.TLabel").grid(
                row=0, column=column, sticky="w", padx=4)
            ttk.Label(stats, text=name, style="Muted.TLabel").grid(row=1, column=column, sticky="w", padx=4)
        self.progress = ttk.Progressbar(parent, mode="determinate")
        self.progress.pack(fill="x")
        self.summary_label = ttk.Label(parent, textvariable=self.summary, foreground=self.palette["muted"])
        self.summary_label.pack(fill="x", pady=(7, 12))
        self.summary_label.bind("<Configure>", lambda event: self.summary_label.configure(wraplength=max(200, event.width - 8)))
        panes = ttk.Panedwindow(parent, orient="vertical")
        panes.pack(fill="both", expand=True)
        list_card = ttk.Frame(panes, style="Card.TFrame", padding=12)
        queue_header = ttk.Frame(list_card, style="Card.TFrame")
        queue_header.pack(fill="x", pady=(0, 10))
        ttk.Label(queue_header, text="Translation queue", style="Section.TLabel").pack(side="left")
        self.buttons["Choose chunks"] = ttk.Button(queue_header, text="Select by number…",
                                                   command=self.choose_chunks)
        self.buttons["Choose chunks"].pack(side="right")
        self.buttons["Edit chunk"] = ttk.Button(queue_header, text="Edit chunk…", command=self.edit_chunk)
        self.buttons["Edit chunk"].pack(side="right", padx=8)
        self.buttons["Search chunks"] = ttk.Button(queue_header, text="Search…", command=self.search_chunks)
        self.buttons["Search chunks"].pack(side="right")
        bookmark_actions = ttk.Frame(list_card, style="Card.TFrame")
        bookmark_actions.pack(fill="x", pady=(0, 8))
        for name, label, command in (("Bookmark", "Bookmark selected", self.bookmark_selected),
                                     ("Remove bookmark", "Remove bookmark", lambda: self.bookmark_selected(remove=True)),
                                     ("Bookmarks", "Bookmarks…", self.show_bookmarks)):
            self.buttons[name] = ttk.Button(bookmark_actions, text=label, command=command)
            self.buttons[name].pack(side="left", padx=(0, 6))
        self.buttons["Replace words"] = ttk.Button(bookmark_actions, text="Replace words…", command=self.replace_words)
        self.buttons["Replace words"].pack(side="right")
        self.buttons["Quality tools"] = ttk.Button(bookmark_actions, text="Quality tools…", command=self.quality_windows.open)
        self.buttons["Quality tools"].pack(side="left", padx=(0, 6))
        filters = ttk.Frame(list_card, style="Card.TFrame")
        filters.pack(fill="x", pady=(0, 8))
        self.queue_status = tk.StringVar(value="All")
        self.queue_review = tk.StringVar(value="All")
        self.queue_bookmarked = tk.BooleanVar(value=False)
        self.queue_query = tk.StringVar()
        self.queue_visible = tk.StringVar(value="0 of 0 chunks shown")
        ttk.Label(filters, text="Status").grid(row=0, column=0, padx=(0, 5))
        ttk.Combobox(filters, textvariable=self.queue_status, state="readonly", width=13,
                     values=("All", "Pending", "Completed", "Resumed", "Retranslated", "Error", "Retry", "Skipped")).grid(row=0, column=1, padx=(0, 12))
        ttk.Label(filters, text="Review").grid(row=0, column=2, padx=(0, 5))
        ttk.Combobox(filters, textvariable=self.queue_review, state="readonly", width=13,
                     values=("All", "Not checked", "Checked", "Issues found", "Check error", "Retry")).grid(row=0, column=3, padx=(0, 12))
        ttk.Checkbutton(filters, text="Bookmarked only", variable=self.queue_bookmarked).grid(row=0, column=4, sticky="w")
        ttk.Label(filters, text="Text / chunk ID").grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(filters, textvariable=self.queue_query).grid(row=1, column=1, columnspan=4, sticky="ew", pady=(8, 0))
        ttk.Button(filters, text="Reset view", command=self.reset_queue_view).grid(row=1, column=5, padx=(8, 0), pady=(8, 0))
        filters.columnconfigure(4, weight=1)
        ttk.Label(list_card, textvariable=self.queue_visible, style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
        self.selection_label = ttk.Label(list_card, text="Ctrl/Cmd-click for multiple chunks; Shift-click for a range.",
                                         style="Muted.TLabel")
        self.selection_label.pack(anchor="w", pady=(0, 8))
        list_frame = ttk.Frame(list_card, style="Card.TFrame")
        list_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(list_frame, columns=("bookmark", "unit", "status", "check", "detail"),
                                 show="tree headings", selectmode="extended", height=7)
        for column, title, width in (("#0", "#", 45), ("bookmark", "★", 40), ("unit", "SOURCE", 210),
                                     ("status", "STATUS", 105), ("check", "REVIEW", 110),
                                     ("detail", "DETAILS", 180)):
            self.tree.heading(column, text=title, command=lambda name=column: self.sort_queue(name))
            self.tree.column(column, width=width, minwidth=40, stretch=column in ("unit", "detail"))
        for tag, foreground in (("Pending", "#63758a"), ("Completed", "#087769"),
                                ("Resumed", "#327190"), ("Retranslated", "#6655a2"),
                                ("Error", "#b43848"), ("Retry", "#a36512"), ("Skipped", "#84909e")):
            self.tree.tag_configure(tag, foreground=foreground)
        self.tree.tag_configure("stripe", background="#f7f9fc")
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        horizontal = ttk.Scrollbar(list_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        horizontal.grid(row=1, column=0, sticky="ew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self.selection_changed)
        for variable in (self.queue_status, self.queue_review, self.queue_bookmarked, self.queue_query):
            variable.trace_add("write", self.schedule_queue_refresh)
        self._queue_headings()
        panes.add(list_card, weight=1)
        preview_frame = ttk.Frame(panes, padding=(0, 10, 0, 0))
        self.source_preview = self._text_panel(preview_frame, "Original text", 0)
        self.target_preview = self._text_panel(preview_frame, "Translation & review", 1)
        panes.add(preview_frame, weight=1)
        self._set_text(self.source_preview, "Select a chunk to read its original text.")
        self._set_text(self.target_preview, "Your translation and proofreading notes will appear here.")

    def _text_panel(self, parent: ttk.Frame, title: str, column: int) -> tk.Text:
        panel = ttk.Frame(parent, style="Card.TFrame", padding=12)
        panel.grid(row=0, column=column, sticky="nsew", padx=(0, 6) if column == 0 else (6, 0))
        parent.columnconfigure(column, weight=1, uniform="previews")
        parent.rowconfigure(0, weight=1)
        ttk.Label(panel, text=title, style="Section.TLabel").pack(anchor="w", pady=(0, 10))
        content = ttk.Frame(panel, style="Card.TFrame")
        content.pack(fill="both", expand=True)
        text = tk.Text(content, wrap="word", height=9, width=24, state="disabled",
                       background=self.palette["surface"], foreground=self.palette["ink"],
                       relief="flat", borderwidth=0, highlightthickness=0, padx=2, pady=4,
                       spacing1=3, spacing3=6, selectbackground=self.palette["selected"],
                       font=(tkfont.nametofont("TkDefaultFont").actual("family"), 11))
        scroll = ttk.Scrollbar(content, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return text

    def save_launch_preferences(self) -> None:
        try:
            settings = load_settings(self.settings_path)
            settings = replace(settings, start_lm_studio=False, lmstudio_path=self.lmstudio_path)
            settings_manager.last_input_file = self.source.get()
            save_settings(self.settings_path, settings, require_model=False)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Cannot save launch option", str(exc), parent=self.root)

    def save_analysis_preference(self) -> None:
        try:
            settings = replace(load_settings(self.settings_path), analyze_novel=self.analyze_novel.get())
            save_settings(self.settings_path, settings, require_model=False)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Cannot save analysis option", str(exc), parent=self.root)

    def _capture_provider(self) -> None:
        self.server_urls[self.active_provider] = self.server_url.get().strip()
        selection = self.model_selection.get().strip()
        self.provider_models[self.active_provider] = self.model_choices.get(selection, selection)
        self.provider_keys[self.active_provider] = self.values["api_key"].get().strip()

    def _provider_controls(self) -> None:
        self.provider_combo.configure(state="disabled" if self.busy else "readonly")
        self.server_entry.configure(state="disabled" if self.busy else "normal")
        state = "normal" if not self.busy and self.active_provider == "lmstudio" else "disabled"
        self.launch_option.configure(state=state)
        self.launch_path_button.configure(state=state)

    def _persist_provider(self) -> None:
        settings = replace(load_settings(self.settings_path), provider=self.active_provider,
                           model=self.provider_models.get(self.active_provider, ""),
                           lmstudio_url=self.server_urls["lmstudio"], ollama_url=self.server_urls["ollama"])
        save_settings(self.settings_path, settings, require_model=False)

    def _provider_selected(self, event=None) -> None:
        if self.busy:
            return
        chosen = {"LM Studio":"lmstudio", "Ollama":"ollama"}[self.provider.get()]
        if chosen == self.active_provider:
            return
        self._capture_provider()
        self.active_provider = chosen
        self.server_url.set(self.server_urls[chosen])
        self.model_choices.clear()
        self.model_combo.configure(values=())
        self.model_selection.set(self.provider_models.get(chosen, ""))
        self.values["model"].set(self.provider_models.get(chosen, ""))
        self.values["api_key"].set(self.provider_keys.get(chosen, ""))
        self.discovery_generation += 1
        self.models_loading = False
        self._provider_controls()
        try:
            self._persist_provider()
        except (OSError, ValueError, TypeError) as exc:
            self.model_help.set(f"Provider changed; settings could not be saved: {exc}")
        self.refresh_models()

    def _server_changed(self, event=None) -> None:
        if self.busy or self.server_url.get().strip() == self.server_urls[self.active_provider]:
            return
        self._capture_provider()
        self.discovery_generation += 1
        self.models_loading = False
        try:
            self._persist_provider()
        except (OSError, ValueError, TypeError) as exc:
            self.model_help.set(str(exc))
            return
        self.refresh_models()

    def choose_studio_path(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, title="Choose LM Studio executable (or select a file inside its macOS .app)")
        if path:
            candidate = Path(path)
            bundle = next((p for p in (candidate, *candidate.parents) if p.suffix.lower() == ".app"), None)
            self.lmstudio_path = str(bundle or candidate)
            self.save_launch_preferences()

    def start_local_studio(self) -> None:
        """Compatibility entry point: connect without launching a process."""
        self.refresh_models()

    def refresh_models(self) -> None:
        if self.models_loading or self.busy:
            return
        self.models_loading = True
        self.refresh_button.configure(state="disabled")
        provider = self.active_provider
        generation = self.discovery_generation
        self.connection.set(f"{provider_label(provider)} · connecting")
        self.model_help.set("Looking for your local models…")
        # Snapshot Tk variables here, never in the worker thread.
        try:
            self._capture_provider()
            client = create_client(Settings(provider=provider, lmstudio_url=self.server_urls["lmstudio"],
                                   ollama_url=self.server_urls["ollama"], api_key=self.values["api_key"].get().strip()))
        except ValueError as exc:
            self.models_loading = False
            self.refresh_button.configure(state="normal")
            self.model_help.set(str(exc))
            return
        def worker():
            try:
                self.events.put({"type": "models", "models": client.list_models(), "provider":provider, "generation":generation})
            except Exception as exc:
                logging.getLogger(__name__).warning("Model discovery failed: %s", exc)
                self.events.put({"type": "models", "error": str(exc), "provider":provider, "generation":generation})
        threading.Thread(target=worker, daemon=True, name="model-discovery").start()

    def _model_selected(self, event=None) -> None:
        selection = self.model_selection.get().strip()
        self.values["model"].set(self.model_choices.get(selection, selection))

    def _models_received(self, event: dict) -> None:
        if (event.get("provider", self.active_provider) != self.active_provider or
                event.get("generation", self.discovery_generation) != self.discovery_generation):
            return
        self.models_loading = False
        self.refresh_button.configure(state="disabled" if self.busy else "normal")
        if "error" in event:
            self.connection.set(f"{provider_label(self.active_provider)} · unavailable")
            self.model_help.set(event["error"] + " Start the local server, then Refresh. You can also enter a model ID.")
            return
        models = event["models"]
        current = self.model_selection.get().strip()
        identifier = self.model_choices.get(current, current)
        self.model_choices = {model.label: model.identifier for model in models}
        self.model_combo.configure(values=[model.label for model in models])
        if current and current not in self.model_choices:
            # A refresh can arrive during translation, or a selected model can
            # disappear/change state. Never turn its display label into an ID.
            self.model_choices[current] = identifier
        if not self.busy:
            matching = next((model.label for model in models if model.identifier == identifier), None)
            if matching:
                self.model_selection.set(matching)
            elif not identifier and models:
                self.model_selection.set(models[0].label)
            elif identifier:
                self.model_selection.set(identifier)
            self._model_selected()
        name = provider_label(self.active_provider)
        self.connection.set(f"{name} · {len(models)} models")
        self.model_help.set(("Choose a local model or type its exact name. Ollama loads downloaded models on request."
                             if self.active_provider == "ollama" else
                             "Choose a model or type its ID. Enable auto-loading or load the model in LM Studio.")
                            if models else f"No local models found. Download a model in {name}, then Refresh.")

    def _controls(self, busy: bool) -> None:
        self.busy = busy
        self.refresh_button.configure(state="disabled" if busy or self.models_loading else "normal")
        for widget in [self.browse, self.source_entry, self.check_option, self.analysis_option, self.open_button,
                       self.open_saved_button, self.launch_option, self.launch_path_button, *self.option_widgets]:
            widget.configure(state="disabled" if busy else "normal")
        self.saved_combo.configure(state="disabled" if busy else "readonly")
        self._provider_controls()
        self._prompt_controls()
        for name, button in self.buttons.items():
            enabled = (not busy if name == "Start / load" else
                       busy and self.engine is not None if name in ("Pause", "Resume", "Cancel") else
                       not busy and self.job is not None)
            button.configure(state="normal" if enabled else "disabled")

    def choose_file(self) -> None:
        path = filedialog.askopenfilename(parent=self.root, filetypes=[
            ("Books", "*.epub *.pdf *.docx *.txt"), ("All files", "*")])
        if path:
            self.source.set(path)
            self.open_book()

    def get_settings(self, validate: bool = True) -> Settings:
        self._model_selected()
        self._capture_provider()
        settings = Settings(**{name: value.get().strip() for name, value in self.values.items()
                               if name not in ("max_workers", "retries", "timeout")},
                            max_workers=int(self.values["max_workers"].get()),
                            retries=int(self.values["retries"].get()),
                            timeout=float(self.values["timeout"].get()),
                            proofread=self.proofread.get(), analyze_novel=self.analyze_novel.get(), custom_prompt=self.custom_prompt,
                            provider=self.active_provider, lmstudio_url=self.server_urls["lmstudio"], ollama_url=self.server_urls["ollama"],
                            use_custom_prompt=self.use_custom_prompt.get(),
                            start_lm_studio=False, lmstudio_path=self.lmstudio_path)
        if validate:
            settings.validate()
        return settings

    def _background(self, action) -> None:
        def worker():
            try:
                action()
            except Exception as exc:
                logging.getLogger(__name__).exception("Background operation failed")
                self.events.put({"type": "fatal", "detail": str(exc)})
            finally:
                self.events.put({"type": "idle"})
        self._controls(True)
        threading.Thread(target=worker, daemon=False, name="job-coordinator").start()

    def start(self) -> None:
        self._load_book(True)

    def refresh_saved_books(self) -> None:
        def worker():
            try:
                self.events.put({"type": "saved_books", "books": saved_books(self.data_directory / "jobs")})
            except Exception as exc:
                logging.getLogger(__name__).warning("Cannot list saved books: %s", exc)
        threading.Thread(target=worker, daemon=True, name="saved-book-discovery").start()

    def open_saved_book(self) -> None:
        if self.busy:
            return
        saved = self.saved_choices.get(self.saved_selection.get())
        if saved is None:
            messagebox.showinfo("Saved books", "Select a saved book first.", parent=self.root)
            return
        self._load_book(False, saved=saved)

    def open_book(self) -> None:
        self._load_book(False)

    def _restore_settings(self, settings: Settings) -> None:
        self._capture_provider()
        if self.active_provider != settings.provider:
            self.discovery_generation += 1
            self.models_loading = False
            self.model_choices.clear()
            self.model_combo.configure(values=())
        self.active_provider = settings.provider
        self.provider.set(provider_label(settings.provider))
        self.server_urls.update(lmstudio=settings.lmstudio_url, ollama=settings.ollama_url)
        self.server_url.set(self.server_urls[settings.provider])
        self.values["api_key"].set(settings.api_key)
        for name, variable in self.values.items():
            if name != "api_key":
                variable.set(str(getattr(settings, name)))
        self.model_selection.set(settings.model)
        self.proofread.set(settings.proofread)
        self.analyze_novel.set(settings.analyze_novel)
        self.use_custom_prompt.set(settings.use_custom_prompt)
        self.custom_prompt = settings.custom_prompt
        self._provider_controls()
        if not settings.use_custom_prompt:
            self._refresh_prompt_choices(identifier=PromptStore.DEFAULT_ID)
        else:
            match = next((p for p in self.prompt_store.all() if p.text == settings.custom_prompt), None)
            self._refresh_prompt_choices(identifier=match.identifier if match else None,
                                         snapshot=settings.custom_prompt if match is None else None)

    def _load_book(self, run: bool, saved=None) -> None:
        if self.busy:
            return
        try:
            settings = self.get_settings(validate=run)
            filename = str(saved.source_path) if saved else self.source.get().strip()
            if not filename:
                raise ValueError("Choose an input file.")
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Cannot open book", str(exc), parent=self.root)
            return
        self.summary.set("Opening book and finding saved translations…")
        opened_directory = self.job.directory if self.job else None
        provider_keys = dict(self.provider_keys)
        def action():
            book = load_book(filename)
            chosen = settings
            match = saved
            if match is None:
                match = matching_saved_book(saved_books(self.data_directory / "jobs"), book.digest,
                                            settings, opened_directory, restore_languages=not run)
            if match:
                if book.digest != match.digest:
                    raise ValueError("Saved source is damaged or changed; open the original file.")
                chosen = replace(match.settings, api_key=provider_keys.get(match.settings.provider, ""),
                                 analyze_novel=settings.analyze_novel,
                                 lmstudio_url=settings.lmstudio_url, ollama_url=settings.ollama_url,
                                 start_lm_studio=False, lmstudio_path=settings.lmstudio_path)
            chosen.validate()
            job = Job(book, chosen, self.data_directory / "jobs")
            if match and job.directory != match.directory:
                raise ValueError("Saved book configuration does not match its translation folder.")
            if match:
                provider = settings.provider if settings.model else match.last_provider or chosen.provider
                model = settings.model or match.last_model or chosen.model
                job.configure_translation(replace(settings if run else chosen, provider=provider, model=model,
                                                  api_key=provider_keys.get(provider, "")))
            engine = TranslationEngine(job, self.events.put)
            self.events.put({"type": "loaded", "job": job, "engine": engine,
                             "restore": bool(match), "source": filename, "run": run})
            if run:
                engine.run()
        self._background(action)

    def _prompt_controls(self) -> None:
        self.prompt_combo.configure(state="disabled" if self.busy else "readonly")
        identifier = self.prompt_choices.get(self.prompt_selection.get(), PromptStore.DEFAULT_ID) if hasattr(self, "prompt_choices") else PromptStore.DEFAULT_ID
        writable = not self.busy and not self.prompt_store.read_only
        self.prompt_buttons["New"].configure(state="normal" if writable else "disabled")
        for name in ("Edit", "Delete"):
            self.prompt_buttons[name].configure(state="normal" if writable and identifier not in (PromptStore.DEFAULT_ID, "book") else "disabled")

    def _refresh_prompt_choices(self, identifier: str | None = None, snapshot: str | None = None) -> None:
        self.prompt_choices = {p.name: p.identifier for p in self.prompt_store.all()}
        self.book_prompt_text = snapshot or ""
        if snapshot is not None:
            self.prompt_choices["Book prompt (unsaved)"] = "book"
            identifier = "book"
        identifier = identifier or self.prompt_store.selected_id
        name = next((name for name, key in self.prompt_choices.items() if key == identifier), PromptStore.DEFAULT_NAME)
        self.prompt_combo.configure(values=list(self.prompt_choices))
        self.prompt_selection.set(name)
        self._apply_prompt()
        self._prompt_controls()

    def _apply_prompt(self) -> None:
        identifier = self.prompt_choices[self.prompt_selection.get()]
        self.use_custom_prompt.set(identifier != PromptStore.DEFAULT_ID)
        self.custom_prompt = self.book_prompt_text if identifier == "book" else self.prompt_store.get(identifier).text

    def _prompt_selected(self, event=None) -> None:
        if self.busy:
            return
        identifier = self.prompt_choices[self.prompt_selection.get()]
        try:
            if identifier != "book" and not self.prompt_store.read_only:
                self.prompt_store.select(identifier)
            self._apply_prompt()
            self._prompt_controls()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot select prompt", str(exc), parent=self.root)
            self._refresh_prompt_choices()

    def delete_prompt(self) -> None:
        if self.busy:
            return
        identifier = self.prompt_choices[self.prompt_selection.get()]
        if identifier in (PromptStore.DEFAULT_ID, "book"):
            return
        name = self.prompt_store.get(identifier).name
        if not messagebox.askyesno("Delete saved prompt", f"Delete '{name}' from the prompt library?", parent=self.root):
            return
        try:
            self.prompt_store.delete(identifier)
            if self.prompt_store.selected_id != PromptStore.DEFAULT_ID:
                self.prompt_store.select(PromptStore.DEFAULT_ID)
            self._refresh_prompt_choices(identifier=PromptStore.DEFAULT_ID)
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot delete prompt", str(exc), parent=self.root)

    def edit_prompt(self, new: bool = False) -> None:
        if self.busy:
            return
        identifier = self.prompt_choices[self.prompt_selection.get()]
        new = new or identifier in (PromptStore.DEFAULT_ID, "book")
        prompt = self.prompt_store.get(identifier) if identifier != "book" else None
        dialog = tk.Toplevel(self.root)
        dialog.title("Create prompt" if new else "Edit saved prompt")
        dialog.geometry("790x650")
        dialog.transient(self.root)
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=16)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Prompt name").pack(anchor="w")
        name_var = tk.StringVar(value="" if new else prompt.name)
        name_entry = ttk.Entry(panel, textvariable=name_var)
        name_entry.pack(fill="x", pady=(5, 10))
        ttk.Label(panel, text="This replaces the system prompt for new or retranslated chunks.\n"
                  "Optional placeholders: <input_lang> and <output_lang>.").pack(anchor="w", pady=10)
        text = tk.Text(panel, wrap="word", undo=True, padx=10, pady=10)
        text.pack(fill="both", expand=True)
        text.insert("1.0", self.book_prompt_text if identifier == "book" else prompt.text)
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(12, 0))
        def save():
            try:
                value = text.get("1.0", "end-1c")
                saved = (self.prompt_store.create(name_var.get(), value) if new else
                         self.prompt_store.update(identifier, name_var.get(), value))
                if self.prompt_store.selected_id != saved.identifier:
                    self.prompt_store.select(saved.identifier)
                self._refresh_prompt_choices(identifier=saved.identifier)
            except (OSError, ValueError) as exc:
                messagebox.showerror("Cannot save prompt", str(exc), parent=dialog)
                return
            dialog.destroy()
        ttk.Button(actions, text="Save & select", command=save, style="Primary.TButton").pack(side="right")
        ttk.Button(actions, text="Cancel", command=dialog.destroy).pack(side="right", padx=8)
        ttk.Button(actions, text="Insert default prompt", command=lambda: self._replace_editor(text, TRANSLATION_PROMPT)).pack(side="left")
        name_entry.focus_set()

    @staticmethod
    def _replace_editor(widget: tk.Text, value: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", value)

    def edit_chunk(self) -> None:
        if self.busy or self.job is None or self.engine is None:
            return
        index = self.selected()
        if index is None or len(self.tree.selection()) != 1:
            messagebox.showinfo("Edit one chunk", "Select exactly one chunk to edit.", parent=self.root)
            return
        engine = self.engine
        try:
            value = self.job.read_translation(index) if self.job.chunk_path(index).exists() else ""
        except (OSError, ValueError):
            value = ""  # Allows repairing a corrupt translation manually.
        dialog = tk.Toplevel(self.root)
        dialog.title(f"Edit translated chunk {index}")
        dialog.geometry("900x640")
        dialog.transient(self.root)
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=16)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text=f"Chunk {index} · Edit translation", style="Section.TLabel").pack(anchor="w")
        ttk.Label(panel, text="Save replaces this chunk's file and clears its old proofreading result. Leave blank to omit its translated text.").pack(anchor="w", pady=10)
        columns = ttk.Frame(panel)
        columns.pack(fill="both", expand=True)
        source = self._text_panel(columns, "Original text", 0)
        editor = self._text_panel(columns, "Editable translation", 1)
        self._set_text(source, self.job.chunks[index].text)
        editor.configure(state="normal", undo=True)
        editor.insert("1.0", value)
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=(12, 0))
        def save():
            try:
                engine.save_chunk_edit(index, editor.get("1.0", "end-1c"))
            except (OSError, ValueError, RuntimeError) as exc:
                messagebox.showerror("Cannot save edit", str(exc), parent=dialog)
                return
            dialog.destroy()
        ttk.Button(actions, text="Save chunk", command=save, style="Primary.TButton").pack(side="right")
        ttk.Button(actions, text="Cancel", command=dialog.destroy).pack(side="right", padx=8)
        editor.focus_set()

    def pause(self) -> None:
        if self.engine:
            self.engine.pause()
            self.summary.set("Paused. In-flight requests finish; their results wait before saving.")

    def resume(self) -> None:
        if self.engine:
            self.engine.resume()
            self.summary.set("Resumed.")

    def cancel(self) -> None:
        if self.engine:
            self.engine.cancel()
            self.summary.set("Cancelling; waiting for in-flight requests (up to the request timeout).")

    def selected(self) -> int | None:
        selection = self.tree.selection()
        focused = self.tree.focus()
        return int(focused if focused in selection else selection[0]) if selection else None

    def selection_changed(self, event=None) -> None:
        count = len(self.tree.selection())
        self.selection_label.configure(text=f"{count} selected · Ctrl/Cmd-click to add chunks; Shift-click for a range.")
        self.preview()

    def schedule_queue_refresh(self, *args) -> None:
        # Coalesce worker updates so a large queue is not sorted for every event.
        if self.queue_refresh_after is None:
            self.queue_refresh_after = self.root.after(150, self.refresh_queue_view)

    def _queue_headings(self) -> None:
        for column, title in (("#0", "#"), ("bookmark", "★"), ("unit", "SOURCE"),
                              ("status", "STATUS"), ("check", "REVIEW"), ("detail", "DETAILS")):
            suffix = (" ▼" if self.queue_descending else " ▲") if column == self.queue_sort else ""
            self.tree.heading(column, text=title + suffix)

    def sort_queue(self, column: str) -> None:
        self.queue_descending = not self.queue_descending if column == self.queue_sort else False
        self.queue_sort = column
        self._queue_headings()
        self.refresh_queue_view()

    def reset_queue_view(self) -> None:
        self.queue_status.set("All")
        self.queue_review.set("All")
        self.queue_bookmarked.set(False)
        self.queue_query.set("")
        self.queue_sort, self.queue_descending = "#0", False
        self._queue_headings()
        self.refresh_queue_view()

    def refresh_queue_view(self) -> None:
        if self.queue_refresh_after is not None:
            self.root.after_cancel(self.queue_refresh_after)
            self.queue_refresh_after = None
        rows = []
        if self.job is not None:
            for chunk in self.job.chunks:
                values = self.tree.set(str(chunk.index))
                rows.append({**values, "index": chunk.index, "source": chunk.text,
                             "translation": self.previews.get(chunk.index, ""),
                             "label": self.bookmarks.get(chunk.index, "")})
        visible = queue_view(rows, self.queue_status.get(), self.queue_review.get(),
                             self.queue_bookmarked.get(), self.queue_query.get(),
                             self.queue_sort, self.queue_descending)
        visible_ids = {str(index) for index in visible}
        hidden_selection = [item for item in self.tree.selection() if item not in visible_ids]
        if hidden_selection:
            self.tree.selection_remove(*hidden_selection)
        hidden = [item for item in self.tree.get_children() if item not in visible_ids]
        if hidden:
            self.tree.detach(*hidden)
        for position, index in enumerate(visible):
            self.tree.move(str(index), "", position)
        self.queue_visible.set(f"{len(visible):,} of {len(rows):,} chunks shown · Click a column heading to sort")
        if hidden_selection:
            self.selection_changed()
            if self.selected() is None:
                self._set_text(self.source_preview, "Select a visible chunk to read its original text.")
                self._set_text(self.target_preview, "Select a visible chunk to read its translation.")

    def select_queue_chunks(self, indices: list[int]) -> None:
        # Search, bookmarks and direct selection must reveal filtered-out targets.
        self.refresh_queue_view()
        if not {str(index) for index in indices}.issubset(self.tree.get_children()):
            self.queue_status.set("All")
            self.queue_review.set("All")
            self.queue_bookmarked.set(False)
            self.queue_query.set("")
            self.refresh_queue_view()
        self.tree.selection_set([str(index) for index in indices])
        self.tree.focus(str(indices[0]))
        self.tree.see(str(indices[0]))
        self.selection_changed()

    @staticmethod
    def _size_dialog(dialog: tk.Toplevel, width: int, height: int) -> None:
        """Fit the window to the screen while keeping its controls accessible."""
        width = min(width, max(320, dialog.winfo_screenwidth() - 80))
        height = min(height, max(320, dialog.winfo_screenheight() - 100))
        dialog.geometry(f"{width}x{height}")
        dialog.minsize(min(720, width), min(480, height))

    def replace_words(self) -> None:
        if self.busy or self.job is None or self.engine is None:
            return
        job, engine = self.job, self.engine
        dialog = tk.Toplevel(self.root)
        dialog.title("Replace text across translated chunks")
        self._size_dialog(dialog, 1050, 790)
        dialog.transient(self.root)
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=16)
        panel.pack(fill="both", expand=True)
        panel.columnconfigure(0, weight=1)
        panel.rowconfigure(4, weight=1)
        panel.rowconfigure(5, weight=2)
        ttk.Label(panel, text="Replace in all translated chunks", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        controls = ttk.Frame(panel)
        controls.grid(row=1, column=0, sticky="ew", pady=12)
        find, replacement = tk.StringVar(), tk.StringVar()
        whole_word, match_case = tk.BooleanVar(value=True), tk.BooleanVar(value=False)
        controls.columnconfigure(1, weight=1)
        controls.columnconfigure(3, weight=1)
        ttk.Label(controls, text="Find").grid(row=0, column=0, padx=(0, 6))
        find_entry = ttk.Entry(controls, textvariable=find)
        find_entry.grid(row=0, column=1, sticky="ew", padx=(0, 16))
        ttk.Label(controls, text="Replace with").grid(row=0, column=2, padx=(0, 6))
        replacement_entry = ttk.Entry(controls, textvariable=replacement)
        replacement_entry.grid(row=0, column=3, sticky="ew")
        options = ttk.Frame(panel)
        options.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        word_check = ttk.Checkbutton(options, text="Whole words only", variable=whole_word)
        word_check.pack(side="left")
        case_check = ttk.Checkbutton(options, text="Match case", variable=match_case)
        case_check.pack(side="left", padx=12)
        status = tk.StringVar(value="Preview first, review the results, then click Replace words to apply.")
        status_label = ttk.Label(panel, textvariable=status, wraplength=1000)
        status_label.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        panel.bind("<Configure>", lambda event: status_label.configure(wraplength=max(200, event.width - 32)))
        rows = ttk.Frame(panel)
        rows.grid(row=4, column=0, sticky="nsew")
        tree = ttk.Treeview(rows, columns=("chunk", "count", "before", "after"), show="headings", selectmode="browse", height=7)
        for column, title, width in (("chunk", "Chunk", 65), ("count", "Replacements", 140),
                                    ("before", "Before", 390), ("after", "After", 390)):
            tree.heading(column, text=title)
            tree.column(column, width=width, stretch=column in ("before", "after"))
        scroll = ttk.Scrollbar(rows, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        previews = ttk.Frame(panel)
        previews.grid(row=5, column=0, sticky="nsew", pady=(12, 0))
        before_text = self._text_panel(previews, "Before replacement", 0)
        after_text = self._text_panel(previews, "After replacement", 1)
        actions = ttk.Frame(panel)
        # A fixed footer stays visible as the results and previews shrink.
        actions.grid(row=6, column=0, sticky="ew", pady=(12, 0))
        messages = queue.Queue()
        state = {"preview": None, "running": False, "after": None}
        inputs = (find_entry, replacement_entry, word_check, case_check)
        def invalidate(*args):
            if state["running"]:
                return
            state["preview"] = None
            apply_button.configure(state="disabled")
            status.set("Settings changed. Click Preview replacements before applying.")
        def close():
            if state["running"]:
                return
            if state["after"] is not None:
                dialog.after_cancel(state["after"])
            dialog.destroy()
        def selected(event=None):
            row = tree.selection()
            plan = state["preview"]
            if not row or plan is None:
                return
            change = plan.replacements[int(row[0])]
            for widget, text, spans in ((before_text, change.before, change.before_spans),
                                        (after_text, change.after, change.after_spans)):
                self._set_text(widget, text)
                widget.tag_configure("replace_match", background="#ffe69a", foreground="#17283b")
                for start, end in spans:
                    widget.tag_add("replace_match", f"1.0+{start}c", f"1.0+{end}c")
                if spans:
                    widget.see(f"1.0+{spans[0][0]}c")
        def poll():
            state["after"] = None
            try:
                phase, result, error = messages.get_nowait()
            except queue.Empty:
                state["after"] = dialog.after(50, poll)
                return
            state["running"] = False
            if phase == "apply":
                self._controls(False)
            close_button.configure(state="normal")
            preview_button.configure(state="normal")
            for widget in inputs:
                widget.configure(state="normal")
            if error:
                state["preview"] = None
                status.set(error + " Generate a fresh preview before applying again.")
                apply_button.configure(state="disabled")
                return
            if phase == "apply":
                plan = state["preview"]
                count = sum(len(c.before_spans) for c in plan.replacements)
                status.set(f"Applied {count} replacements in {len(plan.replacements)} chunks. Original files were backed up.")
                state["preview"] = None
                apply_button.configure(state="disabled")
                return
            state["preview"] = result
            for number, change in enumerate(result.replacements):
                start, end = change.before_spans[0]
                new_start, new_end = change.after_spans[0]
                tree.insert("", "end", iid=str(number), values=(change.chunk_index, len(change.before_spans),
                            " ".join(change.before[max(0, start-40):end+70].split()),
                            " ".join(change.after[max(0, new_start-40):new_end+70].split())))
            count = sum(len(c.before_spans) for c in result.replacements)
            status.set(f"Preview: {count} replacements in {len(result.replacements)} chunks. No files changed. Click Replace words to apply."
                       + (" Fix unreadable translation files first: " + "; ".join(result.errors[:3]) if result.errors else ""))
            apply_button.configure(state="normal" if result.replacements and not result.errors else "disabled")
            if result.replacements:
                tree.selection_set("0")
                selected()
        def start_worker(phase, action):
            state["running"] = True
            if phase == "apply":
                self._controls(True)
            for widget in (*inputs, preview_button, apply_button, close_button):
                widget.configure(state="disabled")
            status.set("Preparing replacement preview…" if phase == "preview" else "Applying reviewed replacements…")
            def worker():
                try:
                    messages.put((phase, action(), None))
                except Exception as exc:
                    logging.getLogger(__name__).exception("Replacement %s failed", phase)
                    messages.put((phase, None, str(exc)))
            threading.Thread(target=worker, daemon=False, name="chunk-replacement").start()
            state["after"] = dialog.after(50, poll)
        def preview(event=None):
            if state["running"]:
                return
            query, new_text, words, case = find.get(), replacement.get(), whole_word.get(), match_case.get()
            children = tree.get_children()
            if children:
                tree.delete(*children)
            state["preview"] = None
            self._set_text(before_text, "")
            self._set_text(after_text, "")
            start_worker("preview", lambda: job.preview_replacement(query, new_text, words, case))
        def apply():
            if state["running"] or state["preview"] is None:
                return
            plan = state["preview"]
            start_worker("apply", lambda: engine.apply_replacement(plan))
        preview_button = ttk.Button(actions, text="Preview replacements", command=preview)
        preview_button.pack(side="left")
        apply_button = ttk.Button(actions, text="Replace words", command=apply, state="disabled", style="Primary.TButton")
        apply_button.pack(side="right")
        close_button = ttk.Button(actions, text="Close", command=close)
        close_button.pack(side="right", padx=8)
        for variable in (find, replacement, whole_word, match_case):
            variable.trace_add("write", invalidate)
        tree.bind("<<TreeviewSelect>>", selected)
        find_entry.bind("<Return>", preview)
        dialog.protocol("WM_DELETE_WINDOW", close)
        find_entry.focus_set()

    def bookmark_selected(self, remove: bool = False) -> None:
        if self.busy or self.engine is None:
            return
        indices = [int(index) for index in self.tree.selection()]
        if not indices:
            messagebox.showinfo("Select chunks", "Select the chunks you want to bookmark.", parent=self.root)
            return
        label = ""
        if not remove:
            label = simpledialog.askstring("Bookmark selected chunks", "Optional bookmark label (leave blank for a simple bookmark):",
                                           initialvalue=self.bookmarks.get(indices[0], ""), parent=self.root)
            if label is None:
                return
        try:
            self.bookmarks = self.engine.set_bookmarks(indices, label, remove=remove)
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror("Cannot change bookmarks", str(exc), parent=self.root)
            return
        for index in indices:
            self.tree.set(str(index), "bookmark", "★" if index in self.bookmarks else "")
        self.summary.set(f"{len(self.bookmarks)} bookmarked chunks in this book.")
        self.schedule_queue_refresh()

    def show_bookmarks(self) -> None:
        if self.busy or self.job is None or self.engine is None:
            return
        try:
            self.bookmarks = self.job.get_bookmarks()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot read bookmarks", str(exc), parent=self.root)
            return
        if not self.bookmarks:
            messagebox.showinfo("Bookmarks", "No bookmarks yet. Select chunks and click Bookmark selected.", parent=self.root)
            return
        dialog = tk.Toplevel(self.root)
        dialog.title("Bookmarked chunks")
        self._size_dialog(dialog, 850, 480)
        dialog.transient(self.root)
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=16)
        panel.pack(fill="both", expand=True)
        heading = tk.StringVar(value=f"{len(self.bookmarks)} bookmarked chunks")
        ttk.Label(panel, textvariable=heading, style="Section.TLabel").pack(anchor="w", pady=(0, 8))
        ttk.Label(panel, text="Ctrl/Cmd-click or Shift-click to select multiple bookmarks.").pack(anchor="w", pady=(0, 8))
        actions = ttk.Frame(panel)
        actions.pack(side="bottom", fill="x", pady=(12, 0))
        rows = ttk.Frame(panel)
        rows.pack(fill="both", expand=True)
        tree = ttk.Treeview(rows, columns=("chunk", "label", "source"), show="headings", selectmode="extended")
        for column, title, width in (("chunk", "Chunk", 65), ("label", "Bookmark label", 220),
                                    ("source", "Original text", 480)):
            tree.heading(column, text=title)
            tree.column(column, width=width, stretch=column != "chunk")
        scroll = ttk.Scrollbar(rows, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        for index, label in sorted(self.bookmarks.items()):
            tree.insert("", "end", iid=str(index), values=(index, label, " ".join(self.job.chunks[index].text.split())[:160]))
        def jump(event=None):
            selection = tree.selection()
            if not selection:
                return
            focused = tree.focus()
            index = focused if focused in selection else selection[0]
            self.select_queue_chunks([int(index)])
            dialog.destroy()
        def selection_changed(event=None):
            selected = bool(tree.selection())
            delete_button.configure(state="normal" if selected else "disabled")
            open_button.configure(state="normal" if selected else "disabled")
        def delete_selected(event=None):
            indices = [int(index) for index in tree.selection()]
            if not indices:
                return
            try:
                self.bookmarks = self.engine.set_bookmarks(indices, remove=True)
            except (OSError, ValueError, RuntimeError) as exc:
                messagebox.showerror("Cannot delete bookmarks", str(exc), parent=dialog)
                return
            tree.delete(*(str(index) for index in indices))
            for index in indices:
                self.tree.set(str(index), "bookmark", "")
            heading.set(f"{len(self.bookmarks)} bookmarked chunks" if self.bookmarks else "No bookmarks remaining")
            self.summary.set(f"{len(self.bookmarks)} bookmarked chunks in this book.")
            self.schedule_queue_refresh()
            selection_changed()
        def select_all(event=None):
            tree.selection_set(tree.get_children())
            selection_changed()
            return "break"
        delete_button = ttk.Button(actions, text="Delete selected bookmarks", command=delete_selected, state="disabled")
        delete_button.pack(side="left")
        open_button = ttk.Button(actions, text="Open chunk", command=jump, style="Primary.TButton", state="disabled")
        open_button.pack(side="right")
        ttk.Button(actions, text="Close", command=dialog.destroy).pack(side="right", padx=8)
        tree.bind("<<TreeviewSelect>>", selection_changed)
        tree.bind("<Delete>", delete_selected)
        tree.bind("<Control-a>", select_all)
        tree.bind("<Command-a>", select_all)
        tree.bind("<Double-1>", jump)
        tree.bind("<Return>", jump)
        tree.focus_set()

    def search_chunks(self) -> None:
        if self.busy or self.job is None:
            return
        job = self.job
        dialog = tk.Toplevel(self.root)
        dialog.title("Search all chunks")
        dialog.geometry("1000x580")
        dialog.transient(self.root)
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=16)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="Search source and translated chunks", style="Section.TLabel").pack(anchor="w")
        controls = ttk.Frame(panel)
        controls.pack(fill="x", pady=12)
        query = tk.StringVar()
        scope = tk.StringVar(value="Both")
        case_sensitive = tk.BooleanVar(value=False)
        entry = ttk.Entry(controls, textvariable=query)
        entry.pack(side="left", fill="x", expand=True, padx=(0, 8))
        scope_box = ttk.Combobox(controls, textvariable=scope, values=("Both", "Source", "Translation"),
                                  state="readonly", width=13)
        scope_box.pack(side="left", padx=(0, 8))
        case_box = ttk.Checkbutton(controls, text="Match case", variable=case_sensitive)
        case_box.pack(side="left", padx=(0, 8))
        status = tk.StringVar(value="Enter a word or phrase. Results show the chunk where it appears.")
        ttk.Label(panel, textvariable=status, wraplength=930).pack(anchor="w", pady=(0, 10))
        results_frame = ttk.Frame(panel)
        results_frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(results_frame, columns=("chunk", "field", "count", "unit", "snippet"),
                            show="headings", selectmode="browse")
        for column, label, width in (("chunk", "Chunk", 65), ("field", "Found in", 95),
                                     ("count", "Matches", 70), ("unit", "Source unit", 200),
                                     ("snippet", "Matching text", 470)):
            tree.heading(column, text=label)
            tree.column(column, width=width, stretch=column in ("unit", "snippet"))
        scroll = ttk.Scrollbar(results_frame, command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        messages = queue.Queue()
        state = {"after": None, "running": False, "matches": ()}
        def close():
            if state["after"] is not None:
                dialog.after_cancel(state["after"])
            dialog.destroy()
        def jump(event=None):
            selection = tree.selection()
            if not selection:
                return
            match = state["matches"][int(selection[0])]
            # Reading from disk again prevents stale preview caches after edits.
            self.previews.pop(match.chunk_index, None)
            self.select_queue_chunks([match.chunk_index])
            widget = self.source_preview if match.field == "Source" else self.target_preview
            def highlight():
                widget.tag_configure("search_match", background="#ffe69a", foreground="#17283b")
                for start, end in match.spans:
                    widget.tag_add("search_match", f"1.0+{start}c", f"1.0+{end}c")
                if match.spans:
                    widget.see(f"1.0+{match.spans[0][0]}c")
            # Run after Treeview's queued selection event refreshes the preview.
            self.root.after_idle(highlight)
            close()
        def poll():
            state["after"] = None
            try:
                result, error = messages.get_nowait()
            except queue.Empty:
                state["after"] = dialog.after(50, poll)
                return
            state["running"] = False
            search_button.configure(state="normal")
            entry.configure(state="normal")
            scope_box.configure(state="readonly")
            case_box.configure(state="normal")
            if error:
                status.set(error)
                return
            state["matches"] = result.matches
            for number, match in enumerate(result.matches):
                tree.insert("", "end", iid=str(number), values=(match.chunk_index, match.field,
                            len(match.spans), match.label, match.snippet))
            chunk_count = len({match.chunk_index for match in result.matches})
            occurrence_count = sum(len(match.spans) for match in result.matches)
            status.set(f"{occurrence_count} matches in {chunk_count} chunks. Double-click a result or choose Open chunk."
                       + (f" {len(result.errors)} unreadable translation files: " + "; ".join(result.errors[:3])
                          if result.errors else ""))
        def search(event=None):
            if state["running"]:
                return
            text, area, match_case = query.get(), scope.get(), case_sensitive.get()
            if not text.strip():
                status.set("Enter text to search for.")
                return
            children = tree.get_children()
            if children:
                tree.delete(*children)
            state["matches"] = ()
            state["running"] = True
            status.set("Searching all chunks…")
            for widget in (entry, scope_box, case_box, search_button):
                widget.configure(state="disabled")
            def worker():
                try:
                    messages.put((job.search(text, area, match_case), None))
                except Exception as exc:
                    logging.getLogger(__name__).exception("Chunk search failed")
                    messages.put((None, str(exc)))
            threading.Thread(target=worker, daemon=True, name="chunk-search").start()
            state["after"] = dialog.after(50, poll)
        search_button = ttk.Button(controls, text="Search", command=search, style="Primary.TButton")
        search_button.pack(side="left")
        footer = ttk.Frame(panel)
        footer.pack(fill="x", pady=(12, 0))
        ttk.Button(footer, text="Open chunk", command=jump, style="Primary.TButton").pack(side="right")
        ttk.Button(footer, text="Close", command=close).pack(side="right", padx=8)
        entry.bind("<Return>", search)
        tree.bind("<Double-1>", jump)
        tree.bind("<Return>", jump)
        dialog.protocol("WM_DELETE_WINDOW", close)
        entry.focus_set()

    def choose_chunks(self) -> None:
        if self.busy or self.job is None:
            return
        value = simpledialog.askstring("Select specific chunks",
                                       f"Chunk IDs are zero-based (0–{len(self.job.chunks) - 1}).\n"
                                       "Enter numbers or inclusive ranges, e.g. 3, 8, 12-16:",
                                       parent=self.root)
        if value is None:
            return
        try:
            indices = parse_chunk_selection(value, len(self.job.chunks))
        except ValueError as exc:
            messagebox.showerror("Invalid chunk selection", str(exc), parent=self.root)
            return
        self.select_queue_chunks(indices)

    def retranslate(self) -> None:
        if self.busy:
            return
        indices = [int(index) for index in self.tree.selection()]
        if not indices or self.engine is None:
            messagebox.showinfo("Select chunks", "Select chunks in the queue or use Select by number first.", parent=self.root)
            return
        try:
            settings = self.get_settings()
            self.job.configure_translation(settings)
            save_settings(self.settings_path, settings)
            self.engine = TranslationEngine(self.job, self.events.put)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Cannot retranslate", str(exc), parent=self.root)
            return
        engine = self.engine
        self.summary.set(f"Retranslating {len(indices)} selected chunks…")
        self._background(lambda: engine.retranslate_chunks(indices))

    def export(self) -> None:
        if self.job is None:
            return
        job = self.job
        suffix = ".epub" if job.book.kind == "epub" else ".txt"
        path = filedialog.asksaveasfilename(parent=self.root, defaultextension=suffix,
                                           initialfile=job.book.path.stem + "_translated" + suffix,
                                           filetypes=[("Translated book", "*" + suffix)])
        if path:
            def action():
                job.export(Path(path))
                self.events.put({"type": "exported", "path": path})
            self._background(action)

    def _loaded(self, event: dict) -> None:
        self.job, self.engine = event["job"], event["engine"]
        try:
            self.analysis_excluded = exclusion_count(self.job.directory)
        except (OSError, ValueError, TypeError) as exc:
            self.analysis_excluded = 0
            logging.getLogger(__name__).warning("Cannot load glossary exclusion count: %s", exc)
        if event.get("restore"):
            self._restore_settings(self.job.settings)
        self.source.set(event.get("source", str(self.job.book.path)))
        settings_manager.last_input_file = self.source.get()
        try:
            save_settings(self.settings_path, self.job.settings)
        except OSError as exc:
            logging.getLogger(__name__).warning("Cannot save UI settings: %s", exc)
        # Detached (filtered) rows must also be deleted when switching books.
        children = [str(index) for index in self.statuses if self.tree.exists(str(index))]
        if children:
            self.tree.delete(*children)
        self.statuses.clear()
        self.details.clear()
        self.check_details.clear()
        self.previews.clear()
        try:
            self.bookmarks = self.job.get_bookmarks()
        except (OSError, ValueError) as exc:
            self.bookmarks = {}
            messagebox.showwarning("Bookmarks", str(exc), parent=self.root)
        for chunk in self.job.chunks:
            status, detail = "Pending", ""
            if self.job.chunk_path(chunk.index).exists():
                try:
                    self.previews[chunk.index] = self.job.read_translation(chunk.index)
                    status = "Skipped" if should_skip(chunk.text) else "Resumed"
                except (OSError, ValueError) as exc:
                    status, detail = "Error", str(exc)
            self.statuses[chunk.index] = status
            self.details[chunk.index] = detail
            self.tree.insert("", "end", iid=str(chunk.index), text=str(chunk.index),
                             tags=(status, "stripe") if chunk.index % 2 else (status,), values=(
                "★" if chunk.index in self.bookmarks else "",
                self.job.book.units[chunk.unit_index].label, status, "", detail))
        self.refresh_queue_view()
        self.progress.configure(maximum=max(1, len(self.job.chunks)), value=0)
        self._controls(True)
        self._update_counts("Recognized saved book. " if self.job.recognized else "Book opened. ")
        self.refresh_saved_books()
        self._set_text(self.source_preview, "Select a chunk to read its original text.")
        self._set_text(self.target_preview, "Your translation and proofreading notes will appear here.")
        if self.closing:
            self.engine.cancel()

    def _handle(self, event: dict) -> None:
        kind = event["type"]
        if kind == "studio_started":
            if self.active_provider != "lmstudio":
                return
            self.model_help.set(event["detail"])
            self.refresh_models()
            self.root.after(4000, self.refresh_models)
            self.root.after(12000, self.refresh_models)
        elif kind == "saved_books":
            self.saved_choices = {book.label: book for book in event["books"]}
            self.saved_combo.configure(values=list(self.saved_choices))
            if self.saved_selection.get() not in self.saved_choices:
                self.saved_selection.set(next(iter(self.saved_choices), ""))
        elif kind == "models":
            self._models_received(event)
        elif kind == "loaded":
            self._loaded(event)
        elif kind == "analysis":
            if "excluded_count" in event:
                self.analysis_excluded = event["excluded_count"]
            self.summary.set(event["detail"])
            if event.get("ready"):
                self.progress.configure(maximum=len(self.statuses), value=sum(
                    status in ("Completed", "Resumed", "Retranslated", "Skipped") for status in self.statuses.values()))
            elif event.get("total"):
                self.progress.configure(maximum=event["total"], value=event["completed"])
        elif kind in ("chunk", "check"):
            index = event["index"]
            if kind == "chunk":
                self.statuses[index] = event["status"]
                self.tree.item(str(index), tags=(event["status"], "stripe") if index % 2 else (event["status"],))
                self.details[index] = event.get("detail", "")
                self.tree.set(str(index), "status", event["status"])
                self.tree.set(str(index), "detail", event.get("detail", ""))
                if "preview" in event:
                    self.previews[index] = event["preview"]
            else:
                self.tree.set(str(index), "check", event["check"])
                self.check_details[index] = event.get("detail", "")
            self._update_counts()
            self.schedule_queue_refresh()
            if self.selected() == index:
                self.preview()
        elif kind == "fatal":
            self.summary.set(event["detail"])
            if not self.closing:
                messagebox.showerror("Operation failed", event["detail"], parent=self.root)
        elif kind == "exported":
            self.summary.set(f"Saved {event['path']}")
        elif kind == "finished":
            self.progress.configure(maximum=len(self.statuses))
            self._update_counts("Cancelled. " if event["cancelled"] else "Run finished. ")
        elif kind == "idle":
            self._controls(False)
            if not self.model_choices and not self.models_loading:
                self.refresh_models()

    def _update_counts(self, prefix: str = "") -> None:
        counts = Counter(self.statuses.values())
        names = ("Pending", "Completed", "Resumed", "Retranslated", "Error", "Retry", "Skipped")
        for name in names:
            self.count_vars[name].set(str(counts[name]))
        done = sum(counts[name] for name in ("Completed", "Resumed", "Retranslated", "Skipped"))
        self.summary.set(f"{prefix}{done:,} of {len(self.statuses):,} chunks ready" +
                         (f" · {counts['Error']} need attention" if counts["Error"] else "") +
                         (f" · {self.analysis_excluded:,} glossary exclusions" if getattr(self, "analysis_excluded", 0) else ""))
        self.progress.configure(value=sum(counts[name] for name in
                                          ("Completed", "Resumed", "Retranslated", "Skipped")))

    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.configure(state="normal")
        widget.delete("1.0", "end")
        widget.insert("1.0", text)
        widget.configure(state="disabled")

    def preview(self) -> None:
        index = self.selected()
        if index is None or self.job is None:
            return
        chunk = self.job.chunks[index]
        translated = self.previews.get(index, "")
        if not translated and self.job.chunk_path(index).exists():
            try:
                translated = self.job.read_translation(index)
                self.previews[index] = translated
            except (OSError, ValueError) as exc:
                translated = str(exc)
        detail = self.details.get(index, "")
        check = self.check_details.get(index, "")
        self._set_text(self.source_preview, chunk.text or "[Whitespace-only unit]")
        self._set_text(self.target_preview, translated + (f"\n\nStatus: {detail}" if detail else "")
                       + (f"\n\nProofreading:\n{check}" if check else ""))

    def _poll(self) -> None:
        try:
            for _ in range(200):
                self._handle(self.events.get_nowait())
        except queue.Empty:
            pass
        if self.closing and not self.busy:
            self.root.destroy()
        else:
            self.root.after(100, self._poll)

    def close(self) -> None:
        if self.busy:
            self.closing = True
            self.cancel()
            self.summary.set("Closing after active requests stop; saved chunks are retained.")
        else:
            self.root.destroy()
