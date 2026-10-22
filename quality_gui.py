"""Main-thread quality tools with background inference and explicit acceptance."""
from __future__ import annotations

import queue
import json
import threading
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import messagebox, ttk

from quality import GLOSSARY_CATEGORIES, GLOSSARY_CATEGORY_LABELS, QualityProfile
from novel_analysis import excluded_suggestions


class QualityWindows:
    def __init__(self, app):
        self.app = app

    def dialog(self, title: str, width: int = 1000, height: int = 720):
        dialog = tk.Toplevel(self.app.root)
        dialog.title(title)
        self.app._size_dialog(dialog, width, height)
        dialog.transient(self.app.root)
        dialog.grab_set()
        panel = ttk.Frame(dialog, padding=16)
        panel.pack(fill="both", expand=True)
        footer = ttk.Frame(panel)
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        return dialog, panel, footer

    @staticmethod
    def text(parent, value="", height=12, readonly=False):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True, pady=6)
        text = tk.Text(frame, wrap="word", height=height, undo=True, padx=10, pady=10,
                       font="TkDefaultFont", relief="flat")
        scrollbar = ttk.Scrollbar(frame, command=text.yview)
        text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        text.pack(fill="both", expand=True)
        text.insert("1.0", value)
        if readonly:
            text.configure(state="disabled")
        return text

    def open(self):
        if self.app.busy or self.app.job is None:
            return
        dialog, panel, footer = self.dialog("Translation quality tools", 740, 560)
        ttk.Label(panel, text="Improve meaning, consistency and literary voice", style="Section.TLabel").pack(anchor="w", pady=8)
        ttk.Label(panel, text="Guidance affects future requests. Proposals require explicit acceptance.", wraplength=680).pack(anchor="w", pady=8)
        for label, action in (("Book glossary, characters, style and examples", self.profile),
                              ("Reanalyze whole novel / refresh AI glossary", self.analyze),
                              ("Review excluded glossary suggestions", self.excluded),
                              ("Review empty translations", self.empty),
                              ("Check selected chunks for lost meaning", self.check_selected),
                              ("Propose revision for selected chunk", lambda: self.proposals(False)),
                              ("Compare two models on selected chunk", lambda: self.proposals(True)),
                              ("View / restore selected chunk's history", self.history)):
            def launch(action=action):
                dialog.destroy()
                action()
            ttk.Button(panel, text=label, command=launch).pack(fill="x", pady=3)
        ttk.Button(footer, text="Close", command=dialog.destroy).pack(side="right")

    def analyze(self):
        try:
            engine = self.current_engine()
        except (OSError, ValueError, RuntimeError) as exc:
            messagebox.showerror("Cannot analyze novel", str(exc), parent=self.app.root)
            return
        self.app._background(lambda: engine.analyze_novel(force=True))

    def excluded(self):
        try:
            rows = excluded_suggestions(self.app.job.directory)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Cannot read excluded suggestions", str(exc), parent=self.app.root)
            return
        dialog, panel, footer = self.dialog("Excluded glossary suggestions", 1100, 740)
        ttk.Label(panel, text=f"{len(rows):,} excluded suggestions · not used for translation",
                  style="Section.TLabel").pack(anchor="w", pady=6)
        ttk.Label(panel, text="These candidates failed validation. Includes earlier scans/models. "
                  "Select a row to inspect its source passage and model suggestion. "
                  "Verified entries remain in Book glossary.", wraplength=1000).pack(anchor="w", pady=6)
        frame = ttk.Frame(panel)
        frame.pack(fill="both", expand=True, pady=6)
        tree = ttk.Treeview(frame, columns=("passage", "entry", "model", "reason"), show="headings", height=9)
        for key, label, width in (("passage", "Passage", 70), ("entry", "Entry", 65),
                                  ("model", "Model", 220), ("reason", "Reason excluded", 580)):
            tree.heading(key, text=label)
            tree.column(key, width=width, stretch=key in ("model", "reason"))
        scroll = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        for index, row in enumerate(rows):
            tree.insert("", "end", iid=str(index), values=(row["passage_number"], row.get("entry_number", ""),
                                                          row["model"], row["reason"]))
        preview = self.text(panel, "Select an excluded suggestion to inspect it.", height=10, readonly=True)
        def selected(event=None):
            if tree.selection():
                row = rows[int(tree.selection()[0])]
                self.app._set_text(preview, f"Reason: {row['reason']}\n\nExcluded model suggestion:\n" +
                                   json.dumps(row.get("entry"), ensure_ascii=False, indent=2) +
                                   "\n\nAnalyzed source passage:\n" + row["passage"])
        tree.bind("<<TreeviewSelect>>", selected)
        ttk.Button(footer, text="Close", command=dialog.destroy).pack(side="right")

    def one_selected(self):
        index = self.app.selected()
        if index is None or len(self.app.tree.selection()) != 1:
            raise ValueError("Select exactly one chunk in the translation queue first.")
        return index

    def profile(self):
        app = self.app
        try:
            current = app.job.quality_profile()
        except (OSError, ValueError, TypeError, KeyError) as exc:
            messagebox.showerror("Cannot read book guidance", str(exc), parent=app.root)
            return
        dialog, panel, footer = self.dialog("Book translation guidance", 1040, 760)
        notebook = ttk.Notebook(panel)
        notebook.pack(fill="both", expand=True)
        tabs = {}
        for name in ("Context", "Glossary", "Characters & style", "Approved examples"):
            tabs[name] = ttk.Frame(notebook, padding=12)
            notebook.add(tabs[name], text=name)
        enabled = tk.BooleanVar(value=current.use_context)
        radius = tk.StringVar(value=str(current.neighboring_chunks))
        budget = tk.StringVar(value=str(current.context_chars))
        tab = tabs["Context"]
        ttk.Checkbutton(tab, text="Use surrounding source and EPUB paragraph context", variable=enabled).pack(anchor="w", pady=12)
        for label, variable, lower, upper in (("Neighboring chunks on each side (0–5)", radius, 0, 5),
                                               ("Source context character budget (1,000–24,000)", budget, 1000, 24000)):
            ttk.Label(tab, text=label).pack(anchor="w", pady=(12, 5))
            ttk.Spinbox(tab, textvariable=variable, from_=lower, to=upper, width=12).pack(anchor="w")
        ttk.Label(tab, text="EPUB fragments in a paragraph are translated together when possible.\n"
                  "Only requested chunk IDs may be returned; tags and files keep their original structure.\n"
                  "Large paragraphs are bounded by the context budget. Neighboring translated text is not\n"
                  "used, so parallel workers cannot introduce timing-dependent context.\n\n"
                  "The source-under-four-characters rule still applies, including forced retranslation.\n"
                  "Guidance and context increase the model's input size; allow enough context tokens in your selected provider.",
                  wraplength=900).pack(anchor="w", pady=20)
        entries = [dict(e) for e in current.glossary]
        category_choices = dict(zip(GLOSSARY_CATEGORY_LABELS, GLOSSARY_CATEGORIES))
        category_labels = dict(zip(GLOSSARY_CATEGORIES, GLOSSARY_CATEGORY_LABELS))
        glossary_tab = tabs["Glossary"]
        editor = ttk.Frame(glossary_tab)
        editor.pack(side="bottom", fill="x", pady=8)
        columns = ("source", "target", "category", "notes", "occurrences")
        list_frame = ttk.Frame(glossary_tab)
        list_frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="extended", height=9)
        for column, label, width in (("source", "Source term", 160), ("target", "Preferred translation", 160),
                                     ("category", "Category", 140), ("notes", "Treatment / uncertainty", 260),
                                     ("occurrences", "Exact matches", 90)):
            tree.heading(column, text=label)
            tree.column(column, width=width)
        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        tree.pack(fill="both", expand=True)
        values = {key: tk.StringVar(value=category_labels["other"] if key == "category" else "")
                  for key in ("source", "target", "category", "notes", "evidence")}
        for number, (key, label) in enumerate((("source", "Source"), ("target", "Target"),
                                              ("category", "Category"), ("notes", "Notes"), ("evidence", "Source evidence"))):
            ttk.Label(editor, text=label).grid(row=number, column=0, sticky="w", padx=(0, 10), pady=4)
            field = (ttk.Combobox(editor, textvariable=values[key], values=GLOSSARY_CATEGORY_LABELS, state="readonly")
                     if key == "category" else ttk.Entry(editor, textvariable=values[key]))
            field.grid(row=number, column=1, sticky="ew", pady=4)
        editor.columnconfigure(1, weight=1)
        def reload_terms():
            tree.delete(*tree.get_children())
            for i, entry in enumerate(entries):
                tree.insert("", "end", iid=str(i), values=tuple(category_labels[entry.get("category", "other")]
                            if k == "category" else entry.get(k, "") for k in columns))
        def select_term(event=None):
            selection = tree.selection()
            if len(selection) == 1:
                for key, variable in values.items():
                    entry = entries[int(selection[0])]
                    variable.set(category_labels[entry.get("category", "other")] if key == "category" else entry.get(key, ""))
        def put_term(update=False):
            entry = {key: variable.get().strip() for key, variable in values.items()}
            entry["category"] = category_choices[entry["category"]]
            try:
                result = list(entries)
                if update:
                    if len(tree.selection()) != 1:
                        raise ValueError("Select one glossary entry to update.")
                    index = int(tree.selection()[0])
                    previous = entries[index]
                    entry = dict(previous, **entry, origin="manual")
                    if entry["source"] != previous["source"]:
                        entry.pop("occurrences", None)
                    result[index] = entry
                else:
                    result.append(entry)
                replace(current, glossary=result).validate()
            except ValueError as exc:
                messagebox.showerror("Invalid glossary entry", str(exc), parent=dialog)
                return
            entries[:] = result
            reload_terms()
        def delete_terms():
            selected = {int(i) for i in tree.selection()}
            entries[:] = [e for i, e in enumerate(entries) if i not in selected]
            reload_terms()
        controls = ttk.Frame(editor)
        controls.grid(row=5, column=0, columnspan=2, sticky="ew", pady=6)
        for label, action in (("Add", put_term), ("Update selected", lambda: put_term(True)), ("Delete selected", delete_terms)):
            ttk.Button(controls, text=label, command=action).pack(side="left", padx=(0, 8))
        tree.bind("<<TreeviewSelect>>", select_term)
        reload_terms()
        ttk.Label(glossary_tab, text="AI suggestions are applied before translation. Review names, proposed renderings and evidence. "
                  "Exact matches count literal occurrences, not aliases or metaphor variants.", wraplength=900).pack(anchor="w", pady=6)
        notes_tab = tabs["Characters & style"]
        ttk.Label(notes_tab, text="Character facts: names, pronouns, relationships and who is speaking").pack(anchor="w")
        characters = self.text(notes_tab, current.character_notes, height=8)
        ttk.Label(notes_tab, text="Style guide: voice, register, dialogue, dialect and punctuation").pack(anchor="w")
        style = self.text(notes_tab, current.style_guide, height=8)
        examples = [dict(e) for e in current.examples]
        example_tab = tabs["Approved examples"]
        example_actions = ttk.Frame(example_tab)
        example_actions.pack(side="bottom", fill="x", pady=8)
        example_tree = ttk.Treeview(example_tab, columns=("source", "translation"), show="headings", selectmode="extended")
        for key, label in (("source", "Approved source"), ("translation", "Your approved translation")):
            example_tree.heading(key, text=label)
            example_tree.column(key, width=430)
        example_tree.pack(fill="both", expand=True)
        def reload_examples():
            example_tree.delete(*example_tree.get_children())
            for i, entry in enumerate(examples):
                example_tree.insert("", "end", iid=str(i), values=(entry["source"][:180], entry["translation"][:180]))
        def approve_selected():
            try:
                index = self.one_selected()
                source, translation = app.job.chunks[index].text, app.job.read_translation(index)
                if not translation.strip():
                    raise ValueError("Empty translations cannot be approved as examples.")
                examples[:] = [e for e in examples if e["source"] != source] + [{"source": source, "translation": translation}]
                reload_examples()
            except (OSError, ValueError) as exc:
                messagebox.showerror("Cannot approve example", str(exc), parent=dialog)
        def delete_examples():
            selected = {int(i) for i in example_tree.selection()}
            examples[:] = [e for i, e in enumerate(examples) if i not in selected]
            reload_examples()
        ttk.Button(example_actions, text="Approve selected queue chunk", command=approve_selected).pack(side="left")
        ttk.Button(example_actions, text="Delete selected examples", command=delete_examples).pack(side="left", padx=8)
        ttk.Label(example_tab, text="Up to 3 relevant examples guide future requests; translations are never reused automatically.", wraplength=900).pack(anchor="w", pady=8)
        reload_examples()
        def save():
            try:
                profile = QualityProfile(enabled.get(), int(radius.get()), int(budget.get()), entries,
                                         characters.get("1.0", "end-1c"), style.get("1.0", "end-1c"), examples)
                app.engine.save_quality_profile(profile)
            except (OSError, ValueError, RuntimeError) as exc:
                messagebox.showerror("Cannot save guidance", str(exc), parent=dialog)
                return
            app.summary.set("Book guidance saved. Existing translations remain until you retranslate them.")
            dialog.destroy()
        ttk.Button(footer, text="Save book guidance", command=save, style="Primary.TButton").pack(side="right")
        ttk.Button(footer, text="Cancel", command=dialog.destroy).pack(side="right", padx=8)

    def empty(self):
        app = self.app
        try:
            rows = app.job.empty_translations()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Cannot review empty translations", str(exc), parent=app.root)
            return
        dialog, panel, footer = self.dialog("Review empty translations", 1000, 560)
        ttk.Label(panel, text="Empty chunk files preserve your existing blank-output rules.", style="Section.TLabel").pack(anchor="w", pady=8)
        ttk.Label(panel, text="A saved empty file alone cannot distinguish model output from a manual blank edit.\n"
                  "No additional resume markers are created. Short-source chunks stay skipped even on retry.").pack(anchor="w", pady=8)
        tree = ttk.Treeview(panel, columns=("index", "reason", "source"), show="headings", selectmode="extended")
        for key, title, width in (("index", "Chunk", 70), ("reason", "Reason", 390), ("source", "Source text", 460)):
            tree.heading(key, text=title)
            tree.column(key, width=width)
        tree.pack(fill="both", expand=True)
        for index, reason in rows:
            tree.insert("", "end", iid=str(index), values=(index, reason, app.job.chunks[index].text[:160]))
        def jump():
            if tree.selection():
                app.select_queue_chunks([int(tree.selection()[0])])
                dialog.destroy()
        def retry():
            if not tree.selection():
                return
            try:
                app.engine.save_quality_profile(replace(app.job.quality_profile(), use_context=True))
            except (OSError, ValueError, RuntimeError) as exc:
                messagebox.showerror("Cannot enable context", str(exc), parent=dialog)
                return
            app.select_queue_chunks([int(i) for i in tree.selection()])
            dialog.destroy()
            app.retranslate()
        def report():
            from tkinter import filedialog
            path = filedialog.asksaveasfilename(parent=dialog, defaultextension=".txt", initialfile="empty_translation_report.txt")
            if path:
                from settings_manager import atomic_write
                try:
                    atomic_write(Path(path), "\n\n".join(f"Chunk {i}: {reason}\n{app.job.chunks[i].text}" for i, reason in rows))
                except OSError as exc:
                    messagebox.showerror("Cannot save report", str(exc), parent=dialog)
        for label, action in (("Open chunk", jump), ("Retry selected with context", retry), ("Save report", report)):
            ttk.Button(footer, text=label, command=action).pack(side="left", padx=(0, 8))
        ttk.Button(footer, text="Close", command=dialog.destroy).pack(side="right")

    def check_selected(self):
        indices = [int(i) for i in self.app.tree.selection()]
        if not indices:
            messagebox.showinfo("Select chunks", "Select chunks in the queue first.", parent=self.app.root)
            return
        try:
            engine = self.current_engine()
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Cannot configure quality check", str(exc), parent=self.app.root)
            return
        self.app._background(lambda: engine.check_chunks(indices))

    def current_engine(self):
        from translator import TranslationEngine
        self.app.job.configure_translation(self.app.get_settings())
        self.app.engine = TranslationEngine(self.app.job, self.app.events.put)
        return self.app.engine

    def proposals(self, comparison=False):
        app = self.app
        try:
            index = self.one_selected()
            engine = self.current_engine()
            if len(app.job.chunks[index].text.strip()) < 4:
                raise ValueError("Source chunks shorter than four characters remain skipped.")
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showinfo("Choose a chunk", str(exc), parent=app.root)
            return
        dialog, panel, footer = self.dialog("Compare models" if comparison else "Review revision proposal", 1180, 820)
        ttk.Label(panel, text=f"Chunk {index} · Source text", style="Section.TLabel").pack(anchor="w")
        self.text(panel, app.job.chunks[index].text, height=4, readonly=True)
        choices = ttk.Frame(panel)
        choices.pack(fill="x", pady=8)
        model_a = tk.StringVar(value=app.values["model"].get())
        models = list(dict.fromkeys(app.model_choices.values()))
        model_b = tk.StringVar(value=next((model for model in models if model != model_a.get()), ""))
        model_fields = [("Model A" if comparison else "Revision model", model_a)]
        if comparison:
            model_fields.append(("Model B", model_b))
        for label, variable in model_fields:
            ttk.Label(choices, text=label).pack(side="left", padx=(0, 6))
            ttk.Combobox(choices, textvariable=variable, values=models, width=30).pack(side="left", padx=(0, 12))
        status = tk.StringVar(value="Proposals never overwrite a chunk until you accept one.")
        ttk.Label(panel, textvariable=status, wraplength=1050).pack(anchor="w", pady=6)
        panes = ttk.Frame(panel)
        panes.pack(fill="both", expand=True)
        draft = app._text_panel(panes, "Candidate A" if comparison else "Current translation", 0)
        proposed = app._text_panel(panes, "Candidate B" if comparison else "Proposed revision", 1)
        try:
            original = app.job.read_translation(index) if app.job.chunk_path(index).exists() else ""
        except (OSError, ValueError):
            original = ""
        app._set_text(draft, "" if comparison else original)
        state = {"running": False, "proposals": [], "after": None}
        messages = queue.Queue()
        def close():
            if not state["running"]:
                if state["after"] is not None:
                    dialog.after_cancel(state["after"])
                dialog.destroy()
        def accept(number):
            try:
                app.engine.apply_proposal(state["proposals"][number])
            except (OSError, ValueError, RuntimeError, IndexError) as exc:
                messagebox.showerror("Cannot accept proposal", str(exc), parent=dialog)
                return
            dialog.destroy()
        def poll():
            state["after"] = None
            try:
                results, error = messages.get_nowait()
            except queue.Empty:
                state["after"] = dialog.after(60, poll)
                return
            state["running"] = False
            app._controls(False)
            generate.configure(state="normal")
            close_button.configure(state="normal")
            if error:
                status.set(error)
                return
            state["proposals"] = results
            if comparison:
                app._set_text(draft, results[0]["after"])
                app._set_text(proposed, results[1]["after"])
                status.set("Candidates are shown in randomized order. Review meaning and voice before accepting.")
            else:
                app._set_text(proposed, results[0]["after"])
                import difflib
                for widget, old_side in ((draft, True), (proposed, False)):
                    widget.tag_configure("revision_change", background="#ffe69a")
                    for tag, a, b, c, d in difflib.SequenceMatcher(None, original, results[0]["after"]).get_opcodes():
                        if tag != "equal":
                            start, end = (a, b) if old_side else (c, d)
                            widget.tag_add("revision_change", f"1.0+{start}c", f"1.0+{end}c")
                status.set("Changes are highlighted. An empty proposal removes translated text if accepted.")
            for button in accept_buttons:
                button.configure(state="normal")
            reveal.configure(state="normal" if comparison else "disabled")
        def start():
            if state["running"]:
                return
            selected_models = [model_a.get().strip(), model_b.get().strip()]
            if not selected_models[0] or (comparison and not selected_models[1]):
                status.set("Enter the model ID(s) first.")
                return
            state["running"] = True
            state["proposals"] = []
            app._controls(True)
            for button in [generate, close_button, reveal, *accept_buttons]:
                button.configure(state="disabled")
            status.set("Comparing models…" if comparison else "Preparing source-aware revision…")
            def worker():
                try:
                    result = (engine.compare_models(index, selected_models) if comparison else
                              [engine.propose_revision(index, selected_models[0])])
                    messages.put((result, None))
                except Exception as exc:
                    messages.put((None, str(exc)))
            # Closed dialog callbacks can form cycles containing Tk variables.
            # Collect them on the UI thread before a worker allocates objects.
            import gc
            gc.collect()
            threading.Thread(target=worker, daemon=False, name="quality-proposal").start()
            state["after"] = dialog.after(60, poll)
        def reveal_models():
            status.set(" · ".join(f"Candidate {chr(65+i)}: {p['model']}" for i, p in enumerate(state["proposals"])))
        generate = ttk.Button(footer, text="Compare models" if comparison else "Generate revision", command=start)
        generate.pack(side="left")
        reveal = ttk.Button(footer, text="Reveal models", command=reveal_models, state="disabled")
        if comparison:
            reveal.pack(side="left", padx=8)
        accept_buttons = []
        for number in range(2 if comparison else 1):
            button = ttk.Button(footer, text=f"Accept candidate {chr(65+number)}" if comparison else "Accept revision",
                                command=lambda number=number: accept(number), state="disabled", style="Primary.TButton")
            button.pack(side="right", padx=(8, 0))
            accept_buttons.append(button)
        close_button = ttk.Button(footer, text="Close", command=close)
        close_button.pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", close)

    def history(self):
        app = self.app
        try:
            index = self.one_selected()
        except ValueError as exc:
            messagebox.showinfo("Select one chunk", str(exc), parent=app.root)
            return
        dialog, panel, footer = self.dialog(f"Chunk {index} · Translation history", 950, 700)
        paths = sorted((app.job.directory/"history"/str(index)).glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
        tree = ttk.Treeview(panel, columns=("time",), show="headings", height=6)
        tree.heading("time", text="Saved previous version (newest first)")
        tree.pack(fill="x")
        from datetime import datetime
        for i, path in enumerate(paths):
            tree.insert("", "end", iid=str(i), values=(datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),))
        preview = self.text(panel, "Select a previous version. Restoring it also saves the current version.", readonly=True)
        state = {"value": None}
        def selected(event=None):
            try:
                state["value"] = paths[int(tree.selection()[0])].read_text(encoding="utf-8")
                app._set_text(preview, state["value"])
            except (OSError, ValueError, IndexError) as exc:
                state["value"] = None
                app._set_text(preview, str(exc))
        def restore():
            if state["value"] is None:
                return
            try:
                app.engine.save_chunk_edit(index, state["value"])
            except (OSError, ValueError, RuntimeError) as exc:
                messagebox.showerror("Cannot restore version", str(exc), parent=dialog)
                return
            dialog.destroy()
        tree.bind("<<TreeviewSelect>>", selected)
        ttk.Button(footer, text="Restore selected version", command=restore, style="Primary.TButton").pack(side="right")
        ttk.Button(footer, text="Close", command=dialog.destroy).pack(side="right", padx=8)
