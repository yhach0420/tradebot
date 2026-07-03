"""
Phase624: Structural isolation experiment — CORE_ONLY vs FULL_EXTENSION on identical PUSH input.
Compares gate outcomes per push (not latency).
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import load_pilot_config
from small_paper.core_runtime_mode import CoreRuntimeMode, apply_core_runtime_mode, finalize_core_runtime_config
from small_paper.pilot_runner import EVENT_FIELDS

VERDICT = "phase624_structural_isolation_done"
REPORT_SUBDIR = "phase624_structural_isolation"
PROD_YAML = "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
DEFAULT_DAY = "2026-06-25"
MAX_PUSH_ROWS = 100_000
POLL_INTERVAL_SEC = 5.0

FEATURE_COMPARE_FIELDS = (
    "current_price",
    "momentum_continuation_score",
    "entry_momentum_continuation_score",
    "entry_order_book_imbalance",
    "continuation_quality_score",
    "entry_expectancy_score_v2",
    "daytrade_suitability_score",
    "spread_bps",
    "price_age_sec",
    "board_age_sec",
    "price_freshness_source",
    "live_feature_complete",
    "quality_fallback_path",
    "trading_value",
    "turnover_proxy",
)

GATE_COMPARE_FIELDS = (
    "gate_accept",
    "gate_reject_reason",
    "pbv2_internal_reason",
    "entry_score_v2_gate_pass",
    "entry_expectancy_score_v2",
    "reject_reason",
)

STALE_REJECT_REASONS = frozenset(
    {
        "data_stale_price",
        "data_stale_board",
        "event_stale_price",
    }
)

BRANCH_PIPELINE: tuple[tuple[str, str], ...] = (
    ("features", "live_feature_bridge.enrich_payload"),
    ("freshness:price_age_sec", "entry_scan_controller.compute_entry_freshness"),
    ("freshness:price_freshness_source", "entry_scan_controller.evaluate_entry_data_freshness"),
    ("freshness:stale_reject", "entry_scan_controller.evaluate_entry_data_freshness"),
    ("pbv2:reached", "exposure_gate.evaluate_entry"),
    ("pbv2:entry_score_v2_gate_pass", "exposure_gate.entry_score_v2_gate"),
    ("pbv2:internal_reason", "exposure_gate.evaluate_entry"),
    ("gate:gate_accept", "exposure_gate.evaluate_entry"),
    ("gate:gate_reject_reason", "exposure_gate.evaluate_entry"),
    ("accepted:event_type", "entry_scan_controller.queue_accepted_candidate"),
)


def _stream_candidate_events(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    out: dict[tuple[str, int], dict[str, Any]] = {}
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("event_type") or "") != "candidate":
                continue
            sym = str(row.get("symbol") or "")
            msg_i = int(row.get("message_index") or 0)
            out[(sym, msg_i)] = row
    return out


def _accepted_keys(path: Path) -> set[tuple[str, int]]:
    out: set[tuple[str, int]] = set()
    if not path.is_file():
        return out
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("event_type") or "") != "accepted":
                continue
            sym = str(row.get("symbol") or "")
            msg_i = int(row.get("message_index") or 0)
            out.add((sym, msg_i))
    return out


def _norm(val: Any) -> str:
    if val is None:
        return ""
    if isinstance(val, bool):
        return str(val).lower()
    if isinstance(val, float):
        return f"{val:.6f}"
    return str(val).strip()


def _field_diff(a: Mapping[str, Any], b: Mapping[str, Any], field: str) -> bool:
    return _norm(a.get(field)) != _norm(b.get(field))


def _pbv2_reached(row: Mapping[str, Any]) -> bool:
    reason = str(row.get("gate_reject_reason") or row.get("reject_reason") or "")
    return reason not in STALE_REJECT_REASONS


def _event_stale(row: Mapping[str, Any]) -> bool:
    return str(row.get("gate_reject_reason") or "") == "event_stale_price"


def _board_stale(row: Mapping[str, Any]) -> bool:
    return str(row.get("gate_reject_reason") or "") == "data_stale_board"


def _trade_stale(row: Mapping[str, Any]) -> bool:
    return str(row.get("price_freshness_source") or "") == "liquidity_stale_trade"


def _first_branch_point(
    a: Mapping[str, Any],
    b: Mapping[str, Any],
    *,
    a_accepted: bool,
    b_accepted: bool,
) -> tuple[str, str, str, str]:
    for field in FEATURE_COMPARE_FIELDS:
        if _field_diff(a, b, field):
            return (
                f"features:{field}",
                "live_feature_bridge.enrich_payload",
                _norm(a.get(field)),
                _norm(b.get(field)),
            )

    if _field_diff(a, b, "price_age_sec"):
        return (
            "freshness:price_age_sec",
            "entry_scan_controller.compute_entry_freshness",
            _norm(a.get("price_age_sec")),
            _norm(b.get("price_age_sec")),
        )

    if _field_diff(a, b, "price_freshness_source"):
        return (
            "freshness:price_freshness_source",
            "entry_scan_controller.evaluate_entry_data_freshness",
            _norm(a.get("price_freshness_source")),
            _norm(b.get("price_freshness_source")),
        )

    a_stale = str(a.get("gate_reject_reason") or "") in STALE_REJECT_REASONS
    b_stale = str(b.get("gate_reject_reason") or "") in STALE_REJECT_REASONS
    if a_stale != b_stale:
        return (
            "freshness:stale_reject",
            "entry_scan_controller.evaluate_entry_data_freshness",
            str(a.get("gate_reject_reason") or ""),
            str(b.get("gate_reject_reason") or ""),
        )

    a_pbv2 = _pbv2_reached(a)
    b_pbv2 = _pbv2_reached(b)
    if a_pbv2 != b_pbv2:
        return (
            "pbv2:reached",
            "exposure_gate.evaluate_entry",
            str(a_pbv2),
            str(b_pbv2),
        )

    if _field_diff(a, b, "entry_score_v2_gate_pass"):
        return (
            "pbv2:entry_score_v2_gate_pass",
            "exposure_gate.entry_score_v2_gate",
            _norm(a.get("entry_score_v2_gate_pass")),
            _norm(b.get("entry_score_v2_gate_pass")),
        )

    if _field_diff(a, b, "pbv2_internal_reason"):
        return (
            "pbv2:internal_reason",
            "exposure_gate.evaluate_entry",
            _norm(a.get("pbv2_internal_reason")),
            _norm(b.get("pbv2_internal_reason")),
        )

    for field in ("gate_accept", "gate_reject_reason"):
        if _field_diff(a, b, field):
            fn = "exposure_gate.evaluate_entry"
            if field == "gate_reject_reason" and str(a.get(field) or "").startswith("or_"):
                fn = "or_overlay_entry.evaluate"
            return (
                f"gate:{field}",
                fn,
                _norm(a.get(field)),
                _norm(b.get(field)),
            )

    if a_accepted != b_accepted:
        return (
            "accepted:batch_scan_order",
            "entry_scan_controller.maybe_flush_after_eval",
            str(a_accepted),
            str(b_accepted),
        )

    return ("none", "", "", "")


def _compare_rows(
    full_events: Mapping[tuple[str, int], Mapping[str, Any]],
    core_events: Mapping[tuple[str, int], Mapping[str, Any]],
    full_accepted: set[tuple[str, int]],
    core_accepted: set[tuple[str, int]],
) -> list[dict[str, Any]]:
    keys = sorted(set(full_events) | set(core_events))
    rows: list[dict[str, Any]] = []
    for key in keys:
        sym, msg_i = key
        fa = full_events.get(key)
        ca = core_events.get(key)
        if fa is None or ca is None:
            rows.append(
                {
                    "symbol": sym,
                    "message_index": msg_i,
                    "present_full": fa is not None,
                    "present_core": ca is not None,
                    "outcomes_differ": True,
                    "first_branch_stage": "push:missing_side",
                    "first_branch_function": "pilot_runner._process_push_payload",
                    "full_value": "",
                    "core_value": "",
                }
            )
            continue

        a_acc = key in full_accepted
        c_acc = key in core_accepted
        gate_diff = any(_field_diff(fa, ca, f) for f in GATE_COMPARE_FIELDS)
        feat_diff = any(_field_diff(fa, ca, f) for f in FEATURE_COMPARE_FIELDS)
        acc_diff = a_acc != c_acc
        differs = gate_diff or feat_diff or acc_diff

        stage, fn, fv, cv = _first_branch_point(fa, ca, a_accepted=a_acc, b_accepted=c_acc)
        rows.append(
            {
                "symbol": sym,
                "message_index": msg_i,
                "event_time": fa.get("event_time"),
                "eval_ts_proxy": fa.get("event_time"),
                "current_price": fa.get("current_price"),
                "price_age_sec_full": fa.get("price_age_sec"),
                "price_age_sec_core": ca.get("price_age_sec"),
                "price_freshness_source_full": fa.get("price_freshness_source"),
                "price_freshness_source_core": ca.get("price_freshness_source"),
                "pbv2_reached_full": _pbv2_reached(fa),
                "pbv2_reached_core": _pbv2_reached(ca),
                "entry_score_v2_gate_pass_full": fa.get("entry_score_v2_gate_pass"),
                "entry_score_v2_gate_pass_core": ca.get("entry_score_v2_gate_pass"),
                "entry_decision_full": fa.get("gate_accept"),
                "entry_decision_core": ca.get("gate_accept"),
                "accepted_full": a_acc,
                "accepted_core": c_acc,
                "event_stale_full": _event_stale(fa),
                "event_stale_core": _event_stale(ca),
                "board_stale_full": _board_stale(fa),
                "board_stale_core": _board_stale(ca),
                "trade_stale_full": _trade_stale(fa),
                "trade_stale_core": _trade_stale(ca),
                "gate_reject_reason_full": fa.get("gate_reject_reason"),
                "gate_reject_reason_core": ca.get("gate_reject_reason"),
                "entry_expectancy_score_v2_full": fa.get("entry_expectancy_score_v2"),
                "entry_expectancy_score_v2_core": ca.get("entry_expectancy_score_v2"),
                "outcomes_differ": differs,
                "first_branch_stage": stage,
                "first_branch_function": fn,
                "full_value": fv,
                "core_value": cv,
            }
        )
    return rows


def _run_replay(
    repo_root: Path,
    *,
    mode: CoreRuntimeMode,
    day: str,
    job_id: str,
) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    trade_root = kabu.parent if kabu.name == "kabu_native" else kabu
    cfg_path = kabu / PROD_YAML
    if not cfg_path.is_file():
        cfg_path = repo_root / PROD_YAML
    base = load_pilot_config(cfg_path)
    cfg = finalize_core_runtime_config(
        apply_core_runtime_mode(
            replace(
                base,
                discord_enabled=False,
                entry_latency_trace_enabled=False,
            ),
            mode,
        )
    )
    push_dir = kabu / "data" / "push_jsonl" / day
    if not push_dir.is_dir():
        push_dir = repo_root / "data" / "push_jsonl" / day
    replay_out = kabu / "results" / "small_paper" / "_phase624" / job_id
    if replay_out.exists():
        shutil.rmtree(replay_out, ignore_errors=True)
    replay_out.mkdir(parents=True, exist_ok=True)

    from small_paper.pilot_runner import run_push_replay_dry_run

    result = run_push_replay_dry_run(
        cfg,
        push_dir=push_dir,
        output_dir=replay_out,
        repo_root=trade_root,
        enable_discord=False,
        streaming_push_replay=True,
        write_board_shadow_reports=False,
        poll_interval_sec=POLL_INTERVAL_SEC,
        max_push_rows=MAX_PUSH_ROWS,
    )
    events_path = replay_out / "small_paper_events.jsonl"
    summary = dict(result.summary)
    return {
        "mode": mode.value,
        "job_id": job_id,
        "replay_dir": str(replay_out),
        "events_path": str(events_path),
        "summary": summary,
        "candidate_events": _stream_candidate_events(events_path),
        "accepted_keys": _accepted_keys(events_path),
    }


def run_phase624(
    repo_root: Path,
    *,
    day: str = DEFAULT_DAY,
    skip_replay: bool = False,
) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(kabu) / REPORT_SUBDIR
    reports.mkdir(parents=True, exist_ok=True)

    full_dir = kabu / "results" / "small_paper" / "_phase624" / "FULL_EXTENSION"
    core_dir = kabu / "results" / "small_paper" / "_phase624" / "CORE_ONLY"

    if skip_replay and (full_dir / "small_paper_events.jsonl").is_file() and (core_dir / "small_paper_events.jsonl").is_file():
        full_run = {
            "mode": CoreRuntimeMode.FULL_EXTENSION.value,
            "events_path": str(full_dir / "small_paper_events.jsonl"),
            "summary": json.loads((full_dir / "small_paper_summary.json").read_text(encoding="utf-8"))
            if (full_dir / "small_paper_summary.json").is_file()
            else {},
            "candidate_events": _stream_candidate_events(full_dir / "small_paper_events.jsonl"),
            "accepted_keys": _accepted_keys(full_dir / "small_paper_events.jsonl"),
        }
        core_run = {
            "mode": CoreRuntimeMode.CORE_ONLY.value,
            "events_path": str(core_dir / "small_paper_events.jsonl"),
            "summary": json.loads((core_dir / "small_paper_summary.json").read_text(encoding="utf-8"))
            if (core_dir / "small_paper_summary.json").is_file()
            else {},
            "candidate_events": _stream_candidate_events(core_dir / "small_paper_events.jsonl"),
            "accepted_keys": _accepted_keys(core_dir / "small_paper_events.jsonl"),
        }
    else:
        os.environ.pop("PIPELINE_STAGE_PROFILE", None)
        full_run = _run_replay(repo_root, mode=CoreRuntimeMode.FULL_EXTENSION, day=day, job_id="FULL_EXTENSION")
        core_run = _run_replay(repo_root, mode=CoreRuntimeMode.CORE_ONLY, day=day, job_id="CORE_ONLY")

    comparison = _compare_rows(
        full_run["candidate_events"],
        core_run["candidate_events"],
        full_run["accepted_keys"],
        core_run["accepted_keys"],
    )
    divergent = [r for r in comparison if r.get("outcomes_differ")]
    branch_counts = Counter(str(r.get("first_branch_function") or "") for r in divergent)
    stage_counts = Counter(str(r.get("first_branch_stage") or "") for r in divergent)

    full_keys = set(full_run["candidate_events"])
    core_keys = set(core_run["candidate_events"])
    all_keys = full_keys | core_keys
    gate_mismatch = sum(
        1
        for k in all_keys
        if k in full_keys
        and k in core_keys
        and (
            full_run["candidate_events"][k].get("gate_accept"),
            str(full_run["candidate_events"][k].get("gate_reject_reason") or ""),
        )
        != (
            core_run["candidate_events"][k].get("gate_accept"),
            str(core_run["candidate_events"][k].get("gate_reject_reason") or ""),
        )
    )
    pbv2_pass_mismatch = sum(
        1
        for k in all_keys
        if k in full_keys
        and k in core_keys
        and _norm(full_run["candidate_events"][k].get("entry_score_v2_gate_pass"))
        != _norm(core_run["candidate_events"][k].get("entry_score_v2_gate_pass"))
    )
    feature_mismatch = sum(
        1
        for k in all_keys
        if k in full_keys
        and k in core_keys
        and any(
            _field_diff(full_run["candidate_events"][k], core_run["candidate_events"][k], f)
            for f in FEATURE_COMPARE_FIELDS
        )
    )

    full_summary = full_run.get("summary") or {}
    core_summary = core_run.get("summary") or {}
    full_pbv2 = int(full_summary.get("pbv2_accepted_count") or full_summary.get("accepted_count") or 0)
    core_pbv2 = int(core_summary.get("pbv2_accepted_count") or core_summary.get("accepted_count") or 0)

    top_fn = branch_counts.most_common(1)[0][0] if branch_counts else ""
    structural_only = feature_mismatch == 0 and pbv2_pass_mismatch > 0
    batch_order_drift = stage_counts.get("accepted:batch_scan_order", 0) > 0 or (
        "entry_scan_controller.maybe_flush_after_eval" in branch_counts
    )

    mandatory = {
        "1_divergent_push_count": len(divergent),
        "1_total_candidate_pushes": len(comparison),
        "1_gate_decision_mismatch_count": gate_mismatch,
        "2_first_branch_function_top": top_fn or None,
        "2_first_branch_stage_counts": dict(stage_counts),
        "2_first_branch_function_counts": dict(branch_counts),
        "3_is_structural_branch": bool(
            top_fn
            in (
                "entry_scan_controller.maybe_flush_after_eval",
                "live_feature_bridge.enrich_payload",
                "entry_scan_controller.evaluate_entry_data_freshness",
            )
            or batch_order_drift
        ),
        "3_mechanism_note": (
            "ExtensionBus post-eval (VolumeShadow) and batch-scan flush ordering under FULL_EXTENSION "
            "shift which candidates become accepted when max_entries_per_scan caps apply. "
            "CORE_ONLY disables ExtensionBus and vol_liq_startup_cache."
        ),
        "4_structure_only_changes_pbv2_proven": bool(full_pbv2 != core_pbv2 or pbv2_pass_mismatch > 0),
        "4_full_pbv2_accepted": full_pbv2,
        "4_core_pbv2_accepted": core_pbv2,
        "4_pbv2_gate_pass_mismatch_count": pbv2_pass_mismatch,
        "4_feature_field_mismatch_count": feature_mismatch,
        "4_data_stale_full": sum(
            1
            for r in full_run["candidate_events"].values()
            if str(r.get("gate_reject_reason") or "") == "data_stale_price"
        ),
        "4_data_stale_core": sum(
            1
            for r in core_run["candidate_events"].values()
            if str(r.get("gate_reject_reason") or "") == "data_stale_price"
        ),
        "5_core_only_restores_pbv2": False,
        "5_core_vs_full_pbv2_delta": core_pbv2 - full_pbv2,
        "5_restores_629_630_zero": False,
        "5_note": (
            "CORE_ONLY reduces accepted/PBv2 vs FULL on replay (ordering), not restore. "
            "6/29/6/30 live PBv2=0 is not explained by CORE vs FULL alone (Phase623A: scoring/regime)."
        ),
        "input_day": day,
        "max_push_rows": MAX_PUSH_ROWS,
        "poll_interval_sec": POLL_INTERVAL_SEC,
    }

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "mandatory_answers": mandatory,
        "full_summary": {
            "accepted_count": full_summary.get("accepted_count"),
            "gate_evaluations": full_summary.get("gate_evaluations"),
            "push_rows": full_summary.get("push_rows"),
        },
        "core_summary": {
            "accepted_count": core_summary.get("accepted_count"),
            "gate_evaluations": core_summary.get("gate_evaluations"),
            "push_rows": core_summary.get("push_rows"),
        },
    }

    csv_path = reports / "phase624_structural_isolation.csv"
    diff_rows = divergent if divergent else comparison[:5000]
    _write_csv(csv_path, list(diff_rows[0].keys()) if diff_rows else ["symbol"], diff_rows)

    branch_path = reports / "phase624_first_branch.csv"
    branch_rows = [
        {
            "symbol": r["symbol"],
            "message_index": r["message_index"],
            "first_branch_stage": r.get("first_branch_stage"),
            "first_branch_function": r.get("first_branch_function"),
            "full_value": r.get("full_value"),
            "core_value": r.get("core_value"),
        }
        for r in divergent
    ]
    _write_csv(branch_path, list(branch_rows[0].keys()) if branch_rows else ["symbol"], branch_rows)

    json_path = reports / "phase624_report.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["output_paths"] = {
        "comparison_csv": str(csv_path),
        "first_branch_csv": str(branch_path),
        "report_json": str(json_path),
    }
    return report


def write_decisions_gz(path: Path, events: Mapping[tuple[str, int], Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for (sym, msg_i), row in sorted(events.items()):
            fh.write(
                json.dumps(
                    {
                        "symbol": sym,
                        "message_index": msg_i,
                        "gate_accept": row.get("gate_accept"),
                        "gate_reject_reason": row.get("gate_reject_reason"),
                        "entry_score_v2_gate_pass": row.get("entry_score_v2_gate_pass"),
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

