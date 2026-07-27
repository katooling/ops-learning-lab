#!/usr/bin/env python3
"""Fail closed on artifacts that should not enter the public repository."""

from __future__ import annotations

from pathlib import Path
import hashlib
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_SUFFIXES = {".jsonl", ".pem", ".key", ".p12", ".pfx"}
FORBIDDEN_NAMES = {"credentials.json", "secrets.json"}
SECRET_PATTERNS = (
    re.compile(rb"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(rb"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(rb"xox[baprs]-[A-Za-z0-9-]{20,}"),
    re.compile(rb"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(
        rb"(?i)(?:api[_-]?key|password|secret)"
        rb"\s*[:=]\s*['\"]?[A-Za-z0-9_./+=-]{12,}"
    ),
)
ABSOLUTE_HOME_PATTERN = re.compile(rb"/(?:Users|home)/[^/\s]+/")
MAX_PUBLIC_FILE_BYTES = 2_000_000


def _safe_path_fingerprint(raw_path: bytes) -> str:
    return hashlib.sha256(raw_path).hexdigest()[:12]


def candidates(root: Path = ROOT) -> list[Path]:
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise RuntimeError("publication audit requires a Git repository") from exc
    return sorted(
        root / relative.decode("utf-8")
        for relative in result.stdout.split(b"\0")
        if relative
    )


def audit_repository(root: Path = ROOT) -> list[str]:
    violations: list[str] = []
    for path in candidates(root):
        relative = path.relative_to(root)
        raw_relative = relative.as_posix().encode("utf-8")
        if any(pattern.search(raw_relative) for pattern in SECRET_PATTERNS):
            label = f"tracked path sha256:{_safe_path_fingerprint(raw_relative)}"
            violations.append(f"{label}: contains a secret-shaped value")
        else:
            label = str(relative)
        if path.is_symlink():
            violations.append(f"{label}: symbolic links are not publishable")
            continue
        is_environment_file = path.name == ".env" or (
            path.name.startswith(".env.") and path.name != ".env.example"
        )
        if (
            is_environment_file
            or path.name in FORBIDDEN_NAMES
            or path.suffix.lower() in FORBIDDEN_SUFFIXES
        ):
            violations.append(f"{label}: forbidden file type")
            continue
        if path.stat().st_size > MAX_PUBLIC_FILE_BYTES:
            violations.append(f"{label}: file exceeds publication size limit")
            continue
        data = path.read_bytes()
        if ABSOLUTE_HOME_PATTERN.search(data):
            violations.append(f"{label}: contains an absolute home path")
        for pattern in SECRET_PATTERNS:
            if pattern.search(data):
                violations.append(f"{label}: contains a secret-shaped value")
                break
    return violations


def main() -> int:
    try:
        violations = audit_repository(ROOT)
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if violations:
        for violation in violations:
            print(violation, file=sys.stderr)
        return 1
    print(f"publication audit passed: {len(candidates(ROOT))} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
