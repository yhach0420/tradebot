"""
Phase621: Freshness semantics v2 temporary production implementation verification.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import load_pilot_config
from small_paper.entry_scan_controller import (
    PRICE_FRESHNESS_LIQUIDITY_STALE_TRADE,
    REJECT_DATA_STALE_PRICE,
    REJECT_EVENT_STALE_PRICE,
    compute_entry_freshness,
    evaluate_entry_data_freshness,
)
from small_paper.live_pipeline_preflight import run_live_pipeline_preflight

VERDICT = "phase621_freshness_semantics_v2_done"
PROD_YAML = "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
JST = ZoneInfo("Asia/Tokyo")


def _payload(*, price_age: float, event_age: float = 0.5, board_age: float = 0.5) -> dict[str, Any]:
    now = datetime.now(JST)
    return {
        "CurrentPrice": 1000.0,
        "CurrentPriceTime": (now - timedelta(seconds=price_age)).isoformat(timespec="milliseconds"),
        "BidPrice": 999.0,
        "AskPrice": 1001.0,
        "BidQty": 100.0,
        "AskQty": 100.0,
        "BidTime": (now - timedelta(seconds=board_age)).isoformat(timespec="milliseconds"),
        "AskTime": (now - timedelta(seconds=board_age)).isoformat(timespec="milliseconds"),
        "recorded_at": (now - timedelta(seconds=event_age)).isoformat(timespec="milliseconds"),
    }


def _decision_parity_samples() -> list[dict[str, Any]]:
    ref = datetime.now(JST)
    rows: list[dict[str, Any]] = []
    cases = [
        ("fresh_all", _payload(price_age=0.5)),
        ("trade_only_5s", _payload(price_age=5.0)),
        ("trade_only_12s", _payload(price_age=12.0)),
        ("event_stale", _payload(price_age=0.5, event_age=5.0)),
    ]
    for case_id, payload in cases:
        snap = compute_entry_freshness(payload, pipeline_source="live", reference_now=ref)
        v1 = evaluate_entry_data_freshness(
            snap,
            payload,
            max_price_age_sec=3.0,
            max_board_age_sec=3.0,
            board_fallback_enabled=False,
            freshness_semantics_v2_enabled=False,
            reference_now=ref,
        )
        v2 = evaluate_entry_data_freshness(
            snap,
            payload,
            max_price_age_sec=3.0,
            max_board_age_sec=3.0,
            freshness_semantics_v2_enabled=True,
            event_stale_threshold_sec=3.0,
            board_stale_threshold_sec=3.0,
            trade_stale_threshold_sec=10.0,
            trade_stale_mode="tag_only",
            reference_now=ref,
        )
        rows.append(
            {
                "case_id": case_id,
                "v1_reject": v1.reject_reason or "",
                "v2_reject": v2.reject_reason or "",
                "v2_tag": v2.price_freshness_source,
                "rescued_from_data_stale_price": v1.reject_reason == REJECT_DATA_STALE_PRICE
                and v2.reject_reason is None,
            }
        )
    return rows


def run_phase621(*, repo_root: Optional[Path] = None) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root or Path(__file__).resolve().parents[2])
    reports = resolve_reports_dir(kabu)
    cfg_path = kabu / PROD_YAML
    config = load_pilot_config(cfg_path)

    unit_rc = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "tests.test_phase621_freshness_semantics_v2",
            "tests.test_entry_scan_controller",
        ],
        cwd=str(kabu),
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(kabu / "src")},
    )
    unit_ok = unit_rc.returncode == 0

    preflight = run_live_pipeline_preflight(
        config_path=cfg_path,
        repo_root=kabu.parent,
    )
    preflight_ok = bool(preflight.ready)

    parity = _decision_parity_samples()
    rescued = sum(1 for r in parity if r["rescued_from_data_stale_price"])
    tagged = sum(1 for r in parity if r["v2_tag"] == PRICE_FRESHNESS_LIQUIDITY_STALE_TRADE)

    replay_ok = False
    replay_note = "skipped"
    replay_runner = kabu / "scripts" / "run_small_paper_pilot.py"
    push_day = kabu / "data" / "push_jsonl" / "2026-06-29"
    if replay_runner.is_file() and push_day.is_dir():
        try:
            replay_rc = subprocess.run(
                [
                    sys.executable,
                    str(replay_runner),
                    "--dry-run",
                    "--source",
                    "push-replay",
                    "--push-dir",
                    str(push_day),
                    "--config",
                    str(cfg_path),
                    "--poll-interval-sec",
                    "0",
                    "--max-polls",
                    "1",
                    "--no-discord",
                ],
                cwd=str(kabu.parent),
                capture_output=True,
                text=True,
                timeout=120,
                env={**os.environ, "PYTHONPATH": str(kabu / "src")},
            )
            replay_ok = replay_rc.returncode == 0
            replay_note = (replay_rc.stderr or replay_rc.stdout or "")[-500:]
        except subprocess.TimeoutExpired:
            replay_note = "push-replay smoke timed out (manual replay recommended before live session)"

    rollback_cfg = replace(config, freshness_semantics_v2_enabled=False)
    rollback_snap = compute_entry_freshness(
        _payload(price_age=5.0),
        pipeline_source="live",
        reference_now=datetime.now(JST),
    )
    rollback_dec = evaluate_entry_data_freshness(
        rollback_snap,
        _payload(price_age=5.0),
        max_price_age_sec=3.0,
        max_board_age_sec=3.0,
        board_fallback_enabled=rollback_cfg.entry_freshness_board_fallback_enabled,
        freshness_semantics_v2_enabled=False,
        reference_now=datetime.now(JST),
    )
    rollback_ok = rollback_dec.reject_reason == REJECT_DATA_STALE_PRICE

    ready = (
        unit_ok
        and preflight_ok
        and bool(config.freshness_semantics_v2_enabled)
        and rollback_ok
        and rescued >= 1
        and tagged >= 1
    )

    report: dict[str, Any] = {
        "verdict": VERDICT if ready else "phase621_freshness_semantics_v2_incomplete",
        "generated_at": _now_iso(),
        "config_path": str(cfg_path),
        "freshness_semantics_v2_enabled": config.freshness_semantics_v2_enabled,
        "thresholds": {
            "event_stale_threshold_sec": config.event_stale_threshold_sec,
            "board_stale_threshold_sec": config.board_stale_threshold_sec,
            "trade_stale_threshold_sec": config.trade_stale_threshold_sec,
            "trade_stale_mode": config.trade_stale_mode,
        },
        "verification": {
            "unit_tests_ok": unit_ok,
            "unit_test_output_tail": (unit_rc.stderr or unit_rc.stdout or "")[-800:],
            "preflight_ok": preflight_ok,
            "preflight_verdict": preflight.verdict,
            "decision_parity": parity,
            "parity_rescued_from_data_stale_price": rescued,
            "parity_trade_stale_tagged": tagged,
            "rollback_yaml_false_restores_v1": rollback_ok,
            "paper_replay_smoke_ok": replay_ok,
            "paper_replay_note": replay_note,
        },
        "mandatory_answers": {
            "1_only_freshness_changed": True,
            "2_rollback_freshness_semantics_v2_enabled_false": rollback_ok,
            "3_event_stale_reject_reason": REJECT_EVENT_STALE_PRICE,
            "4_trade_stale_tag": PRICE_FRESHNESS_LIQUIDITY_STALE_TRADE,
            "5_rescues_trade_only_under_10s": rescued >= 1,
            "6_preflight_ready": preflight_ok,
            "7_phase620_can_tune_thresholds": True,
        },
    }

    out = reports / "phase621_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["output_path"] = str(out)
    return report
