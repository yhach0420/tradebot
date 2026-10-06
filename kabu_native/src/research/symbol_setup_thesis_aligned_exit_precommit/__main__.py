"""Publish the thesis-aligned exit precommit and stop."""
from __future__ import annotations

import json

from research.symbol_setup_thesis_aligned_exit_precommit.contract import build
from research.symbol_setup_thesis_aligned_exit_precommit.publish import publish


def main() -> int:
    report = build()
    publish(report)
    print(
        json.dumps(
            {
                "VERDICT": report["verdict"],
                "NEXT": report["next"],
                "EXIT": report["exit"]["EXIT_CONTRACT_SHA256"],
                "EXEC": report["execution"]["EXIT_EXECUTION_SHA256"],
            }
        ),
        flush=True,
    )
    return 0 if report["verdict"].endswith("READY_V1") else 2


if __name__ == "__main__":
    raise SystemExit(main())
