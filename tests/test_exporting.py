from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from ops_learning_lab.domain import SchemaError
from ops_learning_lab.export_repository import ExportRepository
from ops_learning_lab.exporting import ExportError, ExportPolicy, StandaloneExporter
from ops_learning_lab.learning_bundle import LearningPackBundle
from ops_learning_lab.storage import StorageError
from tests.fixtures_learning import accepted_snapshot, bundle, learning_pack


CANARY = b"PRIVATE-CANARY-export-7f39c2"


def repository(root: Path) -> ExportRepository:
    exports = root / "exports"
    exports.mkdir()
    return ExportRepository.open(exports)


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


def prepare_cli_export(root: Path) -> tuple[Path, Path, Path]:
    home = root / "public-home"
    pack_root = home / "packs" / "synthetic-etl"
    pack_root.mkdir(parents=True)
    (home / "exports").mkdir()
    (pack_root / "pack.json").write_text(
        json.dumps(
            learning_pack().to_dict(),
            ensure_ascii=False,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    bundle_file = root / "bundle.json"
    bundle_file.write_text(
        json.dumps(bundle().to_dict(), ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    canary_file = root / "canary.bin"
    canary_file.write_bytes(CANARY)
    return home, bundle_file, canary_file


class StandaloneExporterTests(unittest.TestCase):
    def test_cli_exports_without_private_or_staged_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, bundle_file, canary_file = prepare_cli_export(root)

            result = run_cli(
                "export",
                "--home",
                str(home),
                "--pack-id",
                "synthetic-etl",
                "--bundle",
                str(bundle_file),
                "--canary-file",
                str(canary_file),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            receipt = json.loads(result.stdout)
            self.assertEqual(receipt["privacy_status"], "passed")
            self.assertEqual(receipt["files_scanned"], 1)
            artifact = home / "exports" / receipt["relative_path"]
            self.assertTrue(artifact.is_file())
            self.assertFalse((home / "private").exists())
            self.assertFalse((home / "staged").exists())

    def test_cli_rejects_malformed_or_symlinked_bundle_before_export(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home, bundle_file, canary_file = prepare_cli_export(root)
            bundle_file.write_text(
                '{"schema_version":1,"schema_version":1}',
                encoding="utf-8",
            )
            arguments = (
                "export",
                "--home",
                str(home),
                "--pack-id",
                "synthetic-etl",
                "--bundle",
                str(bundle_file),
                "--canary-file",
                str(canary_file),
            )

            malformed = run_cli(*arguments)

            self.assertEqual(malformed.returncode, 1)
            self.assertIn("not strict JSON", malformed.stderr)
            self.assertEqual(list((home / "exports").iterdir()), [])

            bundle_file.unlink()
            external = root / "external-bundle.json"
            external.write_text(
                json.dumps(bundle().to_dict(), sort_keys=True),
                encoding="utf-8",
            )
            bundle_file.symlink_to(external)
            symlinked = run_cli(*arguments)
            self.assertEqual(symlinked.returncode, 1)
            self.assertIn("cannot be a symbolic link", symlinked.stderr)
            self.assertEqual(list((home / "exports").iterdir()), [])

    def test_export_is_stable_allowlisted_and_independent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exporter = StandaloneExporter(repository(root))
            policy = ExportPolicy((CANARY,))

            first = exporter.export(bundle(), policy)
            second = exporter.export(bundle(), policy)

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
            self.assertNotIn("update-" + "2" * 20, content)
            self.assertNotIn("proposal-" + "3" * 20, content)
            self.assertNotIn("expected_choice_id", content)
            self.assertNotIn("file:", content)
            self.assertNotIn("localhost", content)
            self.assertNotIn("<script", content)
            self.assertNotIn(" src=", content)
            self.assertNotIn(" href=", content)

            outside = root / "portable.html"
            shutil.copyfile(artifact, outside)
            shutil.rmtree(root / "exports")
            self.assertIn("Trace one safe record", outside.read_text(encoding="utf-8"))

    def test_output_identity_changes_only_with_rendered_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            exporter = StandaloneExporter(repository(root))
            original = bundle()
            changed = LearningPackBundle.build(
                accepted_snapshot(),
                concepts=(
                    replace(original.concepts[0], title="Evidence scope revised"),
                ),
                lessons=original.lessons,
            )

            first = exporter.export(original, ExportPolicy((CANARY,)))
            second = exporter.export(changed, ExportPolicy((CANARY,)))

            self.assertNotEqual(first.export_id, second.export_id)
            self.assertNotEqual(first.artifact_sha256, second.artifact_sha256)

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
                exporter.export(unsafe, ExportPolicy((canary,)))

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
            receipt = StandaloneExporter(repository(root)).export(
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
                exporter.export(
                    bundle(),
                    ExportPolicy((CANARY,), max_export_bytes=10),
                )
            with mock.patch(
                "ops_learning_lab.exporting._render_html",
                return_value=b"<html>" + CANARY + b"</html>",
            ):
                with self.assertRaisesRegex(ExportError, "privacy canary"):
                    exporter.export(bundle(), ExportPolicy((CANARY,)))
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
            receipt = exporter.export(bundle(), ExportPolicy((CANARY,)))
            target = root / "exports" / receipt.relative_path
            target.unlink()
            outside = root / "outside.html"
            outside.write_text("untouched", encoding="utf-8")
            target.symlink_to(outside)
            with self.assertRaisesRegex(StorageError, "symbolic link"):
                exporter.export(bundle(), ExportPolicy((CANARY,)))
            self.assertEqual(outside.read_text(encoding="utf-8"), "untouched")

        if hasattr(os, "mkfifo"):
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                repo = repository(root)
                exporter = StandaloneExporter(repo)
                receipt = exporter.export(bundle(), ExportPolicy((CANARY,)))
                target = root / "exports" / receipt.relative_path
                target.unlink()
                os.mkfifo(target)
                with self.assertRaisesRegex(StorageError, "regular file"):
                    exporter.export(bundle(), ExportPolicy((CANARY,)))

    def test_interrupted_write_leaves_prior_export_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = repository(root)
            exporter = StandaloneExporter(repo)
            prior = exporter.export(bundle(), ExportPolicy((CANARY,)))
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

            with mock.patch(
                "ops_learning_lab.export_repository._write_atomic",
                side_effect=OSError("synthetic interruption"),
            ):
                with self.assertRaisesRegex(OSError, "synthetic interruption"):
                    exporter.export(changed, ExportPolicy((CANARY,)))

            self.assertEqual(prior_path.read_bytes(), prior_bytes)
            self.assertEqual(
                [path.name for path in (root / "exports").iterdir()],
                [prior_path.name],
            )

    def test_export_rejects_unvalidated_input_and_missing_canary(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            exporter = StandaloneExporter(repository(Path(directory)))
            with self.assertRaisesRegex(ExportError, "validated"):
                exporter.export({"pack_id": "synthetic"}, ExportPolicy((CANARY,)))  # type: ignore[arg-type]
            with self.assertRaisesRegex(ExportError, "at least one"):
                ExportPolicy(())
            with self.assertRaises(SchemaError):
                LearningPackBundle.from_dict(
                    {**bundle().to_dict(), "unapproved_attachment": "path"}
                )


if __name__ == "__main__":
    unittest.main()
