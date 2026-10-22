"""Disable automatic LM Studio launch without replacing the rest of the app."""
from __future__ import annotations

import argparse
import ast
from datetime import datetime
import os
from pathlib import Path
import tempfile


def span(source: str, node: ast.AST) -> tuple[int, int]:
    lines = source.encode("utf-8").splitlines(keepends=True)
    return (sum(map(len, lines[:node.lineno - 1])) + node.col_offset,
            sum(map(len, lines[:node.end_lineno - 1])) + node.end_col_offset)


def edit_source(source: str, edits: list[tuple[int, int, str]]) -> str:
    data = source.encode("utf-8")
    previous = len(data) + 1
    for start, end, value in sorted(set(edits), reverse=True):
        if end > previous:
            raise ValueError("Source edits overlap; no files were changed.")
        data = data[:start] + value.encode("utf-8") + data[end:]
        previous = start
    return data.decode("utf-8")


def update_gui(source: str) -> str:
    tree = ast.parse(source)
    gui = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "TranslatorGUI"), None)
    if gui is None:
        raise ValueError("Unrecognized gui.py: TranslatorGUI was not found.")
    edits = []
    retired = next((n for n in gui.body if isinstance(n, ast.FunctionDef)
                    and n.name == "start_local_studio"), None)
    retired_span = span(source, retired) if retired else None
    if retired:
        start, end = retired_span
        edits.append((start, end, 'def start_local_studio(self) -> None:\n'
                      '        """Compatibility entry point: connect without launching a process."""\n'
                      '        self.refresh_models()'))
    for node in ast.walk(tree):
        if retired_span and hasattr(node, "lineno"):
            start, end = span(source, node)
            if retired_span[0] <= start and end <= retired_span[1]:
                continue
        if isinstance(node, ast.ImportFrom) and node.module == "lmstudio":
            names = [alias for alias in node.names if alias.name != "launch_lm_studio"]
            if len(names) != len(node.names):
                value = ast.unparse(ast.ImportFrom(module="lmstudio", names=names, level=0)) if names else ""
                edits.append((*span(source, node), value))
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "after" and len(node.args) >= 2:
            callback = node.args[1]
            if isinstance(callback, ast.Attribute) and callback.attr == "start_local_studio":
                edits.append((*span(source, callback), ast.unparse(callback.value) + ".refresh_models"))
        if (isinstance(func, ast.Attribute) and func.attr == "pack"
            and isinstance(func.value, ast.Attribute) and isinstance(func.value.value, ast.Name)
            and func.value.value.id == "self" and func.value.attr in ("launch_option", "launch_path_button")):
            edits.append((*span(source, node), ast.unparse(func.value) + ".pack_forget()"))
        for keyword in node.keywords:
            if keyword.arg == "start_lm_studio":
                edits.append((*span(source, keyword.value), "False"))
        # Retire the UI variable as well as the persisted setting.
        if isinstance(node, ast.Call) and isinstance(func, ast.Attribute) and func.attr == "BooleanVar":
            if any(isinstance(k.value, ast.Attribute) and k.value.attr == "start_lm_studio"
                   for k in node.keywords):
                for keyword in node.keywords:
                    if keyword.arg == "value":
                        edits.append((*span(source, keyword.value), "False"))
    updated = edit_source(source, edits)
    if any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
           and n.func.id == "launch_lm_studio" for n in ast.walk(ast.parse(updated))):
        raise ValueError("Unexpected additional launcher call; no files were changed.")
    return updated


def update_settings(source: str) -> str:
    tree = ast.parse(source)
    settings = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Settings"), None)
    if settings is None:
        raise ValueError("Unrecognized settings_manager.py: Settings was not found.")
    edits = []
    for node in settings.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "start_lm_studio":
                edits.append((*span(source, node.value), "False"))
    for function in tree.body:
        if not isinstance(function, ast.FunctionDef) or function.name not in ("load_settings", "save_settings"):
            continue
        forced = any(isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)
                     and n.value.value is False and any(
                         isinstance(t, ast.Subscript) and isinstance(t.value, ast.Name)
                         and t.value.id == "values" and isinstance(t.slice, ast.Constant)
                         and t.slice.value == "start_lm_studio" for t in n.targets)
                     for n in ast.walk(function))
        if forced:
            continue
        if function.name == "load_settings":
            target = next((n for n in function.body if isinstance(n, ast.Assign)
                           and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name)
                           and n.value.func.id == "Settings"), None)
            if target is None:
                raise ValueError("Unrecognized settings loader; no files were changed.")
            position, _ = span(source, target)
            edits.append((position, position, 'values["start_lm_studio"] = False\n    '))
        else:
            target = next((n for n in function.body if isinstance(n, ast.Assign)
                           and any(isinstance(t, ast.Name) and t.id == "values" for t in n.targets)
                           and isinstance(n.value, ast.Call) and isinstance(n.value.func, ast.Name)
                           and n.value.func.id == "asdict"), None)
            if target is None:
                raise ValueError("Unrecognized settings writer; no files were changed.")
            _, position = span(source, target)
            edits.append((position, position, '\n    values["start_lm_studio"] = False'))
    return edit_source(source, edits)


def atomic_write(path: Path, data: bytes) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".startup-fix-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def apply_update(directory: Path) -> Path | None:
    updates = []
    for name, transform in (("gui.py", update_gui), ("settings_manager.py", update_settings)):
        path = directory / name
        if not path.is_file():
            raise ValueError(f"{name} was not found. Extract this update into the application's folder.")
        original = path.read_bytes()
        updated = transform(original.decode("utf-8"))
        compile(updated, str(path), "exec")
        if updated.encode("utf-8") != original:
            updates.append((path, original, updated.encode("utf-8")))
    if not updates:
        return None
    backup = directory / ("startup_backup_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    backup.mkdir()
    for path, original, _ in updates:
        (backup / path.name).write_bytes(original)
    try:
        for path, _, updated in updates:
            atomic_write(path, updated)
    except OSError:
        # Preserve the original pair if a write fails partway through.
        for path, original, _ in updates:
            atomic_write(path, original)
        raise
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app_directory", nargs="?", type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        backup = apply_update(args.app_directory.resolve())
    except (OSError, ValueError, SyntaxError, UnicodeError) as exc:
        print(f"Update could not be applied: {exc}")
        return 1
    print("Automatic LM Studio startup is disabled. Start your server manually, then click Refresh.")
    if backup:
        print(f"Original source files were backed up to: {backup}")
    else:
        print("The application already has this update.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
