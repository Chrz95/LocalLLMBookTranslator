import tkinter as tk
from tkinter import ttk, messagebox
import queue
import os
import json
from pathlib import Path

STATUS_COLORS = {
    "pending": "gray",
    "completed": "green",
    "resumed": "blue",
    "retry": "orange",
    "error": "red",
    "skipped": "purple",
    "retranslated": "cyan",
    "checked": "darkgreen",
    "issues": "red",
}

class LogWindow:
    def __init__(self, root, path="translator.log", poll_interval=1000, max_lines=2000):
        self.root = root
        self.path = path
        self.poll_interval = poll_interval
        self.max_lines = max_lines
        self.offset = 0

        self.win = tk.Toplevel(root)
        self.win.title("Translator Log")
        self.win.geometry("900x600")
        self.win.configure(bg="#1e1e1e")

        self.text = tk.Text(self.win, wrap="word", bg="#111", fg="#eee", font=("Consolas", 11))
        self.text.pack(fill="both", expand=True)

        self._tail_once()

    def _tail_once(self):
        try:
            if not os.path.exists(self.path):
                self.win.after(self.poll_interval, self._tail_once)
                return

            with open(self.path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(self.offset)
                new = f.read()
                self.offset = f.tell()

            if new:
                self.text.insert(tk.END, new)
                lines = int(self.text.index('end-1c').split('.')[0])
                if lines > self.max_lines:
                    delete_to = f"{lines - self.max_lines}.0"
                    self.text.delete("1.0", delete_to)
                self.text.see(tk.END)
        except Exception:
            pass
        finally:
            self.win.after(self.poll_interval, self._tail_once)


class ProgressGUI:
    def __init__(self, total_chunks, retranslate_callback=None, poll_interval=100):
        self.total = total_chunks
        self.retranslate_callback = retranslate_callback
        self.paused = False
        self.cancelled = False

        self.chunk_preview = [""] * total_chunks
        self.chunk_status = ["pending"] * total_chunks
        self.completed_count = 0

        self._ui_queue = queue.Queue()

        bg = "#1e1e1e"
        fg = "#ffffff"

        self.root = tk.Tk()
        self.root.title("AI Book Translator")
        self.root.geometry("900x700")
        self.root.configure(bg=bg)

        ttk_style = ttk.Style(self.root)
        try:
            ttk_style.theme_use("clam")
        except Exception:
            pass

        header = ttk.Frame(self.root)
        header.pack(fill="x", padx=12, pady=12)
        ttk.Label(header, text="Translation Progress", font=("Segoe UI", 16, "bold")).pack(side="left")

        bar_frame = ttk.Frame(self.root)
        bar_frame.pack(fill="x", padx=12)
        self.progress = ttk.Progressbar(bar_frame, length=700, mode="determinate")
        self.progress["maximum"] = total_chunks
        self.progress.pack(side="left", padx=6, pady=6)
        self.percent_label = ttk.Label(bar_frame, text="0%")
        self.percent_label.pack(side="left", padx=6)

        self.status = ttk.Label(self.root, text="", font=("Segoe UI", 10))
        self.status.pack(pady=(6, 12))

        ctrl = ttk.Frame(self.root)
        ctrl.pack(pady=6)
        self.pause_btn = ttk.Button(ctrl, text="Pause", command=self.toggle_pause)
        self.pause_btn.pack(side="left", padx=6)
        self.cancel_btn = ttk.Button(ctrl, text="Cancel", command=self.cancel)
        self.cancel_btn.pack(side="left", padx=6)
        ttk.Button(ctrl, text="Show Log", command=lambda: LogWindow(self.root)).pack(side="left", padx=6)

        frame = ttk.Frame(self.root)
        frame.pack(fill="both", expand=True, padx=12, pady=12)

        self.chunk_list = tk.Listbox(frame, font=("Consolas", 11))
        self.chunk_list.pack(side="left", fill="both", expand=True)
        self.chunk_list.bind("<Double-Button-1>", self._on_chunk_double)

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=self.chunk_list.yview)
        scrollbar.pack(side="right", fill="y")
        self.chunk_list.config(yscrollcommand=scrollbar.set)

        for i in range(total_chunks):
            self.chunk_list.insert(tk.END, f"Chunk {i+1}: pending")

        self.root.after(poll_interval, self._process_queue)

    # ---------------------------
    # queue interface
    # ---------------------------
    def enqueue_update(self, current_chunk, message="", status="completed", chunk_text=None):
        self._ui_queue.put((current_chunk, message, status, chunk_text))

    def _process_queue(self):
        processed = 0
        while not self._ui_queue.empty() and processed < 80:
            try:
                current_chunk, message, status, chunk_text = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            self._apply_update(current_chunk, message, status, chunk_text)
            processed += 1
        self.root.after(100, self._process_queue)

    def _apply_update(self, current_chunk, message, status, chunk_text):
        if current_chunk < 1 or current_chunk > self.total:
            return
        idx = current_chunk - 1

        if chunk_text is not None:
            self.chunk_preview[idx] = chunk_text[:1024]

        prev_status = self.chunk_status[idx]
        self.chunk_status[idx] = status

        self.chunk_list.delete(idx)
        self.chunk_list.insert(idx, f"Chunk {idx+1}: {status}")
        self.chunk_list.itemconfig(idx, {'fg': STATUS_COLORS.get(status, "gray")})

        completed_states = ("completed", "resumed", "retranslated", "checked")
        if prev_status not in completed_states and status in completed_states:
            self.completed_count += 1
        elif prev_status in completed_states and status not in completed_states:
            self.completed_count = max(0, self.completed_count - 1)

        self.progress["value"] = self.completed_count
        percent = int((self.completed_count / self.total) * 100) if self.total else 0
        self.percent_label.config(text=f"{percent}%")

        self.status.config(text=message)

    def update(self, current_chunk, message="", status="completed", chunk_text=None):
        if tk._default_root and tk._default_root == self.root:
            self._apply_update(current_chunk, message, status, chunk_text)
        else:
            self.enqueue_update(current_chunk, message, status, chunk_text)

    def toggle_pause(self):
        self.paused = not self.paused
        self.pause_btn.config(text="Resume" if self.paused else "Pause")
        self.status.config(text="Paused" if self.paused else "Running")

    def cancel(self):
        if messagebox.askyesno("Cancel", "Are you sure you want to cancel translation?"):
            self.cancelled = True
            self.status.config(text="Cancelled")

    def is_paused(self):
        return self.paused

    def is_cancelled(self):
        return self.cancelled

    def _on_chunk_double(self, event):
        sel = self.chunk_list.curselection()
        if not sel:
            return
        idx = sel[0]
        preview = self.chunk_preview[idx] or "(No preview available)"
        win = tk.Toplevel(self.root)
        win.title(f"Chunk {idx+1}")
        txt = tk.Text(win, wrap="word", font=("Consolas", 11))
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", preview)
        txt.config(state="disabled")

        btn_frame = ttk.Frame(win)
        btn_frame.pack(fill="x", pady=8)
        if self.retranslate_callback:
            ttk.Button(btn_frame, text="Retranslate", command=lambda i=idx: self.root.after(0, lambda: self.retranslate_callback(i))).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="View full chunk file", command=lambda i=idx: self._open_full_chunk(i)).pack(side="left", padx=6)
        ttk.Button(btn_frame, text="View check result", command=lambda i=idx: self._open_check_result(i)).pack(side="left", padx=6)

    def _open_full_chunk(self, idx):
        path = Path("chunks") / f"{idx}.txt"
        if not path.exists():
            messagebox.showinfo("Not found", "Full chunk file not found on disk.")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror("Read failed", str(e))
            return
        win = tk.Toplevel(self.root)
        win.title(f"Full chunk {idx}")
        txt = tk.Text(win, wrap="word", font=("Consolas", 11))
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", content)
        txt.config(state="disabled")

    def _open_check_result(self, idx):
        path = Path("checks") / f"{idx}.json"
        if not path.exists():
            messagebox.showinfo("No check", "No check result for this chunk.")
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("Open failed", str(e))
            return
        win = tk.Toplevel(self.root)
        win.title(f"Check result — chunk {idx}")
        txt = tk.Text(win, wrap="word", font=("Consolas", 11))
        txt.pack(fill="both", expand=True)
        txt.insert("1.0", json.dumps(data, ensure_ascii=False, indent=2))
        txt.config(state="disabled")

    def force_complete(self):
        self.completed_count = self.total
        self.progress["value"] = self.total
        self.percent_label.config(text="100%")
        self.status.config(text="Translation complete")
