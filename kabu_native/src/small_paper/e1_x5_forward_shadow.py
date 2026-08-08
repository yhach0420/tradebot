"""E1_X5 Forward Shadow — independent observe-only portfolio (never submits orders).

Paper Trade: default ON (unset/empty). Explicit 0/false/no/off disables.
Non-Paper / Live: always forced OFF. No PBv2 CAP / ENTRY / EXIT impact.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence, TextIO
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

ENV_KEY = "E1_X5_FORWARD_SHADOW"
STRATEGY_ID = "E1_X5"
THRESHOLD = 0.48256067040851486
SPREAD_MAX_BPS = 5.0
LOT = 100
COST_RATE = 0.0005
CAP = 5
STOP_BPS = -15.0
TRAIL_ARM_BPS = 20.0
GIVEBACK = 0.40
TARGET_BPS = 50.0
MAX_HOLD_SEC = 300.0

_TRUE = frozenset({"1", "true", "TRUE", "yes", "YES", "on", "ON"})
_FALSE = frozenset({"0", "false", "FALSE", "no", "NO", "off", "OFF"})

_LOG = logging.getLogger(__name__)
_startup_emitted = False


@dataclass(frozen=True)
class EnableDecision:
    enabled: bool
    reason: str
    env_raw: Optional[str]
    paper_runtime: bool


def _parse_env_token(env_value: Optional[str]) -> tuple[Optional[bool], bool]:
    """Return (parsed_bool_or_None, is_invalid).

    None parsed means unset or empty (Paper → default ON).
    Invalid non-empty tokens force OFF with warning.
    """
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


def resolve_e1_x5_forward_shadow_enabled(
    *,
    is_paper_runtime: bool,
    env_value: Optional[str],
) -> EnableDecision:
    """Single enablement decision for E1_X5 Forward Shadow."""
    if not is_paper_runtime:
        return EnableDecision(
            enabled=False,
            reason="NON_PAPER_FORCED_OFF",
            env_raw=env_value,
            paper_runtime=False,
        )
    parsed, invalid = _parse_env_token(env_value)
    if invalid:
        return EnableDecision(
            enabled=False,
            reason="INVALID_ENV_FORCED_OFF",
            env_raw=env_value,
            paper_runtime=True,
        )
    if parsed is None:
        return EnableDecision(
            enabled=True,
            reason="PAPER_DEFAULT_ON",
            env_raw=env_value,
            paper_runtime=True,
        )
    if parsed:
        return EnableDecision(
            enabled=True,
            reason="PAPER_ENV_ON",
            env_raw=env_value,
            paper_runtime=True,
        )
    return EnableDecision(
        enabled=False,
        reason="PAPER_ENV_OFF",
        env_raw=env_value,
        paper_runtime=True,
    )


def resolve_e1_x5_forward_shadow_from_runtime(
    cfg: Any = None,
    env: Optional[Mapping[str, str]] = None,
) -> EnableDecision:
    """Resolve using formal Paper/Live runtime flags (no filename heuristics)."""
    from small_paper.forward_observer_defaults import (
        is_live_or_real_order_context,
        is_paper_runtime,
    )

    src: Mapping[str, str] = env if env is not None else os.environ
    raw = src.get(ENV_KEY)
    # Live / real-order path: never enable, even if paper flag is somehow set.
    if is_live_or_real_order_context(cfg):
        return EnableDecision(
            enabled=False,
            reason="NON_PAPER_FORCED_OFF",
            env_raw=raw,
            paper_runtime=False,
        )
    paper = bool(is_paper_runtime(cfg))
    return resolve_e1_x5_forward_shadow_enabled(
        is_paper_runtime=paper,
        env_value=raw,
    )


def e1_x5_forward_shadow_enabled(
    env: Optional[Mapping[str, str]] = None,
    cfg: Any = None,
) -> bool:
    """Convenience wrapper around resolve_e1_x5_forward_shadow_from_runtime."""
    return resolve_e1_x5_forward_shadow_from_runtime(cfg=cfg, env=env).enabled


def format_e1_x5_forward_shadow_startup_lines(decision: EnableDecision) -> list[str]:
    if decision.enabled:
        return [
            "E1_X5_FORWARD_SHADOW: ENABLED",
            f"reason: {decision.reason}",
            "portfolio: independent CAP5",
            "pbv2_cap_impact: none",
            "order_api: disabled",
            "submit/cancel/live: 0/0/0",
        ]
    return [
        "E1_X5_FORWARD_SHADOW: DISABLED",
        f"reason: {decision.reason}",
    ]


def emit_e1_x5_forward_shadow_startup_once(
    decision: EnableDecision,
    *,
    stream: Optional[TextIO] = None,
    save_path: Optional[Path] = None,
    force: bool = False,
) -> list[str]:
    """Print/save enablement lines once per process (secrets never included)."""
    global _startup_emitted
    lines = format_e1_x5_forward_shadow_startup_lines(decision)
    if decision.reason == "INVALID_ENV_FORCED_OFF":
        _LOG.warning(
            "E1_X5_FORWARD_SHADOW invalid env value forced OFF (value not logged)"
        )
    if _startup_emitted and not force:
        return lines
    _startup_emitted = True
    out = stream if stream is not None else sys.stdout
    text = "\n".join(lines)
    try:
        print(text, file=out, flush=True)
    except Exception:
        pass
    if save_path is not None:
        try:
            save_path = Path(save_path)
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(text + "\n", encoding="utf-8")
        except Exception:
            pass
    return lines


def _bps(entry: float, px: float) -> float:
    return (px - entry) / entry * 10000.0 if entry > 0 else 0.0


def econ(entry: float, exit_px: float) -> dict[str, float]:
    gross = (exit_px - entry) * LOT
    cost = entry * LOT * COST_RATE
    net = gross - cost
    bps = net / (entry * LOT) * 10000.0 if entry > 0 else 0.0
    return {
        "gross_pnl_yen_100": gross,
        "cost_yen_100": cost,
        "net_pnl_yen_100": net,
        "net_bps": bps,
    }


@dataclass
class ShadowPosition:
    symbol: str
    entry_time: datetime
    entry_ask: float
    score: float
    spread_bps: float
    mfe_bps: float = 0.0
    mae_bps: float = 0.0
    trail_active: bool = False
    sample_id: str = ""
    day: str = ""


@dataclass
class E1X5ForwardShadowSession:
    """Independent CAP5 virtual portfolio for E1_X5."""

    enabled: bool = False
    enable_decision: Optional[EnableDecision] = None
    startup_lines: list[str] = field(default_factory=list)
    positions: dict[str, ShadowPosition] = field(default_factory=dict)
    entries: list[dict[str, Any]] = field(default_factory=list)
    exits: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[dict[str, Any]] = field(default_factory=list)
    cap_blocked: int = 0
    same_symbol_blocked: int = 0
    notify_fn: Optional[Callable[[str], None]] = None
    evaluated_count: int = 0
    missing_score_count: int = 0  # legacy alias; prefer missing_score_after_valid_tick
    no_evaluation_count: int = 0
    missing_score_after_valid_tick: int = 0
    tick_build_failed_count: int = 0
    identity_fail_count: int = 0
    duplicate_eval_suppressed: int = 0
    _evaluated_event_keys: set[str] = field(default_factory=set)

    # Forward gate targets (display only; progress comes from qualified Live provenance)
    FORWARD_GATE_TARGET_SESSIONS: int = 5
    FORWARD_GATE_TARGET_TRADES: int = 30

    @classmethod
    def maybe_create(
        cls,
        cfg: Any = None,
        *,
        save_path: Optional[Path] = None,
        emit_startup: Optional[bool] = None,
    ) -> "E1X5ForwardShadowSession":
        decision = resolve_e1_x5_forward_shadow_from_runtime(cfg=cfg)
        # Emit once on Paper create; non-paper stays quiet unless explicitly requested.
        do_emit = decision.paper_runtime if emit_startup is None else bool(emit_startup)
        if do_emit:
            lines = emit_e1_x5_forward_shadow_startup_once(
                decision, save_path=save_path
            )
        else:
            lines = format_e1_x5_forward_shadow_startup_lines(decision)
        return cls(enabled=decision.enabled, enable_decision=decision, startup_lines=lines)

    def _sess(self, ts: datetime) -> Optional[str]:
        from research.ueia_continuous_session_tradability_repair.session import continuous_session_id
        return continuous_session_id(ts)

    def _sess_end(self, ts: datetime) -> Optional[datetime]:
        from research.ueia_continuous_session_tradability_repair.session import session_end_time
        return session_end_time(ts)

    def _notify(self, text: str) -> None:
        if self.notify_fn is None:
            return
        try:
            self.notify_fn(text)
        except Exception:
            pass

    def _coerce_ts(self, ts: Any) -> datetime:
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                return ts.replace(tzinfo=JST)
            return ts.astimezone(JST)
        if isinstance(ts, (int, float)):
            return datetime.fromtimestamp(float(ts), tz=JST)
        if isinstance(ts, str) and ts.strip():
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=JST)
                return dt.astimezone(JST)
            except Exception:
                pass
        # Do not silently use wall-clock for strategy decisions.
        raise ValueError("E1_X5 decision_time_missing")

    def _event_key(self, symbol: str, ts: datetime, sample_id: str, event_sequence: Any) -> str:
        if sample_id:
            return f"sid:{sample_id}"
        if event_sequence is not None and str(event_sequence) != "":
            return f"seq:{symbol}|{event_sequence}|{ts.isoformat()}"
        return f"ts:{symbol}|{ts.isoformat()}"

    def on_missing_score(
        self,
        *,
        symbol: str,
        ts: Any,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
        reason: str = "NO_EVALUATION_MISSING_SCORE",
        sample_id: str = "",
        day: str = "",
        event_sequence: Any = None,
        mid: Optional[float] = None,
    ) -> str:
        """Explicit non-evaluation. Not an ENTRY=0.

        TICK_BUILD_FAILED / BAD_SYMBOL / SESSION_OTHER → no_evaluation (no valid tick).
        MODEL_UNAVAILABLE / SCORE_COMPUTE_FAILED / NO_EVALUATION_MISSING_SCORE →
        missing_score_after_valid_tick (sample due but score absent).
        """
        if not self.enabled:
            return "DISABLED"
        ts_dt = self._coerce_ts(ts)
        if symbol in self.positions and bid is not None and bid > 0:
            self._update_position(symbol, ts_dt, float(bid))
        reason_s = str(reason or "NO_EVALUATION_MISSING_SCORE")
        no_eval_reasons = {
            "TICK_BUILD_FAILED",
            "BAD_SYMBOL",
            "SESSION_OTHER",
            "NO_EVALUATION_DECISION_TIME_MISSING",
        }
        if reason_s in no_eval_reasons or reason_s.startswith("TICK_BUILD"):
            self.no_evaluation_count += 1
            if reason_s == "TICK_BUILD_FAILED":
                self.tick_build_failed_count += 1
            # Keep legacy counter for older readers, but Discord topline uses no_evaluation.
            self.missing_score_count += 1
        else:
            self.missing_score_after_valid_tick += 1
            self.missing_score_count += 1
        self.candidates.append({
            "timestamp": ts_dt,
            "symbol": symbol,
            "score": None,
            "threshold": THRESHOLD,
            "spread_bps": None,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "entry_decision": "NO_EVALUATION",
            "reject_reason": reason_s,
            "active_positions": len(self.positions),
            "cap": CAP,
            "sample_id": sample_id,
            "day": day or ts_dt.strftime("%Y%m%d"),
            "event_sequence": event_sequence,
        })
        return reason_s

    def on_identity_fail(
        self,
        *,
        symbol: str,
        ts: Any,
        reason: str,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
    ) -> str:
        if not self.enabled:
            return "DISABLED"
        ts_dt = self._coerce_ts(ts)
        if symbol in self.positions and bid is not None and bid > 0:
            self._update_position(symbol, ts_dt, float(bid))
        self.identity_fail_count += 1
        self.candidates.append({
            "timestamp": ts_dt,
            "symbol": symbol,
            "score": None,
            "threshold": THRESHOLD,
            "spread_bps": None,
            "bid": bid,
            "ask": ask,
            "mid": None,
            "entry_decision": "NO_EVALUATION",
            "reject_reason": reason,
            "active_positions": len(self.positions),
            "cap": CAP,
        })
        return reason

    def on_quote(
        self,
        *,
        symbol: str,
        ts: Any,
        bid: Optional[float],
        ask: Optional[float],
        score: Optional[float] = None,
        spread_bps: Optional[float] = None,
        sample_id: str = "",
        day: str = "",
        mid: Optional[float] = None,
        event_sequence: Any = None,
    ) -> None:
        if not self.enabled:
            return
        ts_dt = self._coerce_ts(ts)
        if symbol in self.positions and bid is not None and bid > 0:
            self._update_position(symbol, ts_dt, float(bid))
        if score is None:
            # Position mark only — not an evaluation attempt (NO_SAMPLE path).
            return
        self.try_entry(
            symbol=symbol,
            ts=ts_dt,
            bid=bid,
            ask=ask,
            score=float(score),
            spread_bps=spread_bps,
            sample_id=sample_id,
            day=day,
            mid=mid,
            event_sequence=event_sequence,
        )

    def evaluate_entry_gates(
        self,
        *,
        symbol: str,
        ts: datetime,
        bid: Optional[float],
        ask: Optional[float],
        score: float,
        spread_bps: Optional[float],
    ) -> tuple[Optional[str], float]:
        """Shared ENTRY gate ladder (Paper Runtime + G1). Returns (reject_reason|None, spread_bps)."""
        if self._sess(ts) is None:
            return "SESSION_INVALID", float(spread_bps or 0.0)
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
            return "INVALID_QUOTE", float(spread_bps or 0.0)
        if spread_bps is None:
            spread_bps = (ask - bid) / ask * 10000.0
        if score < THRESHOLD:
            return "SCORE_BELOW_THRESHOLD", float(spread_bps)
        if float(spread_bps) > SPREAD_MAX_BPS + 1e-9:
            return "SPREAD_OVER_5BPS", float(spread_bps)
        if symbol in self.positions:
            self.same_symbol_blocked += 1
            return "SAME_SYMBOL_OPEN", float(spread_bps)
        if len(self.positions) >= CAP:
            self.cap_blocked += 1
            return "CAP5_BLOCKED", float(spread_bps)
        return None, float(spread_bps)

    def try_entry(
        self,
        *,
        symbol: str,
        ts: datetime,
        bid: Optional[float],
        ask: Optional[float],
        score: float,
        spread_bps: Optional[float],
        sample_id: str = "",
        day: str = "",
        mid: Optional[float] = None,
        event_sequence: Any = None,
    ) -> Optional[str]:
        if not self.enabled:
            return "DISABLED"
        ts = self._coerce_ts(ts)
        ek = self._event_key(symbol, ts, sample_id, event_sequence)
        if ek in self._evaluated_event_keys:
            self.duplicate_eval_suppressed += 1
            return "DUPLICATE_EVENT"
        self._evaluated_event_keys.add(ek)
        self.evaluated_count += 1
        reason, spread_bps = self.evaluate_entry_gates(
            symbol=symbol, ts=ts, bid=bid, ask=ask, score=score, spread_bps=spread_bps
        )
        if reason is not None:
            self._log_candidate(ts, symbol, score, spread_bps, bid, ask, mid, False, reason)
            return reason
        pos = ShadowPosition(
            symbol=symbol, entry_time=ts, entry_ask=float(ask), score=float(score),
            spread_bps=float(spread_bps), sample_id=sample_id,
            day=day or ts.strftime("%Y%m%d"),
        )
        self.positions[symbol] = pos
        self.entries.append({
            "timestamp": ts, "symbol": symbol, "score": score, "threshold": THRESHOLD,
            "spread_bps": spread_bps, "bid": bid, "ask": ask, "mid": mid,
            "entry_decision": "ENTER", "reject_reason": None,
            "active_positions": len(self.positions), "cap": CAP,
            "sample_id": sample_id, "day": pos.day,
            "event_sequence": event_sequence,
        })
        self._log_candidate(ts, symbol, score, spread_bps, bid, ask, mid, True, None)
        self._notify(
            f"[E1_X5 SHADOW ENTRY]\n{symbol}\nscore={score:.6f}\nspread_bps={spread_bps:.3f}\n"
            f"entry_ask={ask}\nactive_positions={len(self.positions)}"
        )
        return None

    def _log_candidate(self, ts, symbol, score, spread_bps, bid, ask, mid, entered, reason):
        self.candidates.append({
            "timestamp": ts, "symbol": symbol, "score": score, "threshold": THRESHOLD,
            "spread_bps": spread_bps, "bid": bid, "ask": ask, "mid": mid,
            "entry_decision": "ENTER" if entered else "REJECT",
            "reject_reason": reason, "active_positions": len(self.positions), "cap": CAP,
        })

    def _update_position(self, symbol: str, ts: datetime, bid: float) -> None:
        pos = self.positions.get(symbol)
        if pos is None:
            return
        sess0 = self._sess(pos.entry_time)
        sess1 = self._sess(ts)
        if sess0 is not None and sess1 != sess0:
            self._close(pos, ts, bid, "SESSION_CLOSE")
            return
        end = self._sess_end(pos.entry_time)
        if end is not None and ts >= end:
            self._close(pos, ts, bid, "SESSION_CLOSE")
            return
        ret = _bps(pos.entry_ask, bid)
        pos.mfe_bps = max(pos.mfe_bps, ret)
        pos.mae_bps = min(pos.mae_bps, ret)
        if pos.mfe_bps >= TRAIL_ARM_BPS - 1e-9:
            pos.trail_active = True
        hold = (ts - pos.entry_time).total_seconds()
        if ret <= STOP_BPS + 1e-9:
            self._close(pos, ts, bid, "STOP")
            return
        if ret >= TARGET_BPS - 1e-9:
            self._close(pos, ts, bid, "TARGET")
            return
        if pos.trail_active:
            floor = pos.mfe_bps * (1.0 - GIVEBACK)
            if ret <= floor + 1e-9:
                self._close(pos, ts, bid, "TRAILING")
                return
        if hold >= MAX_HOLD_SEC - 1e-9:
            self._close(pos, ts, bid, "MAX_HOLD")
            return

    def _close(self, pos: ShadowPosition, ts: datetime, bid: float, reason: str) -> None:
        if pos.symbol not in self.positions:
            return
        e = econ(pos.entry_ask, bid)
        hold = (ts - pos.entry_time).total_seconds()
        self.exits.append({
            "entry_time": pos.entry_time, "exit_time": ts, "symbol": pos.symbol,
            "entry_ask": pos.entry_ask, "exit_bid": bid, "exit_reason": reason,
            "score": pos.score, "spread_bps": pos.spread_bps,
            "mfe_bps": pos.mfe_bps, "mae_bps": pos.mae_bps, "holding_sec": hold,
            "sample_id": pos.sample_id, "day": pos.day, **e,
        })
        del self.positions[pos.symbol]
        self._notify(
            f"[E1_X5 SHADOW EXIT]\n{pos.symbol}\nexit_reason={reason}\n"
            f"entry_ask={pos.entry_ask}\nexit_bid={bid}\n"
            f"pnl_yen_100={e['net_pnl_yen_100']:.2f}\npnl_bps={e['net_bps']:.3f}\n"
            f"holding_sec={hold:.1f}\nmfe_bps={pos.mfe_bps:.3f}"
        )

    def force_enter(self, **kwargs) -> None:
        """Parity helper: enter bypassing CAP (caller manages portfolio set)."""
        prev_cap = CAP
        # temporarily clear CAP by removing other positions tracking — not used; direct inject
        symbol = kwargs["symbol"]
        ts = kwargs["ts"]
        ask = float(kwargs["ask"])
        score = float(kwargs["score"])
        spread_bps = float(kwargs.get("spread_bps") or 0.0)
        self.positions[symbol] = ShadowPosition(
            symbol=symbol, entry_time=ts, entry_ask=ask, score=score,
            spread_bps=spread_bps, sample_id=kwargs.get("sample_id") or "",
            day=kwargs.get("day") or ts.strftime("%Y%m%d"),
        )
        self.entries.append({
            "timestamp": ts, "symbol": symbol, "score": score, "ask": ask,
            "spread_bps": spread_bps, "sample_id": kwargs.get("sample_id"),
            "entry_decision": "ENTER", "cap": prev_cap,
        })

    def exclusive_entry_funnel(self) -> dict[str, int]:
        """Exclusive terminal buckets among EVALUATED candidates only.

        Denominator = evaluated SCORE attempts (ENTER + REJECT). NO_EVALUATION
        events are excluded from this funnel and reported via no_evaluation_breakdown().
        """
        buckets = {
            "missing_score_after_valid_tick": 0,
            "threshold_fail": 0,
            "spread_fail": 0,
            "same_symbol_blocked": 0,
            "cap_blocked": 0,
            "accepted_entry": 0,
            "other_reject": 0,
        }
        for c in self.candidates:
            dec = str(c.get("entry_decision") or "")
            reason = str(c.get("reject_reason") or "")
            # NO_EVALUATION is never part of the exclusive evaluated funnel
            if dec == "NO_EVALUATION":
                continue
            if dec == "ENTER":
                buckets["accepted_entry"] += 1
            elif reason == "SCORE_BELOW_THRESHOLD":
                buckets["threshold_fail"] += 1
            elif reason == "SPREAD_OVER_5BPS":
                buckets["spread_fail"] += 1
            elif reason == "SAME_SYMBOL_OPEN":
                buckets["same_symbol_blocked"] += 1
            elif reason == "CAP5_BLOCKED":
                buckets["cap_blocked"] += 1
            elif reason in ("MODEL_UNAVAILABLE",) or reason.startswith("SCORE_COMPUTE_FAILED"):
                # Evaluated-path missing score after valid tick (rare; usually NO_EVALUATION)
                buckets["missing_score_after_valid_tick"] += 1
            elif "MISSING" in reason.upper() or reason.startswith("NO_EVALUATION_MISSING"):
                buckets["missing_score_after_valid_tick"] += 1
            else:
                buckets["other_reject"] += 1
        buckets["terminal_sum"] = (
            buckets["missing_score_after_valid_tick"]
            + buckets["threshold_fail"]
            + buckets["spread_fail"]
            + buckets["same_symbol_blocked"]
            + buckets["cap_blocked"]
            + buckets["accepted_entry"]
            + buckets["other_reject"]
        )
        return buckets

    def no_evaluation_breakdown(self) -> dict[str, Any]:
        """Separate aggregate — not part of entry_funnel_exclusive."""
        reasons: dict[str, int] = {}
        for c in self.candidates:
            if str(c.get("entry_decision") or "") != "NO_EVALUATION":
                continue
            r = str(c.get("reject_reason") or "UNKNOWN")
            reasons[r] = reasons.get(r, 0) + 1
        return {
            "evaluated": int(self.evaluated_count),
            "no_evaluation": int(self.no_evaluation_count),
            "no_evaluation_reason_breakdown": dict(reasons),
            "missing_score_after_valid_tick": int(self.missing_score_after_valid_tick),
            "tick_build_failed": int(self.tick_build_failed_count),
        }

    def forward_gate_display(
        self,
        *,
        valid_sessions: int = 0,
        valid_trades: int = 0,
        complete_am_pm_days: int = 0,
        excluded: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """Separate target vs valid Live progress (Replay/fixture/synthetic excluded)."""
        return {
            "forward_gate_target_sessions": int(self.FORWARD_GATE_TARGET_SESSIONS),
            "forward_gate_target_trades": int(self.FORWARD_GATE_TARGET_TRADES),
            "valid_progress_sessions": int(valid_sessions),
            "valid_progress_trades": int(valid_trades),
            "complete_am_pm_days": int(complete_am_pm_days),
            "excluded": list(excluded or ["20260727 PM (NOT_ADOPTED)"]),
            "lines": [
                (
                    f"Forward gate target: {self.FORWARD_GATE_TARGET_SESSIONS} valid sessions / "
                    f"{self.FORWARD_GATE_TARGET_TRADES} completed trades"
                ),
                f"Valid progress: {int(valid_sessions)} sessions / {int(valid_trades)} trades",
                f"Complete AM+PM days: {int(complete_am_pm_days)}",
                f"Excluded: {', '.join(excluded or ['20260727 PM (NOT_ADOPTED)'])}",
            ],
        }

    def stop_overshoot_yen(self) -> float:
        """Price move beyond -15bps STOP threshold only (excludes explicit 5bps cost)."""
        total = 0.0
        for x in self.exits:
            if str(x.get("exit_reason") or "") != "STOP":
                continue
            ask = float(x.get("entry_ask") or 0)
            bid = float(x.get("exit_bid") or 0)
            if ask <= 0 or bid <= 0:
                continue
            stop_px = ask * (1.0 + STOP_BPS / 10000.0)  # STOP_BPS is negative
            # Overshoot = further adverse move past stop threshold price
            if bid < stop_px:
                total += (bid - stop_px) * 100.0
        return float(total)

    def summary(self) -> dict[str, Any]:
        pnls = [x["net_pnl_yen_100"] for x in self.exits]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        draws = [p for p in pnls if p == 0]
        bps = [x["net_bps"] for x in self.exits]
        holds = [float(x.get("holding_sec") or 0) for x in self.exits]
        eq = peak = max_dd = 0.0
        for p in pnls:
            eq += p
            peak = max(peak, eq)
            max_dd = min(max_dd, eq - peak)
        reasons: dict[str, int] = {}
        for x in self.exits:
            reasons[x["exit_reason"]] = reasons.get(x["exit_reason"], 0) + 1
        decision = self.enable_decision
        funnel = self.exclusive_entry_funnel()
        no_eval = self.no_evaluation_breakdown()
        return {
            "strategy": STRATEGY_ID, "enabled": self.enabled,
            "enable_reason": decision.reason if decision else None,
            "paper_runtime": decision.paper_runtime if decision else None,
            "startup_lines": list(self.startup_lines),
            "trades": len(self.exits), "open_positions": len(self.positions),
            "wins": len(wins), "losses": len(losses), "draws": len(draws),
            "total_pnl_yen_100": sum(pnls) if pnls else 0.0,
            "avg_pnl_yen_100": (sum(pnls) / len(pnls)) if pnls else None,
            "avg_bps": (sum(bps) / len(bps)) if bps else None,
            "avg_holding_sec": (sum(holds) / len(holds)) if holds else None,
            "best_trade_yen_100": max(pnls) if pnls else None,
            "worst_trade_yen_100": min(pnls) if pnls else None,
            "profit_factor_yen_100": (sum(wins) / abs(sum(losses))) if losses else None,
            "max_drawdown": max_dd, "exit_reasons": reasons,
            "cap_blocked": self.cap_blocked, "same_symbol_blocked": self.same_symbol_blocked,
            "candidates": len(self.candidates), "entries_n": len(self.entries),
            "evaluated_count": int(self.evaluated_count),
            "no_evaluation_count": int(self.no_evaluation_count),
            "missing_score_after_valid_tick": int(self.missing_score_after_valid_tick),
            "tick_build_failed_count": int(self.tick_build_failed_count),
            # Legacy total of on_missing_score calls (includes no_evaluation + missing_after_valid_tick)
            "missing_score_count": int(self.missing_score_count),
            "identity_fail_count": int(self.identity_fail_count),
            "duplicate_eval_suppressed": int(self.duplicate_eval_suppressed),
            "candidate_count": len(self.candidates),
            "entry_funnel_exclusive": funnel,
            "no_evaluation_breakdown": no_eval,
            "stop_overshoot_yen_100": self.stop_overshoot_yen(),
            "forward_gate": self.forward_gate_display(),
            "topline_evaluated_no_evaluation": {
                "evaluated": int(self.evaluated_count),
                "no_evaluation": int(self.no_evaluation_count),
            },
            "evaluation_status": (
                "NO_EVALUATION"
                if self.evaluated_count == 0 and self.no_evaluation_count > 0
                else (
                    "EVALUATED"
                    if self.evaluated_count > 0
                    else "NO_EVALUATION"
                )
            ),
            "order_api": "disabled",
            "pbv2_cap_impact": "none",
            "submit": 0, "cancel": 0, "live_order": 0,
        }

    def virtual_ledger_payload(self) -> dict[str, Any]:
        """Independent virtual ENTRY/EXIT ledger (observe-only; not PBv2)."""
        from small_paper.e1_x5_artifact_sot import canonical_ledger_hash

        open_rows = [
            {
                "symbol": p.symbol,
                "entry_time": p.entry_time,
                "entry_ask": p.entry_ask,
                "score": p.score,
                "spread_bps": p.spread_bps,
                "sample_id": p.sample_id,
                "day": p.day,
            }
            for p in self.positions.values()
        ]
        exits = list(self.exits)
        for row in exits:
            if "holding_sec" not in row:
                et, xt = row.get("entry_time"), row.get("exit_time")
                if hasattr(et, "timestamp") and hasattr(xt, "timestamp"):
                    row = dict(row)
                    row["holding_sec"] = (xt - et).total_seconds()
        # normalize exits for hash (copy with holding_sec)
        exits_norm: list[dict[str, Any]] = []
        for x in exits:
            row = dict(x)
            if "holding_sec" not in row:
                et, xt = row.get("entry_time"), row.get("exit_time")
                if hasattr(et, "timestamp") and hasattr(xt, "timestamp"):
                    row["holding_sec"] = (xt - et).total_seconds()
                else:
                    row["holding_sec"] = 0.0
            exits_norm.append(row)
        ledger_sha = canonical_ledger_hash(exits_norm, version="v1") if exits_norm else canonical_ledger_hash([], version="v1")
        agg = aggregate_from_virtual_ledger(
            entries=list(self.entries),
            exits=exits_norm,
            open_rows=open_rows,
            summary=self.summary(),
        )
        return {
            "strategy": STRATEGY_ID,
            "observe_only": True,
            "g1_adopted": False,
            "ledger_sha256": ledger_sha,
            "ledger_hash_version": "e1_x5_trade_ledger_hash_v1",
            "entries": list(self.entries),
            "exits": exits_norm,
            "open_positions": open_rows,
            "aggregates": agg,
            "submit": 0,
            "cancel": 0,
            "live_order": 0,
        }


def aggregate_from_virtual_ledger(
    *,
    entries: Sequence[Mapping[str, Any]],
    exits: Sequence[Mapping[str, Any]],
    open_rows: Sequence[Mapping[str, Any]],
    summary: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """Independent aggregates from virtual ledger only (not PBv2 reject/delta)."""
    s = dict(summary or {})
    pnls = [float(x.get("net_pnl_yen_100") or 0) for x in exits]
    wins = sum(1 for p in pnls if p > 0)
    losses = sum(1 for p in pnls if p < 0)
    draws = sum(1 for p in pnls if p == 0)
    gp = sum(p for p in pnls if p > 0)
    gl = -sum(p for p in pnls if p < 0)
    pf = (gp / gl) if gl > 0 else None
    reasons: dict[str, int] = {}
    for x in exits:
        r = str(x.get("exit_reason") or "")
        reasons[r] = reasons.get(r, 0) + 1
    return {
        "evaluated": int(s.get("evaluated_count") or 0),
        "no_evaluation": int(s.get("no_evaluation_count") or 0),
        "ENTRY": len(entries),
        "completed": len(exits),
        "open": len(open_rows),
        "net_pnl_yen_100": float(sum(pnls)) if pnls else 0.0,
        "profit_factor": pf,
        "wins": wins,
        "losses": losses,
        "draws": draws,
        "exit_reasons": reasons,
        "valid_progress": s.get("forward_gate") if isinstance(s.get("forward_gate"), Mapping) else {},
        "cap_blocked": int(s.get("cap_blocked") or 0),
        "same_symbol_blocked": int(s.get("same_symbol_blocked") or 0),
    }


def persist_e1_x5_virtual_ledger(session: E1X5ForwardShadowSession, output_dir: Path) -> dict[str, Any]:
    """Write independent E1_X5 virtual ledger files under session output_dir."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    payload = session.virtual_ledger_payload()

    def _json_default(obj: Any) -> Any:
        if isinstance(obj, datetime):
            dt = obj if obj.tzinfo else obj.replace(tzinfo=JST)
            return dt.astimezone(JST).isoformat()
        raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")

    ledger_path = out / "e1_x5_virtual_ledger.json"
    entries_path = out / "e1_x5_virtual_entries.jsonl"
    exits_path = out / "e1_x5_virtual_exits.jsonl"
    ledger_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    with entries_path.open("w", encoding="utf-8") as fh:
        for row in payload["entries"]:
            fh.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")
    with exits_path.open("w", encoding="utf-8") as fh:
        for row in payload["exits"]:
            fh.write(json.dumps(row, ensure_ascii=False, default=_json_default) + "\n")
    meta = {
        "ledger_path": str(ledger_path),
        "entries_path": str(entries_path),
        "exits_path": str(exits_path),
        "ledger_sha256": payload["ledger_sha256"],
        "aggregates": payload["aggregates"],
        "entries_n": len(payload["entries"]),
        "exits_n": len(payload["exits"]),
        "open_n": len(payload["open_positions"]),
    }
    (out / "e1_x5_virtual_ledger_meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return meta


def simulate_x5_on_ticks(
    ticks: Sequence[Any],
    entry_idx: int,
    entry_time: datetime,
    entry_ask: float,
    *,
    bid_fn,
    session_id_fn,
    session_end_fn,
) -> dict[str, Any]:
    """Pure X5 exit simulation (runtime-identical rules) for parity."""
    sess = E1X5ForwardShadowSession(enabled=True)
    sess.force_enter(
        symbol="__SYM__", ts=entry_time, ask=entry_ask, score=THRESHOLD,
        spread_bps=0.0, sample_id="parity", day=entry_time.strftime("%Y%m%d"),
    )
    # monkeypatch session helpers
    sess._sess = session_id_fn  # type: ignore[method-assign]
    sess._sess_end = session_end_fn  # type: ignore[method-assign]
    sess0 = session_id_fn(entry_time)
    for j in range(entry_idx, len(ticks)):
        t = ticks[j]
        if session_id_fn(t.ts) != sess0:
            # close at last bid before boundary
            for k in range(j - 1, entry_idx - 1, -1):
                bb = bid_fn(ticks[k])
                if bb is not None and "__SYM__" in sess.positions:
                    sess._close(sess.positions["__SYM__"], ticks[k].ts, float(bb), "SESSION_CLOSE")
                    break
            break
        b = bid_fn(t)
        if b is None:
            continue
        sess._update_position("__SYM__", t.ts, float(b))
        if "__SYM__" not in sess.positions:
            break
    if "__SYM__" in sess.positions:
        for k in range(len(ticks) - 1, entry_idx - 1, -1):
            if session_id_fn(ticks[k].ts) != sess0:
                continue
            bb = bid_fn(ticks[k])
            if bb is not None:
                sess._close(sess.positions["__SYM__"], ticks[k].ts, float(bb), "DATA_END")
                break
    if not sess.exits:
        return {}
    return sess.exits[0]
