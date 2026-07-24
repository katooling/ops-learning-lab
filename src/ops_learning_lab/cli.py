"""Command-line interface for the local learning trust boundary."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Sequence

from .domain import SchemaError, SourceReference
from .storage import LearningHome, StorageError


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opslearn")
    subcommands = parser.add_subparsers(dest="command", required=True)

    initialize = subcommands.add_parser(
        "init", help="initialize a private learning home"
    )
    initialize.add_argument("--home", type=Path, required=True)

    capture = subcommands.add_parser(
        "capture", help="capture one source into private intake"
    )
    capture.add_argument("--home", type=Path, required=True)
    capture.add_argument("--source-type", required=True)
    capture.add_argument("--source-id", required=True)
    capture.add_argument("--observed-at", default=None)
    capture.add_argument("--input", type=Path, required=True)

    audit = subcommands.add_parser(
        "audit-privacy", help="prove canary bytes are absent from publishable areas"
    )
    audit.add_argument("--home", type=Path, required=True)
    audit.add_argument("--canary-file", type=Path, required=True)
    return parser


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "init":
            home = LearningHome.initialize(arguments.home)
            _emit({"status": "initialized", "home": str(home.root)})
            return 0

        if arguments.command == "capture":
            home = LearningHome.open(arguments.home)
            content = arguments.input.read_bytes()
            source = SourceReference(
                source_type=arguments.source_type,
                source_id=arguments.source_id,
                observed_at=arguments.observed_at or _utc_now(),
            )
            manifest = home.capture(content, source)
            _emit(
                {
                    "status": "captured",
                    "intake_id": manifest.intake_id,
                    "content_sha256": manifest.content_sha256,
                    "byte_count": manifest.byte_count,
                }
            )
            return 0

        if arguments.command == "audit-privacy":
            home = LearningHome.open(arguments.home)
            leaks = home.audit_canary(arguments.canary_file.read_bytes())
            _emit(
                {
                    "status": "passed" if not leaks else "failed",
                    "leaks": leaks,
                }
            )
            return 0 if not leaks else 2
    except (OSError, SchemaError, StorageError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command: {arguments.command}")
