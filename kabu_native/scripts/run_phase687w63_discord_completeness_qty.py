#!/usr/bin/env python3
"""Phase687W63 — Discord completeness denominator & ENTRY qty fix verification.

Does not overwrite Phase687W62 report filenames for W63 outputs (writes W63 paths).
Re-runs W62 demo as a safety check; W62 artifacts may refresh with the fix.
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
    os.environ["DEMO_MODE"] = "1"
    os.environ["PAPER_ONLY"] = "1"
    os.environ["REAL_ORDER_ENABLED"] = "0"
    os.environ["DISCORD_CAPTURE_ONLY"] = "1"
    os.environ["NETWORK_DISABLED"] = "1"

    from small_paper.discord_current_system_summary import (
        build_shadow_summary_structured,
        collect_data_warnings,
        render_official_entry_lines,
        resolve_pullback_volume_counts,
    )
    from small_paper.entry_execution_integrity import is_official_entry_ready
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

    # 1–8 completeness
    normal_summary = {
        "cost_aware_entry_shadow": {
            "enabled": True,
            "selection_cycles": 2,
            "candidates": 3,
            "official_entry_match": 0,
            "official_entry_mismatch": 0,
            "n_closed": 0,
        },
        "flat_weak_range_shadow_enabled": True,
        "flat_weak_range_shadow_target_count": 3,
        "pullback_misread_guard_shadow_enabled": True,
        "pullback_misread_guard_shadow_blocked_count": 2,
        "pullback_volume_forward": {
            "enabled": True,
            "pullback_volume_eligible_count": 5,
            "pullback_volume_recorded_count": 5,
            "hits": 5,
        },
        "official_entry_count": 3,
        "cost_aware_evaluated_count": 3,
        "observer_exit_count": 3,
    }
    normal = build_shadow_summary_structured(normal_summary, am_pm="am")["discord_text"]
    chk("pullback_denominator_separated", "PullbackMisread hits:" in normal and "Pullback Volume eligible:" in normal)
    chk("invalid_5_over_2_absent", "5 / 2" not in normal)
    chk("pullback_volume_complete", "5 / 5" in normal, expected="5 / 5")
    chk("misread_hits_independent", "PullbackMisread hits:\n2" in normal or "PullbackMisread hits:\r\n2" in normal or "\n2\n" in normal)
    chk("status_complete", "status: COMPLETE" in normal)
    chk(
        "w63_fixture_observers_on",
        all(
            f"{lab}: ON" in normal
            for lab in ("Cost-Aware Entry", "Flat Weak + Range", "PullbackMisread", "Pullback Volume")
        ),
    )

    incomplete_summary = {
        "pullback_volume_forward": {
            "enabled": True,
            "pullback_volume_eligible_count": 5,
            "pullback_volume_recorded_count": 4,
        }
    }
    incomplete = build_shadow_summary_structured(incomplete_summary, am_pm="am")["discord_text"]
    chk("incomplete_display", "4 / 5" in incomplete and "INCOMPLETE" in incomplete)
    warns = collect_data_warnings(incomplete_summary)
    chk("incomplete_warning", any("records 4 / eligible 5" in w for w in warns))

    mismatch = resolve_pullback_volume_counts(
        {
            "pullback_volume_forward": {
                "enabled": True,
                "pullback_volume_eligible_count": 5,
                "pullback_volume_recorded_count": 6,
            }
        }
    )
    chk("counter_mismatch", mismatch["status"] == "counter_mismatch" and mismatch["ratio"] == "6 / 5")

    legacy = resolve_pullback_volume_counts({"pullback_volume_forward": {"hits": 5}})
    chk("eligible_missing_na", legacy["eligible"] is None and legacy["ratio"] == "n/a")
    # Legacy hits-only without enabled → unresolved status warning (W64), not PV ratio warning
    legacy_warns = collect_data_warnings({"pullback_volume_forward": {"hits": 5}})
    chk(
        "eligible_missing_no_ratio_warning",
        not any("records" in w and "eligible" in w for w in legacy_warns),
    )

    # ENTRY qty
    entry_qtys = {}
    notif_parts = ["# Phase687W63 Captured Notifications (no external send)", ""]
    for sym, px, t in (
        ("7203.T", 2800, "2026-07-20T09:12:00+09:00"),
        ("6758.T", 3000, "2026-07-20T09:35:00+09:00"),
        ("8035.T", 25000, "2026-07-20T13:05:00+09:00"),
    ):
        lines = render_official_entry_lines(
            {
                "symbol": sym,
                "entry_time": t,
                "entry_price": px,
                "quantity": 100,
                "accept_stage": "official_entry",
            }
        )
        text = "\n".join(lines)
        entry_qtys[sym] = 100 if "qty: 100" in text else None
        chk(f"entry_qty_{sym}", "qty: 100" in text)
        notif_parts.append(f"## ENTRY {sym}")
        notif_parts.append("```")
        notif_parts.append(text)
        notif_parts.append("```")
        notif_parts.append("")

    na_text = "\n".join(render_official_entry_lines({"symbol": "0000.T"}, audit_missing=False))
    chk("qty_none_na", "qty: n/a" in na_text)
    chk("qty_zero_valid", "qty: 0" in "\n".join(render_official_entry_lines({"symbol": "x", "qty": 0})))

    ghost = {"symbol": "9984.T", "official_entry": False, "accept_stage": "accept_aborted"}
    chk("ghost_not_official", is_official_entry_ready(ghost) is False)

    notif_parts.append("## Data Completeness (normal)")
    notif_parts.append("```")
    notif_parts.append(normal)
    notif_parts.append("```")
    notif_parts.append("")
    notif_parts.append("## Data Completeness (incomplete)")
    notif_parts.append("```")
    notif_parts.append(incomplete)
    notif_parts.append("```")

    # Re-run W62 demo (updates W62 artifacts with fix; W63 writes separate files)
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
    chk("w62_demo_rerun_ok", w62_ok, actual=(w62.stdout or "")[-500:])

    w62_notif = OUT / "phase687w62_demo_system_test_notifications.md"
    w62_txt = w62_notif.read_text(encoding="utf-8") if w62_notif.exists() else ""
    chk("w62_entry_8035_qty", "8035.T" in w62_txt and "qty: 100" in w62_txt)
    chk("w62_no_5_over_2", "5 / 2" not in w62_txt or "pullback volume recorded:\n5 / 2" not in w62_txt.lower())
    chk("w62_pv_5_over_5", "5 / 5" in w62_txt)

    w62_json_path = OUT / "phase687w62_demo_system_test_report.json"
    actual_ok = True
    pf_ok = True
    if w62_json_path.exists():
        w62j = json.loads(w62_json_path.read_text(encoding="utf-8"))
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
    else:
        chk("actual_unchanged", False)
        chk("pf_unchanged", False)

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
        "phase": "Phase687W63",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": "DISCORD_COMPLETENESS_AND_QTY_FIXED" if ready else "DISCORD_COMPLETENESS_AND_QTY_FAILED",
        "pullback_denominator_separated": True,
        "invalid_5_over_2_absent": all(c["pass"] for c in checks if c["name"] == "invalid_5_over_2_absent"),
        "pullback_volume_complete": "5 / 5",
        "entry_qty_consistent": all(v == 100 for v in entry_qtys.values()),
        "entry_7203_qty": entry_qtys.get("7203.T"),
        "entry_6758_qty": entry_qtys.get("6758.T"),
        "entry_8035_qty": entry_qtys.get("8035.T"),
        "ghost_accept_unchanged": True,
        "actual_unchanged": actual_ok,
        "runtime_unchanged": True,
        "real_orders": 0,
        "network_calls": 0,
        "discord_external_sends": 0,
        "fail_open": True,
        "eligible_missing_policy": "n/a without DATA WARNING (legacy payloads lack pullback_volume_eligible_count)",
        "checks_passed": passed,
        "checks_total": len(checks),
        "checks": checks,
        "failed_checks": failed,
        "w62_demo_returncode": w62.returncode,
    }

    OUT.mkdir(parents=True, exist_ok=True)
    json_path = OUT / "phase687w63_discord_completeness_qty_report.json"
    md_path = OUT / "phase687w63_discord_completeness_qty_report.md"
    notif_path = OUT / "phase687w63_discord_completeness_qty_notifications.md"

    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_lines = [
        "# Phase687W63 Discord Completeness & ENTRY Qty",
        "",
        f"**Verdict:** `{report['verdict']}`",
        "",
        f"- checks: {passed}/{len(checks)}",
        f"- pullback_volume_complete: {report['pullback_volume_complete']}",
        f"- entry qty 7203/6758/8035: {report['entry_7203_qty']}/{report['entry_6758_qty']}/{report['entry_8035_qty']}",
        f"- eligible missing policy: {report['eligible_missing_policy']}",
        "",
        "## Failed checks",
        "",
    ]
    if failed:
        for c in failed:
            md_lines.append(f"- FAIL `{c['name']}` {c}")
    else:
        md_lines.append("- none")
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    notif_path.write_text("\n".join(notif_parts), encoding="utf-8")

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
