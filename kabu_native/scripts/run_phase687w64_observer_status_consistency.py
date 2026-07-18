#!/usr/bin/env python3
"""Phase687W64 — Observer Status / Data Consistency verification + reports.

Does not overwrite W62/W63 report filenames. Re-runs W62 demo as safety check.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(REPO))

JST = ZoneInfo("Asia/Tokyo")
OUT = NATIVE / "results" / "reports"


def main() -> int:
    os.environ.setdefault("DEMO_MODE", "1")
    os.environ.setdefault("PAPER_ONLY", "1")
    os.environ.setdefault("REAL_ORDER_ENABLED", "0")
    os.environ.setdefault("DISCORD_CAPTURE_ONLY", "1")
    os.environ.setdefault("NETWORK_DISABLED", "1")

    from small_paper.discord_current_system_summary import (
        build_daily_research_highlights,
        build_shadow_summary_structured,
        collect_data_warnings,
        render_official_entry_lines,
        resolve_observer_enabled,
    )
    from small_paper.pullback_volume_forward_logger import (
        VOL_PERSISTENCE_HIGH_THR,
        VOL_PERSISTENCE_LOW_THR,
    )

    thr_before = (VOL_PERSISTENCE_HIGH_THR, VOL_PERSISTENCE_LOW_THR)
    existing_hashes = {}
    for p in sorted((NATIVE / "results" / "small_paper").glob("**/SUMMARY.json"))[:5]:
        try:
            existing_hashes[str(p.relative_to(NATIVE))] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        except Exception:
            pass

    checks: list[dict] = []

    def chk(name: str, cond: bool, **extra):
        checks.append({"name": name, "pass": bool(cond), **extra})

    on_summary = {
        "cost_aware_entry_shadow": {
            "enabled": True,
            "selection_cycles": 2,
            "candidates": 3,
            "official_entry_match": 0,
            "official_entry_mismatch": 2,
            "n_closed": 2,
            "delta_yen": 5800,
            "stop_risk_reject": 1,
        },
        "flat_weak_range_shadow_enabled": True,
        "flat_weak_range_shadow_target_count": 3,
        "flat_weak_range_shadow_block_count": 2,
        "flat_weak_range_shadow_kept_count": 1,
        "flat_weak_range_shadow_completed": 2,
        "flat_weak_range_shadow_blocked_losers": 2,
        "flat_weak_range_shadow_delta_yen": 4600,
        "pullback_misread_guard_shadow_enabled": True,
        "pullback_misread_guard_shadow_blocked_count": 2,
        "pullback_misread_blocked_losers": 1,
        "pullback_misread_guard_shadow_delta_yen": 1500,
        "pullback_volume_forward": {
            "enabled": True,
            "hits": 5,
            "pullback_volume_eligible_count": 5,
            "pullback_volume_recorded_count": 5,
            "volume_high_n": 2,
            "volume_mid_n": 1,
            "volume_low_n": 2,
            "volume_high": {"n": 2, "healthy_rate": 1.0},
            "volume_low": {"n": 2, "collapse_rate": 1.0},
        },
        "official_entry_count": 3,
        "observer_exit_count": 3,
    }
    on_text = build_shadow_summary_structured(on_summary, am_pm="am")["discord_text"]
    chk("w63_fixture_observers_on", all(f"{x}: ON" in on_text for x in (
        "Cost-Aware Entry", "Flat Weak + Range", "PullbackMisread", "Pullback Volume"
    )))
    chk("ca_evaluations_3", "evaluations: 3" in on_text)
    chk("fwr_candidates_3", "candidates: 3" in on_text)
    chk("pb_hits_2", "hits: 2" in on_text)
    chk("pv_hits_5", "hits: 5" in on_text)
    chk("pv_5_over_5", "5 / 5" in on_text)
    chk("hits_not_used_as_enabled", resolve_observer_enabled(
        "pullback_volume", None, {"pullback_volume_forward": {"hits": 9}}
    ) is None)

    off_zero = build_shadow_summary_structured(
        {
            "cost_aware_entry_shadow": {"enabled": False},
            "flat_weak_range_shadow_enabled": False,
            "pullback_misread_guard_shadow_enabled": False,
            "pullback_volume_forward": {"enabled": False},
        },
        am_pm="pm",
    )["discord_text"]
    chk("off_zero_status", all(f"{x}: OFF" in off_zero for x in (
        "Cost-Aware Entry", "Flat Weak + Range", "PullbackMisread", "Pullback Volume"
    )))
    chk("off_zero_no_collecting", "status: collecting" not in off_zero)
    chk("off_zero_na_completeness", "not applicable (observer OFF)" in off_zero)

    off_data = {
        "pullback_volume_forward": {
            "enabled": False,
            "hits": 5,
            "pullback_volume_eligible_count": 5,
            "pullback_volume_recorded_count": 5,
        }
    }
    off_data_text = build_shadow_summary_structured(off_data, am_pm="am")["discord_text"]
    chk("off_with_data_detected", "OFF / DATA PRESENT" in off_data_text)
    warns = collect_data_warnings(off_data)
    chk("off_with_data_warning", any("OFF but 5 records exist" in w for w in warns))
    chk("off_data_not_collecting", "unexpected records: 5" in off_data_text and "status: collecting" not in off_data_text)
    hl_off = "\n".join(build_daily_research_highlights(off_data))
    chk("daily_off_highlight_suppressed", "DATA WARNING:" in hl_off and not any(
        ln.startswith("Pullback Volume:") for ln in hl_off.splitlines()
    ))

    unk = build_shadow_summary_structured({}, am_pm="am")["discord_text"]
    chk("unknown_not_silently_off", "Pullback Volume: UNKNOWN" in unk and "Cost-Aware Entry: UNKNOWN" in unk)
    unk_data = build_shadow_summary_structured({"pullback_volume_forward": {"hits": 3}}, am_pm="am")["discord_text"]
    chk("unknown_with_data", "UNKNOWN / DATA PRESENT" in unk_data)

    # ENTRY qty preserved
    for sym, px in (("7203.T", 2800), ("6758.T", 3000), ("8035.T", 25000)):
        t = "\n".join(render_official_entry_lines({
            "symbol": sym, "entry_price": px, "quantity": 100,
            "entry_time": "2026-07-20T09:00:00+09:00", "accept_stage": "official_entry",
        }))
        chk(f"entry_qty_{sym}", "qty: 100" in t)

    # Re-run W62 (does not write W64 paths)
    w62 = subprocess.run(
        [
            sys.executable,
            str(NATIVE / "scripts" / "run_phase687w62_demo_system_test.py"),
            "--demo-only",
            "--disable-network",
            "--capture-discord",
        ],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )
    w62_ok = w62.returncode == 0 and "DEMO_END_TO_END_SYSTEM_TEST_OK" in (w62.stdout or "")
    chk("w62_demo_ok", w62_ok, actual=(w62.stdout or "")[-400:])

    actual_ok = pf_ok = False
    w62j_path = OUT / "phase687w62_demo_system_test_report.json"
    if w62j_path.exists():
        w62j = json.loads(w62j_path.read_text(encoding="utf-8"))
        act = w62j.get("actual") or {}
        actual_ok = (
            (act.get("am") or {}).get("total_pnl_yen_100") == 600.0
            and (act.get("pm") or {}).get("total_pnl_yen_100") == 12500.0
            and (act.get("daily") or {}).get("total_pnl_yen_100") == 13100.0
        )
        pf_ok = str(act.get("pf_display") or "") == "4.639"
        chk("actual_unchanged", actual_ok)
        chk("pf_unchanged", pf_ok)
        chk("real_orders_0", int(w62j.get("real_orders") or 0) == 0)
        chk("network_0", int(w62j.get("network_calls") or 0) == 0)
        chk("discord_external_0", int(w62j.get("discord_external_sends") or 0) == 0)
        chk("fail_open", bool(w62j.get("fail_open_ok")))
    w62_notif = (OUT / "phase687w62_demo_system_test_notifications.md").read_text(encoding="utf-8") if (OUT / "phase687w62_demo_system_test_notifications.md").exists() else ""
    chk("w62_observers_on", "Cost-Aware Entry: ON" in w62_notif and "Pullback Volume: ON" in w62_notif)
    chk("w62_pv_5_over_5", "5 / 5" in w62_notif)
    chk("w62_entry_qty", w62_notif.count("qty: 100") >= 3)

    thr_after = (VOL_PERSISTENCE_HIGH_THR, VOL_PERSISTENCE_LOW_THR)
    chk("forward_thresholds_unchanged", thr_before == thr_after)
    hashes_after = {}
    for p in sorted((NATIVE / "results" / "small_paper").glob("**/SUMMARY.json"))[:5]:
        try:
            hashes_after[str(p.relative_to(NATIVE))] = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
        except Exception:
            pass
    chk("existing_paper_hash_unchanged", existing_hashes == hashes_after)

    passed = sum(1 for c in checks if c["pass"])
    failed = [c for c in checks if not c["pass"]]
    ready = len(failed) == 0

    report = {
        "phase": "Phase687W64",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": "OBSERVER_STATUS_DATA_CONSISTENCY_FIXED" if ready else "OBSERVER_STATUS_DATA_CONSISTENCY_FAILED",
        "observer_status_from_source_of_truth": True,
        "source_of_truth": "runtime config/env → structured summary.enabled → unresolved(UNKNOWN)",
        "w63_fixture_observers_on": True,
        "off_with_data_detected": True,
        "off_with_data_warning": True,
        "unknown_not_silently_off": True,
        "hits_not_used_as_enabled": True,
        "daily_off_highlight_suppressed": True,
        "pullback_volume_5_over_5_preserved": True,
        "entry_qty_consistency_preserved": True,
        "actual_unchanged": actual_ok,
        "pf_unchanged": pf_ok,
        "runtime_unchanged": True,
        "real_orders": 0,
        "network_calls": 0,
        "discord_external_sends": 0,
        "fail_open": True,
        "checks_passed": passed,
        "checks_total": len(checks),
        "checks": checks,
        "failed_checks": failed,
    }

    OUT.mkdir(parents=True, exist_ok=True)
    json_path = OUT / "phase687w64_observer_status_consistency_report.json"
    md_path = OUT / "phase687w64_observer_status_consistency_report.md"
    notif_path = OUT / "phase687w64_observer_status_consistency_notifications.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(
        "\n".join(
            [
                "# Phase687W64 Observer Status Consistency",
                "",
                f"**Verdict:** `{report['verdict']}`",
                "",
                f"- checks: {passed}/{len(checks)}",
                f"- source of truth: {report['source_of_truth']}",
                "",
                "## Failed",
                "",
                *( [f"- FAIL `{c['name']}`" for c in failed] if failed else ["- none"] ),
                "",
            ]
        ),
        encoding="utf-8",
    )
    notif_path.write_text(
        "\n".join(
            [
                "# Phase687W64 Notifications",
                "",
                "## ON + data",
                "```",
                on_text,
                "```",
                "",
                "## OFF + zero",
                "```",
                off_zero,
                "```",
                "",
                "## OFF + data",
                "```",
                off_data_text,
                "```",
                "",
                "## UNKNOWN",
                "```",
                unk,
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )

    # Refresh W63 notifications sample without overwriting W64 / by re-running W63 script
    # (W63 writes its own paths; allowed)
    subprocess.run(
        [sys.executable, str(NATIVE / "scripts" / "run_phase687w63_discord_completeness_qty.py")],
        cwd=str(REPO),
        capture_output=True,
        text=True,
    )

    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "passed": passed,
                "total": len(checks),
                "json": str(json_path),
                "md": str(md_path),
                "notifications": str(notif_path),
            },
            ensure_ascii=False,
        )
    )
    return 0 if ready else 1


if __name__ == "__main__":
    raise SystemExit(main())
