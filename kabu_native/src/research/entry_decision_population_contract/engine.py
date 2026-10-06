"""A2 Exact Dual-Lane plus CLOCK contract snapshots. No strategy change."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x34a_execution_policy.executable_board import is_executable_continuous_board
from research.e1_x34b_entry_execution.features import preentry_from_board
from research.entry_objective_redesign_c3.engine import _xs_imbalance
from research.entry_objective_redesign_c3.oof import _predict_row, apply_norm
from research.uniform10_b_followup.classify import classify_t0_row
from research.uniform10_b_followup.engine import BFollowEngine
from research.uniform10_entry_rebuild.features import source_series
from research.uniform10_entry_rebuild_v2.features import causal_features
from small_paper.v1r_native_entry_live import FEATURE_ORDER


def reconstruct_payload(src: dict[str, Any], board: dict[str, Any], i: int) -> dict[str, Any]:
    """Rebuild is_executable_continuous_board input from ingest-stored board row. Not a guess."""
    bid = float(board["bid"][i]) if board.get("bid") is not None else None
    ask = float(board["ask"][i]) if board.get("ask") is not None else None
    bq = float(board["bid_qty"][i]) if board.get("bid_qty") is not None else None
    aq = float(board["ask_qty"][i]) if board.get("ask_qty") is not None else None
    special = bool(board["special"][i]) if board.get("special") is not None else src.get("special")
    return {
        "AskSign": src.get("AskSign"),
        "BidSign": src.get("BidSign"),
        "Buy1": {"Price": bid, "Qty": bq, "Sign": src.get("Buy1.Sign")},
        "Sell1": {"Price": ask, "Qty": aq, "Sign": src.get("Sell1.Sign")},
        "OpeningPrice": src.get("OpeningPrice"),
        "OpeningPriceTime": src.get("OpeningPriceTime"),
        "CurrentPrice": src.get("kabu_CurrentPrice") if src.get("kabu_CurrentPrice") is not None else src.get("CurrentPrice"),
        "CurrentPriceTime": src.get("kabu_CurrentPriceTime") or src.get("CurrentPriceTime"),
        "CurrentPriceStatus": src.get("CurrentPriceStatus"),
        "TradingVolume": src.get("TradingVolume"),
        "TradingVolumeTime": src.get("TradingVolumeTime"),
        "SpecialQuote": special,
        "locked_or_crossed": src.get("locked_or_crossed"),
    }


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


class ContractEngine(BFollowEngine):
    """CURRENT ENTRY Exact (executable_t0_only) with per-CLOCK RAW/EXACT/C3 contract rows."""

    def __init__(self, *a: Any, c3_fit: Optional[dict[str, Any]] = None, **k: Any) -> None:
        super().__init__(*a, executable_t0_only=True, score_pct=None, **k)
        self.c3_fit = c3_fit or {}
        self.contract_rows: list[dict[str, Any]] = []
        self._c3_series_cache: dict[str, Any] = {}

    def _c3_series(self, s: str) -> Any:
        # Match C3LookupEngine: first-touch cache of source_series (Exact C3 behavior).
        if s not in self._c3_series_cache:
            self._c3_series_cache[s] = source_series(self.boards.get(s) or [])
        return self._c3_series_cache[s]

    def _run_anchor(self, *, anchor: str, t0: float, day: str, session: str) -> list[dict[str, Any]]:
        out = super()._run_anchor(anchor=anchor, t0=t0, day=day, session=session)
        self._record_contract(anchor=anchor, t0=t0, day=day, session=session)
        return out

    def _record_contract(self, *, anchor: str, t0: float, day: str, session: str) -> None:
        fit = self.c3_fit or {}
        feats = list(fit.get("features") or [])
        collected: list[dict[str, Any]] = []
        rows_out: list[dict[str, Any]] = []
        for raw in list(self.universe):
            s = str(raw).replace(".T", "")
            src_rows = self.boards.get(s) or []
            board = self._board_arrays(s)
            rec: dict[str, Any] = {
                "date": day,
                "session": session,
                "anchor": anchor,
                "t0": float(t0),
                "symbol": s,
                "decision_id": f"{day}|{session}|{anchor}|{s}",
                "board_event_time": None,
                "snapshot_sequence": None,
                "bid": None,
                "ask": None,
                "AskSign": None,
                "BidSign": None,
                "CurrentPriceStatus": None,
                "raw_executable": None,
                "exact_executable": None,
                "current_feature_available": False,
                "current_score": None,
                "c3_feature_complete": False,
                "c3_live_score": None,
                "c3_missing_reason": "NOT_EVALUATED",
                "exact_in_admit_pool": False,
            }
            if board.get("t") is None or board["t"].size == 0:
                rec["c3_missing_reason"] = "NOT_EVALUATED"
                rows_out.append(rec)
                continue
            i = int(np.searchsorted(board["t"], t0, side="right") - 1)
            if i < 0:
                rec["c3_missing_reason"] = "NOT_EVALUATED"
                rows_out.append(rec)
                continue
            src = src_rows[i] if i < len(src_rows) else {}
            rec["board_event_time"] = float(board["t"][i])
            rec["snapshot_sequence"] = src.get("sequence")
            rec["bid"] = _f(board["bid"][i]) if board.get("bid") is not None else None
            rec["ask"] = _f(board["ask"][i]) if board.get("ask") is not None else None
            rec["AskSign"] = src.get("AskSign")
            rec["BidSign"] = src.get("BidSign")
            rec["CurrentPriceStatus"] = src.get("CurrentPriceStatus")
            klass = classify_t0_row(src, event_t=float(board["t"][i]))
            rec["exact_executable"] = bool(klass.get("executable_at_t0"))
            pay = reconstruct_payload(src, board, i)
            gate = is_executable_continuous_board(pay, event_t=float(board["t"][i]))
            rec["raw_executable"] = bool(gate.get("ok"))
            rec["raw_state"] = gate.get("state")
            rec["exact_state"] = klass.get("state")
            live = preentry_from_board(board, t0)
            cur_ok = not any(
                live.get(f) is None or not np.isfinite(live.get(f) if live.get(f) is not None else np.nan)
                for f in FEATURE_ORDER
            )
            rec["current_feature_available"] = bool(cur_ok)
            if cur_ok:
                try:
                    sc = float(self.score_fn(live))
                except Exception:
                    sc = float("nan")
                if np.isfinite(sc):
                    rec["current_score"] = sc
            limit_ok = rec["bid"] is not None and rec["bid"] > 0 and np.isfinite(rec["bid"])
            rec["exact_in_admit_pool"] = bool(
                rec["exact_executable"] and rec["current_score"] is not None and limit_ok
            )
            if not rec["exact_executable"]:
                rec["c3_missing_reason"] = "NOT_EVALUATED"
                rows_out.append(rec)
                continue
            if not limit_ok:
                rec["c3_missing_reason"] = "EXCLUDED_BEFORE_RANK"
                rows_out.append(rec)
                continue
            raw_feats = causal_features(board, t0, rows=None, series=self._c3_series(s))
            rec["_c3_raw"] = {k: raw_feats.get(k) for k in ("imbalance", *feats) if k != "xs_imbalance_z"}
            collected.append(rec)
            rows_out.append(rec)

        if collected and feats:
            feat_rows = []
            for rec in collected:
                raw = rec.pop("_c3_raw", {}) or {}
                feat_rows.append({"symbol": rec["symbol"], **raw})
            _xs_imbalance(feat_rows)
            for rec, fr in zip(collected, feat_rows):
                rec["c3_feature_complete"] = all(_f(fr.get(f)) is not None for f in feats)
            normed = apply_norm(feat_rows, feats, str(fit.get("normalization") or "none"))
            for rec, nr in zip(collected, normed):
                sc = _predict_row(fit, nr) if fit else None
                if sc is None or not np.isfinite(float(sc)):
                    rec["c3_live_score"] = None
                    rec["c3_missing_reason"] = "EXCLUDED_BEFORE_RANK"
                else:
                    rec["c3_live_score"] = float(sc)
                    rec["c3_missing_reason"] = None
        for rec in rows_out:
            rec.pop("_c3_raw", None)
        self.contract_rows.extend(rows_out)
