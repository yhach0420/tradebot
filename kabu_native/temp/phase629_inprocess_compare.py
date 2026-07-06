"""In-process HEAD vs Phase629 replay compare (eliminates cross-run wall-clock drift).

Runs pre-refactor pilot_runner then refactored pilot_runner sequentially in one
process per day, then compares outputs with phase629_stage_refactoring.compare_day.

usage:
  python phase629_inprocess_compare.py [day ...]
  default days: 2026-06-25 2026-06-29 2026-06-30 2026-07-01
"""
from __future__ import annotations

import json
import shutil
import sys
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = REPO / "kabu_native"
WORKTREE = Path(r"c:\Users\yhach\Documents\tradebotfile-phase629-baseline")
YAML = KABU / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
POLL = 5.0
DEFAULT_DAYS = ("2026-06-25", "2026-06-29", "2026-06-30", "2026-07-01")
OUT_ROOT = KABU / "results" / "small_paper" / "_phase629"
REPORT_DIR = KABU / "results" / "reports" / "phase629_stage_refactoring"

for p in (KABU / "src", REPO):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)


def _purge_modules() -> None:
    for mod in list(sys.modules):
        if mod.startswith("small_paper") or mod.startswith("research"):
            del sys.modules[mod]


def _run_replay(*, src_root: Path, staging_dir: Path, day: str) -> Path:
    """Run replay into staging_dir (fixed session key for vol_liq cache parity)."""
    _purge_modules()
    for p in (src_root / "kabu_native" / "src", src_root):
        s = str(p)
        if s in sys.path:
            sys.path.remove(s)
    for p in (src_root / "kabu_native" / "src", src_root):
        sys.path.insert(0, str(p))

    from small_paper.config import load_pilot_config
    from small_paper.pilot_runner import run_push_replay_dry_run
    import small_paper.pilot_runner as pr

    print(f"REPLAY day={day} pilot={pr.__file__} staging={staging_dir}", flush=True)
    cfg = replace(
        load_pilot_config(YAML),
        discord_enabled=False,
        entry_latency_trace_enabled=False,
        # Production entry_scan_window_sec (2.0) kept; push-replay drives the
        # scan clock from recorded_at (Phase629A) so Stage overhead cannot
        # shift flush boundaries.
    )
    if staging_dir.exists():
        shutil.rmtree(staging_dir, ignore_errors=True)
    staging_dir.mkdir(parents=True, exist_ok=True)
    run_push_replay_dry_run(
        cfg,
        push_dir=KABU / "data" / "push_jsonl" / day,
        output_dir=staging_dir,
        repo_root=REPO,
        poll_interval_sec=POLL,
        streaming_push_replay=True,
        enable_discord=False,
        write_board_shadow_reports=False,
    )
    return staging_dir


def _copy_replay_out(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst, ignore_errors=True)
    shutil.copytree(src, dst)


def main() -> int:
    days = sys.argv[1:] if len(sys.argv) > 1 else list(DEFAULT_DAYS)
    from research.phase629_stage_refactoring import compare_day

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    all_match = True
    for day in days:
        day_key = day.replace("-", "")
        head_tag = f"inproc_head_{day_key}"
        after_tag = f"inproc_after_{day_key}"
        # Same staging path => identical run_session_key / vol_liq_startup cache.
        staging = OUT_ROOT / "regression_staging" / day_key
        _run_replay(src_root=WORKTREE, staging_dir=staging, day=day)
        _copy_replay_out(staging, OUT_ROOT / head_tag / day_key)
        _run_replay(src_root=REPO, staging_dir=staging, day=day)
        _copy_replay_out(staging, OUT_ROOT / after_tag / day_key)
        r = compare_day(day, head_tag, after_tag)
        results.append(r)
        ok = bool(r.get("match"))
        all_match = all_match and ok
        print(f"{day}: match={ok}", flush=True)

    out = {
        "mode": "inprocess_sequential",
        "tag_a": "inproc_head",
        "tag_b": "inproc_after",
        "all_match": all_match,
        "days": results,
    }
    report_path = REPORT_DIR / "phase629_inprocess_compare.json"
    report_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"ALL_MATCH={all_match} -> {report_path}", flush=True)
    return 0 if all_match else 1


if __name__ == "__main__":
    raise SystemExit(main())
