"""REFERENCE stream + persist FEATURE_ORDER rows. Not a strategy. No CLOCK_GRID change."""
from __future__ import annotations

import gc
import os
import sys
import time
from pathlib import Path
from typing import Any

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_10min_opportunity.replay import _stream_grid
from research.anchor_timing_robustness.grid import canonical_grid, hm_epoch, hm_label, session_of_epoch
from research.fixed_anchor_mechanism_audit_p3_0.diagnostic import score_universe_at
from small_paper.v1r_native_entry_live import FEATURE_ORDER


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    t0w = time.perf_counter()
    try:
        eng, _dual, pref = _stream_grid(
            day=day,
            capture=capture,
            universe=universe,
            variant="REFERENCE",
            fire_mode="production",
            allowed_hm=None,
        )
        if eng is None or not pref.get("ok"):
            return {
                "ok": False,
                "date": day,
                "blocker": pref.get("blocker") or "REFERENCE_STREAM_FAILED",
                "elapsed_sec": round(time.perf_counter() - t0w, 3),
            }
        rows: list[dict[str, Any]] = []
        leak = False
        for h, m in canonical_grid():
            t0 = hm_epoch(day, h, m)
            sess = session_of_epoch(day, t0)
            if sess is None:
                continue
            sc = score_universe_at(eng, t0=float(t0), day=day, session=sess)
            if sc.get("snapshot_future_leak"):
                leak = True
            for rec in sc.get("rows") or []:
                if not rec.get("feature_evaluable"):
                    continue
                rows.append(
                    {
                        "date": day,
                        "period": payload.get("period"),
                        "anchor": hm_label(h, m),
                        "session": sess,
                        "symbol": rec.get("symbol"),
                        "rank": rec.get("rank"),
                        "selected": bool(rec.get("selected")),
                        "score": rec.get("score"),
                        "alloc_score": rec.get("alloc_score"),
                        "limit": rec.get("limit"),
                        **{f: rec.get(f) for f in FEATURE_ORDER},
                    }
                )
        print(f"{day} FEATURE_ROWS n={len(rows)} leak={leak} pnl={pref.get('pnl')}", flush=True)
        del eng, _dual
        gc.collect()
        return {
            "ok": True,
            "date": day,
            "period": payload.get("period"),
            "feature_rows": rows,
            "snapshot_future_leak": bool(leak),
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
