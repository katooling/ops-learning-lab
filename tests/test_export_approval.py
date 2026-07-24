from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from ops_learning_lab._bound_directory import _BoundDirectory
from ops_learning_lab.domain import SchemaError
from ops_learning_lab.export_approval import (
    ExportApproval,
    ExportApprovalRepository,
)
from ops_learning_lab.storage import StorageError
from tests.fixtures_learning import accepted_snapshot, bundle


class ExportApprovalTests(unittest.TestCase):
    def test_approval_is_deterministic_strict_and_bundle_complete(self) -> None:
        learning_bundle = bundle()
        first = ExportApproval.build(learning_bundle, accepted_snapshot())
        second = ExportApproval.build(learning_bundle, accepted_snapshot())

        self.assertEqual(first, second)
        self.assertEqual(first.bundle_sha256, learning_bundle.bundle_sha256)
        self.assertEqual(
            first.accepted_snapshot_sha256,
            learning_bundle.accepted_snapshot_sha256,
        )
        self.assertRegex(first.approval_sha256, r"^[0-9a-f]{64}$")
        self.assertEqual(
            first.approval_id,
            f"export-approval-{first.approval_sha256[:20]}",
        )
        with self.assertRaisesRegex(SchemaError, "fields"):
            ExportApproval.from_dict(
                {**first.to_dict(), "silent_extra_authority": True}
            )

    def test_repository_is_immutable_and_rejects_tampered_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approvals = root / "approvals"
            approvals.mkdir()
            learning_bundle = bundle()
            approval = ExportApproval.build(
                learning_bundle,
                accepted_snapshot(),
            )
            repository = ExportApprovalRepository(
                _BoundDirectory.open(approvals, "export approval directory")
            )
            try:
                self.assertEqual(repository.save(approval), approval)
                self.assertEqual(repository.save(approval), approval)
                self.assertEqual(repository.require(learning_bundle), approval)

                path = approvals / f"{learning_bundle.bundle_sha256}.json"
                value = json.loads(path.read_text(encoding="utf-8"))
                value["pack_version"] = 2
                path.write_text(json.dumps(value), encoding="utf-8")
                with self.assertRaisesRegex(SchemaError, "does not match"):
                    repository.require(learning_bundle)
            finally:
                repository.close()

    def test_missing_approval_never_inherits_authority_from_bundle_storage(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            approvals = root / "approvals"
            approvals.mkdir()
            repository = ExportApprovalRepository(
                _BoundDirectory.open(approvals, "export approval directory")
            )
            try:
                with self.assertRaisesRegex(StorageError, "explicitly approved"):
                    repository.require(bundle())
            finally:
                repository.close()


if __name__ == "__main__":
    unittest.main()
