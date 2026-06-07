#!/usr/bin/env python3
"""
Phase293: pre-gate feature fix review for entry_score_v2 (review only).

Validate scenarios fixing HBRecent pre-gate timing and Duration:high live cutoff.
Output: kabu_native/results/reports/phase293_score_pregate_feature_fix_review.json
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native/results/reports/phase293_score_pregate_feature_fix_review.json"
PHASE292 = REPO / "kabu_native/results/reports/phase292_score_generation_integrity_audit.json"

DATE_START = 20260518
DATE_END = 20260605
TARGET_DAYS = ("20260604", "20260605")
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
    spec = importlib.util.spec_from_file_location("phase71_p293", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase71_p293"] = mod
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


def _percentile(vals: list[float], pct: float) -> Optional[float]:
    if not vals:
        return None
    s = sorted(vals)
    idx = min(len(s) - 1, int(len(s) * pct / 100.0))
    return float(s[idx])


def _live_duration_cutoffs(p270: Any, sessions: list[dict[str, Any]]) -> dict[str, Any]:
    vals: list[float] = []
    live_sessions = 0
    for meta in sessions:
        if meta.get("stream") != "live":
            continue
        live_sessions += 1
        events = p270._load_events(SMALL_PAPER / meta["session_id"])
        for ev in events:
            if str(ev.get("event_type") or "") not in ("candidate", "accepted", "rejected"):
                continue
            raw = p270._float(ev.get("max_continuation_duration"))
            if raw is not None:
                vals.append(float(raw))
    return {
        "live_sessions_sampled": live_sessions,
        "sample_count": len(vals),
        "p33": _percentile(vals, 33),
        "p66": _percentile(vals, 66),
        "p80": _percentile(vals, 80),
        "p90": _percentile(vals, 90),
        "max": max(vals) if vals else None,
        "legacy_duration_high_cutoff": 406.0,
        "recommended_for_C_D": _percentile(vals, 66),
    }


@dataclass
class ScenarioConfig:
    scenario_id: str
    label: str
    hbrecent_pregate: bool = False
    duration_p66: float = 406.0


def _scenario_configs(duration_live_p66: float) -> dict[str, ScenarioConfig]:
    return {
        "A": ScenarioConfig("A", "現行 (gate前HBRecent欠落 + Duration cutoff 406)"),
        "B": ScenarioConfig("B", "HBRecent pre-gateのみ", hbrecent_pregate=True),
        "C": ScenarioConfig(
            "C",
            f"Duration cutoff live補正のみ (p66={duration_live_p66})",
            duration_p66=duration_live_p66,
        ),
        "D": ScenarioConfig(
            "D",
            f"HBRecent pre-gate + Duration cutoff live補正 (p66={duration_live_p66})",
            hbrecent_pregate=True,
            duration_p66=duration_live_p66,
        ),
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
        from small_paper.extended_entry_shadow import _high_break_recent
        from storage.intraday_recorder import parse_kabu_time

        sym = str(ev.get("symbol") or "")
        px = p270._float(ev.get("current_price")) or 0.0
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        if not sym or px <= 0 or not ent:
            return False
        ts = parse_kabu_time(ent, fallback=datetime.now(JST)).timestamp()
        return _high_break_recent(self.rings.get(sym, []), ts, px)


def _active_tokens(ev: dict[str, Any], score_points: dict[str, int], duration_p66: float) -> dict[str, bool]:
    from small_paper.entry_expectancy_score_shadow import TERTILE_CUTOFFS, _bin_tertile, _float

    active: dict[str, bool] = {}
    for token, pts in score_points.items():
        if pts <= 0:
            continue
        lbl = token.split(":", 1)[0]
        if lbl == "HBRecent":
            hb = ev.get("entry_high_break_recent")
            if hb is None:
                active[token] = False
                continue
            tok = f"HBRecent:{'yes' if str(hb).lower() in ('true', '1', 'yes') else 'no'}"
        elif lbl == "Duration":
            v = _float(ev.get("max_continuation_duration"))
            if v is None:
                active[token] = False
                continue
            cuts = TERTILE_CUTOFFS["Duration"]
            level = _bin_tertile(v, cuts["p33"], duration_p66)
            tok = f"Duration:{level}"
        else:
            from small_paper.entry_expectancy_score_shadow import _feature_token

            tok = _feature_token(lbl, ev)
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
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2

    work = dict(ev)
    audit: dict[str, Any] = {
        "entry_high_break_recent": work.get("entry_high_break_recent"),
        "max_continuation_duration": work.get("max_continuation_duration"),
        "trading_value": work.get("trading_value"),
        "entry_order_book_imbalance": work.get("entry_order_book_imbalance"),
    }

    if cfg.hbrecent_pregate:
        hb = ring.hbrecent(work, p270)
        work["entry_high_break_recent"] = hb
        audit["entry_high_break_recent"] = hb
        audit["entry_high_break_recent_source"] = "pregate_price_ring"
    else:
        work["entry_high_break_recent"] = None
        audit["entry_high_break_recent"] = None
        audit["entry_high_break_recent_source"] = "gate_current_none"

    active = _active_tokens(work, score_points, cfg.duration_p66)
    score = sum(score_points[t] for t, on in active.items() if on)
    audit["active_score_tokens"] = [t for t, on in active.items() if on]
    audit["entry_score_v2"] = score
    return score, audit


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
        self.score_appearance: Counter[int] = Counter()
        self.v2_pass_count = 0
        self._pending_time: Optional[str] = None
        self._pending: list[tuple[dict[str, Any], int, dict[str, Any]]] = []
        self._day = ""
        self._session_id = ""
        self._universe_syms: set[str] = set()
        self._daytrade_state: Any = None
        self._price_guard = _price_guard_state()
        self._ring = PriceRingTracker()
        self._appearance_at_session_start: Counter[int] = Counter()

    def _hard_exclude(self) -> frozenset[str]:
        return BASE_HARD_EXCLUDE | frozenset(AUX_FILTER.get("hard_exclude_extra") or [])

    def _in_pool(self, ev: dict[str, Any]) -> bool:
        et = str(ev.get("event_type") or "")
        gr = str(ev.get("gate_reject_reason") or "")
        if et == "accepted":
            return True
        if et == "rejected":
            return gr not in self._hard_exclude()
        return False

    def _aux_fail(self, ev: dict[str, Any]) -> Optional[str]:
        sym = str(ev.get("symbol") or "")
        if AUX_FILTER.get("price_risk_universe") and self._universe_syms:
            if sym not in self._universe_syms:
                return "outside_price_risk_universe"
        if AUX_FILTER.get("price_risk_guard"):
            gr = self._price_guard.check(ev)
            if gr.blocked:
                return "entry_price_risk_guard"
        return None

    def _score_row(self, ev: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if self.cfg.scenario_id == "A":
            et = str(ev.get("event_type") or "")
            gr = str(ev.get("gate_reject_reason") or "")
            if et == "rejected" and gr == "entry_score_v2_below_threshold":
                raw = ev.get("entry_expectancy_score_v2")
                if raw is not None and raw != "":
                    score = int(raw)
                    return score, {
                        "entry_score_v2": score,
                        "entry_high_break_recent": None,
                        "entry_high_break_recent_source": "gate_logged_reject",
                        "active_score_tokens": [],
                    }
        return _compute_v2(
            ev,
            score_points=self.score_points,
            cfg=self.cfg,
            ring=self._ring,
            p270=self.p270,
        )

    def _try_open(self, item: tuple[dict[str, Any], int, dict[str, Any]]) -> None:
        ev, score, _audit = item
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = self.p270._float(ev.get("current_price")) or self.p270._float(ev.get("entry_price")) or 0.0
        if not sym or not ent or px <= 0:
            return
        if score < V2_MIN:
            self.reject_reason_counts["entry_score_v2_below_threshold"] += 1
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
        ts = self.p71._parse_ts(ent) if hasattr(self.p71, "_parse_ts") else self.p270._parse_ts(ent)
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
        act._entry_score_v2 = score  # noqa: SLF001 — replay audit only
        self.active[sym] = act

    def _close(self, act: Any, *, close_price: float, reason: str, entry_score: int) -> None:
        pnl = float(self.p71._pnl_pct(act.trade.entry_price, close_price))
        self.completed.append(
            CompletedTrade(
                pnl_pct=pnl,
                stop_hit=str(reason) == "stop_hit",
                symbol=str(act.trade.symbol),
                day=self._day,
                entry_score_v2=entry_score,
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

        score: Optional[int] = None
        audit: dict[str, Any] = {}
        if self._in_pool(ev):
            score, audit = self._score_row(ev)
            self.score_appearance[score] += 1
            if score >= V2_MIN:
                self.v2_pass_count += 1

        if self._pending_time is None:
            self._pending_time = ev_time
        if ev_time != self._pending_time:
            self._flush()
            self._pending_time = ev_time

        if et == "candidate":
            if sym not in self.active or px <= 0 or not ent:
                return
            ts = self.p71._parse_ts(ent) if hasattr(self.p71, "_parse_ts") else self.p270._parse_ts(ent)
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
                entry_score = getattr(act, "_entry_score_v2", 0)
                self._close(act, close_price=float(px), reason=str(reason), entry_score=entry_score)
                self.active.pop(sym, None)

        elif et == "accepted":
            if score is None:
                score, audit = self._score_row(ev)
            self._pending.append((ev, int(score), audit))
        elif et == "rejected" and self._pool_exclude_reason(ev) is None:
            if score is None:
                score, audit = self._score_row(ev)
            self._pending.append((ev, int(score), audit))

    def finalize(self, session_end: str) -> None:
        self._flush()
        for sym, act in list(self.active.items()):
            last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
            entry_score = getattr(act, "_entry_score_v2", 0)
            self._close(act, close_price=float(last_px), reason="session_end", entry_score=entry_score)
        self.active.clear()

    def session_appearance_delta(self) -> Counter[int]:
        delta: Counter[int] = Counter()
        for score, cnt in self.score_appearance.items():
            prev = self._appearance_at_session_start.get(score, 0)
            if cnt > prev:
                delta[score] = cnt - prev
        return delta

    def begin_session(self, meta: dict[str, Any]) -> None:
        self._day = meta["day"]
        self._session_id = meta["session_id"]
        self.sym_states = {}
        self.active = {}
        self._pending = []
        self._pending_time = None
        self._ring = PriceRingTracker()
        self._appearance_at_session_start = Counter(self.score_appearance)
        if AUX_FILTER.get("price_risk_universe"):
            self._universe_syms = self.p270._load_universe_symbols(self._day, price_risk=True)
        else:
            self._universe_syms = set()


def _metrics(trades: list[CompletedTrade], reject_mc: int, appearances: Counter[int], v2_pass: int) -> dict[str, Any]:
    pnls = [t.pnl_pct for t in trades]
    stops = sum(1 for t in trades if t.stop_hit)
    n = len(trades)
    sym_counts = Counter(t.symbol for t in trades)
    top_sym, top_n = ("", 0)
    if sym_counts:
        top_sym, top_n = sym_counts.most_common(1)[0]
    concentration = round(100.0 * top_n / n, 2) if n else 0.0
    score4 = sum(c for s, c in appearances.items() if s == 4)
    score5 = sum(c for s, c in appearances.items() if s == 5)
    score_ge4 = sum(c for s, c in appearances.items() if s >= 4)
    score_ge5 = sum(c for s, c in appearances.items() if s >= 5)
    base = {
        "appearance_count": sum(appearances.values()),
        "score_distribution": {str(k): v for k, v in sorted(appearances.items())},
        "max_score": max(appearances.keys()) if appearances else None,
        "score4_count": score4,
        "score5_count": score5,
        "score_ge4_count": score_ge4,
        "score_ge5_count": score_ge5,
        "v2_gate_pass_count": v2_pass,
        "trade_count": n,
        "max_concurrent_reject_count": reject_mc,
        "traded_symbol_count": len(sym_counts),
        "top_symbol": top_sym,
        "symbol_concentration_pct": concentration,
    }
    if n == 0:
        return {
            **base,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "win_rate": None,
            "stop_rate": None,
        }
    wins = sum(1 for p in pnls if p > 0)
    wins_sum = sum(p for p in pnls if p > 0)
    loss_sum = abs(sum(p for p in pnls if p < 0))
    pf = round(wins_sum / loss_sum, 4) if loss_sum > 0 else (None if wins_sum <= 0 else float("inf"))
    return {
        **base,
        "profit_factor": pf if pf != float("inf") else "inf",
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 6),
        "win_rate": round(wins / n, 4),
        "stop_rate": round(stops / n, 4),
    }


def _zero_trade_rates(calendar_days: list[str], daily: dict[str, dict[str, dict[str, Any]]], scenario_ids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for sid in scenario_ids:
        z = sum(1 for d in calendar_days if daily.get(d, {}).get(sid, {}).get("trade_count", 0) == 0)
        out[sid] = {
            "zero_trade_days": z,
            "calendar_days": len(calendar_days),
            "zero_trade_day_rate": round(z / len(calendar_days), 4) if calendar_days else None,
        }
    return out


def _duration_sensitivity(
    events: list[dict[str, Any]],
    ring: PriceRingTracker,
    p270: Any,
    score_points: dict[str, int],
    cutoffs: dict[str, Optional[float]],
) -> dict[str, Any]:
    rows: dict[str, Any] = {}
    for label, p66 in cutoffs.items():
        if p66 is None:
            continue
        cfg = ScenarioConfig("C", label, duration_p66=float(p66))
        dist: Counter[int] = Counter()
        for ev in events:
            if str(ev.get("event_type") or "") not in ("accepted", "rejected"):
                continue
            gr = str(ev.get("gate_reject_reason") or "")
            if str(ev.get("event_type") or "") == "rejected" and gr in BASE_HARD_EXCLUDE:
                continue
            score, _ = _compute_v2(ev, score_points=score_points, cfg=cfg, ring=ring, p270=p270)
            dist[score] += 1
        rows[label] = {
            "duration_p66": p66,
            "score_distribution": {str(k): v for k, v in sorted(dist.items())},
            "score_ge4": sum(v for k, v in dist.items() if k >= 4),
            "score_ge5": sum(v for k, v in dist.items() if k >= 5),
        }
    return rows


def _entry_time_drift_note() -> dict[str, Any]:
    if PHASE292.is_file():
        try:
            p292 = json.loads(PHASE292.read_text(encoding="utf-8"))
            per = p292.get("per_session") or []
            drift = [
                {
                    "session_id": r.get("session_id"),
                    **(r.get("checks", {}).get("7_entry_time_date_drift") or {}),
                }
                for r in per
                if _day_from_sid(str(r.get("session_id") or "")) == "20260605"
            ]
            return {
                "status": "separate_issue_logged",
                "source": "phase292_score_generation_integrity_audit.json",
                "note": (
                    "20260605: Kabu CurrentPriceTime one-day lag vs event_time on 366 events. "
                    "Not fixed in Phase293 scenarios; investigate upstream timestamp separately."
                ),
                "sessions": drift,
            }
        except (OSError, json.JSONDecodeError):
            pass
    return {"status": "phase292_report_missing", "note": "entry_time drift flagged for 20260605"}


def _compare_vs_a(overall: dict[str, dict[str, Any]]) -> dict[str, Any]:
    a = overall.get("A") or {}
    a_pf = a.get("profit_factor")
    a_pnl = float(a.get("total_pnl_pct") or 0)
    a_trades = int(a.get("trade_count") or 0)
    try:
        a_pf_f = float(a_pf) if a_pf not in (None, "inf") else None
    except (TypeError, ValueError):
        a_pf_f = None

    rows: list[dict[str, Any]] = []
    beats: list[str] = []
    for sid, m in overall.items():
        if sid == "A":
            continue
        pf = m.get("profit_factor")
        try:
            pf_f = float(pf) if pf not in (None, "inf") else None
        except (TypeError, ValueError):
            pf_f = None
        pnl = float(m.get("total_pnl_pct") or 0)
        trades = int(m.get("trade_count") or 0)
        pf_better = pf_f is not None and a_pf_f is not None and pf_f > a_pf_f
        pnl_better = pnl > a_pnl
        both = pf_better and pnl_better
        if both:
            beats.append(sid)
        rows.append(
            {
                "scenario": sid,
                "delta_trade_count": trades - a_trades,
                "delta_score_ge4": int(m.get("score_ge4_count") or 0) - int(a.get("score_ge4_count") or 0),
                "delta_score_ge5": int(m.get("score_ge5_count") or 0) - int(a.get("score_ge5_count") or 0),
                "delta_v2_gate_pass": int(m.get("v2_gate_pass_count") or 0) - int(a.get("v2_gate_pass_count") or 0),
                "delta_profit_factor": round(pf_f - a_pf_f, 4) if pf_f is not None and a_pf_f is not None else None,
                "delta_total_pnl_pct": round(pnl - a_pnl, 4),
                "beats_A_on_pf_and_pnl": both,
            }
        )
    return {
        "baseline_A": {
            "profit_factor": a_pf,
            "total_pnl_pct": a_pnl,
            "trade_count": a_trades,
            "score_ge4_count": a.get("score_ge4_count"),
            "score_ge5_count": a.get("score_ge5_count"),
        },
        "candidates_beating_A_pf_and_pnl": beats,
        "per_scenario": rows,
    }


def main() -> int:
    p270 = _bootstrap()
    p71 = _load_p71()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2

    score_points = dict(SCORE_POINTS_V2)
    sessions, skipped = _discover_sessions(p270)
    duration_stats = _live_duration_cutoffs(p270, sessions)
    live_p66 = float(duration_stats["p66"] or 38.0)
    scenario_defs = _scenario_configs(live_p66)
    scenario_ids = list(scenario_defs.keys())

    print(
        f"sessions={len(sessions)} skipped={len(skipped)} duration_live_p66={live_p66}",
        flush=True,
    )

    sims: dict[str, ScenarioSim] = {
        sid: ScenarioSim(scenario_defs[sid], score_points, p71, p270) for sid in scenario_ids
    }
    target_metrics: dict[str, dict[str, Any]] = {sid: defaultdict(Counter) for sid in scenario_ids}

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
        for sid, sim in sims.items():
            sim.begin_session(meta)
            for ev in ordered:
                sim.on_row(ev)
            sim.finalize(session_end)
            if meta["day"] in TARGET_DAYS and meta.get("stream") == "live":
                for score, cnt in sim.session_appearance_delta().items():
                    target_metrics[sid][meta["day"]][score] += cnt
        if i % 5 == 0 or i == len(sessions):
            print(f"  sessions [{i}/{len(sessions)}]", flush=True)

    overall: dict[str, dict[str, Any]] = {}
    for sid, sim in sims.items():
        overall[sid] = _metrics(
            sim.completed,
            sim.max_concurrent_reject_count,
            sim.score_appearance,
            sim.v2_pass_count,
        )

    calendar_days = sorted({s["day"] for s in sessions})
    daily: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for day in calendar_days:
        day_sessions = [s for s in sessions if s["day"] == day]
        for sid, sim in sims.items():
            td = [t for t in sim.completed if t.day == day]
            daily[day][sid] = _metrics(
                td,
                sim.max_concurrent_reject_count,
                Counter(),
                0,
            )

    zero_rates = _zero_trade_rates(calendar_days, daily, scenario_ids)
    for sid in scenario_ids:
        overall[sid]["zero_trade_day_rate"] = zero_rates[sid]["zero_trade_day_rate"]
        overall[sid]["zero_trade_days"] = zero_rates[sid]["zero_trade_days"]

    target_day_summary: dict[str, dict[str, Any]] = {}
    for sid in scenario_ids:
        target_day_summary[sid] = {}
        for day in TARGET_DAYS:
            dist = target_metrics[sid][day]
            target_day_summary[sid][day] = {
                "score_distribution": {str(k): v for k, v in sorted(dist.items())},
                "max_score": max(dist.keys()) if dist else None,
                "score4_count": dist.get(4, 0),
                "score5_count": dist.get(5, 0),
                "score_ge4_count": sum(v for k, v in dist.items() if k >= 4),
                "score_ge5_count": sum(v for k, v in dist.items() if k >= 5),
            }

    # Duration cutoff sensitivity on target live events only
    sens_events: list[dict[str, Any]] = []
    ring = PriceRingTracker()
    for meta in sessions:
        if meta["day"] not in TARGET_DAYS or meta.get("stream") != "live":
            continue
        events = p270._load_events(SMALL_PAPER / meta["session_id"])
        for ev in sorted(
            events,
            key=lambda e: (
                p270._parse_ts(str(e.get("event_time") or "")),
                int(p270._float(e.get("message_index")) or 0),
            ),
        ):
            ring.observe(ev, p270)
            if str(ev.get("event_type") or "") in ("accepted", "rejected"):
                sens_events.append(ev)
    duration_sensitivity = _duration_sensitivity(
        sens_events,
        ring,
        p270,
        score_points,
        {
            "legacy_406": 406.0,
            "live_p66": duration_stats.get("p66"),
            "live_p80": duration_stats.get("p80"),
            "live_p90": duration_stats.get("p90"),
        },
    )

    comparison = _compare_vs_a(overall)

    report = {
        "phase": 293,
        "mode": "score_pregate_feature_fix_review",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraints": {
            "review_only": True,
            "production_logic_changes_forbidden": True,
            "entry_exit_logic_unchanged": True,
            "entry_score_v2_min": V2_MIN,
            "max_concurrent_positions": MAX_POS,
            "auxiliary_filters": "Phase287 A_current (daytrade ON + price-risk universe/guard)",
        },
        "fix_candidates": {
            "1_hbrecent_pregate": "compute entry_high_break_recent from price_ring before v2 score (scenarios B/D)",
            "2_duration_cutoff_live": f"replace Duration p66={duration_stats.get('legacy_duration_high_cutoff')} with live p66={live_p66} (scenarios C/D)",
            "3_reject_feature_persistence": (
                "proposed production change: persist entry_high_break_recent, max_continuation_duration, "
                "trading_value, entry_order_book_imbalance, active_score_tokens on reject events"
            ),
            "4_entry_time_drift": _entry_time_drift_note(),
        },
        "duration_cutoff_analysis": duration_stats,
        "duration_cutoff_sensitivity_target_live": duration_sensitivity,
        "scenarios": {
            sid: {
                "id": sid,
                "label": scenario_defs[sid].label,
                "hbrecent_pregate": scenario_defs[sid].hbrecent_pregate,
                "duration_p66": scenario_defs[sid].duration_p66,
            }
            for sid in scenario_ids
        },
        "date_range": {"start": DATE_START, "end": DATE_END},
        "target_days": list(TARGET_DAYS),
        "sessions": {
            "count": len(sessions),
            "skipped_count": len(skipped),
            "skipped": skipped,
        },
        "overall": overall,
        "target_live_days": target_day_summary,
        "daily_by_scenario": {d: dict(v) for d, v in daily.items()},
        "zero_trade_rates": zero_rates,
        "comparison_vs_A": comparison,
        "verdict": _build_verdict(overall, target_day_summary, live_p66, comparison),
    }

    def _json_default(val: Any) -> Any:
        if val == float("inf"):
            return "inf"
        raise TypeError(type(val))

    OUT.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {OUT}", flush=True)
    for sid in scenario_ids:
        m = overall[sid]
        print(
            f"  {sid}: ge4={m.get('score_ge4_count')} ge5={m.get('score_ge5_count')} "
            f"trades={m.get('trade_count')} PF={m.get('profit_factor')} PnL={m.get('total_pnl_pct')}",
            flush=True,
        )
    return 0


def _build_verdict(
    overall: dict[str, dict[str, Any]],
    target: dict[str, dict[str, Any]],
    live_p66: float,
    comparison: dict[str, Any],
) -> dict[str, Any]:
    a = overall.get("A") or {}
    d = overall.get("D") or {}
    b = overall.get("B") or {}
    c = overall.get("C") or {}
    t4_a = sum(target.get("A", {}).get(day, {}).get("score_ge4_count", 0) for day in TARGET_DAYS)
    t4_d = sum(target.get("D", {}).get(day, {}).get("score_ge4_count", 0) for day in TARGET_DAYS)
    t5_d = sum(target.get("D", {}).get(day, {}).get("score_ge5_count", 0) for day in TARGET_DAYS)
    return {
        "target_live_score_ge4_A": t4_a,
        "target_live_score_ge4_D": t4_d,
        "target_live_score_ge5_D": t5_d,
        "hbrecent_pregate_restores_score4_on_target": t4_d > t4_a,
        "duration_live_p66_used": live_p66,
        "recommended_scenario_for_production_trial": (
            "D"
            if int(d.get("score_ge4_count") or 0) >= int(b.get("score_ge4_count") or 0)
            and int(d.get("score_ge4_count") or 0) >= int(c.get("score_ge4_count") or 0)
            else "B or C"
        ),
        "comparison_vs_A": comparison.get("candidates_beating_A_pf_and_pnl"),
        "summary": (
            f"Scenario A on 20260604-20260605 live produces score_ge4={t4_a}. "
            f"Pre-gate HBRecent + live Duration p66={live_p66} (scenario D) yields "
            f"score_ge4={t4_d}, score_ge5={t5_d} on target days. "
            "Full-session replay: see overall metrics for PF/PnL tradeoff before production adoption."
        ),
    }


if __name__ == "__main__":
    raise SystemExit(main())
