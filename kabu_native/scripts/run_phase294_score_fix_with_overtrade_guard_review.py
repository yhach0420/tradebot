#!/usr/bin/env python3
"""
Phase294: overtrade guard review on Phase293-D score fix (review only).

Output: kabu_native/results/reports/phase294_score_fix_with_overtrade_guard_review.json
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native/results/reports/phase294_score_fix_with_overtrade_guard_review.json"
PHASE293 = REPO / "kabu_native/results/reports/phase293_score_pregate_feature_fix_review.json"

DATE_START = 20260518
DATE_END = 20260605
TARGET_DAYS = ("20260604", "20260605")
V2_MIN_DEFAULT = 5
MAX_POS = 3
V1_MODE = "legacy"
V1_RATIO = 0.85
REJECT_REPLAY_MAX_EVENTS = 500_000
JST = ZoneInfo("Asia/Tokyo")
TOP_N_PER_SYMBOL_DAY = 3
SYMBOL_COOLDOWN_SEC = 300.0
OVERTRADE_MAX_TRADES = 1500

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
    spec = importlib.util.spec_from_file_location("phase71_p294", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase71_p294"] = mod
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


def _live_duration_p66(p270: Any, sessions: list[dict[str, Any]]) -> float:
    if PHASE293.is_file():
        try:
            p293 = json.loads(PHASE293.read_text(encoding="utf-8"))
            rec = p293.get("duration_cutoff_analysis", {}).get("p66")
            if rec is not None:
                return float(rec)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    vals: list[float] = []
    for meta in sessions:
        if meta.get("stream") != "live":
            continue
        events = p270._load_events(SMALL_PAPER / meta["session_id"])
        for ev in events:
            if str(ev.get("event_type") or "") not in ("candidate", "accepted", "rejected"):
                continue
            raw = p270._float(ev.get("max_continuation_duration"))
            if raw is not None:
                vals.append(float(raw))
    return float(_percentile(vals, 66) or 12.0)


@dataclass
class GuardConfig:
    v2_min: int = 5
    required_tokens: frozenset[str] = field(default_factory=frozenset)
    daytrade_stricter: bool = False
    daytrade_percentile: float = 0.50
    top_n_per_symbol_day: Optional[int] = None
    symbol_cooloff: bool = False
    symbol_entry_cooldown_sec: Optional[float] = None
    exclude_opening_0900_0915: bool = False
    symbol_metrics_only: bool = False


@dataclass
class ScenarioConfig:
    scenario_id: str
    label: str
    hbrecent_pregate: bool = False
    duration_p66: float = 406.0
    guard: GuardConfig = field(default_factory=GuardConfig)


def _scenario_definitions(duration_p66: float) -> dict[str, ScenarioConfig]:
    d_base = GuardConfig(v2_min=5)
    return {
        "A": ScenarioConfig("A", "現行 (Phase293-A)", duration_p66=406.0),
        "D": ScenarioConfig(
            "D",
            "Phase293-D (HBRecent pre-gate + Duration live p66)",
            hbrecent_pregate=True,
            duration_p66=duration_p66,
            guard=d_base,
        ),
        "D1": ScenarioConfig(
            "D1",
            "D + score>=6",
            hbrecent_pregate=True,
            duration_p66=duration_p66,
            guard=GuardConfig(v2_min=6),
        ),
        "D2": ScenarioConfig(
            "D2",
            "D + score>=5 + daytrade stricter (top35%)",
            hbrecent_pregate=True,
            duration_p66=duration_p66,
            guard=GuardConfig(v2_min=5, daytrade_stricter=True, daytrade_percentile=0.65),
        ),
        "D3": ScenarioConfig(
            "D3",
            "D + score>=5 + HBRecent:no必須",
            hbrecent_pregate=True,
            duration_p66=duration_p66,
            guard=GuardConfig(v2_min=5, required_tokens=frozenset({"HBRecent:no"})),
        ),
        "D4": ScenarioConfig(
            "D4",
            "D + score>=5 + Duration:high必須",
            hbrecent_pregate=True,
            duration_p66=duration_p66,
            guard=GuardConfig(v2_min=5, required_tokens=frozenset({"Duration:high"})),
        ),
        "D5": ScenarioConfig(
            "D5",
            "D + score>=5 + Momentum:low必須",
            hbrecent_pregate=True,
            duration_p66=duration_p66,
            guard=GuardConfig(v2_min=5, required_tokens=frozenset({"Momentum:low"})),
        ),
        "D6": ScenarioConfig(
            "D6",
            f"D + score>=5 + top {TOP_N_PER_SYMBOL_DAY} per symbol/day",
            hbrecent_pregate=True,
            duration_p66=duration_p66,
            guard=GuardConfig(v2_min=5, top_n_per_symbol_day=TOP_N_PER_SYMBOL_DAY),
        ),
        "D7": ScenarioConfig(
            "D7",
            f"D + score>=5 + same-symbol cooldown {int(SYMBOL_COOLDOWN_SEC)}s",
            hbrecent_pregate=True,
            duration_p66=duration_p66,
            guard=GuardConfig(v2_min=5, symbol_entry_cooldown_sec=SYMBOL_COOLDOWN_SEC),
        ),
        "D8": ScenarioConfig(
            "D8",
            "D + score>=5 + exclude 09:00-09:15",
            hbrecent_pregate=True,
            duration_p66=duration_p66,
            guard=GuardConfig(v2_min=5, exclude_opening_0900_0915=True),
        ),
        "D9": ScenarioConfig(
            "D9",
            "D + 銘柄別集計のみ (same gates as D, symbol breakdown)",
            hbrecent_pregate=True,
            duration_p66=duration_p66,
            guard=GuardConfig(v2_min=5, symbol_metrics_only=True),
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
    from small_paper.entry_expectancy_score_shadow import TERTILE_CUTOFFS, _bin_tertile, _float, _feature_token

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
    work = dict(ev)
    if cfg.scenario_id == "A":
        et = str(ev.get("event_type") or "")
        gr = str(ev.get("gate_reject_reason") or "")
        if et == "rejected" and gr == "entry_score_v2_below_threshold":
            raw = ev.get("entry_expectancy_score_v2")
            if raw is not None and raw != "":
                score = int(raw)
                return score, {"entry_score_v2": score, "active_score_tokens": []}

    if cfg.hbrecent_pregate:
        hb = ring.hbrecent(work, p270)
        work["entry_high_break_recent"] = hb
    else:
        work["entry_high_break_recent"] = None

    active = _active_tokens(work, score_points, cfg.duration_p66)
    score = sum(score_points[t] for t, on in active.items() if on)
    return score, {
        "entry_score_v2": score,
        "active_score_tokens": [t for t, on in active.items() if on],
        "entry_high_break_recent": work.get("entry_high_break_recent"),
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
_COOLOFF_CACHE: dict[str, Any] = {}


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


def _symbol_cooloff_state(session_id: str) -> Any:
    if session_id in _COOLOFF_CACHE:
        return _COOLOFF_CACHE[session_id]
    from small_paper.symbol_cooloff import (
        RULE_PRIOR_AVG_PNL_NEGATIVE_TRADES_GE_5,
        SymbolCooloffConfig,
        SymbolCooloffState,
        aggregate_prior_stats,
        apply_cooloff_rule,
        discover_sessions_with_trades,
    )

    base = REPO / "kabu_native/results/small_paper"
    sources = discover_sessions_with_trades(base, before_session_key=session_id)
    prior = aggregate_prior_stats(sources)
    cfg = SymbolCooloffConfig(
        enabled=True,
        rule=RULE_PRIOR_AVG_PNL_NEGATIVE_TRADES_GE_5,
        min_trades=5,
        threshold=0.0,
    )
    cooloff = apply_cooloff_rule(
        prior,
        rule=cfg.rule,
        min_trades=cfg.min_trades,
        threshold=cfg.threshold,
    )
    state = SymbolCooloffState(
        config=cfg,
        run_session_key=session_id,
        source_sessions=[s for s, _ in sources],
        cooloff_symbols=cooloff,
        prior_stats=prior,
    )
    _COOLOFF_CACHE[session_id] = state
    return state


def _entry_in_opening_exclude(ent: str) -> bool:
    from storage.intraday_recorder import parse_kabu_time

    dt = parse_kabu_time(ent, fallback=datetime.now(JST))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    dt = dt.astimezone(JST)
    mins = dt.hour * 60 + dt.minute
    return (9 * 60) <= mins < (9 * 60 + 15)


@dataclass
class CompletedTrade:
    pnl_pct: float
    stop_hit: bool
    symbol: str
    day: str
    entry_score_v2: int = 0
    mfe_pct: float = 0.0
    mae_pct: float = 0.0


class ScenarioSim:
    def __init__(self, cfg: ScenarioConfig, score_points: dict[str, int], p71: Any, p270: Any):
        self.cfg = cfg
        self.guard = cfg.guard
        self.score_points = score_points
        self.p71 = p71
        self.p270 = p270
        self.sym_states: dict[str, Any] = {}
        self.active: dict[str, Any] = {}
        self.completed: list[CompletedTrade] = []
        self.max_concurrent_reject_count = 0
        self.reject_reason_counts: Counter[str] = Counter()
        self._pending_time: Optional[str] = None
        self._pending: list[tuple[dict[str, Any], int, dict[str, Any]]] = []
        self._day = ""
        self._session_id = ""
        self._universe_syms: set[str] = set()
        self._daytrade_state: Any = None
        self._cooloff_state: Any = None
        self._price_guard = _price_guard_state()
        self._ring = PriceRingTracker()
        self._symbol_day_opens: Counter[tuple[str, str]] = Counter()
        self._last_symbol_entry_ts: dict[str, float] = {}

    def _hard_exclude(self) -> frozenset[str]:
        extra = set(AUX_FILTER.get("hard_exclude_extra") or [])
        if self.guard.daytrade_stricter:
            extra.discard("daytrade_suitability")
        return BASE_HARD_EXCLUDE | frozenset(extra)

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
        if self.guard.daytrade_stricter and self._daytrade_state is not None:
            if self._daytrade_state.check(ev).blocked:
                return "daytrade_suitability_stricter"
        if self.guard.symbol_cooloff and self._cooloff_state is not None:
            if self._cooloff_state.check(sym).blocked:
                return "symbol_cooloff"
        return None

    def _guard_fail(self, ev: dict[str, Any], score: int, audit: dict[str, Any]) -> Optional[str]:
        g = self.guard
        if score < g.v2_min:
            return "entry_score_v2_below_threshold"
        tokens = set(audit.get("active_score_tokens") or [])
        missing = g.required_tokens - tokens
        if missing:
            return f"required_token_missing:{','.join(sorted(missing))}"
        if g.exclude_opening_0900_0915:
            ent = str(ev.get("entry_time") or ev.get("event_time") or "")
            if ent and _entry_in_opening_exclude(ent):
                return "opening_0900_0915_excluded"
        sym = str(ev.get("symbol") or "")
        if g.top_n_per_symbol_day is not None and sym:
            key = (self._day, sym)
            if self._symbol_day_opens[key] >= g.top_n_per_symbol_day:
                return "top_n_per_symbol_day"
        if g.symbol_entry_cooldown_sec and sym:
            ent = str(ev.get("entry_time") or ev.get("event_time") or "")
            ts = self.p71._parse_ts(ent) if hasattr(self.p71, "_parse_ts") else self.p270._parse_ts(ent)
            last = self._last_symbol_entry_ts.get(sym)
            if last is not None and (ts - last) < float(g.symbol_entry_cooldown_sec):
                return "symbol_entry_cooldown"
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
        act._entry_score_v2 = score  # noqa: SLF001
        self.active[sym] = act
        self._symbol_day_opens[(self._day, sym)] += 1
        self._last_symbol_entry_ts[sym] = ts

    def _close(self, act: Any, *, close_price: float, reason: str, entry_score: int) -> None:
        pnl = float(self.p71._pnl_pct(act.trade.entry_price, close_price))
        mfe = 0.0
        mae = 0.0
        if act.rich_ticks:
            pnls = [float(t.get("pnl_pct") or 0) for t in act.rich_ticks]
            mfe = max(pnls) if pnls else 0.0
            mae = abs(min(pnls)) if pnls else 0.0
        self.completed.append(
            CompletedTrade(
                pnl_pct=pnl,
                stop_hit=str(reason) == "stop_hit",
                symbol=str(act.trade.symbol),
                day=self._day,
                entry_score_v2=entry_score,
                mfe_pct=mfe,
                mae_pct=mae,
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
            score, audit = self._score_row(ev)
            self._pending.append((ev, int(score), audit))
        elif et == "rejected" and self._pool_exclude_reason(ev) is None:
            score, audit = self._score_row(ev)
            self._pending.append((ev, int(score), audit))

    def finalize(self, session_end: str) -> None:
        self._flush()
        for sym, act in list(self.active.items()):
            last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
            entry_score = getattr(act, "_entry_score_v2", 0)
            self._close(act, close_price=float(last_px), reason="session_end", entry_score=entry_score)
        self.active.clear()

    def begin_session(self, meta: dict[str, Any]) -> None:
        self._day = meta["day"]
        self._session_id = meta["session_id"]
        self.sym_states = {}
        self.active = {}
        self._pending = []
        self._pending_time = None
        self._ring = PriceRingTracker()
        if AUX_FILTER.get("price_risk_universe"):
            self._universe_syms = self.p270._load_universe_symbols(self._day, price_risk=True)
        else:
            self._universe_syms = set()
        pct = (
            self.guard.daytrade_percentile
            if self.guard.daytrade_stricter
            else float(AUX_FILTER.get("daytrade_percentile") or 0.50)
        )
        if self.guard.daytrade_stricter or AUX_FILTER.get("daytrade_mode") == "on":
            self._daytrade_state = _daytrade_state(self.p270, self._session_id, pct)
        else:
            self._daytrade_state = None
        if self.guard.symbol_cooloff:
            self._cooloff_state = _symbol_cooloff_state(self._session_id)
        else:
            self._cooloff_state = None


def _pf(pnls: list[float]) -> Any:
    wins = sum(p for p in pnls if p > 0)
    loss = abs(sum(p for p in pnls if p < 0))
    if loss <= 0:
        return None if wins <= 0 else "inf"
    return round(wins / loss, 4)


def _metrics(
    trades: list[CompletedTrade],
    reject_mc: int,
    *,
    symbol_breakdown: bool = False,
) -> dict[str, Any]:
    pnls = [t.pnl_pct for t in trades]
    n = len(trades)
    sym_counts = Counter(t.symbol for t in trades)
    top_sym, top_n = ("", 0)
    if sym_counts:
        top_sym, top_n = sym_counts.most_common(1)[0]
    concentration = round(100.0 * top_n / n, 2) if n else 0.0
    base: dict[str, Any] = {
        "trade_count": n,
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
    if symbol_breakdown and trades:
        by_sym: dict[str, list[CompletedTrade]] = defaultdict(list)
        for t in trades:
            by_sym[t.symbol].append(t)
        rows = []
        for sym, ts in sorted(by_sym.items(), key=lambda x: (-len(x[1]), x[0])):
            sp = [x.pnl_pct for x in ts]
            rows.append(
                {
                    "symbol": sym,
                    "trade_count": len(ts),
                    "profit_factor": _pf(sp),
                    "total_pnl_pct": round(sum(sp), 4),
                    "avg_pnl_pct": round(sum(sp) / len(sp), 6),
                    "avg_mfe_pct": round(sum(x.mfe_pct for x in ts) / len(ts), 4),
                    "avg_mae_pct": round(sum(x.mae_pct for x in ts) / len(ts), 4),
                }
            )
        base["symbol_breakdown"] = rows
    return base


def _zero_trade_rates(calendar_days: list[str], daily: dict[str, dict[str, Any]], scenario_ids: list[str]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for sid in scenario_ids:
        z = sum(1 for d in calendar_days if daily.get(d, {}).get(sid, {}).get("trade_count", 0) == 0)
        out[sid] = {
            "zero_trade_days": z,
            "calendar_days": len(calendar_days),
            "zero_trade_day_rate": round(z / len(calendar_days), 4) if calendar_days else None,
        }
    return out


def _qualifies(m: dict[str, Any], *, baseline_a_zero_rate: float, d_trade_count: int) -> dict[str, Any]:
    pf = m.get("profit_factor")
    try:
        pf_ok = pf is not None and pf != "inf" and float(pf) > 1.05
    except (TypeError, ValueError):
        pf_ok = False
    pnl_ok = float(m.get("total_pnl_pct") or 0) > 0
    zr = m.get("zero_trade_day_rate")
    zero_ok = zr is not None and float(zr) < float(baseline_a_zero_rate)
    tc = int(m.get("trade_count") or 0)
    not_overtrade = 5 <= tc <= OVERTRADE_MAX_TRADES and tc < int(d_trade_count * 0.25)
    return {
        "pf_gt_1_05": pf_ok,
        "total_pnl_gt_0": pnl_ok,
        "zero_trade_rate_improved_vs_A": zero_ok,
        "trade_count_not_excessive": not_overtrade,
        "passes_all": pf_ok and pnl_ok and zero_ok and not_overtrade,
    }


def main() -> int:
    p270 = _bootstrap()
    p71 = _load_p71()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2

    score_points = dict(SCORE_POINTS_V2)
    sessions, skipped = _discover_sessions(p270)
    duration_p66 = _live_duration_p66(p270, sessions)
    scenario_defs = _scenario_definitions(duration_p66)
    scenario_ids = list(scenario_defs.keys())

    print(f"sessions={len(sessions)} duration_p66={duration_p66} scenarios={len(scenario_ids)}", flush=True)

    sims = {
        sid: ScenarioSim(scenario_defs[sid], score_points, p71, p270) for sid in scenario_ids
    }

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

    calendar_days = sorted({s["day"] for s in sessions})
    overall: dict[str, dict[str, Any]] = {}
    daily: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    target_days: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)

    for sid, sim in sims.items():
        sym_only = scenario_defs[sid].guard.symbol_metrics_only
        overall[sid] = _metrics(
            sim.completed,
            sim.max_concurrent_reject_count,
            symbol_breakdown=sym_only,
        )
        overall[sid]["reject_reason_counts"] = dict(sim.reject_reason_counts.most_common(20))
        for day in calendar_days:
            td = [t for t in sim.completed if t.day == day]
            daily[day][sid] = _metrics(td, sim.max_concurrent_reject_count)
        for day in TARGET_DAYS:
            td = [t for t in sim.completed if t.day == day]
            target_days[sid][day] = _metrics(td, sim.max_concurrent_reject_count)

    zero_rates = _zero_trade_rates(calendar_days, daily, scenario_ids)
    for sid in scenario_ids:
        overall[sid]["zero_trade_day_rate"] = zero_rates[sid]["zero_trade_day_rate"]
        overall[sid]["zero_trade_days"] = zero_rates[sid]["zero_trade_days"]

    d_trades = int(overall.get("D", {}).get("trade_count") or 0)
    a_zero = float(overall.get("A", {}).get("zero_trade_day_rate") or 1.0)
    qualifications = {
        sid: _qualifies(overall[sid], baseline_a_zero_rate=a_zero, d_trade_count=d_trades)
        for sid in scenario_ids
        if sid not in ("A", "D9")
    }
    passing = [sid for sid, q in qualifications.items() if q.get("passes_all")]

    report = {
        "phase": 294,
        "mode": "score_fix_with_overtrade_guard_review",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraints": {
            "review_only": True,
            "production_logic_changes_forbidden": True,
            "entry_exit_logic_unchanged": True,
            "sessions": 34,
            "date_range": {"start": DATE_START, "end": DATE_END},
        },
        "phase293_d_baseline": {
            "duration_live_p66": duration_p66,
            "trade_count": d_trades,
            "profit_factor": overall.get("D", {}).get("profit_factor"),
            "total_pnl_pct": overall.get("D", {}).get("total_pnl_pct"),
        },
        "guard_parameters": {
            "top_n_per_symbol_day": TOP_N_PER_SYMBOL_DAY,
            "symbol_entry_cooldown_sec": SYMBOL_COOLDOWN_SEC,
            "daytrade_stricter_percentile": 0.65,
            "overtrade_max_trades_threshold": OVERTRADE_MAX_TRADES,
        },
        "scenarios": {
            sid: {
                "id": sid,
                "label": scenario_defs[sid].label,
                "hbrecent_pregate": scenario_defs[sid].hbrecent_pregate,
                "duration_p66": scenario_defs[sid].duration_p66,
                "guard": {
                    "v2_min": scenario_defs[sid].guard.v2_min,
                    "required_tokens": sorted(scenario_defs[sid].guard.required_tokens),
                    "daytrade_stricter": scenario_defs[sid].guard.daytrade_stricter,
                    "top_n_per_symbol_day": scenario_defs[sid].guard.top_n_per_symbol_day,
                    "symbol_entry_cooldown_sec": scenario_defs[sid].guard.symbol_entry_cooldown_sec,
                    "exclude_opening_0900_0915": scenario_defs[sid].guard.exclude_opening_0900_0915,
                    "symbol_cooloff": scenario_defs[sid].guard.symbol_cooloff,
                    "symbol_metrics_only": scenario_defs[sid].guard.symbol_metrics_only,
                },
            }
            for sid in scenario_ids
        },
        "sessions": {"count": len(sessions), "skipped": skipped},
        "overall": overall,
        "target_days_live": dict(target_days),
        "daily_by_scenario": {d: dict(v) for d, v in daily.items()},
        "zero_trade_rates": zero_rates,
        "qualification_criteria": {
            "profit_factor_gt": 1.05,
            "total_pnl_pct_gt": 0,
            "zero_trade_day_rate_lt_A": a_zero,
            "trade_count_range": f"5..{OVERTRADE_MAX_TRADES} and <25% of D",
        },
        "qualifications": qualifications,
        "scenarios_passing_all_criteria": passing,
        "verdict": {
            "D_overtrade_confirmed": d_trades > OVERTRADE_MAX_TRADES,
            "passing_scenarios": passing,
            "best_pf_among_passing": (
                max(
                    (
                        (sid, float(overall[sid].get("profit_factor") or 0))
                        for sid in passing
                        if overall[sid].get("profit_factor") not in (None, "inf")
                    ),
                    key=lambda x: x[1],
                    default=None,
                )
            ),
            "summary": _verdict_summary(overall, passing, d_trades, a_zero),
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    for sid in scenario_ids:
        m = overall[sid]
        print(
            f"  {sid}: trades={m['trade_count']} PF={m.get('profit_factor')} "
            f"PnL={m.get('total_pnl_pct')} zero={m.get('zero_trade_day_rate')}",
            flush=True,
        )
    print(f"passing={passing}", flush=True)
    return 0


def _verdict_summary(
    overall: dict[str, dict[str, Any]],
    passing: list[str],
    d_trades: int,
    a_zero: float,
) -> str:
    if passing:
        best = max(passing, key=lambda s: float(overall[s].get("profit_factor") or 0))
        m = overall[best]
        return (
            f"Phase293-D trades={d_trades} is overtrade (PF={overall.get('D', {}).get('profit_factor')}). "
            f"Guards passing all criteria: {passing}. Best among them: {best} "
            f"(trades={m.get('trade_count')}, PF={m.get('profit_factor')}, PnL={m.get('total_pnl_pct')}, "
            f"zero_rate={m.get('zero_trade_day_rate')} vs A={a_zero})."
        )
    return (
        f"Phase293-D trades={d_trades} overtrade confirmed. No D1-D8 scenario met all criteria "
        f"(PF>1.05, PnL>0, zero_rate<A, trades 5-{OVERTRADE_MAX_TRADES}). "
        "See per-scenario metrics and D9 symbol breakdown for next tuning."
    )


if __name__ == "__main__":
    raise SystemExit(main())
