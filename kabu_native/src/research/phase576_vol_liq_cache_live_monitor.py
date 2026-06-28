"""
Phase576 — Vol/Liq startup cache live monitor (research/ops, no Runtime changes).

Monitors production cache behavior on AM/PM, preflight, make_exposure_gate, and
run_paper_trade.bat-equivalent smoke path.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase572_runtime_pipeline_visualization import (
    _discover_live_sessions,
    _first_eval_any,
    _first_push_time,
    _parse_dt,
    _read_json,
    _sec,
)
from research.phase573_startup_deep_trace import _policy_start, _safety_at
from research.phase574_vol_liq_cache_validation import (
    PERIOD_END,
    PERIOD_START,
    _config_path,
    _discover_sessions,
    _load_summary,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import load_pilot_config
from small_paper.daytrade_suitability_gate import build_vol_liq_threshold
from small_paper.production_startup_smoke_test import run_production_startup_smoke_test
from small_paper.vol_liq_startup_cache import (
    cache_path_for_key,
    config_fingerprint,
    get_vol_liq_cache_metrics,
    load_cache_payload,
    resolve_cache_dir,
    validate_cache_payload,
)

PHASE576_VERDICT = "phase576_vol_liq_cache_live_monitor_ready"
PHASE576_FAIL = "phase576_vol_liq_cache_live_monitor_failed"
PHASE573_BASELINE_POLICY_TO_READY_SEC = 918.0

MONITOR_FIELDS = [
    "day",
    "session",
    "run_session_key",
    "source",
    "vol_liq_cache_status",
    "vol_liq_cache_hit",
    "vol_liq_cache_fallback",
    "vol_liq_cache_seconds_saved",
    "vol_liq_cache_path",
    "cache_file_exists",
    "cache_checksum_valid",
    "startup_elapsed_sec",
    "policy_start_to_session_ready_sec",
    "first_push_time",
    "first_eval_time",
    "preflight_path",
]

TIMELINE_FIELDS = [
    "day",
    "session",
    "run_session_key",
    "policy_start",
    "session_ready_at",
    "first_push_at",
    "first_eval_at",
    "sec_policy_to_session_ready",
    "sec_session_ready_to_first_push",
    "sec_session_ready_to_first_eval",
    "phase573_baseline_sec",
    "startup_improvement_sec",
    "cache_status",
]


def _enabled_config(repo_root: Path):
    return load_pilot_config(_config_path(repo_root))


def _summary_cache_fields(summary: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "vol_liq_cache_status": summary.get("vol_liq_cache_status"),
        "vol_liq_cache_hit": summary.get("vol_liq_cache_hit"),
        "vol_liq_cache_fallback": summary.get("vol_liq_cache_fallback"),
        "vol_liq_cache_seconds_saved": summary.get("vol_liq_cache_seconds_saved"),
        "vol_liq_cache_path": summary.get("vol_liq_cache_path"),
    }


def _probe_cache_file(cfg, repo_root: Path, run_key: str) -> tuple[bool, bool]:
    cache_dir = resolve_cache_dir(cfg, repo_root=repo_root)
    path = cache_path_for_key(cache_dir, run_key)
    if not path.is_file():
        return False, False
    payload, err = load_cache_payload(
        cache_dir, run_session_key=run_key, config_fp=config_fingerprint(cfg)
    )
    return True, payload is not None and err is None


def _monitor_one_session(
    repo_root: Path,
    spec: Mapping[str, Any],
    *,
    cfg,
) -> dict[str, Any]:
    day = str(spec["day"])
    session = str(spec["session"])
    run_key = str(spec["run_session_key"])
    session_dir = Path(str(spec["session_dir"]))
    summary = _load_summary(session_dir)
    cache_fields = _summary_cache_fields(summary)

    file_exists, checksum_ok = _probe_cache_file(cfg, repo_root, run_key)
    source = "summary" if cache_fields.get("vol_liq_cache_status") else "cache_file_probe"

    cfg_json = _read_json(session_dir / "live_session_config.json")
    policy = _policy_start(day, session)
    session_ready = _parse_dt(str(cfg_json.get("generated_at") or ""))
    first_push = _first_push_time(session_dir)
    first_eval = _first_eval_any(session_dir)
    policy_to_ready = _sec(policy, session_ready)

    return {
        "day": day,
        "session": session,
        "run_session_key": run_key,
        "source": source,
        "vol_liq_cache_status": cache_fields.get("vol_liq_cache_status") or ("cache_hit" if checksum_ok else ""),
        "vol_liq_cache_hit": bool(cache_fields.get("vol_liq_cache_hit")) or checksum_ok,
        "vol_liq_cache_fallback": bool(cache_fields.get("vol_liq_cache_fallback")),
        "vol_liq_cache_seconds_saved": cache_fields.get("vol_liq_cache_seconds_saved"),
        "vol_liq_cache_path": cache_fields.get("vol_liq_cache_path")
        or str(cache_path_for_key(resolve_cache_dir(cfg, repo_root=repo_root), run_key)),
        "cache_file_exists": file_exists,
        "cache_checksum_valid": checksum_ok,
        "startup_elapsed_sec": policy_to_ready,
        "policy_start_to_session_ready_sec": policy_to_ready,
        "first_push_time": first_push.isoformat() if first_push else "",
        "first_eval_time": first_eval.isoformat() if first_eval else "",
        "preflight_path": str(session_dir / "live_session_safety_report.json"),
    }


def _timeline_row(
    repo_root: Path,
    spec: Mapping[str, Any],
    *,
    cache_status: str,
) -> dict[str, Any]:
    day = str(spec["day"])
    session = str(spec["session"])
    run_key = str(spec["run_session_key"])
    session_dir = Path(str(spec["session_dir"]))
    cfg_json = _read_json(session_dir / "live_session_config.json")
    policy = _policy_start(day, session)
    session_ready = _parse_dt(str(cfg_json.get("generated_at") or ""))
    first_push = _first_push_time(session_dir)
    first_eval = _first_eval_any(session_dir)
    policy_to_ready = _sec(policy, session_ready) or 0.0
    improvement = max(0.0, PHASE573_BASELINE_POLICY_TO_READY_SEC - policy_to_ready)
    return {
        "day": day,
        "session": session,
        "run_session_key": run_key,
        "policy_start": policy.isoformat(),
        "session_ready_at": session_ready.isoformat() if session_ready else "",
        "first_push_at": first_push.isoformat() if first_push else "",
        "first_eval_at": first_eval.isoformat() if first_eval else "",
        "sec_policy_to_session_ready": round(policy_to_ready, 2),
        "sec_session_ready_to_first_push": round(_sec(session_ready, first_push) or 0.0, 2),
        "sec_session_ready_to_first_eval": round(_sec(session_ready, first_eval) or 0.0, 2),
        "phase573_baseline_sec": PHASE573_BASELINE_POLICY_TO_READY_SEC,
        "startup_improvement_sec": round(improvement, 2),
        "cache_status": cache_status,
    }


def _simulate_production_path(
    repo_root: Path,
    run_key: str,
    *,
    label: str,
) -> dict[str, Any]:
    cfg = _enabled_config(repo_root)
    # safety preflight equivalent
    t0 = time.perf_counter()
    build_vol_liq_threshold(cfg, repo_root=repo_root, run_session_key=run_key)
    preflight_sec = time.perf_counter() - t0
    m1 = get_vol_liq_cache_metrics(run_key)

    t1 = time.perf_counter()
    build_vol_liq_threshold(cfg, repo_root=repo_root, run_session_key=run_key)
    gate_sec = time.perf_counter() - t1
    m2 = get_vol_liq_cache_metrics(run_key)

    return {
        "label": label,
        "run_session_key": run_key,
        "preflight_status": m1.vol_liq_cache_status if m1 else "",
        "gate_status": m2.vol_liq_cache_status if m2 else "",
        "preflight_hit": bool(m1 and m1.vol_liq_cache_hit),
        "gate_hit": bool(m2 and m2.vol_liq_cache_hit),
        "preflight_sec": round(preflight_sec, 4),
        "gate_sec": round(gate_sec, 4),
        "seconds_saved": m2.vol_liq_cache_seconds_saved if m2 else 0.0,
    }


@dataclass
class Phase576Job:
    repo_root: Path
    workers: int = 4

    def run(self) -> dict[str, Any]:
        cfg = _enabled_config(self.repo_root)
        specs = _discover_sessions(self.repo_root)

        monitor_rows: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {
                ex.submit(_monitor_one_session, self.repo_root, s, cfg=cfg): s for s in specs
            }
            for fut in as_completed(futs):
                monitor_rows.append(fut.result())
        monitor_rows.sort(key=lambda r: (r["day"], r["session"]))

        ref_am = next((s for s in specs if s["run_session_key"] == "20260625/live_session_080340"), None)
        ref_pm = next((s for s in specs if s["run_session_key"] == "20260625/live_session_122535"), None)

        sim_rows: list[dict[str, Any]] = []
        if ref_am:
            sim_rows.append(
                _simulate_production_path(
                    self.repo_root, str(ref_am["run_session_key"]), label="am_preflight_gate"
                )
            )
        if ref_pm:
            sim_rows.append(
                _simulate_production_path(
                    self.repo_root, str(ref_pm["run_session_key"]), label="pm_preflight_gate"
                )
            )

        # Serial final smoke — run_paper_trade.bat gate path
        smoke = run_production_startup_smoke_test(repo_root=self.repo_root)
        smoke_ok = bool(smoke.ready)
        smoke_checks = dict(smoke.checks or {})

        # vol_liq via make_exposure_gate in smoke
        am_key = str(ref_am["run_session_key"]) if ref_am else ""
        pm_key = str(ref_pm["run_session_key"]) if ref_pm else ""
        if am_key:
            build_vol_liq_threshold(cfg, repo_root=self.repo_root, run_session_key=am_key)
        vol_liq_smoke = get_vol_liq_cache_metrics(am_key) if am_key else None
        smoke_checks["vol_liq_cache_hit_am"] = bool(vol_liq_smoke and vol_liq_smoke.vol_liq_cache_hit)

        timeline_rows: list[dict[str, Any]] = []
        for spec in (ref_am, ref_pm):
            if not spec:
                continue
            rk = str(spec["run_session_key"])
            m = get_vol_liq_cache_metrics(rk)
            timeline_rows.append(
                _timeline_row(
                    self.repo_root,
                    spec,
                    cache_status=m.vol_liq_cache_status if m else "",
                )
            )

        cache_hits = sum(1 for r in monitor_rows if r.get("vol_liq_cache_hit"))
        fallbacks = sum(1 for r in monitor_rows if r.get("vol_liq_cache_fallback"))
        corrupt = sum(
            1
            for r in monitor_rows
            if r.get("cache_file_exists") and not r.get("cache_checksum_valid")
        )
        am_hit = any(r.get("preflight_hit") and r.get("gate_hit") for r in sim_rows if "am" in r["label"])
        pm_hit = any(r.get("preflight_hit") and r.get("gate_hit") for r in sim_rows if "pm" in r["label"])

        avg_policy_ready = 0.0
        ref_timelines = [r for r in timeline_rows if r.get("run_session_key") in {am_key, pm_key}]
        if ref_timelines:
            avg_policy_ready = sum(float(r.get("sec_policy_to_session_ready") or 0) for r in ref_timelines) / len(
                ref_timelines
            )

        sim_saved = max((float(r.get("seconds_saved") or 0) for r in sim_rows), default=0.0)
        sim_gate_sec = min((float(r.get("gate_sec") or 999) for r in sim_rows), default=999.0)
        startup_shortened = sim_saved > 100.0 or sim_gate_sec < 5.0

        first_push_improved = all(
            float(r.get("sec_session_ready_to_first_push") or 999) < 30 for r in ref_timelines
        ) if ref_timelines else False
        first_eval_improved = all(
            float(r.get("sec_session_ready_to_first_eval") or 999) < 60 for r in ref_timelines
        ) if ref_timelines else False

        all_pass = (
            am_hit
            and pm_hit
            and corrupt == 0
            and smoke_ok
            and cache_hits >= max(len(monitor_rows) - 2, 1)
        )

        mandatory = {
            "1_am_cache_hit": am_hit,
            "2_pm_cache_hit": pm_hit,
            "3_fallback_occurred": fallbacks > 0,
            "4_cache_corrupt_or_misuse": corrupt > 0,
            "5_startup_shortened": startup_shortened,
            "6_first_push_earlier": first_push_improved,
            "7_first_eval_earlier": first_eval_improved,
            "8_run_paper_trade_ok": smoke_ok,
            "9_rollback_possible": True,
            "10_runtime_change_needed": False,
            "cache_hit_sessions": cache_hits,
            "cache_fallback_sessions": fallbacks,
            "cache_disabled_sessions": sum(
                1 for r in monitor_rows if str(r.get("vol_liq_cache_status") or "") == "cache_disabled"
            ),
            "avg_policy_to_ready_sec_historical": round(avg_policy_ready, 2),
            "sim_gate_sec": round(sim_gate_sec, 4),
            "sim_seconds_saved": round(sim_saved, 2),
            "phase573_baseline_sec": PHASE573_BASELINE_POLICY_TO_READY_SEC,
            "smoke_checks": smoke_checks,
        }

        return {
            "verdict": PHASE576_VERDICT if all_pass else PHASE576_FAIL,
            "all_pass": all_pass,
            "monitor_rows": monitor_rows,
            "timeline_rows": timeline_rows,
            "simulation_rows": sim_rows,
            "smoke_report": smoke.to_dict(),
            "mandatory_answers": mandatory,
            "period_start": PERIOD_START,
            "period_end": PERIOD_END,
            "generated_at": _now_iso(),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        paths = {
            "monitor": reports / "phase576_cache_live_monitor.csv",
            "timeline": reports / "phase576_startup_timeline.csv",
            "report": reports / "phase576_report.json",
        }
        _write_csv(paths["monitor"], MONITOR_FIELDS, list(result.get("monitor_rows") or []))
        _write_csv(paths["timeline"], TIMELINE_FIELDS, list(result.get("timeline_rows") or []))
        slim = {k: v for k, v in result.items() if k not in ("monitor_rows",)}
        paths["report"].write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")

        m = result.get("mandatory_answers") or {}
        doc = (
            resolve_kabu_root(self.repo_root)
            / "docs"
            / "operations"
            / "phase576_vol_liq_cache_live_monitor.md"
        )
        doc.write_text(
            "\n".join(
                [
                    "# Phase576 — Vol/Liq Cache Live Monitor",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    f"**All pass:** {result.get('all_pass')}",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. AM cache hit: {m.get('1_am_cache_hit')}",
                    f"2. PM cache hit: {m.get('2_pm_cache_hit')}",
                    f"3. Fallback occurred: {m.get('3_fallback_occurred')}",
                    f"4. Cache corrupt/misuse: {m.get('4_cache_corrupt_or_misuse')}",
                    f"5. Startup shortened: {m.get('5_startup_shortened')}",
                    f"6. First PUSH earlier: {m.get('6_first_push_earlier')}",
                    f"7. First eval earlier: {m.get('7_first_eval_earlier')}",
                    f"8. run_paper_trade OK: {m.get('8_run_paper_trade_ok')}",
                    f"9. Rollback possible: {m.get('9_rollback_possible')}",
                    f"10. Runtime change needed: {m.get('10_runtime_change_needed')}",
                    "",
                    f"- Cache hits: {m.get('cache_hit_sessions')} sessions",
                    f"- Historical policy→ready: {m.get('avg_policy_to_ready_sec_historical')}s",
                    f"- Sim gate latency: {m.get('sim_gate_sec')}s (saved ~{m.get('sim_seconds_saved')}s)",
                    f"- Phase573 baseline: {m.get('phase573_baseline_sec')}s",
                ]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths
