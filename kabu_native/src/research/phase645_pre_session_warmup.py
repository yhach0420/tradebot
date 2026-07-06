"""
Phase645: Pre-session warmup register — audit and mandatory answers.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from small_paper.pre_session_warmup import (
    PHASE645_VERDICT,
    compute_ready_delay_sec,
    pre_session_warmup_enabled,
)

REPORT_DIR = "phase645_pre_session_warmup"

AUDIT_FIELDS = [
    "day",
    "session_dir",
    "session_kind",
    "pre_session_warmup_enabled",
    "allowed_entry_start",
    "session_ready_ts",
    "first_gate_eval_ts",
    "ready_delay_sec",
    "legacy_ready_delay_sec",
    "warmup_ring_push_count",
    "gate_evaluations",
]

# Phase572 reference: typical legacy init delay after 09:03 (seconds)
LEGACY_AM_READY_DELAY_SEC = 918.0  # ~15.3 min (20260625)
LEGACY_PM_READY_DELAY_SEC = 1384.0  # ~23 min (20260625 PM)


def _load_summary(path: Path) -> Optional[dict[str, Any]]:
    fp = path / "small_paper_summary.json"
    if not fp.is_file():
        return None
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _legacy_ready_delay(summary: Mapping[str, Any]) -> Optional[float]:
    cfg_fp = summary.get("session_dir")
    if not cfg_fp:
        return None
    # Approximate from live_session_config.generated_at vs allowed_entry_start
    allowed = summary.get("allowed_entry_start")
    ready = summary.get("session_ready_ts") or summary.get("generated_at")
    if not allowed or not ready:
        return None
    from datetime import date

    return compute_ready_delay_sec(
        allowed_entry_start=str(allowed),
        first_gate_eval_ts=str(ready),
        trade_date=date.today(),
    )


def audit_live_sessions(small_paper_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not small_paper_root.is_dir():
        return rows
    for day_dir in sorted(small_paper_root.iterdir()):
        if not day_dir.is_dir() or not (len(day_dir.name) == 8 and day_dir.name.isdigit()):
            continue
        for sess in sorted(day_dir.glob("live_session_*")):
            summary = _load_summary(sess)
            if summary is None:
                continue
            am_pm = summary.get("am_pm_session") or {}
            if isinstance(am_pm, Mapping):
                kind = str(am_pm.get("kind") or "am")
                allowed = am_pm.get("allowed_entry_start")
            else:
                kind = "am"
                allowed = summary.get("allowed_entry_start")
            rows.append(
                {
                    "day": day_dir.name,
                    "session_dir": str(sess),
                    "session_kind": kind,
                    "pre_session_warmup_enabled": summary.get("pre_session_warmup_enabled"),
                    "allowed_entry_start": allowed,
                    "session_ready_ts": summary.get("session_ready_ts"),
                    "first_gate_eval_ts": summary.get("first_gate_eval_ts"),
                    "ready_delay_sec": summary.get("ready_delay_sec"),
                    "legacy_ready_delay_sec": _legacy_ready_delay(summary),
                    "warmup_ring_push_count": summary.get("pre_session_warmup_ring_push_count"),
                    "gate_evaluations": summary.get("gate_evaluations"),
                }
            )
    return rows


def build_mandatory_answers(*, audit_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    with_warmup = [r for r in audit_rows if r.get("pre_session_warmup_enabled")]
    delays = [
        float(r["ready_delay_sec"])
        for r in with_warmup
        if r.get("ready_delay_sec") is not None
    ]
    return {
        "1_legacy_ready_delay_sec_am_typical": LEGACY_AM_READY_DELAY_SEC,
        "1_legacy_ready_delay_sec_pm_typical": LEGACY_PM_READY_DELAY_SEC,
        "2_wait_until_change": (
            "Warmup enabled: wait_until(warmup_start 08:50/12:15) then init; "
            "legacy: wait_until(session_start 09:03/12:33) then init"
        ),
        "3_entry_block_guarantee": (
            "ring_only_warmup_active() → _warmup_ring_only_push() returns before Stage0 gate; "
            "entry_allowed_now() enforced via am_pm_policy"
        ),
        "4_target_first_gate_eval_sec": "<= 60s after allowed_entry_start when warmup completes by warmup_start",
        "5_pm_supported": True,
        "6_rollback": "Set live.pre_session_warmup_enabled=false in production YAML (no bat change)",
        "7_run_paper_trade_bat_change_required": False,
        "live_sessions_with_warmup_metrics": len(with_warmup),
        "live_ready_delay_samples": delays,
        "live_ready_delay_p50": sorted(delays)[len(delays) // 2] if delays else None,
        "no_live_warmup_traces_yet": len(with_warmup) == 0,
    }


@dataclass
class Phase645Job:
    native_root: Path

    def run(self) -> dict[str, Any]:
        small_paper = self.native_root / "results" / "small_paper"
        audit_rows = audit_live_sessions(small_paper)
        answers = build_mandatory_answers(audit_rows=audit_rows)
        return {
            "verdict": PHASE645_VERDICT,
            "generated_at": _now_iso(),
            "mandatory_answers": answers,
            "audit_rows": audit_rows,
            "implementation": {
                "pre_session_warmup_enabled_default": True,
                "pre_session_warmup_am_start": "08:50",
                "pre_session_warmup_pm_start": "12:15",
            },
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        out = self.native_root / "results" / "reports" / REPORT_DIR
        out.mkdir(parents=True, exist_ok=True)
        _write_csv(out / "phase645_ready_delay_audit.csv", AUDIT_FIELDS, list(result.get("audit_rows") or []))
        report = {
            "phase": "645",
            "verdict": result.get("verdict"),
            "generated_at": result.get("generated_at"),
            "mandatory_answers": result.get("mandatory_answers"),
            "implementation": result.get("implementation"),
        }
        report_fp = out / "phase645_report.json"
        report_fp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return {"report": report_fp, "audit": out / "phase645_ready_delay_audit.csv"}


def main() -> int:
    here = Path(__file__).resolve()
    native = here.parents[2]
    job = Phase645Job(native_root=native)
    result = job.run()
    paths = job.write_outputs(result)
    print(json.dumps({"verdict": result.get("verdict"), "paths": {k: str(v) for k, v in paths.items()}}, indent=2))
    print(json.dumps(result.get("mandatory_answers"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
