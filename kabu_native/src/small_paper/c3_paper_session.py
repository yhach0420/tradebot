"""Paper owner for research C3. The sealed X1 class is not edited.

C3 adds Buy1.Time exit freshness and a residual ledger. ENTRY, support,
and the one-tick fire stay on the inherited book. Live orders stay off.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from research.v16_bid_freshness_residual_accounting_v1.run import BookC3, _seed

NATIVE = Path(__file__).resolve().parents[2]
DEFAULT_RESIDUAL_PATH = NATIVE / "results" / "paper" / "c3_residual_ledger.json"
MODE_PATH = Path(__file__).with_name("c3_paper_mode.json")


class C3PaperSessionExecutor(BookC3):
    """Same decisions as research BookC3, with a restorable residual file."""

    strategy_mode = "C3"

    def __init__(self) -> None:
        super().__init__()
        self.candidate_name = "V16_C3_BUY1_TIME_RESIDUAL"
        self.residual_path = residual_ledger_path()
        self.residual_load_error = ""
        self.live_order_enabled = False

    def bind_production_schedule(self, *, day: str, kind: str) -> None:
        super().bind_production_schedule(day=day, kind=kind)
        self._load_residuals()

    def bind_safe_pm_flatten(self, *, day: str) -> None:
        super().bind_safe_pm_flatten(day=day)
        self._load_residuals()

    def on_session_boundary(self, *_args: Any, **_kwargs: Any) -> None:
        super().on_session_boundary(*_args, **_kwargs)
        self._save_residuals()

    def snapshot_status(self) -> dict[str, Any]:
        body = super().snapshot_status()
        body["strategy_mode"] = self.strategy_mode
        body["residual_notional"] = round(sum(float(row["notional"]) for row in self.still_open), 6)
        body["residual_n"] = len(self.still_open)
        body["live_order_enabled"] = False
        body["submit"] = 0
        body["cancel"] = 0
        body["live"] = 0
        return body

    def _load_residuals(self) -> None:
        path = Path(self.residual_path)
        if not path.is_file():
            return
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
            rows = list(body.get("residuals") or [])
            for row in rows:
                float(row["entry_px"])
                float(row["entry_t"])
                float(row["support"])
                str(row["symbol"])
        except Exception as exc:
            self.residual_load_error = type(exc).__name__
            self.admission = lambda _symbol, _t: (False, "RESIDUAL_LEDGER_FAIL_CLOSED")
            return
        if rows:
            _seed(self, rows)

    def _save_residuals(self) -> None:
        if self.residual_load_error:
            return
        path = Path(self.residual_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            "strategy_mode": self.strategy_mode,
            "residuals": list(self.still_open),
            "pnl_booked_for_unfilled": False,
        }
        path.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def residual_ledger_path() -> Path:
    """Production ledger, unless a verification process sets its own path."""
    override = os.environ.get("TRADEBOT_C3_RESIDUAL_PATH", "").strip()
    return Path(override) if override else DEFAULT_RESIDUAL_PATH


def paper_strategy_mode_path(path: Path | None = None) -> Path:
    """Production mode file, unless a verification process points elsewhere."""
    if path is not None:
        return Path(path)
    override = os.environ.get("TRADEBOT_PAPER_STRATEGY_MODE_PATH", "").strip()
    return Path(override) if override else MODE_PATH


def residual_restore_block(path: Path | None = None) -> str:
    """X1 restore stays closed while a C3 residual is present or unreadable."""
    target = residual_ledger_path() if path is None else Path(path)
    if not target.is_file():
        return ""
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
        rows = list(body.get("residuals") or [])
    except Exception:
        return "C3_RESIDUAL_LEDGER_FAIL_CLOSED"
    if rows:
        return "C3_RESIDUAL_BLOCKS_X1_RESTORE"
    return ""


def read_paper_strategy_mode(path: Path | None = None) -> str:
    """Disabled or missing mode keeps the sealed X1 owner."""
    mode_path = paper_strategy_mode_path(path)
    if not mode_path.is_file():
        return "FIXED_SUPPORT_X1"
    try:
        body = json.loads(mode_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("PAPER_STRATEGY_MODE_FAIL_CLOSED") from exc
    if not isinstance(body, dict):
        raise RuntimeError("PAPER_STRATEGY_MODE_FAIL_CLOSED")
    if not body.get("enabled"):
        return "FIXED_SUPPORT_X1"
    selected = str(body.get("selected") or "")
    if selected not in {"C3", "FIXED_SUPPORT_X1"}:
        raise RuntimeError("PAPER_STRATEGY_MODE_FAIL_CLOSED")
    return selected
