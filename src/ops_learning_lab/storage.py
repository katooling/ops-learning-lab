"""Filesystem trust boundaries for a local learning home."""

from __future__ import annotations

from dataclasses import dataclass
import errno
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
from typing import Iterable

from .domain import ID_PATTERN, IntakeManifest, SourceReference
from .json_contract import JsonContractError, decode_json_object


MARKER_FILE = ".ops-learning-lab-home"
PRIVATE_DIRECTORY = "private"
PRIVATE_INBOX = Path(PRIVATE_DIRECTORY) / "inbox"
STAGED_DIRECTORY = "staged"
STAGED_UPDATES = Path(STAGED_DIRECTORY) / "updates"
PUBLIC_DIRECTORIES = ("packs", "snapshots", "exports")


class StorageError(RuntimeError):
    """Raised when a learning home violates its filesystem contract."""


def _write_atomic(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _inside_git_worktree(path: Path) -> bool:
    return any((candidate / ".git").exists() for candidate in (path, *path.parents))


def _require_owned_private_directory(path: Path, home: Path, label: str) -> None:
    if path.is_symlink() or not path.is_dir():
        raise StorageError(f"{label} is missing or unsafe")
    try:
        path.resolve().relative_to(home.resolve())
    except ValueError as exc:
        raise StorageError(f"{label} escapes the learning home") from exc
    stat = path.stat()
    if hasattr(os, "getuid") and stat.st_uid != os.getuid():
        raise StorageError(f"{label} is not owned by the current user")
    if stat.st_mode & 0o077:
        raise StorageError(f"{label} permissions must be 0700")


def _read_confined_regular_file(path: Path, parent: Path, label: str) -> bytes:
    if path.is_symlink():
        raise StorageError(f"{label} cannot be a symbolic link")
    try:
        path.resolve(strict=True).relative_to(parent.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise StorageError(f"{label} is missing or escapes its intake") from exc

    flags = (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise StorageError(f"cannot read {label}") from exc
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise StorageError(f"{label} is not a regular file")
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = -1
            return handle.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


@dataclass(frozen=True, slots=True)
class LearningHome:
    root: Path

    @classmethod
    def initialize(cls, root: str | Path) -> LearningHome:
        requested = Path(root).expanduser()
        if requested.is_symlink():
            raise StorageError("learning home cannot be a symbolic link")
        candidate = requested.resolve(strict=False)
        if _inside_git_worktree(candidate):
            raise StorageError("learning home cannot be inside a Git worktree")
        candidate.mkdir(parents=True, exist_ok=True)
        candidate = candidate.resolve()

        private = candidate / PRIVATE_DIRECTORY
        if private.is_symlink():
            raise StorageError("private directory cannot be a symbolic link")
        private.mkdir(exist_ok=True)
        os.chmod(private, 0o700)
        inbox = candidate / PRIVATE_INBOX
        if inbox.is_symlink():
            raise StorageError("private inbox cannot be a symbolic link")
        inbox.mkdir(exist_ok=True)
        os.chmod(inbox, 0o700)
        _require_owned_private_directory(private, candidate, "private directory")
        _require_owned_private_directory(inbox, candidate, "private inbox")
        staged = candidate / STAGED_DIRECTORY
        if staged.is_symlink():
            raise StorageError("staged directory cannot be a symbolic link")
        staged.mkdir(exist_ok=True)
        os.chmod(staged, 0o700)
        updates = candidate / STAGED_UPDATES
        if updates.is_symlink():
            raise StorageError("staged updates cannot be a symbolic link")
        updates.mkdir(exist_ok=True)
        os.chmod(updates, 0o700)
        _require_owned_private_directory(staged, candidate, "staged directory")
        _require_owned_private_directory(updates, candidate, "staged updates")
        for directory in PUBLIC_DIRECTORIES:
            target = candidate / directory
            if target.is_symlink():
                raise StorageError(f"{directory} cannot be a symbolic link")
            target.mkdir(exist_ok=True)

        marker = candidate / MARKER_FILE
        if not marker.exists():
            _write_atomic(marker, b'{"schema_version":1}\n', 0o600)
        return cls(candidate)

    @classmethod
    def open(cls, root: str | Path) -> LearningHome:
        candidate = Path(root).expanduser()
        if candidate.is_symlink():
            raise StorageError("learning home cannot be a symbolic link")
        candidate = candidate.resolve()
        if not (candidate / MARKER_FILE).is_file():
            raise StorageError("learning home is not initialized")
        if _inside_git_worktree(candidate):
            raise StorageError("learning home cannot be inside a Git worktree")
        private = candidate / PRIVATE_DIRECTORY
        inbox = candidate / PRIVATE_INBOX
        _require_owned_private_directory(private, candidate, "private directory")
        _require_owned_private_directory(inbox, candidate, "private inbox")
        _require_owned_private_directory(
            candidate / STAGED_DIRECTORY,
            candidate,
            "staged directory",
        )
        _require_owned_private_directory(
            candidate / STAGED_UPDATES,
            candidate,
            "staged updates",
        )
        for directory in PUBLIC_DIRECTORIES:
            target = candidate / directory
            if target.is_symlink() or not target.is_dir():
                raise StorageError(f"{directory} is missing or unsafe")
        return cls(candidate)

    def capture(self, content: bytes, source: SourceReference) -> IntakeManifest:
        inbox = self.root / PRIVATE_INBOX
        _require_owned_private_directory(
            self.root / PRIVATE_DIRECTORY,
            self.root,
            "private directory",
        )
        _require_owned_private_directory(inbox, self.root, "private inbox")
        content_digest = sha256(content).hexdigest()
        identity = b"\0".join(
            (
                source.source_type.encode("utf-8"),
                source.source_id.encode("utf-8"),
                content_digest.encode("ascii"),
            )
        )
        intake_id = f"intake-{sha256(identity).hexdigest()[:20]}"
        destination = inbox / intake_id
        if destination.is_symlink():
            raise StorageError("intake destination cannot be a symbolic link")

        manifest = IntakeManifest(
            intake_id=intake_id,
            content_sha256=content_digest,
            byte_count=len(content),
            raw_file="raw.bin",
            source=source,
        )
        raw_path = destination / manifest.raw_file
        manifest_path = destination / "manifest.json"

        if destination.exists():
            if not destination.is_dir():
                raise StorageError("intake destination is not a directory")
            existing = self.read_manifest(intake_id)
            same_source = (
                existing.source.source_type == source.source_type
                and existing.source.source_id == source.source_id
            )
            if (
                not same_source
                or existing.content_sha256 != content_digest
                or existing.byte_count != len(content)
                or _read_confined_regular_file(
                    raw_path,
                    destination,
                    "raw intake file",
                )
                != content
            ):
                raise StorageError("intake identity collides with different content")
            return existing

        manifest_bytes = (
            json.dumps(
                manifest.to_dict(),
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")

        staging = Path(
            tempfile.mkdtemp(prefix=f".{intake_id}.staging-", dir=inbox)
        )
        try:
            os.chmod(staging, 0o700)
            _write_atomic(staging / manifest.raw_file, content, 0o600)
            _write_atomic(staging / "manifest.json", manifest_bytes, 0o600)
            try:
                staging.rename(destination)
            except OSError as exc:
                if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                    raise
                # Another identical capture won the atomic directory rename.
                # The collision is accepted only after the winner is verified.
                existing = self.read_manifest(intake_id)
                if (
                    existing.source.source_type != source.source_type
                    or existing.source.source_id != source.source_id
                    or existing.content_sha256 != content_digest
                    or existing.byte_count != len(content)
                    or _read_confined_regular_file(
                        destination / existing.raw_file,
                        destination,
                        "raw intake file",
                    )
                    != content
                ):
                    raise StorageError(
                        "concurrent intake identity collides with different content"
                    )
                return existing
        except BaseException:
            if staging.exists():
                shutil.rmtree(staging)
            raise
        return manifest

    def read_manifest(self, intake_id: str) -> IntakeManifest:
        if not isinstance(intake_id, str) or not ID_PATTERN.fullmatch(intake_id):
            raise StorageError("intake_id does not match the schema")
        inbox = self.root / PRIVATE_INBOX
        _require_owned_private_directory(inbox, self.root, "private inbox")
        intake = inbox / intake_id
        if intake.is_symlink() or not intake.is_dir():
            raise StorageError(f"cannot read manifest for {intake_id}")
        try:
            intake.resolve().relative_to(inbox.resolve())
        except ValueError as exc:
            raise StorageError("intake manifest escapes the private inbox") from exc
        path = intake / "manifest.json"
        try:
            manifest_bytes = _read_confined_regular_file(
                path,
                intake,
                "intake manifest",
            )
            value = decode_json_object(manifest_bytes, "intake manifest")
            return IntakeManifest.from_dict(value)
        except (JsonContractError, TypeError, ValueError) as exc:
            raise StorageError(f"cannot read manifest for {intake_id}") from exc

    def audit_canary(self, canary: bytes) -> list[str]:
        if not canary:
            raise StorageError("privacy canary must not be empty")
        leaks: list[str] = []
        for file_path in self._public_files():
            if canary in file_path.read_bytes():
                leaks.append(str(file_path.relative_to(self.root)))
        return sorted(leaks)

    def _public_files(self) -> Iterable[Path]:
        for directory in PUBLIC_DIRECTORIES:
            root = self.root / directory
            for path in root.rglob("*"):
                if path.is_symlink():
                    raise StorageError(
                        f"symbolic links are forbidden in publishable areas: {path}"
                    )
                if path.is_file():
                    yield path
