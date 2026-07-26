"""E1_X5 Forward Shadow — independent observe-only portfolio (never submits orders).

Paper Trade: default ON (unset/empty). Explicit 0/false/no/off disables.
Non-Paper / Live: always forced OFF. No PBv2 CAP / ENTRY / EXIT impact.
"""
from __future__ import annotations

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

    def on_quote(
        self,
        *,
        symbol: str,
        ts: datetime,
        bid: Optional[float],
        ask: Optional[float],
        score: Optional[float] = None,
        spread_bps: Optional[float] = None,
        sample_id: str = "",
        day: str = "",
        mid: Optional[float] = None,
    ) -> None:
        if not self.enabled:
            return
        if symbol in self.positions and bid is not None and bid > 0:
            self._update_position(symbol, ts, float(bid))
        if score is not None:
            self.try_entry(
                symbol=symbol, ts=ts, bid=bid, ask=ask, score=float(score),
                spread_bps=spread_bps, sample_id=sample_id, day=day, mid=mid,
            )

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
    ) -> Optional[str]:
        if not self.enabled:
            return "DISABLED"
        if self._sess(ts) is None:
            reason = "SESSION_INVALID"
            self._log_candidate(ts, symbol, score, spread_bps, bid, ask, mid, False, reason)
            return reason
        if bid is None or ask is None or bid <= 0 or ask <= 0 or ask < bid:
            reason = "INVALID_QUOTE"
            self._log_candidate(ts, symbol, score, spread_bps, bid, ask, mid, False, reason)
            return reason
        if spread_bps is None:
            spread_bps = (ask - bid) / ask * 10000.0
        if score < THRESHOLD:
            reason = "SCORE_BELOW_THRESHOLD"
            self._log_candidate(ts, symbol, score, spread_bps, bid, ask, mid, False, reason)
            return reason
        if float(spread_bps) > SPREAD_MAX_BPS + 1e-9:
            reason = "SPREAD_OVER_5BPS"
            self._log_candidate(ts, symbol, score, spread_bps, bid, ask, mid, False, reason)
            return reason
        if symbol in self.positions:
            reason = "SAME_SYMBOL_OPEN"
            self.same_symbol_blocked += 1
            self._log_candidate(ts, symbol, score, spread_bps, bid, ask, mid, False, reason)
            return reason
        if len(self.positions) >= CAP:
            reason = "CAP5_BLOCKED"
            self.cap_blocked += 1
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

    def summary(self) -> dict[str, Any]:
        pnls = [x["net_pnl_yen_100"] for x in self.exits]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        bps = [x["net_bps"] for x in self.exits]
        eq = peak = max_dd = 0.0
        for p in pnls:
            eq += p
            peak = max(peak, eq)
            max_dd = min(max_dd, eq - peak)
        reasons: dict[str, int] = {}
        for x in self.exits:
            reasons[x["exit_reason"]] = reasons.get(x["exit_reason"], 0) + 1
        decision = self.enable_decision
        return {
            "strategy": STRATEGY_ID, "enabled": self.enabled,
            "enable_reason": decision.reason if decision else None,
            "paper_runtime": decision.paper_runtime if decision else None,
            "startup_lines": list(self.startup_lines),
            "trades": len(self.exits), "open_positions": len(self.positions),
            "wins": len(wins), "losses": len(losses),
            "total_pnl_yen_100": sum(pnls) if pnls else 0.0,
            "avg_pnl_yen_100": (sum(pnls) / len(pnls)) if pnls else None,
            "avg_bps": (sum(bps) / len(bps)) if bps else None,
            "profit_factor_yen_100": (sum(wins) / abs(sum(losses))) if losses else None,
            "max_drawdown": max_dd, "exit_reasons": reasons,
            "cap_blocked": self.cap_blocked, "same_symbol_blocked": self.same_symbol_blocked,
            "candidates": len(self.candidates), "entries_n": len(self.entries),
            "order_api": "disabled",
            "pbv2_cap_impact": "none",
            "submit": 0, "cancel": 0, "live_order": 0,
        }


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
