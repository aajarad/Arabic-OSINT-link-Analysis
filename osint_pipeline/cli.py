"""Command-line interface.

Examples
--------
Run the full pipeline over a local JSONL export:

    python -m osint_pipeline run --collector file \\
        --source data/samples/arabic_posts.jsonl --output output

Verify archive integrity later:

    python -m osint_pipeline verify --archive output/archive
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .pipeline import Pipeline, PipelineConfig
from .collectors import CollectorUnavailable, EthicsPolicy, available_collectors
from .archive import Archiver


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="osint_pipeline",
        description="Arabic OSINT Collection & Link-Analysis Pipeline",
    )
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the full collect->analyse pipeline")
    run.add_argument("--collector", default="file", choices=available_collectors())
    run.add_argument("--source", help="Path to JSON/JSONL export (file collector)")
    run.add_argument("--feeds", nargs="*", help="RSS/Atom feed URLs (rss collector)")
    run.add_argument("--output", default="output", help="Output directory")
    run.add_argument("--ner-backend", default="auto",
                     choices=["auto", "rule", "camel", "transformers"])
    run.add_argument("--ner-model", default=None, help="Override model checkpoint")
    run.add_argument("--archive-dir", default=None,
                     help="Archive directory (default: <output>/archive)")
    run.add_argument("--labels", nargs="+", default=None,
                     choices=["PERSON", "LOC", "ORG"],
                     help="Entity labels to include in the graph (default: all)")
    run.add_argument("--max-items", type=int, default=None)
    run.add_argument("--min-delay", type=float, default=0.0,
                     help="Min seconds between source requests (rate limit)")

    verify = sub.add_parser("verify", help="Re-hash archived blobs and check integrity")
    verify.add_argument("--archive", required=True, help="Archive directory to verify")

    info = sub.add_parser("info", help="Show available collectors / NER backends")
    return p


def _err(msg: str) -> None:
    print(f"error: {msg}", file=sys.stderr)


def _cmd_run(args) -> int:
    if args.max_items is not None and args.max_items < 0:
        _err("--max-items must be >= 0")
        return 2
    if args.min_delay < 0:
        _err("--min-delay must be >= 0")
        return 2
    if args.collector == "file" and not args.source:
        _err("--source is required for the file collector")
        return 2
    if args.collector == "rss" and not args.feeds:
        _err("--feeds is required for the rss collector")
        return 2

    policy = EthicsPolicy(
        min_seconds_between_requests=args.min_delay,
        max_items=args.max_items,
    )
    extra = {}
    if args.labels:
        extra["entity_labels"] = tuple(args.labels)
    config = PipelineConfig(
        output_dir=args.output,
        archive_dir=args.archive_dir,
        ner_backend=args.ner_backend,
        ner_model=args.ner_model,
        ethics=policy,
        **extra,
    )
    try:
        pipeline = Pipeline(config)
        if args.collector == "file":
            summary = pipeline.run("file", path=args.source)
        else:
            summary = pipeline.run("rss", feed_urls=args.feeds)
    except FileNotFoundError as exc:
        _err(str(exc))
        return 3
    except (CollectorUnavailable, RuntimeError, ValueError, OSError) as exc:
        _err(str(exc))
        return 1

    print(json.dumps(
        {k: v for k, v in summary.items() if k != "timeline_preview"},
        ensure_ascii=False, indent=2,
    ))
    print(f"\nReport: {summary['artifacts']['report_html']}", file=sys.stderr)
    return 0


def _cmd_verify(args) -> int:
    archiver = Archiver(args.archive)
    report = archiver.verify()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["intact"] else 1


def _has_module(mod: str) -> bool:
    from importlib.util import find_spec

    try:
        return find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def _backend_status() -> dict:
    """Report which NER backends can actually be imported in this environment."""
    has = _has_module
    return {
        "rule": True,
        "camel": has("camel_tools"),
        "transformers": has("transformers") and has("torch"),
    }


def _cmd_info(_args) -> int:
    print("Collectors:", ", ".join(available_collectors()))
    has_feedparser = _has_module("feedparser")
    print("  file: available")
    print("  rss: " + ("available" if has_feedparser else "unavailable (pip install feedparser)"))
    status = _backend_status()
    print("NER backends: auto, " + ", ".join(status))
    for name, ok in status.items():
        print(f"  {name}: {'available' if ok else 'not installed'}")
    chain = [n for n in ("camel", "transformers", "rule") if status[n]]
    print(f"  auto would try: {' -> '.join(chain)}")
    return 0


def _force_utf8_stdio() -> None:
    """Windows consoles default to cp1252 and cannot print Arabic. Reconfigure."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass


def main(argv=None) -> int:
    _force_utf8_stdio()
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse exits 2 on usage errors / 0 on --help
        return exc.code if isinstance(exc.code, int) else 2
    if args.command == "run":
        return _cmd_run(args)
    if args.command == "verify":
        return _cmd_verify(args)
    if args.command == "info":
        return _cmd_info(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
