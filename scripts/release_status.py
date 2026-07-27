#!/usr/bin/env python3
"""Emit a deterministic, read-only summary of the local release state."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


VERSION_TAG = re.compile(r"v[0-9]+(?:\.[0-9]+){2}(?:[-+][0-9A-Za-z.-]+)?")


class ReleaseStatusError(RuntimeError):
    """The repository cannot prove a coherent local release state."""


def git(repo: Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ("git", "--no-optional-locks", "-C", str(repo), *arguments),
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise ReleaseStatusError(
            f"git {' '.join(arguments)} failed without changing the repository"
        )
    return result.stdout.strip()


def version_tags_at_head(repo: Path) -> tuple[str, ...]:
    tags = tuple(
        sorted(
            tag
            for tag in git(repo, "tag", "--points-at", "HEAD").splitlines()
            if VERSION_TAG.fullmatch(tag)
        )
    )
    if len(tags) > 1:
        raise ReleaseStatusError("HEAD has more than one version tag")
    return tags


def inspect_release(repo: Path, expected_tag: str | None = None) -> dict[str, object]:
    if git(repo, "rev-parse", "--is-shallow-repository") != "false":
        raise ReleaseStatusError("release status requires complete Git history")
    if git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ReleaseStatusError("release status requires a clean worktree")

    commit_sha = git(repo, "rev-parse", "HEAD")
    tree_sha = git(repo, "rev-parse", "HEAD^{tree}")
    branch = git(repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False) or None
    tags = version_tags_at_head(repo)
    release_tag = tags[0] if tags else None

    if expected_tag is not None:
        if not VERSION_TAG.fullmatch(expected_tag):
            raise ReleaseStatusError("expected tag is not a version tag")
        if release_tag != expected_tag:
            raise ReleaseStatusError("expected version tag does not point to HEAD")

    tag_object_sha = None
    if release_tag is not None:
        if git(repo, "cat-file", "-t", f"refs/tags/{release_tag}") != "tag":
            raise ReleaseStatusError("version tags must be annotated")
        if git(repo, "rev-parse", f"{release_tag}^{{commit}}") != commit_sha:
            raise ReleaseStatusError("version tag does not peel to HEAD")
        tag_object_sha = git(repo, "rev-parse", f"refs/tags/{release_tag}")

    return {
        "branch": branch,
        "clean": True,
        "commit_sha": commit_sha,
        "release_tag": release_tag,
        "schema_version": 1,
        "shallow": False,
        "tag_object_sha": tag_object_sha,
        "tree_sha": tree_sha,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="emit deterministic JSON for a clean local release state"
    )
    parser.add_argument(
        "--expect-tag",
        help="require one annotated version tag with this name to point to HEAD",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    try:
        status = inspect_release(arguments.repo.resolve(), arguments.expect_tag)
    except ReleaseStatusError as error:
        print(f"release status failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(status, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
