"""V26G13: O(1) exact candidate_event_count. Values match legacy full scan."""
from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

from small_paper.evaluation_reachability import EvaluationReachabilityTracker, READY_WARMUP
from small_paper.pilot_runner import (
    _canonical_candidate_event_count,
    _note_candidate_event_recorded,
    _sync_reachability_summary,
)
from small_paper.v1r_activation_binding import file_sha256
from small_paper.v1r_exit_v2_activation_gate import STRATEGY_SHA
from small_paper.v1r_exit_v2_contract import EXIT_V2_CANDIDATE_SHA
from small_paper.v1r_native_entry_live import ENTRY_SHA
from small_paper.v1r_primary_runtime import ANCHOR_SHA

NATIVE = Path(__file__).resolve().parents[1]
PROSP = NATIVE / "results/research/v1r_exit_v2_prospective_activation"
C10_DUALLANE_SHA = "2cdb61f2e5f39a8f4ef782fa3d0059797b70c015887df5d94aa0520ba04b66f6"
C12_ID = "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G12_12"
C12_SHA = "7769527e34e6b2df323a36c0b65162d603a5bf55b2f62120b5d3e42fd7abff95"
V25_SHA = "46ce502c2373868f3b231bf8a3762cd47d706132698731b35e770c5f8a575d83"
C10_SHA = "b89c39881b2ba48c2d1b051c28acf0221e7f361b46e55f0a1a3b99abafc6c20e"
C11_SHA = "d1ada73cd2434abda895db3fd7977d16d17de550dbbf5038c5ae76b1fee4d9c1"
STRATEGY = "9ad4ba2730892d40c757d940b82480e620e502e3e789839120e90b18be082547"
ENTRY = "f2887bb2be539cc173aee438a43ee8afb8cfa2b8c31380937ecd843e90dd9b29"
ANCHOR = "4a2f176ef6f52458cb0e5b38764275e6ddafc01e1849693965b116089514eac2"
EXIT = "6cc3b8aade76e323682ec39dfd06878aab0ff1a99dd42922744b0054a7ea3255"
LIVE_EVENTS = (
    NATIVE / "results" / "small_paper" / "20260826" / "live_session_080751" / "small_paper_events.jsonl"
)


def _legacy(events) -> int:
    return int(sum(1 for e in events if isinstance(e, dict) and e.get("event_type") == "candidate"))


def _ctx(events=None):
    tr = EvaluationReachabilityTracker()
    state = SimpleNamespace(
        events=list(events or []),
        accepted_rows=[],
        evaluation_reachability_summary={},
    )
    ctx = SimpleNamespace(
        evaluation_reachability=tr,
        state=state,
        entry_eligible_symbols={"285A", "3907"},
    )
    tr.seed_candidate_event_count_from_events(state.events)
    state._evaluation_reachability_tracker = tr
    return ctx


def _record_candidate(ctx, extra=None) -> None:
    row = {"event_type": "candidate"}
    if extra:
        row.update(extra)
    ctx.state.events.append(row)
    _note_candidate_event_recorded(ctx)


def test_identity_pins_unchanged() -> None:
    assert STRATEGY_SHA == STRATEGY
    assert ENTRY_SHA == ENTRY
    assert ANCHOR_SHA == ANCHOR
    assert EXIT_V2_CANDIDATE_SHA == EXIT
    dual = NATIVE / "src" / "small_paper" / "v1r_live_dual_lane.py"
    assert file_sha256(dual) == C10_DUALLANE_SHA
    c12 = json.loads((PROSP / f"{C12_ID}.json").read_text(encoding="utf-8"))
    assert c12.get("sha256") == C12_SHA
    v25 = json.loads((PROSP / "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V25.json").read_text(encoding="utf-8"))
    assert v25.get("sha256") == V25_SHA
    c10 = json.loads((PROSP / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G10_10.json").read_text(encoding="utf-8"))
    c11 = json.loads((PROSP / "V1R_EXIT_V2_PAPER_PRIMARY_CANDIDATE_V26G11_11.json").read_text(encoding="utf-8"))
    assert c10.get("sha256") == C10_SHA
    assert c11.get("sha256") == C11_SHA
    sel = json.loads((PROSP / "active_v1r_activation.json").read_text(encoding="utf-8"))
    assert sel.get("activation_id") == "V1R_EXIT_V2_PAPER_PRIMARY_ACTIVATION_V25"


def test_new_session_starts_at_zero() -> None:
    ctx = _ctx()
    assert ctx.evaluation_reachability.candidate_event_count == 0
    _sync_reachability_summary(ctx)
    assert ctx.state.evaluation_reachability_summary["candidate_count"] == 0
    assert _legacy(ctx.state.events) == 0


def test_increment_only_on_actual_append() -> None:
    ctx = _ctx()
    _sync_reachability_summary(ctx)
    _sync_reachability_summary(ctx)
    assert ctx.evaluation_reachability.candidate_event_count == 0
    _record_candidate(ctx)
    _record_candidate(ctx)
    ctx.state.events.append({"event_type": "rejected"})
    _sync_reachability_summary(ctx)
    assert ctx.evaluation_reachability.candidate_event_count == 2
    assert ctx.state.evaluation_reachability_summary["candidate_count"] == 2
    assert _legacy(ctx.state.events) == 2


def test_summary_and_pbv2_do_not_double_count() -> None:
    ctx = _ctx()
    _record_candidate(ctx)
    for _ in range(20):
        _sync_reachability_summary(ctx)
    assert ctx.evaluation_reachability.candidate_event_count == 1
    assert _legacy(ctx.state.events) == 1


def test_restore_one_shot_then_increment() -> None:
    existing = [{"event_type": "candidate"}] * 7 + [{"event_type": "rejected"}] * 3
    ctx = _ctx(existing)
    assert ctx.evaluation_reachability.candidate_event_count == 7
    _record_candidate(ctx)
    assert ctx.evaluation_reachability.candidate_event_count == 8
    assert ctx.evaluation_reachability.candidate_event_count == _legacy(ctx.state.events)


def test_restore_seed_is_idempotent() -> None:
    ctx = _ctx([{"event_type": "candidate"}] * 4)
    ctx.evaluation_reachability.seed_candidate_event_count_from_events(
        ctx.state.events + [{"event_type": "candidate"}] * 50
    )
    assert ctx.evaluation_reachability.candidate_event_count == 4


def test_session_reset_clears_counter() -> None:
    ctx = _ctx()
    _record_candidate(ctx)
    ctx.state.events.clear()
    ctx.evaluation_reachability.reset_candidate_event_count()
    assert ctx.evaluation_reachability.candidate_event_count == 0
    _sync_reachability_summary(ctx)
    assert ctx.state.evaluation_reachability_summary["candidate_count"] == 0


def test_invalidate_then_restore_from_replaced_events() -> None:
    ctx = _ctx()
    _record_candidate(ctx)
    restored = [{"event_type": "candidate"}] * 11
    ctx.state.events = restored
    ctx.evaluation_reachability.invalidate_candidate_event_count()
    ctx.evaluation_reachability.seed_candidate_event_count_from_events(ctx.state.events)
    assert ctx.evaluation_reachability.candidate_event_count == 11


def test_resync_does_not_reset_counter() -> None:
    ctx = _ctx()
    _record_candidate(ctx)
    _record_candidate(ctx)
    ctx.evaluation_reachability.apply_realtime_resync_watermark(
        head_seq=24419, head_event_time="2026-08-26T08:50:04.322+09:00", generation=1
    )
    assert ctx.evaluation_reachability.candidate_event_count == 2
    assert ctx.evaluation_reachability.get("285A").readiness == READY_WARMUP


def test_live_events_jsonl_restore_parity() -> None:
    if not LIVE_EVENTS.is_file():
        return
    events = []
    with LIVE_EVENTS.open(encoding="utf-8") as fh:
        for i, line in enumerate(fh):
            if not line.strip():
                continue
            events.append(json.loads(line))
            if i >= 25000:
                break
    tr = EvaluationReachabilityTracker()
    tr.seed_candidate_event_count_from_events(events)
    assert tr.candidate_event_count == _legacy(events)


def test_checkpoint_parity_10k_steps() -> None:
    ctx = _ctx()
    mismatches = 0
    for i in range(1, 1201):
        et = "candidate" if i % 3 != 0 else "rejected"
        ctx.state.events.append({"event_type": et})
        if et == "candidate":
            _note_candidate_event_recorded(ctx)
        if i in {10, 30, 60, 90, 120, 300, 600, 900, 1200}:
            _sync_reachability_summary(ctx)
            got = int(ctx.state.evaluation_reachability_summary["candidate_count"])
            if got != _legacy(ctx.state.events):
                mismatches += 1
    assert mismatches == 0
    assert _canonical_candidate_event_count(ctx.state) == _legacy(ctx.state.events)


def test_summary_fields_do_not_gain_candidate_count() -> None:
    tr = EvaluationReachabilityTracker()
    fields = tr.summary_fields()
    assert "candidate_count" not in fields


def test_complexity_is_flat_vs_events_n() -> None:
    ns = (10_000, 30_000, 60_000, 90_000, 120_000, 200_000)
    ms = {}
    for n in ns:
        events = [{"event_type": "candidate" if i % 5 == 0 else "rejected"} for i in range(n)]
        ctx = _ctx(events)
        for s in ("285A", "3907", "5801"):
            ctx.evaluation_reachability.get(s)
        _sync_reachability_summary(ctx)
        t0 = time.perf_counter()
        loops = 8
        for _ in range(loops):
            _sync_reachability_summary(ctx)
        ms[n] = (time.perf_counter() - t0) / loops * 1000.0
        assert ctx.state.evaluation_reachability_summary["candidate_count"] == _legacy(events)
    ratio = ms[200_000] / max(ms[10_000], 1e-9)
    assert ms[200_000] < 2.0, ms
    assert ratio < 4.0, ms
    slope = (ms[200_000] - ms[10_000]) / 190_000.0
    assert slope < 5e-6, (slope, ms)
