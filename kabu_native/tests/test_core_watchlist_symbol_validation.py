"""Phase 149: TSE alpha watch symbols (e.g. 186A.T) in core watchlist validation."""

from __future__ import annotations

import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
for p in (NATIVE / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

from universe.core_watchlist import (  # noqa: E402
    can_add_to_core,
    can_replace_core,
    normalize_watch_symbol,
    validate_watch_symbol,
)


def test_numeric_symbols_still_valid() -> None:
    for raw in ("7203.T", "9984.t", "7203"):
        sym = normalize_watch_symbol(raw)
        ok, reason = validate_watch_symbol(sym)
        assert ok, (raw, reason)
        assert sym in ("7203.T", "9984.T")


def test_alpha_symbols_valid() -> None:
    for sym in ("186A.T", "130A.T", "153A.T", "186a.t"):
        ok, reason = validate_watch_symbol(sym)
        assert ok, (sym, reason)


def test_alpha_normalize_without_suffix() -> None:
    assert normalize_watch_symbol("186A") == "186A.T"


def test_invalid_symbols_rejected() -> None:
    for sym in ("12345.T", "186AA.T", "18A.T", "ABCD.T", "7203.TX", ""):
        ok, reason = validate_watch_symbol(sym)
        assert not ok, sym
        assert reason == "invalid_symbol"


def test_replace_with_alpha_symbols() -> None:
    ok, ordered, reject, msg = can_replace_core("7203.T,9984.T,186A.T,130A.T,153A.T")
    assert ok, (reject, msg)
    assert ordered == ["7203.T", "9984.T", "186A.T", "130A.T", "153A.T"]


def test_add_alpha_symbol() -> None:
    ok, reject, msg = can_add_to_core([], "186A.T")
    assert ok, (reject, msg)
