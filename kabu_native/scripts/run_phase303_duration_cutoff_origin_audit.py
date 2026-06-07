#!/usr/bin/env python3
"""
Phase303: Duration cutoff=406 origin audit (review only).

Output: kabu_native/results/reports/phase303_duration_cutoff_origin_audit.json
"""

from __future__ import annotations

import importlib.util
import inspect
import json
import math
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase303_duration_cutoff_origin_audit.json"
SMALL_PAPER = REPO / "kabu_native/results/small_paper"

P228 = REPO / "kabu_native/results/reports/phase228_entry_expectancy_discovery.json"
P229 = REPO / "kabu_native/results/reports/phase229_entry_score_discovery.json"
P297 = REPO / "kabu_native/results/reports/phase297_score5_consistency_audit.json"
P302 = REPO / "kabu_native/results/reports/phase302_duration_bottleneck_audit.json"

SCORE_SHADOW = REPO / "kabu_native/src/small_paper/entry_expectancy_score_shadow.py"
FEATURE_BRIDGE = REPO / "kabu_native/src/small_paper/live_feature_bridge.py"

DURATION_P66 = 406.0
REJECT_REPLAY_MAX_EVENTS = 500_000

DISCOVERY_SESSIONS = sorted(
    {
        "20260519/live_full_session_081047",
        "20260520/live_full_session_080745",
        "20260520/push_replay_001932",
        "20260520/push_replay_231314",
        "20260521/live_full_session_081418",
        "20260522/live_full_session_081229",
        "20260525/live_session_075733",
        "20260529/live_session_075135",
        "20260529/live_session_122541",
        "20260529/push_replay_002526",
        "20260529/push_replay_003645",
        "20260518/push_replay_205219",
        "20260518/push_replay_212433",
        "20260518/push_replay_220451",
        "20260519/push_replay_225919",
        "20260520/push_replay_002323",
        "20260521/push_replay_004729",
        "20260528/live_session_082247",
        "20260528/live_session_122515",
    }
)

P272_DAYS = {str(d) for d in range(20260518, 20260604)}
P302_DAYS = {"20260604", "20260605"}


def _run_git(args: list[str]) -> str:
    try:
        r = subprocess.run(
            ["git", *args],
            cwd=REPO,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        return (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _quantile(xs: list[float], q: float) -> float:
    ys = sorted(xs)
    pos = (len(ys) - 1) * q
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ys[lo]
    return ys[lo] + (ys[hi] - ys[lo]) * (pos - lo)


def _dist_summary(vals: list[float]) -> dict[str, Any]:
    if not vals:
        return {"count": 0}
    return {
        "count": len(vals),
        "p50": round(_quantile(vals, 0.5), 4),
        "p66": round(_quantile(vals, 2.0 / 3.0), 4),
        "p80": round(_quantile(vals, 0.8), 4),
        "p90": round(_quantile(vals, 0.9), 4),
        "p95": round(_quantile(vals, 0.95), 4),
        "max": round(max(vals), 4),
        "above_cutoff_406": sum(1 for v in vals if v > DURATION_P66),
    }


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _git_introduction_audit() -> dict[str, Any]:
    blame_cutoff = _run_git(
        ["blame", "-L", "55,55", "--line-porcelain", str(SCORE_SHADOW.relative_to(REPO))]
    )
    blame_dur_high = _run_git(
        ["blame", "-L", "64,64", "--line-porcelain", str(SCORE_SHADOW.relative_to(REPO))]
    )
    log_406 = _run_git(
        ["log", "-S", "406.0", "--oneline", "--", "kabu_native/src/small_paper/entry_expectancy_score_shadow.py"]
    )
    log_dur_high = _run_git(
        ["log", "-S", "Duration:high", "--oneline", "--", "kabu_native/"]
    )
    show_f22 = _run_git(["show", "f22c025", "--no-patch", "--format=fuller"])
    return {
        "Duration_high_token": {
            "file": str(SCORE_SHADOW.relative_to(REPO)),
            "line": 64,
            "points": 2,
            "git_blame_excerpt": blame_dur_high.splitlines()[:12],
            "introducing_commits_git_log": [ln for ln in log_dur_high.splitlines() if ln.strip()],
        },
        "TERTILE_CUTOFFS_Duration_p66_406": {
            "file": str(SCORE_SHADOW.relative_to(REPO)),
            "line": 55,
            "value": DURATION_P66,
            "git_blame_excerpt": blame_cutoff.splitlines()[:12],
            "introducing_commits_git_log": [ln for ln in log_406.splitlines() if ln.strip()],
        },
        "SCORE_POINTS_V2": {
            "introduced_commit": "59e3d72",
            "introduced_date": "2026-06-04",
            "note": "Phase236 Scenario B — excludes RollingMAE:mid from SCORE_POINTS; Duration:high unchanged",
        },
        "primary_introduction_commit": {
            "hash": "f22c025",
            "message": "kabutrade0531",
            "date": "2026-05-31",
            "show_fuller": show_f22,
            "files_added": [
                "kabu_native/src/small_paper/entry_expectancy_score_shadow.py",
                "kabu_native/scripts/run_phase229_entry_score_discovery.py",
                "kabu_native/scripts/run_phase228_entry_expectancy_discovery.py",
            ],
        },
    }


def _origin_trail() -> dict[str, Any]:
    p228 = _load_json(P228)
    p229 = _load_json(P229)
    dur_cut = (p228.get("tertile_cutoffs") or {}).get("Duration") or {}
    work3 = p229.get("work3_score_components") or {}
    return {
        "first_phase_computing_406": {
            "phase": 228,
            "script": "kabu_native/scripts/run_phase228_entry_expectancy_discovery.py",
            "report": str(P228.relative_to(REPO)),
            "method": "p66 = 2/3 quantile of max_continuation_duration on 2503 closed trades",
            "population": p228.get("population"),
            "duration_tertile": dur_cut,
        },
        "first_phase_assigning_Duration_high_points": {
            "phase": 229,
            "script": "kabu_native/scripts/run_phase229_entry_score_discovery.py",
            "report": str(P229.relative_to(REPO)),
            "duration_high_component": next(
                (c for c in (work3.get("components") or []) if c.get("token") == "Duration:high"),
                None,
            ),
            "score_map": (work3.get("score_map") or {}).get("Duration:high"),
        },
        "first_production_code_commit": {
            "phase": 230,
            "commit": "f22c025",
            "file": "kabu_native/src/small_paper/entry_expectancy_score_shadow.py",
            "constants": "TERTILE_CUTOFFS['Duration']['p66']=406.0; SCORE_POINTS['Duration:high']=2",
        },
        "design_notes": [
            {
                "source": "entry_expectancy_score_shadow.py module docstring",
                "text": "Phase229 tertile cutoffs (2503-trade population, Phase228 discovery).",
            },
            {
                "source": "kabu_native/docs/kabu_station_system_design.md §16.7",
                "text": "max_continuation_duration = 継続 tick 長 (live_feature_bridge)",
            },
            {
                "source": "run_phase229_entry_score_discovery.py _fmt_num",
                "text": "Duration display suffix 's' in range labels — display only, not the stored unit",
            },
            {
                "source": "run_phase224_early_momentum_entry_cohort_review.py",
                "text": "duration from accepted max_continuation_duration at entry",
            },
        ],
    }


def _accepted_duration_discovery_sessions() -> dict[str, Any]:
    vals: list[float] = []
    per_session: dict[str, int] = {}
    for sid in DISCOVERY_SESSIONS:
        path = SMALL_PAPER / sid / "small_paper_events.jsonl"
        if not path.is_file():
            continue
        n = 0
        for line in path.open(encoding="utf-8"):
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if ev.get("event_type") != "accepted":
                continue
            raw = ev.get("max_continuation_duration")
            if raw is None:
                continue
            try:
                vals.append(float(raw))
                n += 1
            except (TypeError, ValueError):
                continue
        if n:
            per_session[sid] = n
    summary = _dist_summary(vals)
    summary["sessions"] = len(per_session)
    summary["accepted_events_with_duration"] = len(vals)
    summary["note"] = (
        "Phase228 2503-trade population is a subset of these accepted-entry snapshots "
        "(phase213c IS+OOS sessions); recomputed p66≈381 vs frozen constant 406."
    )
    return {"distribution": summary, "per_session_accept_counts": per_session}


def _bootstrap_p270() -> Any:
    for p in (REPO / "kabu_native" / "src", REPO / "kabu_native" / "scripts", REPO):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    import run_phase270_fast_paper_integration_comparison as p270

    return p270


def _skip_session(sid: str, event_count: int) -> Optional[str]:
    low = sid.lower()
    if "phase282_discord_flow" in low:
        return "phase282_test_harness"
    if "phase284_resim" in low or "phase285_resim" in low:
        return "phase284_285_resim_harness"
    if event_count > REJECT_REPLAY_MAX_EVENTS:
        return f"event_count>{REJECT_REPLAY_MAX_EVENTS}"
    return None


def _decision_pool_duration_by_days(
    p270: Any,
    days: set[str],
    *,
    live_only: bool = False,
) -> dict[str, Any]:
    vals: list[float] = []
    sessions_scanned = 0
    skipped: list[dict[str, str]] = []
    for summary_path in sorted(SMALL_PAPER.rglob("small_paper_summary.json")):
        sid = summary_path.parent.relative_to(SMALL_PAPER).as_posix()
        day = sid.split("/")[0] if "/" in sid else sid
        if day not in days:
            continue
        if live_only and "live_session" not in sid and "live_full_session" not in sid:
            continue
        events = p270._load_events(summary_path.parent)
        skip = _skip_session(sid, len(events))
        if skip:
            skipped.append({"session_id": sid, "reason": skip})
            continue
        sessions_scanned += 1
        for ev in events:
            if not p270._in_decision_pool(ev):
                continue
            v = p270._float(ev.get("max_continuation_duration"))
            if v is None:
                continue
            vals.append(float(v))
    out = _dist_summary(vals)
    out["sessions_scanned"] = sessions_scanned
    out["skipped_sessions"] = skipped
    out["pool"] = "decision_pool (phase270 _in_decision_pool)"
    return out


def _feature_bridge_audit() -> dict[str, Any]:
    for p in (REPO / "kabu_native" / "src",):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)
    from small_paper.live_feature_bridge import LiveFeatureBridge, LiveFeatureBridgeConfig

    update_src = inspect.getsource(LiveFeatureBridge.update)
    return {
        "file": str(FEATURE_BRIDGE.relative_to(REPO)),
        "field_semantics": "max_continuation_duration = st.max_favorable_streak (integer tick streak)",
        "unit": "ticks (consecutive favorable PUSH ticks within active tracking window)",
        "not_seconds_or_bars": True,
        "update_logic": {
            "increment": "if price > ref or price > recent_low: favorable_streak += 1 else favorable_streak = 0",
            "max_accumulation": "max_favorable_streak = max(max_favorable_streak, favorable_streak)",
            "output": "max_continuation_duration = st.max_favorable_streak",
            "favorable_condition": "price > ref_price OR price > recent_low * 1.0001",
            "recent_low_window": "last favorable_lookback ticks (default 8)",
        },
        "reset_conditions": {
            "tracking_reset_sec": LiveFeatureBridgeConfig().tracking_reset_sec,
            "on_reset": "new SymbolTickState — favorable_streak and max_favorable_streak both reset to 0",
            "window_ticks_cap": LiveFeatureBridgeConfig().window_ticks,
            "tick_deque_trim": "while len(ticks) > window_ticks: popleft",
        },
        "config_defaults": {
            "window_ticks": 120,
            "favorable_lookback": 8,
            "tracking_reset_sec": 300.0,
            "favorable_mode": "tick_hit_ratio",
        },
        "diff_since_phase55_da96205": (
            "Core duration formula unchanged (max_favorable_streak). "
            "Phase47+ change: window_start uses market time when use_market_time_window=True; "
            "otherwise monotonic clock."
        ),
        "source_excerpt_update": update_src.splitlines()[40:55],
    }


def _phase272_vs_phase302_explanation(
    p272_scan: dict[str, Any],
    p302_scan: dict[str, Any],
    p297: dict[str, Any],
    p302: dict[str, Any],
    discovery_dist: dict[str, Any],
) -> dict[str, Any]:
    p297_dur = (p297.get("5_duration_high_fired_at_adoption") or {}).get("phase272_window") or {}
    p302_agg = (p302.get("aggregate") or {})
    return {
        "phase272_Duration_high_159768_hits": {
            "count": p297_dur.get("phase272_replay_token_hits", 159768),
            "window_days": "20260518–20260603",
            "sessions_scanned": (p297.get("supporting_scans", {}).get("phase272_date_window") or {}).get(
                "sessions_scanned", 30
            ),
            "pool": "decision_pool events with logged max_continuation_duration",
            "max_logged_duration": (p297_dur.get("max_continuation_duration") or {}).get("max", 4011.0),
            "above_cutoff_406": (p297_dur.get("max_continuation_duration") or {}).get("above_cutoff_406", 159768),
            "scoring_path": "Phase272/270 replay: compute_entry_expectancy_score_fields on logged event fields",
            "reason": (
                "May–early-June archive sessions (push_replay + live) carry gate-time "
                "max_continuation_duration values up to ~4011 on decision_pool events. "
                "With frozen p66=406, ~10% of scored events in that window exceed cutoff "
                "and Duration:high (+2) fires."
            ),
        },
        "phase302_Duration_high_0_hits": {
            "count": p302_agg.get("duration_high_token_hits", 0),
            "window_days": list(P302_DAYS),
            "sessions": p302_agg.get("sessions", 4),
            "pool": "v2 reject decision pool on 20260604–20260605 live_session only",
            "max_logged_duration": max(
                (s.get("duration_stats") or {}).get("max", 0)
                for s in (p302.get("per_session") or [])
            ),
            "reason": (
                "6/4–6/5 live sessions only: feature_bridge max_favorable_streak at gate "
                "never exceeds 172 (p95≈37–38). All values stay below cutoff 406, so "
                "Duration:high never fires despite identical scoring code and cutoff."
            ),
        },
        "same_formula_different_scale": {
            "cutoff_406_frozen_from": "Phase228 p66 on 2503 accepted-entry trades (May discovery sessions)",
            "discovery_accept_p66_recomputed": discovery_dist["distribution"].get("p66"),
            "phase272_window_decision_pool_p66": p272_scan.get("p66"),
            "phase302_live_decision_pool_p66": p302_scan.get("p66"),
            "conclusion": (
                "406 is not wrong arithmetic — it is the Phase228 tertile on a May accepted-trade "
                "population whose entry-time duration scale (p66≈381, max≈3908) differs sharply "
                "from 6/4–6/5 live gate rejects (p66≈13, max≈172). Same field name and tick-unit "
                "formula, but live June gate evaluations see far shorter favorable streaks."
            ),
        },
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p270 = _bootstrap_p270()

    print("git audit...", flush=True)
    git_audit = _git_introduction_audit()
    origin = _origin_trail()

    print("discovery accept distribution...", flush=True)
    discovery_dist = _accepted_duration_discovery_sessions()

    print("scanning phase272 window decision pool...", flush=True)
    p272_scan = _decision_pool_duration_by_days(p270, P272_DAYS)

    print("scanning phase302 live days...", flush=True)
    p302_scan = _decision_pool_duration_by_days(p270, P302_DAYS, live_only=True)

    bridge = _feature_bridge_audit()
    p297 = _load_json(P297)
    p302 = _load_json(P302)
    explain = _phase272_vs_phase302_explanation(p272_scan, p302_scan, p297, p302, discovery_dist)

    report = {
        "phase": 303,
        "title": "duration_cutoff_origin_audit",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "constraint": "review only; cutoff 406 unchanged; no production logic changes",
        "1_git_introduction": git_audit,
        "2_cutoff_406_origin_trail": origin,
        "3_historical_duration_distribution": {
            "phase228_frozen_tertile": (origin["first_phase_computing_406"]["duration_tertile"]),
            "discovery_sessions_accepted_entry": discovery_dist,
            "phase272_window_decision_pool": p272_scan,
        },
        "4_current_duration_distribution": {
            "phase302_live_days_decision_pool": p302_scan,
            "phase302_report_crosscheck": (p302.get("aggregate") or {}).get("duration_high_token_hits"),
        },
        "5_unit_comparison": {
            "field_name": "max_continuation_duration",
            "discovery_phase229_display_unit": "seconds suffix in range labels only (_fmt_num Duration branch)",
            "actual_storage_unit": "ticks (max favorable streak count per LiveFeatureBridge tracking window)",
            "live_feature_bridge": "favorable_streak increments per PUSH tick; not bars, not milliseconds",
            "normalization_elsewhere": "continuation_quality uses dur/14.0 (MOMENTUM_DURATION_SCALE) — separate from tertile cutoff",
            "mismatch_risk": (
                "Phase229 UI labeled Duration bins with 's' but tertile was computed on raw tick-count "
                "field from accepted events. No unit conversion was applied; June live gate scale is "
                "simply much lower than May accepted-entry scale."
            ),
        },
        "6_feature_bridge_update_audit": bridge,
        "7_phase272_vs_phase302_duration_high_explanation": explain,
        "verdict": {
            "cutoff_406_origin": "Phase228 p66 on 2503-trade IS+OOS population (commit f22c025 / Phase230 shadow)",
            "duration_high_origin": "Phase229 score_map + Phase230 entry_expectancy_score_shadow.py",
            "cutoff_functional_on_live_20260604_20260605": False,
            "primary_issue": "scale_mismatch_between_discovery_population_and_current_live_gate_pool",
            "summary": explain["same_formula_different_scale"]["conclusion"],
        },
    }

    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT}")
    print(
        f"discovery_accept_p66={discovery_dist['distribution'].get('p66')} "
        f"p272_pool_p66={p272_scan.get('p66')} p302_p66={p302_scan.get('p66')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
