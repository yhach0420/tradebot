"""
Phase487 — stop_low_mfe Runtime Impact Replay (research only).

CAP replay of Phase483–484 late_chase / vwap_trap guard candidates vs PBv2 baseline.
"""

from __future__ import annotations

import csv
import json
import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import (
    JST,
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _now_iso,
)
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _ensure_enriched, _load_replay_pool
from research.phase463_trend_pullback_population_tournament import (
    _fill_close_proxy_shadows,
    _filter_replay_pool,
    _valid_replay_trade,
)
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase481_stop_low_mfe_reduction_tournament import (
    GuardSpec,
    _blocked_stats,
    _build_trade_rows,
    _pass_with_guard,
    _replay_metrics,
    _symbol_day_attr,
    _top_shares,
)
from research.phase484_stop_low_mfe_feature_discovery import (
    _board_features,
    _compute_base_features,
    _load_day_event_snaps,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

EXPLICIT_PATTERNS = (
    "P1_A2_r15_minus_r5",
    "P1_B2_vwap_extension_rate",
    "P2_A2_r15_minus_r5_B2_vwap_extension_rate",
)
TOP10_RANK = 10

GUARD_REPLAY_FIELDS = [
    "guard_id",
    "pattern_id",
    "label",
    "conditions",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "stop_low_mfe_count",
    "stop_low_mfe_pnl_yen",
    "delta_pnl_vs_baseline",
    "delta_pf_vs_baseline",
    "delta_maxdd_vs_baseline",
    "delta_stop_low_mfe_count",
    "delta_stop_low_mfe_pnl",
    "blocked_winners",
    "blocked_losers",
    "blocked_stop_low_mfe",
    "blocked_pnl_yen",
    "rank_by_pnl",
]

SYMBOL_DAY_FIELDS = [
    "guard_id",
    "symbol",
    "day",
    "accepted_count",
    "total_pnl_yen",
    "stop_low_mfe_count",
    "stop_low_mfe_pnl_yen",
    "delta_pnl_vs_baseline",
]

ROBUSTNESS_FIELDS = [
    "test",
    "guard_id",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "stop_low_mfe_count",
    "delta_pnl_vs_full",
    "top_day_share",
    "top_symbol_share",
]

_COND_RE = re.compile(r"^([\w\d]+)([<>])([-\d.eE+]+)@")


def _parse_conditions(conditions: str) -> list[tuple[str, str, float]]:
    out: list[tuple[str, str, float]] = []
    for part in (p.strip() for p in conditions.split(" AND ")):
        m = _COND_RE.match(part)
        if not m:
            continue
        out.append((m.group(1), "gt" if m.group(2) == ">" else "lt", float(m.group(3))))
    return out


def _check_condition(feats: Mapping[str, Any], feat: str, direction: str, thr: float) -> bool:
    v = feats.get(feat)
    if v is None:
        return False
    return float(v) > thr if direction == "gt" else float(v) < thr


def _load_phase484_patterns(reports: Path) -> list[dict[str, str]]:
    path = reports / "phase484_stop_low_mfe_patterns.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def _safe_day_from_ts(val: Any) -> bool:
    dt = _parse_ts(val)
    if dt is None:
        return False
    try:
        dt.astimezone(JST).strftime("%Y%m%d")
    except (OverflowError, OSError, ValueError):
        return False
    return True


def _filter_replay_pool_safe(
    replay_pool: Sequence[Mapping[str, Any]],
    np_shadows: Mapping[str, Any],
) -> list[dict[str, Any]]:
    out = _filter_replay_pool(replay_pool, np_shadows)
    safe: list[dict[str, Any]] = []
    dropped = 0
    for trade in out:
        key = _position_key(trade)
        if not _valid_replay_trade(trade, np_shadows.get(key)):
            dropped += 1
            continue
        if not _safe_day_from_ts(trade.get("exit_time")):
            dropped += 1
            continue
        safe.append(trade)
    if dropped:
        print(f"phase487 dropped invalid exit timestamps: {dropped}", flush=True)
    return safe


def _select_guard_patterns(patterns: Sequence[Mapping[str, str]]) -> list[dict[str, str]]:
    by_id = {str(p["pattern_id"]): p for p in patterns if p.get("pattern_id")}
    selected: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(pid: str) -> None:
        if pid in seen or pid not in by_id:
            return
        seen.add(pid)
        selected.append(dict(by_id[pid]))

    for pid in EXPLICIT_PATTERNS:
        add(pid)
    top10 = sorted(
        patterns,
        key=lambda p: int(p.get("rank_by_separation") or 999),
    )[:TOP10_RANK]
    for p in top10:
        add(str(p.get("pattern_id") or ""))
    return selected


def _build_feature_index(
    pool: Sequence[Mapping[str, Any]],
    *,
    kabu_root: Path,
) -> dict[str, dict[str, Any]]:
    days_needed = sorted({str(t.get("day") or "")[:8] for t in pool if t.get("day")})
    day_snaps = {day: _load_day_event_snaps(kabu_root, day) for day in days_needed}
    out: dict[str, dict[str, Any]] = {}
    for tr in pool:
        key = _position_key(tr)
        feats = _compute_base_features(tr)
        day = str(tr.get("day") or "")[:8]
        feats.update(_board_features(tr, day_snaps.get(day, {})))
        out[key] = feats
    return out


def _pattern_guard(
    pattern: Mapping[str, str],
    feature_index: Mapping[str, Mapping[str, Any]],
) -> GuardSpec:
    pid = str(pattern.get("pattern_id") or "")
    conditions = str(pattern.get("conditions") or "")
    parsed = _parse_conditions(conditions)

    def reject_fn(trade: Mapping[str, Any]) -> bool:
        feats = feature_index.get(_position_key(trade))
        if feats is None:
            return False
        return all(_check_condition(feats, f, d, t) for f, d, t in parsed)

    label = pid.replace("_", " ")
    return GuardSpec(
        guard_id=pid,
        label=f"late_chase guard {label}",
        conditions=conditions,
        reject_fn=reject_fn,
        threshold_summary=str(pattern.get("threshold_summary") or ""),
    )


def _verdict(
    *,
    best: Mapping[str, Any],
    baseline_id: str,
    robust_rows: Sequence[Mapping[str, Any]],
) -> str:
    best_id = str(best.get("guard_id") or baseline_id)
    delta_pnl = float(best.get("delta_pnl_vs_baseline") or 0)
    if best_id == baseline_id or delta_pnl <= 0:
        return "no_runtime_edge"

    loo = [float(r.get("delta_pnl_vs_full") or 0) for r in robust_rows if str(r.get("test", "")).startswith("LOO_")]
    if loo and min(loo) < -30000:
        return "overfit_guard"
    if float(best.get("top_day_share") or 0) > 0.45:
        return "overfit_guard"

    delta_slm = int(best.get("delta_stop_low_mfe_count") or 0)
    blocked_win = int(best.get("blocked_winners") or 0)
    if delta_pnl >= 5000 and delta_slm <= -2 and blocked_win <= 12:
        return "guard_candidate_found"
    if delta_pnl >= 1000 and delta_slm < 0:
        return "guard_candidate_found"
    return "no_runtime_edge"


def _next_actions(verdict: str, best: Mapping[str, Any]) -> list[str]:
    actions = [f"Verdict: {verdict}"]
    gid = best.get("guard_id")
    if verdict == "guard_candidate_found":
        actions.append(f"Shadow replay guard {gid}: {best.get('conditions')}")
    elif verdict == "overfit_guard":
        actions.append(f"Guard {gid} improves in-sample but LOO unstable — shadow only")
    else:
        actions.append("Phase484 guards block net-positive trades — keep PBv2 baseline")
        actions.append("Prioritize entry quality research (Phase483 root cause) over guard adoption")
    actions.append(f"Best delta PnL vs baseline: {best.get('delta_pnl_vs_baseline')}")
    return actions


def run_phase487(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool_safe(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)
    trade_by_key = {_position_key(t): t for t in replay_pool}

    pbv2_pool = [t for t in replay_pool if pass_pbv2(t)]
    feature_index = _build_feature_index(pbv2_pool, kabu_root=kabu)

    patterns = _load_phase484_patterns(reports)
    guard_patterns = _select_guard_patterns(patterns)
    guards = [_pattern_guard(p, feature_index) for p in guard_patterns]

    st_base = simulate_capacity_replay(
        replay_pool,
        runtime_shadows,
        mode="phase487_baseline",
        entry_block_fn=_entry_block(pass_pbv2),
        baseline_accepted_keys=set(),
    )
    baseline_rows = _build_trade_rows(st_base, trade_by_key=trade_by_key, price_idx=price_idx)
    baseline = _replay_metrics(st_base, trade_by_key=trade_by_key, price_idx=price_idx)

    tournament_rows: list[dict[str, Any]] = [
        {
            "guard_id": "baseline",
            "pattern_id": "baseline",
            "label": "PBv2 runtime (no guard)",
            "conditions": "pass_pbv2",
            **{k: baseline[k] for k in baseline if not k.startswith("_")},
            "delta_pnl_vs_baseline": 0.0,
            "delta_pf_vs_baseline": 0.0,
            "delta_maxdd_vs_baseline": 0.0,
            "delta_stop_low_mfe_count": 0,
            "delta_stop_low_mfe_pnl": 0.0,
            "blocked_winners": 0,
            "blocked_losers": 0,
            "blocked_stop_low_mfe": 0,
            "blocked_pnl_yen": 0.0,
        }
    ]
    replay_attr: list[dict[str, Any]] = []
    replay_attr.extend(_symbol_day_attr("baseline", baseline_rows, baseline_rows))
    states: dict[str, Any] = {"baseline": st_base}

    for g in guards:
        st = simulate_capacity_replay(
            replay_pool,
            runtime_shadows,
            mode=f"phase487_{g.guard_id}",
            entry_block_fn=_entry_block(_pass_with_guard(g)),
            baseline_accepted_keys=set(),
        )
        states[g.guard_id] = st
        met = _replay_metrics(st, trade_by_key=trade_by_key, price_idx=price_idx)
        blocked_keys = baseline["_keys"] - met["_keys"]
        blk = _blocked_stats(baseline_rows, blocked_keys)
        top_day, top_sym = _top_shares(st)
        row = {
            "guard_id": g.guard_id,
            "pattern_id": g.guard_id,
            "label": g.label,
            "conditions": g.conditions,
            **{k: met[k] for k in met if not k.startswith("_")},
            "delta_pnl_vs_baseline": round(float(met["total_pnl_yen"]) - float(baseline["total_pnl_yen"]), 2),
            "delta_pf_vs_baseline": round((met["profit_factor"] or 0) - (baseline["profit_factor"] or 0), 4),
            "delta_maxdd_vs_baseline": round(float(met["max_drawdown_yen"]) - float(baseline["max_drawdown_yen"]), 2),
            "delta_stop_low_mfe_count": int(met["stop_low_mfe_count"]) - int(baseline["stop_low_mfe_count"]),
            "delta_stop_low_mfe_pnl": round(float(met["stop_low_mfe_pnl_yen"]) - float(baseline["stop_low_mfe_pnl_yen"]), 2),
            "top_day_share": top_day,
            "top_symbol_share": top_sym,
            **blk,
        }
        tournament_rows.append(row)
        replay_attr.extend(_symbol_day_attr(g.guard_id, met["_rows"], baseline_rows))

    tournament_rows.sort(key=lambda r: float(r.get("total_pnl_yen") or -1e18), reverse=True)
    for i, r in enumerate(tournament_rows, start=1):
        r["rank_by_pnl"] = i

    guard_only = [r for r in tournament_rows if str(r.get("guard_id")) != "baseline"]
    best_guard = max(
        guard_only,
        key=lambda r: (
            float(r.get("delta_pnl_vs_baseline") or -1e18),
            int(r.get("delta_stop_low_mfe_count") or 0),
            -int(r.get("blocked_winners") or 999),
        ),
    )
    best = best_guard if float(best_guard.get("delta_pnl_vs_baseline") or 0) > 0 else tournament_rows[0]
    best_id = str(best.get("guard_id") or "baseline")
    full_pnl = float(best.get("total_pnl_yen") or 0)
    robust_rows: list[dict[str, Any]] = []

    def _run_guard_pool(pool: Sequence[Mapping[str, Any]], test: str) -> None:
        if best_id == "baseline":
            st = simulate_capacity_replay(
                pool,
                runtime_shadows,
                mode=f"phase487_robust_{test}",
                entry_block_fn=_entry_block(pass_pbv2),
                baseline_accepted_keys=set(),
            )
        else:
            g = next(x for x in guards if x.guard_id == best_id)
            st = simulate_capacity_replay(
                pool,
                runtime_shadows,
                mode=f"phase487_robust_{test}",
                entry_block_fn=_entry_block(_pass_with_guard(g)),
                baseline_accepted_keys=set(),
            )
        met = _replay_metrics(st, trade_by_key=trade_by_key, price_idx=price_idx)
        td, ts = _top_shares(st)
        robust_rows.append(
            {
                "test": test,
                "guard_id": best_id,
                "total_pnl_yen": met["total_pnl_yen"],
                "profit_factor": met["profit_factor"],
                "max_drawdown_yen": met["max_drawdown_yen"],
                "accepted_count": met["accepted_count"],
                "stop_low_mfe_count": met["stop_low_mfe_count"],
                "delta_pnl_vs_full": round(float(met["total_pnl_yen"]) - full_pnl, 2),
                "top_day_share": td,
                "top_symbol_share": ts,
            }
        )

    days = sorted({str(t.get("day") or "")[:8] for t in replay_pool if t.get("day")})
    for day in days:
        _run_guard_pool([t for t in replay_pool if str(t.get("day") or "")[:8] != day], f"LOO_{day}")
    _run_guard_pool(replay_pool, "full")
    for sym in ("6976.T", "4062.T"):
        _run_guard_pool(
            [t for t in replay_pool if str(t.get("symbol") or "") != sym],
            f"exclude_{sym.replace('.T', '')}",
        )

    verdict = _verdict(best=best, baseline_id="baseline", robust_rows=robust_rows)
    sym6976 = next((r for r in replay_attr if r["guard_id"] == best_id and r["symbol"] == "6976" and r["day"] == "ALL"), {})
    sym4062 = next((r for r in replay_attr if r["guard_id"] == best_id and r["symbol"] == "4062" and r["day"] == "ALL"), {})

    loo_deltas = [float(r.get("delta_pnl_vs_full") or 0) for r in robust_rows if str(r.get("test", "")).startswith("LOO_")]
    overfit_risk = (
        "high"
        if loo_deltas and min(loo_deltas) < -40000
        else "moderate"
        if loo_deltas and statistics.pstdev(loo_deltas) > 30000
        else "low"
    )

    mandatory = {
        "1_best_candidate": best_id,
        "2_pnl_improvement": best.get("delta_pnl_vs_baseline"),
        "3_pf_improvement": best.get("delta_pf_vs_baseline"),
        "4_maxdd_change": best.get("delta_maxdd_vs_baseline"),
        "5_stop_low_mfe_reduction": {
            "count_delta": best.get("delta_stop_low_mfe_count"),
            "pnl_delta": best.get("delta_stop_low_mfe_pnl"),
            "baseline_count": baseline["stop_low_mfe_count"],
            "candidate_count": best.get("stop_low_mfe_count"),
        },
        "6_6976_impact": sym6976,
        "7_4062_impact": sym4062,
        "8_runtime_candidate": verdict == "guard_candidate_found" and float(best.get("delta_pnl_vs_baseline") or 0) >= 10000,
        "9_shadow_candidate": best_id if verdict in ("guard_candidate_found", "overfit_guard") else None,
        "10_next_actions": _next_actions(verdict, best),
        "verdict": verdict,
        "baseline_pnl": baseline["total_pnl_yen"],
        "baseline_pf": baseline["profit_factor"],
        "baseline_maxdd": baseline["max_drawdown_yen"],
        "baseline_accepted": baseline["accepted_count"],
        "baseline_stop_low_mfe_count": baseline["stop_low_mfe_count"],
        "guard_count": len(guards),
        "overfit_risk": overfit_risk,
        "top5_guards": tournament_rows[:5],
        "explicit_candidates": list(EXPLICIT_PATTERNS),
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_guard_replay": tournament_rows,
        "_symbol_day": replay_attr,
        "_robustness": robust_rows,
    }


@dataclass
class Phase487Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase487(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "guard_replay": reports / "phase487_guard_replay.csv",
            "symbol_day": reports / "phase487_guard_symbol_day.csv",
            "robustness": reports / "phase487_guard_robustness.csv",
            "summary": reports / "phase487_summary.json",
        }
        _write_csv(paths["guard_replay"], GUARD_REPLAY_FIELDS, list(result.get("_guard_replay") or []))
        _write_csv(paths["symbol_day"], SYMBOL_DAY_FIELDS, list(result.get("_symbol_day") or []))
        _write_csv(paths["robustness"], ROBUSTNESS_FIELDS, list(result.get("_robustness") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase487_stop_low_mfe_runtime_impact_replay.md"
        self._write_report(report, result)
        paths["report"] = report
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        guards = list(result.get("_guard_replay") or [])
        lines = [
            "# Phase487 — stop_low_mfe Runtime Impact Replay",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')}-{result.get('period_end')}",
            "",
            "## Mandatory answers",
            "",
            f"1. Best candidate: **{m.get('1_best_candidate')}**",
            f"2. PnL improvement: **{m.get('2_pnl_improvement')}**",
            f"3. PF improvement: **{m.get('3_pf_improvement')}**",
            f"4. maxDD change: **{m.get('4_maxdd_change')}**",
            f"5. stop_low_mfe reduction: **{m.get('5_stop_low_mfe_reduction')}**",
            f"6. 6976 impact: **{m.get('6_6976_impact')}**",
            f"7. 4062 impact: **{m.get('7_4062_impact')}**",
            f"8. Runtime candidate: **{m.get('8_runtime_candidate')}**",
            f"9. Shadow candidate: **{m.get('9_shadow_candidate')}**",
            f"10. Next actions: {m.get('10_next_actions')}",
            "",
            "## Top guards by PnL",
            "",
        ]
        for g in guards[:8]:
            if str(g.get("guard_id")) == "baseline":
                continue
            lines.append(
                f"- **{g.get('guard_id')}** PnL {g.get('total_pnl_yen')} "
                f"dPnL {g.get('delta_pnl_vs_baseline')} slm {g.get('delta_stop_low_mfe_count')} "
                f"blkW {g.get('blocked_winners')}"
            )
        lines.extend(["", f"**Verdict:** `{result.get('verdict')}`", ""])
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
