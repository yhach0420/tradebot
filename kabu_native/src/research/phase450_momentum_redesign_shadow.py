"""
Phase450 — Momentum gate redesign shadow.

Shadow-evaluates momentum ENTRY gate variants vs Phase423 baseline replay
(20260529–20260619, CAP5, capacity-aware).

Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import (
    CANONICAL_BASELINE_END,
    PERIOD_START,
    load_canonical_live_config_trades,
)
from research.market_sector_heat import _pf, _write_csv
from research.phase271_leverage_attribution_and_robustness import simulate_audited
from research.phase382_capital_constrained_backtest import _day_from_ts, _float, _parse_ts, _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import (
    _build_price_index,
    _enrich_trades,
    _load_accepted_index,
)
from research.phase438_momentum_low_audit import _day_high_context
from research.phase443_full_runtime_combined_capital_sim import (
    CAP,
    LEVERAGE,
    STARTING_EQUITY,
    STOP_POLICY,
    _chronological_pnls_from_log,
    _stop_rate_from_log,
    simulate_capacity_replay,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_expectancy_score_shadow import (
    ENTRY_SCORE_V2_GATE_MIN,
    SCORE_POINTS_V2,
    TERTILE_CUTOFFS,
    active_score_tokens_v2,
    momentum_low_required_for_v2,
)

JST = ZoneInfo("Asia/Tokyo")

PERIOD_END = "20260619"
DAY_618 = "20260618"
DAY_619 = "20260619"
MOMENTUM_LOW_CUTOFF = float(TERTILE_CUTOFFS["Momentum"]["p33"])

TARGET_SYMBOLS = ("6976.T", "6920.T", "4062.T")

COMPARISON_FIELDS = [
    "variant",
    "total_pnl_yen",
    "delta_pnl_vs_baseline",
    "profit_factor",
    "delta_pf_vs_baseline",
    "max_drawdown_yen",
    "delta_maxdd_vs_baseline",
    "stop_rate",
    "delta_stop_rate_vs_baseline",
    "accepted_count",
    "reject_count",
    "gate_reject_count",
    "daily_pnl_618",
    "delta_daily_pnl_618",
    "daily_pnl_619",
    "delta_daily_pnl_619",
    "symbol_pnl_6976",
    "delta_symbol_pnl_6976",
    "symbol_pnl_6920",
    "delta_symbol_pnl_6920",
    "symbol_pnl_4062",
    "delta_symbol_pnl_4062",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _optional_float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _load_candidate_stream(repo_root: Path) -> list[dict[str, Any]]:
    trades, _meta = load_canonical_live_config_trades(
        repo_root,
        period_start=PERIOD_START,
        baseline_end=CANONICAL_BASELINE_END,
    )
    out: list[dict[str, Any]] = []
    for t in trades:
        day = str(t.get("day") or "")
        if day < PERIOD_START or day > PERIOD_END:
            continue
        if _parse_ts(str(t.get("entry_time") or "")) is None:
            continue
        if _float(t.get("entry_price")) <= 0:
            continue
        out.append(dict(t))
    out.sort(
        key=lambda r: (
            _parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST),
            str(r.get("symbol") or ""),
        )
    )
    return out


def _enrich_candidates(candidates: Sequence[Mapping[str, Any]], *, kabu: Path) -> list[dict[str, Any]]:
    accepted_idx = _load_accepted_index(kabu)
    price_idx = _build_price_index(kabu)
    rows = _enrich_trades(list(candidates), kabu_root=kabu, accepted_idx=accepted_idx, price_idx=price_idx)
    out: list[dict[str, Any]] = []
    for row in rows:
        t = dict(row)
        key = (str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
        acc = dict(accepted_idx.get(key, {}))
        for fld in ("quality_fallback_path", "entry_rise_15min_pct", "pure_price_momentum"):
            if acc.get(fld) not in (None, ""):
                t[fld] = acc[fld]
        ctx = _day_high_context(t, price_idx=price_idx)
        t.update(ctx)
        mins = _optional_float(ctx.get("minutes_since_day_high_update"))
        t["recent_high_update"] = 1 if mins is not None and mins <= 15.0 else 0
        t["day_high_distance_pct"] = t.get("day_high_distance_pct") or t.get("entry_near_day_high_pct")
        out.append(t)
    return out


def _v2_entry_score(trade: Mapping[str, Any]) -> int:
    tokens = active_score_tokens_v2(trade)
    return sum(SCORE_POINTS_V2.get(tok, 0) for tok in tokens)


def _board_mid_ok(trade: Mapping[str, Any]) -> bool:
    return "Board:mid" in active_score_tokens_v2(trade)


def trend_assisted_momentum_score(trade: Mapping[str, Any]) -> float:
    """Variant B: blend legacy score with r15/r30/day-high proximity."""
    base = _optional_float(trade.get("momentum_continuation_score")) or 0.0
    r15 = _optional_float(trade.get("return_15min_pct"))
    r30 = _optional_float(trade.get("return_30min_pct"))
    dist = abs(
        _optional_float(trade.get("day_high_distance_pct") or trade.get("entry_near_day_high_pct")) or 0.0
    )
    r15_n = 0.5 if r15 is None else min(1.0, max(0.0, (r15 + 0.3) / 0.6))
    r30_n = 0.5 if r30 is None else min(1.0, max(0.0, (r30 + 0.45) / 0.9))
    dist_n = min(1.0, max(0.0, 1.0 - dist / 2.5))
    return min(1.0, max(0.0, 0.45 * base + 0.25 * r15_n + 0.20 * r30_n + 0.10 * dist_n))


def _momentum_low_raw(trade: Mapping[str, Any]) -> bool:
    return momentum_low_required_for_v2(trade)


def _momentum_low_trend_assisted(trade: Mapping[str, Any]) -> bool:
    return trend_assisted_momentum_score(trade) <= MOMENTUM_LOW_CUTOFF


def _passes_baseline_entry(trade: Mapping[str, Any]) -> bool:
    return _momentum_low_raw(trade) and _board_mid_ok(trade) and _v2_entry_score(trade) >= ENTRY_SCORE_V2_GATE_MIN


def _passes_variant_b(trade: Mapping[str, Any]) -> bool:
    return _momentum_low_trend_assisted(trade) and _board_mid_ok(trade) and _v2_entry_score(trade) >= ENTRY_SCORE_V2_GATE_MIN


def _passes_variant_c(trade: Mapping[str, Any]) -> bool:
    return (
        _passes_baseline_entry(trade)
        and int(trade.get("recent_high_update") or 0) == 1
    )


def _passes_variant_d(trade: Mapping[str, Any]) -> bool:
    if not _passes_baseline_entry(trade):
        return False
    if str(trade.get("quality_fallback_path") or "").lower() == "true":
        return False
    mom = _optional_float(trade.get("momentum_continuation_score"))
    if mom is not None and mom <= 0.01:
        return False
    if _optional_float(trade.get("entry_vwap_dev_pct")) is None:
        return False
    return True


def _passes_variant_e(trade: Mapping[str, Any]) -> bool:
    return (
        _passes_variant_b(trade)
        and int(trade.get("recent_high_update") or 0) == 1
        and str(trade.get("quality_fallback_path") or "").lower() != "true"
        and (_optional_float(trade.get("momentum_continuation_score")) or 0.0) > 0.01
        and _optional_float(trade.get("entry_vwap_dev_pct")) is not None
    )


def _make_block_fn(pass_fn: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
    def block(trade: Mapping[str, Any]) -> bool:
        return not pass_fn(trade)

    return block


VARIANTS: tuple[tuple[str, str, Callable[[Mapping[str, Any]], bool]], ...] = (
    ("A_baseline", "baseline", _passes_baseline_entry),
    ("B_trend_assisted", "trend_assisted_momentum", _passes_variant_b),
    ("C_recent_high_update", "opening_peak", _passes_variant_c),
    ("D_fallback_reject", "fallback_path", _passes_variant_d),
    ("E_combined", "combined", _passes_variant_e),
)


def _symbol_pnl_from_state(state: Any) -> dict[str, float]:
    out: dict[str, float] = {sym: 0.0 for sym in TARGET_SYMBOLS}
    for row in state.trade_log:
        sym = str(row.get("symbol") or "")
        if sym in out:
            out[sym] += float(row.get("pnl_yen") or 0.0)
    return {k: round(v, 2) for k, v in out.items()}


def _metrics_from_state(state: Any, *, variant: str) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    total = round(sum(chron), 2)
    sym_pnl = _symbol_pnl_from_state(state)
    gate_rejects = sum(
        1
        for r in (state.reject_log or [])
        if str(r.get("reason") or "") not in (
            "max_concurrent_positions",
            "insufficient_buying_power",
            "same_symbol_open",
        )
    )
    return {
        "variant": variant,
        "total_pnl_yen": total,
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron),
        "stop_rate": _stop_rate_from_log(state.trade_log),
        "accepted_count": state.accepted_trade_count,
        "reject_count": state.rejected_trade_count,
        "gate_reject_count": gate_rejects,
        "daily_pnl_618": round(float(state.daily_pnls.get(DAY_618, 0.0)), 2),
        "daily_pnl_619": round(float(state.daily_pnls.get(DAY_619, 0.0)), 2),
        **{f"symbol_pnl_{sym.replace('.T', '')}": sym_pnl.get(sym, 0.0) for sym in TARGET_SYMBOLS},
        "_state": state,
    }


def _verdict(best_variant: str) -> str:
    mapping = {
        "B_trend_assisted": "trend_assisted_momentum_candidate",
        "C_recent_high_update": "opening_peak_candidate",
        "D_fallback_reject": "fallback_path_candidate",
        "E_combined": "combined_candidate",
    }
    return mapping.get(best_variant, "combined_candidate")


def run_phase450_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    candidates = _load_candidate_stream(repo_root)
    enriched = _enrich_candidates(candidates, kabu=kabu)

    baseline_sim = simulate_audited(
        enriched,
        starting_equity=STARTING_EQUITY,
        leverage=LEVERAGE,
        cap=CAP,
        stop_policy=STOP_POLICY,
    )
    baseline_keys = set((baseline_sim.get("accepted_pnls") or {}).keys())

    metrics_rows: list[dict[str, Any]] = []
    for variant_id, _label, pass_fn in VARIANTS:
        state = simulate_capacity_replay(
            enriched,
            {},
            mode=variant_id,
            entry_block_fn=_make_block_fn(pass_fn),
            baseline_accepted_keys=baseline_keys,
        )
        m = _metrics_from_state(state, variant=variant_id)
        metrics_rows.append(m)

    base = metrics_rows[0]
    base_pnl = float(base["total_pnl_yen"])
    base_pf = float(base["profit_factor"] or 0.0)
    base_dd = float(base["max_drawdown_yen"] or 0.0)
    base_stop = float(base["stop_rate"] or 0.0)
    base_618 = float(base["daily_pnl_618"])
    base_619 = float(base["daily_pnl_619"])
    base_sym = {sym.replace(".T", ""): float(base.get(f"symbol_pnl_{sym.replace('.T', '')}") or 0.0) for sym in TARGET_SYMBOLS}

    for m in metrics_rows:
        m["delta_pnl_vs_baseline"] = round(float(m["total_pnl_yen"]) - base_pnl, 2)
        m["delta_pf_vs_baseline"] = round(float(m["profit_factor"] or 0) - base_pf, 4)
        m["delta_maxdd_vs_baseline"] = round(float(m["max_drawdown_yen"] or 0) - base_dd, 2)
        m["delta_stop_rate_vs_baseline"] = round(float(m["stop_rate"] or 0) - base_stop, 4)
        m["delta_daily_pnl_618"] = round(float(m["daily_pnl_618"]) - base_618, 2)
        m["delta_daily_pnl_619"] = round(float(m["daily_pnl_619"]) - base_619, 2)
        for sym in TARGET_SYMBOLS:
            key = f"symbol_pnl_{sym.replace('.T', '')}"
            m[f"delta_{key}"] = round(float(m.get(key) or 0) - base_sym[sym.replace(".T", "")], 2)

    challengers = [m for m in metrics_rows if m["variant"] != "A_baseline"]
    best = max(challengers, key=lambda r: float(r["delta_pnl_vs_baseline"]))
    verdict = _verdict(str(best["variant"]))
    any_positive = any(float(m["delta_pnl_vs_baseline"]) > 0 for m in challengers)

    summary = {
        "phase": "450-Momentum-Gate-Redesign-Shadow",
        "generated_at": _now_iso(),
        "verdict": verdict,
        "period": f"{PERIOD_START}..{PERIOD_END}",
        "baseline_gate": {
            "momentum_score": "0.40*price_mom + 0.25*vwap_part + 0.35*mfe_proxy",
            "momentum_low_cutoff": MOMENTUM_LOW_CUTOFF,
            "board_mid_required": True,
            "entry_score_v2_min": ENTRY_SCORE_V2_GATE_MIN,
        },
        "variants": {
            "B_trend_assisted": "trend_assisted_momentum_score <= cutoff with r15/r30/day_high blend",
            "C_recent_high_update": "recent_high_update=1 (day high updated within 15m)",
            "D_fallback_reject": "reject quality_fallback_path or score<=0.01 or vwap missing",
            "E_combined": "B + C + D",
        },
        "candidate_count": len(enriched),
        "comparison": [{k: m.get(k) for k in COMPARISON_FIELDS} for m in metrics_rows],
        "best_variant": best["variant"],
        "any_variant_beats_baseline_pnl": any_positive,
        "note_619": "20260619 not in canonical candidate stream — daily_pnl_619=0 for all variants",
        "mandatory_answers": {
            "1_best_variant": best["variant"],
            "2_pnl_improvement_yen": best["delta_pnl_vs_baseline"],
            "3_pf_improvement": best["delta_pf_vs_baseline"],
            "4_maxdd_improvement_yen": best["delta_maxdd_vs_baseline"],
            "5_stop_rate_improvement": best["delta_stop_rate_vs_baseline"],
            "6_delta_618": best["delta_daily_pnl_618"],
            "7_delta_619": best["delta_daily_pnl_619"],
            "8_delta_6976": best.get("delta_symbol_pnl_6976"),
            "9_delta_6920": best.get("delta_symbol_pnl_6920"),
            "10_runtime_adoption_candidate": float(best["delta_pnl_vs_baseline"]) > 0
            and float(best["delta_maxdd_vs_baseline"]) <= 0,
            "4062_delta_yen": best.get("delta_symbol_pnl_4062"),
        },
    }

    public_rows = [{k: m.get(k) for k in COMPARISON_FIELDS if not k.startswith("_")} for m in metrics_rows]
    return {"summary": summary, "_comparison_rows": public_rows}


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    m = s.get("mandatory_answers") or {}
    cmp_ = s.get("comparison") or []
    lines = [
        "# Phase450 — Momentum Gate Redesign Shadow",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Verdict: **{s.get('verdict')}**",
        f"Period: {s.get('period')}",
        "",
        "## Comparison",
        "",
        "| Variant | PnL | ΔPnL | PF | MaxDD | Stop | Acc | 6/18 Δ | 6/19 Δ | 6976 Δ | 6920 Δ | 4062 Δ |",
        "|---------|-----|------|-----|-------|------|-----|--------|--------|--------|--------|--------|",
    ]
    for row in cmp_:
        lines.append(
            f"| {row.get('variant')} | {row.get('total_pnl_yen')} | {row.get('delta_pnl_vs_baseline')} | "
            f"{row.get('profit_factor')} | {row.get('max_drawdown_yen')} | {row.get('stop_rate')} | "
            f"{row.get('accepted_count')} | {row.get('delta_daily_pnl_618')} | {row.get('delta_daily_pnl_619')} | "
            f"{row.get('delta_symbol_pnl_6976')} | {row.get('delta_symbol_pnl_6920')} | {row.get('delta_symbol_pnl_4062')} |"
        )
    lines.extend(
        [
            "",
            "## Mandatory answers",
            "",
            f"1. 最良variant: **{m.get('1_best_variant')}**",
            f"2. PnL改善: {m.get('2_pnl_improvement_yen')} yen",
            f"3. PF改善: {m.get('3_pf_improvement')}",
            f"4. maxDD改善: {m.get('4_maxdd_improvement_yen')} yen",
            f"5. stop率改善: {m.get('5_stop_rate_improvement')}",
            f"6. 6/18改善: {m.get('6_delta_618')} yen",
            f"7. 6/19改善: {m.get('7_delta_619')} yen",
            f"8. 6976改善: {m.get('8_delta_6976')} yen",
            f"9. 6920改善: {m.get('9_delta_6920')} yen",
            f"10. Runtime採用候補: {m.get('10_runtime_adoption_candidate')} (4062 Δ: {m.get('4062_delta_yen')} yen)",
        ]
    )
    return "\n".join(lines) + "\n"


@dataclass
class Phase450Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase450_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "comparison": reports / "phase450_momentum_redesign_shadow.csv",
            "summary": reports / "phase450_momentum_redesign_summary.json",
            "report": kabu / "docs" / "operations" / "phase450_momentum_redesign_shadow_report.md",
        }
        _write_csv(paths["comparison"], COMPARISON_FIELDS, result.get("_comparison_rows") or [])
        paths["summary"].write_text(
            json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text(render_report_md(result), encoding="utf-8")
        return paths
