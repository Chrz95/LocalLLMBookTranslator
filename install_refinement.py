"""Install translation refinement while preserving the existing application."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime
import json
import os
from pathlib import Path
import tempfile
import textwrap


class Editor:
    def __init__(self, source: str):
        self.source = source

    @property
    def tree(self):
        return ast.parse(self.source)

    def bounds(self, node):
        lines = self.source.encode("utf-8").splitlines(keepends=True)
        return (sum(map(len, lines[:node.lineno - 1])) + node.col_offset,
                sum(map(len, lines[:node.end_lineno - 1])) + node.end_col_offset)

    def replace(self, start, end, value):
        data = self.source.encode("utf-8")
        self.source = (data[:start] + value.encode("utf-8") + data[end:]).decode("utf-8")

    def node(self, node, value):
        self.replace(*self.bounds(node), value)

    def after(self, node, value):
        _, position = self.bounds(node)
        self.replace(position, position, "\n" + textwrap.indent(textwrap.dedent(value).strip(), " " * node.col_offset))

    def before(self, node, value):
        position, _ = self.bounds(node)
        position -= node.col_offset
        self.replace(position, position, textwrap.indent(textwrap.dedent(value).strip(), " " * node.col_offset) + "\n")

    def cls(self, name):
        return one([n for n in self.tree.body if isinstance(n, ast.ClassDef) and n.name == name], name)

    def method(self, cls, name):
        return one([n for n in self.cls(cls).body if isinstance(n, ast.FunctionDef) and n.name == name], name)

    def add_method(self, cls, name, source):
        if any(isinstance(n, ast.FunctionDef) and n.name == name for n in self.cls(cls).body):
            return
        _, end = self.bounds(self.cls(cls))
        self.replace(end, end, "\n\n" + textwrap.indent(textwrap.dedent(source).strip(), "    "))


def one(nodes, description):
    if len(nodes) != 1:
        raise ValueError(f"Cannot safely identify {description}; no application files were changed.")
    return nodes[0]


def dotted(node, name):
    return isinstance(node, (ast.Attribute, ast.Name)) and ast.unparse(node) == name


def assignment(function, name):
    return one([n for n in ast.walk(function) if isinstance(n, ast.Assign)
                and any(dotted(t, name) for t in n.targets)], name)


def called(function, name):
    return [n for n in ast.walk(function) if isinstance(n, ast.Call) and dotted(n.func, name)]


def update_settings(source, snippets):
    edit = Editor(source)
    if not any(isinstance(n, ast.AnnAssign) and dotted(n.target, "refine_translations")
               for n in edit.cls("Settings").body):
        edit.before(edit.method("Settings", "validate"), "refine_translations: bool = True\n")
    method = edit.method("Settings", "validate")
    if not any(dotted(n, "self.refine_translations") for n in ast.walk(method)):
        edit.before(method.body[0], 'if type(self.refine_translations) is not bool:\n'
                    '    raise ValueError("Translation refinement preference must be a boolean.")')
    return edit.source


def update_engine(source, snippets):
    edit = Editor(source)
    edit.cls("TranslationEngine")
    if not any(isinstance(n, ast.ImportFrom) and n.module == "refinement" for n in edit.tree.body):
        imports = [n for n in edit.tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
        if not imports:
            raise ValueError("Unrecognized translator imports; no application files were changed.")
        edit.after(imports[-1], "from refinement import RefinementMixin")
    cls = edit.cls("TranslationEngine")
    if not any(dotted(n, "RefinementMixin") for n in cls.bases):
        if cls.bases:
            raise ValueError("Unexpected TranslationEngine inheritance; no files were changed.")
        start, _ = edit.bounds(cls)
        header = "class TranslationEngine:"
        if not edit.source.encode()[start:].startswith(header.encode()):
            raise ValueError("Unexpected TranslationEngine declaration.")
        edit.replace(start, start + len(header), "class TranslationEngine(RefinementMixin):")
    for name, variable, statement in (
        ("_process", "translation", "translation = self._refine_translations([chunk], {index: translation}, self.reference(index))[index]"),
        ("_process_group", "translations", "translations = self._refine_translations(chunks, translations, reference)")):
        method = edit.method("TranslationEngine", name)
        if not called(method, "self._refine_translations"):
            target = one([n for n in ast.walk(method) if isinstance(n, ast.Assign)
                          and any(dotted(t, variable) for t in n.targets)
                          and isinstance(n.value, ast.Call) and dotted(n.value.func, "self._request")], name + " draft")
            edit.after(target, statement)
    method = edit.method("TranslationEngine", "_parallel")
    if not any(n.arg == "processor" for n in method.args.args):
        _, end = edit.bounds(method.args.defaults[-1])
        edit.replace(end, end, ", processor: Callable | None = None")
        target = one([n for n in called(edit.method("TranslationEngine", "_parallel"), "pool.submit")
                      if n.args and dotted(n.args[0], "self._process_group")], "worker dispatch")
        edit.node(target.args[0], "processor or self._process_group")
    method = edit.method("TranslationEngine", "_process")
    if not any(isinstance(n, ast.Constant) and n.value == "Translation refined" for n in ast.walk(method)):
        target = one([n for n in called(method, "self.event") if len(n.args) >= 3
                      and dotted(n.args[1], "status")], "completion detail")
        start, end = edit.bounds(target.args[2])
        previous = edit.source.encode()[start:end].decode()
        edit.node(target.args[2], '("Translation refined" if status != "Resumed" and '
                  'self.job.settings.refine_translations and translation.strip() else (' + previous + '))')
    method = edit.method("TranslationEngine", "_process_group")
    if not any(isinstance(n, ast.Constant) and n.value == "Paragraph translated and refined" for n in ast.walk(method)):
        target = one([n for n in ast.walk(method) if isinstance(n, ast.Constant)
                      and n.value == "Paragraph translated in context"], "paragraph detail")
        edit.node(target, '("Paragraph translated and refined" if self.job.settings.refine_translations '
                  'else "Paragraph translated in context")')
    return edit.source


def update_gui(source, snippets):
    edit = Editor(source)
    method = edit.method("TranslatorGUI", "__init__")
    if not any(dotted(n, "self.refine_translations") for n in ast.walk(method)):
        edit.after(assignment(method, "self.proofread"),
                   "self.refine_translations = tk.BooleanVar(value=settings.refine_translations)")
    method = edit.method("TranslatorGUI", "_settings_panel")
    if not any(dotted(n, "self.refine_option") for n in ast.walk(method)):
        edit.before(assignment(method, "self.check_option"), snippets["refinement_checkbox"])
    edit.add_method("TranslatorGUI", "save_refinement_preference", snippets["save_refinement_preference"])
    method = edit.method("TranslatorGUI", "_controls")
    if not any(dotted(n, "self.refine_option") for n in ast.walk(method)):
        target = one([n for n in ast.walk(method) if isinstance(n, ast.List)
                      and any(dotted(item, "self.check_option") for item in n.elts)], "editable controls")
        _, end = edit.bounds(target.elts[0])
        edit.replace(end, end, ", self.refine_option")
    method = edit.method("TranslatorGUI", "get_settings")
    target = one(called(method, "Settings"), "GUI settings constructor")
    if not any(k.arg == "refine_translations" for k in target.keywords):
        keyword = one([k for k in target.keywords if k.arg == "proofread"], "proofreading preference")
        _, end = edit.bounds(keyword)
        edit.replace(end, end, ", refine_translations=self.refine_translations.get()")
    method = edit.method("TranslatorGUI", "_restore_settings")
    if not called(method, "self.refine_translations.set"):
        edit.after(one(called(method, "self.proofread.set"), "restore proofreading"),
                   "self.refine_translations.set(settings.refine_translations)")
    method = edit.method("TranslatorGUI", "_load_book")
    for target in reversed(called(method, "replace")):
        if any(k.arg == "analyze_novel" for k in target.keywords) and not any(
                k.arg == "refine_translations" for k in target.keywords):
            keyword = one([k for k in target.keywords if k.arg == "analyze_novel"], "saved-book preferences")
            _, end = edit.bounds(keyword)
            edit.replace(end, end, ", refine_translations=settings.refine_translations")
    return edit.source


def update_quality(source, snippets):
    edit = Editor(source)
    method = edit.method("QualityWindows", "open")
    if not any(dotted(n, "self.refine_selected") for n in ast.walk(method)):
        target = one([n for n in ast.walk(method) if isinstance(n, ast.Tuple) and len(n.elts) == 2
                      and isinstance(n.elts[0], ast.Constant)
                      and n.elts[0].value == "Check selected chunks for lost meaning"], "quality menu")
        _, end = edit.bounds(target)
        edit.replace(end, end, ', ("Refine selected translations (applies corrections)", self.refine_selected)')
    edit.add_method("QualityWindows", "refine_selected", snippets["refine_selected"])
    return edit.source


def update_main(source, snippets):
    edit = Editor(source)
    main = one([n for n in edit.tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"], "CLI entry point")
    if not any(isinstance(n, ast.Constant) and n.value == "--no-refinement" for n in ast.walk(main)):
        target = one([n for n in called(main, "parser.add_argument") if n.args
                      and isinstance(n.args[0], ast.Constant) and n.args[0].value == "--proofread"], "CLI options")
        edit.after(target, 'parser.add_argument("--no-refinement", action="store_true", '
                   'help="Disable the default source-aware refinement pass")')
    main = one([n for n in edit.tree.body if isinstance(n, ast.FunctionDef) and n.name == "main"], "CLI entry point")
    target = one(called(main, "Settings"), "CLI settings constructor")
    if not any(k.arg == "refine_translations" for k in target.keywords):
        keyword = one([k for k in target.keywords if k.arg == "proofread"], "CLI proofreading setting")
        _, end = edit.bounds(keyword)
        edit.replace(end, end, ", refine_translations=not args.no_refinement")
    return edit.source


def atomic_write(path: Path, data: bytes):
    descriptor, temporary = tempfile.mkstemp(prefix=".refinement-update-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def apply_update(directory: Path, bundle: Path) -> Path | None:
    snippets = json.loads((bundle / "feature_snippets.json").read_text(encoding="utf-8"))
    updates = []
    for name, transform in (("settings_manager.py", update_settings), ("translator.py", update_engine),
                            ("gui.py", update_gui), ("quality_gui.py", update_quality), ("main.py", update_main)):
        path = directory / name
        if not path.is_file():
            raise ValueError(f"{name} was not found. Extract the update beside the application's main.py.")
        original = path.read_bytes()
        updated = transform(original.decode("utf-8"), snippets).encode("utf-8")
        compile(updated, str(path), "exec")
        if original != updated:
            updates.append((path, original, updated))
    path = directory / "refinement.py"
    original = path.read_bytes() if path.exists() else None
    updated = (bundle / "refinement_module.py").read_bytes()
    compile(updated, str(path), "exec")
    if original != updated:
        updates.append((path, original, updated))
    if not updates:
        return None
    backup = directory / ("refinement_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    backup.mkdir()
    for path, original, _ in updates:
        if original is not None:
            (backup / path.name).write_bytes(original)
    try:
        for path, _, updated in updates:
            atomic_write(path, updated)
    except OSError:
        failures = []
        for path, original, _ in updates:
            try:
                if original is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write(path, original)
            except OSError:
                failures.append(path.name)
        if failures:
            raise OSError(f"Update failed; restore {failures} from {backup}.")
        raise
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app_directory", nargs="?", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        backup = apply_update(args.app_directory.resolve(), Path(__file__).resolve().parent)
    except (OSError, ValueError, SyntaxError, UnicodeError) as exc:
        print(f"Update could not be applied: {exc}")
        return 1
    print("Refine translated text is now available and enabled by default.")
    print("For existing chunks: Quality tools > Refine selected translations.")
    print(f"Original source files: {backup}" if backup else "This update is already installed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
