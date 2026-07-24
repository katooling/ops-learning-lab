from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from ops_learning_lab.bundle_repository import BundleRepository
from ops_learning_lab.compiler import compile_update
from ops_learning_lab.domain import SchemaError
from ops_learning_lab.domain import SourceReference
from ops_learning_lab.export_repository import ExportRepository
from ops_learning_lab.export_approval import ExportApproval
from ops_learning_lab.exporting import ExportError, ExportPolicy, StandaloneExporter
from ops_learning_lab.learning_bundle import LearningPackBundle
from ops_learning_lab.pack_repository import PackRepository
from ops_learning_lab.promotion import PromotionService
from ops_learning_lab.promotion_models import PromotionDecision, PromotionPlan
from ops_learning_lab.staging import PackUpdateRepository
from ops_learning_lab.storage import LearningHome, StorageError
from tests.fixtures_learning import accepted_snapshot, bundle, learning_pack


CANARY = b"PRIVATE-CANARY-export-7f39c2"


def repository(root: Path) -> ExportRepository:
    exports = root / "exports"
    exports.mkdir()
    return ExportRepository.open(exports)


def approved_export(
    exporter: StandaloneExporter,
    learning_bundle: LearningPackBundle,
    policy: ExportPolicy,
):
    return exporter.export(
        learning_bundle,
        policy,
        approval=ExportApproval.build(learning_bundle, accepted_snapshot()),
    )


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "ops_learning_lab", *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


def start_cli_paused_after_publishable_home_open(
    *arguments: str,
) -> subprocess.Popen[str]:
    """Run the real CLI with one deterministic test-only process checkpoint."""

    script = """
import os
import signal
import sys
from ops_learning_lab.cli import main
from ops_learning_lab.publishable_home import PublishableHome

original_open = PublishableHome.open.__func__

def paused_open(cls, path):
    home = original_open(cls, path)
    os.kill(os.getpid(), signal.SIGSTOP)
    return home

PublishableHome.open = classmethod(paused_open)
raise SystemExit(main(sys.argv[1:]))
"""
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    return subprocess.Popen(
        [sys.executable, "-c", script, *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def prepare_cli_export(
    root: Path,
    *,
    approve: bool = True,
) -> tuple[Path, str, Path]:
    home = LearningHome.initialize(root / "learning-home")
    pack_root = home.root / "packs" / "synthetic-etl"
    pack_root.mkdir()
    (pack_root / "pack.json").write_text(
        json.dumps(
            learning_pack().to_dict(),
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    learning_bundle = bundle()
    with BundleRepository.open(home.root) as bundles:
        bundles.save(learning_bundle)
    canary_file = root / "canary.bin"
    canary_file.write_bytes(CANARY)
    if approve:
        result = run_cli(
            "export-approve",
            "--home",
            str(home.root),
            "--bundle-sha256",
            learning_bundle.bundle_sha256,
        )
        if result.returncode != 0:
            raise AssertionError(result.stderr)
    return home.root, learning_bundle.bundle_sha256, canary_file


def rebuild_lesson(lesson, **changes):
    values = {
        "lesson_id": lesson.lesson_id,
        "title": lesson.title,
        "concept_id": lesson.concept_id,
        "claim_id": lesson.claim_id,
        "outcome": lesson.outcome,
        "map_stages": lesson.map_stages,
        "prediction": lesson.prediction,
        "activity": lesson.activity,
        "evidence": lesson.evidence,
        "explanation": lesson.explanation,
    }
    values.update(changes)
    return lesson.__class__.build(**values)


class StandaloneExporterTests(unittest.TestCase):
    def test_cli_approval_is_explicit_deterministic_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, bundle_sha256, _ = prepare_cli_export(root, approve=False)
            arguments = (
                "export-approve",
                "--home",
                str(home),
                "--bundle-sha256",
                bundle_sha256,
            )

            first = run_cli(*arguments)
            second = run_cli(*arguments)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            first_receipt = json.loads(first.stdout)
            second_receipt = json.loads(second.stdout)
            self.assertEqual(first_receipt["status"], "approved")
            self.assertEqual(second_receipt["status"], "already-approved")
            self.assertEqual(
                {
                    key: value
                    for key, value in first_receipt.items()
                    if key != "status"
                },
                {
                    key: value
                    for key, value in second_receipt.items()
                    if key != "status"
                },
            )
            self.assertEqual(
                len(list((home / "snapshots" / "export-approvals").iterdir())),
                1,
            )

    def test_cli_rejects_canonical_but_unapproved_bundle_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, bundle_sha256, canary_file = prepare_cli_export(
                root,
                approve=False,
            )

            unapproved = run_cli(
                "export",
                "--home",
                str(home),
                "--bundle-sha256",
                bundle_sha256,
                "--canary-file",
                str(canary_file),
            )

            self.assertEqual(unapproved.returncode, 1)
            self.assertIn("explicitly approved", unapproved.stderr)
            self.assertEqual(list((home / "exports").iterdir()), [])

            original = bundle()
            changed = LearningPackBundle.build(
                accepted_snapshot(),
                concepts=(
                    replace(
                        original.concepts[0],
                        title="Unapproved but schema-valid concept",
                    ),
                ),
                lessons=(
                    rebuild_lesson(
                        original.lessons[0],
                        title="Unapproved but schema-valid lesson",
                    ),
                ),
            )
            with BundleRepository.open(home) as bundles:
                bundles.save(changed)

            changed_export = run_cli(
                "export",
                "--home",
                str(home),
                "--bundle-sha256",
                changed.bundle_sha256,
                "--canary-file",
                str(canary_file),
            )

            self.assertEqual(changed_export.returncode, 1)
            self.assertIn("explicitly approved", changed_export.stderr)
            self.assertEqual(list((home / "exports").iterdir()), [])

    def test_cli_exports_only_a_canonical_stored_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, bundle_sha256, canary_file = prepare_cli_export(root)

            result = run_cli(
                "export",
                "--home",
                str(home),
                "--bundle-sha256",
                bundle_sha256,
                "--canary-file",
                str(canary_file),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["privacy_status"], "passed")
            self.assertEqual(receipt["files_scanned"], 1)
            self.assertRegex(
                receipt["approval_id"],
                r"^export-approval-[0-9a-f]{20}$",
            )
            self.assertRegex(receipt["approval_sha256"], r"^[0-9a-f]{64}$")
            artifact = home / "exports" / receipt["relative_path"]
            self.assertTrue(artifact.is_file())

    def test_later_promotion_does_not_invalidate_approved_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home_path, bundle_sha256, canary_file = prepare_cli_export(root)
            home = LearningHome.open(home_path)
            raw = b"Claim: A second synthetic reviewed claim."
            manifest = home.capture(
                raw,
                SourceReference(
                    source_type="synthetic-note",
                    source_id="second-promotion",
                    observed_at="2026-07-25T12:00:00Z",
                ),
            )
            updates = PackUpdateRepository.open(home.root)
            staged = updates.stage(compile_update(raw, manifest))
            service = PromotionService(
                updates,
                PackRepository.open(home.root),
                clock=lambda: "2026-07-25T12:00:01Z",
            )
            review = service.review(
                staged.update_id,
                target_pack_id="synthetic-etl",
                target_pack_title="Synthetic ETL evidence",
            )
            plan = PromotionPlan(
                update_id=staged.update_id,
                proposal_sha256=staged.proposal_sha256,
                target_pack_id=review.target_pack_id,
                target_pack_title=review.target_pack_title,
                expected_base_version=review.expected_base_version,
                expected_base_sha256=review.expected_base_sha256,
                decisions=(
                    PromotionDecision(
                        proposal_id=staged.proposed_claims[0].proposal_id,
                        action="accept",
                        sanitized_text="A later accepted snapshot exists.",
                        fact_status="current",
                        history_action="add",
                        target_claim_id=None,
                        sensitivity_reviewed=True,
                        rejection_reason=None,
                    ),
                ),
            )
            preview = service.preview(plan)
            service.commit(plan, preview.preview_sha256)
            current = PackRepository.open(home.root).snapshot("synthetic-etl")
            self.assertIsNotNone(current)
            assert current is not None
            self.assertEqual(current.version, 2)

            result = run_cli(
                "export",
                "--home",
                str(home.root),
                "--bundle-sha256",
                bundle_sha256,
                "--canary-file",
                str(canary_file),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(
                receipt["accepted_snapshot_sha256"],
                bundle().accepted_snapshot_sha256,
            )
            self.assertNotEqual(
                receipt["accepted_snapshot_sha256"],
                current.content_sha256,
            )

    def test_cli_rejects_malformed_or_symlinked_canonical_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, bundle_sha256, canary_file = prepare_cli_export(root)
            bundle_file = (
                home
                / "snapshots"
                / "learning-packs"
                / f"{bundle_sha256}.json"
            )
            bundle_file.write_text(
                '{"schema_version":1,"schema_version":1}',
                encoding="utf-8",
            )
            arguments = (
                "export",
                "--home",
                str(home),
                "--bundle-sha256",
                bundle_sha256,
                "--canary-file",
                str(canary_file),
            )

            malformed = run_cli(*arguments)

            self.assertEqual(malformed.returncode, 1)
            self.assertIn("does not match the schema", malformed.stderr)
            self.assertEqual(list((home / "exports").iterdir()), [])

            bundle_file.unlink()
            external = root / "untrusted-bundle.json"
            external.write_text(
                json.dumps(bundle().to_dict(), sort_keys=True),
                encoding="utf-8",
            )
            bundle_file.symlink_to(external)
            symlinked = run_cli(*arguments)
            self.assertEqual(symlinked.returncode, 1)
            self.assertIn("cannot read", symlinked.stderr)
            self.assertEqual(list((home / "exports").iterdir()), [])

    def test_initialized_home_private_capture_to_cli_export_stays_sanitized(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = LearningHome.initialize(root / "learning-home")
            raw = (
                b"Synthetic ETL evidence.\nClaim: "
                + CANARY
                + b" is private source detail.\n"
            )
            manifest = home.capture(
                raw,
                SourceReference(
                    source_type="synthetic-private-note",
                    source_id="private-source-identifier",
                    observed_at="2026-07-24T12:00:00Z",
                ),
            )
            updates = PackUpdateRepository.open(home.root)
            packs = PackRepository.open(home.root)
            staged = updates.stage(compile_update(raw, manifest))
            service = PromotionService(
                updates,
                packs,
                forbidden_canaries=(CANARY.decode("utf-8"),),
                clock=lambda: "2026-07-24T12:00:01Z",
            )
            review = service.review(
                staged.update_id,
                target_pack_id="synthetic-etl",
                target_pack_title="Synthetic ETL evidence",
            )
            plan = PromotionPlan(
                update_id=staged.update_id,
                proposal_sha256=staged.proposal_sha256,
                target_pack_id=review.target_pack_id,
                target_pack_title=review.target_pack_title,
                expected_base_version=review.expected_base_version,
                expected_base_sha256=review.expected_base_sha256,
                decisions=(
                    PromotionDecision(
                        proposal_id=staged.proposed_claims[0].proposal_id,
                        action="accept",
                        sanitized_text=(
                            "A normalized synthetic cost needs explicit "
                            "validation evidence."
                        ),
                        fact_status="current",
                        history_action="add",
                        target_claim_id=None,
                        sensitivity_reviewed=True,
                        rejection_reason=None,
                    ),
                ),
            )
            preview = service.preview(plan)
            service.commit(plan, preview.preview_sha256)
            snapshot = packs.snapshot("synthetic-etl")
            self.assertIsNotNone(snapshot)
            assert snapshot is not None
            template = bundle()
            learning_bundle = LearningPackBundle.build(
                snapshot,
                concepts=template.concepts,
                lessons=(
                    rebuild_lesson(
                        template.lessons[0],
                        claim_id=snapshot.claims[0].claim_id,
                    ),
                ),
            )
            with BundleRepository.open(home.root) as bundles:
                bundles.save(learning_bundle)
            canary_file = root / "private-canary.bin"
            canary_file.write_bytes(CANARY)
            approved = run_cli(
                "export-approve",
                "--home",
                str(home.root),
                "--bundle-sha256",
                learning_bundle.bundle_sha256,
            )
            self.assertEqual(approved.returncode, 0, approved.stderr)

            result = run_cli(
                "export",
                "--home",
                str(home.root),
                "--bundle-sha256",
                learning_bundle.bundle_sha256,
                "--canary-file",
                str(canary_file),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(
                receipt["bundle_sha256"],
                learning_bundle.bundle_sha256,
            )
            self.assertEqual(home.audit_canary(CANARY), [])
            generated = [
                path
                for area in ("packs", "snapshots", "exports")
                for path in (home.root / area).rglob("*")
                if path.is_file()
            ]
            self.assertGreaterEqual(len(generated), 3)
            for path in generated:
                self.assertNotIn(CANARY, path.read_bytes(), path)

    def test_cli_rejects_replaced_learning_home_before_adapter_setup(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, bundle_sha256, canary_file = prepare_cli_export(root)
            moved = root / "learning-home-moved"
            home.rename(moved)
            home.symlink_to(moved, target_is_directory=True)

            result = run_cli(
                "export",
                "--home",
                str(home),
                "--bundle-sha256",
                bundle_sha256,
                "--canary-file",
                str(canary_file),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("missing or unsafe", result.stderr)
            self.assertEqual(list((moved / "exports").iterdir()), [])

    @unittest.skipUnless(
        hasattr(os, "WUNTRACED") and hasattr(signal, "SIGSTOP"),
        "requires POSIX process-stop support",
    )
    def test_real_cli_rejects_ancestor_replacement_after_home_is_open(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, bundle_sha256, canary_file = prepare_cli_export(root)
            process = start_cli_paused_after_publishable_home_open(
                "export",
                "--home",
                str(home),
                "--bundle-sha256",
                bundle_sha256,
                "--canary-file",
                str(canary_file),
            )
            try:
                waited, status = os.waitpid(process.pid, os.WUNTRACED)
                self.assertEqual(waited, process.pid)
                self.assertTrue(os.WIFSTOPPED(status))

                displaced = root / "learning-home-displaced"
                home.rename(displaced)
                home.mkdir()
                (home / "exports").mkdir()
                os.kill(process.pid, signal.SIGCONT)
                stdout, stderr = process.communicate(timeout=10)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()

            self.assertEqual(process.returncode, 1, stdout)
            self.assertIn("learning home changed after it was opened", stderr)
            self.assertEqual(list((home / "exports").iterdir()), [])
            self.assertEqual(list((displaced / "exports").iterdir()), [])

    def test_exporter_capability_excludes_private_and_staged_stores(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exporter = StandaloneExporter(repository(root))

            receipt = approved_export(exporter, bundle(), ExportPolicy((CANARY,)))

            self.assertEqual(set(exporter.__dict__), {"repository"})
            self.assertEqual(
                set(exporter.repository.__dict__),
                {"_directory", "root"},
            )
            self.assertFalse((root / "private").exists())
            self.assertFalse((root / "staged").exists())
            self.assertTrue((root / "exports" / receipt.relative_path).is_file())

    def test_export_is_stable_allowlisted_and_independent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exporter = StandaloneExporter(repository(root))
            policy = ExportPolicy((CANARY,))

            first = approved_export(exporter, bundle(), policy)
            second = approved_export(exporter, bundle(), policy)

            self.assertFalse(first.already_exists)
            self.assertTrue(second.already_exists)
            self.assertEqual(first.export_id, second.export_id)
            self.assertEqual(first.artifact_sha256, second.artifact_sha256)
            self.assertEqual(first.relative_path, second.relative_path)
            self.assertEqual(first.privacy_status, "passed")
            self.assertEqual(first.files_scanned, 1)

            artifact = root / "exports" / first.relative_path
            content = artifact.read_text(encoding="utf-8")
            self.assertIn("Synthetic ETL evidence", content)
            self.assertIn("Evidence scope", content)
            self.assertIn("Trace one safe record", content)
            self.assertIn("Does not prove", content)
            self.assertIn(bundle().bundle_sha256, content)
            self.assertIn(bundle().accepted_snapshot_sha256, content)
            self.assertIn(first.approval_id, content)
            self.assertIn(first.approval_sha256, content)
            self.assertIn(bundle().lessons[0].lesson_revision_sha256, content)
            self.assertIn(
                bundle().lessons[0].outcome.outcome_revision_sha256,
                content,
            )
            self.assertIn(
                bundle().lessons[0].activity.input_revision_sha256,
                content,
            )
            self.assertEqual(content.count("<li><dl><dt>Event</dt>"), 3)
            self.assertIn("<dd>event-001</dd>", content)
            self.assertIn("<dd>event-002</dd>", content)
            self.assertIn("<dt>Duplicate excess rows</dt><dd>1</dd>", content)
            self.assertIn("<dt>Published total</dt><dd>7 cents</dd>", content)
            self.assertIn(
                "<dt>Unique-record total</dt><dd>5 cents</dd>",
                content,
            )
            self.assertNotIn("update-" + "2" * 20, content)
            self.assertNotIn("proposal-" + "3" * 20, content)
            self.assertNotIn("expected_choice_id", content)
            self.assertNotIn("file:", content)
            self.assertNotIn("localhost", content)
            self.assertNotIn("<script", content)
            self.assertNotIn(" src=", content)
            self.assertNotIn('href="http', content)
            self.assertIn('href="#content"', content)

            outside = root / "portable.html"
            shutil.copyfile(artifact, outside)
            shutil.rmtree(root / "exports")
            self.assertIn("Trace one safe record", outside.read_text(encoding="utf-8"))

    def test_semantic_copy_change_changes_artifact_identity_and_path(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exporter = StandaloneExporter(repository(root))
            original = bundle()
            changed = LearningPackBundle.build(
                accepted_snapshot(),
                concepts=original.concepts,
                lessons=(
                    rebuild_lesson(
                        original.lessons[0],
                        activity=replace(
                            original.lessons[0].activity,
                            instructions=(
                                "Apply the same deterministic validation rule, "
                                "then inspect every public result."
                            ),
                        ),
                    ),
                ),
            )

            first = approved_export(exporter, original, ExportPolicy((CANARY,)))
            second = approved_export(exporter, changed, ExportPolicy((CANARY,)))

            self.assertNotEqual(first.export_id, second.export_id)
            self.assertNotEqual(first.artifact_sha256, second.artifact_sha256)
            self.assertNotEqual(first.relative_path, second.relative_path)

    def test_canary_is_checked_before_and_after_html_escaping(self) -> None:
        original = bundle()
        canary = b"<PRIVATE&CANARY>"
        unsafe = LearningPackBundle.build(
            accepted_snapshot(),
            concepts=(
                replace(
                    original.concepts[0],
                    summary=f"Unsafe {canary.decode('utf-8')} content",
                ),
            ),
            lessons=original.lessons,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exporter = StandaloneExporter(repository(root))

            with self.assertRaisesRegex(ExportError, "privacy canary"):
                approved_export(exporter, unsafe, ExportPolicy((canary,)))

            self.assertEqual(list((root / "exports").iterdir()), [])

    def test_renderer_escapes_reviewed_html(self) -> None:
        original = bundle()
        safe = LearningPackBundle.build(
            accepted_snapshot(),
            concepts=(
                replace(
                    original.concepts[0],
                    summary="<script>alert('synthetic')</script>",
                ),
            ),
            lessons=original.lessons,
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            receipt = approved_export(
                StandaloneExporter(repository(root)),
                safe,
                ExportPolicy((CANARY,)),
            )
            content = (root / "exports" / receipt.relative_path).read_text(
                encoding="utf-8"
            )
            self.assertNotIn("<script>alert", content)
            self.assertIn("&lt;script&gt;alert", content)

    def test_oversize_and_renderer_canary_fail_before_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exporter = StandaloneExporter(repository(root))
            with self.assertRaisesRegex(ExportError, "size limit"):
                approved_export(
                    exporter,
                    bundle(),
                    ExportPolicy((CANARY,), max_export_bytes=10),
                )
            with mock.patch(
                "ops_learning_lab.exporting._render_html",
                return_value=b"<html>" + CANARY + b"</html>",
            ):
                with self.assertRaisesRegex(ExportError, "privacy canary"):
                    approved_export(exporter, bundle(), ExportPolicy((CANARY,)))
            self.assertEqual(list((root / "exports").iterdir()), [])

    def test_repository_rejects_path_symlink_and_fifo_hazards(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = repository(root)
            with self.assertRaisesRegex(StorageError, "pack_id"):
                repo.commit("../escape", "export-" + "1" * 20, b"safe")

            outside = root / "outside"
            outside.mkdir()
            exports = root / "exports"
            exports.rmdir()
            exports.symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(StorageError, "missing or unsafe"):
                ExportRepository.open(exports)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = repository(root)
            exporter = StandaloneExporter(repo)
            receipt = approved_export(exporter, bundle(), ExportPolicy((CANARY,)))
            target = root / "exports" / receipt.relative_path
            target.unlink()
            outside = root / "outside.html"
            outside.write_text("untouched", encoding="utf-8")
            target.symlink_to(outside)
            with self.assertRaisesRegex(StorageError, "cannot read"):
                approved_export(exporter, bundle(), ExportPolicy((CANARY,)))
            self.assertEqual(outside.read_text(encoding="utf-8"), "untouched")

        if hasattr(os, "mkfifo"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo = repository(root)
                exporter = StandaloneExporter(repo)
                receipt = approved_export(
                    exporter,
                    bundle(),
                    ExportPolicy((CANARY,)),
                )
                target = root / "exports" / receipt.relative_path
                target.unlink()
                os.mkfifo(target)
                with self.assertRaisesRegex(StorageError, "regular file"):
                    approved_export(
                        exporter,
                        bundle(),
                        ExportPolicy((CANARY,)),
                    )

    def test_interrupted_write_leaves_prior_export_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = repository(root)
            exporter = StandaloneExporter(repo)
            prior = approved_export(exporter, bundle(), ExportPolicy((CANARY,)))
            prior_path = root / "exports" / prior.relative_path
            prior_bytes = prior_path.read_bytes()
            original = bundle()
            changed = LearningPackBundle.build(
                accepted_snapshot(),
                concepts=(
                    replace(original.concepts[0], title="Revised evidence scope"),
                ),
                lessons=original.lessons,
            )

            with mock.patch.object(
                repo._directory,
                "atomic_replace",
                side_effect=OSError("synthetic interruption"),
            ):
                with self.assertRaisesRegex(OSError, "synthetic interruption"):
                    approved_export(exporter, changed, ExportPolicy((CANARY,)))

            self.assertEqual(prior_path.read_bytes(), prior_bytes)
            self.assertEqual(
                [path.name for path in (root / "exports").iterdir()],
                [prior_path.name],
            )

    def test_export_commit_detects_directory_swap_without_writing_replacement(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = repository(root)
            exporter = StandaloneExporter(repo)
            exports = root / "exports"
            displaced = root / "exports-displaced"
            original_replace = os.replace

            def swap_then_replace(
                source: str,
                target: str,
                *,
                src_dir_fd: int,
                dst_dir_fd: int,
            ) -> None:
                exports.rename(displaced)
                exports.mkdir()
                original_replace(
                    source,
                    target,
                    src_dir_fd=src_dir_fd,
                    dst_dir_fd=dst_dir_fd,
                )

            with mock.patch(
                "ops_learning_lab._bound_directory.os.replace",
                side_effect=swap_then_replace,
            ):
                with self.assertRaisesRegex(
                    StorageError,
                    "changed during atomic commit",
                ):
                    approved_export(
                        exporter,
                        bundle(),
                        ExportPolicy((CANARY,)),
                    )

            self.assertEqual(list(exports.iterdir()), [])
            self.assertEqual(list(displaced.iterdir()), [])

    def test_export_rejects_unvalidated_input_and_missing_canary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            exporter = StandaloneExporter(repository(Path(directory)))
            with self.assertRaisesRegex(ExportError, "validated"):
                exporter.export(  # type: ignore[arg-type]
                    {"pack_id": "synthetic"},
                    ExportPolicy((CANARY,)),
                    approval=ExportApproval.build(bundle(), accepted_snapshot()),
                )
            with self.assertRaisesRegex(ExportError, "at least one"):
                ExportPolicy(())
            with self.assertRaises(SchemaError):
                LearningPackBundle.from_dict(
                    {**bundle().to_dict(), "unapproved_attachment": "path"}
                )


if __name__ == "__main__":
    unittest.main()
