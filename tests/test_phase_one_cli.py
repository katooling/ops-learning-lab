from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from ops_learning_lab.domain import SourceReference
from ops_learning_lab.storage import LearningHome, StorageError


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    existing = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = (
        f"{SOURCE_ROOT}{os.pathsep}{existing}" if existing else str(SOURCE_ROOT)
    )
    return subprocess.run(
        [sys.executable, "-m", "ops_learning_lab", *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


class PhaseOneCliTests(unittest.TestCase):
    def test_capture_is_private_idempotent_and_auditable(self) -> None:
        canary = b"PRIVATE-CANARY-7a13d9c2\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "learning-home"
            source = root / "source.txt"
            source.write_bytes(canary)

            initialized = run_cli("init", "--home", str(home))
            self.assertEqual(initialized.returncode, 0, initialized.stderr)

            capture_arguments = (
                "capture",
                "--home",
                str(home),
                "--source-type",
                "codex-task",
                "--source-id",
                "synthetic-task-1",
                "--input",
                str(source),
            )
            first = run_cli(*capture_arguments)
            second = run_cli(*capture_arguments)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            first_result = json.loads(first.stdout)
            second_result = json.loads(second.stdout)
            self.assertEqual(first_result, second_result)
            self.assertNotIn(canary.decode().strip(), first.stdout)

            intake_id = first_result["intake_id"]
            raw_path = home / "private" / "inbox" / intake_id / "raw.bin"
            manifest_path = home / "private" / "inbox" / intake_id / "manifest.json"
            self.assertEqual(raw_path.read_bytes(), canary)
            self.assertTrue(manifest_path.is_file())
            self.assertEqual(raw_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(manifest_path.stat().st_mode & 0o777, 0o600)

            for public_directory in ("packs", "snapshots", "exports"):
                files = [
                    path
                    for path in (home / public_directory).rglob("*")
                    if path.is_file()
                ]
                self.assertEqual(files, [])

            audit = run_cli(
                "audit-privacy",
                "--home",
                str(home),
                "--canary-file",
                str(source),
            )
            self.assertEqual(audit.returncode, 0, audit.stderr)
            self.assertEqual(json.loads(audit.stdout), {"leaks": [], "status": "passed"})

    def test_privacy_audit_fails_when_canary_reaches_exports(self) -> None:
        canary = b"PRIVATE-CANARY-41c85c0e\n"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "learning-home"
            source = root / "source.txt"
            source.write_bytes(canary)
            self.assertEqual(
                run_cli("init", "--home", str(home)).returncode,
                0,
            )
            leaked = home / "exports" / "leaked.txt"
            leaked.write_bytes(canary)

            audit = run_cli(
                "audit-privacy",
                "--home",
                str(home),
                "--canary-file",
                str(source),
            )

            self.assertEqual(audit.returncode, 2)
            self.assertEqual(
                json.loads(audit.stdout),
                {"leaks": ["exports/leaked.txt"], "status": "failed"},
            )

    def test_learning_home_refuses_path_inside_git_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory) / "public-repository"
            (repository / ".git").mkdir(parents=True)
            home = repository / "runtime-home"

            result = run_cli("init", "--home", str(home))

            self.assertEqual(result.returncode, 1)
            self.assertIn("cannot be inside a Git worktree", result.stderr)
            self.assertFalse(home.exists())

    def test_capture_rejects_private_inbox_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "learning-home"
            external = root / "external"
            external.mkdir()
            self.assertEqual(run_cli("init", "--home", str(home)).returncode, 0)
            inbox = home / "private" / "inbox"
            inbox.rmdir()
            inbox.symlink_to(external, target_is_directory=True)
            source = root / "source.txt"
            source.write_text("PRIVATE-CANARY-SYMLINK\n", encoding="utf-8")

            result = run_cli(
                "capture",
                "--home",
                str(home),
                "--source-type",
                "pasted-text",
                "--source-id",
                "symlink-test",
                "--input",
                str(source),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("private inbox is missing or unsafe", result.stderr)
            self.assertEqual(list(external.iterdir()), [])

    def test_open_rejects_private_permission_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "learning-home"
            self.assertEqual(run_cli("init", "--home", str(home)).returncode, 0)
            (home / "private").chmod(0o755)

            result = run_cli("audit-privacy", "--home", str(home), "--canary-file", __file__)

            self.assertEqual(result.returncode, 1)
            self.assertIn("permissions must be 0700", result.stderr)

    def test_read_manifest_rejects_path_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = LearningHome.initialize(root / "learning-home")
            outside = root / "outside"
            outside.mkdir()
            (outside / "manifest.json").write_text(
                '{"schema_version": 1}\n',
                encoding="utf-8",
            )

            with self.assertRaisesRegex(
                StorageError, "intake_id does not match the schema"
            ):
                home.read_manifest("../../outside")

    def test_failed_staged_capture_leaves_no_partial_intake(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home_path = Path(directory) / "learning-home"
            home = LearningHome.initialize(home_path)
            source = SourceReference(
                source_type="pasted-text",
                source_id="interrupted-capture",
                observed_at="2026-07-24T12:00:00Z",
            )
            original_write = __import__(
                "ops_learning_lab.storage",
                fromlist=["_write_atomic"],
            )._write_atomic

            def fail_manifest(path: Path, data: bytes, mode: int) -> None:
                if path.name == "manifest.json":
                    raise OSError("synthetic manifest failure")
                original_write(path, data, mode)

            with mock.patch(
                "ops_learning_lab.storage._write_atomic",
                side_effect=fail_manifest,
            ):
                with self.assertRaisesRegex(OSError, "synthetic manifest failure"):
                    home.capture(b"PRIVATE-CANARY-INTERRUPTED\n", source)

            inbox = home_path / "private" / "inbox"
            self.assertEqual(list(inbox.iterdir()), [])
