from __future__ import annotations

import os
from pathlib import Path
import tempfile
from threading import Thread
import unittest

from ops_learning_lab.bundle_repository import BundleRepository
from ops_learning_lab.domain import SchemaError
from ops_learning_lab.lesson_content import build_codex_etl_bundle
from ops_learning_lab.promotion_models import (
    AcceptedClaim,
    AcceptedPackSnapshot,
    AcceptedProvenance,
)
from ops_learning_lab.storage import LearningHome, StorageError


NOW = "2026-07-24T12:00:00Z"


def accepted_snapshot(*, version: int = 1, digest: str = "a" * 64):
    return AcceptedPackSnapshot(
        pack_id="codex-etl",
        title="Synthetic Codex ETL",
        version=version,
        content_sha256=digest,
        claims=(
            AcceptedClaim(
                claim_id="claim-" + "1" * 20,
                text="A successful job does not prove valid downstream data.",
                fact_status="current",
                history_action="add",
                target_claim_id=None,
                provenance=AcceptedProvenance(
                    source_type="synthetic-note",
                    observed_at=NOW,
                    staged_update_id="update-" + "2" * 20,
                    proposal_id="proposal-" + "3" * 20,
                ),
            ),
        ),
    )


class BundleRepositoryTests(unittest.TestCase):
    def test_explicit_save_is_content_addressed_and_current_pack_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            repository = BundleRepository.open(home.root)
            snapshot = accepted_snapshot()
            bundle = build_codex_etl_bundle(snapshot)

            self.assertEqual(repository.save(bundle), bundle)
            self.assertEqual(repository.save(bundle), bundle)
            self.assertEqual(
                repository.require_current(bundle.bundle_sha256, snapshot),
                bundle,
            )
            path = repository.root / f"{bundle.bundle_sha256}.json"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

            with self.assertRaisesRegex(SchemaError, "current accepted"):
                repository.require_current(
                    bundle.bundle_sha256,
                    accepted_snapshot(version=2, digest="b" * 64),
                )

    def test_rejects_symlink_fifo_and_wrong_digest_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            repository = BundleRepository.open(home.root)
            bundle = build_codex_etl_bundle(accepted_snapshot())
            path = repository.root / f"{bundle.bundle_sha256}.json"

            path.symlink_to(home.root / ".ops-learning-lab-home")
            with self.assertRaisesRegex(StorageError, "cannot read"):
                repository.snapshot(bundle.bundle_sha256)
            path.unlink()

            if hasattr(os, "mkfifo"):
                os.mkfifo(path)
                with self.assertRaisesRegex(StorageError, "regular file"):
                    repository.snapshot(bundle.bundle_sha256)
                path.unlink()

            repository.save(bundle)
            wrong = "f" * 64
            wrong_path = repository.root / f"{wrong}.json"
            wrong_path.write_bytes(path.read_bytes())
            with self.assertRaisesRegex(SchemaError, "filename"):
                repository.snapshot(wrong)

    def test_root_replacement_and_concurrent_idempotent_saves_fail_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            repository = BundleRepository.open(home.root)
            bundle = build_codex_etl_bundle(accepted_snapshot())
            failures: list[BaseException] = []

            def save() -> None:
                try:
                    repository.save(bundle)
                except BaseException as exc:
                    failures.append(exc)

            workers = [Thread(target=save) for _ in range(8)]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join()
            self.assertEqual(failures, [])
            self.assertEqual(repository.snapshot(bundle.bundle_sha256), bundle)

            snapshot_root = repository.root
            moved = snapshot_root.with_name("learning-packs-moved")
            snapshot_root.rename(moved)
            snapshot_root.symlink_to(moved, target_is_directory=True)
            with self.assertRaisesRegex(StorageError, "changed"):
                repository.snapshot(bundle.bundle_sha256)


if __name__ == "__main__":
    unittest.main()
