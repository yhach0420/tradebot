"""Plan 2.1 Stage-2 ENTRY redesign: as-of feature inventory + fixed candidate groups.

Stage-1 (full 200-package JointRegistry sweep, run e1x6_p21_20260802_204337_49eabae8)
returned passers=0 (E1_X6_NO_ROBUST_JOINT_STRATEGY): every package lost money on 6/9
days; profits existed only on 20260722/20260731. Per plan §3.5 the ENTRY structure is
redesigned from the as-of feature inventory below. All groups, grids, caps and
enumeration order are FIXED here (code SHA locked into stage-2 P1) before any
candidate economics are read.

As-of guarantees: every feature at decision time T uses only events with ts <= T
inside the same partition (bundle arrays are partition-local, so no AM->PM or
cross-day leakage). Insufficient history => NaN => entry predicate fails (both
directions), never a default value. No future values, no MFE/MAE features, no
dates, no symbol-specific conditions, no time-of-day filters.

UNAVAILABLE (documented inventory gap): volume, board depth/imbalance and trade
tape are NOT present in the captured bundles (score rows carry bid/ask/mid/spread
only; exit streams carry bid only). Volume/board confirmation groups from plan
§3.5 therefore cannot be built in stage-2 without a re-capture and are explicitly
out of scope; spread is the only order-book feature.

No Shadow / Runtime / Paper / Live changes.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

import numpy as np

# ---------------------------------------------------------------------------
# Feature inventory (as-of definitions)
# ---------------------------------------------------------------------------

FEATURE_INVENTORY: dict[str, dict[str, str]] = {
    "score": {
        "source": "score sample at decision event",
        "definition": "DMidD4H6 score at T (as emitted by the frozen provider)",
        "group": "signal_level",
    },
    "spread_bps": {
        "source": "quote at decision event",
        "definition": "(ask-bid)/ask*1e4 at T",
        "group": "orderbook_confirmation",
    },
    "score_slope_60s": {
        "source": "symbol SCORE sample stream",
        "definition": "(score(T) - score(<=T-60s)) / 60",
        "group": "signal_continuity",
    },
    "score_accel_60s": {
        "source": "symbol SCORE sample stream",
        "definition": "slope_60s(T) - slope_60s(T-60s) using samples <=T-60s / <=T-120s",
        "group": "signal_continuity",
    },
    "score_min_120s": {
        "source": "symbol SCORE sample stream",
        "definition": "min score over samples in (T-120s, T] (continuity floor)",
        "group": "signal_continuity",
    },
    "ret_60s_bps": {
        "source": "symbol bid stream",
        "definition": "(bid(T)/bid(<=T-60s)-1)*1e4 (pre-entry follow-through)",
        "group": "price_dynamics",
    },
    "ret_300s_bps": {
        "source": "symbol bid stream",
        "definition": "(bid(T)/bid(<=T-300s)-1)*1e4",
        "group": "price_dynamics",
    },
    "stall_60s_bps": {
        "source": "symbol bid stream",
        "definition": "(bid(T)/max bid over (T-60s,T]-1)*1e4 (0=at local high)",
        "group": "price_dynamics",
    },
    "rv_300s_bps": {
        "source": "symbol bid stream",
        "definition": "std of 5s-grid log bid returns over (T-300s,T] *1e4",
        "group": "volatility_state",
    },
    "mkt_ret_60s_bps": {
        "source": "all-symbol bid streams (partition)",
        "definition": "median across symbols of 60s bid return on the partition 5s grid at T",
        "group": "market_state",
    },
}

FEATURES_UNAVAILABLE: dict[str, str] = {
    "volume": "not captured in bundles (score rows / exit streams carry no volume)",
    "board_depth_imbalance": "not captured in bundles (bid/ask top only)",
    "trade_tape": "not captured in bundles",
}

# ---------------------------------------------------------------------------
# Fixed grids and enumeration (locked before economics)
# ---------------------------------------------------------------------------

# quantiles are computed over eligible mask-in signals across ALL 9 days
# (same pre-registered process as stage-1 threshold_source), NaN excluded.
STAGE2_FEATURE_GRIDS: dict[str, tuple[float, ...]] = {
    "score": (0.7, 0.8, 0.9),
    "score_slope_60s": (0.5, 0.6, 0.75),
    "score_accel_60s": (0.5, 0.6),
    "score_min_120s": (0.5, 0.7),
    "ret_60s_bps": (0.5, 0.6, 0.75),
    "stall_60s_bps": (0.5, 0.7),
    "rv_300s_bps": (0.5, 0.75),
    "spread_bps": (0.75,),
    "mkt_ret_60s_bps": (0.5, 0.6),
}

STAGE2_ENTRY_CAP = 50
STAGE2_JOINT_CAP = 200

# Group templates: (group_id, ((feature, direction, grid_key_subset), ...))
# grid_key_subset=None -> use the full grid for that feature.
_G = STAGE2_FEATURE_GRIDS
STAGE2_GROUP_TEMPLATES: tuple[tuple[str, tuple[tuple[str, str, tuple[float, ...]], ...]], ...] = (
    (
        "G1_CONTINUATION",
        (("score", "higher_better", _G["score"]),
         ("score_slope_60s", "higher_better", _G["score_slope_60s"])),
    ),
    (
        "G2_FOLLOW_THROUGH",
        (("score", "higher_better", _G["score"]),
         ("ret_60s_bps", "higher_better", _G["ret_60s_bps"])),
    ),
    (
        "G3_PERSISTENCE",
        (("score_min_120s", "higher_better", _G["score_min_120s"]),
         ("score_accel_60s", "higher_better", _G["score_accel_60s"])),
    ),
    (
        "G4_AT_HIGHS",
        (("score", "higher_better", _G["score"]),
         ("stall_60s_bps", "higher_better", _G["stall_60s_bps"])),
    ),
    (
        "G5_CALM_VOL",
        (("score", "higher_better", _G["score"]),
         ("rv_300s_bps", "lower_better", _G["rv_300s_bps"])),
    ),
    (
        "G6_ACTIVE_VOL",
        (("score", "higher_better", _G["score"]),
         ("rv_300s_bps", "higher_better", _G["rv_300s_bps"])),
    ),
    (
        "G7_MARKET_TAILWIND",
        (("score", "higher_better", _G["score"]),
         ("mkt_ret_60s_bps", "higher_better", _G["mkt_ret_60s_bps"])),
    ),
    (
        "G8_CONFIRMED_TRIPLE",
        (("score", "higher_better", _G["score"]),
         ("score_slope_60s", "higher_better", (0.5,)),
         ("ret_60s_bps", "higher_better", (0.5,)),
         ("spread_bps", "lower_better", _G["spread_bps"])),
    ),
)


def enumerate_stage2_entries(
    quantile_values: Mapping[str, Mapping[float, float]],
) -> list[dict[str, Any]]:
    """Deterministic enumeration of stage-2 entry candidates (lex order, capped).

    quantile_values: feature -> {q: threshold_value} resolved from the
    pre-registered full-period distribution.
    """
    out: list[dict[str, Any]] = []
    for gid, spec in STAGE2_GROUP_TEMPLATES:
        combos: list[list[tuple[str, str, float]]] = [[]]
        for feat, direction, qs in spec:
            combos = [c + [(feat, direction, q)] for c in combos for q in qs]
        for combo in combos:
            feats = [f for f, _, _ in combo]
            dirs = "&".join(f"{f}:{d}" for f, d, _ in combo)
            qspec = ",".join(f"{f}@q{q}" for f, _, q in combo)
            thresholds = {f: float(quantile_values[f][q]) for f, _, q in combo}
            out.append(
                {
                    "candidate_id": f"S2|{gid}|{','.join(feats)}|{dirs}|{qspec}",
                    "group_id": gid,
                    "family": "REDESIGN_AND",
                    "features": feats,
                    "direction": dirs,
                    "quantiles": {f: q for f, _, q in combo},
                    "thresholds": thresholds,
                }
            )
    out.sort(key=lambda c: c["candidate_id"])
    if len(out) > STAGE2_ENTRY_CAP:
        raise SystemExit(
            f"FAIL: stage-2 entry enumeration {len(out)} exceeds pre-registered cap {STAGE2_ENTRY_CAP}"
        )
    return out


def stage2_exit_families() -> list[dict[str, Any]]:
    """Four executable EXIT families tied to the score-based entry rationale."""
    from small_paper.e1_x5_forward_shadow import (
        GIVEBACK,
        MAX_HOLD_SEC,
        STOP_BPS,
        TARGET_BPS,
        TRAIL_ARM_BPS,
    )

    base = {
        "initial_stop_bps": float(STOP_BPS),
        "target_bps": float(TARGET_BPS),
        "trailing": {"arm_bps": float(TRAIL_ARM_BPS), "giveback": float(GIVEBACK)},
        "max_hold_sec": float(MAX_HOLD_SEC),
    }
    return [
        {"exit_family_id": "S2X_A_X5_BASE", **base},
        {
            "exit_family_id": "S2X_B_INVALIDATION",
            **base,
            "_exec_invalidation_score_drop": 0.15,
            "rationale": "score-based entry invalidated when score decays 0.15 from entry",
        },
        {
            "exit_family_id": "S2X_C_NO_PROGRESS",
            **base,
            "_exec_no_progress_sec": 120.0,
            "_exec_no_progress_mfe_bps": 5.0,
            "rationale": "exit if no follow-through (MFE<=5bps and r<=0) after 120s",
        },
        {
            "exit_family_id": "S2X_D_INV_NP_TIGHT",
            **base,
            "initial_stop_bps": float(STOP_BPS) * 0.75,
            "_exec_invalidation_score_drop": 0.15,
            "_exec_no_progress_sec": 180.0,
            "_exec_no_progress_mfe_bps": 3.0,
            "rationale": "tighter stop + invalidation + slow no-progress",
        },
    ]


# ---------------------------------------------------------------------------
# As-of feature computation on a PartitionBundle
# ---------------------------------------------------------------------------

def _asof_values(ts_arr: np.ndarray, val_arr: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Last value at or before each t; NaN when no such sample exists."""
    idx = np.searchsorted(ts_arr, t, side="right") - 1
    out = np.full(t.shape, np.nan, dtype=np.float64)
    ok = idx >= 0
    out[ok] = val_arr[idx[ok]]
    return out


def compute_bundle_features(
    bundle: Any,
    signals: Sequence[Mapping[str, Any]],
    *,
    grid_step_sec: float = 5.0,
    mkt_ret_window_sec: float = 60.0,
) -> dict[str, np.ndarray]:
    """Per-signal as-of feature arrays (float64, NaN = insufficient history)."""
    n = len(signals)
    feats = {
        name: np.full(n, np.nan, dtype=np.float64)
        for name in FEATURE_INVENTORY
    }

    sig_ts = np.asarray([float(r["decision_ts"]) for r in signals], dtype=np.float64)
    for i, r in enumerate(signals):
        feats["score"][i] = float(r["score"])
        sp = r.get("spread_bps")
        if sp is None:
            b0, a0 = float(r["bid"]), float(r["ask"])
            sp = (a0 - b0) / a0 * 10000.0 if a0 > 0 else np.nan
        feats["spread_bps"][i] = float(sp)

    # --- partition-level market grid (median 60s bid return across symbols) ---
    all_min = min(
        (float(st["ts"][0]) for st in bundle.sym_streams.values() if st["ts"].shape[0]),
        default=None,
    )
    all_max = max(
        (float(st["ts"][-1]) for st in bundle.sym_streams.values() if st["ts"].shape[0]),
        default=None,
    )
    mkt_grid_t: Optional[np.ndarray] = None
    mkt_grid_v: Optional[np.ndarray] = None
    if all_min is not None and all_max is not None and all_max > all_min:
        g0 = all_min
        n_grid = int(np.floor((all_max - g0) / grid_step_sec)) + 1
        mkt_grid_t = g0 + np.arange(n_grid, dtype=np.float64) * grid_step_sec
        lag = int(round(mkt_ret_window_sec / grid_step_sec))
        per_sym_rets: list[np.ndarray] = []
        for sym in sorted(bundle.sym_streams):
            st = bundle.sym_streams[sym]
            if st["ts"].shape[0] < 2:
                continue
            gb = _asof_values(st["ts"], st["bid"], mkt_grid_t)
            ret = np.full(n_grid, np.nan, dtype=np.float64)
            if n_grid > lag:
                prev = gb[:-lag]
                cur = gb[lag:]
                ok = (~np.isnan(prev)) & (~np.isnan(cur)) & (prev > 0)
                seg = np.full(n_grid - lag, np.nan, dtype=np.float64)
                seg[ok] = (cur[ok] / prev[ok] - 1.0) * 10000.0
                ret[lag:] = seg
            per_sym_rets.append(ret)
        if per_sym_rets:
            stack = np.vstack(per_sym_rets)
            import warnings

            with np.errstate(all="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                mkt_grid_v = np.nanmedian(stack, axis=0)

    # --- per-symbol as-of features ---
    order = np.argsort(np.asarray([str(r["symbol"]) for r in signals], dtype=object), kind="stable")
    by_sym: dict[str, list[int]] = {}
    for i in order:
        by_sym.setdefault(str(signals[int(i)]["symbol"]), []).append(int(i))

    rv_lag_n = int(round(300.0 / grid_step_sec))
    for sym, idxs in by_sym.items():
        ii = np.asarray(idxs, dtype=np.int64)
        T = sig_ts[ii]

        sc = bundle.sym_scores.get(sym)
        if sc is not None and sc["ts"].shape[0]:
            sts, sval = sc["ts"], sc["score"]
            s_now = feats["score"][ii]
            s60 = _asof_values(sts, sval, T - 60.0)
            s120 = _asof_values(sts, sval, T - 120.0)
            slope = (s_now - s60) / 60.0
            feats["score_slope_60s"][ii] = slope
            feats["score_accel_60s"][ii] = slope - (s60 - s120) / 60.0
            lo = np.searchsorted(sts, T - 120.0, side="right")
            hi = np.searchsorted(sts, T, side="right")
            for k in range(ii.shape[0]):
                a, b = int(lo[k]), int(hi[k])
                if b > a:
                    feats["score_min_120s"][ii[k]] = float(np.min(sval[a:b]))

        st = bundle.sym_streams.get(sym)
        if st is not None and st["ts"].shape[0]:
            bts, bval = st["ts"], st["bid"]
            b_now = np.asarray([float(signals[int(j)]["bid"]) for j in ii], dtype=np.float64)
            b60 = _asof_values(bts, bval, T - 60.0)
            b300 = _asof_values(bts, bval, T - 300.0)
            with np.errstate(all="ignore"):
                feats["ret_60s_bps"][ii] = (b_now / b60 - 1.0) * 10000.0
                feats["ret_300s_bps"][ii] = (b_now / b300 - 1.0) * 10000.0
            lo = np.searchsorted(bts, T - 60.0, side="right")
            hi = np.searchsorted(bts, T, side="right")
            for k in range(ii.shape[0]):
                a, b = int(lo[k]), int(hi[k])
                if b > a:
                    mx = float(np.max(bval[a:b]))
                    if mx > 0:
                        feats["stall_60s_bps"][ii[k]] = (float(b_now[k]) / mx - 1.0) * 10000.0
            # realized vol on 5s as-of grid over (T-300, T]
            rel = np.arange(rv_lag_n + 1, dtype=np.float64) * grid_step_sec - 300.0
            grid_mat = T[:, None] + rel[None, :]
            gv = _asof_values(bts, bval, grid_mat.ravel()).reshape(grid_mat.shape)
            import warnings

            with np.errstate(all="ignore"), warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)
                lg = np.log(gv)
                dr = np.diff(lg, axis=1)
                cnt = np.sum(~np.isnan(dr), axis=1)
                rv = np.nanstd(dr, axis=1) * 10000.0
            ok = cnt >= max(6, rv_lag_n // 4)
            feats["rv_300s_bps"][ii[ok]] = rv[ok]

        if mkt_grid_t is not None and mkt_grid_v is not None:
            gi = np.searchsorted(mkt_grid_t, T, side="right") - 1
            okg = gi >= 0
            feats["mkt_ret_60s_bps"][ii[okg]] = mkt_grid_v[gi[okg]]

    return feats


def resolve_quantile_values(
    pooled: Mapping[str, np.ndarray],
) -> dict[str, dict[float, float]]:
    """Deterministic per-feature quantile thresholds from pooled signal features."""
    out: dict[str, dict[float, float]] = {}
    for feat, qs in STAGE2_FEATURE_GRIDS.items():
        v = np.asarray(pooled[feat], dtype=np.float64)
        v = v[~np.isnan(v)]
        v.sort(kind="stable")
        if v.shape[0] == 0:
            raise SystemExit(f"FAIL: no finite samples for feature {feat}")
        out[feat] = {float(q): float(np.quantile(v, q)) for q in qs}
    return out
