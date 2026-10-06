"""Bind the failed baseline and freeze directional volume only if its contract holds."""
from __future__ import annotations

import hashlib
import json
import math

from research.symbol_setup_baseline_complete_development.verify import identity
from research.symbol_setup_directional_volume_precommit import (
    ANALYSIS_ID,
    COMPLETE_STRATEGY_ID,
    COMPLETE_STRATEGY_SHA256,
    NEXT_STOP,
    NEXT_STOP_REPAIR,
    PRIMARY_DEFICIENCY,
    PRIMARY_HORIZON_SEC,
    RANGE_BREAK_REPAIR_ID,
    RANGE_BREAK_STATUS,
    REPAIR_ID,
    SUPPORT_MIN_N,
    VERDICT_CLOSED,
    VERDICT_PARITY,
    VERDICT_UNRESOLVED,
)
from research.symbol_setup_directional_volume_precommit.decide import analyze
from research.symbol_setup_directional_volume_precommit.isolation import NATIVE
from research.symbol_setup_directional_volume_precommit.publish import publish
from research.symbol_setup_directional_volume_precommit.scan import ContractMismatch, PopulationMismatch, Probe, scan
from research.symbol_setup_directional_volume_precommit.semantics import repair_spec, semantics
from research.simple_tech_entry_family.bars import SymbolBarBuilder


def _sha(body: object) -> str:
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


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


def _self_check() -> None:
    start = 1_784_000_000.0
    end = start + 3600.0
    builder = SymbolBarBuilder(am_start=start, am_end=end)
    probe = Probe(am_start=start, am_end=end)
    events = [
        (start + 1, 100.0, 10.0, 99.0, 101.0),
        (start + 2, 101.0, 15.0, 100.0, 101.0),
        (start + 3, 100.0, 25.0, 100.0, 101.0),
        (start + 4, 100.5, 30.0, 100.0, 101.0),
        (start + 60, 100.0, 30.0, 100.0, 101.0),
    ]
    for et, px, cum, bid, ask in events:
        kwargs = {"et": et, "px": px, "cum_vol": cum, "bid": bid, "ask": ask, "continuous": True}
        builder.on_event(**kwargs)
        probe.on_event(**kwargs)
    builder.close_session()
    probe.close_session()
    bars = builder.as_arrays()
    if abs(float(bars["volume"].sum()) - 20.0) > 1e-9:
        raise RuntimeError("volume_self_check")
    if abs(float(bars["ask_vol"].sum()) - 5.0) > 1e-9 or abs(float(bars["bid_vol"].sum()) - 10.0) > 1e-9:
        raise RuntimeError("side_self_check")
    if abs(probe.ask - 5.0) > 1e-9 or abs(probe.bid - 10.0) > 1e-9 or abs(probe.volume - 20.0) > 1e-9:
        raise RuntimeError("probe_self_check")
    if not semantics()["resolved"]:
        raise RuntimeError("semantics_self_check")


def _shell(verdict: str, nxt: str, **extra: object) -> dict:
    ready = bool(extra.get("repair_ready"))
    spec = repair_spec() if ready else None
    return {
        "analysis_id": ANALYSIS_ID,
        "verdict": verdict,
        "next": nxt,
        "complete_strategy_id": COMPLETE_STRATEGY_ID,
        "complete_strategy_sha256": COMPLETE_STRATEGY_SHA256,
        "primary_deficiency": PRIMARY_DEFICIENCY,
        "repair_id": REPAIR_ID if ready else None,
        "repair_spec_sha256": _sha(spec) if spec else None,
        "repair_spec": spec,
        "repair_ready": ready,
        "historical_verdicts_rewritten": False,
        "NEW_PNL_RUN": False,
        "NEW_COMPLETE_STRATEGY_RUN": False,
        "research_only": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "prospective_data_opened": False,
        "prospective_rows_read": 0,
        "semantics": semantics(),
        "parent": extra.get("parent") or {},
        "prior_repair": extra.get("prior_repair") or {},
        "analysis": extra.get("analysis") or {},
        "research_gates": {
            "primary_horizon_sec": PRIMARY_HORIZON_SEC,
            "support_min_n": SUPPORT_MIN_N,
            "support_all_folds": True,
            "raw_mid_180_median": "> 0",
            "bid_anchor_180_median": "> 0",
            "ask_to_bid_is_primary_gate": False,
            "baseline_raw_mid_180_median": 0.0,
            "baseline_bid_anchor_180_median": 0.0,
            "baseline_comparator_n": 121,
            "ORIGINAL18_raw_mid_180_median": ">= 0",
            "EXTENSION17_raw_mid_180_median": ">= 0",
            "folds_positive": "at least 2 of 3 raw_mid_180_median > 0",
            "single_day_monopoly": False,
            "single_symbol_monopoly": False,
            "board_can_rescue": False,
            "primary_population": "DIRECTIONAL_VOLUME_PLUS_PRICE_ACTION_PRE_BOARD",
            "future_pass": "SYMBOL_SETUP_DIRECTIONAL_VOLUME_MECHANISM_SUPPORTED_V1",
            "future_fail": "SYMBOL_SETUP_DIRECTIONAL_VOLUME_MECHANISM_NOT_SUPPORTED_V1",
            "future_support_fail": "SYMBOL_SETUP_DIRECTIONAL_VOLUME_MECHANISM_INSUFFICIENT_SUPPORT_V1",
        },
        "no_optimization": {
            "threshold_searched": False,
            "ratio_grid": False,
            "range_break_combined": False,
            "up_down_candidate": False,
            "MA_CHANGED": False,
            "BB_CHANGED": False,
            "RCI_CHANGED": False,
            "PRICE_ACTION_CHANGED": False,
            "BOARD_CHANGED": False,
            "ENTRY_EXECUTION_CHANGED": False,
            "EXIT_CHANGED": False,
            "PORTFOLIO_CHANGED": False,
            "UNIVERSE_CHANGED": False,
            "POSITION_SIZE_CHANGED": False,
            "COST_CHANGED": False,
            "CONTEXT_OR_PB1_ADDED": False,
        },
    }


def main() -> int:
    _self_check()
    before = identity()
    failed = json.loads((NATIVE / "results/research/symbol_setup_baseline_complete_strategy_development_v1/report.json").read_text(encoding="utf-8"))
    closed = json.loads((NATIVE / "results/research/symbol_setup_pullback_range_break_mechanism_v1/report.json").read_text(encoding="utf-8"))
    primary = failed["primary"]
    parent = {
        "complete_strategy_id": COMPLETE_STRATEGY_ID,
        "complete_strategy_sha256": failed.get("complete_strategy_sha256"),
        "identity_before_ok": bool(before["ok"]),
        "sha_match": failed.get("complete_strategy_sha256") == COMPLETE_STRATEGY_SHA256,
        "trade_n": primary.get("trade_n") == 7,
        "net_pnl_yen": _close(primary.get("net_pnl_yen"), -31500.0),
        "pf": _close(primary.get("PF"), 0.46153846153846156),
        "primary_deficiency": PRIMARY_DEFICIENCY,
    }
    prior = {
        "RANGE_BREAK_REPAIR_ID": RANGE_BREAK_REPAIR_ID,
        "RANGE_BREAK_REPAIR_STATUS": RANGE_BREAK_STATUS,
        "published_verdict": closed.get("verdict"),
        "closed": closed.get("verdict") == "SYMBOL_SETUP_PULLBACK_RANGE_BREAK_MECHANISM_NOT_SUPPORTED_V1",
        "RANGE_BREAK_COMBINED_WITH_NEW_REPAIR": False,
        "price_action_restored": "close[t] > EMA9[t] AND close[t] > high[t-1] AND close[t] <= BB_upper[t]",
    }
    if not (all(bool(parent[k]) for k in ("identity_before_ok", "sha_match", "trade_n", "net_pnl_yen", "pf")) and prior["closed"] and semantics()["resolved"]):
        verdict = VERDICT_CLOSED if not prior["closed"] or not parent["sha_match"] else VERDICT_UNRESOLVED
        nxt = NEXT_STOP if verdict == VERDICT_CLOSED else NEXT_STOP_REPAIR
        publish(_shell(verdict, nxt, parent=parent, prior_repair=prior, repair_ready=False))
        print(json.dumps({"VERDICT": verdict, "NEXT": nxt}), flush=True)
        return 2
    pre = json.loads((NATIVE / "results/research/symbol_setup_baseline_complete_strategy_precommit_v1/report.json").read_text(encoding="utf-8"))
    try:
        scanned = scan(before["universe_sessions"], _groups(pre))
    except PopulationMismatch as exc:
        publish(_shell(VERDICT_PARITY, NEXT_STOP, parent=parent, prior_repair=prior, repair_ready=False, analysis={"parity": exc.detail}))
        print(json.dumps({"VERDICT": VERDICT_PARITY, "NEXT": NEXT_STOP}, default=str), flush=True)
        return 2
    except ContractMismatch as exc:
        publish(_shell(VERDICT_UNRESOLVED, NEXT_STOP_REPAIR, parent=parent, prior_repair=prior, repair_ready=False, analysis={"contract_mismatch": exc.detail}))
        print(json.dumps({"VERDICT": VERDICT_UNRESOLVED, "NEXT": NEXT_STOP_REPAIR}, default=str), flush=True)
        return 2
    after = identity()
    parent["identity_after_ok"] = bool(after["ok"])
    if not after["ok"]:
        publish(_shell(VERDICT_CLOSED, NEXT_STOP, parent=parent, prior_repair=prior, repair_ready=False))
        print(json.dumps({"VERDICT": VERDICT_CLOSED, "NEXT": NEXT_STOP}), flush=True)
        return 2
    analysis = analyze(scanned)
    report = _shell(analysis["verdict"], analysis["next"], parent=parent, prior_repair=prior, repair_ready=bool(analysis["repair_ready"]), analysis=analysis)
    publish(report)
    print(
        json.dumps(
            {
                "VERDICT": analysis["verdict"],
                "NEXT": analysis["next"],
                "SHA": report["repair_spec_sha256"],
                "CLASS_FRAC": (analysis.get("reconciliation") or {}).get("classification_fraction"),
                "READY": analysis["repair_ready"],
            }
        ),
        flush=True,
    )
    return 0 if analysis["repair_ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
