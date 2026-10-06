"""Publish the minimal-completion precommit and stop."""
from __future__ import annotations

import json

from research.symbol_setup_baseline_minimal_completion.contract import build
from research.symbol_setup_baseline_minimal_completion.publish import publish


def main() -> int:
    report = build()
    publish(report)
    print(
        json.dumps(
            {
                "VERDICT": report["verdict"],
                "NEXT": report["next"],
                "DATA_READY": report["coverage"]["EXACT_V1_REPLAY_DATA_READY"],
                "SESSIONS": report["coverage"]["eligible_session_n"],
            }
        ),
        flush=True,
    )
    return 0 if report["verdict"] != "FAIL_CLOSED" else 2


if __name__ == "__main__":
    raise SystemExit(main())
