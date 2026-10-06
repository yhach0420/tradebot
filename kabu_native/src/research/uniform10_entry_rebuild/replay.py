"""A/B/C causal replay under corrected Passive Fill. A is REFERENCE Dual Lane."""
from __future__ import annotations

import gc
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

NATIVE = Path(__file__).resolve().parents[3]
if str(NATIVE / "src") not in sys.path:
    sys.path.insert(0, str(NATIVE / "src"))
if str(NATIVE / "scripts") not in sys.path:
    sys.path.insert(0, str(NATIVE / "scripts"))

from research.anchor_10min_opportunity.replay import _Discard, _session_fallback
from research.anchor_timing_robustness.grid import hm_epoch, hm_label
from research.anchor_timing_robustness.replay import _portfolio_pack, _pop_webhooks
from research.anchor_vs_event_driven.run_comparison import _boot, _stream_day
from research.fixed_anchor_mechanism_audit_p3_0.engine import P3Engine
from research.uniform10_entry_rebuild import UNIFORM10
from research.uniform10_entry_rebuild.engine import RebuildEngine
from research.uniform10_entry_rebuild.features import (
    attach_cross_section,
    extra_from_board,
    labels_from_board,
    source_series,
)
from small_paper.v1r_native_entry_live import FEATURE_ORDER


def _stream(
    *,
    day: str,
    capture: Path,
    universe: list[str],
    variant: str,
    allowed_hm: Optional[tuple[tuple[int, int], ...]],
    fire_mode: str,
    engine_cls: type = P3Engine,
    rebuild_model: Optional[dict[str, Any]] = None,
) -> tuple[Any, Any, dict[str, Any]]:
    eng, dual = _boot(universe, engine_cls)
    if dual is None or not eng.ready:
        return None, None, {
            "ok": False,
            "date": day,
            "variant": variant,
            "blocker": getattr(eng, "fail_reason", "dual_unavailable"),
        }
    if rebuild_model and hasattr(eng, "rebuild_model"):
        eng.rebuild_model = rebuild_model
    eng.require_executable_continuous_fill = True
    eng.offset_sec = 0
    eng.allowed_hm = allowed_hm
    eng.fire_mode = fire_mode
    eng.notify_enabled = False
    eng.ingest_audit = _Discard()  # type: ignore[assignment]
    events_n, last_et = _stream_day(day, capture, eng, dual)
    eng._harvest(eng.events)
    packed = _portfolio_pack(
        eng, dual, day=day, variant=variant, offset_sec=0, events_n=events_n, last_et=last_et
    )
    packed["variant"] = variant
    return eng, dual, packed


def extract_panel(eng: Any, *, day: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    leak = False
    series_cache: dict[str, Any] = {}
    for h, m in UNIFORM10:
        t0 = hm_epoch(day, h, m)
        sess = _session_fallback(day, h, m, t0)
        an = hm_label(h, m)
        for sym in list(eng.universe):
            s = str(sym).replace(".T", "")
            board = eng._board_arrays(s)
            src_rows = eng.boards.get(s) or []
            if board["t"].size == 0:
                continue
            i = int(__import__("numpy").searchsorted(board["t"], t0, side="right") - 1)
            if i < 0:
                continue
            snap_t = float(board["t"][i])
            if snap_t > float(t0) + 1e-9:
                leak = True
                continue
            if s not in series_cache:
                series_cache[s] = source_series(src_rows)
            feats = extra_from_board(board, t0, series=series_cache[s])
            labs = labels_from_board(board, t0)
            cur = None
            if all(feats.get(f) is not None for f in FEATURE_ORDER):
                try:
                    import numpy as np

                    sc = float(eng.score_fn(feats))
                    if np.isfinite(sc):
                        cur = sc
                except Exception:
                    cur = None
            rec = {
                "date": day,
                "anchor": an,
                "session": sess,
                "symbol": s,
                "snapshot_minus_t0": snap_t - float(t0),
                "current_score": cur,
                **feats,
                **labs,
            }
            rows.append(rec)
    attach_cross_section(rows)
    for r in rows:
        r["snapshot_future_leak"] = leak
        r["feature_evaluable"] = all(
            r.get(f) is not None and r.get(f) == r.get(f) for f in ("spread_bps", "imbalance")
        )
    return rows


def process_day(payload: dict[str, Any]) -> dict[str, Any]:
    _pop_webhooks()
    os.environ.pop("KABU_V1R_ENTRY_WEBHOOK_URL", None)
    day = str(payload["date"])
    capture = Path(payload["capture_path"])
    universe = list(payload["universe"])
    stage = str(payload.get("stage") or "B_PANEL")
    t0w = time.perf_counter()
    try:
        if stage == "B_PANEL":
            eng, _d, packed = _stream(
                day=day,
                capture=capture,
                universe=universe,
                variant="B_UNIFORM10_CURRENT_ENTRY",
                allowed_hm=UNIFORM10,
                fire_mode="shifted_grid",
            )
            if eng is None or not packed.get("ok"):
                return {"ok": False, "date": day, "stage": stage, "blocker": packed.get("blocker")}
            panel = extract_panel(eng, day=day)
            print(f"{day} B trades={packed.get('trade_n')} pnl={packed.get('pnl')} panel={len(panel)}", flush=True)
            trades = packed.get("trades") or []
            del eng, _d, packed
            gc.collect()
            return {
                "ok": True,
                "date": day,
                "stage": stage,
                "trades": trades,
                "panel": panel,
                "elapsed_sec": round(time.perf_counter() - t0w, 3),
            }
        if stage == "C":
            model = payload.get("rebuild_model") or {}
            eng, _d, packed = _stream(
                day=day,
                capture=capture,
                universe=universe,
                variant="C_UNIFORM10_REBUILT_ENTRY",
                allowed_hm=UNIFORM10,
                fire_mode="shifted_grid",
                engine_cls=RebuildEngine,
                rebuild_model=model,
            )
            if eng is None or not packed.get("ok"):
                return {"ok": False, "date": day, "stage": stage, "blocker": packed.get("blocker")}
            print(f"{day} C trades={packed.get('trade_n')} pnl={packed.get('pnl')}", flush=True)
            trades = packed.get("trades") or []
            del eng, _d, packed
            gc.collect()
            return {
                "ok": True,
                "date": day,
                "stage": stage,
                "trades": trades,
                "elapsed_sec": round(time.perf_counter() - t0w, 3),
            }
        return {"ok": False, "date": day, "blocker": f"unknown_stage:{stage}"}
    except Exception as exc:
        return {
            "ok": False,
            "date": day,
            "stage": stage,
            "blocker": f"{type(exc).__name__}:{exc}",
            "elapsed_sec": round(time.perf_counter() - t0w, 3),
        }
