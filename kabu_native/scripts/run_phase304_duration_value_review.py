#!/usr/bin/env python3
"""
Phase304: Duration value review — A (no Duration) vs B (Duration + accepted-entry dynamic p66).

Replay window matches Phase272–274: 20260518–20260603.
Output: kabu_native/results/reports/phase304_duration_value_review.json
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native/results/reports/phase304_duration_value_review.json"
CHECKPOINT = REPO / "kabu_native/results/reports/phase304_duration_value_review.checkpoint.json"

DATE_START = 20260518
DATE_END = 20260603
V2_MIN = 5
MAX_POS = 3
V1_MODE = "legacy"
V1_RATIO = 0.85
REJECT_REPLAY_MAX_EVENTS = 500_000
FIXED_DURATION_P66 = 406.0
JST = ZoneInfo("Asia/Tokyo")

BASE_HARD_EXCLUDE = frozenset(
    {
        "symbol_cooloff",
        "risk_cluster_block",
        "daily_loss_guard",
        "wrong_profile",
        "outside_allowed_trading_window",
        "low_liquidity_shadow",
        "low_liquidity_shadow_reject",
    }
)

AUX_FILTER = {
    "hard_exclude_extra": frozenset({"daytrade_suitability", "entry_price_risk_guard"}),
    "daytrade_mode": "on",
    "daytrade_percentile": 0.50,
    "price_risk_universe": True,
    "price_risk_guard": True,
}


def _bootstrap() -> Any:
    for p in (REPO / "kabu_native" / "src", REPO / "kabu_native" / "scripts", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    import run_phase270_fast_paper_integration_comparison as p270

    return p270


def _load_p71() -> Any:
    path = REPO / "kabu_native/scripts/run_phase71_split_momentum_fade_review.py"
    spec = importlib.util.spec_from_file_location("phase71_p304", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase71_p304"] = mod
    spec.loader.exec_module(mod)
    return mod


def _day_from_sid(sid: str) -> Optional[str]:
    parts = sid.replace("\\", "/").split("/")
    if parts and len(parts[0]) == 8 and parts[0].isdigit():
        return parts[0]
    return None


def _day_in_range(day: str) -> bool:
    try:
        d = int(day)
        return DATE_START <= d <= DATE_END
    except ValueError:
        return False


def _skip_session(sid: str, event_count: int) -> Optional[str]:
    low = sid.lower()
    if "phase282_discord_flow" in low:
        return "phase282_test_harness"
    if "phase284_resim" in low or "phase285_resim" in low:
        return "phase284_285_resim_harness"
    if event_count > REJECT_REPLAY_MAX_EVENTS:
        return f"event_count>{REJECT_REPLAY_MAX_EVENTS}"
    return None


def _discover_sessions(p270: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    found: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for summary_path in sorted(SMALL_PAPER.rglob("small_paper_summary.json")):
        sid = summary_path.parent.relative_to(SMALL_PAPER).as_posix()
        day = _day_from_sid(sid)
        if not day or not _day_in_range(day):
            continue
        events = p270._load_events(summary_path.parent)
        if not events:
            continue
        skip = _skip_session(sid, len(events))
        if skip:
            skipped.append({"session_id": sid, "day": day, "event_count": len(events), "reason": skip})
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = {}
        found.append(
            {
                "session_id": sid,
                "day": day,
                "stream": p270._session_stream(sid, summary),
                "event_count": len(events),
            }
        )
    return found, skipped


def _quantile(xs: list[float], q: float) -> float:
    ys = sorted(xs)
    pos = (len(ys) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    return ys[lo] + (ys[hi] - ys[lo]) * (pos - lo)


def _accepted_entry_duration_cutoffs(p270: Any, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    vals: list[float] = []
    for meta in sessions:
        for ev in p270._load_events(SMALL_PAPER / meta["session_id"]):
            if str(ev.get("event_type") or "") != "accepted":
                continue
            raw = p270._float(ev.get("max_continuation_duration"))
            if raw is not None:
                vals.append(float(raw))
    if not vals:
        return {
            "count": 0,
            "p33": 31.0,
            "p66": FIXED_DURATION_P66,
            "source": "fallback_fixed",
        }
    return {
        "count": len(vals),
        "p33": round(_quantile(vals, 1.0 / 3.0), 4),
        "p66": round(_quantile(vals, 2.0 / 3.0), 4),
        "p50": round(_quantile(vals, 0.5), 4),
        "p95": round(_quantile(vals, 0.95), 4),
        "max": round(max(vals), 4),
        "source": "accepted_entry_replay_window",
    }


class PriceRingTracker:
    def __init__(self) -> None:
        self.rings: dict[str, list[tuple[float, float]]] = {}

    def observe(self, ev: dict[str, Any], p270: Any) -> None:
        from small_paper.extended_entry_shadow import append_price_tick
        from storage.intraday_recorder import parse_kabu_time

        sym = str(ev.get("symbol") or "")
        px = p270._float(ev.get("current_price")) or 0.0
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        if not sym or px <= 0 or not ent:
            return
        ts = parse_kabu_time(ent, fallback=datetime.now(JST)).timestamp()
        append_price_tick(self.rings.setdefault(sym, []), ts=ts, px=px)

    def hbrecent(self, ev: dict[str, Any], p270: Any) -> bool:
        from small_paper.extended_entry_shadow import compute_entry_high_break_recent_field
        from storage.intraday_recorder import parse_kabu_time

        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = p270._float(ev.get("current_price")) or 0.0
        if not sym or not ent:
            return False
        ts = parse_kabu_time(ent, fallback=datetime.now(JST)).timestamp()
        return bool(
            compute_entry_high_break_recent_field(
                trade=ev,
                payload={"CurrentPrice": px},
                price_ring=self.rings.get(sym, []),
                entry_ts=ts,
            )["entry_high_break_recent"]
        )


def _board_from_event(ev: dict[str, Any]) -> Optional[float]:
    from small_paper.board_imbalance_shadow import compute_entry_order_book_imbalance_field

    payload: dict[str, Any] = {}
    for key in ("BidQty", "AskQty"):
        if ev.get(key) is not None:
            payload[key] = ev[key]
    if payload:
        return compute_entry_order_book_imbalance_field(payload=payload).get("entry_order_book_imbalance")
    logged = ev.get("entry_order_book_imbalance")
    if logged is None:
        return None
    try:
        return float(logged)
    except (TypeError, ValueError):
        return None


@dataclass
class ScenarioConfig:
    scenario_id: str
    label: str
    include_duration: bool = False
    duration_p33: float = 31.0
    duration_p66: float = FIXED_DURATION_P66
    duration_mode: str = "excluded"


def _active_tokens(
    work: dict[str, Any],
    score_points: dict[str, int],
    *,
    duration_p33: float,
    duration_p66: float,
) -> dict[str, bool]:
    from small_paper.entry_expectancy_score_shadow import _bin_tertile, _float, _feature_token

    active: dict[str, bool] = {}
    for token, pts in score_points.items():
        if pts <= 0:
            continue
        lbl = token.split(":", 1)[0]
        if lbl == "HBRecent":
            hb = work.get("entry_high_break_recent")
            if hb is None:
                active[token] = False
                continue
            tok = f"HBRecent:{'yes' if str(hb).lower() in ('true', '1', 'yes') else 'no'}"
        elif lbl == "Duration":
            v = _float(work.get("max_continuation_duration"))
            if v is None:
                active[token] = False
                continue
            level = _bin_tertile(v, duration_p33, duration_p66)
            tok = f"Duration:{level}"
        else:
            tok = _feature_token(lbl, work)
        active[token] = tok == token
    return active


def _compute_v2(
    ev: dict[str, Any],
    *,
    score_points: dict[str, int],
    cfg: ScenarioConfig,
    ring: PriceRingTracker,
    p270: Any,
) -> tuple[int, dict[str, Any]]:
    work = dict(ev)
    work["entry_high_break_recent"] = ring.hbrecent(work, p270)
    imb = _board_from_event(ev)
    if imb is not None:
        work["entry_order_book_imbalance"] = imb

    active = _active_tokens(
        work,
        score_points,
        duration_p33=cfg.duration_p33,
        duration_p66=cfg.duration_p66,
    )
    score = sum(score_points[t] for t, on in active.items() if on)
    return score, {
        "entry_score_v2": score,
        "active_score_tokens": [t for t, on in active.items() if on],
        "entry_high_break_recent": work.get("entry_high_break_recent"),
        "entry_order_book_imbalance": work.get("entry_order_book_imbalance"),
    }


def _price_guard_state() -> Any:
    from small_paper.entry_price_risk_guard import EntryPriceRiskGuardConfig, EntryPriceRiskGuardState

    return EntryPriceRiskGuardState(
        config=EntryPriceRiskGuardConfig(
            enabled=True,
            min_entry_price=50.0,
            max_tick_ratio_pct=5.0,
            shadow_only=True,
        )
    )


_DAYTRADE_CACHE: dict[tuple[str, float], Any] = {}


def _daytrade_state(p270: Any, session_id: str, percentile: float) -> Any:
    key = (session_id, round(float(percentile), 4))
    if key in _DAYTRADE_CACHE:
        return _DAYTRADE_CACHE[key]
    from small_paper.daytrade_suitability import percentile_value
    from small_paper.daytrade_suitability_gate import (
        DaytradeSuitabilityConfig,
        DaytradeSuitabilityState,
        discover_sessions_for_suitability_prior,
        prior_vol_liq_scores,
    )

    base = REPO / "kabu_native/results/small_paper"
    sources = discover_sessions_for_suitability_prior(base, before_session_key=session_id)
    scores, used = prior_vol_liq_scores(sources, repo_root=REPO)
    th = percentile_value(scores, percentile) if scores else None
    state = DaytradeSuitabilityState(
        config=DaytradeSuitabilityConfig(enabled=True),
        run_session_key=session_id,
        source_sessions=used,
        vol_liq_threshold=round(th, 6) if th is not None else None,
        prior_quality_trade_count=len(scores),
    )
    _DAYTRADE_CACHE[key] = state
    return state


@dataclass
class CompletedTrade:
    pnl_pct: float
    stop_hit: bool
    symbol: str
    day: str
    entry_score_v2: int = 0


class ScenarioSim:
    def __init__(self, cfg: ScenarioConfig, score_points: dict[str, int], p71: Any, p270: Any):
        self.cfg = cfg
        self.score_points = score_points
        self.p71 = p71
        self.p270 = p270
        self.sym_states: dict[str, Any] = {}
        self.active: dict[str, Any] = {}
        self.completed: list[CompletedTrade] = []
        self.max_concurrent_reject_count = 0
        self.reject_reason_counts: Counter[str] = Counter()
        self.duration_high_gate_hits = 0
        self._pending_time: Optional[str] = None
        self._pending: list[tuple[dict[str, Any], int, dict[str, Any]]] = []
        self._day = ""
        self._session_id = ""
        self._universe_syms: set[str] = set()
        self._daytrade_state: Any = None
        self._price_guard = _price_guard_state()
        self._ring = PriceRingTracker()

    def _hard_exclude(self) -> frozenset[str]:
        return BASE_HARD_EXCLUDE | frozenset(AUX_FILTER.get("hard_exclude_extra") or [])

    def _aux_fail(self, ev: dict[str, Any]) -> Optional[str]:
        sym = str(ev.get("symbol") or "")
        if AUX_FILTER.get("price_risk_universe") and self._universe_syms:
            if sym not in self._universe_syms:
                return "outside_price_risk_universe"
        if AUX_FILTER.get("price_risk_guard"):
            gr = self._price_guard.check(ev)
            if gr.blocked:
                return "entry_price_risk_guard"
        if self._daytrade_state is not None:
            if self._daytrade_state.check(ev).blocked:
                return "daytrade_suitability"
        return None

    def _guard_fail(self, ev: dict[str, Any], score: int, audit: dict[str, Any]) -> Optional[str]:
        if score < V2_MIN:
            return "entry_score_v2_below_threshold"
        if self.cfg.include_duration:
            tokens = set(audit.get("active_score_tokens") or [])
            if "Duration:high" in tokens:
                self.duration_high_gate_hits += 1
        return None

    def _score_row(self, ev: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        return _compute_v2(
            ev,
            score_points=self.score_points,
            cfg=self.cfg,
            ring=self._ring,
            p270=self.p270,
        )

    def _try_open(self, item: tuple[dict[str, Any], int, dict[str, Any]]) -> None:
        ev, score, audit = item
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = self.p270._float(ev.get("current_price")) or self.p270._float(ev.get("entry_price")) or 0.0
        if not sym or not ent or px <= 0:
            return
        guard_reason = self._guard_fail(ev, score, audit)
        if guard_reason:
            self.reject_reason_counts[guard_reason] += 1
            return
        aux = self._aux_fail(ev)
        if aux:
            self.reject_reason_counts[aux] += 1
            return
        if sym in self.active:
            return
        if len(self.active) >= MAX_POS:
            self.max_concurrent_reject_count += 1
            self.reject_reason_counts["max_concurrent"] += 1
            return
        ts = self.p71._parse_ts(ent)
        st = self.sym_states.setdefault(sym, self.p71.SymState())
        comps = self.p71._components(st, ts=ts, price=float(px), ev=ev)
        q = self.p270._float(ev.get("continuation_quality_score")) or 0.0
        tr = self.p71.StructuralTrade(sym, ent, float(px), float(q))
        act = self.p71.ActiveTrade(
            trade=tr,
            entry_ts=ts,
            rich_ticks=[
                {
                    "price": float(px),
                    "pnl_pct": 0.0,
                    "quality": comps["quality"],
                    "momentum": comps["momentum"],
                    "favorable": comps["favorable"],
                    "pure_price_momentum": comps["pure_price_momentum"],
                    "vwap_strength": comps["vwap_strength"],
                    "mfe_proxy": comps["mfe_proxy"],
                }
            ],
        )
        act._entry_score_v2 = score  # noqa: SLF001
        self.active[sym] = act

    def _close(self, act: Any, *, close_price: float, reason: str) -> None:
        pnl = float(self.p71._pnl_pct(act.trade.entry_price, close_price))
        self.completed.append(
            CompletedTrade(
                pnl_pct=pnl,
                stop_hit=str(reason) == "stop_hit",
                symbol=str(act.trade.symbol),
                day=self._day,
                entry_score_v2=int(getattr(act, "_entry_score_v2", 0)),
            )
        )

    def _flush(self) -> None:
        if not self._pending:
            return
        for item in sorted(self._pending, key=lambda x: int(self.p270._float(x[0].get("message_index")) or 0)):
            self._try_open(item)
        self._pending = []

    def _pool_exclude_reason(self, ev: dict[str, Any]) -> Optional[str]:
        if str(ev.get("event_type") or "") != "rejected":
            return None
        gr = str(ev.get("gate_reject_reason") or "")
        if gr in self._hard_exclude():
            return gr
        return None

    def on_row(self, ev: dict[str, Any]) -> None:
        self._ring.observe(ev, self.p270)
        et = str(ev.get("event_type") or "")
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = self.p270._float(ev.get("current_price")) or 0.0
        ev_time = str(ev.get("event_time") or "")

        if self._pending_time is None:
            self._pending_time = ev_time
        if ev_time != self._pending_time:
            self._flush()
            self._pending_time = ev_time

        if et == "candidate":
            if sym not in self.active or px <= 0 or not ent:
                return
            ts = self.p71._parse_ts(ent)
            st = self.sym_states.setdefault(sym, self.p71.SymState())
            act = self.active[sym]
            comps = self.p71._components(st, ts=ts, price=float(px), ev=ev)
            act.rich_ticks.append(
                {
                    "price": float(px),
                    "pnl_pct": self.p71._pnl_pct(act.trade.entry_price, float(px)),
                    "quality": comps["quality"],
                    "momentum": comps["momentum"],
                    "favorable": comps["favorable"],
                    "pure_price_momentum": comps["pure_price_momentum"],
                    "vwap_strength": comps["vwap_strength"],
                    "mfe_proxy": comps["mfe_proxy"],
                }
            )
            sig = self.p71.simulate_combined_split(
                act.rich_ticks,
                act.trade.entry_price,
                momentum_mode=V1_MODE,
                ratio=V1_RATIO,
                allow_session_end=False,
            )
            if sig:
                _, reason, _ = sig
                self._close(act, close_price=float(px), reason=str(reason))
                self.active.pop(sym, None)

        elif et == "accepted":
            score, audit = self._score_row(ev)
            self._pending.append((ev, int(score), audit))
        elif et == "rejected" and self._pool_exclude_reason(ev) is None:
            score, audit = self._score_row(ev)
            self._pending.append((ev, int(score), audit))

    def finalize(self, session_end: str) -> None:
        self._flush()
        for _, act in list(self.active.items()):
            last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
            self._close(act, close_price=float(last_px), reason="session_end")
        self.active.clear()

    def begin_session(self, meta: dict[str, Any]) -> None:
        self._day = meta["day"]
        self._session_id = meta["session_id"]
        self.sym_states = {}
        self.active = {}
        self._pending = []
        self._pending_time = None
        self._ring = PriceRingTracker()
        self._universe_syms = (
            self.p270._load_universe_symbols(self._day, price_risk=True)
            if AUX_FILTER.get("price_risk_universe")
            else set()
        )
        if AUX_FILTER.get("daytrade_mode") == "on":
            self._daytrade_state = _daytrade_state(
                self.p270, self._session_id, float(AUX_FILTER.get("daytrade_percentile") or 0.50)
            )
        else:
            self._daytrade_state = None


def _pf(pnls: list[float]) -> Any:
    wins = sum(p for p in pnls if p > 0)
    loss = abs(sum(p for p in pnls if p < 0))
    if loss <= 0:
        return None if wins <= 0 else "inf"
    return round(wins / loss, 4)


def _metrics(trades: list[CompletedTrade], reject_mc: int) -> dict[str, Any]:
    pnls = [t.pnl_pct for t in trades]
    n = len(trades)
    sym_counts = Counter(t.symbol for t in trades)
    top_sym, top_n = ("", 0)
    if sym_counts:
        top_sym, top_n = sym_counts.most_common(1)[0]
    concentration = round(100.0 * top_n / n, 2) if n else 0.0
    hhi = 0.0
    if n:
        hhi = round(sum((c / n) ** 2 for c in sym_counts.values()), 6)
    base: dict[str, Any] = {
        "trade_count": n,
        "max_concurrent_reject_count": reject_mc,
        "traded_symbol_count": len(sym_counts),
        "top_symbol": top_sym,
        "top_symbol_trade_count": top_n,
        "symbol_concentration_pct": concentration,
        "symbol_hhi": hhi,
    }
    if n == 0:
        base.update(
            {
                "profit_factor": None,
                "total_pnl_pct": 0.0,
                "avg_pnl_pct": None,
                "win_rate": None,
                "stop_rate": None,
            }
        )
    else:
        wins = sum(1 for p in pnls if p > 0)
        stops = sum(1 for t in trades if t.stop_hit)
        base.update(
            {
                "profit_factor": _pf(pnls),
                "total_pnl_pct": round(sum(pnls), 4),
                "avg_pnl_pct": round(sum(pnls) / n, 6),
                "win_rate": round(wins / n, 4),
                "stop_rate": round(stops / n, 4),
            }
        )
    if sym_counts:
        rows = []
        for sym, cnt in sym_counts.most_common(15):
            sp = [t.pnl_pct for t in trades if t.symbol == sym]
            rows.append(
                {
                    "symbol": sym,
                    "trade_count": cnt,
                    "share_pct": round(100.0 * cnt / n, 2),
                    "total_pnl_pct": round(sum(sp), 4),
                    "profit_factor": _pf(sp),
                }
            )
        base["top_symbols"] = rows
    return base


def _zero_trade_rates(calendar_days: list[str], daily: dict[str, dict[str, Any]], scenario_ids: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for sid in scenario_ids:
        z = sum(1 for d in calendar_days if daily.get(d, {}).get(sid, {}).get("trade_count", 0) == 0)
        out[sid] = {
            "zero_trade_days": z,
            "calendar_days": len(calendar_days),
            "zero_trade_day_rate": round(z / len(calendar_days), 4) if calendar_days else None,
        }
    return out


def _json_safe(obj: Any) -> Any:
    if isinstance(obj, frozenset):
        return sorted(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


def _pf_ok(pf: Any, *, min_pf: float = 1.0) -> bool:
    if pf is None:
        return False
    if pf == "inf":
        return True
    try:
        return float(pf) >= min_pf
    except (TypeError, ValueError):
        return False


def _gate_score_scan(
    p270: Any,
    sessions: list[dict[str, Any]],
    *,
    duration_p33: float,
    duration_p66: float,
    include_duration: bool,
) -> dict[str, Any]:
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2

    score_points = (
        dict(SCORE_POINTS_V2)
        if include_duration
        else {k: v for k, v in SCORE_POINTS_V2.items() if k != "Duration:high"}
    )
    cfg = ScenarioConfig(
        "scan",
        "gate_score_scan",
        include_duration=include_duration,
        duration_p33=duration_p33,
        duration_p66=duration_p66,
    )
    ring = PriceRingTracker()
    pool = 0
    pass5 = 0
    dur_high = 0
    for meta in sessions:
        ring = PriceRingTracker()
        for ev in p270._load_events(SMALL_PAPER / meta["session_id"]):
            ring.observe(ev, p270)
            et = str(ev.get("event_type") or "")
            if et == "accepted":
                pass
            elif et == "rejected":
                gr = str(ev.get("gate_reject_reason") or "")
                if gr in (BASE_HARD_EXCLUDE | frozenset(AUX_FILTER.get("hard_exclude_extra") or [])):
                    continue
            else:
                continue
            pool += 1
            score, audit = _compute_v2(ev, score_points=score_points, cfg=cfg, ring=ring, p270=p270)
            if score >= V2_MIN:
                pass5 += 1
            if "Duration:high" in (audit.get("active_score_tokens") or []):
                dur_high += 1
    return {
        "decision_pool_gate_candidates": pool,
        "score_ge5_count": pass5,
        "duration_high_token_count": dur_high,
        "duration_p66": duration_p66,
    }


def _verdict(
    overall: dict[str, dict[str, Any]],
    dyn_cutoffs: dict[str, Any],
    gate_scan: dict[str, Any],
) -> dict[str, Any]:
    a = overall.get("A", {})
    b = overall.get("B", {})
    b_dyn = gate_scan.get("B_dynamic", {})
    b_fix = gate_scan.get("B_fixed406", {})

    def _better(new: dict[str, Any], old: dict[str, Any]) -> bool:
        pf_new = _pf_ok(new.get("profit_factor"))
        pf_old = _pf_ok(old.get("profit_factor"))
        pnl_new = float(new.get("total_pnl_pct") or 0)
        pnl_old = float(old.get("total_pnl_pct") or 0)
        if pf_new and not pf_old:
            return True
        if pf_new and pf_old and float(new.get("profit_factor") or 0) > float(old.get("profit_factor") or 0):
            return pnl_new >= pnl_old * 0.9
        return pnl_new > pnl_old and (new.get("profit_factor") is not None)

    duration_required = _better(b, a)
    dyn_pass = int(b_dyn.get("score_ge5_count") or 0)
    fix_pass = int(b_fix.get("score_ge5_count") or 0)
    dyn_dur = int(b_dyn.get("duration_high_token_count") or 0)
    fix_dur = int(b_fix.get("duration_high_token_count") or 0)
    dynamic_useful = (
        dyn_pass > fix_pass
        or (dyn_dur > 0 and fix_dur == 0)
        or (dyn_pass >= fix_pass and dyn_dur > fix_dur)
    )

    reasons = []
    if duration_required:
        reasons.append(
            f"B (Duration+dynamic p66={dyn_cutoffs.get('p66')}) beats A on PF/PnL "
            f"(PF {b.get('profit_factor')} vs {a.get('profit_factor')}, "
            f"PnL {b.get('total_pnl_pct')} vs {a.get('total_pnl_pct')})."
        )
    else:
        reasons.append(
            f"A (no Duration) matches or beats B "
            f"(PF {a.get('profit_factor')} vs {b.get('profit_factor')}, "
            f"PnL {a.get('total_pnl_pct')} vs {b.get('total_pnl_pct')})."
        )
    if dynamic_useful:
        reasons.append(
            f"Dynamic p66={dyn_cutoffs.get('p66')} yields more gate-pass candidates than fixed 406 "
            f"(score>=5: {dyn_pass} vs {fix_pass}; Duration:high: {dyn_dur} vs {fix_dur})."
        )
    else:
        reasons.append(
            f"Dynamic percentile does not improve gate-pass vs fixed 406 "
            f"(score>=5: {dyn_pass} vs {fix_pass})."
        )

    return {
        "Duration_required": "yes" if duration_required else "no",
        "Duration_dynamic_percentile_useful": "yes" if dynamic_useful else "no",
        "rationale": reasons,
        "comparison_summary": {
            "A_no_duration": {k: a.get(k) for k in ("trade_count", "profit_factor", "total_pnl_pct", "win_rate", "zero_trade_day_rate", "symbol_concentration_pct")},
            "B_duration_dynamic": {k: b.get(k) for k in ("trade_count", "profit_factor", "total_pnl_pct", "win_rate", "zero_trade_day_rate", "symbol_concentration_pct", "duration_high_pass_count")},
            "B_fixed406_gate_scan": b_fix,
            "B_dynamic_gate_scan": b_dyn,
        },
    }


def main() -> int:
    p270 = _bootstrap()
    p71 = _load_p71()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2

    score_points_full = dict(SCORE_POINTS_V2)
    score_points_no_dur = {k: v for k, v in SCORE_POINTS_V2.items() if k != "Duration:high"}

    sessions, skipped = _discover_sessions(p270)
    dyn_cutoffs = _accepted_entry_duration_cutoffs(p270, sessions)

    scenario_defs = {
        "A": ScenarioConfig(
            "A",
            "HBRecent+Board, Durationなし",
            include_duration=False,
            duration_mode="excluded",
        ),
        "B": ScenarioConfig(
            "B",
            "HBRecent+Board+Duration (accepted-entry dynamic percentile)",
            include_duration=True,
            duration_p33=float(dyn_cutoffs["p33"]),
            duration_p66=float(dyn_cutoffs["p66"]),
            duration_mode="accepted_entry_dynamic",
        ),
    }

    sims = {
        "A": ScenarioSim(scenario_defs["A"], score_points_no_dur, p71, p270),
        "B": ScenarioSim(scenario_defs["B"], score_points_full, p71, p270),
    }

    print(
        f"sessions={len(sessions)} dyn_p66={dyn_cutoffs.get('p66')} "
        f"dyn_p33={dyn_cutoffs.get('p33')}",
        flush=True,
    )

    overall: dict[str, dict[str, Any]] = {}
    daily: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    calendar_days = sorted({s["day"] for s in sessions})

    if CHECKPOINT.is_file():
        try:
            ck = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
            overall = ck.get("overall") or {}
            daily = defaultdict(dict, ck.get("daily_by_scenario") or {})
            calendar_days = ck.get("calendar_days") or calendar_days
            dyn_cutoffs = ck.get("accepted_entry_dynamic_cutoffs") or dyn_cutoffs
            print("loaded checkpoint — skipping replay", flush=True)
        except (OSError, json.JSONDecodeError):
            overall = {}

    if not overall:
        for i, meta in enumerate(sessions, 1):
            sdir = SMALL_PAPER / meta["session_id"]
            events = p270._load_events(sdir)
            if not events:
                continue
            session_end = p71._session_end(events)
            ordered = sorted(
                events,
                key=lambda e: (
                    p270._parse_ts(str(e.get("event_time") or "")),
                    int(p270._float(e.get("message_index")) or 0),
                ),
            )
            for sim in sims.values():
                sim.begin_session(meta)
            for ev in ordered:
                for sim in sims.values():
                    sim.on_row(ev)
            for sim in sims.values():
                sim.finalize(session_end)
            if i % 5 == 0 or i == len(sessions):
                print(f"  [{i}/{len(sessions)}]", flush=True)

        for sid, sim in sims.items():
            overall[sid] = _metrics(sim.completed, sim.max_concurrent_reject_count)
            overall[sid]["reject_reason_counts"] = dict(sim.reject_reason_counts.most_common(15))
            overall[sid]["duration_high_pass_count"] = sim.duration_high_gate_hits
            for day in calendar_days:
                td = [t for t in sim.completed if t.day == day]
                daily[day][sid] = _metrics(td, sim.max_concurrent_reject_count)

        CHECKPOINT.write_text(
            json.dumps(
                _json_safe(
                    {
                        "overall": overall,
                        "daily_by_scenario": {d: dict(v) for d, v in daily.items()},
                        "calendar_days": calendar_days,
                        "accepted_entry_dynamic_cutoffs": dyn_cutoffs,
                    }
                ),
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"checkpoint {CHECKPOINT}", flush=True)

    zero_rates = _zero_trade_rates(calendar_days, daily, list(sims.keys()))
    for sid in sims:
        overall[sid]["zero_trade_day_rate"] = zero_rates[sid]["zero_trade_day_rate"]
        overall[sid]["zero_trade_days"] = zero_rates[sid]["zero_trade_days"]

    print("gate score scan B_dynamic vs B_fixed406...", flush=True)
    gate_scan = {
        "B_dynamic": _gate_score_scan(
            p270,
            sessions,
            duration_p33=float(dyn_cutoffs["p33"]),
            duration_p66=float(dyn_cutoffs["p66"]),
            include_duration=True,
        ),
        "B_fixed406": _gate_score_scan(
            p270,
            sessions,
            duration_p33=31.0,
            duration_p66=FIXED_DURATION_P66,
            include_duration=True,
        ),
    }
    verdict = _verdict(overall, dyn_cutoffs, gate_scan)

    report = {
        "phase": 304,
        "title": "duration_value_review",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraint": "review only; no production/cutoff/entry/exit changes",
        "replay_window": {
            "start": DATE_START,
            "end": DATE_END,
            "aligned_with": ["Phase272", "Phase273", "Phase274"],
            "engine": "Phase71 virtual exit replay (Phase270 family)",
            "entry_gate": f"entry_score_v2>={V2_MIN}",
            "max_concurrent": MAX_POS,
            "aux_filters": _json_safe(AUX_FILTER),
        },
        "scenarios": {
            sid: {
                "id": sid,
                "label": scenario_defs[sid].label,
                "hbrecent_pregate": True,
                "board_pregate": True,
                "include_duration": scenario_defs[sid].include_duration,
                "duration_mode": scenario_defs[sid].duration_mode,
                "duration_p33": scenario_defs[sid].duration_p33,
                "duration_p66": scenario_defs[sid].duration_p66,
                "score_points": list(
                    (score_points_no_dur if sid == "A" else score_points_full).keys()
                ),
            }
            for sid in scenario_defs
        },
        "accepted_entry_dynamic_cutoffs": dyn_cutoffs,
        "sessions": {"count": len(sessions), "skipped": skipped},
        "comparison": {
            "A": overall["A"],
            "B": overall["B"],
        },
        "gate_score_scan_dynamic_vs_fixed406": gate_scan,
        "daily_by_scenario": {d: dict(v) for d, v in daily.items()},
        "zero_trade_rates": zero_rates,
        "verdict": verdict,
        "phase272_crosscheck": {
            "phase272_B_v2_ge5_trade_count": 928,
            "phase272_B_v2_ge5_profit_factor": 1.1093,
            "note": "Phase272 used logged score fields; Phase304 recomputes HBRecent+Board pre-gate scores.",
        },
    }

    OUT.write_text(json.dumps(_json_safe(report), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(
        f"A tc={overall['A']['trade_count']} PF={overall['A']['profit_factor']} | "
        f"B tc={overall['B']['trade_count']} PF={overall['B']['profit_factor']} | "
        f"verdict required={verdict['Duration_required']} dynamic={verdict['Duration_dynamic_percentile_useful']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
