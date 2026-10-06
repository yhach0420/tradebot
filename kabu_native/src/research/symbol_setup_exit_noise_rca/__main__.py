"""Run the slope-loss noise diagnosis. Do not rerun the complete strategy portfolio."""
from __future__ import annotations

import json
import math

from research.symbol_setup_baseline_complete_development.verify import identity
from research.symbol_setup_baseline_complete_strategy_precommit.runner import loss_reason, thesis_live
from research.symbol_setup_exit_noise_rca import (
    ANALYSIS_ID,
    COMPLETE_STRATEGY_ID,
    COMPLETE_STRATEGY_SHA256,
    EXIT_ID,
    EXIT_SHA256,
    NEXT_STOP,
    VERDICT_CLOSED,
    VERDICT_PARITY,
)
from research.symbol_setup_exit_noise_rca.decide import analyze
from research.symbol_setup_exit_noise_rca.isolation import NATIVE
from research.symbol_setup_exit_noise_rca.publish import publish
from research.symbol_setup_exit_noise_rca.scan import PopulationMismatch, scan
from research.symbol_setup_exit_noise_rca.state import classify_slope


def _close(got: object, exp: float) -> bool:
    try:
        return math.isclose(float(got), float(exp), rel_tol=0.0, abs_tol=1e-9)
    except (TypeError, ValueError):
        return False


def _self_check() -> None:
    if not thesis_live(10, 9, 8):
        raise RuntimeError("live")
    if loss_reason(10, 9, 9.1) != "SLOW_TREND_SLOPE_LOSS":
        raise RuntimeError("slope")
    if loss_reason(8, 9, 8) != "FAST_SLOW_CROSS_LOSS":
        raise RuntimeError("cross")
    if loss_reason(8, 9, 9.5) != "BOTH_TREND_COMPONENTS_LOST":
        raise RuntimeError("both")
    live = {"thesis_live": True, "fast_slow_relation_live": True, "bid": 101.0}
    dead = {"thesis_live": False, "fast_slow_relation_live": False, "bid": 99.0}
    held = {"thesis_live": False, "fast_slow_relation_live": True, "bid": 101.0}
    if classify_slope([live, dead, dead], 100.0)["label"] != "TEMPORARY_AND_PRICE_RECOVERED":
        raise RuntimeError("price")
    if classify_slope([held, held, held], 100.0)["label"] != "AMBIGUOUS_SLOPE_FAILURE":
        raise RuntimeError("ambiguous")
    if classify_slope([held, dead, held], 100.0)["label"] != "TERMINAL_SLOPE_FAILURE":
        raise RuntimeError("terminal")


def _shell(verdict: str, nxt: str, **extra: object) -> dict:
    return {
        "analysis_id": ANALYSIS_ID,
        "verdict": verdict,
        "next": nxt,
        "complete_strategy_id": COMPLETE_STRATEGY_ID,
        "complete_strategy_sha256": COMPLETE_STRATEGY_SHA256,
        "exit_id": EXIT_ID,
        "exit_sha256": EXIT_SHA256,
        "primary_deficiency": "SYMBOL_SETUP_EDGE_NOT_ESTABLISHED",
        "EXIT_CHANGED": False,
        "PERSISTENCE_CHANGED": False,
        "TIMEFRAME_CHANGED": False,
        "ENTRY_CHANGED": False,
        "RANGE_BREAK_REPAIR_USED": False,
        "DIRECTIONAL_VOLUME_REPAIR_USED": False,
        "NEW_COMPLETE_STRATEGY_RUN": False,
        "NEW_PNL_RUN": False,
        "K_SELECTED": False,
        "historical_verdicts_rewritten": False,
        "research_only": True,
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "prospective_data_opened": False,
        "prospective_rows_read": 0,
        "prior_context": {
            "v27": "PERSISTENCE::3m::A_EMA_STRUCTURE_LOSS remains MECHANISM_DISCOVERY_ONLY. It is not current policy.",
            "v28": "3m EMA persistence K=6 remains DIAGNOSTIC_EVIDENCE_ONLY and was not adopted. It is not current policy.",
            "current_rca": "1-minute completed bars and the full current thesis, with slope-only loss as the focus.",
        },
        "no_repair": {
            "EXIT_CHANGED": False,
            "PERSISTENCE_CHANGED": False,
            "TIMEFRAME_CHANGED": False,
            "ENTRY_CHANGED": False,
            "K_SELECTED": False,
            "NEW_COMPLETE_STRATEGY_RUN": False,
            "NEW_PNL_RUN": False,
        },
        "identity": extra.get("identity") or {},
        "parity": extra.get("parity") or {},
        "analysis": extra.get("analysis") or {},
        "episodes": extra.get("episodes") or [],
    }


def main() -> int:
    _self_check()
    before = identity()
    failed = json.loads((NATIVE / "results/research/symbol_setup_baseline_complete_strategy_development_v1/report.json").read_text(encoding="utf-8"))
    primary = failed["primary"]
    ident = {
        "identity_before_ok": bool(before["ok"]),
        "strategy_sha_match": failed.get("complete_strategy_sha256") == COMPLETE_STRATEGY_SHA256,
        "exit_sha_match": EXIT_SHA256 == "5962da204396b9f578dfa19becbbd39f88c30c71a8b768c4b1a1c7d232115e1b",
        "trade_n": primary.get("trade_n") == 7,
        "net_pnl_yen": _close(primary.get("net_pnl_yen"), -31500.0),
        "pf": _close(primary.get("PF"), 0.46153846153846156),
    }
    if not all(ident.values()):
        publish(_shell(VERDICT_CLOSED, NEXT_STOP, identity=ident))
        print(json.dumps({"VERDICT": VERDICT_CLOSED, "NEXT": NEXT_STOP}), flush=True)
        return 2
    try:
        scanned = scan(before["universe_sessions"])
    except PopulationMismatch as exc:
        publish(_shell(VERDICT_PARITY, NEXT_STOP, identity=ident, parity={"ok": False, "detail": exc.detail}))
        print(json.dumps({"VERDICT": VERDICT_PARITY, "NEXT": NEXT_STOP}, default=str), flush=True)
        return 2
    after = identity()
    ident["identity_after_ok"] = bool(after["ok"])
    if not after["ok"]:
        publish(_shell(VERDICT_CLOSED, NEXT_STOP, identity=ident))
        print(json.dumps({"VERDICT": VERDICT_CLOSED, "NEXT": NEXT_STOP}), flush=True)
        return 2
    analysis = analyze(scanned)
    parity = {"technical": scanned["technical"], "board_pass": scanned["board"], "fill": scanned["fill"], "ok": True}
    publish(_shell(analysis["verdict"], analysis["next"], identity=ident, parity=parity, analysis=analysis, episodes=scanned["episodes"]))
    print(
        json.dumps(
            {
                "VERDICT": analysis["verdict"],
                "NEXT": analysis["next"],
                "SLOPE": analysis["slope_episode_n"],
                "TEMP": analysis["temporary_n"],
                "PRICE": analysis["price_recovered_n"],
                "HYP": analysis["k1_noise_hypothesis"],
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
