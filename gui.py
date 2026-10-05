"""Tk widgets are accessed exclusively by the main thread."""
from __future__ import annotations

import logging
import queue
import threading
import tkinter as tk
from collections import Counter
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import settings_manager
from extractors import load_book
from settings_manager import Settings, load_settings, save_settings
from translator import Job, TranslationEngine


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
        self.summary = tk.StringVar(value="Choose a book, enter the loaded model name, then start.")
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._poll)

    def _build(self) -> None:
        self.root.title("Offline Literary Translator — LM Studio")
        self.root.geometry("1150x820")
        self.root.minsize(800, 600)
        container = ttk.Frame(self.root, padding=12)
        container.pack(fill="both", expand=True)
        source_row = ttk.Frame(container)
        source_row.pack(fill="x")
        ttk.Label(source_row, text="Input file").pack(side="left")
        self.source_entry = ttk.Entry(source_row, textvariable=self.source)
        self.source_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.browse = ttk.Button(source_row, text="Browse…", command=self.choose_file)
        self.browse.pack(side="left")
        options = ttk.Frame(container)
        options.pack(fill="x", pady=10)
        self.option_widgets = []
        labels = (("model", "Model"), ("input_lang", "From"), ("output_lang", "To"),
                  ("max_workers", "Workers"), ("retries", "Retries"),
                  ("timeout", "Timeout (s)"), ("api_key", "API key (optional)"))
        for column, (name, label) in enumerate(labels):
            ttk.Label(options, text=label).grid(row=0, column=column, sticky="w", padx=3)
            entry = ttk.Entry(options, textvariable=self.values[name],
                              width=22 if name == "model" else 12,
                              show="•" if name == "api_key" else "")
            entry.grid(row=1, column=column, sticky="ew", padx=3)
            self.option_widgets.append(entry)
            options.columnconfigure(column, weight=1)
        self.check_option = ttk.Checkbutton(container, text="Proofread translations (extra local requests)",
                                            variable=self.proofread)
        self.check_option.pack(anchor="w")
        actions = ttk.Frame(container)
        actions.pack(fill="x", pady=8)
        self.buttons = {}
        for name, command in (("Start / load", self.start), ("Pause", self.pause),
                              ("Resume", self.resume), ("Cancel", self.cancel),
                              ("Retranslate chunk", self.retranslate), ("Export", self.export)):
            button = ttk.Button(actions, text=name, command=command)
            button.pack(side="left", padx=(0, 6))
            self.buttons[name] = button
        self.progress = ttk.Progressbar(container, mode="determinate")
        self.progress.pack(fill="x")
        ttk.Label(container, textvariable=self.summary, wraplength=1050).pack(anchor="w", pady=6)
        panes = ttk.Panedwindow(container, orient="vertical")
        panes.pack(fill="both", expand=True)
        list_frame = ttk.Frame(panes)
        self.tree = ttk.Treeview(list_frame, columns=("unit", "status", "check", "detail"),
                                 show="tree headings", selectmode="browse", height=12)
        for column, title, width in (("#0", "Chunk", 65), ("unit", "Source unit", 310),
                                     ("status", "Status", 95), ("check", "Check", 100),
                                     ("detail", "Details", 290)):
            self.tree.heading(column, text=title)
            self.tree.column(column, width=width, stretch=column in ("unit", "detail"))
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda event: self.preview())
        panes.add(list_frame, weight=1)
        preview_frame = ttk.Frame(panes)
        self.source_preview = self._text_panel(preview_frame, "Source", 0)
        self.target_preview = self._text_panel(preview_frame, "Translation / check details", 1)
        panes.add(preview_frame, weight=1)
        self._controls(False)

    @staticmethod
    def _text_panel(parent: ttk.Frame, title: str, column: int) -> tk.Text:
        panel = ttk.LabelFrame(parent, text=title)
        panel.grid(row=0, column=column, sticky="nsew", padx=3)
        parent.columnconfigure(column, weight=1)
        parent.rowconfigure(0, weight=1)
        text = tk.Text(panel, wrap="word", height=12, width=40, state="disabled")
        scroll = ttk.Scrollbar(panel, command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return text

    def _controls(self, busy: bool) -> None:
        self.busy = busy
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
        return int(selection[0]) if selection else None

    def retranslate(self) -> None:
        index = self.selected()
        if index is None or self.engine is None:
            messagebox.showinfo("Select a chunk", "Select one chunk first.", parent=self.root)
            return
        engine = self.engine
        self._background(lambda: engine.retranslate_single_chunk(index))

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
            self.tree.insert("", "end", iid=str(chunk.index), text=str(chunk.index), values=(
                self.job.book.units[chunk.unit_index].label, "Pending", "", ""))
        self.progress.configure(maximum=max(1, len(self.job.chunks)), value=0)
        self._controls(True)
        if self.closing:
            self.engine.cancel()

    def _handle(self, event: dict) -> None:
        kind = event["type"]
        if kind == "loaded":
            self._loaded(event)
        elif kind in ("chunk", "check"):
            index = event["index"]
            if kind == "chunk":
                self.statuses[index] = event["status"]
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
        self.summary.set(prefix + "  |  ".join(f"{name}: {counts[name]}" for name in names))
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
