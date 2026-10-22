# Literary Translator

**Your books. Your model. An entirely local workflow.**

Literary Translator is a Python desktop application for translating EPUB, PDF,
DOCX and TXT books with models served locally by LM Studio or Ollama. Its side-by-side
workspace brings translation, review and editing together so you can work
through a book at your own pace.

For EPUB books, it replaces text nodes while retaining the original markup,
metadata, cover, images, fonts, CSS, spine and navigation. PDF and DOCX are
extracted as text and export to TXT; PDF extraction requires a text layer.

## What you can do

- Choose LM Studio or Ollama, discover its local models, and switch providers/models for selected chunks.
- Translate in parallel, pause, resume or cancel, with saved chunk files providing resume state.
- Reopen recognized books and continue previous translations, even after renaming the input file.
- Compare original and translated text, edit any translated chunk, and save blank edits.
- Retranslate specific chunks or ranges using the default literary prompt or a saved custom prompt.
- Create, edit, delete and select your own prompt templates.
- Optionally proofread translations and inspect reported issues.
- Analyze the whole novel before translation, disabled by default, to build an
  editable glossary of character names, titles, geography, magical abilities,
  factions, invented languages, recurring metaphors and other special terms.
- Sort the queue and combine status, review, bookmark and text filters.
- Search source and translated chunks, and jump directly to matches.
- Bookmark chunks, add labels and delete one or multiple bookmarks.
- Preview word replacements across translated chunks before applying them, with backups.
- Automatically start LM Studio on launch, or turn that option off.

Chunks contain at most 4,000 source characters. Source text shorter than four
characters after trimming and normalization, plus punctuation-only text, saves
an empty translation without contacting the model. Every translation prompt
also asks the model to return empty output if it judges the source incomplete
or unclear. That judgment and translation quality depend on the selected model.

## Translation quality workspace

**Quality tools** adds surrounding source context, EPUB paragraph translation
with validated chunk mappings, a per-book glossary, character notes, a style
guide and relevant human-approved translation examples. Meaning-focused checks
flag potential omissions and role/tense changes. Revision proposals and two-model
comparisons remain separate from saved translations until accepted, with stale
result checks and previous-version history. An empty-translation review window
supports context retries and reports while retaining the short-source skip rule.

The pretranslation analysis reads every extracted source passage in bounded
passes. Suggestions include a category, proposed target rendering, treatment
notes, source evidence and exact-match counts. Existing glossary choices are
preserved, analysis can be paused or cancelled, and completed analysis passes
are retained for reuse. AI suggestions need review for meaning and consistency.

The included command-line benchmark compares local models on 16 authored
accuracy probes, with and without source context. It saves model outputs and
an HTML report for human review, and supports your own book excerpts, target
language, prompt and book guidance. It produces no automatic literary-quality
score and runs on the computer hosting LM Studio.

## Icon and screenshots

The navy and teal icon combines an open book with opposing translation arrows.
It appears in the application header and is registered as the window icon.
The included ICO file can also be assigned to a Windows shortcut. PNG and SVG
versions are included for other platforms and sizes.

Screenshots in `screenshots/` show the actual running Tkinter application on a
Linux virtual display. They use an authored sample book and sample translations,
not live model results. Appearance and window decorations may vary by operating
system. Sample data is not included in your saved-book workspace.

1. `01_translation_workspace.png`: translation setup, status counters, queue and side-by-side text.
2. `02_filtered_queue.png`: bookmarked chunks and column sorting.
3. `03_chunk_editor.png`: source text beside an editable translation.
4. `04_replace_words.png`: reviewed before/after changes with the enabled Replace words action.
5. `05_bookmarks.png`: multiple selected bookmarks and the delete action.
6. `06_book_guidance.png`: per-book context, glossary, character/style notes and approved examples.

See `README.md` for installation, running instructions and format limitations.
