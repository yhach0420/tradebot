"""Phase413 Runtime policy tests (same_symbol_open_policy=no_overlap_replace)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
PARENT = REPO.parent
for p in (REPO / "src", PARENT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


class _DummyWriter:
    def __init__(self) -> None:
        self.events: list[dict] = []

    def append_event(self, e: dict) -> None:
        self.events.append(dict(e))


class _DummyObserver:
    def __init__(self, open_symbols: set[str]) -> None:
        self._open = set(open_symbols)
        self.close_for_overlap_called = 0

    def has_open(self, symbol: str) -> bool:
        return symbol in self._open

    def close_for_overlap(self, *args, **kwargs):  # pragma: no cover
        self.close_for_overlap_called += 1
        raise AssertionError("close_for_overlap must not be called under no_overlap_replace")


class TestPhase413NoOverlapReplacePolicy(unittest.TestCase):
    def test_same_symbol_open_is_rejected(self) -> None:
        from small_paper.pilot_runner import _maybe_reject_same_symbol_open_overlap, REJECT_SAME_SYMBOL_OPEN_OVERLAP

        cfg = SimpleNamespace(same_symbol_open_policy="no_overlap_replace")
        state = SimpleNamespace(events=[], reject_rows=[], bucket_summary={})
        writer = _DummyWriter()
        ctx = SimpleNamespace(
            config=cfg,
            observer=_DummyObserver({"6981.T"}),
            state=state,
            writer=writer,
            source="test",
            discord=None,
        )
        trade = {"symbol": "6981.T", "entry_time": "2026-06-16T09:00:00+09:00", "profile": "p"}
        decision = SimpleNamespace(quality_tier="Q")
        payload = {"CurrentPrice": 1000}

        rejected = _maybe_reject_same_symbol_open_overlap(
            ctx, sym="6981.T", trade=trade, decision=decision, payload=payload, msg_i=1
        )
        self.assertTrue(rejected)
        self.assertEqual(len(state.reject_rows), 1)
        self.assertEqual(state.reject_rows[0]["reject_reason"], REJECT_SAME_SYMBOL_OPEN_OVERLAP)
        self.assertEqual(len(writer.events), 1)
        self.assertEqual(writer.events[0]["event_type"], "rejected")
        self.assertEqual(writer.events[0]["reject_reason"], REJECT_SAME_SYMBOL_OPEN_OVERLAP)

    def test_same_symbol_closed_is_allowed(self) -> None:
        from small_paper.pilot_runner import _maybe_reject_same_symbol_open_overlap

        cfg = SimpleNamespace(same_symbol_open_policy="no_overlap_replace")
        state = SimpleNamespace(events=[], reject_rows=[], bucket_summary={})
        writer = _DummyWriter()
        ctx = SimpleNamespace(
            config=cfg,
            observer=_DummyObserver(set()),
            state=state,
            writer=writer,
            source="test",
            discord=None,
        )
        trade = {"symbol": "6981.T", "entry_time": "2026-06-16T09:00:00+09:00", "profile": "p"}
        decision = SimpleNamespace(quality_tier="Q")
        payload = {"CurrentPrice": 1000}

        rejected = _maybe_reject_same_symbol_open_overlap(
            ctx, sym="6981.T", trade=trade, decision=decision, payload=payload, msg_i=1
        )
        self.assertFalse(rejected)

    def test_different_symbol_is_allowed(self) -> None:
        from small_paper.pilot_runner import _maybe_reject_same_symbol_open_overlap

        cfg = SimpleNamespace(same_symbol_open_policy="no_overlap_replace")
        state = SimpleNamespace(events=[], reject_rows=[], bucket_summary={})
        writer = _DummyWriter()
        ctx = SimpleNamespace(
            config=cfg,
            observer=_DummyObserver({"6981.T"}),
            state=state,
            writer=writer,
            source="test",
            discord=None,
        )
        trade = {"symbol": "7203.T", "entry_time": "2026-06-16T09:00:00+09:00", "profile": "p"}
        decision = SimpleNamespace(quality_tier="Q")
        payload = {"CurrentPrice": 1000}

        rejected = _maybe_reject_same_symbol_open_overlap(
            ctx, sym="7203.T", trade=trade, decision=decision, payload=payload, msg_i=1
        )
        self.assertFalse(rejected)

    def test_policy_replace_keeps_legacy_behavior(self) -> None:
        from small_paper.pilot_runner import _maybe_reject_same_symbol_open_overlap

        cfg = SimpleNamespace(same_symbol_open_policy="replace")
        state = SimpleNamespace(events=[], reject_rows=[], bucket_summary={})
        writer = _DummyWriter()
        ctx = SimpleNamespace(
            config=cfg,
            observer=_DummyObserver({"6981.T"}),
            state=state,
            writer=writer,
            source="test",
            discord=None,
        )
        trade = {"symbol": "6981.T", "entry_time": "2026-06-16T09:00:00+09:00", "profile": "p"}
        decision = SimpleNamespace(quality_tier="Q")
        payload = {"CurrentPrice": 1000}

        rejected = _maybe_reject_same_symbol_open_overlap(
            ctx, sym="6981.T", trade=trade, decision=decision, payload=payload, msg_i=1
        )
        self.assertFalse(rejected)

