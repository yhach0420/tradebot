#!/usr/bin/env python3
"""
Phase 100: Verify full JPX master setup and dynamic-universe readiness (--skip-kabu only).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
TRADABLE_MIN = 500


def _bootstrap() -> tuple[Path, Path]:
    script = Path(__file__).resolve()
    native_root = script.parents[1]
    repo_root = script.parents[2]
    return repo_root, native_root


def _scan_raw(raw_dir: Path) -> list[dict]:
    if not raw_dir.is_dir():
        return []
    out = []
    for p in sorted(raw_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in (".xlsx", ".xls", ".csv"):
            out.append(
                {
                    "name": p.name,
                    "path": str(p),
                    "mtime": p.stat().st_mtime,
                    "size_bytes": p.stat().st_size,
                }
            )
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def main() -> int:
    repo_root, native_root = _bootstrap()
    for p in (native_root / "src", repo_root):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)

    parser = argparse.ArgumentParser(description="Phase 100 JPX master setup check")
    parser.add_argument("--day-stamp", default=None)
    parser.add_argument("--allow-sample", action="store_true")
    parser.add_argument("--skip-build", action="store_true", help="Only scan raw dir, do not rebuild")
    args = parser.parse_args()

    day_stamp = args.day_stamp or datetime.now(JST).strftime("%Y%m%d")
    raw_dir = repo_root / "data" / "jpx" / "raw"
    reports_dir = native_root / "results" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    raw_listing = _scan_raw(raw_dir)
    has_official = any(
        p["name"].lower() in ("listed_issues.xlsx", "listed_issues.xls", "listed_issues.csv")
        for p in raw_listing
    )

    build_payload: dict = {}
    if not args.skip_build:
        cmd = [
            sys.executable,
            str(native_root / "scripts" / "build_jpx_symbol_master.py"),
            "--date-stamp",
            day_stamp,
        ]
        if args.allow_sample:
            cmd.append("--allow-sample")
        proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, timeout=300)
        build_payload["build_exit_code"] = proc.returncode
        build_payload["build_stdout"] = proc.stdout.strip()
        p100 = reports_dir / f"phase100_jpx_master_setup_check_{day_stamp}.json"
        if p100.is_file():
            build_payload = json.loads(p100.read_text(encoding="utf-8"))

    dyn_payload: dict = {}
    tradable_count = int(build_payload.get("tradable_count") or 0)
    if tradable_count >= TRADABLE_MIN and build_payload.get("ready_for_dynamic_universe_build"):
        cmd = [
            sys.executable,
            str(native_root / "scripts" / "build_dynamic_universe.py"),
            "--skip-kabu",
            "--date-stamp",
            day_stamp,
        ]
        proc = subprocess.run(cmd, cwd=str(repo_root), capture_output=True, text=True, timeout=120)
        p98 = reports_dir / f"phase98_dynamic_universe_build_{day_stamp}.json"
        if p98.is_file():
            dyn_payload = json.loads(p98.read_text(encoding="utf-8"))

    verdict = str(build_payload.get("verdict") or "need_user_to_download_jpx_file")
    if not raw_listing:
        verdict = "need_user_to_download_jpx_file"
    elif verdict == "need_user_to_download_jpx_file":
        pass
    elif verdict == "parser_fix_required":
        pass
    elif build_payload.get("sample_only") or tradable_count < TRADABLE_MIN:
        verdict = "sample_master_only"
    elif tradable_count >= TRADABLE_MIN:
        verdict = "full_jpx_master_ready"

    dyn_ok = (
        dyn_payload.get("need_symbol_master") is False
        and dyn_payload.get("static_count") == 27
        and dyn_payload.get("total_count", 0) <= 50
        and dyn_payload.get("board_skipped") or dyn_payload.get("skip_kabu")
    )

    report = {
        "phase": 100,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "verdict": verdict,
        "verdict_options": {
            "A": "full_jpx_master_ready",
            "B": "need_user_to_download_jpx_file",
            "C": "sample_master_only",
            "D": "parser_fix_required",
        },
        "raw_dir": str(raw_dir.relative_to(repo_root)),
        "raw_file_found": bool(build_payload.get("raw_file_found")),
        "raw_file_path": build_payload.get("raw_file_path"),
        "has_official_listed_issues_name": has_official,
        "raw_dir_files": raw_listing,
        "sample_only": build_payload.get("sample_only"),
        "all_count": build_payload.get("all_count"),
        "tradable_count": tradable_count,
        "prime_count": build_payload.get("prime_count"),
        "standard_count": build_payload.get("standard_count"),
        "growth_count": build_payload.get("growth_count"),
        "excluded_reason_counts": build_payload.get("excluded_reason_counts"),
        "market_distribution": build_payload.get("market_distribution"),
        "output_paths": build_payload.get("output_paths"),
        "ready_for_dynamic_universe_build": build_payload.get("ready_for_dynamic_universe_build"),
        "optional_diagnostics": build_payload.get("optional_diagnostics"),
        "jpx_build": build_payload,
        "dynamic_universe_skip_kabu": dyn_payload,
        "ready_for_board_runtime": bool(
            dyn_ok
            and verdict == "full_jpx_master_ready"
        ),
        "setup_doc": "kabu_native/docs/jpx_symbol_master_setup.md",
        "constraints": [
            "no_production_pilot_yaml_change",
            "no_board_or_live_in_phase100",
            "no_symbol_hardcode",
        ],
    }

    out_path = reports_dir / f"phase100_jpx_master_setup_check_{day_stamp}.json"
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"verdict": verdict, "tradable_count": tradable_count, "path": str(out_path)}, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
