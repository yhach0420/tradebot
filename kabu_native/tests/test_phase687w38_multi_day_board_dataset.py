"""Phase687W38 — multi-day board dataset append eligibility / idempotency / schema."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from research.board_entry_dataset_append import (
    SCHEMA_VERSION,
    append_session,
    detect_session_meta,
    import_existing_dataframe,
    is_eligible,
    load_manifest,
    maybe_append_session_board_dataset,
    reanalysis_gate,
)


def test_reanalysis_gates():
    assert reanalysis_gate(1) == "ACCUMULATING"
    assert reanalysis_gate(5) == "INTERIM_CHECK_ONLY"
    assert reanalysis_gate(10) == "CANDIDATE_STABILITY_EVAL"
    assert reanalysis_gate(20) == "READY_FOR_ADOPTION_REVIEW"


def test_ineligible_invalid_no_push(tmp_path: Path):
    day = tmp_path / "20260716"
    sess = day / "live_session_122532"
    sess.mkdir(parents=True)
    (sess / "small_paper_summary.json").write_text(
        json.dumps({"session_validity": "INVALID_NO_PUSH", "accepted_count": 0, "am_pm_session": {"kind": "pm"}}),
        encoding="utf-8",
    )
    (sess / "session_seal.json").write_text(
        json.dumps({"session_seal_status": "NOT_SEALED"}),
        encoding="utf-8",
    )
    # minimal native layout
    (tmp_path / "src" / "small_paper").mkdir(parents=True)
    (tmp_path / "data").mkdir(parents=True)
    # detect uses session parent name as trading_date; put under native-like tree
    native = tmp_path
    paper = native / "results" / "small_paper" / "20260716" / "live_session_122532"
    paper.mkdir(parents=True)
    (paper / "small_paper_summary.json").write_text(
        (sess / "small_paper_summary.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (paper / "session_seal.json").write_text(
        (sess / "session_seal.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    meta = detect_session_meta(paper)
    ok, reason = is_eligible(meta)
    assert ok is False
    assert "VALID_SESSION" in reason or "SEALED" in reason
    r = append_session(native_root=native, session_dir=paper)
    assert r["status"] == "SKIPPED_INELIGIBLE"


def test_import_idempotent(tmp_path: Path):
    (tmp_path / "src" / "small_paper").mkdir(parents=True)
    (tmp_path / "data").mkdir(parents=True)
    df = pd.DataFrame(
        [
            {
                "trading_date": "20260716",
                "symbol": "6506.T",
                "symbol_code": "6506",
                "entry_time": "2026-07-16T10:00:00+09:00",
                "route": "PBV2",
                "board_sync_ok": True,
                "board_sync_lag_sec": 0.1,
                "sync_clock": "received_at_jst_backward",
                "board_at_entry_imbalance_l5": 0.1,
                "pnl_pct": 0.1,
            }
        ]
    )
    r1 = import_existing_dataframe(
        native_root=tmp_path,
        df=df,
        trading_date="20260716",
        session_kind="am",
        session_id="live_session_073602",
    )
    assert r1["status"] == "INGESTED"
    r2 = import_existing_dataframe(
        native_root=tmp_path,
        df=df,
        trading_date="20260716",
        session_kind="am",
        session_id="live_session_073602",
    )
    assert r2["status"] == "SKIPPED_ALREADY_INGESTED"
    man = load_manifest(tmp_path / "results" / "research" / "board_entry_dataset")
    assert man["schema_version"] == SCHEMA_VERSION
    assert man["n_trading_days"] == 1


def test_am_pm_append_does_not_overwrite(tmp_path: Path):
    (tmp_path / "src" / "small_paper").mkdir(parents=True)
    (tmp_path / "data").mkdir(parents=True)
    df_am = pd.DataFrame(
        [
            {
                "trading_date": "20260716",
                "symbol": "6506.T",
                "symbol_code": "6506",
                "entry_time": "2026-07-16T10:00:00+09:00",
                "route": "PBV2",
                "board_sync_ok": True,
                "board_sync_lag_sec": 0.1,
                "sync_clock": "received_at_jst_backward",
                "board_at_entry_imbalance_l5": 0.1,
                "pnl_pct": 0.1,
            }
        ]
    )
    df_pm = pd.DataFrame(
        [
            {
                "trading_date": "20260716",
                "symbol": "6474.T",
                "symbol_code": "6474",
                "entry_time": "2026-07-16T13:00:00+09:00",
                "route": "PBV2",
                "board_sync_ok": True,
                "board_sync_lag_sec": 0.2,
                "sync_clock": "received_at_jst_backward",
                "board_at_entry_imbalance_l5": -0.1,
                "pnl_pct": -0.2,
            }
        ]
    )
    import_existing_dataframe(
        native_root=tmp_path,
        df=df_am,
        trading_date="20260716",
        session_kind="am",
        session_id="live_session_am",
    )
    import_existing_dataframe(
        native_root=tmp_path,
        df=df_pm,
        trading_date="20260716",
        session_kind="pm",
        session_id="live_session_pm",
    )
    part = tmp_path / "results" / "research" / "board_entry_dataset" / "trading_date=20260716" / "entries.parquet"
    out = pd.read_parquet(part)
    assert len(out) == 2
    assert set(out["session_kind"]) == {"am", "pm"}
    assert set(out["symbol_code"]) == {"6506", "6474"}


def test_fail_open_wrapper(tmp_path: Path):
    # nonexistent session → error dict, not raise
    r = maybe_append_session_board_dataset(
        native_root=tmp_path,
        session_dir=tmp_path / "missing",
        summary={"session_validity": "VALID_SESSION"},
    )
    assert r.get("status") in ("ERROR", "SKIPPED_INELIGIBLE")
