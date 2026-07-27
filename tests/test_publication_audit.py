from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = REPOSITORY_ROOT / "scripts" / "audit_publication.py"
SPEC = importlib.util.spec_from_file_location("audit_publication", AUDIT_PATH)
assert SPEC is not None and SPEC.loader is not None
audit_publication = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(audit_publication)


def git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
    )


class PublicationAuditTests(unittest.TestCase):
    def test_safe_candidate_passes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            (root / "README.md").write_text("Synthetic public content\n")

            self.assertEqual(audit_publication.audit_repository(root), [])

    def test_force_added_environment_file_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            (root / ".gitignore").write_text(".env.*\n")
            (root / ".env.local").write_text("SYNTHETIC_ONLY=true\n")
            git(root, "add", ".gitignore")
            git(root, "add", "-f", ".env.local")

            violations = audit_publication.audit_repository(root)

            self.assertTrue(any("forbidden file type" in item for item in violations))

    def test_secret_shaped_value_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            (root / "bad.txt").write_text(
                "api" + "_key = '" + "abcdefghijkl" + "mnopqrstuvwx'\n"
            )

            violations = audit_publication.audit_repository(root)

            self.assertTrue(
                any("secret-shaped value" in item for item in violations)
            )

    def test_absolute_home_path_fails_even_when_username_contains_s(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            private_path = "/" + "Users/synthetic/private/source.txt\n"
            (root / "bad.txt").write_text(private_path)

            violations = audit_publication.audit_repository(root)

            self.assertTrue(
                any("absolute home path" in item for item in violations)
            )

    def test_symlink_candidate_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            target = root / "target.txt"
            target.write_text("safe\n")
            (root / "link.txt").symlink_to(target)

            violations = audit_publication.audit_repository(root)

            self.assertTrue(any("symbolic links" in item for item in violations))

    def test_missing_git_metadata_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(RuntimeError, "requires a Git repository"):
                audit_publication.audit_repository(Path(directory))
