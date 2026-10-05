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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from extractors import Book
from lmstudio import APIError, LMStudioClient, TRANSLATION_PROMPT
from settings_manager import Settings, atomic_write

MAX_CHARS = 4000
CHUNK_VERSION = "lossless-text-nodes-v1"


@dataclass(frozen=True)
class Chunk:
    index: int
    unit_index: int
    text: str
    prefix: str
    suffix: str


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

    def _parallel(self) -> None:
        pending = iter(self.job.chunks)
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
                        futures.add(pool.submit(self._process, chunk, False))
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
        if not 0 <= index < len(self.job.chunks):
            raise IndexError("Chunk index is out of range.")
        with self.operation():
            self.log.info("Retranslating chunk %s", index)
            try:
                self._process(self.job.chunks[index], True)
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
