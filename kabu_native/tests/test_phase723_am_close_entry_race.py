# -*- coding: utf-8 -*-
"""Phase723: session-close ENTRY race + Discord summary-after-local-save."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from small_paper.pilot_runner import (
    REJECT_SESSION_CLOSING,
    _LiveRunState,
    _entry_admission_closed,
    _execute_accepted_entry,
    _process_scan_flush,
    _reject_session_closing_entry,
)


@dataclass
class _FakeCand:
    symbol: str
    trade: dict
    payload: dict
    msg_i: int = 0
    entry_signal_mono: float = 0.0
    freshness: Any = field(
        default_factory=lambda: MagicMock(
            data_source="push", price_age_sec=0.1, board_age_sec=0.1
        )
    )


@dataclass
class _FakeFlush:
    accepted: list
    scan_id: str = "scan-close"
    entry_candidates_count: int = 1


class _MemWriter:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def append_event(self, row: dict) -> None:
        self.events.append(dict(row))

    def append_entry_scan_audit(self, *_a: Any, **_k: Any) -> None:
        return None


def _ctx(*, closed: bool = False) -> Any:
    st = _LiveRunState(started_mono=time.monotonic())
    st.entry_admission_closed = closed
    st.session_force_close_done = closed
    st.stop_requested = closed
    st.stop_reason = "morning_session_close" if closed else ""
    st.reject_rows = []
    st.events = []
    st.accepted_rows = []
    st.bucket_summary = {}
    writer = _MemWriter()
    ctx = MagicMock()
    ctx.state = st
    ctx.writer = writer
    ctx.source = "live"
    ctx.config = MagicMock()
    ctx.am_pm_policy = None
    ctx.extension_bus = None
    ctx.stage_profiler = None
    return ctx


def test_entry_admission_closed_flag_true_when_force_close() -> None:
    ctx = _ctx(closed=True)
    assert _entry_admission_closed(ctx) is True


def test_reject_session_closing_records_operational_boundary() -> None:
    ctx = _ctx(closed=True)
    _reject_session_closing_entry(
        ctx,
        sym="7803.T",
        trade={"symbol": "7803.T", "continuation_quality_score": 0.9},
        payload={"CurrentPrice": 370},
        msg_i=1,
    )
    assert ctx.state.reject_rows
    assert ctx.state.reject_rows[-1]["final_reject_reason"] == REJECT_SESSION_CLOSING
    assert ctx.state.reject_rows[-1]["operational_boundary_reject"] is True
    assert any(e.get("reject_reason") == REJECT_SESSION_CLOSING for e in ctx.writer.events)


def test_process_scan_flush_drops_queued_accepts_when_closing() -> None:
    ctx = _ctx(closed=True)
    flush = _FakeFlush(
        accepted=[
            _FakeCand(
                symbol="7803.T",
                trade={"symbol": "7803.T", "continuation_quality_score": 0.8},
                payload={"CurrentPrice": 370},
                msg_i=99,
            )
        ]
    )
    _process_scan_flush(ctx, flush)
    assert len(ctx.state.accepted_rows) == 0
    assert any(r.get("final_reject_reason") == REJECT_SESSION_CLOSING for r in ctx.state.reject_rows)


def test_execute_accepted_entry_rejects_at_register_when_closing() -> None:
    ctx = _ctx(closed=True)
    decision = MagicMock(accept=True, reason="ok", continuation_quality_score=0.9, quality_tier="A")
    _execute_accepted_entry(
        ctx,
        sym="7803.T",
        trade={"symbol": "7803.T", "continuation_quality_score": 0.9},
        decision=decision,
        payload={"CurrentPrice": 370},
        enriched={},
        msg_i=7,
        bucket="am",
        score5_ord=None,
    )
    assert len(ctx.state.accepted_rows) == 0
    assert any(r.get("final_reject_reason") == REJECT_SESSION_CLOSING for r in ctx.state.reject_rows)


def test_discord_session_end_worker_loads_summary_capture_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from small_paper.bounded_side_task import _execute_request

    session_dir = tmp_path / "live_session_demo"
    session_dir.mkdir()
    tmp_dir = session_dir / "_side_task_tmp" / "discord_session_end-demo"
    tmp_dir.mkdir(parents=True)
    summary = {
        "trading_date": "20260723",
        "stop_reason": "morning_session_close",
        "accepted_count": 49,
        "canonical_total_pnl_yen_100": -148850.0,
        "am_pm_session": {"kind": "am"},
        "canonical_summary": {
            "total_pnl_yen_100": -148850.0,
            "trade_count": 49,
            "win_count": 10,
            "loss_count": 39,
        },
        "session_validity": "VALID_SESSION",
    }
    (session_dir / "small_paper_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False), encoding="utf-8"
    )
    monkeypatch.setenv("DISCORD_CAPTURE_ONLY", "1")
    out = _execute_request(
        {
            "task": "discord_session_end",
            "task_id": "discord_session_end-demo",
            "session_dir": str(session_dir),
            "tmp_dir": str(tmp_dir),
            "extra": {
                "native_root": str(tmp_path),
                "summary_path": str(session_dir / "small_paper_summary.json"),
                "dedupe_key": "am_summary|20260723",
            },
        }
    )
    assert out.get("ok") is True
    capture = tmp_dir / "discord_capture.json"
    assert capture.is_file()
    cap = json.loads(capture.read_text(encoding="utf-8"))
    assert cap["capture_only"] is True
    assert cap["accepted_count"] == 49


def test_discord_session_end_missing_summary_is_pending(tmp_path: Path) -> None:
    from small_paper.bounded_side_task import _execute_request

    session_dir = tmp_path / "sess"
    session_dir.mkdir()
    tmp_dir = session_dir / "_side_task_tmp" / "x"
    tmp_dir.mkdir(parents=True)
    out = _execute_request(
        {
            "task": "discord_session_end",
            "task_id": "x",
            "session_dir": str(session_dir),
            "tmp_dir": str(tmp_dir),
            "extra": {},
        }
    )
    assert out.get("ok") is False
    assert out.get("pending") is True
