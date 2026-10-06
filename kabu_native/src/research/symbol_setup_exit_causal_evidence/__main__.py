"""Preflight the exit-evidence lane. Do not rewrite the published report."""
from __future__ import annotations

import json

from research.symbol_setup_exit_causal_evidence.preflight import run as run_preflight


def main() -> int:
    result = run_preflight()
    print(json.dumps({"READY": result["ready"], "TARGET": result["target_classification_now"], "PUSH_TRUE_PATH": result["historical_push_capture_present"], "REPORT_REWRITTEN": result["report_rewritten"]}), flush=True)
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
