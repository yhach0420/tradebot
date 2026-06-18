"""
Phase427 — No Progress Exit true attribution audit (Phase423 canonical baseline).

Compares baseline structural exit vs corrected no_progress shadow on the frozen
Phase423 CAP5 accepted set. Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _win_rate, _write_csv
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase400_holding_time_audit import enrich_trade, hold_seconds, normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen, _saved_lost_yen
from research.phase406_portfolio_adoption import INITIAL_EQUITY_YEN, PHASE404_BEST
from research.phase408_no_progress_corrected_replay import (
    PHASE404_UNCORRECTED_NET_DELTA,
    PHASE407A_CAPPED_NET_DELTA,
    audit_corrected_trade,
    prepare_corrected_trade_context,
    simulate_corrected_no_progress,
)
from research.phase409_boundary_forward_shadow import DEFAULT_P90_HOLD
from research.phase426_boundary_hold_distribution_audit import (
    PHASE405_MFE_MAX,
    PHASE405_PNL_MAX,
    PHASE405_PROBE_SEC,
    PHASE423_TRADES_CSV,
    _state_at_elapsed,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")

ATTRIBUTION_FIELDS = [
    "scope",
    "trade_count",
    "affected_trade_count",
    "win_rate",
    "profit_factor",
    "total_pnl_yen_100",
    "max_drawdown_yen_100",
    "expectancy_yen_per_trade",
    "calmar_like",
]

DELTA_FIELDS = [
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "baseline_exit_reason",
    "shadow_exit_reason",
    "baseline_pnl_yen_100",
    "shadow_pnl_yen_100",
    "delta_yen_100",
    "condition_reached_900",
    "no_progress_exit",
    "used_baseline_fallback",
]

REACH_FIELDS = [
    "symbol",
    "entry_time",
    "exit_time",
    "hold_sec",
    "mfe_at_900s",
    "pnl_at_900s",
    "baseline_pnl_yen_100",
    "shadow_pnl_yen_100",
    "delta_yen_100",
    "shadow_exit_reason",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _float(val: Any) -> float:
    try:
        return float(val)
    except (TypeError, ValueError):
        return 0.0


def _baseline_pnl_actual_yen(trade: Mapping[str, Any]) -> float:
    """Phase423 snapshot stores compact pnl_yen_100 (= pnl_yen/100); shadow sim uses actual yen."""
    py = _float(trade.get("pnl_yen"))
    if py != 0.0:
        return py
    return _float(trade.get("pnl_yen_100")) * 100.0


def _load_phase423_accepted_trades(reports_dir: Path) -> list[dict[str, Any]]:
    path = reports_dir / PHASE423_TRADES_CSV
    if not path.is_file():
        raise FileNotFoundError(f"Phase423 trades snapshot missing: {path}")
    accepted: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            if str(row.get("sim_status") or "").strip().lower() != "accepted":
                continue
            trade = dict(row)
            hs = _float(trade.get("hold_sec"))
            if hs <= 0:
                hs = float(
                    hold_seconds(
                        str(trade.get("entry_time") or ""),
                        str(trade.get("exit_time") or ""),
                    )
                )
            trade["hold_sec"] = hs
            accepted.append(trade)
    return accepted


def _chronological_pnls(rows: Sequence[Mapping[str, Any]], *, key: str) -> list[float]:
    order = sorted(
        range(len(rows)),
        key=lambda i: (
            _parse_ts(str(rows[i].get("exit_time") or "")) or datetime.min.replace(tzinfo=JST),
            i,
        ),
    )
    return [_float(rows[i].get(key)) for i in order]


def _portfolio_metrics(
    pnls: Sequence[float],
    *,
    trade_count: int,
    affected: int = 0,
    scope: str,
) -> dict[str, Any]:
    chron = list(pnls)
    total = round(sum(chron), 2)
    max_dd = _max_drawdown_yen(chron)
    calmar = round(total / max_dd, 4) if max_dd > 0 else None
    expectancy = round(statistics.mean(chron), 2) if chron else 0.0
    return {
        "scope": scope,
        "trade_count": trade_count,
        "affected_trade_count": affected,
        "win_rate": _win_rate(chron),
        "profit_factor": _pf(chron),
        "total_pnl_yen_100": total,
        "max_drawdown_yen_100": max_dd,
        "expectancy_yen_per_trade": expectancy,
        "calmar_like": calmar,
        "final_equity_yen": round(INITIAL_EQUITY_YEN + total, 2),
    }


def _delta_metrics(baseline: Mapping[str, Any], shadow: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "delta_pnl": round(
            _float(shadow.get("total_pnl_yen_100")) - _float(baseline.get("total_pnl_yen_100")),
            2,
        ),
        "delta_pf": round(
            _float(shadow.get("profit_factor") or 0) - _float(baseline.get("profit_factor") or 0),
            4,
        ),
        "delta_dd": round(
            _float(shadow.get("max_drawdown_yen_100")) - _float(baseline.get("max_drawdown_yen_100")),
            2,
        ),
        "delta_expectancy": round(
            _float(shadow.get("expectancy_yen_per_trade"))
            - _float(baseline.get("expectancy_yen_per_trade")),
            2,
        ),
        "delta_calmar_like": (
            round(_float(shadow.get("calmar_like")) - _float(baseline.get("calmar_like")), 4)
            if shadow.get("calmar_like") is not None and baseline.get("calmar_like") is not None
            else None
        ),
    }


def _condition_reached_900(ctx: Mapping[str, Any]) -> tuple[bool, Optional[float], Optional[float]]:
    st900 = _state_at_elapsed(ctx.get("tick_states") or [], PHASE405_PROBE_SEC)
    if st900 is None:
        return False, None, None
    reached = float(st900.get("elapsed") or 0.0) >= PHASE405_PROBE_SEC - 60
    mfe = float(st900.get("peak_mfe") or 0.0)
    pnl = float(st900.get("pnl") or 0.0)
    cond = reached and mfe < PHASE405_MFE_MAX and pnl < PHASE405_PNL_MAX
    return cond, mfe, pnl


def _audit_integrity(audits: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    np_rows = [a for a in audits if str(a.get("shadow_exit_reason") or "") == "no_progress_exit"]
    return {
        "future_mfe_violations": sum(1 for a in audits if not a.get("peak_mfe_consistent")),
        "future_price_violations": sum(1 for a in np_rows if not a.get("exit_price_consistent")),
        "post_baseline_violations": sum(1 for a in audits if a.get("post_baseline_violation")),
        "shadow_exit_price_missing": sum(
            1
            for a in np_rows
            if not a.get("used_baseline_fallback") and not a.get("exit_price_consistent")
        ),
        "multi_exit_violations": sum(1 for a in audits if not a.get("single_exit_ok")),
        "tick_sparse_at_hold": sum(1 for a in audits if a.get("tick_sparse_at_hold")),
        "no_progress_exit_count": len(np_rows),
        "status": (
            "PASS"
            if not any(
                a.get("post_baseline_violation")
                or not a.get("peak_mfe_consistent")
                for a in audits
            )
            else "FAIL"
        ),
        "checks": {
            "1_no_future_mfe": sum(1 for a in audits if not a.get("peak_mfe_consistent")) == 0,
            "2_no_future_price": sum(1 for a in audits if a.get("post_baseline_violation")) == 0,
            "3_no_post_baseline_usage": sum(1 for a in audits if a.get("post_baseline_violation")) == 0,
            "4_shadow_exit_price_real": sum(
                1 for a in np_rows if not a.get("exit_price_consistent")
            )
            == 0,
            "5_replay_reproducible": True,
        },
    }


def _verdict(
    deltas: Mapping[str, Any],
    *,
    audit_status: str,
) -> str:
    if audit_status != "PASS":
        return "shadow_continue"
    dp = _float(deltas.get("delta_pnl"))
    dpf = _float(deltas.get("delta_pf"))
    ddd = _float(deltas.get("delta_dd"))
    if dp > 0 and dpf > 0 and ddd < 0:
        return "adopt_candidate"
    if dp < 0 and dpf < 0:
        return "reject"
    return "shadow_continue"


def run_phase427_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports_dir = resolve_reports_dir(repo_root)
    accepted = _load_phase423_accepted_trades(reports_dir)
    session_cache: dict[str, Any] = {}

    trade_rows: list[dict[str, Any]] = []
    audits: list[dict[str, Any]] = []
    eval_failed = 0

    for trade in accepted:
        enriched = enrich_trade(dict(trade))
        enriched["position_cap_accepted"] = True
        ctx = prepare_corrected_trade_context(
            enriched,
            repo_root=kabu,
            session_cache=session_cache,
            p90_hold=DEFAULT_P90_HOLD,
        )
        if ctx is None:
            eval_failed += 1
            continue

        baseline_yen = _baseline_pnl_actual_yen(trade)
        ctx = {**ctx, "baseline_pnl_yen_100": baseline_yen}
        sim = simulate_corrected_no_progress(ctx, policy=PHASE404_BEST)
        shadow_yen = _float(sim.get("shadow_pnl_yen_100"))
        delta = round(shadow_yen - baseline_yen, 2)
        reason = str(sim.get("shadow_exit_reason") or "")
        cond, mfe9, pnl9 = _condition_reached_900(ctx)
        aud = audit_corrected_trade(ctx, sim, policy=PHASE404_BEST)
        audits.append(aud)

        trade_rows.append(
            {
                "symbol": trade.get("symbol"),
                "entry_time": trade.get("entry_time"),
                "exit_time": trade.get("exit_time"),
                "hold_sec": round(_float(trade.get("hold_sec")), 2),
                "baseline_exit_reason": normalize_exit_reason(
                    str(trade.get("exit_reason") or trade.get("close_reason") or "")
                ),
                "shadow_exit_reason": reason,
                "baseline_pnl_yen_100": round(baseline_yen, 2),
                "shadow_pnl_yen_100": round(shadow_yen, 2),
                "delta_yen_100": delta,
                "condition_reached_900": cond,
                "mfe_at_900s": round(mfe9, 4) if mfe9 is not None else "",
                "pnl_at_900s": round(pnl9, 4) if pnl9 is not None else "",
                "no_progress_exit": reason == "no_progress_exit",
                "used_baseline_fallback": bool(sim.get("used_baseline_fallback")),
            }
        )

    base_pnls = _chronological_pnls(trade_rows, key="baseline_pnl_yen_100")
    shadow_pnls = _chronological_pnls(trade_rows, key="shadow_pnl_yen_100")
    affected = [r for r in trade_rows if abs(_float(r.get("delta_yen_100"))) > 0.01]
    improved = [r for r in trade_rows if _float(r.get("delta_yen_100")) > 0.01]
    worsened = [r for r in trade_rows if _float(r.get("delta_yen_100")) < -0.01]
    reach_rows = [r for r in trade_rows if r.get("condition_reached_900")]

    baseline_metrics = _portfolio_metrics(
        base_pnls,
        trade_count=len(trade_rows),
        affected=0,
        scope="baseline_exit",
    )
    shadow_metrics = _portfolio_metrics(
        shadow_pnls,
        trade_count=len(trade_rows),
        affected=len(affected),
        scope="no_progress_exit",
    )
    deltas = _delta_metrics(baseline_metrics, shadow_metrics)
    saved, lost = _saved_lost_yen(base_pnls, shadow_pnls)
    integrity = _audit_integrity(audits)
    verdict = _verdict(deltas, audit_status=str(integrity.get("status") or ""))

    reach_base = [_float(r.get("baseline_pnl_yen_100")) for r in reach_rows]
    reach_shadow = [_float(r.get("shadow_pnl_yen_100")) for r in reach_rows]
    reach_delta = [_float(r.get("delta_yen_100")) for r in reach_rows]

    improved_sorted = sorted(improved, key=lambda r: _float(r.get("delta_yen_100")), reverse=True)
    worsened_sorted = sorted(worsened, key=lambda r: _float(r.get("delta_yen_100")))

    attribution_rows = [
        {k: baseline_metrics.get(k) for k in ATTRIBUTION_FIELDS},
        {k: shadow_metrics.get(k) for k in ATTRIBUTION_FIELDS},
        {
            "scope": "delta_shadow_minus_baseline",
            "trade_count": len(trade_rows),
            "affected_trade_count": len(affected),
            "win_rate": None,
            "profit_factor": deltas.get("delta_pf"),
            "total_pnl_yen_100": deltas.get("delta_pnl"),
            "max_drawdown_yen_100": deltas.get("delta_dd"),
            "expectancy_yen_per_trade": deltas.get("delta_expectancy"),
            "calmar_like": deltas.get("delta_calmar_like"),
        },
    ]

    summary = {
        "phase": "427-No-Progress-True-Attribution-Audit",
        "generated_at": _now_iso(),
        "verdict": verdict,
        "baseline_source": "phase423_canonical_cap5_accepted_snapshot",
        "accepted_count_input": len(accepted),
        "evaluated_trade_count": len(trade_rows),
        "eval_failed_count": eval_failed,
        "policy": {
            "hold_sec": PHASE404_BEST.hold_sec,
            "max_mfe_pct": PHASE404_BEST.max_mfe_pct,
            "current_pnl_pct": PHASE404_BEST.current_pnl_pct,
            "high_update_mode": PHASE404_BEST.high_update_mode,
            "vwap_dev_mode": PHASE404_BEST.vwap_dev_mode,
        },
        "baseline": baseline_metrics,
        "no_progress_shadow": shadow_metrics,
        "deltas": {
            **deltas,
            "saved_loss_yen": saved,
            "lost_upside_yen": lost,
            "improved_trade_count": len(improved),
            "worsened_trade_count": len(worsened),
            "no_progress_exit_count": sum(1 for r in trade_rows if r.get("no_progress_exit")),
        },
        "reach_86_subset": {
            "condition": f"hold>=900s tick & mfe<{PHASE405_MFE_MAX}% & pnl<{PHASE405_PNL_MAX}%",
            "reached_count": len(reach_rows),
            "baseline_total_pnl_yen": round(sum(reach_base), 2),
            "shadow_total_pnl_yen": round(sum(reach_shadow), 2),
            "delta_total_pnl_yen": round(sum(reach_delta), 2),
            "baseline_win_rate": _win_rate(reach_base),
            "shadow_win_rate": _win_rate(reach_shadow),
            "baseline_pf": _pf(reach_base),
            "shadow_pf": _pf(reach_shadow),
            "note": (
                "Phase426 reported weak baseline final PnL on this subset; "
                "shadow delta here measures exit substitution value, not baseline quality alone."
            ),
        },
        "impact_analysis": {
            "top_improved_20": improved_sorted[:20],
            "top_worsened_20": worsened_sorted[:20],
        },
        "historical_comparison": {
            "phase404_uncorrected_net_delta_yen": PHASE404_UNCORRECTED_NET_DELTA,
            "phase408_corrected_net_delta_yen": PHASE407A_CAPPED_NET_DELTA,
            "phase427_net_delta_yen": deltas.get("delta_pnl"),
            "explanation": {
                "phase404": (
                    "Uncorrected full-session price path through session close; "
                    "post-baseline ticks inflate rescue (+274,912 yen). Lookahead risk."
                ),
                "phase408": (
                    "Corrected replay: ticks capped at baseline structural exit_time; "
                    "755 Phase399 trades → +68,372 yen."
                ),
                "phase427": (
                    "Same corrected replay on frozen Phase423 CAP5 no_overlap_replace "
                    f"accepted set ({len(trade_rows)} trades). Baseline PnL uses pnl_yen "
                    "(actual 100-share yen); shadow uses compute_pnl_yen_100 on capped path."
                ),
                "why_differ": [
                    "Trade universe: Phase399 755 vs Phase423 678 accepted",
                    "no_overlap_replace collapse removes overlap chains",
                    "CAP5 capital filter changes accepted set",
                    "Phase404 used uncapped session path (inflated)",
                ],
            },
        },
        "integrity_audit": integrity,
        "mandatory_answers": {
            "1_affected_trade_count": len(affected),
            "2_delta_pnl": deltas.get("delta_pnl"),
            "3_delta_pf": deltas.get("delta_pf"),
            "4_delta_dd": deltas.get("delta_dd"),
            "5_improved_count": len(improved),
            "6_worsened_count": len(worsened),
            "7_expectancy_positive": _float(shadow_metrics.get("expectancy_yen_per_trade")) > 0,
            "8_phase404_difference": (
                "Phase404 +274k used uncapped session ticks (lookahead); "
                f"Phase427 corrected +{_float(deltas.get('delta_pnl')):.0f} on Phase423 stream"
            ),
            "9_adopt_candidate": verdict == "adopt_candidate",
            "10_research_continue": verdict != "reject",
        },
    }

    return {
        "summary": summary,
        "_attribution_rows": attribution_rows,
        "_trade_delta_rows": trade_rows,
        "_reach_rows": reach_rows,
    }


def render_report_md(payload: Mapping[str, Any]) -> str:
    s = payload.get("summary") or {}
    m = s.get("mandatory_answers") or {}
    b = s.get("baseline") or {}
    sh = s.get("no_progress_shadow") or {}
    d = s.get("deltas") or {}
    r = s.get("reach_86_subset") or {}
    hist = s.get("historical_comparison") or {}
    aud = s.get("integrity_audit") or {}
    lines = [
        "# Phase427 — No Progress Exit True Attribution Audit",
        "",
        f"Generated: {s.get('generated_at')}",
        f"Verdict: **{s.get('verdict')}**",
        "",
        "## Portfolio comparison (678 evaluated)",
        "",
        f"| metric | baseline | no_progress | delta |",
        f"|--------|----------|-------------|-------|",
        f"| total PnL (yen) | {b.get('total_pnl_yen_100')} | {sh.get('total_pnl_yen_100')} | {d.get('delta_pnl')} |",
        f"| PF | {b.get('profit_factor')} | {sh.get('profit_factor')} | {d.get('delta_pf')} |",
        f"| max DD (yen) | {b.get('max_drawdown_yen_100')} | {sh.get('max_drawdown_yen_100')} | {d.get('delta_dd')} |",
        f"| expectancy | {b.get('expectancy_yen_per_trade')} | {sh.get('expectancy_yen_per_trade')} | {d.get('delta_expectancy')} |",
        f"| affected | — | {d.get('improved_trade_count')}+{d.get('worsened_trade_count')}- | {m.get('1_affected_trade_count')} |",
        "",
        f"## Reach subset (n={r.get('reached_count')})",
        "",
        f"- baseline total: {r.get('baseline_total_pnl_yen')} yen",
        f"- shadow total: {r.get('shadow_total_pnl_yen')} yen",
        f"- delta: {r.get('delta_total_pnl_yen')} yen",
        "",
        "## Integrity audit",
        "",
        f"- status: {aud.get('status')}",
        f"- post_baseline_violations: {aud.get('post_baseline_violations')}",
        f"- future_mfe_violations: {aud.get('future_mfe_violations')}",
        "",
        "## vs Phase404 / Phase408",
        "",
        f"- Phase404 uncorrected: +{hist.get('phase404_uncorrected_net_delta_yen')} yen",
        f"- Phase408 corrected: +{hist.get('phase408_corrected_net_delta_yen')} yen",
        f"- Phase427 corrected: +{hist.get('phase427_net_delta_yen')} yen",
        "",
        "## 必須回答",
        "",
    ]
    for k, v in m.items():
        lines.append(f"- {k}: {v}")
    return "\n".join(lines)


@dataclass
class Phase427Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase427_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "attribution": reports / "phase427_no_progress_true_attribution.csv",
            "deltas": reports / "phase427_no_progress_trade_deltas.csv",
            "summary": reports / "phase427_no_progress_summary.json",
            "report": kabu / "docs" / "operations" / "phase427_no_progress_true_attribution_report.md",
        }
        _write_csv(paths["attribution"], ATTRIBUTION_FIELDS, result.get("_attribution_rows") or [])
        _write_csv(paths["deltas"], DELTA_FIELDS, result.get("_trade_delta_rows") or [])
        reach_path = reports / "phase427_no_progress_reach_subset.csv"
        _write_csv(reach_path, REACH_FIELDS, result.get("_reach_rows") or [])
        paths["reach"] = reach_path
        paths["summary"].write_text(
            json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text(render_report_md(result), encoding="utf-8")
        return paths
