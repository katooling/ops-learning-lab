from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from threading import Thread
import unittest
from unittest import mock

from ops_learning_lab.compiler import compile_update, propose_pack_match
from ops_learning_lab.domain import (
    PackProfile,
    SchemaError,
    SourceReference,
    StagedPackUpdate,
)
from ops_learning_lab.shell import make_server
from ops_learning_lab.staging import PackUpdateRepository
from ops_learning_lab.storage import LearningHome, StorageError

from test_phase_one_cli import run_cli


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


class PackMatchTests(unittest.TestCase):
    PROFILES = (
        PackProfile(
            pack_id="alpha-pack",
            title="Alpha",
            match_terms=("alpha", "shared", "trace"),
        ),
        PackProfile(
            pack_id="beta-pack",
            title="Beta",
            match_terms=("beta", "shared", "trace"),
        ),
    )

    def test_one_plausible_pack_is_proposed_with_exact_shared_terms(self) -> None:
        result = propose_pack_match("alpha trace only", self.PROFILES)

        self.assertEqual(result.kind, "strong")
        self.assertEqual(result.proposed_pack_id, "alpha-pack")
        self.assertEqual(result.candidates[0].matched_terms, ("alpha", "trace"))
        self.assertEqual(len(result.candidates[0].match_profile_sha256), 64)
        self.assertIsNone(result.candidates[0].expected_base_version)
        self.assertIsNone(result.candidates[0].expected_base_sha256)
        self.assertIn("alpha, trace", result.reasons[0])

    def test_several_plausible_packs_require_learner_choice(self) -> None:
        result = propose_pack_match("alpha beta shared trace", self.PROFILES)

        self.assertEqual(result.kind, "ambiguous")
        self.assertIsNone(result.proposed_pack_id)
        self.assertEqual(
            tuple(candidate.pack_id for candidate in result.candidates),
            ("alpha-pack", "beta-pack"),
        )

    def test_no_plausible_pack_proposes_a_new_pack(self) -> None:
        result = propose_pack_match("a completely novel subject", self.PROFILES)

        self.assertEqual(result.kind, "new_pack")
        self.assertEqual(result.candidates, ())
        self.assertIsNone(result.proposed_pack_id)


class CaptureModeJourneyTests(unittest.TestCase):
    def test_capture_stages_once_and_shell_never_serves_raw_intake(self) -> None:
        raw_canary = "PRIVATE-RAW-CANARY-9f4e8c"
        claim = "<script>alert('synthetic')</script> costs remain unverified"
        content = (
            f"{raw_canary}\n"
            "Codex ETL usage cost tokens.\n"
            f"Claim: {claim}\n"
            "Claim [historical]: The synthetic trial once reported zero cost.\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home_path = root / "learning-home"
            source_path = root / "source.txt"
            source_path.write_text(content, encoding="utf-8")
            self.assertEqual(run_cli("init", "--home", str(home_path)).returncode, 0)
            packs_before = tuple((home_path / "packs").rglob("*"))

            arguments = (
                "capture",
                "--home",
                str(home_path),
                "--source-type",
                "pasted-text",
                "--source-id",
                "synthetic-capture-1",
                "--observed-at",
                "2026-07-24T12:00:00Z",
                "--input",
                str(source_path),
            )
            first = run_cli(*arguments)
            second = run_cli(*arguments)

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stdout, second.stdout)
            result = json.loads(first.stdout)
            self.assertEqual(result["status"], "staged")
            self.assertEqual(result["match_kind"], "strong")
            self.assertEqual(result["proposed_pack_id"], "codex-etl")
            self.assertFalse(result["lesson_started"])
            self.assertNotIn(raw_canary, first.stdout)
            self.assertNotIn(str(home_path), first.stdout)
            self.assertEqual(
                len(list((home_path / "private" / "inbox").glob("intake-*"))),
                1,
            )
            self.assertEqual(
                len(list((home_path / "staged" / "updates").glob("update-*.json"))),
                1,
            )
            self.assertEqual(tuple((home_path / "packs").rglob("*")), packs_before)
            for path in home_path.rglob("*"):
                if path.is_file() and path.name != "raw.bin":
                    self.assertNotIn(raw_canary.encode(), path.read_bytes())
            staged_path = next(
                (home_path / "staged" / "updates").glob("update-*.json")
            )
            staged_bytes = staged_path.read_bytes()
            self.assertNotIn(b"synthetic-capture-1", staged_bytes)
            self.assertEqual(staged_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(
                (home_path / "staged").stat().st_mode & 0o777,
                0o700,
            )
            self.assertEqual(
                (home_path / "staged" / "updates").stat().st_mode & 0o777,
                0o700,
            )

            repository = PackUpdateRepository.open(home_path)
            server = make_server(repository, "127.0.0.1", 0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                port = server.server_address[1]
                connection = http.client.HTTPConnection("127.0.0.1", port)
                connection.request("GET", result["review_path"])
                response = connection.getresponse()
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertEqual(response.getheader("Cache-Control"), "no-store")
                self.assertIn(
                    "default-src 'none'",
                    response.getheader("Content-Security-Policy", ""),
                )
                self.assertIn("Staged Pack Update", body)
                self.assertIn("Proposed destination: Synthetic Codex ETL", body)
                self.assertIn("Nothing has changed in any Learning Pack", body)
                self.assertIn("&lt;script&gt;", body)
                self.assertNotIn("<script>alert", body)
                self.assertNotIn(raw_canary, body)
                self.assertNotIn(str(home_path), body)
                self.assertNotIn("raw.bin", body)
                self.assertNotIn("/private/", body)

                connection.request("HEAD", result["review_path"])
                head_response = connection.getresponse()
                self.assertEqual(head_response.status, 200)
                self.assertEqual(head_response.read(), b"")
                self.assertEqual(
                    int(head_response.getheader("Content-Length", "0")),
                    len(body.encode("utf-8")),
                )

                connection.request("HEAD", "/private/inbox")
                private_head = connection.getresponse()
                self.assertEqual(private_head.status, 404)
                private_head.read()

                connection.request("GET", "/private/inbox")
                private_response = connection.getresponse()
                self.assertEqual(private_response.status, 404)
                private_response.read()

                connection.request("POST", "/updates")
                post_response = connection.getresponse()
                self.assertEqual(post_response.status, 405)
                self.assertEqual(post_response.getheader("Allow"), "GET, HEAD")
                post_response.read()
                connection.close()

                hostile = http.client.HTTPConnection("127.0.0.1", port)
                hostile.putrequest("GET", "/", skip_host=True)
                hostile.putheader("Host", "example.test")
                hostile.endheaders()
                hostile_response = hostile.getresponse()
                self.assertEqual(hostile_response.status, 400)
                hostile_response.read()
                hostile.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

            # A new server instance sees the same durable staged proposal.
            restarted = make_server(
                PackUpdateRepository.open(home_path),
                "127.0.0.1",
                0,
            )
            self.assertEqual(len(list(repository.list())), 1)
            restarted.server_close()

    def test_serve_command_announces_an_ephemeral_loopback_url(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory) / "learning-home"
            self.assertEqual(run_cli("init", "--home", str(home)).returncode, 0)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(SOURCE_ROOT)
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "ops_learning_lab",
                    "serve",
                    "--home",
                    str(home),
                    "--port",
                    "0",
                ],
                cwd=REPOSITORY_ROOT,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            try:
                assert process.stdout is not None
                announcement = json.loads(process.stdout.readline())
                self.assertEqual(announcement["status"], "serving")
                self.assertRegex(
                    announcement["url"],
                    r"^http://127\.0\.0\.1:\d+/$",
                )
            finally:
                process.terminate()
                process.wait(timeout=3)
                if process.stdout is not None:
                    process.stdout.close()
                if process.stderr is not None:
                    process.stderr.close()

    def test_concurrent_identical_capture_produces_one_intake_and_update(self) -> None:
        content = (
            b"Codex ETL usage cost.\n"
            b"Claim: Synthetic normalized cost is non-negative.\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            source = SourceReference(
                source_type="pasted-text",
                source_id="concurrent-capture",
                observed_at="2026-07-24T12:00:00Z",
            )
            repository = PackUpdateRepository.open(home.root)

            def capture() -> tuple[str, str]:
                manifest = home.capture(content, source)
                update = repository.stage(compile_update(content, manifest))
                return manifest.intake_id, update.update_id

            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(lambda _: capture(), range(16)))

            self.assertEqual(len(set(results)), 1)
            self.assertEqual(
                len(list((home.root / "private" / "inbox").glob("intake-*"))),
                1,
            )
            self.assertEqual(len(list(repository.root.glob("update-*.json"))), 1)

    def test_staged_update_symlink_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = LearningHome.initialize(root / "learning-home")
            repository = PackUpdateRepository.open(home.root)
            source = SourceReference(
                source_type="pasted-text",
                source_id="stage-symlink",
                observed_at="2026-07-24T12:00:00Z",
            )
            content = b"Codex ETL usage.\nClaim: A synthetic claim.\n"
            manifest = home.capture(content, source)
            update = compile_update(content, manifest)
            external = root / "external.json"
            external.write_text("{}", encoding="utf-8")
            (repository.root / f"{update.update_id}.json").symlink_to(external)

            with self.assertRaisesRegex(StorageError, "symbolic link"):
                repository.stage(update)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO files are not supported")
    def test_staged_update_fifo_fails_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            repository = PackUpdateRepository.open(home.root)
            path = repository.root / f"update-{'a' * 20}.json"
            os.mkfifo(path)

            with self.assertRaisesRegex(StorageError, "regular file"):
                list(repository.list())

    def test_corrupt_staged_update_is_not_rendered(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            repository = PackUpdateRepository.open(home.root)
            corrupt = repository.root / f"update-{'a' * 20}.json"
            corrupt.write_text('{"schema_version":1}', encoding="utf-8")

            with self.assertRaisesRegex(SchemaError, "does not match the schema"):
                list(repository.list())

    def test_tampered_staged_update_fails_digest_validation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            repository = PackUpdateRepository.open(home.root)
            source = SourceReference(
                source_type="pasted-text",
                source_id="tamper-test",
                observed_at="2026-07-24T12:00:00Z",
            )
            content = b"Codex ETL usage.\nClaim: Original synthetic claim.\n"
            manifest = home.capture(content, source)
            update = repository.stage(compile_update(content, manifest))
            path = repository.root / f"{update.update_id}.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["proposed_claims"][0]["text"] = "Tampered claim"
            path.write_text(json.dumps(value), encoding="utf-8")

            with self.assertRaisesRegex(SchemaError, "does not match the schema"):
                repository.get(update.update_id)

    def test_staged_update_rejects_duplicate_json_keys(self) -> None:
        repository, update, temporary = self._one_staged_update()
        with temporary:
            path = repository.root / f"{update.update_id}.json"
            encoded = path.read_text(encoding="utf-8")
            encoded = encoded.replace(
                '"schema_version": 1',
                '"schema_version": 1, "schema_version": 1',
                1,
            )
            path.write_text(encoded, encoding="utf-8")

            with self.assertRaisesRegex(SchemaError, "does not match the schema"):
                repository.get(update.update_id)

            server = make_server(repository, "127.0.0.1", 0)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection(
                    "127.0.0.1",
                    server.server_address[1],
                )
                connection.request("GET", f"/updates/{update.update_id}")
                response = connection.getresponse()
                body = response.read().decode("utf-8")
                self.assertEqual(response.status, 500)
                self.assertIn("Staged update is unavailable", body)
                self.assertNotIn("duplicate", body.lower())
                self.assertNotIn(str(repository.root), body)
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_staged_update_rejects_nested_non_finite_number(self) -> None:
        repository, update, temporary = self._one_staged_update()
        with temporary:
            path = repository.root / f"{update.update_id}.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["match"]["candidates"][0]["matched_terms"][0] = float("nan")
            path.write_text(json.dumps(value), encoding="utf-8")

            with self.assertRaisesRegex(SchemaError, "does not match the schema"):
                repository.get(update.update_id)

    def test_staged_update_converts_nested_type_error_to_schema_error(self) -> None:
        repository, update, temporary = self._one_staged_update()
        with temporary:
            path = repository.root / f"{update.update_id}.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["proposal_sha256"] = None
            path.write_text(json.dumps(value), encoding="utf-8")

            with self.assertRaisesRegex(SchemaError, "does not match the schema"):
                repository.get(update.update_id)

    def test_failed_staged_replace_leaves_no_destination_or_temporary_file(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as directory:
            home = LearningHome.initialize(Path(directory) / "learning-home")
            repository = PackUpdateRepository.open(home.root)
            source = SourceReference(
                source_type="pasted-text",
                source_id="interrupted-stage",
                observed_at="2026-07-24T12:00:00Z",
            )
            content = b"Codex ETL usage.\nClaim: Atomic staged write.\n"
            manifest = home.capture(content, source)
            update = compile_update(content, manifest)

            with mock.patch(
                "pathlib.Path.replace",
                side_effect=OSError("synthetic staged replace failure"),
            ):
                with self.assertRaisesRegex(
                    OSError,
                    "synthetic staged replace failure",
                ):
                    repository.stage(update)

            self.assertEqual(list(repository.root.iterdir()), [])

    @staticmethod
    def _one_staged_update() -> tuple[
        PackUpdateRepository,
        StagedPackUpdate,
        tempfile.TemporaryDirectory[str],
    ]:
        temporary = tempfile.TemporaryDirectory()
        home = LearningHome.initialize(Path(temporary.name) / "learning-home")
        repository = PackUpdateRepository.open(home.root)
        source = SourceReference(
            source_type="pasted-text",
            source_id="strict-json-test",
            observed_at="2026-07-24T12:00:00Z",
        )
        content = b"Codex ETL usage.\nClaim: Strict synthetic JSON.\n"
        manifest = home.capture(content, source)
        update = repository.stage(compile_update(content, manifest))
        return repository, update, temporary


class CaptureInputTests(unittest.TestCase):
    def test_capture_rejects_symbolic_link_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "learning-home"
            target = root / "source.txt"
            target.write_text("synthetic", encoding="utf-8")
            link = root / "source-link.txt"
            link.symlink_to(target)
            self.assertEqual(run_cli("init", "--home", str(home)).returncode, 0)

            result = run_cli(
                "capture",
                "--home",
                str(home),
                "--source-type",
                "pasted-text",
                "--source-id",
                "symlink",
                "--input",
                str(link),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("cannot be a symbolic link", result.stderr)
            self.assertEqual(list((home / "private" / "inbox").iterdir()), [])

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO files are not supported")
    def test_capture_rejects_fifo_input_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "learning-home"
            fifo = root / "source.pipe"
            os.mkfifo(fifo)
            self.assertEqual(run_cli("init", "--home", str(home)).returncode, 0)

            result = run_cli(
                "capture",
                "--home",
                str(home),
                "--source-type",
                "pasted-text",
                "--source-id",
                "fifo",
                "--input",
                str(fifo),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("regular file", result.stderr)
            self.assertEqual(list((home / "private" / "inbox").iterdir()), [])

    def test_invalid_claim_status_fails_before_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "learning-home"
            source = root / "source.txt"
            source.write_text(
                "Claim [certain]: This status is not supported.\n",
                encoding="utf-8",
            )
            self.assertEqual(run_cli("init", "--home", str(home)).returncode, 0)

            result = run_cli(
                "capture",
                "--home",
                str(home),
                "--source-type",
                "pasted-text",
                "--source-id",
                "invalid-status",
                "--input",
                str(source),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("unsupported Claim status", result.stderr)
            self.assertEqual(list((home / "private" / "inbox").iterdir()), [])

    def test_capture_rejects_oversized_input_before_persistence(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            home = root / "learning-home"
            source = root / "large.txt"
            source.write_bytes(b"x" * 1_048_577)
            self.assertEqual(run_cli("init", "--home", str(home)).returncode, 0)

            result = run_cli(
                "capture",
                "--home",
                str(home),
                "--source-type",
                "pasted-text",
                "--source-id",
                "oversized",
                "--input",
                str(source),
            )

            self.assertEqual(result.returncode, 1)
            self.assertIn("safety limit", result.stderr)
            self.assertEqual(list((home / "private" / "inbox").iterdir()), [])
