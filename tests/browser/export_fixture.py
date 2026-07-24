"""Create one deterministic standalone artifact for Playwright."""

from __future__ import annotations

import argparse
from pathlib import Path

from ops_learning_lab.export_repository import ExportRepository
from ops_learning_lab.export_approval import ExportApproval
from ops_learning_lab.exporting import ExportPolicy, StandaloneExporter
from tests.fixtures_learning import accepted_snapshot, bundle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    exports = arguments.output / "exports"
    exports.mkdir(parents=True)
    with ExportRepository.open(exports) as repository:
        learning_bundle = bundle()
        receipt = StandaloneExporter(repository).export(
            learning_bundle,
            ExportPolicy((b"PRIVATE-BROWSER-SMOKE-CANARY",)),
            approval=ExportApproval.build(
                learning_bundle,
                accepted_snapshot(),
            ),
        )
    print((exports / receipt.relative_path).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
