"""Research-only BoardBuf subclass: CurrentPrice / signs / locked. No Runtime write."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.e1_x34a_execution_policy.executable_board import _ts
from small_paper.v1r_native_entry_live import _BoardBuf


def _grow(arr: np.ndarray, new: int, dtype: Any) -> np.ndarray:
    out = np.empty(new, dtype=dtype)
    n = min(arr.size, new)
    out[:n] = arr[:n]
    return out


def _f(v: Any) -> float:
    try:
        if v is None or v == "":
            return float("nan")
        x = float(v)
        return x if x == x else float("nan")
    except (TypeError, ValueError):
        return float("nan")


class MarkBoardBuf(_BoardBuf):
    """KEEPALL buffer with last-trade/signs for V3 marks. Process-local."""

    __slots__ = ("last_px", "last_status", "last_px_t", "ask_sign", "bid_sign", "locked")

    def __init__(self) -> None:
        super().__init__()
        self.last_px = np.empty(64, dtype=float)
        self.last_status = np.empty(64, dtype=float)
        self.last_px_t = np.empty(64, dtype=float)
        self.ask_sign = np.empty(64, dtype=object)
        self.bid_sign = np.empty(64, dtype=object)
        self.locked = np.empty(64, dtype=bool)

    def compact_tail(self, keep: int | None = None) -> None:
        return

    def _ensure(self) -> None:
        size = int(self.t.size)
        if int(self.last_px.size) >= size:
            return
        self.last_px = _grow(self.last_px, size, float)
        self.last_status = _grow(self.last_status, size, float)
        self.last_px_t = _grow(self.last_px_t, size, float)
        self.ask_sign = _grow(self.ask_sign, size, object)
        self.bid_sign = _grow(self.bid_sign, size, object)
        self.locked = _grow(self.locked, size, bool)

    def append(self, row: Any) -> None:
        super().append(row)
        self._ensure()
        i = int(self.n) - 1
        self.last_px[i] = _f(
            row.get("kabu_CurrentPrice") if row.get("kabu_CurrentPrice") is not None else row.get("CurrentPrice")
        )
        st = row.get("CurrentPriceStatus")
        try:
            self.last_status[i] = float(st) if st is not None and st != "" else float("nan")
        except (TypeError, ValueError):
            self.last_status[i] = float("nan")
        px_t = _ts(row.get("kabu_CurrentPriceTime") or row.get("CurrentPriceTime"))
        self.last_px_t[i] = float(px_t) if px_t is not None else float("nan")
        self.ask_sign[i] = str(row.get("AskSign") or "")
        self.bid_sign[i] = str(row.get("BidSign") or "")
        self.locked[i] = bool(row.get("locked_or_crossed"))

    def view(self) -> dict[str, np.ndarray]:
        d = super().view()
        n = int(self.n)
        if n <= 0:
            d["last_px"] = np.asarray([], dtype=float)
            d["last_status"] = np.asarray([], dtype=float)
            d["last_px_t"] = np.asarray([], dtype=float)
            d["ask_sign"] = np.asarray([], dtype=object)
            d["bid_sign"] = np.asarray([], dtype=object)
            d["locked"] = np.asarray([], dtype=bool)
            return d
        d["last_px"] = self.last_px[:n]
        d["last_status"] = self.last_status[:n]
        d["last_px_t"] = self.last_px_t[:n]
        d["ask_sign"] = self.ask_sign[:n]
        d["bid_sign"] = self.bid_sign[:n]
        d["locked"] = self.locked[:n]
        return d


def patch_board_buf_for_marks() -> None:
    import small_paper.v1r_native_entry_live as native

    native._BoardBuf = MarkBoardBuf  # type: ignore[misc, assignment]
    MarkBoardBuf.compact_tail = lambda self, keep=None: None  # type: ignore[method-assign, assignment]
