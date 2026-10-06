"""Bind the failed baseline and freeze one Price Action repair."""
from __future__ import annotations

import json

from research.symbol_setup_one_mechanism_repair_precommit import (
    ANALYSIS_ID,
    CASE_PARITY,
    CASE_READY,
    COMPLETE_STRATEGY_ID,
    COMPLETE_STRATEGY_SHA256,
    NEXT_RESEARCH,
    NEXT_STOP,
    PRIMARY_DEFICIENCY,
    REPAIR_ID,
)
from research.symbol_setup_one_mechanism_repair_precommit.contract import build
from research.symbol_setup_one_mechanism_repair_precommit.publish import publish


def main() -> int:
    built = build()
    verdict, nxt = (CASE_READY, NEXT_RESEARCH) if built["ok"] else (CASE_PARITY, NEXT_STOP)
    report = {
        "analysis_id": ANALYSIS_ID,
        "verdict": verdict,
        "next": nxt,
        "complete_strategy_id": COMPLETE_STRATEGY_ID,
        "complete_strategy_sha256": COMPLETE_STRATEGY_SHA256,
        "primary_deficiency": PRIMARY_DEFICIENCY,
        "repair_id": REPAIR_ID,
        "repair_spec_sha256": built["repair_spec_sha256"],
        "repair_spec": built["repair_spec"],
        "population": built["population"],
        "baseline_180": built["baseline_180"],
        "gates": built["gates"],
        "checks": built["checks"],
        "frozen_failure": {
            "trade_n": 7,
            "net_pnl_yen": -31500.0,
            "PF": 0.46153846153846156,
            "primary_deficiency": PRIMARY_DEFICIENCY,
        },
        "no_run": {
            "NEW_COMPLETE_STRATEGY_RUN": False,
            "NEW_PNL_RUN": False,
            "lookback_searched": False,
            "MA_CHANGED": False,
            "BB_CHANGED": False,
            "RCI_CHANGED": False,
            "VOLUME_CHANGED": False,
            "BOARD_CHANGED": False,
            "EXIT_CHANGED": False,
            "EXECUTION_CHANGED": False,
            "CONTEXT_OR_PB1_ADDED": False,
        },
        "prospective_data_opened": False,
        "prospective_rows_read": 0,
        "research_only": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
    }
    publish(report)
    print(json.dumps({"VERDICT": verdict, "NEXT": nxt, "SHA": built["repair_spec_sha256"], "OK": built["ok"]}), flush=True)
    return 0 if built["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
