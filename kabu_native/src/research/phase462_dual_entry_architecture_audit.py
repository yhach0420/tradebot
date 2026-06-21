"""
Phase462 — Dual Entry Architecture Audit (research only).

Compares Pullback (current runtime) vs Trend entry vs Dual (OR) architecture.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv, read_jpx_sector_map
from research.phase382_capital_constrained_backtest import _position_key
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase436_pullback_guard_redesign_shadow import _stream_events, guard_high_drift
from research.phase441_boundary_no_progress_overlap_audit import BEST_NP_POLICY, _precompute_np_shadows
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import (
    DAY_619,
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _chronological_pnls_from_log,
    _enrich_candidates,
    _load_candidate_stream,
    _now_iso,
    _optional_float,
    _symbol_pnl_from_log,
)
from research.phase451b_entry_shape_tournament_mid_high import _board_token, _passes_baseline_mid_high
from research.phase456_entry_features import enrich_trade_phase456_features
from research.phase456c_vwap_structure_features import enrich_trade_phase456c_features
from research.phase459_winner_pattern_audit import _stop_rate_from_log
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.weak_shape_reject_entry_guard import would_block_weak_shape_reject

REPLAY_MODE = "phase456_runtime_np"
TARGET_SYMBOLS = ("6976.T", "4062.T", "3441.T", "6492.T", "7256.T", "7600.T")
MISSED_UPTREND = ("3441.T", "6492.T", "7256.T", "6466.T", "7600.T")

ARCHITECTURE_FIELDS = [
    "cohort",
    "count",
    "total_pnl_yen",
    "profit_factor",
    "max_drawdown_yen",
    "accepted_count",
    "stop_rate",
]

SYMBOL_FIELDS = [
    "symbol",
    "pullback_captured",
    "trend_captured",
    "dual_captured",
    "pullback_pnl_yen",
    "trend_pnl_yen",
    "dual_pnl_yen",
]

MISSED_FIELDS = [
    "symbol",
    "day",
    "was_candidate",
    "passes_trend",
    "passes_pullback",
    "dual_replay_captured",
    "trend_replay_captured",
    "best_r15",
    "best_r30",
    "best_vwap_above_ratio",
    "best_high_update_count_30m",
]


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


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


def _rise(trade: Mapping[str, Any], mins: int) -> Optional[float]:
    return _float(trade.get(f"return_{mins}min_pct")) or _float(trade.get(f"entry_rise_{mins}min_pct"))


def _vwap_above_ratio(trade: Mapping[str, Any]) -> Optional[float]:
    return _float(trade.get("vwap_above_ratio")) or _float(trade.get("vwap_above_ratio_20tick"))


def _board_mid_high(trade: Mapping[str, Any]) -> bool:
    tok = _board_token(trade) or ""
    return tok in ("Board:mid", "Board:high")


def _weak_shape_block(trade: Mapping[str, Any]) -> bool:
    return would_block_weak_shape_reject(_map_runtime_fields(trade))


def passes_pullback(trade: Mapping[str, Any]) -> bool:
    if not _passes_baseline_mid_high(trade):
        return False
    if guard_high_drift(trade):
        return False
    if _weak_shape_block(trade):
        return False
    return True


def passes_trend(trade: Mapping[str, Any]) -> bool:
    if not _board_mid_high(trade):
        return False
    if (_rise(trade, 15) or -1e18) <= 0:
        return False
    if (_rise(trade, 30) or -1e18) <= 0:
        return False
    if (_vwap_above_ratio(trade) or 0) < 0.5:
        return False
    if (_float(trade.get("high_update_count_30m")) or 0) < 2:
        return False
    return True


def _iter_sessions(kabu_root: Path) -> list[tuple[str, Path]]:
    base = kabu_root / "results" / "small_paper"
    out: list[tuple[str, Path]] = []
    if not base.is_dir():
        return out
    for day_dir in sorted(base.iterdir()):
        if not day_dir.is_dir() or day_dir.name < PERIOD_START or day_dir.name > PERIOD_END:
            continue
        for sess in sorted(day_dir.iterdir()):
            if sess.is_dir() and sess.name.startswith("live_session"):
                out.append((day_dir.name, sess))
    return out


def _load_day619_inject(kabu: Path) -> list[dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for day, sess in _iter_sessions(kabu):
        if day != DAY_619:
            continue
        for row in _stream_events(sess / "small_paper_events.csv"):
            etype = str(row.get("event_type") or "")
            if etype not in ("candidate", "rejected", "reject"):
                continue
            sym = str(row.get("symbol") or "")
            et = str(row.get("entry_time") or row.get("event_time") or "")
            if not sym or not et:
                continue
            key = f"{sym}|{et}"
            rec = records.setdefault(
                key,
                {"symbol": sym, "day": day, "entry_time": et, "outcome": etype},
            )
            px = _float(row.get("entry_price") or row.get("current_price"))
            if px:
                rec["entry_price"] = px
            for fld, val in row.items():
                if val not in (None, ""):
                    rec[fld] = val
    return list(records.values())


def _enrich_all(
    trades: Sequence[Mapping[str, Any]],
    *,
    price_idx: Mapping[tuple[str, str], list],
    sector_map: Mapping[str, str],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        row = dict(t)
        row.update(enrich_trade_phase456_features(row, price_idx=price_idx, sector_map=sector_map))
        row.update(enrich_trade_phase456c_features(row, price_idx=price_idx))
        out.append(row)
    return out


def _cohort_metrics(
    trades: Sequence[Mapping[str, Any]],
    *,
    cohort: str,
    pnl_key: str = "pnl_yen",
) -> dict[str, Any]:
    pnls = [_float(t.get(pnl_key) or t.get("pnl_yen_100")) or 0.0 for t in trades]
    chron = pnls  # attribution uses per-trade order approx
    return {
        "cohort": cohort,
        "count": len(trades),
        "total_pnl_yen": round(sum(pnls), 2),
        "profit_factor": _pf(pnls),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "accepted_count": len(trades),
        "stop_rate": None,
    }


def _replay_metrics(state: Any, *, variant: str) -> dict[str, Any]:
    chron = _chronological_pnls_from_log(state.trade_log)
    sym_pnl = _symbol_pnl_from_log(state.trade_log)
    accepted_syms = {str(r.get("symbol") or "") for r in state.trade_log}
    return {
        "cohort": variant,
        "count": state.accepted_trade_count,
        "total_pnl_yen": round(sum(chron), 2),
        "profit_factor": _pf(chron),
        "max_drawdown_yen": _max_drawdown_yen(chron) if chron else 0.0,
        "accepted_count": state.accepted_trade_count,
        "stop_rate": _stop_rate_from_log(state.trade_log),
        **{f"captured_{s.replace('.T', '')}": s in accepted_syms for s in TARGET_SYMBOLS},
        **{f"symbol_pnl_{s.replace('.T', '')}": sym_pnl.get(s.replace(".T", ""), 0.0) for s in TARGET_SYMBOLS},
    }


def _entry_block(pass_fn: Callable[[Mapping[str, Any]], bool]) -> Callable[[Mapping[str, Any]], bool]:
    def block(trade: Mapping[str, Any]) -> bool:
        return not pass_fn(trade)

    return block


def _verdict(
    *,
    pullback: Mapping[str, Any],
    trend: Mapping[str, Any],
    dual: Mapping[str, Any],
    missed_captured: int,
) -> str:
    p_pb = float(pullback.get("total_pnl_yen") or 0)
    p_tr = float(trend.get("total_pnl_yen") or 0)
    p_du = float(dual.get("total_pnl_yen") or 0)
    if p_du > max(p_pb, p_tr) + 5000 and missed_captured >= 2:
        return "dual_entry_candidate"
    if p_du > max(p_pb, p_tr) + 10000:
        return "dual_entry_candidate"
    if p_pb >= p_tr and p_pb >= p_du:
        return "pullback_only_superior"
    if p_tr > p_pb and p_tr >= p_du:
        return "trend_only_superior"
    if abs(p_du - max(p_pb, p_tr)) < 5000:
        return "no_dual_edge"
    return "no_dual_edge"


def run_phase462_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)
    sector_map = read_jpx_sector_map(kabu)
    enriched_base = _enrich_all(
        _enrich_candidates(_load_candidate_stream(repo_root), kabu=kabu),
        price_idx=price_idx,
        sector_map=sector_map,
    )

    inject_raw = _load_day619_inject(kabu)
    inject = _enrich_all(inject_raw, price_idx=price_idx, sector_map=sector_map)
    base_keys = {_position_key(t) for t in enriched_base}
    enriched = list(enriched_base) + [t for t in inject if _position_key(t) not in base_keys]
    np_shadows = _precompute_np_shadows(enriched, kabu=kabu, np_policy=BEST_NP_POLICY)

    # Part A — classify baseline (pullback-only) accepted trades by entry shape
    pb_state = simulate_capacity_replay(
        enriched,
        np_shadows,
        mode=f"{REPLAY_MODE}_pullback",
        entry_block_fn=_entry_block(passes_pullback),
        baseline_accepted_keys=set(),
    )
    pb_log = []
    for row in pb_state.trade_log:
        tr = dict(row.get("trade") or {})
        tr["pnl_yen"] = row.get("pnl_yen")
        pb_log.append(tr)

    pullback_trades = [t for t in pb_log if passes_pullback(t)]
    trend_trades = [t for t in pb_log if passes_trend(t)]
    part_a_rows = [
        _cohort_metrics(pullback_trades, cohort="Pullback型"),
        _cohort_metrics(trend_trades, cohort="Trend型"),
    ]
    for r in part_a_rows:
        if r["cohort"] == "Pullback型":
            r["stop_rate"] = _stop_rate_from_log(pb_state.trade_log)

    # Part B — overlap on full candidate pool (shadow pnl)
    overlap: dict[str, list[dict[str, Any]]] = {
        "A_pullback_only": [],
        "B_trend_only": [],
        "both": [],
        "neither": [],
    }
    for t in enriched:
        key = _position_key(t)
        sh = np_shadows.get(key)
        pnl = float(sh.shadow_pnl_yen) if sh and sh.eval_ok else 0.0
        row = {**t, "shadow_pnl_yen": pnl}
        pb = passes_pullback(t)
        tr = passes_trend(t)
        if pb and not tr:
            overlap["A_pullback_only"].append(row)
        elif tr and not pb:
            overlap["B_trend_only"].append(row)
        elif pb and tr:
            overlap["both"].append(row)
        else:
            overlap["neither"].append(row)

    part_b_rows = [
        _cohort_metrics(overlap[k], cohort=k, pnl_key="shadow_pnl_yen") for k in overlap
    ]

    # Part C — replay D/E/C
    def _pass_dual(t: Mapping[str, Any]) -> bool:
        return passes_pullback(t) or passes_trend(t)

    replay_variants = {
        "D_pullback_only": passes_pullback,
        "E_trend_only": passes_trend,
        "C_dual_entry": _pass_dual,
    }
    replay_rows: list[dict[str, Any]] = []
    replay_by_variant: dict[str, dict[str, Any]] = {}
    for vid, fn in replay_variants.items():
        st = simulate_capacity_replay(
            enriched,
            np_shadows,
            mode=f"{REPLAY_MODE}_{vid}",
            entry_block_fn=_entry_block(fn),
            baseline_accepted_keys=set(),
        )
        m = _replay_metrics(st, variant=vid)
        replay_rows.append(m)
        replay_by_variant[vid] = m

    # Part D — symbol analysis
    symbol_rows: list[dict[str, Any]] = []
    for sym in TARGET_SYMBOLS:
        code = sym.replace(".T", "")
        symbol_rows.append(
            {
                "symbol": sym,
                "pullback_captured": replay_by_variant["D_pullback_only"].get(f"captured_{code}"),
                "trend_captured": replay_by_variant["E_trend_only"].get(f"captured_{code}"),
                "dual_captured": replay_by_variant["C_dual_entry"].get(f"captured_{code}"),
                "pullback_pnl_yen": replay_by_variant["D_pullback_only"].get(f"symbol_pnl_{code}"),
                "trend_pnl_yen": replay_by_variant["E_trend_only"].get(f"symbol_pnl_{code}"),
                "dual_pnl_yen": replay_by_variant["C_dual_entry"].get(f"symbol_pnl_{code}"),
            }
        )

    # Part E — missed uptrend recovery (6/19)
    missed_rows: list[dict[str, Any]] = []
    by_sym_619: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in enriched:
        if str(t.get("day") or "") == DAY_619 and str(t.get("symbol") or "") in MISSED_UPTREND:
            by_sym_619[str(t.get("symbol") or "")].append(t)

    missed_captured = 0
    for sym in MISSED_UPTREND:
        cands = by_sym_619.get(sym, [])
        best = max(cands, key=lambda t: (_float(_rise(t, 15)) or -1e18), default=None)
        tr_ok = any(passes_trend(t) for t in cands)
        pb_ok = any(passes_pullback(t) for t in cands)
        dual_cap = bool(replay_by_variant["C_dual_entry"].get(f"captured_{sym.replace('.T', '')}"))
        trend_cap = bool(replay_by_variant["E_trend_only"].get(f"captured_{sym.replace('.T', '')}"))
        if dual_cap or trend_cap:
            missed_captured += 1
        missed_rows.append(
            {
                "symbol": sym,
                "day": DAY_619,
                "was_candidate": bool(cands),
                "passes_trend": tr_ok,
                "passes_pullback": pb_ok,
                "dual_replay_captured": dual_cap,
                "trend_replay_captured": trend_cap,
                "best_r15": _rise(best, 15) if best else None,
                "best_r30": _rise(best, 30) if best else None,
                "best_vwap_above_ratio": _vwap_above_ratio(best) if best else None,
                "best_high_update_count_30m": best.get("high_update_count_30m") if best else None,
            }
        )

    pb = replay_by_variant["D_pullback_only"]
    tr = replay_by_variant["E_trend_only"]
    du = replay_by_variant["C_dual_entry"]
    verdict = _verdict(pullback=pb, trend=tr, dual=du, missed_captured=missed_captured)

    def _sym_strategy(sym: str) -> str:
        code = sym.replace(".T", "")
        if du.get(f"captured_{code}") and not pb.get(f"captured_{code}"):
            return "Trend/Dual"
        if pb.get(f"captured_{code}"):
            return "Pullback"
        if tr.get(f"captured_{code}"):
            return "Trend"
        if du.get(f"captured_{code}"):
            return "Dual"
        return "None"

    mandatory = {
        "1_pullback_only_pnl": pb["total_pnl_yen"],
        "2_trend_only_pnl": tr["total_pnl_yen"],
        "3_dual_entry_pnl": du["total_pnl_yen"],
        "4_pf_comparison": {
            "pullback": pb["profit_factor"],
            "trend": tr["profit_factor"],
            "dual": du["profit_factor"],
        },
        "5_maxdd_comparison": {
            "pullback": pb["max_drawdown_yen"],
            "trend": tr["max_drawdown_yen"],
            "dual": du["max_drawdown_yen"],
        },
        "6_6976_strategy": _sym_strategy("6976.T"),
        "7_4062_strategy": _sym_strategy("4062.T"),
        "8_3441_strategy": _sym_strategy("3441.T"),
        "9_6492_strategy": _sym_strategy("6492.T"),
        "10_7256_strategy": _sym_strategy("7256.T"),
        "11_trend_has_independent_value": float(tr["total_pnl_yen"]) > float(pb["total_pnl_yen"]) * 0.5,
        "12_dual_runtime_candidate": verdict == "dual_entry_candidate",
        "13_next_actions": [
            "Shadow-test dual entry (Pullback OR Trend) before runtime split",
            "Trend path: r15>0, r30>0, vwap_above>=0.5, high_update>=2, board mid/high",
            "Walk-forward validate overlap cohort PnL on days after 6/19",
        ],
        "verdict": verdict,
        "missed_uptrend_trend_captured": missed_captured,
        "overlap_counts": {k: len(v) for k, v in overlap.items()},
    }

    architecture_rows = part_a_rows + part_b_rows + replay_rows

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "mandatory_answers": mandatory,
        "verdict": verdict,
        "part_a": part_a_rows,
        "part_b": part_b_rows,
        "part_c": replay_rows,
        "part_d": symbol_rows,
        "part_e": missed_rows,
        "_architecture_rows": architecture_rows,
        "_symbol_rows": symbol_rows,
        "_missed_rows": missed_rows,
    }


@dataclass
class Phase462Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase462_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "architecture": reports / "phase462_dual_entry_architecture.csv",
            "symbols": reports / "phase462_dual_entry_symbol_analysis.csv",
            "summary": reports / "phase462_dual_entry_summary.json",
        }
        _write_csv(paths["architecture"], ARCHITECTURE_FIELDS, list(result.get("_architecture_rows") or []))
        _write_csv(paths["symbols"], SYMBOL_FIELDS, list(result.get("_symbol_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase462_dual_entry_architecture.md"
        m = result.get("mandatory_answers") or {}
        pf = m.get("4_pf_comparison") or {}
        dd = m.get("5_maxdd_comparison") or {}
        lines = [
            "# Phase462 — Dual Entry Architecture Audit",
            "",
            f"Generated: {result.get('generated_at')}",
            f"Period: {result.get('period_start')}..{result.get('period_end')}",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            "",
            "## Strategy definitions",
            "",
            "- **A Pullback:** Momentum:low + Board:mid/high + NOT High Drift + NOT Weak Shape",
            "- **B Trend:** r15>0 AND r30>0 AND vwap_above_ratio>=0.5 AND high_update_count_30m>=2 AND Board:mid/high",
            "- **C Dual:** A OR B",
            "",
            "## Part C — Replay",
            "",
            "| variant | PnL | PF | maxDD | accepted | stop_rate |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for row in result.get("part_c") or []:
            lines.append(
                f"| {row.get('cohort')} | {row.get('total_pnl_yen')} | {row.get('profit_factor')} "
                f"| {row.get('max_drawdown_yen')} | {row.get('accepted_count')} | {row.get('stop_rate')} |"
            )
        lines.extend(
            [
                "",
                "## Part D — Symbol analysis",
                "",
                "| symbol | Pullback | Trend | Dual |",
                "|---|---|---|---|",
            ]
        )
        for row in result.get("part_d") or []:
            lines.append(
                f"| {row.get('symbol')} | {row.get('pullback_captured')} | {row.get('trend_captured')} "
                f"| {row.get('dual_captured')} |"
            )
        lines.extend(
            [
                "",
                "## Part E — Missed uptrend (6/19)",
                "",
                "| symbol | candidate | trend_pass | dual_cap | trend_cap |",
                "|---|---|---|---|---|",
            ]
        )
        for row in result.get("part_e") or []:
            lines.append(
                f"| {row.get('symbol')} | {row.get('was_candidate')} | {row.get('passes_trend')} "
                f"| {row.get('dual_replay_captured')} | {row.get('trend_replay_captured')} |"
            )
        lines.extend(
            [
                "",
                "## Mandatory answers",
                "",
                f"1. Pullback PnL: **{m.get('1_pullback_only_pnl')}**",
                f"2. Trend PnL: **{m.get('2_trend_only_pnl')}**",
                f"3. Dual PnL: **{m.get('3_dual_entry_pnl')}**",
                f"4. PF: Pullback **{pf.get('pullback')}** / Trend **{pf.get('trend')}** / Dual **{pf.get('dual')}**",
                f"5. maxDD: Pullback **{dd.get('pullback')}** / Trend **{dd.get('trend')}** / Dual **{dd.get('dual')}**",
                f"6–10. 6976/4062/3441/6492/7256: **{m.get('6_6976_strategy')}** / **{m.get('7_4062_strategy')}** / "
                f"**{m.get('8_3441_strategy')}** / **{m.get('9_6492_strategy')}** / **{m.get('10_7256_strategy')}**",
                f"11. Trend independent value: **{m.get('11_trend_has_independent_value')}**",
                f"12. Dual Runtime candidate: **{m.get('12_dual_runtime_candidate')}**",
                f"13. Next: {m.get('13_next_actions')}",
                "",
            ]
        )
        report.write_text("\n".join(lines), encoding="utf-8")
        paths["report"] = report
        return paths
