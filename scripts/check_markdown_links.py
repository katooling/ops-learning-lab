#!/usr/bin/env python3
"""Check that relative links in publishable Markdown resolve locally."""

from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]+\]\(([^)\s]+)(?:\s+['\"][^'\"]*['\"])?\)")
REMOTE_SCHEMES = {"http", "https", "mailto"}


def markdown_candidates(root: Path = ROOT) -> list[Path]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "*.md",
            "-z",
        ],
        check=True,
        capture_output=True,
    )
    return sorted(
        root / relative.decode("utf-8")
        for relative in result.stdout.split(b"\0")
        if relative
    )


def find_broken_links(root: Path = ROOT) -> list[str]:
    broken: list[str] = []
    for document in markdown_candidates(root):
        text = document.read_text(encoding="utf-8")
        for raw_target in MARKDOWN_LINK.findall(text):
            target = raw_target.strip("<>")
            parsed = urlsplit(target)
            if parsed.scheme.lower() in REMOTE_SCHEMES or not parsed.path:
                continue
            if parsed.scheme or parsed.netloc:
                broken.append(
                    f"{document.relative_to(root)}: unsupported link target {target}"
                )
                continue

            destination = (document.parent / unquote(parsed.path)).resolve()
            try:
                destination.relative_to(root.resolve())
            except ValueError:
                broken.append(
                    f"{document.relative_to(root)}: link escapes repository: {target}"
                )
                continue
            if not destination.exists():
                broken.append(
                    f"{document.relative_to(root)}: missing link target {target}"
                )
    return broken


def main() -> int:
    try:
        broken = find_broken_links(ROOT)
    except (OSError, subprocess.CalledProcessError, UnicodeDecodeError) as exc:
        print(f"Markdown link check failed: {exc}", file=sys.stderr)
        return 1
    if broken:
        for item in broken:
            print(item, file=sys.stderr)
        return 1
    print(f"Markdown link check passed: {len(markdown_candidates(ROOT))} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
