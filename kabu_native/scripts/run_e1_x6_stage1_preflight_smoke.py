"""Stage-1 preflight: P1 completeness + 20260723 AM smoke via same writer/schema path.

Exit non-zero on any failure. Do NOT start full A/B if this fails.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
REPO = NATIVE.parent
for p in (str(SRC), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)


def main() -> int:
    from research.e1_x6_provisional.analysis_mask import build_mask_index
    from research.e1_x6_provisional.canonical_partition_replay import (
        assert_signal_ledger_nonempty_when_decisions_or_trades,
        replay_partition,
    )
    from research.e1_x6_provisional.p0_manifest import build_source_manifest
    from research.e1_x6_provisional.p1_lock import build_p1_lock
    from research.e1_x6_provisional.p2_execute import load_partition_events
    from research.e1_x6_provisional.pipeline import _confirm_plan_unique
    from research.e1_x6_provisional.util import (
        new_final_run_id,
        norm_cache_dir,
        progress,
        sha256_file,
        sha256_obj,
        temp_work_root,
        write_json,
    )
    from small_paper.e1_x5_dmid_score_provider import DMidD4H6ScoreProvider

    plan = _confirm_plan_unique()
    progress(f"PREFLIGHT plan unique={plan['unique']} version={plan['version_on_disk']} ok={plan['version_ok']}")
    if not plan["unique"] or not plan["version_ok"]:
        print("FAIL: plan SoT", plan)
        return 2

    # Fixtures first
    import pytest

    rc = pytest.main(
        ["-q", "--tb=line", str(NATIVE / "tests" / "test_e1_x6_research_builder_contracts.py")]
    )
    if rc != 0:
        print(f"FAIL: fixtures pytest_rc={rc}")
        return 3

    run_id = f"preflight_{new_final_run_id()}"
    work = temp_work_root(run_id)
    work.mkdir(parents=True, exist_ok=True)

    m = build_source_manifest(final=True)
    sm_sha = sha256_obj({k: v for k, v in m.items() if k != "sha256"})
    idx = build_mask_index(m)
    mask_sha = sha256_obj(
        [
            {
                "day": w.get("day"),
                "am_pm": w.get("am_pm"),
                "analysis_mask_id": w.get("analysis_mask_id"),
                "valid_window_start": w.get("valid_window_start"),
                "valid_window_end": w.get("valid_window_end"),
            }
            for w in (m.get("windows") or [])
        ]
    )
    p1 = build_p1_lock(
        run_id=run_id,
        source_manifest_sha256=sm_sha,
        analysis_mask_sha256=mask_sha,
        plan_version=plan["version_on_disk"],
        plan_sha256=plan["sha256"],
    )
    write_json(work / "p1_lock.json", p1)
    if p1.get("p1_precommit_status") != "P1_PRECOMMIT_COMPLETE":
        print("FAIL: P1_PRECOMMIT_INCOMPLETE", p1.get("p1_precommit_missing"))
        return 4
    for k in (
        "config_fingerprint",
        "dependency_versions",
        "numeric_precision",
        "schema_shas",
        "canonical_event_sort",
        "test_code_sha",
    ):
        if not p1.get(k):
            print(f"FAIL: P1 null field {k}")
            return 4

    events, info, uni, gaps = load_partition_events(
        "20260723", "AM", idx, cache_dir=norm_cache_dir()
    )
    progress(f"PREFLIGHT 20260723 AM events={len(events)} qc={info.get('quality_class')}")
    if not events:
        print("FAIL: no events for 20260723 AM")
        return 5

    def factory():
        p = DMidD4H6ScoreProvider.maybe_create()
        assert p and p.ready
        return p

    part = replay_partition(
        day="20260723",
        am_pm="AM",
        events_in_valid_window=events,
        universe=uni,
        provider_factory=factory,
        entry_mode="X5",
        mask_meta=info,
        gap_intervals=gaps,
        collect_score_rows=False,
    )
    assert_signal_ledger_nonempty_when_decisions_or_trades(
        signal_ledger=part.signal_ledger,
        decision_ledger=part.decision_ledger,
        completed_trades=part.completed_trades,
    )
    # Same writer path as full run
    smoke_dir = work / "smoke" / "20260723_AM"
    smoke_dir.mkdir(parents=True, exist_ok=True)
    write_json(smoke_dir / "signal_ledger.json", part.signal_ledger)
    write_json(smoke_dir / "decision_ledger.json", part.decision_ledger)
    write_json(smoke_dir / "completed_trades.json", part.completed_trades)
    write_json(smoke_dir / "censored_ledger.json", part.censored_ledger)
    shas = {
        "signal_ledger_sha256": sha256_obj(part.signal_ledger),
        "decision_ledger_sha256": sha256_obj(part.decision_ledger),
        "completed_trade_ledger_sha256": sha256_obj(part.completed_trades),
        "metrics": part.metrics(),
    }
    write_json(smoke_dir / "ledger_shas.json", shas)

    # Excel path smoke (openpyxl write of Tests-like sheet)
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Tests"
    ws.append(["test_name", "result"])
    ws.append(["preflight_20260723_AM", "PASS"])
    xlsx_path = smoke_dir / "preflight_audit_smoke.xlsx"
    wb.save(xlsx_path)
    xsha = hashlib.sha256(xlsx_path.read_bytes()).hexdigest()

    summary = {
        "status": "PREFLIGHT_PASS",
        "run_id": run_id,
        "plan": plan,
        "p1_lock_sha256": p1["p1_lock_sha256"],
        "p1_precommit_status": p1["p1_precommit_status"],
        "smoke": {
            "day": "20260723",
            "am_pm": "AM",
            "events": len(events),
            "trades": len(part.completed_trades),
            "signals": len(part.signal_ledger),
            "decisions": len(part.decision_ledger),
            "shas": shas,
            "xlsx_sha256": xsha,
        },
        "safety": {"submit": 0, "cancel": 0, "live": 0},
    }
    write_json(work / "preflight_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    progress("PREFLIGHT_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
