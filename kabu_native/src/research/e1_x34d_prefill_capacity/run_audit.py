"""E1_X34D runner — pre-fill hard capacity admission (research/paper only)."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x28_executable_joint.board import verify_board_mapping
from research.e1_x31_population_direction.identity import ab_identity, reproduce_population
from research.e1_x32_upstream_attribution.eval_stages import load_boards_for_symbols
from research.e1_x33b_neutral_anchor.neutral import (
    candidate_symbols_by_day,
    planned_neutral_anchors,
)
from research.e1_x34c_passive_deployability.capacity import simulate_capacity
from research.e1_x34c_passive_deployability.events import build_events
from research.e1_x34c_passive_deployability.metrics import summarize_mode as x34c_summarize

from . import (
    ANALYSIS_ID,
    ANCHOR_SHA,
    BOARD_MAPPING_SHA,
    DOCUMENT_ID,
    ENTRY_SHA,
    EXEC_SHA,
    EXPECTED_C1_ACCEPTED,
    EXPECTED_C1_OPP600,
    EXPECTED_SIGNALS,
    EXPECTED_U0_OPP600,
    FORBIDDEN_FROM,
    NEXT_FAIL,
    NEXT_PASS,
    OCCUPANCY_PROXY_600S,
    ORDER_ASC,
    ORDER_DESC,
    ORDER_HASH,
    POSITION_CAP,
    SOURCE_X34C_RUN,
    WAIT_SEC,
    X34C_QUALIFICATION,
)
from .admission import simulate_prefill
from .metrics import (
    capacity_drag,
    day_table,
    lodo_prefill,
    loso_prefill,
    ordering_sensitivity,
    summarize_prefill,
)
from .publish import publish
from .verdict import decide_verdict, freeze_admission_manifest

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x34d_prefill_capacity"
X33B = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"
X34A = NATIVE / "results" / "research" / "e1_x34a_execution_policy"
X34C = NATIVE / "results" / "research" / "e1_x34c_passive_deployability"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x34d_prefill_capacity.py"
    env = {**os.environ, "PYTHONPATH": str(NATIVE / "src")}
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
    return {"passed": passed, "failed": failed, "returncode": p.returncode, "tail": out[-2000:]}


def _verify_sources() -> dict[str, Any]:
    entry = json.loads((X34C / "PASSIVE_FILL_ENTRY_V1.json").read_text(encoding="utf-8"))
    anchor = json.loads((X33B / "NEUTRAL_FIXED_CLOCK_ANCHOR_V1.json").read_text(encoding="utf-8"))
    pol = json.loads((X34A / "ENTRY_EXECUTION_POLICY_V1.json").read_text(encoding="utf-8"))
    x34c = json.loads((X34C / "report.json").read_text(encoding="utf-8"))

    def _ok(body, expected):
        raw = {k: v for k, v in body.items() if k != "sha256"}
        return body.get("sha256") == expected and hashlib.sha256(
            json.dumps(raw, sort_keys=True, default=str).encode()
        ).hexdigest() == expected

    return {
        "entry_ok": _ok(entry, ENTRY_SHA),
        "anchor_ok": _ok(anchor, ANCHOR_SHA),
        "exec_ok": _ok(pol, EXEC_SHA),
        "x34c_run": x34c.get("run_id"),
        "x34c_run_ok": x34c.get("run_id") == SOURCE_X34C_RUN,
        "x34c_verdict": x34c.get("verdict"),
    }


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x34d_prefill_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    mapping = verify_board_mapping()
    assert mapping.get("ok") and mapping.get("mapping_sha") == BOARD_MAPPING_SHA
    src = _verify_sources()
    assert src["entry_ok"] and src["anchor_ok"] and src["exec_ok"] and src["x34c_run_ok"], src
    print("  source bind OK", flush=True)
    print(f"  X34C qualification: {X34C_QUALIFICATION[:80]}...", flush=True)

    print("=== population / events ===", flush=True)
    rows_pop, labels, identity = reproduce_population()
    ab_pop = ab_identity(rows_pop, labels, identity)
    assert ab_pop["ok"], ab_pop
    assert not any(str(r.get("date") or "") >= FORBIDDEN_FROM for r in rows_pop)
    pool = candidate_symbols_by_day(rows_pop)
    planned = planned_neutral_anchors(pool)
    boards = load_boards_for_symbols(sorted({(a["date"], a["symbol"]) for a in planned}))
    events = build_events(planned, boards)
    assert len(events) == EXPECTED_SIGNALS

    # U0 unlimited
    u0 = x34c_summarize(events, mode="unlimited", ret_key_prefix="fill_based_ret")
    assert abs(float(u0["opp_w_ret600"]) - EXPECTED_U0_OPP600) < 1e-6
    print(f"  U0 opp600={u0['opp_w_ret600']}", flush=True)

    # C1 post-fill (X34C identity)
    print("=== C1 post-fill capacity (X34C) ===", flush=True)
    c1_sim = simulate_capacity(events)
    c1 = x34c_summarize(c1_sim["events"], mode="deployable", ret_key_prefix="fill_based_ret")
    c1_ok = (
        abs(float(c1["opp_w_ret600"]) - EXPECTED_C1_OPP600) < 1e-6
        and c1_sim["accepted_fills"] == EXPECTED_C1_ACCEPTED
    )
    print(f"  C1 opp600={c1['opp_w_ret600']} accepted={c1_sim['accepted_fills']} match={c1_ok}", flush=True)
    assert c1_ok

    # C2 pre-fill primary ASC + diagnostics
    print("=== C2 pre-fill hard capacity ===", flush=True)
    modes = {
        ORDER_ASC: None,
        ORDER_DESC: None,
        ORDER_HASH: None,
    }
    adm_by_mode = {}
    econ_by_mode = {}
    for mode in modes:
        adm = simulate_prefill(events, order_mode=mode)
        assert adm["hard_cap_violations"] == 0, adm
        assert adm["max_open_plus_pending"] <= POSITION_CAP, adm
        sm = summarize_prefill(adm["events"], label=mode)
        adm_by_mode[mode] = {k: v for k, v in adm.items() if k != "events"}
        econ_by_mode[mode] = {k: v for k, v in sm.items() if k != "day_means"}
        print(
            f"  {mode}: admitted={adm['orders_admitted']} fills={adm['accepted_fills']} "
            f"opp600={sm.get('opp_w_ret600')} hard_viol={adm['hard_cap_violations']}",
            flush=True,
        )

    adm_asc = simulate_prefill(events, order_mode=ORDER_ASC)
    c2_events = adm_asc["events"]
    c2 = summarize_prefill(c2_events, label="C2_ASC")
    sens = ordering_sensitivity(econ_by_mode)

    drag = capacity_drag(u0.get("opp_w_ret600"), c1.get("opp_w_ret600"), c2.get("opp_w_ret600"))
    lodo = lodo_prefill(c2_events)
    loso = loso_prefill(c2_events)
    days = day_table(c2_events, u0_day=u0.get("day_means"))

    decision = decide_verdict(
        c2=c2,
        admission=adm_by_mode[ORDER_ASC],
        lodo=lodo,
        sensitivity=sens,
    )
    print(f"  verdict={decision['verdict']} gates={decision.get('gates')}", flush=True)

    manifest = None
    if decision.get("freeze"):
        manifest = freeze_admission_manifest(
            admission=adm_by_mode[ORDER_ASC], decision=decision,
        )
        (OUT / "PASSIVE_ORDER_ADMISSION_V1.json").write_text(
            json.dumps(manifest, indent=2, default=str), encoding="utf-8",
        )

    # A/B
    adm_b = simulate_prefill(events, order_mode=ORDER_ASC)
    c2_b = summarize_prefill(adm_b["events"], label="C2_B")
    ab_ok = abs(float(c2_b["opp_w_ret600"]) - float(c2["opp_w_ret600"])) < 1e-12

    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": decision["verdict"],
        "verdict_detail": decision,
        "entry_sha": ENTRY_SHA,
        "anchor_sha": ANCHOR_SHA,
        "execution_sha": EXEC_SHA,
        "source_x34c_run": SOURCE_X34C_RUN,
        "source_verify": src,
        "x34c_qualification": X34C_QUALIFICATION,
        "occupancy_proxy_sec": OCCUPANCY_PROXY_600S,
        "occupancy_label": "OCCUPANCY_PROXY_600S",
        "position_cap": POSITION_CAP,
        "pending_reserves_slot": True,
        "wait_sec": WAIT_SEC,
        "baselines_summary": {
            "U0_unlimited": u0.get("opp_w_ret600"),
            "C1_post_fill": c1.get("opp_w_ret600"),
            "C2_pre_fill_asc": c2.get("opp_w_ret600"),
        },
        "U0": {k: v for k, v in u0.items() if k != "day_means"},
        "C1": {
            "economics": {k: v for k, v in c1.items() if k != "day_means"},
            "accepted_fills": c1_sim["accepted_fills"],
            "identity_match_x34c": c1_ok,
        },
        "C2": {
            "economics": {k: v for k, v in c2.items() if k != "day_means"},
            "admission": adm_by_mode[ORDER_ASC],
            "primary_ordering": ORDER_ASC,
        },
        "capacity_drag": drag,
        "ordering_diagnostic": {
            m: econ_by_mode[m] for m in (ORDER_ASC, ORDER_DESC, ORDER_HASH)
        },
        "ordering_sensitivity": sens,
        "day_level_c2": days,
        "lodo": {k: v for k, v in lodo.items() if k != "folds"},
        "lodo_folds": lodo.get("folds"),
        "loso": loso,
        "manifest_created": bool(manifest),
        "manifest_sha": (manifest or {}).get("sha256"),
        "recommended_next": NEXT_PASS if decision.get("freeze") else NEXT_FAIL,
        "opened_20260810": False,
        "prospective_observer_started": False,
        "prospective_evidence_consumed": False,
        "no_runtime_change": True,
        "no_exit_design": True,
        "no_short": True,
        "no_future_ranking": True,
        "no_predictive_allocator": True,
        "denominator_includes_blocked": True,
        "safety": {
            "research_paper_only": True,
            "submit_cancel_live": "0/0/0",
            "discord_production": False,
        },
        "ab_determinism": {"ok": ab_ok, "population": ab_pop},
        "identities": {
            "unlimited_x34c": abs(float(u0["opp_w_ret600"]) - EXPECTED_U0_OPP600) < 1e-6,
            "post_fill_x34c": c1_ok,
            "signals": len(events) == EXPECTED_SIGNALS,
        },
    }

    interim = {
        "run_id": run_id,
        "verdict": decision["verdict"],
        "entry_sha": ENTRY_SHA,
        "anchor_sha": ANCHOR_SHA,
        "execution_sha": EXEC_SHA,
        "x34c_qualification": X34C_QUALIFICATION,
        "occupancy_label": "OCCUPANCY_PROXY_600S",
        "baselines_summary": report["baselines_summary"],
        "capacity_drag": drag,
        "C2_admission": adm_by_mode[ORDER_ASC],
        "C2_economics": report["C2"]["economics"],
        "ordering_sensitivity": sens,
        "lodo": report["lodo"],
        "loso": {k: v for k, v in loso.items() if k != "sample"},
        "hard_cap_violations": adm_by_mode[ORDER_ASC]["hard_cap_violations"],
        "max_open_plus_pending": adm_by_mode[ORDER_ASC]["max_open_plus_pending"],
        "pending_reserves_slot": True,
        "no_future_ranking": True,
        "no_post_fill_retroactive": True,
        "pending_expiry_sec": WAIT_SEC,
        "denominator_includes_blocked": True,
        "identities": report["identities"],
        "manifest_created": bool(manifest),
        "manifest_sha": (manifest or {}).get("sha256"),
        "opened_20260810": False,
        "no_runtime_change": True,
        "no_exit_design": True,
        "no_short": True,
        "submit_cancel_live": "0/0/0",
        "ab_determinism": report["ab_determinism"],
        "gates": decision.get("gates"),
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{
            "run_id": run_id,
            "verdict": decision["verdict"],
            "U0": u0.get("opp_w_ret600"),
            "C1": c1.get("opp_w_ret600"),
            "C2": c2.get("opp_w_ret600"),
            **drag,
        }],
        "day_c2": days,
        "ordering": [
            {"mode": m, **{k: econ_by_mode[m].get(k) for k in (
                "opp_w_ret600", "pf_equiv_600", "ss_balanced_ret600", "positive_days", "accepted_fills"
            )}}
            for m in (ORDER_ASC, ORDER_DESC, ORDER_HASH)
        ],
        "lodo": lodo.get("folds") or [],
    }
    publish(OUT, report, sheets)

    print("=== tests ===", flush=True)
    tests = _run_tests()
    report["tests"] = tests
    publish(OUT, report, sheets)

    print(f"=== DONE {decision['verdict']} ===", flush=True)
    print(json.dumps({
        "run_id": run_id,
        "verdict": decision["verdict"],
        "U0": u0.get("opp_w_ret600"),
        "C1": c1.get("opp_w_ret600"),
        "C2": c2.get("opp_w_ret600"),
        "drag": drag,
        "hard_viol": adm_by_mode[ORDER_ASC]["hard_cap_violations"],
        "manifest": bool(manifest),
    }, indent=2))
    return report


if __name__ == "__main__":
    main()
