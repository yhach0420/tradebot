"""
Phase470 — Momentum:low Necessity Tournament (research only).

Tests whether Momentum:low is required for Pullback runtime performance.
"""

from __future__ import annotations

import json
import pickle
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv
from research.phase365_production_stack_validation import phase364_blocked_only
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import guard_high_drift
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import (
    DAY_618,
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _chronological_pnls_from_log,
    _now_iso,
    _symbol_pnl_from_log,
)
from research.phase451b_entry_shape_tournament_mid_high import (
    _board_token,
    _v2_entry_score,
)
from research.phase459_winner_pattern_audit import _stop_rate_from_log
from research.phase463_trend_pullback_population_tournament import (
    _fill_close_proxy_shadows,
    _filter_replay_pool,
    _rise,
    _weak_shape_block,
    pass_a0_baseline,
)
from research.phase465b_trend_gate_redesign import _gate_t4, _make_trend_only
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_expectancy_score_shadow import ENTRY_SCORE_V2_GATE_MIN

REPLAY_MODE = "phase456_runtime_np"

LATE_CHASE_R10_LO = 0.3719
LATE_CHASE_DAY_HIGH_LO = 1.1872
SYMBOL_EXCLUDE = ("6920.T", "6976.T", "4062.T")
TREND_T4_FN = _make_trend_only(_gate_t4)

VARIANT_LABELS: dict[str, str] = {
    "A": "Baseline (Momentum:low + Board mid/high + HD + WS)",
    "B": "Pullback only (no Momentum:low)",
    "C": "Pullback only + Late Chase Guard",
    "D": "Trend only T4 (consecutive_above_ticks >= 20)",
    "E": "Pullback OR Trend (B OR D)",
}

TOURNAMENT_FIELDS = [
    "variant",
    "label",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "stop_rate",
    "delta_pnl_vs_A",
    "delta_pf_vs_A",
    "delta_maxdd_vs_A",
    "delta_accepted_vs_A",
    "daily_pnl_618",
    "daily_pnl_619",
    "delta_daily_pnl_618",
    "delta_daily_pnl_619",
    "symbol_pnl_6920",
    "symbol_pnl_6976",
    "symbol_pnl_4062",
    "delta_symbol_pnl_6920",
    "delta_symbol_pnl_6976",
    "delta_symbol_pnl_4062",
    "captured_3441",
    "captured_6492",
    "captured_7256",
    "captured_7600",
    "top_day_share",
    "top_symbol_share",
    "rank_by_pnl",
]

ROBUSTNESS_FIELDS = [
    "test",
    "variant",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "delta_pnl_vs_full",
    "top_day_share",
    "top_symbol_share",
]


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _day_high_distance(trade: Mapping[str, Any]) -> float:
    return abs(
        _float(trade.get("day_high_distance_pct"))
        or _float(trade.get("entry_near_day_high_pct"))
        or 0.0
    )


def late_chase_block(trade: Mapping[str, Any]) -> bool:
    r10 = _rise(trade, 10)
    if r10 is None:
        return False
    return r10 < LATE_CHASE_R10_LO and _day_high_distance(trade) < LATE_CHASE_DAY_HIGH_LO


def _pass_board_mid_high_no_momentum(trade: Mapping[str, Any]) -> bool:
    tok = _board_token(trade)
    if tok == "Board:mid":
        return _v2_entry_score(trade) >= ENTRY_SCORE_V2_GATE_MIN
    if tok == "Board:high":
        return True
    return False


def _pass_pullback_core_no_momentum(trade: Mapping[str, Any]) -> bool:
    if not _pass_board_mid_high_no_momentum(trade):
        return False
    if guard_high_drift(trade):
        return False
    if _weak_shape_block(trade):
        return False
    if phase364_blocked_only(trade):
        return False
    return True


def pass_a(trade: Mapping[str, Any]) -> bool:
    return pass_a0_baseline(trade)


def pass_b(trade: Mapping[str, Any]) -> bool:
    return _pass_pullback_core_no_momentum(trade)


def pass_c(trade: Mapping[str, Any]) -> bool:
    if not _pass_pullback_core_no_momentum(trade):
        return False
    return not late_chase_block(trade)


def pass_d(trade: Mapping[str, Any]) -> bool:
    return TREND_T4_FN(trade)


def pass_e(trade: Mapping[str, Any]) -> bool:
    return pass_b(trade) or pass_d(trade)


VARIANT_PASS: dict[str, Callable[[Mapping[str, Any]], bool]] = {
    "A": pass_a,
    "B": pass_b,
    "C": pass_c,
    "D": pass_d,
    "E": pass_e,
}


def _entry_block(pass_fn: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
    return lambda t: not pass_fn(t)


def _concentration(trade_log: Sequence[Mapping[str, Any]]) -> tuple[float, float]:
    if not trade_log:
        return 0.0, 0.0
    total = sum(abs(_float(r.get("pnl_yen")) or 0) for r in trade_log)
    if total <= 0:
        return 0.0, 0.0
    by_day: Counter[str] = Counter()
    by_sym: Counter[str] = Counter()
    for r in trade_log:
        tr = r.get("trade") or {}
        by_day[str(tr.get("day") or "")[:8]] += abs(_float(r.get("pnl_yen")) or 0)
        by_sym[str(tr.get("symbol") or "")] += abs(_float(r.get("pnl_yen")) or 0)
    return round(max(by_day.values()) / total, 4), round(max(by_sym.values()) / total, 4)


def _symbol_pnl_custom(trade_log: Sequence[Mapping[str, Any]], code: str) -> float:
    total = 0.0
    for r in trade_log:
        if str(r.get("symbol") or "").replace(".T", "") == code:
            total += float(r.get("pnl_yen") or 0)
    return round(total, 2)


def _variant_metrics(
    state: Any,
    *,
    variant: str,
    baseline: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    sym_pnl = _symbol_pnl_from_log(state.trade_log)
    accepted_syms = {str(r.get("symbol") or "") for r in state.trade_log}
    top_day, top_sym = _concentration(state.trade_log)
    row = {
        "variant": variant,
        "label": VARIANT_LABELS.get(variant, variant),
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "accepted_count": state.accepted_trade_count,
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "daily_pnl_618": round(float(state.daily_pnls.get(DAY_618, 0.0)), 2),
        "daily_pnl_619": round(float(state.daily_pnls.get(DAY_619, 0.0)), 2),
        "symbol_pnl_6920": sym_pnl.get("6920", 0.0) or _symbol_pnl_custom(state.trade_log, "6920"),
        "symbol_pnl_6976": sym_pnl.get("6976", 0.0),
        "symbol_pnl_4062": sym_pnl.get("4062", 0.0),
        "top_day_share": top_day,
        "top_symbol_share": top_sym,
        **{
            f"captured_{c}": f"{c}.T" in accepted_syms or c in {str(s).replace(".T", "") for s in accepted_syms}
            for c in ("3441", "6492", "7256", "7600")
        },
    }
    if baseline:
        row["delta_pnl_vs_A"] = round(float(row["total_pnl_yen"]) - float(baseline["total_pnl_yen"]), 2)
        row["delta_pf_vs_A"] = round(float(row["profit_factor"] or 0) - float(baseline["profit_factor"] or 0), 4)
        row["delta_maxdd_vs_A"] = round(float(row["max_drawdown_yen"]) - float(baseline["max_drawdown_yen"]), 2)
        row["delta_accepted_vs_A"] = int(row["accepted_count"]) - int(baseline["accepted_count"])
        row["delta_daily_pnl_618"] = round(float(row["daily_pnl_618"]) - float(baseline["daily_pnl_618"]), 2)
        row["delta_daily_pnl_619"] = round(float(row["daily_pnl_619"]) - float(baseline["daily_pnl_619"]), 2)
        row["delta_symbol_pnl_6920"] = round(float(row["symbol_pnl_6920"]) - float(baseline["symbol_pnl_6920"]), 2)
        row["delta_symbol_pnl_6976"] = round(float(row["symbol_pnl_6976"]) - float(baseline["symbol_pnl_6976"]), 2)
        row["delta_symbol_pnl_4062"] = round(float(row["symbol_pnl_4062"]) - float(baseline["symbol_pnl_4062"]), 2)
    else:
        for k in (
            "delta_pnl_vs_A",
            "delta_pf_vs_A",
            "delta_maxdd_vs_A",
            "delta_accepted_vs_A",
            "delta_daily_pnl_618",
            "delta_daily_pnl_619",
            "delta_symbol_pnl_6920",
            "delta_symbol_pnl_6976",
            "delta_symbol_pnl_4062",
        ):
            row[k] = 0 if k == "delta_accepted_vs_A" else 0.0
    return row


def _run_variant(
    variant: str,
    *,
    replay_pool: Sequence[Mapping[str, Any]],
    np_shadows: Mapping[str, Any],
    baseline: Optional[Mapping[str, Any]] = None,
) -> tuple[dict[str, Any], Any]:
    st = simulate_capacity_replay(
        replay_pool,
        np_shadows,
        mode=f"{REPLAY_MODE}_p470_{variant}",
        entry_block_fn=_entry_block(VARIANT_PASS[variant]),
        baseline_accepted_keys=set(),
    )
    return _variant_metrics(st, variant=variant, baseline=baseline), st


def _load_replay_pool(reports: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = reports / ".phase463_cache" / "population.pkl"
    if not path.is_file():
        raise FileNotFoundError("phase463 cache required")
    with path.open("rb") as fh:
        payload = pickle.load(fh)
    return list(payload["replay_pool"]), dict(payload.get("np_shadows") or {})


def _parallel_worker(args: tuple[str, str]) -> dict[str, Any]:
    import sys
    from pathlib import Path as _Path

    repo = _Path(__file__).resolve().parents[2]
    kabu = _Path(__file__).resolve().parents[1]
    for p in (kabu / "src", repo):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    variant, cache_path = args
    with Path(cache_path).open("rb") as fh:
        payload = pickle.load(fh)
    row, _ = _run_variant(
        variant,
        replay_pool=payload["replay_pool"],
        np_shadows=payload["np_shadows"],
        baseline=payload.get("baseline_a"),
    )
    return row


def _verdict(
    *,
    row_a: Mapping[str, Any],
    row_b: Mapping[str, Any],
    row_c: Mapping[str, Any],
    best_row: Mapping[str, Any],
    overfit: bool,
) -> str:
    a_pnl = float(row_a.get("total_pnl_yen") or 0)
    b_pnl = float(row_b.get("total_pnl_yen") or 0)
    c_pnl = float(row_c.get("total_pnl_yen") or 0)
    best_var = str(best_row.get("variant") or "A")
    b_delta = b_pnl - a_pnl
    c_delta = c_pnl - a_pnl

    if overfit and best_var != "A":
        return "overfit_candidate" if best_var in ("B", "C") else "momentum_redundant"

    if best_var == "C" and c_delta > 5000 and b_delta >= -3000:
        return "late_chase_replaces_momentum"

    if b_delta > 5000:
        return "momentum_harmful"
    if b_delta >= -3000 and float(row_b.get("profit_factor") or 0) >= float(row_a.get("profit_factor") or 0) - 0.05:
        return "momentum_redundant"
    if b_delta < -10000 or float(row_b.get("profit_factor") or 0) < float(row_a.get("profit_factor") or 0) - 0.15:
        return "momentum_required"
    return "momentum_redundant"


def run_phase470(
    *,
    repo_root: Path,
    parallel: bool = False,
    max_workers: int = 4,
) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, np_shadows = _load_replay_pool(reports)
    np_shadows = _fill_close_proxy_shadows(replay_pool, np_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool(replay_pool, np_shadows)
    print(f"phase470 replay pool: {len(replay_pool)}", flush=True)

    row_a, state_a = _run_variant("A", replay_pool=replay_pool, np_shadows=np_shadows)
    cache_path = reports / ".phase470_cache" / "replay.pkl"
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("wb") as fh:
        pickle.dump({"replay_pool": replay_pool, "np_shadows": np_shadows, "baseline_a": row_a}, fh, protocol=pickle.HIGHEST_PROTOCOL)

    tournament_rows: list[dict[str, Any]] = [row_a]
    states: dict[str, Any] = {"A": state_a}

    others = [v for v in VARIANT_PASS if v != "A"]
    if parallel:
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            futs = [ex.submit(_parallel_worker, (v, str(cache_path))) for v in others]
            for fut in as_completed(futs):
                tournament_rows.append(fut.result())
    else:
        for v in others:
            row, st = _run_variant(v, replay_pool=replay_pool, np_shadows=np_shadows, baseline=row_a)
            tournament_rows.append(row)
            states[v] = st

    tournament_rows.sort(key=lambda r: float(r.get("total_pnl_yen") or 0), reverse=True)
    for i, r in enumerate(tournament_rows, start=1):
        r["rank_by_pnl"] = i

    row_b = next(r for r in tournament_rows if r["variant"] == "B")
    row_c = next(r for r in tournament_rows if r["variant"] == "C")
    best_row = tournament_rows[0]
    best_variant = str(best_row.get("variant") or "A")

    if best_variant not in states:
        _, states[best_variant] = _run_variant(
            best_variant, replay_pool=replay_pool, np_shadows=np_shadows, baseline=row_a
        )

    robust_rows: list[dict[str, Any]] = []
    full_pnl = float(best_row.get("total_pnl_yen") or 0)
    days = sorted({str(t.get("day") or "")[:8] for t in replay_pool if t.get("day")})
    for day in days:
        pool = [t for t in replay_pool if str(t.get("day") or "")[:8] != day]
        row, _ = _run_variant(best_variant, replay_pool=pool, np_shadows=np_shadows, baseline=row_a)
        robust_rows.append(
            {
                "test": f"LOO_{day}",
                "variant": best_variant,
                "total_pnl_yen": row["total_pnl_yen"],
                "profit_factor": row["profit_factor"],
                "max_drawdown_yen": row["max_drawdown_yen"],
                "accepted_count": row["accepted_count"],
                "delta_pnl_vs_full": round(float(row["total_pnl_yen"]) - full_pnl, 2),
                "top_day_share": row["top_day_share"],
                "top_symbol_share": row["top_symbol_share"],
            }
        )
    robust_rows.append(
        {
            "test": "full",
            "variant": best_variant,
            "total_pnl_yen": best_row["total_pnl_yen"],
            "profit_factor": best_row["profit_factor"],
            "max_drawdown_yen": best_row["max_drawdown_yen"],
            "accepted_count": best_row["accepted_count"],
            "delta_pnl_vs_full": 0.0,
            "top_day_share": best_row["top_day_share"],
            "top_symbol_share": best_row["top_symbol_share"],
        }
    )
    for sym in SYMBOL_EXCLUDE:
        pool = [t for t in replay_pool if str(t.get("symbol") or "") != sym]
        row, _ = _run_variant(best_variant, replay_pool=pool, np_shadows=np_shadows, baseline=row_a)
        robust_rows.append(
            {
                "test": f"exclude_{sym.replace('.T', '')}",
                "variant": best_variant,
                "total_pnl_yen": row["total_pnl_yen"],
                "profit_factor": row["profit_factor"],
                "max_drawdown_yen": row["max_drawdown_yen"],
                "accepted_count": row["accepted_count"],
                "delta_pnl_vs_full": round(float(row["total_pnl_yen"]) - full_pnl, 2),
                "top_day_share": row["top_day_share"],
                "top_symbol_share": row["top_symbol_share"],
            }
        )

    loo_pnls = [float(r["total_pnl_yen"]) for r in robust_rows if str(r["test"]).startswith("LOO_")]
    overfit = (
        float(best_row.get("top_day_share") or 0) > 0.45
        or float(best_row.get("top_symbol_share") or 0) > 0.45
        or (loo_pnls and full_pnl > 0 and min(loo_pnls) < 0)
    )

    verdict = _verdict(row_a=row_a, row_b=row_b, row_c=row_c, best_row=best_row, overfit=overfit)
    a_vs_b = float(row_b.get("delta_pnl_vs_A") or 0)
    a_vs_c = float(row_c.get("delta_pnl_vs_A") or 0)
    b_vs_c = round(float(row_c.get("total_pnl_yen") or 0) - float(row_b.get("total_pnl_yen") or 0), 2)

    momentum_necessary = verdict == "momentum_required"

    mandatory = {
        "1_momentum_low_necessary": momentum_necessary,
        "1b_momentum_verdict": verdict,
        "2_best_variant": f"{best_row.get('variant')} ({best_row.get('label')})",
        "3_A_vs_B_delta_pnl": a_vs_b,
        "4_A_vs_C_delta_pnl": a_vs_c,
        "5_B_vs_C_delta_pnl": b_vs_c,
        "6_pf_improvement_best_vs_A": best_row.get("delta_pf_vs_A"),
        "7_maxdd_change_best_vs_A": best_row.get("delta_maxdd_vs_A"),
        "8_accepted_change_best_vs_A": best_row.get("delta_accepted_vs_A"),
        "9_6976_impact": {"A": row_a.get("symbol_pnl_6976"), "best": best_row.get("symbol_pnl_6976")},
        "10_4062_impact": {"A": row_a.get("symbol_pnl_4062"), "best": best_row.get("symbol_pnl_4062")},
        "11_6920_impact": {"A": row_a.get("symbol_pnl_6920"), "best": best_row.get("symbol_pnl_6920")},
        "12_improvement_618": best_row.get("delta_daily_pnl_618"),
        "13_improvement_619": best_row.get("delta_daily_pnl_619"),
        "14_overfit": overfit,
        "15_runtime_candidate": verdict in ("momentum_redundant", "late_chase_replaces_momentum", "momentum_harmful")
        and best_row.get("variant") != "A"
        and not overfit,
        "16_shadow_candidate": best_row.get("variant") if best_row.get("variant") != "A" else None,
        "17_next_actions": [
            "Drop Momentum:low if B≈A and C improves" if verdict == "momentum_redundant" else f"Keep Momentum:low ({verdict})",
            f"Shadow {best_row.get('variant')} if delta_pnl>{best_row.get('delta_pnl_vs_A')}",
            "6976 concentration check before any gate change",
        ],
        "verdict": verdict,
        "row_A": row_a,
        "row_B": row_b,
        "row_C": row_c,
        "best_row": best_row,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_tournament_rows": tournament_rows,
        "_robust_rows": robust_rows,
    }


@dataclass
class Phase470Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        return run_phase470(
            repo_root=self.repo_root,
            parallel=self.parallel,
            max_workers=self.max_workers,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "tournament": reports / "phase470_momentum_necessity_tournament.csv",
            "robustness": reports / "phase470_momentum_necessity_robustness.csv",
            "summary": reports / "phase470_momentum_necessity_summary.json",
        }
        _write_csv(paths["tournament"], TOURNAMENT_FIELDS, list(result.get("_tournament_rows") or []))
        _write_csv(paths["robustness"], ROBUSTNESS_FIELDS, list(result.get("_robust_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase470_momentum_necessity_tournament.md"
        m = result.get("mandatory_answers") or {}
        rows = list(result.get("_tournament_rows") or [])
        lines = [
            "# Phase470 — Momentum:low Necessity Tournament",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"Momentum:low necessary: **{m.get('1_momentum_low_necessary')}**",
            "",
            "| var | label | PnL | PF | maxDD | accepted | Δ vs A |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
        for r in sorted(rows, key=lambda x: x.get("variant", "")):
            lines.append(
                f"| {r.get('variant')} | {r.get('label')} | {r.get('total_pnl_yen')} "
                f"| {r.get('profit_factor')} | {r.get('max_drawdown_yen')} | {r.get('accepted_count')} "
                f"| {r.get('delta_pnl_vs_A')} |"
            )
        lines.extend(
            [
                "",
                f"Best: **{m.get('2_best_variant')}**",
                f"A vs B: **{m.get('3_A_vs_B_delta_pnl')}**",
                f"A vs C: **{m.get('4_A_vs_C_delta_pnl')}**",
            ]
        )
        report.write_text("\n".join(lines), encoding="utf-8")
        paths["report"] = report
        return paths
