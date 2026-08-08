"""E1_X35R runner — Fixed-Horizon EXIT Contract Reconciliation."""
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
from research.e1_x35_passive_exit.paths import load_fill_episodes

from . import (
    ANALYSIS_ID,
    ANCHOR_SHA,
    BOARD_MAPPING_SHA,
    CANONICAL_LOOKUP,
    DOCUMENT_ID,
    ENTRY_SHA,
    EXEC_SHA,
    EXPECTED_FILLS,
    FORBIDDEN_FROM,
    HORIZONS,
    SOURCE_X35_RUN,
    X35_FIXED_MEANS,
    X35_PATH_MEANS,
)
from .contracts import contract_table, episode_compare, summarize_horizon
from .publish import publish
from .recompute import recompute_fixed
from .verdict import decide_verdict, freeze_manifest

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x35r_exit_contract"
X33B = NATIVE / "results" / "research" / "e1_x33b_neutral_anchor"
X34A = NATIVE / "results" / "research" / "e1_x34a_execution_policy"
X34C = NATIVE / "results" / "research" / "e1_x34c_passive_deployability"
X35 = NATIVE / "results" / "research" / "e1_x35_passive_exit"


def _run_tests() -> dict[str, Any]:
    import os
    tp = NATIVE / "tests" / "research" / "test_e1_x35r_exit_contract.py"
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


def _sha_ok(body: dict, exp: str) -> bool:
    raw = {k: v for k, v in body.items() if k != "sha256"}
    return body.get("sha256") == exp and hashlib.sha256(
        json.dumps(raw, sort_keys=True, default=str).encode()
    ).hexdigest() == exp


def _verify() -> dict[str, Any]:
    entry = json.loads((X34C / "PASSIVE_FILL_ENTRY_V1.json").read_text(encoding="utf-8"))
    anchor = json.loads((X33B / "NEUTRAL_FIXED_CLOCK_ANCHOR_V1.json").read_text(encoding="utf-8"))
    pol = json.loads((X34A / "ENTRY_EXECUTION_POLICY_V1.json").read_text(encoding="utf-8"))
    x35 = json.loads((X35 / "report.json").read_text(encoding="utf-8"))
    return {
        "entry_ok": _sha_ok(entry, ENTRY_SHA),
        "anchor_ok": _sha_ok(anchor, ANCHOR_SHA),
        "exec_ok": _sha_ok(pol, EXEC_SHA),
        "x35_run": x35.get("run_id"),
        "x35_ok": x35.get("run_id") == SOURCE_X35_RUN,
        "x35_verdict": x35.get("verdict"),
        "x35_path_exec600": (x35.get("path_aggregate") or {}).get("exec_600", {}).get("mean"),
        "x35_fixed600": (x35.get("fixed_controls") or {}).get("E0_FIXED_600", {}).get("mean_ret_bps"),
    }


def main() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    run_id = "e1x35r_contract_" + datetime.now(JST).strftime("%Y%m%d_%H%M%S") + "_A"
    print(f"=== {ANALYSIS_ID} {run_id} ===", flush=True)

    mapping = verify_board_mapping()
    assert mapping.get("ok") and mapping.get("mapping_sha") == BOARD_MAPPING_SHA
    ver = _verify()
    assert ver["entry_ok"] and ver["anchor_ok"] and ver["exec_ok"] and ver["x35_ok"], ver
    print(f"  X35 bind OK run={ver['x35_run']}", flush=True)
    print(f"  X35 path600={ver['x35_path_exec600']} fixed600={ver['x35_fixed600']}", flush=True)

    print("=== load 330 fills + paths ===", flush=True)
    rows_pop, labels, identity = reproduce_population()
    ab_pop = ab_identity(rows_pop, labels, identity)
    assert ab_pop["ok"]
    assert not any(str(r.get("date") or "") >= FORBIDDEN_FROM for r in rows_pop)
    pool = candidate_symbols_by_day(rows_pop)
    planned = planned_neutral_anchors(pool)
    boards = load_boards_for_symbols(sorted({(a["date"], a["symbol"]) for a in planned}))
    eps = load_fill_episodes(planned, boards)
    assert len(eps) == EXPECTED_FILLS
    print(f"  episodes={len(eps)}", flush=True)

    # A/B
    eps_b = load_fill_episodes(planned, boards)
    ab_ok = len(eps_b) == len(eps)

    print("=== contract table + episode identity ===", flush=True)
    ctable = contract_table()
    horizon_summaries = {}
    episode_samples = {}
    for H in HORIZONS:
        rows = episode_compare(eps, float(H))
        sm = summarize_horizon(rows)
        horizon_summaries[H] = sm
        # sample mismatches for audit (cap)
        mm = [r for r in rows if r["mismatch_reason"] != "IDENTICAL"][:40]
        episode_samples[H] = mm
        print(
            f"  H={H}: path={sm['path_mean']:.4f} fixed={sm['fixed_mean']:.4f} "
            f"mismatch={sm['mismatch_count']} identical={sm['identical_count']} "
            f"reasons={sm['reason_breakdown']}",
            flush=True,
        )

    h600 = horizon_summaries[600]
    _non_id = {k: v for k, v in h600["reason_breakdown"].items() if k != "IDENTICAL"}
    _primary = max(_non_id, key=_non_id.get) if _non_id else "NONE"
    h600_cause = (
        "PATH_EXEC_600 uses last valid Buy1 with offs<=600 "
        f"(mean~{h600['path_mean']:.4f}); "
        "E0_FIXED_600 uses first valid Buy1 with offs>=600 "
        f"(mean~{h600['fixed_mean']:.4f}). "
        f"Primary mismatch class={_primary}. "
        f"mismatch_n={h600['mismatch_count']} delta_sum={h600['mismatch_delta_sum']:.4f} "
        f"(mean_delta={h600['mismatch_delta_mean']}). "
        f"Canonical chooses {CANONICAL_LOOKUP} (= E0_FIXED / X35 EXIT control)."
    )
    print(f"  H600 cause: {h600_cause[:200]}...", flush=True)

    # identity vs X35 reported
    x35_identity = {}
    for H in HORIZONS:
        pm = horizon_summaries[H]["path_mean"]
        fm = horizon_summaries[H]["fixed_mean"]
        x35_identity[H] = {
            "path_mean_reproduced": pm,
            "fixed_mean_reproduced": fm,
            "path_match_x35_ref": abs(pm - X35_PATH_MEANS[H]) < 0.01,
            "fixed_match_x35_ref": abs(fm - X35_FIXED_MEANS[H]) < 0.01,
            "x35_path_ref": X35_PATH_MEANS[H],
            "x35_fixed_ref": X35_FIXED_MEANS[H],
        }

    print("=== recompute canonical FIXED controls ===", flush=True)
    recomputed = recompute_fixed(eps)
    for H in HORIZONS:
        sm = recomputed[f"FIXED{H}"]
        print(
            f"  FIXED{H}: ret={sm['mean_ret_bps']:.4f} PF={sm['pf']:.3f} "
            f"pos={sm['positive_days']}/{sm['n_days']} hold_med={(sm.get('hold_sec') or {}).get('median')}",
            flush=True,
        )

    decision = decide_verdict(recomputed)
    print(f"  verdict={decision['verdict']}", flush=True)

    manifest = None
    if decision.get("freeze"):
        manifest = freeze_manifest(decision=decision, f600=recomputed["FIXED600"])
        (OUT / "PASSIVE_FIXED600_EXIT_BASELINE_V1.json").write_text(
            json.dumps(manifest, indent=2, default=str), encoding="utf-8",
        )
    else:
        mp = OUT / "PASSIVE_FIXED600_EXIT_BASELINE_V1.json"
        if mp.exists():
            mp.unlink()

    f600 = recomputed["FIXED600"]
    report: dict[str, Any] = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "verdict": decision["verdict"],
        "verdict_detail": decision,
        "source_x35_run": SOURCE_X35_RUN,
        "entry_sha": ENTRY_SHA,
        "anchor_sha": ANCHOR_SHA,
        "execution_sha": EXEC_SHA,
        "source_verify": ver,
        "n_fills": len(eps),
        "contract_table": ctable,
        "canonical_lookup": CANONICAL_LOOKUP,
        "canonical_semantic": (
            "ENTRY=fill_time/fill_price; target=fill_time+H; "
            "EXIT=first valid Buy1 (qty>=100, freshness<=5s, not special, same session) "
            "with offs>=H; else SESSION_CLOSE at last valid Buy1. No mid/synthetic."
        ),
        "h600_cause": h600_cause,
        "horizon_summaries": {str(k): v for k, v in horizon_summaries.items()},
        "x35_identity": {str(k): v for k, v in x35_identity.items()},
        "recomputed": {
            k: {kk: vv for kk, vv in v.items() if kk != "day_means"}
            for k, v in recomputed.items()
        },
        "recomputed_fixed600_summary": {
            "mean_ret_bps": f600.get("mean_ret_bps"),
            "pf": f600.get("pf"),
            "positive_days": f600.get("positive_days"),
            "n_days": f600.get("n_days"),
            "ss_balanced": f600.get("ss_balanced"),
            "day_balanced": f600.get("day_balanced"),
            "hold_mean": (f600.get("hold_sec") or {}).get("mean"),
            "hold_median": (f600.get("hold_sec") or {}).get("median"),
            "reason_counts": f600.get("reason_counts"),
            "canonical_matches_evaluate": f600.get("canonical_matches_evaluate"),
        },
        "x35_verdict_changed": decision.get("x35_verdict_changed"),
        "no_new_exit_search": True,
        "no_allocator_tuning": True,
        "no_runtime_change": True,
        "no_short": True,
        "entry_origin_fill_time": True,
        "executable_bid_exit": True,
        "exact_target_timestamp": True,
        "manifest_created": bool(manifest),
        "manifest_sha": (manifest or {}).get("sha256"),
        "recommended_next": decision.get("next"),
        "opened_20260810": False,
        "prospective_observer_started": False,
        "prospective_evidence_consumed": False,
        "capacity_note": (
            "If FIXED600 confirmed, X34D 600s occupancy proxy ≈ real hold; "
            "X36 focus = pre-fill admission among <=available_slots, not EXIT timing relief."
        ),
        "safety": {"research_paper_only": True, "submit_cancel_live": "0/0/0"},
        "ab_determinism": {"ok": ab_ok, "population": ab_pop},
    }

    interim = {
        "run_id": run_id,
        "verdict": decision["verdict"],
        "source_x35_run": SOURCE_X35_RUN,
        "entry_sha": ENTRY_SHA,
        "n_fills": len(eps),
        "contract_table": ctable,
        "h600_cause": h600_cause,
        "horizon_summaries": report["horizon_summaries"],
        "x35_identity": report["x35_identity"],
        "recomputed_fixed600_summary": report["recomputed_fixed600_summary"],
        "x35_verdict_changed": decision.get("x35_verdict_changed"),
        "canonical_lookup": CANONICAL_LOOKUP,
        "entry_origin_fill_time": True,
        "exact_target_timestamp": True,
        "executable_bid_exit": True,
        "qty_min": 100,
        "freshness_max_sec": 5.0,
        "no_special_quote": True,
        "same_session": True,
        "session_close_deterministic": True,
        "no_synthetic_price": True,
        "no_new_exit_search": True,
        "no_allocator_tuning": True,
        "no_runtime_change": True,
        "no_short": True,
        "opened_20260810": False,
        "submit_cancel_live": "0/0/0",
        "manifest_created": bool(manifest),
        "manifest_sha": (manifest or {}).get("sha256"),
        "recommended_next": decision.get("next"),
        "ab_determinism": report["ab_determinism"],
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    sheets = {
        "summary": [{
            "run_id": run_id,
            "verdict": decision["verdict"],
            "path600": h600["path_mean"],
            "fixed600": h600["fixed_mean"],
            "mismatch600": h600["mismatch_count"],
            "canonical": CANONICAL_LOOKUP,
            "manifest": bool(manifest),
        }],
        "contract": ctable,
        "horizons": [
            {"H": H, **{k: v for k, v in horizon_summaries[H].items() if k != "day_level_mismatch_delta_sum"}}
            for H in HORIZONS
        ],
        "h600_days": [
            {"date": d, "mismatch_delta_sum": v}
            for d, v in (h600.get("day_level_mismatch_delta_sum") or {}).items()
        ],
        "mismatch_sample_600": episode_samples[600],
        "recomputed": [
            {
                "id": k,
                "ret": v.get("mean_ret_bps"),
                "pf": v.get("pf"),
                "pos_days": v.get("positive_days"),
                "ss": v.get("ss_balanced"),
                "day_bal": v.get("day_balanced"),
                "hold_med": (v.get("hold_sec") or {}).get("median"),
                "reasons": v.get("reason_counts"),
            }
            for k, v in recomputed.items()
        ],
    }
    publish(OUT, report, sheets)

    print("=== tests ===", flush=True)
    tests = _run_tests()
    report["tests"] = tests
    interim["tests"] = tests
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")
    publish(OUT, report, sheets)

    print(f"=== DONE {decision['verdict']} ===", flush=True)
    print(json.dumps({
        "run_id": run_id,
        "verdict": decision["verdict"],
        "path600": h600["path_mean"],
        "fixed600": h600["fixed_mean"],
        "mismatch600": h600["mismatch_count"],
        "manifest": bool(manifest),
        "sha": (manifest or {}).get("sha256"),
        "next": decision.get("next"),
    }, indent=2))
    return report


if __name__ == "__main__":
    main()
