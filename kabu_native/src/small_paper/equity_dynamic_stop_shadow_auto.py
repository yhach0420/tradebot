"""
Phase266: auto-run Equity Dynamic Stop Shadow after paper session aggregation.

Research-only — does not change Runtime / Universe / Entry / YAML trading behavior.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
LOG_PREFIX = "[equity_dynamic_stop_shadow]"

log = logging.getLogger(__name__)


def infer_validation_day(
    *,
    day: Optional[str] = None,
    output_dir: Optional[Path] = None,
) -> str:
    if day:
        return str(day)
    if output_dir is not None:
        for part in reversed(output_dir.resolve().parts):
            if len(part) == 8 and part.isdigit():
                return part
    return datetime.now(JST).strftime("%Y%m%d")


def _ensure_structural_trades_csv(
    session_dir: Path,
    *,
    repo_root: Path,
    config: Any,
    poll_interval_sec: Optional[float],
) -> bool:
    trades_path = session_dir / "structural_trades.csv"
    if trades_path.is_file():
        return True
    try:
        from research.structural_trades_backfill import backfill_session

        row = backfill_session(session_dir, repo_root=repo_root)
        if str(row.get("status") or "") == "generated":
            return trades_path.is_file()
        from research.structural_observer_review import build_and_write_structural_observer_review

        build_and_write_structural_observer_review(
            session_dir,
            pilot_config=config,
            poll_interval_sec=poll_interval_sec,
            structural_exit_policy=str(getattr(config, "structural_exit_policy", "") or ""),
        )
    except Exception as exc:
        log.warning("%s structural_trades_prepare_failed: %s", LOG_PREFIX, exc)
        return trades_path.is_file()
    return trades_path.is_file()


def _emit_block(block: Mapping[str, Any]) -> None:
    lines = [LOG_PREFIX, f"day={block.get('day')}", f"status={block.get('status')}"]
    if block.get("days") is not None:
        lines.append(f"days={block.get('days')}")
    if block.get("best_policy_1p5m") is not None:
        lines.append(f"best_policy_1p5m={block.get('best_policy_1p5m')}")
    if block.get("best_policy_5m") is not None:
        lines.append(f"best_policy_5m={block.get('best_policy_5m')}")
    if block.get("adopt_not_allowed") is not None:
        lines.append(f"adopt_not_allowed={block.get('adopt_not_allowed')}")
    if block.get("warning"):
        lines.append(f"warning={block.get('warning')}")
    text = "\n".join(lines)
    print(text, flush=True)
    if block.get("status") == "warning":
        log.warning(text)
    else:
        log.info(text)


def run_equity_dynamic_stop_shadow_auto(
    *,
    repo_root: Path,
    output_dir: Optional[Path] = None,
    day: Optional[str] = None,
    config: Any = None,
    poll_interval_sec: Optional[float] = None,
    reports_dir: Optional[Path] = None,
) -> dict[str, Any]:
    from research.equity_dynamic_stop_shadow import PERIOD_START, EquityDynamicStopShadow

    validation_day = infer_validation_day(day=day, output_dir=output_dir)
    block: dict[str, Any] = {
        "phase": "266-Auto-Run-Equity-Dynamic-Stop-Shadow",
        "day": validation_day,
        "status": "skipped",
        "days": None,
        "best_policy_1p5m": None,
        "best_policy_5m": None,
        "adopt_not_allowed": None,
    }

    if validation_day < PERIOD_START:
        block["status"] = "skipped_before_period"
        _emit_block(block)
        return block

    try:
        if output_dir is not None and config is not None:
            _ensure_structural_trades_csv(
                output_dir,
                repo_root=repo_root,
                config=config,
                poll_interval_sec=poll_interval_sec,
            )

        reports = reports_dir or (repo_root / "kabu_native" / "results" / "reports")
        job = EquityDynamicStopShadow(repo_root=repo_root, reports_dir=reports)
        result = job.run()
        paths = job.write_outputs(result)

        summary = result.get("summary") or {}
        verdict = result.get("verdict") or {}
        period_days = list(summary.get("period_days") or [])
        block.update(
            {
                "days": len(period_days),
                "period_days": period_days,
                "base_entry_count": summary.get("base_entry_count"),
                "best_policy_1p5m": verdict.get("best_policy_at_1p5m"),
                "best_policy_5m": verdict.get("best_policy_at_5m"),
                "adopt_not_allowed": verdict.get("adopt_not_allowed"),
                "summary_path": str(paths.get("summary")),
                "last_status": "logged_forward_shadow",
            }
        )
        block["status"] = "success" if period_days else "skipped_no_period_trades"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        block["status"] = "warning"
        block["warning"] = error
        log.warning("%s run_failed day=%s error=%s", LOG_PREFIX, validation_day, error)

    _emit_block(block)
    return block
