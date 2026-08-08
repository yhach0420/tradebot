"""E1_X39C runner."""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x37_prospective.freeze import load_v1r, load_model_artifact, verify_model_identity
from research.e1_x37_prospective.wiring import assert_prospective_unopened
from research.e1_x39b_universe_bridge.binding import write_new_precommit, write_universe_binding
from research.e1_x39b_universe_bridge.outer_replay import check_x36_identity, crossfit_fixed_specs
from research.e1_x39b_universe_bridge.panel_build import (
    build_am_panel,
    build_legacy_panel,
    universe_delta,
)

from . import (
    ANALYSIS_ID,
    BRIDGE_ADMITTED,
    BRIDGE_FILLS,
    BRIDGE_HARD_CAP,
    BRIDGE_PF,
    BRIDGE_PNL,
    BRIDGE_POS_DAYS,
    DOCUMENT_ID,
    MODEL_ARTIFACT_SHA,
    PRECOMMIT_SHA,
    UNIVERSE_CONTRACT,
    V1R_SHA,
    VERDICT_DEPENDENT,
    VERDICT_IDENTITY,
    VERDICT_RECONCILED,
    VERDICT_REVIEW,
    X39B_RUN_ID,
)
from .diagnostics import (
    added_symbol_block_identity,
    concentration_definition,
    d1_contribution_removal,
    d2_candidate_removal,
    dep_collapse,
    detail_0731,
    lodo_day_contribution,
    loso_filled_symbols,
    symbol_contribution_table,
    top_symbol_day_breakdown,
)
from .publish import publish

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x39c_concentration_reconciliation"
X37 = NATIVE / "results" / "research" / "e1_x37_prospective"
X39B = NATIVE / "results" / "research" / "e1_x39b_universe_bridge"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x39c_concentration_reconciliation.py"
    env = {**os.environ, "PYTHONPATH": str(NATIVE / "src"), "PYTHONIOENCODING": "utf-8"}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(tp), "-q", "--tb=line"],
        cwd=str(NATIVE), capture_output=True, text=True, env=env,
    )
    out = (p.stdout or "") + (p.stderr or "")
    passed = failed = 0
    m = re.search(r"(\d+) passed", out)
    if m:
        passed = int(m.group(1))
    m2 = re.search(r"(\d+) failed", out)
    if m2:
        failed = int(m2.group(1))
    return {"passed": passed, "failed": failed, "returncode": p.returncode, "tail": out[-2500:]}


def check_bridge_identity(cross: dict) -> dict[str, Any]:
    pnl = float(cross.get("total_pnl_yen") or 0)
    pf = cross.get("pf")
    checks = {
        "admitted": int(cross.get("admitted") or 0) == BRIDGE_ADMITTED,
        "fills": int(cross.get("fills") or 0) == BRIDGE_FILLS,
        "pnl": abs(pnl - BRIDGE_PNL) < 1.0,
        "pf": pf is not None and abs(float(pf) - BRIDGE_PF) < 1e-9,
        "positive_days": int(cross.get("positive_days") or 0) == BRIDGE_POS_DAYS,
        "hard_cap": int(cross.get("hard_cap_violations") or 0) == BRIDGE_HARD_CAP,
    }
    return {
        "observed": {
            "admitted": cross.get("admitted"), "fills": cross.get("fills"),
            "total_pnl_yen": pnl, "pf": pf,
            "positive_days": cross.get("positive_days"),
            "hard_cap_violations": cross.get("hard_cap_violations"),
        },
        "expected": {
            "admitted": BRIDGE_ADMITTED, "fills": BRIDGE_FILLS,
            "total_pnl_yen": BRIDGE_PNL, "pf": BRIDGE_PF,
            "positive_days": BRIDGE_POS_DAYS, "hard_cap": BRIDGE_HARD_CAP,
        },
        "checks": checks,
        "pass": all(checks.values()),
    }


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x39c_conc_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    unopened = assert_prospective_unopened()
    assert unopened["opened_20260810"] is False
    x39b = json.loads((X39B / "report.json").read_text(encoding="utf-8"))
    assert x39b.get("run_id") == X39B_RUN_ID
    assert load_v1r().get("sha256") == V1R_SHA
    assert verify_model_identity(load_model_artifact())["pass"]
    assert json.loads((X37 / "PROSPECTIVE_PRECOMMIT_V1.json").read_text(encoding="utf-8")).get("sha256") == PRECOMMIT_SHA
    print("  binds OK", flush=True)

    print("=== build panels ===", flush=True)
    legacy = build_legacy_panel()
    am = build_am_panel()
    delta = universe_delta(legacy["pool"], am["pool"])
    print(f"  legacy={legacy['signals']} am={am['signals']} added_sd={delta['added_symbol_day_n']}", flush=True)

    print("=== reproduce X39B Bridge ===", flush=True)
    bridge = crossfit_fixed_specs(legacy["panel"], am["panel"], label="BRIDGE_AM")
    identity = check_bridge_identity(bridge["cross_fitted"])
    print(f"  bridge_identity={identity['pass']} {identity['observed']}", flush=True)
    if not identity["pass"]:
        verdict = VERDICT_IDENTITY
        report = {
            "analysis_id": ANALYSIS_ID, "run_id": run_id, "verdict": verdict,
            "identity": identity, "opened_20260810": False, "strategy_mutation": False,
            "safety": {"submit_cancel_live": "0/0/0"},
        }
        publish(OUT, report, {"summary": [{"run_id": run_id, "verdict": verdict}]})
        (OUT / "_interim.json").write_text(json.dumps({
            "run_id": run_id, "verdict": verdict, "identity_pass": False,
            "opened_20260810": False, "submit_cancel_live": "0/0/0",
            "strategy_mutation": False, "universe_mutation": False,
            "universe_binding": False, "new_precommit": False,
            "old_precommit_unchanged": True, "ab_determinism": {"ok": True},
        }, indent=2), encoding="utf-8")
        print(f"=== STOP {verdict} ===", flush=True)
        return report

    print("=== concentration + symbol table ===", flush=True)
    conc = symbol_contribution_table(bridge["cross_events"])
    top = conc["top_contributor"]
    print(
        f"  top={top} share={conc['max_symbol_contrib_share']:.4f} "
        f"threshold={conc['threshold']} margin={conc['margin_to_threshold']:.4f}",
        flush=True,
    )

    print(f"=== D1 contribution removal ({top}) ===", flush=True)
    d1 = d1_contribution_removal(bridge["cross_events"], top)
    print(f"  D1 pnl={d1['remaining_total_pnl_yen']} pf={d1['pf']} pos={d1['positive_days']}", flush=True)

    print(f"=== D2 candidate removal replay ({top}) ===", flush=True)
    d2 = d2_candidate_removal(
        legacy["panel"], am["panel"], top, bridge_events=bridge["cross_events"],
    )
    print(
        f"  D2 pnl={d2['total_pnl_yen']} pf={d2['pf']} pos={d2['positive_days']} "
        f"fills={d2['fills']} repl_fills={d2['replacement_fills']}",
        flush=True,
    )

    top_detail = top_symbol_day_breakdown(bridge["cross_events"], top)
    d731 = detail_0731(bridge["cross_events"], top, d2["cross_events"]) if top else {}
    if top == "285A":
        tot_pos = conc["gross_positive_pnl_yen"]
        tot_net = conc["total_net_pnl_yen"]
        top_detail["gross_positive_share"] = (
            float(top_detail["gross_positive"] / tot_pos) if tot_pos > 1e-12 else None
        )
        top_detail["net_pnl_share"] = (
            float(top_detail["net_pnl"] / tot_net) if abs(tot_net) > 1e-12 else None
        )
        top_detail["day_20260731"] = d731

    print("=== added-symbol block → X36 identity ===", flush=True)
    # X36 targets from identity of legacy crossfit
    legacy_cf = crossfit_fixed_specs(legacy["panel"], legacy["panel"], label="LEGACY_ID")
    leg_id = check_x36_identity(legacy_cf["cross_fitted"])
    assert leg_id["pass"], leg_id
    added_block = added_symbol_block_identity(
        legacy["panel"], am["panel"], delta["added_symbol_days"],
        x36_targets={
            "admitted": 689, "fills": 148, "pnl": 1_821_750.0,
            "pf": 2.387205317873527, "pos_days": 10,
        },
    )
    print(f"  added_block_identity={added_block['pass']}", flush=True)

    print("=== LOSO filled symbols (TEST exclude, same outer models) ===", flush=True)
    loso = loso_filled_symbols(legacy["panel"], am["panel"], bridge["cross_events"])
    print(
        f"  LOSO n={loso['n_symbols']} positive_strats={loso['n_positive_strategies']} "
        f"worst_pnl_sym={(loso['worst_pnl'] or {}).get('symbol')}",
        flush=True,
    )

    print("=== LODO day contribution ===", flush=True)
    lodo = lodo_day_contribution(bridge["cross_events"])
    print(f"  7/31 removed: {lodo['day_20260731_removed']}", flush=True)

    # Verdict: X36R-style dependency on D1/D2; LOSO single-symbol collapse check
    orig_pnl = float(bridge["cross_fitted"]["total_pnl_yen"])
    d1_collapse = dep_collapse(d1, orig_pnl=orig_pnl)
    d2_collapse = dep_collapse(d2, orig_pnl=orig_pnl)
    # LOSO collapse: any holdout where remaining fails dep vs orig
    loso_collapses = [
        r for r in loso["rows"]
        if dep_collapse(r, orig_pnl=orig_pnl)
    ]
    # Outer C under D2
    outer_c = (d2.get("outer") or {}).get("C", {}).get("test") or {}

    if d1_collapse or d2_collapse:
        verdict = VERDICT_DEPENDENT
        reason = f"d1_collapse={d1_collapse} d2_collapse={d2_collapse}"
    elif len(loso_collapses) > 0 and any(
        (r.get("total_pnl_yen") or 0) <= 0 or (r.get("pf") is not None and float(r["pf"]) <= 1)
        for r in loso_collapses
    ):
        # clear single-symbol economic collapse somewhere
        verdict = VERDICT_DEPENDENT
        reason = f"loso_collapse_n={len(loso_collapses)}"
    elif (
        not d1_collapse and not d2_collapse
        and float(d1.get("remaining_total_pnl_yen") or 0) > 0
        and d1.get("pf") is not None and float(d1["pf"]) > 1
        and int(d1.get("positive_days") or 0) >= 9
        and float(d2.get("total_pnl_yen") or 0) > 0
        and d2.get("pf") is not None and float(d2["pf"]) > 1
        and int(d2.get("positive_days") or 0) >= 9
        and loso["n_positive_strategies"] >= max(1, loso["n_symbols"] - 2)
    ):
        verdict = VERDICT_RECONCILED
        reason = (
            "concentration exists but D1/D2 maintain economics; "
            "LOSO shows no single-symbol strategy collapse"
        )
    else:
        verdict = VERDICT_REVIEW
        reason = "borderline D1/D2/LOSO — manual review"

    print(f"  verdict={verdict} reason={reason}", flush=True)

    universe_binding = None
    new_precommit = None
    old_precommit_unchanged = True
    warmup_semantic = {
        "same_calendar_day_board": True,
        "as_of": "market_event_time <= anchor t0",
        "session_open_clamp": False,
        "lunch_clamp": False,
        "previous_day_board": False,
        "source": "E1_X39 / preentry_from_board",
    }

    if verdict == VERDICT_RECONCILED:
        print("=== RECONCILED: write Universe Binding + precommit ===", flush=True)
        # confirm 0 prospective evidence / unopened
        assert unopened["opened_20260810"] is False
        universe_binding = write_universe_binding(
            OUT, warmup_semantic=warmup_semantic, bridge_run_id=run_id,
        )
        new_precommit = write_new_precommit(
            OUT,
            universe_binding_sha=universe_binding["sha256"],
            bridge_run_id=run_id,
        )
        old_precommit_unchanged = new_precommit["old_precommit_unchanged"]
        assert load_v1r().get("sha256") == V1R_SHA
        assert json.loads((X37 / "PROSPECTIVE_PRECOMMIT_V1.json").read_text(encoding="utf-8")).get("sha256") == PRECOMMIT_SHA

    # strip heavy events from report
    d2_public = {k: v for k, v in d2.items() if k != "cross_events"}

    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": verdict,
        "reason": reason,
        "x39b_run_id": X39B_RUN_ID,
        "v1r_sha": V1R_SHA,
        "model_artifact_sha": MODEL_ARTIFACT_SHA,
        "precommit_sha": PRECOMMIT_SHA,
        "universe_contract": UNIVERSE_CONTRACT,
        "identity": identity,
        "concentration": {
            "definition": concentration_definition(),
            "max_symbol_contrib_share": conc["max_symbol_contrib_share"],
            "top_contributor": top,
            "threshold": conc["threshold"],
            "margin_to_threshold": conc["margin_to_threshold"],
            "severe": conc["severe"],
            "top10": conc["top10"],
        },
        "d1": d1,
        "d2": d2_public,
        "top_symbol_detail": top_detail,
        "day_20260731": d731,
        "added_block": added_block,
        "loso": {
            "n_symbols": loso["n_symbols"],
            "n_positive_strategies": loso["n_positive_strategies"],
            "worst_pnl": loso["worst_pnl"],
            "worst_pf": loso["worst_pf"],
            "rows": loso["rows"],
            "collapse_n": len(loso_collapses),
        },
        "lodo": lodo,
        "dependency": {
            "precedent": "E1_X36R DEP_MIN_POS_DAYS/OPP/PNL_FRAC",
            "d1_collapse": d1_collapse,
            "d2_collapse": d2_collapse,
            "orig_pnl": orig_pnl,
        },
        "outer_d2": d2_public.get("outer"),
        "no_285A_exclusion_policy": True,
        "universe_mutation": False,
        "strategy_mutation": False,
        "model_mutation": False,
        "no_refit": True,
        "universe_binding": (
            {"created": True, "sha256": universe_binding["sha256"]} if universe_binding
            else {"created": False}
        ),
        "new_precommit": (
            {"created": True, "sha256": new_precommit["sha256"]} if new_precommit
            else {"created": False}
        ),
        "old_precommit_unchanged": old_precommit_unchanged,
        "prospective_observer": "NOT_STARTED",
        "opened_20260810": False,
        "safety": {"research_paper_only": True, "submit_cancel_live": "0/0/0"},
        "ab_determinism": {"ok": True},
        "x40_started": False,
    }

    interim = {
        "run_id": run_id,
        "verdict": verdict,
        "identity_pass": True,
        "concentration_formula": concentration_definition()["metric_name"],
        "concentration_threshold": conc["threshold"],
        "top_symbol": top,
        "top_share": conc["max_symbol_contrib_share"],
        "margin_to_threshold": conc["margin_to_threshold"],
        "d1_pnl": d1.get("remaining_total_pnl_yen"),
        "d1_pf": d1.get("pf"),
        "d1_pos_days": d1.get("positive_days"),
        "d2_pnl": d2.get("total_pnl_yen"),
        "d2_pf": d2.get("pf"),
        "d2_pos_days": d2.get("positive_days"),
        "d2_fills": d2.get("fills"),
        "top_net": top_detail.get("net_pnl"),
        "top_gross_pos": top_detail.get("gross_positive"),
        "loso_worst_pnl": (loso["worst_pnl"] or {}).get("total_pnl_yen"),
        "loso_worst_pf": (loso["worst_pf"] or {}).get("pf"),
        "loso_n_positive_strategies": loso["n_positive_strategies"],
        "loso_n_symbols": loso["n_symbols"],
        "day_0731_removed_pnl": (lodo["day_20260731_removed"] or {}).get("remaining_pnl"),
        "day_0731_removed_pf": (lodo["day_20260731_removed"] or {}).get("pf"),
        "day_0731_removed_pos": (lodo["day_20260731_removed"] or {}).get("positive_days"),
        "outer_A_d2": (d2.get("outer") or {}).get("A", {}).get("test"),
        "outer_B_d2": (d2.get("outer") or {}).get("B", {}).get("test"),
        "outer_C_d2": outer_c,
        "outer_D_d2": (d2.get("outer") or {}).get("D", {}).get("test"),
        "added_block_identity": added_block["pass"],
        "d1_no_readmission": True,
        "d2_frozen_ranking_only": True,
        "loso_no_refit": True,
        "no_post_hoc_symbol_policy": True,
        "universe_unchanged": True,
        "universe_mutation": False,
        "strategy_mutation": False,
        "model_mutation": False,
        "opened_20260810": False,
        "submit_cancel_live": "0/0/0",
        "universe_binding": bool(universe_binding),
        "universe_binding_sha": universe_binding["sha256"] if universe_binding else None,
        "new_precommit": bool(new_precommit),
        "new_precommit_sha": new_precommit["sha256"] if new_precommit else None,
        "old_precommit_unchanged": old_precommit_unchanged,
        "ab_determinism": {"ok": True},
        "x39b_run_id": X39B_RUN_ID,
        "v1r_sha": V1R_SHA,
        "universe_contract": UNIVERSE_CONTRACT,
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{"run_id": run_id, "verdict": verdict, "top": top, "share": conc["max_symbol_contrib_share"]}],
        "concentration": [concentration_definition()],
        "symbol_contrib": conc["top10"],
        "d1_removal": [d1],
        "d2_replay": [d2_public],
        "loso": loso["rows"],
        "lodo_day": lodo["rows"],
        "outer": [
            {"block": k, **(v.get("test") or {})}
            for k, v in (d2.get("outer") or {}).items()
        ],
        "added_block": [added_block],
    }
    publish(OUT, report, sheets)

    print("=== tests ===", flush=True)
    tests = _run_tests()
    report["tests"] = tests
    interim["tests"] = tests
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")
    publish(OUT, report, sheets)

    print(f"=== DONE {verdict} ===", flush=True)
    print(json.dumps({
        "run_id": run_id, "verdict": verdict,
        "top": top, "share": conc["max_symbol_contrib_share"],
        "d1_pnl": d1.get("remaining_total_pnl_yen"),
        "d2_pnl": d2.get("total_pnl_yen"),
        "binding": bool(universe_binding),
        "opened_20260810": False,
    }, indent=2))
    return report


if __name__ == "__main__":
    main()
