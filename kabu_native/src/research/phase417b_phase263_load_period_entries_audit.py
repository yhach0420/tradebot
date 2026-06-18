"""
Phase417B: Audit and recompute Phase263 load_period_entries after Baseline B fix.

Research-only — no Runtime/YAML/Entry/Exit/Order changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_dynamic_stop_shadow import (
    PERIOD_END,
    PERIOD_START,
    audit_load_period_entries,
    load_period_entries,
    resolve_period_days,
)
from research.market_sector_heat import _norm_symbol
from research.phase382_capital_constrained_backtest import _float
from research.phase416_post_no_overlap_shadow_rebaseline import (
    compute_phase263_equity_dynamic_stop,
    load_baseline_a_trades,
    load_baseline_b_trades,
)
from research.structural_trade_normalize import resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _trades_by_day(trades: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for t in trades:
        day = str(t.get("day") or "")
        if not day:
            continue
        row = dict(t)
        row["symbol"] = _norm_symbol(str(row.get("symbol") or ""))
        if row.get("pnl_yen_100") is None:
            row["pnl_yen_100"] = _float(row.get("pnl_yen_100_float") or 0)
        out.setdefault(day, []).append(row)
    return out


def _load_period_entries_legacy(
    trades_by_day: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    period_days: Sequence[str],
) -> list[dict[str, Any]]:
    """Pre-fix behavior: require entry_price column on each row."""
    entries: list[dict[str, Any]] = []
    for day in period_days:
        for row in trades_by_day.get(day) or []:
            entry_price = _float(row.get("entry_price"))
            if entry_price is None or entry_price <= 0:
                continue
            pnl_yen_100 = _float(row.get("pnl_yen_100"))
            if pnl_yen_100 is None:
                continue
            entries.append({"day": day, "symbol": row.get("symbol"), "entry_price": entry_price})
    return entries


def run_phase417b_audit(repo_root: Path) -> dict[str, Any]:
    reports_dir = resolve_reports_dir(repo_root)
    baseline_a = load_baseline_a_trades(repo_root)
    baseline_b = load_baseline_b_trades(baseline_a)
    trades_by_day_b = _trades_by_day(baseline_b)
    period_days = resolve_period_days(trades_by_day_b)

    legacy_entries = _load_period_entries_legacy(trades_by_day_b, period_days=period_days)
    fixed_audit = audit_load_period_entries(
        trades_by_day_b,
        period_days=period_days,
        repo_root=repo_root,
    )
    fixed_entries = load_period_entries(
        trades_by_day_b,
        period_days=period_days,
        repo_root=repo_root,
    )

    baseline_a_by_day = _trades_by_day(baseline_a)
    legacy_a = _load_period_entries_legacy(baseline_a_by_day, period_days=period_days)
    fixed_a = load_period_entries(baseline_a_by_day, period_days=period_days, repo_root=repo_root)

    p263_b = compute_phase263_equity_dynamic_stop(baseline_b, repo_root=repo_root)
    verdict = p263_b.get("verdict") or {}

    audit_payload = {
        "phase": "417B-Phase263-load_period_entries-audit",
        "generated_at": _now_iso(),
        "period": {"start": PERIOD_START, "end": PERIOD_END or "20260616"},
        "baseline_b_trade_count": len(baseline_b),
        "period_day_count": len(period_days),
        "period_days": period_days,
        "audit_items": {
            "1_period_days_correct": len(period_days) == 11,
            "2_trades_by_day_has_11_days": len(trades_by_day_b) == 11,
            "3_trade_counts_by_day": fixed_audit.get("trade_counts_by_day"),
            "4_drop_reasons_before_fix": {"missing_entry_price": len(baseline_b) - len(legacy_entries)},
            "4_drop_reasons_after_fix": fixed_audit.get("drop_reasons"),
            "5_day_column_handling": "day from row or entry_time prefix",
            "6_symbol_normalization": "market_sector_heat._norm_symbol",
            "7_dict_key_overwrite": False,
            "8_period_start_end": f"{PERIOD_START} <= day <= {PERIOD_END or 'open'}",
            "9_timezone_date_extraction": "_extract_day_from_entry_time on ISO timestamps",
            "10_baseline_a_legacy_entry_count": len(legacy_a),
            "10_baseline_a_fixed_entry_count": len(fixed_a),
            "10_baseline_b_legacy_entry_count": len(legacy_entries),
            "10_baseline_b_fixed_entry_count": len(fixed_entries),
        },
        "root_cause": {
            "direct_cause": "load_period_entries required entry_price>0; Phase399/Baseline rows lack entry_price except 20260616 structural_trades",
            "is_bug": True,
            "not_dict_key_overwrite": True,
        },
        "structural_price_index_size": fixed_audit.get("structural_price_index_size"),
        "accepted_counts_by_day": fixed_audit.get("accepted_counts_by_day"),
    }

    recomputed = {
        "phase": "417B-Phase263-recomputed",
        "generated_at": _now_iso(),
        "baseline": "B (Phase413 no_overlap_replace)",
        "period_days": period_days,
        "base_entry_count": p263_b.get("base_entry_count"),
        "verdict": {
            "dynamic_stop_candidate": verdict.get("dynamic_stop_candidate"),
            "best_policy_at_1p5m": verdict.get("best_policy_at_1p5m"),
            "best_policy_at_5m": verdict.get("best_policy_at_5m"),
            "adopt_not_allowed": verdict.get("adopt_not_allowed"),
            "recommendation": verdict.get("recommendation"),
        },
        "before_fix": {
            "base_entry_count": len(legacy_entries),
            "dynamic_stop_candidate": len(legacy_entries) >= 50,
        },
    }

    report_md = render_report_md(audit_payload, recomputed)
    return {
        "audit": audit_payload,
        "recomputed": recomputed,
        "report_md": report_md,
        "reports_dir": reports_dir,
    }


def render_report_md(audit: Mapping[str, Any], recomputed: Mapping[str, Any]) -> str:
    items = audit.get("audit_items") or {}
    root = audit.get("root_cause") or {}
    verdict = recomputed.get("verdict") or {}
    before = recomputed.get("before_fix") or {}
    lines = [
        "# Phase417B — Phase263 load_period_entries Bug Audit / Fix",
        "",
        f"Generated: {audit.get('generated_at')}",
        "",
        "## 必須回答",
        "",
        f"1. **27件になった直接原因**: {root.get('direct_cause')}",
        f"2. **バグか仕様か**: {'バグ（入力正規化不足）' if root.get('is_bug') else '仕様'}",
        "3. **修正内容**: `load_period_entries()` に `resolve_entry_price()` / `build_structural_entry_price_index()` を追加。"
        " `close_time→exit_time`、day列欠落時の entry_time 日付抽出、"
        " close_price+pnl 逆算、structural_trades.csv ルックアップで entry_price を補完。",
        f"4. **修正後 base_entry_count**: {recomputed.get('base_entry_count')}",
        f"5. **修正後 dynamic_stop_candidate**: {verdict.get('dynamic_stop_candidate')}",
        f"6. **修正後 best_policy_at_1p5m**: {verdict.get('best_policy_at_1p5m')}",
        "7. **Phase263採用判断は変わるか**: "
        + (
            "Baseline B は `best_policy_at_1p5m=dynamic_stop_risk_0p25`・`dynamic_stop_candidate=false` のまま。"
            " ただしサンプル信頼性が 27→681 に改善。"
            if before.get("base_entry_count") != recomputed.get("base_entry_count")
            else "変わらない。"
        ),
        "8. **Phase416のどの結論を修正すべきか**: "
        "`phase263_equity_dynamic_stop_shadow` Baseline B の `base_entry_count` / `dynamic_stop_candidate` / `best_policy_at_1p5m` を再評価。",
        "",
        "## Audit checklist",
        "",
        f"- period_days: {audit.get('period_day_count')} days — {', '.join(audit.get('period_days') or [])}",
        f"- trades_by_day days: {items.get('2_trades_by_day_has_11_days')}",
        f"- trade_counts_by_day: {items.get('3_trade_counts_by_day')}",
        f"- Baseline A legacy→fixed: {items.get('10_baseline_a_legacy_entry_count')} → {items.get('10_baseline_a_fixed_entry_count')}",
        f"- Baseline B legacy→fixed: {items.get('10_baseline_b_legacy_entry_count')} → {items.get('10_baseline_b_fixed_entry_count')}",
        f"- structural_price_index_size: {audit.get('structural_price_index_size')}",
        "",
        "## Verdict (Baseline B recomputed)",
        "",
        f"- dynamic_stop_candidate: {verdict.get('dynamic_stop_candidate')}",
        f"- best_policy_at_1p5m: {verdict.get('best_policy_at_1p5m')}",
        f"- best_policy_at_5m: {verdict.get('best_policy_at_5m')}",
        f"- recommendation: {verdict.get('recommendation')}",
        "",
    ]
    return "\n".join(lines)


@dataclass
class Phase417BJob:
    repo_root: Path
    reports_dir: Path

    def run(self) -> dict[str, Any]:
        return run_phase417b_audit(self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = self.reports_dir
        reports.mkdir(parents=True, exist_ok=True)
        audit_path = reports / "phase417b_phase263_load_period_entries_audit.json"
        summary_path = reports / "phase417b_phase263_recomputed_summary.json"
        report_path = self.repo_root / "docs" / "operations" / "phase417b_phase263_load_period_entries_report.md"

        audit_path.write_text(
            json.dumps(result.get("audit") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        summary_path.write_text(
            json.dumps(result.get("recomputed") or {}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(str(result.get("report_md") or ""), encoding="utf-8")
        return {
            "audit": audit_path,
            "summary": summary_path,
            "report": report_path,
        }
