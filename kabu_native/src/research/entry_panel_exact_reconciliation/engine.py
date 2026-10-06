"""Canonical decision snapshot: Exact CLOCK fire + Exact feature functions. No panel fork."""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from research.e1_x34a_execution_policy.executable_board import is_executable_continuous_board
from research.e1_x34b_entry_execution.features import preentry_from_board
from research.entry_decision_population_contract.engine import ContractEngine, reconstruct_payload
from research.entry_objective_redesign_c3.engine import _xs_imbalance
from research.entry_objective_redesign_c3.oof import _predict_row, apply_norm
from research.entry_panel_exact_reconciliation import F0_CURRENT6, F1_C2_6, F1_RAW
from research.entry_panel_exact_reconciliation.causality import max_source_event_time
from research.uniform10_b_followup.classify import classify_t0_row
from research.uniform10_entry_rebuild_v2.features import causal_features
from small_paper.v1r_native_entry_live import FEATURE_ORDER


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if np.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _score_collected(
    collected: list[dict[str, Any]],
    fit: dict[str, Any],
    *,
    score_key: str,
    complete_key: str,
    reason_key: str,
) -> None:
    feats = list(fit.get("features") or [])
    if not collected or not feats or not fit:
        for rec in collected:
            rec[score_key] = None
            rec[complete_key] = False
            rec[reason_key] = rec.get(reason_key) or "NOT_EVALUATED"
        return
    feat_rows = []
    for rec in collected:
        raw = rec.get("_c3_raw") or {}
        feat_rows.append({"symbol": rec["symbol"], **raw})
    _xs_imbalance(feat_rows)
    for rec, fr in zip(collected, feat_rows):
        rec["xs_imbalance_z"] = _f(fr.get("xs_imbalance_z"))
        for k in F1_RAW:
            rec[k] = _f(fr.get(k))
        rec[complete_key] = all(_f(fr.get(f)) is not None for f in feats)
    normed = apply_norm(feat_rows, feats, str(fit.get("normalization") or "none"))
    for rec, nr in zip(collected, normed):
        sc = _predict_row(fit, nr) if fit.get("kind") == "ridge" else None
        if sc is None or not np.isfinite(float(sc)):
            rec[score_key] = None
            rec[reason_key] = "EXCLUDED_BEFORE_RANK"
        else:
            rec[score_key] = float(sc)
            rec[reason_key] = None


class CanonicalEngine(ContractEngine):
    """Exact Dual-Lane CLOCK consumer. Research panel IS this snapshot. No MarkBoardBuf patch."""

    def __init__(self, *a: Any, oof_fit: Optional[dict[str, Any]] = None, **k: Any) -> None:
        super().__init__(*a, **k)
        self.oof_fit = oof_fit or {}

    def _record_contract(self, *, anchor: str, t0: float, day: str, session: str) -> None:
        c3_fit = self.c3_fit or {}
        oof_fit = self.oof_fit or {}
        c3_feats = list(c3_fit.get("features") or [])
        oof_feats = list(oof_fit.get("features") or [])
        need = tuple(dict.fromkeys((*F1_RAW, *F0_CURRENT6, "imbalance", *c3_feats, *oof_feats)))
        collected: list[dict[str, Any]] = []
        rows_out: list[dict[str, Any]] = []
        for raw in list(self.universe):
            s = str(raw).replace(".T", "")
            src_rows = self.boards.get(s) or []
            board = self._board_arrays(s)
            buf = self._board_buf.get(s)
            rec: dict[str, Any] = {
                "date": day,
                "session": session,
                "anchor": anchor,
                "t0": float(t0),
                "symbol": s,
                "decision_id": f"{day}|{session}|{anchor}|{s}",
                "board_event_time": None,
                "snapshot_sequence": None,
                "event_age_sec": None,
                "bid": None,
                "ask": None,
                "AskSign": None,
                "BidSign": None,
                "Buy1_Sign": None,
                "Sell1_Sign": None,
                "CurrentPriceStatus": None,
                "boards_n": len(src_rows),
                "buf_n": int(getattr(buf, "n", 0) or 0),
                "raw_executable": None,
                "exact_executable": None,
                "canonical_executable": None,
                "current_feature_available": False,
                "current_score": None,
                "c3_feature_complete": False,
                "c3_live_score": None,
                "c3_oof_score": None,
                "c3_missing_reason": "NOT_EVALUATED",
                "c3_oof_missing_reason": "NOT_EVALUATED",
                "exact_in_admit_pool": False,
                "max_source_event_time": None,
                "future_event_use": False,
                "series_has_future_unused": False,
                "series_t_used": None,
                "c3_series_cached": s in self._c3_series_cache,
            }
            for f in F0_CURRENT6:
                rec[f"cur_{f}"] = None
            for f in F1_C2_6:
                rec[f] = None
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
            rec["event_age_sec"] = float(t0) - float(board["t"][i])
            rec["bid"] = _f(board["bid"][i]) if board.get("bid") is not None else None
            rec["ask"] = _f(board["ask"][i]) if board.get("ask") is not None else None
            rec["AskSign"] = src.get("AskSign")
            rec["BidSign"] = src.get("BidSign")
            rec["Buy1_Sign"] = src.get("Buy1.Sign")
            rec["Sell1_Sign"] = src.get("Sell1.Sign")
            rec["CurrentPriceStatus"] = src.get("CurrentPriceStatus")
            klass = classify_t0_row(src, event_t=float(board["t"][i]))
            rec["exact_executable"] = bool(klass.get("executable_at_t0"))
            rec["canonical_executable"] = rec["exact_executable"]
            rec["exact_state"] = klass.get("state")
            pay = reconstruct_payload(src, board, i)
            gate = is_executable_continuous_board(pay, event_t=float(board["t"][i]))
            rec["raw_executable"] = bool(gate.get("ok"))
            rec["raw_state"] = gate.get("state")
            live = preentry_from_board(board, t0)
            for f in F0_CURRENT6:
                rec[f"cur_{f}"] = _f(live.get(f))
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
            src_t = max_source_event_time(board, t0, series=None)
            rec["max_source_event_time"] = src_t.get("max_source_event_time")
            rec["future_event_use"] = bool(src_t.get("future_event_use"))
            rec["series_has_future_unused"] = bool(src_t.get("series_has_future_unused"))
            rec["series_t_used"] = src_t.get("series_t_used")
            rec["lookback_t_max"] = src_t.get("lookback_t_max")
            limit_ok = rec["bid"] is not None and rec["bid"] > 0 and np.isfinite(rec["bid"])
            rec["exact_in_admit_pool"] = bool(
                rec["exact_executable"] and rec["current_score"] is not None and limit_ok
            )
            if not rec["exact_executable"]:
                rec["c3_missing_reason"] = "NOT_EVALUATED"
                rec["c3_oof_missing_reason"] = "NOT_EVALUATED"
                rows_out.append(rec)
                continue
            if not limit_ok:
                rec["c3_missing_reason"] = "EXCLUDED_BEFORE_RANK"
                rec["c3_oof_missing_reason"] = "EXCLUDED_BEFORE_RANK"
                rows_out.append(rec)
                continue
            # Same first-touch as C3LookupEngine: cache series only when the name is collected.
            series = self._c3_series(s)
            src_t = max_source_event_time(board, t0, series=series)
            rec["max_source_event_time"] = src_t.get("max_source_event_time")
            rec["future_event_use"] = bool(src_t.get("future_event_use"))
            rec["series_has_future_unused"] = bool(src_t.get("series_has_future_unused"))
            rec["series_t_used"] = src_t.get("series_t_used")
            rec["lookback_t_max"] = src_t.get("lookback_t_max")
            rec["c3_series_cached"] = True
            raw_feats = causal_features(board, t0, rows=None, series=series)
            for f in F0_CURRENT6:
                rec[f] = _f(raw_feats.get(f))
            for f in F1_RAW:
                rec[f] = _f(raw_feats.get(f))
            rec["_c3_raw"] = {k: raw_feats.get(k) for k in need if k != "xs_imbalance_z"}
            collected.append(rec)
            rows_out.append(rec)

        if collected:
            feat_rows = [{"symbol": rec["symbol"], **(rec.get("_c3_raw") or {})} for rec in collected]
            _xs_imbalance(feat_rows)
            for rec, fr in zip(collected, feat_rows):
                rec["xs_imbalance_z"] = _f(fr.get("xs_imbalance_z"))

        _score_collected(
            collected,
            c3_fit,
            score_key="c3_live_score",
            complete_key="c3_feature_complete",
            reason_key="c3_missing_reason",
        )
        _score_collected(
            collected,
            oof_fit,
            score_key="c3_oof_score",
            complete_key="c3_oof_feature_complete",
            reason_key="c3_oof_missing_reason",
        )
        for rec in rows_out:
            rec.pop("_c3_raw", None)
        self.contract_rows.extend(rows_out)
