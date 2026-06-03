#!/usr/bin/env python3
"""
Phase266: Compare quality gate vs entry_score_v2 gate scenarios (review only).

Simulates cap=3 with chronological event_time groups on live+push_replay events.
Replay trades.csv lacks score/quality fields — reported separately as trade-filter only.

Output: kabu_native/results/reports/phase266_quality_replacement_score_gate.json
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
SMALL_PAPER = REPO / "kabu_native" / "results" / "small_paper"
REPLAY_ROOT = REPO / "kabu_native" / "results" / "replay"
OUT = REPO / "kabu_native" / "results" / "reports" / "phase266_quality_replacement_score_gate.json"

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

SCENARIOS = (
    {"id": "1_baseline_quality_ge70", "label": "quality>=0.70 (current)", "quality_min": 0.70, "score_v2_min": None},
    {"id": "2_score_v2_ge3", "label": "quality ignored + entry_score_v2>=3", "quality_min": None, "score_v2_min": 3},
    {"id": "3_score_v2_ge4", "label": "quality ignored + entry_score_v2>=4", "quality_min": None, "score_v2_min": 4},
    {"id": "4_score_v2_ge5", "label": "quality ignored + entry_score_v2>=5", "quality_min": None, "score_v2_min": 5},
    {"id": "5_quality_ge70_score_v2_ge4", "label": "quality>=0.70 + entry_score_v2>=4", "quality_min": 0.70, "score_v2_min": 4},
    {"id": "6_quality_ge70_score_v2_ge5", "label": "quality>=0.70 + entry_score_v2>=5", "quality_min": 0.70, "score_v2_min": 5},
)


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


def _enrich(ev: dict[str, Any]) -> dict[str, Any]:
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    q = _float(ev.get("continuation_quality_score"))
    sf = compute_entry_expectancy_score_fields(trade=ev)
    v2 = _int(sf.get("entry_expectancy_score_v2"))
    v1 = _int(sf.get("entry_expectancy_score"))
    return {"quality": q, "entry_score": v1, "entry_score_v2": v2}


def _passes_scenario(ev: dict[str, Any], scenario: dict[str, Any]) -> bool:
    sc = _enrich(ev)
    q = sc["quality"]
    v2 = sc["entry_score_v2"]
    qmin = scenario.get("quality_min")
    smin = scenario.get("score_v2_min")
    if qmin is not None:
        if q is None or float(q) < float(qmin):
            return False
    if smin is not None:
        if v2 is None or int(v2) < int(smin):
            return False
    return True


def _in_decision_pool(ev: dict[str, Any]) -> bool:
    et = str(ev.get("event_type") or "")
    if et == "accepted":
        return True
    if et == "rejected":
        reason = str(ev.get("gate_reject_reason") or "")
        return reason not in HARD_EXCLUDE_REASONS
    return False


@dataclass
class CompletedTrade:
    pnl_pct: float
    stop_hit: bool
    stream: str = "combined"


def _metrics(pnls: list[float], stops: int) -> dict[str, Any]:
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


class ScenarioSim:
    def __init__(self, scenario: dict[str, Any], p71: Any):
        self.scenario = scenario
        self.p71 = p71
        self.sym_states: dict[str, Any] = {}
        self.active: dict[str, Any] = {}
        self.completed: list[CompletedTrade] = []
        self.max_concurrent_reject_count = 0
        self.gate_reject_count = 0
        self.max_mc_by_stream: dict[str, int] = {}
        self.gate_reject_by_stream: dict[str, int] = {}
        self._pending_time: Optional[str] = None
        self._pending: list[dict[str, Any]] = []
        self._current_stream = "combined"

    def _close(self, act: Any, *, close_time: str, close_price: float, reason: str) -> None:
        pnl = float(self.p71._pnl_pct(act.trade.entry_price, close_price))
        self.completed.append(
            CompletedTrade(
                pnl_pct=pnl,
                stop_hit=str(reason) == "stop_hit",
                stream=self._current_stream,
            )
        )

    def _try_open(self, ev: dict[str, Any]) -> None:
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or ev.get("event_time") or "")
        px = _float(ev.get("current_price")) or _float(ev.get("entry_price")) or 0.0
        if not sym or not ent or px <= 0:
            return
        if not _passes_scenario(ev, self.scenario):
            self.gate_reject_count += 1
            self.gate_reject_by_stream[self._current_stream] = (
                self.gate_reject_by_stream.get(self._current_stream, 0) + 1
            )
            return
        if sym in self.active:
            return
        if len(self.active) >= MAX_POS:
            self.max_concurrent_reject_count += 1
            self.max_mc_by_stream[self._current_stream] = (
                self.max_mc_by_stream.get(self._current_stream, 0) + 1
            )
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
        ordered = sorted(self._pending, key=lambda ev: int(ev.get("message_index") or 0))
        for ev in ordered:
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
                self._close(act, close_time=ent, close_price=float(px), reason=str(reason))
                self.active.pop(sym, None)

        if _in_decision_pool(ev):
            self._pending.append(ev)

    def finalize(self, session_end: str) -> None:
        self._flush()
        for act in list(self.active.values()):
            last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
            self._close(act, close_time=session_end, close_price=float(last_px), reason="session_end")
        self.active.clear()

    def summary(self, *, stream: Optional[str] = None) -> dict[str, Any]:
        trades = self.completed
        if stream is not None:
            trades = [t for t in self.completed if t.stream == stream]
        pnls = [t.pnl_pct for t in trades]
        stops = sum(1 for t in trades if t.stop_hit)
        mc = self.max_concurrent_reject_count
        gr = self.gate_reject_count
        if stream is not None:
            mc = self.max_mc_by_stream.get(stream, 0)
            gr = self.gate_reject_by_stream.get(stream, 0)
        return {
            "scenario_id": self.scenario["id"],
            "label": self.scenario["label"],
            "gate_definition": {
                "quality_min": self.scenario.get("quality_min"),
                "entry_score_v2_min": self.scenario.get("score_v2_min"),
            },
            **_metrics(pnls, stops),
            "max_concurrent_count": mc,
            "gate_reject_count": gr,
        }


def _session_stream(session_id: str, summary: Optional[dict[str, Any]]) -> str:
    sid = session_id.replace("\\", "/")
    base = sid.split("/")[-1].lower()
    mode = str((summary or {}).get("mode") or "").lower()
    source = str((summary or {}).get("source") or "").lower()
    if "live_full_session" in base or "live_session" in base:
        return "live"
    if "push_replay" in base or source in ("push-replay", "push_replay") or "push_replay" in mode:
        return "push_replay"
    if source == "replay" or ("replay" in mode and "push" not in mode and "live" not in mode):
        return "replay"
    if sid.count("/") == 0 and len(sid) == 8 and sid.isdigit():
        return "replay"
    return "other"


def _passes_scenario_trade(row: dict[str, Any], scenario: dict[str, Any]) -> bool:
    q = _float(row.get("continuation_quality_score"))
    v2 = _int(row.get("entry_expectancy_score_v2"))
    qmin = scenario.get("quality_min")
    smin = scenario.get("score_v2_min")
    if qmin is not None:
        if q is None or float(q) < float(qmin):
            return False
    if smin is not None:
        if v2 is None or int(v2) < int(smin):
            return False
    return True


def _trade_level_scenarios(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for scenario in SCENARIOS:
        subset = [r for r in rows if _passes_scenario_trade(r, scenario)]
        pnls = [float(r.get("pnl_pct") or 0) for r in subset]
        stops = sum(1 for r in subset if r.get("stop_hit"))
        out.append(
            {
                "scenario_id": scenario["id"],
                "label": scenario["label"],
                "gate_definition": {
                    "quality_min": scenario.get("quality_min"),
                    "entry_score_v2_min": scenario.get("score_v2_min"),
                },
                **_metrics(pnls, stops),
                "max_concurrent_count": None,
                "gate_reject_count": None,
                "method": "trade_level_counterfactual_no_cap_sim",
            }
        )
    return out


def _load_light_replay_trades(mod: Any, p217: Any, p71: Any) -> list[dict[str, Any]]:
    """Fast replay trade load (no p217 book enrich). Used when session has no events."""
    from small_paper.entry_expectancy_score_shadow import compute_entry_expectancy_score_fields

    p238 = _load_module(
        "phase238_p266_lite", "kabu_native/scripts/run_phase238_entry_score_v2_full_history_validation.py"
    )
    rows: list[dict[str, Any]] = []
    for meta in p238.discover_replay_sessions(SMALL_PAPER, mod, p71):
        if meta.get("stream") != "replay":
            continue
        sid = meta["session_id"]
        sdir = SMALL_PAPER / sid
        if _load_events(sdir):
            continue
        trades = p217._load_session_full_trades(mod, sid, p71)
        accept_by_key: dict[tuple[str, str], dict[str, Any]] = {}
        for ev in _load_events(sdir):
            if str(ev.get("event_type") or "") != "accepted":
                continue
            key = (str(ev.get("symbol") or ""), str(ev.get("entry_time") or ev.get("event_time") or ""))
            accept_by_key[key] = ev
        for tr in trades:
            key = (str(tr.get("symbol") or ""), str(tr.get("entry_time") or ""))
            ev = accept_by_key.get(key, tr)
            tr = dict(tr)
            tr["stream"] = "replay"
            tr["stop_hit"] = str(tr.get("exit_reason") or "") == "stop_hit"
            tr.update(compute_entry_expectancy_score_fields(trade=ev))
            rows.append(tr)
    return rows


def _pick_adoption(results: list[dict[str, Any]]) -> dict[str, Any]:
    baseline = next((r for r in results if r["scenario_id"] == "1_baseline_quality_ge70"), None)
    if not baseline:
        return {"verdict": "no_baseline"}
    b_pf = baseline.get("profit_factor") or 0
    b_avg = baseline.get("avg_pnl_pct") or 0
    b_n = int(baseline.get("trade_count") or 0)
    b_mc = int(baseline.get("max_concurrent_count") or 0)

    candidates = [r for r in results if r["scenario_id"] != "1_baseline_quality_ge70"]
    scored: list[tuple[float, dict[str, Any]]] = []
    for r in candidates:
        pf = r.get("profit_factor")
        if pf is None:
            continue
        avg = float(r.get("avg_pnl_pct") or 0)
        n = int(r.get("trade_count") or 0)
        mc = int(r.get("max_concurrent_count") or 0)
        # Prefer higher PF and avg pnl; penalize tiny sample and huge mc inflation
        uplift = (float(pf) - float(b_pf)) + (avg - float(b_avg)) * 10.0
        if n < max(50, int(b_n * 0.15)):
            uplift -= 2.0
        if mc > b_mc * 1.5:
            uplift -= 0.5
        scored.append((uplift, r))
    scored.sort(key=lambda x: -x[0])
    best = scored[0][1] if scored else None
    beats = False
    if best:
        beats = (
            (best.get("profit_factor") or 0) > (b_pf or 0)
            and (best.get("avg_pnl_pct") or 0) > (b_avg or 0)
            and int(best.get("trade_count") or 0) >= max(50, int(b_n * 0.1))
        )
    return {
        "baseline": {
            "scenario_id": baseline["scenario_id"],
            "profit_factor": b_pf,
            "avg_pnl_pct": b_avg,
            "trade_count": b_n,
            "max_concurrent_count": b_mc,
        },
        "recommended_candidate": best,
        "beats_baseline_pf_and_avg_pnl": beats,
        "verdict": (
            "adopt_entry_score_v2_gate_next_phase"
            if beats and best and best["scenario_id"] in ("3_score_v2_ge4", "4_score_v2_ge5", "6_quality_ge70_score_v2_ge5")
            else "keep_quality_gate_or_refine"
        ),
        "rationale": (
            "Recommend replacing quality-only gate when a score_v2 scenario beats baseline on PF and avg_pnl "
            "with sufficient trade_count. Implementation not applied in Phase266 (review only)."
        ),
    }


def main() -> int:
    _bootstrap()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p71 = _load_module("phase71_p266", "kabu_native/scripts/run_phase71_split_momentum_fade_review.py")
    mod = _load_module(
        "phase213c_p266", "kabu_native/scripts/run_phase213c_board_imbalance_cohort_stability_review.py"
    )
    p217 = _load_module(
        "phase217_p266", "kabu_native/scripts/run_phase217_stop_hit_root_cause_review.py"
    )

    streams = ("live", "push_replay", "replay")
    sims = {s["id"]: ScenarioSim(s, p71) for s in SCENARIOS}
    event_sessions_by_stream: dict[str, int] = {st: 0 for st in streams}

    total_event_sessions = 0
    if SMALL_PAPER.is_dir():
        for summary_path in sorted(SMALL_PAPER.rglob("small_paper_summary.json")):
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                summary = {}
            sdir = summary_path.parent
            sid = sdir.relative_to(SMALL_PAPER).as_posix()
            stream = _session_stream(sid, summary)
            if stream not in event_sessions_by_stream:
                continue
            events = _load_events(sdir)
            if not events:
                continue
            event_sessions_by_stream[stream] += 1
            total_event_sessions += 1
            if total_event_sessions % 5 == 0:
                print(f"  event_sessions={total_event_sessions} last={sid}", flush=True)
            session_end = p71._session_end(events)
            for sim in sims.values():
                sim._current_stream = stream
            for ev in sorted(
                events,
                key=lambda e: (
                    _parse_ts(str(e.get("event_time") or "")),
                    int(_float(e.get("message_index")) or 0),
                ),
            ):
                for sim in sims.values():
                    sim.on_row(ev)
            for sim in sims.values():
                sim.finalize(session_end)

    print("loading replay trade fallback (light)...", flush=True)
    replay_trade_rows = _load_light_replay_trades(mod, p217, p71)

    results_by_stream: dict[str, Any] = {}
    for st in streams:
        results_by_stream[st] = {
            "sessions_with_events": event_sessions_by_stream[st],
            "method": "event_cap3_phase71_replay",
            "scenarios": [sims[s["id"]].summary(stream=st) for s in SCENARIOS],
        }
    if replay_trade_rows:
        results_by_stream["replay"]["trade_fallback"] = {
            "sessions": "replay without events",
            "method": "trade_level_counterfactual",
            "scenarios": _trade_level_scenarios(replay_trade_rows),
        }

    combined_results = [sims[s["id"]].summary() for s in SCENARIOS]
    adoption = _pick_adoption(combined_results)

    report = {
        "phase": 266,
        "mode": "quality_replacement_score_gate_review",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraints": {
            "review_only": True,
            "implementation_applied": False,
            "entry_changed": False,
            "universe_changed": False,
            "exit_changed": False,
            "yaml_changed": False,
        },
        "method": {
            "live_push_replay": {
                "population": "small_paper events (accepted + non-hard rejects)",
                "cap": MAX_POS,
                "ordering": "event_time group, message_index order",
                "pnl": "Phase71 structural virtual replay",
                "max_concurrent_count": "simulated rejects when 3 slots full",
            },
            "replay_trade_fallback": {
                "population": "phase217 enriched trades (push_replay/replay, no events)",
                "max_concurrent_count": "null — cap not re-simulated",
            },
            "score_v2": "entry_expectancy_score_shadow (RollingMAE:mid=0)",
        },
        "scenarios": list(SCENARIOS),
        "sessions_with_events": event_sessions_by_stream,
        "replay_trade_fallback_count": len(replay_trade_rows),
        "results_by_stream": results_by_stream,
        "results_combined_event_sim": combined_results,
        "adoption_decision": adoption,
        "next_phase_recommendation": {
            "action": adoption.get("verdict"),
            "candidate": (adoption.get("recommended_candidate") or {}).get("scenario_id"),
            "note": "If adopt_entry_score_v2_gate_next_phase: remove quality>=0.70, add entry_score_v2 threshold in exposure_gate (not done in Phase266).",
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {OUT}", flush=True)
    print(f"adoption={adoption.get('verdict')}", flush=True)
    for st in streams:
        sc = results_by_stream[st]["scenarios"]
        base = next(x for x in sc if x["scenario_id"] == "1_baseline_quality_ge70")
        best4 = next(x for x in sc if x["scenario_id"] == "3_score_v2_ge4")
        print(
            f"  {st}: n1={base['trade_count']} pf1={base['profit_factor']} "
            f"n4={best4['trade_count']} pf4={best4['profit_factor']}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
