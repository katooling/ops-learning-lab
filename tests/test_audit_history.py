from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = REPOSITORY_ROOT / "scripts" / "audit_history.py"
SPEC = importlib.util.spec_from_file_location("audit_history", AUDIT_PATH)
assert SPEC is not None and SPEC.loader is not None
audit_history = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_history)
APPROVED_EMAIL = "release-author@users.noreply.github.com"
APPROVED = frozenset({APPROVED_EMAIL})


def git(
    root: Path,
    *arguments: str,
    environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        env=environment,
    )


def initialize(root: Path) -> None:
    git(root, "init")
    git(root, "config", "user.name", "Release Author")
    git(root, "config", "user.email", APPROVED_EMAIL)


def commit(root: Path, message: str = "safe synthetic commit") -> None:
    git(root, "add", "-A")
    git(root, "commit", "-m", message)


class HistoryAuditTests(unittest.TestCase):
    def test_safe_complete_history_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize(root)
            (root / "README.md").write_text("Synthetic public content\n")
            commit(root)
            (root / "README.md").write_text("Updated synthetic public content\n")
            commit(root, "safe update")

            self.assertEqual(
                audit_history.audit_repository(root, approved_emails=APPROVED),
                [],
            )

    def test_unapproved_author_email_is_fingerprinted_not_echoed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize(root)
            (root / "README.md").write_text("Synthetic public content\n")
            environment = os.environ.copy()
            environment.update(
                {
                    "GIT_AUTHOR_NAME": "Unsafe Author",
                    "GIT_AUTHOR_EMAIL": "private-address@example.test",
                }
            )
            git(root, "add", "README.md")
            git(root, "commit", "-m", "synthetic", environment=environment)

            violations = audit_history.audit_repository(
                root,
                approved_emails=APPROVED,
            )

            self.assertTrue(any("unapproved email" in item for item in violations))
            self.assertFalse(
                any("private-address@example.test" in item for item in violations)
            )

    def test_deleted_historical_secret_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize(root)
            secret = "api" + "_key = '" + "abcdefghijklmnopqrstuvwx" + "'\n"
            (root / "temporary.txt").write_text(secret)
            commit(root)
            (root / "temporary.txt").unlink()
            (root / "README.md").write_text("Safe replacement\n")
            commit(root, "remove temporary content")

            violations = audit_history.audit_repository(
                root,
                approved_emails=APPROVED,
            )

            self.assertTrue(
                any("secret-shaped value" in item for item in violations)
            )

    def test_deleted_historical_home_path_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize(root)
            home_path = "/" + "Users/synthetic/private/source.txt\n"
            (root / "temporary.txt").write_text(home_path)
            commit(root)
            (root / "temporary.txt").unlink()
            (root / "README.md").write_text("Safe replacement\n")
            commit(root, "remove temporary path")

            violations = audit_history.audit_repository(
                root,
                approved_emails=APPROVED,
            )

            self.assertTrue(
                any("absolute home path" in item for item in violations)
            )

    def test_historical_symbolic_link_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize(root)
            (root / "target.txt").write_text("safe\n")
            (root / "link.txt").symlink_to("target.txt")
            commit(root)
            (root / "link.txt").unlink()
            commit(root, "remove link")

            violations = audit_history.audit_repository(
                root,
                approved_emails=APPROVED,
            )

            self.assertTrue(any("unsafe Git mode 120000" in item for item in violations))

    def test_annotated_tag_requires_approved_tagger(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize(root)
            (root / "README.md").write_text("Synthetic public content\n")
            commit(root)
            environment = os.environ.copy()
            environment.update(
                {
                    "GIT_COMMITTER_NAME": "Unsafe Tagger",
                    "GIT_COMMITTER_EMAIL": "private-tagger@example.test",
                }
            )
            git(
                root,
                "tag",
                "-a",
                "v0.1.0",
                "-m",
                "Synthetic release",
                environment=environment,
            )

            violations = audit_history.audit_repository(
                root,
                approved_emails=APPROVED,
            )

            self.assertTrue(
                any("tag v0.1.0 tagger: unapproved email" in item for item in violations)
            )

    def test_lightweight_tag_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize(root)
            (root / "README.md").write_text("Synthetic public content\n")
            commit(root)
            git(root, "tag", "v0.1.0")

            violations = audit_history.audit_repository(
                root,
                approved_emails=APPROVED,
            )

            self.assertIn(
                "tag v0.1.0: lightweight tags have no auditable tagger identity",
                violations,
            )

    def test_annotated_tag_must_target_a_commit(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            initialize(root)
            (root / "README.md").write_text("Synthetic public content\n")
            commit(root)
            blob_id = git(root, "hash-object", "README.md").stdout.strip().decode()
            git(
                root,
                "tag",
                "-a",
                "unsafe-blob-tag",
                "-m",
                "Synthetic release",
                blob_id,
            )

            violations = audit_history.audit_repository(
                root,
                approved_emails=APPROVED,
            )

            self.assertIn(
                "tag unsafe-blob-tag: annotated tag must target a commit",
                violations,
            )

    def test_shallow_checkout_fails_as_incomplete(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source"
            clone = Path(directory) / "clone"
            source.mkdir()
            initialize(source)
            (source / "README.md").write_text("one\n")
            commit(source)
            (source / "README.md").write_text("two\n")
            commit(source, "second")
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    source.as_uri(),
                    str(clone),
                ],
                check=True,
                capture_output=True,
            )

            with self.assertRaisesRegex(
                audit_history.HistoryAuditError,
                "complete history",
            ):
                audit_history.audit_repository(clone, approved_emails=APPROVED)
