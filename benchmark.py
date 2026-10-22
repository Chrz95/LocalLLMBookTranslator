"""Run real local inference and produce a source-grounded human-review report."""
from __future__ import annotations

import argparse
import html
import json
import os
import secrets
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path

from lmstudio import APIError, LMStudioClient
from providers import create_client, provider_label
from quality import QualityProfile
from settings_manager import Settings, atomic_write
from translator import should_skip


def load_suite(path: Path) -> dict:
    suite = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(suite, dict) or suite.get("version") != 1
            or not isinstance(suite.get("cases"), list) or not suite["cases"]):
        raise ValueError("Suite must contain version 1 and a nonempty cases list.")
    for key in ("name", "source_language", "reference_language"):
        if not isinstance(suite.get(key), str) or not suite[key].strip():
            raise ValueError(f"Suite requires {key}.")
    ids = set()
    for case in suite["cases"]:
        if not isinstance(case, dict):
            raise ValueError("Each test case must be an object.")
        for key in ("id", "focus", "source", "reference", "review_notes"):
            if not isinstance(case.get(key), str):
                raise ValueError(f"Each test case requires a string {key}.")
        if not case["id"].strip() or case["id"] in ids:
            raise ValueError("Case IDs must be nonempty and unique.")
        if len(case["source"]) > 4000:
            raise ValueError("Each target source must be at most 4,000 characters.")
        ids.add(case["id"])
        for key in ("before", "after"):
            if not isinstance(case.get(key, ""), str):
                raise ValueError("Before/after source context must be text.")
    return suite


def reference_context(case: dict, profile: QualityProfile) -> dict:
    """Use source context only; review references never enter inference requests."""
    segments = []
    budget = profile.context_chars
    for key, index in (("before", 0), ("after", 2)):
        source = case.get(key, "")
        value = source[-budget:] if key == "before" and budget else source[:budget]
        if value:
            segments.append({"chunk_id": index, "text": value, "same_paragraph": False,
                             "truncated": len(value) < len(source)})
            budget -= len(value)
    text = " ".join([case["source"]] + [item["text"] for item in segments])
    return {"target_chunk_id": 1, "source_segments": segments,
            "context_truncated": any(item["truncated"] for item in segments) or
                sum(len(case.get(key, "")) for key in ("before", "after")) > profile.context_chars,
            "book_guidance": profile.guidance(text)}


def evaluate_case(client: LMStudioClient, checker: LMStudioClient | None,
                  case: dict, context: dict | None) -> dict:
    started = time.perf_counter()
    result = {"status": "error", "translation": "", "check": None, "error": ""}
    try:
        if should_skip(case["source"]):
            result["status"] = "skipped"
        else:
            result["translation"] = client.translate(case["source"], context=context)
            result["status"] = "translated" if result["translation"] else "empty"
            if checker and result["translation"]:
                try:
                    result["check"] = checker.check(case["source"], result["translation"], context)
                except APIError as exc:
                    result["check_error"] = str(exc)
    except APIError as exc:
        result["error"] = str(exc)
    result["seconds"] = round(time.perf_counter() - started, 3)
    return result


def report_html(results: dict) -> str:
    """Render escaped outputs; model responses are never executable markup."""
    def escaped(value):
        return html.escape(str(value))

    cards = []
    for case in results["suite"]["cases"]:
        rows = []
        for row in results["runs"]:
            if row["case_id"] != case["id"]:
                continue
            details = row.get("error") or row.get("check_error", "")
            check = row.get("check")
            if check is not None:
                details += "\nModel review (unverified): " + json.dumps(check, ensure_ascii=False)
            rows.append(f'<tr><td>{escaped(row["model"])}<br>{escaped(row["mode"])}</td>'
                        f'<td>{escaped(row["status"])}<br>{row["seconds"]:.3f}s</td>'
                        f'<td><pre>{escaped(row["translation"])}</pre></td>'
                        f'<td><pre>{escaped(details)}</pre></td></tr>')
        cards.append(f'<section><h2>{escaped(case["id"])} · {escaped(case["focus"])}</h2>'
                     f'<h3>Source</h3><pre>{escaped(case["source"])}</pre>'
                     f'<details><summary>Source context</summary><pre>{escaped(case.get("before", ""))}'
                     f'\n[TARGET]\n{escaped(case.get("after", ""))}</pre></details>'
                     f'<p><strong>Review criteria:</strong> {escaped(case["review_notes"])}</p>'
                     f'<details><summary>Authored reference ({escaped(results["suite"]["reference_language"])})</summary>'
                     f'<pre>{escaped(case["reference"])}</pre><p>An example wording, not a definitive gold translation.</p></details>'
                     '<table><thead><tr><th>Model / mode</th><th>Status / duration</th>'
                     '<th>Translation</th><th>Error / optional model review</th></tr></thead>'
                     f'<tbody>{"".join(rows)}</tbody></table></section>')
    return ('<!doctype html><html lang="en"><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Literary translation benchmark</title><style>'
            'body{font:16px/1.55 system-ui;color:#173047;background:#edf3f6;margin:0;padding:32px}'
            'main{max-width:1300px;margin:auto}section{background:white;padding:24px;margin:24px 0;border-radius:12px}'
            'table{width:100%;border-collapse:collapse;table-layout:fixed;margin-top:18px}'
            'th,td{padding:12px;border:1px solid #ccd9df;text-align:left;vertical-align:top;overflow-wrap:anywhere}'
            'th{background:#e3eff0}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;margin:0}'
            'summary{cursor:pointer}h2{color:#146b71}details{margin:12px 0}'
            '@media(max-width:700px){body{padding:10px}section{padding:12px}th,td{padding:6px}}'
            '</style><main><h1>Literary translation benchmark</h1>'
            f'<p>{escaped(results["source_language"])} → {escaped(results["target_language"])} · '
            f'{escaped(results["created_at"])} · {escaped(results["status"])}</p>'
            '<p>Assess faithfulness first, then voice and fluency. Check who acts on whom, every clause, '
            'negation, tense, numbers, ambiguity, and extra commentary. Record major errors and minor errors '
            'per passage. A fluent output can still contain a major meaning error. Blank outputs for meaningful '
            'sources need review; short/punctuation sources intentionally skip inference.</p>'
            '<p>This report has no automatic literary-quality score. Timings include optional checking. '
            'Check results are model judgments, not verified findings. The app prompt and '
            'provider server generation settings affect results; keep server settings consistent across runs.</p>'
            + "".join(cards) + '</main></html>')


def save_results(directory: Path, results: dict) -> None:
    atomic_write(directory / "results.json", json.dumps(results, ensure_ascii=False, indent=2))
    atomic_write(directory / "report.html", report_html(results))


def run_benchmark(suite: dict, settings: Settings, models: list[str], directory: Path,
                  endpoint: str | None = None, profile: QualityProfile | None = None,
                  check_model: str = "", modes: tuple[str, ...] = ("plain", "context")) -> dict:
    settings.validate(require_model=False)
    profile = profile or QualityProfile()
    profile.validate()
    results = {"version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
               "source_language": settings.input_lang, "target_language": settings.output_lang,
               "status": "running", "suite": suite, "models": models, "check_model": check_model, "runs": [],
               "quality_profile": asdict(profile),
               "effective_prompt": create_client(settings).translation_prompt(),
               "provider": settings.provider,
               "generation_settings": f"Inherited from {provider_label(settings.provider)}; record server settings separately."}
    checker = create_client(replace(settings, model=check_model), endpoint) if check_model else None
    save_results(directory, results)
    try:
        for model in models:
            client = create_client(replace(settings, model=model), endpoint)
            for case in suite["cases"]:
                for mode in modes:
                    context = reference_context(case, profile) if mode == "context" else None
                    result = evaluate_case(client, checker, case, context)
                    result.update(model=model, case_id=case["id"], mode=mode, reference_context=context)
                    results["runs"].append(result)
                    save_results(directory, results)
                    print(f'{model} | {case["id"]} | {mode}: {result["status"]}', flush=True)
        results["status"] = "completed_with_errors" if any(
            r["status"] == "error" or r.get("check_error") for r in results["runs"]) else "completed"
    except BaseException:
        results["status"] = "interrupted"
        raise
    finally:
        save_results(directory, results)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", type=Path, default=Path(__file__).parent / "benchmark_suite.json")
    parser.add_argument("--models", nargs="+", help="Exact local model IDs; no implicit model choice")
    parser.add_argument("--list-models", action="store_true")
    parser.add_argument("--to-lang", default="English")
    parser.add_argument("--endpoint", help="Override the selected provider chat endpoint")
    parser.add_argument("--provider", choices=("lmstudio", "ollama"), default="lmstudio")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--profile", type=Path, help="Optional existing book quality.json")
    parser.add_argument("--prompt-file", type=Path, help="Optional custom translation prompt")
    parser.add_argument("--check-model", default="", help="Optional proofreader; adds requests")
    parser.add_argument("--mode", choices=("both", "plain", "context"), default="both")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).parent / "benchmark_runs")
    parser.add_argument("--report-from", type=Path, help="Rebuild report from saved results, without inference")
    args = parser.parse_args()
    try:
        if args.report_from:
            results = json.loads(args.report_from.read_text(encoding="utf-8"))
            atomic_write(args.report_from.parent / "report.html", report_html(results))
            print(args.report_from.parent / "report.html")
            return 0
        suite = load_suite(args.suite)
        prompt = args.prompt_file.read_text(encoding="utf-8") if args.prompt_file else ""
        settings = Settings(input_lang=suite["source_language"], output_lang=args.to_lang,
                            timeout=args.timeout, provider=args.provider,
                            api_key=os.environ.get("OLLAMA_API_KEY" if args.provider == "ollama" else "LMSTUDIO_API_KEY", ""),
                            custom_prompt=prompt, use_custom_prompt=bool(args.prompt_file))
        settings.validate(require_model=False)
        if args.list_models:
            for model in create_client(settings, args.endpoint).list_models():
                print(f"{model.identifier}\t{model.name}\t{model.state}")
            return 0
        if not args.models or any(not model.strip() for model in args.models):
            parser.error("Select --models using the IDs from --list-models.")
        # Fail early rather than writing dozens of connection-failure entries.
        create_client(settings, args.endpoint).list_models()
        profile = QualityProfile.load(args.profile) if args.profile else QualityProfile()
        directory = args.output_dir / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") +
                                       "_" + secrets.token_hex(4))
        modes = ("plain", "context") if args.mode == "both" else (args.mode,)
        results = run_benchmark(suite, settings, list(dict.fromkeys(args.models)), directory,
                                args.endpoint, profile, args.check_model, modes)
        print(f"Saved {directory / 'report.html'}")
        return 1 if results["status"] == "completed_with_errors" else 0
    except KeyboardInterrupt:
        print("Interrupted; completed results were retained.", file=sys.stderr)
        return 130
    except (OSError, ValueError, TypeError, KeyError, APIError) as exc:
        print(f"Benchmark failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
