from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CHECKER_PATH = REPOSITORY_ROOT / "scripts" / "check_markdown_links.py"
SPEC = importlib.util.spec_from_file_location("check_markdown_links", CHECKER_PATH)
assert SPEC is not None and SPEC.loader is not None
check_markdown_links = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_markdown_links)


def git(root: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
    )


class MarkdownLinkTests(unittest.TestCase):
    def test_relative_file_and_directory_links_pass(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            (root / "docs").mkdir()
            (root / "docs" / "guide.md").write_text("# Guide\n")
            (root / "README.md").write_text(
                "[Guide](docs/guide.md) and [Docs](docs/) and [section](#local)\n"
            )

            self.assertEqual(check_markdown_links.find_broken_links(root), [])

    def test_missing_relative_link_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            (root / "README.md").write_text("[Missing](docs/missing.md)\n")

            broken = check_markdown_links.find_broken_links(root)

            self.assertEqual(
                broken,
                ["README.md: missing link target docs/missing.md"],
            )

    def test_repository_escape_fails(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            (root / "README.md").write_text("[Private](../private/note.md)\n")

            broken = check_markdown_links.find_broken_links(root)

            self.assertEqual(
                broken,
                ["README.md: link escapes repository: ../private/note.md"],
            )

    def test_remote_links_are_not_fetched(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            git(root, "init")
            (root / "README.md").write_text(
                "[Web](https://example.test/path) and [mail](mailto:a@example.test)\n"
            )

            self.assertEqual(check_markdown_links.find_broken_links(root), [])
