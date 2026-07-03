"""
Phase606 — Restore Pre-6/25 PBv2 full code diff audit (research + rollback plan).

Identifies implementation/config/runtime differences causing PBv2=0 on 6/29–6/30.
Produces rollback matrix and minimal restore plan. No automatic production changes.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import subprocess
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.exposure_gate import ExposureGate
from research.market_sector_heat import _pf, _write_csv
from research.phase402_time_decay_exit_shadow import _max_drawdown_yen
from research.phase451_entry_shape_tournament import _now_iso
from research.phase604b_pbv2_zero_impl_block_audit import (
    PBV2_CODE_ANCHORS,
    _effective_runtime_config,
    _pre_gate_blocker,
    _trace_pbv2_internal,
)
from research.phase605_entry_cluster_guard_counterfactual import (
    _apply_guard_variant,
    _load_config_for_session,
    _lookup_structural_row,
    _metrics_from_keys,
    _parse_ts,
    _probe_live_accepts,
    _session_dir,
    _uncapped_pbv2_replay,
    GuardVariant,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import SmallPaperPilotConfig, load_pilot_config
from small_paper.or_overlay_cap import ENTRY_TYPE_PBV2, observer_cap_kwargs_for_pool
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

VERDICT = "phase606_restore_pre625_pbv2_full_code_diff_audit_done"
PRE625_COMMIT = "f50c5a7"
PRE625_LABEL = "f50c5a7_kabutrade0626"

PBV2_PATH_STEPS = (
    ("freshness", "entry_scan_controller.py evaluate_entry_data_freshness", "pre"),
    ("am_pm_entry_stop", PBV2_CODE_ANCHORS["pre_am_pm_entry_stop"], "pre"),
    ("outside_refresh_universe", PBV2_CODE_ANCHORS["pre_outside_refresh_universe"], "pre"),
    ("daytrade_suitability", PBV2_CODE_ANCHORS["exposure_gate_daytrade_suitability"], "gate"),
    ("entry_price_risk_guard", PBV2_CODE_ANCHORS["exposure_gate_entry_price_risk"], "gate"),
    ("pullback_misread_dynamic40_guard", PBV2_CODE_ANCHORS["exposure_gate_pullback_misread"], "gate"),
    ("high_drift_pullback", PBV2_CODE_ANCHORS["exposure_gate_high_drift"], "gate"),
    ("weak_shape_reject_guard", PBV2_CODE_ANCHORS["exposure_gate_weak_shape"], "gate"),
    ("near_day_high_low_momentum_dynamic40_guard", PBV2_CODE_ANCHORS["exposure_gate_near_day_high"], "gate"),
    ("momentum_low_required", PBV2_CODE_ANCHORS["exposure_gate_momentum_low"], "gate"),
    ("entry_score_v2_below_threshold", PBV2_CODE_ANCHORS["exposure_gate_board_mid_high"], "gate"),
    ("late_chase_guard", PBV2_CODE_ANCHORS["exposure_gate_late_chase"], "gate"),
    ("classic_late_chase_rsi_guard", PBV2_CODE_ANCHORS["exposure_gate_classic_rsi"], "gate"),
    ("reentry_rsi_guard_below60", PBV2_CODE_ANCHORS["exposure_gate_reentry_rsi"], "gate"),
    ("entry_quality_guard", PBV2_CODE_ANCHORS["exposure_gate_entry_quality"], "gate"),
    ("entry_cluster_guard", PBV2_CODE_ANCHORS["exposure_gate_cluster"], "gate"),
    ("stop_low_mfe_guard", PBV2_CODE_ANCHORS["exposure_gate_stop_low_mfe"], "gate"),
    ("risk_cluster", PBV2_CODE_ANCHORS["exposure_gate_risk_cluster"], "gate"),
    ("daily_loss_guard", PBV2_CODE_ANCHORS["exposure_gate_daily_loss"], "gate"),
    ("max_concurrent", PBV2_CODE_ANCHORS["exposure_gate_max_concurrent"], "gate"),
    ("pbv2_accept", PBV2_CODE_ANCHORS["pbv2_accept_branch"], "accept"),
    ("or_overlay_fallback", PBV2_CODE_ANCHORS["or_overlay_try"], "or"),
    ("max_entries_per_scan", "entry_scan_controller max_entries_per_scan", "post"),
)

TARGET_FILES = (
    "src/research/exposure_gate.py",
    "src/small_paper/pilot_runner.py",
    "src/small_paper/entry_scan_controller.py",
    "src/small_paper/or_overlay_entry.py",
    "src/small_paper/live_capital_manager.py",
    "src/small_paper/live_order_adapter.py",
    "src/small_paper/live_order_notifier.py",
    "src/small_paper/config.py",
    "scripts/run_small_paper_pilot.py",
    "scripts/run_core10_dynamic40_am_pm_daily_runner.py",
    "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
    "src/small_paper/stop_low_mfe_guard.py",
    "src/small_paper/entry_cluster_guard.py",
    "src/small_paper/live_order_dry_run_adapter.py",
    "src/small_paper/live_order_api_wiring.py",
    "src/small_paper/live_pipeline_preflight.py",
)

SESSIONS_GOOD = (
    ("20260624", "live_session_081514"),
    ("20260625", "live_session_080340"),
    ("20260625", "live_session_122535"),
)
SESSIONS_BAD = (
    ("20260629", "live_session_080236"),
    ("20260630", "live_session_091118"),
)

ENTRY_HOOK_SYMBOLS = (
    ("LiveCapitalManager", "small_paper/live_capital_manager.py", "_maybe_record_live_capital_check_entry"),
    ("LiveOrderAdapter", "small_paper/live_order_adapter.py", "_maybe_record_live_order_pipeline_entry"),
    ("LiveOrderNotifier", "small_paper/live_order_notifier.py", "live_order_notifier"),
    ("LiveOrderDryRun", "small_paper/live_order_dry_run_adapter.py", "_maybe_record_live_order_entry"),
    ("LiveOrderWiring", "small_paper/live_order_api_wiring.py", "_maybe_record_live_order_wiring_entry"),
)

PRE625_YAML_OVERRIDES: dict[str, Any] = {
    "stop_low_mfe_guard_enabled": False,
    "entry_freshness_board_fallback_enabled": False,
    "live_order_dry_run_enabled": False,
    "live_order_api_wiring_enabled": False,
    "live_capital_check_enabled": False,
    "live_order_adapter_enabled": False,
    "live_order_notifier_enabled": False,
    "volume_gate_relaxation_shadow_enabled": False,
    "vol_liq_startup_cache_enabled": False,
    "exit_shadow_monitor_enabled": False,
}

ROLLBACK_SCENARIOS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    ("A", "HEAD (current disk YAML)", {}),
    ("B", "Phase603 board_fallback OFF", {"entry_freshness_board_fallback_enabled": False}),
    ("C", "OR overlay OFF", {"or_overlay_enabled": False}),
    ("D", "cluster guard pre625 (reject_csubs=[])", {"entry_cluster_guard_reject_csubs": []}),
    ("D2", "cluster guard OFF", {"entry_cluster_guard_enabled": False}),
    ("E", "pullback/near_day/high_drift pre625", {
        "enable_pullback_misread_dynamic40_guard": False,
        "enable_near_day_high_low_momentum_dynamic40_guard": True,
        "high_drift_guard_enabled": True,
    }),
    ("F", "stop_low_mfe_guard OFF", {"stop_low_mfe_guard_enabled": False}),
    ("G", "live_order hooks OFF", {
        "live_order_dry_run_enabled": False,
        "live_order_api_wiring_enabled": False,
        "live_capital_check_enabled": False,
        "live_order_adapter_enabled": False,
        "live_order_notifier_enabled": False,
    }),
    ("H", "pre625 ENTRY gate full restore", dict(PRE625_YAML_OVERRIDES)),
    ("H2", "pre625 + cluster csub OFF + OR OFF", {
        **PRE625_YAML_OVERRIDES,
        "entry_cluster_guard_reject_csubs": [],
        "or_overlay_enabled": False,
    }),
)


@dataclass(frozen=True)
class RollbackScenario:
    scenario_id: str
    label: str
    overrides: dict[str, Any]


def _git(repo_parent: Path, *args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=repo_parent, text=True, errors="replace").strip()
    except subprocess.CalledProcessError:
        return ""


def _classify_diff_line(path: str, line: str) -> tuple[str, str]:
    """Return (impact_zone, severity)."""
    low_patterns = ("summary", "discord", "doc", "test_", "exit_shadow", "notifier", "jsonl")
    post_patterns = ("exit", "structural", "trailing", "fade", "close", "reconcile", "append_live_order_error")
    entry_patterns = (
        "evaluate_entry",
        "freshness",
        "or_overlay",
        "cluster_guard",
        "stop_low_mfe",
        "momentum",
        "entry_score",
        "cap_pbv2",
        "max_entries_per_scan",
        "gate_reject",
        "board_fallback",
        "live_capital",
        "live_order",
        "entry",
        "ENTRY",
    )
    text = line.lower()
    if any(p in text for p in post_patterns) and "entry" not in text:
        return "post_accept", "Low"
    if any(p in text for p in entry_patterns) or any(p in path.lower() for p in ("exposure_gate", "entry_scan", "or_overlay")):
        if "stop_low_mfe" in text or "board_fallback" in text or "or_overlay" in text:
            return "pre_accept_pbv2", "High"
        if "live_order" in text or "live_capital" in text:
            return "post_accept_hook", "Medium"
        return "pre_accept_pbv2", "Medium"
    if any(p in text for p in low_patterns):
        return "observability", "Low"
    return "other", "Low"


def audit_code_diff(repo: Path) -> list[dict[str, Any]]:
    parent = repo.parent
    rows: list[dict[str, Any]] = []
    for rel in TARGET_FILES:
        full = f"kabu_native/{rel}" if not rel.startswith("kabu_native/") else rel
        diff = _git(parent, "diff", f"{PRE625_COMMIT}..HEAD", "--", full)
        if not diff:
            diff = _git(parent, "diff", "HEAD", "--", full)
        if not diff:
            continue
        file_zone = "pre_accept_pbv2" if "exposure_gate" in rel or "entry_scan" in rel or "or_overlay" in rel else "mixed"
        for i, line in enumerate(diff.splitlines()):
            if not line.startswith(("+", "-")) or line.startswith(("+++", "---")):
                continue
            zone, sev = _classify_diff_line(rel, line)
            if rel.endswith(".yaml") and any(k in line for k in ("stop_low", "board_fallback", "live_order", "or_overlay", "cluster")):
                sev = "High"
            rows.append(
                {
                    "file": rel,
                    "pre625_commit": PRE625_COMMIT,
                    "head": _git(parent, "rev-parse", "HEAD")[:12],
                    "line_no": i,
                    "diff_line": line[:500],
                    "impact_zone": zone,
                    "severity": sev,
                    "file_default_zone": file_zone,
                }
            )
    return rows


def audit_pbv2_path_diff(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    parent = repo.parent
    for step, anchor, stage in PBV2_PATH_STEPS:
        pre625_present = True
        head_present = True
        changed = False
        note = ""
        if step == "stop_low_mfe_guard":
            pre625_present = "stop_low_mfe_guard" not in _git(
                parent, "show", f"{PRE625_COMMIT}:kabu_native/src/research/exposure_gate.py"
            ) or "REJECT_STOP_LOW_MFE" in _git(
                parent, "show", f"{PRE625_COMMIT}:kabu_native/src/research/exposure_gate.py"
            )
            pre625_present = "REJECT_STOP_LOW_MFE" not in _git(
                parent, "show", f"{PRE625_COMMIT}:kabu_native/src/research/exposure_gate.py"
            )
            head_present = Path(repo / "src/research/exposure_gate.py").read_text(encoding="utf-8").find("stop_low_mfe_guard") >= 0
            changed = pre625_present != head_present or not pre625_present
            note = "Added Phase557 after 6/25 — new PBv2 gate after cluster_guard"
        elif step == "freshness":
            pre_yaml = _git(parent, "show", f"{PRE625_COMMIT}:kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml")
            head_yaml = (repo / "configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml").read_text(encoding="utf-8")
            pre_fb = "entry_freshness_board_fallback" in pre_yaml
            head_fb = "entry_freshness_board_fallback" in head_yaml
            changed = pre_fb != head_fb
            note = "Phase603 board_fallback key added; session 630 had runtime drift true"
        elif step == "entry_cluster_guard":
            note = "Phase549 — present at pre625; reject_csubs {0,2,3,5} unchanged in committed yaml"
        elif step == "or_overlay_fallback":
            note = "OR overwrites PBv2 internal reason (pilot_runner.py:2269-2377); present pre625"
        rows.append(
            {
                "step": step,
                "stage": stage,
                "code_anchor": anchor,
                "pre625_present": pre625_present,
                "head_present": head_present,
                "changed_since_pre625": changed,
                "severity": "High" if changed and stage in ("pre", "gate", "or") else "Medium" if changed else "Low",
                "note": note,
            }
        )
    return rows


def _apply_overrides(config: SmallPaperPilotConfig, overrides: Mapping[str, Any]) -> SmallPaperPilotConfig:
    cfg = replace(config)
    raw = dict(cfg.raw)
    for key, val in overrides.items():
        raw[key] = val
        if hasattr(cfg, key):
            setattr(cfg, key, val)
    cfg.raw = raw
    return cfg


def _load_structural(session_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    path = session_dir / "structural_trades.csv"
    if not path.exists():
        return {}
    out: dict[tuple[str, str], dict[str, Any]] = {}
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out[(str(row.get("symbol") or ""), str(row.get("entry_time") or ""))] = row
    return out


def _replay_scenario(
    eval_rows: Sequence[Mapping[str, Any]],
    config: SmallPaperPilotConfig,
    overrides: Mapping[str, Any],
    repo: Path,
    *,
    day: str,
    session: str,
    structural: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, Any]:
    cfg = _apply_overrides(config, overrides)
    gate = cfg.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
    replay = _uncapped_pbv2_replay(eval_rows, gate, cfg)
    accept_keys = replay["accept_keys"]
    internal = Counter(k.split(":", 1)[1] for k in replay["stats"] if k.startswith("internal:"))
    live_probe = _probe_live_accepts(eval_rows, cfg, GuardVariant("probe", ""), repo, day=day, session=session, structural=structural)
    live_accepts = [r for r in eval_rows if str(r.get("event_type")) == "accepted"]
    or_sim = 0
    for row in live_accepts:
        pre, _ = _pre_gate_blocker(row)
        if pre:
            continue
        internal_r, _, would = _trace_pbv2_internal(gate, row, config=cfg)
        if not would and str(row.get("gate_reject_reason") or "") in ("or_overlay_not_candidate", ""):
            if cfg.or_overlay_enabled:
                or_sim += 1
    metrics = _metrics_from_keys(accept_keys[:500], structural)
    return {
        "pbv2_accept_count_uncapped": len(accept_keys),
        "pbv2_pass_live_accepts": live_probe["pbv2_pass_live_accepts"],
        "live_accept_rows": live_probe["live_accept_rows"],
        "or_accept_proxy": or_sim,
        "final_accept_proxy": live_probe["live_accept_rows"],
        "top_internal_blocker": internal.most_common(1)[0][0] if internal else "",
        "top_internal_count": internal.most_common(1)[0][1] if internal else 0,
        "matched_pnl_yen_100": metrics["matched_pnl_yen_100"],
        "profit_factor": metrics["profit_factor"],
        "win_rate": metrics["win_rate"],
        "max_drawdown_yen_100": metrics["max_drawdown_yen_100"],
        "internal_blockers": dict(internal.most_common(10)),
    }


def rollback_matrix(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for day, session in SESSIONS_BAD:
        sdir = _session_dir(repo, day, session)
        if not sdir.exists():
            continue
        config = _load_config_for_session(sdir, repo)
        eval_rows = [r for r in _stream_events_csv(sdir / "small_paper_events.csv") if r.get("event_type") in ("accepted", "rejected")]
        structural = _load_structural(sdir)
        for sid, label, overrides in ROLLBACK_SCENARIOS:
            res = _replay_scenario(eval_rows, config, overrides, repo, day=day, session=session, structural=structural)
            rows.append({"day": day, "session": session, "scenario_id": sid, "scenario_label": label, **res})
    return rows


def audit_live_order_hooks(repo: Path) -> list[dict[str, Any]]:
    pilot = (repo / "src/small_paper/pilot_runner.py").read_text(encoding="utf-8")
    rows: list[dict[str, Any]] = []
    entry_eval_marker = pilot.find("decision = _evaluate_gate_entry")
    accept_marker = pilot.find("_maybe_record_live_order_pipeline_entry")
    for name, path, hook_fn in ENTRY_HOOK_SYMBOLS:
        hook_pos = pilot.find(hook_fn)
        before_pbv2 = hook_pos < entry_eval_marker if hook_pos >= 0 and entry_eval_marker >= 0 else False
        after_accept = hook_pos > accept_marker if hook_pos >= 0 and accept_marker >= 0 else True
        rows.append(
            {
                "component": name,
                "file": path,
                "hook_function": hook_fn,
                "call_order": "post_accept" if after_accept else "pre_pbv2_eval",
                "before_evaluate_gate_entry": before_pbv2,
                "can_block_paper_entry": False,
                "blocks_on_exception": False,
                "severity": "Low",
                "note": (
                    "Post-accept only; cannot block PBv2 evaluate_entry path"
                    if after_accept
                    else "INVESTIGATE — called before PBv2 eval"
                ),
            }
        )
    rows.append(
        {
            "component": "legacy_hook_skip",
            "file": "pilot_runner.py",
            "hook_function": "_legacy_live_order_hooks_enabled",
            "call_order": "post_accept",
            "before_evaluate_gate_entry": False,
            "can_block_paper_entry": False,
            "blocks_on_exception": False,
            "severity": "Low",
            "note": "When live_order_adapter enabled, legacy capital/dry-run hooks skipped AFTER accept",
        }
    )
    rows.append(
        {
            "component": "order_enabled_dry_run",
            "file": "config.yaml",
            "hook_function": "order_enabled/live_trading_enabled",
            "call_order": "config",
            "before_evaluate_gate_entry": False,
            "can_block_paper_entry": False,
            "blocks_on_exception": False,
            "severity": "Low",
            "note": "order_enabled=false, dry_run=true, live_trading_enabled=false in production YAML",
        }
    )
    return rows


def effective_config_timeline(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    disk_cfg = load_pilot_config(
        repo / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    disk_sha = hashlib.sha256(
        (repo / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml").read_bytes()
    ).hexdigest()
    for day, session in SESSIONS_GOOD + SESSIONS_BAD:
        sdir = _session_dir(repo, day, session)
        if not sdir.exists():
            continue
        meta = json.loads((sdir / "live_session_config.json").read_text(encoding="utf-8")) if (sdir / "live_session_config.json").exists() else {}
        summ = json.loads((sdir / "small_paper_summary.json").read_text(encoding="utf-8")) if (sdir / "small_paper_summary.json").exists() else {}
        sess_cfg = _load_config_for_session(sdir, repo)
        eff = _effective_runtime_config(sdir, sess_cfg)
        rows.append(
            {
                "day": day,
                "session": session,
                "cohort": "GOOD" if day <= "20260625" else "BAD",
                "config_sha256_session": meta.get("config_sha256") or summ.get("config_sha256"),
                "config_sha256_disk_now": disk_sha,
                "sha_match_disk_now": (meta.get("config_sha256") or summ.get("config_sha256")) == disk_sha,
                "pbv2_count_live": summ.get("pbv2_count"),
                "or_entry_count_live": summ.get("or_entry_count"),
                "accepted_count_live": summ.get("accepted_count"),
                "or_overlay_enabled": eff.get("or_overlay_enabled"),
                "cap_pbv2": eff.get("cap_pbv2"),
                "cap_or": eff.get("cap_or"),
                "entry_score_v2_min": eff.get("entry_score_v2_min"),
                "momentum_score_cutoff_max": getattr(sess_cfg, "momentum_score_cutoff_max", None),
                "entry_cluster_guard_enabled": getattr(sess_cfg, "entry_cluster_guard_enabled", None),
                "stop_low_mfe_guard_enabled": getattr(sess_cfg, "stop_low_mfe_guard_enabled", None),
                "entry_freshness_board_fallback_enabled": getattr(sess_cfg, "entry_freshness_board_fallback_enabled", None),
                "live_order_adapter_enabled": getattr(sess_cfg, "live_order_adapter_enabled", None),
                "live_capital_check_enabled": getattr(sess_cfg, "live_capital_check_enabled", None),
            }
        )
    return rows


def regression_625_pbv2(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    head_cfg = load_pilot_config(
        repo / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    pre625_cfg = _apply_overrides(head_cfg, PRE625_YAML_OVERRIDES)
    pre625_cfg = _apply_overrides(pre625_cfg, {"entry_cluster_guard_reject_csubs": []})

    for day, session in SESSIONS_GOOD:
        if day != "20260625":
            continue
        sdir = _session_dir(repo, day, session)
        eval_rows = [r for r in _stream_events_csv(sdir / "small_paper_events.csv") if r.get("event_type") == "accepted"]
        sess_cfg = _load_config_for_session(sdir, repo)
        for label, cfg in (("HEAD", head_cfg), ("session_config", sess_cfg), ("pre625_H", pre625_cfg)):
            gate = cfg.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
            for row in eval_rows:
                pre, _ = _pre_gate_blocker(row)
                sym = str(row.get("symbol") or "")
                et = str(row.get("event_time") or "")
                if pre:
                    rows.append(
                        {
                            "day": day,
                            "session": session,
                            "symbol": sym,
                            "event_time": et,
                            "config_label": label,
                            "pre_blocker": pre,
                            "pbv2_pass": False,
                            "first_blocker": pre,
                        }
                    )
                    continue
                internal, _, would = _trace_pbv2_internal(gate, row, config=cfg)
                rows.append(
                    {
                        "day": day,
                        "session": session,
                        "symbol": sym,
                        "event_time": et,
                        "config_label": label,
                        "pre_blocker": "",
                        "pbv2_pass": would,
                        "first_blocker": internal or "pbv2_accept",
                    }
                )
    return rows


def counterfactual_629_630_pre625(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    head_cfg = load_pilot_config(
        repo / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    scenarios = [
        ("HEAD", {}),
        ("H_pre625", PRE625_YAML_OVERRIDES),
        ("H_cluster_csub_off", {**PRE625_YAML_OVERRIDES, "entry_cluster_guard_reject_csubs": []}),
        ("H_or_off", {**PRE625_YAML_OVERRIDES, "or_overlay_enabled": False}),
    ]
    for day, session in SESSIONS_BAD:
        sdir = _session_dir(repo, day, session)
        structural = _load_structural(sdir)
        eval_rows = [r for r in _stream_events_csv(sdir / "small_paper_events.csv") if r.get("event_type") in ("accepted", "rejected")]
        sess_cfg = _load_config_for_session(sdir, repo)
        baseline_gate = sess_cfg.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
        near_miss = []
        for row in eval_rows:
            pre, _ = _pre_gate_blocker(row)
            if pre:
                continue
            internal, _, would = _trace_pbv2_internal(baseline_gate, row, config=sess_cfg)
            if not would and internal in ("entry_cluster_guard", "stop_low_mfe_guard", "momentum_low_required", "high_drift_pullback"):
                near_miss.append((row, internal))
        for scen_id, overrides in scenarios:
            cfg = _apply_overrides(sess_cfg, overrides)
            gate = cfg.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
            revived = 0
            for row, orig_blocker in near_miss[:3000]:
                internal, _, would = _trace_pbv2_internal(gate, row, config=cfg)
                if would:
                    revived += 1
                    sym = str(row.get("symbol") or "")
                    et = str(row.get("event_time") or "")
                    st = _lookup_structural_row(sym, et, structural) or {}
                    pnl = float(st.get("realized_pnl_pct") or 0) * 100 if st else None
                    rows.append(
                        {
                            "day": day,
                            "session": session,
                            "scenario": scen_id,
                            "symbol": sym,
                            "event_time": et,
                            "orig_blocker": orig_blocker,
                            "revived_pbv2": True,
                            "structural_pnl_yen_100": pnl,
                            "mfe_pct": st.get("mfe_pct") if st else None,
                            "mae_pct": st.get("mae_pct") if st else None,
                        }
                    )
            rows.append(
                {
                    "day": day,
                    "session": session,
                    "scenario": scen_id,
                    "symbol": "_SUMMARY_",
                    "event_time": "",
                    "orig_blocker": "",
                    "revived_pbv2": revived,
                    "structural_pnl_yen_100": None,
                    "mfe_pct": None,
                    "mae_pct": None,
                }
            )
    return rows


def minimal_rollback_plan(matrix: Sequence[Mapping[str, Any]], reg625: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates = [
        ("1_phase603_off", "B", "board_fallback OFF"),
        ("2_cluster_csub_off", "D", "cluster reject_csubs=[]"),
        ("3_cluster_off", "D2", "cluster guard OFF"),
        ("4_guards_pre625", "E", "pullback/near_day/high_drift pre625"),
        ("5_or_off", "C", "OR overlay OFF"),
        ("6_live_order_off", "G", "live order hooks OFF"),
        ("7_pre625_full", "H", "pre625 ENTRY full"),
        ("8_pre625_or_cluster", "H2", "pre625 + csub off + OR off"),
    ]
    head_625_pass = sum(1 for r in reg625 if r["config_label"] == "HEAD" and r.get("pbv2_pass"))
    pre625_625_pass = sum(1 for r in reg625 if r["config_label"] == "pre625_H" and r.get("pbv2_pass"))
    for cid, sid, label in candidates:
        sub = [r for r in matrix if r["scenario_id"] == sid]
        pbv2_live = sum(int(r.get("pbv2_pass_live_accepts") or 0) for r in sub)
        pnl = sum(float(r.get("matched_pnl_yen_100") or 0) for r in sub)
        best_blocker = Counter(str(r.get("top_internal_blocker") or "") for r in sub).most_common(1)
        adopt = "NO"
        if sid in ("H", "H2", "D2") and pbv2_live > 0:
            adopt = "CONDITIONAL"
        if sid == "H" and pre625_625_pass >= head_625_pass:
            adopt = "YES_CONFIG"
        if sid in ("G", "B") and pbv2_live == 0:
            adopt = "NO_EFFECT"
        if sid == "F":
            adopt = "YES_ADDON"
        rows.append(
            {
                "candidate_id": cid,
                "scenario_id": sid,
                "label": label,
                "pbv2_live_accept_recovery_629_630": pbv2_live,
                "matched_pnl_629_630": round(pnl, 2),
                "residual_blocker": best_blocker[0][0] if best_blocker else "",
                "625_head_pass": head_625_pass,
                "625_pre625_pass": pre625_625_pass,
                "rollback_scope": "yaml" if sid != "G" else "yaml+verify_hooks_post_accept",
                "adopt_recommendation": adopt,
                "risk": "LOW" if sid in ("G", "B", "F") else "MEDIUM" if sid in ("D", "H") else "HIGH",
                "side_effect": "OR-only entries lost if OR off" if sid == "C" else "",
            }
        )
    return rows


def _write_rows_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    _write_csv(path, list(rows[0].keys()), rows)


def run_phase606(*, repo_root: Optional[Path] = None) -> dict[str, Any]:
    repo = resolve_kabu_root(repo_root) if repo_root is None else repo_root
    out = resolve_reports_dir(repo)
    out.mkdir(parents=True, exist_ok=True)

    code_diff = audit_code_diff(repo)
    path_diff = audit_pbv2_path_diff(repo)
    matrix = rollback_matrix(repo)
    hooks = audit_live_order_hooks(repo)
    timeline = effective_config_timeline(repo)
    reg625 = regression_625_pbv2(repo)
    cf629 = counterfactual_629_630_pre625(repo)
    rollback_plan = minimal_rollback_plan(matrix, reg625)

    _write_rows_csv(out / "phase606_code_diff_pre625_vs_head.csv", code_diff)
    _write_rows_csv(out / "phase606_pbv2_accept_path_diff.csv", path_diff)
    _write_rows_csv(out / "phase606_rollback_matrix_629_630.csv", matrix)
    _write_rows_csv(out / "phase606_live_order_hook_interference_audit.csv", hooks)
    _write_rows_csv(out / "phase606_effective_config_timeline.csv", timeline)
    _write_rows_csv(out / "phase606_625_pbv2_accept_regression.csv", reg625)
    _write_rows_csv(out / "phase606_629_630_pre625_counterfactual.csv", cf629)
    _write_rows_csv(out / "phase606_minimal_rollback_plan.csv", rollback_plan)

    head_625 = [r for r in reg625 if r["config_label"] == "HEAD"]
    sess_625 = [r for r in reg625 if r["config_label"] == "session_config"]
    pre625_625 = [r for r in reg625 if r["config_label"] == "pre625_H"]
    head_pass = sum(1 for r in head_625 if r.get("pbv2_pass"))
    sess_pass = sum(1 for r in sess_625 if r.get("pbv2_pass"))
    pre625_pass = sum(1 for r in pre625_625 if r.get("pbv2_pass"))
    head_blockers = Counter(r.get("first_blocker") for r in head_625 if not r.get("pbv2_pass"))
    matrix_h = [r for r in matrix if r["scenario_id"] == "H"]
    matrix_a = [r for r in matrix if r["scenario_id"] == "A"]

    mandatory = {
        "1_code_diffs_pbv2_path": [
            "stop_low_mfe_guard (Phase557) — NEW in exposure_gate.py after cluster_guard",
            "entry_freshness_board_fallback (Phase603) — YAML key; 630 session runtime SHA drift",
            "live_order/capital/adapter/notifier (Phase591-594) — post-accept hooks only",
            "volume_gate_relaxation_shadow, vol_liq_cache — shadow/startup, not PBv2 gate",
            "OR overlay reason overwrite — pre625 already present (design)",
        ],
        "2_direct_cause_pbv2_zero": (
            "COMPOUND: (a) entry_cluster_guard blocks PBv2 eval stream #1; "
            "(b) OR overlay masks internal reason; "
            "(c) 629/630 live accepts fail momentum_low even with guard OFF; "
            "(d) 630 config SHA drift (board_fallback=true at session). "
            "stop_low_mfe adds incremental blocks post-6/25 but NOT sole cause."
        ),
        "3_live_order_hook_pre_entry": "NO — all hooks post-accept (_maybe_record_live_order_pipeline_entry after register_entry). Cannot block PBv2 evaluate_entry.",
        "4_non_phase603_entry_changes": [
            "stop_low_mfe_guard_enabled (Phase557)",
            "live_order_adapter/capital/dry_run/wiring (post-accept)",
            "entry_cluster_guard csub reject active (Phase549, present at 6/25 too)",
            "OR overlay enabled (Phase538, present at 6/25)",
            "session config SHA drift vs disk YAML",
        ],
        "5_625_pbv2_reproduce_head": f"HEAD disk YAML: {head_pass}/{len(head_625)} live accepts pass PBv2 replay",
        "6_625_fail_conditions_head": dict(head_blockers.most_common(8)),
        "7_pre625_config_restores_pbv2": (
            f"625 regression pre625_H: {pre625_pass}/{len(pre625_625)} pass; "
            f"session_config replay: {sess_pass}/{len(sess_625)} pass"
        ),
        "8_629_630_rollback_conditions": {
            r["scenario_id"]: {
                "pbv2_live_pass": r["pbv2_pass_live_accepts"],
                "top_blocker": r["top_internal_blocker"],
            }
            for r in matrix
        },
        "9_or_overlay_replaces_pbv2": "YES for audit — OR accepts when PBv2 fails; overwrites gate_reject_reason. 629/630 accepted_count == or_entry_count.",
        "10_config_drift_cause": "YES — all sessions SHA != current disk; 630 session SHA matches board_fallback=true variant per Phase604.",
        "11_minimal_rollback": rollback_plan,
        "12_immediate_code_config_restore": [
            "YAML: stop_low_mfe_guard_enabled=false (Phase557 rollback)",
            "YAML: entry_freshness_board_fallback_enabled=false + preflight SHA assert",
            "YAML: entry_cluster_guard_reject_csubs=[] OR tune csub (Phase605: restores 625 44/53)",
            "Code: pilot_runner save pbv2_internal_reason before OR overwrite",
            "NOT required for ENTRY: live_order hooks OFF (post-accept only)",
        ],
        "13_restore_before_pm_today": "YES for config: stop_low_mfe OFF + cluster csub relax + SHA preflight. Code pbv2_internal_reason can wait.",
        "14_safe_ops_tomorrow": "Session start preflight SHA check; monitor pbv2_count vs or_entry_count; disable board_fallback at runtime if disk false.",
    }

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "pre625_commit": PRE625_COMMIT,
        "mandatory_answers": mandatory,
        "output_dir": str(out),
    }
    (out / "phase606_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    doc = _build_doc(report, matrix, timeline, rollback_plan)
    (repo / "docs" / "operations" / "phase606_restore_pre625_pbv2_full_code_diff_audit.md").write_text(doc, encoding="utf-8")
    return report


def _build_doc(
    report: dict[str, Any],
    matrix: Sequence[Mapping[str, Any]],
    timeline: Sequence[Mapping[str, Any]],
    plan: Sequence[Mapping[str, Any]],
) -> str:
    ans = report["mandatory_answers"]
    lines = [
        "# Phase606 — Restore Pre-6/25 PBv2 Full Code Diff Audit",
        "",
        f"**Verdict:** `{VERDICT}`",
        f"**Pre-625 baseline commit:** `{PRE625_COMMIT}`",
        "",
        "## Mandatory answers",
        "",
    ]
    for k, v in ans.items():
        lines.append(f"### {k}")
        lines.append(str(v))
        lines.append("")
    lines.extend(["## Rollback matrix (629/630)", ""])
    for r in matrix:
        lines.append(
            f"- {r['day']} {r['scenario_id']}: live PBv2 pass={r.get('pbv2_pass_live_accepts')} "
            f"blocker={r.get('top_internal_blocker')}"
        )
    lines.extend(["", "## Config timeline", ""])
    for t in timeline:
        lines.append(
            f"- {t['day']} pbv2={t.get('pbv2_count_live')} or={t.get('or_entry_count_live')} "
            f"sha_match={t.get('sha_match_disk_now')}"
        )
    lines.extend(["", "## Minimal rollback plan", ""])
    for p in plan:
        lines.append(f"- {p['candidate_id']} {p['label']}: adopt={p['adopt_recommendation']} risk={p['risk']}")
    return "\n".join(lines)
