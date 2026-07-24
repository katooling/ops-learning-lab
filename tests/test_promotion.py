from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import http.client
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Thread
import tempfile
import unittest
from unittest import mock
from urllib.parse import urlencode

from ops_learning_lab.compiler import compile_update
from ops_learning_lab.domain import SchemaError, SourceReference
from ops_learning_lab.pack_repository import PackRepository
from ops_learning_lab.promotion import PromotionService
from ops_learning_lab.promotion_models import (
    PromotionDecision,
    PromotionError,
    PromotionPlan,
    StalePromotionError,
)
from ops_learning_lab.shell import make_server
from ops_learning_lab.staging import PackUpdateRepository
from ops_learning_lab.storage import LearningHome, StorageError


NOW = "2026-07-24T12:00:00Z"
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"


def run_cli(*arguments: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(SOURCE_ROOT)
    return subprocess.run(
        [sys.executable, "-m", "ops_learning_lab", *arguments],
        cwd=REPOSITORY_ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )


class PromotionFixture:
    def __init__(self, directory: str) -> None:
        self.root = Path(directory)
        self.home = LearningHome.initialize(self.root / "learning-home")
        self.updates = PackUpdateRepository.open(self.home.root)
        self.packs = PackRepository.open(self.home.root)
        self.service = PromotionService(
            self.updates,
            self.packs,
            clock=lambda: NOW,
        )

    def stage(self, content: str, source_id: str):
        encoded = content.encode("utf-8")
        manifest = self.home.capture(
            encoded,
            SourceReference(
                source_type="pasted-text",
                source_id=source_id,
                observed_at=NOW,
            ),
        )
        return self.updates.stage(compile_update(encoded, manifest))

    def plan(self, update, decisions, *, pack_id="codex-etl", title="Synthetic Codex ETL"):
        review = self.service.review(
            update.update_id,
            target_pack_id=pack_id,
            target_pack_title=title,
        )
        return PromotionPlan(
            update_id=update.update_id,
            proposal_sha256=update.proposal_sha256,
            target_pack_id=pack_id,
            target_pack_title=title,
            expected_base_version=review.expected_base_version,
            expected_base_sha256=review.expected_base_sha256,
            decisions=tuple(decisions),
        )


def accept(proposal_id: str, text: str, *, history="add", target=None):
    return PromotionDecision(
        proposal_id=proposal_id,
        action="accept",
        sanitized_text=text,
        fact_status="current",
        history_action=history,
        target_claim_id=target,
        sensitivity_reviewed=True,
        rejection_reason=None,
    )


def reject(proposal_id: str, reason="unsupported"):
    return PromotionDecision(
        proposal_id=proposal_id,
        action="reject",
        sanitized_text=None,
        fact_status=None,
        history_action=None,
        target_claim_id=None,
        sensitivity_reviewed=None,
        rejection_reason=reason,
    )


class PromotionServiceTests(unittest.TestCase):
    def test_safe_promotion_keeps_rejection_history_and_excludes_private_canary(self):
        canary = "PRIVATE-CANARY-DO-NOT-PUBLISH"
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            update = fixture.stage(
                "Codex ETL usage cost.\n"
                f"Claim: {canary} raw synthetic cost detail.\n"
                "Claim: A neighboring claim without enough evidence.\n",
                "private-source-label",
            )
            plan = fixture.plan(
                update,
                (
                    accept(
                        update.proposed_claims[0].proposal_id,
                        "Normalized synthetic cost is reviewed before use.",
                    ),
                    reject(update.proposed_claims[1].proposal_id),
                ),
            )

            preview = fixture.service.preview(plan)
            self.assertIsNone(fixture.packs.get("codex-etl"))
            result = fixture.service.commit(plan, preview.preview_sha256)

            self.assertFalse(result.already_applied)
            self.assertEqual(result.pack.version, 1)
            self.assertEqual(len(result.pack.claims), 1)
            self.assertEqual(len(result.promotion.decisions), 2)
            self.assertEqual(
                result.promotion.decisions[1].rejection_reason,
                "unsupported",
            )
            pack_path = fixture.home.root / "packs" / "codex-etl" / "pack.json"
            accepted_bytes = pack_path.read_bytes()
            self.assertNotIn(canary.encode(), accepted_bytes)
            self.assertNotIn(b"private-source-label", accepted_bytes)
            self.assertNotIn(update.content_sha256.encode(), accepted_bytes)
            self.assertNotIn(update.intake_id.encode(), accepted_bytes)
            self.assertEqual(pack_path.stat().st_mode & 0o777, 0o600)

            reopened = PackRepository.open(fixture.home.root).get("codex-etl")
            self.assertEqual(reopened, result.pack)
            snapshot = PackRepository.open(fixture.home.root).snapshot("codex-etl")
            self.assertEqual(snapshot.pack_id, "codex-etl")
            self.assertEqual(snapshot.content_sha256, result.pack.content_sha256)
            self.assertFalse(hasattr(snapshot, "promotions"))
            retry = fixture.service.commit(plan, preview.preview_sha256)
            self.assertTrue(retry.already_applied)
            self.assertEqual(retry.pack, result.pack)
            self.assertFalse(hasattr(fixture.packs, "write"))
            self.assertFalse(hasattr(fixture.packs, "locked"))
            self.assertFalse(hasattr(fixture.packs, "_write_promoted"))
            self.assertFalse(hasattr(fixture.packs, "_locked_for_promotion"))

    def test_contradiction_preserves_earlier_claim_as_contradicted_history(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            original = fixture.stage(
                "Codex ETL usage.\nClaim: The synthetic source reports zero cost.\n",
                "original",
            )
            original_plan = fixture.plan(
                original,
                (
                    accept(
                        original.proposed_claims[0].proposal_id,
                        "The reviewed synthetic source reports zero cost.",
                    ),
                ),
            )
            original_preview = fixture.service.preview(original_plan)
            first = fixture.service.commit(
                original_plan,
                original_preview.preview_sha256,
            )
            old_claim = first.pack.claims[0]

            correction = fixture.stage(
                "Codex ETL usage.\n"
                "Claim [contradicted]: The synthetic source now reports a positive cost.\n",
                "correction",
            )
            correction_plan = fixture.plan(
                correction,
                (
                    accept(
                        correction.proposed_claims[0].proposal_id,
                        "The reviewed synthetic source now reports a positive cost.",
                        history="contradict",
                        target=old_claim.claim_id,
                    ),
                ),
            )
            correction_preview = fixture.service.preview(correction_plan)
            second = fixture.service.commit(
                correction_plan,
                correction_preview.preview_sha256,
            )

            self.assertEqual(second.pack.version, 2)
            self.assertEqual(second.pack.claims[0].claim_id, old_claim.claim_id)
            self.assertEqual(second.pack.claims[0].fact_status, "contradicted")
            self.assertEqual(
                second.pack.claims[1].target_claim_id,
                old_claim.claim_id,
            )
            self.assertEqual(second.pack.claims[1].history_action, "contradict")

    def test_stale_base_and_second_destination_are_rejected_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            first_update = fixture.stage(
                "Codex ETL usage.\nClaim: First synthetic fact.\n",
                "first",
            )
            first_plan = fixture.plan(
                first_update,
                (
                    accept(
                        first_update.proposed_claims[0].proposal_id,
                        "First reviewed synthetic fact.",
                    ),
                ),
            )
            preview = fixture.service.preview(first_plan)

            alternate = PromotionPlan(
                **{
                    **first_plan.to_dict(),
                    "target_pack_id": "other-pack",
                    "target_pack_title": "Other Pack",
                    "decisions": first_plan.decisions,
                }
            )
            # A strong match cannot be redirected, even before commit.
            with self.assertRaisesRegex(PromotionError, "strong match target"):
                fixture.service.preview(alternate)

            fixture.service.commit(first_plan, preview.preview_sha256)
            different = fixture.plan(
                first_update,
                (
                    accept(
                        first_update.proposed_claims[0].proposal_id,
                        "A different reviewed wording.",
                    ),
                ),
            )
            different_preview = fixture.service.preview(different)
            with self.assertRaisesRegex(
                StalePromotionError,
                "already has a different",
            ):
                fixture.service.commit(different, different_preview.preview_sha256)
            self.assertEqual(fixture.packs.get("codex-etl").version, 1)

    def test_exact_retry_survives_missing_stage_while_different_command_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            update = fixture.stage(
                "Codex ETL usage.\nClaim: Retry synthetic fact.\n",
                "retry-stage",
            )
            plan = fixture.plan(
                update,
                (
                    accept(
                        update.proposed_claims[0].proposal_id,
                        "Retry reviewed synthetic fact.",
                    ),
                ),
            )
            preview = fixture.service.preview(plan)
            fixture.service.commit(plan, preview.preview_sha256)
            (
                fixture.updates.root / f"{update.update_id}.json"
            ).unlink()

            retry = fixture.service.commit(plan, preview.preview_sha256)
            self.assertTrue(retry.already_applied)
            changed = PromotionPlan(
                **{
                    **plan.to_dict(),
                    "decisions": (
                        accept(
                            update.proposed_claims[0].proposal_id,
                            "Different retry wording.",
                        ),
                    ),
                }
            )
            with self.assertRaisesRegex(StalePromotionError, "different Promotion"):
                fixture.service.commit(changed, preview.preview_sha256)

    def test_concurrent_different_decisions_on_one_update_have_one_winner(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            update = fixture.stage(
                "Codex ETL usage.\nClaim: Concurrent synthetic fact.\n",
                "concurrent",
            )
            plans = (
                fixture.plan(
                    update,
                    (
                        accept(
                            update.proposed_claims[0].proposal_id,
                            "Concurrent reviewed wording A.",
                        ),
                    ),
                ),
                fixture.plan(
                    update,
                    (
                        accept(
                            update.proposed_claims[0].proposal_id,
                            "Concurrent reviewed wording B.",
                        ),
                    ),
                ),
            )
            previews = tuple(fixture.service.preview(plan) for plan in plans)

            def commit(index):
                try:
                    return fixture.service.commit(
                        plans[index],
                        previews[index].preview_sha256,
                    )
                except StalePromotionError as exc:
                    return exc

            with ThreadPoolExecutor(max_workers=2) as executor:
                outcomes = list(executor.map(commit, (0, 1)))

            self.assertEqual(
                sum(not isinstance(item, Exception) for item in outcomes),
                1,
            )
            self.assertEqual(
                sum(isinstance(item, StalePromotionError) for item in outcomes),
                1,
            )
            self.assertEqual(fixture.packs.get("codex-etl").version, 1)

    def test_one_update_cannot_be_promoted_to_two_new_pack_destinations(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            update = fixture.stage(
                "Novel isolated subject.\nClaim: A novel synthetic fact.\n",
                "new-pack",
            )
            self.assertEqual(update.match.kind, "new_pack")
            plans = (
                fixture.plan(
                    update,
                    (
                        accept(
                            update.proposed_claims[0].proposal_id,
                            "A reviewed fact for destination one.",
                        ),
                    ),
                    pack_id="destination-one",
                    title="Destination One",
                ),
                fixture.plan(
                    update,
                    (
                        accept(
                            update.proposed_claims[0].proposal_id,
                            "A reviewed fact for destination two.",
                        ),
                    ),
                    pack_id="destination-two",
                    title="Destination Two",
                ),
            )
            previews = tuple(fixture.service.preview(plan) for plan in plans)
            fixture.service.commit(plans[0], previews[0].preview_sha256)
            with self.assertRaisesRegex(StalePromotionError, "already has a different"):
                fixture.service.commit(plans[1], previews[1].preview_sha256)
            self.assertIsNotNone(fixture.packs.get("destination-one"))
            self.assertIsNone(fixture.packs.get("destination-two"))

    def test_pack_change_after_preview_is_a_stale_conflict(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            initial = fixture.stage(
                "Codex ETL usage.\nClaim: Initial synthetic fact.\n",
                "initial",
            )
            initial_plan = fixture.plan(
                initial,
                (
                    accept(
                        initial.proposed_claims[0].proposal_id,
                        "Initial reviewed synthetic fact.",
                    ),
                ),
            )
            initial_preview = fixture.service.preview(initial_plan)
            fixture.service.commit(initial_plan, initial_preview.preview_sha256)

            earlier = fixture.stage(
                "Codex ETL usage.\nClaim: Earlier-previewed synthetic fact.\n",
                "earlier-preview",
            )
            later = fixture.stage(
                "Codex ETL usage.\nClaim: Intervening synthetic fact.\n",
                "intervening",
            )
            earlier_plan = fixture.plan(
                earlier,
                (
                    accept(
                        earlier.proposed_claims[0].proposal_id,
                        "Earlier-previewed reviewed fact.",
                    ),
                ),
            )
            later_plan = fixture.plan(
                later,
                (
                    accept(
                        later.proposed_claims[0].proposal_id,
                        "Intervening reviewed fact.",
                    ),
                ),
            )
            earlier_preview = fixture.service.preview(earlier_plan)
            later_preview = fixture.service.preview(later_plan)
            fixture.service.commit(later_plan, later_preview.preview_sha256)

            with self.assertRaisesRegex(StalePromotionError, "changed after review"):
                fixture.service.commit(earlier_plan, earlier_preview.preview_sha256)
            self.assertEqual(fixture.packs.get("codex-etl").version, 2)

    def test_interrupted_replace_preserves_previous_pack_and_complete_history(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            first = fixture.stage(
                "Codex ETL usage.\nClaim: Stable synthetic fact.\n",
                "stable",
            )
            first_plan = fixture.plan(
                first,
                (
                    accept(
                        first.proposed_claims[0].proposal_id,
                        "Stable reviewed synthetic fact.",
                    ),
                ),
            )
            first_preview = fixture.service.preview(first_plan)
            fixture.service.commit(first_plan, first_preview.preview_sha256)
            before = (
                fixture.home.root / "packs" / "codex-etl" / "pack.json"
            ).read_bytes()

            second = fixture.stage(
                "Codex ETL usage.\nClaim: Later synthetic fact.\n",
                "later",
            )
            second_plan = fixture.plan(
                second,
                (
                    accept(
                        second.proposed_claims[0].proposal_id,
                        "Later reviewed synthetic fact.",
                    ),
                ),
            )
            second_preview = fixture.service.preview(second_plan)
            with mock.patch(
                "pathlib.Path.replace",
                side_effect=OSError("synthetic replace interruption"),
            ):
                with self.assertRaisesRegex(OSError, "replace interruption"):
                    fixture.service.commit(second_plan, second_preview.preview_sha256)

            pack_path = fixture.home.root / "packs" / "codex-etl" / "pack.json"
            self.assertEqual(pack_path.read_bytes(), before)
            self.assertEqual(fixture.packs.get("codex-etl").version, 1)
            self.assertEqual(
                list(pack_path.parent.glob(".pack.json.*")),
                [],
            )

    def test_directory_fsync_failure_after_replace_returns_committed_result(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            update = fixture.stage(
                "Codex ETL usage.\nClaim: Durable synthetic fact.\n",
                "post-replace-sync",
            )
            plan = fixture.plan(
                update,
                (
                    accept(
                        update.proposed_claims[0].proposal_id,
                        "Durably reviewed synthetic fact.",
                    ),
                ),
            )
            preview = fixture.service.preview(plan)
            real_fsync = os.fsync
            calls = 0

            def fail_directory_sync(descriptor):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("synthetic directory fsync failure")
                return real_fsync(descriptor)

            with mock.patch(
                "ops_learning_lab.storage.os.fsync",
                side_effect=fail_directory_sync,
            ):
                result = fixture.service.commit(plan, preview.preview_sha256)

            self.assertFalse(result.already_applied)
            self.assertEqual(fixture.packs.get("codex-etl"), result.pack)
            retry = fixture.service.commit(plan, preview.preview_sha256)
            self.assertTrue(retry.already_applied)

    def test_malformed_and_private_only_accepted_text_fail_before_write(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            update = fixture.stage(
                "Codex ETL usage.\nClaim: Candidate synthetic fact.\n",
                "private",
            )
            with self.assertRaisesRegex(SchemaError, "private-only"):
                accept(
                    update.proposed_claims[0].proposal_id,
                    f"Keep {update.intake_id} in the accepted pack.",
                )
            with self.assertRaisesRegex(SchemaError, "sensitivity"):
                PromotionDecision(
                    proposal_id=update.proposed_claims[0].proposal_id,
                    action="accept",
                    sanitized_text="Reviewed text.",
                    fact_status="current",
                    history_action="add",
                    target_claim_id=None,
                    sensitivity_reviewed=False,
                    rejection_reason=None,
                )
            self.assertIsNone(fixture.packs.get("codex-etl"))
            malformed = {
                **fixture.plan(
                    update,
                    (
                        accept(
                            update.proposed_claims[0].proposal_id,
                            "Safely reviewed text.",
                        ),
                    ),
                ).to_dict(),
                "unexpected": True,
            }
            with self.assertRaisesRegex(SchemaError, "fields"):
                PromotionPlan.from_dict(malformed)
            malformed_decision = fixture.plan(
                update,
                (
                    accept(
                        update.proposed_claims[0].proposal_id,
                        "Another safely reviewed text.",
                    ),
                ),
            ).to_dict()
            malformed_decision["decisions"][0]["action"] = ["accept"]
            with self.assertRaisesRegex(SchemaError, "action"):
                PromotionPlan.from_dict(malformed_decision)

    def test_structural_private_markers_fail_when_loading_accepted_pack(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            update = fixture.stage(
                "Codex ETL usage.\nClaim: Load synthetic fact.\n",
                "load-private-marker",
            )
            plan = fixture.plan(
                update,
                (
                    accept(
                        update.proposed_claims[0].proposal_id,
                        "Safely reviewed load fact.",
                    ),
                ),
            )
            fixture.service.commit(
                plan,
                fixture.service.preview(plan).preview_sha256,
            )
            pack_path = fixture.home.root / "packs" / "codex-etl" / "pack.json"
            value = json.loads(pack_path.read_text(encoding="utf-8"))
            value["claims"][0]["text"] = f"Expose {update.intake_id}"
            # Recalculate neither digest nor model: structural validation must fire first.
            pack_path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(SchemaError, "does not match the schema"):
                fixture.packs.get("codex-etl")

    def test_configured_exact_canary_blocks_preview_before_any_pack_write(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            canary = "opaque-learning-marker-71fcd"
            service = PromotionService(
                fixture.updates,
                fixture.packs,
                clock=lambda: NOW,
                forbidden_canaries=(canary,),
            )
            update = fixture.stage(
                "Codex ETL usage.\nClaim: Canary candidate.\n",
                "configured-canary",
            )
            plan = fixture.plan(
                update,
                (
                    accept(
                        update.proposed_claims[0].proposal_id,
                        f"Reviewed text with {canary}.",
                    ),
                ),
            )
            with self.assertRaisesRegex(PromotionError, "configured private canary"):
                service.preview(plan)
            self.assertIsNone(fixture.packs.get("codex-etl"))

    def test_preview_change_summary_is_deterministic_and_classified(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            update = fixture.stage(
                "Codex ETL usage.\n"
                "Claim [current]: First summary fact.\n"
                "Claim [historical]: Second summary fact.\n",
                "summary",
            )
            plan = fixture.plan(
                update,
                (
                    accept(
                        update.proposed_claims[0].proposal_id,
                        "First generalized summary fact.",
                    ),
                    reject(update.proposed_claims[1].proposal_id, "not-relevant"),
                ),
            )
            first = fixture.service.preview(plan).changes
            second = fixture.service.preview(plan).changes
            self.assertEqual(first, second)
            self.assertEqual(
                first.removed,
                (update.proposed_claims[1].proposal_id,),
            )
            self.assertEqual(
                first.retained,
                (
                    f"{update.proposed_claims[0].proposal_id}:safe-provenance",
                    f"{update.proposed_claims[0].proposal_id}:fact-status",
                ),
            )
            self.assertEqual(
                first.generalized,
                (f"{update.proposed_claims[0].proposal_id}:text",),
            )

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO files are not supported")
    def test_pack_symlink_and_fifo_hazards_fail_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            external = fixture.root / "external"
            external.mkdir()
            (fixture.packs.root / "codex-etl").symlink_to(external)
            with self.assertRaisesRegex(StorageError, "symbolic link"):
                fixture.packs.get("codex-etl")
            (fixture.packs.root / "codex-etl").unlink()
            pack_root = fixture.packs.root / "codex-etl"
            pack_root.mkdir()
            os.mkfifo(pack_root / "pack.json")
            with self.assertRaisesRegex(StorageError, "regular file"):
                fixture.packs.get("codex-etl")


class PromotionHttpJourneyTests(unittest.TestCase):
    def test_review_preview_promote_and_reopen_pack_with_http_guards(self):
        canary = "RAW-ONLY-CANARY-71FCD"
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            update = fixture.stage(
                f"{canary}\nCodex ETL usage.\n"
                "Claim: A private draft says synthetic costs are non-negative.\n"
                "Claim: An unsupported neighboring statement.\n",
                "browser-source",
            )
            server = make_server(
                fixture.updates,
                "127.0.0.1",
                0,
                promotion=fixture.service,
            )
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            origin = f"http://127.0.0.1:{port}"
            connection = http.client.HTTPConnection("127.0.0.1", port)
            try:
                connection.request("GET", f"/updates/{update.update_id}")
                response = connection.getresponse()
                review_body = response.read().decode("utf-8")
                self.assertEqual(response.status, 200)
                self.assertIn("Review staged content", review_body)
                csp = response.getheader("Content-Security-Policy", "")
                self.assertIn("frame-ancestors 'none'", csp)
                self.assertIn("base-uri 'none'", csp)
                self.assertEqual(response.getheader("Referrer-Policy"), "same-origin")
                self.assertIn("<fieldset>", review_body)
                self.assertIn("<legend>", review_body)
                self.assertIn('name="claim-0-action" value="accept"', review_body)
                self.assertNotIn('value="accept" checked', review_body)
                self.assertIn('id="target-pack-id" name="target-pack-id"', review_body)
                self.assertNotIn('name="target-pack-id" value=', review_body)
                self.assertNotIn('name="claim-0-text">A private', review_body)
                self.assertNotIn(canary, review_body)
                self.assertIn("@media (max-width: 20rem)", review_body)
                csrf = re.search(
                    r'name="csrf-token" value="([0-9a-f]{64})"',
                    review_body,
                ).group(1)

                form = {
                    "csrf-token": csrf,
                    "signed-review": re.search(
                        r'name="signed-review" value="([^"]+)"',
                        review_body,
                    ).group(1),
                    "review-signature": re.search(
                        r'name="review-signature" value="([^"]+)"',
                        review_body,
                    ).group(1),
                    "target-pack-id": "codex-etl",
                    "target-pack-title": "Synthetic Codex ETL",
                    "claim-0-proposal-id": update.proposed_claims[0].proposal_id,
                    "claim-0-action": "accept",
                    "claim-0-text": "Reviewed synthetic costs are non-negative.",
                    "claim-0-status": "current",
                    "claim-0-history": "add",
                    "claim-0-target": "",
                    "claim-0-sensitivity": "reviewed",
                    "claim-0-reason": "",
                    "claim-1-proposal-id": update.proposed_claims[1].proposal_id,
                    "claim-1-action": "reject",
                    "claim-1-text": "",
                    "claim-1-status": "",
                    "claim-1-history": "",
                    "claim-1-target": "",
                    "claim-1-reason": "unsupported",
                }
                headers = {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Origin": origin,
                }

                hostile = http.client.HTTPConnection("127.0.0.1", port)
                hostile.request(
                    "POST",
                    f"/updates/{update.update_id}/preview",
                    urlencode(form),
                    {"Content-Type": headers["Content-Type"]},
                )
                blocked = hostile.getresponse()
                self.assertEqual(blocked.status, 403)
                blocked.read()
                hostile.close()

                invalid_csrf = {**form, "csrf-token": "0" * 64}
                connection.request(
                    "POST",
                    f"/updates/{update.update_id}/preview",
                    urlencode(invalid_csrf),
                    headers,
                )
                csrf_response = connection.getresponse()
                csrf_body = csrf_response.read().decode("utf-8")
                self.assertEqual(csrf_response.status, 400)
                self.assertIn("CSRF token is invalid", csrf_body)
                self.assertIsNone(fixture.packs.get("codex-etl"))

                tampered_review = {
                    **form,
                    "review-signature": "0" * 64,
                }
                connection.request(
                    "POST",
                    f"/updates/{update.update_id}/preview",
                    urlencode(tampered_review),
                    headers,
                )
                signed_response = connection.getresponse()
                signed_body = signed_response.read().decode("utf-8")
                self.assertEqual(signed_response.status, 400)
                self.assertIn("signed review integrity check failed", signed_body)
                self.assertIsNone(fixture.packs.get("codex-etl"))

                connection.request(
                    "POST",
                    f"/updates/{update.update_id}/preview",
                    urlencode(form),
                    headers,
                )
                preview_response = connection.getresponse()
                preview_body = preview_response.read().decode("utf-8")
                self.assertEqual(preview_response.status, 200, preview_body)
                self.assertIn("No write has happened", preview_body)
                self.assertIsNone(fixture.packs.get("codex-etl"))

                hidden = {
                    name: re.search(
                        rf'name="{name}" value="([^"]+)"',
                        preview_body,
                    ).group(1)
                    for name in (
                        "csrf-token",
                        "signed-plan",
                        "plan-signature",
                        "preview-sha256",
                    )
                }
                hidden["confirm"] = "yes"
                connection.request(
                    "POST",
                    f"/updates/{update.update_id}/promote",
                    urlencode(hidden),
                    headers,
                )
                promoted = connection.getresponse()
                self.assertEqual(promoted.status, 303)
                self.assertEqual(promoted.getheader("Location"), "/packs/codex-etl")
                promoted.read()

                connection.request("GET", "/packs/codex-etl")
                pack_response = connection.getresponse()
                pack_body = pack_response.read().decode("utf-8")
                self.assertEqual(pack_response.status, 200)
                self.assertIn("Reviewed synthetic costs are non-negative.", pack_body)
                self.assertIn("Promotion history", pack_body)
                self.assertIn("rejected: unsupported", pack_body)
                self.assertNotIn(canary, pack_body)
                self.assertNotIn("A private draft says", pack_body)
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)

    def test_get_review_base_change_returns_409_before_preview_and_preserves_input(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            initial = fixture.stage(
                "Codex ETL usage.\nClaim: Initial browser fact.\n",
                "http-initial",
            )
            initial_plan = fixture.plan(
                initial,
                (
                    accept(
                        initial.proposed_claims[0].proposal_id,
                        "Initial reviewed browser fact.",
                    ),
                ),
            )
            fixture.service.commit(
                initial_plan,
                fixture.service.preview(initial_plan).preview_sha256,
            )
            stale = fixture.stage(
                "Codex ETL usage.\nClaim: Stale browser candidate.\n",
                "http-stale",
            )
            intervening = fixture.stage(
                "Codex ETL usage.\nClaim: Intervening browser candidate.\n",
                "http-intervening",
            )
            server = make_server(
                fixture.updates,
                "127.0.0.1",
                0,
                promotion=fixture.service,
            )
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]
            origin = f"http://127.0.0.1:{port}"
            headers = {
                "Content-Type": "application/x-www-form-urlencoded",
                "Origin": origin,
            }
            connection = http.client.HTTPConnection("127.0.0.1", port)
            try:
                connection.request("GET", f"/updates/{stale.update_id}")
                response = connection.getresponse()
                body = response.read().decode("utf-8")
                csrf = re.search(
                    r'name="csrf-token" value="([0-9a-f]{64})"',
                    body,
                ).group(1)
                entered = "Preserve this entered reviewed wording."
                form = {
                    "csrf-token": csrf,
                    "signed-review": re.search(
                        r'name="signed-review" value="([^"]+)"',
                        body,
                    ).group(1),
                    "review-signature": re.search(
                        r'name="review-signature" value="([^"]+)"',
                        body,
                    ).group(1),
                    "target-pack-id": "codex-etl",
                    "target-pack-title": "Synthetic Codex ETL",
                    "claim-0-proposal-id": stale.proposed_claims[0].proposal_id,
                    "claim-0-action": "accept",
                    "claim-0-text": entered,
                    "claim-0-status": "current",
                    "claim-0-history": "add",
                    "claim-0-target": "",
                    "claim-0-sensitivity": "reviewed",
                    "claim-0-reason": "",
                }
                intervening_plan = fixture.plan(
                    intervening,
                    (
                        accept(
                            intervening.proposed_claims[0].proposal_id,
                            "Intervening reviewed browser fact.",
                        ),
                    ),
                )
                fixture.service.commit(
                    intervening_plan,
                    fixture.service.preview(intervening_plan).preview_sha256,
                )

                connection.request(
                    "POST",
                    f"/updates/{stale.update_id}/preview",
                    urlencode(form),
                    headers,
                )
                conflict = connection.getresponse()
                conflict_body = conflict.read().decode("utf-8")
                self.assertEqual(conflict.status, 409)
                self.assertIn("No changes were made", conflict_body)
                self.assertIn(entered, conflict_body)
                self.assertIn("preview again", conflict_body)
                self.assertEqual(fixture.packs.get("codex-etl").version, 2)
            finally:
                connection.close()
                server.shutdown()
                server.server_close()
                thread.join(timeout=2)


class PromotionCliJourneyTests(unittest.TestCase):
    def test_cli_preview_and_commit_use_the_same_canonical_pack_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            update = fixture.stage(
                "Codex ETL usage.\nClaim: CLI synthetic draft.\n",
                "cli-source",
            )
            plan = fixture.plan(
                update,
                (
                    accept(
                        update.proposed_claims[0].proposal_id,
                        "CLI reviewed synthetic fact.",
                    ),
                ),
            )
            plan_path = fixture.root / "plan.json"
            plan_path.write_text(
                json.dumps(plan.to_dict()),
                encoding="utf-8",
            )

            preview = run_cli(
                "promotion-preview",
                "--home",
                str(fixture.home.root),
                "--plan",
                str(plan_path),
            )
            self.assertEqual(preview.returncode, 0, preview.stderr)
            preview_result = json.loads(preview.stdout)
            self.assertFalse(preview_result["written"])
            self.assertIsNone(fixture.packs.get("codex-etl"))

            commit = run_cli(
                "promotion-commit",
                "--home",
                str(fixture.home.root),
                "--plan",
                str(plan_path),
                "--preview-sha256",
                preview_result["preview_sha256"],
            )
            self.assertEqual(commit.returncode, 0, commit.stderr)
            self.assertEqual(json.loads(commit.stdout)["status"], "promoted")
            self.assertEqual(fixture.packs.get("codex-etl").version, 1)

    def test_cli_configured_canary_file_blocks_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = PromotionFixture(directory)
            canary = "cli-opaque-canary-91da7"
            update = fixture.stage(
                "Codex ETL usage.\nClaim: CLI canary draft.\n",
                "cli-canary",
            )
            plan = fixture.plan(
                update,
                (
                    accept(
                        update.proposed_claims[0].proposal_id,
                        f"Reviewed text contains {canary}.",
                    ),
                ),
            )
            plan_path = fixture.root / "plan.json"
            plan_path.write_text(json.dumps(plan.to_dict()), encoding="utf-8")
            canary_path = fixture.root / "canary.txt"
            canary_path.write_text(canary, encoding="utf-8")

            preview = run_cli(
                "promotion-preview",
                "--home",
                str(fixture.home.root),
                "--plan",
                str(plan_path),
                "--forbidden-canary-file",
                str(canary_path),
            )
            self.assertEqual(preview.returncode, 1)
            self.assertIn("configured private canary", preview.stderr)
            self.assertIsNone(fixture.packs.get("codex-etl"))


if __name__ == "__main__":
    unittest.main()
