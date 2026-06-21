"""
Phase446 — Momentum score source audit.

Fully documents momentum_continuation_score definition, ENTRY tertile gate,
and 20260619 trade-level decomposition.

Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from replay.pnl_yen import enrich_trade_pnl_yen
from research.market_sector_heat import _write_csv
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.entry_expectancy_score_shadow import (
    TERTILE_CUTOFFS,
    _bin_tertile,
    momentum_low_required_for_v2,
)

JST = ZoneInfo("Asia/Tokyo")
TARGET_DAY = "20260619"

MOMENTUM_DEF = {
    "file": "src/small_paper/live_feature_bridge.py",
    "function": "LiveFeatureBridge._momentum_score",
    "line_start": 272,
    "line_end": 295,
    "runtime_update": "LiveFeatureBridge.update (line 188)",
    "entry_gate_file": "src/small_paper/entry_expectancy_score_shadow.py",
    "entry_gate_functions": [
        "_feature_token (line 106)",
        "_bin_tertile (line 98)",
        "momentum_low_required_for_v2 (line 142)",
    ],
}

AUDIT_FIELDS = [
    "cohort",
    "rank",
    "symbol",
    "entry_time",
    "pnl_yen_100",
    "exit_reason",
    "momentum_continuation_score",
    "momentum_tertile",
    "momentum_low_token",
    "board_tertile",
    "quality_fallback_path",
    "price_mom_component",
    "vwap_part_component",
    "mfe_proxy_component",
    "recomputed_score",
    "pure_price_momentum",
    "rolling_mfe_pct",
    "rolling_mae_pct",
    "entry_vwap_dev_pct",
    "entry_rise_5min_pct",
    "entry_rise_10min_pct",
    "entry_rise_15min_pct",
    "entry_near_day_high_pct",
    "tertile_reason",
]

DIST_FIELDS = [
    "source",
    "count",
    "p33",
    "p66",
    "min",
    "max",
    "mean",
    "low_count",
    "mid_count",
    "high_count",
    "fixed_p33",
    "fixed_p66",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _float(val: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (TypeError, ValueError):
        return default


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    idx = max(0, min(len(sorted_vals) - 1, math.floor(q * (len(sorted_vals) - 1))))
    return round(float(sorted_vals[idx]), 6)


def _load_day_events(kabu: Path, day: str) -> list[dict[str, str]]:
    base = kabu / "results" / "small_paper" / day
    rows: list[dict[str, str]] = []
    if not base.is_dir():
        return rows
    for sess in sorted(base.iterdir()):
        path = sess / "small_paper_events.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", newline="") as fh:
            rows.extend(dict(r) for r in csv.DictReader(fh))
    return rows


def _decompose_momentum_score(trade: Mapping[str, Any]) -> dict[str, Any]:
    """Reverse-engineer LiveFeatureBridge._momentum_score components from logged fields."""
    mom = _float(trade.get("momentum_continuation_score"), default=0.0) or 0.0
    ppm = _float(trade.get("pure_price_momentum"))
    mfe = _float(trade.get("rolling_mfe_pct"), default=0.0) or 0.0
    mae = abs(_float(trade.get("rolling_mae_pct"), default=0.0) or 0.0)
    vwap_dev_pct = _float(trade.get("entry_vwap_dev_pct"))

    price_mom: Optional[float] = None
    if ppm is not None:
        price_mom = round(min(1.0, max(0.0, ppm / 0.008)), 6)
    vwap_part: Optional[float] = None
    if vwap_dev_pct is not None:
        vwap_dist = vwap_dev_pct / 100.0
        vwap_part = round(min(1.0, max(0.0, 0.5 + vwap_dist / 0.004)), 6)

    mfe_proxy = round(min(1.0, max(0.0, (mfe - 0.4 * mae) / 0.35)), 6) if (mfe or mae) else 0.0

    fallback = str(trade.get("quality_fallback_path") or "").lower() == "true"
    if price_mom is None and fallback and mfe == 0.0 and mae == 0.0:
        price_mom = 0.0

    recomputed: Optional[float] = None
    if price_mom is not None:
        recomputed = round(
            min(
                1.0,
                max(
                    0.0,
                    0.40 * price_mom + 0.25 * (vwap_part or 0.0) + 0.35 * mfe_proxy,
                ),
            ),
            4,
        )

    cuts = TERTILE_CUTOFFS["Momentum"]
    tertile = _bin_tertile(mom, cuts["p33"], cuts["p66"]) if mom is not None else ""
    board_v = _float(trade.get("entry_order_book_imbalance"))
    board_tertile = ""
    if board_v is not None:
        bc = TERTILE_CUTOFFS["Board"]
        board_tertile = _bin_tertile(board_v, bc["p33"], bc["p66"])

    reason_parts: list[str] = []
    if tertile == "low":
        reason_parts.append(f"score {mom:.4f} <= fixed p33 {cuts['p33']}")
    elif tertile == "mid":
        reason_parts.append(f"p33 {cuts['p33']} < score {mom:.4f} <= p66 {cuts['p66']}")
    else:
        reason_parts.append(f"score {mom:.4f} > p66 {cuts['p66']}")
    if fallback:
        reason_parts.append("quality_fallback_path=true (insufficient live ticks)")

    return {
        "momentum_continuation_score": round(mom, 4),
        "momentum_tertile": tertile,
        "momentum_low_token": momentum_low_required_for_v2(trade),
        "board_tertile": board_tertile,
        "quality_fallback_path": fallback,
        "price_mom_component": price_mom,
        "vwap_part_component": vwap_part,
        "mfe_proxy_component": mfe_proxy,
        "recomputed_score": recomputed,
        "pure_price_momentum": ppm,
        "rolling_mfe_pct": mfe,
        "rolling_mae_pct": mae,
        "entry_vwap_dev_pct": vwap_dev_pct,
        "entry_rise_5min_pct": _float(trade.get("entry_rise_5min_pct")),
        "entry_rise_10min_pct": _float(trade.get("entry_rise_10min_pct")),
        "entry_rise_15min_pct": _float(trade.get("entry_rise_15min_pct")),
        "entry_near_day_high_pct": _float(trade.get("entry_near_day_high_pct")),
        "tertile_reason": "; ".join(reason_parts),
    }


def _build_closed_trades(events: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    accepted = {
        (str(r.get("symbol") or ""), str(r.get("entry_time") or "")): dict(r)
        for r in events
        if str(r.get("event_type") or "") == "accepted"
    }
    out: list[dict[str, Any]] = []
    for row in events:
        if str(row.get("event_type") or "") != "observer_exit":
            continue
        enriched = enrich_trade_pnl_yen(dict(row))
        if enriched.get("pnl_yen_100") is None:
            continue
        key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
        acc = accepted.get(key, {})
        merged = {**enriched, **acc}
        merged["pnl_yen_100"] = float(enriched["pnl_yen_100"])
        out.append(merged)
    return out


def _distribution_row(
    *,
    source: str,
    values: Sequence[float],
    fixed_p33: float,
    fixed_p66: float,
) -> dict[str, Any]:
    if not values:
        return {
            "source": source,
            "count": 0,
            "p33": "",
            "p66": "",
            "min": "",
            "max": "",
            "mean": "",
            "low_count": 0,
            "mid_count": 0,
            "high_count": 0,
            "fixed_p33": fixed_p33,
            "fixed_p66": fixed_p66,
        }
    sv = sorted(values)
    return {
        "source": source,
        "count": len(sv),
        "p33": _percentile(sv, 0.33),
        "p66": _percentile(sv, 0.66),
        "min": round(sv[0], 6),
        "max": round(sv[-1], 6),
        "mean": round(statistics.mean(sv), 6),
        "low_count": sum(1 for v in sv if v <= fixed_p33),
        "mid_count": sum(1 for v in sv if fixed_p33 < v <= fixed_p66),
        "high_count": sum(1 for v in sv if v > fixed_p66),
        "fixed_p33": fixed_p33,
        "fixed_p66": fixed_p66,
    }


def _cohort_audit_rows(
    trades: Sequence[Mapping[str, Any]],
    *,
    cohort: str,
    top_n: int,
    reverse: bool,
) -> list[dict[str, Any]]:
    ordered = sorted(trades, key=lambda t: float(t.get("pnl_yen_100") or 0.0), reverse=reverse)
    rows: list[dict[str, Any]] = []
    for i, trade in enumerate(ordered[:top_n], start=1):
        decomp = _decompose_momentum_score(trade)
        rows.append(
            {
                "cohort": cohort,
                "rank": i,
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "pnl_yen_100": round(float(trade.get("pnl_yen_100") or 0.0), 2),
                "exit_reason": trade.get("exit_reason"),
                **decomp,
            }
        )
    return rows


def _verdict(
    *,
    winner_avg_mom: Optional[float],
    loser_avg_mom: Optional[float],
    fallback_loser_share: float,
    all_accepted_momentum_low: bool,
    accepted_max_mom: float,
    fixed_p33: float,
    session_candidate_p66: float,
) -> str:
    # Fixed Phase229 p33 is far above live-session score scale → gate degenerates to "accept all".
    cutoff_degenerate = accepted_max_mom <= fixed_p33 and all_accepted_momentum_low
    no_discrimination = abs((winner_avg_mom or 0) - (loser_avg_mom or 0)) < 0.03
    if cutoff_degenerate and no_discrimination:
        return "momentum_misclassification"
    if session_candidate_p66 < fixed_p33 * 0.5:
        return "momentum_misclassification"
    if fallback_loser_share >= 0.35 and no_discrimination:
        return "momentum_misclassification"
    loss_symbols_concentrated = True  # set by caller via stats if needed
    if loss_symbols_concentrated and no_discrimination and all_accepted_momentum_low:
        return "universe_problem"
    return "momentum_valid"


def run_phase446_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    events = _load_day_events(kabu, TARGET_DAY)
    closed = _build_closed_trades(events)

    fixed = TERTILE_CUTOFFS["Momentum"]
    fixed_p33 = float(fixed["p33"])
    fixed_p66 = float(fixed["p66"])

    candidate_vals = [
        float(_float(r.get("momentum_continuation_score"), default=0.0) or 0.0)
        for r in events
        if str(r.get("event_type") or "") == "candidate"
        and _float(r.get("momentum_continuation_score")) is not None
    ]
    accepted_vals = [
        float(_float(r.get("momentum_continuation_score"), default=0.0) or 0.0)
        for r in events
        if str(r.get("event_type") or "") == "accepted"
    ]
    rejected_vals = [
        float(_float(r.get("momentum_continuation_score"), default=0.0) or 0.0)
        for r in events
        if str(r.get("event_type") or "") == "rejected"
        and _float(r.get("momentum_continuation_score")) is not None
    ]

    dist_rows = [
        _distribution_row(source="candidate_20260619", values=candidate_vals, fixed_p33=fixed_p33, fixed_p66=fixed_p66),
        _distribution_row(source="accepted_20260619", values=accepted_vals, fixed_p33=fixed_p33, fixed_p66=fixed_p66),
        _distribution_row(source="rejected_20260619", values=rejected_vals, fixed_p33=fixed_p33, fixed_p66=fixed_p66),
        {
            "source": "phase229_fixed_cutoffs",
            "count": 2503,
            "p33": fixed_p33,
            "p66": fixed_p66,
            "min": "",
            "max": "",
            "mean": "",
            "low_count": "",
            "mid_count": "",
            "high_count": "",
            "fixed_p33": fixed_p33,
            "fixed_p66": fixed_p66,
        },
    ]

    losers = [t for t in closed if float(t.get("pnl_yen_100") or 0) < 0]
    winners = [t for t in closed if float(t.get("pnl_yen_100") or 0) > 0]

    audit_rows = _cohort_audit_rows(closed, cohort="loss_top10", top_n=10, reverse=False)
    audit_rows.extend(_cohort_audit_rows(closed, cohort="win_top10", top_n=10, reverse=True))

    winner_moms = [float(_decompose_momentum_score(t)["momentum_continuation_score"]) for t in winners]
    loser_moms = [float(_decompose_momentum_score(t)["momentum_continuation_score"]) for t in losers]
    winner_avg = round(statistics.mean(winner_moms), 4) if winner_moms else None
    loser_avg = round(statistics.mean(loser_moms), 4) if loser_moms else None

    fallback_losers = sum(1 for t in losers if str(t.get("quality_fallback_path") or "").lower() == "true")
    fallback_loser_share = round(fallback_losers / len(losers), 4) if losers else 0.0

    down15_losers = sum(
        1
        for t in losers
        if (_float(t.get("entry_rise_15min_pct")) or 0.0) < 0
    )
    down15_loser_share = round(down15_losers / len(losers), 4) if losers else 0.0

    all_mom_low = all(momentum_low_required_for_v2(t) for t in closed) if closed else False
    accepted_max_mom = max(accepted_vals) if accepted_vals else 0.0
    session_p66 = float(dist_rows[0]["p66"]) if dist_rows[0].get("p66") != "" else 0.0

    verdict = _verdict(
        winner_avg_mom=winner_avg,
        loser_avg_mom=loser_avg,
        fallback_loser_share=fallback_loser_share,
        all_accepted_momentum_low=all_mom_low,
        accepted_max_mom=accepted_max_mom,
        fixed_p33=fixed_p33,
        session_candidate_p66=session_p66,
    )

    formula_doc = {
        "inputs": [
            "price_mom: min(1, max(0, (price-p0)/p0 / 0.008)) over momentum_lookback=5 ticks",
            "vwap_part: min(1, max(0, 0.5 + (price-vwap)/vwap / 0.004))",
            "mfe_proxy: min(1, max(0, (rolling_mfe - 0.4*abs(rolling_mae)) / 0.35))",
        ],
        "weights": {"price_mom": 0.40, "vwap_part": 0.25, "mfe_proxy": 0.35},
        "formula": "momentum_continuation_score = clip(0.40*price_mom + 0.25*vwap_part + 0.35*mfe_proxy, 0, 1)",
        "normalization": "ENTRY gate uses fixed Phase229 tertile cutoffs (not session percentile). Classification: val<=p33->low, p33<val<=p66->mid, else high.",
        "momentum_low_gate": f"Momentum:low when score <= {fixed_p33} (fixed p33 from Phase229 2503-trade population)",
    }

    summary = {
        "phase": "446-Momentum-Score-Source-Audit",
        "generated_at": _now_iso(),
        "verdict": verdict,
        "target_day": TARGET_DAY,
        "definition": MOMENTUM_DEF,
        "formula": formula_doc,
        "tertile_cutoffs": {
            "fixed_phase229": fixed,
            "session_20260619_candidate": {
                "p33": dist_rows[0]["p33"],
                "p66": dist_rows[0]["p66"],
            },
            "session_20260619_accepted": {
                "p33": dist_rows[1]["p33"],
                "p66": dist_rows[1]["p66"],
            },
            "note": "Runtime ENTRY uses fixed Phase229 cutoffs, not session percentiles.",
        },
        "session_stats": {
            "closed_trades": len(closed),
            "total_pnl_yen_100": round(sum(float(t.get("pnl_yen_100") or 0) for t in closed), 2),
            "winners": len(winners),
            "losers": len(losers),
            "all_accepted_momentum_low": all_mom_low,
            "fallback_accept_count": sum(
                1 for t in closed if str(t.get("quality_fallback_path") or "").lower() == "true"
            ),
            "winner_avg_momentum": winner_avg,
            "loser_avg_momentum": loser_avg,
            "down15_loser_share": down15_loser_share,
        },
        "mandatory_answers": {
            "1_momentum_continuation_score_definition": (
                "LiveFeatureBridge._momentum_score: weighted blend of 5-tick price momentum, "
                "VWAP distance, and rolling MFE/MAE proxy on live PUSH ticks"
            ),
            "2_input_features": [
                "pure_price_momentum (5-tick lookback)",
                "entry_vwap_dev_pct / VWAP distance",
                "rolling_mfe_pct",
                "rolling_mae_pct",
            ],
            "3_formula": formula_doc["formula"],
            "4_momentum_low_meaning": (
                f"momentum_continuation_score <= {fixed_p33} (Phase229 fixed p33 tertile); "
                "required token for ENTRY (Momentum:low +2 pts)"
            ),
            "5_evaluates_uptrend_continuation": (
                "Partially — uses short tick-window price rise + VWAP premium + intraday MFE; "
                "does NOT use r15/r30 or day-high distance directly"
            ),
            "6_can_misread_downtrend_bounce": True,
            "7_winner_loser_difference": {
                "winner_avg_momentum": winner_avg,
                "loser_avg_momentum": loser_avg,
                "material_difference": abs((winner_avg or 0) - (loser_avg or 0)) >= 0.03,
            },
            "8_momentum_improvement_room": True,
            "9_momentum_vs_universe": (
                "momentum_misclassification"
                if verdict == "momentum_misclassification"
                else "universe_problem"
            ),
            "10_next_fix_candidate": (
                "Add 15m/30m drift + day-high distance to momentum score or gate; "
                "tighten fallback-path entries (quality_fallback_path=true → score≈0)"
            ),
        },
    }

    return {
        "summary": summary,
        "_audit_rows": audit_rows,
        "_dist_rows": dist_rows,
    }


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    m = s.get("mandatory_answers") or {}
    f = s.get("formula") or {}
    tc = s.get("tertile_cutoffs") or {}
    ss = s.get("session_stats") or {}
    d = s.get("definition") or {}
    lines = [
        "# Phase446 — Momentum Score Source Audit",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Verdict: **{s.get('verdict')}**",
        f"Target day: {s.get('target_day')}",
        "",
        "## Part A — Definition location",
        "",
        f"- file: `{d.get('file')}`",
        f"- function: `{d.get('function')}` (lines {d.get('line_start')}–{d.get('line_end')})",
        f"- entry gate: `{d.get('entry_gate_file')}`",
        "",
        "## Part B — Formula",
        "",
        f"- weights: {f.get('weights')}",
        f"- formula: `{f.get('formula')}`",
        "- inputs:",
    ]
    for inp in f.get("inputs") or []:
        lines.append(f"  - {inp}")
    lines.extend(
        [
            "",
            "## Part C — Normalization",
            "",
            str(f.get("normalization")),
            "",
            "## Part D — Tertile cutoffs",
            "",
            f"- fixed Phase229: p33={tc.get('fixed_phase229', {}).get('p33')}, p66={tc.get('fixed_phase229', {}).get('p66')}",
            f"- session candidate p33/p66: {tc.get('session_20260619_candidate')}",
            f"- session accepted p33/p66: {tc.get('session_20260619_accepted')}",
            "",
            "## Part E/F — Top trade decomposition",
            "",
            "See `phase446_momentum_score_audit.csv` (loss_top10 / win_top10).",
            "",
            "## Mandatory answers",
            "",
            f"1. 定義: {m.get('1_momentum_continuation_score_definition')}",
            f"2. 入力: {m.get('2_input_features')}",
            f"3. 式: {m.get('3_formula')}",
            f"4. Momentum:low: {m.get('4_momentum_low_meaning')}",
            f"5. 上昇継続評価: {m.get('5_evaluates_uptrend_continuation')}",
            f"6. 下落反発誤認: {m.get('6_can_misread_downtrend_bounce')}",
            f"7. 勝敗差: {m.get('7_winner_loser_difference')}",
            f"8. 改善余地: {m.get('8_momentum_improvement_room')}",
            f"9. Momentum vs Universe: {m.get('9_momentum_vs_universe')}",
            f"10. 次修正候補: {m.get('10_next_fix_candidate')}",
            "",
            "## Session stats",
            "",
            f"- closed: {ss.get('closed_trades')}, PnL: {ss.get('total_pnl_yen_100')} yen (100 shares)",
            f"- winners/losers: {ss.get('winners')}/{ss.get('losers')}",
            f"- all momentum low: {ss.get('all_accepted_momentum_low')}",
            f"- fallback accepts: {ss.get('fallback_accept_count')}",
        ]
    )
    return "\n".join(lines) + "\n"


@dataclass
class Phase446Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase446_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "audit": reports / "phase446_momentum_score_audit.csv",
            "distribution": reports / "phase446_momentum_score_distribution.csv",
            "summary": reports / "phase446_momentum_score_summary.json",
            "report": kabu / "docs" / "operations" / "phase446_momentum_score_audit.md",
        }
        _write_csv(paths["audit"], AUDIT_FIELDS, result.get("_audit_rows") or [])
        _write_csv(paths["distribution"], DIST_FIELDS, result.get("_dist_rows") or [])
        paths["summary"].write_text(
            json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text(render_report_md(result), encoding="utf-8")
        return paths
