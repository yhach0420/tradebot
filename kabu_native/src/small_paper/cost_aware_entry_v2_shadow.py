"""Cost-Aware Entry V2 Shadow — observe-only, Paper default ON, Live force OFF.

Primary arm: H_board_ts (f_np_imb_chg_60)
Secondary arm: I_price_board (f_chase, f_near_high, f_np_imb_chg_60)

Does NOT block/add real ENTRY, Discord ENTRY notifications, or orders.
submit=0, cancel=0, live_order=0 always.
"""

from __future__ import annotations

import json
import math
import queue
import threading
import time
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from small_paper.forward_observer_defaults import (
    resolve_cost_aware_entry_v2_shadow,
)

JST = ZoneInfo("Asia/Tokyo")

OWNERSHIP = "RESEARCH"
SHADOW_NAME = "cost_aware_entry_v2_shadow"
ENV_KEY = "COST_AWARE_ENTRY_V2_SHADOW"
CONFIG_KEY = "cost_aware_entry_v2_shadow"
COST_BPS = 0.05

# Bounded async JSONL writer (accept path must never block on disk I/O)
JSONL_QUEUE_MAX = 256
JSONL_FLUSH_TIMEOUT_SEC = 2.0
STATE_MAX_KEYS = 5000
STATE_PRUNE_CLOSED_AFTER_SUMMARY = True

PRIMARY_ARM = "H_board_ts"
SECONDARY_ARM = "I_price_board"
ARM_FEATURES = {
    "H_board_ts": ["f_np_imb_chg_60"],
    "I_price_board": ["f_chase", "f_near_high", "f_np_imb_chg_60"],
    "K_v2_final": ["f_np_imb_chg_60"],
}

_DEFAULT_THRESHOLDS: dict[str, Optional[float]] = {
    "t_chase": None,
    "t_near": None,
    "t_mom_lo": None,
    "t_imb_chg": None,
    "t_ret60_neg": None,
    "t_bounce_hi": None,
}


def shadow_enabled(cfg: Any = None) -> bool:
    enabled, _src = resolve_cost_aware_entry_v2_shadow(cfg)
    return enabled


def shadow_enabled_with_source(cfg: Any = None) -> tuple[bool, str]:
    return resolve_cost_aware_entry_v2_shadow(cfg)


def _f(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    except (TypeError, ValueError):
        return None


def _cost_yen(entry: float) -> float:
    return round(float(entry) * 100.0 * (COST_BPS / 100.0), 2)


def join_key(
    *,
    position_id: str = "",
    symbol: str = "",
    entry_time: str = "",
    accepted_at: str = "",
) -> str:
    pid = str(position_id or "").strip()
    if pid:
        return pid
    sym = str(symbol or "").strip()
    et = str(entry_time or accepted_at or "").strip()
    if sym and et:
        return f"{sym}|{et}"
    return ""


def load_thresholds(native_root: Optional[Path] = None) -> tuple[dict[str, Optional[float]], str, int]:
    thr = dict(_DEFAULT_THRESHOLDS)
    roots = []
    if native_root is not None:
        roots.append(Path(native_root))
    roots.append(Path(__file__).resolve().parents[2])
    for root in roots:
        report = root / "results" / "research" / "cost_aware_v2" / "report.json"
        if report.is_file():
            try:
                payload = json.loads(report.read_text(encoding="utf-8"))
                for k, v in (payload.get("thresholds") or {}).items():
                    thr[k] = _f(v)
                hist = int(
                    (payload.get("n_np_pre_entry_board_history_formal")
                     or payload.get("n_np_pre_entry_board_history")
                     or 0)
                )
                return thr, "report.json", hist
            except Exception:
                pass
    return thr, "default_none", 0


def extract_features(
    trade: Mapping[str, Any],
    *,
    np_row: Optional[Mapping[str, Any]] = None,
) -> dict[str, Optional[float]]:
    rise = _f(trade.get("entry_rise_5min_pct") or trade.get("r60_sec"))
    near = _f(trade.get("entry_near_day_high_pct") or trade.get("day_high_distance_pct"))
    mom = _f(trade.get("entry_momentum_continuation_score") or trade.get("momentum_continuation_score"))
    chase = None
    if rise is not None and near is not None:
        chase = float(rise) + 0.5 * max(0.0, float(near))
    elif rise is not None:
        chase = float(rise)
    feats: dict[str, Optional[float]] = {
        "f_chase": chase,
        "f_near_high": near,
        "f_mom": mom,
        "f_bounce": _f(trade.get("microseq_bounce_from_recent_low")),
        "f_spread": _f(trade.get("spread_bps")),
        "f_pbv2": _f(trade.get("entry_expectancy_score_v2") or trade.get("continuation_quality_score")),
        "f_np_imb_chg_60": _f(trade.get("np_imb_chg_60s") or trade.get("np_imb_chg_60") or trade.get("f_np_imb_chg_60")),
        "f_np_ret_60": _f(trade.get("np_ret_60s") or trade.get("np_ret_60")),
    }
    if np_row and not bool(np_row.get("np_future_leakage")):
        feats["f_np_imb_chg_60"] = _f(np_row.get("np_imb_chg_60s") or np_row.get("np_imb_chg_60")) or feats["f_np_imb_chg_60"]
        feats["f_np_ret_60"] = _f(np_row.get("np_ret_60s") or np_row.get("np_ret_60")) or feats["f_np_ret_60"]
    return feats


def evaluate_v2(
    feats: Mapping[str, Optional[float]],
    *,
    thresholds: Optional[Mapping[str, Optional[float]]] = None,
    policy: str = "H_board_ts",
) -> dict[str, Any]:
    if policy == "K_v2_final":
        policy = "H_board_ts"
    thr = dict(_DEFAULT_THRESHOLDS)
    if thresholds:
        thr.update({k: _f(v) for k, v in thresholds.items()})
    reasons: list[str] = []
    missing = [k for k, v in feats.items() if v is None and k.startswith("f_")]

    def has(k: str) -> bool:
        return _f(feats.get(k)) is not None

    def fv(k: str) -> Optional[float]:
        return _f(feats.get(k))

    if policy == "H_board_ts":
        if not has("f_np_imb_chg_60"):
            return {
                "v2_verdict": "FAIL_OPEN",
                "v2_keep": True,
                "v2_score": 0.0,
                "reject_reasons": ["INSUFFICIENT_BOARD_HISTORY"],
                "missing": missing,
                "thresholds": thr,
                "policy": policy,
                "fail_open": True,
                "board_feature_available": False,
                "features_used": ARM_FEATURES["H_board_ts"],
            }
        imb = fv("f_np_imb_chg_60")
        if thr.get("t_imb_chg") is not None and imb is not None and imb <= float(thr["t_imb_chg"]):
            reasons.append("board_deteriorate")
    elif policy == "I_price_board":
        chase = fv("f_chase")
        near = fv("f_near_high")
        if thr.get("t_chase") is not None and chase is not None and chase >= float(thr["t_chase"]):
            reasons.append("high_chase")
        if thr.get("t_near") is not None and near is not None and near >= float(thr["t_near"]):
            reasons.append("high_near_high")
        imb = fv("f_np_imb_chg_60")
        if imb is not None and thr.get("t_imb_chg") is not None and imb <= float(thr["t_imb_chg"]):
            reasons.append("board_deteriorate")
    elif policy == "B_stop":
        chase = fv("f_chase")
        near = fv("f_near_high")
        if thr.get("t_chase") is not None and chase is not None and chase >= float(thr["t_chase"]):
            reasons.append("high_chase")
        if thr.get("t_near") is not None and near is not None and near >= float(thr["t_near"]):
            reasons.append("high_near_high")
    elif policy == "C_np":
        mom = fv("f_mom")
        weak_mom = thr.get("t_mom_lo") is not None and mom is not None and mom <= float(thr["t_mom_lo"])
        imb = fv("f_np_imb_chg_60")
        if weak_mom and imb is not None and thr.get("t_imb_chg") is not None:
            if imb <= float(thr["t_imb_chg"]):
                reasons.append("weak_mom_board_deteriorate")
    else:
        return {
            "v2_verdict": "FAIL_OPEN",
            "v2_keep": True,
            "v2_score": 0.0,
            "reject_reasons": [f"UNKNOWN_POLICY:{policy}"],
            "missing": missing,
            "thresholds": thr,
            "policy": policy,
            "fail_open": True,
            "board_feature_available": has("f_np_imb_chg_60"),
            "features_used": [],
        }

    score = 0.0
    for k, w in (("f_np_imb_chg_60", 1.0), ("f_chase", -0.8), ("f_near_high", -0.5)):
        val = _f(feats.get(k))
        if val is not None:
            score += w * float(val)
    keep = len(reasons) == 0
    return {
        "v2_verdict": "KEEP" if keep else "REJECT",
        "v2_keep": keep,
        "v2_score": round(score, 6),
        "reject_reasons": reasons,
        "missing": missing,
        "thresholds": thr,
        "policy": policy,
        "fail_open": False,
        "board_feature_available": has("f_np_imb_chg_60"),
        "features_used": list(ARM_FEATURES.get(policy, [])),
    }


@dataclass
class CostAwareV2ShadowState:
    join_failed_count: int = 0
    join_failure_reasons: dict = field(default_factory=dict)
    enabled: bool = False
    enabled_source: str = "default"
    # join_key -> candidate record (deduped)
    by_key: dict[str, dict[str, Any]] = field(default_factory=dict)
    thresholds: dict[str, Optional[float]] = field(default_factory=dict)
    threshold_source: str = "default_none"
    history_sample_count: int = 0
    session_dir: Optional[str] = None
    submit: int = 0
    cancel: int = 0
    live_order: int = 0
    # async JSONL / bounded state telemetry
    writer_alive: bool = False
    queue_depth: int = 0
    dropped_records: int = 0
    write_error_count: int = 0
    last_write_error: Optional[str] = None
    prune_count: int = 0
    state_high_watermark: int = 0
    session_closing_excluded_count: int = 0
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)
    _write_q: queue.Queue = field(default_factory=lambda: queue.Queue(maxsize=JSONL_QUEUE_MAX), repr=False, compare=False)
    _writer_thread: Optional[threading.Thread] = field(default=None, repr=False, compare=False)
    _writer_stop: threading.Event = field(default_factory=threading.Event, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.thresholds:
            thr, src, hist = load_thresholds()
            self.thresholds = thr
            self.threshold_source = src
            self.history_sample_count = hist

    def safety_zeros(self) -> dict[str, int]:
        return {"submit": 0, "cancel": 0, "live_order": 0}

    def ensure_writer(self) -> None:
        """Start daemon JSONL writer once (accept path only enqueues)."""
        with self._lock:
            if self._writer_thread is not None and self._writer_thread.is_alive():
                self.writer_alive = True
                return
            self._writer_stop.clear()
            th = threading.Thread(target=self._writer_loop, name="v2-shadow-jsonl", daemon=True)
            self._writer_thread = th
            th.start()
            self.writer_alive = True

    def _writer_loop(self) -> None:
        while not self._writer_stop.is_set():
            try:
                item = self._write_q.get(timeout=0.2)
            except queue.Empty:
                with self._lock:
                    self.queue_depth = self._write_q.qsize()
                continue
            if item is None:
                self._write_q.task_done()
                break
            session_dir, record = item
            try:
                append_shadow_jsonl_sync(Path(session_dir), record)
            except Exception as exc:
                with self._lock:
                    self.write_error_count += 1
                    self.last_write_error = f"{type(exc).__name__}:{exc}"
            finally:
                self._write_q.task_done()
                with self._lock:
                    self.queue_depth = self._write_q.qsize()
        with self._lock:
            self.writer_alive = False

    def enqueue_jsonl(self, record: Mapping[str, Any]) -> None:
        if not self.session_dir:
            return
        self.ensure_writer()
        try:
            self._write_q.put_nowait((self.session_dir, dict(record)))
            with self._lock:
                self.queue_depth = self._write_q.qsize()
        except queue.Full:
            with self._lock:
                self.dropped_records += 1
                self.queue_depth = self._write_q.qsize()

    def flush_writer(self, *, timeout_sec: float = JSONL_FLUSH_TIMEOUT_SEC) -> bool:
        """Best-effort drain; never block runner exit beyond timeout."""
        deadline = time.perf_counter() + float(timeout_sec)
        while time.perf_counter() < deadline:
            with self._lock:
                depth = self._write_q.qsize()
            if depth == 0:
                return True
            time.sleep(0.05)
        return False

    def stop_writer(self, *, timeout_sec: float = JSONL_FLUSH_TIMEOUT_SEC) -> None:
        self.flush_writer(timeout_sec=timeout_sec)
        self._writer_stop.set()
        try:
            self._write_q.put_nowait(None)
        except queue.Full:
            pass
        th = self._writer_thread
        if th is not None:
            th.join(timeout=min(0.5, float(timeout_sec)))

    def snapshot_records(self) -> list[dict[str, Any]]:
        with self._lock:
            return [deepcopy(v) for v in self.by_key.values()]

    def prune_closed(self, *, keep_open: bool = True, max_keys: int = STATE_MAX_KEYS) -> int:
        """Drop CLOSED records when over max_keys; never drop OPEN/PENDING."""
        with self._lock:
            if len(self.by_key) <= max_keys:
                self.state_high_watermark = max(self.state_high_watermark, len(self.by_key))
                return 0
            closed_keys = [
                k
                for k, v in self.by_key.items()
                if str(v.get("exit_status") or "") not in ("", "pending", "open", "OPEN")
                and v.get("exit_status") != "pending"
            ]
            # Prefer pruning oldest closed first
            closed_keys.sort(key=lambda k: str(self.by_key[k].get("timestamp") or ""))
            removed = 0
            for k in closed_keys:
                if len(self.by_key) <= max_keys:
                    break
                if keep_open and self.by_key[k].get("exit_status") == "pending":
                    continue
                del self.by_key[k]
                removed += 1
            self.prune_count += removed
            self.state_high_watermark = max(self.state_high_watermark, len(self.by_key) + removed)
            return removed


def note_accepted_candidate(
    state: CostAwareV2ShadowState,
    *,
    symbol: str,
    trade: Mapping[str, Any],
    np_row: Optional[Mapping[str, Any]] = None,
    session: str = "",
    position_id: str = "",
    entry_time: str = "",
    entry_price: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    """Observe-only record for an official Runtime ACCEPT (deduped by join key)."""
    key = join_key(
        position_id=position_id or str(trade.get("position_id") or ""),
        symbol=symbol or str(trade.get("symbol") or ""),
        entry_time=entry_time or str(trade.get("entry_time") or trade.get("accepted_at") or ""),
        accepted_at=str(trade.get("accepted_at") or ""),
    )
    if not key:
        return None
    with state._lock:
        if key in state.by_key:
            return state.by_key[key]

    feats = extract_features(trade, np_row=np_row)
    arm_h = evaluate_v2(feats, thresholds=state.thresholds, policy=PRIMARY_ARM)
    arm_i = evaluate_v2(feats, thresholds=state.thresholds, policy=SECONDARY_ARM)
    ep = _f(entry_price if entry_price is not None else trade.get("entry_price") or trade.get("CurrentPrice"))
    board_ok = feats.get("f_np_imb_chg_60") is not None
    ts = datetime.now(JST).isoformat(timespec="seconds")
    rec = {
        "timestamp": ts,
        "session": session,
        "symbol": symbol,
        "position_id": key,
        "join_key": key,
        "runtime_verdict": "ACCEPT",
        "H_board_ts_verdict": arm_h["v2_verdict"],
        "I_price_board_verdict": arm_i["v2_verdict"],
        "f_np_imb_chg_60": feats.get("f_np_imb_chg_60"),
        "f_chase": feats.get("f_chase"),
        "f_near_high": feats.get("f_near_high"),
        "board_feature_available": board_ok,
        "fail_open": bool(arm_h.get("fail_open")),
        "threshold": state.thresholds.get("t_imb_chg"),
        "threshold_source": state.threshold_source,
        "history_count": state.history_sample_count,
        "thresholds": dict(state.thresholds),
        "entry_price": ep,
        "exit_status": "pending",
        "actual_pnl_raw": None,
        "actual_pnl_5bps": None,
        "counterfactual_delta_raw": None,
        "counterfactual_delta_5bps": None,
        "winner": None,
        "stop": None,
        "no_progress": None,
        "arms": {
            "H_board_ts": arm_h,
            "I_price_board": arm_i,
        },
        "feature_values": feats,
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
    }
    with state._lock:
        if key in state.by_key:
            return state.by_key[key]
        if len(state.by_key) >= STATE_MAX_KEYS:
            state.prune_closed(max_keys=STATE_MAX_KEYS)
        if len(state.by_key) >= STATE_MAX_KEYS:
            # Do not grow unbounded; do not delete OPEN/PENDING. Refuse new shadow row.
            state.dropped_records += 1
            state.state_high_watermark = max(state.state_high_watermark, len(state.by_key) + 1)
            return None
        state.by_key[key] = rec
        state.state_high_watermark = max(state.state_high_watermark, len(state.by_key))
        if len(state.by_key) > STATE_MAX_KEYS:
            state.prune_closed(max_keys=STATE_MAX_KEYS)
    # Non-blocking enqueue — never sync-write on accept path
    try:
        state.enqueue_jsonl(rec)
    except Exception:
        pass
    return rec


# Backward-compatible alias used by older call sites
def note_candidate(
    state: CostAwareV2ShadowState,
    *,
    scan_id: str = "",
    symbol: str,
    trade: Mapping[str, Any],
    official_accept: bool,
    old_cost_aware_verdict: Optional[str] = None,
    np_row: Optional[Mapping[str, Any]] = None,
    session: str = "",
    position_id: str = "",
) -> Optional[dict[str, Any]]:
    del scan_id, old_cost_aware_verdict
    if not official_accept:
        return None
    return note_accepted_candidate(
        state,
        symbol=symbol,
        trade=trade,
        np_row=np_row,
        session=session,
        position_id=position_id,
        entry_time=str(trade.get("entry_time") or ""),
        entry_price=_f(trade.get("entry_price")),
    )


def _find_pending_for_exit(
    state: CostAwareV2ShadowState, exit_row: Mapping[str, Any]
) -> tuple[Optional[str], Optional[dict[str, Any]], Optional[str]]:
    """Resolve pending shadow row for an official EXIT. Returns (key, rec, fail_reason)."""
    pid = str(exit_row.get("position_id") or "").strip()
    sym = str(exit_row.get("symbol") or "").strip()
    et = str(exit_row.get("entry_time") or "").strip()
    key = join_key(position_id=pid, symbol=sym, entry_time=et)
    with state._lock:
        if key and key in state.by_key:
            return key, state.by_key[key], None
        # Alias: join_key was stored as position_id in note_accepted_candidate
        if pid:
            for k, r in state.by_key.items():
                if r.get("position_id") == pid or r.get("join_key") == pid:
                    return k, r, None
        # Soft: exact symbol|entry_time
        if sym and et:
            soft = f"{sym}|{et}"
            if soft in state.by_key:
                return soft, state.by_key[soft], None
            for k, r in state.by_key.items():
                if r.get("symbol") != sym:
                    continue
                if r.get("exit_status") == "closed":
                    continue
                # entry_time / timestamp substring or within same minute
                cand_ts = str(r.get("timestamp") or r.get("join_key") or k)
                if et in cand_ts or et in k or et[:16] in cand_ts:
                    return k, r, None
        # Soft: single pending for symbol
        if sym:
            pending = [
                (k, r)
                for k, r in state.by_key.items()
                if r.get("symbol") == sym and r.get("exit_status") != "closed"
            ]
            if len(pending) == 1:
                return pending[0][0], pending[0][1], None
            if len(pending) > 1:
                return None, None, "JOIN_FAILED"
        return None, None, "NO_RUNTIME_EXIT" if not pid and not (sym and et) else "JOIN_FAILED"


def note_exit(state: CostAwareV2ShadowState, exit_row: Mapping[str, Any]) -> bool:
    """Attach EXIT for counterfactual Summary evaluation only (not used at ENTRY time)."""
    key, rec, fail = _find_pending_for_exit(state, exit_row)
    if rec is None:
        # Only count as join failure when this exit's symbol is a V2 candidate.
        sym = str(exit_row.get("symbol") or "")
        with state._lock:
            known = any(r.get("symbol") == sym for r in state.by_key.values()) if sym else False
            if known:
                state.join_failed_count = int(getattr(state, "join_failed_count", 0) or 0) + 1
                reasons = getattr(state, "join_failure_reasons", None)
                if not isinstance(reasons, dict):
                    reasons = {}
                    state.join_failure_reasons = reasons
                rsn = fail or "JOIN_FAILED"
                reasons[rsn] = int(reasons.get(rsn, 0) or 0) + 1
        return False
    with state._lock:
        if rec.get("exit_status") == "closed":
            return True
        _apply_exit_to_rec(rec, exit_row)
        rec["join_status"] = "CLOSED_READY"
        rec["join_failure_reason"] = None
        payload = {**rec, "event": "exit_join", "join_key_resolved": key}
    try:
        state.enqueue_jsonl(payload)
    except Exception:
        pass
    return True


def _exit_match_score(rec: Mapping[str, Any], exit_row: Mapping[str, Any]) -> int:
    """Higher is better; 0 = no match."""
    sym = str(exit_row.get("symbol") or "")
    if str(rec.get("symbol") or "") != sym:
        return 0
    et = str(exit_row.get("entry_time") or "")
    pid = str(exit_row.get("position_id") or "")
    score = 1
    if pid and pid in (str(rec.get("position_id") or ""), str(rec.get("join_key") or "")):
        score += 100
    if et:
        blob = f"{rec.get('join_key')}|{rec.get('timestamp')}|{rec.get('position_id')}"
        if et in blob:
            score += 50
        elif et[:16] in blob:
            score += 20
    return score


def finalize_pending_exits(
    state: CostAwareV2ShadowState,
    exit_rows: Sequence[Mapping[str, Any]],
    *,
    session_force_close: bool = True,
) -> dict[str, int]:
    """Session-end join of official EXITs into pending V2 candidates (observe-only)."""
    exits = [dict(r) for r in exit_rows]
    used: set[int] = set()
    success = 0
    failed = 0
    with state._lock:
        pending_items = [
            (k, r) for k, r in state.by_key.items() if r.get("exit_status") != "closed"
        ]
    for _k, rec in pending_items:
        best_i = -1
        best_score = 0
        for i, er in enumerate(exits):
            if i in used:
                continue
            sc = _exit_match_score(rec, er)
            if sc > best_score:
                best_score = sc
                best_i = i
        if best_i >= 0 and best_score > 0:
            used.add(best_i)
            _apply_exit_to_rec(rec, exits[best_i])
            rec["join_status"] = "CLOSED_READY"
            rec["join_failure_reason"] = None
            success += 1
        else:
            failed += 1
            reason = "NO_RUNTIME_EXIT" if session_force_close else "PENDING"
            rec["join_status"] = reason
            rec["join_failure_reason"] = reason
            # Do not invent 0 yen for pending/unjoined.
            rec["exit_status"] = "pending"
            rec["actual_pnl_raw"] = None
            rec["actual_pnl_5bps"] = None
    with state._lock:
        pending = sum(1 for _k, r in state.by_key.items() if r.get("exit_status") != "closed")
    return {
        "join_success_count": success,
        "join_failed_count": failed,
        "pending_count": pending,
    }


def _apply_exit_to_rec(rec: dict[str, Any], exit_row: Mapping[str, Any]) -> None:
    pnl = _f(exit_row.get("actual_pnl_yen_100") or exit_row.get("pnl_yen_100"))
    ep = _f(rec.get("entry_price") or exit_row.get("entry_price"))
    xp = _f(exit_row.get("exit_price") or exit_row.get("current_price"))
    if pnl is None and ep is not None and xp is not None:
        pnl = round((xp - ep) * 100.0, 2)
    pnl_5 = None if pnl is None or ep is None else round(float(pnl) - _cost_yen(ep), 2)
    reason = str(exit_row.get("exit_reason") or exit_row.get("structural_exit_reason") or "")
    rec["exit_status"] = "closed"
    rec["exit_reason"] = reason
    rec["actual_pnl_raw"] = pnl
    rec["actual_pnl_5bps"] = pnl_5
    rec["winner"] = bool(pnl is not None and pnl > 0)
    rec["stop"] = reason == "stop_hit" or bool(exit_row.get("stop_hit"))
    rec["no_progress"] = reason == "no_progress_exit" or bool(exit_row.get("no_progress_exit"))
    # Per-arm CF delta vs runtime for this trade: REJECT → shadow 0, KEEP → actual
    for arm in (PRIMARY_ARM, SECONDARY_ARM):
        verd = rec.get(f"{arm}_verdict")
        if verd == "REJECT":
            # shadow skips → delta = 0 - actual = -actual
            rec[f"{arm}_cf_raw"] = 0.0
            rec[f"{arm}_cf_5bps"] = 0.0
            rec[f"{arm}_delta_raw"] = None if pnl is None else round(0.0 - float(pnl), 2)
            rec[f"{arm}_delta_5bps"] = None if pnl_5 is None else round(0.0 - float(pnl_5), 2)
        else:
            # KEEP / FAIL_OPEN: same as runtime
            rec[f"{arm}_cf_raw"] = pnl
            rec[f"{arm}_cf_5bps"] = pnl_5
            rec[f"{arm}_delta_raw"] = 0.0 if pnl is not None else None
            rec[f"{arm}_delta_5bps"] = 0.0 if pnl_5 is not None else None
    rec["counterfactual_delta_raw"] = rec.get("H_board_ts_delta_raw")
    rec["counterfactual_delta_5bps"] = rec.get("H_board_ts_delta_5bps")


def _arm_stats(records: Sequence[Mapping[str, Any]], arm: str) -> dict[str, Any]:
    n = len(records)
    keep = sum(1 for r in records if r.get(f"{arm}_verdict") in ("KEEP", "FAIL_OPEN"))
    reject = sum(1 for r in records if r.get(f"{arm}_verdict") == "REJECT")
    closed = [r for r in records if r.get("exit_status") == "closed" and r.get("actual_pnl_raw") is not None]
    pending = n - len(closed)
    winner_sac = sum(1 for r in closed if r.get(f"{arm}_verdict") == "REJECT" and r.get("winner"))
    loser_rej = sum(
        1
        for r in closed
        if r.get(f"{arm}_verdict") == "REJECT"
        and r.get("actual_pnl_raw") is not None
        and float(r["actual_pnl_raw"]) < 0
    )
    stop_av = sum(1 for r in closed if r.get(f"{arm}_verdict") == "REJECT" and r.get("stop"))
    np_av = sum(1 for r in closed if r.get(f"{arm}_verdict") == "REJECT" and r.get("no_progress"))

    if pending == n or not closed:
        return {
            "evaluated": n,
            "keep": keep,
            "reject": reject,
            "reject_rate": round(reject / n, 4) if n else None,
            "winner_sacrifice": winner_sac,
            "loser_reject": loser_rej,
            "stop_avoided": stop_av,
            "no_progress_avoided": np_av,
            "pnl_status": "pending",
            "counterfactual_raw_pnl": None,
            "counterfactual_pnl_5bps": None,
            "delta_raw": None,
            "delta_5bps": None,
            "runtime_raw_pnl": None,
            "runtime_pnl_5bps": None,
        }

    runtime_raw = sum(float(r["actual_pnl_raw"]) for r in closed)
    runtime_5_list = [
        float(r["actual_pnl_5bps"]) for r in closed if r.get("actual_pnl_5bps") is not None
    ]
    runtime_5 = sum(runtime_5_list)
    cf_raw = 0.0
    cf_5_list: list[float] = []
    for r in closed:
        verd = r.get(f"{arm}_verdict")
        if verd == "REJECT":
            continue
        cf_raw += float(r["actual_pnl_raw"])
        if r.get("actual_pnl_5bps") is not None:
            cf_5_list.append(float(r["actual_pnl_5bps"]))
    cf_5 = sum(cf_5_list)

    def _pf(yens: Sequence[float]) -> Optional[float]:
        gp = sum(y for y in yens if y > 0)
        gl = abs(sum(y for y in yens if y < 0))
        if gl > 1e-12:
            return round(gp / gl, 4)
        if gp > 0:
            return float("inf")
        return None

    runtime_pf = _pf(runtime_5_list) if runtime_5_list else None
    cost_aware_pf = _pf(cf_5_list) if cf_5_list else None
    pf_delta = None
    if isinstance(runtime_pf, (int, float)) and isinstance(cost_aware_pf, (int, float)):
        if math.isinf(runtime_pf) or math.isinf(cost_aware_pf):
            pf_delta = None if math.isinf(runtime_pf) and math.isinf(cost_aware_pf) else (
                "inf" if math.isinf(cost_aware_pf) else "-inf" if math.isinf(runtime_pf) else None
            )
        else:
            pf_delta = round(float(cost_aware_pf) - float(runtime_pf), 4)
    return {
        "evaluated": n,
        "keep": keep,
        "reject": reject,
        "reject_rate": round(reject / n, 4) if n else None,
        "winner_sacrifice": winner_sac,
        "loser_reject": loser_rej,
        "stop_avoided": stop_av,
        "no_progress_avoided": np_av,
        "pnl_status": "partial" if pending else "ready",
        "pending_exits": pending,
        "counterfactual_raw_pnl": round(cf_raw, 2),
        "counterfactual_pnl_5bps": round(cf_5, 2),
        "runtime_raw_pnl": round(runtime_raw, 2),
        "runtime_pnl_5bps": round(runtime_5, 2),
        "delta_raw": round(cf_raw - runtime_raw, 2),
        "delta_5bps": round(cf_5 - runtime_5, 2),
        "runtime_pf_5bps": runtime_pf if not (isinstance(runtime_pf, float) and math.isinf(runtime_pf)) else "inf",
        "cost_aware_pf_5bps": cost_aware_pf if not (isinstance(cost_aware_pf, float) and math.isinf(cost_aware_pf)) else "inf",
        "pf_delta_5bps": pf_delta,
        "delta_eligible_count": len(closed),
    }


def summarize_state(state: CostAwareV2ShadowState) -> dict[str, Any]:
    records = state.snapshot_records()
    n = len(records)
    board_ok = sum(1 for r in records if r.get("board_feature_available"))
    board_miss = n - board_ok
    fail_open = sum(1 for r in records if r.get("fail_open") or r.get("H_board_ts_verdict") == "FAIL_OPEN")
    warmup = 1 if state.history_sample_count < 20 and board_ok == 0 and n > 0 else 0
    h = _arm_stats(records, PRIMARY_ARM)
    i = _arm_stats(records, SECONDARY_ARM)
    all_fail_open = n > 0 and fail_open == n
    closed_n = sum(1 for r in records if r.get("exit_status") == "closed" and r.get("actual_pnl_raw") is not None)
    pending_n = n - closed_n
    join_failed_n = sum(
        1
        for r in records
        if r.get("join_failure_reason") in ("JOIN_FAILED", "NO_RUNTIME_EXIT", "NO_PRICE_PATH")
    )
    status = "CLOSED_READY" if n and pending_n == 0 else ("PENDING" if pending_n == n else "PARTIAL_PIPELINE")
    if all_fail_open:
        status = "FAIL_OPEN"
    return {
        "enabled": bool(state.enabled),
        "enabled_source": state.enabled_source,
        "observe_only": True,
        "blocks_real_entry": False,
        "real_block_count": 0,
        "discord_entry": False,
        "mainline_pnl_included": False,
        "canonical_pnl_mixed": False,
        "primary_arm": PRIMARY_ARM,
        "secondary_arm": SECONDARY_ARM,
        "evaluated_candidates": n,
        "evaluable_count": closed_n,
        "board_feature_available": board_ok,
        "board_feature_missing": board_miss,
        "fail_open_count": fail_open,
        "warmup_count": warmup,
        "join_success_count": closed_n,
        "join_failed_count": join_failed_n,
        "pending_count": pending_n,
        "delta_eligible_count": closed_n,
        "status": status,
        "verdict_label": "FAIL_OPEN" if all_fail_open else ("READY" if n and pending_n == 0 else ("PENDING" if n else "NO_CANDIDATES")),
        "verdict_reason": "INSUFFICIENT_BOARD_HISTORY" if all_fail_open else None,
        "H_board_ts": h,
        "I_price_board": {**i, "forward_candidate": False, "role": "secondary / non-forward"},
        "runtime_total_raw": h.get("runtime_raw_pnl"),
        "runtime_total_5bps": h.get("runtime_pnl_5bps"),
        "cost_aware_total_raw": h.get("counterfactual_raw_pnl"),
        "cost_aware_total_5bps": h.get("counterfactual_pnl_5bps"),
        "delta_total_raw": h.get("delta_raw"),
        "delta_total_5bps": h.get("delta_5bps"),
        "runtime_pf_5bps": h.get("runtime_pf_5bps"),
        "cost_aware_pf_5bps": h.get("cost_aware_pf_5bps"),
        "pf_delta_5bps": h.get("pf_delta_5bps"),
        "board_feature": {
            "name": "f_np_imb_chg_60",
            "availability": board_ok,
            "coverage_rate": round(board_ok / n, 4) if n else None,
            "missing_rate": round(board_miss / n, 4) if n else None,
            "threshold_used": state.thresholds.get("t_imb_chg"),
            "threshold_source": state.threshold_source,
            "history_sample_count": state.history_sample_count,
        },
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "shadow_name": SHADOW_NAME,
        "features_used": ARM_FEATURES[PRIMARY_ARM],
        "default_enabled_paper": True,
        "writer_alive": bool(state.writer_alive),
        "queue_depth": int(state.queue_depth),
        "dropped_records": int(state.dropped_records),
        "write_error_count": int(state.write_error_count),
        "last_write_error": state.last_write_error,
        "prune_count": int(state.prune_count),
        "state_high_watermark": int(state.state_high_watermark),
        "state_count": n,
        "session_closing_excluded_count": int(getattr(state, "session_closing_excluded_count", 0) or 0),
    }


def _fmt_yen(v: Any) -> str:
    x = _f(v)
    if x is None:
        return "N/A"
    sign = "+" if x > 0 else ""
    return f"{sign}{x:,.0f}円"


def _fmt_pf(v: Any) -> str:
    if v in (None, ""):
        return "N/A"
    if v == "inf" or (isinstance(v, float) and math.isinf(v)):
        return "inf"
    try:
        return f"{float(v):.2f}"
    except (TypeError, ValueError):
        return str(v)


def format_discord_lines(summary: Mapping[str, Any], *, am_pm: str = "") -> list[str]:
    """Independent Discord Summary block (not ENTRY notify). Fail-open on errors upstream."""
    try:
        block = summary.get("cost_aware_entry_v2_shadow")
        session_raw = am_pm or summary.get("am_pm") or ""
        if not session_raw and isinstance(block, Mapping):
            session_raw = block.get("session_kind") or ""
        session_tag = str(session_raw or "").upper()
        title = "[Cost-Aware V2 Shadow]"
        if session_tag in ("AM", "PM"):
            title = f"[Cost-Aware V2 Shadow - {session_tag}]"
        if not isinstance(block, Mapping):
            if summary.get("cost_aware_entry_v2_shadow_enabled") is False:
                return [title, "状態: OFF / observe-only", "Primary: H_board_ts"]
            return []
        if not block.get("enabled"):
            return [
                title,
                "状態: OFF / observe-only",
                f"Primary: {block.get('primary_arm') or PRIMARY_ARM}",
            ]

        h = block.get("H_board_ts") if isinstance(block.get("H_board_ts"), Mapping) else {}
        i = block.get("I_price_board") if isinstance(block.get("I_price_board"), Mapping) else {}
        lines = [
            title,
            "状態: ON / observe-only",
            f"対象候補: {block.get('evaluated_candidates') or 0}",
            f"評価可能: {block.get('evaluable_count') if block.get('evaluable_count') is not None else block.get('join_success_count') or 0}",
            f"join成功: {block.get('join_success_count') or 0}",
            f"join失敗: {block.get('join_failed_count') or 0}",
            f"pending: {block.get('pending_count') or 0}",
            "",
            "H_board_ts:",
            f"KEEP: {h.get('keep')}",
            f"REJECT: {h.get('reject')}",
            f"fail-open: {block.get('fail_open_count') or 0}",
            "",
            "I_price_board:",
            f"KEEP: {i.get('keep')}",
            f"REJECT: {i.get('reject')}",
            "",
            f"Runtime 5bps: {_fmt_yen(block.get('runtime_total_5bps') if block.get('runtime_total_5bps') is not None else h.get('runtime_pnl_5bps'))}",
            f"Cost-Aware 5bps: {_fmt_yen(block.get('cost_aware_total_5bps') if block.get('cost_aware_total_5bps') is not None else h.get('counterfactual_pnl_5bps'))}",
            f"ΔPnL 5bps: {_fmt_yen(block.get('delta_total_5bps') if block.get('delta_total_5bps') is not None else h.get('delta_5bps'))}",
            f"Runtime PF: {_fmt_pf(block.get('runtime_pf_5bps') if block.get('runtime_pf_5bps') is not None else h.get('runtime_pf_5bps'))}",
            f"Cost-Aware PF: {_fmt_pf(block.get('cost_aware_pf_5bps') if block.get('cost_aware_pf_5bps') is not None else h.get('cost_aware_pf_5bps'))}",
            f"PF差: {_fmt_pf(block.get('pf_delta_5bps') if block.get('pf_delta_5bps') is not None else h.get('pf_delta_5bps'))}",
            "",
            "判定: observation only",
        ]
        if block.get("verdict_label") == "FAIL_OPEN":
            lines = [
                title,
                "状態: ON / observe-only",
                f"対象候補: {block.get('evaluated_candidates') or 0}",
                "判定: FAIL_OPEN",
                f"理由: {block.get('verdict_reason') or 'INSUFFICIENT_BOARD_HISTORY'}",
                f"fail-open: {block.get('fail_open_count') or 0}",
                "損益評価: N/A",
            ]
        text = "\n".join(lines)
        if len(text) > 1800:
            lines = lines[:28] + ["…(truncated)"]
        return lines
    except Exception:
        return ["[Cost-Aware V2 Shadow]", "状態: degraded / observe-only", "Primary: H_board_ts"]


def append_shadow_jsonl_sync(session_dir: Path, record: Mapping[str, Any]) -> None:
    path = Path(session_dir) / "cost_aware_entry_v2_shadow.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(dict(record), ensure_ascii=False, default=str) + "\n")


def append_shadow_jsonl(session_dir: Path, record: Mapping[str, Any]) -> None:
    """Backward-compatible sync write (prefer state.enqueue_jsonl on accept path)."""
    append_shadow_jsonl_sync(session_dir, record)


def assert_no_orders(state: CostAwareV2ShadowState) -> None:
    if state.submit or state.cancel or state.live_order:
        raise AssertionError(
            f"V2 shadow order counters non-zero: submit={state.submit} "
            f"cancel={state.cancel} live_order={state.live_order}"
        )


def merge_daily_records(session_blocks: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, Any]]:
    """Merge AM/PM records by join_key (no double count)."""
    out: dict[str, dict[str, Any]] = {}
    for block in session_blocks:
        records = block.get("records") if isinstance(block, Mapping) else None
        if isinstance(records, Mapping):
            for k, v in records.items():
                if k not in out:
                    out[k] = dict(v)
                else:
                    # prefer closed exit
                    if out[k].get("exit_status") != "closed" and v.get("exit_status") == "closed":
                        out[k] = dict(v)
        by_key = block.get("by_key") if isinstance(block, Mapping) else None
        if isinstance(by_key, Mapping):
            for k, v in by_key.items():
                if k not in out:
                    out[k] = dict(v)
                elif out[k].get("exit_status") != "closed" and v.get("exit_status") == "closed":
                    out[k] = dict(v)
    return out
