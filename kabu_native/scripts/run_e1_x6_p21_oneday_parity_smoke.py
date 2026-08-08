"""One-day capture + oracle parity smoke (20260723). Research-only, no state changes."""
from __future__ import annotations

import sys
import time
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
for p in (str(NATIVE / "src"), str(NATIVE.parent)):
    if p not in sys.path:
        sys.path.insert(0, p)


def main() -> int:
    from research.e1_x6_provisional.analysis_mask import build_mask_index
    from research.e1_x6_provisional.joint_oracle_replay import (
        ExitParams,
        PartitionBundle,
        parity_check_bundle,
    )
    from research.e1_x6_provisional.oracle_capture import capture_day, durable_bundle_root
    from research.e1_x6_provisional.p0_manifest import build_source_manifest
    from research.e1_x6_provisional.util import progress
    from small_paper.e1_x5_forward_shadow import (
        GIVEBACK,
        MAX_HOLD_SEC,
        STOP_BPS,
        TARGET_BPS,
        THRESHOLD,
        TRAIL_ARM_BPS,
    )

    day = sys.argv[1] if len(sys.argv) > 1 else "20260723"
    manifest = build_source_manifest(final=True)
    mask_index = build_mask_index(manifest)
    out_dir = durable_bundle_root("smoke_oneday")
    t0 = time.time()
    metas = capture_day(day, mask_index=mask_index, out_dir=out_dir)
    progress(f"SMOKE: capture {day} done dt={time.time() - t0:.1f}s windows={len(metas)}")

    xp = ExitParams(
        stop_bps=float(STOP_BPS),
        target_bps=float(TARGET_BPS),
        trail_arm_bps=float(TRAIL_ARM_BPS),
        giveback=float(GIVEBACK),
        max_hold_sec=float(MAX_HOLD_SEC),
    )
    all_ok = True
    for m in metas:
        b = PartitionBundle.load(out_dir / m["file"])
        r = parity_check_bundle(b, x5_threshold=float(THRESHOLD), xp=xp)
        print(
            f"PARITY {b.day} {b.am_pm} oracle_n={r['oracle_n']} session_n={r['session_n']} "
            f"oracle_pnl={r['oracle_pnl']} session_pnl={r['session_pnl']} match={r['match']}",
            flush=True,
        )
        if not r["match"]:
            all_ok = False
            for mm in r["mismatches"][:20]:
                print("  MM:", mm, flush=True)
    print("SMOKE_PARITY_ALL_MATCH" if all_ok else "SMOKE_PARITY_FAILED", flush=True)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
