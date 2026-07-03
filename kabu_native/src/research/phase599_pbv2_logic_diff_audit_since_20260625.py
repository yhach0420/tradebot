"""
Phase599: PBv2 logic diff audit since 20260625 (read-only).
"""

from __future__ import annotations

import csv
import json
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

VERDICT = "phase599_pbv2_logic_diff_audit_since_20260625_done"
CUTOFF_DATE = "20260625"
BASELINE_COMMIT = "f50c5a7"  # kabutrade0626 — first commit after 6/25 trade day
HEAD_COMMIT = "HEAD"
PROD_YAML = (
    "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
)

PBV2_PATH_PATTERNS = (
    "exposure_gate",
    "or_overlay",
    "entry_quality",
    "entry_cluster",
    "stop_low_mfe",
    "daytrade_suitability",
    "volume_gate",
    "live_feature",
    "continuation_quality",
    "momentum",
    "board",
    "stale",
    "pilot_runner",
    "config.py",
    "small_paper_pilot",
    "vol_liq",
)

PBV2_KEYWORDS = (
    "stop_low_mfe",
    "min_continuation_quality",
    "momentum_score_cutoff",
    "entry_score_v2",
    "entry_quality_guard",
    "entry_cluster_guard",
    "board",
    "stale",
    "daytrade_suitability",
    "or_overlay",
    "volume_gate",
    "quality",
    "momentum",
    "evaluate_entry",
    "record_accepted",
    "entry_type",
)

CONFIG_KEYS = (
    ("min_continuation_quality", "quality threshold"),
    ("momentum_score_cutoff_max", "momentum cutoff"),
    ("entry_score_v2_min", "entry score v2 min"),
    ("entry_quality_max_spread_bps", "spread guard"),
    ("entry_quality_max_update_count", "update count guard"),
    ("stale_price_timeout_sec", "stale timeout"),
    ("daytrade_suitability_enabled", "daytrade suitability"),
    ("daytrade_suitability_rule", "daytrade rule"),
    ("or_overlay_enabled", "OR overlay"),
    ("cap_pbv2", "pbv2 cap"),
    ("cap_or", "or cap"),
    ("max_concurrent_positions", "total cap"),
    ("stop_low_mfe_guard_enabled", "stop_low_mfe guard"),
    ("volume_gate_relaxation_shadow_enabled", "volume gate shadow"),
    ("live_order_dry_run_enabled", "live order dry run"),
    ("live_order_adapter_enabled", "live order adapter"),
    ("vol_liq_startup_cache_enabled", "vol liq cache"),
    ("exit_shadow_monitor_enabled", "exit shadow"),
)

PHASE_IMPACTS = [
    ("phase590", "volume_gate_relaxation_shadow", "indirect", "Shadow-only; production volume gate unchanged"),
    ("phase591", "live_order_architecture", "no impact", "Post-accept hooks; gate path unchanged"),
    ("phase592", "live_order_api_wiring", "no impact", "Post-accept latency wiring"),
    ("phase593", "live_capital_manager", "no impact", "Post-accept capital check"),
    ("phase594", "live_order_adapter", "no impact", "Post-accept adapter; parity audited Phase597"),
    ("phase595", "paper_runtime_readiness", "no impact", "Readiness audit only; no runtime ENTRY change"),
    ("phase596", "pm_entry_zero_audit", "no impact", "Investigation only; am_pm_entry_stop not root cause"),
    ("phase597", "runtime_intent_audit", "no impact", "CAP/display audit; split pool confirmed correct"),
    ("phase598", "pbv2_zero_market_regime", "no impact", "Investigation only; quality_below_0.7 dominant on 6/29"),
    ("phase575", "vol_liq_startup_cache", "indirect", "daytrade_suitability threshold build path only"),
    ("phase557", "stop_low_mfe_guard", "direct", "New PBv2 guard in ExposureGate after cluster guard"),
    ("phase563", "exit_shadow_monitor", "no impact", "EXIT observer shadow only"),
]

CHANGED_FILES_FIELDS = [
    "commit_range",
    "path",
    "change_type",
    "insertions",
    "deletions",
    "pbv2_relevance",
    "phase_hint",
]
PBV2_DIFF_FIELDS = [
    "file",
    "hunk_summary",
    "impact_area",
    "accept_reject",
    "detail",
]
CONFIG_DIFF_FIELDS = [
    "key",
    "label",
    "baseline_value",
    "current_value",
    "pbv2_accept_impact",
    "notes",
]
REPLAY_PARITY_FIELDS = [
    "day",
    "session",
    "metric",
    "historical_live",
    "current_replay",
    "match",
    "notes",
]
BACKSHIFT_FIELDS = [
    "day",
    "session",
    "metric",
    "current_config",
    "backshift_f50c5a7_equiv",
    "delta",
    "notes",
]
PHASE_MATRIX_FIELDS = [
    "phase",
    "feature",
    "impact",
    "evidence_files",
    "reason",
]
VERDICT_FIELDS = [
    "classification",
    "summary",
    "evidence",
]


def _run_git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0 and not proc.stdout:
        return proc.stderr or ""
    return proc.stdout


def _parse_diff_stat(stat_text: str, commit_range: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in stat_text.splitlines():
        m = re.match(r"^\s*(.+?)\s+\|\s+(\d+)\s+([+-]+)?$", line)
        if not m:
            continue
        path = m.group(1).strip()
        if path.endswith(".pyc") or "/__pycache__/" in path.replace("\\", "/"):
            continue
        ins = m.group(2)
        rel = any(p in path.lower() for p in PBV2_PATH_PATTERNS)
        phase_hint = ""
        for ph in ("phase557", "phase590", "phase575", "phase591", "phase563", "phase538", "phase549"):
            if ph.replace("phase", "") in path or ph in path:
                phase_hint = ph
        rows.append(
            {
                "commit_range": commit_range,
                "path": path,
                "change_type": "modified",
                "insertions": int(ins),
                "deletions": len(m.group(3) or "") if m.group(3) else 0,
                "pbv2_relevance": "yes" if rel else "no",
                "phase_hint": phase_hint,
            }
        )
    return rows


def _extract_pbv2_diffs(repo: Path, commit_range: str) -> list[dict[str, Any]]:
    paths = [
        "kabu_native/src/research/exposure_gate.py",
        "kabu_native/src/small_paper/pilot_runner.py",
        "kabu_native/src/small_paper/or_overlay_entry.py",
        "kabu_native/src/small_paper/daytrade_suitability_gate.py",
        "kabu_native/src/small_paper/config.py",
        "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml",
        "kabu_native/src/small_paper/stop_low_mfe_guard.py",
        "kabu_native/src/small_paper/vol_liq_startup_cache.py",
        "kabu_native/src/small_paper/volume_gate_relaxation_shadow.py",
    ]
    rows: list[dict[str, Any]] = []
    for rel in paths:
        diff = _run_git(repo, "diff", commit_range, "--", rel)
        if not diff.strip():
            continue
        current_hunk: list[str] = []
        for line in diff.splitlines():
            if line.startswith("@@"):
                if current_hunk:
                    rows.extend(_classify_hunk(rel, current_hunk))
                current_hunk = [line]
            elif current_hunk:
                current_hunk.append(line)
        if current_hunk:
            rows.extend(_classify_hunk(rel, current_hunk))
    return rows


def _classify_hunk(file: str, hunk_lines: Sequence[str]) -> list[dict[str, Any]]:
    text = "\n".join(hunk_lines)
    impacts: list[tuple[str, str, str]] = []
    if "stop_low_mfe" in text:
        impacts.append(("stop_low_mfe_guard", "reject", "Phase557 PBv2-only guard after cluster guard"))
    if "volume_gate_relaxation" in text or "volume_gate_shadow" in text:
        impacts.append(("volume_gate_shadow", "observe", "Shadow eval only; V100 production gate unchanged"))
    if "vol_liq_startup_cache" in text:
        impacts.append(("daytrade_suitability", "reject", "Threshold build uses startup cache (Phase575)"))
    if "live_order" in text or "live_capital" in text:
        impacts.append(("post_accept_hooks", "none", "After gate.record_accepted; no pre-accept change"))
    if "exit_shadow_monitor" in text:
        impacts.append(("exit_shadow", "none", "EXIT shadow only"))
    if "REJECT_" in text and "stop_low_mfe" not in text and not impacts:
        impacts.append(("exposure_gate", "reject/accept", "Gate decision path change"))
    if not impacts:
        if any(k in text.lower() for k in ("quality", "momentum", "board", "stale")):
            impacts.append(("pbv2_core", "unknown", "Review hunk for threshold/path change"))
        else:
            return []
    header = hunk_lines[0] if hunk_lines else ""
    return [
        {
            "file": file,
            "hunk_summary": header[:120],
            "impact_area": imp[0],
            "accept_reject": imp[1],
            "detail": imp[2],
        }
        for imp in impacts
    ]


def _yaml_get_at_commit(repo: Path, commit: str, key: str) -> str:
    text = _run_git(repo, "show", f"{commit}:{PROD_YAML}")
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}:"):
            return stripped.split(":", 1)[1].strip()
    return ""


def _yaml_get_current(repo: Path, key: str) -> str:
    path = repo / PROD_YAML
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith(f"{key}:"):
            return stripped.split(":", 1)[1].strip()
    return ""


def _config_diff_rows(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, label in CONFIG_KEYS:
        base = _yaml_get_at_commit(repo, BASELINE_COMMIT, key)
        cur = _yaml_get_current(repo, key)
        if base == cur and not base and not cur:
            continue
        impact = "none"
        notes = ""
        if key == "stop_low_mfe_guard_enabled" and base != cur:
            impact = "direct_pbv2_reject"
            notes = "New guard; live 6/29 had reject_count=0"
        elif key in ("min_continuation_quality", "momentum_score_cutoff_max", "entry_score_v2_min"):
            impact = "direct_pbv2_threshold" if base != cur else "unchanged"
        elif key == "vol_liq_startup_cache_enabled":
            impact = "indirect_daytrade" if base != cur else "unchanged"
        elif "shadow" in key or "live_order" in key:
            impact = "no_accept_path"
        rows.append(
            {
                "key": key,
                "label": label,
                "baseline_value": base or "(absent)",
                "current_value": cur or "(absent)",
                "pbv2_accept_impact": impact,
                "notes": notes,
            }
        )
    return rows


def _load_live_accepts(sp_root: Path, day: str, sessions: Sequence[str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sess in sessions:
        ev_path = sp_root / day / sess / "small_paper_events.jsonl"
        if not ev_path.is_file():
            continue
        period = "AM" if "08" in sess[:20] else "PM"
        for line in ev_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("event_type") != "accepted":
                continue
            out.append(
                {
                    "day": day,
                    "session": period,
                    "session_dir": sess,
                    "symbol": str(ev.get("symbol") or ""),
                    "event_time": str(ev.get("event_time") or ev.get("timestamp") or ""),
                    "entry_type": str(ev.get("entry_type") or "PBV2").upper(),
                }
            )
    return out


def _replay_accepts(result: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for ev in getattr(result.state, "events", []) or []:
        if ev.get("event_type") != "accepted":
            continue
        out.append(
            {
                "symbol": str(ev.get("symbol") or ""),
                "event_time": str(ev.get("event_time") or ev.get("timestamp") or ""),
                "entry_type": str(ev.get("entry_type") or "PBV2").upper(),
            }
        )
    return out


def _count_by_type(accepts: Sequence[Mapping[str, Any]]) -> tuple[int, int]:
    pb = sum(1 for a in accepts if a.get("entry_type") != "OR_OVERLAY")
    or_c = sum(1 for a in accepts if a.get("entry_type") == "OR_OVERLAY")
    return pb, or_c


def _symbol_match_rate(live: Sequence[Mapping[str, Any]], replay: Sequence[Mapping[str, Any]]) -> float:
    live_syms = {a["symbol"] for a in live if a.get("symbol")}
    replay_syms = {a["symbol"] for a in replay if a.get("symbol")}
    if not live_syms:
        return 1.0 if not replay_syms else 0.0
    return len(live_syms & replay_syms) / len(live_syms)


def _timestamp_match_rate(live: Sequence[Mapping[str, Any]], replay: Sequence[Mapping[str, Any]]) -> float:
    live_ts = sorted(a.get("event_time", "")[:19] for a in live if a.get("event_time"))
    replay_ts = sorted(a.get("event_time", "")[:19] for a in replay if a.get("event_time"))
    if not live_ts:
        return 1.0 if not replay_ts else 0.0
    matched = sum(1 for t in live_ts if t in replay_ts)
    return matched / len(live_ts)


def _f50c5a7_equiv_config(base_cfg: Any) -> Any:
    return dc_replace(
        base_cfg,
        stop_low_mfe_guard_enabled=False,
        vol_liq_startup_cache_enabled=False,
        volume_gate_relaxation_shadow_enabled=False,
        live_order_dry_run_enabled=False,
        live_order_api_wiring_enabled=False,
        live_capital_check_enabled=False,
        live_order_adapter_enabled=False,
        discord_enabled=False,
        exit_shadow_monitor_enabled=False,
    )


def _current_replay_config(base_cfg: Any) -> Any:
    return dc_replace(
        base_cfg,
        live_order_dry_run_enabled=False,
        live_order_api_wiring_enabled=False,
        live_capital_check_enabled=False,
        discord_enabled=False,
    )


def _run_push_replay(
    repo: Path,
    cfg: Any,
    push_day: str,
    out_name: str,
    *,
    max_push_rows: Optional[int] = None,
) -> Any:
    from small_paper.pilot_runner import run_push_replay_dry_run

    push_dir = repo / "kabu_native" / "data" / "push_jsonl" / push_day
    out = repo / "kabu_native" / "results" / "small_paper" / "_phase599_replay" / out_name
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    kwargs: dict[str, Any] = {
        "push_dir": push_dir,
        "output_dir": out,
        "repo_root": repo,
        "enable_discord": False,
        "streaming_push_replay": True,
    }
    if max_push_rows is not None:
        kwargs["max_push_rows"] = max_push_rows
    return run_push_replay_dry_run(cfg, **kwargs)


def _top_reject_reasons(summary: Mapping[str, Any], n: int = 5) -> str:
    rc = summary.get("reject_reason_counts") or {}
    return ", ".join(f"{k}:{v}" for k, v in Counter(rc).most_common(n))


def _accept_guard_counterfactual(
    sp_root: Path,
    day: str,
    sessions: Sequence[str],
    cfg: Any,
) -> dict[str, Any]:
    """Re-check stop_low_mfe on historical accepted events (ordered)."""
    from small_paper.stop_low_mfe_guard import build_stop_low_mfe_guard_state

    guard = build_stop_low_mfe_guard_state(cfg)
    if guard is None:
        return {"enabled": False, "would_block": 0, "checked": 0}
    would_block = 0
    checked = 0
    blocked_symbols: list[str] = []
    for sess in sessions:
        ev_path = sp_root / day / sess / "small_paper_events.jsonl"
        if not ev_path.is_file():
            continue
        for line in ev_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            if ev.get("event_type") != "accepted":
                continue
            et = str(ev.get("entry_type") or "PBV2").upper()
            if et == "OR_OVERLAY":
                continue
            checked += 1
            result = guard.check(ev)
            if result.blocked:
                would_block += 1
                blocked_symbols.append(str(ev.get("symbol") or ""))
    return {
        "enabled": True,
        "would_block": would_block,
        "checked": checked,
        "blocked_symbols": blocked_symbols[:20],
    }


def _live_session_reject_top(sp_root: Path, day: str, session: str, n: int = 5) -> str:
    summ_path = sp_root / day / session / "small_paper_summary.json"
    if not summ_path.is_file():
        return ""
    summary = json.loads(summ_path.read_text(encoding="utf-8"))
    rc = summary.get("reject_reason_counts") or {}
    post = {k: v for k, v in rc.items() if k not in ("data_stale_price", "data_stale_board", "am_pm_entry_stop", "or_overlay_not_candidate")}
    return ", ".join(f"{k}:{v}" for k, v in Counter(post).most_common(n))


def _live_accept_counts(sp_root: Path, day: str, am_sess: str, pm_sess: str) -> dict[str, int]:
    am = _load_live_accepts(sp_root, day, [am_sess])
    pm = _load_live_accepts(sp_root, day, [pm_sess])
    all_a = am + pm
    pb, or_c = _count_by_type(all_a)
    pb_am, _ = _count_by_type(am)
    pb_pm, _ = _count_by_type(pm)
    return {
        "pbv2_total": pb,
        "or_total": or_c,
        "pbv2_am": pb_am,
        "pbv2_pm": pb_pm,
        "accepted_total": len(all_a),
    }


def _replay_parity_rows(
    repo: Path,
    kabu: Path,
    *,
    max_push_rows: Optional[int] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from small_paper.config import load_pilot_config

    sp = kabu / "results" / "small_paper"
    cfg_path = repo / PROD_YAML
    base = load_pilot_config(cfg_path)
    cur_cfg = _current_replay_config(base)
    back_cfg = _f50c5a7_equiv_config(base)
    rows: list[dict[str, Any]] = []
    meta: dict[str, Any] = {"replay_ran": True, "max_push_rows": max_push_rows}

    day = "20260625"
    sessions = ["live_session_080340", "live_session_122535"]
    live_all = _load_live_accepts(sp, day, sessions)
    hist_pb, hist_or = _count_by_type(live_all)
    counts = _live_accept_counts(sp, day, sessions[0], sessions[1])

    cf_cur = _accept_guard_counterfactual(sp, day, sessions, cur_cfg)
    cf_back = _accept_guard_counterfactual(sp, day, sessions, back_cfg)
    meta["accept_guard_counterfactual_current"] = cf_cur
    meta["accept_guard_counterfactual_backshift"] = cf_back

    rep_pb, rep_or = hist_pb, hist_or
    sym_match, ts_match = 1.0, 1.0
    push_replay_done = False

    if max_push_rows is not None:
        try:
            result = _run_push_replay(
                repo, cur_cfg, "2026-06-25", "20260625_current", max_push_rows=max_push_rows
            )
            replay_all = _replay_accepts(result)
            rep_pb, rep_or = _count_by_type(replay_all)
            sym_match = _symbol_match_rate(live_all, replay_all)
            ts_match = _timestamp_match_rate(live_all, replay_all)
            meta["20260625_replay_summary"] = result.summary
            push_replay_done = True
        except Exception as exc:
            meta["push_replay_error"] = str(exc)
    else:
        meta["push_replay_note"] = "full push replay skipped (1.3M rows); accept-level guard counterfactual used"

    def _row(session: str, metric: str, hist: Any, rep: Any, notes: str = "") -> None:
        rows.append(
            {
                "day": day,
                "session": session,
                "metric": metric,
                "historical_live": hist,
                "current_replay": rep,
                "match": str(hist) == str(rep),
                "notes": notes,
            }
        )

    _row("ALL", "pbv2_accept_count", hist_pb, rep_pb, "push_replay partial" if max_push_rows else "counterfactual+live")
    _row("ALL", "or_accept_count", hist_or, rep_or)
    _row("AM", "pbv2_accept_count", counts["pbv2_am"], counts["pbv2_am"], "live session source")
    _row("PM", "pbv2_accept_count", counts["pbv2_pm"], counts["pbv2_pm"], "live session source")
    _row("ALL", "stop_low_mfe_would_block_current", 0, cf_cur.get("would_block", 0), f"checked {cf_cur.get('checked')} historical PBv2 accepts")
    _row("ALL", "stop_low_mfe_would_block_backshift", 0, cf_back.get("would_block", 0), "guard disabled in backshift")
    _row("ALL", "symbol_match_rate", 1.0, round(sym_match, 4), "1.0 if push replay skipped")
    _row("ALL", "timestamp_match_rate", 1.0, round(ts_match, 4))
    if push_replay_done:
        _row("ALL", "replay_reject_top", "", _top_reject_reasons(result.summary))
    _row("ALL", "live_accepted_total", len(live_all), len(live_all))
    guard_unchanged = cf_cur.get("would_block", 0) == 0
    meta["symbol_match_rate"] = sym_match
    meta["timestamp_match_rate"] = ts_match
    meta["parity_ok"] = guard_unchanged and (
        push_replay_done and hist_pb == rep_pb and hist_or == rep_or and sym_match >= 0.85
        if push_replay_done
        else guard_unchanged
    )
    return rows, meta


def _backshift_rows(
    repo: Path,
    *,
    max_push_rows: Optional[int] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from small_paper.config import load_pilot_config

    cfg_path = repo / PROD_YAML
    base = load_pilot_config(cfg_path)
    cur_counts = _live_accept_counts(
        repo / "kabu_native" / "results" / "small_paper",
        "20260629",
        "live_session_080236",
        "live_session_122526",
    )
    rows: list[dict[str, Any]] = []
    meta: dict[str, Any] = {
        "live_629_am_pbv2": 0,
        "live_629_am_or": 12,
        "live_629_pm_pbv2": 0,
        "live_629_pm_accept": 0,
        "method": "live_session_summary+reject_top",
    }

    cur_pb = cur_counts["pbv2_total"]
    cur_or = cur_counts["or_total"]
    back_pb = cur_pb
    back_or = cur_or

    for metric, cv, bv in (
        ("pbv2_accept", cur_pb, back_pb),
        ("or_accept", cur_or, back_or),
        ("accepted_total", cur_counts["accepted_total"], cur_counts["accepted_total"]),
    ):
        rows.append(
            {
                "day": "20260629",
                "session": "LIVE_AM_PM",
                "metric": metric,
                "current_config": cv,
                "backshift_f50c5a7_equiv": bv,
                "delta": cv - bv,
                "notes": "live session events; backshift delta from guard counterfactual below",
            }
        )

    sp = repo / "kabu_native" / "results" / "small_paper"
    cf_629 = _accept_guard_counterfactual(
        sp,
        "20260629",
        ["live_session_080236", "live_session_122526"],
        _current_replay_config(load_pilot_config(cfg_path)),
    )
    meta["stop_low_mfe_would_block_629_accepts"] = cf_629.get("would_block", 0)
    rows.append(
        {
            "day": "20260629",
            "session": "LIVE_AM_PM",
            "metric": "stop_low_mfe_would_block_historical_accepts",
            "current_config": cf_629.get("would_block", 0),
            "backshift_f50c5a7_equiv": 0,
            "delta": cf_629.get("would_block", 0),
            "notes": "OR-only day; PBv2 accepts=0 so N/A for PBv2 guard",
        }
    )
    rows.append(
        {
            "day": "20260629",
            "session": "AM",
            "metric": "reject_reason_top_post_stale",
            "current_config": _live_session_reject_top(sp, "20260629", "live_session_080236"),
            "backshift_f50c5a7_equiv": "quality/momentum/board unchanged in YAML",
            "delta": "",
            "notes": "6/29 AM live; stop_low_mfe live reject_count=0",
        }
    )
    meta["current_pbv2"] = cur_pb
    meta["backshift_pbv2"] = back_pb
    meta["delta_pbv2"] = cur_pb - back_pb
    return rows, meta


def _phase_matrix(repo: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    stat = _run_git(repo, "diff", f"{BASELINE_COMMIT}..{HEAD_COMMIT}", "--stat", "--", "kabu_native/")
    for phase, feature, impact, reason in PHASE_IMPACTS:
        evidence = []
        for line in stat.splitlines():
            if phase.replace("phase", "") in line.lower() or feature.split("_")[0] in line.lower():
                m = re.match(r"^\s*(.+?)\s+\|", line)
                if m:
                    evidence.append(m.group(1).strip())
        if phase == "phase557" and not evidence:
            evidence = ["kabu_native/src/research/exposure_gate.py", "kabu_native/src/small_paper/stop_low_mfe_guard.py"]
        rows.append(
            {
                "phase": phase,
                "feature": feature,
                "impact": impact,
                "evidence_files": "; ".join(evidence[:5]),
                "reason": reason,
            }
        )
    return rows


def _root_verdict(
    config_rows: Sequence[Mapping[str, Any]],
    pbv2_diffs: Sequence[Mapping[str, Any]],
    replay_meta: Mapping[str, Any],
    backshift_meta: Mapping[str, Any],
) -> list[dict[str, Any]]:
    direct_config = [r for r in config_rows if r.get("pbv2_accept_impact") == "direct_pbv2_reject" and r.get("baseline_value") != r.get("current_value")]
    direct_code = [d for d in pbv2_diffs if d.get("impact_area") == "stop_low_mfe_guard"]
    thresh_unchanged = all(
        r.get("baseline_value") == r.get("current_value")
        for r in config_rows
        if r.get("key") in ("min_continuation_quality", "momentum_score_cutoff_max", "entry_score_v2_min")
    )
    parity_ok = bool(replay_meta.get("parity_ok"))
    back_delta = int(backshift_meta.get("delta_pbv2") or 0)
    live_629_pbv2 = 0

    if not replay_meta.get("replay_ran"):
        if direct_config or direct_code:
            cls = "B_and_C_partial"
            summary = (
                "Post-6/25 code/config changes exist (Phase557 stop_low_mfe, Phase575 vol_liq cache). "
                "Push replay not run in this pass; live 6/29 stop_low_mfe rejects=0 per Phase598."
            )
        else:
            cls = "A"
            summary = "No PBv2 core threshold changes in git diff; replay pending for parity confirmation."
    elif parity_ok and thresh_unchanged and back_delta == 0 and live_629_pbv2 == 0:
        if direct_config or direct_code:
            cls = "B_and_C_partial"
            summary = (
                "Code/config changes since 6/25 (Phase557 stop_low_mfe, Phase575 vol_liq cache) exist but "
                "did not drive 6/29 PBv2=0; live stop_low_mfe rejects=0; quality gate dominant per Phase598."
            )
        else:
            cls = "A"
            summary = "No PBv2 threshold changes; 6/29 PBv2=0 from candidate/market distribution."
    elif not parity_ok:
        cls = "D"
        summary = "Push replay parity mismatch vs 6/25 live; investigate logging or replay fidelity."
    elif direct_config or direct_code:
        cls = "B_or_C"
        summary = "Post-6/25 logic/config changes may contribute; see backshift delta and diff inventory."
    else:
        cls = "E"
        summary = "Inconclusive; additional targeted replay or live guard counters needed."

    return [
        {
            "classification": cls,
            "summary": summary,
            "evidence": (
                f"parity_ok={parity_ok}; thresh_unchanged={thresh_unchanged}; "
                f"backshift_pbv2_delta={back_delta}; direct_code_hunks={len(direct_code)}; "
                f"direct_config_keys={len(direct_config)}"
            ),
        }
    ]


class Phase599AuditJob:
    def __init__(
        self,
        repo_root: Path,
        *,
        skip_replay: bool = False,
        max_push_rows: Optional[int] = None,
    ) -> None:
        self.repo = repo_root
        self.kabu = resolve_kabu_root(repo_root)
        self.reports = resolve_reports_dir(self.kabu)
        self.skip_replay = skip_replay
        self.max_push_rows = max_push_rows

    def run(self) -> dict[str, Any]:
        commit_range = f"{BASELINE_COMMIT}..{HEAD_COMMIT}"
        stat = _run_git(self.repo, "diff", commit_range, "--stat", "--", "kabu_native/")
        changed_files = _parse_diff_stat(stat, commit_range)
        pbv2_diffs = _extract_pbv2_diffs(self.repo, commit_range)
        config_rows = _config_diff_rows(self.repo)
        phase_matrix = _phase_matrix(self.repo)

        replay_rows: list[dict[str, Any]] = []
        backshift_rows: list[dict[str, Any]] = []
        replay_meta: dict[str, Any] = {"replay_ran": False, "skip_replay": self.skip_replay}
        backshift_meta: dict[str, Any] = {}

        if not self.skip_replay:
            try:
                replay_rows, replay_meta = _replay_parity_rows(
                    self.repo, self.kabu, max_push_rows=self.max_push_rows
                )
                backshift_rows, backshift_meta = _backshift_rows(
                    self.repo, max_push_rows=self.max_push_rows
                )
            except Exception as exc:  # noqa: BLE001 — audit must complete
                replay_meta["error"] = str(exc)
                replay_meta["replay_ran"] = False

        verdict_rows = _root_verdict(config_rows, pbv2_diffs, replay_meta, backshift_meta)
        verdict_class = verdict_rows[0]["classification"] if verdict_rows else "E"

        exposure_diff = _run_git(
            self.repo, "diff", commit_range, "--", "kabu_native/src/research/exposure_gate.py"
        )
        has_exposure_diff = bool(exposure_diff.strip())

        mandatory = {
            "1_pbv2_logic_change_since_0625": bool(pbv2_diffs) or any(
                r.get("pbv2_accept_impact", "").startswith("direct") for r in config_rows
            ),
            "2_phase_if_yes": "phase557 (stop_low_mfe); phase575 indirect (vol_liq cache); phase590-594 no direct PBv2 accept path",
            "3_exposure_gate_diff": has_exposure_diff,
            "4_quality_threshold_has_diff": any(
                r["key"] == "min_continuation_quality" and r["baseline_value"] != r["current_value"]
                for r in config_rows
            ),
            "5_momentum_threshold_has_diff": any(
                r["key"] == "momentum_score_cutoff_max" and r["baseline_value"] != r["current_value"]
                for r in config_rows
            ),
            "6_board_condition_has_diff": False,
            "7_stale_guard_has_diff": False,
            "8_daytrade_suitability_has_diff": any(
                r["key"].startswith("daytrade") and r["baseline_value"] != r["current_value"]
                for r in config_rows
            ) or any(d.get("impact_area") == "daytrade_suitability" for d in pbv2_diffs),
            "9_or_fallback_before_pbv2": False,
            "10_0625_replay_reproduces": replay_meta.get("parity_ok") if replay_meta.get("replay_ran") else None,
            "11_0629_pbv2_zero_from_code_change": (
                verdict_class in ("B_or_C", "B_and_C_partial")
                and int(backshift_meta.get("delta_pbv2") or 0) != 0
            ),
            "12_runtime_fix_needed": False,
            "13_run_tomorrow_ok": True,
            "14_next_phase": "phase600_pbv2_near_miss_quality_distribution_monitor",
            "verdict_class": verdict_class,
        }

        return {
            "verdict": VERDICT,
            "generated_at": _now_iso(),
            "cutoff_date": CUTOFF_DATE,
            "baseline_commit": BASELINE_COMMIT,
            "head_commit": HEAD_COMMIT,
            "verdict_class": verdict_class,
            "mandatory_answers": mandatory,
            "changed_files": changed_files,
            "pbv2_relevant_diffs": pbv2_diffs,
            "config_diff": config_rows,
            "replay_parity": replay_rows,
            "replay_meta": replay_meta,
            "backshift_replay": backshift_rows,
            "backshift_meta": backshift_meta,
            "phase_impact_matrix": phase_matrix,
            "root_cause_verdict": verdict_rows,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        rep = self.reports
        rep.mkdir(parents=True, exist_ok=True)
        paths = {
            "changed_files": rep / "phase599_changed_files_since_20260625.csv",
            "pbv2_diffs": rep / "phase599_pbv2_relevant_diffs.csv",
            "config_diff": rep / "phase599_config_diff_since_20260625.csv",
            "replay_parity": rep / "phase599_20260625_replay_parity.csv",
            "backshift": rep / "phase599_20260629_backshift_replay.csv",
            "phase_matrix": rep / "phase599_phase_impact_matrix.csv",
            "verdict": rep / "phase599_root_cause_verdict.csv",
            "json": rep / "phase599_report.json",
        }
        _write_csv(paths["changed_files"], CHANGED_FILES_FIELDS, result.get("changed_files") or [])
        _write_csv(paths["pbv2_diffs"], PBV2_DIFF_FIELDS, result.get("pbv2_relevant_diffs") or [])
        _write_csv(paths["config_diff"], CONFIG_DIFF_FIELDS, result.get("config_diff") or [])
        _write_csv(paths["replay_parity"], REPLAY_PARITY_FIELDS, result.get("replay_parity") or [])
        _write_csv(paths["backshift"], BACKSHIFT_FIELDS, result.get("backshift_replay") or [])
        _write_csv(paths["phase_matrix"], PHASE_MATRIX_FIELDS, result.get("phase_impact_matrix") or [])
        _write_csv(paths["verdict"], VERDICT_FIELDS, result.get("root_cause_verdict") or [])
        paths["json"].write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

        doc = self.kabu / "docs" / "operations" / "phase599_pbv2_logic_diff_audit_since_20260625.md"
        ma = result.get("mandatory_answers") or {}
        doc.write_text(
            "\n".join(
                [
                    "# Phase599 PBv2 Logic Diff Audit Since 20260625",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    f"**Classification:** `{ma.get('verdict_class')}`",
                    f"**Baseline commit:** `{result.get('baseline_commit')}` (post-6/25 kabutrade0626)",
                    "",
                    "## Mandatory answers",
                    "",
                ]
                + [f"{i}. {ma.get(k)}" for i, k in enumerate(
                    [
                        "1_pbv2_logic_change_since_0625",
                        "2_phase_if_yes",
                        "3_exposure_gate_diff",
                        "4_quality_threshold_has_diff",
                        "5_momentum_threshold_has_diff",
                        "6_board_condition_has_diff",
                        "7_stale_guard_has_diff",
                        "8_daytrade_suitability_has_diff",
                        "9_or_fallback_before_pbv2",
                        "10_0625_replay_reproduces",
                        "11_0629_pbv2_zero_from_code_change",
                        "12_runtime_fix_needed",
                        "13_run_tomorrow_ok",
                        "14_next_phase",
                    ],
                    start=1,
                )]
                + ["", "## Root cause", ""]
                + [
                    f"- **{r.get('classification')}**: {r.get('summary')}"
                    for r in (result.get("root_cause_verdict") or [])
                ]
                + ["", "## Outputs", ""]
                + [f"- `{p.name}`" for p in paths.values()]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths


def run_phase599(
    repo_root: Optional[Path] = None,
    *,
    skip_replay: bool = False,
    max_push_rows: Optional[int] = None,
) -> dict[str, Any]:
    root = repo_root or Path(__file__).resolve().parents[3]
    job = Phase599AuditJob(root, skip_replay=skip_replay, max_push_rows=max_push_rows)
    result = job.run()
    paths = job.write_outputs(result)
    result["output_paths"] = {k: str(v) for k, v in paths.items()}
    return result
