"""
Phase409: Phase405 corrected boundary forward shadow logger.

Daily parallel evaluation of Phase408 corrected boundary policy on paper sessions.
Research / shadow only — no Runtime Exit / Entry / YAML / Discord production changes.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _pf, _norm_symbol
from research.phase382_capital_constrained_backtest import _parse_ts, _write_csv
from research.phase400_holding_time_audit import enrich_trade, normalize_exit_reason
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen, _normalize_shadow_exit, _saved_lost_yen
from research.phase406_portfolio_adoption import load_phase405_boundary_policy
from research.phase408_no_progress_corrected_replay import (
    prepare_corrected_trade_context,
    simulate_corrected_boundary,
)
from research.research_output_layers import COMMON_RESEARCH_CONSTRAINTS
from research.structural_trade_normalize import (
    normalize_structural_trade_row,
    copy_outputs_to_daily_research,
    resolve_kabu_root,
    resolve_reports_dir,
)

JST = ZoneInfo("Asia/Tokyo")
FORWARD_PERIOD_START = "20260616"
MIN_OBSERVE_DAYS = 5
MIN_ADOPTION_REVIEW_DAYS = 10
DEFAULT_P90_HOLD = 1290.6

TRADE_FIELDS = [
    "logged_at",
    "day",
    "session",
    "symbol",
    "entry_time",
    "exit_time",
    "baseline_pnl_yen_100",
    "shadow_pnl_yen_100",
    "delta_yen",
    "baseline_exit_reason",
    "shadow_exit_reason",
    "shadow_exit_ts",
    "used_baseline_fallback",
    "post_baseline_violation",
]

DAILY_FIELDS = [
    "day",
    "session_count",
    "trade_count",
    "structural_trade_count",
    "eval_failed_count",
    "baseline_total_pnl_yen_100",
    "shadow_total_pnl_yen_100",
    "delta_pnl_yen_100",
    "baseline_pf",
    "shadow_pf",
    "baseline_maxdd_yen_100",
    "shadow_maxdd_yen_100",
    "boundary_exit_count",
    "boundary_eligible_count",
    "affected_trade_count",
    "saved_loss_yen",
    "lost_upside_yen",
    "verdict",
    "status",
]


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _resolve_kabu_root(repo_root: Path) -> Path:
    return resolve_kabu_root(repo_root)


def _read_csv_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [dict(r) for r in csv.DictReader(f)]


def _replace_day_rows(
    existing: Sequence[Mapping[str, Any]],
    new_rows: Sequence[Mapping[str, Any]],
    *,
    day: str,
) -> list[dict[str, Any]]:
    kept = [dict(r) for r in existing if str(r.get("day") or "") != day]
    kept.extend(dict(r) for r in new_rows)
    return sorted(
        kept,
        key=lambda r: (
            str(r.get("day") or ""),
            str(r.get("session") or ""),
            str(r.get("entry_time") or ""),
            str(r.get("symbol") or ""),
        ),
    )


def _chronological_pnls(
    rows: Sequence[Mapping[str, Any]],
    *,
    pnl_key: str,
) -> list[float]:
    sort_keys = [
        (_parse_ts(str(r.get("exit_time") or "")) or datetime.min.replace(tzinfo=JST), i)
        for i, r in enumerate(rows)
    ]
    order = [i for _, i in sorted(sort_keys, key=lambda x: (x[0], x[1]))]
    return [float(rows[i][pnl_key]) for i in order]


def forward_verdict(day_count: int) -> str:
    if day_count < MIN_OBSERVE_DAYS:
        return "observe"
    if day_count < MIN_ADOPTION_REVIEW_DAYS:
        return "review_required"
    return "adoption_review_allowed"


def load_structural_trades_for_day(repo_root: Path, day: str) -> list[dict[str, Any]]:
    """Load live paper structural trades for ``day`` with session metadata."""
    import json as _json

    kabu = _resolve_kabu_root(repo_root)
    roots = [
        kabu / "results" / "small_paper",
        kabu / "results" / "paper_trade",
    ]
    seen_roots: set[str] = set()
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for root in roots:
        key = str(root.resolve()) if root.is_dir() else ""
        if not key or key in seen_roots:
            continue
        seen_roots.add(key)
        for csv_path in sorted(root.rglob("structural_trades.csv")):
            sess_dir = csv_path.parent
            trade_day = sess_dir.parent.name
            if trade_day != day:
                continue
            summary_path = sess_dir / "small_paper_summary.json"
            if summary_path.is_file():
                try:
                    summary = _json.loads(summary_path.read_text(encoding="utf-8"))
                except (OSError, _json.JSONDecodeError):
                    summary = {}
                if str(summary.get("source") or "") == "push-replay":
                    continue
            session = sess_dir.name
            try:
                with csv_path.open(encoding="utf-8", newline="") as f:
                    for row in csv.DictReader(f):
                        norm = normalize_structural_trade_row(row, day=trade_day, session=session)
                        if norm is None:
                            continue
                        dedupe_key = (
                            trade_day,
                            session,
                            str(norm.get("symbol") or ""),
                            str(norm.get("entry_time") or ""),
                        )
                        if dedupe_key in seen:
                            continue
                        seen.add(dedupe_key)
                        out.append(norm)
            except OSError:
                continue
    return out


def evaluate_boundary_shadow_trade(
    trade: Mapping[str, Any],
    *,
    repo_root: Path,
    session_cache: dict[str, Any],
    boundary_rules: Mapping[int, Any],
    p90_hold: float = DEFAULT_P90_HOLD,
    logged_at: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    enriched = enrich_trade(trade)
    enriched["position_cap_accepted"] = True
    enriched["_p90_hold"] = p90_hold
    ctx = prepare_corrected_trade_context(
        enriched,
        repo_root=repo_root,
        session_cache=session_cache,
        p90_hold=p90_hold,
    )
    if ctx is None:
        return None

    sim = simulate_corrected_boundary(ctx, buckets=boundary_rules)
    cap_ts = float(ctx["baseline_cap_ts"])
    exit_ts = float(sim.get("shadow_exit_ts") or cap_ts)
    baseline = float(ctx["baseline_pnl_yen_100"])
    shadow = float(sim["shadow_pnl_yen_100"])
    shadow_reason = _normalize_shadow_exit(str(sim.get("shadow_exit_reason") or ""))

    return {
        "logged_at": logged_at or _now_iso(),
        "day": ctx.get("day"),
        "session": ctx.get("session"),
        "symbol": ctx.get("symbol"),
        "entry_time": ctx.get("entry_time"),
        "exit_time": ctx.get("exit_time"),
        "baseline_pnl_yen_100": round(baseline, 2),
        "shadow_pnl_yen_100": round(shadow, 2),
        "delta_yen": round(shadow - baseline, 2),
        "baseline_exit_reason": normalize_exit_reason(str(ctx.get("baseline_exit_reason") or "")),
        "shadow_exit_reason": shadow_reason,
        "shadow_exit_ts": exit_ts,
        "used_baseline_fallback": bool(sim.get("used_baseline_fallback")),
        "post_baseline_violation": exit_ts > cap_ts + 1e-6,
    }


def aggregate_day_metrics(
    day_rows: Sequence[Mapping[str, Any]],
    *,
    day: str,
    session_count: int,
    structural_trade_count: int,
    eval_failed_count: int,
    status: str = "logged_forward_shadow",
) -> dict[str, Any]:
    if not day_rows:
        return {
            "day": day,
            "session_count": session_count,
            "trade_count": 0,
            "structural_trade_count": structural_trade_count,
            "eval_failed_count": eval_failed_count,
            "baseline_total_pnl_yen_100": 0.0,
            "shadow_total_pnl_yen_100": 0.0,
            "delta_pnl_yen_100": 0.0,
            "baseline_pf": None,
            "shadow_pf": None,
            "baseline_maxdd_yen_100": 0.0,
            "shadow_maxdd_yen_100": 0.0,
            "boundary_exit_count": 0,
            "boundary_eligible_count": 0,
            "affected_trade_count": 0,
            "saved_loss_yen": 0.0,
            "lost_upside_yen": 0.0,
            "verdict": "observe",
            "status": status,
        }
    baseline_pnls = [float(r["baseline_pnl_yen_100"]) for r in day_rows]
    shadow_pnls = [float(r["shadow_pnl_yen_100"]) for r in day_rows]
    saved, lost = _saved_lost_yen(baseline_pnls, shadow_pnls)
    affected = sum(1 for b, s in zip(baseline_pnls, shadow_pnls) if abs(s - b) > 0.01)
    boundary_exits = sum(
        1 for r in day_rows if "boundary" in str(r.get("shadow_exit_reason") or "")
    )
    boundary_eligible = sum(
        1
        for r in day_rows
        if float(r.get("hold_sec") or 0) >= 300 or "boundary" in str(r.get("shadow_exit_reason") or "")
    )
    sessions = {str(r.get("session") or "") for r in day_rows if r.get("session")}
    baseline_chron = _chronological_pnls(day_rows, pnl_key="baseline_pnl_yen_100")
    shadow_chron = _chronological_pnls(day_rows, pnl_key="shadow_pnl_yen_100")
    return {
        "day": day,
        "session_count": session_count or len(sessions),
        "trade_count": len(day_rows),
        "structural_trade_count": structural_trade_count,
        "eval_failed_count": eval_failed_count,
        "baseline_total_pnl_yen_100": round(sum(baseline_pnls), 2),
        "shadow_total_pnl_yen_100": round(sum(shadow_pnls), 2),
        "delta_pnl_yen_100": round(sum(shadow_pnls) - sum(baseline_pnls), 2),
        "baseline_pf": _pf(baseline_chron),
        "shadow_pf": _pf(shadow_chron),
        "baseline_maxdd_yen_100": _max_drawdown_yen(baseline_chron),
        "shadow_maxdd_yen_100": _max_drawdown_yen(shadow_chron),
        "boundary_exit_count": boundary_exits,
        "boundary_eligible_count": boundary_eligible,
        "affected_trade_count": affected,
        "saved_loss_yen": saved,
        "lost_upside_yen": lost,
        "verdict": "observe",
        "status": status,
    }


def compute_cumulative_summary(
    trade_rows: Sequence[Mapping[str, Any]],
    daily_rows: Sequence[Mapping[str, Any]],
    *,
    failed_days: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    period_days = sorted(
        {
            str(r.get("day") or "")
            for r in daily_rows
            if str(r.get("day") or "") >= FORWARD_PERIOD_START
        }
    )
    period_rows = [r for r in trade_rows if str(r.get("day") or "") in period_days]
    day_count = len(period_days)
    session_count = sum(int(r.get("session_count") or 0) for r in daily_rows if str(r.get("day") or "") in period_days)

    baseline_pnls = [float(r["baseline_pnl_yen_100"]) for r in period_rows]
    shadow_pnls = [float(r["shadow_pnl_yen_100"]) for r in period_rows]
    saved, lost = _saved_lost_yen(baseline_pnls, shadow_pnls)
    affected = sum(1 for b, s in zip(baseline_pnls, shadow_pnls) if abs(s - b) > 0.01)
    boundary_exits = sum(
        1 for r in period_rows if "boundary" in str(r.get("shadow_exit_reason") or "")
    )
    boundary_eligible = sum(
        int(r.get("boundary_eligible_count") or 0)
        for r in daily_rows
        if str(r.get("day") or "") in period_days
    )
    post_baseline = sum(1 for r in period_rows if r.get("post_baseline_violation"))

    baseline_chron = _chronological_pnls(period_rows, pnl_key="baseline_pnl_yen_100")
    shadow_chron = _chronological_pnls(period_rows, pnl_key="shadow_pnl_yen_100")
    baseline_total = round(sum(baseline_pnls), 2)
    shadow_total = round(sum(shadow_pnls), 2)
    baseline_pf = _pf(baseline_chron)
    shadow_pf = _pf(shadow_chron)
    baseline_dd = _max_drawdown_yen(baseline_chron)
    shadow_dd = _max_drawdown_yen(shadow_chron)

    verdict = forward_verdict(day_count)
    adoption_review_allowed = (
        day_count >= MIN_ADOPTION_REVIEW_DAYS
        and shadow_total >= baseline_total
        and (shadow_pf or 0) >= (baseline_pf or 0)
        and shadow_dd <= baseline_dd + 0.01
    )

    return {
        "day_count": day_count,
        "session_count": session_count,
        "period_days": period_days,
        "failed_days": list(failed_days or []),
        "baseline_total_pnl_yen_100": baseline_total,
        "shadow_total_pnl_yen_100": shadow_total,
        "delta_pnl_yen_100": round(shadow_total - baseline_total, 2),
        "baseline_pf": baseline_pf,
        "shadow_pf": shadow_pf,
        "baseline_maxdd_yen_100": baseline_dd,
        "shadow_maxdd_yen_100": shadow_dd,
        "boundary_exit_count": boundary_exits,
        "boundary_eligible_count": boundary_eligible,
        "affected_trade_count": affected,
        "saved_loss_yen": saved,
        "lost_upside_yen": lost,
        "post_baseline_usage_count": post_baseline,
        "replay_audit_pass": post_baseline == 0,
        "verdict": verdict,
        "adoption_review_allowed": adoption_review_allowed,
        "auto_adopt_forbidden": True,
        "phase408_reference": {
            "net_delta_yen": 144890.32,
            "profit_factor": 1.341,
            "max_drawdown_yen_100": 78350.58,
        },
    }


def apply_daily_verdicts(
    daily_rows: list[dict[str, Any]],
    period_days: Sequence[str],
) -> None:
    for row in daily_rows:
        day = str(row.get("day") or "")
        if day not in period_days:
            continue
        idx = period_days.index(day) + 1
        row["verdict"] = forward_verdict(idx)


def build_report_markdown(result: Mapping[str, Any]) -> str:
    fwd = result.get("forward_summary") or {}
    lines = [
        "# Phase409 — Phase405 Corrected Forward Shadow",
        "",
        "Daily forward shadow for Phase408 corrected boundary policy (Stack C / position_cap_mode).",
        "",
        f"- generated_at: {result.get('generated_at')}",
        f"- day_count: {fwd.get('day_count')}",
        f"- verdict: {fwd.get('verdict')}",
        f"- adoption_review_allowed: {fwd.get('adoption_review_allowed')}",
        "",
        "## Cumulative metrics",
        "",
        f"| baseline PnL | ¥{fwd.get('baseline_total_pnl_yen_100')} |",
        f"| shadow PnL | ¥{fwd.get('shadow_total_pnl_yen_100')} |",
        f"| delta | ¥{fwd.get('delta_pnl_yen_100')} |",
        f"| baseline PF | {fwd.get('baseline_pf')} |",
        f"| shadow PF | {fwd.get('shadow_pf')} |",
        f"| baseline maxDD | ¥{fwd.get('baseline_maxdd_yen_100')} |",
        f"| shadow maxDD | ¥{fwd.get('shadow_maxdd_yen_100')} |",
        "",
        "## Phase408 reference (historical corrected replay)",
        "",
        f"- net_delta: ¥{(fwd.get('phase408_reference') or {}).get('net_delta_yen')}",
        f"- PF: {(fwd.get('phase408_reference') or {}).get('profit_factor')}",
        f"- maxDD: ¥{(fwd.get('phase408_reference') or {}).get('max_drawdown_yen_100')}",
        "",
        "## Adoption gates",
        "",
        f"- day_count < {MIN_OBSERVE_DAYS}: observe",
        f"- day_count >= {MIN_OBSERVE_DAYS}: review_required",
        f"- day_count >= {MIN_ADOPTION_REVIEW_DAYS}: adoption_review_allowed (manual review only)",
        "",
        str((result.get("verdict") or {}).get("note")),
        "",
    ]
    return "\n".join(lines)


def run_forward_shadow_logger(
    *,
    repo_root: Path,
    reports_dir: Path,
    day: Optional[str] = None,
    phase405_policy_path: Optional[Path] = None,
) -> dict[str, Any]:
    day = day or datetime.now(JST).strftime("%Y%m%d")
    paths = BoundaryForwardShadowLogger(repo_root=repo_root, reports_dir=reports_dir).paths()

    trade_rows = _read_csv_rows(paths["trades"])
    daily_rows = _read_csv_rows(paths["daily"])
    failed_days: list[str] = []
    if paths["summary"].is_file():
        try:
            prior = json.loads(paths["summary"].read_text(encoding="utf-8"))
            failed_days = list(prior.get("failed_days") or [])
        except (OSError, json.JSONDecodeError):
            pass

    phase405_policy_path = phase405_policy_path or (
        reports_dir / "phase405_time_boundary_policy.csv"
    )
    boundary_rules = load_phase405_boundary_policy(phase405_policy_path)

    last_run: dict[str, Any] = {"day": day}
    day_trades = load_structural_trades_for_day(repo_root, day)

    if day < FORWARD_PERIOD_START:
        last_run["status"] = "skipped_before_forward_period"
    elif not day_trades:
        last_run["status"] = "skipped_no_structural_trades"
    else:
        session_cache: dict[str, Any] = {}
        logged_at = _now_iso()
        new_rows: list[dict[str, Any]] = []
        eval_failed = 0
        for trade in day_trades:
            row = evaluate_boundary_shadow_trade(
                trade,
                repo_root=repo_root,
                session_cache=session_cache,
                boundary_rules=boundary_rules,
                logged_at=logged_at,
            )
            if row is not None:
                row["hold_sec"] = float(trade.get("hold_sec") or 0)
                new_rows.append(row)
            else:
                eval_failed += 1
        trade_rows = _replace_day_rows(trade_rows, new_rows, day=day)
        sessions = {str(t.get("session") or "") for t in day_trades if t.get("session")}
        day_metrics = aggregate_day_metrics(
            new_rows,
            day=day,
            session_count=len(sessions),
            structural_trade_count=len(day_trades),
            eval_failed_count=eval_failed,
        )
        daily_rows = _replace_day_rows(daily_rows, [day_metrics], day=day)
        if eval_failed and not new_rows:
            if day not in failed_days:
                failed_days.append(day)
        last_run["status"] = "logged_forward_shadow"
        last_run["trade_count"] = len(new_rows)
        last_run["structural_trade_count"] = len(day_trades)
        last_run["session_count"] = len(sessions)
        last_run["eval_failed_count"] = eval_failed

    forward_summary = compute_cumulative_summary(trade_rows, daily_rows, failed_days=failed_days)
    apply_daily_verdicts(daily_rows, forward_summary.get("period_days") or [])

    note = (
        "Forward shadow logging only; Runtime Exit/Entry/Universe/YAML/Discord production unchanged. "
        "Shadow failure must not affect paper session success. "
        "Auto adoption forbidden; review after 5 business days, adoption review after 10."
    )

    return {
        "phase": "409-Boundary-Forward-Shadow",
        "title": "Phase405 corrected boundary forward shadow",
        "generated_at": _now_iso(),
        "purpose": "Daily parallel evaluation of Phase408 corrected boundary policy on Stack C paper sessions",
        "constraints": {
            **COMMON_RESEARCH_CONSTRAINTS,
            "forward_shadow_logging_only": True,
            "runtime_exit_unchanged": True,
            "auto_adopt_forbidden": True,
        },
        "policy": {
            "source": "phase408_corrected_boundary",
            "phase405_policy_path": str(phase405_policy_path),
            "forward_period_start": FORWARD_PERIOD_START,
            "min_observe_days": MIN_OBSERVE_DAYS,
            "min_adoption_review_days": MIN_ADOPTION_REVIEW_DAYS,
        },
        "output_paths": {k: str(v) for k, v in paths.items()},
        "forward_summary": forward_summary,
        "last_run": last_run,
        "failed_days": failed_days,
        "verdict": {"note": note},
        "_trade_rows": trade_rows,
        "_daily_rows": daily_rows,
    }


@dataclass
class BoundaryForwardShadowLogger:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "trades": self.reports_dir / "phase409_boundary_forward_shadow_trades.csv",
            "daily": self.reports_dir / "phase409_boundary_forward_shadow_daily.csv",
            "summary": self.reports_dir / "phase409_boundary_forward_shadow_summary.json",
            "report": self.reports_dir / "phase409_boundary_forward_shadow_report.md",
        }

    def run(self, *, day: Optional[str] = None) -> dict[str, Any]:
        return run_forward_shadow_logger(
            repo_root=self.repo_root,
            reports_dir=self.reports_dir,
            day=day,
        )

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["trades"].parent.mkdir(parents=True, exist_ok=True)
        _write_csv(paths["trades"], list(result.get("_trade_rows") or []), TRADE_FIELDS)
        _write_csv(paths["daily"], list(result.get("_daily_rows") or []), DAILY_FIELDS)
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths["report"].write_text(build_report_markdown(result), encoding="utf-8")

        from storage.results_paths import dual_write_output_paths, infer_day_from_result

        day = infer_day_from_result(result) or datetime.now(JST).strftime("%Y%m%d")
        dual_write_output_paths(self.repo_root, day, paths)
        copy_outputs_to_daily_research(self.repo_root, day, paths)

        ops_report = (
            _resolve_kabu_root(self.repo_root)
            / "docs"
            / "operations"
            / "phase409_boundary_forward_shadow_report.md"
        )
        ops_report.parent.mkdir(parents=True, exist_ok=True)
        ops_report.write_text(build_report_markdown(result), encoding="utf-8")

        return paths
