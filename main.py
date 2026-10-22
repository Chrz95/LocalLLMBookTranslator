"""Launch the GUI, or run a translation headlessly with --input and --output."""
from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Offline literary translation with LM Studio or Ollama")
    parser.add_argument("--data-dir", type=Path, default=Path(__file__).resolve().parent / "data")
    parser.add_argument("--input", type=Path, help="Headless input book")
    parser.add_argument("--output", type=Path, help="EPUB output for EPUB input; TXT otherwise")
    parser.add_argument("--model", default="")
    parser.add_argument("--provider", choices=("lmstudio", "ollama"), default="lmstudio")
    parser.add_argument("--server-url", help="Provider server base URL, including scheme and port")
    parser.add_argument("--from-lang", default="German")
    parser.add_argument("--to-lang", default="English")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--proofread", action="store_true")
    parser.add_argument("--novel-analysis", action=argparse.BooleanOptionalAction, default=False,
                        help="Enable optional glossary analysis before translation (default: off)")
    parser.add_argument("--retranslate", type=int, metavar="INDEX")
    args = parser.parse_args()
    if args.input and not args.output:
        parser.error("--input requires --output")
    if not args.input and (args.output or args.retranslate is not None):
        parser.error("--output and --retranslate require --input")
    try:
        args.data_dir.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(level=logging.INFO, handlers=[RotatingFileHandler(
            args.data_dir / "translator.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")],
            format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        if args.input:
            from extractors import load_book
            from settings_manager import Settings
            from dataclasses import replace
            from translator import Job, TranslationEngine
            settings = Settings(model=args.model, input_lang=args.from_lang,
                                output_lang=args.to_lang, max_workers=args.workers,
                                retries=args.retries, timeout=args.timeout,
                                proofread=args.proofread, analyze_novel=args.novel_analysis, provider=args.provider)
            if args.server_url:
                field = "ollama_url" if args.provider == "ollama" else "lmstudio_url"
                settings = replace(settings, **{field: args.server_url})
            job = Job(load_book(args.input), settings, args.data_dir / "jobs")
            errors = []
            def report(event):
                if event.get("status") == "Error":
                    errors.append(event)
                print(event.get("index", ""),
                      event.get("status", event.get("check", event["type"])),
                      event.get("detail", ""), flush=True)
            engine = TranslationEngine(job, report)
            if args.retranslate is None:
                engine.run()
            else:
                engine.retranslate_single_chunk(args.retranslate)
            if errors:
                raise RuntimeError(f"{len(errors)} chunk(s) failed; saved chunks are retained.")
            job.export(args.output)
            print(f"Saved {args.output}")
        else:
            import tkinter as tk
            from gui import TranslatorGUI
            root = tk.Tk()
            TranslatorGUI(root, args.data_dir)
            root.mainloop()
        return 0
    except KeyboardInterrupt:
        logging.getLogger(__name__).warning("Interrupted")
        return 130
    except Exception as exc:
        logging.getLogger(__name__).exception("Application failed")
        print(f"Error: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
