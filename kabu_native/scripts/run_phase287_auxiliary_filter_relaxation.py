#!/usr/bin/env python3
"""
Phase287: Auxiliary filter relaxation review with entry_score_v2>=5 fixed.

Compare A/B/C/D on fast paper replay (20260518-20260603).
Output: kabu_native/results/reports/phase287_auxiliary_filter_relaxation_review.json
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

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase287_auxiliary_filter_relaxation_review.json"

V2_MIN = 5
MAX_POS = 3

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

SCENARIOS: dict[str, dict[str, Any]] = {
    "A_current": {
        "label": "v2>=5 + daytrade ON + price-risk universe/guard",
        "hard_exclude_extra": frozenset({"daytrade_suitability", "entry_price_risk_guard"}),
        "daytrade_mode": "on",
        "daytrade_percentile": 0.50,
        "price_risk_universe": True,
        "price_risk_guard": True,
    },
    "B_daytrade_off": {
        "label": "v2>=5 + daytrade OFF + price-risk ON",
        "hard_exclude_extra": frozenset({"entry_price_risk_guard"}),
        "daytrade_mode": "off",
        "daytrade_percentile": None,
        "price_risk_universe": True,
        "price_risk_guard": True,
    },
    "C_daytrade_relaxed": {
        "label": "v2>=5 + daytrade top35% + price-risk ON",
        "hard_exclude_extra": frozenset({"entry_price_risk_guard"}),
        "daytrade_mode": "relaxed",
        "daytrade_percentile": 0.35,
        "price_risk_universe": True,
        "price_risk_guard": True,
    },
    "D_close300_current": {
        "label": "v2>=5 + close>=300 universe + rest current (same as A)",
        "hard_exclude_extra": frozenset({"daytrade_suitability", "entry_price_risk_guard"}),
        "daytrade_mode": "on",
        "daytrade_percentile": 0.50,
        "price_risk_universe": True,
        "price_risk_guard": True,
    },
}


def _bootstrap() -> Any:
    for p in (REPO / "kabu_native" / "src", REPO / "kabu_native" / "scripts", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    import run_phase270_fast_paper_integration_comparison as p270

    return p270


def _load_p71():
    path = REPO / "kabu_native/scripts/run_phase71_split_momentum_fade_review.py"
    spec = importlib.util.spec_from_file_location("phase71_p287", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["phase71_p287"] = mod
    spec.loader.exec_module(mod)
    return mod


_DAYTRADE_CACHE: dict[tuple[str, float], Any] = {}


def _daytrade_state(
    p270: Any,
    session_id: str,
    percentile: float,
) -> Any:
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


@dataclass
class CompletedTrade:
    pnl_pct: float
    stop_hit: bool
    symbol: str
    day: str


class ScenarioSim:
    def __init__(self, scenario_id: str, scenario: dict[str, Any], p71: Any, p270: Any):
        self.scenario_id = scenario_id
        self.scenario = scenario
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
        return BASE_HARD_EXCLUDE | frozenset(self.scenario.get("hard_exclude_extra") or [])

    def _in_pool(self, ev: dict[str, Any]) -> bool:
        et = str(ev.get("event_type") or "")
        gr = str(ev.get("gate_reject_reason") or "")
        if et == "accepted":
            return True
        if et == "rejected":
            if gr in self._hard_exclude():
                return False
            mode = str(self.scenario.get("daytrade_mode") or "off")
            if mode == "relaxed" and gr == "daytrade_suitability":
                return self._daytrade_passes_relaxed(ev)
            return True
        return False

    def _daytrade_passes_relaxed(self, ev: dict[str, Any]) -> bool:
        """Re-admit historical daytrade rejects only if they pass the relaxed cutoff."""
        if self._daytrade_state is None:
            return False
        return not self._daytrade_state.check(ev).blocked

    def _aux_fail(self, ev: dict[str, Any]) -> Optional[str]:
        sym = str(ev.get("symbol") or "")
        if self.scenario.get("price_risk_universe") and self._universe_syms:
            if sym not in self._universe_syms:
                return "outside_price_risk_universe"
        if self.scenario.get("price_risk_guard"):
            gr = self._price_guard.check(ev)
            if gr.blocked:
                self._price_guard.reject_count += 1
                return "entry_price_risk_guard"
        # Daytrade for C is enforced in _in_pool (marginal rejects only), not on accepted rows.
        return None

    def _v2_fail(self, ev: dict[str, Any]) -> bool:
        sc = self.p270._enrich(ev)
        v2 = sc.get("entry_score_v2")
        return v2 is None or int(v2) < V2_MIN

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

        if et == "candidate" and sym in self.active and px > 0 and ent:
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
                momentum_mode="legacy",
                ratio=0.85,
                allow_session_end=False,
            )
            if sig:
                _, reason, _ = sig
                self._close(act, close_price=float(px), reason=str(reason))
                self.active.pop(sym, None)

        if self._in_pool(ev):
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
        if self.scenario.get("price_risk_universe"):
            self._universe_syms = self.p270._load_universe_symbols(
                self._day, price_risk=True
            )
        else:
            self._universe_syms = set()
        mode = str(self.scenario.get("daytrade_mode") or "off")
        pct = self.scenario.get("daytrade_percentile")
        if mode == "relaxed" and pct is not None:
            self._daytrade_state = _daytrade_state(self.p270, self._session_id, float(pct))
        else:
            self._daytrade_state = None


def _metrics(trades: list[CompletedTrade], reject_mc: int, rejects: Counter) -> dict[str, Any]:
    pnls = [t.pnl_pct for t in trades]
    stops = sum(1 for t in trades if t.stop_hit)
    n = len(pnls)
    by_day: dict[str, int] = Counter(t.day for t in trades)
    if n == 0:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "win_rate": None,
            "stop_rate": None,
            "max_concurrent_count": reject_mc,
            "traded_symbol_count": 0,
            "reject_reason_counts": dict(rejects),
        }
    wins = sum(1 for p in pnls if p > 0)
    wins_sum = sum(p for p in pnls if p > 0)
    loss_sum = abs(sum(p for p in pnls if p < 0))
    pf = round(wins_sum / loss_sum, 4) if loss_sum > 0 else (None if wins_sum <= 0 else float("inf"))
    return {
        "trade_count": n,
        "profit_factor": pf,
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 6),
        "win_rate": round(wins / n, 4),
        "stop_rate": round(stops / n, 4),
        "max_concurrent_count": reject_mc,
        "traded_symbol_count": len({t.symbol for t in trades}),
        "active_day_count": len(by_day),
        "reject_reason_counts": dict(rejects),
    }


def _recommendation(results: dict[str, dict[str, Any]]) -> dict[str, Any]:
    a = results["A_current"]
    b = results["B_daytrade_off"]
    c = results["C_daytrade_relaxed"]
    best_pnl = max(results.items(), key=lambda x: (x[1].get("total_pnl_pct") or -1e9))
    best_pf = max(
        [k for k in results if results[k].get("profit_factor") is not None],
        key=lambda k: results[k].get("profit_factor") or 0,
    )
    trade_up = (b.get("trade_count") or 0) > (a.get("trade_count") or 0)
    pnl_up = (b.get("total_pnl_pct") or 0) > (a.get("total_pnl_pct") or 0)
    return {
        "best_total_pnl_scenario": best_pnl[0],
        "best_pf_scenario": best_pf,
        "B_vs_A_trade_count_delta": int((b.get("trade_count") or 0) - (a.get("trade_count") or 0)),
        "B_vs_A_pnl_delta": round((b.get("total_pnl_pct") or 0) - (a.get("total_pnl_pct") or 0), 4),
        "daytrade_off_increases_trades": trade_up,
        "daytrade_off_improves_pnl": pnl_up,
        "note": "entry_score_v2_min=5 fixed in all scenarios; score4 never admitted",
        "suggested_next_step": (
            "trial daytrade_suitability_enabled=false in shadow if B improves PnL without PF collapse"
            if pnl_up and (b.get("profit_factor") or 0) >= (a.get("profit_factor") or 0) * 0.95
            else "keep daytrade ON; try C relaxed threshold only if C beats B on PnL and PF"
        ),
    }


def main() -> int:
    p270 = _bootstrap()
    p71 = _load_p71()
    OUT.parent.mkdir(parents=True, exist_ok=True)

    sessions = p270._discover_sessions()
    # D is identical to A on replay (same pool + filters); computed from A after run.
    run_ids = [k for k in SCENARIOS if k != "D_close300_current"]
    sims = {sid: ScenarioSim(sid, SCENARIOS[sid], p71, p270) for sid in run_ids}

    for sid in run_ids:
        sim = sims[sid]
        print(f"  scenario={sid} start", flush=True)
        for i, meta in enumerate(sessions, 1):
            sdir = p270.SMALL_PAPER / meta["session_id"]
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
            if i % 5 == 0:
                print(f"    {sid} sessions={i}/{len(sessions)}", flush=True)
        m = _metrics(sim.completed, sim.max_concurrent_reject_count, sim.reject_reason_counts)
        print(f"  scenario={sid} done trades={m['trade_count']} PF={m['profit_factor']}", flush=True)

    overall = {
        sid: _metrics(sim.completed, sim.max_concurrent_reject_count, sim.reject_reason_counts)
        for sid, sim in sims.items()
    }
    overall["D_close300_current"] = {
        **overall["A_current"],
        "note": "Identical to A_current on replay (close>=300 via price-risk universe)",
    }

    calendar_days = sorted({s["day"] for s in sessions})
    daily: dict[str, dict[str, Any]] = {}
    for day in calendar_days:
        daily[day] = {}
        for sid in run_ids:
            td = [t for t in sims[sid].completed if t.day == day]
            daily[day][sid] = _metrics(
                td, sims[sid].max_concurrent_reject_count, Counter()
            )
        if "A_current" in daily[day]:
            daily[day]["D_close300_current"] = daily[day]["A_current"]

    zero_rates = {}
    for sid in SCENARIOS:
        src = "A_current" if sid == "D_close300_current" else sid
        z = sum(1 for d in calendar_days if daily.get(d, {}).get(src, {}).get("trade_count", 0) == 0)
        zero_rates[sid] = {
            "zero_trade_days": z,
            "calendar_days": len(calendar_days),
            "zero_trade_rate": round(z / len(calendar_days), 4) if calendar_days else None,
        }

    report = {
        "phase": 287,
        "mode": "auxiliary_filter_relaxation_review",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraints": {
            "entry_score_v2_min": V2_MIN,
            "score4_never_admitted": True,
            "expectancy_gate_via_v2": True,
            "review_only": True,
        },
        "method": {
            "engine": "Phase71 replay + Phase270 session scan",
            "date_range": [p270.DATE_START, p270.DATE_END],
            "sessions": len(sessions),
            "note": (
                "A/D: historical daytrade+price_risk rejects excluded from pool; "
                "B: all historical daytrade rejects re-enter pool; "
                "C: accepted rows unchanged + daytrade rejects passing relaxed vol_liq cutoff only; "
                "price_risk guard applied at open for all scenarios"
            ),
        },
        "scenarios": {
            k: {
                **{kk: (sorted(vv) if isinstance(vv, frozenset) else vv) for kk, vv in v.items()},
                "id": k,
            }
            for k, v in SCENARIOS.items()
        },
        "1_overall": overall,
        "2_daily_by_scenario": daily,
        "3_zero_trade_rates": zero_rates,
        "4_recommendation": _recommendation(overall),
        "phase274_reference_B": {"profit_factor": 1.0445, "total_pnl_pct": 18.4405, "trade_count": 5111},
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}")
    for sid, m in overall.items():
        print(f"  {sid}: trades={m['trade_count']} PF={m['profit_factor']} PnL={m['total_pnl_pct']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
