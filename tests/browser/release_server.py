"""Run the real Product Shell against one retained synthetic test home."""

from __future__ import annotations

import argparse
from pathlib import Path
import signal
from threading import Thread

from ops_learning_lab.bundle_repository import BundleRepository
from ops_learning_lab.learner_state import EventAttemptStore
from ops_learning_lab.learning_service import LearningService
from ops_learning_lab.pack_repository import PackRepository
from ops_learning_lab.promotion import PromotionService
from ops_learning_lab.shell import make_server
from ops_learning_lab.staging import PackUpdateRepository
from ops_learning_lab.storage import LearningHome


EARLY_TIME = "2026-07-24T12:00:00Z"
REVIEW_TIME = "2026-07-31T12:00:00Z"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True, type=Path)
    parser.add_argument("--canary-file", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    arguments = parser.parse_args()

    home = LearningHome.open(arguments.home)
    attempts = EventAttemptStore.open(home.root)
    bundles = BundleRepository.open(home.root)
    canary = arguments.canary_file.read_text(encoding="utf-8")
    promotion = PromotionService(
        PackUpdateRepository.open(home.root),
        PackRepository.open(home.root),
        forbidden_canaries=(canary,),
    )

    def clock() -> str:
        demonstrated = any(
            entry.status == "completed"
            and entry.attempt_kind == "learning"
            and entry.completed_record is not None
            and entry.completed_record.evaluation is not None
            and entry.completed_record.evaluation.qualifies
            for entry in attempts.history().attempts
        )
        return REVIEW_TIME if demonstrated else EARLY_TIME

    learning = LearningService(
        promotion.packs,
        bundles,
        attempts,
        clock=clock,
    )
    server = make_server(
        PackUpdateRepository.open(home.root),
        "127.0.0.1",
        arguments.port,
        promotion=promotion,
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
        bundles.close()
        attempts.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
