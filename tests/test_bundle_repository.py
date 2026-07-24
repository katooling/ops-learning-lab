from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import tempfile
from threading import Thread
import unittest

from ops_learning_lab.bundle_repository import BundleRepository
from ops_learning_lab.domain import SchemaError
from ops_learning_lab.storage import LearningHome, StorageError
from tests.fixtures_learning import accepted_snapshot, bundle


class BundleRepositoryTests(unittest.TestCase):
    def test_explicit_save_is_content_addressed_and_current_pack_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            repository = BundleRepository.open(home.root)
            snapshot = accepted_snapshot()
            learning_bundle = bundle()

            self.assertEqual(repository.save(learning_bundle), learning_bundle)
            self.assertEqual(repository.save(learning_bundle), learning_bundle)
            self.assertEqual(
                repository.require_current(
                    learning_bundle.bundle_sha256,
                    snapshot,
                ),
                learning_bundle,
            )
            path = repository.root / f"{learning_bundle.bundle_sha256}.json"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

            with self.assertRaisesRegex(SchemaError, "current accepted"):
                repository.require_current(
                    learning_bundle.bundle_sha256,
                    replace(
                        accepted_snapshot(),
                        version=2,
                        content_sha256="b" * 64,
                    ),
                )

    def test_rejects_symlink_fifo_and_wrong_digest_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            repository = BundleRepository.open(home.root)
            learning_bundle = bundle()
            path = repository.root / f"{learning_bundle.bundle_sha256}.json"

            path.symlink_to(home.root / ".ops-learning-lab-home")
            with self.assertRaisesRegex(StorageError, "cannot read"):
                repository.snapshot(learning_bundle.bundle_sha256)
            path.unlink()

            if hasattr(os, "mkfifo"):
                os.mkfifo(path)
                with self.assertRaisesRegex(StorageError, "regular file"):
                    repository.snapshot(learning_bundle.bundle_sha256)
                path.unlink()

            repository.save(learning_bundle)
            wrong = "f" * 64
            wrong_path = repository.root / f"{wrong}.json"
            wrong_path.write_bytes(path.read_bytes())
            with self.assertRaisesRegex(SchemaError, "filename"):
                repository.snapshot(wrong)

    def test_root_replacement_and_concurrent_idempotent_saves_fail_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            repository = BundleRepository.open(home.root)
            learning_bundle = bundle()
            failures: list[BaseException] = []

            def save() -> None:
                try:
                    repository.save(learning_bundle)
                except BaseException as exc:
                    failures.append(exc)

            workers = [Thread(target=save) for _ in range(8)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
            self.assertEqual(failures, [])
            self.assertEqual(
                repository.snapshot(learning_bundle.bundle_sha256),
                learning_bundle,
            )

            snapshot_root = repository.root
            moved = snapshot_root.with_name("learning-packs-moved")
            snapshot_root.rename(moved)
            snapshot_root.symlink_to(moved, target_is_directory=True)
            with self.assertRaisesRegex(StorageError, "changed"):
                repository.snapshot(learning_bundle.bundle_sha256)


if __name__ == "__main__":
    unittest.main()
