"""Publish the complete-strategy precommit and stop before any replay."""
from __future__ import annotations

import json

from research.symbol_setup_baseline_complete_strategy_precommit.contract import build
from research.symbol_setup_baseline_complete_strategy_precommit.publish import publish


def main() -> int:
    report = build()
    publish(report)
    print(
        json.dumps(
            {
                "VERDICT": report["verdict"],
                "NEXT": report["next"],
                "STRATEGY": report["complete_strategy_sha256"],
                "UNIVERSE": report["universe"].get("resolved"),
            }
        ),
        flush=True,
    )
    return 0 if str(report["verdict"]).endswith("READY_V1") else 2


if __name__ == "__main__":
    raise SystemExit(main())
