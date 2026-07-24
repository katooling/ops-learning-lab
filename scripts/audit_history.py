#!/usr/bin/env python3
"""Fail closed when release-reachable Git history is unsafe to publish."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import hashlib
import re
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
APPROVED_EMAILS = frozenset(
    {
        "72454341+Mohamad-Kamar@users.noreply.github.com",
        "noreply@github.com",
    }
)
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
IDENTITY_EMAIL_PATTERN = re.compile(rb" <([^<>\n]+)> \d+ [+-]\d{4}$")
MAX_PUBLIC_BLOB_BYTES = 2_000_000
REGULAR_BLOB_MODES = {b"100644", b"100755"}


class HistoryAuditError(RuntimeError):
    """The repository cannot be audited completely."""


def _git(root: Path, *arguments: str, input_bytes: bytes | None = None) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            input=input_bytes,
            check=True,
            capture_output=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HistoryAuditError(
            f"history audit could not run git {' '.join(arguments)}"
        ) from exc
    return result.stdout


def _object(root: Path, object_id: str) -> bytes:
    return _git(root, "cat-file", "-p", object_id)


def _email_from_identity(line: bytes, label: str) -> str:
    match = IDENTITY_EMAIL_PATTERN.search(line)
    if match is None:
        raise HistoryAuditError(f"{label} has malformed identity metadata")
    try:
        return match.group(1).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HistoryAuditError(f"{label} has non-UTF-8 identity metadata") from exc


def _safe_email_fingerprint(email: str) -> str:
    return hashlib.sha256(email.encode("utf-8")).hexdigest()[:12]


def _scan_bytes(label: str, data: bytes) -> list[str]:
    violations: list[str] = []
    if ABSOLUTE_HOME_PATTERN.search(data):
        violations.append(f"{label}: contains an absolute home path")
    if any(pattern.search(data) for pattern in SECRET_PATTERNS):
        violations.append(f"{label}: contains a secret-shaped value")
    return violations


def _release_commits(root: Path) -> list[str]:
    revisions = _git(root, "rev-list", "--topo-order", "HEAD", "--tags")
    commits = [line.decode("ascii") for line in revisions.splitlines() if line]
    if not commits:
        raise HistoryAuditError("history audit requires at least one reachable commit")
    return commits


def _audit_commit(
    root: Path,
    commit_id: str,
    approved_emails: frozenset[str],
) -> list[str]:
    encoded = _object(root, commit_id)
    headers, separator, message = encoded.partition(b"\n\n")
    if not separator:
        raise HistoryAuditError(f"commit {commit_id} has malformed metadata")
    violations: list[str] = []
    identities: dict[bytes, bytes] = {}
    for line in headers.splitlines():
        key, _, value = line.partition(b" ")
        if key in {b"author", b"committer"}:
            identities[key] = value
    for key in (b"author", b"committer"):
        if key not in identities:
            raise HistoryAuditError(
                f"commit {commit_id} has no {key.decode('ascii')} identity"
            )
        label = f"commit {commit_id} {key.decode('ascii')}"
        email = _email_from_identity(identities[key], label)
        if email not in approved_emails:
            violations.append(
                f"{label}: unapproved email "
                f"(sha256:{_safe_email_fingerprint(email)})"
            )
    violations.extend(_scan_bytes(f"commit {commit_id} message", message))
    return violations


def _tag_object_ids(root: Path) -> list[tuple[str, str, str]]:
    encoded = _git(
        root,
        "for-each-ref",
        "--format=%(refname:short)%00%(objecttype)%00%(objectname)%00",
        "refs/tags",
    )
    fields = encoded.split(b"\0")
    if fields and fields[-1] == b"\n":
        fields.pop()
    while fields and not fields[-1]:
        fields.pop()
    if len(fields) % 3:
        raise HistoryAuditError("tag reference output was malformed")
    result: list[tuple[str, str, str]] = []
    for offset in range(0, len(fields), 3):
        try:
            result.append(
                tuple(
                    value.strip().decode("utf-8")
                    for value in fields[offset : offset + 3]
                )
            )
        except UnicodeDecodeError as exc:
            raise HistoryAuditError("tag metadata is not UTF-8") from exc
    return result


def _audit_tags(
    root: Path,
    approved_emails: frozenset[str],
) -> list[str]:
    violations: list[str] = []
    for name, object_type, object_id in _tag_object_ids(root):
        if object_type != "tag":
            violations.append(
                f"tag {name}: lightweight tags have no auditable tagger identity"
            )
            continue
        encoded = _object(root, object_id)
        headers, separator, message = encoded.partition(b"\n\n")
        if not separator:
            raise HistoryAuditError(f"tag {name} has malformed metadata")
        target_type = next(
            (
                line.removeprefix(b"type ")
                for line in headers.splitlines()
                if line.startswith(b"type ")
            ),
            None,
        )
        if target_type != b"commit":
            violations.append(f"tag {name}: annotated tag must target a commit")
        tagger = next(
            (
                line.removeprefix(b"tagger ")
                for line in headers.splitlines()
                if line.startswith(b"tagger ")
            ),
            None,
        )
        if tagger is None:
            violations.append(f"tag {name}: annotated tag has no tagger identity")
        else:
            email = _email_from_identity(tagger, f"tag {name} tagger")
            if email not in approved_emails:
                violations.append(
                    f"tag {name} tagger: unapproved email "
                    f"(sha256:{_safe_email_fingerprint(email)})"
                )
        violations.extend(_scan_bytes(f"tag {name} message", message))
    return violations


def _tree_entries(root: Path, commit_id: str) -> list[tuple[bytes, str, bytes]]:
    encoded = _git(root, "ls-tree", "-r", "-z", "--full-tree", commit_id)
    entries: list[tuple[bytes, str, bytes]] = []
    for record in encoded.split(b"\0"):
        if not record:
            continue
        metadata, separator, raw_path = record.partition(b"\t")
        if not separator:
            raise HistoryAuditError(f"commit {commit_id} has malformed tree output")
        fields = metadata.split()
        if len(fields) != 3:
            raise HistoryAuditError(f"commit {commit_id} has malformed tree metadata")
        mode, object_type, raw_object_id = fields
        if object_type != b"blob":
            raise HistoryAuditError(
                f"commit {commit_id} contains unsupported {object_type!r} tree entry"
            )
        try:
            object_id = raw_object_id.decode("ascii")
        except UnicodeDecodeError as exc:
            raise HistoryAuditError("Git object ID is not ASCII") from exc
        entries.append((mode, object_id, raw_path))
    return entries


def _display_path(raw_path: bytes) -> str:
    try:
        return raw_path.decode("utf-8")
    except UnicodeDecodeError:
        return "<non-UTF-8-path>"


def _unsafe_path_reason(raw_path: bytes) -> str | None:
    try:
        decoded = raw_path.decode("utf-8")
    except UnicodeDecodeError:
        return "path is not UTF-8"
    if any(ord(character) < 32 or ord(character) == 127 for character in decoded):
        return "path contains control characters"
    path = PurePosixPath(decoded)
    if path.is_absolute() or ".." in path.parts or ".git" in path.parts:
        return "path is not a safe repository-relative path"
    is_environment_file = path.name == ".env" or (
        path.name.startswith(".env.") and path.name != ".env.example"
    )
    if (
        is_environment_file
        or path.name in FORBIDDEN_NAMES
        or path.suffix.lower() in FORBIDDEN_SUFFIXES
    ):
        return "forbidden file type"
    return None


def _audit_blobs(root: Path, commits: list[str]) -> list[str]:
    violations: list[str] = []
    scanned_content: set[str] = set()
    scanned_entries: set[tuple[bytes, str, bytes]] = set()
    for commit_id in commits:
        for mode, object_id, raw_path in _tree_entries(root, commit_id):
            entry = (mode, object_id, raw_path)
            if entry in scanned_entries:
                continue
            scanned_entries.add(entry)
            path = _display_path(raw_path)
            label = f"reachable blob {object_id} at {path}"
            if mode not in REGULAR_BLOB_MODES:
                violations.append(f"{label}: unsafe Git mode {mode.decode('ascii')}")
            reason = _unsafe_path_reason(raw_path)
            if reason is not None:
                violations.append(f"{label}: {reason}")
            if object_id in scanned_content:
                continue
            scanned_content.add(object_id)
            size_bytes = _git(root, "cat-file", "-s", object_id).strip()
            try:
                size = int(size_bytes)
            except ValueError as exc:
                raise HistoryAuditError(
                    f"blob {object_id} reported a malformed size"
                ) from exc
            if size > MAX_PUBLIC_BLOB_BYTES:
                violations.append(
                    f"{label}: exceeds {MAX_PUBLIC_BLOB_BYTES}-byte release limit"
                )
                continue
            violations.extend(_scan_bytes(label, _object(root, object_id)))
    return violations


def audit_repository(
    root: Path = ROOT,
    *,
    approved_emails: frozenset[str] = APPROVED_EMAILS,
) -> list[str]:
    if _git(root, "rev-parse", "--is-inside-work-tree").strip() != b"true":
        raise HistoryAuditError("history audit requires a Git worktree")
    if _git(root, "rev-parse", "--is-shallow-repository").strip() != b"false":
        raise HistoryAuditError(
            "history audit requires complete history; use a non-shallow checkout"
        )
    commits = _release_commits(root)
    violations: list[str] = []
    for commit_id in commits:
        violations.extend(_audit_commit(root, commit_id, approved_emails))
    violations.extend(_audit_tags(root, approved_emails))
    violations.extend(_audit_blobs(root, commits))
    return sorted(set(violations))


def main() -> int:
    try:
        violations = audit_repository(ROOT)
    except HistoryAuditError as exc:
        print(f"history audit incomplete: {exc}", file=sys.stderr)
        return 1
    if violations:
        for violation in violations:
            print(violation, file=sys.stderr)
        return 1
    commits = _release_commits(ROOT)
    print(f"history audit passed: {len(commits)} release-reachable commits")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
