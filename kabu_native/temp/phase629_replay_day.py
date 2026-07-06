"""Phase629: run one full-day push replay (production YAML) into _phase629/<tag>/<day>.

usage: phase629_replay_day.py <day> <tag> [det|prod] [src_root]
  det      -> entry_scan_window_sec=0 (deterministic scan batching; applied
              equally to baseline/after so the equivalence check stays valid)
  src_root -> optional path to an alternate source tree (e.g. git worktree of
              the pre-refactor HEAD) whose kabu_native/src is imported instead.
"""
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
KABU = REPO / "kabu_native"

day = sys.argv[1]          # e.g. 2026-06-30
tag = sys.argv[2]          # baseline | after | baseline_det | after_det ...
mode = sys.argv[3] if len(sys.argv) > 3 else "prod"
src_root = Path(sys.argv[4]) if len(sys.argv) > 4 else REPO

for p in (Path(src_root) / "kabu_native" / "src", Path(src_root)):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

PROD_YAML = KABU / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
POLL_INTERVAL_SEC = 5.0


def main() -> int:
    import shutil
    from dataclasses import replace

    from small_paper.config import load_pilot_config
    from small_paper.pilot_runner import run_push_replay_dry_run
    import small_paper.pilot_runner as pr

    print("pilot_runner loaded from:", pr.__file__, flush=True)
    cfg = load_pilot_config(PROD_YAML)
    cfg = replace(cfg, discord_enabled=False, entry_latency_trace_enabled=False)
    if mode == "det":
        cfg = replace(cfg, entry_scan_window_sec=0.0)
    push_dir = KABU / "data" / "push_jsonl" / day
    out = KABU / "results" / "small_paper" / "_phase629" / tag / day.replace("-", "")
    if out.exists():
        shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    result = run_push_replay_dry_run(
        cfg,
        push_dir=push_dir,
        output_dir=out,
        repo_root=REPO,
        poll_interval_sec=POLL_INTERVAL_SEC,
        streaming_push_replay=True,
        enable_discord=False,
        write_board_shadow_reports=False,
    )
    dt = time.monotonic() - t0
    print(
        f"PHASE629_REPLAY_DONE tag={tag} day={day} mode={mode} sec={dt:.0f} "
        f"events={len(result.events or [])} accepted={len(result.accepted or [])} "
        f"rejects={len(result.rejects or [])}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
