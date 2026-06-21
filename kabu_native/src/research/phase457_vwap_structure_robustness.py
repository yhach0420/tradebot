"""
Phase457 — VWAP Structure Robustness Audit (research only).

Validates D4 guard robustness vs baseline (Phase452 Runtime + NP exit).
D4: consecutive_above_ticks < 20.5 AND vwap_dev_pct < 0.20875
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY, _precompute_np_shadows
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import (
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _enrich_candidates,
    _load_candidate_stream,
    _now_iso,
)
from research.phase451b_entry_shape_tournament_mid_high import _runtime_entry_block_mid_high
from research.phase456c_vwap_structure_features import enrich_trade_phase456c_features
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.weak_shape_reject_entry_guard import would_block_weak_shape_reject

REPLAY_MODE = "phase456_runtime_np"

# Phase456C canonical D4 thresholds (fixed — do not re-derive)
D4_CONSECUTIVE_ABOVE_LO = 20.5
D4_VWAP_DEV_PCT_LO = 0.20875

FORWARD_DAYS = ("20260615", "20260616", "20260617", "20260618", "20260619")
TARGET_SYMBOLS = ("6976", "6920", "4062")

ROBUSTNESS_FIELDS = [
    "section",
    "metric",
    "value",
    "detail",
]

DAILY_FIELDS = [
    "day",
    "baseline_pnl",
    "d4_pnl",
    "delta",
    "delta_sign",
]

SYMBOL_FIELDS = [
    "symbol",
    "baseline_pnl",
    "d4_pnl",
    "delta_pnl",
    "rank",
]


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _pnl_yen(trade: Mapping[str, Any]) -> float:
    raw = trade.get("pnl_yen")
    if raw not in (None, ""):
        return float(raw)
    y100 = _float(trade.get("pnl_yen_100_float")) or _float(trade.get("pnl_yen_100"))
    return round(float(y100), 2) if y100 is not None else 0.0


def _map_runtime_fields(trade: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(trade)
    for src, dst in (
        ("return_5min_pct", "entry_rise_5min_pct"),
        ("return_10min_pct", "entry_rise_10min_pct"),
        ("return_15min_pct", "entry_rise_15min_pct"),
        ("return_30min_pct", "entry_rise_30min_pct"),
    ):
        if out.get(dst) is None and out.get(src) is not None:
            out[dst] = out[src]
    return out


def _weak_shape_block(trade: Mapping[str, Any]) -> bool:
    return would_block_weak_shape_reject(_map_runtime_fields(trade))


def _runtime_baseline_block(trade: Mapping[str, Any]) -> bool:
    return _runtime_entry_block_mid_high(_weak_shape_block)(trade)


def d4_guard(trade: Mapping[str, Any]) -> bool:
    b2 = (_float(trade.get("consecutive_above_ticks")) or 1e18) < D4_CONSECUTIVE_ABOVE_LO
    c1 = (_float(trade.get("vwap_dev_pct")) or 1e18) < D4_VWAP_DEV_PCT_LO
    return b2 and c1


def _entry_block(extra: Optional[Callable[[Mapping[str, Any]], bool]] = None):
    def block(trade: Mapping[str, Any]) -> bool:
        if _runtime_entry_block_mid_high(_weak_shape_block)(trade):
            return True
        if extra is not None and extra(trade):
            return True
        return False

    return block


def _total_pnl(state: Any) -> float:
    return round(sum(float(v) for v in state.daily_pnls.values()), 2)


def _symbol_pnl_from_log(trade_log: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    out: dict[str, float] = defaultdict(float)
    for row in trade_log:
        sym = str(row.get("symbol") or "").replace(".T", "")
        out[sym] += float(row.get("pnl_yen") or 0)
    return {k: round(v, 2) for k, v in out.items()}


def _run_pair(
    enriched: Sequence[Mapping[str, Any]],
    np_shadows: Mapping[str, Any],
) -> tuple[Any, Any]:
    base = simulate_capacity_replay(
        enriched,
        np_shadows,
        mode=REPLAY_MODE,
        entry_block_fn=_entry_block(None),
        baseline_accepted_keys=set(),
    )
    d4 = simulate_capacity_replay(
        enriched,
        np_shadows,
        mode=REPLAY_MODE,
        entry_block_fn=_entry_block(d4_guard),
        baseline_accepted_keys=set(),
    )
    return base, d4


def _daily_rows(base: Any, d4: Any) -> list[dict[str, Any]]:
    days = sorted(set(base.daily_pnls) | set(d4.daily_pnls))
    rows: list[dict[str, Any]] = []
    for day in days:
        bp = round(float(base.daily_pnls.get(day, 0.0)), 2)
        dp = round(float(d4.daily_pnls.get(day, 0.0)), 2)
        delta = round(dp - bp, 2)
        sign = "improved" if delta > 0 else ("worsened" if delta < 0 else "unchanged")
        rows.append(
            {
                "day": day,
                "baseline_pnl": bp,
                "d4_pnl": dp,
                "delta": delta,
                "delta_sign": sign,
            }
        )
    return rows


def _part_a(daily_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    improved = sum(1 for r in daily_rows if float(r.get("delta") or 0) > 0)
    worsened = sum(1 for r in daily_rows if float(r.get("delta") or 0) < 0)
    unchanged = sum(1 for r in daily_rows if float(r.get("delta") or 0) == 0)
    best = max(daily_rows, key=lambda r: float(r.get("delta") or 0))
    worst = min(daily_rows, key=lambda r: float(r.get("delta") or 0))
    return {
        "improved_days": improved,
        "worsened_days": worsened,
        "unchanged_days": unchanged,
        "max_improve_day": best.get("day"),
        "max_improve_delta": best.get("delta"),
        "max_worsen_day": worst.get("day"),
        "max_worsen_delta": worst.get("delta"),
    }


def _part_b_loo(
    enriched: Sequence[Mapping[str, Any]],
    np_shadows: Mapping[str, Any],
) -> dict[str, Any]:
    days = sorted({str(t.get("day") or "")[:8] for t in enriched if t.get("day")})
    rows: list[dict[str, Any]] = []
    deltas: list[float] = []
    for excl in days:
        subset = [t for t in enriched if str(t.get("day") or "")[:8] != excl]
        base, d4 = _run_pair(subset, np_shadows)
        delta = round(_total_pnl(d4) - _total_pnl(base), 2)
        deltas.append(delta)
        rows.append({"excluded_day": excl, "loo_delta": delta})
    return {
        "loo_rows": rows,
        "mean_delta": round(statistics.mean(deltas), 2) if deltas else 0.0,
        "median_delta": round(statistics.median(deltas), 2) if deltas else 0.0,
        "min_delta": min(deltas) if deltas else 0.0,
        "max_delta": max(deltas) if deltas else 0.0,
        "positive_loo_count": sum(1 for d in deltas if d > 0),
        "loo_day_count": len(deltas),
    }


def _top_day_share(daily_rows: Sequence[Mapping[str, Any]], total_delta: float) -> float:
    if total_delta == 0:
        return 0.0
    best = max((float(r.get("delta") or 0) for r in daily_rows), default=0.0)
    if best <= 0:
        return 0.0
    return round(best / total_delta, 4)


def _symbol_rows(base: Any, d4: Any) -> list[dict[str, Any]]:
    bs = _symbol_pnl_from_log(base.trade_log)
    ds = _symbol_pnl_from_log(d4.trade_log)
    syms = sorted(set(bs) | set(ds))
    rows = []
    for sym in syms:
        bp = bs.get(sym, 0.0)
        dp = ds.get(sym, 0.0)
        rows.append({"symbol": sym, "baseline_pnl": bp, "d4_pnl": dp, "delta_pnl": round(dp - bp, 2)})
    rows.sort(key=lambda r: float(r.get("delta_pnl") or 0), reverse=True)
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows


def _top_symbol_share(symbol_rows: Sequence[Mapping[str, Any]], total_delta: float) -> float:
    if total_delta <= 0 or not symbol_rows:
        return 0.0
    best = max(float(r.get("delta_pnl") or 0) for r in symbol_rows)
    if best <= 0:
        return 0.0
    return round(best / total_delta, 4)


def _blocked_trades(enriched: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    blocked: list[dict[str, Any]] = []
    for t in enriched:
        if _runtime_baseline_block(t):
            continue
        if not d4_guard(t):
            continue
        pnl = _pnl_yen(t)
        blocked.append(
            {
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "day": str(t.get("day") or "")[:8],
                "pnl_yen": pnl,
                "outcome": "loss" if pnl < 0 else ("win" if pnl > 0 else "flat"),
                "consecutive_above_ticks": t.get("consecutive_above_ticks"),
                "vwap_dev_pct": t.get("vwap_dev_pct"),
            }
        )
    return blocked


def _part_d(
    blocked: Sequence[Mapping[str, Any]],
    symbol_rows: Sequence[Mapping[str, Any]],
    total_delta: float,
) -> dict[str, Any]:
    by_sym_block: dict[str, list] = defaultdict(list)
    for b in blocked:
        sym = str(b.get("symbol") or "").replace(".T", "")
        by_sym_block[sym].append(b)

    sym_delta = {str(r.get("symbol")): float(r.get("delta_pnl") or 0) for r in symbol_rows}
    out: dict[str, Any] = {}
    for sym in TARGET_SYMBOLS:
        bl = by_sym_block.get(sym, [])
        delta = sym_delta.get(sym, 0.0)
        out[sym] = {
            "block_count": len(bl),
            "blocked_loss_count": sum(1 for b in bl if float(b.get("pnl_yen") or 0) < 0),
            "blocked_win_count": sum(1 for b in bl if float(b.get("pnl_yen") or 0) > 0),
            "blocked_loss_pnl": round(sum(float(b.get("pnl_yen") or 0) for b in bl if float(b.get("pnl_yen") or 0) < 0), 2),
            "blocked_win_pnl": round(sum(float(b.get("pnl_yen") or 0) for b in bl if float(b.get("pnl_yen") or 0) > 0), 2),
            "delta_pnl": delta,
            "contribution_rate": round(delta / total_delta, 4) if total_delta else 0.0,
        }
    return out


def _part_f(daily_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in daily_rows if str(r.get("day") or "") in FORWARD_DAYS]


def _verdict(
    *,
    part_a: Mapping[str, Any],
    loo: Mapping[str, Any],
    top_day_share: float,
    top_symbol_share: float,
    total_delta: float,
) -> str:
    if total_delta < 5000:
        return "overfit_reject"
    if top_day_share > 0.5 or top_symbol_share > 0.5:
        return "high_concentration"
    if float(loo.get("median_delta") or 0) < 0:
        return "overfit_reject"
    improved = int(part_a.get("improved_days") or 0)
    worsened = int(part_a.get("worsened_days") or 0)
    if (
        float(loo.get("mean_delta") or 0) > 10000
        and float(loo.get("median_delta") or 0) > 5000
        and improved >= worsened
        and top_day_share < 0.4
        and top_symbol_share < 0.4
    ):
        return "robust_runtime_candidate"
    if total_delta > 0 and float(loo.get("median_delta") or 0) > 0:
        return "shadow_only_candidate"
    return "overfit_reject"


def run_phase457_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    enriched = _enrich_candidates(_load_candidate_stream(repo_root), kabu=kabu)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    for t in enriched:
        t.update(enrich_trade_phase456c_features(t, price_idx=price_idx))

    np_shadows = _precompute_np_shadows(enriched, kabu=kabu, np_policy=BEST_NP_POLICY)
    base, d4 = _run_pair(enriched, np_shadows)

    total_base = _total_pnl(base)
    total_d4 = _total_pnl(d4)
    total_delta = round(total_d4 - total_base, 2)

    daily_rows = _daily_rows(base, d4)
    part_a = _part_a(daily_rows)
    loo = _part_b_loo(enriched, np_shadows)
    top_day_share = _top_day_share(daily_rows, total_delta)
    loo["top_day_share"] = top_day_share

    symbol_rows = _symbol_rows(base, d4)
    top20 = symbol_rows[:20]
    top_symbol_share = _top_symbol_share(symbol_rows, total_delta)

    blocked = _blocked_trades(enriched)
    part_d = _part_d(blocked, symbol_rows, total_delta)
    forward_rows = _part_f(daily_rows)

    loss_n = sum(1 for b in blocked if float(b.get("pnl_yen") or 0) < 0)
    win_n = sum(1 for b in blocked if float(b.get("pnl_yen") or 0) > 0)

    verdict = _verdict(
        part_a=part_a,
        loo=loo,
        top_day_share=top_day_share,
        top_symbol_share=top_symbol_share,
        total_delta=total_delta,
    )
    overfit = "high" if verdict == "overfit_reject" else (
        "medium" if verdict == "high_concentration" else "low"
    )
    shadow_ok = float(loo.get("median_delta") or 0) > 0 and total_delta > 5000

    mandatory = {
        "1_improved_days": part_a.get("improved_days"),
        "2_worsened_days": part_a.get("worsened_days"),
        "3_max_improve_day": part_a.get("max_improve_day"),
        "4_max_worsen_day": part_a.get("max_worsen_day"),
        "5_loo_mean_delta": loo.get("mean_delta"),
        "6_loo_median_delta": loo.get("median_delta"),
        "7_top_day_share": top_day_share,
        "8_top_symbol_share": top_symbol_share,
        "9_6976_contribution_rate": part_d.get("6976", {}).get("contribution_rate"),
        "10_4062_contribution_rate": part_d.get("4062", {}).get("contribution_rate"),
        "11_block_loss_win": f"{loss_n}L / {win_n}W",
        "12_overfit_judgment": overfit,
        "13_runtime_candidate": verdict == "robust_runtime_candidate",
        "14_shadow_candidate": shadow_ok and verdict != "overfit_reject",
        "15_next_actions": [
            "Shadow-only D4 — LOO robust but 6/9 day concentration (50%)"
            if verdict == "high_concentration"
            else ("Runtime shadow D4" if verdict == "robust_runtime_candidate" else "Do not deploy D4"),
            "Phase457B extended walk-forward",
        ],
        "verdict": verdict,
        "total_delta_pnl": total_delta,
        "baseline_pnl": total_base,
        "d4_pnl": total_d4,
        "blocked_count": len(blocked),
    }

    robustness_rows: list[dict[str, Any]] = [
        {"section": "summary", "metric": k, "value": v, "detail": ""}
        for k, v in mandatory.items()
        if k not in ("15_next_actions",)
    ]
    for r in loo.get("loo_rows") or []:
        robustness_rows.append(
            {
                "section": "loo",
                "metric": "loo_delta",
                "value": r.get("loo_delta"),
                "detail": r.get("excluded_day"),
            }
        )
    for sym, info in part_d.items():
        for mk, mv in info.items():
            robustness_rows.append(
                {"section": f"symbol_{sym}", "metric": mk, "value": mv, "detail": sym}
            )

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "d4_rule": {
            "consecutive_above_ticks_lt": D4_CONSECUTIVE_ABOVE_LO,
            "vwap_dev_pct_lt": D4_VWAP_DEV_PCT_LO,
        },
        "part_a": part_a,
        "part_b_loo": {k: v for k, v in loo.items() if k != "loo_rows"},
        "part_d_symbols": part_d,
        "part_e_blocks": blocked,
        "part_f_forward": forward_rows,
        "mandatory_answers": mandatory,
        "verdict": verdict,
        "_robustness_rows": robustness_rows,
        "_daily_rows": daily_rows,
        "_symbol_rows": symbol_rows,
        "_symbol_top20": top20,
    }


@dataclass
class Phase457Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase457_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "robustness": reports / "phase457_vwap_structure_robustness.csv",
            "daily": reports / "phase457_vwap_structure_daily.csv",
            "symbol": reports / "phase457_vwap_structure_symbol.csv",
            "summary": reports / "phase457_vwap_structure_summary.json",
        }
        _write_csv(paths["robustness"], ROBUSTNESS_FIELDS, list(result.get("_robustness_rows") or []))
        _write_csv(paths["daily"], DAILY_FIELDS, list(result.get("_daily_rows") or []))
        _write_csv(paths["symbol"], SYMBOL_FIELDS, list(result.get("_symbol_top20") or []))

        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase457_vwap_structure_robustness.md"
        m = result.get("mandatory_answers") or {}
        pa = result.get("part_a") or {}
        report.write_text(
            "\n".join(
                [
                    "# Phase457 — VWAP Structure Robustness Audit",
                    "",
                    f"Generated: {result.get('generated_at')}",
                    f"Period: {result.get('period_start')}..{result.get('period_end')}",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    "",
                    "## D4 rule",
                    "",
                    f"- `consecutive_above_ticks < {D4_CONSECUTIVE_ABOVE_LO}`",
                    f"- `vwap_dev_pct < {D4_VWAP_DEV_PCT_LO}`",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. Improved days: **{m.get('1_improved_days')}**",
                    f"2. Worsened days: **{m.get('2_worsened_days')}**",
                    f"3. Max improve day: **{m.get('3_max_improve_day')}** ({pa.get('max_improve_delta')} yen)",
                    f"4. Max worsen day: **{m.get('4_max_worsen_day')}** ({pa.get('max_worsen_delta')} yen)",
                    f"5. LOO mean delta: **{m.get('5_loo_mean_delta')}**",
                    f"6. LOO median delta: **{m.get('6_loo_median_delta')}**",
                    f"7. top_day_share: **{m.get('7_top_day_share')}**",
                    f"8. top_symbol_share: **{m.get('8_top_symbol_share')}**",
                    f"9. 6976 contribution: **{m.get('9_6976_contribution_rate')}**",
                    f"10. 4062 contribution: **{m.get('10_4062_contribution_rate')}**",
                    f"11. Block L/W: **{m.get('11_block_loss_win')}**",
                    f"12. Overfit: **{m.get('12_overfit_judgment')}**",
                    f"13. Runtime candidate: **{m.get('13_runtime_candidate')}**",
                    f"14. Shadow candidate: **{m.get('14_shadow_candidate')}**",
                    f"15. Next: {m.get('15_next_actions')}",
                    "",
                    "See CSV/JSON outputs for full daily, symbol, and block detail.",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        paths["report"] = report
        return paths
