#!/usr/bin/env python3
"""
Phase290: entry_score_v2 reweight scenario review (review only).

Compare score-point variants with v2>=5 gate fixed on Phase289 sessions (46).
Output: kabu_native/results/reports/phase290_entry_score_v2_reweight_review.json
"""

from __future__ import annotations

import importlib.util
import json
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
PHASE289 = REPO / "kabu_native/results/reports/phase289_entry_score_v2_factor_attribution.json"
OUT = REPO / "kabu_native/results/reports/phase290_entry_score_v2_reweight_review.json"

DATE_START = 20260518
DATE_END = 20260605
V2_MIN = 5
MAX_POS = 3
V1_MODE = "legacy"
V1_RATIO = 0.85

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
    spec = importlib.util.spec_from_file_location("phase71_p290", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase71_p290"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_phase289() -> dict[str, Any]:
    if not PHASE289.is_file():
        return {}
    return json.loads(PHASE289.read_text(encoding="utf-8"))


def _base_points() -> dict[str, int]:
    from small_paper.entry_expectancy_score_shadow import SCORE_POINTS_V2

    return dict(SCORE_POINTS_V2)


def _omit(points: dict[str, int], *tokens: str) -> dict[str, int]:
    return {k: v for k, v in points.items() if k not in tokens and v > 0}


def _set(points: dict[str, int], **kw: int) -> dict[str, int]:
    out = dict(points)
    for k, v in kw.items():
        if v <= 0:
            out.pop(k, None)
        else:
            out[k] = v
    return {k: v for k, v in out.items() if v > 0}


def _build_scenario_i(base: dict[str, int], phase289: dict[str, Any]) -> tuple[dict[str, int], dict[str, Any]]:
    patterns = phase289.get("top_20_patterns") or []
    selected: list[dict[str, Any]] = []
    tokens: set[str] = set()
    for p in patterns:
        pf = p.get("profit_factor")
        if pf is None or pf == "inf":
            continue
        try:
            pf_f = float(pf)
        except (TypeError, ValueError):
            continue
        if pf_f <= 1.05:
            continue
        oc = int(p.get("outcome_count") or 0)
        selected.append(
            {
                "pattern": p.get("pattern"),
                "profit_factor": pf_f,
                "outcome_count": oc,
                "active_tokens": list(p.get("active_tokens") or []),
            }
        )
        for t in p.get("active_tokens") or []:
            tokens.add(str(t))
    points = {t: base[t] for t in sorted(tokens) if t in base}
    meta = {
        "rule": "union of tokens from Phase289 top_20_patterns with PF>1.05",
        "source_patterns": selected,
        "excluded_tokens": sorted(set(base) - set(points)),
    }
    return points, meta


def _scenario_definitions() -> dict[str, dict[str, Any]]:
    base = _base_points()
    phase289 = _load_phase289()
    i_points, i_meta = _build_scenario_i(base, phase289)
    return {
        "A": {
            "label": "現行 (SCORE_POINTS_V2)",
            "score_points": dict(base),
        },
        "B": {
            "label": "HBRecent:no 除外",
            "score_points": _omit(base, "HBRecent:no"),
        },
        "C": {
            "label": "Board:mid 除外",
            "score_points": _omit(base, "Board:mid"),
        },
        "D": {
            "label": "HBRecent:no + Board:mid 除外",
            "score_points": _omit(base, "HBRecent:no", "Board:mid"),
        },
        "E": {
            "label": "Duration:high +2→+1",
            "score_points": _set(base, **{"Duration:high": 1}),
        },
        "F": {
            "label": "Price:high +1→0",
            "score_points": _omit(base, "Price:high"),
        },
        "G": {
            "label": "TV:mid +1→0",
            "score_points": _omit(base, "TV:mid"),
        },
        "H": {
            "label": "Momentum:low +1→+2",
            "score_points": _set(base, **{"Momentum:low": 2}),
        },
        "I": {
            "label": "Phase289 PF>1.05 パターン union のみ",
            "score_points": i_points,
            "phase289_pattern_meta": i_meta,
        },
    }


def _compute_v2(ev: dict[str, Any], score_points: dict[str, int]) -> int:
    from small_paper.entry_expectancy_score_shadow import _feature_token

    score = 0
    for token, pts in score_points.items():
        if pts <= 0:
            continue
        lbl = token.split(":", 1)[0]
        tok = _feature_token(lbl, ev)
        if tok == token:
            score += pts
    return score


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
    if event_count > 500_000:
        return f"event_count>{500_000}"
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


def _price_guard_state() -> Any:
    from small_paper.entry_price_risk_guard import (
        EntryPriceRiskGuardConfig,
        EntryPriceRiskGuardState,
    )

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


class ScenarioSim:
    def __init__(self, scenario_id: str, score_points: dict[str, int], p71: Any, p270: Any):
        self.scenario_id = scenario_id
        self.score_points = score_points
        self.p71 = p71
        self.p270 = p270
        self.sym_states: dict[str, Any] = {}
        self.active: dict[str, Any] = {}
        self.completed: list[CompletedTrade] = []
        self.max_concurrent_reject_count = 0
        self.reject_reason_counts: Counter[str] = Counter()
        self._pending_time: Optional[str] = None
        self._pending: list[dict[str, Any]] = []
        self._day = ""
        self._session_id = ""
        self._universe_syms: set[str] = set()
        self._daytrade_state: Any = None
        self._price_guard = _price_guard_state()

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

    def _v2_fail(self, ev: dict[str, Any]) -> bool:
        return _compute_v2(ev, self.score_points) < V2_MIN

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

    def _try_open(self, ev: dict[str, Any]) -> None:
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = self.p270._float(ev.get("current_price")) or self.p270._float(ev.get("entry_price")) or 0.0
        if not sym or not ent or px <= 0:
            return
        if self._v2_fail(ev):
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
        self.active[sym] = self.p71.ActiveTrade(
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

    def _flush(self) -> None:
        if not self._pending:
            return
        for ev in sorted(self._pending, key=lambda e: int(self.p270._float(e.get("message_index")) or 0)):
            self._try_open(ev)
        self._pending = []

    def _pool_exclude_reason(self, ev: dict[str, Any]) -> Optional[str]:
        if str(ev.get("event_type") or "") != "rejected":
            return None
        gr = str(ev.get("gate_reject_reason") or "")
        if gr in self._hard_exclude():
            return gr
        return None

    def on_row(self, ev: dict[str, Any]) -> None:
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
            self._pending.append(ev)
        elif et == "rejected" and self._pool_exclude_reason(ev) is None:
            self._pending.append(ev)

    def finalize(self, session_end: str) -> None:
        self._flush()
        for act in list(self.active.values()):
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
        if AUX_FILTER.get("price_risk_universe"):
            self._universe_syms = self.p270._load_universe_symbols(self._day, price_risk=True)
        else:
            self._universe_syms = set()
        pct = AUX_FILTER.get("daytrade_percentile")
        if AUX_FILTER.get("daytrade_mode") == "on" and pct is not None:
            self._daytrade_state = _daytrade_state(self.p270, self._session_id, float(pct))
        else:
            self._daytrade_state = None


def _metrics(trades: list[CompletedTrade], reject_mc: int) -> dict[str, Any]:
    pnls = [t.pnl_pct for t in trades]
    stops = sum(1 for t in trades if t.stop_hit)
    n = len(pnls)
    sym_counts = Counter(t.symbol for t in trades)
    top_sym, top_n = ("", 0)
    if sym_counts:
        top_sym, top_n = sym_counts.most_common(1)[0]
    concentration = round(100.0 * top_n / n, 2) if n else 0.0
    if n == 0:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "win_rate": None,
            "stop_rate": None,
            "max_concurrent_reject_count": reject_mc,
            "traded_symbol_count": 0,
            "top_symbol": top_sym,
            "top_symbol_trade_share_pct": concentration,
            "symbol_concentration_pct": concentration,
        }
    wins = sum(1 for p in pnls if p > 0)
    wins_sum = sum(p for p in pnls if p > 0)
    loss_sum = abs(sum(p for p in pnls if p < 0))
    pf = round(wins_sum / loss_sum, 4) if loss_sum > 0 else (None if wins_sum <= 0 else float("inf"))
    return {
        "trade_count": n,
        "profit_factor": pf if pf != float("inf") else "inf",
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 6),
        "win_rate": round(wins / n, 4),
        "stop_rate": round(stops / n, 4),
        "max_concurrent_reject_count": reject_mc,
        "traded_symbol_count": len(sym_counts),
        "top_symbol": top_sym,
        "top_symbol_trade_share_pct": concentration,
        "symbol_concentration_pct": concentration,
    }


def _zero_trade_rates(
    calendar_days: list[str],
    daily: dict[str, dict[str, dict[str, Any]]],
    scenario_ids: list[str],
) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for sid in scenario_ids:
        z = sum(1 for d in calendar_days if daily.get(d, {}).get(sid, {}).get("trade_count", 0) == 0)
        out[sid] = {
            "zero_trade_days": z,
            "calendar_days": len(calendar_days),
            "zero_trade_day_rate": round(z / len(calendar_days), 4) if calendar_days else None,
        }
    return out


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
                "delta_profit_factor": round(pf_f - a_pf_f, 4) if pf_f is not None and a_pf_f is not None else None,
                "delta_total_pnl_pct": round(pnl - a_pnl, 4),
                "beats_A_on_pf_and_pnl": both,
                "beats_A_on_pf_only": pf_better and not pnl_better,
                "beats_A_on_pnl_only": pnl_better and not pf_better,
            }
        )
    return {
        "baseline_A": {
            "profit_factor": a_pf,
            "total_pnl_pct": a_pnl,
            "trade_count": a_trades,
        },
        "candidates_beating_A_pf_and_pnl": beats,
        "has_candidate_beating_A": bool(beats),
        "per_scenario": rows,
    }


def main() -> int:
    p270 = _bootstrap()
    p71 = _load_p71()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    scenario_defs = _scenario_definitions()
    scenario_ids = list(scenario_defs.keys())
    sessions, skipped_sessions = _discover_sessions(p270)
    print(
        f"sessions={len(sessions)} skipped={len(skipped_sessions)} scenarios={len(scenario_ids)}",
        flush=True,
    )

    sims: dict[str, ScenarioSim] = {}
    overall: dict[str, dict[str, Any]] = {}

    for scen_id in scenario_ids:
        sim = ScenarioSim(scen_id, scenario_defs[scen_id]["score_points"], p71, p270)
        print(f"scenario={scen_id} start", flush=True)
        for i, meta in enumerate(sessions, 1):
            sdir = SMALL_PAPER / meta["session_id"]
            events = p270._load_events(sdir)
            if not events:
                continue
            session_end = p71._session_end(events)
            sim.begin_session(meta)
            for ev in sorted(
                events,
                key=lambda e: (
                    p270._parse_ts(str(e.get("event_time") or "")),
                    int(p270._float(e.get("message_index")) or 0),
                ),
            ):
                sim.on_row(ev)
            sim.finalize(session_end)
            if i % 10 == 0 or i == len(sessions):
                print(f"  {scen_id} [{i}/{len(sessions)}]", flush=True)
        sims[scen_id] = sim
        overall[scen_id] = _metrics(sim.completed, sim.max_concurrent_reject_count)
        print(
            f"scenario={scen_id} done trades={overall[scen_id]['trade_count']} "
            f"PF={overall[scen_id]['profit_factor']}",
            flush=True,
        )

    calendar_days = sorted({s["day"] for s in sessions})
    daily: dict[str, dict[str, dict[str, Any]]] = {}
    for day in calendar_days:
        daily[day] = {}
        for sid, sim in sims.items():
            td = [t for t in sim.completed if t.day == day]
            daily[day][sid] = _metrics(td, sim.max_concurrent_reject_count)

    zero_rates = _zero_trade_rates(calendar_days, daily, scenario_ids)
    for sid in scenario_ids:
        overall[sid]["zero_trade_day_rate"] = zero_rates[sid]["zero_trade_day_rate"]
        overall[sid]["zero_trade_days"] = zero_rates[sid]["zero_trade_days"]

    comparison = _compare_vs_a(overall)

    report = {
        "phase": 290,
        "mode": "entry_score_v2_reweight_review",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraints": {
            "review_only": True,
            "production_logic_changes_forbidden": True,
            "entry_score_v2_min": V2_MIN,
            "max_concurrent_positions": MAX_POS,
            "auxiliary_filters": "Phase287 A_current (daytrade ON + price-risk universe/guard)",
        },
        "date_range": {"start": DATE_START, "end": DATE_END, "label": "20260518-20260605"},
        "sessions": {
            "count": len(sessions),
            "target_count_phase289": 46,
            "skipped_count": len(skipped_sessions),
            "skipped": skipped_sessions,
            "ids": [s["session_id"] for s in sessions],
        },
        "scenarios": {
            sid: {
                "id": sid,
                "label": scenario_defs[sid]["label"],
                "score_points": scenario_defs[sid]["score_points"],
                **(
                    {"phase289_pattern_meta": scenario_defs[sid]["phase289_pattern_meta"]}
                    if scenario_defs[sid].get("phase289_pattern_meta")
                    else {}
                ),
            }
            for sid in scenario_ids
        },
        "overall": overall,
        "daily_by_scenario": daily,
        "zero_trade_rates": zero_rates,
        "comparison_vs_A": comparison,
        "verdict": {
            "has_candidate_beating_A": comparison["has_candidate_beating_A"],
            "candidates": comparison["candidates_beating_A_pf_and_pnl"],
            "note": (
                "Beats A when both profit_factor and total_pnl_pct exceed baseline A "
                "on replay with v2>=5 fixed."
            ),
        },
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
            f"  {sid}: trades={m['trade_count']} PF={m['profit_factor']} "
            f"PnL={m['total_pnl_pct']} zero_rate={m.get('zero_trade_day_rate')}",
            flush=True,
        )
    print(f"beats_A={comparison['candidates_beating_A_pf_and_pnl']}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
