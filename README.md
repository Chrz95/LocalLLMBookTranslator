# Offline Literary Translator

### Translate books locally. Preserve their structure. Refine every chapter.

A Python desktop application for translating novels and other long-form writing with **LM Studio or Ollama**. Bring your own local model, guide its choices with a book glossary and style notes, and review translations in a dedicated editing workspace.

**EPUB · PDF · DOCX · TXT | Local inference | Editable translations | Resume support**

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/Q1J229F1AG)

[Features](#features) · [Translation quality](#built-for-literary-translation) · [Getting started](#getting-started) · [Supported formats](#supported-formats) · [Command line](#command-line)

## Why use it?

Translating a novel takes more than sending paragraphs to a model. Names need to stay consistent, dialogue needs a recognizable voice, and a book's layout needs to survive export.

Offline Literary Translator brings translation, book-specific guidance, quality checks and manual editing into one application. Work through a whole book, return to a difficult passage, compare local models, or correct a recurring term across your saved translations.

- **Keep inference on your computer.** Connect to LM Studio or Ollama without requiring a hosted inference service.
- **Preserve EPUB presentation.** Replace text while retaining chapter structure, markup, cover, images, fonts and styles.
- **Build consistent translations.** Supply preferred terminology, character notes, style guidance and approved examples.
- **Stay in control.** Edit any translated chunk, retranslate selected passages, inspect revisions and restore saved history.
- **Continue long projects.** Reopen saved books and resume from existing chunk files.

## Features

| Capability | What you can do |
| --- | --- |
| Local AI providers | Switch between LM Studio and Ollama; discover available models in a dropdown or enter an exact model ID. |
| Multiple input formats | Import EPUB, PDF, DOCX and UTF-8 TXT. |
| Translation queue | Follow progress, sort and filter chunks, and see Pending, Completed, Resumed, Retranslated, Error, Retry and Skipped states. |
| Parallel processing | Configure worker count, request timeout and retry count. |
| Run controls | Pause, resume or cancel a translation while retaining saved work. |
| Source and translation preview | Inspect passages side by side and edit the translated text, including saving an empty translation. |
| Targeted retranslation | Retranslate selected chunks or a range using the current model and prompt. |
| Prompt library | Create, edit, delete and select saved translation prompts alongside the default literary prompt. |
| Book recognition | Recognize compatible saved work when reopening the same book and language pair. |
| Search | Search source text and translated chunks, then jump to matching passages. |
| Bookmarks | Mark passages for later and manage or delete multiple bookmarks. |
| Find and replace | Preview replacements across translated chunks before applying them. |
| Revision history | Preview and restore earlier saved translations for individual chunks. |
| Export | Rebuild translated EPUBs or export other supported inputs as UTF-8 TXT. |

The resizable Tkinter interface includes a scrollable setup sidebar, progress bar, status counters, color-coded queue and source/translation previews. See the repository's [screenshots folder](screenshots/) for interface captures; older captures may not show every current control.

## Built for literary translation

### Faithful translation instructions

The default prompt emphasizes meaning, tone, emotional intensity, pacing, narrative voice and stylistic nuance. It asks the model to preserve ambiguity, handle expressive spellings, and pay close attention to grammatical roles, tense, pronoun references, idioms and figurative language.

You can choose another saved prompt or write your own for a particular book, language pair or translation approach.

### Context-aware passages

Source context is enabled by default. Nearby passages help the model interpret references and dialogue. For EPUBs, fragments belonging to the same paragraph can be translated together with explicit chunk IDs while preserving their original text-node positions.

Adjust context size and neighboring-chunk count in **Quality tools…** to suit your model's context window.

### Book glossary, characters and style

Save book-specific guidance that accompanies relevant translation requests:

- **Glossary:** preferred translations and usage notes for recurring terms.
- **Character notes:** names, pronouns, relationships and dialogue attribution.
- **Style guide:** narrative register, dialect, punctuation and voice preferences.
- **Approved examples:** reviewed source/translation pairs that guide future wording.

Guidance stays with the saved book job. Relevant glossary entries and examples are retrieved within bounded request budgets.

### Optional novel analysis

Enable **Analyze novel before translation** to scan the extracted novel for terminology that may need special treatment. This option is **disabled by default**.

The analysis proposes an editable glossary covering character names, titles, geography, magical abilities, factions, invented languages, recurring metaphors and other special terms. Suggestions include source evidence and treatment notes. Invalid individual suggestions are excluded and available for review; existing glossary choices are retained.

This prepass adds inference time. Review the suggestions before relying on their interpretations or preferred translations.

### Refinement and proofreading

These tools serve different parts of the review process:

| Tool | Purpose |
| --- | --- |
| Automatic refinement | In builds with the refinement update installed, checks new translations against their source and applies corrections for inconsistencies, mistakes, omissions and mistranslated words. Enabled by default and optional. |
| Refine selected translations | Applies the refinement pass to selected saved translations. |
| Proofreading | Reports bilingual review findings as **Checked** or **Issues found** without silently rewriting the translation. |
| Lost-meaning check | Reviews selected passages for missing or added clauses, negation, numbers, grammatical roles and attribution errors. |
| Revision proposals | Generates a candidate revision for side-by-side review; changes are applied only when accepted. |
| Model comparison | Compares two models from the selected provider with identities hidden until revealed. Accept a candidate explicitly. |
| Empty-translation review | Finds saved blank translations so meaningful passages can be inspected or retried. |

Refinement requires a usable structured response from the model. A failed refinement retains existing chunk files; disable automatic refinement if necessary while investigating a response error.

AI checks are aids to review. Translation quality depends on the model, language pair, context and guidance; no prompt or automated check guarantees literary accuracy.

## EPUB preservation

The EPUB exporter replaces recorded text spans inside HTML/XHTML while keeping the original document structure and resources.

It preserves:

- Chapter and paragraph structure, tags, attributes, links and inline formatting.
- Cover, images, SVG assets, fonts and external CSS.
- Metadata, identifiers, rights, publisher and original language metadata.
- Spine order and navigation structure.

HTML navigation labels can be translated as text; their links and structure remain intact. The NCX table of contents is copied unchanged. Metadata is retained in its original language.

Model output is escaped as text so it cannot insert new markup. Translation changes the EPUB's contents, so the exported archive is not byte-identical to the original and any existing digital signature should not be expected to remain valid.

## Supported formats

| Input | Output | What to expect |
| --- | --- | --- |
| EPUB | EPUB | Text translation with original markup, metadata and resources preserved. |
| PDF | UTF-8 TXT | Extracts the existing text layer in page order. No OCR or PDF layout reconstruction. |
| DOCX | UTF-8 TXT | Extracts supported body, table-cell and text-box paragraphs. Does not rebuild a styled Word document. |
| TXT | UTF-8 TXT | Accepts UTF-8 text with an optional BOM; retains line boundaries and boundary whitespace. |

Scanned PDFs need OCR before import. PDF reading order depends on the source text layer. DRM-encrypted EPUBs and some malformed or unsupported EPUB structures are rejected with an error.

## Getting started

### 1. Prepare a local model

Install **LM Studio** or **Ollama** and download a chat-capable language model suitable for your language pair.

Start the chosen application's local server yourself. The translator does not need to launch LM Studio on startup.

| Provider | Default server URL |
| --- | --- |
| LM Studio | `http://localhost:1234` |
| Ollama | `http://localhost:11434` |

Models and Python dependencies must already be downloaded for offline use. Inference goes to the configured server address; use a local address to keep book text on your computer.

### 2. Install the application

Requires **Python 3.10 or newer**, Tkinter and a graphical desktop.

Download or clone this repository, open a terminal in its folder, then create a virtual environment:

```bash
python -m venv .venv
```

Activate it on Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Or on Linux/macOS:

```bash
source .venv/bin/activate
```

Install dependencies and launch:

```bash
python -m pip install -r requirements.txt
python main.py
```

Tkinter is normally included with the Python installers for Windows and macOS. On Debian/Ubuntu, install `python3-tk` if needed.

### 3. Translate your first book

1. Select **AI provider**, confirm **Server URL**, and click **Refresh**.
2. Select an available local model and set the source and target languages.
3. Open a book. Compatible saved work is recognized automatically.
4. Choose a translation prompt and add glossary, character or style guidance as needed.
5. Configure workers, refinement and proofreading. Enable novel analysis if wanted.
6. Click **Start translation** and follow the queue.
7. Review passages, edit wording or retranslate selected chunks.
8. Click **Export book** when the required chunks are complete.

Switch models or providers between runs to try a different approach. Existing translations remain saved until you explicitly edit or retranslate them.

## Resume and saved work

Source units are split into chunks of at most **4,000 characters**. Translation resume uses the presence of `chunks/<index>.txt` files, including intentionally empty files. No `resume_progress.json` is used.

- Reopen a saved book and start again to translate remaining chunks.
- Use **Retranslate selected** to replace specific saved translations.
- Pause holds completed in-flight results before saving; cancellation retains previously saved chunks.
- Failed retranslation requests retain the previous chunk file.
- Source text shorter than four characters, or containing only punctuation/whitespace, is saved as an empty translation without a model request.
- The model is instructed to return empty text for incomplete or unclear source rather than an explanation. Review blank translations before export.

Keep your application's `data/` folder when updating. It contains saved book work and preferences. Back it up for long-running projects.

## Command line

Translate with LM Studio:

```bash
python main.py --provider lmstudio --model your-model-id \
  --input novel.epub --output novel_en.epub \
  --from-lang German --to-lang English --workers 2 --proofread
```

Or with Ollama:

```bash
python main.py --provider ollama --model your-model:latest \
  --input novel.epub --output novel_en.epub \
  --from-lang German --to-lang English
```

Run the same command to resume that job. Use `--retranslate 17` to force chunk 17, `--server-url` to override the provider's base URL, or `--data-dir` to choose a workspace. Add `--novel-analysis` for the optional terminology prepass. Builds with the refinement update support `--no-refinement` to disable the automatic editing pass.

GUI reopening prefers compatible saved work when switching models/providers. CLI job selection follows the supplied provider and model.

## Development and testing

The application uses Python, Tkinter/ttk and provider-specific local HTTP clients. Core modules cover extraction, chunking, translation, settings, provider integration and the graphical workflow.

Run the automated suite:

```bash
python -m unittest discover -s tests -v
```

Tests use temporary books and controlled model responses to check file preservation, resume, editing, provider integration and response validation. They do not measure real-world literary quality or replace testing with your chosen local model.

To compare local models on sample passages, see the included `benchmark.py` tool:

```bash
python benchmark.py --provider ollama --list-models
python benchmark.py --provider ollama --models your-model:latest --to-lang English
```

## Support the project

If Offline Literary Translator helps you work through a book, you can support its development on Ko-fi.

[![ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/Q1J229F1AG)
