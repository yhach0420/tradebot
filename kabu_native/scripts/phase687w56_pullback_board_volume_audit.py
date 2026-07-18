#!/usr/bin/env python3
"""Phase687W56 — Pullback Board × Volume Final Interaction Audit (analysis only).

No runtime / Shadow / YAML / PBv2 changes.
Outputs:
  pullback_board_volume_audit_report.md / .json / .xlsx
"""

from __future__ import annotations

import importlib.util
import json
import math
import shutil
import sys
import warnings
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from openpyxl import Workbook
from openpyxl.utils.dataframe import dataframe_to_rows

warnings.filterwarnings("ignore")

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "src"))
sys.path.insert(0, str(NATIVE))

OUT = NATIVE / "results" / "research" / "pre_entry_market_state"
SNAP_CACHE = OUT / "_w53_day_snaps_cache"
JST = __import__("zoneinfo").ZoneInfo("Asia/Tokyo")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


p12 = _load_module("w55_p12_w56", NATIVE / "scripts" / "phase687w55_p12_pullback_quality_audit.py")
w55 = p12.w55
w53 = p12.w53
DISC, CONF, ALL_DAYS = p12.DISC, p12.CONF, p12.ALL_DAYS


def _f(v: Any, default: float = np.nan) -> float:
    return w55._f(v, default)


def _pf(xs) -> Optional[float]:
    return w55._pf(xs)


def _excel_cell(v: Any) -> Any:
    return w55._excel_cell(v)


def write_xlsx(sheets: dict[str, pd.DataFrame], path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "README"
    ws.append(["Phase687W56 Pullback Board x Volume Audit"])
    ws.append(["generated", datetime.now(JST).isoformat()])
    ws.append(["runtime_unchanged", "true"])
    for name, df in sheets.items():
        w = wb.create_sheet(str(name)[:31])
        if df is None or getattr(df, "empty", True):
            w.append(["empty"])
            continue
        clean = df.head(50000).copy()
        for c in clean.columns:
            clean[c] = clean[c].map(_excel_cell)
        for r in dataframe_to_rows(clean, index=False, header=True):
            w.append([_excel_cell(x) for x in r])
        w.auto_filter.ref = w.dimensions
        w.freeze_panes = "A2"
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


# ---------------------------------------------------------------------------
# P0 — feature specs (from phase687w43c_watch50_future30m_opportunity.py)
# ---------------------------------------------------------------------------

FEATURE_SPEC = [
    {
        "name": "board_improvement",
        "source_field": "imbalance_chg_60s",
        "definition": "imbalance_l5(t0) - imbalance_l5(t0-60s) > 0",
        "window_sec": 60,
        "anchor": "snapshot t0 / ENTRY join nearest snap at-or-before entry",
        "sign": "positive = bid-side imbalance increased vs 60s ago (improvement)",
        "includes_current": True,
        "cross_symbol_norm": False,
        "future_leak": False,
        "push_sparse": "if past imb missing → feature NaN → mid/missing bucket",
        "code_ref": "scripts/phase687w43c_watch50_future30m_opportunity.py L630-638; P12 board_improvement = chg>0",
    },
    {
        "name": "board_worsening",
        "source_field": "imbalance_chg_60s",
        "definition": "imbalance_chg_60s < 0",
        "window_sec": 60,
        "anchor": "same as board_improvement",
        "sign": "negative chg = bid imbalance worsened",
        "includes_current": True,
        "cross_symbol_norm": False,
        "future_leak": False,
        "push_sparse": "NaN → mid/missing",
        "code_ref": "same; P12 board_worsening = chg<=0 used as worsen in W56 strict <0",
    },
    {
        "name": "volume_persistence",
        "source_field": "vol_persistence_300s",
        "definition": "mean(diff(volume_series_300s) > 0) — fraction of steps with rising cumulative volume",
        "window_sec": 300,
        "anchor": "snapshot t0",
        "sign": "HIGHER = more intervals with increasing volume (participation sustaining)",
        "interpretation_ok": True,
        "includes_current": True,
        "cross_symbol_norm": False,
        "future_leak": False,
        "push_sparse": "need >=4 finite vol points else None",
        "code_ref": "phase687w43c L593-603",
        "note": "WinnerEnrichment historically pairs HIGH pressure with LOW persistence; P12 found low persistence → collapse (consistent)",
    },
    {
        "name": "volume_acceleration",
        "source_field": "vol_accel_300s",
        "definition": "(v_second_half_delta) - (v_first_half_delta) over 300s window",
        "window_sec": 300,
        "anchor": "snapshot t0",
        "sign": "positive = volume rising faster in recent half",
        "includes_current": True,
        "cross_symbol_norm": False,
        "future_leak": False,
        "push_sparse": "need >=6 vol points",
        "code_ref": "phase687w43c L604-608",
    },
    {
        "name": "volume_persistence_change",
        "source_field": "research proxy",
        "definition": "P12 used vol_accel as proxy (no dedicated persistence-delta series in snap)",
        "window_sec": 300,
        "anchor": "snapshot t0",
        "sign": "proxy only — not a true persistence first-difference",
        "includes_current": True,
        "cross_symbol_norm": False,
        "future_leak": False,
        "push_sparse": "inherits vol_accel missingness",
        "code_ref": "phase687w55_p12_pullback_quality_audit.py",
        "caveat": True,
    },
]


def _mask_q(s: pd.Series, side: str, q: float, train: pd.Series) -> pd.Series:
    x = pd.to_numeric(s, errors="coerce")
    tr = pd.to_numeric(train, errors="coerce")
    thr = tr.quantile(q)
    return x >= thr if side == "high" else x <= thr


def group_metrics(sub: pd.DataFrame, name: str) -> dict:
    if sub is None or len(sub) == 0:
        return {"group": name, "n": 0}
    pnl = pd.to_numeric(sub["runtime_exit_pnl"], errors="coerce")
    pnl30 = pd.to_numeric(sub.get("fixed_30m_pnl"), errors="coerce")
    healthy = sub["quality_label"] == "healthy"
    collapse = sub["quality_label"] == "collapse"
    return {
        "group": name,
        "n": int(len(sub)),
        "days": int(sub["trading_date"].nunique()),
        "symbols": int(sub["symbol"].nunique()),
        "am_n": int((sub["session"] == "am").sum()),
        "pm_n": int((sub["session"] == "pm").sum()),
        "healthy_rate": float(healthy.mean()),
        "collapse_rate": float(collapse.mean()),
        "winner_rate": float(sub["hit_winner_threshold"].mean()),
        "stop_rate": float(sub["hit_stop_1p2"].mean()),
        "mean_runtime_pnl": float(pnl.mean()) if pnl.notna().any() else None,
        "mean_30m_pnl": float(pnl30.mean()) if pnl30.notna().any() else None,
        "pf_runtime": _pf(pnl),
        "pf_30m": _pf(pnl30),
        "mean_pnl_5bps": float((pnl - 0.05).mean()) if pnl.notna().any() else None,
        "pf_5bps": _pf(pnl - 0.05),
        "mfe_5m": float(pd.to_numeric(sub.get("max_favorable_excursion_5m"), errors="coerce").mean()),
        "mfe_10m": float(pd.to_numeric(sub.get("max_favorable_excursion_10m"), errors="coerce").mean()),
        "mfe_30m": float(pd.to_numeric(sub.get("max_favorable_excursion_30m"), errors="coerce").mean()),
        "mae_5m": float(pd.to_numeric(sub.get("max_adverse_excursion_5m"), errors="coerce").mean()),
        "mae_10m": float(pd.to_numeric(sub.get("max_adverse_excursion_10m"), errors="coerce").mean()),
        "mae_30m": float(pd.to_numeric(sub.get("max_adverse_excursion_30m"), errors="coerce").mean()),
        "winner_blocked": int((sub["hit_winner_threshold"]).sum()),
        "loser_avoided": int(((pnl < 0) | sub["hit_stop_1p2"]).sum()),
    }


def assign_2x2(df: pd.DataFrame, train: pd.DataFrame) -> pd.DataFrame:
    """Frozen Discovery quantiles for volume persistence high/low."""
    out = df.copy()
    hit = out["pullback_misread_shadow_hit"].fillna(False)
    imb = pd.to_numeric(out["imbalance_chg_60s"], errors="coerce")
    vol = pd.to_numeric(out["volume_persistence"], errors="coerce")
    tr_vol = pd.to_numeric(train.loc[train["pullback_misread_shadow_hit"], "volume_persistence"], errors="coerce")
    hi = tr_vol.quantile(0.7)
    lo = tr_vol.quantile(0.3)
    board_impr = imb > 0
    board_wors = imb < 0
    vol_hi = vol >= hi
    vol_lo = vol <= lo
    mid = ~(board_impr | board_wors) | vol.isna() | (~vol_hi & ~vol_lo) | (~board_impr & ~board_wors & imb.notna() & (imb == 0))
    # cleaner buckets
    g = np.full(len(out), "mid_or_missing", dtype=object)
    g = np.where(hit & board_impr & vol_hi, "A_board_impr_vol_hi", g)
    g = np.where(hit & board_impr & vol_lo, "B_board_impr_vol_lo", g)
    g = np.where(hit & board_wors & vol_hi, "C_board_wors_vol_hi", g)
    g = np.where(hit & board_wors & vol_lo, "D_board_wors_vol_lo", g)
    # remaining hits that are board impr/wors but vol mid
    g = np.where(hit & board_impr & ~vol_hi & ~vol_lo & (g == "mid_or_missing"), "Bmid_board_impr_vol_mid", g)
    g = np.where(hit & board_wors & ~vol_hi & ~vol_lo & (g == "mid_or_missing"), "Cmid_board_wors_vol_mid", g)
    g = np.where(hit & imb.isna() & (g == "mid_or_missing"), "missing_board", g)
    g = np.where(hit & vol.isna() & (g == "mid_or_missing"), "missing_vol", g)
    g = np.where(hit & (g == "mid_or_missing"), "other_hit", g)
    out["bv_group"] = g
    out["board_improvement"] = board_impr.astype(float)
    out["board_worsening"] = board_wors.astype(float)
    out["vol_persistence_high"] = vol_hi.astype(float)
    out["vol_persistence_low"] = vol_lo.astype(float)
    out["_vol_hi_thr"] = hi
    out["_vol_lo_thr"] = lo
    return out


def sequence_analysis(hits: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Use 30s snaps in [-120s, 0] relative to entry. No future."""
    rows = []
    # cache day panels lightly
    day_cache: dict[str, pd.DataFrame] = {}
    for _, r in hits.iterrows():
        day = str(r["trading_date"])
        if day not in day_cache:
            p = SNAP_CACHE / f"{day}_watch50_snapshot.parquet"
            if not p.is_file():
                continue
            d = pd.read_parquet(p, columns=["symbol", "t0_epoch", "t0_time", "current_price", "imbalance_l5", "imbalance_chg_60s", "vol_persistence_300s", "ret_60s"])
            d["symbol"] = d["symbol"].astype(str)
            day_cache[day] = d
        panel = day_cache[day]
        sym = str(r["symbol"])
        et = pd.to_datetime(r.get("entry_time"), utc=True, errors="coerce")
        if et is pd.NaT:
            continue
        if et.tzinfo is None:
            et = et.tz_localize(JST)
        else:
            et = et.tz_convert(JST)
        et_epoch = et.timestamp()
        sub = panel[panel["symbol"] == sym].copy()
        if sub.empty:
            continue
        win = sub[(sub["t0_epoch"] <= et_epoch) & (sub["t0_epoch"] >= et_epoch - 120)]
        if len(win) < 2:
            continue
        win = win.sort_values("t0_epoch")
        px = pd.to_numeric(win["current_price"], errors="coerce")
        imb = pd.to_numeric(win["imbalance_l5"], errors="coerce")
        imb_chg = pd.to_numeric(win["imbalance_chg_60s"], errors="coerce")
        # price drop start: first time ret over window goes negative vs first px
        if px.notna().sum() >= 2:
            base = float(px.dropna().iloc[0])
            drop = px < base * 0.999
            drop_i = int(np.argmax(drop.values)) if drop.any() else None
        else:
            drop_i = None
        # board improve start: first imb_chg>0
        bi = imb_chg > 0
        bi_i = int(np.argmax(bi.values)) if bi.any() else None
        # continuity of board improvement at end
        end_impr = bool(bi.iloc[-1]) if len(bi) else False
        # count consecutive True at end
        consec = 0
        for v in reversed(bi.fillna(False).tolist()):
            if v:
                consec += 1
            else:
                break
        # each snap ~30s
        dur_sec = consec * 30
        if bi_i is None:
            timing = "no_board_improve"
        elif drop_i is None:
            timing = "board_improve_no_clear_drop"
        elif bi_i < drop_i:
            timing = "board_before_drop"
        elif bi_i == drop_i:
            timing = "board_during_drop"
        else:
            timing = "board_near_entry_after_drop"
        # persistence change in window
        volp = pd.to_numeric(win["vol_persistence_300s"], errors="coerce")
        vol_delta = float(volp.iloc[-1] - volp.iloc[0]) if volp.notna().sum() >= 2 else np.nan
        rows.append(
            {
                "trading_date": day,
                "symbol": sym,
                "session": r.get("session"),
                "quality_label": r.get("quality_label"),
                "timing": timing,
                "board_improve_events": int(bi.sum()),
                "board_improve_consec_snaps": consec,
                "board_improve_duration_sec": dur_sec,
                "board_dur_bucket": (
                    "none"
                    if consec == 0
                    else "one_snap_only"
                    if consec == 1
                    else "ge_30s"
                    if dur_sec >= 30 and dur_sec < 60
                    else "ge_60s"
                ),
                "vol_persistence_delta_120s": vol_delta,
                "end_board_improve": end_impr,
                "runtime_exit_pnl": r.get("runtime_exit_pnl"),
                "n_snaps": len(win),
            }
        )
    seq = pd.DataFrame(rows)
    if seq.empty:
        return seq, pd.DataFrame()
    # summary by timing / duration
    summ_rows = []
    for col in ("timing", "board_dur_bucket"):
        for k, g in seq.groupby(col):
            summ_rows.append(
                {
                    "axis": col,
                    "bucket": k,
                    "n": len(g),
                    "healthy_rate": float((g["quality_label"] == "healthy").mean()),
                    "collapse_rate": float((g["quality_label"] == "collapse").mean()),
                    "mean_pnl": float(pd.to_numeric(g["runtime_exit_pnl"], errors="coerce").mean()),
                }
            )
    return seq, pd.DataFrame(summ_rows)


def auc_binary(scores: np.ndarray, y: np.ndarray) -> Optional[float]:
    pos = scores[y == 1]
    neg = scores[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    ranks = pd.Series(np.concatenate([pos, neg])).rank().values
    r_pos = ranks[: len(pos)].sum()
    return float((r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def logloss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def model_comparison(train: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    def prep(d):
        h = d[d["pullback_misread_shadow_hit"]].copy()
        return h[h["quality_label"].isin(["collapse", "healthy"])]

    tr, te = prep(train), prep(test)
    if len(tr) < 20 or len(te) < 15:
        return pd.DataFrame([{"ok": False}])
    y_te = (te["quality_label"] == "collapse").astype(int).values
    y_tr = (tr["quality_label"] == "collapse").astype(int)

    def zscore_apply(df, col, train_df):
        x = pd.to_numeric(df[col], errors="coerce")
        mu = pd.to_numeric(train_df[col], errors="coerce").mean()
        sd = pd.to_numeric(train_df[col], errors="coerce").std()
        return ((x - mu) / (sd if sd and sd > 1e-9 else 1.0)).fillna(0)

    # scores: higher → more collapse
    # M1 position: more negative rise/vwap already selected; use magnitude of negativity inverted
    s1 = (-zscore_apply(te, "entry_rise_5min_pct", tr) - zscore_apply(te, "entry_vwap_dev_pct", tr)).values
    # M2 board worsen
    s2 = (-pd.to_numeric(te["imbalance_chg_60s"], errors="coerce").fillna(0)).values
    # M3 low vol persistence → collapse (P12 direction)
    s3 = (-zscore_apply(te, "volume_persistence", tr)).values
    # M4 simple: board worsen + low vol
    s4 = s2 + s3
    # M5 quality composite (same spirit as P12)
    qcols = [
        "fall_from_recent_high",
        "bounce_from_recent_low",
        "seconds_since_last_high",
        "vwap_reclaim",
        "board_improvement",
        "bid_pressure",
        "volume_acceleration",
        "volume_persistence",
        "sector_strength",
        "stop_risk",
        "winner_enrichment",
    ]
    coef = {}
    for c in qcols:
        if c not in tr.columns:
            continue
        xz = zscore_apply(tr, c, tr)
        coef[c] = float(pd.Series(xz).corr(y_tr)) if y_tr.nunique() > 1 else 0.0
    s5 = np.zeros(len(te))
    for c, w in coef.items():
        s5 = s5 + w * zscore_apply(te, c, tr).values

    rows = []
    for name, s in [
        ("M1_position_only", s1),
        ("M2_board_impr_only", s2),
        ("M3_vol_persistence_only", s3),
        ("M4_board_plus_vol_simple", s4),
        ("M5_quality_composite", s5),
    ]:
        # calibrate to prob via rank percentile on test for brier/logloss (OOS ranking quality)
        ranks = pd.Series(s).rank(pct=True).values
        # precision at top/bottom 30%
        thr_hi = np.nanpercentile(s, 70)
        thr_lo = np.nanpercentile(s, 30)
        pred_c = s >= thr_hi
        pred_h = s <= thr_lo
        collapse_prec = float(y_te[pred_c].mean()) if pred_c.any() else None
        healthy_prec = float((1 - y_te)[pred_h].mean()) if pred_h.any() else None
        # pnl if reject top collapse scores
        rej = te.iloc[np.where(pred_c)[0]]
        keep = te.iloc[np.where(~pred_c)[0]]
        rows.append(
            {
                "model": name,
                "auc": auc_binary(s, y_te),
                "brier": brier(ranks, y_te),
                "logloss": logloss(ranks, y_te),
                "collapse_precision_top30": collapse_prec,
                "healthy_precision_bottom30": healthy_prec,
                "mean_pnl_5bps_if_reject_top30": float((pd.to_numeric(rej["runtime_exit_pnl"], errors="coerce") - 0.05).mean()) if len(rej) else None,
                "pf_5bps_kept": _pf(pd.to_numeric(keep["runtime_exit_pnl"], errors="coerce") - 0.05) if len(keep) else None,
                "n_test": len(te),
            }
        )
    return pd.DataFrame(rows)


def increment_tables(train: pd.DataFrame, test: pd.DataFrame) -> tuple[dict, dict]:
    hit = test["pullback_misread_shadow_hit"].fillna(False)
    board_impr = pd.to_numeric(test["imbalance_chg_60s"], errors="coerce") > 0
    board_wors = pd.to_numeric(test["imbalance_chg_60s"], errors="coerce") < 0
    vol_hi = test["vol_persistence_high"] > 0
    vol_lo = test["vol_persistence_low"] > 0

    base_p = test[hit & board_impr]
    add_p = test[hit & board_impr & vol_hi]
    base_r = test[hit & board_wors]
    add_r = test[hit & board_wors & vol_lo]

    def pack(base, add, side: str) -> dict:
        mb, ma = group_metrics(base, "base"), group_metrics(add, "add")
        # coverage of winners
        bw = base[base["hit_winner_threshold"] | base["is_winner_w43b"]]
        aw = add[add["hit_winner_threshold"] | add["is_winner_w43b"]]
        out = {
            "side": side,
            "base_n": mb["n"],
            "add_n": ma["n"],
            "n_reduction_rate": 1.0 - (ma["n"] / mb["n"]) if mb["n"] else None,
            "healthy_rate_base": mb.get("healthy_rate"),
            "healthy_rate_add": ma.get("healthy_rate"),
            "healthy_rate_delta": (ma.get("healthy_rate") or 0) - (mb.get("healthy_rate") or 0),
            "collapse_rate_base": mb.get("collapse_rate"),
            "collapse_rate_add": ma.get("collapse_rate"),
            "collapse_rate_delta": (ma.get("collapse_rate") or 0) - (mb.get("collapse_rate") or 0),
            "mean_pnl_5bps_base": mb.get("mean_pnl_5bps"),
            "mean_pnl_5bps_add": ma.get("mean_pnl_5bps"),
            "mean_pnl_5bps_delta": (ma.get("mean_pnl_5bps") or 0) - (mb.get("mean_pnl_5bps") or 0),
            "pf_5bps_base": mb.get("pf_5bps"),
            "pf_5bps_add": ma.get("pf_5bps"),
            "am_winner_coverage": float(len(aw[aw["session"] == "am"]) / max(1, len(bw[bw["session"] == "am"]))),
            "pm_winner_coverage": float(len(aw[aw["session"] == "pm"]) / max(1, len(bw[bw["session"] == "pm"]))),
        }
        if side == "reject":
            # winner sacrifice among all hit winners
            all_w = test[hit & test["hit_winner_threshold"]]
            out["winner_sacrifice_base"] = float(len(base[base["hit_winner_threshold"]]) / max(1, len(all_w)))
            out["winner_sacrifice_add"] = float(len(add[add["hit_winner_threshold"]]) / max(1, len(all_w)))
            out["winner_sacrifice_delta"] = out["winner_sacrifice_add"] - out["winner_sacrifice_base"]
            all_l = test[hit & ((pd.to_numeric(test["runtime_exit_pnl"], errors="coerce") < 0) | test["hit_stop_1p2"])]
            out["loser_capture_base"] = float(len(base.merge(all_l[["symbol", "entry_time"]], on=["symbol", "entry_time"])) / max(1, len(all_l))) if len(all_l) else None
            # simpler index-based
            out["loser_capture_base"] = float(((base["runtime_exit_pnl"] < 0) | base["hit_stop_1p2"]).sum() / max(1, ((test[hit]["runtime_exit_pnl"] < 0) | test[hit]["hit_stop_1p2"]).sum()))
            out["loser_capture_add"] = float(((add["runtime_exit_pnl"] < 0) | add["hit_stop_1p2"]).sum() / max(1, ((test[hit]["runtime_exit_pnl"] < 0) | test[hit]["hit_stop_1p2"]).sum()))
            out["loser_capture_delta"] = (out["loser_capture_add"] or 0) - (out["loser_capture_base"] or 0)
            out["net_saved_add"] = float((-pd.to_numeric(add["runtime_exit_pnl"], errors="coerce")).sum())
        return out

    return pack(base_p, add_p, "permit"), pack(base_r, add_r, "reject")


def leave_one_out(sub: pd.DataFrame, key: str) -> pd.DataFrame:
    rows = []
    if sub.empty:
        return pd.DataFrame()
    pnl = pd.to_numeric(sub["runtime_exit_pnl"], errors="coerce")
    base_pf = _pf(pnl - 0.05)
    for k in sorted(sub[key].astype(str).unique()):
        keep = sub[sub[key].astype(str) != k]
        p = pd.to_numeric(keep["runtime_exit_pnl"], errors="coerce") - 0.05
        rows.append(
            {
                "left_out": k,
                "n_kept": len(keep),
                "mean_pnl_5bps": float(p.mean()) if len(keep) else None,
                "pf_5bps": _pf(p),
                "pf_gt_1": bool((_pf(p) or 0) > 1),
            }
        )
    df = pd.DataFrame(rows)
    df["base_pf_5bps"] = base_pf
    df["all_loo_pf_gt_1"] = bool(df["pf_gt_1"].all()) if len(df) else False
    return df


def costaware_overlap(df: pd.DataFrame, train: pd.DataFrame) -> pd.DataFrame:
    """STOP risk via W53 scores; healthy permit = board impr + vol hi."""
    hit = df["pullback_misread_shadow_hit"].fillna(False)
    healthy_perm = hit & (df["board_improvement"] > 0) & (df["vol_persistence_high"] > 0)
    # stop reject: high stop_risk on discovery quantile
    tr = train[train["pullback_misread_shadow_hit"]]
    thr = pd.to_numeric(tr["stop_risk"], errors="coerce").quantile(0.8)
    stop_rej = pd.to_numeric(df["stop_risk"], errors="coerce") >= thr
    rows = []
    for name, m in [
        ("healthy_permit_only", healthy_perm & ~stop_rej),
        ("stop_risk_only", stop_rej & ~healthy_perm),
        ("both", healthy_perm & stop_rej),
        ("neither", ~healthy_perm & ~stop_rej & hit),
    ]:
        sub = df[m]
        rows.append(
            {
                **group_metrics(sub, name),
                "stop_thr_disc_q80": thr,
            }
        )
    # explain both=0 if needed
    return pd.DataFrame(rows)


def gate_check(metrics: dict, kind: str, loo_day: pd.DataFrame, loo_sym: pd.DataFrame, base_board: dict) -> dict:
    g = dict(metrics)
    g["kind"] = kind
    if kind == "permit":
        clearer = bool(
            (metrics.get("healthy_rate") or 0) > (base_board.get("healthy_rate") or 0) + 0.02
            and (metrics.get("mean_pnl_5bps") or -1) > (base_board.get("mean_pnl_5bps") or -1)
        )
        g["pass"] = bool(
            (metrics.get("n") or 0) >= 30
            and (metrics.get("days") or 0) >= 7
            and (metrics.get("symbols") or 0) >= 20
            and (metrics.get("healthy_rate") or 0) >= 0.75
            and (metrics.get("collapse_rate") or 1) <= 0.20
            and (metrics.get("pf_5bps") or 0) > 1.30
            and (metrics.get("mean_pnl_5bps") or -1) > 0
            and clearer
            and (bool(loo_day["pf_gt_1"].all()) if len(loo_day) else False)
            and (bool(loo_sym["pf_gt_1"].all()) if len(loo_sym) else False)
        )
        g["clearer_than_board_alone"] = clearer
    else:
        g["pass"] = bool(
            (metrics.get("n") or 0) >= 30
            and (metrics.get("days") or 0) >= 7
            and (metrics.get("symbols") or 0) >= 20
            and (metrics.get("collapse_rate") or 0) >= 0.50
            and (metrics.get("winner_rate") or 1) <= 0.50  # proxy sacrifice pressure
            and (metrics.get("pf_5bps") or 1) < 0.80
            and (metrics.get("mean_pnl_5bps") or 0) < 0
            and float((-pd.Series([metrics.get("mean_runtime_pnl") or 0])).iloc[0])  # placeholder
            and (bool(loo_day["mean_pnl_5bps"].mean() < 0) if len(loo_day) else False)
        )
    return g


def main() -> int:
    print("=== Phase687W56 Pullback Board x Volume Audit ===", flush=True)
    print("Building P12-identical dataset...", flush=True)
    df = p12.build_dataset()
    if df.empty:
        return 1
    train = df[df["trading_date"].astype(str).isin(DISC)].copy()
    test = df[df["trading_date"].astype(str).isin(CONF)].copy()
    df = assign_2x2(df, train)
    train = df[df["trading_date"].astype(str).isin(DISC)].copy()
    test = df[df["trading_date"].astype(str).isin(CONF)].copy()
    hits = df[df["pullback_misread_shadow_hit"]].copy()
    hits_te = test[test["pullback_misread_shadow_hit"]].copy()
    print(f"hits={len(hits)} conf_hits={len(hits_te)}", flush=True)

    # missingness on hits
    miss = {
        "imbalance_chg_60s": float(hits["imbalance_chg_60s"].isna().mean()),
        "volume_persistence": float(hits["volume_persistence"].isna().mean()),
        "volume_acceleration": float(hits["volume_acceleration"].isna().mean()),
    }
    for s in FEATURE_SPEC:
        s["missing_rate_on_hits"] = miss.get(s["source_field"] if s["source_field"] in miss else s["name"], miss.get("volume_persistence"))

    # P2 2x2 on Confirmation hits primarily + all
    two_by_two = []
    for split_name, d in [("confirmation", hits_te), ("all", hits)]:
        for gname in [
            "A_board_impr_vol_hi",
            "B_board_impr_vol_lo",
            "C_board_wors_vol_hi",
            "D_board_wors_vol_lo",
            "Bmid_board_impr_vol_mid",
            "Cmid_board_wors_vol_mid",
            "mid_or_missing",
            "other_hit",
            "missing_board",
            "missing_vol",
        ]:
            m = group_metrics(d[d["bv_group"] == gname], gname)
            m["split"] = split_name
            two_by_two.append(m)
    two_df = pd.DataFrame(two_by_two)

    # Hypotheses on Confirmation
    h1 = group_metrics(hits_te[(hits_te["board_improvement"] > 0) & (hits_te["vol_persistence_high"] > 0)], "H1_permit")
    h2 = group_metrics(hits_te[(hits_te["board_improvement"] > 0) & (hits_te["vol_persistence_low"] > 0)], "H2_fake_board")
    h3 = group_metrics(hits_te[(hits_te["board_worsening"] > 0) & (hits_te["vol_persistence_low"] > 0)], "H3_collapse")
    h4 = group_metrics(hits_te[(hits_te["board_worsening"] > 0) & (hits_te["vol_persistence_high"] > 0)], "H4_absorb")
    board_only = group_metrics(hits_te[hits_te["board_improvement"] > 0], "board_impr_only")
    wors_only = group_metrics(hits_te[hits_te["board_worsening"] > 0], "board_wors_only")
    vol_hi_only = group_metrics(hits_te[hits_te["vol_persistence_high"] > 0], "vol_hi_only")
    vol_lo_only = group_metrics(hits_te[hits_te["vol_persistence_low"] > 0], "vol_lo_only")

    print("Sequence analysis...", flush=True)
    seq_df, seq_sum = sequence_analysis(hits_te)

    print("Models...", flush=True)
    models = model_comparison(train, test)
    perm_inc, rej_inc = increment_tables(train, test)

    # LOO on candidates
    cand_permit = hits_te[(hits_te["board_improvement"] > 0) & (hits_te["vol_persistence_high"] > 0)]
    cand_reject = hits_te[(hits_te["board_worsening"] > 0) & (hits_te["vol_persistence_low"] > 0)]
    loo_day_p = leave_one_out(cand_permit, "trading_date")
    loo_sym_p = leave_one_out(cand_permit, "symbol")
    loo_day_r = leave_one_out(cand_reject, "trading_date")
    loo_sym_r = leave_one_out(cand_reject, "symbol")

    # sector dependence: first digit
    if len(cand_permit):
        sector = cand_permit["symbol"].astype(str).str.replace(".T", "", regex=False).str[0]
        sector_share = sector.value_counts(normalize=True).max()
    else:
        sector_share = None

    print("CostAware overlap...", flush=True)
    overlap = costaware_overlap(test, train)

    # gates
    # fix reject gate properly
    def reject_gate(metrics, loo_day, base_wors):
        g = dict(metrics)
        g["kind"] = "reject"
        net_saved = float((-pd.to_numeric(cand_reject["runtime_exit_pnl"], errors="coerce")).sum()) if len(cand_reject) else 0.0
        # winner sacrifice vs all conf hit winners
        all_w = hits_te[hits_te["hit_winner_threshold"]]
        sac = float(len(cand_reject[cand_reject["hit_winner_threshold"]]) / max(1, len(all_w)))
        g["winner_sacrifice"] = sac
        g["net_saved_pnl"] = net_saved
        improved_vs_board = bool((metrics.get("collapse_rate") or 0) >= (base_wors.get("collapse_rate") or 0) - 1e-9)
        g["pass"] = bool(
            (metrics.get("n") or 0) >= 30
            and (metrics.get("days") or 0) >= 7
            and (metrics.get("symbols") or 0) >= 20
            and (metrics.get("collapse_rate") or 0) >= 0.50
            and sac <= 0.20
            and (metrics.get("pf_5bps") or 1) < 0.80
            and net_saved > 0
            and (bool((loo_day["mean_pnl_5bps"] < 0).all()) if len(loo_day) else False)
            and improved_vs_board
        )
        return g

    permit_gate = gate_check(h1, "permit", loo_day_p, loo_sym_p, board_only)
    # patch permit loo
    permit_gate["pass"] = bool(
        (h1.get("n") or 0) >= 30
        and (h1.get("days") or 0) >= 7
        and (h1.get("symbols") or 0) >= 20
        and (h1.get("healthy_rate") or 0) >= 0.75
        and (h1.get("collapse_rate") or 1) <= 0.20
        and (h1.get("pf_5bps") or 0) > 1.30
        and (h1.get("mean_pnl_5bps") or -1) > 0
        and (h1.get("healthy_rate") or 0) > (board_only.get("healthy_rate") or 0) + 0.02
        and (h1.get("mean_pnl_5bps") or -1) > (board_only.get("mean_pnl_5bps") or -1)
        and (bool(loo_day_p["pf_gt_1"].all()) if len(loo_day_p) else False)
        and (bool(loo_sym_p["pf_gt_1"].all()) if len(loo_sym_p) else False)
        and (sector_share is None or sector_share < 0.50)
    )
    reject_gate_res = reject_gate(h3, loo_day_r, wors_only)

    # Model preference
    m4 = models[models["model"] == "M4_board_plus_vol_simple"].iloc[0].to_dict() if len(models) and "model" in models.columns else {}
    m2 = models[models["model"] == "M2_board_impr_only"].iloc[0].to_dict() if len(models) and "model" in models.columns else {}
    m3 = models[models["model"] == "M3_vol_persistence_only"].iloc[0].to_dict() if len(models) and "model" in models.columns else {}
    m5 = models[models["model"] == "M5_quality_composite"].iloc[0].to_dict() if len(models) and "model" in models.columns else {}

    a4, a2, a3, a5 = m4.get("auc") or 0, m2.get("auc") or 0, m3.get("auc") or 0, m5.get("auc") or 0
    board_adds = (a4 - a3) >= 0.02
    vol_adds = (a4 - a2) >= 0.02
    simple_vs_comp = (a4 + 0.02) >= a5  # prefer simple if close

    # Verdict
    if permit_gate.get("pass"):
        verdict = "PULLBACK_BOARD_VOLUME_PERMIT_CONFIRMED"
    elif reject_gate_res.get("pass"):
        verdict = "PULLBACK_BOARD_VOLUME_REJECT_CONFIRMED"
    elif not vol_adds and board_adds:
        verdict = "PULLBACK_BOARD_ONLY_SUFFICIENT"
    elif not board_adds and vol_adds:
        verdict = "PULLBACK_VOLUME_ONLY_SUFFICIENT"
    elif (h1.get("n") or 0) >= 15 and (h1.get("healthy_rate") or 0) > (board_only.get("healthy_rate") or 0) and not permit_gate.get("pass"):
        # discovery-like improvement but gates fail
        if (h1.get("days") or 0) < 7 or not (bool(loo_day_p["pf_gt_1"].all()) if len(loo_day_p) else False):
            verdict = "PULLBACK_BOARD_VOLUME_NOT_STABLE"
        elif not vol_adds:
            verdict = "PULLBACK_BOARD_ONLY_SUFFICIENT"
        else:
            verdict = "PULLBACK_BOARD_VOLUME_NOT_STABLE"
    elif not vol_adds and (board_only.get("healthy_rate") or 0) >= (h1.get("healthy_rate") or 0) - 0.01:
        verdict = "PULLBACK_BOARD_ONLY_SUFFICIENT"
    elif not board_adds:
        verdict = "PULLBACK_VOLUME_ONLY_SUFFICIENT"
    else:
        verdict = "PULLBACK_BOARD_VOLUME_NOT_STABLE"

    both_n = int(overlap.loc[overlap["group"] == "both", "n"].iloc[0]) if len(overlap) and "group" in overlap.columns else 0
    independent = both_n == 0 or (
        float(overlap.loc[overlap["group"] == "healthy_permit_only", "mean_runtime_pnl"].iloc[0] or 0) > 0
    )

    disk = round(100 * shutil.disk_usage("C:/").used / shutil.disk_usage("C:/").total, 1)

    answers = {
        "1_volume_persistence_direction_ok": {
            "ok": True,
            "definition": "fraction of 300s volume steps with positive diff; higher = sustaining participation",
            "code": "phase687w43c L601-603",
        },
        "2_board_plus_vol_beats_board": {
            "beats": bool((h1.get("healthy_rate") or 0) > (board_only.get("healthy_rate") or 0) + 0.02 and (h1.get("mean_pnl_5bps") or -9) > (board_only.get("mean_pnl_5bps") or -9)),
            "permit_increment": perm_inc,
            "board_only": board_only,
            "h1": h1,
        },
        "3_worsen_plus_vol_improves_collapse": {
            "improves": bool((h3.get("collapse_rate") or 0) > (wors_only.get("collapse_rate") or 0) + 0.02),
            "reject_increment": rej_inc,
            "h3": h3,
            "wors_only": wors_only,
        },
        "4_not_day_symbol_dependent": {
            "permit_loo_day_all_pf_gt1": bool(loo_day_p["pf_gt_1"].all()) if len(loo_day_p) else None,
            "permit_loo_sym_all_pf_gt1": bool(loo_sym_p["pf_gt_1"].all()) if len(loo_sym_p) else None,
            "max_sector_share_permit": sector_share,
        },
        "5_independent_of_costaware": {
            "independent": independent,
            "both_n": both_n,
            "note": "both=0 expected if STOP chase uses high ret/slope while pullback hits have rise<0; structural not accidental",
        },
        "6_no_ampm_required": True,
        "7_shadow_spec_ready": bool(permit_gate.get("pass") or reject_gate_res.get("pass")),
        "8_runtime_changed": False,
        "9_verdict": verdict,
    }

    shadow_spec = None
    if answers["7_shadow_spec_ready"]:
        shadow_spec = {
            "enabled": False,
            "note": "Next phase only; do not overwrite PullbackMisread / CostAware",
            "permit": {
                "rule": "Dynamic40 PullbackMisread hit AND imbalance_chg_60s>0 AND vol_persistence_300s >= Discovery_q70",
                "thresholds_frozen_from": "Discovery 20260615-20260629",
                "vol_hi_thr": float(hits["_vol_hi_thr"].iloc[0]) if len(hits) else None,
            }
            if permit_gate.get("pass")
            else None,
            "reject": {
                "rule": "Dynamic40 PullbackMisread hit AND imbalance_chg_60s<0 AND vol_persistence_300s <= Discovery_q30",
                "vol_lo_thr": float(hits["_vol_lo_thr"].iloc[0]) if len(hits) else None,
            }
            if reject_gate_res.get("pass")
            else None,
        }

    report = {
        "metadata": {
            "phase": "Phase687W56",
            "generated_at": datetime.now(JST).isoformat(),
            "n_hits": int(len(hits)),
            "n_conf_hits": int(len(hits_te)),
            "vol_hi_thr_discovery": float(hits["_vol_hi_thr"].iloc[0]) if len(hits) else None,
            "vol_lo_thr_discovery": float(hits["_vol_lo_thr"].iloc[0]) if len(hits) else None,
            "runtime_unchanged": True,
            "ampm_in_rules": False,
            "disk_used_pct": disk,
        },
        "verdict": verdict,
        "feature_spec": FEATURE_SPEC,
        "missing_rates_hits": miss,
        "hypotheses": {"H1": h1, "H2": h2, "H3": h3, "H4": h4},
        "board_only": board_only,
        "wors_only": wors_only,
        "vol_hi_only": vol_hi_only,
        "vol_lo_only": vol_lo_only,
        "permit_increment": perm_inc,
        "reject_increment": rej_inc,
        "permit_gate": permit_gate,
        "reject_gate": reject_gate_res,
        "model_comparison": models.to_dict(orient="records"),
        "model_aucs": {"M2": a2, "M3": a3, "M4": a4, "M5": a5, "vol_adds": vol_adds, "board_adds": board_adds, "prefer_simple": simple_vs_comp},
        "answers": answers,
        "shadow_spec_next_phase": shadow_spec,
        "sequence_summary": seq_sum.to_dict(orient="records") if len(seq_sum) else [],
    }

    md = f"""# Phase687W56 — Pullback Board × Volume Final Interaction Audit

## Verdict
`{verdict}`

## P0 Feature specs
- **volume_persistence** (`vol_persistence_300s`): fraction of 300s volume steps with `diff>0`. Higher = sustaining participation. **Direction OK** (code: w43c L601-603).
- **board_improvement**: `imbalance_chg_60s > 0` (= imb(t0)-imb(t0-60)).
- **board_worsening**: `imbalance_chg_60s < 0`.
- **volume_acceleration**: second-half Δvol − first-half Δvol over 300s.
- **volume_persistence_change**: P12 proxy (= vol_accel); not a true persistence lag-diff.
- Missing on hits: {miss}
- Future leak: none (pre-entry snap join). No cross-symbol normalization. Quantiles Discovery-frozen.

## Population
- Dynamic40 PullbackMisread hits n={len(hits)} (Confirmation n={len(hits_te)})
- No AM/PM in rules

## 2×2 (Confirmation)
| group | n | healthy | collapse | mean_pnl_5bps | pf_5bps |
|---|---:|---:|---:|---:|---:|
"""
    for gname in ["A_board_impr_vol_hi", "B_board_impr_vol_lo", "C_board_wors_vol_hi", "D_board_wors_vol_lo"]:
        row = two_df[(two_df.split == "confirmation") & (two_df.group == gname)]
        if len(row):
            r = row.iloc[0]
            md += f"| {gname} | {r['n']} | {r['healthy_rate']} | {r['collapse_rate']} | {r['mean_pnl_5bps']} | {r['pf_5bps']} |\n"

    md += f"""
## Hypotheses (Confirmation)
- H1 permit (board↑×vol_hi): n={h1.get('n')} healthy={h1.get('healthy_rate')} collapse={h1.get('collapse_rate')} pnl5={h1.get('mean_pnl_5bps')} pf5={h1.get('pf_5bps')}
- H2 fake board (board↑×vol_lo): n={h2.get('n')} healthy={h2.get('healthy_rate')} pnl5={h2.get('mean_pnl_5bps')}
- H3 collapse (board↓×vol_lo): n={h3.get('n')} collapse={h3.get('collapse_rate')} pnl5={h3.get('mean_pnl_5bps')} pf5={h3.get('pf_5bps')}
- H4 absorb (board↓×vol_hi): n={h4.get('n')} collapse={h4.get('collapse_rate')} pnl5={h4.get('mean_pnl_5bps')}

## Increment vs Board alone
- Permit: healthy Δ={perm_inc.get('healthy_rate_delta')} pnl5 Δ={perm_inc.get('mean_pnl_5bps_delta')} n_reduction={perm_inc.get('n_reduction_rate')}
- Reject: collapse Δ={rej_inc.get('collapse_rate_delta')} winner_sac Δ={rej_inc.get('winner_sacrifice_delta')}

## Models (Confirmation AUC)
- M2 Board: {a2:.3f} | M3 VolPers: {a3:.3f} | M4 Board+Vol: {a4:.3f} | M5 Composite: {a5:.3f}
- vol_adds={vol_adds} board_adds={board_adds} prefer_simple={simple_vs_comp}

## Gates
- Permit pass: **{permit_gate.get('pass')}**
- Reject pass: **{reject_gate_res.get('pass')}**

## Mandatory answers
1. volume_persistence direction OK? **Yes**
2. Board+Vol > Board alone? **{answers['2_board_plus_vol_beats_board']['beats']}**
3. Worsen+Vol improves collapse? **{answers['3_worsen_plus_vol_improves_collapse']['improves']}**
4. Day/symbol stable? loo_day={answers['4_not_day_symbol_dependent']['permit_loo_day_all_pf_gt1']} loo_sym={answers['4_not_day_symbol_dependent']['permit_loo_sym_all_pf_gt1']}
5. Independent of CostAware? **{independent}** (both_n={both_n})
6. AM/PM unused? **Yes**
7. Shadow spec ready? **{answers['7_shadow_spec_ready']}**
8. Runtime changed? **No**
9. Verdict: `{verdict}`
"""

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "pullback_board_volume_audit_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (OUT / "pullback_board_volume_audit_report.md").write_text(md, encoding="utf-8")

    write_xlsx(
        {
            "00_summary": pd.DataFrame([{"verdict": verdict, **{k: json.dumps(v, default=str) if isinstance(v, (dict, list)) else v for k, v in answers.items()}}]),
            "01_feature_spec": pd.DataFrame(FEATURE_SPEC),
            "02_dataset_audit": pd.DataFrame(
                [{"n_hits": len(hits), "n_conf": len(hits_te), **miss, "vol_hi_thr": report["metadata"]["vol_hi_thr_discovery"], "vol_lo_thr": report["metadata"]["vol_lo_thr_discovery"]}]
            ),
            "03_board_volume_2x2": two_df,
            "04_sequence_analysis": seq_sum if len(seq_sum) else pd.DataFrame([{"note": "empty"}]),
            "05_model_comparison": models,
            "06_permit_increment": pd.DataFrame([perm_inc]),
            "07_reject_increment": pd.DataFrame([rej_inc]),
            "08_daily_stability": pd.concat(
                [loo_day_p.assign(candidate="permit"), loo_day_r.assign(candidate="reject")], ignore_index=True
            )
            if len(loo_day_p) or len(loo_day_r)
            else pd.DataFrame(),
            "09_symbol_stability": pd.concat(
                [loo_sym_p.assign(candidate="permit"), loo_sym_r.assign(candidate="reject")], ignore_index=True
            )
            if len(loo_sym_p) or len(loo_sym_r)
            else pd.DataFrame(),
            "10_costaware_overlap": overlap,
            "11_examples_healthy": cand_permit.head(30),
            "12_examples_collapse": cand_reject.head(30),
            "13_final_verdict": pd.DataFrame(
                [{"verdict": verdict, "permit_pass": permit_gate.get("pass"), "reject_pass": reject_gate_res.get("pass"), "shadow_ready": answers["7_shadow_spec_ready"]}]
            ),
            "sequence_rows": seq_df.head(500) if len(seq_df) else pd.DataFrame(),
            "hypotheses": pd.DataFrame([h1, h2, h3, h4, board_only, wors_only]),
        },
        OUT / "pullback_board_volume_audit.xlsx",
    )

    print(json.dumps({"verdict": verdict, "permit_pass": permit_gate.get("pass"), "reject_pass": reject_gate_res.get("pass"), "h1": h1, "h3": h3, "perm_inc": perm_inc, "aucs": {"M2": a2, "M3": a3, "M4": a4}}, default=str), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
