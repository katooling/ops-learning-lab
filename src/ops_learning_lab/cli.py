"""Command-line interface for the local learning trust boundary."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import stat
import sys
from typing import Sequence

from .compiler import compile_update, validate_capture_text
from .domain import SchemaError, SourceReference
from .shell import make_server
from .staging import PackUpdateRepository
from .storage import LearningHome, StorageError


MAX_CAPTURE_BYTES = 1_048_576


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

    serve = subcommands.add_parser(
        "serve", help="serve the local staged-update Product Shell"
    )
    serve.add_argument("--home", type=Path, required=True)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    return parser


def _emit(value: dict[str, object]) -> None:
    print(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _read_capture_input(path: Path) -> bytes:
    if path.is_symlink():
        raise StorageError("capture input cannot be a symbolic link")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StorageError("cannot read capture input") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise StorageError("capture input must be a regular file")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            content = handle.read(MAX_CAPTURE_BYTES + 1)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(content) > MAX_CAPTURE_BYTES:
        raise StorageError(
            f"capture input exceeds the {MAX_CAPTURE_BYTES}-byte safety limit"
        )
    return content


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "init":
            home = LearningHome.initialize(arguments.home)
            _emit({"status": "initialized", "home": str(home.root)})
            return 0

        if arguments.command == "capture":
            home = LearningHome.open(arguments.home)
            content = _read_capture_input(arguments.input)
            try:
                capture_text = content.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise SchemaError("Capture Mode accepts UTF-8 text only") from exc
            validate_capture_text(capture_text)
            source = SourceReference(
                source_type=arguments.source_type,
                source_id=arguments.source_id,
                observed_at=arguments.observed_at or _utc_now(),
            )
            manifest = home.capture(content, source)
            update = PackUpdateRepository.open(home.root).stage(
                compile_update(content, manifest)
            )
            _emit(
                {
                    "status": "staged",
                    "intake_id": manifest.intake_id,
                    "content_sha256": manifest.content_sha256,
                    "byte_count": manifest.byte_count,
                    "update_id": update.update_id,
                    "match_kind": update.match.kind,
                    "proposed_pack_id": update.match.proposed_pack_id,
                    "review_path": f"/updates/{update.update_id}",
                    "lesson_started": False,
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

        if arguments.command == "serve":
            home = LearningHome.open(arguments.home)
            repository = PackUpdateRepository.open(home.root)
            server = make_server(repository, arguments.host, arguments.port)
            host, port = server.server_address[:2]
            _emit({"status": "serving", "url": f"http://{host}:{port}/"})
            sys.stdout.flush()
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
    except (OSError, SchemaError, StorageError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command: {arguments.command}")
