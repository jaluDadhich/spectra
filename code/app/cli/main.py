"""Spectra CLI — batch review entry points (spec section 12).

Commands:
  spectra validate --submission-id ... --input-dir ...
  spectra review   --submission-id ... --input-dir ... [--profile ...]
                   [--ruleset ...] [--deterministic-only]
  spectra export   --run-id ... --format json|csv|markdown
  spectra evaluate --predictions-run-id ... --ground-truth ...
  spectra rules validate [--ruleset ...]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from app.agents import Orchestrator
from app.catalog.profiles import load_profile
from app.config import get_settings
from app.logging_config import configure_logging
from app.evidence import build_export_json, render_csv, render_markdown_report
from app.rules import load_registry
from app.schemas.review import ReviewRequest
from app.storage import CaseRepository


def _repo(settings) -> CaseRepository:
    return CaseRepository(settings.sqlite_path)


def cmd_validate(args: argparse.Namespace) -> int:
    from app.adapters.loader import is_auxiliary_json, load_json_file, parse_envelope

    root = Path(args.input_dir)
    if not root.is_dir():
        print(f"error: input dir not found: {root}", file=sys.stderr)
        return 2
    failures = 0
    for path in sorted(p for p in root.glob("*.json") if not is_auxiliary_json(p)):
        data, errors = load_json_file(path)
        if errors:
            failures += 1
            for e in errors:
                print(f"INVALID {path.name}: {e}")
            continue
        doc = parse_envelope(data, path.name)
        if doc.validation_errors:
            failures += 1
            for e in doc.validation_errors:
                print(f"INVALID {path.name}: {e}")
        else:
            doc_type = doc.envelope.document.document_type or "(sniffed)"
            print(f"OK      {path.name}: type={doc_type}")
    print(f"\n{failures} invalid file(s)")
    return 1 if failures else 0


def cmd_review(args: argparse.Namespace) -> int:
    settings = get_settings()
    orchestrator = Orchestrator(settings=settings, repository=_repo(settings))
    request = ReviewRequest(
        submission_id=args.submission_id,
        input_dir=args.input_dir,
        line_of_business=args.line_of_business,
        jurisdiction=args.jurisdiction,
        effective_date=args.effective_date,
        review_date=args.review_date,
        profile=args.profile,
        ruleset=args.ruleset,
        deterministic_only=args.deterministic_only,
        allow_partial=not args.strict,
    )
    result = orchestrator.run_review(request)
    print(f"run_id:        {result.run_id}")
    print(f"submission:    {result.submission_id}")
    print(f"status:        {result.status.value}{' (PARTIAL)' if result.partial else ''}")
    print(f"findings:      {len(result.findings)}")
    for f in result.findings[:25]:
        print(f"  [{f.severity.value:13}] {f.taxonomy_id:16} {f.title}")
    if result.category_assessments:
        print("categories:")
        for a in result.category_assessments:
            print(f"  {a.issue_id:5} {a.status.value:15} {a.category}")
    if result.errors:
        print("errors/notes:")
        for e in result.errors:
            print(f"  - {e}")
    usage = result.token_usage
    print(f"llm:           calls={usage.calls} est_cost_usd={usage.estimated_cost_usd}")
    # PASS/REVIEW/HOLD are valid review outcomes (exit 0); ERROR/INCOMPLETE -> 3
    return 3 if result.status.value == "ERROR/INCOMPLETE" else 0


def cmd_export(args: argparse.Namespace) -> int:
    settings = get_settings()
    repo = _repo(settings)
    result = repo.get_run(args.run_id)
    if result is None:
        print(f"error: run not found: {args.run_id}", file=sys.stderr)
        return 2
    if args.format == "json":
        payload = json.dumps(build_export_json(result), indent=2, default=str)
    elif args.format == "csv":
        payload = render_csv(result)
    else:
        payload = render_markdown_report(result)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(payload)
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    from app.evaluation import evaluate_run

    settings = get_settings()
    repo = _repo(settings)
    result = repo.get_run(args.predictions_run_id)
    if result is None:
        print(f"error: run not found: {args.predictions_run_id}", file=sys.stderr)
        return 2
    report = evaluate_run(result, Path(args.ground_truth))
    payload = json.dumps(report, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(payload)
    return 0 if report["summary"]["false_negatives"] == 0 else 1


def cmd_rules_validate(args: argparse.Namespace) -> int:
    try:
        registry = load_registry(args.ruleset)
    except Exception as exc:
        print(f"ruleset INVALID: {exc}", file=sys.stderr)
        return 1
    load_profile("commercial_package_default")
    print(f"ruleset OK: {registry.ruleset.ruleset_id}@{registry.ruleset.version} "
          f"({len(registry.ruleset.rules)} rules, "
          f"{len(registry.enabled_rules())} enabled)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spectra", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("validate", help="Validate input JSON files")
    p.add_argument("--submission-id", required=True)
    p.add_argument("--input-dir", required=True)
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("review", help="Run a full review")
    p.add_argument("--submission-id", required=True)
    p.add_argument("--input-dir", required=True)
    p.add_argument("--line-of-business", default="package")
    p.add_argument("--jurisdiction", default=None)
    p.add_argument("--effective-date", default=None)
    p.add_argument("--review-date", default=None)
    p.add_argument("--profile", default=None)
    p.add_argument("--ruleset", default=None)
    p.add_argument("--deterministic-only", action="store_true")
    p.add_argument("--strict", action="store_true",
                   help="Fail instead of running in PARTIAL mode on invalid files")
    p.set_defaults(func=cmd_review)

    p = sub.add_parser("export", help="Export a completed run")
    p.add_argument("--run-id", required=True)
    p.add_argument("--format", choices=["json", "csv", "markdown"], default="markdown")
    p.add_argument("--output", default=None)
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("evaluate", help="Evaluate a run against ground truth")
    p.add_argument("--predictions-run-id", required=True)
    p.add_argument("--ground-truth", required=True)
    p.add_argument("--output", default=None)
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("rules", help="Ruleset utilities")
    rules_sub = p.add_subparsers(dest="rules_command", required=True)
    pv = rules_sub.add_parser("validate", help="Validate a ruleset")
    pv.add_argument("--ruleset", default="default")
    pv.set_defaults(func=cmd_rules_validate)
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_logging(get_settings())
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
