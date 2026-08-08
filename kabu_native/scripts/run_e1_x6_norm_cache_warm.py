"""Warm the durable normalization cache for E1_X6 Plan 2.1 (data prep only).

No economics, no strategy evaluation, no Shadow/Runtime/Paper changes.
Writes slim normalized event pickles to results/research/_e1_x5_norm_cache.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
SRC = NATIVE / "src"
REPO = NATIVE.parent
for p in (str(SRC), str(REPO)):
    if p not in sys.path:
        sys.path.insert(0, p)


def main() -> int:
    import small_paper.e1_x5_canonical_replay as cr
    from research.e1_x6_provisional.constants import DAYS
    from research.e1_x6_provisional.util import native_root

    root = native_root()
    cache = root / "results" / "research" / "_e1_x5_norm_cache"
    cache.mkdir(parents=True, exist_ok=True)
    for day in DAYS:
        t0 = time.time()
        events, report = cr.normalize_day(root, day, cache_dir=cache, use_cache=True)
        print(
            f"NORM day={day} rows={report.normalized_rows} events={len(events)} "
            f"sessions={len(getattr(report, 'sessions', []) or [])} dt={time.time() - t0:.1f}s",
            flush=True,
        )
        del events
    print("NORM_CACHE_WARM_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
