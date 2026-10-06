"""Run the precommitted directional-volume research. Do not run a complete strategy."""
from __future__ import annotations

import hashlib
import json
import math

from research.simple_tech_entry_family.bars import SymbolBarBuilder
from research.symbol_setup_baseline_complete_development.verify import identity
from research.symbol_setup_directional_volume_mechanism import (
    ANALYSIS_ID,
    COMPLETE_STRATEGY_ID,
    COMPLETE_STRATEGY_SHA256,
    NEXT_STOP,
    NEXT_STOP_REPAIR,
    PRIMARY_DEFICIENCY,
    REPAIR_ID,
    REPAIR_SPEC_SHA256,
    VERDICT_CLOSED,
    VERDICT_PARITY,
)
from research.symbol_setup_directional_volume_mechanism.decide import analyze
from research.symbol_setup_directional_volume_mechanism.isolation import NATIVE
from research.symbol_setup_directional_volume_mechanism.publish import publish
from research.symbol_setup_directional_volume_mechanism.scan import ContractMismatch, LockedBuilder, PopulationMismatch, scan
from research.symbol_setup_directional_volume_precommit.semantics import repair_spec
from research.symbol_setup_failure_evidence_gap.scan import PriceParityError


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
        fold = next(name for name, days in folds.items() if day in days)
        out[str(day)] = {"lineage": "ORIGINAL18" if day in original else "EXTENSION17", "fold": fold}
    return out


def _self_check() -> None:
    if _sha(repair_spec()) != REPAIR_SPEC_SHA256:
        raise RuntimeError("repair_sha")
    start = 1_784_000_000.0
    end = start + 3600.0
    builder = LockedBuilder(am_start=start, am_end=end)
    plain = SymbolBarBuilder(am_start=start, am_end=end)
    events = [
        (start + 1, 100.0, 10.0, 99.0, 101.0),
        (start + 2, 100.0, 20.0, 100.0, 100.0),
        (start + 60, 100.0, 20.0, 100.0, 101.0),
    ]
    for et, px, cum, bid, ask in events:
        kwargs = {"et": et, "px": px, "cum_vol": cum, "bid": bid, "ask": ask, "continuous": True}
        builder.on_event(**kwargs)
        plain.on_event(**kwargs)
    builder.close_session()
    plain.close_session()
    if abs(float(builder.as_arrays()["ask_vol"].sum()) - float(plain.as_arrays()["ask_vol"].sum())) > 1e-9:
        raise RuntimeError("builder_drift")
    if len(builder.locked_bars) != 1 or builder.locked_bars[0]["vol"] != 10.0 or builder.locked_bars[0]["n"] != 1.0:
        raise RuntimeError("locked_self_check")


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
        "EXCHANGE_AGGRESSOR_FLAG": False,
        "signed_volume_wording": "ASK_CLASSIFIED_VOLUME / BID_CLASSIFIED_VOLUME",
        "RANGE_BREAK_COMBINED": False,
        "historical_verdicts_rewritten": False,
        "NEW_COMPLETE_STRATEGY_RUN": False,
        "NEW_PNL_RUN": False,
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
        "threshold_searched": False,
        "up_down_candidate_defined": False,
        "research_only": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "prospective_data_opened": False,
        "prospective_rows_read": 0,
        "identity": extra.get("identity") or {},
        "analysis": extra.get("analysis") or {},
        "repaired_signals": extra.get("repaired_signals") or [],
        "no_pnl": {"NEW_COMPLETE_STRATEGY_RUN": False, "NEW_PNL_RUN": False},
    }


def main() -> int:
    _self_check()
    before = identity()
    failed = json.loads((NATIVE / "results/research/symbol_setup_baseline_complete_strategy_development_v1/report.json").read_text(encoding="utf-8"))
    closed = json.loads((NATIVE / "results/research/symbol_setup_pullback_range_break_mechanism_v1/report.json").read_text(encoding="utf-8"))
    primary = failed["primary"]
    ident = {
        "identity_before_ok": bool(before["ok"]),
        "repair_spec_match": _sha(repair_spec()) == REPAIR_SPEC_SHA256,
        "strategy_sha_match": failed.get("complete_strategy_sha256") == COMPLETE_STRATEGY_SHA256,
        "trade_n": primary.get("trade_n") == 7,
        "net_pnl_yen": _close(primary.get("net_pnl_yen"), -31500.0),
        "pf": _close(primary.get("PF"), 0.46153846153846156),
        "range_break_closed": closed.get("verdict") == "SYMBOL_SETUP_PULLBACK_RANGE_BREAK_MECHANISM_NOT_SUPPORTED_V1",
        "RANGE_BREAK_COMBINED": False,
    }
    if not all(bool(ident[k]) for k in ident if k != "RANGE_BREAK_COMBINED"):
        publish(_shell(VERDICT_CLOSED, NEXT_STOP, identity=ident))
        print(json.dumps({"VERDICT": VERDICT_CLOSED, "NEXT": NEXT_STOP}), flush=True)
        return 2
    pre = json.loads((NATIVE / "results/research/symbol_setup_baseline_complete_strategy_precommit_v1/report.json").read_text(encoding="utf-8"))
    gap = json.loads((NATIVE / "results/research/symbol_setup_failure_evidence_gap_v1/report.json").read_text(encoding="utf-8"))
    try:
        scanned = scan(before["universe_sessions"], _groups(pre))
    except PopulationMismatch as exc:
        publish(_shell(VERDICT_PARITY, NEXT_STOP, identity=ident, analysis={"parity": exc.detail}))
        print(json.dumps({"VERDICT": VERDICT_PARITY, "NEXT": NEXT_STOP}, default=str), flush=True)
        return 2
    except (PriceParityError, ContractMismatch) as exc:
        detail = getattr(exc, "detail", {"error": str(exc)})
        publish(_shell(VERDICT_CLOSED, NEXT_STOP, identity=ident, analysis={"mismatch": detail}))
        print(json.dumps({"VERDICT": VERDICT_CLOSED, "NEXT": NEXT_STOP}, default=str), flush=True)
        return 2
    after = identity()
    ident["identity_after_ok"] = bool(after["ok"])
    if not after["ok"]:
        publish(_shell(VERDICT_CLOSED, NEXT_STOP, identity=ident))
        print(json.dumps({"VERDICT": VERDICT_CLOSED, "NEXT": NEXT_STOP}), flush=True)
        return 2
    analysis = analyze(scanned, gap["analysis"]["tables"]["ALL_TECHNICAL_SIGNALS"])
    repaired = [row for row in scanned["volume_rows"] if row.get("buy_volume_dominant") and row.get("price_action_pass")]
    publish(_shell(analysis["verdict"], analysis["next"], identity=ident, analysis=analysis, repaired_signals=repaired))
    cell = next(row for row in analysis["pre_board"] if int(row["horizon_sec"]) == 180)
    print(
        json.dumps(
            {
                "VERDICT": analysis["verdict"],
                "NEXT": analysis["next"],
                "N": analysis["repaired_pre_board_n"],
                "DOM": analysis["buy_volume_dominant_n"],
                "RAW180": cell["raw_mid_median"],
                "BID180": cell["bid_anchor_median"],
                "LABEL": analysis["interpretation"],
            }
        ),
        flush=True,
    )
    return 0 if analysis["verdict"] == "SYMBOL_SETUP_DIRECTIONAL_VOLUME_MECHANISM_SUPPORTED_V1" else 2


if __name__ == "__main__":
    raise SystemExit(main())
