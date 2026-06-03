#!/usr/bin/env python3
"""
Phase270 (review only): Old vs new integrated system on fast paper event replay.

A (legacy): quality>=0.70, legacy universe symbol sets (analytic)
B (new): entry_score_v2>=4, price-risk universe symbol sets (analytic)

Output: kabu_native/results/reports/phase270_fast_paper_integration_comparison.json
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
REPORTS = REPO / "kabu_native" / "results" / "reports"
OUT = REPO / "kabu_native/results/reports/phase270_fast_paper_integration_comparison.json"

DATE_START = 20260518
DATE_END = 20260603
MAX_POS = 3
V1_MODE = "legacy"
V1_RATIO = 0.85

HARD_EXCLUDE_REASONS = frozenset(
    {
        "daytrade_suitability",
        "symbol_cooloff",
        "risk_cluster_block",
        "daily_loss_guard",
        "wrong_profile",
        "outside_allowed_trading_window",
        "entry_price_risk_guard",
        "low_liquidity_shadow",
        "low_liquidity_shadow_reject",
    }
)

SYSTEM_A = {
    "id": "A_old_system",
    "label": "legacy universe + quality>=0.70",
    "quality_min": 0.70,
    "score_v2_min": None,
    "quality_reject_key": "low_quality",
    "score_reject_key": None,
}

SYSTEM_B = {
    "id": "B_new_system",
    "label": "price-risk universe + entry_score_v2>=4",
    "quality_min": None,
    "score_v2_min": 4,
    "quality_reject_key": None,
    "score_reject_key": "entry_score_v2_below_threshold",
}


def _bootstrap() -> None:
    for p in (REPO / "kabu_native" / "src", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_module(name: str, rel: str) -> Any:
    path = REPO / rel
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _int(val: Any) -> Optional[int]:
    try:
        if val is None or val == "":
            return None
        return int(float(val))
    except (TypeError, ValueError):
        return None


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / gl, 4)


def _day_from_sid(sid: str) -> Optional[str]:
    parts = sid.split("/")
    if parts and len(parts[0]) == 8 and parts[0].isdigit():
        return parts[0]
    return None


def _day_in_range(day: str) -> bool:
    try:
        d = int(day)
        return DATE_START <= d <= DATE_END
    except ValueError:
        return False


def _session_stream(sid: str, summary: dict[str, Any]) -> str:
    base = sid.split("/")[-1].lower()
    source = str((summary or {}).get("source") or "").lower()
    if "live_full_session" in base or "live_session" in base:
        return "live"
    if "push_replay" in base:
        return "push_replay"
    if source == "replay":
        return "replay"
    return "other"


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
    csv_path = session_dir / "small_paper_events.csv"
    if csv_path.is_file():
        with csv_path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    jsonl = session_dir / "small_paper_events.jsonl"
    if jsonl.is_file():
        out: list[dict[str, Any]] = []
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return out
    return []


def _load_universe_symbols(day: str, *, price_risk: bool) -> set[str]:
    name = (
        f"universe_core10_dynamic40_price_risk_am_{day}.csv"
        if price_risk
        else f"universe_core10_dynamic40_am_{day}.csv"
    )
    path = REPORTS / name
    if not path.is_file():
        return set()
    with path.open(encoding="utf-8", newline="") as f:
        return {str(r.get("symbol") or "").strip() for r in csv.DictReader(f) if r.get("symbol")}


def _enrich(ev: dict[str, Any]) -> dict[str, Any]:
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    q = _float(ev.get("continuation_quality_score"))
    sf = compute_entry_expectancy_score_fields(trade=ev)
    v2 = _int(sf.get("entry_expectancy_score_v2"))
    return {"quality": q, "entry_score_v2": v2}


def _gate_fail_reason(ev: dict[str, Any], system: dict[str, Any]) -> Optional[str]:
    sc = _enrich(ev)
    q = sc["quality"]
    v2 = sc["entry_score_v2"]
    qmin = system.get("quality_min")
    smin = system.get("score_v2_min")
    if qmin is not None:
        if q is None or float(q) < float(qmin):
            return str(system.get("quality_reject_key") or "low_quality")
    if smin is not None:
        if v2 is None or int(v2) < int(smin):
            return str(system.get("score_reject_key") or "entry_score_v2_below_threshold")
    return None


def _passes_gate(ev: dict[str, Any], system: dict[str, Any]) -> bool:
    return _gate_fail_reason(ev, system) is None


def _in_decision_pool(ev: dict[str, Any]) -> bool:
    et = str(ev.get("event_type") or "")
    if et == "accepted":
        return True
    if et == "rejected":
        return str(ev.get("gate_reject_reason") or "") not in HARD_EXCLUDE_REASONS
    return False


@dataclass
class CompletedTrade:
    pnl_pct: float
    stop_hit: bool
    symbol: str
    day: str
    stream: str


class SystemSim:
    def __init__(self, system: dict[str, Any], p71: Any):
        self.system = system
        self.p71 = p71
        self.sym_states: dict[str, Any] = {}
        self.active: dict[str, Any] = {}
        self.completed: list[CompletedTrade] = []
        self.max_concurrent_reject_count = 0
        self.reject_reason_counts: Counter[str] = Counter()
        self._pending_time: Optional[str] = None
        self._pending: list[dict[str, Any]] = []
        self._stream = ""
        self._day = ""

    def _close(self, act: Any, *, close_price: float, reason: str) -> None:
        pnl = float(self.p71._pnl_pct(act.trade.entry_price, close_price))
        self.completed.append(
            CompletedTrade(
                pnl_pct=pnl,
                stop_hit=str(reason) == "stop_hit",
                symbol=str(act.trade.symbol),
                day=self._day,
                stream=self._stream,
            )
        )

    def _try_open(self, ev: dict[str, Any]) -> None:
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = _float(ev.get("current_price")) or _float(ev.get("entry_price")) or 0.0
        if not sym or not ent or px <= 0:
            return
        fail = _gate_fail_reason(ev, self.system)
        if fail:
            self.reject_reason_counts[fail] += 1
            return
        if sym in self.active:
            return
        if len(self.active) >= MAX_POS:
            self.max_concurrent_reject_count += 1
            self.reject_reason_counts["max_concurrent"] += 1
            return
        ts = self.p71._parse_ts(ent) if hasattr(self.p71, "_parse_ts") else _parse_ts(ent)
        st = self.sym_states.setdefault(sym, self.p71.SymState())
        comps = self.p71._components(st, ts=ts, price=float(px), ev=ev)
        q = _float(ev.get("continuation_quality_score")) or 0.0
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
        for ev in sorted(self._pending, key=lambda e: int(_float(e.get("message_index")) or 0)):
            self._try_open(ev)
        self._pending = []

    def on_row(self, ev: dict[str, Any]) -> None:
        et = str(ev.get("event_type") or "")
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = _float(ev.get("current_price")) or 0.0
        ev_time = str(ev.get("event_time") or "")
        if self._pending_time is None:
            self._pending_time = ev_time
        if ev_time != self._pending_time:
            self._flush()
            self._pending_time = ev_time

        if et == "candidate" and sym in self.active and px > 0 and ent:
            ts = self.p71._parse_ts(ent) if hasattr(self.p71, "_parse_ts") else _parse_ts(ent)
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

        if _in_decision_pool(ev):
            self._pending.append(ev)

    def finalize(self, session_end: str) -> None:
        self._flush()
        for act in list(self.active.values()):
            last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
            self._close(act, close_price=float(last_px), reason="session_end")
        self.active.clear()


def _metrics_from_trades(trades: list[CompletedTrade]) -> dict[str, Any]:
    pnls = [t.pnl_pct for t in trades]
    stops = sum(1 for t in trades if t.stop_hit)
    n = len(pnls)
    if n == 0:
        return {
            "trade_count": 0,
            "profit_factor": None,
            "total_pnl_pct": 0.0,
            "avg_pnl_pct": None,
            "win_rate": None,
            "stop_rate": None,
        }
    wins = sum(1 for p in pnls if p > 0)
    return {
        "trade_count": n,
        "profit_factor": _pf(pnls),
        "total_pnl_pct": round(sum(pnls), 4),
        "avg_pnl_pct": round(sum(pnls) / n, 6),
        "win_rate": round(wins / n, 4),
        "stop_rate": round(stops / n, 4),
    }


def _filter_trades(
    trades: list[CompletedTrade],
    *,
    day: Optional[str] = None,
    stream: Optional[str] = None,
) -> list[CompletedTrade]:
    out = trades
    if day:
        out = [t for t in out if t.day == day]
    if stream:
        out = [t for t in out if t.stream == stream]
    return out


def _discover_sessions() -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for summary_path in sorted(SMALL_PAPER.rglob("small_paper_summary.json")):
        sid = summary_path.parent.relative_to(SMALL_PAPER).as_posix()
        day = _day_from_sid(sid)
        if not day or not _day_in_range(day):
            continue
        if not _load_events(summary_path.parent):
            continue
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            summary = {}
        found.append(
            {
                "session_id": sid,
                "day": day,
                "stream": _session_stream(sid, summary),
                "summary_mode": summary.get("mode"),
                "summary_source": summary.get("source"),
            }
        )
    return found


def _universe_buckets(day: str) -> dict[str, Any]:
    leg = _load_universe_symbols(day, price_risk=False)
    pr = _load_universe_symbols(day, price_risk=True)
    return {
        "day": day,
        "legacy_csv_available": bool(leg),
        "price_risk_csv_available": bool(pr),
        "legacy_only": sorted(leg - pr),
        "price_risk_only": sorted(pr - leg),
        "overlap": sorted(leg & pr),
        "legacy_count": len(leg),
        "price_risk_count": len(pr),
    }


def _trades_by_universe_bucket(
    trades: list[CompletedTrade], buckets: dict[str, Any]
) -> dict[str, Any]:
    leg_only = set(buckets.get("legacy_only") or [])
    pr_only = set(buckets.get("price_risk_only") or [])
    overlap = set(buckets.get("overlap") or [])
    groups: dict[str, list[CompletedTrade]] = {
        "legacy_only": [],
        "price_risk_only": [],
        "overlap": [],
        "unknown_no_csv": [],
    }
    for t in trades:
        if t.symbol in overlap:
            groups["overlap"].append(t)
        elif t.symbol in leg_only:
            groups["legacy_only"].append(t)
        elif t.symbol in pr_only:
            groups["price_risk_only"].append(t)
        else:
            groups["unknown_no_csv"].append(t)
    return {k: {**_metrics_from_trades(v), "symbols_traded": len({t.symbol for t in v})} for k, v in groups.items()}


def _adoption_decision(overall_a: dict[str, Any], overall_b: dict[str, Any], daily: dict[str, Any]) -> dict[str, Any]:
    a_pf = overall_a.get("profit_factor") or 0
    b_pf = overall_b.get("profit_factor") or 0
    a_pnl = overall_a.get("total_pnl_pct") or 0
    b_pnl = overall_b.get("total_pnl_pct") or 0
    a_n = int(overall_a.get("trade_count") or 0)
    b_n = int(overall_b.get("trade_count") or 0)

    improved = int(daily.get("improved_days") or 0)
    worsened = int(daily.get("worsened_days") or 0)
    zero_b = int(daily.get("zero_trade_days_b") or 0)

    better = (
        isinstance(b_pf, (int, float))
        and isinstance(a_pf, (int, float))
        and b_pf > a_pf
        and b_pnl > a_pnl
        and b_n >= max(100, int(a_n * 0.05))
    )
    if better and improved >= worsened * 1.5:
        conf = "high"
    elif better:
        conf = "medium"
    elif b_pnl > a_pnl and b_pf > a_pf:
        conf = "low"
    else:
        conf = "low"

    risks = []
    if zero_b > 0:
        risks.append(f"B has {zero_b} zero-trade days in comparison window")
    if b_n < a_n * 0.2:
        risks.append("B trade_count much lower than A — opportunity reduction")
    if not daily.get("universe_csv_days"):
        risks.append("Limited universe CSV days — universe bucket metrics partial")
    risks.append("Historical events were generated under mixed production configs; counterfactual sim")
    risks.append("Replay stream largely excluded (few event sessions)")

    next_action = (
        "proceed_shadow_live_with_B_configuration"
        if better and conf in ("high", "medium")
        else "extend_fast_paper_monitoring_before_full_adoption"
    )

    return {
        "new_system_better": bool(better),
        "adoption_confidence": conf,
        "remaining_risks": risks,
        "next_action": next_action,
        "rationale": {
            "A_pf": a_pf,
            "B_pf": b_pf,
            "A_total_pnl_pct": a_pnl,
            "B_total_pnl_pct": b_pnl,
            "A_trade_count": a_n,
            "B_trade_count": b_n,
            "improved_days": improved,
            "worsened_days": worsened,
        },
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p71 = _load_module("phase71_p270", "kabu_native/scripts/run_phase71_split_momentum_fade_review.py")

    sessions = _discover_sessions()
    sim_a = SystemSim(SYSTEM_A, p71)
    sim_b = SystemSim(SYSTEM_B, p71)

    for i, meta in enumerate(sessions, 1):
        sdir = SMALL_PAPER / meta["session_id"]
        events = _load_events(sdir)
        if not events:
            continue
        session_end = p71._session_end(events)
        stream = meta["stream"]
        day = meta["day"]
        for sim in (sim_a, sim_b):
            sim._stream = stream
            sim._day = day
            sim.sym_states = {}
            sim.active = {}
        for ev in sorted(
            events,
            key=lambda e: (
                _parse_ts(str(e.get("event_time") or "")),
                int(_float(e.get("message_index")) or 0),
            ),
        ):
            sim_a.on_row(ev)
            sim_b.on_row(ev)
        sim_a.finalize(session_end)
        sim_b.finalize(session_end)
        if i % 5 == 0:
            print(f"  sessions={i}/{len(sessions)}", flush=True)

    overall_a = _metrics_from_trades(sim_a.completed)
    overall_b = _metrics_from_trades(sim_b.completed)
    overall_a["max_concurrent_count"] = sim_a.max_concurrent_reject_count
    overall_b["max_concurrent_count"] = sim_b.max_concurrent_reject_count
    overall_a["reject_reason_counts"] = dict(sim_a.reject_reason_counts)
    overall_b["reject_reason_counts"] = dict(sim_b.reject_reason_counts)

    by_stream: dict[str, Any] = {}
    for st in ("live", "push_replay", "replay", "other"):
        ta = _filter_trades(sim_a.completed, stream=st)
        tb = _filter_trades(sim_b.completed, stream=st)
        if not ta and not tb:
            continue
        by_stream[st] = {
            "A": {**_metrics_from_trades(ta), "max_concurrent_count": sim_a.max_concurrent_reject_count},
            "B": {**_metrics_from_trades(tb), "max_concurrent_count": sim_b.max_concurrent_reject_count},
            "session_count": sum(1 for s in sessions if s["stream"] == st),
        }

    days = sorted({s["day"] for s in sessions})
    daily_rows: list[dict[str, Any]] = []
    improved = worsened = unchanged = zero_a = zero_b = 0
    for day in days:
        ta = _filter_trades(sim_a.completed, day=day)
        tb = _filter_trades(sim_b.completed, day=day)
        ma = _metrics_from_trades(ta)
        mb = _metrics_from_trades(tb)
        if ma["trade_count"] == 0:
            zero_a += 1
        if mb["trade_count"] == 0:
            zero_b += 1
        verdict = "unchanged"
        if mb["trade_count"] and ma["trade_count"]:
            if (mb.get("total_pnl_pct") or 0) > (ma.get("total_pnl_pct") or 0):
                verdict = "improved"
                improved += 1
            elif (mb.get("total_pnl_pct") or 0) < (ma.get("total_pnl_pct") or 0):
                verdict = "worsened"
                worsened += 1
            else:
                unchanged += 1
        elif mb["trade_count"] and not ma["trade_count"]:
            verdict = "improved"
            improved += 1
        elif ma["trade_count"] and not mb["trade_count"]:
            verdict = "worsened"
            worsened += 1
        daily_rows.append({"day": day, "verdict": verdict, "A": ma, "B": mb})

    universe_days = []
    universe_trade_impact: dict[str, Any] = {}
    for day in days:
        b = _universe_buckets(day)
        if b["legacy_csv_available"] or b["price_risk_csv_available"]:
            universe_days.append(day)
        tb = _filter_trades(sim_b.completed, day=day)
        universe_trade_impact[day] = {
            "buckets": b,
            "B_trades_by_bucket": _trades_by_universe_bucket(tb, b),
        }

    daily_summary = {
        "days": daily_rows,
        "improved_days": improved,
        "worsened_days": worsened,
        "unchanged_days": unchanged,
        "zero_trade_days_a": zero_a,
        "zero_trade_days_b": zero_b,
        "universe_csv_days": universe_days,
    }

    adoption = _adoption_decision(overall_a, overall_b, daily_summary)

    report = {
        "phase": 270,
        "mode": "fast_paper_integration_comparison",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraints": {
            "review_only": True,
            "code_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "production_logic_changes_forbidden": True,
        },
        "method": {
            "engine": "Phase71 virtual exit replay on small_paper events (Phase243/266 family)",
            "date_range": [DATE_START, DATE_END],
            "cap": MAX_POS,
            "systems": {"A": SYSTEM_A, "B": SYSTEM_B},
            "note": (
                "Universe close>=300 effect is analyzed via saved universe CSV symbol sets; "
                "ENTRY gate A vs B applied uniformly on same historical candidate stream."
            ),
        },
        "sessions": {
            "count": len(sessions),
            "by_stream": dict(Counter(s["stream"] for s in sessions)),
            "session_ids": [s["session_id"] for s in sessions],
        },
        "1_overall_comparison": {"A": overall_a, "B": overall_b, "delta": {
            "profit_factor": (
                round((overall_b.get("profit_factor") or 0) - (overall_a.get("profit_factor") or 0), 4)
                if overall_a.get("profit_factor") is not None and overall_b.get("profit_factor") is not None
                else None
            ),
            "total_pnl_pct": round(
                (overall_b.get("total_pnl_pct") or 0) - (overall_a.get("total_pnl_pct") or 0), 4
            ),
            "trade_count": int(overall_b.get("trade_count") or 0) - int(overall_a.get("trade_count") or 0),
        }},
        "2_daily_comparison": daily_summary,
        "3_by_source": by_stream,
        "4_reject_structure": {
            "A_expected": ["low_quality", "max_concurrent"],
            "B_expected": ["entry_score_v2_below_threshold", "max_concurrent"],
            "A_actual": dict(sim_a.reject_reason_counts),
            "B_actual": dict(sim_b.reject_reason_counts),
        },
        "5_universe_diff_impact": {
            "by_day": universe_trade_impact,
            "aggregate_B_trades": _trades_by_universe_bucket(
                sim_b.completed,
                {
                    "legacy_only": [],
                    "price_risk_only": [],
                    "overlap": [],
                },
            )
            if not universe_days
            else _aggregate_universe_buckets(sim_b.completed, universe_days),
        },
        "6_adoption_decision": adoption,
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    print(f"B_better={adoption['new_system_better']} confidence={adoption['adoption_confidence']}", flush=True)
    return 0


def _aggregate_universe_buckets(trades: list[CompletedTrade], days: list[str]) -> dict[str, Any]:
    groups: dict[str, list[CompletedTrade]] = defaultdict(list)
    for day in days:
        b = _universe_buckets(day)
        for t in _filter_trades(trades, day=day):
            sym = t.symbol
            if sym in set(b["overlap"]):
                groups["overlap"].append(t)
            elif sym in set(b["legacy_only"]):
                groups["legacy_only"].append(t)
            elif sym in set(b["price_risk_only"]):
                groups["price_risk_only"].append(t)
    return {k: _metrics_from_trades(v) for k, v in groups.items()}


if __name__ == "__main__":
    raise SystemExit(main())
