"""E1_X15 run audit."""
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

from research.e1_x6_provisional.util import sha256_obj

from . import (
    ANALYSIS_ID,
    DAYS,
    DOCUMENT_ID,
    FEATURES_ALLOWED,
    FORBIDDEN_ALPHA,
    FORBIDDEN_RISK_FROM,
    REBOUND_Q80,
    SOURCE_RUN,
    SOURCE_VERDICT,
    STUDY_TYPE,
    VARIANTS,
    VERDICT_MIXED,
    VERDICT_NONE,
    VERDICT_READY,
    VOL_PCT_Q80,
    VWAP_Q80,
)
from .anchors import select_anchors_for_episodes
from .episodes import build_episodes
from .evaluate import (
    exclude_day,
    freshness_strata,
    incremental,
    variant_metrics,
)
from .publish import publish

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x15_rpfe_incremental_entry"
SOURCE = NATIVE / "results" / "research" / "e1_x14_holdout_reconciliation" / "report.json"
CACHE_MATCHED = OUT / "_matched_cache.jsonl"


def _run_tests() -> dict[str, Any]:
    test_path = NATIVE / "tests" / "research" / "test_e1_x15_rpfe_incremental_entry.py"
    import os
    env = {**os.environ, "PYTHONPATH": str(NATIVE / "src")}
    p = subprocess.run(
        [sys.executable, "-m", "pytest", str(test_path), "-q", "--tb=line"],
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
    return {
        "exit_code": p.returncode, "passed": passed, "failed": failed,
        "total": passed + failed or 1,
        "rows": [{"test": "pytest_suite",
                  "outcome": "PASSED" if p.returncode == 0 else "FAILED",
                  "detail": out[-2500:]}],
    }


def _passes_vs_c0(m: dict, c0: dict, without22_m: dict, without22_c0: dict) -> tuple[bool, list[str]]:
    from . import GATE
    reasons = []
    if m["support_episodes"] < GATE["support_min"]:
        reasons.append("support")
    if m["entry_days"] < GATE["entry_days_min"]:
        reasons.append("entry_days")
    if (m.get("day_balanced_forward_return") or -1e9) <= (c0.get("day_balanced_forward_return") or 0):
        reasons.append("day_bal_not_improved")
    if (m.get("mean_first_touch_plus5_before_minus5") or -1e9) <= (c0.get("mean_first_touch_plus5_before_minus5") or 0):
        reasons.append("touch_not_improved")
    if m.get("mean_MAE_180s") is not None and c0.get("mean_MAE_180s") is not None:
        if m["mean_MAE_180s"] < c0["mean_MAE_180s"]:
            reasons.append("MAE_worse")
    if m.get("no_progress_rate") is not None and c0.get("no_progress_rate") is not None:
        if m["no_progress_rate"] > c0["no_progress_rate"]:
            reasons.append("NoProgress_increased")
    # 0722 exclusion: improvement direction vs C0 maintained
    d_with = (m.get("day_balanced_forward_return") or 0) - (c0.get("day_balanced_forward_return") or 0)
    d_wo = (without22_m.get("day_balanced_forward_return") or 0) - (without22_c0.get("day_balanced_forward_return") or 0)
    if d_with > 0 and d_wo <= 0:
        reasons.append("0722_exclusion_flip")
    max_day = (m.get("max_single_day_contribution") or {}).get("frac") or 1
    max_sym = (m.get("max_single_symbol_contribution") or {}).get("frac") or 1
    if max_day > GATE["max_day_contrib"]:
        reasons.append("max_day")
    if max_sym > GATE["max_sym_contrib"]:
        reasons.append("max_sym")
    return len(reasons) == 0, reasons


def run(*, label: str = "A", force: bool = False) -> dict[str, Any]:
    run_id = f"e1x15_rpfeinc_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}_{label}"
    src = json.loads(SOURCE.read_text(encoding="utf-8"))
    assert src.get("run_id") == SOURCE_RUN
    assert src.get("verdict") == SOURCE_VERDICT

    OUT.mkdir(parents=True, exist_ok=True)
    print("=== Build RPFE episodes ===", flush=True)
    episodes = build_episodes(DAYS)
    print(f"episodes={len(episodes)}", flush=True)

    if CACHE_MATCHED.exists() and not force:
        print("=== Load matched cache ===", flush=True)
        matched = [json.loads(l) for l in CACHE_MATCHED.read_text(encoding="utf-8").splitlines() if l.strip()]
    else:
        print("=== Select anchors (heavy) ===", flush=True)
        matched = select_anchors_for_episodes(
            episodes, progress_cb=lambda s: print(s, flush=True),
        )
        with CACHE_MATCHED.open("w", encoding="utf-8") as f:
            for m in matched:
                # drop bulky diag nested for cache size? keep
                f.write(json.dumps(m, default=str) + "\n")

    print("=== Metrics ===", flush=True)
    metrics = {v: variant_metrics(matched, v) for v in VARIANTS}
    matched_wo = exclude_day(matched, "20260722")
    metrics_wo = {v: variant_metrics(matched_wo, v) for v in VARIANTS}

    incs = {
        "C3_vs_C0": incremental(metrics["C3"], metrics["C0"], "C3-C0"),
        "C3_vs_C2": incremental(metrics["C3"], metrics["C2"], "C3-C2"),
        "C2_vs_C1": incremental(metrics["C2"], metrics["C1"], "C2-C1"),
        "C1_vs_C0": incremental(metrics["C1"], metrics["C0"], "C1-C0"),
        "C2_vs_C0": incremental(metrics["C2"], metrics["C0"], "C2-C0"),
    }

    gate_detail = {}
    for v in ("C1", "C2", "C3"):
        ok, reasons = _passes_vs_c0(metrics[v], metrics["C0"], metrics_wo[v], metrics_wo["C0"])
        gate_detail[v] = {"gate_pass": ok, "fail_reasons": reasons}

    def improves(inc: dict) -> bool:
        fr = inc.get("day_balanced_fr_delta")
        ft = inc.get("first_touch_delta")
        return (fr is not None and fr > 0) or (ft is not None and ft > 0)

    selected = None
    # C1
    if gate_detail["C1"]["gate_pass"]:
        selected = "C1"
    # C2 only if gate pass AND improves C1
    if gate_detail["C2"]["gate_pass"]:
        if gate_detail["C1"]["gate_pass"]:
            if improves(incs["C2_vs_C1"]):
                selected = "C2"
            else:
                gate_detail["C2"]["fail_reasons"] = gate_detail["C2"]["fail_reasons"] + ["no_incremental_vs_C1"]
        else:
            selected = "C2"
    # C3 only if gate pass AND improves C2 (and don't adopt complexity without gain)
    if gate_detail["C3"]["gate_pass"]:
        if selected == "C2":
            if improves(incs["C3_vs_C2"]):
                selected = "C3"
            else:
                gate_detail["C3"]["fail_reasons"] = gate_detail["C3"]["fail_reasons"] + ["no_incremental_vs_C2"]
        elif selected is None:
            if improves(incs["C3_vs_C2"]) or improves(incs["C3_vs_C0"]):
                selected = "C3"
            else:
                gate_detail["C3"]["fail_reasons"] = gate_detail["C3"]["fail_reasons"] + ["no_incremental_value"]

    fresh = freshness_strata(matched, "C3")

    any_improve = any(
        (incs[k].get("day_balanced_fr_delta") or 0) > 0 or (incs[k].get("first_touch_delta") or 0) > 0
        for k in ("C1_vs_C0", "C2_vs_C0", "C3_vs_C0")
    )
    if selected:
        verdict = VERDICT_READY
    elif any_improve:
        verdict = VERDICT_MIXED
    else:
        verdict = VERDICT_NONE

    # Prospective precommit only if READY
    precommit = None
    precommit_status = "NOT_CREATED"
    if verdict == VERDICT_READY and selected:
        body = {
            "candidate_id": f"E1_X15_{selected}",
            "variant": selected,
            "conditions": {
                "C1": "REBOUND_READY",
                "C2": "REBOUND_READY AND VWAP_DISTANCE_OK",
                "C3": "REBOUND_READY AND VWAP_DISTANCE_OK AND ACTIVITY_READY",
            }[selected],
            "thresholds": {
                "distance_from_vwap_bps_q80_upper_reject": VWAP_Q80,
                "rebound_from_recent_low_bps_q80": REBOUND_Q80,
                "volume_percentile_60s_q80": VOL_PCT_Q80,
            },
            "episode_rule": "same symbol/session RPFE candidate-gap episode; one anchor per variant",
            "label_rule": "DIRECTIONAL_REFERENCE_PRICE_LABEL; no bid/ask PnL",
            "execution_rule_for_next_phase": "paper-only observer; open 20260803 only in separate prospective run",
            "target_prospective_day": "20260803",
            "precommit_at_jst": datetime.now(JST).isoformat(),
            "study_type": STUDY_TYPE,
        }
        body["precommit_sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True, default=str).encode()
        ).hexdigest()
        precommit = body
        precommit_status = "CREATED_PENDING_SEPARATE_PROSPECTIVE_RUN"

    # A/B
    metrics_b = {v: variant_metrics(matched, v) for v in VARIANTS}
    ab_match = sha256_obj(metrics) == sha256_obj(metrics_b)

    # interim for tests
    interim = {
        "matched_n": len(matched),
        "metrics": metrics,
        "selected_candidate": selected,
        "gate_detail": gate_detail,
        "incs": incs,
        "features": list(FEATURES_ALLOWED),
        "variants": list(VARIANTS),
        "thresholds": {"vwap": VWAP_Q80, "rebound": REBOUND_Q80, "vol_pct": VOL_PCT_Q80},
        "exclude_20260722": {"with": metrics, "without": metrics_wo},
        "freshness": fresh,
        "precommit_status": precommit_status,
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    print("=== Tests ===", flush=True)
    tests = _run_tests()

    # slim matched for sheet (cap)
    matched_slim = []
    for m in matched[:5000]:
        matched_slim.append({k: v for k, v in m.items() if not k.endswith("_rebound_diag")})

    report = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "label": label,
        "generated_at_jst": datetime.now(JST).isoformat(),
        "study_type": STUDY_TYPE,
        "verdict": verdict,
        "source_run": SOURCE_RUN,
        "source_verdict": SOURCE_VERDICT,
        "days_used": list(DAYS),
        "note_days_already_used_in_feature_selection": True,
        "features_allowed": list(FEATURES_ALLOWED),
        "variants": list(VARIANTS),
        "threshold_contract": {
            "VWAP_DISTANCE_OK": f"distance_from_vwap_bps <= {VWAP_Q80}",
            "REBOUND_READY": f"rebound_from_recent_low_bps >= {REBOUND_Q80}",
            "ACTIVITY_READY": f"volume_percentile_60s >= {VOL_PCT_Q80}",
            "construction": "E1_X14 DESIGN fixed",
            "retune_forbidden": True,
            "vwap_role": "late-chase upper reject only",
        },
        "n_rpfe_episodes": len(episodes),
        "n_matched_rows": len(matched),
        "variant_metrics": metrics,
        "incremental_value": incs,
        "exclude_20260722": {
            "with_20260722": {v: metrics[v].get("day_balanced_forward_return") for v in VARIANTS},
            "without_20260722": {v: metrics_wo[v].get("day_balanced_forward_return") for v in VARIANTS},
        },
        "freshness_strata": fresh,
        "candidate_selection": gate_detail,
        "selected_candidate": selected,
        "prospective_precommit_status": precommit_status,
        "prospective_precommit": precommit,
        "forbidden_claims": [
            "historical holdout passed", "robust", "final stable",
            "freeze ready", "prospective passed", "production ready",
        ],
        "safety": {
            "submit_cancel_live": "0/0/0",
            "mainline_changed": False,
            "production_YAML_changed": False,
            "ENTRY_changed": False,
            "EXIT_changed": False,
            "Universe_changed": False,
            "opened_20260803": False,
            "opened_20260804": False,
            "risk_only_alpha_used": False,
            "Prospective_consumed": False,
            "Shadow": False,
            "Forward": False,
            "Paper_connection": False,
            "Discord": False,
        },
        "_sheets": {
            "SourceIdentity": [
                {"key": "source_run", "value": SOURCE_RUN},
                {"key": "source_verdict", "value": SOURCE_VERDICT},
                {"key": "study_type", "value": STUDY_TYPE},
            ],
            "ThresholdContract": [
                {"name": "VWAP_Q80", "value": VWAP_Q80, "role": "upper_reject"},
                {"name": "REBOUND_Q80", "value": REBOUND_Q80, "role": "ready"},
                {"name": "VOL_PCT_Q80", "value": VOL_PCT_Q80, "role": "ready"},
            ],
            "RPFEEpisodes": [{"n": len(episodes), "gap_sec": 300}],
            "CandidateDefinitions": [
                {"id": "C0", "rule": "first RPFE episode candidate"},
                {"id": "C1", "rule": "REBOUND_READY"},
                {"id": "C2", "rule": "REBOUND_READY AND VWAP_DISTANCE_OK"},
                {"id": "C3", "rule": "REBOUND_READY AND VWAP_DISTANCE_OK AND ACTIVITY_READY"},
            ],
            "AnchorSelection": [{"one_anchor_per_episode_per_variant": True}],
            "MatchedComparison": matched_slim[:2000],
            "DirectionalOutcomes": [metrics[v] for v in VARIANTS],
            "FirstTouch": [{"variant": v, "touch": metrics[v].get("mean_first_touch_plus5_before_minus5")} for v in VARIANTS],
            "NoProgress": [{"variant": v, "rate": metrics[v].get("no_progress_rate")} for v in VARIANTS],
            "IncrementalValue": list(incs.values()),
            "DailyBalance": [{"variant": v, "day_bal": metrics[v].get("day_balanced_forward_return")} for v in VARIANTS],
            "SymbolBalance": [{"variant": v, "sym_bal": metrics[v].get("symbol_balanced_forward_return")} for v in VARIANTS],
            "LODO": [{"variant": v, "lodo": metrics[v].get("lodo")} for v in VARIANTS],
            "LOSO": [{"note": "symbol-balanced FR used as LOSO proxy"}],
            "Exclude20260722": [
                {"set": "with", **{v: metrics[v].get("day_balanced_forward_return") for v in VARIANTS}},
                {"set": "without", **{v: metrics_wo[v].get("day_balanced_forward_return") for v in VARIANTS}},
            ],
            "FreshnessStrata": [fresh],
            "CandidateSelection": [{"variant": k, **v} for k, v in gate_detail.items()] + [
                {"variant": "SELECTED", "selected": selected}
            ],
            "ProspectivePrecommit": [precommit] if precommit else [{"status": precommit_status}],
            "ChangeLog": [
                {"change": "exploratory_combination_on_feature_selection_days", "note": STUDY_TYPE},
                {"change": "no_20260803_open", "note": "precommit only if READY"},
            ],
        },
    }
    det = {"ab_match": ab_match, "metrics_sha": sha256_obj(metrics), "n_matched": len(matched)}
    publish(report, tests, det, OUT)
    print("VERDICT", verdict, "selected", selected, flush=True)
    return report


if __name__ == "__main__":
    run()
