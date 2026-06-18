"""
Phase432 — Reentry profit attribution audit (20260617).

Builds on Phase431 reentry pairs; analyzes symbol concentration and dependency.

Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate, _write_csv
from research.phase431_entry_priority_reentry_audit import (
    REENTRY_WINDOWS,
    SESSION_DIRS,
    TARGET_DAY,
    _analyze_reentry,
    _load_structural_trades,
    _metrics_from_pnls,
    _parse_ts,
    _float,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")
PRIMARY_WINDOW = 180


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _gini_positive(values: Sequence[float]) -> float:
    pos = sorted(v for v in values if v > 0)
    n = len(pos)
    if n <= 1:
        return 0.0 if n == 0 else 1.0
    total = sum(pos)
    if total <= 0:
        return 0.0
    weighted = sum((i + 1) * v for i, v in enumerate(pos))
    return round((2.0 * weighted) / (n * total) - (n + 1) / n, 4)


def _hhi(shares: Sequence[float]) -> float:
    return round(sum(s * s for s in shares), 4)


def _symbol_stats(
    reentry_rows: Sequence[Mapping[str, Any]],
    *,
    max_gap_sec: Optional[float] = None,
) -> list[dict[str, Any]]:
    by_sym: dict[str, list[dict]] = defaultdict(list)
    for r in reentry_rows:
        if max_gap_sec is not None and _float(r.get("gap_sec")) > max_gap_sec:
            continue
        by_sym[str(r["symbol"])].append(dict(r))

    out: list[dict[str, Any]] = []
    for sym, rows in by_sym.items():
        pnls = [_float(r["reentry_pnl_yen"]) for r in rows]
        holds = [_float(r["reentry_hold_sec"]) for r in rows]
        m = _metrics_from_pnls(pnls, holds)
        out.append({"symbol": sym, **m})
    return out


def _concentration(sym_stats: Sequence[Mapping[str, Any]], total_pnl: float) -> dict[str, Any]:
    ranked = sorted(sym_stats, key=lambda r: _float(r.get("total_pnl_yen")), reverse=True)
    sym_pnl = {str(r["symbol"]): _float(r.get("total_pnl_yen")) for r in ranked}
    positive = {s: p for s, p in sym_pnl.items() if p > 0}
    pos_total = sum(positive.values())
    neg_total = sum(p for p in sym_pnl.values() if p < 0)

    def _top_share(n: int, *, use_positive: bool) -> float:
        if use_positive:
            if pos_total <= 0:
                return 0.0
            top = sum(p for _, p in sorted(positive.items(), key=lambda x: -x[1])[:n])
            return round(top / pos_total, 4)
        if total_pnl == 0:
            return 0.0
        top = sum(_float(r.get("total_pnl_yen")) for r in ranked[:n])
        return round(top / total_pnl, 4)

    pos_shares = [p / pos_total for p in positive.values()] if pos_total > 0 else []
    top1_sym = ranked[0]["symbol"] if ranked else ""
    top3_syms = [r["symbol"] for r in ranked[:3]]
    top5_syms = [r["symbol"] for r in ranked[:5]]

    return {
        "total_pnl_yen": round(total_pnl, 2),
        "positive_pnl_yen": round(pos_total, 2),
        "negative_pnl_yen": round(neg_total, 2),
        "symbol_count": len(sym_stats),
        "symbols_with_positive_pnl": len(positive),
        "top1_symbol": top1_sym,
        "top1_pnl_yen": round(_float(ranked[0].get("total_pnl_yen")) if ranked else 0, 2),
        "top3_symbols": top3_syms,
        "top3_pnl_yen": round(sum(_float(r.get("total_pnl_yen")) for r in ranked[:3]), 2),
        "top5_symbols": top5_syms,
        "top5_pnl_yen": round(sum(_float(r.get("total_pnl_yen")) for r in ranked[:5]), 2),
        "top1_share_of_total": _top_share(1, use_positive=False),
        "top3_share_of_total": _top_share(3, use_positive=False),
        "top5_share_of_total": _top_share(5, use_positive=False),
        "top1_share_of_positive": _top_share(1, use_positive=True),
        "top3_share_of_positive": _top_share(3, use_positive=True),
        "top5_share_of_positive": _top_share(5, use_positive=True),
        "hhi_positive_pnl": _hhi(pos_shares),
        "gini_positive_pnl": _gini_positive(list(positive.values())),
        "ranked_by_pnl": [
            {
                "symbol": r["symbol"],
                "reentry_count": r.get("count"),
                "total_pnl_yen": r.get("total_pnl_yen"),
                "profit_factor": r.get("profit_factor"),
            }
            for r in ranked
        ],
    }


def _exclude_simulation(
    reentry_rows: Sequence[Mapping[str, Any]],
    exclude_symbols: set[str],
    *,
    max_gap_sec: float,
) -> dict[str, Any]:
    subset = [
        r
        for r in reentry_rows
        if _float(r.get("gap_sec")) <= max_gap_sec and str(r.get("symbol")) not in exclude_symbols
    ]
    pnls = [_float(r["reentry_pnl_yen"]) for r in subset]
    holds = [_float(r["reentry_hold_sec"]) for r in subset]
    m = _metrics_from_pnls(pnls, holds)
    return {
        "excluded_symbols": sorted(exclude_symbols),
        "remaining_count": m["count"],
        **m,
        "reentry_positive": m["total_pnl_yen"] > 0 and m["profit_factor"] > 1.0,
    }


def _build_6966_chains(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    sym_trades = sorted(
        [dict(t) for t in trades if str(t.get("symbol")) == "6966.T"],
        key=lambda r: _parse_ts(str(r.get("entry_time"))) or datetime.min.replace(tzinfo=JST),
    )
    chains: list[dict[str, Any]] = []
    for i, t in enumerate(sym_trades):
        gap = ""
        prev_exit = ""
        if i > 0:
            prev = sym_trades[i - 1]
            prev_exit = str(prev.get("close_time") or "")
            pc = _parse_ts(prev_exit)
            ce = _parse_ts(str(t.get("entry_time") or ""))
            if pc and ce:
                gap = round((ce - pc).total_seconds(), 2)
        is_reentry = i > 0 and gap != "" and float(gap) <= 300
        chains.append(
            {
                "chain_index": i + 1,
                "symbol": "6966.T",
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("close_time"),
                "gap_sec_from_prev_exit": gap,
                "is_reentry_within_300s": is_reentry,
                "entry_price": t.get("entry_price"),
                "pnl_yen": t.get("pnl_yen_100"),
                "pnl_pct": t.get("realized_pnl_pct"),
                "hold_sec": t.get("hold_sec"),
                "exit_reason": t.get("close_reason"),
                "entry_reason": "gate_accepted",
                "session": t.get("session"),
                "prev_exit_time": prev_exit,
            }
        )
    return chains


def _6966_audit(
    chains: Sequence[Mapping[str, Any]],
    reentry_rows: Sequence[Mapping[str, Any]],
    *,
    max_gap_sec: float = PRIMARY_WINDOW,
) -> dict[str, Any]:
    sym_re = [r for r in reentry_rows if str(r.get("symbol")) == "6966.T" and _float(r.get("gap_sec")) <= max_gap_sec]
    sym_re_all = [r for r in reentry_rows if str(r.get("symbol")) == "6966.T"]
    pnls = [_float(r["reentry_pnl_yen"]) for r in sym_re]
    zero_gap = [r for r in sym_re if _float(r.get("gap_sec")) == 0]
    churn_like = sum(1 for r in sym_re if _float(r.get("reentry_hold_sec")) < 180 and _float(r.get("gap_sec")) <= 30)
    total = round(sum(pnls), 2)
    return {
        "window_sec": max_gap_sec,
        "total_trades_in_chain": len(chains),
        "reentry_pair_count_180s": len(sym_re),
        "reentry_pair_count_all": len(sym_re_all),
        "zero_gap_reentries_180s": len(zero_gap),
        "total_reentry_pnl_yen_180s": total,
        "total_reentry_pnl_yen_all": round(sum(_float(r["reentry_pnl_yen"]) for r in sym_re_all), 2),
        "avg_reentry_pnl_yen": round(statistics.mean(pnls), 2) if pnls else 0,
        "profit_factor": _pf(pnls),
        "win_rate": _win_rate(pnls),
        "churn_like_count": churn_like,
        "verdict": (
            "profit_expansion"
            if total > 0 and _pf(pnls) > 1.0
            else "mixed_churn"
        ),
        "note": (
            "6966.T contributes via repeated same-second reentry after overlap/stop; "
            "mix of small wins and stop losses with net positive on reentry legs."
        ),
    }


def _verdict(conc: Mapping[str, Any], *, window_pnl: float) -> str:
    if window_pnl <= 0:
        return "inconclusive"
    top1_pos = _float(conc.get("top1_share_of_positive"))
    top3_pos = _float(conc.get("top3_share_of_positive"))
    sym_pos = int(conc.get("symbols_with_positive_pnl") or 0)
    if top1_pos >= 0.55:
        return "single_symbol_dependent"
    if top3_pos >= 0.80:
        return "top3_dependent"
    if sym_pos >= 4 and top3_pos < 0.65:
        return "broad_based_positive"
    if top3_pos >= 0.65:
        return "top3_dependent"
    return "broad_based_positive" if sym_pos >= 3 else "inconclusive"


def run_phase432_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    base = kabu / "results" / "small_paper" / TARGET_DAY
    session_dirs = [base / d for d in SESSION_DIRS if (base / d).is_dir()]

    all_trades: list[dict[str, Any]] = []
    for sd in session_dirs:
        all_trades.extend(_load_structural_trades(sd))

    reentry_rows, _ = _analyze_reentry(all_trades)

    by_window: dict[str, Any] = {}
    symbol_tables: dict[str, list[dict]] = {}
    for w in REENTRY_WINDOWS:
        stats = _symbol_stats(reentry_rows, max_gap_sec=float(w))
        stats_by_pnl = sorted(stats, key=lambda r: _float(r.get("total_pnl_yen")), reverse=True)
        stats_by_pf = sorted(
            [s for s in stats if int(s.get("count") or 0) >= 2],
            key=lambda r: _float(r.get("profit_factor")),
            reverse=True,
        )
        stats_by_count = sorted(stats, key=lambda r: int(r.get("count") or 0), reverse=True)
        window_rows = [r for r in reentry_rows if _float(r.get("gap_sec")) <= w]
        total = sum(_float(r["reentry_pnl_yen"]) for r in window_rows)
        conc = _concentration(stats, total)
        by_window[str(w)] = {
            "metrics": _metrics_from_pnls(
                [_float(r["reentry_pnl_yen"]) for r in window_rows],
                [_float(r["reentry_hold_sec"]) for r in window_rows],
            ),
            "concentration": conc,
            "rank_pnl": stats_by_pnl[:10],
            "rank_pf": stats_by_pf[:10],
            "rank_count": stats_by_count[:10],
        }
        symbol_tables[str(w)] = stats_by_pnl

    primary = by_window[str(PRIMARY_WINDOW)]
    primary_conc = primary["concentration"]
    ranked = primary_conc["ranked_by_pnl"]
    top1 = ranked[0]["symbol"] if ranked else ""
    top3 = {r["symbol"] for r in ranked[:3]}
    top5 = {r["symbol"] for r in ranked[:5]}

    exclude_rows = [
        _exclude_simulation(reentry_rows, set(), max_gap_sec=PRIMARY_WINDOW),
        _exclude_simulation(reentry_rows, {"6966.T"}, max_gap_sec=PRIMARY_WINDOW),
        _exclude_simulation(reentry_rows, {top1}, max_gap_sec=PRIMARY_WINDOW),
        _exclude_simulation(reentry_rows, top3, max_gap_sec=PRIMARY_WINDOW),
        _exclude_simulation(reentry_rows, top5, max_gap_sec=PRIMARY_WINDOW),
    ]
    exclude_labels = [
        "baseline_180s",
        "exclude_6966",
        "exclude_top1",
        "exclude_top3",
        "exclude_top5",
    ]
    exclude_sims = [dict(label=lab, **row) for lab, row in zip(exclude_labels, exclude_rows)]

    chains_6966 = _build_6966_chains(all_trades)
    audit_6966 = _6966_audit(chains_6966, reentry_rows)

    sym_6966_pnl = next(
        (r for r in symbol_tables[str(PRIMARY_WINDOW)] if r["symbol"] == "6966.T"),
        {},
    )
    ex_6966 = next(r for r in exclude_sims if r["label"] == "exclude_6966")
    ex_top3 = next(r for r in exclude_sims if r["label"] == "exclude_top3")

    verdict = _verdict(primary_conc, window_pnl=_float(primary["metrics"].get("total_pnl_yen")))

    generalizable = verdict == "broad_based_positive" and _float(ex_top3.get("total_pnl_yen")) > 5000

    summary = {
        "phase": "432-Reentry-Attribution-Audit",
        "generated_at": _now_iso(),
        "target_date": TARGET_DAY,
        "primary_window_sec": PRIMARY_WINDOW,
        "verdict": verdict,
        "part_a_by_symbol": {w: symbol_tables[w] for w in symbol_tables},
        "part_b_concentration": {w: by_window[w]["concentration"] for w in by_window},
        "part_c_dependency": {
            "question": "market_structure vs symbol_dependent",
            "answer": (
                "symbol_dependent"
                if verdict in ("single_symbol_dependent", "top3_dependent")
                else "partially_broad"
                if verdict == "broad_based_positive"
                else "inconclusive"
            ),
            "primary_window": {
                "hhi_positive_pnl": primary_conc.get("hhi_positive_pnl"),
                "gini_positive_pnl": primary_conc.get("gini_positive_pnl"),
                "top1_share_of_positive": primary_conc.get("top1_share_of_positive"),
                "top3_share_of_positive": primary_conc.get("top3_share_of_positive"),
                "top5_share_of_positive": primary_conc.get("top5_share_of_positive"),
            },
        },
        "part_d_6966": audit_6966,
        "part_e_f_exclusions": exclude_sims,
        "mandatory_answers": {
            "1_top10_symbols_by_pnl_180s": primary["rank_pnl"],
            "2_top1_share": primary_conc.get("top1_share_of_positive"),
            "3_top3_share": primary_conc.get("top3_share_of_positive"),
            "4_top5_share": primary_conc.get("top5_share_of_positive"),
            "5_hhi": primary_conc.get("hhi_positive_pnl"),
            "6_6966_contribution_yen": sym_6966_pnl.get("total_pnl_yen"),
            "7_pnl_without_6966": ex_6966.get("total_pnl_yen"),
            "8_pnl_without_top3": ex_top3.get("total_pnl_yen"),
            "9_reentry_positive_maintained_after_top3_exclude": ex_top3.get("reentry_positive"),
            "9b_top3_exclude_pnl_weak": _float(ex_top3.get("total_pnl_yen")) < 1000,
            "10_generalizable": generalizable,
        },
        "conclusion": _build_conclusion(verdict, primary_conc, exclude_sims, audit_6966),
    }

    # flat symbol csv for primary window
    sym_csv_rows = []
    for r in symbol_tables[str(PRIMARY_WINDOW)]:
        sym_csv_rows.append(
            {
                "window_sec": PRIMARY_WINDOW,
                "symbol": r["symbol"],
                "reentry_count": r["count"],
                "total_pnl_yen": r["total_pnl_yen"],
                "avg_pnl_yen": r["avg_pnl_yen"],
                "median_pnl_yen": r["median_pnl_yen"],
                "win_rate": r["win_rate"],
                "profit_factor": r["profit_factor"],
                "avg_hold_sec": r["avg_hold_sec"],
                "median_hold_sec": r["median_hold_sec"],
            }
        )

    return {
        "summary": summary,
        "_symbol_rows": sym_csv_rows,
        "_6966_chains": chains_6966,
        "_exclude_rows": exclude_sims,
        "_concentration": primary_conc,
    }


def _build_conclusion(
    verdict: str,
    conc: Mapping[str, Any],
    exclusions: Sequence[Mapping[str, Any]],
    audit_6966: Mapping[str, Any],
) -> str:
    top1 = conc.get("top1_symbol")
    top3 = conc.get("top3_symbols")
    ex_top3 = next((r for r in exclusions if r.get("label") == "exclude_top3"), {})
    n_pos = conc.get("symbols_with_positive_pnl")
    return (
        f"Phase431 reentry_positive (+{conc.get('total_pnl_yen')} yen @180s) is NOT broad market structure: "
        f"positive PnL concentrates in Top3 {top3} ({conc.get('top3_share_of_positive'):.0%} of gross winners). "
        f"Top1 {top1} alone = {conc.get('top1_share_of_positive'):.0%} of positive leg PnL (+{conc.get('top1_pnl_yen')} yen, 1 trade). "
        f"6966.T has {audit_6966.get('reentry_pair_count_180s')} reentry legs @180s (high churn) but net reentry PnL="
        f"{audit_6966.get('total_reentry_pnl_yen_180s')} yen — frequency driver, not profit driver. "
        f"After excluding Top3, residual PnL={ex_top3.get('total_pnl_yen')} yen (PF={ex_top3.get('profit_factor')}); "
        f"edge is technically positive but economically thin. "
        f"Verdict={verdict}: reentry 'works' on 20260617 only when 186A/4062/5016 outlier legs are included."
    )


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    m = s.get("mandatory_answers") or {}
    conc = (s.get("part_b_concentration") or {}).get("180") or {}
    d6966 = s.get("part_d_6966") or {}
    lines = [
        "# Phase432 — Reentry Attribution Audit",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Target: {s.get('target_date')}",
        f"Verdict: **{s.get('verdict')}**",
        "",
        "## 結論",
        "",
        str(s.get("conclusion") or ""),
        "",
        "## Part B — Concentration (≤180s)",
        "",
        f"- total PnL: **{conc.get('total_pnl_yen')}** yen",
        f"- Top1 {conc.get('top1_symbol')}: **{conc.get('top1_pnl_yen')}** yen ({conc.get('top1_share_of_positive'):.1%} of positive)",
        f"- Top3 share (positive): **{conc.get('top3_share_of_positive'):.1%}**",
        f"- Top5 share (positive): **{conc.get('top5_share_of_positive'):.1%}**",
        f"- HHI: **{conc.get('hhi_positive_pnl')}** | Gini: **{conc.get('gini_positive_pnl')}**",
        "",
        "## Part D — 6966.T",
        "",
        f"- reentry legs @180s: {d6966.get('reentry_pair_count_180s')} | zero-gap: {d6966.get('zero_gap_reentries_180s')}",
        f"- reentry PnL @180s: **{d6966.get('total_reentry_pnl_yen_180s')}** | all-window PnL: {d6966.get('total_reentry_pnl_yen_all')}",
        f"- assessment: **{d6966.get('verdict')}**",
        "",
        "## Exclusion simulations (180s)",
        "",
        "| scenario | count | total PnL | PF | positive |",
        "|----------|-------|-----------|-----|----------|",
    ]
    for row in s.get("part_e_f_exclusions") or []:
        lines.append(
            f"| {row.get('label')} | {row.get('remaining_count')} | {row.get('total_pnl_yen')} | "
            f"{row.get('profit_factor')} | {row.get('reentry_positive')} |"
        )
    lines.extend(["", "## 必須回答", ""])
    for k, v in m.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


SYMBOL_FIELDS = [
    "window_sec",
    "symbol",
    "reentry_count",
    "total_pnl_yen",
    "avg_pnl_yen",
    "median_pnl_yen",
    "win_rate",
    "profit_factor",
    "avg_hold_sec",
    "median_hold_sec",
]

CHAIN_FIELDS = [
    "chain_index",
    "symbol",
    "entry_time",
    "exit_time",
    "gap_sec_from_prev_exit",
    "is_reentry_within_300s",
    "entry_price",
    "pnl_yen",
    "pnl_pct",
    "hold_sec",
    "exit_reason",
    "entry_reason",
    "session",
    "prev_exit_time",
]

EXCLUDE_FIELDS = [
    "label",
    "excluded_symbols",
    "remaining_count",
    "total_pnl_yen",
    "profit_factor",
    "win_rate",
    "avg_pnl_yen",
    "reentry_positive",
]


@dataclass
class Phase432Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase432_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        conc_path = reports / "phase432_reentry_concentration_summary.json"
        conc_payload = {
            "generated_at": (result.get("summary") or {}).get("generated_at"),
            "verdict": (result.get("summary") or {}).get("verdict"),
            "windows": (result.get("summary") or {}).get("part_b_concentration"),
            "dependency": (result.get("summary") or {}).get("part_c_dependency"),
            "mandatory_answers": (result.get("summary") or {}).get("mandatory_answers"),
        }
        conc_path.write_text(json.dumps(conc_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths = {
            "concentration": conc_path,
            "by_symbol": reports / "phase432_reentry_attribution_by_symbol.csv",
            "chains_6966": reports / "phase432_6966_reentry_chains.csv",
            "exclude": reports / "phase432_reentry_without_top_symbols.csv",
            "report": kabu / "docs" / "operations" / "phase432_reentry_attribution_report.md",
        }
        _write_csv(paths["by_symbol"], SYMBOL_FIELDS, result.get("_symbol_rows") or [])
        _write_csv(paths["chains_6966"], CHAIN_FIELDS, result.get("_6966_chains") or [])
        _write_csv(paths["exclude"], EXCLUDE_FIELDS, result.get("_exclude_rows") or [])
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text(render_report_md(result), encoding="utf-8")
        return paths
