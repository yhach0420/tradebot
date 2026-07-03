"""
Phase627: regression suite for the PBv2 cluster-guard collapse production fix.

Required tests:
  T1 feature-incomplete candidate is never rejected by the cluster guard
  T2 feature-complete reject-classified candidate IS rejected
  T3 OR overlay final-reason mask preserves pbv2_internal_reason
  T4 6/29-equivalent live data does not reproduce the ~100% cluster reject
  T5 coexists with Phase621 freshness semantics v2 production config
  T6 run_paper_trade.bat preflight chain passes

Outputs:
  results/reports/phase627_cluster_guard_production_fix.json
  results/reports/phase627_regression_summary.csv
"""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

NATIVE_ROOT = Path(__file__).resolve().parents[2]  # kabu_native/
REPO_ROOT = NATIVE_ROOT.parent  # tradebotfile/

PHASE627_VERDICT = "phase627_cluster_guard_production_fix_done"
PRODUCTION_YAML = (
    NATIVE_ROOT
    / "configs"
    / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)

# 6/29 collapse-era guard config (deployed reject sets).
COLLAPSE_REJECT_CLUSTERS = frozenset({5})
COLLAPSE_REJECT_CSUBS = frozenset({0, 2, 3, 5})

T4_SAMPLE_LIMIT = 3000


def _result(test_id: str, name: str, passed: bool, detail: str) -> dict[str, Any]:
    return {"test_id": test_id, "name": name, "passed": bool(passed), "detail": detail}


def _load_guard_state(config: Any, *, reject_csubs: frozenset[int], exception_enabled: bool):
    from small_paper.entry_cluster_guard import build_entry_cluster_guard_state

    state = build_entry_cluster_guard_state(config, repo_root=REPO_ROOT)
    assert state is not None, "cluster guard state is None (guard disabled in production YAML?)"
    return replace(
        state,
        config=replace(
            state.config,
            reject_clusters=COLLAPSE_REJECT_CLUSTERS,
            reject_csubs=reject_csubs,
            exception_enabled=exception_enabled,
        ),
    )


def _complete_trade(model: Any) -> dict[str, Any]:
    """Candidate with every classifier feature present raw (no fill paths used)."""
    trade: dict[str, Any] = {"symbol": "6976.T"}
    feats = set(model.cluster_features) | set(model.subcluster_features) | set(model.csub_features)
    for i, f in enumerate(sorted(feats)):
        trade[f] = 0.1 + 0.01 * i
    # keep liquidity burst below the exception threshold
    trade["board_update_frequency"] = 0.001
    trade["relative_volume"] = 1.0
    trade["liquidity_burst"] = 0.001
    return trade


def test_t1_feature_incomplete_no_reject(config: Any) -> dict[str, Any]:
    from small_paper.entry_cluster_guard import CLUSTER_GUARD_FEATURE_INCOMPLETE

    guard = _load_guard_state(config, reject_csubs=COLLAPSE_REJECT_CSUBS, exception_enabled=False)
    incomplete = {"symbol": "6976.T", "entry_time": "2026-06-29T09:30:00+09:00"}
    cls = guard.model.classify(dict(incomplete))
    # force this candidate's own classification into the reject sets
    forced = replace(
        guard,
        config=replace(
            guard.config,
            reject_clusters=frozenset({int(cls["cluster_id"])}),
            reject_csubs=frozenset(
                {int(cls["new_subcluster_id"])} if int(cls["new_subcluster_id"]) >= 0 else set()
            ),
        ),
    )
    chk = forced.check(incomplete)
    ok = (
        not chk.blocked
        and chk.cluster_guard_status == CLUSTER_GUARD_FEATURE_INCOMPLETE
        and not chk.feature_complete
        and len(chk.missing_features) > 0
        and forced.feature_incomplete_count == 1
        and forced.reject_count == 0
    )
    return _result(
        "T1",
        "feature incomplete -> tag only, no reject",
        ok,
        f"blocked={chk.blocked} status={chk.cluster_guard_status} "
        f"missing={len(chk.missing_features)} incomplete_count={forced.feature_incomplete_count}",
    )


def test_t2_feature_complete_reject(config: Any) -> dict[str, Any]:
    from small_paper.entry_cluster_guard import CLUSTER_GUARD_REJECTED

    guard = _load_guard_state(config, reject_csubs=COLLAPSE_REJECT_CSUBS, exception_enabled=False)
    trade = _complete_trade(guard.model)
    cls = guard.model.classify(dict(trade))
    forced = replace(
        guard,
        config=replace(
            guard.config,
            reject_clusters=frozenset({int(cls["cluster_id"])}),
            reject_csubs=frozenset(
                {int(cls["new_subcluster_id"])} if int(cls["new_subcluster_id"]) >= 0 else set()
            ),
        ),
    )
    chk = forced.check(trade)
    ok = chk.blocked and chk.cluster_guard_status == CLUSTER_GUARD_REJECTED and chk.feature_complete
    return _result(
        "T2",
        "feature complete + reject-classified -> reject",
        ok,
        f"blocked={chk.blocked} status={chk.cluster_guard_status} "
        f"feature_complete={chk.feature_complete} cid={chk.cluster_id} csub={chk.new_subcluster_id}",
    )


def test_t3_or_overlay_mask_preserves_internal_reason() -> dict[str, Any]:
    from research.exposure_gate import GateDecision
    from small_paper.pilot_runner import (
        EVENT_FIELDS,
        _LiveRunState,
        _record_pbv2_internal_reject,
    )

    state = _LiveRunState(started_mono=0.0)
    trade: dict[str, Any] = {"symbol": "6976.T"}
    _record_pbv2_internal_reject(state, trade, GateDecision(accept=False, reason="entry_cluster_guard"))
    # OR overlay masks the final decision reason (pilot_runner does exactly this).
    trade["or_overlay_reason"] = "or_overlay_not_candidate"
    trade["final_reject_reason"] = "or_overlay_not_candidate"
    fields_ok = all(
        f in EVENT_FIELDS
        for f in ("pbv2_internal_reason", "pbv2_internal_gate", "or_overlay_reason", "final_reject_reason")
    )
    ok = (
        trade.get("pbv2_internal_reason") == "entry_cluster_guard"
        and trade.get("pbv2_internal_gate") == "entry_cluster_guard"
        and trade.get("final_reject_reason") == "or_overlay_not_candidate"
        and state.pbv2_internal_reason_counts.get("entry_cluster_guard") == 1
        and fields_ok
    )
    return _result(
        "T3",
        "OR overlay mask preserves pbv2_internal_reason (+CSV/JSONL fields wired)",
        ok,
        f"internal={trade.get('pbv2_internal_reason')} final={trade.get('final_reject_reason')} "
        f"event_fields_wired={fields_ok}",
    )


def _iter_20260629_candidates(limit: int):
    import glob

    paths = sorted(
        glob.glob(str(NATIVE_ROOT / "results" / "small_paper" / "20260629" / "live_session_*" / "small_paper_events.jsonl"))
    )
    n = 0
    for p in paths:
        with open(p, encoding="utf-8") as f:
            for line in f:
                if n >= limit:
                    return
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if row.get("event_type") != "candidate":
                    continue
                yield row
                n += 1


def test_t4_20260629_no_mass_reject(config: Any) -> dict[str, Any]:
    guard = _load_guard_state(config, reject_csubs=COLLAPSE_REJECT_CSUBS, exception_enabled=True)
    total = 0
    blocked_new = 0
    reject_classified = 0  # what the 6/29 code would have rejected
    feature_incomplete = 0
    for row in _iter_20260629_candidates(T4_SAMPLE_LIMIT):
        total += 1
        chk = guard.check(dict(row))
        if chk.blocked:
            blocked_new += 1
        if chk.cluster_guard_status == "FEATURE_INCOMPLETE":
            feature_incomplete += 1
            reject_classified += 1  # incomplete path only triggers when classification was a reject
        elif chk.blocked or chk.via_exception:
            reject_classified += 1
    if total == 0:
        return _result("T4", "6/29 data regression", False, "no 20260629 candidate rows found")
    blocked_rate = 100.0 * blocked_new / total
    legacy_rate = 100.0 * reject_classified / total
    ok = blocked_rate < 1.0 and reject_classified > 0.5 * total
    return _result(
        "T4",
        "6/29-equivalent data: cluster guard 99% reject does not recur",
        ok,
        f"sample={total} new_blocked={blocked_new} ({blocked_rate:.2f}%) "
        f"legacy_reject_classified={reject_classified} ({legacy_rate:.2f}%) "
        f"feature_incomplete_tagged={feature_incomplete}",
    )


def test_t5_phase621_coexistence(config: Any) -> dict[str, Any]:
    from small_paper.phase627_preflight import phase627_preflight_checks

    raw = config.raw
    v2 = bool(raw.get("freshness_semantics_v2_enabled"))
    csubs = list(raw.get("entry_cluster_guard_reject_csubs", [0, 2, 3, 5]))
    errors = phase627_preflight_checks(config, repo_root=REPO_ROOT)
    gate_ok = True
    try:
        config.make_exposure_gate(repo_root=REPO_ROOT, run_session_key="00010101/phase627_regression")
    except Exception as exc:  # noqa: BLE001
        gate_ok = False
        errors.append(f"make_exposure_gate failed: {exc}")
    ok = v2 and csubs == [] and not errors and gate_ok
    return _result(
        "T5",
        "coexists with Phase621 freshness v2 production config",
        ok,
        f"freshness_v2={v2} reject_csubs={csubs} preflight_errors={errors[:3]} gate_build={gate_ok}",
    )


def test_t6_run_paper_trade_preflight() -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(NATIVE_ROOT / "src")
    env.setdefault("PYTHONIOENCODING", "utf-8")
    steps = [
        ("check_live_pipeline_preflight", [sys.executable, str(NATIVE_ROOT / "scripts" / "check_live_pipeline_preflight.py")]),
        (
            "production_startup_smoke_test",
            [
                sys.executable,
                str(NATIVE_ROOT / "scripts" / "run_production_startup_smoke_test.py"),
                "--exit-policy-shadow",
                "trailing-mfe",
            ],
        ),
    ]
    details = []
    ok = True
    for name, cmd in steps:
        proc = subprocess.run(cmd, cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, timeout=600)
        details.append(f"{name}: exit={proc.returncode}")
        if proc.returncode != 0:
            ok = False
            tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
            details.append(" | ".join(tail))
    return _result("T6", "run_paper_trade.bat preflight chain passes", ok, "; ".join(details))


def main() -> int:
    from small_paper.config import load_pilot_config

    config = load_pilot_config(PRODUCTION_YAML)
    results = [
        test_t1_feature_incomplete_no_reject(config),
        test_t2_feature_complete_reject(config),
        test_t3_or_overlay_mask_preserves_internal_reason(),
        test_t4_20260629_no_mass_reject(config),
        test_t5_phase621_coexistence(config),
        test_t6_run_paper_trade_preflight(),
    ]
    all_pass = all(r["passed"] for r in results)

    out_dir = NATIVE_ROOT / "results" / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / "phase627_regression_summary.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["test_id", "name", "passed", "detail"])
        w.writeheader()
        w.writerows(results)

    report = {
        "verdict": PHASE627_VERDICT if all_pass else "phase627_regression_failed",
        "all_pass": all_pass,
        "production_yaml": str(PRODUCTION_YAML),
        "results": results,
    }
    (out_dir / "phase627_regression_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"[{mark}] {r['test_id']} {r['name']}: {r['detail']}")
    print(f"verdict={report['verdict']}")
    return 0 if all_pass else 1


if __name__ == "__main__":
    for p in (str(NATIVE_ROOT / "src"), str(NATIVE_ROOT), str(REPO_ROOT)):
        if p not in sys.path:
            sys.path.insert(0, p)
    raise SystemExit(main())
