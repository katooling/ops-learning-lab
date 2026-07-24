"""Directory-descriptor-bound storage operations.

The pathname is used only to open and verify the approved directory. All file
operations after that point are relative to the retained directory descriptor.
"""

from __future__ import annotations

from dataclasses import dataclass
import errno
import os
from pathlib import Path
from secrets import token_hex
import stat

from .storage import StorageError


@dataclass(frozen=True, slots=True)
class _BoundWriteOutcome:
    """Truthful result once an atomic replacement is visible."""

    replaced: bool
    directory_synced: bool


@dataclass(frozen=True, slots=True)
class _BoundCreateOutcome:
    """Result of an atomic create-if-absent operation."""

    created: bool
    directory_synced: bool


class _BoundDirectory:
    """A narrow capability bound to one already-approved directory inode."""

    def __init__(
        self,
        path: Path,
        descriptor: int,
        device: int,
        inode: int,
        label: str,
    ) -> None:
        self.path = path
        self._descriptor = descriptor
        self._device = device
        self._inode = inode
        self._label = label

    @classmethod
    def open(cls, path: Path, label: str) -> _BoundDirectory:
        if not isinstance(path, Path):
            raise StorageError(f"{label} path is invalid")
        lexical_path = Path(os.path.abspath(path.expanduser()))
        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(lexical_path, flags)
        except OSError as exc:
            raise StorageError(f"{label} is missing or unsafe") from exc
        try:
            metadata = os.fstat(descriptor)
            cls._validate_metadata(metadata, label)
            visible = os.stat(lexical_path, follow_symlinks=False)
            if (
                not stat.S_ISDIR(visible.st_mode)
                or visible.st_dev != metadata.st_dev
                or visible.st_ino != metadata.st_ino
            ):
                raise StorageError(f"{label} changed while it was opened")
            return cls(
                lexical_path,
                descriptor,
                metadata.st_dev,
                metadata.st_ino,
                label,
            )
        except BaseException:
            os.close(descriptor)
            raise

    @staticmethod
    def _validate_metadata(metadata: os.stat_result, label: str) -> None:
        if not stat.S_ISDIR(metadata.st_mode):
            raise StorageError(f"{label} is missing or unsafe")
        if hasattr(os, "getuid") and metadata.st_uid != os.getuid():
            raise StorageError(f"{label} is not owned by the current user")
        if metadata.st_mode & 0o022:
            raise StorageError(f"{label} cannot be group or world writable")

    def read_regular(self, name: str, label: str) -> bytes | None:
        """Read one leaf regular file without following links.

        Missing files return ``None``. Other file types fail closed.
        """

        self._require_open()
        self._require_leaf_name(name)
        self._validate_bound_descriptor()
        self._require_path_binding()
        content = self._read_regular_bound(name, label)
        self._require_path_binding()
        return content

    def open_child_directory(
        self,
        name: str,
        label: str,
        *,
        create: bool = False,
        mode: int = 0o700,
    ) -> _BoundDirectory:
        """Open one child directory relative to this retained descriptor.

        The caller never resolves the child through an independently trusted
        pathname. Both the parent and child remain bound to their original
        inodes, so replacing any visible ancestor makes later operations fail.
        """

        self._require_open()
        self._require_leaf_name(name)
        self._validate_bound_descriptor()
        self._require_path_binding()
        if create:
            try:
                os.mkdir(name, mode=mode, dir_fd=self._descriptor)
            except FileExistsError:
                pass
            except OSError as exc:
                raise StorageError(f"cannot create {label}") from exc

        flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(name, flags, dir_fd=self._descriptor)
        except OSError as exc:
            raise StorageError(f"{label} is missing or unsafe") from exc
        try:
            metadata = os.fstat(descriptor)
            self._validate_metadata(metadata, label)
            visible = os.stat(
                name,
                dir_fd=self._descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISDIR(visible.st_mode)
                or visible.st_dev != metadata.st_dev
                or visible.st_ino != metadata.st_ino
            ):
                raise StorageError(f"{label} changed while it was opened")
            self._require_path_binding()
            return _BoundDirectory(
                self.path / name,
                descriptor,
                metadata.st_dev,
                metadata.st_ino,
                label,
            )
        except BaseException:
            os.close(descriptor)
            raise

    def _read_regular_bound(self, name: str, label: str) -> bytes | None:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor = os.open(name, flags, dir_fd=self._descriptor)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise StorageError(f"cannot read {label}") from exc
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise StorageError(f"{label} is not a regular file")
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = -1
                return handle.read()
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def atomic_replace(
        self,
        name: str,
        content: bytes,
        mode: int,
    ) -> _BoundWriteOutcome:
        """Atomically replace one leaf relative to the retained directory."""

        self._require_open()
        self._require_leaf_name(name)
        if not isinstance(content, bytes):
            raise StorageError("atomic content must be bytes")
        if not isinstance(mode, int) or isinstance(mode, bool):
            raise StorageError("atomic file mode must be an integer")
        self._validate_bound_descriptor()
        self._require_path_binding()

        temporary_name, descriptor = self._create_temporary(name)
        committed = False
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())

            self._require_path_binding()
            os.replace(
                temporary_name,
                name,
                src_dir_fd=self._descriptor,
                dst_dir_fd=self._descriptor,
            )
            committed = True
            directory_synced = self._sync_directory()
            if not self._path_matches_binding():
                self._remove_committed_if_equal(name, content)
                raise StorageError(f"{self._label} changed during atomic commit")
            return _BoundWriteOutcome(
                replaced=True,
                directory_synced=directory_synced,
            )
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if not committed:
                self._unlink_if_present(temporary_name)

    def atomic_create(
        self,
        name: str,
        content: bytes,
        mode: int,
    ) -> _BoundCreateOutcome:
        """Create one immutable leaf without ever replacing an existing leaf."""

        self._require_open()
        self._require_leaf_name(name)
        if not isinstance(content, bytes):
            raise StorageError("atomic content must be bytes")
        if not isinstance(mode, int) or isinstance(mode, bool):
            raise StorageError("atomic file mode must be an integer")
        self._validate_bound_descriptor()
        self._require_path_binding()

        temporary_name, descriptor = self._create_temporary(name)
        try:
            os.fchmod(descriptor, mode)
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = -1
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            self._require_path_binding()
            try:
                os.link(
                    temporary_name,
                    name,
                    src_dir_fd=self._descriptor,
                    dst_dir_fd=self._descriptor,
                    follow_symlinks=False,
                )
            except FileExistsError:
                return _BoundCreateOutcome(False, True)
            directory_synced = self._sync_directory()
            if not self._path_matches_binding():
                self._remove_committed_if_equal(name, content)
                raise StorageError(f"{self._label} changed during atomic create")
            return _BoundCreateOutcome(True, directory_synced)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            self._unlink_if_present(temporary_name)

    def close(self) -> None:
        if self._descriptor >= 0:
            os.close(self._descriptor)
            self._descriptor = -1

    def __enter__(self) -> _BoundDirectory:
        self._require_open()
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except OSError:
            pass

    def _create_temporary(self, target: str) -> tuple[str, int]:
        for _ in range(128):
            candidate = f".{target}.{token_hex(8)}.tmp"
            try:
                descriptor = os.open(
                    candidate,
                    os.O_WRONLY
                    | os.O_CREAT
                    | os.O_EXCL
                    | getattr(os, "O_NOFOLLOW", 0),
                    0o600,
                    dir_fd=self._descriptor,
                )
            except FileExistsError:
                continue
            return candidate, descriptor
        raise StorageError("cannot allocate an atomic temporary file")

    def _sync_directory(self) -> bool:
        try:
            os.fsync(self._descriptor)
        except OSError:
            return False
        return True

    def _remove_committed_if_equal(self, name: str, content: bytes) -> None:
        try:
            visible = self._read_regular_bound(name, "atomic target")
            if visible == content:
                os.unlink(name, dir_fd=self._descriptor)
                self._sync_directory()
        except (OSError, StorageError):
            pass

    def _unlink_if_present(self, name: str) -> None:
        try:
            os.unlink(name, dir_fd=self._descriptor)
        except FileNotFoundError:
            pass
        except OSError as exc:
            if exc.errno != errno.ENOENT:
                raise

    def _validate_bound_descriptor(self) -> None:
        metadata = os.fstat(self._descriptor)
        self._validate_metadata(metadata, self._label)
        if metadata.st_dev != self._device or metadata.st_ino != self._inode:
            raise StorageError(f"{self._label} descriptor identity changed")

    def _require_path_binding(self) -> None:
        if not self._path_matches_binding():
            raise StorageError(f"{self._label} changed after it was opened")

    def _path_matches_binding(self) -> bool:
        try:
            visible = os.stat(self.path, follow_symlinks=False)
        except OSError:
            return False
        return (
            stat.S_ISDIR(visible.st_mode)
            and visible.st_dev == self._device
            and visible.st_ino == self._inode
        )

    @staticmethod
    def _require_leaf_name(name: str) -> None:
        if (
            not isinstance(name, str)
            or not name
            or name in {".", ".."}
            or Path(name).name != name
            or "/" in name
            or (os.altsep is not None and os.altsep in name)
        ):
            raise StorageError("bound directory accepts only leaf file names")

    def _require_open(self) -> None:
        if self._descriptor < 0:
            raise StorageError(f"{self._label} is closed")
