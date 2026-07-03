"""Investigation B: runtime config diff + code diff tables."""
import csv
import json
import subprocess

OUT = "results/reports/fresh_pbv2_reanalysis"

SESSIONS = [
    ("20260624", "081514", "AM"), ("20260624", "122521", "PM"),
    ("20260625", "080340", "AM"), ("20260625", "122535", "PM"),
    ("20260629", "080236", "AM"), ("20260629", "122526", "PM"),
    ("20260630", "091118", "AM"),
    ("20260701", "080616", "AM"),
]

CONFIG_KEYS = [
    # execution / structure
    ("config_sha256", "lsc"), ("universe_csv_path", "lsc"), ("poll_interval_sec", "lsc"),
    ("session_start", "lsc"), ("session_end", "lsc"), ("intraday_refresh_enabled", "lsc"),
    ("core_runtime_mode", "lsc"), ("pre625_runtime_structure_mode", "lsc"),
    ("extension_bus_enabled", "lsc"), ("audit_enabled", "lsc"),
    ("live_trading_enabled", "lsc"), ("live_order_dry_run_enabled", "lsc"),
    # gates (effective values recorded in summary)
    ("entry_score_v2_min", "sum"), ("momentum_score_cutoff_max", "sum"),
    ("daytrade_suitability_enabled", "sum"), ("daytrade_suitability_threshold", "sum"),
    ("high_drift_guard_enabled", "sum"), ("late_chase_guard_enabled", "sum"),
    ("enable_near_day_high_low_momentum_dynamic40_guard", "sum"),
    ("classic_late_chase_rsi_guard_enabled", "sum"),
    ("reentry_rsi_guard_enabled", "sum"), ("entry_quality_guard_enabled", "sum"),
    ("or_overlay_enabled", "sum"), ("cap_pbv2", "sum"), ("cap_or", "sum"),
    ("or_max_update_count", "sum"),
    ("entry_cluster_guard_enabled", "sum"), ("entry_cluster_guard_exception_enabled", "sum"),
    ("entry_cluster_guard_liquidity_burst_threshold", "sum"),
    ("cluster_guard_reject_count", "sum"), ("cluster_guard_blocked_cluster_counts", "sum"),
    ("stop_low_mfe_guard_enabled", "sum"),
    ("vol_liq_startup_cache_enabled", "sum"), ("vol_liq_cache_status", "sum"),
    ("volume_gate_relaxation_shadow_enabled", "sum"),
    ("freshness_semantics_v2_enabled", "sum"),
    ("event_stale_reject_count", "sum"), ("board_stale_reject_count", "sum"),
    ("trade_stale_tag_count", "sum"),
    ("max_concurrent_positions", "sum"),
    ("position_cap_mode", "sum"),
    ("accepted_count", "sum"), ("pbv2_count", "sum"), ("or_count", "sum"),
]

data = {}
for d, s, ap in SESSIONS:
    base = f"results/small_paper/{d}/live_session_{s}"
    lsc = json.load(open(f"{base}/live_session_config.json", encoding="utf-8"))
    sm = json.load(open(f"{base}/small_paper_summary.json", encoding="utf-8"))
    data[(d, ap)] = (lsc, sm)

cols = [f"{d}_{ap}" for d, s, ap in SESSIONS]
rows = []
for key, src in CONFIG_KEYS:
    vals = []
    for d, s, ap in SESSIONS:
        lsc, sm = data[(d, ap)]
        v = (lsc if src == "lsc" else sm).get(key, "(absent)")
        if isinstance(v, dict):
            v = json.dumps(v, ensure_ascii=False)
        if isinstance(v, str) and "universe" in key:
            v = v.split("\\")[-1]
        vals.append(v)
    differs = len(set(str(v) for v in vals)) > 1
    rows.append([key, src, differs] + vals)

# effective csub set: not summarized; derive from evidence
rows.append(["entry_cluster_guard_reject_csubs(effective,evidence)", "derived", True,
             "(guard absent)", "(guard absent)", "(guard absent)", "(guard absent)",
             "[0,2,3,5] (c3_s5 rejects=4269)", "[0,2,3,5] (c3_s5 rejects=5519)",
             "[0,2,3,5] (c3_s5 rejects=6339)", "[] (rejects=0; Phase606 rollback in working YAML)"])
rows.append(["entry_freshness_board_fallback(effective,evidence)", "derived", True,
             "absent", "absent", "absent", "absent",
             "absent(v1 stale=60%)", "absent(v1 stale=67%)",
             "active(fallback_used=16571, stale 5%)", "superseded by semantics v2"])
rows.append(["runtime_code_generation(evidence)", "derived", True,
             "pre-OR-overlay (no or_overlay rejects; ~196a559+)",
             "pre-OR-overlay (no or_overlay rejects)",
             "OR overlay active (f50c5a7-era working tree)",
             "OR overlay active",
             "924bb1e-era working tree (+cluster/stop_low_mfe/cache/live-order)",
             "924bb1e-era working tree",
             "924bb1e-era +board fallback",
             "Phase616 core-runtime restructure (core_runtime_mode=FULL_EXTENSION, pbv2_internal_reason logged)"])

with open(f"{OUT}/fresh_pbv2_reanalysis_config_diff.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["key", "source", "differs"] + cols)
    w.writerows(rows)

# ---- code diff table ----
def numstat(a, b, label):
    out = subprocess.run(["git", "diff", "--numstat", a] + ([b] if b else []) + ["--", "src", "configs"],
                         capture_output=True, text=True).stdout
    rows = []
    for line in out.strip().splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            rows.append([label, parts[2], parts[0], parts[1]])
    return rows

code_rows = []
code_rows += numstat("196a559", "f50c5a7", "196a559(6/21)->f50c5a7(6/26) [between 6/25 and 6/26 sessions]")
code_rows += numstat("f50c5a7", "924bb1e", "f50c5a7(6/26)->924bb1e(6/28) [before 6/29 sessions]")
code_rows += numstat("924bb1e", None, "924bb1e(6/28)->working tree [6/29..7/3 uncommitted]")

with open(f"{OUT}/fresh_pbv2_reanalysis_code_diff.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["range", "file", "added", "deleted"])
    w.writerows(code_rows)
    w.writerow(["KEY_BEHAVIOR", "or_overlay_entry.py + pilot_runner.py", "", ""])
    w.writerow(["note1", "OR overlay introduced 6/26 commit: _maybe_try_or_overlay_entry REPLACES PBv2 reject reason with or_overlay_not_candidate for symbols without open position (masking)", "", ""])
    w.writerow(["note2", "entry_cluster_guard introduced 6/26 (Phase549) + model path fix 6/28 (Phase552); active from 6/29 sessions with reject_csubs=[0,2,3,5]", "", ""])
    w.writerow(["note3", "stop_low_mfe_guard (Phase557), vol_liq_startup_cache (Phase575), volume_gate_relaxation_shadow (Phase590), live order dry-run (Phase591) added 6/28 commit", "", ""])
    w.writerow(["note4", "working tree (uncommitted, ran 7/1+): Phase616 core_runtime_mode restructure, Phase606 rollback stop_low_mfe=false + reject_csubs=[], Phase621 freshness_semantics_v2, pbv2_internal_reason logging", "", ""])
print("config_diff + code_diff written")
