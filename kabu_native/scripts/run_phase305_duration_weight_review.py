#!/usr/bin/env python3
"""
Phase305: Duration weight review — A(0pt) vs B(+1pt) vs C(+2pt) dynamic percentile.

Replay: 20260518–20260603 (Phase272–274 aligned).
Output: kabu_native/results/reports/phase305_duration_weight_review.json
"""

from __future__ import annotations

import importlib.util
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native/results/reports/phase305_duration_weight_review.json"
CHECKPOINT = REPO / "kabu_native/results/reports/phase305_duration_weight_review.checkpoint.json"
P304_CHECKPOINT = REPO / "kabu_native/results/reports/phase304_duration_value_review.checkpoint.json"

DATE_START = 20260518
DATE_END = 20260603
V2_MIN = 5
MAX_POS = 3
V1_MODE = "legacy"
V1_RATIO = 0.85
REJECT_REPLAY_MAX_EVENTS = 500_000
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
    spec = importlib.util.spec_from_file_location("phase71_p305", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase71_p305"] = mod
    spec.loader.exec_module(mod)
    return mod


def _day_from_sid(sid: str) -> Optional[str]:
    parts = sid.replace("\\", "/").split("/")
    if parts and len(parts[0]) == 8 and parts[0].isdigit():
        return parts[0]
    return None


def _day_in_range(day: str) -> bool:
    try:
        return DATE_START <= int(day) <= DATE_END
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
        return {"count": 0, "p33": 31.0, "p66": 109.0, "source": "fallback"}
    return {
        "count": len(vals),
        "p33": round(_quantile(vals, 1.0 / 3.0), 4),
        "p66": round(_quantile(vals, 2.0 / 3.0), 4),
        "p50": round(_quantile(vals, 0.5), 4),
        "p95": round(_quantile(vals, 0.95), 4),
        "max": round(max(vals), 4),
        "source": "accepted_entry_replay_window",
    }


def _score_points(duration_weight: int) -> dict[str, int]:
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2

    pts = dict(SCORE_POINTS_V2)
    if duration_weight <= 0:
        pts.pop("Duration:high", None)
    else:
        pts["Duration:high"] = duration_weight
    return pts


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
    duration_weight: int
    duration_p33: float
    duration_p66: float


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


def _score_from_ev(
    ev: dict[str, Any],
    *,
    score_points: dict[str, int],
    duration_p33: float,
    duration_p66: float,
    ring: PriceRingTracker,
    p270: Any,
) -> tuple[int, list[str]]:
    work = dict(ev)
    work["entry_high_break_recent"] = ring.hbrecent(work, p270)
    imb = _board_from_event(ev)
    if imb is not None:
        work["entry_order_book_imbalance"] = imb
    active = _active_tokens(work, score_points, duration_p33=duration_p33, duration_p66=duration_p66)
    score = sum(score_points[t] for t, on in active.items() if on)
    return score, [t for t, on in active.items() if on]


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
        self.score_ge5_gate_pass = 0
        self._pending_time: Optional[str] = None
        self._pending: list[tuple[dict[str, Any], int]] = []
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

    def _score_row(self, ev: dict[str, Any]) -> int:
        score, _ = _score_from_ev(
            ev,
            score_points=self.score_points,
            duration_p33=self.cfg.duration_p33,
            duration_p66=self.cfg.duration_p66,
            ring=self._ring,
            p270=self.p270,
        )
        return score

    def _try_open(self, item: tuple[dict[str, Any], int]) -> None:
        ev, score = item
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = self.p270._float(ev.get("current_price")) or self.p270._float(ev.get("entry_price")) or 0.0
        if not sym or not ent or px <= 0:
            return
        if score < V2_MIN:
            self.reject_reason_counts["entry_score_v2_below_threshold"] += 1
            return
        self.score_ge5_gate_pass += 1
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

    def _pool_ok(self, ev: dict[str, Any]) -> bool:
        et = str(ev.get("event_type") or "")
        if et == "accepted":
            return True
        if et == "rejected":
            return str(ev.get("gate_reject_reason") or "") not in self._hard_exclude()
        return False

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
        elif self._pool_ok(ev):
            self._pending.append((ev, self._score_row(ev)))

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


def _metrics(trades: list[CompletedTrade], reject_mc: int, score_ge5: int) -> dict[str, Any]:
    pnls = [t.pnl_pct for t in trades]
    n = len(trades)
    sym_counts = Counter(t.symbol for t in trades)
    top_sym, top_n = ("", 0)
    if sym_counts:
        top_sym, top_n = sym_counts.most_common(1)[0]
    concentration = round(100.0 * top_n / n, 2) if n else 0.0
    base: dict[str, Any] = {
        "trade_count": n,
        "score_ge5_count": score_ge5,
        "max_concurrent_reject_count": reject_mc,
        "traded_symbol_count": len(sym_counts),
        "top_symbol": top_sym,
        "symbol_concentration_pct": concentration,
    }
    if n == 0:
        base.update(
            {
                "profit_factor": None,
                "total_pnl_pct": 0.0,
                "avg_pnl_pct": None,
                "win_rate": None,
            }
        )
    else:
        wins = sum(1 for p in pnls if p > 0)
        base.update(
            {
                "profit_factor": _pf(pnls),
                "total_pnl_pct": round(sum(pnls), 4),
                "avg_pnl_pct": round(sum(pnls) / n, 6),
                "win_rate": round(wins / n, 4),
            }
        )
    return base


def _zero_trade_rates(calendar_days: list[str], daily: dict[str, dict[str, Any]], ids: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for sid in ids:
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


def _pool_event(ev: dict[str, Any]) -> bool:
    et = str(ev.get("event_type") or "")
    if et == "accepted":
        return True
    if et == "rejected":
        gr = str(ev.get("gate_reject_reason") or "")
        extra = AUX_FILTER.get("hard_exclude_extra") or frozenset()
        return gr not in (BASE_HARD_EXCLUDE | extra)
    return False


def _duration_attribution_scan(
    p270: Any,
    sessions: list[dict[str, Any]],
    *,
    duration_weight: int,
    duration_p33: float,
    duration_p66: float,
) -> dict[str, Any]:
    pts_with = _score_points(duration_weight)
    pts_wo = _score_points(0)
    pool = 0
    score_ge5 = 0
    dur_high_active = 0
    dur_in_score5 = 0
    marginal_score5 = 0

    for meta in sessions:
        ring = PriceRingTracker()
        for ev in p270._load_events(SMALL_PAPER / meta["session_id"]):
            ring.observe(ev, p270)
            if not _pool_event(ev):
                continue
            pool += 1
            sw, tok_w = _score_from_ev(
                ev,
                score_points=pts_with,
                duration_p33=duration_p33,
                duration_p66=duration_p66,
                ring=ring,
                p270=p270,
            )
            swo, _ = _score_from_ev(
                ev,
                score_points=pts_wo,
                duration_p33=duration_p33,
                duration_p66=duration_p66,
                ring=ring,
                p270=p270,
            )
            has_dur = "Duration:high" in tok_w
            if has_dur:
                dur_high_active += 1
            if sw >= V2_MIN:
                score_ge5 += 1
                if has_dur:
                    dur_in_score5 += 1
                if swo < V2_MIN and has_dur:
                    marginal_score5 += 1

    return {
        "duration_weight": duration_weight,
        "decision_pool_candidates": pool,
        "score_ge5_count": score_ge5,
        "duration_high_token_active_count": dur_high_active,
        "duration_contributed_to_score5_count": dur_in_score5,
        "would_miss_score5_without_duration_count": marginal_score5,
        "duration_share_of_score5": round(dur_in_score5 / score_ge5, 4) if score_ge5 else 0.0,
        "marginal_share_of_score5": round(marginal_score5 / score_ge5, 4) if score_ge5 else 0.0,
        "miss_score5_without_duration_rate": round(marginal_score5 / pool, 4) if pool else 0.0,
    }


def _pf_num(pf: Any) -> float:
    if pf is None:
        return -1.0
    if pf == "inf":
        return 99.0
    try:
        return float(pf)
    except (TypeError, ValueError):
        return -1.0


def _pick_best_weight(overall: dict[str, dict[str, Any]]) -> tuple[int, bool, str]:
    ranked: list[tuple[float, float, int, str]] = []
    for sid, weight in (("A", 0), ("B", 1), ("C", 2)):
        m = overall.get(sid, {})
        pf = _pf_num(m.get("profit_factor"))
        pnl = float(m.get("total_pnl_pct") or 0)
        tc = int(m.get("trade_count") or 0)
        # Prefer PF with positive PnL; penalize zero-trade or negative PnL
        rank_score = pf if pnl > 0 and tc >= 5 else pf - 2.0
        ranked.append((rank_score, pnl, weight, sid))
    ranked.sort(reverse=True)
    best_weight = ranked[0][2]
    duration_required = best_weight > 0
    rationale = (
        f"Best weight={best_weight} by PF+PnL rank among scenarios with trade_count>=5. "
        f"Ordered: " + ", ".join(f"{s}(w={w},PF={overall.get(s,{}).get('profit_factor')},PnL={overall.get(s,{}).get('total_pnl_pct')})" for _, _, w, s in ranked)
    )
    return best_weight, duration_required, rationale


def main() -> int:
    p270 = _bootstrap()
    p71 = _load_p71()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    sessions, skipped = _discover_sessions(p270)
    dyn = _accepted_entry_duration_cutoffs(p270, sessions)
    p33 = float(dyn["p33"])
    p66 = float(dyn["p66"])

    scenario_defs = {
        "A": ScenarioConfig("A", "Durationなし (0点)", 0, p33, p66),
        "B": ScenarioConfig("B", "Duration動的 (+1点)", 1, p33, p66),
        "C": ScenarioConfig("C", "Duration動的 (+2点)", 2, p33, p66),
    }

    overall: dict[str, dict[str, Any]] = {}
    daily: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    calendar_days = sorted({s["day"] for s in sessions})
    sim_results: dict[str, dict[str, Any]] = {}

    # Reuse Phase304 checkpoint for A (identical scenario)
    if P304_CHECKPOINT.is_file():
        try:
            ck304 = json.loads(P304_CHECKPOINT.read_text(encoding="utf-8"))
            if "A" in (ck304.get("overall") or {}):
                overall["A"] = dict(ck304["overall"]["A"])
                for day, row in (ck304.get("daily_by_scenario") or {}).items():
                    if "A" in row:
                        daily[day]["A"] = dict(row["A"])
                overall["A"]["score_ge5_count"] = overall["A"].get("score_ge5_count") or 0
                print("loaded A from phase304 checkpoint", flush=True)
        except (OSError, json.JSONDecodeError):
            pass

    if CHECKPOINT.is_file():
        try:
            ck = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
            if ck.get("overall"):
                overall.update(ck["overall"])
                daily = defaultdict(dict, ck.get("daily_by_scenario") or {})
                sim_results = ck.get("sim_results") or {}
                print("loaded phase305 checkpoint", flush=True)
        except (OSError, json.JSONDecodeError):
            pass

    need_replay = [sid for sid in ("A", "B", "C") if sid not in overall]

    if need_replay:
        sims = {
            sid: ScenarioSim(scenario_defs[sid], _score_points(scenario_defs[sid].duration_weight), p71, p270)
            for sid in need_replay
        }
        print(f"replay scenarios={need_replay} dyn_p66={p66}", flush=True)
        for i, meta in enumerate(sessions, 1):
            events = p270._load_events(SMALL_PAPER / meta["session_id"])
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
            overall[sid] = _metrics(sim.completed, sim.max_concurrent_reject_count, sim.score_ge5_gate_pass)
            overall[sid]["reject_reason_counts"] = dict(sim.reject_reason_counts.most_common(12))
            sim_results[sid] = {"duration_weight": scenario_defs[sid].duration_weight}
            for day in calendar_days:
                td = [t for t in sim.completed if t.day == day]
                daily[day][sid] = _metrics(td, sim.max_concurrent_reject_count, 0)

        CHECKPOINT.write_text(
            json.dumps(_json_safe({"overall": overall, "daily_by_scenario": dict(daily), "sim_results": sim_results}), indent=2),
            encoding="utf-8",
        )

    zero_rates = _zero_trade_rates(calendar_days, daily, ["A", "B", "C"])
    for sid in ("A", "B", "C"):
        overall[sid]["zero_trade_day_rate"] = zero_rates[sid]["zero_trade_day_rate"]
        overall[sid]["zero_trade_days"] = zero_rates[sid]["zero_trade_days"]

    print("duration attribution scan...", flush=True)
    attr_a = _duration_attribution_scan(p270, sessions, duration_weight=0, duration_p33=p33, duration_p66=p66)
    attr_b = _duration_attribution_scan(p270, sessions, duration_weight=1, duration_p33=p33, duration_p66=p66)
    attr_c = _duration_attribution_scan(p270, sessions, duration_weight=2, duration_p33=p33, duration_p66=p66)

    best_weight, duration_required, pick_reason = _pick_best_weight(overall)

    report = {
        "phase": 305,
        "title": "duration_weight_review",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraint": "review only; HBRecent+Board pre-gate; no production/entry/exit changes",
        "replay_window": {"start": DATE_START, "end": DATE_END, "aligned_with": ["Phase272", "Phase273", "Phase274"]},
        "fixed_conditions": {
            "hbrecent_pregate": True,
            "board_pregate": True,
            "daytrade": True,
            "price_risk": True,
            "entry_score_v2_min": V2_MIN,
            "max_concurrent": MAX_POS,
            "duration_percentile": dyn,
        },
        "scenarios": {
            sid: {
                "id": sid,
                "label": scenario_defs[sid].label,
                "duration_weight": scenario_defs[sid].duration_weight,
            }
            for sid in ("A", "B", "C")
        },
        "sessions": {"count": len(sessions), "skipped": skipped},
        "comparison": {sid: overall[sid] for sid in ("A", "B", "C")},
        "duration_attribution": {
            "A_weight0_reference": attr_a,
            "B_weight1": attr_b,
            "C_weight2": attr_c,
        },
        "verdict": {
            "best_duration_weight": best_weight,
            "Duration_required": duration_required,
            "selection_rationale": pick_reason,
        },
        "phase304_crosscheck": {
            "phase304_A_trade_count": 23,
            "phase304_A_PF": 3.2675,
            "phase304_C_trade_count": 294,
            "phase304_C_PF": 1.057,
        },
    }

    OUT.write_text(json.dumps(_json_safe(report), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(
        f"A w=0 tc={overall['A']['trade_count']} PF={overall['A']['profit_factor']} | "
        f"B w=1 tc={overall['B']['trade_count']} PF={overall['B']['profit_factor']} | "
        f"C w=2 tc={overall['C']['trade_count']} PF={overall['C']['profit_factor']} | "
        f"best={best_weight} required={duration_required}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
