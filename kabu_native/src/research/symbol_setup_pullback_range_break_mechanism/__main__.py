"""Run the precommitted range-break research. Do not run a complete strategy."""
from __future__ import annotations

import json
import math

from research.symbol_setup_baseline_complete_development.verify import identity
from research.symbol_setup_failure_evidence_gap.scan import PriceParityError
from research.symbol_setup_one_mechanism_repair_precommit.contract import _sha, repair_spec
from research.symbol_setup_pullback_range_break_mechanism import (
    ANALYSIS_ID,
    COMPLETE_STRATEGY_ID,
    COMPLETE_STRATEGY_SHA256,
    NEXT_STOP,
    PRIMARY_DEFICIENCY,
    REPAIR_ID,
    REPAIR_SPEC_SHA256,
    VERDICT_CLOSED,
    VERDICT_PARITY,
)
from research.symbol_setup_pullback_range_break_mechanism.decide import analyze
from research.symbol_setup_pullback_range_break_mechanism.isolation import NATIVE
from research.symbol_setup_pullback_range_break_mechanism.publish import publish
from research.symbol_setup_pullback_range_break_mechanism.scan import PopulationMismatch, scan
from research.symbol_setup_pullback_range_break_mechanism.trigger import range_break


def _close(got: object, exp: float) -> bool:
    try:
        return math.isclose(float(got), float(exp), rel_tol=0.0, abs_tol=1e-9)
    except (TypeError, ValueError):
        return False


def _groups(pre: dict) -> dict[str, dict[str, str]]:
    original = set(pre["original18"])
    folds = {name: set(days) for name, days in pre["folds"].items()}
    out = {}
    for day in list(pre["original18"]) + list(pre["extension17"]):
        lineage = "ORIGINAL18" if day in original else "EXTENSION17"
        fold = next(name for name, days in folds.items() if day in days)
        out[str(day)] = {"lineage": lineage, "fold": fold}
    return out


def _shell(verdict: str, nxt: str, **extra: object) -> dict:
    return {
        "analysis_id": ANALYSIS_ID,
        "verdict": verdict,
        "next": nxt,
        "repair_id": REPAIR_ID,
        "repair_spec_sha256": REPAIR_SPEC_SHA256,
        "complete_strategy_id": COMPLETE_STRATEGY_ID,
        "complete_strategy_sha256": COMPLETE_STRATEGY_SHA256,
        "primary_deficiency": PRIMARY_DEFICIENCY,
        "historical_verdicts_rewritten": False,
        "NEW_COMPLETE_STRATEGY_RUN": False,
        "NEW_PNL_RUN": False,
        "MA_CHANGED": False,
        "BB_CHANGED": False,
        "RCI_CHANGED": False,
        "VOLUME_CHANGED": False,
        "BOARD_CHANGED": False,
        "EXIT_CHANGED": False,
        "EXECUTION_CHANGED": False,
        "CONTEXT_OR_PB1_ADDED": False,
        "lookback_searched": False,
        "prospective_data_opened": False,
        "prospective_rows_read": 0,
        "research_only": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "no_pnl": {
            "NEW_COMPLETE_STRATEGY_RUN": False,
            "NEW_PNL_RUN": False,
            "portfolio_pf": None,
            "portfolio_net_pnl": None,
        },
        "identity": extra.get("identity") or {},
        "analysis": extra.get("analysis") or {},
        "repaired_signals": extra.get("repaired_signals") or [],
        **{k: v for k, v in extra.items() if k not in ("identity", "analysis", "repaired_signals")},
    }


def _self_check() -> None:
    import numpy as np

    n = 30
    ind = {k: np.full(n, np.nan) for k in ("close", "high", "ema9", "bb_upper")}
    ind["close"][25] = 10.0
    ind["ema9"][25] = 9.0
    ind["bb_upper"][25] = 11.0
    ind["high"][22] = 9.5
    ind["high"][23] = 9.8
    ind["high"][24] = 9.2
    ok, local = range_break(ind, 25)
    if not ok or local != 9.8:
        raise RuntimeError("range_break_self_check_pass")
    ind["high"][23] = 10.0
    ok, local = range_break(ind, 25)
    if ok or local != 10.0:
        raise RuntimeError("range_break_self_check_fail_strict")
    ind["high"][23] = np.nan
    ok, local = range_break(ind, 25)
    if ok or local is not None:
        raise RuntimeError("range_break_self_check_missing")


def main() -> int:
    _self_check()
    before = identity()
    spec_sha = _sha(repair_spec())
    failed = json.loads((NATIVE / "results/research/symbol_setup_baseline_complete_strategy_development_v1/report.json").read_text(encoding="utf-8"))
    primary = failed["primary"]
    ident = {
        "identity_before_ok": bool(before["ok"]),
        "repair_spec_sha256": spec_sha,
        "repair_spec_match": spec_sha == REPAIR_SPEC_SHA256,
        "strategy_sha_match": failed["complete_strategy_sha256"] == COMPLETE_STRATEGY_SHA256,
        "trade_n": primary["trade_n"] == 7,
        "net_pnl_yen": _close(primary["net_pnl_yen"], -31500.0),
        "pf": _close(primary["PF"], 0.46153846153846156),
    }
    if not all(ident.values()):
        publish(_shell(VERDICT_CLOSED, NEXT_STOP, identity=ident))
        print(json.dumps({"VERDICT": VERDICT_CLOSED, "NEXT": NEXT_STOP}), flush=True)
        return 2
    pre = json.loads((NATIVE / "results/research/symbol_setup_baseline_complete_strategy_precommit_v1/report.json").read_text(encoding="utf-8"))
    gap = json.loads((NATIVE / "results/research/symbol_setup_failure_evidence_gap_v1/report.json").read_text(encoding="utf-8"))
    frozen_table = gap["analysis"]["tables"]["ALL_TECHNICAL_SIGNALS"]
    try:
        scanned = scan(before["universe_sessions"], _groups(pre))
    except PopulationMismatch as exc:
        publish(_shell(VERDICT_PARITY, NEXT_STOP, identity=ident, analysis={"parity": exc.detail}))
        print(json.dumps({"VERDICT": VERDICT_PARITY, "NEXT": NEXT_STOP}, default=str), flush=True)
        return 2
    except PriceParityError as exc:
        publish(_shell(VERDICT_CLOSED, NEXT_STOP, identity=ident, analysis={"price_parity": exc.detail}))
        print(json.dumps({"VERDICT": VERDICT_CLOSED, "NEXT": NEXT_STOP}, default=str), flush=True)
        return 2
    after = identity()
    ident["identity_after_ok"] = bool(after["ok"])
    if not after["ok"]:
        publish(_shell(VERDICT_CLOSED, NEXT_STOP, identity=ident))
        print(json.dumps({"VERDICT": VERDICT_CLOSED, "NEXT": NEXT_STOP}), flush=True)
        return 2
    analysis = analyze(scanned, frozen_table)
    report = _shell(
        analysis["verdict"],
        analysis["next"],
        identity=ident,
        analysis=analysis,
        repaired_signals=scanned["repaired"],
    )
    publish(report)
    cell = next(row for row in analysis["pre_board"] if int(row["horizon_sec"]) == 180)
    print(
        json.dumps(
            {
                "VERDICT": analysis["verdict"],
                "NEXT": analysis["next"],
                "N": analysis["repaired_pre_board_n"],
                "RAW180": cell["raw_mid_median"],
                "BID180": cell["bid_anchor_median"],
                "GATES": analysis["mechanism_pass"],
            }
        ),
        flush=True,
    )
    return 0 if analysis["verdict"].endswith("SUPPORTED_V1") and "NOT_" not in analysis["verdict"] and "INSUFFICIENT" not in analysis["verdict"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
