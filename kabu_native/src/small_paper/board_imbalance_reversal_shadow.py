"""Board Imbalance Reversal — TEMP_FORWARD (H_board_ts only).

Isolates Cost-Aware V2 primary arm H_board_ts:
  reject if f_np_imb_chg_60 <= -0.038599 (SoT from cost_aware_v2/report.json)

Does NOT revive Cost-Aware V1/V2, I_price_board, or any composite arm.
Paper default ON; Live/real-order forced OFF. Observe-only; never submits orders.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from small_paper.cost_aware_entry_v2_shadow import (
    evaluate_v2,
    extract_features,
    join_key,
    load_thresholds,
)
from small_paper.forward_observer_defaults import (
    is_live_or_real_order_context,
    is_paper_runtime,
)

ENV_KEY = "BOARD_IMBALANCE_REVERSAL_SHADOW"
CANONICAL_SHADOW_ID = "board_imbalance_reversal_shadow"
DISPLAY_NAME = "Board Imbalance Reversal"
FEATURE = "f_np_imb_chg_60"
# Source of Truth: results/research/cost_aware_v2/report.json thresholds.t_imb_chg
SOT_THRESHOLD = -0.038599
COMPARISON = "<="  # reject when feature <= threshold
POLICY = "H_board_ts"

_TRUE = frozenset({"1", "true", "TRUE", "yes", "YES", "on", "ON"})
_FALSE = frozenset({"0", "false", "FALSE", "no", "NO", "off", "OFF"})
_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class EnableDecision:
    enabled: bool
    reason: str
    env_raw: Optional[str]
    paper_runtime: bool


def _parse_env_token(env_value: Optional[str]) -> tuple[Optional[bool], bool]:
    if env_value is None:
        return None, False
    s = str(env_value).strip()
    if not s:
        return None, False
    if s in _TRUE:
        return True, False
    if s in _FALSE:
        return False, False
    return None, True


def resolve_board_imbalance_reversal_enabled(
    *,
    is_paper_runtime: bool,
    env_value: Optional[str],
) -> EnableDecision:
    if not is_paper_runtime:
        return EnableDecision(False, "NON_PAPER_FORCED_OFF", env_value, False)
    parsed, invalid = _parse_env_token(env_value)
    if invalid:
        return EnableDecision(False, "INVALID_ENV_FORCED_OFF", env_value, True)
    if parsed is None:
        return EnableDecision(True, "PAPER_DEFAULT_ON", env_value, True)
    if parsed:
        return EnableDecision(True, "PAPER_ENV_ON", env_value, True)
    return EnableDecision(False, "PAPER_ENV_OFF", env_value, True)


def resolve_board_imbalance_reversal_from_runtime(
    cfg: Any = None,
    env: Optional[Mapping[str, str]] = None,
) -> EnableDecision:
    src: Mapping[str, str] = env if env is not None else os.environ
    raw = src.get(ENV_KEY)
    if is_live_or_real_order_context(cfg):
        return EnableDecision(False, "NON_PAPER_FORCED_OFF", raw, False)
    paper = bool(is_paper_runtime(cfg))
    decision = resolve_board_imbalance_reversal_enabled(
        is_paper_runtime=paper, env_value=raw
    )
    if decision.reason == "INVALID_ENV_FORCED_OFF":
        _LOG.warning(
            "BOARD_IMBALANCE_REVERSAL_SHADOW invalid env forced OFF (value not logged)"
        )
    return decision


def load_sot_threshold(native_root: Optional[Any] = None) -> float:
    """Load frozen t_imb_chg; never re-fit. Falls back to SOT_THRESHOLD constant."""
    thr, _src, _hist = load_thresholds(native_root)
    v = thr.get("t_imb_chg")
    if v is None:
        return SOT_THRESHOLD
    # Preserve exact float from JSON (-0.038599); do not round.
    return float(v)


def _missing_reason(
    feats: Mapping[str, Optional[float]],
    np_row: Optional[Mapping[str, Any]],
    eval_out: Mapping[str, Any],
) -> Optional[str]:
    if eval_out.get("fail_open") and not eval_out.get("board_feature_available"):
        if np_row:
            if np_row.get("np_future_leakage"):
                return "BOARD_TIMESTAMP_INVALID"
            ticks = np_row.get("np_ticks_60s")
            if ticks is not None:
                try:
                    if int(ticks) < 2:
                        return "INSUFFICIENT_60S_WINDOW"
                except (TypeError, ValueError):
                    pass
            if np_row.get("np_board_history_seconds") is not None:
                try:
                    if float(np_row["np_board_history_seconds"]) < 60.0:
                        return "INSUFFICIENT_60S_WINDOW"
                except (TypeError, ValueError):
                    pass
            if np_row.get("board_history_valid") is False:
                return "BOARD_HISTORY_MISSING"
        if feats.get(FEATURE) is None:
            return "FEATURE_MISSING"
        return "BOARD_HISTORY_MISSING"
    return None


def evaluate_h_board_ts(
    trade: Mapping[str, Any],
    *,
    np_row: Optional[Mapping[str, Any]] = None,
    threshold: Optional[float] = None,
) -> dict[str, Any]:
    """Pure H_board_ts decision (reuse V2 evaluate_v2 path, H-only)."""
    thr_val = SOT_THRESHOLD if threshold is None else float(threshold)
    feats = extract_features(trade, np_row=np_row)
    out = evaluate_v2(
        feats,
        thresholds={"t_imb_chg": thr_val},
        policy=POLICY,
    )
    miss = _missing_reason(feats, np_row, out)
    would_reject = (not out.get("v2_keep", True)) and (not out.get("fail_open", False))
    return {
        "canonical_shadow_id": CANONICAL_SHADOW_ID,
        "feature": FEATURE,
        "f_np_imb_chg_60": feats.get(FEATURE),
        "threshold": thr_val,
        "comparison": COMPARISON,
        "comparison_result": (
            None
            if feats.get(FEATURE) is None
            else bool(float(feats[FEATURE]) <= thr_val)  # type: ignore[arg-type]
        ),
        "would_reject": would_reject,
        "fail_open": bool(out.get("fail_open")),
        "missing_reason": miss,
        "board_history_valid": bool(out.get("board_feature_available")),
        "v2_verdict": out.get("v2_verdict"),
        "features": feats,
    }


@dataclass
class BoardImbalanceReversalState:
    enabled: bool = False
    enable_reason: str = "default"
    threshold: float = SOT_THRESHOLD
    threshold_source: str = "sot_constant"
    by_key: dict[str, dict[str, Any]] = field(default_factory=dict)
    evaluated: int = 0
    history_valid: int = 0
    missing: int = 0
    would_reject: int = 0
    closed: int = 0
    open_n: int = 0
    incomplete_n: int = 0
    actual_pnl_yen_100: float = 0.0
    counterfactual_delta_yen_100: float = 0.0
    winner_sacrificed: int = 0
    stop_avoided: int = 0
    no_progress_avoided: int = 0
    max_hold_avoided: int = 0
    submit: int = 0
    cancel: int = 0
    live_order: int = 0
    forward_days: int = 0
    gate_status: str = "BOARD_IMBALANCE_REVERSAL_FORWARD_CONTINUE"

    @classmethod
    def maybe_create(cls, cfg: Any = None) -> "BoardImbalanceReversalState":
        d = resolve_board_imbalance_reversal_from_runtime(cfg=cfg)
        thr, src, _ = load_thresholds()
        t = float(thr["t_imb_chg"]) if thr.get("t_imb_chg") is not None else SOT_THRESHOLD
        return cls(
            enabled=d.enabled,
            enable_reason=d.reason,
            threshold=t,
            threshold_source=src if thr.get("t_imb_chg") is not None else "sot_constant",
        )


def note_accepted(
    state: BoardImbalanceReversalState,
    *,
    symbol: str,
    trade: Mapping[str, Any],
    np_row: Optional[Mapping[str, Any]] = None,
    session: str = "",
    position_id: str = "",
    entry_time: str = "",
    entry_price: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    if not state.enabled:
        return None
    key = join_key(
        position_id=position_id or str(trade.get("position_id") or ""),
        symbol=symbol or str(trade.get("symbol") or ""),
        entry_time=entry_time or str(trade.get("entry_time") or trade.get("accepted_at") or ""),
        accepted_at=str(trade.get("accepted_at") or ""),
    )
    if not key or key in state.by_key:
        return state.by_key.get(key)
    decision = evaluate_h_board_ts(trade, np_row=np_row, threshold=state.threshold)
    am_pm = str(session or trade.get("am_pm_label") or trade.get("am_pm") or "")
    rec = {
        "canonical_shadow_id": CANONICAL_SHADOW_ID,
        "session_id": str(trade.get("session_id") or ""),
        "symbol": symbol,
        "candidate_time": entry_time or str(trade.get("entry_time") or trade.get("accepted_at") or ""),
        "pbv2_decision": "ACCEPT",
        "pbv2_accept": True,
        "entry_score_v2": trade.get("entry_expectancy_score_v2")
        or trade.get("continuation_quality_score"),
        "board_history_valid": decision["board_history_valid"],
        "board_history_seconds": (np_row or {}).get("np_board_history_seconds")
        or (np_row or {}).get("board_history_seconds"),
        "f_np_imb_chg_60": decision["f_np_imb_chg_60"],
        "threshold": decision["threshold"],
        "comparison_result": decision["comparison_result"],
        "would_reject": decision["would_reject"],
        "missing_reason": decision["missing_reason"],
        "actual_entry": True,
        "actual_exit": False,
        "actual_exit_reason": None,
        "actual_pnl_yen_100": None,
        "counterfactual_delta_yen_100": None,
        "winner_sacrificed": False,
        "stop_avoided": False,
        "no_progress_avoided": False,
        "max_hold_avoided": False,
        "same_symbol_prior_count": int(trade.get("same_symbol_prior_count") or 0),
        "am_pm_label": am_pm,
        "position_id": key,
        "entry_price": entry_price or trade.get("entry_price"),
        "exit_status": "open",
        "pnl_status": "OPEN",
    }
    state.by_key[key] = rec
    state.evaluated += 1
    if decision["board_history_valid"]:
        state.history_valid += 1
    if decision["missing_reason"]:
        state.missing += 1
    if decision["would_reject"]:
        state.would_reject += 1
    state.open_n = sum(1 for r in state.by_key.values() if r.get("exit_status") == "open")
    return rec


def note_exit(state: BoardImbalanceReversalState, row: Mapping[str, Any]) -> Optional[dict[str, Any]]:
    if not state.enabled:
        return None
    key = join_key(
        position_id=str(row.get("position_id") or row.get("observer_position_id") or ""),
        symbol=str(row.get("symbol") or ""),
        entry_time=str(row.get("entry_time") or ""),
    )
    if not key or key not in state.by_key:
        return None
    rec = state.by_key[key]
    if rec.get("exit_status") == "closed":
        return rec
    pnl = row.get("pnl_yen_100")
    if pnl is None:
        pnl = row.get("realized_pnl_yen_100")
    try:
        pnl_f = float(pnl) if pnl is not None else None
    except (TypeError, ValueError):
        pnl_f = None
    reason = str(row.get("exit_reason") or row.get("observer_exit_reason") or "")
    if pnl_f is None:
        rec["exit_status"] = "incomplete"
        rec["pnl_status"] = "INCOMPLETE"
        state.incomplete_n += 1
        state.open_n = sum(1 for r in state.by_key.values() if r.get("exit_status") == "open")
        return rec

    rec["actual_exit"] = True
    rec["actual_exit_reason"] = reason
    rec["actual_pnl_yen_100"] = pnl_f
    rec["exit_status"] = "closed"
    rec["pnl_status"] = "CLOSED"
    # CF: reject → CF pnl 0; keep → same as actual
    if rec.get("would_reject"):
        cf_pnl = 0.0
        delta = cf_pnl - pnl_f
        rec["counterfactual_delta_yen_100"] = delta
        if pnl_f > 0:
            rec["winner_sacrificed"] = True
            state.winner_sacrificed += 1
        if pnl_f < 0:
            # avoided loss contributes +(-pnl) to delta when reject
            pass
        rlow = reason.lower()
        if "stop" in rlow:
            rec["stop_avoided"] = True
            state.stop_avoided += 1
        if "no_progress" in rlow or "noprogress" in rlow or "no-progress" in rlow:
            rec["no_progress_avoided"] = True
            state.no_progress_avoided += 1
        if "max_hold" in rlow or "maxhold" in rlow:
            rec["max_hold_avoided"] = True
            state.max_hold_avoided += 1
        state.counterfactual_delta_yen_100 = round(
            state.counterfactual_delta_yen_100 + float(delta), 2
        )
    else:
        rec["counterfactual_delta_yen_100"] = 0.0
    state.actual_pnl_yen_100 = round(state.actual_pnl_yen_100 + pnl_f, 2)
    state.closed += 1
    state.open_n = sum(1 for r in state.by_key.values() if r.get("exit_status") == "open")
    return rec


def summary_fields(state: BoardImbalanceReversalState) -> dict[str, Any]:
    days = {
        str(r.get("candidate_time") or "")[:10]
        for r in state.by_key.values()
        if r.get("candidate_time")
    }
    state.forward_days = len([d for d in days if d])
    incomplete_blocks = state.open_n > 0 or state.incomplete_n > 0
    if incomplete_blocks:
        state.gate_status = "BOARD_IMBALANCE_REVERSAL_FORWARD_CONTINUE"
    return {
        "board_imbalance_reversal_shadow_enabled": state.enabled,
        "board_imbalance_reversal_enable_reason": state.enable_reason,
        "board_imbalance_reversal_threshold": state.threshold,
        "board_imbalance_reversal_evaluated": state.evaluated,
        "board_imbalance_reversal_history_valid": state.history_valid,
        "board_imbalance_reversal_missing": state.missing,
        "board_imbalance_reversal_would_reject": state.would_reject,
        "board_imbalance_reversal_closed": state.closed,
        "board_imbalance_reversal_open": state.open_n,
        "board_imbalance_reversal_incomplete": state.incomplete_n,
        "board_imbalance_reversal_actual_pnl_yen_100": state.actual_pnl_yen_100,
        "board_imbalance_reversal_counterfactual_delta_yen_100": state.counterfactual_delta_yen_100,
        "board_imbalance_reversal_winner_sacrificed": state.winner_sacrificed,
        "board_imbalance_reversal_stop_avoided": state.stop_avoided,
        "board_imbalance_reversal_no_progress_avoided": state.no_progress_avoided,
        "board_imbalance_reversal_max_hold_avoided": state.max_hold_avoided,
        "board_imbalance_reversal_pf_after": None,
        "board_imbalance_reversal_forward_days": state.forward_days,
        "board_imbalance_reversal_gate_status": state.gate_status,
        "board_imbalance_reversal_shadow": {
            "enabled": state.enabled,
            "observe_only": True,
            "feature": FEATURE,
            "threshold": state.threshold,
            "comparison": COMPARISON,
            "submit": 0,
            "cancel": 0,
            "live_order": 0,
        },
    }
