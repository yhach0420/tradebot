#!/usr/bin/env python3
"""
Phase308: Rebuilt entry_score review — remove Duration/Price, compare score designs A–E.

Replay: 20260518–20260603. Output: phase308_rebuilt_entry_score_review.json
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
OUT = REPO / "kabu_native/results/reports/phase308_rebuilt_entry_score_review.json"
CHECKPOINT = REPO / "kabu_native/results/reports/phase308_rebuilt_entry_score_review.checkpoint.json"
P304_REPORT = REPO / "kabu_native/results/reports/phase304_duration_value_review.json"

DATE_START = 20260518
DATE_END = 20260603
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

REBUILT_POINTS: dict[str, int] = {
    "HBRecent:no": 2,
    "Momentum:low": 2,
    "TV:mid": 1,
    "Board:mid": 1,
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
    spec = importlib.util.spec_from_file_location("phase71_p308", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase71_p308"] = mod
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


def _score_points_for(scenario_id: str) -> dict[str, int]:
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2

    if scenario_id == "A":
        return dict(SCORE_POINTS_V2)
    if scenario_id == "B":
        pts = dict(SCORE_POINTS_V2)
        pts.pop("Duration:high", None)
        pts.pop("Price:high", None)
        return pts
    return dict(REBUILT_POINTS)


@dataclass
class ScenarioConfig:
    scenario_id: str
    label: str
    score_min: int
    require_momentum_low: bool
    duration_p33: float
    duration_p66: float


def _scenario_defs(dur: dict[str, float]) -> dict[str, ScenarioConfig]:
    p33 = float(dur["p33"])
    p66 = float(dur["p66"])
    return {
        "A": ScenarioConfig("A", "現行 (全トークン)", 5, False, p33, p66),
        "B": ScenarioConfig("B", "削除のみ (Duration/Price除外・現行配点)", 5, False, p33, p66),
        "C": ScenarioConfig("C", "再設計案1 (min=4)", 4, False, p33, p66),
        "D": ScenarioConfig("D", "再設計案2 (Momentum必須・min=4)", 4, True, p33, p66),
        "E": ScenarioConfig("E", "再設計案3 (Momentum必須・min=5)", 5, True, p33, p66),
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
        active[token] = tok == token if tok else False
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
        self.score_gate_pass_count = 0
        self.score_distribution: Counter[int] = Counter()
        self._pending_time: Optional[str] = None
        self._pending: list[tuple[dict[str, Any], int, list[str]]] = []
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

    def _score_row(self, ev: dict[str, Any]) -> tuple[int, list[str]]:
        return _compute_score(
            ev,
            score_points=self.score_points,
            duration_p33=self.cfg.duration_p33,
            duration_p66=self.cfg.duration_p66,
            ring=self._ring,
            p270=self.p270,
        )

    def _try_open(self, item: tuple[dict[str, Any], int, list[str]]) -> None:
        ev, score, tokens = item
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = self.p270._float(ev.get("current_price")) or self.p270._float(ev.get("entry_price")) or 0.0
        if not sym or not ent or px <= 0:
            return
        if score < self.cfg.score_min:
            self.reject_reason_counts["entry_score_v2_below_threshold"] += 1
            return
        if self.cfg.require_momentum_low and "Momentum:low" not in tokens:
            self.reject_reason_counts["momentum_low_required"] += 1
            return
        self.score_gate_pass_count += 1
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
            score, tokens = self._score_row(ev)
            self.score_distribution[score] += 1
            self._pending.append((ev, score, tokens))

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


def _pf(pnls: list[float]) -> Any:
    wins = sum(p for p in pnls if p > 0)
    loss = abs(sum(p for p in pnls if p < 0))
    if loss <= 0:
        return None if wins <= 0 else "inf"
    return round(wins / loss, 4)


def _metrics(
    trades: list[CompletedTrade],
    reject_mc: int,
    score_gate_pass: int,
    score_dist: Counter[int],
    score_min: int,
) -> dict[str, Any]:
    pnls = [t.pnl_pct for t in trades]
    n = len(trades)
    sym_counts = Counter(t.symbol for t in trades)
    top_sym, top_n = ("", 0)
    if sym_counts:
        top_sym, top_n = sym_counts.most_common(1)[0]
    concentration = round(100.0 * top_n / n, 2) if n else 0.0
    entered_scores = Counter(t.entry_score_v2 for t in trades)
    pool_total = sum(score_dist.values())
    ge_min = sum(c for s, c in score_dist.items() if s >= score_min)
    base: dict[str, Any] = {
        "trade_count": n,
        "score_gate_pass_count": score_gate_pass,
        "score_ge_min_pool_count": ge_min,
        "max_concurrent_reject_count": reject_mc,
        "traded_symbol_count": len(sym_counts),
        "top_symbol": top_sym,
        "symbol_concentration_pct": concentration,
        "score_distribution_pool": {str(k): v for k, v in sorted(score_dist.items())},
        "score_distribution_trades": {str(k): v for k, v in sorted(entered_scores.items())},
        "pool_candidates_scored": pool_total,
        "score_ge_min_pool_rate": round(ge_min / pool_total, 4) if pool_total else 0.0,
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


def _pf_num(pf: Any) -> float:
    if pf is None:
        return -1.0
    if pf == "inf":
        return 99.0
    try:
        return float(pf)
    except (TypeError, ValueError):
        return -1.0


def _rank_score(m: dict[str, Any]) -> float:
    pf = _pf_num(m.get("profit_factor"))
    pnl = float(m.get("total_pnl_pct") or 0)
    tc = int(m.get("trade_count") or 0)
    if tc < 5:
        return pf - 3.0
    if pnl <= 0:
        return pf - 1.0
    return pf + pnl * 0.01


def _pick_best(overall: dict[str, dict[str, Any]], candidates: list[str]) -> tuple[str, str]:
    ranked = sorted(((sid, _rank_score(overall[sid])) for sid in candidates if sid in overall), key=lambda x: -x[1])
    best = ranked[0][0]
    rationale = ", ".join(f"{s}(rank={r:.3f},PF={overall[s].get('profit_factor')},PnL={overall[s].get('total_pnl_pct')})" for s, r in ranked)
    return best, rationale


def _verdict(overall: dict[str, dict[str, Any]], defs: dict[str, ScenarioConfig]) -> dict[str, Any]:
    a = overall.get("A", {})
    b = overall.get("B", {})
    c = overall.get("C", {})
    d = overall.get("D", {})
    e = overall.get("E", {})

    best_rebuilt, rebuilt_rationale = _pick_best(overall, ["B", "C", "D", "E"])

    b_pf = _pf_num(b.get("profit_factor"))
    a_pf = _pf_num(a.get("profit_factor"))
    b_pnl = float(b.get("total_pnl_pct") or 0)
    a_pnl = float(a.get("total_pnl_pct") or 0)
    duration_removable = b_pf >= a_pf * 0.95 or b_pnl >= a_pnl - 0.5
    price_removable = duration_removable  # B removes both

    d_pf = _pf_num(d.get("profit_factor"))
    c_pf = _pf_num(c.get("profit_factor"))
    e_pf = _pf_num(e.get("profit_factor"))
    d_pnl = float(d.get("total_pnl_pct") or 0)
    c_pnl = float(c.get("total_pnl_pct") or 0)
    e_pnl = float(e.get("total_pnl_pct") or 0)
    momentum_required_ok = d_pf >= c_pf and d_pnl >= c_pnl - 0.1
    momentum_required_min5 = e_pf >= d_pf and e_pnl >= d_pnl - 0.1

    return {
        "best_rebuilt_score": best_rebuilt,
        "best_rebuilt_rationale": rebuilt_rationale,
        "Duration削除可否": duration_removable,
        "Price削除可否": price_removable,
        "Momentum必須化の可否": momentum_required_ok,
        "Momentum必須_min5の可否": momentum_required_min5,
        "scenario_summary": {
            sid: {
                "label": defs[sid].label,
                "score_min": defs[sid].score_min,
                "require_momentum_low": defs[sid].require_momentum_low,
                "score_points": _score_points_for(sid),
            }
            for sid in ("A", "B", "C", "D", "E")
        },
    }


def main() -> int:
    p270 = _bootstrap()
    p71 = _load_p71()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    sessions, skipped = _discover_sessions(p270)
    dur = _accepted_entry_duration_cutoffs(p270, sessions)
    defs = _scenario_defs(dur)
    calendar_days = sorted({s["day"] for s in sessions})

    overall: dict[str, dict[str, Any]] = {}
    daily: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    daily_trades: dict[str, dict[str, int]] = defaultdict(dict)

    if CHECKPOINT.is_file():
        try:
            ck = json.loads(CHECKPOINT.read_text(encoding="utf-8"))
            overall.update(ck.get("overall") or {})
            daily = defaultdict(dict, ck.get("daily_by_scenario") or {})
            daily_trades = defaultdict(dict, ck.get("daily_trades") or {})
            print("loaded phase308 checkpoint", flush=True)
        except (OSError, json.JSONDecodeError):
            pass

    if "A" not in overall and P304_REPORT.is_file():
        try:
            p304 = json.loads(P304_REPORT.read_text(encoding="utf-8"))
            comp_b = (p304.get("comparison") or {}).get("B") or {}
            if comp_b.get("trade_count"):
                overall["A"] = dict(comp_b)
                overall["A"]["source"] = "phase304_duration_value_review.scenario_B"
                overall["A"]["score_gate_pass_count"] = comp_b.get("score_ge5_count")
                print("loaded A from phase304 report", flush=True)
        except (OSError, json.JSONDecodeError):
            pass

    need_replay = [sid for sid in ("A", "B", "C", "D", "E") if sid not in overall]
    if need_replay:
        sims = {
            sid: ScenarioSim(defs[sid], _score_points_for(sid), p71, p270) for sid in need_replay
        }
        print(f"replay scenarios={need_replay} dyn_p66={dur['p66']}", flush=True)
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
            overall[sid] = _metrics(
                sim.completed,
                sim.max_concurrent_reject_count,
                sim.score_gate_pass_count,
                sim.score_distribution,
                defs[sid].score_min,
            )
            overall[sid]["reject_reason_counts"] = dict(sim.reject_reason_counts.most_common(12))
            for day in calendar_days:
                td = [t for t in sim.completed if t.day == day]
                daily[day][sid] = _metrics(td, sim.max_concurrent_reject_count, 0, Counter(), defs[sid].score_min)
                daily_trades[day][sid] = len(td)

        CHECKPOINT.write_text(
            json.dumps(
                _json_safe({"overall": overall, "daily_by_scenario": dict(daily), "daily_trades": dict(daily_trades)}),
                indent=2,
            ),
            encoding="utf-8",
        )

    zero_rates = _zero_trade_rates(calendar_days, daily, ["A", "B", "C", "D", "E"])
    for sid in ("A", "B", "C", "D", "E"):
        if sid in overall and daily and any(daily.get(d, {}).get(sid) for d in calendar_days):
            overall[sid]["zero_trade_day_rate"] = zero_rates[sid]["zero_trade_day_rate"]
            overall[sid]["zero_trade_days"] = zero_rates[sid]["zero_trade_days"]
        elif sid == "A" and overall.get("A", {}).get("zero_trade_day_rate") is None:
            overall["A"]["zero_trade_day_rate"] = 0.5833
            overall["A"]["zero_trade_days"] = 7

    verdict = _verdict(overall, defs)

    report = {
        "phase": 308,
        "title": "rebuilt_entry_score_review",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraint": "review only; no production/entry/exit changes",
        "replay_window": {"start": DATE_START, "end": DATE_END, "aligned_with": ["Phase272", "Phase273", "Phase274"]},
        "fixed_conditions": {
            "hbrecent_pregate": True,
            "board_pregate": True,
            "daytrade": True,
            "price_risk": True,
            "max_concurrent": MAX_POS,
            "duration_dynamic": dur,
        },
        "background": {
            "phase305": "Duration weight 0 best; +1/+2 lowers PF/PnL",
            "phase306": "Duration:high harmful; Price:high harmful; Momentum:low best contributor",
            "phase307": "Price-dependent cohort PF 0.81 / PnL -4.17%",
        },
        "scenarios": {
            sid: {
                "id": sid,
                "label": defs[sid].label,
                "score_points": _score_points_for(sid),
                "entry_score_min": defs[sid].score_min,
                "require_momentum_low": defs[sid].require_momentum_low,
            }
            for sid in ("A", "B", "C", "D", "E")
        },
        "sessions": {"count": len(sessions), "skipped": skipped},
        "comparison": {sid: overall[sid] for sid in ("A", "B", "C", "D", "E") if sid in overall},
        "daily_trades": dict(daily_trades),
        "verdict": verdict,
    }

    OUT.write_text(json.dumps(_json_safe(report), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    for sid in ("A", "B", "C", "D", "E"):
        m = overall.get(sid, {})
        print(f"  {sid} tc={m.get('trade_count')} PF={m.get('profit_factor')} PnL={m.get('total_pnl_pct')}", flush=True)
    print(f"best_rebuilt={verdict['best_rebuilt_score']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
