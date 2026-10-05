"""Tk widgets are accessed exclusively by the main thread."""
from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog, ttk
from tkinter import font as tkfont

import settings_manager
from extractors import load_book
from lmstudio import LMStudioClient
from settings_manager import Settings, load_settings, save_settings
from translator import Job, TranslationEngine, parse_chunk_selection


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
        self.previews: dict[int, str] = {}
        try:
            settings = load_settings(self.settings_path)
        except (OSError, ValueError, TypeError) as exc:
            logging.getLogger(__name__).exception("Settings could not be loaded")
            settings = Settings()
            messagebox.showwarning("Settings", f"Using defaults: {exc}", parent=root)
        self.values = {name: tk.StringVar(value=str(getattr(settings, name)))
                       for name in ("model", "input_lang", "output_lang", "max_workers",
                                    "retries", "timeout", "api_key")}
        self.proofread = tk.BooleanVar(value=settings.proofread)
        self.source = tk.StringVar(value=settings_manager.last_input_file)
        self.summary = tk.StringVar(value="Choose a book and a local model to begin.")
        self.connection = tk.StringVar(value="LM Studio · connecting")
        self.model_help = tk.StringVar(value="Looking for your local models…")
        self.model_selection = tk.StringVar(value=settings.model)
        self.model_choices: dict[str, str] = {}
        self.models_loading = False
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._poll)
        self.root.after(200, self.refresh_models)

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
        self.root.geometry("1360x900")
        self.root.minsize(1100, 760)
        self._style()
        header = ttk.Frame(self.root, style="Header.TFrame", padding=(24, 18))
        header.pack(fill="x")
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
        model_heading = ttk.Frame(parent, style="Card.TFrame")
        model_heading.grid(row=4, column=0, sticky="ew", pady=(20, 5))
        ttk.Label(model_heading, text="LM Studio model", style="Card.TLabel").pack(side="left")
        self.refresh_button = ttk.Button(model_heading, text="Refresh", command=self.refresh_models)
        self.refresh_button.pack(side="right")
        self.model_combo = ttk.Combobox(parent, textvariable=self.model_selection, width=25,
                                        height=12, state="normal")
        self.model_combo.grid(row=5, column=0, sticky="ew")
        self.model_combo.bind("<<ComboboxSelected>>", self._model_selected)
        ttk.Label(parent, textvariable=self.model_help, style="Muted.TLabel", wraplength=275).grid(
            row=6, column=0, sticky="w", pady=(7, 15))
        fields = ttk.Frame(parent, style="Card.TFrame")
        fields.grid(row=7, column=0, sticky="ew")
        self.option_widgets = [self.model_combo]
        for column, (name, label) in enumerate((("input_lang", "Source language"),
                                               ("output_lang", "Target language"))):
            fields.columnconfigure(column, weight=1, uniform="languages")
            ttk.Label(fields, text=label, style="Card.TLabel").grid(row=0, column=column, sticky="w")
            entry = ttk.Entry(fields, textvariable=self.values[name], width=10)
            entry.grid(row=1, column=column, sticky="ew", padx=(0, 8) if column == 0 else 0, pady=(5, 0))
            self.option_widgets.append(entry)
        ttk.Label(parent, text="Processing", style="Section.TLabel").grid(
            row=8, column=0, sticky="w", pady=(22, 8))
        processing = ttk.Frame(parent, style="Card.TFrame")
        processing.grid(row=9, column=0, sticky="ew")
        for column, name in enumerate(("max_workers", "retries", "timeout")):
            processing.columnconfigure(column, weight=1, uniform="processing")
            label = {"max_workers": "Workers", "retries": "Retries", "timeout": "Timeout (s)"}[name]
            ttk.Label(processing, text=label, style="Muted.TLabel").grid(row=0, column=column, sticky="w")
            entry = ttk.Entry(processing, textvariable=self.values[name], width=5)
            entry.grid(row=1, column=column, sticky="ew", pady=(5, 0), padx=(0, 6))
            self.option_widgets.append(entry)
        ttk.Label(parent, text="API key (optional)", style="Muted.TLabel").grid(
            row=10, column=0, sticky="w", pady=(14, 5))
        key = ttk.Entry(parent, textvariable=self.values["api_key"], show="•")
        key.grid(row=11, column=0, sticky="ew")
        self.option_widgets.append(key)
        self.check_option = ttk.Checkbutton(parent, text="Proofread after translation", variable=self.proofread)
        self.check_option.grid(row=12, column=0, sticky="w", pady=(15, 10))
        ttk.Separator(parent).grid(row=13, column=0, sticky="ew", pady=8)
        ttk.Label(parent, text="Empty, single-character and punctuation-only chunks are skipped automatically.",
                  style="Muted.TLabel", wraplength=270).grid(row=14, column=0, sticky="w", pady=(5, 0))

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
        self.selection_label = ttk.Label(list_card, text="Ctrl/Cmd-click for multiple chunks; Shift-click for a range.",
                                         style="Muted.TLabel")
        self.selection_label.pack(anchor="w", pady=(0, 8))
        list_frame = ttk.Frame(list_card, style="Card.TFrame")
        list_frame.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(list_frame, columns=("unit", "status", "check", "detail"),
                                 show="tree headings", selectmode="extended", height=7)
        for column, title, width in (("#0", "#", 45), ("unit", "SOURCE", 210),
                                     ("status", "STATUS", 105), ("check", "REVIEW", 110),
                                     ("detail", "DETAILS", 180)):
            self.tree.heading(column, text=title)
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

    def refresh_models(self) -> None:
        if self.models_loading or self.busy:
            return
        self.models_loading = True
        self.refresh_button.configure(state="disabled")
        self.connection.set("LM Studio · connecting")
        self.model_help.set("Looking for your local models…")
        # Snapshot Tk variables here, never in the worker thread.
        client = LMStudioClient(Settings(api_key=self.values["api_key"].get().strip()))
        def worker():
            try:
                self.events.put({"type": "models", "models": client.list_models()})
            except Exception as exc:
                logging.getLogger(__name__).warning("Model discovery failed: %s", exc)
                self.events.put({"type": "models", "error": str(exc)})
        threading.Thread(target=worker, daemon=True, name="model-discovery").start()

    def _model_selected(self, event=None) -> None:
        selection = self.model_selection.get().strip()
        self.values["model"].set(self.model_choices.get(selection, selection))

    def _models_received(self, event: dict) -> None:
        self.models_loading = False
        self.refresh_button.configure(state="disabled" if self.busy else "normal")
        if "error" in event:
            self.connection.set("LM Studio · unavailable")
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
        self.connection.set(f"LM Studio · {len(models)} models")
        self.model_help.set("Choose a model or type its ID. Downloaded models need auto-loading enabled or loading in LM Studio."
                            if models else "No language models found. Download a model in LM Studio, then Refresh.")

    def _controls(self, busy: bool) -> None:
        self.busy = busy
        self.refresh_button.configure(state="disabled" if busy or self.models_loading else "normal")
        for widget in [self.browse, self.source_entry, self.check_option, *self.option_widgets]:
            widget.configure(state="disabled" if busy else "normal")
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

    def get_settings(self) -> Settings:
        self._model_selected()
        settings = Settings(**{name: value.get().strip() for name, value in self.values.items()
                               if name not in ("max_workers", "retries", "timeout")},
                            max_workers=int(self.values["max_workers"].get()),
                            retries=int(self.values["retries"].get()),
                            timeout=float(self.values["timeout"].get()),
                            proofread=self.proofread.get())
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
        try:
            settings = self.get_settings()
            filename = self.source.get().strip()
            if not filename:
                raise ValueError("Choose an input file.")
            settings_manager.last_input_file = filename
            save_settings(self.settings_path, settings)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Cannot start", str(exc), parent=self.root)
            return
        self.job = None
        self.engine = None
        self.summary.set("Extracting book…")
        def action():
            job = Job(load_book(filename), settings, self.data_directory / "jobs")
            engine = TranslationEngine(job, self.events.put)
            self.events.put({"type": "loaded", "job": job, "engine": engine})
            engine.run()
        self._background(action)

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
        self.tree.selection_set([str(index) for index in indices])
        self.tree.focus(str(indices[0]))
        self.tree.see(str(indices[0]))
        self.selection_changed()

    def retranslate(self) -> None:
        if self.busy:
            return
        indices = [int(index) for index in self.tree.selection()]
        if not indices or self.engine is None:
            messagebox.showinfo("Select chunks", "Select chunks in the queue or use Select by number first.", parent=self.root)
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
        children = self.tree.get_children()
        if children:
            self.tree.delete(*children)
        self.statuses.clear()
        self.details.clear()
        self.check_details.clear()
        self.previews.clear()
        for chunk in self.job.chunks:
            self.statuses[chunk.index] = "Pending"
            self.tree.insert("", "end", iid=str(chunk.index), text=str(chunk.index),
                             tags=("Pending", "stripe") if chunk.index % 2 else ("Pending",), values=(
                self.job.book.units[chunk.unit_index].label, "Pending", "", ""))
        self.progress.configure(maximum=max(1, len(self.job.chunks)), value=0)
        self._controls(True)
        self._update_counts()
        self._set_text(self.source_preview, "Select a chunk to read its original text.")
        self._set_text(self.target_preview, "Your translation and proofreading notes will appear here.")
        if self.closing:
            self.engine.cancel()

    def _handle(self, event: dict) -> None:
        kind = event["type"]
        if kind == "models":
            self._models_received(event)
        elif kind == "loaded":
            self._loaded(event)
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
            if self.selected() == index:
                self.preview()
        elif kind == "fatal":
            self.summary.set(event["detail"])
            if not self.closing:
                messagebox.showerror("Operation failed", event["detail"], parent=self.root)
        elif kind == "exported":
            self.summary.set(f"Saved {event['path']}")
        elif kind == "finished":
            self._update_counts("Cancelled. " if event["cancelled"] else "Run finished. ")
        elif kind == "idle":
            self._controls(False)

    def _update_counts(self, prefix: str = "") -> None:
        counts = Counter(self.statuses.values())
        names = ("Pending", "Completed", "Resumed", "Retranslated", "Error", "Retry", "Skipped")
        for name in names:
            self.count_vars[name].set(str(counts[name]))
        done = sum(counts[name] for name in ("Completed", "Resumed", "Retranslated", "Skipped"))
        self.summary.set(f"{prefix}{done:,} of {len(self.statuses):,} chunks ready" +
                         (f" · {counts['Error']} need attention" if counts["Error"] else ""))
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
