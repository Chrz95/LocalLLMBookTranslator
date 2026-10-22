# Offline Literary Translator

A complete Python/Tkinter desktop application using LM Studio or Ollama on your
own computer. It imports EPUB, PDF, DOCX and UTF-8 TXT. EPUB inputs export as EPUB;
other inputs export as UTF-8 TXT. No hosted inference service is required.

## Quick start

Requires Python 3.10 or newer, Tkinter, and LM Studio or Ollama with a local model available.

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
Choose **AI provider** in the sidebar. Start LM Studio's local server (default
port 1234) or run the Ollama application/server (default port 11434). The **Server
URL** field accepts the selected server's base URL, without an API path. Use
**Refresh** to discover its models, then select a local language model or type
its exact identifier. Ollama uses the native `/api/chat` endpoint with streaming
disabled; LM Studio uses `/v1/chat/completions`. Both receive the same literary
system prompt and source text. Optional server API keys stay in memory and are
kept separate when switching providers. The model and Python dependencies must
already be downloaded for offline use.

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
Language changes require a separate translation job; provider/model changes apply to future requests in the opened job. Retranslation uses
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
  lmstudio.py             Shared literary prompts and LM Studio HTTP client
  ollama.py               Native Ollama chat and model discovery
  providers.py            Provider selection and cache identity
  quality.py              Book guidance and bounded source context
  quality_gui.py          Guidance, revision, comparison and history dialogs
  novel_analysis.py       Full-source pretranslation terminology analysis
  analysis_parser.py      Safe normalization and validation of model glossary JSON
  benchmark.py            Local model accuracy benchmark and HTML review report
  benchmark_suite.json    Authored German-source accuracy probes
  requirements.txt        pypdf dependency (other runtime modules use stdlib)
  tests/test_application.py
  tests/test_updates.py
  tests/test_selection.py
  tests/test_editing_books_prompts.py
  tests/test_prompt_library.py
  tests/test_search.py
  tests/test_bookmarks.py
  tests/test_queue_view.py
  tests/test_quality.py
  tests/test_novel_analysis.py
  tests/test_novel_analysis_gui.py
  tests/test_analysis_parser.py
  tests/test_analysis_exclusions.py
  tests/test_providers.py
  tests/test_provider_gui.py
  tests/test_benchmark.py
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
      checks/0.json       Optional proofreading result and translation/context hashes
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

Each text node remains a separate unit to preserve original HTML structure.
With source context enabled (the default), requested fragments from the same EPUB
paragraph are translated jointly through a JSON mapping of original chunk IDs.
Only text-node spans are replaced; missing, duplicate or additional IDs and
non-string values are rejected before any target chunk is written. Paragraph
requests contain at most 4,000 target characters, with additional bounded source
context and book guidance. Larger paragraphs are processed in bounded groups.
Mapping validation checks structure, not semantic alignment; review is still
needed because retaining meaning in separate inline nodes can limit natural
word reordering. Disable context in Quality tools for independent requests.
Long units are split at sentence/whitespace boundaries where possible, otherwise
at 4,000 characters. Each translation request contains at most 4,000 source
target characters, excluding additional source context and guidance. Character limits are not token limits: configure a sufficiently
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

## App icon and screenshots

The app loads `assets/app_icon_64.png` as its window icon and displays it in the
header. Keep the `assets/` folder beside the Python files. Larger PNG, SVG and
Windows ICO versions are included; use `assets/app_icon.ico` as a shortcut icon
when creating a Windows shortcut. An ICO file does not turn Python into a standalone
executable. The app runs with the installation steps above.

See `APP_DESCRIPTION.md` for a product description and the `screenshots/` folder
for real captures of the Tkinter interface using sample book data.

## Translation quality tools

Open a book, then click **Quality tools…** beside the bookmark actions.
All book guidance is saved in the job's `quality.json`; it is an annotation,
not a progress file. Chunk files remain the sole resume markers. Existing
chunk IDs, book recognition and saved translations remain compatible.

### Source context and EPUB paragraphs

**Book glossary, characters, style and examples → Context** enables source
context by default, with two neighboring chunks per side and an 8,000-character
context budget. Choose 0–5 neighbors and 1,000–24,000 context characters.
EPUB context never crosses an HTML/XHTML member. Source fragments from the same
paragraph are prioritized with nearby chunks; context is clipped when necessary
and flagged in the request. It never relies on unfinished neighboring translations.
Every fragment carries a chunk ID so the target's position remains explicit.

Multiple requested EPUB fragments from one paragraph use a validated JSON output
mapping, with at most 4,000 target characters per request. Existing saved chunks
provide source context but are not translated again. Selected retranslation
requests only selected IDs. If your model cannot follow the mapping format,
its response surfaces as an error and the previous target files remain intact;
use a more capable model or disable context. Mapping validation cannot prove
that every translated word belongs in the correct text node.

Context adds input tokens beyond the 4,000 target-character limit. Relevant
terms and examples each have a separate 6,000-character budget; character notes
and style guidance can each add another 6,000 characters. Set a sufficient
model context window in LM Studio or lower the budgets/guidance size.

### Glossary, character notes and style guide

The **Glossary** tab supports adding, editing and deleting source terms, preferred
translations and usage notes. Specify allowed grammatical inflections, especially
for Greek. Terms are matched case-insensitively at word boundaries against the
current source and its context; only relevant entries are sent. This is prompt
guidance rather than a guarantee that a model will choose the requested term.

**Characters & style** stores names, pronouns, relationships, dialogue speakers,
narrative register, dialect and punctuation preferences. Changes affect future
requests; retranslate existing chunks to apply them. Guidance survives reopening
and model switching within the same book/language job.

### Learn from approved translations

Select an edited or reviewed chunk in the queue. In **Approved examples**, click
**Approve selected queue chunk**, then **Save book guidance**. Up to 200 examples
can be retained; approving the same source replaces its prior example. Remove
examples from the same tab. Future requests retrieve up to three relevant
examples within a bounded budget. They guide wording and voice; similar text
is never silently reused as a completed translation. Empty translations cannot
be approved as examples. Save edits first, then approve the corrected chunk.

### Check meaning explicitly

Select one or more chunks and choose **Check selected chunks for lost meaning**.
This runs proofreading even when the automatic proofreading checkbox is off.
It checks lost/added clauses, negation, numbers, tense, subject/object roles,
speaker attribution and pronoun references against source context and guidance.
Issue descriptions request quoted evidence and specific corrections. Review
findings in the normal preview; checking never rewrites translations.
Check caches include the translation, reference context/guidance and model;
changing relevant guidance or the checking model invalidates the cache.
The model's judgments can still be wrong and need human assessment.

### Revisions, model comparisons and history

Select exactly one meaningful chunk. **Propose revision for selected chunk**
uses its source, current translation and guidance to generate a revision with
an independently selectable local model. Changes are highlighted beside the
current draft. Nothing changes until **Accept revision**. A blank revision is
allowed; accepting it deliberately clears the translated text.

**Compare two models on selected chunk** generates two translations using the
same source, selected prompt and reference context. Enter or select two local
model IDs, then click **Compare models**. Candidates appear in randomized order
and their model names stay hidden until **Reveal models**. Accept either candidate
explicitly or close without changing the saved translation. Repeat on several
representative passages (dialogue, narration, idioms) to compare models for your
language pair. This is a manual comparison workflow, not an automatic quality score.
Both models must be accessible through the selected provider; model loading
depends on its configuration. Comparison and revision use the currently selected prompt.

Proposals are retained in `proposals/` for inspection. Acceptance verifies that
the book, source chunk and saved translation still match the proposal. A stale
proposal cannot overwrite a later edit. Earlier text is retained in
`history/<chunk ID>/` when editing, accepting proposals or forcing retranslation.
**View / restore selected chunk's history** previews previous text and restores
it through the normal validated edit path, saving the current version first.
Existing old versions from before this release cannot be recovered retroactively.

### Review empty translations

**Review empty translations** lists short/punctuation-only source chunks and
saved empty translations. Open a listed chunk, select several and retry with
context, or save a text report for export review. Retry enables source context
for this book. The source-under-four-characters rule still applies and never
calls the model, even on retry. Meaningful source with a saved empty translation
can be retried. A reopened empty file alone cannot distinguish a model's blank
reply from a manual blank edit; the UI does not invent provenance or create
additional resume markers. Empty exports still preserve EPUB tags and original
boundary whitespace.

The added tools improve the information available to the model and the review
workflow; they do not guarantee higher literary quality for every language/model.
The automated tests use mocked inference responses, and GUI validation uses
sample data. No real local model or human-rated quality benchmark was available
in the development environment.

## Run a real model benchmark

Run this on the computer hosting LM Studio. Enable its local server, then list
the available identifiers:

```bash
python benchmark.py --list-models
```

Copy an exact identifier from that output. To compare two models with and
without context, using English as the target:

```bash
python benchmark.py --models "first-model-id" "second-model-id"
```

The quoted IDs above must be replaced with actual IDs from your model list.
To test Greek instead, add `--to-lang Greek`. Both models must be accessible
in LM Studio. No models are downloaded, and the runner does not configure
automatic loading. Keep LM Studio generation parameters consistent; record
temperature, seed (if supported), context window, model quantization and
server version separately. Model generation settings are inherited from the
server rather than overridden by this benchmark.

The included 16 authored German-source probes cover grammatical roles,
negation, timeline, counterfactuals, pronouns, inline fragments, ambiguity,
idioms, voice, expressive spelling, numbers, register, dialogue resembling
instructions, unclear source, and the mandatory short/punctuation skips.
The English references are illustrative review examples, not definitive gold
translations, and are never sent to the translation model. Review criteria
are likewise withheld. A different target language needs a reviewer who knows
that language; references remain labeled English in the report.

Each run creates a new directory in `benchmark_runs/` with `results.json` and
`report.html`, saved after every request. Open the HTML file in a browser.
It contains source, source context, review criteria, candidate outputs,
errors and duration. Interrupting with Ctrl+C retains completed outputs.
Benchmark files are separate from saved books and never overwrite chunks.
There is no resume-progress file or hidden automatic benchmark reuse.

Optional arguments:

- `--mode plain` or `--mode context`: test only one request mode.
- `--profile "path/to/quality.json"`: include an existing book's glossary,
  character notes, style guide and approved examples in contextual requests.
  The chosen benchmark mode controls context, regardless of the saved toggle.
  Plain mode excludes all reference guidance, so this comparison assesses
  context plus guidance when a profile is supplied.
- `--prompt-file "path/to/prompt.txt"`: test a custom translation prompt;
  the application's empty-output rule still applies.
- `--check-model "model-id"`: optionally request model proofreading of
  nonempty translations. It adds inference calls; its findings remain
  unverified and require human assessment. Duration includes checking.
- `--timeout 300`: allow more time for each request.
- `--suite "path/to/suite.json"`: use your own representative book excerpts.
  Follow the included JSON schema, specify the source language, and keep each
  target at or below 4,000 characters. Supply optional `before`/`after` source
  context and review notes identifying the meanings that must survive.
- `--report-from "path/to/results.json"`: regenerate HTML without inference.
- `--output-dir "path/to/runs"`: choose the result directory.
- `--endpoint "http://localhost:1234/v1/chat/completions"`: choose your local
  server endpoint. If authentication is enabled, use the `LMSTUDIO_API_KEY`
  environment variable. Credentials are not saved in results.

Assess each passage for major meaning errors first: omissions, additions,
reversed actors, wrong negation or tense, invented ambiguity resolution, and
incorrect numbers. Then assess minor errors, register, pacing and fluency.
Track errors per passage and apply the same criteria to every candidate.
Do not equate an empty output on a meaningful passage with success: inspect
whether context made it clear. Short/punctuation source is intentionally empty.
There is no automatic literary-quality score or exact-reference pass/fail test.
For a credible model choice, also test passages from your actual book and have
a competent bilingual reader review them. This runner evaluates individual
targets; use the application's comparison/review tools to assess full chapters
and EPUB segment placement as well.

The runner's transport, report safety, reference isolation and skip rules were
tested with a controlled HTTP server. Real model testing could not run in the
development environment because no LM Studio server was reachable there.


## Analyze the novel before translation (disabled by default)

The sidebar's **Analyze novel before translation** checkbox defaults to off.
Enable it explicitly when you want a full glossary prepass. Older installations
without the new preference marker migrate the former default to off once; later
explicit choices remain saved. Its value is saved
immediately and is used when opening a saved book. It does not change book
identity, chunk IDs, existing translations, prompts or EPUB structure.

When this option is enabled, before translating pending source or forcing a
selected retranslation, the application visits the **entire extracted novel**, not just an opening
sample. Source fragments are grouped in reading order into analysis passages
of at most 4,000 characters. Analysis uses the selected provider and local model
with a dedicated terminology prompt; your chosen literary translation prompt
is still used for the subsequent translation. This pass adds inference time
before the first translation; the progress bar and status show its progress.

The AI looks for eight kinds of special treatment:

- Character names and aliases.
- Titles, ranks and honorifics.
- Geography and place names.
- Magical abilities, systems and spell names.
- Factions, orders and organizations.
- Invented languages and expressions.
- Recurring metaphors and motifs.
- Other terms requiring a consistent literary treatment.

Each suggestion contains an exact source term, a proposed target rendering,
category, treatment/uncertainty notes and a source quotation. The application
validates the response schema and verifies that the term and quotation appear
in the analyzed passage. Up to 60 previously identified terms, within a
6,000-character budget, help the next passage recognize names and recurring
motifs. The model cannot retain the full novel in one request; every passage
is visited, but extraction and semantic recurrence detection remain fallible.

After the full scan succeeds, new terms are merged into the book's editable
`quality.json`. Existing source-term choices are never overwritten, even if
they originated as AI suggestions and were later edited. Up to 5,000 glossary
entries can be retained. The usual relevance matching and 6,000-character
retrieval budget keep the whole glossary from being sent with every translation.
Source quotations and provenance are available for review, without consuming
that translation-guidance budget. Character-name suggestions retain source
spelling unless the source establishes another form; verify all renderings.

Open **Quality tools… → Book glossary, characters, style and examples → Glossary**
to review suggestions, select a category, inspect evidence, edit preferred
translations/notes, or delete entries. **Exact matches** counts case-insensitive
word-boundary occurrences across extracted text. This does not count all
inflections, aliases, image text, or paraphrased metaphor variants, and is not
proof that an AI-labeled metaphor actually recurs. AI categories, interpretations
and target renderings require human review; source grounding alone cannot
establish their correctness. Suggested glossary translations guide future
requests and are not guaranteed to be followed by the model.

**Pause**, **Resume** and **Cancel** also apply during analysis. An in-flight
HTTP request must return or time out before its cancellation can be observed.
No new glossary is committed until the full scan succeeds. Completed analysis
responses are retained under the job's `analysis/passages/`; a completed
`analysis/report.json` records analysis coverage and model identity. These are
analysis caches, not translation progress. Translation resume still depends
only on `chunks/<index>.txt`; no resume-progress JSON is created.

A completed scan is reused when reopening the same book/language/model job,
using the current edited glossary. Deleted terms stay deleted during normal
cached reuse. Changing the runtime model causes analysis with that model before
new translation, while older analysis responses remain available in the cache.
Fully resumed runs and selections consisting solely of skipped source make no
analysis requests. A novel containing meaningful source may include short names
within larger analysis passages; individual translation chunks under four
characters still produce empty translations without translation inference.

To deliberately scan again, choose **Quality tools… → Reanalyze whole novel /
refresh AI glossary**. This reruns analysis and can rediscover deleted terms,
but preserves all existing choices and leaves saved translations untouched.
Manual reanalysis works even when automatic analysis is disabled.
If the model times out or returns an unreadable response, the response-level
analysis must succeed before translation. Unsupported individual suggestions
are excluded and saved for review while valid entries continue. Network failures use the configured
retries; an invalid model response gets one automatic correction attempt. Select
a capable local model, correct the problem and retry, or turn the checkbox off
to proceed with the current glossary. Turning it off does not remove guidance.

Headless analysis is also off by default. Add `--novel-analysis` to enable it.
The older `--no-novel-analysis` spelling remains accepted. Replace model IDs
in command examples with the exact identifiers from your selected provider.
The separate benchmark runner does not automatically analyze its probe suite;
pass a saved book's `quality.json` with `--profile` to test that guidance.

Validation for this feature covers full-scan ordering, all eight categories,
source grounding, schema failures, cancellation and cache reuse, glossary edits
and deletion, manual reanalysis, skips, EPUB preservation, settings migration
and headless GUI-controller behavior. Inference responses are controlled test
responses. No live LM Studio model or graphical display was available for this
release's development checks; included screenshots predate these new controls.

### Fix for “Novel analysis returned an invalid glossary entry”

The original parser required exactly five string fields in every entry, so
extra model metadata, missing notes, or null optional values could stop the
whole analysis. The parser now accepts common field-name variants, ordinary
category labels, missing/null notes, lists of textual notes, extra metadata,
and JSON code fences. Unknown categories and ambiguous or non-text target
renderings still require correction. If no category is supplied, the term is
classified as **Other special terms** rather than inventing a classification.
Valid model quotations are retained. When evidence is absent or inaccurate,
the application extracts a minimal quotation directly from the source passage.

Malformed output is sent back to the model once, together with the source and
the specific validation error. The corrected response is validated again.
Invalid individual entries are excluded with recorded reasons rather than
blocking valid suggestions. Source/target pairs are not guessed. Response-level
JSON/schema failures still get one correction attempt and stop the scan if the
response remains unreadable; they cannot overwrite translations or the glossary. Transport failures remain eligible for the
normal retry policy. Previously completed valid analysis passages remain usable.

Rejected responses are saved locally in the book job's `analysis/errors/`
directory, with the passage number, source text, model ID, validation reason
and complete response. The application's `translator.log` records the exact
diagnostic path. These files can contain book excerpts; attach the newest one
when reporting another analysis failure. Credentials are not included. No
translation-resume markers are added by these diagnostics.

Close the application before updating. Extract the new application files into
your existing application folder and retain your `data/` folder. This release
archive excludes saved books, local preferences, logs and benchmark results so
extracting it over the installation does not overwrite those files. Retry the
translation normally; there is no need to delete chunk files or reanalyze all
previously completed passages just to install this parser fix.

### Fix for “Evidence must quote this passage and contain the source term”

The model may paraphrase its evidence, add ellipses or quotation marks, cite the
wrong sentence, or describe the term instead of quoting it. This no longer
blocks an otherwise valid glossary entry: the app verifies that the source term
is present and extracts its actual text span from the passage. The recovered
quotation preserves the book's spelling, Unicode characters and whitespace.
Case folding and canonical Unicode/whitespace matching allow equivalent forms
without changing the underlying novel or the proposed target translation.
Malformed or conflicting evidence fields are handled the same way. Valid
quotations already supplied by the model are kept.

Recovered evidence supports only the term's presence in the book. It does not
prove the AI's interpretation, category, or proposed translation. Those remain
editable suggestions requiring human review. A source term absent from the
passage is excluded, recorded for review and never used for translation. Incorrect
quotations alone no longer require an extra model call or a full reanalysis.


### Fix for “Source term does not occur in the analyzed passage”

A model may repeat a name from an earlier passage, propose a dictionary/base
form absent from this passage, or invent a candidate. This no longer causes
one glossary entry to stop the entire novel. Each candidate is checked
individually: valid entries are retained, and entries that fail source grounding
or required-field validation are excluded from translation guidance. No
source terms, target renderings or supporting facts are guessed to make them fit.

Excluded suggestions are saved under the book job's `analysis/review/`, with
passage and entry numbers, the original candidate and reason, model ID and
source passage. Open **Quality tools… → Review excluded glossary suggestions**
to inspect them. The window is read-only; verified entries remain editable in
**Book glossary**. It also includes earlier scans/models, so its historical row
count can exceed the exclusion count for the latest completed scan.

The status line retains an exclusion count after translation starts. A completed
analysis report distinguishes `completed_with_exclusions` from `completed` and
records how many candidates were excluded. If every candidate in a passage is
invalid, that passage contributes no glossary terms, records all exclusions,
and translation continues using the existing glossary plus valid suggestions
from other passages. This means terminology coverage may be incomplete; review
these warnings for books where terminology consistency matters.

Passage caches retain their exclusions so cancellation/reopening does not erase
the warning. Reanalyzing with a better model can produce better suggestions.
A successful reanalysis clears obsolete review records for the same passage and
model; existing user glossary choices and translated chunks stay untouched.
Unparseable JSON, an invalid top-level response, connection failures and corrupt
cache files remain errors rather than being mistaken for an empty clean analysis.
This update does not change how translation output itself is validated.

Install over the application files while retaining `data/`, then retry normally.
Previously valid analysis passages and all existing translations can be reused.


## Switch between Ollama and LM Studio

1. In the setup sidebar, choose **AI provider → LM Studio** or **Ollama**.
2. Confirm **Server URL**, then click **Refresh** and select a model. The field
   takes a base URL such as `http://localhost:11434`, without `/api/chat` or `/v1`.
3. Open your book, start/resume translation, or select chunks and click
   **Retranslate selected**. Existing chunks stay saved until explicitly
   retranslated. Provider changes are disabled while a job is active; cancel
   or finish the run first.

The selected provider and both server URLs persist. Model selections and API
keys are retained separately for each provider during the application session;
keys are never saved. An inactive provider's discovery response cannot replace
another provider's dropdown. Model comparisons use two models from the currently
selected provider; switch providers to generate another set of proposals.
Proofreading, revisions, optional novel analysis and paragraph mappings all use
the selected provider, with the same prompts and validation.

Opening/starting the same book with the same languages reuses compatible saved
work in the GUI. The currently opened job is preferred when switching models
or providers. Book identity retains the original model/provider, while the last
runtime provider/model are saved separately. Existing LM Studio job folder
names remain unchanged. New standalone Ollama jobs have separate identities so
identical model names on two providers do not collide. A language-pair change
still creates a separate job. CLI commands create jobs for their selected
provider/model; open an existing job in the GUI to change its runtime provider
without changing its directory.

Proofreading caches include provider/server identity. Optional analysis caches
also distinguish providers and custom server URLs. Old LM Studio caches remain
usable with the default LM Studio address. Provider/model changes never rewrite
EPUB markup, metadata, assets or chunk IDs.

**Start LM Studio on app launch** applies only when LM Studio is selected at
startup with the default loopback address/port. Ollama must already be running;
selecting it does not launch LM Studio. Ollama loads an installed model when a
request needs it. The app does not download models or change server settings.
Known cloud-tagged/remote model entries are filtered out; choose downloaded
local models and use Ollama's local-only configuration if needed. A tags list
may include an embedding-only model when the server omits capability metadata;
select a model that supports chat.

Headless examples (replace model IDs with installed models):

```bash
python main.py --provider ollama --model your-model:latest --input book.epub --output translated.epub
python main.py --provider lmstudio --model your-model-id --input book.epub --output translated.epub
```

Use `--server-url http://localhost:11434` to override the selected provider's
server base URL. Add `--novel-analysis` only when the prepass is wanted.
The benchmark also supports both providers:

```bash
python benchmark.py --provider ollama --list-models
python benchmark.py --provider ollama --models your-model:latest --to-lang English
```

`benchmark.py --endpoint` overrides the full chat endpoint when necessary;
its default follows `--provider`. Benchmark API keys use `OLLAMA_API_KEY` or
`LMSTUDIO_API_KEY` for the selected provider. The report records that provider.

Native Ollama API references: [chat](https://docs.ollama.com/api/chat),
[installed models](https://docs.ollama.com/api/tags), and
[running models](https://docs.ollama.com/api/ps). The integration is dependency-free
and uses the application's existing HTTP transport with environment proxies
bypassed. Automated checks use controlled local HTTP servers; live model quality
and graphical rendering were not tested in this development environment.
