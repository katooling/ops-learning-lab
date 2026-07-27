from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.release_status import ReleaseStatusError, inspect_release


def git(repo: Path, *arguments: str) -> str:
    return subprocess.run(
        ("git", "-C", str(repo), *arguments),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def initialize_repository(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.name", "Release Test")
    git(repo, "config", "user.email", "release-test@users.noreply.github.com")
    (repo / "safe.txt").write_text("safe\n", encoding="utf-8")
    git(repo, "add", "safe.txt")
    git(repo, "commit", "-q", "-m", "initial")
    return repo


class ReleaseStatusTests(unittest.TestCase):
    def test_clean_state_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = initialize_repository(Path(temporary))

            first = inspect_release(repo)
            second = inspect_release(repo)

            self.assertEqual(first, second)
            self.assertTrue(first["clean"])
            self.assertFalse(first["shallow"])
            self.assertIsNone(first["release_tag"])
            json.dumps(first, sort_keys=True)

    def test_inspection_does_not_refresh_the_git_index(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = initialize_repository(Path(temporary))
            index = repo / ".git" / "index"
            before_bytes = index.read_bytes()
            before_mtime = index.stat().st_mtime_ns
            tracked = repo / "safe.txt"
            tracked_stat = tracked.stat()
            os.utime(
                tracked,
                ns=(
                    tracked_stat.st_atime_ns,
                    tracked_stat.st_mtime_ns + 1_000_000_000,
                ),
            )

            inspect_release(repo)

            self.assertEqual(index.read_bytes(), before_bytes)
            self.assertEqual(index.stat().st_mtime_ns, before_mtime)

    def test_dirty_and_shallow_repositories_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = initialize_repository(root)
            (repo / "untracked.txt").write_text("not evidence\n", encoding="utf-8")
            with self.assertRaisesRegex(ReleaseStatusError, "clean worktree"):
                inspect_release(repo)

            (repo / "untracked.txt").unlink()
            (repo / "safe.txt").write_text("second\n", encoding="utf-8")
            git(repo, "commit", "-qam", "second")
            shallow = root / "shallow"
            subprocess.run(
                ("git", "clone", "-q", "--depth", "1", repo.as_uri(), str(shallow)),
                check=True,
            )
            with self.assertRaisesRegex(ReleaseStatusError, "complete Git history"):
                inspect_release(shallow)

    def test_expected_tag_must_be_annotated_and_point_to_head(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = initialize_repository(Path(temporary))
            git(repo, "tag", "v0.1.0")
            with self.assertRaisesRegex(ReleaseStatusError, "annotated"):
                inspect_release(repo, "v0.1.0")

            git(repo, "tag", "-d", "v0.1.0")
            git(repo, "tag", "-a", "v0.1.0", "-m", "version 0.1.0")
            tagged = inspect_release(repo, "v0.1.0")
            self.assertEqual(tagged["release_tag"], "v0.1.0")
            self.assertIsNotNone(tagged["tag_object_sha"])

            (repo / "safe.txt").write_text("later\n", encoding="utf-8")
            git(repo, "commit", "-qam", "later")
            with self.assertRaisesRegex(ReleaseStatusError, "does not point to HEAD"):
                inspect_release(repo, "v0.1.0")


if __name__ == "__main__":
    unittest.main()
