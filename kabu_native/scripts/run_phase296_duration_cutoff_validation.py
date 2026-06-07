#!/usr/bin/env python3
"""
Phase296: Duration:high cutoff validation (review only).

HBRecent pre-gate fixed; compare Duration p66 cutoffs only.
Output: kabu_native/results/reports/phase296_duration_cutoff_validation.json
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
OUT = REPO / "kabu_native/results/reports/phase296_duration_cutoff_validation.json"

DATE_START = 20260518
DATE_END = 20260605
TARGET_DAYS = ("20260604", "20260605")
V2_MIN = 5
MAX_POS = 3
V1_MODE = "legacy"
V1_RATIO = 0.85
REJECT_REPLAY_MAX_EVENTS = 500_000
JST = ZoneInfo("Asia/Tokyo")
CUTOFFS = (406, 12, 19, 28, 40, 60, 100, 200)
OVERTRADE_MAX = 1500

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
    spec = importlib.util.spec_from_file_location("phase71_p296", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase71_p296"] = mod
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
    duration_p66: float,
    ring: PriceRingTracker,
    p270: Any,
) -> tuple[int, dict[str, Any]]:
    work = dict(ev)
    hb = ring.hbrecent(work, p270)
    work["entry_high_break_recent"] = hb
    active = _active_tokens(work, score_points, duration_p66)
    score = sum(score_points[t] for t, on in active.items() if on)
    return score, {
        "entry_score_v2": score,
        "active_score_tokens": [t for t, on in active.items() if on],
        "entry_high_break_recent": hb,
        "duration_high": active.get("Duration:high", False),
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


class CutoffSim:
    def __init__(self, cutoff: float, score_points: dict[str, int], p71: Any, p270: Any):
        self.cutoff = cutoff
        self.cutoff_id = str(int(cutoff)) if cutoff == int(cutoff) else str(cutoff)
        self.score_points = score_points
        self.p71 = p71
        self.p270 = p270
        self.sym_states: dict[str, Any] = {}
        self.active: dict[str, Any] = {}
        self.completed: list[CompletedTrade] = []
        self.max_concurrent_reject_count = 0
        self.reject_reason_counts: Counter[str] = Counter()
        self.score_appearance: Counter[int] = Counter()
        self.duration_high_hits = 0
        self._pending_time: Optional[str] = None
        self._pending: list[tuple[dict[str, Any], int, dict[str, Any]]] = []
        self._day = ""
        self._session_id = ""
        self._universe_syms: set[str] = set()
        self._daytrade_state: Any = None
        self._price_guard = _price_guard_state()
        self._ring = PriceRingTracker()
        self._appearance_start: Counter[int] = Counter()

    def _hard_exclude(self) -> frozenset[str]:
        return BASE_HARD_EXCLUDE | frozenset(AUX_FILTER.get("hard_exclude_extra") or [])

    def _pool_exclude(self, ev: dict[str, Any]) -> Optional[str]:
        if str(ev.get("event_type") or "") != "rejected":
            return None
        gr = str(ev.get("gate_reject_reason") or "")
        if gr in self._hard_exclude():
            return gr
        return None

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
            )
        )

    def _flush(self) -> None:
        if not self._pending:
            return
        for item in sorted(self._pending, key=lambda x: int(self.p270._float(x[0].get("message_index")) or 0)):
            self._try_open(item)
        self._pending = []

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
                self._close(act, close_price=float(px), reason=str(reason))
                self.active.pop(sym, None)

        elif et == "accepted":
            score, audit = _compute_v2(ev, score_points=self.score_points, duration_p66=self.cutoff, ring=self._ring, p270=self.p270)
            self.score_appearance[score] += 1
            if audit.get("duration_high"):
                self.duration_high_hits += 1
            self._pending.append((ev, score, audit))
        elif et == "rejected" and self._pool_exclude(ev) is None:
            score, audit = _compute_v2(ev, score_points=self.score_points, duration_p66=self.cutoff, ring=self._ring, p270=self.p270)
            self.score_appearance[score] += 1
            if audit.get("duration_high"):
                self.duration_high_hits += 1
            self._pending.append((ev, score, audit))

    def finalize(self, session_end: str) -> None:
        self._flush()
        for sym, act in list(self.active.items()):
            last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
            self._close(act, close_price=float(last_px), reason="session_end")
        self.active.clear()

    def session_appearance_delta(self) -> Counter[int]:
        delta: Counter[int] = Counter()
        for score, cnt in self.score_appearance.items():
            prev = self._appearance_start.get(score, 0)
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
        self._appearance_start = Counter(self.score_appearance)
        if AUX_FILTER.get("price_risk_universe"):
            self._universe_syms = self.p270._load_universe_symbols(self._day, price_risk=True)
        else:
            self._universe_syms = set()
        pct = float(AUX_FILTER.get("daytrade_percentile") or 0.50)
        self._daytrade_state = _daytrade_state(self.p270, self._session_id, pct)


def _metrics(trades: list[CompletedTrade], sim: CutoffSim, appearances: Counter[int]) -> dict[str, Any]:
    pnls = [t.pnl_pct for t in trades]
    n = len(trades)
    sym_counts = Counter(t.symbol for t in trades)
    top_sym, top_n = ("", 0)
    if sym_counts:
        top_sym, top_n = sym_counts.most_common(1)[0]
    conc = round(100.0 * top_n / n, 2) if n else 0.0
    score4 = appearances.get(4, 0)
    score5 = appearances.get(5, 0)
    base: dict[str, Any] = {
        "duration_high_cutoff_p66": sim.cutoff,
        "duration_high_hit_count": sim.duration_high_hits,
        "appearance_count": sum(appearances.values()),
        "score_distribution": {str(k): v for k, v in sorted(appearances.items())},
        "max_score": max(appearances.keys()) if appearances else None,
        "score4_count": score4,
        "score5_count": score5,
        "score_ge4_count": sum(v for k, v in appearances.items() if k >= 4),
        "score_ge5_count": sum(v for k, v in appearances.items() if k >= 5),
        "trade_count": n,
        "max_concurrent_reject_count": sim.max_concurrent_reject_count,
        "traded_symbol_count": len(sym_counts),
        "top_symbol": top_sym,
        "symbol_concentration_pct": conc,
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
        return base
    wins = sum(1 for p in pnls if p > 0)
    stops = sum(1 for t in trades if t.stop_hit)
    wsum = sum(p for p in pnls if p > 0)
    lsum = abs(sum(p for p in pnls if p < 0))
    pf = round(wsum / lsum, 4) if lsum > 0 else (None if wsum <= 0 else "inf")
    base.update(
        {
            "profit_factor": pf if pf != "inf" else "inf",
            "total_pnl_pct": round(sum(pnls), 4),
            "avg_pnl_pct": round(sum(pnls) / n, 6),
            "win_rate": round(wins / n, 4),
            "stop_rate": round(stops / n, 4),
        }
    )
    return base


def _qualifies(m: dict[str, Any], *, baseline_zero_rate: float = 1.0) -> dict[str, Any]:
    pf = m.get("profit_factor")
    try:
        pf_ok = pf is not None and pf != "inf" and float(pf) > 1.05
    except (TypeError, ValueError):
        pf_ok = False
    pnl_ok = float(m.get("total_pnl_pct") or 0) > 0
    zr = m.get("zero_trade_day_rate")
    zero_ok = zr is not None and float(zr) < baseline_zero_rate
    tc = int(m.get("trade_count") or 0)
    score5_ok = int(m.get("score5_count") or 0) > 0
    not_over = 5 <= tc <= OVERTRADE_MAX
    return {
        "score5_restored": score5_ok,
        "pf_gt_1_05": pf_ok,
        "total_pnl_gt_0": pnl_ok,
        "zero_trade_rate_improved": zero_ok,
        "trade_count_not_excessive": not_over,
        "passes_all": score5_ok and pf_ok and pnl_ok and zero_ok and not_over,
    }


def main() -> int:
    p270 = _bootstrap()
    p71 = _load_p71()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2

    score_points = dict(SCORE_POINTS_V2)
    sessions, skipped = _discover_sessions(p270)
    print(f"sessions={len(sessions)} cutoffs={len(CUTOFFS)}", flush=True)

    sims = {c: CutoffSim(float(c), score_points, p71, p270) for c in CUTOFFS}
    target_appearances: dict[float, dict[str, Counter[int]]] = {
        c: {d: Counter() for d in TARGET_DAYS} for c in CUTOFFS
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
            if meta["day"] in TARGET_DAYS and meta.get("stream") == "live":
                for score, cnt in sim.session_appearance_delta().items():
                    target_appearances[sim.cutoff][meta["day"]][score] += cnt
        if i % 5 == 0 or i == len(sessions):
            print(f"  [{i}/{len(sessions)}]", flush=True)

    calendar_days = sorted({s["day"] for s in sessions})
    overall: dict[str, dict[str, Any]] = {}
    daily: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    target_days: dict[str, dict[str, dict[str, Any]]] = {}

    for c, sim in sims.items():
        cid = sim.cutoff_id
        overall[cid] = _metrics(sim.completed, sim, sim.score_appearance)
        for day in calendar_days:
            td = [t for t in sim.completed if t.day == day]
            daily[day][cid] = _metrics(td, sim, Counter())
        z = sum(1 for d in calendar_days if daily.get(d, {}).get(cid, {}).get("trade_count", 0) == 0)
        overall[cid]["zero_trade_days"] = z
        overall[cid]["zero_trade_day_rate"] = round(z / len(calendar_days), 4) if calendar_days else None
        target_days[cid] = {}
        for day in TARGET_DAYS:
            td = [t for t in sim.completed if t.day == day]
            target_days[cid][day] = _metrics(td, sim, target_appearances[c][day])

    quals = {sim.cutoff_id: _qualifies(overall[sim.cutoff_id]) for sim in sims.values()}
    passing = [cid for cid, q in quals.items() if q.get("passes_all")]

    c406 = overall.get("406", {})
    verdict = {
        "cutoff_406_inappropriate": int(c406.get("duration_high_hit_count") or 0) == 0
        and int(c406.get("score5_count") or 0) == 0,
        "cutoff_406_vs_12_score5_delta": int(overall.get("12", {}).get("score5_count", 0))
        - int(c406.get("score5_count", 0)),
        "passing_all_criteria": passing,
        "recommended_cutoff": _pick_recommended(overall, quals),
        "summary": _summary_text(overall, quals, passing),
    }

    report = {
        "phase": 296,
        "mode": "duration_cutoff_validation",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraints": {
            "review_only": True,
            "hbrecent_pregate": True,
            "entry_score_v2_min": V2_MIN,
            "auxiliary_filters": "daytrade ON + price-risk universe/guard",
            "max_concurrent": MAX_POS,
            "duration_only_change": True,
        },
        "cutoffs_compared": list(CUTOFFS),
        "sessions": {"count": len(sessions), "skipped": skipped},
        "overall_by_cutoff": overall,
        "target_live_days": target_days,
        "daily_by_cutoff": {d: dict(v) for d, v in daily.items()},
        "qualification_by_cutoff": quals,
        "adoption_criteria": {
            "score5_restored": True,
            "profit_factor_gt": 1.05,
            "total_pnl_pct_gt": 0,
            "zero_trade_day_rate_lt_baseline": 1.0,
            "trade_count_range": f"5..{OVERTRADE_MAX}",
        },
        "verdict": verdict,
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    for c in CUTOFFS:
        cid = str(int(c))
        m = overall[cid]
        print(
            f"  cutoff={cid}: dur_high={m.get('duration_high_hit_count')} "
            f"score5={m.get('score5_count')} trades={m.get('trade_count')} "
            f"PF={m.get('profit_factor')} PnL={m.get('total_pnl_pct')}",
            flush=True,
        )
    print(f"passing={passing} recommended={verdict.get('recommended_cutoff')}", flush=True)
    return 0


def _pick_recommended(overall: dict[str, dict[str, Any]], quals: dict[str, dict[str, Any]]) -> Optional[str]:
    passing = [cid for cid, q in quals.items() if q.get("passes_all")]
    if passing:
        return max(
            passing,
            key=lambda cid: (
                float(overall[cid].get("profit_factor") or 0),
                float(overall[cid].get("total_pnl_pct") or 0),
            ),
        )
    # fallback: best PF among cutoffs with score5>0 and reasonable trades
    candidates = []
    for cid, m in overall.items():
        if int(m.get("score5_count") or 0) <= 0:
            continue
        try:
            pf = float(m.get("profit_factor") or 0)
        except (TypeError, ValueError):
            pf = 0.0
        candidates.append((cid, pf, float(m.get("total_pnl_pct") or 0), int(m.get("trade_count") or 0)))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[1], x[2], -x[3]), reverse=True)
    return candidates[0][0]


def _summary_text(
    overall: dict[str, dict[str, Any]],
    quals: dict[str, dict[str, Any]],
    passing: list[str],
) -> str:
    c406 = overall.get("406", {})
    parts = [
        f"cutoff=406: Duration:high hits={c406.get('duration_high_hit_count')}, score5={c406.get('score5_count')} — inappropriate for live scale.",
    ]
    for cid in ("12", "19", "28", "40", "60", "100", "200"):
        m = overall.get(cid, {})
        if not m:
            continue
        parts.append(
            f"cutoff={cid}: dur_high={m.get('duration_high_hit_count')} score5={m.get('score5_count')} "
            f"trades={m.get('trade_count')} PF={m.get('profit_factor')} PnL={m.get('total_pnl_pct')}."
        )
    if passing:
        parts.append(f"Pass all adoption criteria: {passing}.")
    else:
        parts.append("No cutoff passes all adoption criteria; see qualification_by_cutoff for partial fits.")
    return " ".join(parts)


if __name__ == "__main__":
    raise SystemExit(main())
