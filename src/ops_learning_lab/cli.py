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

from .bundle_repository import BundleRepository
from .compiler import compile_update, validate_capture_text
from .domain import SchemaError, SourceReference
from .export_repository import ExportRepository
from .exporting import (
    DEFAULT_MAX_EXPORT_BYTES,
    ExportError,
    ExportPolicy,
    StandaloneExporter,
)
from .json_contract import JsonContractError, decode_json_object
from .learning_service import InMemoryAttemptStore, LearningService
from .pack_repository import PackRepository
from .promotion import PromotionService
from .promotion_models import PromotionError, StalePromotionError
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
    serve.add_argument(
        "--forbidden-canary-file",
        type=Path,
        action="append",
        default=[],
        help="block exact UTF-8 canary text from accepted pack state",
    )

    review = subcommands.add_parser(
        "promotion-review", help="inspect one immutable staged update and pack base"
    )
    review.add_argument("--home", type=Path, required=True)
    review.add_argument("--update-id", required=True)
    review.add_argument("--target-pack-id", default=None)
    review.add_argument("--target-pack-title", default=None)

    preview = subcommands.add_parser(
        "promotion-preview", help="validate a Promotion plan without writing"
    )
    preview.add_argument("--home", type=Path, required=True)
    preview.add_argument("--plan", type=Path, required=True)
    preview.add_argument(
        "--forbidden-canary-file",
        type=Path,
        action="append",
        default=[],
    )

    promote = subcommands.add_parser(
        "promotion-commit", help="atomically commit a previously previewed plan"
    )
    promote.add_argument("--home", type=Path, required=True)
    promote.add_argument("--plan", type=Path, required=True)
    promote.add_argument("--preview-sha256", required=True)
    promote.add_argument(
        "--forbidden-canary-file",
        type=Path,
        action="append",
        default=[],
    )

    export = subcommands.add_parser(
        "export", help="create one sanitized standalone Learning Pack"
    )
    export.add_argument("--home", type=Path, required=True)
    export.add_argument(
        "--bundle-sha256",
        required=True,
        help="digest of a canonical stored Learning Pack Bundle",
    )
    export.add_argument(
        "--canary-file",
        type=Path,
        action="append",
        required=True,
        help="exact private canary bytes that must be absent",
    )
    export.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_EXPORT_BYTES,
    )
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


def _promotion_service(
    home: LearningHome,
    canary_files: Sequence[Path] = (),
) -> PromotionService:
    canaries: list[str] = []
    for path in canary_files:
        try:
            canary = _read_capture_input(path).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise StorageError("forbidden canary files must be UTF-8") from exc
        if not canary:
            raise StorageError("forbidden canary files must not be empty")
        canaries.append(canary)
    return PromotionService(
        PackUpdateRepository.open(home.root),
        PackRepository.open(home.root),
        forbidden_canaries=tuple(canaries),
    )


def _read_plan(service: PromotionService, path: Path):
    encoded = _read_capture_input(path)
    return service.plan_from_dict(decode_json_object(encoded, "Promotion plan"))


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
            service = _promotion_service(home, arguments.forbidden_canary_file)
            learning = LearningService(
                service.packs,
                BundleRepository.open(home.root),
                InMemoryAttemptStore(),
            )
            server = make_server(
                repository,
                arguments.host,
                arguments.port,
                promotion=service,
                learning=learning,
            )
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

        if arguments.command == "promotion-review":
            home = LearningHome.open(arguments.home)
            review = _promotion_service(home).review(
                arguments.update_id,
                target_pack_id=arguments.target_pack_id,
                target_pack_title=arguments.target_pack_title,
            )
            _emit(
                {
                    "status": "review",
                    "update": review.update.to_dict(),
                    "target_pack_id": review.target_pack_id,
                    "target_pack_title": review.target_pack_title,
                    "expected_base_version": review.expected_base_version,
                    "expected_base_sha256": review.expected_base_sha256,
                    "accepted_claims": (
                        [claim.to_dict() for claim in review.current_pack.claims]
                        if review.current_pack is not None
                        else []
                    ),
                }
            )
            return 0

        if arguments.command == "promotion-preview":
            home = LearningHome.open(arguments.home)
            service = _promotion_service(home, arguments.forbidden_canary_file)
            plan = _read_plan(service, arguments.plan)
            preview = service.preview(plan)
            _emit(
                {
                    "status": "preview",
                    "promotion_id": plan.promotion_id,
                    "preview_sha256": preview.preview_sha256,
                    "resulting_pack": preview.resulting_pack.to_dict(),
                    "changes": {
                        "removed": list(preview.changes.removed),
                        "retained": list(preview.changes.retained),
                        "generalized": list(preview.changes.generalized),
                    },
                    "written": False,
                }
            )
            return 0

        if arguments.command == "promotion-commit":
            home = LearningHome.open(arguments.home)
            service = _promotion_service(home, arguments.forbidden_canary_file)
            plan = _read_plan(service, arguments.plan)
            result = service.commit(plan, arguments.preview_sha256)
            _emit(
                {
                    "status": "already-promoted"
                    if result.already_applied
                    else "promoted",
                    "promotion_id": result.promotion.promotion_id,
                    "pack_id": result.pack.pack_id,
                    "pack_version": result.pack.version,
                    "pack_sha256": result.pack.content_sha256,
                }
            )
            return 0

        if arguments.command == "export":
            home = LearningHome.open(arguments.home)
            with BundleRepository.open(home.root) as bundles:
                bundle = bundles.snapshot(arguments.bundle_sha256)
                if bundle is None:
                    raise StorageError(
                        "Learning Pack Bundle snapshot was not found"
                    )
                snapshot = PackRepository.open(home.root).snapshot(bundle.pack_id)
                if snapshot is None:
                    raise StorageError("accepted Learning Pack does not exist")
                bundle = bundles.require_current(
                    arguments.bundle_sha256,
                    snapshot,
                )
            canaries = tuple(
                _read_capture_input(path) for path in arguments.canary_file
            )
            with ExportRepository.open(home.root / "exports") as exports:
                receipt = StandaloneExporter(exports).export(
                    bundle,
                    ExportPolicy(
                        canaries,
                        max_export_bytes=arguments.max_bytes,
                    ),
                )
            _emit(receipt.to_dict())
            return 0
    except (
        OSError,
        JsonContractError,
        SchemaError,
        StorageError,
        PromotionError,
        StalePromotionError,
        ExportError,
        ValueError,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    raise AssertionError(f"unhandled command: {arguments.command}")
