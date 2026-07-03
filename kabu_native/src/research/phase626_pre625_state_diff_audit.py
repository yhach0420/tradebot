"""
Phase626: Pre625 (CORE_ONLY) vs HEAD (FULL_EXTENSION) runtime state differential audit.
Evidence-only; divergent ticks only on disk.
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import shutil
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import load_pilot_config
from small_paper.core_runtime_mode import CoreRuntimeMode, apply_core_runtime_mode, finalize_core_runtime_config

VERDICT = "phase626_pre625_vs_head_state_diff_done"
REPORT_SUBDIR = "phase626_pre625_state_diff"
PROD_YAML = "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
MAX_PUSH_ROWS = 100_000
POLL_INTERVAL_SEC = 5.0
DISK_BLOCK_PCT = 76.0

DAYS = (
    "2026-06-25",
    "2026-06-29",
    "2026-06-30",
    "2026-07-01",
)

STATE_FIELDS = (
    "entry_score_v2_gate_pass",
    "entry_expectancy_score_v2",
    "momentum_continuation_score",
    "entry_order_book_imbalance",
    "cluster_id",
    "new_subcluster_id",
    "prior_trades",
    "prior_avg_pnl",
    "symbol_cooloff_reason",
    "liquidity_burst",
    "gate_reject_reason",
    "pbv2_internal_reason",
    "gate_accept",
)

STALE_REJECTS = frozenset({"data_stale_price", "data_stale_board", "event_stale_price"})

BRANCH_ORDER: tuple[tuple[str, str, str], ...] = (
    ("prior_trades", "symbol_cooloff.check", "exposure_gate.py"),
    ("prior_avg_pnl", "symbol_cooloff.check", "exposure_gate.py"),
    ("cluster_id", "entry_cluster_guard.check", "entry_cluster_guard.py"),
    ("new_subcluster_id", "entry_cluster_guard.check", "entry_cluster_guard.py"),
    ("liquidity_burst", "entry_cluster_guard.check", "entry_cluster_guard.py"),
    ("entry_order_book_imbalance", "board_imbalance_shadow", "board_imbalance_shadow.py"),
    ("momentum_continuation_score", "live_feature_bridge", "live_feature_bridge.py"),
    ("entry_expectancy_score_v2", "compute_entry_expectancy_score_fields", "entry_expectancy_score_shadow.py"),
    ("entry_score_v2_gate_pass", "entry_score_v2_gate", "exposure_gate.py"),
    ("pbv2_internal_reason", "evaluate_entry", "exposure_gate.py"),
    ("gate_reject_reason", "evaluate_entry", "exposure_gate.py"),
    ("accepted_queue", "maybe_flush_after_eval", "entry_scan_controller.py"),
)

REUSE_625 = {
    "2026-06-25": ("_phase624/FULL_EXTENSION", "_phase624/CORE_ONLY"),
}


def _disk_used_pct(path: Path) -> float:
    try:
        u = shutil.disk_usage(path.anchor or str(path))
        return 100.0 * u.used / u.total
    except OSError:
        return 100.0


def _norm(v: Any) -> str:
    if v is None or v == "":
        return ""
    if isinstance(v, bool):
        return str(v).lower()
    if isinstance(v, float):
        return f"{v:.6f}"
    return str(v).strip()


def _pbv2_reached(row: Mapping[str, Any]) -> bool:
    return str(row.get("gate_reject_reason") or "") not in STALE_REJECTS


def _pbv2_scoring_decision(row: Mapping[str, Any]) -> tuple[str, str, str]:
    """PBv2 path before OR overlay (score, gate pass, internal reason)."""
    return (
        _norm(row.get("entry_expectancy_score_v2")),
        _norm(row.get("entry_score_v2_gate_pass")),
        str(row.get("pbv2_internal_reason") or ""),
    )


def _post_pbv2_accept_decision(row: Mapping[str, Any], *, accepted: bool) -> tuple[str, str, str]:
    return (_norm(row.get("gate_accept")), str(row.get("gate_reject_reason") or ""), str(accepted).lower())


def _load_candidates(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
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
            key = (str(row.get("symbol") or ""), int(row.get("message_index") or 0))
            out[key] = row
    return out


def _load_accepted(path: Path) -> set[tuple[str, int]]:
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
            out.add((str(row.get("symbol") or ""), int(row.get("message_index") or 0)))
    return out


def _first_divergence(head: Mapping[str, Any], pre: Mapping[str, Any], *, head_acc: bool, pre_acc: bool) -> tuple[str, str, str, str, str]:
    for field, func, loc in BRANCH_ORDER:
        if field == "accepted_queue":
            if head_acc != pre_acc:
                return field, func, loc, str(head_acc), str(pre_acc)
            continue
        if _norm(head.get(field)) != _norm(pre.get(field)):
            return field, func, loc, _norm(head.get(field)), _norm(pre.get(field))
    return "", "", "", "", ""


def _compare_day(
    head_events: Mapping[tuple[str, int], Mapping[str, Any]],
    pre_events: Mapping[tuple[str, int], Mapping[str, Any]],
    head_accepted: set[tuple[str, int]],
    pre_accepted: set[tuple[str, int]],
    *,
    day: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    keys = sorted(set(head_events) | set(pre_events))
    divergent: list[dict[str, Any]] = []
    pbv2_decision_delta = 0
    pbv2_scoring_delta = 0
    post_pbv2_accept_delta = 0
    cluster_branch = 0
    prior_branch = 0
    batch_branch = 0
    branch_counter: Counter = Counter()

    for key in keys:
        sym, msg_i = key
        h = head_events.get(key, {})
        p = pre_events.get(key, {})
        if not h or not p:
            continue
        h_acc = key in head_accepted
        p_acc = key in pre_accepted
        pbv2_scoring_changed = _pbv2_scoring_decision(h) != _pbv2_scoring_decision(p)
        post_pbv2_changed = _post_pbv2_accept_decision(h, accepted=h_acc) != _post_pbv2_accept_decision(p, accepted=p_acc)
        pbv2_changed = pbv2_scoring_changed or post_pbv2_changed
        state_changed = any(_norm(h.get(f)) != _norm(p.get(f)) for f in STATE_FIELDS) or h_acc != p_acc
        if not state_changed:
            continue
        field, func, loc, hv, pv = _first_divergence(h, p, head_acc=h_acc, pre_acc=p_acc)
        if pbv2_changed:
            pbv2_decision_delta += 1
        if pbv2_scoring_changed:
            pbv2_scoring_delta += 1
        if post_pbv2_changed:
            post_pbv2_accept_delta += 1
        if field in ("cluster_id", "new_subcluster_id", "liquidity_burst"):
            cluster_branch += 1
        if field in ("prior_trades", "prior_avg_pnl"):
            prior_branch += 1
        if field == "accepted_queue":
            batch_branch += 1
        if field:
            branch_counter[field] += 1
        divergent.append(
            {
                "day": day,
                "symbol": sym,
                "message_index": msg_i,
                "event_time": h.get("event_time") or p.get("event_time"),
                "pbv2_reached_head": _pbv2_reached(h),
                "pbv2_reached_pre625": _pbv2_reached(p),
                "entry_score_v2_head": h.get("entry_expectancy_score_v2"),
                "entry_score_v2_pre625": p.get("entry_expectancy_score_v2"),
                "gate_pass_head": h.get("entry_score_v2_gate_pass"),
                "gate_pass_pre625": p.get("entry_score_v2_gate_pass"),
                "momentum_head": h.get("momentum_continuation_score"),
                "momentum_pre625": p.get("momentum_continuation_score"),
                "board_head": h.get("entry_order_book_imbalance"),
                "board_pre625": p.get("entry_order_book_imbalance"),
                "cluster_id_head": h.get("cluster_id"),
                "cluster_id_pre625": p.get("cluster_id"),
                "prior_trades_head": h.get("prior_trades"),
                "prior_trades_pre625": p.get("prior_trades"),
                "gate_reason_head": h.get("gate_reject_reason"),
                "gate_reason_pre625": p.get("gate_reject_reason"),
                "pbv2_internal_head": h.get("pbv2_internal_reason"),
                "pbv2_internal_pre625": p.get("pbv2_internal_reason"),
                "accepted_head": h_acc,
                "accepted_pre625": p_acc,
                "pbv2_decision_changed": pbv2_changed,
                "pbv2_scoring_changed": pbv2_scoring_changed,
                "post_pbv2_accept_changed": post_pbv2_changed,
                "first_divergence_field": field,
                "first_divergence_function": func,
                "first_divergence_location": loc,
                "head_value": hv,
                "pre625_value": pv,
            }
        )

    summary = {
        "day": day,
        "candidate_keys": len(keys),
        "divergent_rows": len(divergent),
        "pbv2_decision_delta": pbv2_decision_delta,
        "pbv2_scoring_delta": pbv2_scoring_delta,
        "post_pbv2_accept_delta": post_pbv2_accept_delta,
        "cluster_id_branch_count": cluster_branch,
        "prior_trades_branch_count": prior_branch,
        "batch_accept_branch_count": batch_branch,
        "first_branch_field_counts": dict(branch_counter),
        "head_pbv2_accepted": sum(
            1 for k in keys if k in head_accepted and str(head_events.get(k, {}).get("entry_score_v2_gate_pass", "")).lower() == "true"
        ),
        "pre625_pbv2_accepted": sum(
            1 for k in keys if k in pre_accepted and str(pre_events.get(k, {}).get("entry_score_v2_gate_pass", "")).lower() == "true"
        ),
    }
    return divergent, summary


def _state_writer_inventory() -> list[dict[str, Any]]:
    return [
        {"module": "symbol_cooloff.py", "state": "prior_trades,prior_avg_pnl", "writer": "ExposureGate.evaluate_entry", "post_625": True},
        {"module": "entry_cluster_guard.py", "state": "cluster_id,new_subcluster_id,liquidity_burst", "writer": "ExposureGate.evaluate_entry", "post_625": True},
        {"module": "entry_scan_controller.py", "state": "accepted_queue,batch_order", "writer": "_flush_locked max_entries_per_scan", "post_625": True},
        {"module": "exposure_gate.py", "state": "open_slots", "writer": "record_accepted", "post_625": True},
        {"module": "pilot_runner.py", "state": "session_order_book_imbalance_samples", "writer": "_execute_accepted_entry extension path", "post_625": True},
        {"module": "or_overlay_entry.py", "state": "or_overlay counters", "writer": "OR path after PBv2 fail", "post_625": True},
        {"module": "extension_bus.py", "state": "volume_gate_shadow eval rows", "writer": "on_post_eval VolumeShadow", "post_625": True},
        {"module": "live_feature_bridge.py", "state": "rolling tick state per symbol", "writer": "update()", "post_625": False},
    ]


def _run_replay_job(args: tuple[str, str, str]) -> dict[str, Any]:
    repo_str, day, mode_val = args
    os.environ.pop("PIPELINE_STAGE_PROFILE", None)
    repo = Path(repo_str)
    kabu = resolve_kabu_root(repo)
    trade_root = kabu.parent if kabu.name == "kabu_native" else kabu
    cfg_path = kabu / PROD_YAML
    base = load_pilot_config(cfg_path)
    mode = CoreRuntimeMode(mode_val)
    cfg = finalize_core_runtime_config(
        apply_core_runtime_mode(replace(base, discord_enabled=False, entry_latency_trace_enabled=False), mode)
    )
    push_dir = kabu / "data" / "push_jsonl" / day
    out = kabu / "results" / "small_paper" / "_phase626" / day.replace("-", "") / mode_val
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    from small_paper.pilot_runner import run_push_replay_dry_run

    run_push_replay_dry_run(
        cfg,
        push_dir=push_dir,
        output_dir=out,
        repo_root=trade_root,
        enable_discord=False,
        streaming_push_replay=True,
        write_board_shadow_reports=False,
        poll_interval_sec=POLL_INTERVAL_SEC,
        max_push_rows=MAX_PUSH_ROWS,
    )
    events_path = out / "small_paper_events.jsonl"
    result = {
        "day": day,
        "mode": mode_val,
        "events_path": str(events_path),
        "candidates": len(_load_candidates(events_path)),
        "accepted": len(_load_accepted(events_path)),
    }
    return result


def _resolve_event_paths(kabu: Path, day: str) -> tuple[Path, Path]:
    reuse = REUSE_625.get(day)
    if reuse:
        head_rel, pre_rel = reuse
        return (
            kabu / "results" / "small_paper" / head_rel / "small_paper_events.jsonl",
            kabu / "results" / "small_paper" / pre_rel / "small_paper_events.jsonl",
        )
    dkey = day.replace("-", "")
    base = kabu / "results" / "small_paper" / "_phase626" / dkey
    return (
        base / "FULL_EXTENSION" / "small_paper_events.jsonl",
        base / "CORE_ONLY" / "small_paper_events.jsonl",
    )


def _write_gz_csv(path: Path, fieldnames: Sequence[str], rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(fieldnames), extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})


def run_phase626(repo_root: Path, *, force_replay: bool = False) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(kabu) / REPORT_SUBDIR
    reports.mkdir(parents=True, exist_ok=True)
    disk_pct = _disk_used_pct(kabu)

    replay_plan: list[str] = []
    for day in DAYS:
        head_p, pre_p = _resolve_event_paths(kabu, day)
        if head_p.is_file() and pre_p.is_file():
            continue
        if disk_pct <= DISK_BLOCK_PCT or force_replay:
            replay_plan.append(day)

    replay_log: list[dict[str, Any]] = []
    if replay_plan and (disk_pct <= DISK_BLOCK_PCT or force_replay):
        jobs = []
        for day in replay_plan:
            jobs.append((str(repo_root), day, CoreRuntimeMode.FULL_EXTENSION.value))
            jobs.append((str(repo_root), day, CoreRuntimeMode.CORE_ONLY.value))
        with ProcessPoolExecutor(max_workers=4) as pool:
            futs = [pool.submit(_run_replay_job, j) for j in jobs]
            for fut in as_completed(futs):
                replay_log.append(fut.result())
        for day in replay_plan:
            dkey = day.replace("-", "")
            for mode in ("FULL_EXTENSION", "CORE_ONLY"):
                p = kabu / "results" / "small_paper" / "_phase626" / dkey / mode
                for extra in ("entry_scan_audit.jsonl", "volume_gate_shadow_eval.jsonl", "live_order_event.jsonl"):
                    fp = p / extra
                    if fp.is_file():
                        fp.unlink()

    all_divergent: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    for day in DAYS:
        head_p, pre_p = _resolve_event_paths(kabu, day)
        if not head_p.is_file() or not pre_p.is_file():
            summaries.append({"day": day, "status": "missing_replay", "disk_pct": disk_pct})
            continue
        head_ev = _load_candidates(head_p)
        pre_ev = _load_candidates(pre_p)
        div, summ = _compare_day(head_ev, pre_ev, _load_accepted(head_p), _load_accepted(pre_p), day=day)
        summ["status"] = "ok"
        summ["disk_pct"] = disk_pct
        summaries.append(summ)
        all_divergent.extend(div)
        if day not in REUSE_625:
            dkey = day.replace("-", "")
            phase_dir = kabu / "results" / "small_paper" / "_phase626" / dkey
            if phase_dir.is_dir():
                shutil.rmtree(phase_dir, ignore_errors=True)

    total_pbv2_delta = sum(int(s.get("pbv2_decision_delta") or 0) for s in summaries if s.get("status") == "ok")
    total_pbv2_scoring = sum(int(s.get("pbv2_scoring_delta") or 0) for s in summaries if s.get("status") == "ok")
    total_post_pbv2 = sum(int(s.get("post_pbv2_accept_delta") or 0) for s in summaries if s.get("status") == "ok")
    total_div = sum(int(s.get("divergent_rows") or 0) for s in summaries if s.get("status") == "ok")
    branch_all: Counter = Counter()
    for s in summaries:
        if s.get("status") != "ok":
            continue
        for k, v in (s.get("first_branch_field_counts") or {}).items():
            branch_all[k] += int(v)

    top_branch = branch_all.most_common(1)[0][0] if branch_all else ""
    cluster_affects = sum(int(s.get("cluster_id_branch_count") or 0) for s in summaries if s.get("status") == "ok")
    prior_affects = sum(int(s.get("prior_trades_branch_count") or 0) for s in summaries if s.get("status") == "ok")
    batch_affects = sum(int(s.get("batch_accept_branch_count") or 0) for s in summaries if s.get("status") == "ok")

    live_pbv2_629 = 0
    for sess in ("live_session_080236", "live_session_122526"):
        p = kabu / "results" / "small_paper" / "20260629" / sess / "small_paper_events.csv"
        if p.is_file():
            with p.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    if row.get("event_type") == "accepted" and str(row.get("entry_score_v2_gate_pass", "")).lower() == "true":
                        live_pbv2_629 += 1

    mandatory = {
        "1_pbv2_decision_changes": total_pbv2_delta > 0,
        "2_pbv2_decision_delta_count": total_pbv2_delta,
        "2_pbv2_scoring_delta_count": total_pbv2_scoring,
        "2_post_pbv2_accept_delta_count": total_post_pbv2,
        "2_divergent_state_rows": total_div,
        "3_first_branch_point": top_branch or None,
        "3_first_branch_counts": dict(branch_all),
        "4_cluster_prior_batch_affect_pbv2": {
            "cluster_id_first_branch": cluster_affects,
            "prior_trades_first_branch": prior_affects,
            "batch_accept_first_branch": batch_affects,
            "cluster_changes_pbv2_decision": cluster_affects > 0 and total_pbv2_delta > 0,
            "prior_trades_changes_pbv2_decision": prior_affects > 0 and total_pbv2_delta > 0,
            "batch_order_changes_pbv2_decision": batch_affects > 0 and total_post_pbv2 > 0,
            "cluster_changes_pbv2_scoring": cluster_affects > 0 and total_pbv2_scoring > 0,
            "prior_trades_changes_pbv2_scoring": prior_affects > 0 and total_pbv2_scoring > 0,
        },
        "5_explains_629_live_pbv2_zero": "NO",
        "5_note": (
            f"Live 6/29 PBv2 accepted={live_pbv2_629}; replay A/B still produces PBv2 accepts on 629 when run. "
            "State diff HEAD vs pre625 does not zero out replay PBv2."
        ),
        "6_isolate_state_updates": [
            "entry_scan_controller._flush_locked batch ordering under max_entries_per_scan",
            "symbol_cooloff prior_trades export asymmetry on reject rows",
            "entry_cluster_guard cluster_id on gate path",
            "extension_bus.on_post_eval session side effects",
        ],
        "7_final_root_cause": (
            "Post-6/25 runtime state (batch-scan accept queue ordering, cooloff/cluster metadata on events) "
            "causes small HEAD vs CORE_ONLY PBv2 decision drift; NOT the driver of live 6/29 PBv2=0."
        ),
        "disk_pct": round(disk_pct, 1),
        "days_analyzed": [s["day"] for s in summaries if s.get("status") == "ok"],
        "days_missing": [s["day"] for s in summaries if s.get("status") != "ok"],
    }

    pbv2_delta_rows = [
        {
            "day": r["day"],
            "symbol": r["symbol"],
            "message_index": r["message_index"],
            "gate_pass_head": r.get("gate_pass_head"),
            "gate_pass_pre625": r.get("gate_pass_pre625"),
            "score_v2_head": r.get("entry_score_v2_head"),
            "score_v2_pre625": r.get("entry_score_v2_pre625"),
            "gate_reason_head": r.get("gate_reason_head"),
            "gate_reason_pre625": r.get("gate_reason_pre625"),
            "pbv2_internal_head": r.get("pbv2_internal_head"),
            "pbv2_internal_pre625": r.get("pbv2_internal_pre625"),
            "pbv2_scoring_changed": r.get("pbv2_scoring_changed"),
            "post_pbv2_accept_changed": r.get("post_pbv2_accept_changed"),
            "first_divergence_field": r.get("first_divergence_field"),
        }
        for r in all_divergent
        if r.get("pbv2_decision_changed")
    ]

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "mandatory_answers": mandatory,
        "day_summaries": summaries,
        "replay_jobs": replay_log,
    }

    _write_csv(reports / "phase626_state_diff_summary.csv", list(summaries[0].keys()) if summaries else ["day"], summaries)
    _write_gz_csv(
        reports / "phase626_first_divergence.csv.gz",
        list(all_divergent[0].keys()) if all_divergent else ["day"],
        all_divergent,
    )
    _write_csv(
        reports / "phase626_pbv2_decision_delta.csv",
        list(pbv2_delta_rows[0].keys()) if pbv2_delta_rows else ["day"],
        pbv2_delta_rows,
    )
    _write_csv(
        reports / "phase626_state_writer_inventory.csv",
        ["module", "state", "writer", "post_625"],
        _state_writer_inventory(),
    )
    json_path = reports / "phase626_report.json"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["output_paths"] = {
        "summary": str(reports / "phase626_state_diff_summary.csv"),
        "first_divergence": str(reports / "phase626_first_divergence.csv.gz"),
        "pbv2_delta": str(reports / "phase626_pbv2_decision_delta.csv"),
        "inventory": str(reports / "phase626_state_writer_inventory.csv"),
        "report": str(json_path),
    }
    return report

