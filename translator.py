"""Deterministic chunks, file-only resume, parallel translation, and checking."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import unicodedata
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Iterator

from extractors import Book, valid_xml_character
from lmstudio import APIError, LMStudioClient, TRANSLATION_PROMPT
from settings_manager import Settings, atomic_write

MAX_CHARS = 4000
CHUNK_VERSION = "lossless-text-nodes-v1"


def parse_chunk_selection(value: str, chunk_count: int) -> list[int]:
    """Parse zero-based IDs and inclusive ranges, rejecting the entire invalid list."""
    indices = set()
    for token in value.replace("–", "-").replace("—", "-").split(","):
        match = re.fullmatch(r"\s*([0-9]+)(?:\s*-\s*([0-9]+))?\s*", token)
        if not match:
            raise ValueError("Enter chunk numbers or ranges, for example: 3, 8, 12-16.")
        start = int(match[1])
        end = int(match[2]) if match[2] is not None else start
        if start > end or end >= chunk_count:
            raise ValueError(f"Use ascending ranges and chunk numbers from 0 to {chunk_count - 1}.")
        indices.update(range(start, end + 1))
    return sorted(indices)


@dataclass(frozen=True)
class Chunk:
    index: int
    unit_index: int
    text: str
    prefix: str
    suffix: str


@dataclass(frozen=True)
class SavedBook:
    source_path: Path
    digest: str
    settings: Settings
    title: str
    saved_chunks: int
    directory: Path

    @property
    def label(self) -> str:
        return (f"{self.title} · {self.settings.output_lang} · {self.settings.model} "
                f"· {self.saved_chunks} saved · {self.directory.name[:8]}")


def saved_books(workspace: Path) -> list[SavedBook]:
    """Catalogs describe books/settings only; chunk files supply all resume state."""
    results = []
    for path in sorted(Path(workspace).glob("*/book.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            digest, kind = data["digest"], data["kind"]
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or kind not in ("epub", "pdf", "docx", "txt"):
                raise ValueError("Invalid book catalog")
            settings = Settings(**data["settings"])
            settings.validate()
            source = Path(workspace).parent / "books" / f"{digest}.{kind}"
            if source.is_file():
                results.append(SavedBook(source, digest, settings, str(data["title"]),
                                         len(list((path.parent / "chunks").glob("[0-9]*.txt"))), path.parent))
        except (OSError, ValueError, KeyError, TypeError):
            logging.getLogger(__name__).warning("Ignoring invalid book catalog: %s", path)
    return results


def should_skip(text: str) -> bool:
    """Empty, single-character and punctuation/symbol-only chunks produce no text."""
    normalized = unicodedata.normalize("NFC", text.strip())
    return len(normalized) <= 1 or not any(char.isalnum() for char in normalized)


def split_unit(text: str) -> Iterator[tuple[str, str, str]]:
    """Prefer sentence/word boundaries; keep every original boundary space."""
    while text:
        end = min(len(text), MAX_CHARS)
        if end < len(text):
            candidates = list(re.finditer(r'(?<=[.!?…])\s+|\s+', text[:end]))
            if candidates:
                sentences = [m for m in candidates if m.start() and
                             text[m.start() - 1] in ".!?…" and m.end() >= end // 2]
                end = (sentences or candidates)[-1].end()
        piece, text = text[:end], text[end:]
        core = piece.strip()
        if not core:
            yield "", piece, ""
        else:
            left = len(piece) - len(piece.lstrip())
            right = len(piece.rstrip())
            yield piece[left:right], piece[:left], piece[right:]


class Job:
    def __init__(self, book: Book, settings: Settings, workspace: Path):
        settings.validate()
        self.book = book
        self.settings = settings
        identity = json.dumps([book.digest, settings.model, settings.input_lang,
                               settings.output_lang, TRANSLATION_PROMPT, CHUNK_VERSION],
                              ensure_ascii=False).encode("utf-8")
        self.directory = Path(workspace) / hashlib.sha256(identity).hexdigest()
        self.recognized = self.directory.exists()
        self.chunks_dir = self.directory / "chunks"
        self.checks_dir = self.directory / "checks"
        self.chunks_dir.mkdir(parents=True, exist_ok=True)
        self.checks_dir.mkdir(parents=True, exist_ok=True)
        self.chunks: list[Chunk] = []
        self.unit_chunks: dict[int, list[int]] = {}
        for unit_index, unit in enumerate(book.units):
            self.unit_chunks[unit_index] = []
            for text, prefix, suffix in split_unit(unit.text):
                index = len(self.chunks)
                self.chunks.append(Chunk(index, unit_index, text, prefix, suffix))
                self.unit_chunks[unit_index].append(index)
        self.index_map = {chunk.index: chunk.unit_index for chunk in self.chunks}
        self.remember()

    def remember(self) -> None:
        """Retain the source for offline reopening, with no progress in the catalog."""
        source = self.directory.parent.parent / "books" / f"{self.book.digest}.{self.book.kind}"
        if not source.exists():
            payload = self.book.original or self.book.path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != self.book.digest:
                raise ValueError("Source changed during import; reopen the book.")
            atomic_write(source, payload)
        settings = asdict(self.settings)
        settings.pop("api_key")
        title = next(iter(self.book.metadata.get("title", [])), self.book.path.name)
        catalog = self.directory / "book.json"
        if catalog.exists():
            try:
                title = str(json.loads(catalog.read_text(encoding="utf-8"))["title"])
            except (OSError, ValueError, KeyError, TypeError):
                pass
        atomic_write(catalog, json.dumps({
            "digest": self.book.digest, "kind": self.book.kind,
            "title": title, "settings": settings,
        }, ensure_ascii=False, indent=2))

    def chunk_path(self, index: int) -> Path:
        return self.chunks_dir / f"{index}.txt"

    def check_path(self, index: int) -> Path:
        return self.checks_dir / f"{index}.json"

    def read_translation(self, index: int) -> str:
        if should_skip(self.chunks[index].text):
            return ""
        text = self.chunk_path(index).read_text(encoding="utf-8")
        if not text.strip():
            raise ValueError(f"Chunk {index} is empty; retranslate it.")
        return text

    def export(self, destination: Path) -> None:
        units = {}
        for unit_index, indices in self.unit_chunks.items():
            pieces = []
            for index in indices:
                chunk = self.chunks[index]
                if not should_skip(chunk.text) and not self.chunk_path(index).is_file():
                    raise ValueError(f"Chunk {index} is missing; finish translation first.")
                value = self.read_translation(index)
                pieces.append(chunk.prefix + value + chunk.suffix)
            units[unit_index] = "".join(pieces)
        self.book.export(Path(destination), units)


class Cancelled(Exception):
    """Cooperative cancellation; already committed chunks are retained."""


class TranslationEngine:
    def __init__(self, job: Job, emit: Callable[[dict], None] | None = None,
                 client: LMStudioClient | None = None):
        self.job = job
        self.client = client or LMStudioClient(job.settings)
        self.emit = emit or (lambda event: None)
        self._pause_gate = threading.Event()
        self._pause_gate.set()
        self._cancel = threading.Event()
        self._operation_lock = threading.Lock()
        self.log = logging.getLogger(__name__)

    def event(self, index: int, status: str, detail: str = "", **extra) -> None:
        self.emit({"type": "chunk", "index": index, "status": status,
                   "detail": detail, **extra})

    def pause(self) -> None:
        self._pause_gate.clear()

    def resume(self) -> None:
        self._pause_gate.set()

    def cancel(self) -> None:
        self._cancel.set()
        self._pause_gate.set()

    def checkpoint(self) -> None:
        while not self._pause_gate.wait(0.1):
            if self._cancel.is_set():
                raise Cancelled()
        if self._cancel.is_set():
            raise Cancelled()

    @contextmanager
    def operation(self):
        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeError("This job is already running.")
        lock = self.job.directory / ".running"
        owned = False
        try:
            try:
                lock.mkdir()
            except FileExistsError as exc:
                raise RuntimeError(
                    f"Job is locked: {lock}. If a prior process crashed, close all "
                    "translator instances and remove this .running directory."
                ) from exc
            owned = True
            (lock / "pid").write_text(str(os.getpid()), encoding="ascii")
            self._cancel.clear()
            self._pause_gate.set()
            yield
        finally:
            if owned:
                (lock / "pid").unlink(missing_ok=True)
                lock.rmdir()
            self._operation_lock.release()

    def run(self) -> None:
        """Bound the number of futures so cancellation does not queue the book."""
        with self.operation():
            self.log.info("Starting job %s with %s chunks and %s workers",
                          self.job.directory.name, len(self.job.chunks),
                          self.job.settings.max_workers)
            try:
                self._parallel()
            finally:
                self.log.info("Job finished; cancelled=%s", self._cancel.is_set())
                self.emit({"type": "finished", "cancelled": self._cancel.is_set()})

    def _parallel(self, chunks: list[Chunk] | None = None, force: bool = False) -> None:
        pending = iter(self.job.chunks if chunks is None else chunks)
        futures = set()
        with ThreadPoolExecutor(max_workers=self.job.settings.max_workers,
                                thread_name_prefix="translator") as pool:
            exhausted = False
            while futures or not exhausted:
                while (not exhausted and not self._cancel.is_set()
                       and self._pause_gate.is_set()
                       and len(futures) < self.job.settings.max_workers):
                    chunk = next(pending, None)
                    if chunk is None:
                        exhausted = True
                    else:
                        futures.add(pool.submit(self._process, chunk, force))
                if self._cancel.is_set():
                    for chunk in pending:
                        self.event(chunk.index, "Pending", "Cancelled before processing")
                    exhausted = True
                if futures:
                    done, futures = wait(futures, timeout=0.1, return_when=FIRST_COMPLETED)
                    for future in done:
                        future.result()
                elif not exhausted:
                    self._cancel.wait(0.1)

    def retranslate_single_chunk(self, index: int) -> None:
        self.retranslate_chunks([index])

    def save_chunk_edit(self, index: int, text: str) -> None:
        if type(index) is not int or not 0 <= index < len(self.job.chunks):
            raise IndexError("Chunk index is out of range.")
        if not isinstance(text, str):
            raise ValueError("The translation must be text.")
        if should_skip(self.job.chunks[index].text):
            if text:
                raise ValueError("Skipped source chunks must retain an empty translation.")
        elif not text.strip():
            raise ValueError("A meaningful source chunk requires a nonempty translation.")
        if self.job.book.kind == "epub" and any(not valid_xml_character(ord(c)) for c in text):
            raise ValueError("The edit contains characters that are invalid in EPUB/XML.")
        with self.operation():
            atomic_write(self.job.chunk_path(index), text)
            self.job.check_path(index).unlink(missing_ok=True)
            self.emit({"type": "check", "index": index, "check": "Not checked",
                       "detail": "Translation edited manually; previous check was cleared."})
            self.event(index, "Completed", "Edited manually", preview=text)
            self.log.info("Saved manual edit for chunk %s", index)

    def retranslate_chunks(self, indices: list[int]) -> None:
        """Force only selected chunks through the shared pause/cancel worker pool."""
        if not indices:
            raise ValueError("Select at least one chunk.")
        if any(type(index) is not int or not 0 <= index < len(self.job.chunks)
               for index in indices):
            raise IndexError("Chunk index is out of range.")
        selected = [self.job.chunks[index] for index in sorted(set(indices))]
        with self.operation():
            self.log.info("Retranslating %s selected chunks", len(selected))
            try:
                self._parallel(selected, force=True)
            finally:
                self.emit({"type": "finished", "cancelled": self._cancel.is_set()})

    def _request(self, index: int, action: Callable, checking: bool = False):
        for attempt in range(self.job.settings.retries + 1):
            self.checkpoint()
            try:
                result = action()
                self.checkpoint()  # Do not commit a cancelled in-flight response.
                return result
            except APIError as exc:
                if not exc.retryable or attempt == self.job.settings.retries:
                    raise
                if checking:
                    self.emit({"type": "check", "index": index,
                               "check": "Retry", "detail": str(exc)})
                else:
                    self.event(index, "Retry", f"Attempt {attempt + 2}: {exc}")
                if self._cancel.wait(min(2 ** attempt, 10)):
                    raise Cancelled() from exc
        raise RuntimeError("Unreachable retry state")

    def _process(self, chunk: Chunk, force: bool) -> None:
        index = chunk.index
        try:
            self.checkpoint()
            if should_skip(chunk.text):
                # Apply this rule before resume and forced retranslation, including
                # jobs created by an older application version.
                atomic_write(self.job.chunk_path(index), "")
                self.job.check_path(index).unlink(missing_ok=True)
                self.emit({"type": "check", "index": index, "check": "", "detail": ""})
                self.event(index, "Skipped", "Empty, single-character or punctuation-only source",
                           preview="")
                return
            path = self.job.chunk_path(index)
            if path.exists() and not force:
                # Existence is the only resume marker. Invalid files are surfaced,
                # never silently replaced or treated as completed.
                translation = self.job.read_translation(index)
                status = "Resumed"
            else:
                self.event(index, "Pending", "Retranslating" if force else "Translating")
                translation = self._request(index, lambda: self.client.translate(chunk.text))
                atomic_write(path, translation)
                self.job.check_path(index).unlink(missing_ok=True)
                self.emit({"type": "check", "index": index, "check": "", "detail": ""})
                status = "Retranslated" if force else "Completed"
            self.event(index, status, preview=translation)
            if self.job.settings.proofread:
                self._check(chunk, translation)
        except Cancelled:
            self.event(index, "Pending", "Cancelled; any previous chunk file is retained")
        except Exception as exc:
            self.log.exception("Chunk %s failed", index)
            self.event(index, "Error", str(exc))

    def _check(self, chunk: Chunk, translation: str) -> None:
        fingerprint = hashlib.sha256(translation.encode("utf-8")).hexdigest()
        path = self.job.check_path(chunk.index)
        try:
            report = None
            if path.exists():
                try:
                    saved = json.loads(path.read_text(encoding="utf-8"))
                    if (saved.get("translation_sha256") == fingerprint
                        and isinstance(saved.get("issues"), list)):
                        report = saved
                except (ValueError, OSError, AttributeError):
                    self.log.warning("Ignoring invalid check file for %s", chunk.index)
            if report is None:
                report = self._request(chunk.index, lambda: self.client.check(
                    chunk.text, translation), checking=True)
                report = {**report, "translation_sha256": fingerprint,
                          "chunk_index": chunk.index}
                atomic_write(path, json.dumps(report, ensure_ascii=False, indent=2))
            self.emit({"type": "check", "index": chunk.index,
                       "check": "Issues found" if report["issues"] else "Checked",
                       "detail": json.dumps(report["issues"], ensure_ascii=False, indent=2)})
        except Cancelled:
            self.emit({"type": "check", "index": chunk.index,
                       "check": "Not checked", "detail": "Cancelled"})
        except Exception as exc:
            self.log.exception("Check failed for chunk %s", chunk.index)
            self.emit({"type": "check", "index": chunk.index,
                       "check": "Check error", "detail": str(exc)})
