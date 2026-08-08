"""Per-window feature/regime/due bundle builder for Phase B economics replay."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np

from .event_input import EvalEvent, event_from_payload
from .features import (
    FRESH_MAX_AGE_SEC,
    WARMUP_SEC,
    build_symbol_grid,
    compute_symbol_features,
    continuous_lookback_ok,
    entry_allowed_mask,
    session_grid_epochs,
)
from .raw_inventory import _parse_iso, _session_of
from .regime import classify_regime
from .source_manifest import raw_day_dir
from .windows import EXIT_HORIZON_SEC

MKT_LOO_MIN = 30
SNAPSHOT_FRESH_SEC = 30.0


def _load_session_events(
    native_root: Path, day: str, universe: list[str], am_pm: str,
) -> dict[str, list[EvalEvent]]:
    """Load raw PUSH events for one session (availability order = file order)."""
    uset = set(universe)
    rd = raw_day_dir(native_root, day)
    out: dict[str, list[EvalEvent]] = {s: [] for s in universe}
    for fp in sorted(rd.glob("*.jsonl")):
        sym = fp.stem[:-2] if fp.stem.endswith(".T") else fp.stem
        if sym not in uset:
            continue
        with fp.open("rb") as f:
            for lineb in f:
                try:
                    d = json.loads(lineb)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue
                rec = _parse_iso(d.get("recorded_at"))
                if rec is None or _session_of(rec) != am_pm:
                    continue
                ev = event_from_payload(sym, d.get("recorded_at"), d.get("payload") or {})
                if ev is not None:
                    out[sym].append(ev)
    return out


def compute_market_loo_fast(
    sym_feats: dict[str, dict[str, np.ndarray]],
    evaluable: dict[str, np.ndarray],
    n_grid: int,
) -> dict[str, dict[str, np.ndarray]]:
    """Vectorized LOO market aggregates (same semantics as features.compute_market_loo)."""
    import warnings

    from .features import MARKET_FEATURES

    symbols = sorted(sym_feats)
    ns = len(symbols)
    r60 = np.vstack([sym_feats[s]["ret_60s_bps"] for s in symbols])
    r300 = np.vstack([sym_feats[s]["ret_300s_bps"] for s in symbols])
    rv300 = np.vstack([sym_feats[s]["rv_300s_bps"] for s in symbols])
    vratio = np.vstack([sym_feats[s]["vol_ratio_60_300"] for s in symbols])
    spread = np.vstack([sym_feats[s]["spread_bps"] for s in symbols])
    ev = np.vstack([evaluable[s] for s in symbols]).astype(bool)

    spread_med300 = np.full_like(spread, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for i in range(ns):
            # rolling median via stride — keep simple loop over grids for correctness
            for g in range(n_grid):
                w = spread[i, max(0, g - 60):g + 1]
                fin = w[~np.isnan(w)]
                if fin.shape[0] >= 12:
                    spread_med300[i, g] = float(np.median(fin))
        spread_worse = (
            (spread > 1.5 * spread_med300) & ~np.isnan(spread) & ~np.isnan(spread_med300)
        )

    out: dict[str, dict[str, np.ndarray]] = {
        s: {k: np.full(n_grid, np.nan) for k in MARKET_FEATURES} for s in symbols
    }
    # Precompute all-evaluable aggregates, then leave-one-out adjustments where needed
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        for g in range(n_grid):
            evg = ev[:, g]
            n_ev = int(evg.sum())
            if n_ev == 0:
                for s in symbols:
                    out[s]["mkt_evaluable_n"][g] = 0.0
                continue
            for i, s in enumerate(symbols):
                mask = evg.copy()
                mask[i] = False
                o = out[s]
                o["mkt_evaluable_n"][g] = float(np.sum(mask))
                if not np.any(mask):
                    continue
                a60 = r60[mask, g]
                fin60 = a60[~np.isnan(a60)]
                if fin60.shape[0]:
                    o["mkt_ret_60s_med_bps"][g] = float(np.median(fin60))
                    o["mkt_up_ratio_60s"][g] = float(np.mean(fin60 > 0))
                    if fin60.shape[0] >= 4:
                        q75, q25 = np.percentile(fin60, [75, 25])
                        o["mkt_ret_60s_iqr_bps"][g] = float(q75 - q25)
                a300 = r300[mask, g]
                fin300 = a300[~np.isnan(a300)]
                if fin300.shape[0]:
                    o["mkt_ret_300s_med_bps"][g] = float(np.median(fin300))
                arv = rv300[mask, g]
                finrv = arv[~np.isnan(arv)]
                if finrv.shape[0]:
                    o["mkt_rv_300s_med_bps"][g] = float(np.median(finrv))
                avr = vratio[mask, g]
                finvr = avr[~np.isnan(avr)]
                if finvr.shape[0]:
                    o["mkt_vol_expansion"][g] = float(np.median(finvr))
                o["mkt_spread_worse_ratio"][g] = float(np.mean(spread_worse[mask, g]))
    return out


def build_window_bundle(
    native_root: Path,
    day: str,
    am_pm: str,
    universe: list[str],
    symbol_classes: dict[str, str],
    mask_row: dict[str, Any],
) -> dict[str, Any]:
    """Build all per-symbol features, due masks, regimes for one included window."""
    full_grid = session_grid_epochs(day, am_pm)
    vs = mask_row.get("valid_start_epoch")
    ve = mask_row.get("valid_end_epoch")
    if vs is None or ve is None:
        return {"empty": True, "day": day, "am_pm": am_pm}
    # Use full session grid for state continuity; ENTRY gated by due+horizon
    grid = full_grid
    n = grid.shape[0]
    events = _load_session_events(native_root, day, universe, am_pm)

    warm_anchor = max(mask_row["expected_start_epoch"], vs) + WARMUP_SEC
    entry_until = mask_row.get("entry_evaluable_until_epoch")
    if entry_until is None:
        entry_until = (
            mask_row["expected_end_epoch"]
            if ve >= mask_row["expected_end_epoch"] - 1e-9
            else ve - EXIT_HORIZON_SEC
        )

    sym_grids = {}
    sym_feats: dict[str, dict[str, np.ndarray]] = {}
    evaluable: dict[str, np.ndarray] = {}
    due: dict[str, np.ndarray] = {}
    decision_ok: dict[str, np.ndarray] = {}
    lookback_ok: dict[str, np.ndarray] = {}
    entry_allowed = entry_allowed_mask(grid)

    for sym in universe:
        sg = build_symbol_grid(sym, events.get(sym) or [], grid)
        sym_grids[sym] = sg
        feats = compute_symbol_features(sg)
        sym_feats[sym] = feats
        # Market LOO uses structural quote (fresh+ordered), NOT spread filter (R2/R3).
        quote_struct = (
            np.isfinite(sg.bid) & np.isfinite(sg.ask)
            & (sg.bid > 0) & (sg.ask > 0) & (sg.ask >= sg.bid)
            & np.isfinite(sg.last_event_age)
            & (sg.last_event_age <= SNAPSHOT_FRESH_SEC + 1e-9)
        )
        warm_ok = grid >= (grid[0] + WARMUP_SEC - 1e-9)
        evaluable[sym] = quote_struct & warm_ok
        # due = PUSH in this 5s grid (age < 5s) AND within warmup..horizon
        push = np.isfinite(sg.last_event_age) & (sg.last_event_age < 5.0 - 1e-9)
        in_scope = (grid >= warm_anchor - 1e-9) & (grid <= entry_until + 1e-9)
        due[sym] = push & in_scope
        lb = continuous_lookback_ok(sg, 60)
        lookback_ok[sym] = lb
        # decision_ok: structural + lookback; spread gated inside setup machine
        decision_ok[sym] = quote_struct & lb

    mkt = compute_market_loo_fast(sym_feats, evaluable, n)
    # attach market feats + build regimes (per-symbol LOO => per-symbol regime)
    regimes_std: dict[str, list[str]] = {}
    regimes_strict: dict[str, list[str]] = {}
    for sym in universe:
        for k, arr in mkt[sym].items():
            sym_feats[sym][k] = arr
        # market context gate on due grids: mkt_evaluable_n >= 30
        mkt_ok = sym_feats[sym]["mkt_evaluable_n"] >= MKT_LOO_MIN
        decision_ok[sym] = decision_ok[sym] & mkt_ok
        regimes_std[sym] = classify_regime(mkt[sym], strict=False)
        regimes_strict[sym] = classify_regime(mkt[sym], strict=True)

    return {
        "empty": False,
        "day": day,
        "am_pm": am_pm,
        "grid": grid,
        "universe": list(universe),
        "symbol_classes": {s: symbol_classes.get(s, "OTHER") for s in universe},
        "sym_feats": sym_feats,
        "due": due,
        "decision_ok": decision_ok,
        "evaluable": evaluable,
        "entry_allowed": entry_allowed,
        "regimes_std": regimes_std,
        "regimes_strict": regimes_strict,
        "warm_anchor": warm_anchor,
        "entry_until": entry_until,
        "n_grid": n,
    }
