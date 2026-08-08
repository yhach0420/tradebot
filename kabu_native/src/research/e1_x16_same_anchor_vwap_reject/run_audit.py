"""E1_X16 same-anchor VWAP late-chase rejection audit runner."""
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
    FORBIDDEN_ALPHA,
    FORBIDDEN_RISK_FROM,
    REBOUND_MIN_BPS,
    SOURCE_RUN,
    STUDY_TYPE,
    VARIANTS,
    VERDICT_MIXED,
    VERDICT_NONE,
    VERDICT_READY,
    VOLUME_PERCENTILE_MIN,
    VWAP_UPPER_LIMIT_BPS,
)
from .enrich import enrich_c0
from .evaluate import (
    assign_variants,
    availability_audit,
    exclude_symbols,
    incremental,
    reject_gate,
    select,
    variant_metrics,
)
from .publish import publish

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x16_same_anchor_vwap_reject"
SOURCE_X15 = NATIVE / "results" / "research" / "e1_x15_rpfe_incremental_entry" / "report.json"
SUPPORT_MIN_A34 = 100
DAYS_MIN_A34 = 7


def _run_tests() -> dict[str, Any]:
    test_path = NATIVE / "tests" / "research" / "test_e1_x16_same_anchor_vwap_reject.py"
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
    rows = [{"test": "pytest_suite", "outcome": "PASSED" if p.returncode == 0 else "FAILED", "detail": out[-2500:]}]
    # expand named tests from -v style if present
    for line in out.splitlines():
        if " PASSED" in line or " FAILED" in line or " SKIPPED" in line:
            name = line.split()[0]
            outcome = "PASSED" if " PASSED" in line else ("FAILED" if " FAILED" in line else "SKIPPED")
            rows.append({"test": name, "outcome": outcome, "detail": ""})
    return {
        "exit_code": p.returncode, "passed": passed, "failed": failed,
        "total": passed + failed or 1, "rows": rows, "output": out[-4000:],
    }


def _strip_daily(m: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in m.items() if k != "daily"}


def run(*, force_enrich: bool = False) -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    now = datetime.now(JST)
    run_id = f"e1x16_sarej_{now.strftime('%Y%m%d_%H%M%S')}_A"

    src = json.loads(SOURCE_X15.read_text(encoding="utf-8"))
    assert src["run_id"] == SOURCE_RUN, f"source mismatch {src.get('run_id')}"

    rows = enrich_c0(force=force_enrich)
    tagged = assign_variants(rows)

    # complement completeness
    a1 = select(tagged, "A1")
    a2 = select(tagged, "A2")
    a2r = select(tagged, "A2_Rejected")
    assert len(a2) + len(a2r) == len(a1), "A2 + A2_Rejected must partition A1"

    metrics = {v: variant_metrics(tagged, v) for v in VARIANTS}
    incs = {
        "A1_vs_A0": incremental(metrics["A1"], metrics["A0"], "A1_vs_A0"),
        "A2_vs_A1": incremental(metrics["A2"], metrics["A1"], "A2_vs_A1"),
        "A2_Rejected_vs_A2": incremental(metrics["A2_Rejected"], metrics["A2"], "A2_Rejected_vs_A2"),
        "A3_vs_A2": incremental(metrics["A3"], metrics["A2"], "A3_vs_A2"),
        "A4_vs_A3": incremental(metrics["A4"], metrics["A3"], "A4_vs_A3"),
    }
    avail = availability_audit(tagged)
    rej_g = reject_gate(metrics["A2"], metrics["A2_Rejected"])

    # availability separability: if A2≈A1 improvement is tiny vs A1 vs A0
    a1_fr = incs["A1_vs_A0"].get("day_balanced_fr_delta") or 0.0
    a2_fr = incs["A2_vs_A1"].get("day_balanced_fr_delta") or 0.0
    not_separable = False
    if abs(a2_fr) < 1e-12 and abs(a1_fr) > 1e-12:
        not_separable = True
    # if A2 vs A1 does not improve day-bal while A1 vs A0 does most of the lift
    if a2_fr <= 0 and a1_fr > 0:
        # VWAP reject adds no positive increment beyond availability
        pass

    without22 = [r for r in tagged if r["date"] != "20260722"]
    metrics_wo22 = {v: variant_metrics(without22, v) for v in VARIANTS}
    metrics_w22 = metrics  # alias

    ex2354 = exclude_symbols(tagged, ("2354",))
    ex285a = exclude_symbols(tagged, ("285A",))
    metrics_ex2354 = {v: _strip_daily(variant_metrics(ex2354, v)) for v in ("A0", "A1", "A2", "A2_Rejected")}
    metrics_ex285a = {v: _strip_daily(variant_metrics(ex285a, v)) for v in ("A0", "A1", "A2", "A2_Rejected")}

    # A3/A4 support gate
    def a34_status(v: str, inc_key: str, supported_label: str, not_label: str) -> str:
        m = metrics[v]
        if m["support"] < SUPPORT_MIN_A34 or m["entry_days"] < DAYS_MIN_A34:
            return "LOW_SUPPORT"
        d = incs[inc_key].get("day_balanced_fr_delta")
        t = incs[inc_key].get("first_touch_delta")
        if d is not None and d > 0 and t is not None and t > 0:
            return supported_label
        return not_label

    a3_status = a34_status("A3", "A3_vs_A2", "REBOUND_INCREMENT_SUPPORTED", "REBOUND_INCREMENT_NOT_SUPPORTED")
    a4_status = a34_status("A4", "A4_vs_A3", "ACTIVITY_INCREMENT_SUPPORTED", "ACTIVITY_INCREMENT_NOT_SUPPORTED")
    if a3_status == "LOW_SUPPORT":
        a3_status = "LOW_SUPPORT"
    if a4_status == "LOW_SUPPORT":
        a4_status = "LOW_SUPPORT"

    # VWAP reject pure effect vs A1
    a2_improves = (
        (incs["A2_vs_A1"].get("day_balanced_fr_delta") or 0) > 0
        and (incs["A2_vs_A1"].get("first_touch_delta") or 0) > 0
    )
    a2_mae_ok = True
    if metrics["A2"].get("mean_MAE_180s") is not None and metrics["A1"].get("mean_MAE_180s") is not None:
        a2_mae_ok = metrics["A2"]["mean_MAE_180s"] >= metrics["A1"]["mean_MAE_180s"]
    # without 0722 direction maintained
    wo_a2 = metrics_wo22["A2"].get("day_balanced_forward_return") or 0
    wo_a1 = metrics_wo22["A1"].get("day_balanced_forward_return") or 0
    wo_maintained = wo_a2 > wo_a1

    if not_separable or (
        (incs["A2_vs_A1"].get("day_balanced_fr_delta") or 0) <= 0
        and (incs["A2_vs_A1"].get("first_touch_delta") or 0) <= 0
    ):
        if (incs["A2_vs_A1"].get("day_balanced_fr_delta") or 0) <= 0:
            verdict = VERDICT_NONE
            sep_flag = "VWAP_REJECT_EFFECT_NOT_SEPARABLE_FROM_AVAILABILITY" if not_separable else None
        else:
            verdict = VERDICT_NONE
            sep_flag = None
    elif a2_improves and rej_g["pass"] and a2_mae_ok and wo_maintained:
        # mixed if day stability weak
        pos = metrics["A2"]["positive_days"]
        neg = metrics["A2"]["negative_days"]
        if neg > pos or not wo_maintained:
            verdict = VERDICT_MIXED
        else:
            verdict = VERDICT_READY
        sep_flag = None
    elif a2_improves:
        verdict = VERDICT_MIXED
        sep_flag = None
    else:
        verdict = VERDICT_NONE
        sep_flag = None

    # refine NONE when A2 improves FR but reject gate fails → MIXED
    if verdict == VERDICT_NONE and a2_improves:
        verdict = VERDICT_MIXED

    selected = None
    precommit_status = "NOT_CREATED"
    precommit = None
    if verdict == VERDICT_READY:
        selected = "C0_VWAP_LATE_CHASE_REJECT_V1"
        rule = {
            "candidate_id": selected,
            "exact_rule": (
                "C0 anchor AND distance_from_vwap_bps evaluable "
                f"AND distance_from_vwap_bps <= {VWAP_UPPER_LIMIT_BPS}"
            ),
            "threshold": VWAP_UPPER_LIMIT_BPS,
            "feature_source": "push_jsonl as-of at C0 epoch (same anchor)",
            "missing_behavior": "not evaluable → excluded from A1/A2 (A0 still includes)",
            "episode_rule": "one RPFE episode = max 1 C0 anchor",
            "label_rule": "forward labels from C0 time (X15 C0 outcomes)",
            "precommit_at_jst": now.isoformat(),
            "20260803_opened": False,
        }
        precommit = {
            **rule,
            "precommit_sha256": hashlib.sha256(
                json.dumps(rule, sort_keys=True, default=str).encode()
            ).hexdigest(),
        }
        precommit_status = "CREATED"

    # Determinism A/B: recompute metrics hash twice
    m1 = {v: _strip_daily(variant_metrics(tagged, v)) for v in VARIANTS}
    m2 = {v: _strip_daily(variant_metrics(tagged, v)) for v in VARIANTS}
    h1, h2 = sha256_obj(m1), sha256_obj(m2)
    det = {"ab_match": h1 == h2, "hash_a": h1, "hash_b": h2}

    # interim for tests
    interim = {
        "run_id": run_id,
        "source_run": SOURCE_RUN,
        "thresholds": {
            "VWAP_UPPER_LIMIT_BPS": VWAP_UPPER_LIMIT_BPS,
            "REBOUND_MIN_BPS": REBOUND_MIN_BPS,
            "VOLUME_PERCENTILE_MIN": VOLUME_PERCENTILE_MIN,
        },
        "supports": {v: metrics[v]["support"] for v in VARIANTS},
        "incs": incs,
        "reject_gate": rej_g,
        "availability": avail,
        "a3_status": a3_status,
        "a4_status": a4_status,
        "verdict": verdict,
        "days_used": list(DAYS),
        "forbidden": list(FORBIDDEN_ALPHA) + [FORBIDDEN_RISK_FROM],
        "complement_ok": len(a2) + len(a2r) == len(a1),
        "same_anchor": all(r.get("anchor_contract") == "SAME_C0_NO_REANCHOR" for r in tagged),
        "exclude_20260722": {
            "with": {v: _strip_daily(metrics_w22[v]) for v in VARIANTS},
            "without": {v: _strip_daily(metrics_wo22[v]) for v in VARIANTS},
        },
        "n_enriched": len(tagged),
        "separability_flag": sep_flag,
    }
    (OUT / "_interim.json").write_text(json.dumps(interim, indent=2, default=str), encoding="utf-8")

    # Write tests file before running so pytest can import constants; run tests after interim
    tests = _run_tests()

    safety = {
        "submit_cancel_live": "0/0/0",
        "mainline_changed": False,
        "production_yaml_changed": False,
        "ENTRY_changed": False,
        "EXIT_changed": False,
        "Universe_changed": False,
        "20260803_opened": False,
        "20260804_opened": False,
        "Prospective_consumed": False,
        "Shadow": False,
        "Forward": False,
        "Paper_connection": False,
        "Discord": False,
        "paper_trade_only": True,
    }

    supports = {v: metrics[v]["support"] for v in VARIANTS}

    sheets = {
        "SourceIdentity": _kv({
            "analysis_id": ANALYSIS_ID,
            "document_id": DOCUMENT_ID,
            "source_run": SOURCE_RUN,
            "study_type": STUDY_TYPE,
            "days": list(DAYS),
        }),
        "AnchorContract": _kv({
            "entry_time": "C0 canonical anchor time",
            "entry_price": "C0 canonical anchor price",
            "no_reanchoring": True,
            "no_new_episode": True,
            "no_C1_C2_C3_reuse": True,
            "one_episode_max_one_c0": True,
        }),
        "ThresholdContract": _kv(interim["thresholds"]),
        "Variants": [
            {"variant": v, **_strip_daily(metrics[v])} for v in VARIANTS
        ],
        "AvailabilityControl": _kv(avail),
        "DirectionalOutcomes": [
            {
                "variant": v,
                "fr30": metrics[v]["forward_return_30s"],
                "fr60": metrics[v]["forward_return_60s"],
                "fr180": metrics[v]["mean_forward_return_180s"],
                "fr300": metrics[v]["forward_return_300s"],
                "day_bal_fr180": metrics[v]["day_balanced_forward_return"],
                "touch5": metrics[v]["mean_first_touch_plus5_before_minus5"],
                "NoProgress": metrics[v]["no_progress_rate"],
            }
            for v in VARIANTS
        ],
        "RiskDistribution": [
            {"variant": v, "risk": metrics[v]["risk"]} for v in VARIANTS
        ],
        "RejectEffectiveness": [
            {
                "A2": _strip_daily(metrics["A2"]),
                "A2_Rejected": _strip_daily(metrics["A2_Rejected"]),
                "gate": rej_g,
            }
        ],
        "DailyResults": [
            {"variant": v, **d} for v in VARIANTS for d in metrics[v]["daily"]
        ],
        "SymbolResults": [
            {
                "variant": v,
                "symbols_n": metrics[v]["symbols_n"],
                "max_single": metrics[v]["max_single_symbol_contribution"],
                "top10": metrics[v]["top10_symbol_contribution"],
            }
            for v in VARIANTS
        ],
        "Exclude20260722": [
            {"set": "with", "variant": v, **_strip_daily(metrics_w22[v])} for v in VARIANTS
        ] + [
            {"set": "without", "variant": v, **_strip_daily(metrics_wo22[v])} for v in VARIANTS
        ],
        "ExcludeSymbols": [
            {"exclude": "2354", "variant": v, **metrics_ex2354[v]} for v in metrics_ex2354
        ] + [
            {"exclude": "285A", "variant": v, **metrics_ex285a[v]} for v in metrics_ex285a
        ],
        "IncrementalValue": list(incs.values()),
        "CandidateSelection": _kv({
            "verdict": verdict,
            "selected": selected,
            "a3_status": a3_status,
            "a4_status": a4_status,
            "separability_flag": sep_flag,
            "reject_gate": rej_g,
        }),
        "ProspectivePrecommit": _kv(precommit or {"status": precommit_status}),
        "ChangeLog": [
            {"at": now.isoformat(), "note": "E1_X16 same-anchor VWAP late-chase rejection audit"},
        ],
    }

    report = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "source_run": SOURCE_RUN,
        "study_type": STUDY_TYPE,
        "verdict": verdict,
        "supports": supports,
        "a1_availability_effect": incs["A1_vs_A0"],
        "a2_pure_vwap_reject_effect": incs["A2_vs_A1"],
        "a2_rejected_vs_a2": incs["A2_Rejected_vs_A2"],
        "a3_vs_a2": incs["A3_vs_A2"],
        "a4_vs_a3": incs["A4_vs_A3"],
        "metrics": {v: _strip_daily(metrics[v]) for v in VARIANTS},
        "risk_distributions": {v: metrics[v]["risk"] for v in VARIANTS},
        "reject_effectiveness": {
            "gate": rej_g,
            "A2_Rejected": _strip_daily(metrics["A2_Rejected"]),
            "A2": _strip_daily(metrics["A2"]),
        },
        "availability_control": avail,
        "separability_flag": sep_flag,
        "day_stability": {
            v: {
                "positive_days": metrics[v]["positive_days"],
                "negative_days": metrics[v]["negative_days"],
                "zero_or_insufficient_days": metrics[v]["zero_or_insufficient_days"],
            }
            for v in VARIANTS
        },
        "exclude_20260722": {
            "with_A2_day_bal": metrics_w22["A2"]["day_balanced_forward_return"],
            "without_A2_day_bal": metrics_wo22["A2"]["day_balanced_forward_return"],
            "with_A2_vs_A1_delta": (metrics_w22["A2"]["day_balanced_forward_return"] or 0)
            - (metrics_w22["A1"]["day_balanced_forward_return"] or 0),
            "without_A2_vs_A1_delta": (metrics_wo22["A2"]["day_balanced_forward_return"] or 0)
            - (metrics_wo22["A1"]["day_balanced_forward_return"] or 0),
        },
        "exclude_symbols": {
            "2354": metrics_ex2354,
            "285A": metrics_ex285a,
        },
        "a3_status": a3_status,
        "a4_status": a4_status,
        "selected_candidate": selected,
        "prospective_precommit_status": precommit_status,
        "prospective_precommit": precommit,
        "thresholds": interim["thresholds"],
        "safety": safety,
        "_sheets": sheets,
    }

    shas = publish(report, tests, det, OUT)
    report["published_shas"] = shas
    print(json.dumps({
        "run_id": run_id,
        "verdict": verdict,
        "supports": supports,
        "a3_status": a3_status,
        "a4_status": a4_status,
        "selected": selected,
        "precommit": precommit_status,
        "tests": f"{tests['passed']}/{tests['total']}",
        "ab": det["ab_match"],
    }, indent=2))
    return report


def _kv(d: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for k, v in d.items():
        if isinstance(v, (dict, list)):
            rows.append({"key": k, "value": json.dumps(v, default=str)[:12000]})
        else:
            rows.append({"key": k, "value": v})
    return rows


if __name__ == "__main__":
    force = "--force" in sys.argv
    run(force_enrich=force)
