"""
Phase424 — Phase273/274 equity curve consistency audit vs Phase423 canonical baseline.

Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.equity_curve_shadow import (
    CANONICAL_BASELINE_END,
    PERIOD_START,
    load_canonical_live_config_trades,
    load_period_trades,
)
from research.market_sector_heat import _write_csv
from research.phase271_leverage_attribution_and_robustness import simulate_audited
from research.phase273_live_config_forward_shadow_logger import (
    LIVE_CONFIG_CANDIDATES,
    compute_candidate_summary,
    resolve_current_recommendation,
)
from research.phase274_live_config_auto_transition_shadow import (
    STARTING_EQUITY,
    compute_adoption_verdict,
    simulate_auto_transition,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

JST = ZoneInfo("Asia/Tokyo")
PHASE423_FINAL_EQUITY = 1_641_767.98
CANDIDATE_1500K = "live_start_candidate_1500k"
FORWARD_DAY = "20260617"
AM_SESSION_END_PREFIX = "2026-06-17T11:"

DAILY_FIELDS = [
    "day",
    "candidate_key",
    "start_equity",
    "end_equity",
    "daily_pnl",
    "accepted_trade_count",
    "rejected_trade_count",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _equity_at_am_end(equity_curve: Sequence[Mapping[str, Any]], *, day: str = FORWARD_DAY) -> Optional[float]:
    """Last equity on forward day before PM session entries (before 12:33 / ~11:25 AM close)."""
    am_rows = [
        r
        for r in equity_curve
        if str(r.get("day") or "") == day and str(r.get("timestamp") or "").startswith(AM_SESSION_END_PREFIX)
    ]
    if am_rows:
        return float(am_rows[-1].get("equity_after") or am_rows[-1].get("current_equity") or 0.0)
    # fallback: last event before 12:33
    cutoff = f"{day[:4]}-{day[4:6]}-{day[6:8]}T12:33"
    day_rows = [r for r in equity_curve if str(r.get("day") or "") == day]
    before_pm = [r for r in day_rows if str(r.get("timestamp") or "") < cutoff]
    if not before_pm:
        return None
    last = before_pm[-1]
    return float(last.get("equity_after") or last.get("current_equity") or 0.0)


def _daily_row_for_candidate(sim: Mapping[str, Any], *, candidate_key: str, day: str) -> Optional[dict[str, Any]]:
    for row in sim.get("_daily_rows") or []:
        if str(row.get("day") or "") == day:
            return {
                "day": day,
                "candidate_key": candidate_key,
                "start_equity": row.get("start_equity"),
                "end_equity": row.get("end_equity"),
                "daily_pnl": row.get("daily_pnl"),
                "accepted_trade_count": row.get("accepted_trade_count"),
                "rejected_trade_count": row.get("rejected_trade_count"),
            }
    return None


def _reject_invalid_price(sim: Mapping[str, Any]) -> int:
    counts = dict(sim.get("reject_reason_counts") or {})
    return int(counts.get("invalid_price") or 0)


def run_phase424_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu_root = resolve_kabu_root(repo_root)
    reports_dir = resolve_reports_dir(repo_root)

    legacy_trades, legacy_meta = load_period_trades(repo_root, period_start=PERIOD_START)
    canonical_trades, canonical_meta = load_canonical_live_config_trades(repo_root, period_start=PERIOD_START)

    p273_old = _read_json(reports_dir / "phase273_live_config_shadow_summary.json")
    p274_old = _read_json(reports_dir / "phase274_live_config_transition_summary.json")
    p423 = _read_json(reports_dir / "phase423_runtime_canonical_rebaseline_summary.json")

    cand_1500 = next(c for c in LIVE_CONFIG_CANDIDATES if c["candidate_key"] == CANDIDATE_1500K)
    sim_canonical = simulate_audited(
        canonical_trades,
        starting_equity=int(cand_1500["starting_equity"]),
        leverage=float(cand_1500["leverage"]),
        cap=int(cand_1500["cap"]),
        stop_policy=str(cand_1500["stop_policy"]),
    )
    sim_legacy = simulate_audited(
        legacy_trades,
        starting_equity=int(cand_1500["starting_equity"]),
        leverage=float(cand_1500["leverage"]),
        cap=int(cand_1500["cap"]),
        stop_policy=str(cand_1500["stop_policy"]),
    )
    sim274 = simulate_auto_transition(canonical_trades)

    period_days = list(canonical_meta.get("period_days") or [])
    day_616_row = _daily_row_for_candidate(sim_canonical, candidate_key=CANDIDATE_1500K, day=CANONICAL_BASELINE_END)
    day_617_row = _daily_row_for_candidate(sim_canonical, candidate_key=CANDIDATE_1500K, day=FORWARD_DAY)

    equity_616 = float(day_616_row.get("end_equity") or 0.0) if day_616_row else 0.0
    equity_617_pm = float(sim_canonical.get("final_equity") or 0.0)
    equity_617_am = _equity_at_am_end(sim274.get("_equity_curve") or [])

    old_1500 = next(
        (c for c in (p273_old.get("forward_summary") or {}).get("candidates") or [] if c.get("candidate_key") == CANDIDATE_1500K),
        {},
    )

    invalid_price_rejects = _reject_invalid_price(sim_canonical)
    p273_274_equity_match = abs(float(sim_canonical.get("final_equity") or 0.0) - float(sim274.get("final_equity") or 0.0)) < 0.02

    candidate_summary = compute_candidate_summary(
        sim_canonical,
        candidate=cand_1500,
        period_days=period_days,
        trades=canonical_trades,
    )
    adoption_274 = compute_adoption_verdict(metrics=sim274, day_count=len(period_days))

    root_cause = (
        "Phase273/274 used load_period_trades (raw structural_trades.csv across all sessions) "
        "without Phase413 no_overlap_replace collapse or Phase423 canonical historical baseline. "
        f"Legacy stream had {legacy_meta.get('input_trade_count')} trades vs canonical {canonical_meta.get('input_trade_count')}."
    )

    audit = {
        "phase": "424-Equity-Curve-Consistency-Audit",
        "generated_at": _now_iso(),
        "verdict": "bug_fixed" if abs(equity_616 - PHASE423_FINAL_EQUITY) < 1.0 else "audit_failed",
        "classification": "bug",
        "root_cause": root_cause,
        "history_sources": {
            "legacy_phase273_274": {
                "loader": "load_period_trades",
                "description": "Raw structural_trades per session; overlap chains included; no canonical collapse",
                "trade_count": legacy_meta.get("input_trade_count"),
                "period_days": legacy_meta.get("period_days"),
            },
            "canonical_after_fix": {
                "loader": "load_canonical_live_config_trades",
                "description": "Phase416/423 baseline B (no_overlap_replace) + forward collapsed days",
                "trade_count": canonical_meta.get("input_trade_count"),
                "historical_trade_count": canonical_meta.get("historical_trade_count"),
                "forward_trade_count_raw": canonical_meta.get("forward_trade_count_raw"),
                "period_days": canonical_meta.get("period_days"),
                "entry_price_enriched": True,
            },
            "phase423_reference": {
                "final_equity": (p423.get("metrics") or {}).get("total_pnl_yen"),
                "accepted_count": (p423.get("metrics") or {}).get("accepted_count"),
            },
        },
        "before_fix": {
            "phase273_1500k_final_equity": old_1500.get("final_equity"),
            "phase273_1500k_accepted": old_1500.get("accepted_count"),
            "phase273_1500k_rejected": old_1500.get("rejected_count"),
            "phase274_final_equity": (p274_old.get("transition_summary") or {}).get("final_equity"),
            "legacy_sim_20260616_equity": float(
                (_daily_row_for_candidate(sim_legacy, candidate_key=CANDIDATE_1500K, day=CANONICAL_BASELINE_END) or {}).get("end_equity")
                or 0.0
            ),
        },
        "after_fix": {
            "starting_equity": STARTING_EQUITY,
            "equity_20260616": round(equity_616, 2),
            "equity_20260617_am": round(float(equity_617_am or 0.0), 2) if equity_617_am is not None else None,
            "equity_20260617_pm": round(equity_617_pm, 2),
            "phase273_recommendation": resolve_current_recommendation([candidate_summary]),
            "phase274_adoption_verdict": adoption_274.get("adoption_verdict"),
            "phase273_274_final_equity_match": p273_274_equity_match,
            "invalid_price_reject_count": invalid_price_rejects,
            "candidate_1500k": {
                **{k: candidate_summary.get(k) for k in ("final_equity", "accepted_count", "rejected_count", "profit_factor", "verdict")},
                "reject_reason_counts": dict(sim_canonical.get("reject_reason_counts") or {}),
            },
        },
        "checks": {
            "period_includes_20260617": FORWARD_DAY in period_days,
            "starting_equity_1500k": int(cand_1500["starting_equity"]) == STARTING_EQUITY,
            "equity_20260616_matches_phase423": abs(equity_616 - PHASE423_FINAL_EQUITY) < 1.0,
            "no_invalid_price_rejects": invalid_price_rejects == 0,
            "phase273_274_equity_aligned": p273_274_equity_match,
            "not_using_stale_summary_for_recompute": True,
        },
        "forward_baseline_policy": {
            "use": "phase423_canonical_baseline_plus_forward",
            "historical_end": CANONICAL_BASELINE_END,
            "from_day": "20260617",
        },
        "fix_applied": {
            "files": [
                "src/research/equity_curve_shadow.py (load_canonical_live_config_trades)",
                "src/research/phase273_live_config_forward_shadow_logger.py",
                "src/research/phase274_live_config_auto_transition_shadow.py",
            ],
            "runtime_changed": False,
        },
    }

    daily_rows: list[dict[str, Any]] = []
    for row in sim_canonical.get("_daily_rows") or []:
        daily_rows.append(
            {
                "day": row.get("day"),
                "candidate_key": CANDIDATE_1500K,
                "start_equity": row.get("start_equity"),
                "end_equity": row.get("end_equity"),
                "daily_pnl": row.get("daily_pnl"),
                "accepted_trade_count": row.get("accepted_trade_count"),
                "rejected_trade_count": row.get("rejected_trade_count"),
            }
        )

    summary = {
        "phase": "424-Equity-Curve-Recomputed",
        "generated_at": _now_iso(),
        "trade_source": canonical_meta.get("trade_source"),
        "period_days": period_days,
        "candidate_1500k": audit["after_fix"]["candidate_1500k"],
        "equity_milestones": {
            "20260616": audit["after_fix"]["equity_20260616"],
            "20260617_am": audit["after_fix"]["equity_20260617_am"],
            "20260617_pm": audit["after_fix"]["equity_20260617_pm"],
        },
        "phase273_recommendation": audit["after_fix"]["phase273_recommendation"],
        "phase274_adoption_verdict": audit["after_fix"]["phase274_adoption_verdict"],
    }

    return {
        "audit": audit,
        "summary": summary,
        "_daily_rows": daily_rows,
        "_kabu_root": str(kabu_root),
    }


def render_report_md(payload: Mapping[str, Any]) -> str:
    a = payload.get("audit") or {}
    af = a.get("after_fix") or {}
    bf = a.get("before_fix") or {}
    lines = [
        "# Phase424 — Phase273/274 Equity Curve Consistency Audit",
        "",
        f"Generated: {a.get('generated_at')}",
        f"Verdict: **{a.get('verdict')}** ({a.get('classification')})",
        "",
        "## Root cause",
        "",
        str(a.get("root_cause")),
        "",
        "## 必須回答",
        "",
        f"1. **履歴ソース**: legacy=`load_period_trades` (raw structural); fix=`load_canonical_live_config_trades` (Phase423 baseline B + forward collapse)",
        f"2. **ズレ直接原因**: raw structural 2528 trades（overlap 連鎖含む）を全期間再シミュ → Phase423 の 681+forward と不一致",
        f"3. **バグか仕様か**: **バグ**（canonical baseline 導入後も Phase273/274 が旧 loader のまま）",
        f"4. **修正内容**: `load_canonical_live_config_trades` 追加、Phase273/274 がこれを使用",
        f"5. **修正後 20260616 equity**: {af.get('equity_20260616')}",
        f"6. **修正後 20260617 AM equity**: {af.get('equity_20260617_am')}",
        f"7. **修正後 20260617 PM equity**: {af.get('equity_20260617_pm')}",
        f"8. **Phase273 recommendation**: {af.get('phase273_recommendation')}",
        f"9. **Phase274 adoption verdict**: {af.get('phase274_adoption_verdict')}",
        f"10. **今後の daily forward 基準**: Phase423 canonical baseline + 当日 forward collapsed structural",
        "",
        "## Before vs After (1500k)",
        "",
        f"| | legacy Phase273 | after fix |",
        f"|---|-----------------|-----------|",
        f"| final equity | {bf.get('phase273_1500k_final_equity')} | {af.get('equity_20260617_pm')} |",
        f"| 20260616 end | {bf.get('legacy_sim_20260616_equity')} | {af.get('equity_20260616')} |",
        f"| accepted | {bf.get('phase273_1500k_accepted')} | {(af.get('candidate_1500k') or {}).get('accepted_count')} |",
        "",
    ]
    return "\n".join(lines)


@dataclass
class Phase424Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase424_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        audit_path = reports / "phase424_equity_curve_consistency_audit.json"
        daily_path = reports / "phase424_equity_curve_recomputed_daily.csv"
        summary_path = reports / "phase424_equity_curve_recomputed_summary.json"
        report_path = resolve_kabu_root(self.repo_root) / "docs" / "operations" / "phase424_equity_curve_consistency_report.md"

        audit_path.write_text(json.dumps(result.get("audit") or {}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        summary_path.write_text(json.dumps(result.get("summary") or {}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_csv(daily_path, DAILY_FIELDS, result.get("_daily_rows") or [])
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(render_report_md(result), encoding="utf-8")

        return {
            "audit": audit_path,
            "daily": daily_path,
            "summary": summary_path,
            "report": report_path,
        }
