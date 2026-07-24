"""Serve one synthetic Promotion journey for the real-browser proof."""

from __future__ import annotations

import argparse
from pathlib import Path
import signal
import tempfile
from threading import Thread

from ops_learning_lab.compiler import compile_update
from ops_learning_lab.bundle_repository import BundleRepository
from ops_learning_lab.domain import SourceReference
from ops_learning_lab.learner_state import EventAttemptStore
from ops_learning_lab.learning_service import LearningService
from ops_learning_lab.pack_repository import PackRepository
from ops_learning_lab.promotion import PromotionService
from ops_learning_lab.shell import make_server
from ops_learning_lab.staging import PackUpdateRepository
from ops_learning_lab.storage import LearningHome


CANARY = "browser-private-canary-6f103"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", required=True, type=int)
    arguments = parser.parse_args()

    with tempfile.TemporaryDirectory() as directory:
        home = LearningHome.initialize(Path(directory) / "learning-home")
        content = (
            f"{CANARY}\n"
            "Codex ETL usage cost.\n"
            "Claim [current]: Synthetic normalized cost should be non-negative.\n"
            "Claim: A neighboring statement lacks supporting evidence.\n"
        ).encode("utf-8")
        manifest = home.capture(
            content,
            SourceReference(
                source_type="synthetic-browser-fixture",
                source_id="browser-fixture",
                observed_at="2026-07-24T12:00:00Z",
            ),
        )
        updates = PackUpdateRepository.open(home.root)
        updates.stage(compile_update(content, manifest))
        service = PromotionService(
            updates,
            PackRepository.open(home.root),
            forbidden_canaries=(CANARY,),
        )
        attempt_store = EventAttemptStore.open(home.root)

        def clock() -> str:
            demonstrated = any(
                entry.status == "completed"
                and entry.attempt_kind == "learning"
                and entry.completed_record is not None
                and entry.completed_record.evaluation is not None
                and entry.completed_record.evaluation.qualifies
                for entry in attempt_store.history().attempts
            )
            return (
                "2026-07-31T12:00:00Z"
                if demonstrated
                else "2026-07-24T12:00:00Z"
            )

        learning = LearningService(
            service.packs,
            BundleRepository.open(home.root),
            attempt_store,
            clock=clock,
        )
        server = make_server(
            updates,
            "127.0.0.1",
            arguments.port,
            promotion=service,
            learning=learning,
        )

        def stop(_signum, _frame) -> None:
            Thread(target=server.shutdown, daemon=True).start()

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        try:
            server.serve_forever()
        finally:
            server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
