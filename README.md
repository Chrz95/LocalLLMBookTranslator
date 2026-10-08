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
Enable LM Studio's local server on port 1234. The model dropdown automatically
discovers your models on startup; use **Refresh** after downloading or loading
a model. Select one from the list, or type an exact model identifier manually. The app calls
`http://localhost:1234/v1/chat/completions` with `model` and two messages: the
specified literary prompt as the system message and the source chunk as the user
message. An optional server API key can be entered in the GUI; it is not saved.
The model and Python dependencies must already be downloaded for offline use.

1. Choose a book in the setup sidebar, or select one under Saved books. Opening
   recognizes existing work and displays cached chunks without contacting the model.
   For a new book, select a model and set languages first.
2. Set workers/retries/timeout, optionally enable proofreading, then click **Start translation**.
3. Select a chunk to view its source, translation and proofreading findings.
4. **Pause** stops new requests and holds completed in-flight responses before
   saving. **Resume** continues. **Cancel** retains previously saved chunks and
   discards responses from requests cancelled before their commit checkpoint.
5. After cancellation, click **Start translation** with the same settings to resume.
6. **Retranslate selected** forces fresh requests for the selected chunks and
   atomically replaces its file on success. A failed request keeps the old file.
7. **Export book** writes the finished book. Incomplete/malformed chunk files prevent
   export. The source file cannot be overwritten.

Cancellation cannot abort a request already executing inside LM Studio. Closing
while a job is active waits for requests to return or reach their timeout. Start
with one or two workers; more workers may exceed the local model's capacity.
Language/model changes take effect when starting a new job. Retranslation uses
the loaded job's settings. Retries are additional attempts after the first one.

## Automatically start LM Studio

**Start LM Studio on app launch** is enabled by default in the setup sidebar.
Disable the checkbox to leave startup under your control; the preference saves
immediately, even before selecting a model. It applies to the GUI launch.

Startup runs in the background and first checks whether the local API server
is already running. Otherwise it attempts common Windows/macOS installations,
Linux executable names and AppImages. For another installation location, use
**Set LM Studio path…** to choose the executable (or a file inside the macOS
.app bundle). Its path is saved with the launch preference.

When the `lms` CLI is available, startup also requests a local API server on
port 1234 bound to 127.0.0.1. If the CLI is unavailable or startup fails, the
app remains usable; open LM Studio manually and enable **Developer → Start
server**. Model discovery refreshes after startup and retries after short delays.
Startup does not download models, change authentication or select/load a model.

## Retranslate with a different model

Open the saved book, select another model in the model dropdown, select chunks,
and click **Retranslate selected**. The selected chunks now use that model in
the same saved translation job. Other translations, manual edits, bookmarks,
prompts and chunk numbering remain available. The selected runtime model is
remembered and restored when reopening the saved job.

The job's original model remains its folder identity; switching runtime models
for retranslation does not move or duplicate its chunk files. The book catalog
stores the original model and the latest selected model separately. A source or
target language change still requires a separate translation job to prevent
mixing languages. Starting a new translation with a different model may create
a separate job; selected retranslation uses the currently opened job.

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
  tests/test_updates.py
  tests/test_selection.py
  tests/test_editing_books_prompts.py
  tests/test_prompt_library.py
  tests/test_search.py
  tests/test_bookmarks.py
  tests/test_queue_view.py
  tests/test_replacement.py
  tests/test_launch_and_model_switch.py
  README.md
  data/                   Created automatically
    settings.json         UI preferences only; never translation progress
    prompts.json          Named prompts and last selected prompt
    translator.log        Rotating error/operation log
    books/<source hash>.<extension>  Source copies for offline reopening
    jobs/<job hash>/
      book.json           Book identity/settings only; no translation progress
      bookmarks.json      User bookmarks and optional labels; no resume state
      replacement_backups/<id>/  Originals from each applied replacement batch
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

Book catalogs (`book.json`) store source identity, title and settings to support
the saved-book picker. Source copies are retained in `data/books/`; no counts,
completed lists or resume markers are stored in the catalog. The picker derives
its saved count directly from chunk files.

There is **no progress manifest**. Resume relies exclusively on the existence
of `chunks/<index>.txt`. Existing empty or whitespace-only UTF-8 files are valid saved translations
and resume without a model request, including proofreading. Invalid UTF-8
files surface as errors and require editing or explicit retranslation. The
source chunks and `index_map` are regenerated deterministically in memory.
Chunks with fewer than four characters after trimming surrounding whitespace
and Unicode NFC normalization, as well as punctuation/symbol-only chunks
such as `...`, `?!` or `—` are **Skipped**. Unicode NFC normalization recognizes
composed accented letters. `Hi`, `No!`, `你好`, `12`, `A.` and `abc` are skipped;
`Word`, `Hi!!` and `你好世界` translate. The threshold applies to source text,
not translated text. Short chunks never call LM Studio, including proofreading
or forced retranslation. Existing cached translations for short source chunks
are replaced with empty files when translation is run or resumed.
Skipped chunks save an empty `chunks/<index>.txt`, produce no translated text,
and never call translation or proofreading. Boundary whitespace and EPUB tags
remain intact. This rule also applies to resume and forced retranslation: old
translations/checks for these chunks are cleared. Single letters or punctuation
in their own HTML text node will therefore be removed from the exported text,
as requested; they are not copied from the source. Chunk numbering and existing
translations for other chunks remain compatible with the previous release. Chunk indices are
zero-based. Pending, Completed, Resumed, Retranslated, Error, Retry and Skipped
are visible in the GUI; counters show current states, not historical attempts.

Writes use a temporary file in the same directory and atomic replacement.
A `.running/` directory prevents two instances from translating the same job
concurrently. If a process crashes, close all translator instances and remove
that job's stale `.running/` directory before restarting. Its `pid` file helps
identify the owning process. It is a concurrency lock, not resume state.

## Model discovery and interface

The modern Tkinter interface uses a navy header, teal primary actions, a
scrollable setup sidebar, seven status counters, color-coded queue rows and
side-by-side source/translation previews. It uses standard Tkinter/ttk only;
no additional theme packages are required. The layout resizes, and the setup
sidebar scrolls when the window is short.

Model discovery runs in the background without blocking the UI. It tries
LM Studio's native `GET /api/v1/models` endpoint first to include downloaded
models and loaded instances, then falls back to `/api/v0/models` and finally
`/v1/models` for older servers. Known embedding models are excluded when the
server supplies their type. The OpenAI-compatible fallback only returns models
visible to that server and may not expose model types or all downloads.

Dropdown entries show name, state and exact identifier. Loaded models appear
first; custom loaded-instance IDs are used when available. Refresh preserves
your selection. Discovery authenticates using the optional API key; after
entering/changing it, click Refresh. A stopped server, rejected key or empty
model list is shown inline without blocking the application. Manual model-ID
entry remains available. Downloaded models may need to be loaded in LM Studio
or have Just-In-Time loading enabled before translation. No models are
downloaded, loaded or unloaded by discovery itself.

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
large context/output budget in LM Studio. Truncated completions and malformed responses are rejected and can be retried.
Normally finished empty translation replies are accepted and saved.

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

## Replace a word across translated chunks

Open a book and click **Replace words…** above the queue. Enter the text to find
and its replacement. **Whole words only** is enabled by default, so `cat` does
not change `caterpillar`. Matching is case-insensitive by default; enable
**Match case** for exact capitalization. Literal words or phrases are supported;
regular-expression syntax and replacement escapes are treated as normal text.
Replacement text is inserted exactly as entered, without automatic capitalization.

Click **Preview replacements**. No files are changed. The results list every
affected translated chunk with its ID, replacement count and before/after
snippets. Select a row to review the full original and proposed translation;
matched spans are highlighted. Changing any search option invalidates the
preview, so a new preview is required before applying.

Click **Replace words** in the fixed bottom toolbar to save the reviewed changes.
The button enables after a preview with matches and no unreadable chunk files.
Changing the search or replacement options requires a fresh preview.
Untranslated chunks and source text are untouched. Skipped chunks remain empty.
Affected previews refresh, outdated proofreading checks are removed and
bookmarks remain intact. Export the book again to include the replacements.
An empty replacement is allowed, including deleting an entire translated chunk.

Apply rechecks each affected chunk against the preview; if a chunk has changed,
the batch is rejected before writing. Unreadable translation files block Apply
until repaired, rather than silently omitting them from a whole-book operation.
Each successful batch retains original chunk and check files under the job's
`replacement_backups/<id>/` directory. On a write error, the app attempts to
restore every original translation and check. If restoration fails, the error
identifies the affected chunks and backup directory for manual recovery.
Replacement is available while the job is idle and never calls LM Studio.

## Incomplete or unclear source text

Every translation request appends an output rule to the selected prompt, including
custom and saved prompts: if the model judges the source incomplete or unclear,
it must return no characters, without explanations, clarification requests,
guesses or placeholders. Otherwise it must return only the translation.
The built-in literary prompt and saved prompt templates remain intact.

An empty (or whitespace-only) reply with a normal stop is saved as an empty chunk,
marked Completed or Retranslated, and reused on resume. It is not retried or
proofread, and exports without translated text while preserving EPUB markup and
source boundary whitespace. The queue detail says **Empty translation returned
by model**. You can edit or explicitly retranslate it later. Existing translations
are retained until you select them for retranslation.

This judgment and instruction-following depend on the selected local model;
the application does not use keyword rules to remove ordinary translations.
Truncated replies, missing/non-text content and malformed responses remain errors.
Empty proofreading replies remain errors because proofreading requires JSON.

## Sort and filter the translation queue

Click any queue column heading to sort ascending; click again for descending.
The arrow shows the current direction. Chunk IDs sort numerically, and IDs stay
unchanged so edits, bookmarks, selected retranslation and exports address the
same chunks regardless of display order.

Combine **Status**, **Review**, **Bookmarked only** and **Text / chunk ID** filters.
Text matching is case-insensitive and searches source text, translated text,
chunk IDs, source labels, bookmark labels, status and queue details. Filters
update as translations and checks finish. The shown count reflects the view;
progress and status totals still reflect every chunk in the book. No matches
shows an empty queue. **Reset view** clears filters and restores chunk order.

Filtering never pauses or limits a translation run. Chunks hidden by a filter
are removed from the current selection, so selected actions affect visible
chunks only. Search results, bookmarks and **Select by number** clear filters
when needed to reveal their target chunks. Filtering and sorting are available
while a run is active; preferences apply to subsequent books in this session.

## Bookmark specific chunks

Select one or several chunks and click **Bookmark selected**. Enter an optional
label, or leave it blank for a simple bookmark. A star appears in the queue's
bookmark column. Clicking Bookmark selected again can update the label.

Click **Bookmarks…** to list marked chunks, labels and source-text snippets.
Double-click a bookmark or click **Open chunk** to jump to its queue row and
preview. In this window, select one or more rows and click **Delete selected
bookmarks**, or press Delete. Use Ctrl/Cmd-click or Shift-click for multiple
selection, or Ctrl/Cmd+A to select all. The list, count and main queue stars
update immediately. Deleting bookmarks leaves translated chunks intact.
You can also remove bookmarks from the main chunk list with **Remove bookmark**.

Bookmarks persist in the current job's `bookmarks.json`, including when reopening
the same book after renaming it. Editing, retranslating or resuming chunks retains
them. They are local user annotations, independent of chunk files, and never
cause translation to be skipped. They are not inserted into exported books.
Bookmarks belong to a book/model/language job; a different translation job has
its own bookmarks. Bookmark management is available while the job is idle.

## Search all chunks

Open a book and click **Search…** above the translation queue. Enter a word or
phrase, choose **Both**, **Source**, or **Translation**, and optionally enable
**Match case**. Press Enter or click Search. The search runs in a background
thread and searches every chunk in the opened book.

Results show the zero-based chunk number, whether the match is in the source
or translation, occurrence count, source unit and a matching snippet. Double-click
a result or select it and click **Open chunk** to jump to the queue row and
highlight matching text in the preview. You can then edit or retranslate that
chunk using the existing controls.

Search is literal, Unicode-aware and case-insensitive by default. It reads the
latest translation files, including manual edits. Source text is searched even
when a chunk has not yet been translated. Missing translation files are skipped;
unreadable translation files are reported without hiding source matches. Each
chunk/field produces one result row, with all its occurrences counted. Phrases
spanning two separate chunks are not combined. Search is available while the
translation job is idle and does not contact LM Studio.

## Edit translated chunks

Open a book or a saved book; select exactly one chunk and click **Edit chunk…**.
The editor shows the original alongside an editable translation with undo.
Click **Save chunk** to atomically replace that chunk's file. The preview updates,
the row says Edited manually, and the old proofreading result is cleared.
Edits are reused when resuming and included in the next export. No model request
is made when saving an edit. Manual editing is available when the job is idle;
finish or cancel a run first.

You may save an empty or whitespace-only translation. It remains saved when
reopening or resuming and exports without translated text; source boundary
whitespace and EPUB tags stay intact. Use explicit retranslation to replace it
with a new model translation. Skipped source chunks retain empty translations
under the existing skip rule. XML-invalid characters
are rejected for EPUB. Tags typed in the editor are exported as escaped literal
text; they cannot alter the original EPUB structure.

## Recognize and reopen saved books

Recognition uses the entire source file's SHA-256, so an identical book is
recognized after renaming or moving it. Choose a file or click **Open book /
recognize saved work** to inspect saved translations without running inference.
If several configurations exist, the current model/languages are preferred;
otherwise the most recently opened matching book is restored. Model and language
settings are restored with the book to keep translated chunks compatible.

The **Saved books** dropdown lists imported jobs with title, target language,
model and a chunk-file count. Click **Open saved book** to use the retained source
copy; the original file does not need to remain at its previous location. You
can edit and export cached chunks while LM Studio is offline. **Start translation**
uses the currently displayed configuration and resumes only missing chunks.
A different model or language creates its own translation job.

Changing the source bytes creates a different identity, even if its filename or
title remains the same. Existing jobs from earlier releases are recognized using
their original model/language settings; opening them once adds the source copy
and catalog used by the saved-book picker. To preserve earlier work when replacing
the application source, keep your existing `data/` directory (or use `--data-dir`).

## Saved translation prompt library

The setup sidebar has a **Translation prompt** dropdown containing the original
**Default literary translation** prompt and your saved prompts.

- **New** creates a named prompt. The editor starts with the selected prompt's
  text, so you can create a variation of the default or another saved prompt.
- **Edit** modifies the selected custom prompt's name and instructions.
- **Delete** removes the selected custom prompt after confirmation and selects
  the default. The built-in default cannot be edited or deleted.
- **Save & select** saves immediately and activates that prompt. Selection from
  the dropdown also persists immediately, even before opening a book.

Names must be unique (case-insensitive), from 1 to 100 characters. Empty prompt
text is rejected. The library is stored in `data/prompts.json` and remains
available after restarting the application. Keep the existing `data/` directory
when updating the code. A prior single custom prompt is imported automatically
as **Imported custom prompt** when the library is first created.

The selected prompt replaces the system message for new and selected
retranslated chunks. Optional `<input_lang>` and `<output_lang>` placeholders
are expanded from the selected languages; the source chunk remains the user
message. Select a prompt before **Start translation** or **Retranslate selected**.
Prompt management and selection are disabled while a run is active.

Prompt changes preserve cached and edited translations. Existing chunks reuse
their files until explicitly selected for retranslation, so a book may contain
results produced with different prompts. Editing or deleting a library prompt
does not modify previously translated chunks or the prompt snapshot retained
with a saved book.

Opening a saved book restores its stored prompt text. If that text matches a
library prompt, the matching entry is shown. If it was deleted or changed,
**Book prompt (unsaved)** appears temporarily so reopening keeps the original
instructions. Use **New** to save that snapshot as a named prompt; choosing
another entry replaces it for future requests. A deleted template is not
silently recreated. The default always remains available.

Custom translation instructions do not replace the separate proofreading
prompt. Selected retranslation can use a different model in the same saved job;
keep the opened book's source and target languages. Start translation can create
a separate model/language job. If the saved prompt library
is damaged, its file is preserved and the app offers the default in read-only
mode until the file is repaired.

## Retranslate specific chunks

When the current run has finished or been cancelled, select one or more chunks
in the queue. Ctrl/Cmd-click selects individual rows; Shift-click selects a
continuous range. Alternatively, click **Select by number…** and enter zero-based
IDs and inclusive ranges, such as `3, 8, 12-16`. Invalid or out-of-range lists
are rejected; duplicates are removed. The queue shows the selected count, and
the preview follows the focused selected row.

Click **Retranslate selected** to force new translations for only those chunks.
The batch uses the configured worker count and supports Pause, Resume and Cancel.
Successful results overwrite their chunk files and refresh previews/checks;
failed requests retain previous files. Unselected chunk files remain unchanged.
Shorter-than-four-character and punctuation-only chunks still produce empty text
without contacting the model. Export the book again to include the new results.

Programmatically, use `engine.retranslate_chunks([3, 8, 12, 13])` or the existing
`engine.retranslate_single_chunk(3)` method. Both share the same worker logic.

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
pause/cancel behavior, empty-translation skips, model-discovery endpoint
fallbacks/authentication, selection persistence, persistent manual edits, source snapshots, renamed-book
recognition, custom prompts and proofreading validation. They do not measure
literary quality. The GUI requires a graphical desktop. This codebase was tested
headlessly; interaction with a real LM Studio model and visual GUI behavior
must be verified on your computer.

API references:
- https://lmstudio.ai/docs/cli/serve/server-start
- https://lmstudio.ai/docs/developer/rest/list
- https://lmstudio.ai/docs/developer/openai-compat/models
- https://lmstudio.ai/docs/developer/openai-compat
- https://pypdf.readthedocs.io/en/6.7.0/user/extract-text.html
