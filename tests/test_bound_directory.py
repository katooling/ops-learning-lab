from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from ops_learning_lab._bound_directory import _BoundDirectory
from ops_learning_lab.storage import StorageError


class BoundDirectoryTests(unittest.TestCase):
    def test_read_and_atomic_replace_stay_bound_to_approved_inode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approved = root / "approved"
            approved.mkdir()
            bound = _BoundDirectory.open(approved, "approved directory")
            try:
                outcome = bound.atomic_replace("artifact.txt", b"safe", 0o644)
                self.assertTrue(outcome.replaced)
                self.assertEqual(
                    bound.read_regular("artifact.txt", "artifact"),
                    b"safe",
                )
                created = bound.atomic_create("immutable.txt", b"first", 0o600)
                existing = bound.atomic_create("immutable.txt", b"second", 0o600)
                self.assertTrue(created.created)
                self.assertFalse(existing.created)
                self.assertEqual(
                    bound.read_regular("immutable.txt", "immutable artifact"),
                    b"first",
                )
            finally:
                bound.close()

    def test_directory_swap_during_replace_never_writes_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approved = root / "approved"
            displaced = root / "displaced"
            approved.mkdir()
            bound = _BoundDirectory.open(approved, "approved directory")
            original_replace = os.replace

            def swap_then_replace(
                source: str,
                target: str,
                *,
                src_dir_fd: int,
                dst_dir_fd: int,
            ) -> None:
                approved.rename(displaced)
                approved.mkdir()
                original_replace(
                    source,
                    target,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )

            try:
                with mock.patch(
                    "ops_learning_lab._bound_directory.os.replace",
                    side_effect=swap_then_replace,
                ):
                    with self.assertRaisesRegex(
                        StorageError,
                        "changed during atomic commit",
                    ):
                        bound.atomic_replace("artifact.txt", b"safe", 0o644)
            finally:
                bound.close()

            self.assertEqual(list(approved.iterdir()), [])
            self.assertEqual(list(displaced.iterdir()), [])

    def test_existing_atomic_create_rechecks_binding_and_reports_real_sync(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            approved = Path(directory) / "approved"
            approved.mkdir()
            bound = _BoundDirectory.open(approved, "approved directory")
            try:
                bound.atomic_create("immutable.txt", b"first", 0o600)
                with mock.patch.object(
                    _BoundDirectory,
                    "_sync_directory",
                    return_value=False,
                ):
                    existing = bound.atomic_create(
                        "immutable.txt",
                        b"second",
                        0o600,
                    )
                self.assertFalse(existing.created)
                self.assertFalse(existing.directory_synced)
                self.assertEqual(
                    bound.read_regular("immutable.txt", "immutable artifact"),
                    b"first",
                )
            finally:
                bound.close()

    def test_existing_atomic_create_fails_if_ancestor_changes_at_link(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approved = root / "approved"
            displaced = root / "approved-displaced"
            approved.mkdir()
            bound = _BoundDirectory.open(approved, "approved directory")
            try:
                bound.atomic_create("immutable.txt", b"first", 0o600)

                def swap_then_report_existing(*_: object, **__: object) -> None:
                    approved.rename(displaced)
                    approved.mkdir()
                    raise FileExistsError

                with mock.patch(
                    "ops_learning_lab._bound_directory.os.link",
                    side_effect=swap_then_report_existing,
                ):
                    with self.assertRaisesRegex(
                        StorageError,
                        "changed after it was opened",
                    ):
                        bound.atomic_create("immutable.txt", b"second", 0o600)
            finally:
                bound.close()

            self.assertEqual(list(approved.iterdir()), [])
            self.assertEqual(
                (displaced / "immutable.txt").read_bytes(),
                b"first",
            )

    def test_leaf_symlink_fifo_and_traversal_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approved = root / "approved"
            approved.mkdir()
            outside = root / "outside"
            outside.write_bytes(b"private")
            (approved / "link").symlink_to(outside)
            bound = _BoundDirectory.open(approved, "approved directory")
            try:
                with self.assertRaisesRegex(StorageError, "cannot read"):
                    bound.read_regular("link", "artifact")
                with self.assertRaisesRegex(StorageError, "leaf file"):
                    bound.read_regular("../outside", "artifact")
                if hasattr(os, "mkfifo"):
                    os.mkfifo(approved / "pipe")
                    with self.assertRaisesRegex(StorageError, "regular file"):
                        bound.read_regular("pipe", "artifact")
            finally:
                bound.close()

    def test_child_directory_keeps_the_parent_ancestor_binding(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approved = root / "approved"
            child_path = approved / "child"
            child_path.mkdir(parents=True)
            parent = _BoundDirectory.open(approved, "approved directory")
            child = parent.open_child_directory("child", "child directory")
            displaced = root / "approved-displaced"
            try:
                approved.rename(displaced)
                approved.mkdir()

                with self.assertRaisesRegex(
                    StorageError,
                    "changed after it was opened",
                ):
                    parent.open_child_directory("child", "child directory")
                with self.assertRaisesRegex(
                    StorageError,
                    "changed after it was opened",
                ):
                    child.atomic_replace("artifact.txt", b"safe", 0o600)
            finally:
                child.close()
                parent.close()

            self.assertEqual(list(approved.iterdir()), [])
            self.assertEqual(list((displaced / "child").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
