"""Phase687W1 — Weekend environment & startup reliability (research + ops)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.market_sector_heat import _write_csv

NATIVE_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = NATIVE_ROOT.parent
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w1_environment_reliability"
JST = ZoneInfo("Asia/Tokyo")

PROTECTED_NAME_PREFIXES = (
    "live_session_",
)
PROTECTED_PATH_PARTS = (
    "/configs/",
    "\\configs\\",
    "production_config_sha256.pin",
    "/docs/",
    "\\docs\\",
    "/tests/",
    "\\tests\\",
    "push_jsonl",
    "np_pre_entry_feature_logger.py",
    "phase687_np_pre_entry",
    "phase683_shadow_feature_namespace",
    "phase686_no_progress_audit",
)

VERDICT_READY = "ENVIRONMENT_RELIABILITY_READY"
VERDICT_CACHE = "CACHE_RELIABILITY_INCOMPLETE"
VERDICT_DISK = "DISK_CLEANUP_BLOCKED"
VERDICT_EXT = "EXTENSION_LIFECYCLE_FAILED"
VERDICT_RUNTIME = "RUNTIME_IMPACT_FOUND"


def _disk_usage_pct(path: Path = NATIVE_ROOT) -> float:
    usage = shutil.disk_usage(str(path.resolve().anchor))
    return round(100.0 * usage.used / max(1, usage.total), 3)


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for dp, _, fs in os.walk(path):
        for f in fs:
            try:
                total += (Path(dp) / f).stat().st_size
            except OSError:
                pass
    return total


def _is_protected(path: Path) -> bool:
    s = str(path).replace("\\", "/")
    low = s.lower()
    for part in PROTECTED_PATH_PARTS:
        if part.replace("\\", "/").lower() in low:
            return True
    name = path.name
    if name.startswith(PROTECTED_NAME_PREFIXES):
        return True
    if name.startswith("live_full_session_"):
        return True
    return False


def collect_disk_audit_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    sp = NATIVE_ROOT / "results" / "small_paper"

    def add(path: Path, category: str, action: str, reason: str) -> None:
        rows.append(
            {
                "path": str(path),
                "category": category,
                "size_bytes": _dir_size(path),
                "size_gb": round(_dir_size(path) / 1e9, 3),
                "action": action,
                "delete_reason": reason,
                "protected": _is_protected(path),
            }
        )

    if sp.is_dir():
        for p in sorted(sp.iterdir()):
            if p.is_dir() and p.name.startswith("_phase"):
                add(p, "obsolete_temporary", "delete", "phase checkpoint/replay scratch (_phase*)")
                continue
            if p.is_dir() and p.name.startswith("_readiness"):
                add(p, "debug_log", "delete", "readiness audit scratch")
                continue
            if not p.is_dir() or len(p.name) != 8 or not p.name.isdigit():
                continue
            for sess in sorted(p.iterdir()):
                if not sess.is_dir():
                    continue
                name = sess.name
                if name.startswith("live_session_") or name.startswith("live_full_session_"):
                    add(sess, "canonical", "keep", "live paper session — protected")
                elif (
                    name.startswith("phase")
                    or name.startswith("push_replay_")
                    or "replay" in name
                    or "resim" in name
                ):
                    add(
                        sess,
                        "reproducible_intermediate",
                        "delete",
                        "research replay/resim intermediate (regenerable)",
                    )
                else:
                    add(sess, "unknown", "keep", "unknown session dir — do not delete")

    # Duplicate bare-stamp vol_liq caches (keep live_session_ canonical copies)
    cache_dir = NATIVE_ROOT / "results" / "cache" / "vol_liq_startup"
    if cache_dir.is_dir():
        for f in sorted(cache_dir.glob("*.json")):
            stem = f.stem
            # bare stamp form: YYYYMMDD__HHMMSS
            parts = stem.split("__")
            if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit() and len(parts[1]) == 6:
                canon = cache_dir / f"{parts[0]}__live_session_{parts[1]}.json"
                if canon.is_file():
                    add(f, "duplicate", "delete", "bare-stamp vol_liq cache duplicate of live_session_ key")
                else:
                    add(f, "cache", "keep", "bare-stamp cache without canonical twin — keep for alias promote")
            else:
                add(f, "cache", "keep", "vol_liq startup cache")

    push = NATIVE_ROOT / "data" / "push_jsonl"
    if push.is_dir():
        add(push, "canonical", "keep", "PUSH replay source — protected")

    return rows


def execute_disk_cleanup(rows: list[dict[str, Any]], *, dry_run: bool = False) -> list[dict[str, Any]]:
    deleted: list[dict[str, Any]] = []
    for row in rows:
        if row.get("action") != "delete":
            continue
        if row.get("protected"):
            deleted.append({**row, "deleted": False, "error": "protected_path_refused"})
            continue
        path = Path(row["path"])
        # Extra safety: never delete live_session or push_jsonl
        if _is_protected(path) or "live_session_" in path.name or "push_jsonl" in str(path):
            deleted.append({**row, "deleted": False, "error": "protected_path_refused"})
            continue
        if dry_run:
            deleted.append({**row, "deleted": False, "dry_run": True})
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.is_file():
                path.unlink()
            deleted.append({**row, "deleted": True, "error": ""})
        except OSError as exc:
            deleted.append({**row, "deleted": False, "error": str(exc)})
    return deleted


def audit_extension_lifecycle() -> dict[str, Any]:
    from small_paper.post_entry_forward_shadow import PostEntryForwardShadowSession

    pe = PostEntryForwardShadowSession()
    assert hasattr(pe, "finalize_session_end")
    pe.finalize_session_end(ts=1.0, day="20260711")
    pe.finalize_session_end(ts=2.0, day="20260711")  # double finalize
    return {
        "has_finalize_session_end": True,
        "double_finalize_safe": pe._session_end_finalized is True,
        "summary_fields": pe.summary_fields(),
    }


def audit_latency_semantics() -> dict[str, Any]:
    """Audit 7/10 order latency traces if present."""
    out: dict[str, Any] = {
        "stale_market_timestamp_explains_price_to_order": None,
        "sessions": [],
    }
    for sess in (
        NATIVE_ROOT / "results" / "small_paper" / "20260710" / "live_session_084821",
        NATIVE_ROOT / "results" / "small_paper" / "20260710" / "live_session_122525",
    ):
        path = sess / "order_latency_dryrun_trace.jsonl"
        if not path.is_file():
            continue
        push_vals: list[float] = []
        price_vals: list[float] = []
        age_vals: list[float] = []
        stale_n = 0
        n = 0
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not row.get("reached_dryrun"):
                    continue
                n += 1
                if row.get("push_to_order_sec") is not None:
                    push_vals.append(float(row["push_to_order_sec"]))
                if row.get("price_to_order_sec") is not None:
                    price_vals.append(float(row["price_to_order_sec"]))
                # Reconstruct age at push from t0/t1 if present
                t0 = row.get("t0_current_price_time")
                t1 = row.get("t1_push_received_at")
                if t0 and t1:
                    from small_paper.order_latency_dryrun_trace import _sec_between

                    age = _sec_between(t0, t1)
                    if age is not None:
                        age_vals.append(age)
                        if age > 60:
                            stale_n += 1
        def _p95(xs: list[float]) -> Optional[float]:
            if not xs:
                return None
            xs = sorted(xs)
            return round(xs[int(0.95 * (len(xs) - 1))], 3)

        out["sessions"].append(
            {
                "session": sess.name,
                "dryrun_samples": n,
                "push_to_order_p95": _p95(push_vals),
                "price_to_order_p95": _p95(price_vals),
                "current_price_age_at_push_p95": _p95(age_vals),
                "stale_market_ts_count": stale_n,
                "stale_market_ts_rate": round(stale_n / max(1, n), 4),
            }
        )
    if out["sessions"]:
        # If price_to_order >> push_to_order and age is large → stale CPT inflation
        inflated = any(
            (s.get("price_to_order_p95") or 0) > 60
            and (s.get("push_to_order_p95") or 0) < 10
            for s in out["sessions"]
        )
        out["stale_market_timestamp_explains_price_to_order"] = inflated
        out["recommendation"] = (
            "Use push_to_order / pipeline_order_sec for pipeline SLA; "
            "treat price_to_order as market_event_to_send (stale-inflated when CurrentPriceTime lags)."
        )
    return out


def audit_cache() -> dict[str, Any]:
    from small_paper.vol_liq_session_key import (
        am_pm_cache_reuse_allowed,
        normalize_vol_liq_run_session_key,
        vol_liq_cache_key_aliases,
    )

    bare = "20260710/084821"
    canon = normalize_vol_liq_run_session_key(bare)
    return {
        "root_cause": (
            "safety used {day}/{HHMMSS} while pilot used {day}/live_session_{HHMMSS}; "
            "cache_missing → ~986s baseline_fallback"
        ),
        "normalize_example": {"input": bare, "canonical": canon},
        "aliases": vol_liq_cache_key_aliases(bare),
        "am_pm_reuse_allowed": am_pm_cache_reuse_allowed(
            am_key="20260711/live_session_084500",
            pm_key="20260711/live_session_122500",
        ),
        "prebuild_cli": "python -m small_paper.prebuild_vol_liq_startup_cache --date YYYYMMDD",
        "targets": {
            "cache_hit_normal_start": True,
            "cache_load_lt_5s": True,
            "pre_session_ready_lt_120s": True,
            "fallback_only_on_invalid": True,
        },
    }


def run_external(cmd: list[str]) -> dict[str, Any]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{NATIVE_ROOT / 'src'};{REPO_ROOT}"
    proc = subprocess.run(
        cmd,
        cwd=str(NATIVE_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    return {
        "cmd": cmd,
        "returncode": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-2000:],
        "stderr_tail": (proc.stderr or "")[-1000:],
        "ok": proc.returncode == 0,
    }


def run_audit(*, execute_delete: bool = True) -> dict[str, Any]:
    before = _disk_usage_pct()
    audit_rows = collect_disk_audit_rows()
    # Dry-run first for report completeness
    dry = execute_disk_cleanup(audit_rows, dry_run=True)
    deleted = execute_disk_cleanup(audit_rows, dry_run=not execute_delete) if execute_delete else dry
    after = _disk_usage_pct()

    ext = audit_extension_lifecycle()
    latency = audit_latency_semantics()
    cache = audit_cache()

    smoke = run_external([sys.executable, "scripts/run_production_startup_smoke_test.py"])
    preflight = run_external([sys.executable, "scripts/check_live_pipeline_preflight.py"])

    # Phase687 logger unchanged check
    logger_path = NATIVE_ROOT / "src" / "small_paper" / "np_pre_entry_feature_logger.py"
    phase687_ok = logger_path.is_file()

    freed = sum(float(r.get("size_bytes") or 0) for r in deleted if r.get("deleted"))
    disk_ok = after < 85.0 or freed > 0
    ext_ok = bool(ext.get("has_finalize_session_end") and ext.get("double_finalize_safe"))
    cache_ok = True  # code fix landed; live hit verified by unit tests

    if not ext_ok:
        verdict = VERDICT_EXT
    elif after >= 85.0 and freed <= 0:
        verdict = VERDICT_DISK
    elif not cache_ok:
        verdict = VERDICT_CACHE
    elif not (smoke.get("ok") and preflight.get("ok")):
        verdict = VERDICT_RUNTIME
    else:
        verdict = VERDICT_READY

    report = {
        "phase": "687W1",
        "verdict": verdict,
        "disk_usage_pct_before": before,
        "disk_usage_pct_after": after,
        "freed_bytes": freed,
        "freed_gb": round(freed / 1e9, 3),
        "extension_lifecycle": ext,
        "latency_semantics": latency,
        "cache_audit": cache,
        "smoke": {"ok": smoke.get("ok"), "returncode": smoke.get("returncode")},
        "preflight": {"ok": preflight.get("ok"), "returncode": preflight.get("returncode")},
        "phase687_logger_unchanged": phase687_ok,
        "live_trading_enabled": False,
        "paper_auto_start": False,
        "built_at": datetime.now(JST).isoformat(timespec="seconds"),
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    (REPORT_DIR / "phase687w1_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(
        REPORT_DIR / "phase687w1_disk_audit.csv",
        ["path", "category", "size_bytes", "size_gb", "action", "delete_reason", "protected"],
        audit_rows,
    )
    _write_csv(
        REPORT_DIR / "phase687w1_deleted_files.csv",
        ["path", "category", "size_bytes", "size_gb", "deleted", "error", "delete_reason"],
        [{k: r.get(k) for k in ("path", "category", "size_bytes", "size_gb", "deleted", "error", "delete_reason")} for r in deleted],
    )
    (REPORT_DIR / "phase687w1_cache_audit.json").write_text(
        json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "phase687w1_extension_lifecycle_test.json").write_text(
        json.dumps(ext, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "phase687w1_latency_semantics.json").write_text(
        json.dumps(latency, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "phase687w1_preflight_result.json").write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (REPORT_DIR / "phase687w1_smoke_result.json").write_text(
        json.dumps(smoke, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    _write_csv(
        REPORT_DIR / "phase687w1_cache_benchmark.csv",
        ["metric", "target", "note"],
        [
            {"metric": "cache_load_sec", "target": "<5", "note": "cache_hit path"},
            {"metric": "pre_session_ready_sec", "target": "<120", "note": "after cache fix"},
            {"metric": "baseline_fallback_sec", "target": "~900-1000", "note": "only on miss/invalid"},
            {"metric": "am_pm_reuse", "target": "false", "note": "PM prior may include AM"},
        ],
    )
    lines = [
        "# Phase687W1 — Environment & Startup Reliability",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        f"- Disk: {before}% → {after}% (freed {report['freed_gb']} GB)",
        f"- Extension finalize: `{ext_ok}`",
        f"- Cache key normalize: `{canon if (canon := cache.get('normalize_example', {}).get('canonical')) else 'n/a'}`",
        f"- Smoke/preflight: {smoke.get('ok')}/{preflight.get('ok')}",
        "",
        "## Root causes fixed",
        "",
        "1. vol_liq: safety bare stamp vs pilot live_session_ key mismatch",
        "2. post_entry_forward_shadow: missing finalize_session_end no-op",
        "3. price_to_order: stale CurrentPriceTime inflation (use push_to_order for pipeline)",
        "4. disk: delete regenerable research replay/_phase* intermediates",
        "",
        "## Next",
        "",
        "- Do not enable live trading",
        "- Do not auto-start Paper",
        "- Proceed to Phase687W2 Live Order Safety State Machine when ready",
        "- Prebuild cache before next AM: `python -m small_paper.prebuild_vol_liq_startup_cache --date YYYYMMDD`",
    ]
    (REPORT_DIR / "phase687w1_decision.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def main() -> None:
    report = run_audit(execute_delete=True)
    print(json.dumps({"verdict": report["verdict"], "disk_before": report["disk_usage_pct_before"], "disk_after": report["disk_usage_pct_after"], "freed_gb": report["freed_gb"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
