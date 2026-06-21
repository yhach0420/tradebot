"""
Phase469 — Pullback Improvement Tournament (research only).

Integrates Phase455 Late Chase, Phase456C VWAP Structure, Phase461 Near High Exception
on Pullback baseline (pass_a0) with CAP5 replay.
"""

from __future__ import annotations

import json
import pickle
import statistics
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _write_csv
from research.phase365_production_stack_validation import phase364_blocked_only
from research.phase382_capital_constrained_backtest import _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
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
from research.phase459_winner_pattern_audit import _stop_rate_from_log
from research.phase463_trend_pullback_population_tournament import (
    _fill_close_proxy_shadows,
    _filter_replay_pool,
    _rise,
    pass_a0_baseline,
    pass_a2_near_high_exception,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

REPLAY_MODE = "phase456_runtime_np"
JST = ZoneInfo("Asia/Tokyo")

# Phase455 best combo (fixed)
LATE_CHASE_R10_LO = 0.3719
LATE_CHASE_DAY_HIGH_LO = 1.1872

# Phase456C / Phase457 D4 (fixed)
D4_CONSECUTIVE_ABOVE_LO = 20.5
D4_VWAP_DEV_PCT_LO = 0.20875

TARGET_SYMBOLS = ("6920", "6976", "4062", "3441", "6492", "7256", "7600")
SYMBOL_EXCLUDE = ("6920.T", "6976.T", "4062.T")

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

VARIANT_LABELS: dict[str, str] = {
    "A": "Baseline Pullback Runtime",
    "B": "Late Chase Guard (Phase455)",
    "C": "VWAP Structure D4 (Phase456C)",
    "D": "Late Chase OR VWAP Structure",
    "E": "Late Chase AND VWAP Structure",
    "F": "Near High Exception r5>0 (Phase461)",
    "G": "(B OR C) + Near High Exception",
    "H": "(B AND C) + Near High Exception",
}


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


def vwap_structure_block(trade: Mapping[str, Any]) -> bool:
    cat = _float(trade.get("consecutive_above_ticks"))
    vdev = _float(trade.get("vwap_dev_pct"))
    if cat is None or vdev is None:
        return False
    return cat < D4_CONSECUTIVE_ABOVE_LO and vdev < D4_VWAP_DEV_PCT_LO


def _pass_with_guards(
    trade: Mapping[str, Any],
    *,
    use_late_chase: bool,
    use_vwap: bool,
    combo: str,
    near_high: bool,
) -> bool:
    if near_high:
        if not pass_a2_near_high_exception(trade):
            return False
    elif not pass_a0_baseline(trade):
        return False

    blocks: list[bool] = []
    if use_late_chase:
        blocks.append(late_chase_block(trade))
    if use_vwap:
        blocks.append(vwap_structure_block(trade))
    if not blocks:
        return True
    if combo == "or":
        return not any(blocks)
    if combo == "and":
        return not all(blocks)
    return not blocks[0]


def pass_a(trade: Mapping[str, Any]) -> bool:
    return pass_a0_baseline(trade)


def pass_b(trade: Mapping[str, Any]) -> bool:
    return _pass_with_guards(trade, use_late_chase=True, use_vwap=False, combo="single", near_high=False)


def pass_c(trade: Mapping[str, Any]) -> bool:
    return _pass_with_guards(trade, use_late_chase=False, use_vwap=True, combo="single", near_high=False)


def pass_d(trade: Mapping[str, Any]) -> bool:
    return _pass_with_guards(trade, use_late_chase=True, use_vwap=True, combo="or", near_high=False)


def pass_e(trade: Mapping[str, Any]) -> bool:
    return _pass_with_guards(trade, use_late_chase=True, use_vwap=True, combo="and", near_high=False)


def pass_f(trade: Mapping[str, Any]) -> bool:
    return pass_a2_near_high_exception(trade)


def pass_g(trade: Mapping[str, Any]) -> bool:
    return _pass_with_guards(trade, use_late_chase=True, use_vwap=True, combo="or", near_high=True)


def pass_h(trade: Mapping[str, Any]) -> bool:
    return _pass_with_guards(trade, use_late_chase=True, use_vwap=True, combo="and", near_high=True)


VARIANT_PASS: dict[str, Callable[[Mapping[str, Any]], bool]] = {
    "A": pass_a,
    "B": pass_b,
    "C": pass_c,
    "D": pass_d,
    "E": pass_e,
    "F": pass_f,
    "G": pass_g,
    "H": pass_h,
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


def _sym_code(symbol: str) -> str:
    return str(symbol or "").replace(".T", "")


def _symbol_pnl_custom(trade_log: Sequence[Mapping[str, Any]], code: str) -> float:
    total = 0.0
    for r in trade_log:
        if _sym_code(str(r.get("symbol") or "")) == code:
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
        **{f"captured_{c}": f"{c}.T" in accepted_syms or c in {_sym_code(s) for s in accepted_syms} for c in ("3441", "6492", "7256", "7600")},
    }
    if baseline:
        row["delta_pnl_vs_A"] = round(float(row["total_pnl_yen"]) - float(baseline["total_pnl_yen"]), 2)
        row["delta_pf_vs_A"] = round(float(row["profit_factor"] or 0) - float(baseline["profit_factor"] or 0), 4)
        row["delta_maxdd_vs_A"] = round(float(row["max_drawdown_yen"]) - float(baseline["max_drawdown_yen"]), 2)
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
            "delta_daily_pnl_618",
            "delta_daily_pnl_619",
            "delta_symbol_pnl_6920",
            "delta_symbol_pnl_6976",
            "delta_symbol_pnl_4062",
        ):
            row[k] = 0.0
    return row


def _run_variant(
    variant: str,
    *,
    replay_pool: Sequence[Mapping[str, Any]],
    np_shadows: Mapping[str, Any],
    baseline: Optional[Mapping[str, Any]] = None,
) -> tuple[dict[str, Any], Any]:
    pass_fn = VARIANT_PASS[variant]
    st = simulate_capacity_replay(
        replay_pool,
        np_shadows,
        mode=f"{REPLAY_MODE}_p469_{variant}",
        entry_block_fn=_entry_block(pass_fn),
        baseline_accepted_keys=set(),
    )
    return _variant_metrics(st, variant=variant, baseline=baseline), st


def _load_replay_pool(reports: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    path = reports / ".phase463_cache" / "population.pkl"
    if not path.is_file():
        raise FileNotFoundError("phase463 cache required — run phase463 first")
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
    best_row: Mapping[str, Any],
    row_a: Mapping[str, Any],
    overfit: bool,
) -> str:
    delta = float(best_row.get("delta_pnl_vs_A") or 0)
    best_var = str(best_row.get("variant") or "A")
    if overfit and delta > 0:
        return "overfit_candidate"
    if best_var != "A" and delta > 3000 and float(best_row.get("profit_factor") or 0) >= float(row_a.get("profit_factor") or 0):
        return "pullback_improvement_candidate"
    if delta > 1000 and best_var != "A":
        return "pullback_improvement_candidate"
    return "no_improvement"


def run_phase469(
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
    print(f"phase469 replay pool: {len(replay_pool)}", flush=True)

    row_a, state_a = _run_variant("A", replay_pool=replay_pool, np_shadows=np_shadows)
    cache_path = reports / ".phase469_cache" / "replay.pkl"
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

    best_row = tournament_rows[0]
    best_variant = str(best_row.get("variant") or "A")

    robust_rows: list[dict[str, Any]] = []
    full_pnl = float(best_row.get("total_pnl_yen") or 0)
    best_state = states.get(best_variant)
    if best_state is None and best_variant != "A":
        _, best_state = _run_variant(best_variant, replay_pool=replay_pool, np_shadows=np_shadows, baseline=row_a)
    if best_state is None:
        best_state = state_a

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

    verdict = _verdict(best_row=best_row, row_a=row_a, overfit=overfit)

    mandatory = {
        "1_best_variant": f"{best_row.get('variant')} ({best_row.get('label')})",
        "2_pnl_improvement": best_row.get("delta_pnl_vs_A"),
        "3_pf_improvement": best_row.get("delta_pf_vs_A"),
        "4_maxdd_improvement": best_row.get("delta_maxdd_vs_A"),
        "5_6920_impact": {"A": row_a.get("symbol_pnl_6920"), "best": best_row.get("symbol_pnl_6920")},
        "6_6976_impact": {"A": row_a.get("symbol_pnl_6976"), "best": best_row.get("symbol_pnl_6976")},
        "7_4062_impact": {"A": row_a.get("symbol_pnl_4062"), "best": best_row.get("symbol_pnl_4062")},
        "8_improvement_618": best_row.get("delta_daily_pnl_618"),
        "9_improvement_619": best_row.get("delta_daily_pnl_619"),
        "10_overfit": overfit,
        "11_runtime_candidate": verdict == "pullback_improvement_candidate" and not overfit,
        "12_shadow_candidate": best_row.get("variant") if verdict != "no_improvement" else None,
        "13_next_actions": [
            f"Shadow {best_row.get('variant')} if delta_pnl>{best_row.get('delta_pnl_vs_A')}",
            "Walk-forward after 6/19",
            "6976/6920 concentration check before runtime",
        ],
        "verdict": verdict,
        "baseline_A": row_a,
        "best_variant_row": best_row,
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
class Phase469Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 4

    def run(self) -> dict[str, Any]:
        return run_phase469(
            repo_root=self.repo_root,
            parallel=self.parallel,
            max_workers=self.max_workers,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "tournament": reports / "phase469_pullback_improvement_tournament.csv",
            "summary": reports / "phase469_pullback_improvement_summary.json",
            "robustness": reports / "phase469_pullback_improvement_robustness.csv",
        }
        _write_csv(paths["tournament"], TOURNAMENT_FIELDS, list(result.get("_tournament_rows") or []))
        _write_csv(paths["robustness"], ROBUSTNESS_FIELDS, list(result.get("_robust_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase469_pullback_improvement_tournament.md"
        m = result.get("mandatory_answers") or {}
        rows = list(result.get("_tournament_rows") or [])
        lines = [
            "# Phase469 — Pullback Improvement Tournament",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
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
                f"Best: **{m.get('1_best_variant')}**",
                f"Runtime candidate: **{m.get('11_runtime_candidate')}**",
            ]
        )
        report.write_text("\n".join(lines), encoding="utf-8")
        paths["report"] = report
        return paths
