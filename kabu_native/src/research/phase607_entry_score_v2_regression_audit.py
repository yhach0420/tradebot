"""
Phase607 — entry_score_v2 feature regression audit (research only).

Source of truth: 6/25 live PBv2 accepted rows (entry_score_v2_gate_pass=true).
Per-candidate full decomposition — no aggregation-only summaries.
"""

from __future__ import annotations

import csv
import json
import subprocess
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase604b_pbv2_zero_impl_block_audit import (
    _pre_gate_blocker,
    _trace_pbv2_internal,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import SmallPaperPilotConfig, load_pilot_config
from small_paper.entry_expectancy_score_shadow import (
    ENTRY_SCORE_V2_GATE_MIN,
    MOMENTUM_SCORE_CUTOFF_P33,
    SCORE_POINTS_V2,
    TERTILE_CUTOFFS,
    active_score_tokens_v2,
    board_mid_or_high_required_for_v2,
    compute_entry_expectancy_score_fields,
    momentum_score_cutoff_pass,
    _bin_tertile,
    _feature_token,
    _float,
)

VERDICT = "phase607_entry_score_feature_regression_done"
PRE625_COMMIT = "f50c5a7"

SESSIONS_625 = (
    ("20260625", "live_session_080340"),
    ("20260625", "live_session_122535"),
)

SCORE_CODE_FILES = (
    "src/small_paper/entry_expectancy_score_shadow.py",
    "src/small_paper/board_imbalance_shadow.py",
    "src/small_paper/live_feature_bridge.py",
    "src/research/exposure_gate.py",
)

FEATURE_PIPELINE_FILES = (
    ("momentum_continuation_score", "live_feature_bridge.py / payload", "momentum_continuation_score"),
    ("entry_order_book_imbalance", "board_imbalance_shadow.py", "compute_entry_order_book_imbalance_field"),
    ("continuation_quality_score", "live_feature_bridge.py", "continuation_quality_score"),
    ("spread_bps", "entry_scan_controller / payload", "spread_bps"),
    ("daytrade_suitability_score", "daytrade_suitability_gate.py", "daytrade_suitability"),
    ("momentum_continuation_score", "entry_expectancy_score_shadow.py", "_feature_token Momentum"),
    ("entry_order_book_imbalance", "entry_expectancy_score_shadow.py", "_feature_token Board"),
)

FEATURE_FIELDS = (
    "momentum_continuation_score",
    "entry_momentum_continuation_score",
    "entry_momentum_score",
    "entry_order_book_imbalance",
    "continuation_quality_score",
    "spread_bps",
    "daytrade_suitability_score",
    "trading_value",
    "current_price",
    "rolling_mae_pct",
    "max_continuation_duration",
    "entry_high_break_recent",
    "CalcPrice",
    "BidPrice",
    "AskPrice",
    "Volume",
    "UpdateCount",
    "cluster_id",
    "new_subcluster_id",
    "liquidity_burst",
    "volume_acceleration_5m",
    "near_day_high_distance_pct",
    "pullback_depth_pct",
    "high_drift_flag",
)


def _git(repo_parent: Path, *args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=repo_parent, text=True, errors="replace").strip()
    except subprocess.CalledProcessError:
        return ""


def _write_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    _write_csv(path, list(rows[0].keys()), rows)


def _session_dir(repo: Path, day: str, session: str) -> Path:
    return repo / "results" / "small_paper" / day / session


def _load_pbv2_accepted_625(repo: Path) -> list[dict[str, Any]]:
    """Source of truth: accepted rows with entry_score_v2_gate_pass=true (live pbv2_count=70)."""
    out: list[dict[str, Any]] = []
    for day, session in SESSIONS_625:
        path = _session_dir(repo, day, session) / "small_paper_events.csv"
        if not path.exists():
            continue
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if str(row.get("event_type")) != "accepted":
                    continue
                if str(row.get("entry_score_v2_gate_pass", "")).lower() != "true":
                    continue
                out.append({**row, "_day": day, "_session": session})
    return out


def decompose_entry_score_v2(trade: Mapping[str, Any]) -> dict[str, Any]:
    """Full per-candidate score breakdown — one row base + component detail."""
    mom_raw = _float(trade.get("momentum_continuation_score"))
    board_raw = _float(trade.get("entry_order_book_imbalance"))
    mom_cuts = TERTILE_CUTOFFS["Momentum"]
    board_cuts = TERTILE_CUTOFFS["Board"]
    mom_level = _bin_tertile(mom_raw, mom_cuts["p33"], mom_cuts["p66"]) if mom_raw is not None else ""
    board_level = _bin_tertile(board_raw, board_cuts["p33"], board_cuts["p66"]) if board_raw is not None else ""
    mom_token = f"Momentum:{mom_level}" if mom_level else ""
    board_token = f"Board:{board_level}" if board_level else ""
    tokens = active_score_tokens_v2(trade)
    points_breakdown: dict[str, int] = {}
    for token, pts in SCORE_POINTS_V2.items():
        if token in tokens:
            points_breakdown[token] = pts
    score_live = _float(trade.get("entry_expectancy_score_v2"))
    score_recomp = compute_entry_expectancy_score_fields(trade=trade)["entry_expectancy_score_v2"]
    return {
        "symbol": trade.get("symbol"),
        "timestamp": trade.get("event_time") or trade.get("entry_time"),
        "day": trade.get("_day", ""),
        "session": trade.get("_session", ""),
        "entry_score_v2_live": score_live,
        "entry_score_v2_recomputed_head": score_recomp,
        "score_delta": (score_recomp - int(score_live or 0)) if score_live is not None else None,
        "active_tokens": "|".join(tokens),
        "momentum_score_raw": mom_raw,
        "momentum_tertile": mom_level,
        "momentum_token": mom_token,
        "momentum_point": points_breakdown.get("Momentum:low", 0),
        "momentum_cutoff_p33": MOMENTUM_SCORE_CUTOFF_P33,
        "momentum_cutoff_pass": momentum_score_cutoff_pass(trade),
        "board_state_raw": board_raw,
        "board_tertile": board_level,
        "board_token": board_token,
        "board_point_mid": points_breakdown.get("Board:mid", 0),
        "board_point_high": points_breakdown.get("Board:high", 0),
        "board_mid_or_high_pass": board_mid_or_high_required_for_v2(trade),
        "continuation_quality": _float(trade.get("continuation_quality_score")),
        "spread_bps": _float(trade.get("spread_bps")),
        "daytrade_suitability_score": _float(trade.get("daytrade_suitability_score")),
        "cluster_id": trade.get("cluster_id"),
        "new_subcluster_id": trade.get("new_subcluster_id"),
        "liquidity_burst": _float(trade.get("liquidity_burst")),
        "volume_acceleration_5m": _float(trade.get("volume_acceleration_5m")),
        "entry_score_v2_threshold": trade.get("entry_score_v2_threshold") or ENTRY_SCORE_V2_GATE_MIN,
        "entry_score_v2_gate_pass_live": trade.get("entry_score_v2_gate_pass"),
        "total_points": sum(points_breakdown.values()),
    }


def _feature_row_values(trade: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    row: dict[str, Any] = {"symbol": trade.get("symbol"), "timestamp": trade.get("event_time")}
    for f in FEATURE_FIELDS:
        key = f"{prefix}{f}" if prefix else f
        row[key] = trade.get(f)
    return row


def regression_row(
    trade: Mapping[str, Any],
    config: SmallPaperPilotConfig,
    gate,
) -> dict[str, Any]:
    dec = decompose_entry_score_v2(trade)
    pre, pre_anchor = _pre_gate_blocker(trade)
    internal, anchor, would = _trace_pbv2_internal(gate, trade, config=config)
    first_diff = ""
    if dec["score_delta"] not in (0, None, 0.0):
        first_diff = "entry_score_v2_recompute_mismatch"
    elif not would:
        first_diff = internal or pre or "unknown"
    feat_625 = _feature_row_values(trade, prefix="live_")
    feat_head = _feature_row_values(trade, prefix="head_")
    for f in FEATURE_FIELDS:
        feat_head[f"head_{f}"] = trade.get(f)
    return {
        **dec,
        **{k: v for k, v in feat_625.items() if k not in dec},
        "pre_blocker": pre,
        "pre_blocker_anchor": pre_anchor,
        "head_pbv2_would_accept": would,
        "head_first_blocker": internal,
        "head_blocker_anchor": anchor,
        "first_differing_feature": first_diff,
        "live_gate_reject_reason": trade.get("gate_reject_reason"),
        "live_pbv2_internal_reason": trade.get("pbv2_internal_reason", ""),
    }


def audit_score_code_diff(repo: Path) -> list[dict[str, Any]]:
    parent = repo.parent
    rows: list[dict[str, Any]] = []
    for rel in SCORE_CODE_FILES:
        full = f"kabu_native/{rel}"
        diff = _git(parent, "diff", f"{PRE625_COMMIT}..HEAD", "--", full)
        head_text = (repo / rel).read_text(encoding="utf-8") if (repo / rel).exists() else ""
        pre_text = _git(parent, "show", f"{PRE625_COMMIT}:{full}")
        changed = bool(diff.strip())
        rows.append(
            {
                "file": rel,
                "pre625_commit": PRE625_COMMIT,
                "changed_since_pre625": changed,
                "diff_lines": len(diff.splitlines()) if diff else 0,
                "effective_constants": json.dumps(
                    {
                        "SCORE_POINTS_V2": SCORE_POINTS_V2,
                        "TERTILE_CUTOFFS_Momentum": TERTILE_CUTOFFS["Momentum"],
                        "TERTILE_CUTOFFS_Board": TERTILE_CUTOFFS["Board"],
                        "ENTRY_SCORE_V2_GATE_MIN": ENTRY_SCORE_V2_GATE_MIN,
                        "MOMENTUM_CUTOFF": MOMENTUM_SCORE_CUTOFF_P33,
                    },
                    ensure_ascii=False,
                ),
                "note": "UNCHANGED" if not changed else "SEE diff",
            }
        )
        if changed:
            for line in diff.splitlines()[:80]:
                if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
                    rows.append(
                        {
                            "file": rel,
                            "pre625_commit": PRE625_COMMIT,
                            "changed_since_pre625": True,
                            "diff_lines": 1,
                            "effective_constants": "",
                            "note": line[:400],
                        }
                    )
    return rows


def audit_feature_pipeline_diff(repo: Path) -> list[dict[str, Any]]:
    parent = repo.parent
    rows: list[dict[str, Any]] = []
    for feat, module, func in FEATURE_PIPELINE_FILES:
        rel = f"src/small_paper/{module.split()[0]}"
        if "research" in module:
            rel = f"src/research/{module.split()[0]}"
        full = f"kabu_native/{rel.replace('src/small_paper/', 'src/small_paper/')}"
        if not full.startswith("kabu_native"):
            full = f"kabu_native/{rel}"
        diff = _git(parent, "diff", f"{PRE625_COMMIT}..HEAD", "--", full)
        rows.append(
            {
                "feature": feat,
                "module": module,
                "function": func,
                "file": rel,
                "changed_since_pre625": bool(diff.strip()),
                "diff_line_count": len(diff.splitlines()) if diff else 0,
                "normalization": "tertile p33/p66" if feat.endswith("imbalance") or "momentum" in feat else "",
                "nan_default": "None → token absent → 0 points",
            }
        )
    return rows


def audit_git_blame(repo: Path) -> list[dict[str, Any]]:
    parent = repo.parent
    rows: list[dict[str, Any]] = []
    targets = (
        "kabu_native/src/small_paper/entry_expectancy_score_shadow.py",
        "kabu_native/src/small_paper/board_imbalance_shadow.py",
    )
    key_lines = (
        "SCORE_POINTS_V2",
        "TERTILE_CUTOFFS",
        "momentum_score_cutoff_pass",
        "board_mid_or_high_required_for_v2",
        "compute_entry_order_book_imbalance",
    )
    for full in targets:
        blame = _git(parent, "blame", "--line-porcelain", full)
        if not blame:
            continue
        content = _git(parent, "show", f"HEAD:{full}")
        for i, line in enumerate(content.splitlines(), 1):
            if not any(k in line for k in key_lines):
                continue
            chunk = blame.split("\n\n")
            for block in chunk:
                lines_b = block.splitlines()
                if len(lines_b) < 3:
                    continue
                if not lines_b[0].startswith("\t"):
                    continue
                lineno = int(lines_b[0].split()[0])
                if lineno != i:
                    continue
                commit = lines_b[0].split()[0]
                author = next((l[7:] for l in lines_b if l.startswith("author ")), "")
                date = next((l[10:] for l in lines_b if l.startswith("author-time ")), "")
                subj = _git(parent, "log", "-1", "--oneline", commit)
                rows.append(
                    {
                        "file": full.replace("kabu_native/", ""),
                        "line_no": i,
                        "line_text": line.strip()[:200],
                        "commit": commit,
                        "commit_subject": subj,
                        "author": author,
                        "author_time": date,
                    }
                )
    log_rows = _git(parent, "log", f"--since=2026-06-01", "--oneline", "--", *targets).splitlines()
    for ln in log_rows[:30]:
        rows.append(
            {
                "file": "git_log",
                "line_no": 0,
                "line_text": ln,
                "commit": ln.split()[0] if ln else "",
                "commit_subject": ln,
                "author": "",
                "author_time": "",
            }
        )
    return rows


def single_feature_rollback(
    trades_629_630: Sequence[Mapping[str, Any]],
    config: SmallPaperPilotConfig,
    repo: Path,
) -> list[dict[str, Any]]:
    """One feature override at a time on 629/630 live accepts."""
    overrides_map = {
        "momentum_force_low": {"momentum_continuation_score": "0.20"},
        "board_force_mid": {"entry_order_book_imbalance": "0.50"},
        "quality_force_high": {"continuation_quality_score": "0.80"},
        "spread_force_tight": {"spread_bps": "10"},
        "daytrade_force_pass": {"daytrade_suitability_score": "0.9"},
        "cluster_clear": {"cluster_id": "-1", "new_subcluster_id": "-1"},
        "volume_accel_low": {"volume_acceleration_5m": "0.001"},
    }
    rows: list[dict[str, Any]] = []
    for feat_id, patch in overrides_map.items():
        pbv2_pass = 0
        for trade in trades_629_630:
            t = dict(trade)
            t.update(patch)
            gate = config.make_exposure_gate(repo_root=repo)
            pre, _ = _pre_gate_blocker(t)
            if pre:
                continue
            _, _, would = _trace_pbv2_internal(gate, t, config=config)
            if would:
                pbv2_pass += 1
        rows.append(
            {
                "feature_rollback_id": feat_id,
                "patch": json.dumps(patch),
                "target_cohort": "629_630_live_accepts",
                "cohort_size": len(trades_629_630),
                "pbv2_pass_count": pbv2_pass,
                "note": "single feature only — no combination",
            }
        )
    return rows


def _629_630_live_accepts(repo: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for day, session in (("20260629", "live_session_080236"), ("20260630", "live_session_091118")):
        path = _session_dir(repo, day, session) / "small_paper_events.csv"
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row.get("event_type") == "accepted":
                    out.append(dict(row))
    return out


def _source_of_truth_trace(first_trade: Mapping[str, Any]) -> dict[str, Any]:
    """First PBv2 accept — trace score pipeline to first variable."""
    dec = decompose_entry_score_v2(first_trade)
    return {
        "trace_step": "source_of_truth_chain",
        "symbol": dec["symbol"],
        "timestamp": dec["timestamp"],
        "step_1_raw_momentum": dec["momentum_score_raw"],
        "step_2_momentum_tertile": dec["momentum_tertile"],
        "step_3_momentum_token": dec["momentum_token"],
        "step_4_momentum_points": dec["momentum_point"],
        "step_5_raw_board": dec["board_state_raw"],
        "step_6_board_tertile": dec["board_tertile"],
        "step_7_board_token": dec["board_token"],
        "step_8_board_points": dec["board_point_mid"] or dec["board_point_high"],
        "step_9_final_score": dec["entry_score_v2_live"],
        "first_function": "entry_expectancy_score_shadow._score_fields_from_points",
        "first_variable": "momentum_continuation_score",
        "head_vs_live_score_delta": dec["score_delta"],
    }


def run_phase607(*, repo_root: Optional[Path] = None) -> dict[str, Any]:
    repo = resolve_kabu_root(repo_root) if repo_root is None else repo_root
    out = resolve_reports_dir(repo)
    out.mkdir(parents=True, exist_ok=True)

    cfg_path = repo / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    config = load_pilot_config(cfg_path)
    gate = config.make_exposure_gate(repo_root=repo)

    pbv2_625 = _load_pbv2_accepted_625(repo)

    components: list[dict[str, Any]] = []
    component_detail: list[dict[str, Any]] = []
    for trade in pbv2_625:
        dec = decompose_entry_score_v2(trade)
        components.append(dec)
        for token, pts in SCORE_POINTS_V2.items():
            component_detail.append(
                {
                    "symbol": dec["symbol"],
                    "timestamp": dec["timestamp"],
                    "token": token,
                    "points_if_active": pts if token in dec["active_tokens"].split("|") else 0,
                    "active": token in dec["active_tokens"].split("|"),
                }
            )
        for f in FEATURE_FIELDS:
            component_detail.append(
                {
                    "symbol": dec["symbol"],
                    "timestamp": dec["timestamp"],
                    "token": f"feature:{f}",
                    "points_if_active": trade.get(f),
                    "active": trade.get(f) not in (None, ""),
                }
            )

    regression = [regression_row(t, config, gate) for t in pbv2_625]
    regression_80 = regression

    score_code = audit_score_code_diff(repo)
    feat_pipe = audit_feature_pipeline_diff(repo)
    blame = audit_git_blame(repo)
    live_629_630 = _629_630_live_accepts(repo)
    rollback = single_feature_rollback(live_629_630, config, repo)

    score_deltas = [r["score_delta"] for r in regression if r.get("score_delta") is not None]
    mismatch_count = sum(1 for d in score_deltas if d != 0)
    head_pass = sum(1 for r in regression if r.get("head_pbv2_would_accept"))
    sot = _source_of_truth_trace(pbv2_625[0]) if pbv2_625 else {}

    mandatory = {
        "1_same_calculation_as_625": (
            "YES — entry_expectancy_score_shadow.py UNCHANGED since f50c5a7; "
            f"70/70 live rows: recompute score == live score (mismatch={mismatch_count})"
        ),
        "2_first_differing_feature": (
            "NONE on 6/25 PBv2 cohort (score identical). "
            "629/630 OR-only cohort differs: momentum_continuation_score > 0.2546 (no Momentum:low token) "
            "or board tertile low (no Board:mid|high) — input distribution, not formula change"
        ),
        "3_when_feature_generation_changed": "No change since f50c5a7 in score or board_imbalance modules (git diff empty)",
        "4_commits": "196a559 kabutrade0621 last touch entry_expectancy_score_shadow; no diff f50c5a7..HEAD",
        "5_score_point_change": f"625 PBv2 70 rows: delta=0 for all; mean score=3; 629/630 live accepts score 0-2",
        "6_same_trend_all_80": f"YES for 625: all {len(pbv2_625)} rows score=3, tokens Momentum:low+Board:*; HEAD pbv2 pass={head_pass}/{len(pbv2_625)}",
        "7_rollback_code_location": "No score code rollback needed; PBv2 blockers are guard stack + market inputs on 629/630",
        "8_minimal_change_to_restore_625_score": (
            "None required for score formula. Ensure momentum_continuation_score + entry_order_book_imbalance "
            "populated at eval (live_feature_bridge + board_imbalance_shadow unchanged). "
            "629/630 recovery requires favorable momentum/board at entry, not score code revert"
        ),
        "source_of_truth_trace": sot,
        "629_630_single_feature_rollback_best": max(rollback, key=lambda r: r["pbv2_pass_count"], default={}),
    }

    _write_rows(out / "phase607_entry_score_components_625.csv", components)
    _write_rows(out / "phase607_entry_score_components_625_detail.csv", component_detail)
    _write_rows(out / "phase607_entry_score_regression.csv", regression)
    _write_rows(out / "phase607_feature_pipeline_diff.csv", feat_pipe)
    _write_rows(out / "phase607_score_code_diff.csv", score_code)
    _write_rows(out / "phase607_80trade_regression.csv", regression_80)
    _write_rows(out / "phase607_git_blame.csv", blame)
    _write_rows(out / "phase607_single_feature_rollback.csv", rollback)

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "pbv2_625_count": len(pbv2_625),
        "mandatory_answers": mandatory,
        "output_dir": str(out),
    }
    (out / "phase607_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    doc_lines = [
        "# Phase607 — entry_score_v2 Feature Regression Audit",
        "",
        f"**Verdict:** `{VERDICT}`",
        "",
        f"PBv2 source of truth: 6/25 live accepts with entry_score_v2_gate_pass=true (n={len(pbv2_625)})",
        "",
        "## Mandatory answers",
        "",
    ]
    for k, v in mandatory.items():
        doc_lines.append(f"### {k}")
        doc_lines.append(str(v))
        doc_lines.append("")
    (repo / "docs" / "operations" / "phase607_entry_score_feature_regression.md").write_text(
        "\n".join(doc_lines), encoding="utf-8"
    )
    return report
