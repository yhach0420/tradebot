"""
Phase635: PBv2-only rise5 shadow guard validation report (research only).
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase635_pbv2_rise5_shadow"
PHASE635_VERDICT = "phase635_pbv2_rise5_shadow_done"
PHASE635_FAIL = "phase635_pbv2_rise5_shadow_failed"


def _write_csv(fp: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    with fp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def run() -> dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    from small_paper.config import load_pilot_config
    from small_paper.pbv2_rise5_shadow import (
        compute_pbv2_rise5_shadow_fields,
        enrich_exit_pbv2_rise5_shadow_fields,
        rise5_shadow_enabled,
    )
    from small_paper.or_overlay_cap import ENTRY_TYPE_OR

    cfg_path = (
        NATIVE_ROOT
        / "configs"
        / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    config = load_pilot_config(cfg_path)

    scenarios = [
        ("pbv2_keep", {"entry_type": "PBV2", "entry_rise_5min_pct": 0.5}),
        ("pbv2_block", {"entry_type": "PBV2", "entry_rise_5min_pct": 2.5}),
        ("or_skip", {"entry_type": ENTRY_TYPE_OR, "entry_rise_5min_pct": 5.0}),
    ]
    trade_rows: list[dict[str, Any]] = []
    for sid, trade in scenarios:
        entry = compute_pbv2_rise5_shadow_fields(config, trade)
        exit_px = 1010.0 if sid == "pbv2_keep" else 990.0
        exit_fields = enrich_exit_pbv2_rise5_shadow_fields(
            entry,
            entry_price=1000.0,
            exit_price=exit_px,
            exit_reason="stop_hit" if exit_px < 1000 else "trailing_mfe_exit",
            peak_mfe_pct=0.8,
            peak_mae_pct=-0.4,
        )
        trade_rows.append(
            {
                "scenario_id": sid,
                **{k: entry.get(k) for k in entry},
                **{k: exit_fields.get(k) for k in exit_fields},
            }
        )

    actual_total = 0.0
    shadow_total = 0.0
    for r in trade_rows:
        if r.get("scenario_id") == "or_skip":
            continue
        blocked = bool(r.get("pbv2_rise5_shadow_block"))
        ep, xp = 1000.0, 1010.0 if r["scenario_id"] == "pbv2_keep" else 990.0
        actual = round((xp - ep) * 100.0, 2)
        shadow = 0.0 if blocked else actual
        actual_total += actual
        shadow_total += shadow
    summary_row = {
        "pbv2_rise5_shadow_enabled": rise5_shadow_enabled(config),
        "pbv2_rise5_shadow_threshold_pct": config.pbv2_rise5_shadow_threshold_pct,
        "pbv2_rise5_shadow_apply_pool": config.pbv2_rise5_shadow_apply_pool,
        "scenario_count": len(trade_rows),
        "pbv2_block_scenarios": sum(1 for r in trade_rows if r.get("pbv2_rise5_shadow_block")),
        "or_untouched_scenarios": sum(
            1 for r in trade_rows if r.get("scenario_id") == "or_skip" and not r.get("pbv2_rise5_shadow_block")
        ),
        "actual_total_pnl_yen_100": round(actual_total, 2),
        "shadow_total_pnl_yen_100": round(shadow_total, 2),
        "net_shadow_effect_yen": round(shadow_total - actual_total, 2),
    }

    _write_csv(
        REPORT_DIR / "phase635_shadow_trades.csv",
        trade_rows,
        list(trade_rows[0].keys()) if trade_rows else [],
    )
    _write_csv(
        REPORT_DIR / "phase635_shadow_summary.csv",
        [summary_row],
        list(summary_row.keys()),
    )

    checks = {
        "yaml_enabled": rise5_shadow_enabled(config),
        "threshold_is_184": float(config.pbv2_rise5_shadow_threshold_pct) == 1.84,
        "apply_pool_pbv2_only": config.pbv2_rise5_shadow_apply_pool == "PBV2_ONLY",
        "pbv2_only_blocked": any(
            r["scenario_id"] == "pbv2_block" and r.get("pbv2_rise5_shadow_block") for r in trade_rows
        ),
        "or_not_blocked": all(
            not r.get("pbv2_rise5_shadow_block") for r in trade_rows if r["scenario_id"] == "or_skip"
        ),
        "shadow_delta_on_blocked": any(
            float(r.get("pbv2_rise5_shadow_delta_yen") or 0) != 0
            for r in trade_rows
            if r.get("pbv2_rise5_shadow_block")
        ),
    }
    report = {
        "phase": "phase635_pbv2_rise5_shadow",
        "verdict": PHASE635_VERDICT if all(checks.values()) else PHASE635_FAIL,
        "config_path": str(cfg_path),
        "checks": checks,
        "summary": summary_row,
        "implementation": {
            "module": "src/small_paper/pbv2_rise5_shadow.py",
            "hook": "pilot_runner._execute_accepted_entry (post-accept, pre gate.record_accepted)",
            "exit_hook": "observer_position_tracker enrich_exit_pbv2_rise5_shadow_fields",
            "parity_note": "Shadow fields only; no gate decision change",
        },
    }
    (REPORT_DIR / "phase635_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    report = run()
    print(json.dumps({"verdict": report.get("verdict")}, ensure_ascii=False))
    return 0 if report.get("verdict") == PHASE635_VERDICT else 1


if __name__ == "__main__":
    raise SystemExit(main())
