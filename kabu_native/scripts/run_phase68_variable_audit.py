"""
Phase 68: ENTRY/TAKE/EXIT variable implementation audit (read-only).
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
KABU_SRC = ROOT / "kabu_native" / "src"
for p in (str(ROOT), str(KABU_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import importlib.util

def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod

_cqr = _load_module(
    "continuation_quality_ranking",
    KABU_SRC / "research" / "continuation_quality_ranking.py",
)
_sep = _load_module(
    "structural_exit_policies",
    KABU_SRC / "research" / "structural_exit_policies.py",
)
from dataclasses import dataclass

continuation_quality_score = _cqr.continuation_quality_score
tick_from_candidate = _sep.tick_from_candidate
POLICY_COMBINED_STRUCTURAL_EXIT_V1 = _sep.POLICY_COMBINED_STRUCTURAL_EXIT_V1
TRAILING_GIVEBACK_PCT = _sep.TRAILING_GIVEBACK_PCT
VWAP_BREAK_PEAK_PNL = _sep.VWAP_BREAK_PEAK_PNL


@dataclass
class ObserverTrackerConfig:
    take_quality_drop: float = 0.08
    momentum_weaken_ratio: float = 0.85
    favorable_fade_ratio: float = 0.85
    hard_stop_pct: float = 1.20
    structural_exit_policy: str = POLICY_COMBINED_STRUCTURAL_EXIT_V1

SESSION = ROOT / "kabu_native" / "results" / "small_paper" / "20260520" / "live_full_session_080745"
CONFIG_PATH = ROOT / "kabu_native" / "configs" / "small_paper_pilot_q070_cap3_mfe_fav.yaml"

TAKE_QUALITY_DROP = 0.08
MOMENTUM_WEAKEN = 0.85
FAVORABLE_FADE = 0.85
MIN_ENTRY_Q = 0.70


def _parse_ts(s: str) -> float:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _dist(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    vals = sorted(values)
    n = len(vals)

    def pct(p: float) -> float:
        if n == 1:
            return vals[0]
        idx = min(n - 1, max(0, int(p * (n - 1))))
        return round(vals[idx], 6)

    return {
        "n": n,
        "min": round(vals[0], 6),
        "max": round(vals[-1], 6),
        "mean": round(statistics.mean(vals), 6),
        "median": round(statistics.median(vals), 6),
        "p95": pct(0.95),
    }


def _load_events(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            ev = json.loads(line)
            p = ev.get("payload") or ev
            out.append({**p, "event_type": ev.get("event_type") or p.get("event_type")})
    return out


def _field_stats(events: Sequence[Mapping[str, Any]], field: str) -> dict[str, Any]:
    all_v: list[float] = []
    cand_v: list[float] = []
    acc_v: list[float] = []
    for ev in events:
        v = ev.get(field)
        if v is None:
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            continue
        all_v.append(fv)
        et = str(ev.get("event_type") or "")
        if et == "candidate":
            cand_v.append(fv)
        elif et == "accepted":
            acc_v.append(fv)
    return {
        "all_events": _dist(all_v),
        "candidate": _dist(cand_v),
        "accepted": _dist(acc_v),
    }


def _exit_trigger_at_tick(
    t: Mapping[str, Any],
    *,
    peak_q: float,
    peak_mom: float,
    peak_fav: float,
    peak_pnl: float,
    cfg: ObserverTrackerConfig,
) -> Optional[str]:
    q = float(t.get("quality") or 0)
    mom = float(t.get("momentum") or 0)
    fav = float(t.get("favorable") or 0)
    pnl = float(t.get("pnl_pct") or 0)
    if q <= peak_q - cfg.take_quality_drop:
        return "quality_decay_exit"
    if peak_mom > 0 and mom < peak_mom * cfg.momentum_weaken_ratio:
        return "momentum_fade_exit"
    if peak_fav > 0 and fav < peak_fav * cfg.favorable_fade_ratio:
        return "favorable_fade_exit"
    if peak_pnl > VWAP_BREAK_PEAK_PNL and pnl < 0:
        return "vwap_break_exit"
    if peak_pnl > 0 and pnl <= peak_pnl - TRAILING_GIVEBACK_PCT:
        return "mfe_giveback_exit"
    return None


def _replay_exit_breakdown(
    events: Sequence[Mapping[str, Any]],
    trades: Sequence[Mapping[str, str]],
    cfg: ObserverTrackerConfig,
) -> list[dict[str, Any]]:
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for ev in events:
        if ev.get("event_type") != "candidate":
            continue
        sym = str(ev.get("symbol") or "")
        if sym:
            by_sym[sym].append(ev)

    rows: list[dict[str, Any]] = []
    for tr in trades:
        sym = tr["symbol"]
        ent = _parse_ts(tr["entry_time"])
        ex = _parse_ts(tr["close_time"])
        entry_price = float(tr["entry_price"])
        entry_q = float(tr.get("continuation_quality_score") or 0)
        reason = tr["close_reason"]

        cands = [
            e
            for e in by_sym.get(sym, [])
            if ent <= _parse_ts(str(e.get("entry_time") or e.get("timestamp") or "")) <= ex
        ]
        cands.sort(key=lambda e: _parse_ts(str(e.get("entry_time") or e.get("timestamp") or "")))

        rich: list[dict[str, Any]] = []
        peak_q = peak_mom = peak_fav = peak_pnl = 0.0
        exit_tick: Optional[dict[str, Any]] = None
        exit_trigger: Optional[str] = None

        for ev in cands:
            tick = tick_from_candidate(ev, entry_price, entry_q)
            q = float(tick["quality"])
            mom = float(tick["momentum"])
            fav = float(tick["favorable"])
            pnl = float(tick["pnl_pct"])
            peak_q = max(peak_q, q)
            peak_mom = max(peak_mom, mom)
            peak_fav = max(peak_fav, fav)
            peak_pnl = max(peak_pnl, pnl)
            rich.append(tick)
            trig = _exit_trigger_at_tick(
                tick,
                peak_q=peak_q,
                peak_mom=peak_mom,
                peak_fav=peak_fav,
                peak_pnl=peak_pnl,
                cfg=cfg,
            )
            if trig:
                exit_tick = tick
                exit_trigger = trig

        last = rich[-1] if rich else {}
        lt_q = float(last.get("quality") or 0)
        lt_mom = float(last.get("momentum") or 0)
        lt_fav = float(last.get("favorable") or 0)
        lt_pnl = float(last.get("pnl_pct") or 0)

        firing_var = ""
        firing_formula = ""
        if reason == "overlap_replaced_review":
            firing_var = "max_concurrent_positions"
            firing_formula = "new accepted on same symbol while position open"
        elif reason == "session_end":
            firing_var = "session_end"
            firing_formula = "close_all at live_session_end"
        elif reason == "stop_hit":
            firing_var = "hard_stop_pct"
            firing_formula = f"price <= entry * (1 - {cfg.hard_stop_pct}/100)"
        elif exit_trigger and exit_tick:
            firing_var = exit_trigger.replace("_exit", "")
            if exit_trigger == "quality_decay_exit":
                firing_formula = f"q({lt_q}) <= peak_q({peak_q}) - {cfg.take_quality_drop}"
            elif exit_trigger == "momentum_fade_exit":
                firing_formula = (
                    f"mom({lt_mom}) < peak_mom({peak_mom}) * {cfg.momentum_weaken_ratio}"
                )
            elif exit_trigger == "favorable_fade_exit":
                firing_formula = (
                    f"fav({lt_fav}) < peak_fav({peak_fav}) * {cfg.favorable_fade_ratio}"
                )
            elif exit_trigger == "mfe_giveback_exit":
                firing_formula = (
                    f"pnl({lt_pnl}) <= peak_pnl({peak_pnl}) - {TRAILING_GIVEBACK_PCT}"
                )
            elif exit_trigger == "vwap_break_exit":
                firing_formula = f"peak_pnl({peak_pnl}) > {VWAP_BREAK_PEAK_PNL} and pnl({lt_pnl}) < 0"
        else:
            firing_var = "unresolved"
            firing_formula = "no candidate tick matched combined rule at close"

        match = exit_trigger == reason if exit_trigger else reason in (
            "overlap_replaced_review",
            "session_end",
            "stop_hit",
        )

        rows.append(
            {
                "symbol": sym,
                "entry_time": tr["entry_time"],
                "close_time": tr["close_time"],
                "close_reason_csv": reason,
                "replayed_trigger": exit_trigger or "",
                "trigger_matches_csv": match,
                "firing_variable": firing_var,
                "firing_formula_snapshot": firing_formula,
                "tick_count": len(rich),
                "at_exit_quality": round(lt_q, 4),
                "peak_quality": round(peak_q, 4),
                "quality_threshold": round(peak_q - cfg.take_quality_drop, 4),
                "at_exit_momentum": round(lt_mom, 4),
                "peak_momentum": round(peak_mom, 4),
                "momentum_threshold": round(peak_mom * cfg.momentum_weaken_ratio, 4)
                if peak_mom > 0
                else "",
                "at_exit_favorable": round(lt_fav, 4),
                "peak_favorable": round(peak_fav, 4),
                "favorable_threshold": round(peak_fav * cfg.favorable_fade_ratio, 4)
                if peak_fav > 0
                else "",
                "at_exit_pnl_pct": round(lt_pnl, 4),
                "peak_pnl_pct": round(peak_pnl, 4),
                "mfe_giveback_threshold": round(peak_pnl - TRAILING_GIVEBACK_PCT, 4)
                if peak_pnl > 0
                else "",
                "entry_continuation_quality_score": entry_q,
            }
        )
    return rows


def _variable_catalog(cfg: ObserverTrackerConfig) -> list[dict[str, Any]]:
    return [
        {
            "variable": "continuation_quality_score",
            "usage": ["ENTRY", "TAKE", "EXIT", "summary", "review"],
            "implementation_file": "kabu_native/src/research/continuation_quality_ranking.py",
            "functions": ["continuation_components", "continuation_quality_score"],
            "callers": [
                "ExposureGate.evaluate_entry",
                "pilot_runner._enrich_trade_quality",
                "observer_position_tracker.on_tick",
                "structural_exit_policies.tick_from_candidate",
            ],
            "inputs": [
                "momentum_continuation_score",
                "favorable_continuation",
                "max_continuation_duration",
                "max_favorable_excursion_pct (rolling_mfe)",
                "max_adverse_excursion_pct (rolling_mae)",
                "bullish_continuation_score",
                "bearish_accumulation_score",
            ],
            "formula": (
                "q = min(1, 0.30*mom + 0.22*dur_n + 0.20*fav + 0.14*bear_inv "
                "+ 0.14*stability + 0.04*bull); dur_n=min(1,dur/14); "
                "bear_inv=1-min(1,bear); stability=1 if mfe>mae else max(0,0.5+(mfe-mae)/0.5)"
            ),
            "threshold": f"ENTRY accept: q >= {MIN_ENTRY_Q}",
            "risk": "medium",
            "risk_note": "Weighted sum; dur uses favorable_streak not momentum duration name.",
        },
        {
            "variable": "favorable_continuation",
            "usage": ["ENTRY", "TAKE", "EXIT", "summary"],
            "implementation_file": "kabu_native/src/small_paper/live_feature_bridge.py",
            "functions": ["LiveFeatureBridge._resolve_favorable", "mfe_linked_favorable"],
            "callers": ["LiveFeatureBridge.update", "continuation_components"],
            "inputs": ["rolling_mfe_pct", "favorable_mfe_scale=0.003", "favorable_mode=mfe_linked"],
            "formula": "favorable = min(1, max(0, rolling_mfe_pct / 0.003)",
            "threshold": f"EXIT/TAKE fade: fav < peak_fav * {FAVORABLE_FADE}",
            "risk": "high",
            "risk_note": "Phase67 ties favorable to MFE; small MFE caps fav and quality ceiling.",
        },
        {
            "variable": "momentum_continuation_score",
            "usage": ["ENTRY", "TAKE", "EXIT", "summary"],
            "implementation_file": "kabu_native/src/small_paper/live_feature_bridge.py",
            "functions": ["LiveFeatureBridge._momentum_score"],
            "callers": ["LiveFeatureBridge.update", "continuation_components"],
            "inputs": [
                "CurrentPrice",
                "VWAP",
                "price 5 ticks ago",
                "rolling_mfe",
                "rolling_mae abs",
            ],
            "formula": (
                "mom=min(1,max(0,0.40*price_mom+0.25*vwap_part+0.35*mfe_proxy)); "
                "price_mom=min(1,(price-p0)/p0/0.008); vwap_part=min(1,0.5+(price-vwap)/vwap/0.004); "
                "mfe_proxy=min(1,(mfe-0.4*mae)/0.35)"
            ),
            "threshold": f"EXIT/TAKE: mom < peak_mom * {MOMENTUM_WEAKEN}",
            "risk": "medium",
            "risk_note": "5s poll + 5 tick lookback; peak_mom often set on entry tick.",
        },
        {
            "variable": "max_continuation_duration",
            "usage": ["ENTRY (via quality)", "review"],
            "implementation_file": "kabu_native/src/small_paper/live_feature_bridge.py",
            "functions": ["LiveFeatureBridge.update (favorable_streak)"],
            "callers": ["continuation_components dur_n"],
            "inputs": ["consecutive ticks where price>ref or price>recent_low*1.0001"],
            "formula": "max_continuation_duration = max(favorable_streak); dur_n=min(1,dur/14)",
            "threshold": "none direct EXIT; affects q via 0.22*dur_n",
            "risk": "medium",
            "risk_note": "Name differs from Logic Lab max_momentum_continuation_duration.",
        },
        {
            "variable": "adverse_shrinking",
            "usage": ["ENTRY (via quality bear_inv)", "review"],
            "implementation_file": "kabu_native/src/small_paper/live_feature_bridge.py",
            "functions": ["LiveFeatureBridge._adverse_shrinking"],
            "callers": ["snapshot bearish_accumulation_score=1-adverse_shrinking"],
            "inputs": ["price", "running_min", "ref_price", "last_mae_pct", "peak_mae_pct"],
            "formula": (
                "if mae_abs<=0: 1; else 0.5*recovery+0.5*mae_improving; "
                "recovery=min(1,(price-running_min)/max(ref-running_min,1e-9)); "
                "mae_improving = last_mae >= peak_mae*0.98"
            ),
            "threshold": "none direct EXIT",
            "risk": "low",
            "risk_note": "Only 14% weight via bear_inv; not in structural fade rules.",
        },
        {
            "variable": "rolling_mfe_pct",
            "usage": ["ENTRY", "EXIT (indirect via favorable/q)"],
            "implementation_file": "kabu_native/src/small_paper/live_feature_bridge.py",
            "functions": ["LiveFeatureBridge.update"],
            "callers": ["mfe_linked favorable", "bullish_continuation_score", "stability"],
            "inputs": ["running_max", "ref_price"],
            "formula": "rolling_mfe = max(0, (running_max - ref) / ref)",
            "threshold": "favorable = rolling_mfe/0.003",
            "risk": "high",
            "risk_note": "ref resets on 300s tracking_reset_sec; drives Phase67 favorable.",
        },
        {
            "variable": "rolling_mae_pct",
            "usage": ["ENTRY", "EXIT (indirect via q/mom)"],
            "implementation_file": "kabu_native/src/small_paper/live_feature_bridge.py",
            "functions": ["LiveFeatureBridge.update"],
            "callers": ["momentum mfe_proxy", "continuation_components stability"],
            "inputs": ["running_min", "ref_price"],
            "formula": "rolling_mae = min(0, (running_min - ref) / ref)  # <=0",
            "threshold": "none direct",
            "risk": "low",
            "risk_note": "Used in mom proxy and stability branch.",
        },
        {
            "variable": "take_quality_drop",
            "usage": ["TAKE", "EXIT"],
            "implementation_file": "kabu_native/src/small_paper/observer_position_tracker.py",
            "functions": ["_take_reason", "structural_exit_policies.simulate_structural_policy"],
            "callers": ["combined_structural_exit_v1"],
            "inputs": ["continuation_quality at tick", "peak_quality since entry"],
            "formula": f"fire if q <= peak_q - {TAKE_QUALITY_DROP}",
            "threshold": str(TAKE_QUALITY_DROP),
            "risk": "high",
            "risk_note": "21 quality_decay_exit today; TAKE quality_deterioration uses same delta.",
        },
        {
            "variable": "momentum_weaken_ratio",
            "usage": ["TAKE", "EXIT"],
            "implementation_file": "kabu_native/src/small_paper/observer_position_tracker.py",
            "functions": ["_take_reason", "simulate_structural_policy"],
            "callers": ["combined_structural_exit_v1"],
            "inputs": ["momentum_continuation at tick", "peak_momentum"],
            "formula": f"fire if peak_mom>0 and mom < peak_mom * {MOMENTUM_WEAKEN}",
            "threshold": str(MOMENTUM_WEAKEN),
            "risk": "high",
            "risk_note": "64 momentum_fade_exit today; dominant EXIT.",
        },
        {
            "variable": "favorable_fade_ratio",
            "usage": ["TAKE", "EXIT"],
            "implementation_file": "kabu_native/src/small_paper/observer_position_tracker.py",
            "functions": ["_take_reason", "simulate_structural_policy"],
            "callers": ["combined_structural_exit_v1"],
            "inputs": ["favorable_continuation at tick", "peak_favorable"],
            "formula": f"fire if peak_fav>0 and fav < peak_fav * {FAVORABLE_FADE}",
            "threshold": str(FAVORABLE_FADE),
            "risk": "medium",
            "risk_note": "1 favorable_fade_exit today; often blocked by earlier rules.",
        },
        {
            "variable": "quality_decay_exit",
            "usage": ["EXIT"],
            "implementation_file": "kabu_native/src/research/structural_exit_policies.py",
            "functions": ["simulate_structural_policy (combined first check)"],
            "callers": ["ObserverPositionTracker.combined_exit_signal_on_latest_tick"],
            "inputs": ["tick quality", "peak_q", "take_quality_drop"],
            "formula": f"q <= peak_q - {TAKE_QUALITY_DROP}",
            "threshold": "first in combined order",
            "risk": "high",
            "risk_note": "Can fire before momentum_fade on same tick if both true; order matters.",
        },
        {
            "variable": "momentum_fade_exit",
            "usage": ["EXIT"],
            "implementation_file": "kabu_native/src/research/structural_exit_policies.py",
            "functions": ["simulate_structural_policy"],
            "callers": ["combined_exit_signal_on_latest_tick"],
            "inputs": ["tick momentum", "peak_mom", "momentum_weaken_ratio"],
            "formula": f"peak_mom>0 and mom < peak_mom*{MOMENTUM_WEAKEN}",
            "threshold": "2nd in combined",
            "risk": "high",
            "risk_note": "64 trades today.",
        },
        {
            "variable": "favorable_fade_exit",
            "usage": ["EXIT"],
            "implementation_file": "kabu_native/src/research/structural_exit_policies.py",
            "functions": ["simulate_structural_policy"],
            "callers": ["combined_exit_signal_on_latest_tick"],
            "inputs": ["tick favorable", "peak_fav", "favorable_fade_ratio"],
            "formula": f"peak_fav>0 and fav < peak_fav*{FAVORABLE_FADE}",
            "threshold": "3rd in combined",
            "risk": "medium",
            "risk_note": "1 trade today.",
        },
        {
            "variable": "mfe_giveback_exit",
            "usage": ["EXIT"],
            "implementation_file": "kabu_native/src/research/structural_exit_policies.py",
            "functions": ["simulate_structural_policy"],
            "callers": ["combined_exit_signal_on_latest_tick"],
            "inputs": ["tick pnl_pct", "peak_pnl since entry"],
            "formula": f"peak_pnl>0 and pnl <= peak_pnl - {TRAILING_GIVEBACK_PCT}",
            "threshold": f"TRAILING_GIVEBACK_PCT={TRAILING_GIVEBACK_PCT}",
            "risk": "low",
            "risk_note": "0 trades today; needs peak_pnl>0 and 0.18pt giveback.",
        },
        {
            "variable": "overlap_replaced_review",
            "usage": ["EXIT"],
            "implementation_file": "kabu_native/src/small_paper/observer_position_tracker.py",
            "functions": ["close_for_overlap"],
            "callers": ["structural_observer_review replay on new accepted"],
            "inputs": ["max_concurrent_positions=3", "same symbol re-entry"],
            "formula": "close open virtual position when new accepted on occupied symbol",
            "threshold": "not quality/momentum decay",
            "risk": "high",
            "risk_note": "64 trades today; 5s poll causes rapid overlap churn.",
        },
    ]


def main() -> None:
    cfg = ObserverTrackerConfig(structural_exit_policy=POLICY_COMBINED_STRUCTURAL_EXIT_V1)
    events_path = SESSION / "small_paper_events.jsonl"
    trades_path = SESSION / "structural_trades.csv"

    events = _load_events(events_path)
    trades: list[dict[str, str]] = []
    with trades_path.open(encoding="utf-8") as f:
        trades = list(csv.DictReader(f))

    fields = [
        "continuation_quality_score",
        "favorable_continuation",
        "momentum_continuation_score",
        "max_continuation_duration",
        "adverse_shrinking",
        "rolling_mfe_pct",
        "rolling_mae_pct",
    ]
    session_stats = {f: _field_stats(events, f) for f in fields}

    # Quality from components on accepted
    acc_q: list[float] = []
    for ev in events:
        if ev.get("event_type") != "accepted":
            continue
        acc_q.append(continuation_quality_score(ev))

    close_counts = Counter(t["close_reason"] for t in trades)
    exit_rows = _replay_exit_breakdown(events, trades, cfg)

    mismatch = sum(1 for r in exit_rows if not r["trigger_matches_csv"])

    catalog = _variable_catalog(cfg)
    for row in catalog:
        v = row["variable"]
        if v in session_stats:
            row["session_distribution"] = session_stats[v]
        elif v == "continuation_quality_score":
            row["session_distribution"] = {"accepted_recomputed": _dist(acc_q)}

    risk_rank = {
        "high": [
            r["variable"]
            for r in catalog
            if r.get("risk") == "high"
        ],
        "medium": [r["variable"] for r in catalog if r.get("risk") == "medium"],
        "low": [r["variable"] for r in catalog if r.get("risk") == "low"],
        "unknown": [],
    }

    audit = {
        "phase": 68,
        "session_dir": str(SESSION),
        "config_path": str(CONFIG_PATH),
        "structural_exit_policy": POLICY_COMBINED_STRUCTURAL_EXIT_V1,
        "policy_label": "q070_cap3_mfe_fav_trial",
        "entry_thresholds": {
            "min_continuation_quality": MIN_ENTRY_Q,
            "max_concurrent_positions": 3,
            "favorable_mode": "mfe_linked",
            "favorable_mfe_scale": 0.003,
        },
        "exit_thresholds": {
            "take_quality_drop": TAKE_QUALITY_DROP,
            "momentum_weaken_ratio": MOMENTUM_WEAKEN,
            "favorable_fade_ratio": FAVORABLE_FADE,
            "vwap_break_peak_pnl_pct": VWAP_BREAK_PEAK_PNL,
            "trailing_giveback_pct": TRAILING_GIVEBACK_PCT,
            "hard_stop_pct": cfg.hard_stop_pct,
        },
        "structural_trade_count": len(trades),
        "close_reason_counts": dict(close_counts),
        "exit_replay_mismatch_count": mismatch,
        "variables": catalog,
        "session_field_stats": session_stats,
        "risk_priority": risk_rank,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }

    out_json = SESSION / "phase68_variable_audit.json"
    out_csv = SESSION / "phase68_variable_audit.csv"
    out_exit = SESSION / "phase68_exit_variable_breakdown.csv"

    out_json.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")

    csv_fields = [
        "variable",
        "usage",
        "implementation_file",
        "functions",
        "formula",
        "threshold",
        "risk",
        "risk_note",
        "accepted_n",
        "accepted_mean",
        "accepted_median",
        "accepted_p95",
    ]
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_fields)
        w.writeheader()
        for row in catalog:
            dist = row.get("session_distribution", {})
            acc = dist.get("accepted") or dist.get("accepted_recomputed") or {}
            w.writerow(
                {
                    "variable": row["variable"],
                    "usage": "|".join(row["usage"]),
                    "implementation_file": row["implementation_file"],
                    "functions": "|".join(row["functions"]),
                    "formula": row["formula"],
                    "threshold": row["threshold"],
                    "risk": row["risk"],
                    "risk_note": row["risk_note"],
                    "accepted_n": acc.get("n", ""),
                    "accepted_mean": acc.get("mean", ""),
                    "accepted_median": acc.get("median", ""),
                    "accepted_p95": acc.get("p95", ""),
                }
            )

    if exit_rows:
        with out_exit.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(exit_rows[0].keys()))
            w.writeheader()
            w.writerows(exit_rows)

    print(f"Wrote {out_json}")
    print(f"Wrote {out_csv}")
    print(f"Wrote {out_exit} ({len(exit_rows)} rows)")
    print("close_reason_counts:", dict(close_counts))
    print("replay_mismatch:", mismatch)


if __name__ == "__main__":
    main()
