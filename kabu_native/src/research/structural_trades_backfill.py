"""
Phase265-Structural-Trades-Backfill-For-Research.

Derive structural_trades.csv from existing small_paper session events (research only).
Does not modify Runtime / Universe / Entry / YAML or mutate event logs.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import _write_csv
from research.structural_observer_review import build_and_write_structural_observer_review

JST = ZoneInfo("Asia/Tokyo")

PERIOD_START = "20260529"
PERIOD_END = "20260612"

SESSION_CSV_FIELDS = [
    "day",
    "session",
    "session_dir",
    "status",
    "source",
    "rows_generated",
    "structural_trade_count",
    "structural_pf",
    "error",
]

LIVE_SESSION_PREFIXES = ("live_session_", "live_full_session_")


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _count_csv_rows(path: Path) -> int:
    if not path.is_file():
        return 0
    try:
        with path.open(encoding="utf-8", newline="") as f:
            return sum(1 for _ in csv.DictReader(f))
    except OSError:
        return 0


def _session_source(session_dir: Path) -> str:
    summary = _load_json(session_dir / "small_paper_summary.json")
    source = str(summary.get("source") or "").strip()
    if source:
        return source
    cfg = _load_json(session_dir / "live_session_config.json")
    return str(cfg.get("source") or "").strip()


def _is_live_session_dir(session_dir: Path) -> bool:
    name = session_dir.name
    if any(name.startswith(prefix) for prefix in LIVE_SESSION_PREFIXES):
        return True
    return _session_source(session_dir) == "live"


def _is_debug_session(session_dir: Path) -> bool:
    return "debug" in session_dir.name.lower()


def _resolve_pilot_config(session_dir: Path, *, repo_root: Path) -> Any:
    from small_paper.config import load_pilot_config

    cfg_meta = _load_json(session_dir / "live_session_config.json")
    cfg_path = Path(str(cfg_meta.get("config_path") or ""))
    if not cfg_path.is_file():
        summary = _load_json(session_dir / "small_paper_summary.json")
        cfg_path = Path(str(summary.get("config_path") or ""))
    if not cfg_path.is_file():
        fallback = repo_root / "kabu_native" / "configs" / "small_paper_pilot_q070_cap3.yaml"
        cfg_path = fallback if fallback.is_file() else cfg_path
    return load_pilot_config(cfg_path)


def _resolve_poll_interval_sec(session_dir: Path, config: Any) -> float:
    cfg_meta = _load_json(session_dir / "live_session_config.json")
    val = cfg_meta.get("poll_interval_sec")
    if val is not None:
        return float(val)
    summary = _load_json(session_dir / "small_paper_summary.json")
    val = summary.get("poll_interval_sec")
    if val is not None:
        return float(val)
    return float(getattr(config, "poll_interval_sec", 5.0) or 5.0)


def _resolve_structural_exit_policy(session_dir: Path, config: Any) -> str:
    cfg_meta = _load_json(session_dir / "live_session_config.json")
    policy = str(cfg_meta.get("structural_exit_policy") or "").strip()
    if policy:
        return policy
    policy = str(getattr(config, "structural_exit_policy", "") or "").strip()
    if policy:
        return policy
    from research.structural_exit_policies import POLICY_COMBINED_STRUCTURAL_EXIT_V1

    return POLICY_COMBINED_STRUCTURAL_EXIT_V1


def enumerate_sessions(
    *,
    small_paper_root: Path,
    period_start: str = PERIOD_START,
    period_end: str = PERIOD_END,
) -> list[Path]:
    if not small_paper_root.is_dir():
        return []
    sessions: list[Path] = []
    for day_dir in sorted(small_paper_root.iterdir()):
        if not day_dir.is_dir():
            continue
        day = day_dir.name
        if not (day.isdigit() and len(day) == 8):
            continue
        if not (period_start <= day <= period_end):
            continue
        for session_dir in sorted(day_dir.iterdir()):
            if session_dir.is_dir():
                sessions.append(session_dir.resolve())
    return sessions


def classify_session(session_dir: Path) -> str:
    day = session_dir.parent.name
    if not (day.isdigit() and len(day) == 8 and PERIOD_START <= day <= PERIOD_END):
        return "skipped_out_of_period"

    if _is_debug_session(session_dir):
        return "skipped_debug"

    source = _session_source(session_dir)
    if source == "push-replay":
        return "skipped_push_replay"

    if not _is_live_session_dir(session_dir):
        return "skipped_not_live"

    if (session_dir / "structural_trades.csv").is_file():
        return "skipped_existing"

    has_events = (session_dir / "small_paper_events.csv").is_file()
    has_summary = (session_dir / "small_paper_summary.json").is_file()
    if not has_events or not has_summary:
        return "skipped_missing_inputs"

    return "pending"


def backfill_session(
    session_dir: Path,
    *,
    repo_root: Path,
) -> dict[str, Any]:
    day = session_dir.parent.name
    status = classify_session(session_dir)
    row: dict[str, Any] = {
        "day": day,
        "session": session_dir.name,
        "session_dir": str(session_dir),
        "status": status,
        "source": _session_source(session_dir),
        "rows_generated": 0,
        "structural_trade_count": None,
        "structural_pf": None,
        "error": "",
    }
    if status != "pending":
        return row

    try:
        config = _resolve_pilot_config(session_dir, repo_root=repo_root)
        poll_interval_sec = _resolve_poll_interval_sec(session_dir, config)
        structural_exit_policy = _resolve_structural_exit_policy(session_dir, config)
        review = build_and_write_structural_observer_review(
            session_dir,
            pilot_config=config,
            poll_interval_sec=poll_interval_sec,
            structural_exit_policy=structural_exit_policy,
        )
        trades_path = session_dir / "structural_trades.csv"
        rows = _count_csv_rows(trades_path)
        row.update(
            {
                "status": "generated",
                "rows_generated": rows,
                "structural_trade_count": review.get("structural_trade_count"),
                "structural_pf": review.get("structural_pf"),
            }
        )
    except Exception as exc:
        row.update(
            {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    return row


def run_structural_trades_backfill(
    *,
    repo_root: Path,
    small_paper_root: Optional[Path] = None,
    period_start: str = PERIOD_START,
    period_end: str = PERIOD_END,
) -> dict[str, Any]:
    sp_root = small_paper_root or (repo_root / "kabu_native" / "results" / "small_paper")
    session_dirs = enumerate_sessions(
        small_paper_root=sp_root,
        period_start=period_start,
        period_end=period_end,
    )

    by_session: list[dict[str, Any]] = []
    counters = {
        "processed_session_count": 0,
        "generated_structural_trades_count": 0,
        "skipped_existing_count": 0,
        "skipped_push_replay_count": 0,
        "skipped_debug_count": 0,
        "skipped_not_live_count": 0,
        "skipped_missing_inputs_count": 0,
        "failed_session_count": 0,
        "rows_generated_total": 0,
    }

    for session_dir in session_dirs:
        pre_status = classify_session(session_dir)
        if pre_status == "pending":
            counters["processed_session_count"] += 1
            row = backfill_session(session_dir, repo_root=repo_root)
        else:
            row = {
                "day": session_dir.parent.name,
                "session": session_dir.name,
                "session_dir": str(session_dir),
                "status": pre_status,
                "source": _session_source(session_dir),
                "rows_generated": _count_csv_rows(session_dir / "structural_trades.csv"),
                "structural_trade_count": None,
                "structural_pf": None,
                "error": "",
            }

        status = str(row.get("status") or "")
        if status == "generated":
            counters["generated_structural_trades_count"] += 1
            counters["rows_generated_total"] += int(row.get("rows_generated") or 0)
        elif status == "failed":
            counters["failed_session_count"] += 1
        elif status == "skipped_existing":
            counters["skipped_existing_count"] += 1
        elif status == "skipped_push_replay":
            counters["skipped_push_replay_count"] += 1
        elif status == "skipped_debug":
            counters["skipped_debug_count"] += 1
        elif status == "skipped_not_live":
            counters["skipped_not_live_count"] += 1
        elif status == "skipped_missing_inputs":
            counters["skipped_missing_inputs_count"] += 1

        by_session.append(row)

    return {
        "phase": "265-Structural-Trades-Backfill-For-Research",
        "title": "Structural trades backfill for research",
        "generated_at": _now_iso(),
        "purpose": "Derive structural_trades.csv for Phase263 dynamic stop shadow",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "runtime_reflected": False,
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
            "paper_event_mutation_forbidden": True,
            "derived_csv_only": True,
        },
        "period": {"start": period_start, "end": period_end},
        "small_paper_root": str(sp_root),
        "summary": counters,
        "by_session": by_session,
    }


def rerun_phase263(*, repo_root: Path, reports_dir: Path) -> dict[str, Any]:
    from research.equity_dynamic_stop_shadow import EquityDynamicStopShadow

    job = EquityDynamicStopShadow(repo_root=repo_root, reports_dir=reports_dir)
    result = job.run()
    paths = job.write_outputs(result)
    summary = result.get("summary") or {}
    period_days = list(summary.get("period_days") or [])
    base_entry_count = int(summary.get("base_entry_count") or 0)
    summary_rows = result.get("summary_by_equity_risk_pct") or []

    verification = {
        "period_days_nonzero": len(period_days) > 0,
        "trade_count_nonzero": base_entry_count > 0,
        "summary_by_equity_risk_pct_generated": len(summary_rows) > 0,
        "all_checks_passed": (
            len(period_days) > 0 and base_entry_count > 0 and len(summary_rows) > 0
        ),
    }

    return {
        "phase": "263-rerun-after-265-backfill",
        "generated_at": _now_iso(),
        "period_days": period_days,
        "period_day_count": len(period_days),
        "base_entry_count": base_entry_count,
        "summary_by_equity_risk_pct_row_count": len(summary_rows),
        "verification": verification,
        "verdict": result.get("verdict") or {},
        "output_paths": {k: str(v) for k, v in paths.items()},
    }


def build_report_markdown(
    *,
    backfill: Mapping[str, Any],
    phase263_rerun: Mapping[str, Any],
) -> str:
    summary = backfill.get("summary") or {}
    verification = phase263_rerun.get("verification") or {}
    lines = [
        "# Phase265 Structural Trades Backfill For Research",
        "",
        "Derived structural_trades.csv from existing small_paper events (research only).",
        "",
        f"- period: {PERIOD_START} - {PERIOD_END}",
        f"- processed sessions: {summary.get('processed_session_count')}",
        f"- generated: {summary.get('generated_structural_trades_count')}",
        f"- rows generated total: {summary.get('rows_generated_total')}",
        f"- skipped existing: {summary.get('skipped_existing_count')}",
        f"- skipped push-replay: {summary.get('skipped_push_replay_count')}",
        f"- skipped debug: {summary.get('skipped_debug_count')}",
        f"- skipped missing inputs: {summary.get('skipped_missing_inputs_count')}",
        f"- failed: {summary.get('failed_session_count')}",
        "",
        "## Phase263 rerun verification",
        "",
        f"- period_days_nonzero: {verification.get('period_days_nonzero')}",
        f"- trade_count_nonzero: {verification.get('trade_count_nonzero')}",
        f"- summary_by_equity_risk_pct_generated: {verification.get('summary_by_equity_risk_pct_generated')}",
        f"- all_checks_passed: {verification.get('all_checks_passed')}",
        "",
        f"Phase263 period days ({phase263_rerun.get('period_day_count')}): "
        f"{', '.join(phase263_rerun.get('period_days') or [])}",
        f"Phase263 base entries: {phase263_rerun.get('base_entry_count')}",
        "",
    ]
    failed = [r for r in backfill.get("by_session") or [] if r.get("status") == "failed"]
    if failed:
        lines.extend(["## Failed sessions", ""])
        for row in failed:
            lines.append(f"- `{row.get('day')}/{row.get('session')}`: {row.get('error')}")
        lines.append("")
    return "\n".join(lines)


@dataclass
class StructuralTradesBackfillJob:
    repo_root: Path
    reports_dir: Path

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase265_structural_trades_backfill_summary.json",
            "by_session": self.reports_dir / "phase265_structural_trades_backfill_by_session.csv",
            "phase263_rerun": self.reports_dir / "phase265_phase263_rerun_summary.json",
            "report": self.reports_dir / "phase265_report.md",
        }

    def run(
        self,
        *,
        small_paper_root: Optional[Path] = None,
        period_start: str = PERIOD_START,
        period_end: str = PERIOD_END,
        skip_phase263_rerun: bool = False,
    ) -> dict[str, Any]:
        backfill = run_structural_trades_backfill(
            repo_root=self.repo_root,
            small_paper_root=small_paper_root,
            period_start=period_start,
            period_end=period_end,
        )
        phase263_rerun: dict[str, Any] = {}
        if not skip_phase263_rerun:
            phase263_rerun = rerun_phase263(repo_root=self.repo_root, reports_dir=self.reports_dir)
        return {
            "backfill": backfill,
            "phase263_rerun": phase263_rerun,
            "report_markdown": build_report_markdown(backfill=backfill, phase263_rerun=phase263_rerun),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["summary"].parent.mkdir(parents=True, exist_ok=True)
        backfill = dict(result.get("backfill") or {})
        by_session = backfill.pop("by_session", [])
        paths["summary"].write_text(json.dumps(backfill, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        _write_csv(paths["by_session"], SESSION_CSV_FIELDS, by_session)
        phase263_rerun = result.get("phase263_rerun") or {}
        paths["phase263_rerun"].write_text(
            json.dumps(phase263_rerun, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths["report"].write_text(str(result.get("report_markdown") or ""), encoding="utf-8")
        return paths
