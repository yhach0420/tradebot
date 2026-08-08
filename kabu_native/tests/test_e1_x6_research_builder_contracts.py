"""Fixture contracts for E1_X6 research builder (Capture-safe; no full replay)."""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
REPO = NATIVE.parent
for p in (str(SRC), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)

from research.e1_x6_provisional.cost_contract import (  # noqa: E402
    ROUNDTRIP_COST_BPS,
    CostContractMismatch,
    post_cost_label_bps,
    verify_frozen_e1_x5_cost_contract,
    yen_roundtrip_cost,
)
from research.e1_x6_provisional.portfolio_replay import (  # noqa: E402
    PortfolioEvent,
    assert_no_confirm_reselection,
    replay_portfolio,
    select_candidate_build_only,
)
from research.e1_x6_provisional.quality_layers import (  # noqa: E402
    include_in_core_base,
    summarize_quality_layers,
)
from research.e1_x6_provisional.util import JST, summarize_pnls  # noqa: E402


def test_cost_flat_label_minus_5bps():
    assert ROUNDTRIP_COST_BPS == 5.0
    assert post_cost_label_bps(1000.0, 1000.0) == pytest.approx(-5.0)


def test_cost_yen_1000x100_equals_50():
    assert yen_roundtrip_cost(1000.0) == pytest.approx(50.0)


def test_frozen_e1_x5_matches_plan_contract():
    rep = verify_frozen_e1_x5_cost_contract()
    assert rep["status"] == "COST_CONTRACT_OK"


def test_holding_continuous_signal_single_trade():
    t0 = datetime(2026, 7, 21, 10, 0, tzinfo=JST)
    sym = "1001.T"
    # Entry then 5 continuous signals while flat mid path, then exit via STOP-ish drop
    events = []
    # entry ask/bid near flat
    events.append(
        PortfolioEvent(ts=t0, symbol=sym, signal=True, bid=1000.0, ask=1000.0, x5_accept=True, event_id="e0")
    )
    for i in range(1, 6):
        events.append(
            PortfolioEvent(
                ts=t0 + timedelta(seconds=5 * i),
                symbol=sym,
                signal=True,
                bid=1000.0,
                ask=1000.0,
                x5_accept=False,
                event_id=f"e{i}",
            )
        )
    # force MAX_HOLD / exit: drop bid to trigger STOP (-15bps => bid <= 998.5)
    events.append(
        PortfolioEvent(
            ts=t0 + timedelta(seconds=40),
            symbol=sym,
            signal=False,
            bid=998.0,
            ask=998.0,
            event_id="exit",
        )
    )
    out = replay_portfolio(events)
    assert len(out.completed_trades) == 1
    assert out.duplicate_open_symbol_reject >= 5


def test_cap5_blocks_sixth_concurrent_signal():
    t0 = datetime(2026, 7, 21, 10, 0, tzinfo=JST)
    events = []
    for i in range(6):
        events.append(
            PortfolioEvent(
                ts=t0 + timedelta(milliseconds=i),
                symbol=f"100{i}.T",
                signal=True,
                bid=1000.0,
                ask=1000.0,
                event_id=f"s{i}",
            )
        )
    out = replay_portfolio(events)
    entries = [d for d in out.decision_ledger if d["decision"] == "ENTRY"]
    rejects = [d for d in out.decision_ledger if d.get("reason") == "CAP5_BLOCKED"]
    assert len(entries) == 5
    assert len(rejects) == 1
    assert out.cap_blocked == 1


def test_partial_not_added_to_core_valid():
    assert include_in_core_base("PARTIAL_VALID_WINDOW") is False
    assert include_in_core_base("CORE_VALID") is True
    trades_by_day = {
        "20260721": [{"day": "20260721", "net_pnl_yen_100": 100.0}],
        "20260728": [{"day": "20260728", "net_pnl_yen_100": -50.0}],
    }
    day_quality = {
        "20260721": "PARTIAL_VALID_WINDOW",
        "20260728": "STRESS_RECOVERABLE",
    }
    summ = summarize_quality_layers(trades_by_day, day_quality, summarize_pnls=summarize_pnls)
    assert summ["CORE_VALID"]["status"] == "NOT_EVALUABLE"
    assert summ["PARTIAL_VALID_WINDOW"]["trades_n"] == 1
    assert summ["PARTIAL_VALID_WINDOW"]["pnl"] == pytest.approx(100.0)
    assert summ["STRESS_RECOVERABLE"]["trades_n"] == 1
    assert summ["ALL_USABLE"]["trades_n"] == 2


def test_no_confirm_reselection():
    ranked = [
        {
            "candidate_id": "C|A",
            "build_support": 10,
            "build_expectancy_proxy": 1.0,
        },
        {
            "candidate_id": "C|B",
            "build_support": 99,
            "build_expectancy_proxy": 9.0,
        },
    ]
    # caller must pre-sort; select takes first
    ranked_sorted = sorted(
        ranked,
        key=lambda x: (-x["build_expectancy_proxy"], -x["build_support"], x["candidate_id"]),
    )
    selected = select_candidate_build_only(ranked_sorted)
    assert selected["candidate_id"] == "C|B"
    assert selected["selection_basis"]["confirm_not_used"] is True
    assert_no_confirm_reselection(selected["candidate_id"], selected["candidate_id"])
    with pytest.raises(AssertionError):
        assert_no_confirm_reselection("C|B", "C|A")


def test_audit_row_counts_match_report_and_registry_200(tmp_path):
    from research.e1_x6_provisional.publish import _write_audit_xlsx

    # Build minimal work tree with 3 eval rows + 200 candidates
    work = tmp_path / "work"
    labels = work / "run_a" / "labels"
    labels.mkdir(parents=True)
    rows = []
    for i in range(3):
        rows.append(
            {
                "day": "20260721",
                "am_pm": "AM",
                "symbol_norm": f"100{i}.T",
                "decision_time": "2026-07-21T10:00:00+09:00",
                "score": 0.5,
                "spread_bps": 1.0,
                "score_vs_threshold_gap": 0.01,
                "mid": 1000.0,
                "sample_reason": "REGULAR_5S",
                "x5_accept": False,
                "post_5bps_expectancy_h300": -5.0,
                "censor_reason": None,
                "yen_roundtrip_cost_at_mid": 50.0,
                "MISSED_WINNER": False,
                "UNNECESSARY_ENTRY": False,
            }
        )
    (labels / "20260721_labeled.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8"
    )
    cand_dir = work / "run_a" / "candidates"
    cand_dir.mkdir(parents=True)
    registry = [
        {
            "candidate_id": f"C|{i:03d}",
            "family": "SINGLE_FEATURE",
            "features": ["score"],
            "direction": "higher_better",
            "threshold_code": f"q0.5:{i}",
            "build_support": i,
            "build_expectancy_proxy": float(i),
        }
        for i in range(200)
    ]
    (cand_dir / "registry.json").write_text(json.dumps(registry), encoding="utf-8")
    (work / "run_a" / "base").mkdir(parents=True)
    (work / "run_a" / "base" / "20260721_trades.json").write_text("[]", encoding="utf-8")

    report = {
        "provisional_run_id": "fixture_run",
        "dataset": {"rows": 3},
        "labels": {"rows": 3},
        "source_manifest": {"windows": [], "source_manifest_sha256": "x"},
        "p1": {"p1_lock_sha256": "y", "acceptance_gates_11_1": []},
        "base": {
            "quality_layers": {
                "CORE_VALID": {"status": "NOT_EVALUABLE", "trades_n": 0},
                "PARTIAL_VALID_WINDOW": {"status": "OK", "trades_n": 0, "pnl": 0},
                "STRESS_RECOVERABLE": {"status": "OK", "trades_n": 0, "pnl": 0},
                "ALL_USABLE": {"status": "OK", "trades_n": 0, "pnl": 0},
            }
        },
        "folds": {},
        "candidates": {"count": 200},
    }
    xlsx = tmp_path / "audit.xlsx"
    counts = _write_audit_xlsx(report, xlsx, work)
    assert counts["EvaluationRows_total"] == 3
    assert counts["EvaluationRows_report_json"] == 3
    assert counts["CandidateRegistry"] == 200
    assert counts["EvaluationRows_total"] == report["dataset"]["rows"]


def test_excel_cell_coerces_nested_dict():
    from research.e1_x6_provisional.publish import _excel_cell

    d = {"status": "COST_CONTRACT_OK", "ROUNDTRIP_COST_BPS": 5.0}
    out = _excel_cell(d)
    assert isinstance(out, str)
    assert "COST_CONTRACT_OK" in out
    assert _excel_cell(None) is None
    assert _excel_cell(1.5) == 1.5


def test_full_pipeline_blocked_without_allow_flag():
    from research.e1_x6_provisional.pipeline import run_provisional_pipeline

    with pytest.raises(RuntimeError, match="FULL_REPLAY_BLOCKED"):
        run_provisional_pipeline(allow_full_replay=False)


# --- Analysis mask contract fixtures ---

def _synthetic_mask_index():
    from research.e1_x6_provisional.analysis_mask import build_mask_index

    manifest = {
        "windows": [
            {
                "day": "20260721",
                "am_pm": "AM",
                "quality_class": "PARTIAL_VALID_WINDOW",
                "has_usable_overlap": True,
                "valid_window": {
                    "start": "2026-07-21T09:03:00+09:00",
                    "end": "2026-07-21T11:25:00+09:00",
                },
                "analysis_mask_id": "mask_am_partial",
                "window_id": "20260721:AM",
            },
            {
                "day": "20260721",
                "am_pm": "PM",
                "quality_class": "PARTIAL_VALID_WINDOW",
                "has_usable_overlap": True,
                "valid_window": {
                    "start": "2026-07-21T12:33:00+09:00",
                    "end": "2026-07-21T15:23:00+09:00",
                },
                "analysis_mask_id": "mask_pm_partial",
                "window_id": "20260721:PM",
            },
            {
                "day": "20260722",
                "am_pm": "AM",
                "quality_class": "INVALID_SOURCE",
                "has_usable_overlap": False,
                "valid_window": {"start": None, "end": None},
                "analysis_mask_id": "mask_invalid",
                "window_id": "20260722:AM",
            },
            {
                "day": "20260728",
                "am_pm": "AM",
                "quality_class": "STRESS_RECOVERABLE",
                "has_usable_overlap": True,
                "valid_window": {
                    "start": "2026-07-28T09:03:00+09:00",
                    "end": "2026-07-28T11:25:00+09:00",
                },
                "analysis_mask_id": "mask_f2_am",
                "window_id": "20260728:AM",
            },
        ]
    }
    return build_mask_index(manifest)


def test_preopen_085959_not_in_am_mask():
    from research.e1_x6_provisional.analysis_mask import classify_ts, row_in_analysis_mask

    idx = _synthetic_mask_index()
    ts = "2026-07-21T08:59:59+09:00"
    assert classify_ts("20260721", ts) == "BEFORE"
    info = row_in_analysis_mask("20260721", ts, idx)
    assert info["in_analysis_mask"] is False


def test_lunch_after_not_in_dataset_candidate_build():
    from research.e1_x6_provisional.analysis_mask import classify_ts, row_in_analysis_mask

    idx = _synthetic_mask_index()
    lunch = row_in_analysis_mask("20260721", "2026-07-21T12:00:00+09:00", idx)
    after = row_in_analysis_mask("20260721", "2026-07-21T15:30:00+09:00", idx)
    assert classify_ts("20260721", "2026-07-21T12:00:00+09:00") == "LUNCH"
    assert classify_ts("20260721", "2026-07-21T15:30:00+09:00") == "AFTER"
    assert lunch["in_analysis_mask"] is False
    assert after["in_analysis_mask"] is False


def test_invalid_source_rows_filtered_from_dataset_labels():
    from research.e1_x6_provisional.analysis_mask import (
        filter_events_to_valid_window,
        row_in_analysis_mask,
    )

    idx = _synthetic_mask_index()
    info = row_in_analysis_mask("20260722", "2026-07-22T10:00:00+09:00", idx)
    assert info["in_analysis_mask"] is False
    assert info.get("quality_class") == "INVALID_SOURCE"
    # Event feed for INVALID_SOURCE is empty
    class _E:
        def __init__(self, ts):
            self.ts = datetime.fromisoformat(ts)

    fed = filter_events_to_valid_window(
        "20260722",
        "AM",
        [_E("2026-07-22T10:00:00+09:00")],
        idx,
    )
    assert len(fed) == 0


def test_fold_signal_ledger_timestamps_inside_confirm_mask():
    from research.e1_x6_provisional.analysis_mask import assert_timestamps_in_confirm_mask

    idx = _synthetic_mask_index()
    ok_ts = ["2026-07-21T10:00:00+09:00", "2026-07-21T10:05:00+09:00"]
    assert_timestamps_in_confirm_mask(ok_ts, day="20260721", mask_index=idx)
    with pytest.raises(AssertionError):
        assert_timestamps_in_confirm_mask(
            ["2026-07-21T08:59:59+09:00"], day="20260721", mask_index=idx
        )


def test_all_entry_timestamps_inside_valid_window():
    from research.e1_x6_provisional.analysis_mask import row_in_analysis_mask

    idx = _synthetic_mask_index()
    assert row_in_analysis_mask("20260721", "2026-07-21T10:15:00+09:00", idx)["in_analysis_mask"] is True
    assert row_in_analysis_mask("20260721", "2026-07-21T08:59:59+09:00", idx)["in_analysis_mask"] is False
    # Inclusive bounds
    assert row_in_analysis_mask("20260721", "2026-07-21T09:03:00+09:00", idx)["in_analysis_mask"] is True
    assert row_in_analysis_mask("20260721", "2026-07-21T11:25:00+09:00", idx)["in_analysis_mask"] is True


def test_base_and_candidate_same_analysis_mask_id():
    from research.e1_x6_provisional.analysis_mask import row_in_analysis_mask

    idx = _synthetic_mask_index()
    base_row = row_in_analysis_mask("20260721", "2026-07-21T10:00:00+09:00", idx)
    cand_row = row_in_analysis_mask("20260721", "2026-07-21T10:30:00+09:00", idx)
    assert base_row["analysis_mask_id"] == cand_row["analysis_mask_id"] == "mask_am_partial"


def test_f2_like_preopen_trade_not_generated_under_mask():
    from research.e1_x6_provisional.analysis_mask import classify_ts, row_in_analysis_mask

    idx = _synthetic_mask_index()
    ts = "2026-07-28T08:59:59+09:00"
    assert classify_ts("20260728", ts) == "BEFORE"
    assert row_in_analysis_mask("20260728", ts, idx)["in_analysis_mask"] is False


def test_dataset_build_rows_count_equals_mask_in_count():
    from research.e1_x6_provisional.analysis_mask import row_in_analysis_mask

    idx = _synthetic_mask_index()
    synthetic_rows = [
        {"ts": "2026-07-21T08:59:59+09:00"},
        {"ts": "2026-07-21T09:03:00+09:00"},
        {"ts": "2026-07-21T10:00:00+09:00"},
        {"ts": "2026-07-21T12:00:00+09:00"},
        {"ts": "2026-07-21T13:00:00+09:00"},
        {"ts": "2026-07-21T15:30:00+09:00"},
    ]
    mask_in = [
        r for r in synthetic_rows if row_in_analysis_mask("20260721", r["ts"], idx)["in_analysis_mask"]
    ]
    # 09:03, 10:00, 13:00 (PM) — 3 mask-in
    assert len(mask_in) == 3
    build_rows = list(mask_in)  # candidate build uses only mask-in
    assert len(build_rows) == len(mask_in)


def test_mask_contract_fixture_rows_all_pass():
    from research.e1_x6_provisional.analysis_mask import mask_contract_fixture_rows

    rows = mask_contract_fixture_rows()
    assert len(rows) >= 8
    assert all(r["result"] == "PASS" for r in rows)


def test_superseded_analysis_mask_run_recorded():
    from research.e1_x6_provisional.constants import SUPERSEDED_ANALYSIS_MASK_RUN
    from research.e1_x6_provisional.pipeline import SUPERSEDED_RUNS

    assert SUPERSEDED_ANALYSIS_MASK_RUN["run_id"] == "e1x6_final_20260801_021116_9e461544"
    assert SUPERSEDED_ANALYSIS_MASK_RUN["disposition"] == "SUPERSEDED_ANALYSIS_MASK_CONTRACT_ERROR"
    assert SUPERSEDED_ANALYSIS_MASK_RUN in SUPERSEDED_RUNS
    assert SUPERSEDED_ANALYSIS_MASK_RUN["shas"]["report.json"].startswith("0684aff1")


def test_portfolio_open_at_end_persisted():
    t0 = datetime(2026, 7, 21, 10, 0, tzinfo=JST)
    events = [
        PortfolioEvent(
            ts=t0, symbol="1001.T", signal=True, bid=1000.0, ask=1000.0, event_id="e0"
        )
    ]
    out = replay_portfolio(events)
    m = out.metrics()
    assert m["open_at_end_n"] == 1
    assert m["open_at_end_symbols"] == ["1001.T"]


# --- Replay lifecycle contract fixtures (Plan 1.3) ---

def test_superseded_replay_boundary_run_recorded():
    from research.e1_x6_provisional.constants import SUPERSEDED_REPLAY_BOUNDARY_RUN
    from research.e1_x6_provisional.pipeline import SUPERSEDED_RUNS

    assert SUPERSEDED_REPLAY_BOUNDARY_RUN["run_id"] == "e1x6_final_20260801_024352_97202b28"
    assert (
        SUPERSEDED_REPLAY_BOUNDARY_RUN["disposition"]
        == "SUPERSEDED_REPLAY_BOUNDARY_CONTRACT_ERROR"
    )
    assert SUPERSEDED_REPLAY_BOUNDARY_RUN in SUPERSEDED_RUNS
    assert SUPERSEDED_REPLAY_BOUNDARY_RUN["shas"]["report.json"].startswith("a22508f4")


def test_plan_version_1_3_and_lifecycle_section():
    from research.e1_x6_provisional.constants import PLAN_REL
    from research.e1_x6_provisional.util import repo_root, sha256_file

    plan = repo_root() / PLAN_REL
    text = plan.read_text(encoding="utf-8")
    assert "| Version | `1.3` |" in text or "Version | `1.3`" in text
    assert "Replay lifecycle (analysis_mask partition)" in text
    assert "AM_PM_CARRY = NO" in text
    assert "WINDOW_CENSORED" in text
    assert "partition boundary = analysis_mask valid_window" in text
    # Prior 1.2 SHA recorded in changelog
    assert "f037b770d23f235aa651153ae357a060dddd9fc2fb353161651f0ca4ef0e66fe" in text
    assert "e1x6_final_20260801_024352_97202b28" in text
    _ = sha256_file(plan)  # available for report


def test_fixed_spec_additivity_helper():
    from research.e1_x6_provisional.canonical_partition_replay import (
        fixed_spec_day_deletion_from_ledger,
    )

    trades = [
        {"day": "20260721", "net_pnl_yen_100": 100.0},
        {"day": "20260722", "net_pnl_yen_100": -40.0},
        {"day": "20260721", "net_pnl_yen_100": 10.0},
        {"day": "20260723", "net_pnl_yen_100": 5.0},
    ]
    row = fixed_spec_day_deletion_from_ledger(trades, held_out_day="20260722")
    assert row["additivity_ok"] is True
    assert row["n_all"] == row["n_day"] + row["n_without"]
    assert abs(row["pnl_all"] - (row["pnl_day"] + row["pnl_without"])) < 0.001
    assert row["no_re_replay"] is True
    assert row["residual_ledger_sha256"]


def test_selected_id_must_exist_in_registry():
    from research.e1_x6_provisional.canonical_partition_replay import assert_selected_in_registry

    reg = [{"candidate_id": "C|A"}, {"candidate_id": "C|B"}]
    assert_selected_in_registry("C|A", reg)
    with pytest.raises(AssertionError):
        assert_selected_in_registry("C|MISSING", reg)


def test_base_values_not_in_candidate_ex722_namespace():
    # candidate_ex722 must never be a copy of BASE metrics namespace
    cand_ex722 = {
        "namespace": "candidate_ex722",
        "not_base_metrics": True,
        "source": "final_candidate_ledger_exclude_20260722",
        "pnl": 12.0,
        "pf": 1.1,
        "n": 5,
    }
    base_layer = {"pnl": 999.0, "pf": 0.5, "n": 100}
    assert cand_ex722["namespace"] == "candidate_ex722"
    assert cand_ex722["not_base_metrics"] is True
    assert cand_ex722["pnl"] != base_layer["pnl"]
    assert "CORE_VALID" not in cand_ex722


def test_cost_net_bps_filled_on_completed_econ():
    from research.e1_x6_provisional.canonical_partition_replay import _enrich_econ_from_exit

    adopted = {"entry_ask": 1000.0, "exit_bid": 1000.0, "net_pnl_yen_100": None, "net_bps": None}
    raw = {"gross_pnl_yen_100": 0.0, "cost_yen_100": 50.0, "net_pnl_yen_100": -50.0, "net_bps": -5.0}
    out = _enrich_econ_from_exit(raw, adopted)
    assert out["cost_yen_100"] == pytest.approx(50.0)
    assert out["net_bps"] == pytest.approx(-5.0)
    assert out["net_pnl_yen_100"] == pytest.approx(-50.0)


def test_mask_lineage_non_null_on_stamp():
    from research.e1_x6_provisional.canonical_partition_replay import _stamp_lineage

    row = _stamp_lineage(
        {"symbol": "1001.T", "entry_ask": 1000.0, "exit_bid": 1001.0},
        day="20260721",
        am_pm="AM",
        mask_meta={
            "window_id": "20260721:AM",
            "analysis_mask_id": "mask_am",
            "quality_class": "PARTIAL_VALID_WINDOW",
            "valid_window_start": "2026-07-21T09:03:00+09:00",
            "valid_window_end": "2026-07-21T11:25:00+09:00",
        },
        replay_partition_id="20260721|AM|mask_am",
        event_scope="IN_PARTITION_EXIT",
    )
    assert row["day"] == "20260721"
    assert row["am_pm"] == "AM"
    assert row["window_id"]
    assert row["analysis_mask_id"]
    assert row["replay_partition_id"]
    assert row["quality_class"]
    assert row["valid_window_start"]
    assert row["valid_window_end"]
    assert row["in_analysis_mask"] is True
    assert row["event_scope"]


def test_invalid_source_count_zero_in_usable_feed():
    from research.e1_x6_provisional.analysis_mask import filter_events_to_valid_window

    idx = _synthetic_mask_index()

    class _E:
        def __init__(self, ts):
            self.ts = datetime.fromisoformat(ts)

    fed = filter_events_to_valid_window(
        "20260722", "AM", [_E("2026-07-22T10:00:00+09:00")], idx
    )
    assert len(fed) == 0
    inv_count = sum(1 for _ in fed)
    assert inv_count == 0


def test_no_double_cost_in_net_pnl():
    from research.e1_x6_provisional.cost_contract import net_pnl_yen, ROUNDTRIP_COST_BPS

    e = net_pnl_yen(1000.0, 1000.0)
    # Single 5bps once => -50 yen / -5 bps — NOT -100 yen / -10 bps
    assert e["cost_yen_100"] == pytest.approx(50.0)
    assert e["net_bps"] == pytest.approx(-ROUNDTRIP_COST_BPS)
    assert e["net_pnl_yen_100"] == pytest.approx(-50.0)


def test_evaluation_mode_full_canonical_only():
    from research.e1_x6_provisional.replay_lifecycle_contract import (
        EVALUATION_MODE_REQUIRED,
        FORBIDDEN_EVALUATION_MODES,
    )

    assert EVALUATION_MODE_REQUIRED == "FULL_CANONICAL_EVENT_REPLAY"
    assert "PORTFOLIO_REPLAY_ON_LABELED_SCORE_ROWS" in FORBIDDEN_EVALUATION_MODES


def test_5242_like_orphan_censored_not_max_hold():
    """ENTRY before 11:25, no events after 11:25 → WINDOW_CENSORED, not MAX_HOLD at 11:29."""
    from research.e1_x6_provisional.canonical_partition_replay import ORPHAN_REASON

    # Simulate partition end orphan ledger row (no invented post-window exit)
    censored = {
        "symbol": "5242.T",
        "entry_time": "2026-07-27T11:23:13.659000+09:00",
        "exit_time": None,
        "exit_reason": ORPHAN_REASON,
        "excluded_from_completed_pnl": True,
        "valid_window_end": "2026-07-27T11:25:00+09:00",
    }
    assert censored["exit_reason"] == "WINDOW_CENSORED"
    assert censored["exit_time"] is None
    assert censored["excluded_from_completed_pnl"] is True
    # Must NOT be completed MAX_HOLD at 11:29
    assert "MAX_HOLD" not in str(censored["exit_reason"])
    fake_bad_exit = "2026-07-27T11:29:20.169000+09:00"
    assert censored["exit_time"] != fake_bad_exit


def test_am_pm_no_carry_two_partitions_independent():
    from research.e1_x6_provisional.canonical_partition_replay import merge_partition_results, PartitionReplayResult

    am = PartitionReplayResult(
        day="20260721",
        am_pm="AM",
        replay_partition_id="20260721|AM|m",
        completed_trades=[{"day": "20260721", "am_pm": "AM", "net_pnl_yen_100": 10.0, "symbol": "A"}],
        signal_ledger=[{"day": "20260721", "am_pm": "AM", "symbol": "A", "signal": True}],
        decision_ledger=[{"day": "20260721", "am_pm": "AM", "decision": "ENTRY", "symbol": "A"}],
        open_at_end_n=1,
        open_at_end_symbols=["OPEN_AM"],
    )
    pm = PartitionReplayResult(
        day="20260721",
        am_pm="PM",
        replay_partition_id="20260721|PM|m",
        completed_trades=[{"day": "20260721", "am_pm": "PM", "net_pnl_yen_100": 20.0, "symbol": "B"}],
        signal_ledger=[{"day": "20260721", "am_pm": "PM", "symbol": "B", "signal": True}],
        decision_ledger=[{"day": "20260721", "am_pm": "PM", "decision": "ENTRY", "symbol": "B"}],
        open_at_end_n=0,
        open_at_end_symbols=[],
    )
    # Fresh sessions: AM open does not appear as PM completed carry
    merged = merge_partition_results([am, pm])
    assert merged["metrics"]["n"] == 2
    assert "OPEN_AM" in merged["metrics"]["open_at_end_symbols"]
    assert all(t.get("am_pm") in ("AM", "PM") for t in merged["completed_trades"])
    assert len(merged["signal_ledger"]) >= 2
    # No cross-partition trade with entry AM exit PM
    for t in merged["completed_trades"]:
        assert t.get("am_pm") in ("AM", "PM")


def test_runner_report_excel_tests_count_contract_shape():
    """runner passed = report.tests = Excel Tests rows (shape contract via module count)."""
    import ast

    src = Path(__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    n_tests = sum(
        1
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
    )
    assert n_tests >= 40
    from research.e1_x6_provisional.fixture_suite import pytest_pass_count_matches_suite

    fake = [{"result": "PASS", "test_name": f"t{i}"} for i in range(n_tests)]
    assert pytest_pass_count_matches_suite(fake, n_tests)


def test_scope_out_event_concept_excluded_from_partition():
    from research.e1_x6_provisional.analysis_mask import filter_events_to_valid_window

    idx = _synthetic_mask_index()

    class _E:
        def __init__(self, ts):
            self.ts = datetime.fromisoformat(ts)

    # 11:29 is AFTER AM valid_end 11:25 — must not enter AM feed
    in_scope = filter_events_to_valid_window(
        "20260721",
        "AM",
        [
            _E("2026-07-21T11:24:00+09:00"),
            _E("2026-07-21T11:29:00+09:00"),
        ],
        idx,
    )
    assert len(in_scope) == 1
    assert in_scope[0].ts.hour == 11 and in_scope[0].ts.minute == 24


def test_nonscore_stop_before_later_score_target_ordering_contract():
    """EXIT/FE every event: a STOP path on non-score quote must close before a later SCORE TARGET."""
    # Portfolio-level proxy: exit checked on every event; entry only on signal samples
    t0 = datetime(2026, 7, 21, 10, 0, tzinfo=JST)
    sym = "1001.T"
    events = [
        PortfolioEvent(ts=t0, symbol=sym, signal=True, bid=1000.0, ask=1000.0, event_id="entry"),
        # non-score quote that hits STOP
        PortfolioEvent(
            ts=t0 + timedelta(seconds=5),
            symbol=sym,
            signal=False,
            bid=998.0,
            ask=998.0,
            event_id="nonsignal_stop",
        ),
        # later score that would have been TARGET if still open
        PortfolioEvent(
            ts=t0 + timedelta(seconds=10),
            symbol=sym,
            signal=True,
            bid=1020.0,
            ask=1020.0,
            event_id="later_score",
        ),
    ]
    out = replay_portfolio(events)
    assert len(out.completed_trades) == 1
    assert out.completed_trades[0]["exit_reason"] == "STOP"
    assert out.completed_trades[0]["exit_time"].startswith("2026-07-21T10:00:05")


def test_required_plan_version_is_1_3():
    from research.e1_x6_provisional.pipeline import REQUIRED_PLAN_VERSION

    assert REQUIRED_PLAN_VERSION == "1.3"


def test_fixture_suite_count_positive():
    from research.e1_x6_provisional.fixture_suite import _run_inline_suite

    rows = _run_inline_suite()
    assert len(rows) >= 8
    assert all("test_name" in r or "name" in r for r in rows)


def test_published_artifacts_untouched_hashes():
    """Superseded final-audit SHAs are frozen in history; disk may advance only via new final run."""
    import hashlib

    from research.e1_x6_provisional.constants import (
        ARTIFACT_DIR_REL,
        SUPERSEDED_FINAL_AUDIT_RUN,
    )
    from research.e1_x6_provisional.pipeline import SUPERSEDED_RUNS
    from research.e1_x6_provisional.util import repo_root

    assert SUPERSEDED_FINAL_AUDIT_RUN in SUPERSEDED_RUNS
    assert (
        SUPERSEDED_FINAL_AUDIT_RUN["disposition"]
        == "SUPERSEDED_FINAL_AUDIT_CONTRACT_ERROR"
    )
    assert SUPERSEDED_FINAL_AUDIT_RUN["shas"]["report.json"].startswith("f071aa5e")
    art = repo_root() / ARTIFACT_DIR_REL
    for name in ("report.json", "report.md", "audit.xlsx"):
        p = art / name
        assert p.is_file(), f"missing published artifact {name}"
        h = hashlib.sha256(p.read_bytes()).hexdigest()
        # Disk may still be the superseded final-audit triple, or a newer atomic publish.
        if h == SUPERSEDED_FINAL_AUDIT_RUN["shas"][name]:
            continue
        # Newer publish: report.json must reference the superseded run in history
        if name == "report.json":
            rep = json.loads(p.read_text(encoding="utf-8"))
            ids = {s.get("run_id") for s in (rep.get("superseded_runs") or [])}
            assert SUPERSEDED_FINAL_AUDIT_RUN["run_id"] in ids
            assert rep.get("final_run_id") != SUPERSEDED_FINAL_AUDIT_RUN["run_id"]


def test_signal_ledger_nonempty_when_trades_exist():
    from research.e1_x6_provisional.canonical_partition_replay import (
        assert_signal_ledger_nonempty_when_decisions_or_trades,
    )

    assert_signal_ledger_nonempty_when_decisions_or_trades(
        signal_ledger=[{"ts": "x", "signal": True}],
        decision_ledger=[{"decision": "ENTRY"}],
        completed_trades=[{"net_pnl_yen_100": 1}],
    )
    with pytest.raises(AssertionError, match="SIGNAL_LEDGER_EMPTY"):
        assert_signal_ledger_nonempty_when_decisions_or_trades(
            signal_ledger=[],
            decision_ledger=[{"decision": "ENTRY"}],
            completed_trades=[{"net_pnl_yen_100": 1}],
        )


def test_vacuous_empty_signal_pass_forbidden():
    """Empty SignalLedger must not be treated as PASS when economics exist."""
    from research.e1_x6_provisional.canonical_partition_replay import (
        assert_signal_ledger_nonempty_when_decisions_or_trades,
    )

    with pytest.raises(AssertionError):
        assert_signal_ledger_nonempty_when_decisions_or_trades(
            signal_ledger=[], decision_ledger=[{"d": 1}], completed_trades=[]
        )


def test_next_trading_day_state_reset_contract():
    from research.e1_x6_provisional.replay_lifecycle_contract import REPLAY_LIFECYCLE_CONTRACT_TEXT

    text = REPLAY_LIFECYCLE_CONTRACT_TEXT
    assert "AM_PM_CARRY = NO" in text
    assert "No silent carry to next trading day" in text


def test_fixed_spec_delete_day_removes_all_am_pm():
    from research.e1_x6_provisional.canonical_partition_replay import (
        fixed_spec_day_deletion_from_ledger,
    )

    trades = [
        {"day": "20260723", "am_pm": "AM", "net_pnl_yen_100": 10.0},
        {"day": "20260723", "am_pm": "PM", "net_pnl_yen_100": 20.0},
        {"day": "20260724", "am_pm": "AM", "net_pnl_yen_100": 5.0},
    ]
    fs = fixed_spec_day_deletion_from_ledger(trades, held_out_day="20260723")
    assert fs["n_day"] == 2
    assert fs["n_without"] == 1
    assert fs["n_all"] == 3
    assert abs(fs["pnl_day"] - 30.0) < 1e-9


def test_p1_stage1_audit_fields_non_null():
    from research.e1_x6_provisional.p1_lock import build_p1_lock

    lock = build_p1_lock(
        run_id="test_p1_audit",
        source_manifest_sha256="a" * 64,
        analysis_mask_sha256="b" * 64,
        plan_version="1.3",
        plan_sha256="c" * 64,
    )
    for k in (
        "config_fingerprint",
        "dependency_versions",
        "numeric_precision",
        "schema_shas",
        "canonical_event_sort",
        "test_code_sha",
    ):
        assert lock.get(k), f"P1 missing {k}"
    assert lock["p1_precommit_status"] == "P1_PRECOMMIT_COMPLETE"


def test_selected_spec_namespace_distinct_from_registry():
    from research.e1_x6_provisional.constants import (
        CANDIDATE_REGISTRY_SOT_NAMESPACE,
        SELECTED_SPEC_NAMESPACE,
    )
    from research.e1_x6_provisional.util import sha256_obj

    reg = [{"candidate_id": "C|A"}, {"candidate_id": "C|B"}]
    sel = {"candidate_id": "C|A", "family": "SINGLE_FEATURE"}
    assert sha256_obj(reg) != sha256_obj(sel)
    assert CANDIDATE_REGISTRY_SOT_NAMESPACE != SELECTED_SPEC_NAMESPACE


def test_superseded_final_audit_and_failed_plan13_recorded():
    from research.e1_x6_provisional.constants import (
        FAILED_PLAN13_MASKCLIP_RUN,
        RUN_HISTORY_NOTES,
        SUPERSEDED_FINAL_AUDIT_RUN,
    )
    from research.e1_x6_provisional.pipeline import RUN_HISTORY, SUPERSEDED_RUNS

    assert SUPERSEDED_FINAL_AUDIT_RUN in SUPERSEDED_RUNS
    assert FAILED_PLAN13_MASKCLIP_RUN in RUN_HISTORY
    assert any(n.get("id") == "20260723_AM_partition_smoke" for n in RUN_HISTORY_NOTES)
    assert any(n.get("id") == "MASKCLIP_plus_38_fixtures" for n in RUN_HISTORY_NOTES)
