"""Phase B orchestrator: 9-day economics, gates, Rolling-origin, LODO, selection.

STOP after Phase B. No Shadow / Forward / Paper / Discord.
"""
from __future__ import annotations

import os

for _k in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_k] = "1"

import argparse
import json
import secrets
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from . import guard as guard_mod
from .asof_coverage import canonical_day_bundle
from .candidate_replay import replay_all_candidates
from .economics import (
    BASE_COMPLETED,
    BASE_MAX_DD,
    BASE_PNL,
    BASE_STOP_LOSS_TOTAL,
    candidate_metrics,
    evaluate_candidate_gates,
    lodo_fixed_spec,
    lodo_reselect,
    rolling_origin_eval,
    selection_rank_key,
)
from .history import SUPERSEDED_RUNS
from .protected_manifest import build_protected_manifest, manifests_equal
from .report import atomic_publish
from .source_manifest import DAYS, build_source_manifest
from .store import load_checkpoint, run_root, save_checkpoint, sha256_file, sha256_obj, write_json
from .window_bundle import build_window_bundle

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent

# ---- Frozen bindings (user-approved Phase A-R3) ----
R3_RUN_ID = "e1x6r3r3_20260803_042910_da2c8fcb"
R3_REPORT_SHA = "3e539696bab70ccaccbf090f307367e3e8e701be4669dc6ee7294c48565f28ae"
P1_R3_SHA = "8863aaa2124b55f869cf09585d6386fb2fdce4552fd5ebbcc23889c0aa22754e"
REGISTRY_SHA = "5310c4a8f6640d96012f893f5697b9816f794bf21ee6902ce57201676c0f58e9"
SOURCE_MANIFEST_SHA = "b1232905f2a4bf21bc04975f97cbf526372936a3dab70ad2aba9a81498617ef7"
ANALYSIS_MASK_ID = "MASK_R1_ea8f67eb1b559218"
TICK_EVIDENCE_SHA = "e15b83945a10fc50f25f48cbf4c4179387fc0324a3240a2e9839e88d576b018b"
E1X5_BASE_SHA = "138f74676a3ffd3f303f2bfdeb529c9bd4369a0f13f59bb805e65690aefa909f"
PROTECTED_MANIFEST_SHA = "138b51ba25fdd749fcf440b8c656b3f8e7b2504dde37ab6e88afeff7dc6df84f"

R3_ROOT = Path.home() / "e1x6_research_store" / "raw_feature_redesign" / R3_RUN_ID
R3_REPORT = R3_ROOT / "published" / "report.json"
TICK_EVIDENCE = (
    Path.home() / "e1x6_research_store" / "raw_feature_redesign"
    / "official_tick_evidence_r3" / "manifest.json"
)
BASE_RECUT = (
    Path.home() / "e1x6_research_store" / "raw_feature_redesign"
    / "e1x6r3r2_20260803_040009_4d87ffa4" / "e1x5_base_recut.json"
)

META_STRIP_KEYS = {
    "run_id", "published_at_jst", "generated_at", "absolute_path",
    "store_path", "elapsed_sec", "wall_time",
}


def _pause(run_id: str, guard_res: dict[str, Any], done: dict[str, Any]) -> None:
    write_json(run_root(run_id) / "paused.json", {
        "verdict": "E1_X6_RESEARCH_PAUSED_FOR_PAPER",
        "guard": guard_res, "progress": done,
        "paused_at": datetime.now().astimezone().isoformat(),
    })
    print(f"verdict: E1_X6_RESEARCH_PAUSED_FOR_PAPER run_id={run_id}")
    sys.exit(0)


def _guard_or_pause(run_id: str, done: dict[str, Any]) -> None:
    res = guard_mod.paper_guard_check(NATIVE_ROOT, run_root(run_id))
    if not res["ok"]:
        _pause(run_id, res, done)


def _canonical_strip(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _canonical_strip(v) for k, v in sorted(obj.items())
                if k not in META_STRIP_KEYS}
    if isinstance(obj, list):
        return [_canonical_strip(x) for x in obj]
    return obj


def _run_tests() -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{NATIVE_ROOT / 'research'};{NATIVE_ROOT / 'src'}"
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-rA", "--tb=short",
         "-p", "no:cacheprovider",
         str(NATIVE_ROOT / "tests" / "research" / "e1_x6_raw_redesign")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(NATIVE_ROOT), env=env, timeout=3600,
    )
    rows = []
    for line in (proc.stdout or "").splitlines():
        ls = line.strip()
        for status in ("PASSED", "FAILED", "ERROR"):
            if ls.startswith(status + " "):
                rows.append({"test": ls.split(" ", 1)[1].split(" - ")[0], "outcome": status})
    passed = sum(1 for r in rows if r["outcome"] == "PASSED")
    return {"exit_code": proc.returncode, "total": len(rows), "passed": passed,
            "failed": len(rows) - passed, "rows": rows, "tail": (proc.stdout or "")[-3000:]}


def _verify_bindings() -> dict[str, Any]:
    errors = []
    if not R3_REPORT.is_file():
        errors.append(f"R3 report missing: {R3_REPORT}")
    else:
        sha = sha256_file(R3_REPORT)
        if sha != R3_REPORT_SHA:
            errors.append(f"R3 report SHA mismatch: {sha}")
    report = json.loads(R3_REPORT.read_text(encoding="utf-8")) if R3_REPORT.is_file() else {}
    p1 = report.get("p1") or {}
    if p1.get("p1_sha256") != P1_R3_SHA:
        errors.append(f"P1_R3 SHA mismatch: {p1.get('p1_sha256')}")
    if p1.get("candidate_registry_sha256") != REGISTRY_SHA:
        errors.append(f"Registry SHA mismatch: {p1.get('candidate_registry_sha256')}")
    if report.get("source_manifest_sha256") != SOURCE_MANIFEST_SHA:
        errors.append(f"source manifest SHA mismatch: {report.get('source_manifest_sha256')}")
    mask = (report.get("r1") or {}).get("analysis_mask") or {}
    if mask.get("analysis_mask_id") != ANALYSIS_MASK_ID:
        errors.append(f"analysis_mask_id mismatch: {mask.get('analysis_mask_id')}")
    if not TICK_EVIDENCE.is_file() or sha256_file(TICK_EVIDENCE) != TICK_EVIDENCE_SHA:
        errors.append("official tick evidence manifest SHA mismatch")
    if not BASE_RECUT.is_file():
        errors.append("BASE recut missing")
    else:
        base = json.loads(BASE_RECUT.read_text(encoding="utf-8"))
        if base.get("artifact_sha256") != E1X5_BASE_SHA:
            errors.append(f"E1_X5 BASE SHA mismatch: {base.get('artifact_sha256')}")
    pm = (report.get("paper_protected_manifest") or {})
    if pm.get("before_sha256") != PROTECTED_MANIFEST_SHA:
        errors.append(f"protected manifest SHA mismatch: {pm.get('before_sha256')}")
    if report.get("verdict") != "E1_X6_RAW_REDESIGN_P1_R3_READY":
        errors.append(f"R3 verdict not READY: {report.get('verdict')}")
    return {"ok": not errors, "errors": errors, "report": report}


def _included_windows(mask: dict[str, Any]) -> list[str]:
    return sorted(
        wid for wid, w in (mask.get("windows") or {}).items() if w.get("included")
    )


def _aggregate_lane(
    lane_results: dict[str, dict[str, Any]],
    candidates: list[dict[str, Any]],
    included: list[str],
) -> dict[str, Any]:
    """lane_results: window_id -> {sid -> replay result}."""
    per_sid_trades: dict[str, list] = {c["strategy_id"]: [] for c in candidates if c["enabled"]}
    per_sid_cap: dict[str, list] = {c["strategy_id"]: [] for c in candidates if c["enabled"]}
    per_sid_counters: dict[str, dict] = {
        c["strategy_id"]: {
            "completed": 0, "open": 0, "orphan": 0, "censored": 0,
            "invalid_source": 0, "cap_blocked": 0, "rejected_entry": 0,
        }
        for c in candidates if c["enabled"]
    }
    for wid, by_sid in lane_results.items():
        if wid == "20260721_AM":
            raise SystemExit("FAIL: 20260721_AM entered economics (forbidden)")
        for sid, res in by_sid.items():
            per_sid_trades[sid].extend(res["trades"])
            per_sid_cap[sid].extend(res["cap_blocked"])
            for k, v in res["counters"].items():
                per_sid_counters[sid][k] = per_sid_counters[sid].get(k, 0) + v

    summaries = {}
    day_pnls = {}
    for cand in candidates:
        if not cand["enabled"]:
            continue
        sid = cand["strategy_id"]
        completed = [t for t in per_sid_trades[sid] if t.get("status") == "COMPLETED"]
        m = candidate_metrics(completed, windows_included=included)
        gates = evaluate_candidate_gates(m)
        summaries[sid] = {
            "strategy_id": sid,
            "setup": cand["setup"],
            "confirmation": cand["confirmation"],
            "regime_mode": cand["regime_mode"],
            "exit": "EXIT_B" if "EXIT_B" in sid else "EXIT_A",
            "optional_features_in_use": cand.get("optional_features_in_use") or [],
            "metrics": m,
            "gates": gates,
            "counters": per_sid_counters[sid],
            "n_cap_blocked": len(per_sid_cap[sid]),
            "trades": completed,
            "all_trade_rows": per_sid_trades[sid],
            "cap_blocked": per_sid_cap[sid],
        }
        day_pnls[sid] = m["day_pnl"]
    return {"summaries": summaries, "day_pnls": day_pnls}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    guard_mod.apply_thread_caps()
    prio_ok = guard_mod.set_below_normal_priority()
    run_id = args.run_id or (
        f"e1x6r3b_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"
    )
    root = run_root(run_id)
    print(f"run_id={run_id} store={root} below_normal={prio_ok}")
    _guard_or_pause(run_id, {"stage": "start"})

    bind = _verify_bindings()
    if not bind["ok"]:
        report = {
            "plan_id": "E1_X6_RAW_FEATURE_REDESIGN",
            "phase": "PHASE_B",
            "run_id": run_id,
            "verdict": "E1_X6_RAW_REDESIGN_PHASE_B_BLOCKED",
            "block_reason": "BINDING_MISMATCH",
            "binding_errors": bind["errors"],
            "published_at_jst": datetime.now().astimezone().isoformat(),
            "paper_guard": {"triggered": False},
            "tests": {"passed": 0, "total": 0, "failed": 0, "rows": []},
            "p1": {"p1_sha256": None},
            "candidate_registry": [],
            "inventory": {"raw_total_lines": 0, "canonical_total": 0, "days": {}},
            "field_usability": {},
            "source_manifest_days": {},
            "paper_protected_manifest": {"match": False, "before_files": {}},
        }
        shas = atomic_publish(run_id, report)
        print("BLOCKED binding:", bind["errors"])
        print("published", shas)
        sys.exit(2)

    r3 = bind["report"]
    mask = r3["r1"]["analysis_mask"]
    included = _included_windows(mask)
    assert "20260721_AM" not in included
    assert len(included) == 17, f"expected 17 included windows, got {len(included)}"
    candidates = [c for c in r3["candidate_registry"]]
    enabled = [c for c in candidates if c["enabled"]]
    assert len(enabled) == 24
    for c in enabled:
        if c.get("optional_features_in_use"):
            raise SystemExit(f"FAIL: optional features in use for {c['strategy_id']}")

    symbol_classes = {
        sym: row["class"]
        for sym, row in r3["r3"]["tick_official"]["symbol_classes"].items()
    }

    pm_before_fp = root / "paper_protected_manifest_before.json"
    if pm_before_fp.is_file():
        pm_before = json.loads(pm_before_fp.read_text(encoding="utf-8"))
    else:
        pm_before = build_protected_manifest(REPO_ROOT)
        write_json(pm_before_fp, pm_before)
    if pm_before["manifest_sha256"] != PROTECTED_MANIFEST_SHA:
        print(f"BLOCK: protected manifest before mismatch {pm_before['manifest_sha256']}")
        sys.exit(2)

    binding = {
        "r3_report_sha256": R3_REPORT_SHA,
        "p1_sha256": P1_R3_SHA,
        "source_manifest_sha256": SOURCE_MANIFEST_SHA,
        "analysis_mask_id": ANALYSIS_MASK_ID,
    }

    # Universes per day (canonical PM set)
    universes: dict[str, list[str]] = {}
    for day in DAYS:
        _guard_or_pause(run_id, {"stage": "universe", "day": day})
        ck = load_checkpoint(run_id, f"universe_{day}", binding=binding)
        if ck is not None:
            universes[day] = ck["universe"]
        else:
            print(f"loading universe {day}...")
            b = canonical_day_bundle(NATIVE_ROOT, day)
            universes[day] = b["universe"]
            save_checkpoint(run_id, f"universe_{day}",
                            {"universe": b["universe"]}, binding=binding)
        print(f"  universe {day}: {len(universes[day])}")

    def run_lane(lane: str) -> dict[str, Any]:
        lane_results: dict[str, dict[str, Any]] = {}
        for wid in included:
            day, am_pm = wid.split("_")
            _guard_or_pause(run_id, {"stage": f"lane_{lane}", "window": wid})
            ck_name = f"replay_{lane}_{wid}"
            ck = load_checkpoint(run_id, ck_name, binding=binding)
            if ck is not None:
                lane_results[wid] = ck["by_sid"]
                print(f"[{lane}] resume {wid}")
                continue
            print(f"[{lane}] building bundle {wid}...")
            bundle = build_window_bundle(
                NATIVE_ROOT, day, am_pm, universes[day], symbol_classes,
                mask["windows"][wid],
            )
            print(f"[{lane}] replaying 24 candidates on {wid}...")
            by_sid = replay_all_candidates(bundle, enabled)
            # Persist without huge feature arrays — trades only
            slim = {
                sid: {
                    "trades": res["trades"],
                    "cap_blocked": res["cap_blocked"],
                    "counters": res["counters"],
                    # drop rejected detail to keep checkpoint smaller
                }
                for sid, res in by_sid.items()
            }
            save_checkpoint(run_id, ck_name, {"by_sid": slim}, binding=binding)
            lane_results[wid] = slim
            n_tr = sum(r["counters"]["completed"] for r in slim.values())
            print(f"[{lane}] {wid} done completed_sum={n_tr}")
        return _aggregate_lane(lane_results, enabled, included)

    print("=== Lane A ===")
    lane_a = run_lane("A")
    print("=== Lane B (independent) ===")
    lane_b = run_lane("B")

    # A/B canonical compare
    ab_rows = []
    ab_ok = True
    for sid in sorted(lane_a["summaries"]):
        a = lane_a["summaries"][sid]
        b = lane_b["summaries"][sid]
        payloads = {
            "trades": _canonical_strip(a["trades"]),
            "cap_blocked": _canonical_strip(a["cap_blocked"]),
            "counters": a["counters"],
            "metrics_core": _canonical_strip({
                k: a["metrics"][k] for k in (
                    "total_pnl", "completed_trades", "day_pnl", "session_pnl",
                    "max_dd", "stop_loss_total", "pf", "exit_reason_n",
                ) if k in a["metrics"]
            }),
            "gates": a["gates"]["gates"],
        }
        payloads_b = {
            "trades": _canonical_strip(b["trades"]),
            "cap_blocked": _canonical_strip(b["cap_blocked"]),
            "counters": b["counters"],
            "metrics_core": _canonical_strip({
                k: b["metrics"][k] for k in (
                    "total_pnl", "completed_trades", "day_pnl", "session_pnl",
                    "max_dd", "stop_loss_total", "pf", "exit_reason_n",
                ) if k in b["metrics"]
            }),
            "gates": b["gates"]["gates"],
        }
        sha_a = sha256_obj(payloads)
        sha_b = sha256_obj(payloads_b)
        match = sha_a == sha_b
        if not match:
            ab_ok = False
        ab_rows.append({"strategy_id": sid, "sha_a": sha_a, "sha_b": sha_b, "match": match})

    # Integrity
    integrity_ok = True
    integrity_errors = []
    for sid, s in lane_a["summaries"].items():
        c = s["counters"]
        if c.get("open", 0) or c.get("orphan", 0) or c.get("censored", 0):
            integrity_ok = False
            integrity_errors.append(f"{sid}: open/orphan/censored leftover {c}")
        if c.get("invalid_source", 0):
            integrity_ok = False
            integrity_errors.append(f"{sid}: INVALID_SOURCE={c['invalid_source']}")
        # 20260721 day pnl must equal PM session only
        day21 = float(s["metrics"]["day_pnl"].get("20260721", 0.0))
        pm21 = float(s["metrics"]["session_pnl"].get("20260721_PM", 0.0))
        am21 = float(s["metrics"]["session_pnl"].get("20260721_AM", 0.0))
        if abs(am21) > 1e-12:
            integrity_ok = False
            integrity_errors.append(f"{sid}: 20260721_AM pnl leaked {am21}")
        if abs(day21 - pm21) > 1e-6:
            integrity_ok = False
            integrity_errors.append(f"{sid}: 20260721 day!=PM ({day21} vs {pm21})")

    pm_after = build_protected_manifest(REPO_ROOT)
    pm_match, pm_diffs = manifests_equal(pm_before, pm_after)

    # Always compute selection artifacts (even if later blocked) for auditability.
    qualified = [
        sid for sid, s in lane_a["summaries"].items()
        if s["gates"]["all_pass"]
    ]
    rolling = rolling_origin_eval(enabled, lane_a["day_pnls"])
    lodo_r = lodo_reselect(enabled, lane_a["day_pnls"])
    cand_by_id = {c["strategy_id"]: c for c in enabled}
    selected = None
    if qualified and rolling["all_pass"] and lodo_r["all_pass"]:
        best_key = None
        for sid in qualified:
            key = selection_rank_key(lane_a["summaries"][sid]["metrics"], cand_by_id[sid])
            if best_key is None or key < best_key:
                best_key = key
                selected = sid

    lodo_f = lodo_fixed_spec(
        selected or (qualified[0] if qualified else sorted(lane_a["summaries"])[0]),
        lane_a["day_pnls"],
    )

    blocked = (not ab_ok) or (not integrity_ok) or (not pm_match)
    if blocked:
        verdict = "E1_X6_RAW_REDESIGN_PHASE_B_BLOCKED"
    elif selected is not None:
        verdict = "E1_X6_RAW_REDESIGN_PHASE_B_CANDIDATE_SELECTED"
    else:
        verdict = "E1_X6_RAW_REDESIGN_PHASE_B_NO_ROBUST_CANDIDATE"

    print(f"verdict={verdict} ab_ok={ab_ok} integrity_ok={integrity_ok} pm_match={pm_match}")

    tests = _run_tests()
    if tests["exit_code"] != 0:
        verdict = "E1_X6_RAW_REDESIGN_PHASE_B_BLOCKED"
        blocked = True

    # Build publishable report (trades truncated in summary; full in audit sheets via refs)
    candidate_summary = []
    for sid in sorted(lane_a["summaries"]):
        s = lane_a["summaries"][sid]
        m = s["metrics"]
        candidate_summary.append({
            "strategy_id": sid,
            "qualified": s["gates"]["all_pass"],
            "failed_gates": s["gates"]["failed"],
            "total_pnl": m["total_pnl"],
            "completed_trades": m["completed_trades"],
            "pf": m["pf"],
            "pf_status": m["pf_status"],
            "median_day_pnl": m["median_day_pnl"],
            "ex_best1_day_pnl": m["ex_best1_day_pnl"],
            "ex_best2_days_pnl": m["ex_best2_days_pnl"],
            "max_dd": m["max_dd"],
            "stop_loss_total": m["stop_loss_total"],
            "days_with_trades": m["days_with_trades"],
            "top1_day_share": m["top1_day_share_of_gross_positive"],
            "top2_days_share": m["top2_days_share_of_gross_positive"],
            "sensitivity_20260722": m["sensitivity_20260722"],
            "base_comparison": m["base_comparison"],
            "counters": s["counters"],
            "optional_features_in_use": s["optional_features_in_use"],
            "day_pnl": m["day_pnl"],
            "session_pnl": m["session_pnl"],
            "exit_reason_n": m["exit_reason_n"],
            "exit_reason_pnl": m["exit_reason_pnl"],
            "symbol_pnl_top10": dict(sorted(
                m["symbol_pnl"].items(), key=lambda kv: (-kv[1], kv[0])
            )[:10]),
        })

    # Flatten trade / cap ledgers for audit (all candidates)
    trade_ledger = []
    cap_ledger = []
    for sid, s in lane_a["summaries"].items():
        trade_ledger.extend(s["trades"])
        cap_ledger.extend(s["cap_blocked"])

    report: dict[str, Any] = {
        "plan_id": "E1_X6_RAW_FEATURE_REDESIGN",
        "phase": "PHASE_B",
        "run_id": run_id,
        "verdict": verdict,
        "published_at_jst": datetime.now().astimezone().isoformat(),
        "bindings": {
            "r3_run_id": R3_RUN_ID,
            "r3_report_sha256": R3_REPORT_SHA,
            "p1_sha256": P1_R3_SHA,
            "candidate_registry_sha256": REGISTRY_SHA,
            "source_manifest_sha256": SOURCE_MANIFEST_SHA,
            "analysis_mask_id": ANALYSIS_MASK_ID,
            "official_tick_evidence_manifest_sha256": TICK_EVIDENCE_SHA,
            "e1_x5_base_sha256": E1X5_BASE_SHA,
            "protected_manifest_sha256": PROTECTED_MANIFEST_SHA,
            "binding_ok": True,
        },
        "included_windows": included,
        "excluded_windows": ["20260721_AM"],
        "selected_candidate_id": None if blocked else selected,
        "qualified_candidates": [] if blocked else qualified,
        "candidate_summary": candidate_summary,
        "gate_matrix": {
            sid: lane_a["summaries"][sid]["gates"] for sid in sorted(lane_a["summaries"])
        },
        "rolling_origin": rolling,
        "lodo_fixed": lodo_f,
        "lodo_reselect": lodo_r,
        "ab_determinism": {
            "all_match": ab_ok,
            "rows": ab_rows,
        },
        "integrity": {
            "ok": integrity_ok,
            "errors": integrity_errors,
        },
        "base_reference": {
            "completed_trades": BASE_COMPLETED,
            "pnl": BASE_PNL,
            "max_dd": BASE_MAX_DD,
            "stop_loss_total": BASE_STOP_LOSS_TOTAL,
            "artifact_sha256": E1X5_BASE_SHA,
        },
        "trade_ledger": trade_ledger,
        "cap_blocked": cap_ledger,
        "safety": {
            "submit": 0, "cancel": 0, "live": 0,
            "paper_processes_touched": 0,
            "shadow_started": False,
            "discord_notified": False,
        },
        "paper_protected_manifest": {
            "before_sha256": pm_before["manifest_sha256"],
            "after_sha256": pm_after["manifest_sha256"],
            "match": pm_match,
            "before_files": pm_before.get("files") or {},
            "diffs": pm_diffs,
        },
        "paper_guard": {"triggered": False, "below_normal": prio_ok},
        "tests": tests,
        "block_reason": (
            ("AB_MISMATCH" if not ab_ok else "")
            or ("INTEGRITY" if not integrity_ok else "")
            or ("PROTECTED_MANIFEST" if not pm_match else "")
            or ("TESTS" if tests["exit_code"] != 0 else "")
            or None
        ),
        # Minimal stubs so shared Phase A report renderer does not crash
        "p1": r3["p1"],
        "candidate_registry": candidates,
        "inventory": r3.get("inventory") or {"raw_total_lines": 0, "canonical_total": 0, "days": {}},
        "field_usability": (r3.get("r3") or {}).get("field_usability") or {},
        "source_manifest_days": (r3.get("source_manifest_days")
                                 or (r3.get("source_manifest") or {}).get("days") or {}),
        "phase_b": {
            "note": "Economics complete; STOP — no Shadow/Forward/Paper adoption",
            "not_started": ["Shadow", "Forward", "Paper embedding", "Discord"],
        },
        "history_note": SUPERSEDED_RUNS,
    }

    # Extend report.md / xlsx via phase flag in atomic publish path
    from . import report as report_mod
    _orig_md = report_mod.render_report_md
    _orig_xlsx = report_mod.render_audit_xlsx

    def _md_b(rep, sha):
        if rep.get("phase") != "PHASE_B":
            return _orig_md(rep, sha)
        lines = [
            f"# {rep['plan_id']} — Phase B ({rep['verdict']})",
            "",
            f"- run_id: `{rep['run_id']}`",
            f"- report.json sha256 `{sha}`",
            f"- selected: `{rep.get('selected_candidate_id')}`",
            f"- qualified: {rep.get('qualified_candidates')}",
            f"- A/B match: {rep['ab_determinism']['all_match']}",
            f"- integrity ok: {rep['integrity']['ok']}",
            f"- protected manifest match: {rep['paper_protected_manifest']['match']}",
            f"- submit/cancel/live: 0/0/0",
            "",
            "## Candidate summary",
        ]
        for row in rep["candidate_summary"]:
            lines.append(
                f"- `{row['strategy_id']}` pnl={row['total_pnl']:.2f} "
                f"n={row['completed_trades']} pf={row['pf']} "
                f"qualified={row['qualified']} failed={row['failed_gates']}"
            )
        if rep.get("rolling_origin"):
            lines += ["", "## Rolling-origin", f"- pass: {rep['rolling_origin']['all_pass']}",
                      f"- confirm total: {rep['rolling_origin']['confirm_total']}"]
        if rep.get("lodo_reselect"):
            lines += ["", "## LODO reselect", f"- pass: {rep['lodo_reselect']['all_pass']}",
                      f"- held-out total: {rep['lodo_reselect']['held_out_total']}"]
        lines += ["", "## STOP", "Phase B complete. Shadow/Forward/Paper not started."]
        return "\n".join(lines)

    def _xlsx_b(rep, sha, out_fp):
        if rep.get("phase") != "PHASE_B":
            return _orig_xlsx(rep, sha, out_fp)
        from openpyxl import Workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "README"
        for row in (("verdict", rep["verdict"]), ("run_id", rep["run_id"]),
                    ("report_json_sha256", sha), ("selected", rep.get("selected_candidate_id"))):
            ws.append(list(row))

        def sheet(name, headers, rows):
            w = wb.create_sheet(name)
            w.append(headers)
            for r in rows:
                w.append(r)

        sheet("bindings", ["key", "value"], [[k, v] for k, v in rep["bindings"].items()])
        sheet("candidate_summary",
              ["strategy_id", "qualified", "total_pnl", "n", "pf", "median_day",
               "ex_best2", "max_dd", "stop_tot", "failed"],
              [[r["strategy_id"], r["qualified"], r["total_pnl"], r["completed_trades"],
                r["pf"], r["median_day_pnl"], r["ex_best2_days_pnl"], r["max_dd"],
                r["stop_loss_total"], json.dumps(r["failed_gates"])]
               for r in rep["candidate_summary"]])
        gm_rows = []
        for sid, g in rep["gate_matrix"].items():
            for k, v in g["gates"].items():
                gm_rows.append([sid, k, v])
        sheet("gate_matrix", ["strategy_id", "gate", "pass"], gm_rows)
        dp_rows = []
        for r in rep["candidate_summary"]:
            for d, p in r["day_pnl"].items():
                dp_rows.append([r["strategy_id"], d, p])
        sheet("daily_pnl", ["strategy_id", "day", "pnl"], dp_rows)
        sp_rows = []
        for r in rep["candidate_summary"]:
            for w, p in r["session_pnl"].items():
                sp_rows.append([r["strategy_id"], w, p])
        sheet("session_pnl", ["strategy_id", "window", "pnl"], sp_rows)
        sheet("trade_ledger",
              ["strategy_id", "day", "am_pm", "symbol", "exit_reason", "entry_ask",
               "exit_bid", "net_pnl", "entry_time", "exit_time"],
              [[t.get("strategy_id"), t.get("day"), t.get("am_pm"), t.get("symbol"),
                t.get("exit_reason"), t.get("entry_ask"), t.get("exit_bid"),
                t.get("net_pnl_yen_100"), t.get("entry_time"), t.get("exit_time")]
               for t in rep["trade_ledger"]])
        sheet("cap_blocked",
              ["strategy_id", "day", "am_pm", "symbol", "reason", "decision_grid", "trigger_ts"],
              [[c.get("strategy_id"), c.get("day"), c.get("am_pm"), c.get("symbol"),
                c.get("reason"), c.get("decision_grid"), c.get("trigger_ts")]
               for c in rep["cap_blocked"]])
        er_rows = []
        for r in rep["candidate_summary"]:
            for k, n in r["exit_reason_n"].items():
                er_rows.append([r["strategy_id"], k, n, r["exit_reason_pnl"].get(k)])
        sheet("exit_reasons", ["strategy_id", "reason", "n", "pnl"], er_rows)
        sheet("symbol_contribution", ["strategy_id", "symbol", "pnl"],
              [[r["strategy_id"], s, p]
               for r in rep["candidate_summary"]
               for s, p in r.get("symbol_pnl_top10", {}).items()])
        if rep.get("rolling_origin"):
            sheet("rolling_origin",
                  ["fold", "confirm_day", "selected", "confirm_pnl", "build_total"],
                  [[f["fold"], f["confirm_day"], f["selected_strategy_id"],
                    f["confirm_pnl"], f["build_total_pnl"]]
                   for f in rep["rolling_origin"]["folds"]])
        if rep.get("lodo_fixed"):
            sheet("lodo_fixed", ["held_out", "strategy_id", "ex_held_total", "ex_held_median"],
                  [[r["held_out_day"], r["strategy_id"], r["ex_held_total_pnl"],
                    r["ex_held_median_day_pnl"]] for r in rep["lodo_fixed"]["rows"]])
        if rep.get("lodo_reselect"):
            sheet("lodo_reselect", ["held_out", "selected", "held_pnl"],
                  [[r["held_out_day"], r["selected_strategy_id"], r["held_out_pnl"]]
                   for r in rep["lodo_reselect"]["rows"]])
        sens_rows = []
        for r in rep["candidate_summary"]:
            s = r["sensitivity_20260722"]
            sens_rows.append([r["strategy_id"], s.get("ex722_total_pnl"), s.get("ex722_pf"),
                              s.get("contribution_722_share_of_gross_positive"),
                              s.get("ex722_median_day_pnl"),
                              s.get("direction_agreement_with_full")])
        sheet("sensitivity_20260722",
              ["strategy_id", "ex722_pnl", "ex722_pf", "contrib_share", "ex722_median", "dir_agree"],
              sens_rows)
        sheet("base_comparison",
              ["strategy_id", "delta_pnl", "delta_n", "delta_dd", "delta_stop"],
              [[r["strategy_id"], r["base_comparison"]["delta_pnl"],
                r["base_comparison"]["delta_completed"],
                r["base_comparison"]["delta_max_dd"],
                r["base_comparison"]["delta_stop_loss_total"]]
               for r in rep["candidate_summary"]])
        sheet("ab_determinism", ["strategy_id", "sha_a", "sha_b", "match"],
              [[r["strategy_id"], r["sha_a"], r["sha_b"], r["match"]]
               for r in rep["ab_determinism"]["rows"]])
        sheet("tests", ["test", "outcome"],
              [[r["test"], r["outcome"]] for r in rep["tests"]["rows"]])
        sheet("safety_manifest", ["key", "value"],
              [["submit", 0], ["cancel", 0], ["live", 0],
               ["protected_match", rep["paper_protected_manifest"]["match"]],
               ["before_sha", rep["paper_protected_manifest"]["before_sha256"]],
               ["after_sha", rep["paper_protected_manifest"]["after_sha256"]]])
        wb.save(out_fp)

    report_mod.render_report_md = _md_b  # type: ignore
    report_mod.render_audit_xlsx = _xlsx_b  # type: ignore

    shas = atomic_publish(run_id, report)
    print("=== PHASE B COMPLETE — STOP ===")
    print(f"verdict={verdict}")
    print(f"selected={report.get('selected_candidate_id')}")
    print(f"qualified={report.get('qualified_candidates')}")
    for name, sha in shas.items():
        print(f"  {root / 'published' / name} sha={sha}")
    print("Shadow/Forward/Paper/Discord: NOT STARTED")


if __name__ == "__main__":
    main()
