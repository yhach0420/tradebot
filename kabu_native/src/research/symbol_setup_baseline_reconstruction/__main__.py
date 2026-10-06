"""Freeze the reconstructed Simple Tech V1 identity and stop."""
from __future__ import annotations

import json

from research.symbol_setup_baseline_reconstruction.publish import build, publish


def main() -> int:
    report = build()
    publish(report)
    print(json.dumps({"VERDICT": report["verdict"], "NEXT": report["next"], "SHA": report["contract"]["baseline_spec_sha256"]}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
