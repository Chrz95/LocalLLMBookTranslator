import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import subprocess
import sys
import os
import threading
import shutil
import json
from pathlib import Path

import lmstudio
import settings_manager

# Modern launcher UI for the translator application.
# - Tabbed interface (Translate / Chunks / Settings)
# - Dynamic model discovery + health indicator
# - Check-only mode
# - Settings editor (saves to settings.json)
# - Safe background operations (threads)
# - Open chunks / checks folders, view check results list

APP_TITLE = "AI Book Translator — Launcher"
API_BASE = "http://localhost:1234"


def make_translated_path(input_path):
    base, ext = os.path.splitext(input_path)
    return f"{base}-translated{ext}"


def make_incremented_path(path):
    base, ext = os.path.splitext(path)
    i = 1
    candidate = f"{base}-{i}{ext}"
    while os.path.exists(candidate):
        i += 1
        candidate = f"{base}-{i}{ext}"
    return candidate


def list_chunk_files(chunks_dir):
    p = Path(chunks_dir)
    if not p.exists() or not p.is_dir():
        return []
    files = sorted([str(x) for x in p.iterdir() if x.is_file() and x.suffix == ".txt"])
    return files


def list_check_files(checks_dir):
    p = Path(checks_dir)
    if not p.exists() or not p.is_dir():
        return []
    files = sorted([str(x) for x in p.iterdir() if x.is_file() and x.suffix in (".json", ".txt")])
    return files


class LauncherGUI:
    def __init__(self):
        self.settings = settings_manager.load_settings()
        self.root = tk.Tk()
        self.root.title(APP_TITLE)
        self.root.geometry("920x720")
        self._apply_style()

        self._build_header()
        self._build_notebook()
        self._populate_model_list_background()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.mainloop()

    # -------------------------
    # Styling
    # -------------------------
    def _apply_style(self):
        style = ttk.Style(self.root)
        # Use default theme but modernize some colors
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("TNotebook.Tab", padding=(12, 8))
        style.configure("Accent.TButton", foreground="white", background="#007acc")
        style.map("Accent.TButton", background=[("active", "#005f9e")])
        style.configure("Danger.TButton", foreground="white", background="#b00020")
        style.map("Danger.TButton", background=[("active", "#8a0018")])

    # -------------------------
    # Header
    # -------------------------
    def _build_header(self):
        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=12, pady=(12, 6))

        title = ttk.Label(header, text="AI Book Translator", font=("Segoe UI", 18, "bold"))
        title.pack(side="left")

        self.health_canvas = tk.Canvas(header, width=18, height=18, highlightthickness=0)
        self.health_canvas.pack(side="right", padx=(0, 6))
        self.health_circle = self.health_canvas.create_oval(2, 2, 16, 16, fill="gray")
        self.health_label = ttk.Label(header, text="LM Studio: Unknown", font=("Segoe UI", 9))
        self.health_label.pack(side="right", padx=(0, 12))

    # -------------------------
    # Notebook (tabs)
    # -------------------------
    def _build_notebook(self):
        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=12, pady=8)

        self._build_translate_tab()
        self._build_chunks_tab()
        self._build_settings_tab()
        self._build_logs_tab()

    # -------------------------
    # Translate tab
    # -------------------------
    def _build_translate_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="Translate")

        left = ttk.Frame(tab)
        left.pack(side="left", fill="both", expand=True, padx=(12, 6), pady=12)

        right = ttk.Frame(tab, width=320)
        right.pack(side="right", fill="y", padx=(6, 12), pady=12)

        # Input file
        ttk.Label(left, text="Input file", font=("Segoe UI", 10)).pack(anchor="w")
        self.input_entry = ttk.Entry(left)
        self.input_entry.pack(fill="x", pady=6)
        ttk.Button(left, text="Browse", command=self.browse_input).pack(anchor="w")

        # Output file
        ttk.Label(left, text="Output file", font=("Segoe UI", 10)).pack(anchor="w", pady=(12, 0))
        self.output_entry = ttk.Entry(left)
        self.output_entry.pack(fill="x", pady=6)
        ttk.Button(left, text="Browse", command=self.browse_output).pack(anchor="w")

        # File type and languages
        row = ttk.Frame(left)
        row.pack(fill="x", pady=(12, 0))
        ttk.Label(row, text="File type").grid(row=0, column=0, sticky="w")
        self.file_type = tk.StringVar(value=self.settings.get("file_type", "epub"))
        ttk.Combobox(row, textvariable=self.file_type, values=["epub", "pdf", "docx", "txt"], width=10, state="readonly").grid(row=1, column=0, padx=(0, 8))

        ttk.Label(row, text="Input lang").grid(row=0, column=1, sticky="w")
        self.input_lang = tk.StringVar(value=self.settings.get("input_lang", "de"))
        ttk.Combobox(row, textvariable=self.input_lang, values=["de", "en", "el", "fr", "es", "it"], width=8, state="readonly").grid(row=1, column=1, padx=(6, 8))

        ttk.Label(row, text="Output lang").grid(row=0, column=2, sticky="w")
        self.output_lang = tk.StringVar(value=self.settings.get("output_lang", "en"))
        ttk.Combobox(row, textvariable=self.output_lang, values=["en", "de", "el", "fr", "es", "it"], width=8, state="readonly").grid(row=1, column=2, padx=(6, 8))

        # Model selector
        ttk.Label(left, text="Translation model", font=("Segoe UI", 10)).pack(anchor="w", pady=(12, 0))
        model_frame = ttk.Frame(left)
        model_frame.pack(fill="x", pady=6)
        self.model_var = tk.StringVar(value="Loading models...")
        self.model_combo = ttk.Combobox(model_frame, textvariable=self.model_var, values=[self.model_var.get()], state="readonly")
        self.model_combo.pack(side="left", fill="x", expand=True)
        ttk.Button(model_frame, text="Refresh", command=self._populate_model_list_background).pack(side="left", padx=6)

        # Check options
        ttk.Separator(left).pack(fill="x", pady=12)
        self.check_var = tk.BooleanVar(value=self.settings.get("check_translations", False))
        ttk.Checkbutton(left, text="Enable post-check (spelling/grammar/logic)", variable=self.check_var).pack(anchor="w")
        ttk.Label(left, text="Check model").pack(anchor="w", pady=(6, 0))
        self.check_model_var = tk.StringVar(value=self.settings.get("check_model", self.model_var.get()))
        self.check_model_combo = ttk.Combobox(left, textvariable=self.check_model_var, values=[self.check_model_var.get()], state="readonly")
        self.check_model_combo.pack(fill="x", pady=(0, 6))

        # Check-only option
        self.check_only_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(left, text="Check only (do not translate) — run checks on existing chunks", variable=self.check_only_var).pack(anchor="w", pady=(6, 0))

        # LM Studio path
        ttk.Label(left, text="LM Studio path (optional)", font=("Segoe UI", 10)).pack(anchor="w", pady=(12, 0))
        self.lm_entry = ttk.Entry(left)
        self.lm_entry.insert(0, self.settings.get("lm_path", ""))
        self.lm_entry.pack(fill="x", pady=6)
        ttk.Button(left, text="Browse LM Studio executable", command=self.browse_lm).pack(anchor="w")

        # Start button
        btn_frame = ttk.Frame(left)
        btn_frame.pack(fill="x", pady=(18, 6))
        self.start_btn = ttk.Button(btn_frame, text="Start Translation / Check", style="Accent.TButton", command=self.start_process)
        self.start_btn.pack(side="left", padx=(0, 8))
        ttk.Button(btn_frame, text="Open Log", command=self.open_log).pack(side="left")

        # Right column: quick actions & status
        ttk.Label(right, text="Quick actions", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        ttk.Button(right, text="Open chunks folder", command=self.open_chunks_folder).pack(fill="x", pady=6)
        ttk.Button(right, text="Open checks folder", command=self.open_checks_folder).pack(fill="x", pady=6)
        ttk.Button(right, text="List chunk files", command=self.refresh_chunks_list).pack(fill="x", pady=6)
        ttk.Button(right, text="Validate LM Studio", command=self._populate_model_list_background).pack(fill="x", pady=6)

        ttk.Separator(right).pack(fill="x", pady=12)
        ttk.Label(right, text="Launcher status", font=("Segoe UI", 11, "bold")).pack(anchor="w")
        self.status_text = tk.StringVar(value="Idle")
        ttk.Label(right, textvariable=self.status_text, wraplength=280).pack(anchor="w", pady=(6, 0))

    # -------------------------
    # Chunks tab
    # -------------------------
    def _build_chunks_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="Chunks")

        top = ttk.Frame(tab)
        top.pack(fill="x", padx=12, pady=12)
        ttk.Button(top, text="Refresh", command=self.refresh_chunks_list).pack(side="left")
        ttk.Button(top, text="Delete all chunks", style="Danger.TButton", command=self._confirm_clear_chunks).pack(side="left", padx=8)
        ttk.Button(top, text="Run checks on all chunks (no translate)", command=self._run_checks_on_chunks).pack(side="left", padx=8)

        mid = ttk.Frame(tab)
        mid.pack(fill="both", expand=True, padx=12, pady=(6, 12))

        self.chunks_listbox = tk.Listbox(mid, font=("Consolas", 10))
        self.chunks_listbox.pack(side="left", fill="both", expand=True)
        self.chunks_listbox.bind("<Double-Button-1>", self._open_chunk_preview)

        scrollbar = ttk.Scrollbar(mid, orient="vertical", command=self.chunks_listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.chunks_listbox.config(yscrollcommand=scrollbar.set)

        # checks list
        bottom = ttk.Frame(tab)
        bottom.pack(fill="x", padx=12, pady=(0, 12))
        ttk.Label(bottom, text="Check results").pack(anchor="w")
        self.checks_listbox = tk.Listbox(bottom, height=6, font=("Consolas", 10))
        self.checks_listbox.pack(fill="x")
        self.checks_listbox.bind("<Double-Button-1>", self._open_check_preview)

        self.refresh_chunks_list()

    # -------------------------
    # Settings tab
    # -------------------------
    def _build_settings_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="Settings")

        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        # preview length
        ttk.Label(frame, text="Preview length (chars)").grid(row=0, column=0, sticky="w", pady=6)
        self.preview_len_var = tk.IntVar(value=self.settings.get("preview_length", 512))
        ttk.Entry(frame, textvariable=self.preview_len_var, width=12).grid(row=0, column=1, sticky="w", padx=8)

        # chunks dir
        ttk.Label(frame, text="Chunks directory").grid(row=1, column=0, sticky="w", pady=6)
        self.chunks_dir_var = tk.StringVar(value=self.settings.get("chunks_dir", "chunks"))
        ttk.Entry(frame, textvariable=self.chunks_dir_var, width=40).grid(row=1, column=1, sticky="w", padx=8)
        ttk.Button(frame, text="Browse", command=self._browse_chunks_dir).grid(row=1, column=2, padx=6)

        # checks dir
        ttk.Label(frame, text="Checks directory").grid(row=2, column=0, sticky="w", pady=6)
        self.checks_dir_var = tk.StringVar(value=self.settings.get("checks_dir", "checks"))
        ttk.Entry(frame, textvariable=self.checks_dir_var, width=40).grid(row=2, column=1, sticky="w", padx=8)
        ttk.Button(frame, text="Browse", command=self._browse_checks_dir).grid(row=2, column=2, padx=6)

        # max workers
        ttk.Label(frame, text="Max workers (parallel requests)").grid(row=3, column=0, sticky="w", pady=6)
        self.max_workers_var = tk.IntVar(value=self.settings.get("max_workers", 2))
        ttk.Entry(frame, textvariable=self.max_workers_var, width=8).grid(row=3, column=1, sticky="w", padx=8)

        # auto clear / auto increment
        self.auto_clear_var = tk.BooleanVar(value=self.settings.get("auto_clear_chunks", False))
        ttk.Checkbutton(frame, text="Auto clear chunks on new translation", variable=self.auto_clear_var).grid(row=4, column=0, columnspan=2, sticky="w", pady=6)
        self.auto_inc_var = tk.BooleanVar(value=self.settings.get("auto_increment", True))
        ttk.Checkbutton(frame, text="Auto-increment output filename if exists", variable=self.auto_inc_var).grid(row=5, column=0, columnspan=2, sticky="w", pady=6)

        # check translations default
        self.check_translations_default_var = tk.BooleanVar(value=self.settings.get("check_translations", False))
        ttk.Checkbutton(frame, text="Enable post-checks by default", variable=self.check_translations_default_var).grid(row=6, column=0, columnspan=2, sticky="w", pady=6)

        # save button
        ttk.Button(frame, text="Save settings", command=self._save_settings).grid(row=8, column=0, pady=(12, 0))

    # -------------------------
    # Logs tab
    # -------------------------
    def _build_logs_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text="Launcher Logs")

        ttk.Label(tab, text="Launcher log (translator.log)").pack(anchor="w", padx=12, pady=(12, 0))
        self.launcher_log = tk.Text(tab, height=20, bg="#111", fg="#eee", font=("Consolas", 10))
        self.launcher_log.pack(fill="both", expand=True, padx=12, pady=8)
        ttk.Button(tab, text="Refresh log", command=self._refresh_launcher_log).pack(padx=12, pady=(0, 12))

    # -------------------------
    # Background model discovery + health
    # -------------------------
    def _populate_model_list_background(self):
        threading.Thread(target=self._populate_models_and_health, daemon=True).start()

    def _populate_models_and_health(self):
        self._set_status("Checking LM Studio health...")
        healthy = lmstudio.wait_for_api(api_base=API_BASE, timeout=6, poll_interval=1)
        self.root.after(0, lambda: self._set_health(healthy))
        models = []
        if healthy:
            models = lmstudio.get_model_list(api_base=API_BASE, timeout=6)
        if not models:
            models = [self.settings.get("check_model", "qwen2.5-7b-translator")]
        self.root.after(0, lambda: self._set_model_list(models))
        self._set_status("Ready")

    def _set_model_list(self, models):
        self.model_combo.config(values=models)
        self.check_model_combo.config(values=models)
        if not self.model_var.get() or self.model_var.get() == "Loading models...":
            self.model_var.set(models[0] if models else "")
        if not self.check_model_var.get():
            self.check_model_var.set(models[0] if models else "")

    def _set_health(self, healthy):
        if healthy is True:
            color = "green"; text = "LM Studio: OK"
        elif healthy is False:
            color = "red"; text = "LM Studio: DOWN"
        else:
            color = "orange"; text = "LM Studio: Unknown"
        self.health_canvas.itemconfig(self.health_circle, fill=color)
        self.health_label.config(text=text)

    # -------------------------
    # File dialogs and helpers
    # -------------------------
    def browse_input(self):
        file = filedialog.askopenfilename(filetypes=[("EPUB", "*.epub"), ("PDF", "*.pdf"), ("DOCX", "*.docx"), ("Text", "*.txt"), ("All", "*.*")])
        if file:
            self.input_entry.delete(0, tk.END)
            self.input_entry.insert(0, file)
            out = make_translated_path(file)
            if os.path.exists(out) and self.auto_inc_var.get():
                out = make_incremented_path(out)
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, out)

    def browse_output(self):
        file = filedialog.asksaveasfilename(defaultextension=".epub", filetypes=[("EPUB", "*.epub"), ("All", "*.*")])
        if file:
            self.output_entry.delete(0, tk.END)
            self.output_entry.insert(0, file)

    def browse_lm(self):
        file = filedialog.askopenfilename(filetypes=[("Executable", "*.exe"), ("All", "*.*")])
        if file:
            self.lm_entry.delete(0, tk.END)
            self.lm_entry.insert(0, file)
            self._populate_model_list_background()

    def _browse_chunks_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.chunks_dir_var.set(d)

    def _browse_checks_dir(self):
        d = filedialog.askdirectory()
        if d:
            self.checks_dir_var.set(d)

    # -------------------------
    # Actions
    # -------------------------
    def start_process(self):
        # Save some settings first
        self.settings["lm_path"] = self.lm_entry.get().strip()
        self.settings["check_translations"] = bool(self.check_var.get())
        self.settings["check_model"] = str(self.check_model_var.get())
        settings_manager.save_settings(self.settings)

        check_only = bool(self.check_only_var.get())
        lm_path = self.lm_entry.get().strip()
        check_model = self.check_model_var.get().strip()
        model = self.model_var.get().strip()

        if check_only:
            # Launch main.py in check-only mode
            cmd = [sys.executable, "main.py", "--check-only", "--check-model", check_model]
        else:
            input_file = self.input_entry.get().strip()
            output_file = self.output_entry.get().strip()
            if not input_file or not output_file:
                messagebox.showerror("Missing files", "Please select input and output files.")
                return
            cmd = [sys.executable, "main.py", input_file, output_file, "--file-type", self.file_type.get(), "--input-lang", self.input_lang.get(), "--output-lang", self.output_lang.get(), "--model", model]
            if self.check_var.get():
                cmd.extend(["--check-model", check_model])
        if lm_path:
            cmd.extend(["--lm-path", lm_path])

        try:
            subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            messagebox.showinfo("Started", "Process started. Open the Log window to monitor progress.")
        except Exception as e:
            messagebox.showerror("Failed to start", f"Could not start process: {e}")

    def open_log(self):
        # open translator.log in default editor
        path = Path("translator.log")
        if not path.exists():
            messagebox.showinfo("No log", "translator.log not found yet.")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception as e:
            messagebox.showerror("Open log failed", str(e))

    def open_chunks_folder(self):
        d = self.settings.get("chunks_dir", "chunks")
        Path(d).mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(d)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        except Exception as e:
            messagebox.showerror("Open folder failed", str(e))

    def open_checks_folder(self):
        d = self.settings.get("checks_dir", "checks")
        Path(d).mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform.startswith("win"):
                os.startfile(d)
            elif sys.platform == "darwin":
                subprocess.Popen(["open", d])
            else:
                subprocess.Popen(["xdg-open", d])
        except Exception as e:
            messagebox.showerror("Open folder failed", str(e))

    def refresh_chunks_list(self):
        chunks_dir = self.settings.get("chunks_dir", "chunks")
        files = list_chunk_files(chunks_dir)
        self.chunks_listbox.delete(0, tk.END)
        for f in files:
            self.chunks_listbox.insert(tk.END, f)
        checks_dir = self.settings.get("checks_dir", "checks")
        cfiles = list_check_files(checks_dir)
        self.checks_listbox.delete(0, tk.END)
        for f in cfiles:
            self.checks_listbox.insert(tk.END, f)

    def _open_chunk_preview(self, event=None):
        sel = self.chunks_listbox.curselection()
        if not sel:
            return
        path = self.chunks_listbox.get(sel[0])
        try:
            with open(path, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            messagebox.showerror("Read failed", str(e))
            return
        win = tk.Toplevel(self.root)
        win.title(f"Chunk preview — {os.path.basename(path)}")
        txt = tk.Text(win, wrap="word", font=("Consolas", 11))
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", text)
        txt.config(state="disabled")
        btn = ttk.Button(win, text="View check result", command=lambda p=path: self._open_check_for_chunk(p))
        btn.pack(pady=6)

    def _open_check_for_chunk(self, chunk_path):
        # chunk_path ends with /.../<index>.txt
        try:
            idx = int(Path(chunk_path).stem)
        except Exception:
            messagebox.showinfo("No index", "Chunk filename not numeric; cannot find check file.")
            return
        checks_dir = self.settings.get("checks_dir", "checks")
        check_path = os.path.join(checks_dir, f"{idx}.json")
        if not os.path.exists(check_path):
            messagebox.showinfo("No check", f"No check result for chunk {idx}")
            return
        try:
            with open(check_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("Open failed", str(e))
            return
        self._show_json_window(f"Check result — chunk {idx}", data)

    def _open_check_preview(self, event=None):
        sel = self.checks_listbox.curselection()
        if not sel:
            return
        path = self.checks_listbox.get(sel[0])
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror("Read failed", str(e))
            return
        win = tk.Toplevel(self.root)
        win.title(f"Check preview — {os.path.basename(path)}")
        txt = tk.Text(win, wrap="word", font=("Consolas", 11))
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", content)
        txt.config(state="disabled")

    def _show_json_window(self, title, data):
        win = tk.Toplevel(self.root)
        win.title(title)
        txt = tk.Text(win, wrap="word", font=("Consolas", 11))
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", json.dumps(data, ensure_ascii=False, indent=2))
        txt.config(state="disabled")

    def _confirm_clear_chunks(self):
        chunks_dir = self.settings.get("chunks_dir", "chunks")
        files = list_chunk_files(chunks_dir)
        if not files:
            messagebox.showinfo("No chunks", "No chunk files to delete.")
            return
        resp = messagebox.askyesnocancel("Delete chunks", f"Found {len(files)} files in {chunks_dir}.\nYes = Delete all\nNo = Cancel", icon="warning")
        if resp is True:
            try:
                for f in files:
                    os.remove(f)
                messagebox.showinfo("Deleted", f"Deleted {len(files)} files.")
                self.refresh_chunks_list()
            except Exception as e:
                messagebox.showerror("Delete failed", str(e))

    def _run_checks_on_chunks(self):
        # Launch main.py in check-only mode
        check_model = self.check_model_var.get().strip() or self.settings.get("check_model", "")
        cmd = [sys.executable, "main.py", "--check-only", "--check-model", check_model]
        lm_path = self.lm_entry.get().strip()
        if lm_path:
            cmd.extend(["--lm-path", lm_path])
        try:
            subprocess.Popen(cmd, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            messagebox.showinfo("Started", "Check-only process started.")
        except Exception as e:
            messagebox.showerror("Failed", str(e))

    # -------------------------
    # Save settings
    # -------------------------
    def _save_settings(self):
        self.settings["preview_length"] = int(self.preview_len_var.get())
        self.settings["chunks_dir"] = str(self.chunks_dir_var.get())
        self.settings["checks_dir"] = str(self.checks_dir_var.get())
        self.settings["max_workers"] = int(self.max_workers_var.get())
        self.settings["auto_clear_chunks"] = bool(self.auto_clear_var.get())
        self.settings["auto_increment"] = bool(self.auto_inc_var.get())
        self.settings["check_translations"] = bool(self.check_translations_default_var.get())
        settings_manager.save_settings(self.settings)
        messagebox.showinfo("Saved", "Settings saved to settings.json")
        self.refresh_chunks_list()

    # -------------------------
    # Launcher log
    # -------------------------
    def _refresh_launcher_log(self):
        path = Path("translator.log")
        if not path.exists():
            self.launcher_log.delete("1.0", tk.END)
            self.launcher_log.insert("1.0", "No log file yet.")
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
            self.launcher_log.delete("1.0", tk.END)
            self.launcher_log.insert("1.0", content[-20000:])  # keep last chunk
        except Exception as e:
            self.launcher_log.delete("1.0", tk.END)
            self.launcher_log.insert("1.0", f"Failed to read log: {e}")

    # -------------------------
    # Utilities
    # -------------------------
    def _set_status(self, text):
        self.status_text.set(text)

    def _on_close(self):
        self.root.destroy()

