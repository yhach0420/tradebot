#!/usr/bin/env python3
"""W47 research: reentry change rules + 4062 archetype search.

Inputs (under results/research/pre_entry_market_state/_w47_tmp/):
  - entry_panel.parquet
  - entry_features.parquet
  - push_jsonl (exit-time features when needed)

Writes: _w47_tmp/reentry_archetype_results.json
Max 4 workers. Does NOT modify Runtime / YAML / trading conditions.
"""

from __future__ import annotations

import json
import math
import sys
import warnings
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import combinations, product
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.neighbors import NearestNeighbors
from sklearn.preprocessing import StandardScaler

warnings.filterwarnings("ignore", category=UserWarning)

NATIVE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(NATIVE / "scripts"))

import _w47_feature_attach as feat  # noqa: E402

TMP = NATIVE / "results" / "research" / "pre_entry_market_state" / "_w47_tmp"
PANEL_PQ = TMP / "entry_panel.parquet"
FEAT_PQ = TMP / "entry_features.parquet"
OUT_JSON = TMP / "reentry_archetype_results.json"
MAX_WORKERS = 4
COOLOFF_SEC = 1800.0
TARGET_SYMBOL = "4062.T"
MIN_RULE_N = 20
MIN_RULE_N_CONF = 10
WINNER_SACRIFICE_MAX = 0.10

DELTA_SIGNAL_COLS = ("ret_60", "slope_60", "imbalance", "spread_bps", "seconds_since")
ENTRY_FEAT_COLS = (
    "ret_30",
    "ret_60",
    "ret_120",
    "ret_300",
    "slope_60",
    "slope_120",
    "spread_bps",
    "imbalance",
    "seconds_since",
)


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _parse_ts(val: Any):
    return feat._parse_ts(val)


def _safe_float(x: Any) -> Optional[float]:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _pnl_stats(pnl: pd.Series, stop: Optional[pd.Series] = None) -> dict[str, Any]:
    s = pd.to_numeric(pnl, errors="coerce").dropna()
    n = int(len(s))
    if n == 0:
        return {
            "n": 0,
            "net_pnl": None,
            "mean_pnl": None,
            "win_rate": None,
            "pf": None,
            "stop_rate": None,
            "winner_b_rate": None,
        }
    wins = s[s > 0]
    losses = s[s < 0]
    gp = float(wins.sum()) if len(wins) else 0.0
    gl = float(losses.sum()) if len(losses) else 0.0
    pf = (gp / abs(gl)) if abs(gl) > 1e-12 else (999.0 if gp > 0 else None)
    stop_rate = None
    if stop is not None:
        st = stop.reindex(s.index)
        stop_rate = float(st.fillna(False).astype(bool).mean())
    return {
        "n": n,
        "net_pnl": float(s.sum()),
        "mean_pnl": float(s.mean()),
        "win_rate": float((s > 0).mean()),
        "pf": pf,
        "stop_rate": stop_rate,
        "winner_b_rate": float((s > 0).mean()),
    }


def _process_exit_group(args: tuple[str, str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    day, symbol, rows = args
    series = feat.load_symbol_series(day, symbol)
    out: list[dict[str, Any]] = []
    for r in rows:
        xt = _parse_ts(r.get("exit_time"))
        f = feat.features_at(series, xt.timestamp()) if xt is not None else feat.features_at(series, -1.0)
        out.append({"trade_id": r["trade_id"], **{f"exit_{k}": f.get(k) for k in ENTRY_FEAT_COLS}, "exit_feature_ok": f.get("feature_ok")})
    return out


def compute_exit_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Features at previous-trade exit_time via push_jsonl."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for rec in panel.to_dict(orient="records"):
        key = (str(rec.get("trading_date")), str(rec.get("symbol")))
        groups.setdefault(key, []).append(
            {"trade_id": rec["trade_id"], "exit_time": rec.get("exit_time")}
        )
    tasks = [(d, s, rows) for (d, s), rows in sorted(groups.items())]
    rows: list[dict[str, Any]] = []
    workers = min(MAX_WORKERS, max(1, len(tasks)))
    print(f"computing exit features for {len(tasks)} symbol-days (workers={workers})...", flush=True)
    if workers == 1 or len(tasks) <= 1:
        for t in tasks:
            rows.extend(_process_exit_group(t))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(_process_exit_group, t) for t in tasks]
            for fut in as_completed(futs):
                rows.extend(fut.result())
    return pd.DataFrame(rows)


def build_reentry_pairs(panel: pd.DataFrame, entry_feat: pd.DataFrame, exit_feat: pd.DataFrame) -> pd.DataFrame:
    """Same-symbol previous exit → next entry (same trading_date)."""
    ef = entry_feat.set_index("trade_id", drop=False)
    xf = exit_feat.set_index("trade_id", drop=False) if not exit_feat.empty else pd.DataFrame()
    pairs: list[dict[str, Any]] = []
    for (day, sym), g in panel.groupby(["trading_date", "symbol"], sort=False):
        g = g.sort_values("entry_time").reset_index(drop=True)
        if len(g) < 2:
            continue
        for i in range(len(g) - 1):
            prev = g.iloc[i]
            nxt = g.iloc[i + 1]
            xt = _parse_ts(prev.get("exit_time"))
            et = _parse_ts(nxt.get("entry_time"))
            if xt is None or et is None:
                continue
            gap = (et - xt).total_seconds()
            if gap < 0:
                continue
            prev_id = str(prev["trade_id"])
            next_id = str(nxt["trade_id"])
            # exit-state: push features at exit, else previous entry features as proxy
            exit_src = "push_exit"
            exit_vals: dict[str, Any] = {}
            if prev_id in xf.index:
                row_x = xf.loc[prev_id]
                if isinstance(row_x, pd.DataFrame):
                    row_x = row_x.iloc[0]
                for c in ENTRY_FEAT_COLS:
                    exit_vals[c] = row_x.get(f"exit_{c}")
                if not bool(row_x.get("exit_feature_ok")):
                    exit_src = "prev_entry_proxy"
            else:
                exit_src = "prev_entry_proxy"
            if exit_src == "prev_entry_proxy" and prev_id in ef.index:
                row_p = ef.loc[prev_id]
                if isinstance(row_p, pd.DataFrame):
                    row_p = row_p.iloc[0]
                for c in ENTRY_FEAT_COLS:
                    if exit_vals.get(c) is None:
                        exit_vals[c] = row_p.get(c)
            next_vals: dict[str, Any] = {}
            if next_id in ef.index:
                row_n = ef.loc[next_id]
                if isinstance(row_n, pd.DataFrame):
                    row_n = row_n.iloc[0]
                for c in ENTRY_FEAT_COLS:
                    next_vals[c] = row_n.get(c)
            deltas: dict[str, Optional[float]] = {}
            improved: dict[str, bool] = {}
            for c in DELTA_SIGNAL_COLS:
                a = _safe_float(exit_vals.get(c))
                b = _safe_float(next_vals.get(c))
                if a is None or b is None:
                    deltas[f"d_{c}"] = None
                    improved[f"imp_{c}"] = False
                    continue
                d = b - a
                deltas[f"d_{c}"] = d
                if c == "spread_bps":
                    # shrink = improvement
                    improved[f"imp_{c}"] = d < -1e-9
                elif c == "seconds_since":
                    # more recent new-high / bounce proxy
                    improved[f"imp_{c}"] = d < -1e-9
                else:
                    improved[f"imp_{c}"] = d > 1e-9
            n_imp = int(sum(1 for v in improved.values() if v))
            if n_imp <= 0:
                group = "NO_CHANGE"
            elif n_imp <= 2:
                group = "PARTIAL"
            else:
                group = "CONFIRMED_CHANGE"
            pnl = _safe_float(nxt.get("pnl_pct"))
            pairs.append(
                {
                    "trading_date": str(day),
                    "symbol": str(sym),
                    "session": nxt.get("session"),
                    "prev_trade_id": prev_id,
                    "next_trade_id": next_id,
                    "prev_exit_time": prev.get("exit_time"),
                    "next_entry_time": nxt.get("entry_time"),
                    "gap_sec": float(gap),
                    "prev_exit_reason": prev.get("exit_reason"),
                    "prev_label": prev.get("label_primary"),
                    "next_label": nxt.get("label_primary"),
                    "next_pnl_pct": pnl,
                    "next_stop": bool(nxt.get("label_stop")),
                    "next_winner_a": bool(nxt.get("label_winner_a")),
                    "next_winner_b": bool(nxt.get("label_winner_b")),
                    "next_no_progress": bool(nxt.get("label_no_progress")),
                    "exit_feature_source": exit_src,
                    "n_improved": n_imp,
                    "change_group": group,
                    **{f"exit_{c}": exit_vals.get(c) for c in ENTRY_FEAT_COLS},
                    **{f"next_{c}": next_vals.get(c) for c in ENTRY_FEAT_COLS},
                    **deltas,
                    **improved,
                }
            )
    return pd.DataFrame(pairs)


def day_split(days: list[str]) -> tuple[list[str], list[str]]:
    days = sorted(days)
    if not days:
        return [], []
    mid = max(1, len(days) // 2)
    if len(days) == 1:
        return days, days
    disc = days[:mid]
    conf = days[mid:]
    if not conf:
        conf = days[-1:]
    return disc, conf


def group_summary(df: pd.DataFrame) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for g, sub in df.groupby("change_group"):
        out[str(g)] = _pnl_stats(sub["next_pnl_pct"], sub["next_stop"])
    out["ALL"] = _pnl_stats(df["next_pnl_pct"], df["next_stop"])
    return out


def _delta_threshold_specs(disc: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    """Frozen Discovery quantile edges on delta features for 2-4 feature AND rules."""
    specs: dict[str, list[dict[str, Any]]] = {}
    for c in DELTA_SIGNAL_COLS:
        col = f"d_{c}"
        s = pd.to_numeric(disc[col], errors="coerce")
        if s.notna().sum() < MIN_RULE_N:
            continue
        qs = [0.25, 0.5, 0.75]
        vals = {q: float(s.quantile(q)) for q in qs}
        # direction-aware candidates
        if c in ("spread_bps", "seconds_since"):
            # low delta (shrink / more recent high) = improvement
            specs[col] = [
                {"feature": col, "op": "<=", "thr": vals[0.25], "side": "improved_low", "name": f"{col}<=q25"},
                {"feature": col, "op": ">=", "thr": vals[0.75], "side": "worsened_high", "name": f"{col}>=q75"},
                {"feature": col, "op": "<=", "thr": vals[0.5], "side": "le_med", "name": f"{col}<=med"},
                {"feature": col, "op": ">=", "thr": vals[0.5], "side": "ge_med", "name": f"{col}>=med"},
            ]
        else:
            specs[col] = [
                {"feature": col, "op": ">=", "thr": vals[0.75], "side": "improved_high", "name": f"{col}>=q75"},
                {"feature": col, "op": "<=", "thr": vals[0.25], "side": "worsened_low", "name": f"{col}<=q25"},
                {"feature": col, "op": ">=", "thr": vals[0.5], "side": "ge_med", "name": f"{col}>=med"},
                {"feature": col, "op": "<=", "thr": vals[0.5], "side": "le_med", "name": f"{col}<=med"},
            ]
    return specs


def _apply_spec(df: pd.DataFrame, sp: dict[str, Any]) -> pd.Series:
    s = pd.to_numeric(df[sp["feature"]], errors="coerce")
    if sp["op"] == "<=":
        return (s <= float(sp["thr"])).fillna(False)
    return (s >= float(sp["thr"])).fillna(False)


def _apply_rule(df: pd.DataFrame, rule: dict[str, Any]) -> pd.Series:
    mask = pd.Series(True, index=df.index)
    for sp in rule["specs"]:
        mask &= _apply_spec(df, sp)
    return mask.fillna(False)


def search_reentry_rules(
    disc: pd.DataFrame, conf: pd.DataFrame
) -> dict[str, Any]:
    base_all_disc = _pnl_stats(disc["next_pnl_pct"], disc["next_stop"])
    base_all_conf = _pnl_stats(conf["next_pnl_pct"], conf["next_stop"])
    no_change_disc = disc[disc["change_group"] == "NO_CHANGE"]
    no_change_conf = conf[conf["change_group"] == "NO_CHANGE"]
    nc_stop_disc = float(no_change_disc["next_stop"].mean()) if len(no_change_disc) else None
    nc_stop_conf = float(no_change_conf["next_stop"].mean()) if len(no_change_conf) else None

    feat_specs = _delta_threshold_specs(disc)
    feat_names = list(feat_specs.keys())
    reject_cands: list[dict[str, Any]] = []
    permit_cands: list[dict[str, Any]] = []

    # also include group-level masks as simple rules
    for group_name, kind in (("NO_CHANGE", "reject_seed"), ("CONFIRMED_CHANGE", "permit_seed")):
        m_d = disc["change_group"] == group_name
        m_c = conf["change_group"] == group_name
        if int(m_d.sum()) >= MIN_RULE_N:
            if kind == "reject_seed":
                # blocking NO_CHANGE reentries
                kept_d = disc.loc[~m_d]
                kept_c = conf.loc[~m_c]
                blocked_d = disc.loc[m_d]
                blocked_c = conf.loc[m_c]
                st_d = _pnl_stats(kept_d["next_pnl_pct"], kept_d["next_stop"])
                st_c = _pnl_stats(kept_c["next_pnl_pct"], kept_c["next_stop"])
                bl_d = _pnl_stats(blocked_d["next_pnl_pct"], blocked_d["next_stop"])
                bl_c = _pnl_stats(blocked_c["next_pnl_pct"], blocked_c["next_stop"])
                winners_sac = (
                    float(blocked_d["next_winner_b"].sum()) / max(1, int(disc["next_winner_b"].sum()))
                    if int(disc["next_winner_b"].sum())
                    else 0.0
                )
                winners_sac_c = (
                    float(blocked_c["next_winner_b"].sum()) / max(1, int(conf["next_winner_b"].sum()))
                    if int(conf["next_winner_b"].sum())
                    else 0.0
                )
                pnl_improve_d = (st_d["net_pnl"] or -1e18) > (base_all_disc["net_pnl"] or 0)
                pnl_improve_c = (st_c["net_pnl"] or -1e18) > (base_all_conf["net_pnl"] or 0)
                reject_cands.append(
                    {
                        "rule_id": "GROUP::NO_CHANGE_block",
                        "kind": "reentry_reject",
                        "description": "block change_group==NO_CHANGE",
                        "n_features": 0,
                        "specs": [],
                        "discovery": {
                            "blocked": bl_d,
                            "kept": st_d,
                            "net_pnl_delta": (st_d["net_pnl"] or 0) - (base_all_disc["net_pnl"] or 0),
                            "winner_sacrifice_rate": winners_sac,
                            "pnl_improves": pnl_improve_d,
                        },
                        "confirmation": {
                            "blocked": bl_c,
                            "kept": st_c,
                            "net_pnl_delta": (st_c["net_pnl"] or 0) - (base_all_conf["net_pnl"] or 0),
                            "winner_sacrifice_rate": winners_sac_c,
                            "pnl_improves": pnl_improve_c,
                        },
                        "confirmed": bool(
                            pnl_improve_c
                            and winners_sac_c <= WINNER_SACRIFICE_MAX
                            and (bl_c["n"] or 0) >= MIN_RULE_N_CONF
                        ),
                    }
                )
            else:
                st_d = _pnl_stats(disc.loc[m_d, "next_pnl_pct"], disc.loc[m_d, "next_stop"])
                st_c = _pnl_stats(conf.loc[m_c, "next_pnl_pct"], conf.loc[m_c, "next_stop"])
                ok_d = (
                    (st_d["net_pnl"] or -1e18) > 0
                    and (st_d["pf"] or 0) > 1
                    and (nc_stop_disc is None or (st_d["stop_rate"] or 1) < nc_stop_disc)
                )
                ok_c = (
                    (st_c["n"] or 0) >= MIN_RULE_N_CONF
                    and (st_c["net_pnl"] or -1e18) > 0
                    and (st_c["pf"] or 0) > 1
                    and (nc_stop_conf is None or (st_c["stop_rate"] or 1) < nc_stop_conf)
                )
                permit_cands.append(
                    {
                        "rule_id": "GROUP::CONFIRMED_CHANGE_permit",
                        "kind": "reentry_permit",
                        "description": "permit change_group==CONFIRMED_CHANGE",
                        "n_features": 0,
                        "specs": [],
                        "discovery": {**st_d, "vs_no_change_stop": nc_stop_disc, "permit_ok": ok_d},
                        "confirmation": {**st_c, "vs_no_change_stop": nc_stop_conf, "permit_ok": ok_c},
                        "confirmed": bool(ok_c),
                    }
                )

    # 2-4 feature AND rules
    for k in (2, 3, 4):
        if len(feat_names) < k:
            continue
        for feats in combinations(feat_names, k):
            # pick one sidedness per feature; limit explosion: use improved/worsened extremes only
            side_lists = []
            for f in feats:
                sides = [sp for sp in feat_specs[f] if sp["side"] in ("improved_high", "improved_low", "worsened_high", "worsened_low")]
                if not sides:
                    sides = feat_specs[f][:2]
                side_lists.append(sides)
            for combo in product(*side_lists):
                rule = {
                    "specs": [
                        {kk: sp[kk] for kk in ("feature", "op", "thr", "side", "name")}
                        for sp in combo
                    ],
                    "description": " AND ".join(sp["name"] for sp in combo),
                    "features": list(feats),
                    "n_features": k,
                }
                m_d = _apply_rule(disc, rule)
                m_c = _apply_rule(conf, rule)
                n_d = int(m_d.sum())
                if n_d < MIN_RULE_N:
                    continue
                # classify: majority worsened → reject; majority improved → permit
                n_imp_side = sum(1 for sp in combo if "improved" in sp["side"])
                n_wor_side = sum(1 for sp in combo if "worsened" in sp["side"])
                blocked_d = disc.loc[m_d]
                kept_d = disc.loc[~m_d]
                kept_c = conf.loc[~m_c]
                blocked_c = conf.loc[m_c]
                st_kept_d = _pnl_stats(kept_d["next_pnl_pct"], kept_d["next_stop"])
                st_kept_c = _pnl_stats(kept_c["next_pnl_pct"], kept_c["next_stop"])
                st_blk_d = _pnl_stats(blocked_d["next_pnl_pct"], blocked_d["next_stop"])
                st_blk_c = _pnl_stats(blocked_c["next_pnl_pct"], blocked_c["next_stop"])
                mean_imp_d = float(pd.to_numeric(blocked_d["n_improved"], errors="coerce").mean()) if n_d else 0.0

                if n_wor_side >= n_imp_side:
                    wsac_d = (
                        float(blocked_d["next_winner_b"].sum()) / max(1, int(disc["next_winner_b"].sum()))
                        if int(disc["next_winner_b"].sum())
                        else 0.0
                    )
                    wsac_c = (
                        float(blocked_c["next_winner_b"].sum()) / max(1, int(conf["next_winner_b"].sum()))
                        if int(conf["next_winner_b"].sum())
                        else 0.0
                    )
                    pnl_d = (st_kept_d["net_pnl"] or -1e18) > (base_all_disc["net_pnl"] or 0)
                    pnl_c = (st_kept_c["net_pnl"] or -1e18) > (base_all_conf["net_pnl"] or 0)
                    score = (
                        ((st_kept_d["net_pnl"] or 0) - (base_all_disc["net_pnl"] or 0))
                        - 2.0 * wsac_d * abs(base_all_disc["net_pnl"] or 1)
                        - 0.1 * mean_imp_d
                    )
                    reject_cands.append(
                        {
                            "rule_id": f"REJECT::{rule['description']}",
                            "kind": "reentry_reject",
                            "description": rule["description"],
                            "n_features": k,
                            "specs": rule["specs"],
                            "features": rule["features"],
                            "discovery": {
                                "blocked": st_blk_d,
                                "kept": st_kept_d,
                                "net_pnl_delta": (st_kept_d["net_pnl"] or 0) - (base_all_disc["net_pnl"] or 0),
                                "winner_sacrifice_rate": wsac_d,
                                "mean_n_improved_blocked": mean_imp_d,
                                "pnl_improves": pnl_d,
                            },
                            "confirmation": {
                                "blocked": st_blk_c,
                                "kept": st_kept_c,
                                "net_pnl_delta": (st_kept_c["net_pnl"] or 0) - (base_all_conf["net_pnl"] or 0),
                                "winner_sacrifice_rate": wsac_c,
                                "pnl_improves": pnl_c,
                            },
                            "score": score,
                            "confirmed": bool(
                                pnl_c
                                and wsac_c <= WINNER_SACRIFICE_MAX
                                and (st_blk_c["n"] or 0) >= MIN_RULE_N_CONF
                            ),
                        }
                    )
                if n_imp_side >= n_wor_side:
                    ok_d = (
                        (st_blk_d["net_pnl"] or -1e18) > 0
                        and (st_blk_d["pf"] or 0) > 1
                        and (nc_stop_disc is None or (st_blk_d["stop_rate"] or 1) < nc_stop_disc + 1e-12)
                        and mean_imp_d >= 2.0
                    )
                    ok_c = (
                        (st_blk_c["n"] or 0) >= MIN_RULE_N_CONF
                        and (st_blk_c["net_pnl"] or -1e18) > 0
                        and (st_blk_c["pf"] or 0) > 1
                        and (nc_stop_conf is None or (st_blk_c["stop_rate"] or 1) < nc_stop_conf + 1e-12)
                    )
                    score = (st_blk_d["net_pnl"] or 0) * math.sqrt(n_d) + 10.0 * ((st_blk_d["pf"] or 0) - 1)
                    permit_cands.append(
                        {
                            "rule_id": f"PERMIT::{rule['description']}",
                            "kind": "reentry_permit",
                            "description": rule["description"],
                            "n_features": k,
                            "specs": rule["specs"],
                            "features": rule["features"],
                            "discovery": {
                                **st_blk_d,
                                "vs_no_change_stop": nc_stop_disc,
                                "mean_n_improved": mean_imp_d,
                                "permit_ok": ok_d,
                            },
                            "confirmation": {
                                **st_blk_c,
                                "vs_no_change_stop": nc_stop_conf,
                                "permit_ok": ok_c,
                            },
                            "score": score,
                            "confirmed": bool(ok_c),
                        }
                    )

    # dedupe / top
    def _top(cands: list[dict], key: str = "score", n: int = 15) -> list[dict]:
        seen = set()
        ranked = sorted(cands, key=lambda r: r.get(key, -1e18), reverse=True)
        out = []
        for r in ranked:
            desc = r.get("description")
            if desc in seen:
                continue
            seen.add(desc)
            out.append(r)
            if len(out) >= n:
                break
        return out

    reject_top = _top(reject_cands)
    permit_top = _top(permit_cands)
    return {
        "baseline_discovery": base_all_disc,
        "baseline_confirmation": base_all_conf,
        "no_change_stop_rate_discovery": nc_stop_disc,
        "no_change_stop_rate_confirmation": nc_stop_conf,
        "reject_candidates": reject_top,
        "permit_candidates": permit_top,
        "reject_confirmed": [r for r in reject_top if r.get("confirmed")],
        "permit_confirmed": [r for r in permit_top if r.get("confirmed")],
        "n_reject_searched": len(reject_cands),
        "n_permit_searched": len(permit_cands),
    }


def cooloff_baseline(pairs: pd.DataFrame, disc_days: set[str], conf_days: set[str]) -> dict[str, Any]:
    """Fixed 30-min cooloff: block reentries with gap_sec < 1800."""

    def _eval(df: pd.DataFrame) -> dict[str, Any]:
        if df.empty:
            return {"n_pairs": 0}
        block = df["gap_sec"] < COOLOFF_SEC
        kept = df.loc[~block]
        blocked = df.loc[block]
        base = _pnl_stats(df["next_pnl_pct"], df["next_stop"])
        kept_s = _pnl_stats(kept["next_pnl_pct"], kept["next_stop"])
        blocked_s = _pnl_stats(blocked["next_pnl_pct"], blocked["next_stop"])
        wsac = (
            float(blocked["next_winner_b"].sum()) / max(1, int(df["next_winner_b"].sum()))
            if int(df["next_winner_b"].sum())
            else 0.0
        )
        return {
            "n_pairs": int(len(df)),
            "n_blocked": int(block.sum()),
            "n_kept": int((~block).sum()),
            "baseline": base,
            "kept": kept_s,
            "blocked": blocked_s,
            "net_pnl_delta": (kept_s["net_pnl"] or 0) - (base["net_pnl"] or 0),
            "winner_sacrifice_rate": wsac,
        }

    disc = pairs[pairs["trading_date"].isin(disc_days)]
    conf = pairs[pairs["trading_date"].isin(conf_days)]
    return {
        "cooloff_sec": COOLOFF_SEC,
        "all": _eval(pairs),
        "discovery": _eval(disc),
        "confirmation": _eval(conf),
    }


def compare_to_cooloff(rules: dict[str, Any], cool: dict[str, Any]) -> dict[str, Any]:
    cool_c = cool.get("confirmation") or {}
    best_rej = (rules.get("reject_confirmed") or rules.get("reject_candidates") or [{}])[0]
    best_per = (rules.get("permit_confirmed") or rules.get("permit_candidates") or [{}])[0]
    rej_delta = ((best_rej.get("confirmation") or {}).get("net_pnl_delta"))
    cool_delta = cool_c.get("net_pnl_delta")
    return {
        "cooloff_confirmation_net_pnl_delta": cool_delta,
        "best_reject_confirmation_net_pnl_delta": rej_delta,
        "reject_beats_cooloff": (
            rej_delta is not None and cool_delta is not None and rej_delta > cool_delta
        ),
        "best_reject_rule": best_rej.get("description"),
        "best_permit_rule": best_per.get("description"),
        "best_permit_confirmation": best_per.get("confirmation"),
    }


def archetype_4062(panel: pd.DataFrame, entry_feat: pd.DataFrame) -> dict[str, Any]:
    merged = panel.merge(
        entry_feat[["trade_id"] + [c for c in ENTRY_FEAT_COLS if c in entry_feat.columns] + ["feature_ok"]],
        on="trade_id",
        how="left",
        suffixes=("", "_feat"),
    )
    use_cols = [c for c in ENTRY_FEAT_COLS if c in merged.columns]
    feat_ok = merged[use_cols].apply(pd.to_numeric, errors="coerce")
    # keep rows with enough features
    ok_mask = feat_ok.notna().sum(axis=1) >= max(4, len(use_cols) // 2)
    df = merged.loc[ok_mask].copy()
    X = feat_ok.loc[ok_mask].copy()
    med = X.median()
    X = X.fillna(med)
    scaler = StandardScaler()
    Xs = scaler.fit_transform(X.values)

    is_4062 = df["symbol"].astype(str) == TARGET_SYMBOL
    idx_4062 = np.where(is_4062.to_numpy())[0]
    idx_other = np.where(~is_4062.to_numpy())[0]
    n_4062 = int(len(idx_4062))
    if n_4062 == 0:
        return {"error": "no_4062_trades_with_features", "n_4062": 0}

    # nearest neighbors among other symbols
    k = min(15, max(3, len(idx_other) // 50))
    nn_rows: list[dict[str, Any]] = []
    neighbor_trade_ids: set[str] = set()
    if len(idx_other) >= k:
        nn = NearestNeighbors(n_neighbors=k, metric="euclidean")
        nn.fit(Xs[idx_other])
        dists, inds = nn.kneighbors(Xs[idx_4062])
        # also cosine via normalized
        Xs_norm = Xs / (np.linalg.norm(Xs, axis=1, keepdims=True) + 1e-12)
        nn_cos = NearestNeighbors(n_neighbors=k, metric="euclidean")
        nn_cos.fit(Xs_norm[idx_other])
        dists_c, inds_c = nn_cos.kneighbors(Xs_norm[idx_4062])
        for i, local_i in enumerate(idx_4062):
            row = df.iloc[local_i]
            neigh_local = idx_other[inds[i]]
            for j, ni in enumerate(neigh_local):
                nr = df.iloc[ni]
                neighbor_trade_ids.add(str(nr["trade_id"]))
                nn_rows.append(
                    {
                        "query_trade_id": row["trade_id"],
                        "query_pnl": _safe_float(row.get("pnl_pct")),
                        "neighbor_trade_id": nr["trade_id"],
                        "neighbor_symbol": nr["symbol"],
                        "neighbor_pnl": _safe_float(nr.get("pnl_pct")),
                        "neighbor_stop": bool(nr.get("label_stop")),
                        "euclid_dist": float(dists[i][j]),
                        "cosine_proxy_dist": float(dists_c[i][j]) if j < len(dists_c[i]) else None,
                    }
                )
    nn_df = pd.DataFrame(nn_rows)
    nn_summary = {
        "k": k,
        "n_neighbor_rows": int(len(nn_df)),
        "unique_neighbor_trades": int(nn_df["neighbor_trade_id"].nunique()) if len(nn_df) else 0,
        "neighbor_net_pnl": float(nn_df["neighbor_pnl"].sum()) if len(nn_df) else None,
        "neighbor_mean_pnl": float(nn_df["neighbor_pnl"].mean()) if len(nn_df) else None,
        "neighbor_stop_rate": float(nn_df["neighbor_stop"].mean()) if len(nn_df) else None,
        "neighbor_win_rate": float((nn_df["neighbor_pnl"] > 0).mean()) if len(nn_df) else None,
        "top_neighbor_symbols": (
            nn_df["neighbor_symbol"].astype(str).value_counts().head(10).to_dict() if len(nn_df) else {}
        ),
    }

    # clustering on all entries
    n_clusters = int(min(6, max(3, len(df) // 200)))
    km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = km.fit_predict(Xs)
    df = df.copy()
    df["cluster"] = labels
    cluster_stats = []
    for cid in sorted(set(labels)):
        sub = df[df["cluster"] == cid]
        st = _pnl_stats(sub["pnl_pct"], sub["label_stop"])
        n_t = int((sub["symbol"].astype(str) == TARGET_SYMBOL).sum())
        means = {c: float(pd.to_numeric(sub[c], errors="coerce").mean()) for c in use_cols}
        cluster_stats.append({"cluster": int(cid), "n_4062": n_t, "feature_means": means, **st})

    # dominant 4062 cluster(s)
    c4062 = df.loc[is_4062, "cluster"]
    if len(c4062):
        dom = int(c4062.value_counts().index[0])
    else:
        dom = int(cluster_stats[0]["cluster"]) if cluster_stats else 0
    dom_row = next((c for c in cluster_stats if c["cluster"] == dom), {})
    means = dom_row.get("feature_means") or {}

    # rule-based archetype name from common patterns in 4062 / dominant cluster
    def _name_archetype(m: dict[str, float], sub: pd.DataFrame) -> str:
        ret60 = m.get("ret_60")
        slope = m.get("slope_60")
        imb = m.get("imbalance")
        spr = m.get("spread_bps")
        sec = m.get("seconds_since")
        stop_r = float(sub["label_stop"].mean()) if len(sub) else 0
        tags = []
        if ret60 is not None and ret60 > 0.3:
            tags.append("CHASE_HIGH")
        if slope is not None and slope < 0:
            tags.append("SLOPE_FADE")
        if imb is not None and imb < 0:
            tags.append("WEAK_BOARD")
        if spr is not None and spr > 8:
            tags.append("WIDE_SPREAD")
        if sec is not None and sec > 60:
            tags.append("STALE_HIGH")
        if stop_r >= 0.35:
            tags.append("STOP_HEAVY")
        if not tags:
            tags.append("GENERIC_MOMENTUM")
        return "_".join(tags[:4])

    sub_dom = df[df["cluster"] == dom]
    archetype_name = _name_archetype(means, sub_dom)

    # rule-based membership: within 0.75 std of 4062 mean on key features
    t4062 = df.loc[is_4062]
    mu = {c: float(pd.to_numeric(t4062[c], errors="coerce").mean()) for c in use_cols}
    sd = {c: float(pd.to_numeric(t4062[c], errors="coerce").std() or 1.0) for c in use_cols}
    key_feats = [c for c in ("ret_60", "slope_60", "imbalance", "spread_bps", "seconds_since", "ret_300") if c in use_cols]
    z = np.zeros(len(df))
    for c in key_feats:
        s = pd.to_numeric(df[c], errors="coerce").fillna(mu[c]).to_numpy()
        z += ((s - mu[c]) / (sd[c] if sd[c] > 1e-9 else 1.0)) ** 2
    z = np.sqrt(z / max(1, len(key_feats)))
    # archetype match: z <= median z of 4062 OR in dominant cluster, excluding exact symbol filter for others
    z4062_med = float(np.median(z[is_4062.to_numpy()])) if n_4062 else 1.0
    thr = max(z4062_med * 1.25, 0.85)
    df["archetype_z"] = z
    df["in_archetype"] = (df["cluster"] == dom) | (df["archetype_z"] <= thr)
    # for reject test: others matching archetype (not requiring 4062)
    others_arch = df.loc[(~is_4062) & (df["in_archetype"])]
    # also include NN neighbors as archetype members
    if neighbor_trade_ids:
        df.loc[df["trade_id"].astype(str).isin(neighbor_trade_ids), "in_archetype"] = True
        others_arch = df.loc[(~is_4062) & (df["in_archetype"])]

    # Discovery / Confirmation by day for reject test
    days = sorted(df["trading_date"].astype(str).unique())
    disc_days, conf_days = day_split(days)
    disc = df[df["trading_date"].astype(str).isin(disc_days)]
    conf = df[df["trading_date"].astype(str).isin(conf_days)]

    def _reject_arch(split: pd.DataFrame) -> dict[str, Any]:
        # reject archetype matches among non-4062 + optionally all archetype
        mask = split["in_archetype"].astype(bool) & (split["symbol"].astype(str) != TARGET_SYMBOL)
        # also allow rejecting 4062 itself as part of archetype filter (symbol-agnostic rule)
        mask_all = split["in_archetype"].astype(bool)
        base = _pnl_stats(split["pnl_pct"], split["label_stop"])
        kept = split.loc[~mask_all]
        blocked = split.loc[mask_all]
        kept_s = _pnl_stats(kept["pnl_pct"], kept["label_stop"])
        blocked_s = _pnl_stats(blocked["pnl_pct"], blocked["label_stop"])
        n_win_base = int(split["label_winner_b"].sum()) if "label_winner_b" in split.columns else int((pd.to_numeric(split["pnl_pct"], errors="coerce") > 0).sum())
        n_win_blk = int(blocked["label_winner_b"].sum()) if "label_winner_b" in blocked.columns else int((pd.to_numeric(blocked["pnl_pct"], errors="coerce") > 0).sum())
        wsac = (n_win_blk / n_win_base) if n_win_base else 0.0
        # others-only view
        others = split[split["symbol"].astype(str) != TARGET_SYMBOL]
        o_mask = others["in_archetype"].astype(bool)
        o_base = _pnl_stats(others["pnl_pct"], others["label_stop"])
        o_kept = others.loc[~o_mask]
        o_kept_s = _pnl_stats(o_kept["pnl_pct"], o_kept["label_stop"])
        o_blk = others.loc[o_mask]
        o_blk_s = _pnl_stats(o_blk["pnl_pct"], o_blk["label_stop"])
        return {
            "baseline": base,
            "blocked_all_archetype": blocked_s,
            "kept_after_reject": kept_s,
            "net_pnl_delta": (kept_s["net_pnl"] or 0) - (base["net_pnl"] or 0),
            "winners_sacrificed": n_win_blk,
            "winner_sacrifice_rate": wsac,
            "others_only": {
                "baseline": o_base,
                "blocked": o_blk_s,
                "kept": o_kept_s,
                "net_pnl_delta": (o_kept_s["net_pnl"] or 0) - (o_base["net_pnl"] or 0),
                "n_blocked": int(o_mask.sum()),
            },
            "n_archetype": int(mask_all.sum()),
            "n_archetype_other_symbols": int(mask.sum()),
        }

    rej_disc = _reject_arch(disc)
    rej_conf = _reject_arch(conf)

    t4062_stats = _pnl_stats(t4062["pnl_pct"], t4062["label_stop"])
    others_arch_stats = _pnl_stats(others_arch["pnl_pct"], others_arch["label_stop"])
    # reusable if: other-symbol archetype loses (mean/net < 0 or high stop) AND reject improves conf pnl
    others_lose = (
        (others_arch_stats.get("net_pnl") is not None and others_arch_stats["net_pnl"] < 0)
        or (others_arch_stats.get("stop_rate") is not None and others_arch_stats["stop_rate"] >= (t4062_stats.get("stop_rate") or 0) * 0.8)
    ) and (others_arch_stats.get("n") or 0) >= MIN_RULE_N_CONF
    reject_helps = (rej_conf.get("net_pnl_delta") or 0) > 0 or (
        (rej_conf.get("others_only") or {}).get("net_pnl_delta") or 0
    ) > 0
    if others_lose and reject_helps and (rej_conf.get("n_archetype_other_symbols") or 0) >= MIN_RULE_N_CONF:
        verdict = "ARCHETYPE_4062_CONFIRMED"
    else:
        verdict = "ARCHETYPE_4062_SYMBOL_SPECIFIC"

    return {
        "target_symbol": TARGET_SYMBOL,
        "n_4062": n_4062,
        "n_4062_with_features": n_4062,
        "n_universe": int(len(df)),
        "feature_cols": use_cols,
        "key_features": key_feats,
        "4062_stats": t4062_stats,
        "4062_feature_means": mu,
        "archetype_name": archetype_name,
        "archetype_z_threshold": thr,
        "n_clusters": n_clusters,
        "dominant_cluster": dom,
        "cluster_stats": cluster_stats,
        "nearest_neighbors": nn_summary,
        "neighbor_sample": nn_rows[:40],
        "other_symbol_archetype_stats": others_arch_stats,
        "n_other_archetype_trades": int(len(others_arch)),
        "discovery_days": disc_days,
        "confirmation_days": conf_days,
        "reject_test_discovery": rej_disc,
        "reject_test_confirmation": rej_conf,
        "verdict": verdict,
        "verdict_reason": {
            "others_lose": others_lose,
            "reject_helps_confirmation": reject_helps,
            "n_other_on_confirmation": rej_conf.get("n_archetype_other_symbols"),
        },
    }


def main() -> int:
    if not PANEL_PQ.is_file():
        raise SystemExit(f"missing {PANEL_PQ}")
    if not FEAT_PQ.is_file():
        raise SystemExit(f"missing {FEAT_PQ}")

    print("loading panel + entry features...", flush=True)
    panel = pd.read_parquet(PANEL_PQ)
    entry_feat = pd.read_parquet(FEAT_PQ)
    print(f"panel={len(panel)} features={len(entry_feat)}", flush=True)

    exit_feat = compute_exit_features(panel)
    print(f"exit_feat rows={len(exit_feat)} ok={float(exit_feat['exit_feature_ok'].mean()) if len(exit_feat) else None}", flush=True)

    print("building reentry pairs...", flush=True)
    pairs = build_reentry_pairs(panel, entry_feat, exit_feat)
    print(
        f"pairs={len(pairs)} groups={pairs['change_group'].value_counts().to_dict() if len(pairs) else {}}",
        flush=True,
    )

    days_with_pairs = sorted(pairs["trading_date"].astype(str).unique()) if len(pairs) else []
    disc_days, conf_days = day_split(days_with_pairs)
    disc = pairs[pairs["trading_date"].isin(disc_days)].copy() if len(pairs) else pairs
    conf = pairs[pairs["trading_date"].isin(conf_days)].copy() if len(pairs) else pairs
    print(f"Discovery days={disc_days} n={len(disc)}", flush=True)
    print(f"Confirmation days={conf_days} n={len(conf)}", flush=True)

    group_all = group_summary(pairs) if len(pairs) else {}
    group_disc = group_summary(disc) if len(disc) else {}
    group_conf = group_summary(conf) if len(conf) else {}

    print("searching reentry reject/permit rules...", flush=True)
    rules = search_reentry_rules(disc, conf) if len(disc) and len(conf) else {
        "error": "insufficient_pairs_for_split",
        "reject_confirmed": [],
        "permit_confirmed": [],
    }
    cool = cooloff_baseline(pairs, set(disc_days), set(conf_days)) if len(pairs) else {}
    vs_cool = compare_to_cooloff(rules, cool) if cool else {}

    print("4062 archetype analysis...", flush=True)
    arch = archetype_4062(panel, entry_feat)

    # overall reentry verdict
    n_rej_ok = len(rules.get("reject_confirmed") or [])
    n_per_ok = len(rules.get("permit_confirmed") or [])
    if n_rej_ok or n_per_ok:
        reentry_verdict = "REENTRY_CHANGE_RULE_CONFIRMED"
    else:
        reentry_verdict = "NO_STABLE_REENTRY_RULE"

    result = {
        "phase": "W47_reentry_archetype_search",
        "runtime_trading_conditions_modified": False,
        "max_workers": MAX_WORKERS,
        "inputs": {
            "entry_panel": str(PANEL_PQ),
            "entry_features": str(FEAT_PQ),
            "n_panel": int(len(panel)),
            "n_features": int(len(entry_feat)),
        },
        "reentry": {
            "n_pairs": int(len(pairs)),
            "days_with_pairs": days_with_pairs,
            "discovery_days": disc_days,
            "confirmation_days": conf_days,
            "exit_feature_source_counts": (
                pairs["exit_feature_source"].value_counts().to_dict() if len(pairs) else {}
            ),
            "change_group_counts": (
                pairs["change_group"].value_counts().to_dict() if len(pairs) else {}
            ),
            "group_stats_all": group_all,
            "group_stats_discovery": group_disc,
            "group_stats_confirmation": group_conf,
            "improved_signal_defs": {
                "ret_60": "next - exit > 0",
                "slope_60": "next - exit > 0",
                "imbalance": "next - exit > 0",
                "spread_bps": "next - exit < 0 (shrink)",
                "seconds_since": "next - exit < 0 (more recent new-high / bounce proxy)",
                "NO_CHANGE": "n_improved == 0",
                "PARTIAL": "n_improved in {1,2}",
                "CONFIRMED_CHANGE": "n_improved >= 3",
            },
            "rules": rules,
            "cooloff_30m_baseline": cool,
            "vs_cooloff": vs_cool,
            "verdict": reentry_verdict,
        },
        "archetype_4062": arch,
        "verdicts": {
            "reentry": reentry_verdict,
            "archetype": arch.get("verdict"),
        },
    }
    _wj(OUT_JSON, result)
    # compact pair export for debugging
    if len(pairs):
        pairs.to_parquet(TMP / "reentry_pairs.parquet", index=False)

    print("=" * 60, flush=True)
    print(f"RESULT_PATH={OUT_JSON}", flush=True)
    print(f"REENTRY_PAIRS={len(pairs)}", flush=True)
    print(f"REENTRY_VERDICT={reentry_verdict}", flush=True)
    print(f"ARCHETYPE_VERDICT={arch.get('verdict')}", flush=True)
    print(f"ARCHETYPE_NAME={arch.get('archetype_name')}", flush=True)
    print(
        f"reject_confirmed={n_rej_ok} permit_confirmed={n_per_ok} "
        f"cooloff_conf_delta={vs_cool.get('cooloff_confirmation_net_pnl_delta')}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
