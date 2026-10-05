# Offline Literary Translator

A complete Python/Tkinter desktop application using LM Studio on your own
computer. It imports EPUB, PDF, DOCX and UTF-8 TXT. EPUB inputs export as EPUB;
other inputs export as UTF-8 TXT. No hosted inference service is used.

## Quick start

Requires Python 3.10 or newer, Tkinter, and LM Studio with a model loaded.

```bash
python -m venv .venv
```

Activate the environment:

```bash
# Linux / macOS
source .venv/bin/activate
# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Then:

```bash
python -m pip install -r requirements.txt
python main.py
```

Tkinter is normally bundled with the Python installers for Windows and macOS.
On Debian/Ubuntu, install it with `sudo apt install python3-tk` if necessary.
Enable LM Studio's local server on port 1234, load your chosen model, and enter
that model's exact identifier in the GUI. The app calls
`http://localhost:1234/v1/chat/completions` with `model` and two messages: the
specified literary prompt as the system message and the source chunk as the user
message. An optional server API key can be entered in the GUI; it is not saved.
The model and Python dependencies must already be downloaded for offline use.

1. Browse for a book; set model, source language, target language and workers.
2. Optionally enable proofreading. Click **Start / load**.
3. Select a chunk to view its source, translation and proofreading findings.
4. **Pause** stops new requests and holds completed in-flight responses before
   saving. **Resume** continues. **Cancel** retains previously saved chunks and
   discards responses from requests cancelled before their commit checkpoint.
5. After cancellation, click **Start / load** with the same settings to resume.
6. **Retranslate chunk** forces a fresh request for the selected chunk and
   atomically replaces its file on success. A failed request keeps the old file.
7. **Export** writes the finished book. Incomplete/malformed chunk files prevent
   export. The source file cannot be overwritten.

Cancellation cannot abort a request already executing inside LM Studio. Closing
while a job is active waits for requests to return or reach their timeout. Start
with one or two workers; more workers may exceed the local model's capacity.
Language/model changes take effect when starting a new job. Retranslation uses
the loaded job's settings. Retries are additional attempts after the first one.

## Files

```text
literary_translator/
  main.py                 GUI entry point and optional command-line interface
  translator.py           Chunking, workers, resume, checks, reconstruction coordination
  extractors.py           EPUB, PDF, DOCX and TXT readers; EPUB writer
  settings_manager.py     Validated settings and atomic file writes
  gui.py                  Tkinter UI with thread-safe event queue
  lmstudio.py             Local HTTP client and exact translation prompt
  requirements.txt        pypdf dependency (other runtime modules use stdlib)
  tests/test_application.py
  README.md
  data/                   Created automatically
    settings.json         UI preferences only; never translation progress
    translator.log        Rotating error/operation log
    jobs/<job hash>/
      chunks/0.txt        Translated chunk; existence is the resume marker
      chunks/1.txt
      checks/0.json       Optional proofreading result and translation hash
```

`chunks/` and `checks/` are created automatically within each job. The job key
includes the complete input file's SHA-256, model identifier, language pair,
translation prompt and chunking version. Worker count, timeout, retries and
proofreading do not change it. Switching books/languages/models cannot reuse an
unrelated job's chunk files. Changing a model's contents under the same model ID
requires a new data directory or manual removal of that job's chunk files.

There is **no progress manifest**. Resume relies exclusively on the existence
of `chunks/<index>.txt`. Existing empty or invalid UTF-8 files surface as errors
and require explicit retranslation; they are never silently overwritten. The
source chunks and `index_map` are regenerated deterministically in memory.
Whitespace-only chunks are Skipped and do not call the model. Chunk indices are
zero-based. Pending, Completed, Resumed, Retranslated, Error, Retry and Skipped
are visible in the GUI; counters show current states, not historical attempts.

Writes use a temporary file in the same directory and atomic replacement.
A `.running/` directory prevents two instances from translating the same job
concurrently. If a process crashes, close all translator instances and remove
that job's stale `.running/` directory before restarting. Its `pid` file helps
identify the owning process. It is a concurrency lock, not resume state.

Proofreading uses a separate bilingual review prompt and validates the returned
JSON. A check error does not delete a valid translation or prevent export.
Successful checks appear as Checked or Issues found and are shown in the chunk
preview. Checks are reused only when their stored translation hash matches.
Retranslating invalidates the previous check, even when checking is disabled.
The proofreader reports issues; it never silently rewrites your translation.

## EPUB preservation

The EPUB writer copies every original ZIP member in its original order. Only
recorded text spans in HTML/XHTML members are replaced; markup is never parsed
and reserialized for output. Tags, attributes, namespace spelling, links,
paragraph/chapter structure, comments, CSS, scripts, images, cover assets,
embedded/obfuscated fonts, OPF metadata and spine data are preserved. The NCX
TOC is copied unchanged. HTML navigation labels are translated as text; their
links and element structure remain unchanged. Metadata remains in its original
language, including the OPF language field, as explicitly required.

Manifest metadata, cover/image/font/CSS paths, spine and TOC paths are exposed
on the extracted `Book` object. Original resources stay in the ZIP; they do not
need to be extracted to disk. HTML/XHTML members are processed in spine order
first, then remaining documents. Whitespace at each source text-node and chunk
boundary is restored during reconstruction. Model output is escaped as text,
so angle brackets cannot insert new elements into the book.

UTF-8 and BOM-marked UTF-16 markup are supported, as are declared legacy
encodings. Characters absent from a legacy encoding become numeric character
references. Existing unchanged members retain their exact uncompressed bytes;
ZIP compression may produce different compressed bytes for rewritten members.
Do not expect a byte-identical ZIP or a valid preexisting digital signature
after translation.

DRM-encrypted content, multiple package rootfiles, custom DTD entities, visible
CDATA text and duplicate ZIP members are rejected with explicit errors. The
reader permits standard IDPF/Adobe font obfuscation and keeps those bytes and
identifiers unchanged. EPUBs expanding beyond 1 GiB are rejected. Malformed or
nonstandard files may require repair before import.

## Translation fidelity and input limits

The exact requested prompt is included in `lmstudio.py`. A prompt cannot
ensure perfect literary accuracy: results depend on the local model. Review
names, idioms, ambiguities, omissions and tone, even after automated checking.

To guarantee original HTML structure, each text node is a separate unit.
Inline tags can split a sentence into fragments; those fragments are translated
independently, without surrounding chapter context. This preserves the requested
element structure but can reduce fluency, grammatical agreement and cross-chunk
consistency. No automatic alignment or rewrite moves text across inline tags.
Long units are split at sentence/whitespace boundaries where possible, otherwise
at 4,000 characters. Each translation request contains at most 4,000 source
characters. Character limits are not token limits: configure a sufficiently
large context/output budget in LM Studio. Truncated completions, empty replies
and malformed responses are rejected and can be retried.

PDF extraction uses pypdf's existing text layer, in page order. It does not
perform OCR; image-only files need OCR first. Mixed scanned/text PDFs may have
empty pages, and PDF columns/reading order depend on their text layer. PDF
layout, images and fonts are not reconstructed. Page boundaries are preserved
as form-feed separators in TXT output.

DOCX extraction includes body paragraphs, table-cell paragraphs and nested
text-box paragraphs in XML order. Tabs and explicit line breaks are extracted.
Headers, footers, footnotes/endnotes, comments, embedded objects and tracked
revision semantics are not reconstructed; output is text, not a styled DOCX.
TXT input must be UTF-8 (optional BOM); lines and their boundary whitespace are
preserved. All exports should be reviewed before publication.

## Headless operation

```bash
python main.py --input novel.epub --output novel_en.epub \
  --model your-loaded-model-id --from-lang German --to-lang English \
  --workers 2 --proofread
```

Run the same command to resume. Set `--data-dir /path/to/work` to use another
workspace. Force one chunk with `--retranslate 17`; the export still requires
all other chunks to exist. Headless translation errors return a nonzero exit
status and leave already saved chunks intact.

Programmatic use:

```python
from pathlib import Path
from extractors import load_book
from settings_manager import Settings
from translator import Job, TranslationEngine

settings = Settings(model="your-loaded-model-id", input_lang="German",
                    output_lang="English", max_workers=2, proofread=True)
job = Job(load_book("novel.epub"), settings, Path("data/jobs"))
engine = TranslationEngine(job, emit=print)
engine.run()
engine.retranslate_single_chunk(0)
job.export(Path("novel_en.epub"))
```

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests use temporary files, a local mock HTTP server and deterministic fake
translations. They exercise EPUB preservation, parser escaping and encoding,
chunk limits, input formats, job isolation, resume, retries, retranslation,
pause/cancel behavior and proofreading response validation. They do not measure
literary quality. The GUI requires a graphical desktop. This codebase was tested
headlessly; interaction with a real LM Studio model and visual GUI behavior
must be verified on your computer.

API references:
- https://lmstudio.ai/docs/developer/openai-compat
- https://pypdf.readthedocs.io/en/6.7.0/user/extract-text.html
