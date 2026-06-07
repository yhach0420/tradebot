#!/usr/bin/env python3
"""
Phase307: Price:high removal review — A (current) vs B (Price excluded).

Replay: 20260518–20260603. Output: phase307_price_token_removal_review.json
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
OUT = REPO / "kabu_native/results/reports/phase307_price_token_removal_review.json"
CHECKPOINT = REPO / "kabu_native/results/reports/phase307_price_token_removal_review.checkpoint.json"
P306_CHECKPOINT = REPO / "kabu_native/results/reports/phase306_token_fire_rate_profit_attribution.checkpoint.json"
P304_REPORT = REPO / "kabu_native/results/reports/phase304_duration_value_review.json"

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
    spec = importlib.util.spec_from_file_location("phase71_p307", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase71_p307"] = mod
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
        found.append({"session_id": sid, "day": day, "stream": p270._session_stream(sid, summary)})
    return found, skipped


def _quantile(xs: list[float], q: float) -> float:
    ys = sorted(xs)
    pos = (len(ys) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    return ys[lo] + (ys[hi] - ys[lo]) * (pos - lo)


def _accepted_entry_duration_cutoffs(p270: Any, sessions: list[dict[str, Any]]) -> dict[str, float]:
    vals: list[float] = []
    for meta in sessions:
        for ev in p270._load_events(SMALL_PAPER / meta["session_id"]):
            if str(ev.get("event_type") or "") != "accepted":
                continue
            raw = p270._float(ev.get("max_continuation_duration"))
            if raw is not None:
                vals.append(float(raw))
    if not vals:
        return {"p33": 31.0, "p66": 109.0}
    return {"p33": round(_quantile(vals, 1.0 / 3.0), 4), "p66": round(_quantile(vals, 2.0 / 3.0), 4)}


def _score_points(*, exclude_price: bool) -> dict[str, int]:
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2

    pts = dict(SCORE_POINTS_V2)
    if exclude_price:
        pts.pop("Price:high", None)
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
    exclude_price: bool
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


def _compute_score(
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
            enabled=True, min_entry_price=50.0, max_tick_ratio_pct=5.0, shadow_only=True
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
        self._universe_syms: set[str] = set()
        self._daytrade_state: Any = None
        self._price_guard = _price_guard_state()
        self._ring = PriceRingTracker()

    def _hard_exclude(self) -> frozenset[str]:
        return BASE_HARD_EXCLUDE | frozenset(AUX_FILTER.get("hard_exclude_extra") or [])

    def _aux_fail(self, ev: dict[str, Any]) -> Optional[str]:
        sym = str(ev.get("symbol") or "")
        if AUX_FILTER.get("price_risk_universe") and self._universe_syms and sym not in self._universe_syms:
            return "outside_price_risk_universe"
        if AUX_FILTER.get("price_risk_guard") and self._price_guard.check(ev).blocked:
            return "entry_price_risk_guard"
        if self._daytrade_state is not None and self._daytrade_state.check(ev).blocked:
            return "daytrade_suitability"
        return None

    def _score_row(self, ev: dict[str, Any]) -> int:
        score, _ = _compute_score(
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
                self.p270, meta["session_id"], float(AUX_FILTER.get("daytrade_percentile") or 0.50)
            )
        else:
            self._daytrade_state = None


class MarginalPriceDropoutSim(ScenarioSim):
    """Replay only entries that reach score>=5 with Price:high but not without it."""

    def __init__(self, cfg: ScenarioConfig, p71: Any, p270: Any):
        super().__init__(cfg, _score_points(exclude_price=False), p71, p270)
        self.pts_a = _score_points(exclude_price=False)
        self.pts_b = _score_points(exclude_price=True)

    def _marginal_score(self, ev: dict[str, Any]) -> int:
        sa, tok_a = _compute_score(
            ev,
            score_points=self.pts_a,
            duration_p33=self.cfg.duration_p33,
            duration_p66=self.cfg.duration_p66,
            ring=self._ring,
            p270=self.p270,
        )
        sb, _ = _compute_score(
            ev,
            score_points=self.pts_b,
            duration_p33=self.cfg.duration_p33,
            duration_p66=self.cfg.duration_p66,
            ring=self._ring,
            p270=self.p270,
        )
        if sa >= V2_MIN and sb < V2_MIN and "Price:high" in tok_a:
            return sa
        return -1

    def _score_row(self, ev: dict[str, Any]) -> int:
        return self._marginal_score(ev)


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
        base.update({"profit_factor": None, "total_pnl_pct": 0.0, "avg_pnl_pct": None, "win_rate": None})
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


def _price_dependency_scan(
    p270: Any,
    sessions: list[dict[str, Any]],
    *,
    duration_p33: float,
    duration_p66: float,
) -> dict[str, Any]:
    pts_a = _score_points(exclude_price=False)
    pts_b = _score_points(exclude_price=True)
    pool = 0
    score5_a = 0
    score5_b = 0
    price_dep_score5 = 0
    marginal_price_score5 = 0
    drop_from_a = 0

    for meta in sessions:
        ring = PriceRingTracker()
        for ev in p270._load_events(SMALL_PAPER / meta["session_id"]):
            ring.observe(ev, p270)
            if not _pool_event(ev):
                continue
            pool += 1
            sa, tok_a = _compute_score(
                ev, score_points=pts_a, duration_p33=duration_p33, duration_p66=duration_p66, ring=ring, p270=p270
            )
            sb, _ = _compute_score(
                ev, score_points=pts_b, duration_p33=duration_p33, duration_p66=duration_p66, ring=ring, p270=p270
            )
            has_price = "Price:high" in tok_a
            if sa >= V2_MIN:
                score5_a += 1
            if sb >= V2_MIN:
                score5_b += 1
            if sa >= V2_MIN and has_price:
                price_dep_score5 += 1
            if sa >= V2_MIN and sb < V2_MIN and has_price:
                marginal_price_score5 += 1
            if sa >= V2_MIN and sb < V2_MIN:
                drop_from_a += 1

    return {
        "decision_pool_candidates": pool,
        "score_ge5_count_A_with_price": score5_a,
        "score_ge5_count_B_without_price_token": score5_b,
        "price_high_active_in_score5_A": price_dep_score5,
        "marginal_score5_price_dependent": marginal_price_score5,
        "dropout_count_A_ge5_B_lt5": drop_from_a,
        "marginal_share_of_A_score5": round(marginal_price_score5 / score5_a, 4) if score5_a else 0.0,
    }


def _pf_num(pf: Any) -> float:
    if pf is None:
        return 0.0
    if pf == "inf":
        return 99.0
    try:
        return float(pf)
    except (TypeError, ValueError):
        return 0.0


def _verdict(overall: dict[str, Any], dep: dict[str, Any], dropout: dict[str, Any]) -> dict[str, Any]:
    a = overall.get("A", {})
    b = overall.get("B", {})
    a_pf = _pf_num(a.get("profit_factor"))
    b_pf = _pf_num(b.get("profit_factor"))
    a_pnl = float(a.get("total_pnl_pct") or 0)
    b_pnl = float(b.get("total_pnl_pct") or 0)
    marginal = int(dep.get("marginal_score5_price_dependent") or 0)
    drop_tc = int(dropout.get("trade_count") or 0)
    drop_pnl = float(dropout.get("total_pnl_pct") or 0)
    drop_pf = _pf_num(dropout.get("profit_factor"))
    b_clearly_better = b_pf > a_pf and b_pnl > a_pnl
    dropout_harmful = drop_tc >= 10 and drop_pnl < 0 and drop_pf < 1.0
    price_harmful = b_clearly_better or dropout_harmful
    price_required = marginal > 0 and not b_clearly_better
    return {
        "price_token_required": price_required,
        "price_token_harmful": price_harmful,
        "rationale": [
            f"A (with Price) PF={a.get('profit_factor')} PnL={a_pnl} tc={a.get('trade_count')}",
            f"B (no Price token) PF={b.get('profit_factor')} PnL={b_pnl} tc={b.get('trade_count')}",
            f"marginal_score5_price_dependent={marginal} dropout_trades={drop_tc}",
            f"dropout cohort PF={dropout.get('profit_factor')} PnL={drop_pnl}",
            "price_token_harmful=true when B clearly beats A, or marginal dropout cohort loses money (PF<1).",
            "price_token_required=true when marginal Price-dependent score5 exists and B does not clearly beat A.",
        ],
    }


def _metrics_from_p306_trades(trades: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [
        CompletedTrade(
            pnl_pct=float(t["pnl_pct"]),
            stop_hit=bool(t.get("stop_hit", False)),
            symbol=str(t["symbol"]),
            day=str(t["day"]),
            entry_score_v2=int(t.get("entry_score_v2") or 0),
        )
        for t in trades
    ]
    return _metrics(rows, 0, 0)


def main() -> int:
    p270 = _bootstrap()
    p71 = _load_p71()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    sessions, skipped = _discover_sessions(p270)
    dur = _accepted_entry_duration_cutoffs(p270, sessions)
    p33 = float(dur["p33"])
    p66 = float(dur["p66"])
    calendar_days = sorted({s["day"] for s in sessions})

    scenario_defs = {
        "A": ScenarioConfig("A", "現行 (Price:high 含む)", False, p33, p66),
        "B": ScenarioConfig("B", "Price:high 除外", True, p33, p66),
    }

    overall: dict[str, dict[str, Any]] = {}
    daily: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    dropout_metrics: dict[str, Any] = {}

    if CHECKPOINT.is_file():
        try:
            ck = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
            overall.update(ck.get("overall") or {})
            daily = defaultdict(dict, ck.get("daily_by_scenario") or {})
            dropout_metrics = ck.get("dropout") or {}
            print("loaded phase307 checkpoint", flush=True)
        except (OSError, json.JSONDecodeError):
            pass

    if "A" not in overall and P304_REPORT.is_file():
        try:
            p304 = json.loads(P304_REPORT.read_text(encoding="utf-8"))
            comp_b = (p304.get("comparison") or {}).get("B") or {}
            if comp_b.get("trade_count"):
                overall["A"] = dict(comp_b)
                overall["A"]["source"] = "phase304_duration_value_review.scenario_B"
                print("loaded A from phase304 report", flush=True)
        except (OSError, json.JSONDecodeError):
            pass

    if "A" not in overall and P306_CHECKPOINT.is_file():
        try:
            ck = json.loads(P306_CHECKPOINT.read_text(encoding="utf-8"))
            trades = ck.get("trades") or []
            if trades:
                overall["A"] = _metrics_from_p306_trades(trades)
                print(f"loaded A from phase306 checkpoint ({len(trades)} trades)", flush=True)
        except (OSError, json.JSONDecodeError):
            pass

    need_replay = [sid for sid in ("A", "B") if sid not in overall]
    need_dropout = not dropout_metrics

    if need_replay or need_dropout:
        sims: dict[str, ScenarioSim] = {}
        if "B" in need_replay:
            sims["B"] = ScenarioSim(
                scenario_defs["B"], _score_points(exclude_price=True), p71, p270
            )
        if "A" in need_replay:
            sims["A"] = ScenarioSim(
                scenario_defs["A"], _score_points(exclude_price=False), p71, p270
            )
        dropout_sim: Optional[MarginalPriceDropoutSim] = None
        if need_dropout:
            dropout_sim = MarginalPriceDropoutSim(scenario_defs["A"], p71, p270)
        print(
            f"replay scenarios={list(sims.keys())} dropout={need_dropout} dyn_p66={p66}",
            flush=True,
        )
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
            if dropout_sim is not None:
                dropout_sim.begin_session(meta)
            for ev in ordered:
                for sim in sims.values():
                    sim.on_row(ev)
                if dropout_sim is not None:
                    dropout_sim.on_row(ev)
            for sim in sims.values():
                sim.finalize(session_end)
            if dropout_sim is not None:
                dropout_sim.finalize(session_end)
            if i % 5 == 0 or i == len(sessions):
                print(f"  [{i}/{len(sessions)}]", flush=True)
        for sid, sim in sims.items():
            overall[sid] = _metrics(sim.completed, sim.max_concurrent_reject_count, sim.score_ge5_gate_pass)
            overall[sid]["reject_reason_counts"] = dict(sim.reject_reason_counts.most_common(12))
            for day in calendar_days:
                td = [t for t in sim.completed if t.day == day]
                daily[day][sid] = _metrics(td, sim.max_concurrent_reject_count, 0)
        if dropout_sim is not None:
            dropout_metrics = _metrics(
                dropout_sim.completed, dropout_sim.max_concurrent_reject_count, dropout_sim.score_ge5_gate_pass
            )
        CHECKPOINT.write_text(
            json.dumps(
                _json_safe({"overall": overall, "daily_by_scenario": dict(daily), "dropout": dropout_metrics}),
                indent=2,
            ),
            encoding="utf-8",
        )

    zero_rates = _zero_trade_rates(calendar_days, daily, ["A", "B"])
    for sid in ("A", "B"):
        if sid in overall and daily and any(daily.get(d, {}).get(sid) for d in calendar_days):
            overall[sid]["zero_trade_day_rate"] = zero_rates[sid]["zero_trade_day_rate"]
            overall[sid]["zero_trade_days"] = zero_rates[sid]["zero_trade_days"]

    print("price dependency scan...", flush=True)
    dep = _price_dependency_scan(p270, sessions, duration_p33=p33, duration_p66=p66)

    if "A" in overall and not overall["A"].get("score_ge5_count"):
        overall["A"]["score_ge5_count"] = dep.get("score_ge5_count_A_with_price")

    dropout_note = {
        "description": "Replay of entries that score>=5 under A but <5 under B with Price:high active",
        "gate_level_dropout_count": dep["dropout_count_A_ge5_B_lt5"],
        "marginal_price_only_count": dep["marginal_score5_price_dependent"],
        "simulated_dropout_trades": dropout_metrics,
    }

    verdict = _verdict(overall, dep, dropout_metrics)

    report = {
        "phase": 307,
        "title": "price_token_removal_review",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraint": "review only; no production/entry/exit changes",
        "replay_window": {"start": DATE_START, "end": DATE_END, "aligned_with": ["Phase272", "Phase273", "Phase274"]},
        "fixed_conditions": {
            "hbrecent_pregate": True,
            "board_pregate": True,
            "duration_dynamic": dur,
            "daytrade": True,
            "price_risk": True,
            "entry_score_v2_min": V2_MIN,
            "price_high_cutoff_p66": 4645.0,
            "note": "Price:high uses Phase229 tertile on current_price; structural rationale not established per Phase306",
        },
        "scenarios": {
            "A": {"label": scenario_defs["A"].label, "score_points": list(_score_points(exclude_price=False).keys())},
            "B": {"label": scenario_defs["B"].label, "score_points": list(_score_points(exclude_price=True).keys())},
        },
        "sessions": {"count": len(sessions), "skipped": skipped},
        "comparison": {sid: overall[sid] for sid in ("A", "B") if sid in overall},
        "price_dependency_analysis": {**dep, "dropout_detail": dropout_note},
        "phase306_crosscheck": {
            "phase306_price_high_delta_pnl": -3.9405,
            "phase306_price_high_fire_rate_loss_vs_win": "61.6% vs 52.3%",
        },
        "verdict": verdict,
    }

    OUT.write_text(json.dumps(_json_safe(report), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    a, b = overall.get("A", {}), overall.get("B", {})
    print(
        f"A tc={a.get('trade_count')} PF={a.get('profit_factor')} | "
        f"B tc={b.get('trade_count')} PF={b.get('profit_factor')} | "
        f"required={verdict['price_token_required']} harmful={verdict['price_token_harmful']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
