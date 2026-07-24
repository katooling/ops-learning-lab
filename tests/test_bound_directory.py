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


if __name__ == "__main__":
    unittest.main()
