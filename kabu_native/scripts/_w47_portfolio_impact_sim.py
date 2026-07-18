#!/usr/bin/env python3
"""W47 research: portfolio impact counterfactual simulations.

Reads entry panel + features (and optional reject/trigger JSONs). Writes
_w47_tmp/portfolio_impact_results.json. Does NOT modify Runtime / YAML / PBv2
or overwrite Paper artifacts.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(NATIVE / "src"))

from replay.pnl_yen import compute_pnl_yen_100  # noqa: E402

TMP = NATIVE / "results" / "research" / "pre_entry_market_state" / "_w47_tmp"
PANEL_PQ = TMP / "entry_panel.parquet"
FEAT_PQ = TMP / "entry_features.parquet"
WINNER_JSON = TMP / "winner_trigger_results.json"
REJECT_JSON = TMP / "stop_np_reject_results.json"
OUT_JSON = TMP / "portfolio_impact_results.json"
SNAP_0717 = TMP / "day_snaps" / "20260717_watch50_snapshot.parquet"

TARGET_DAYS = 20
CAP = 5
SHARES = 100
HORIZON_PROXY = timedelta(minutes=30)
COST = 0.0  # gross; fees/tax excluded per pnl_yen.py


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default) + "\n", encoding="utf-8")


def _json_default(o: Any) -> Any:
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        v = float(o)
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, pd.Timestamp):
        return o.isoformat()
    if isinstance(o, datetime):
        return o.isoformat()
    return str(o)


def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None or val == "" or (isinstance(val, float) and math.isnan(val)):
        return None
    if isinstance(val, pd.Timestamp):
        return val.to_pydatetime()
    if isinstance(val, datetime):
        return val
    try:
        return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except ValueError:
        return None


def _num(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _parse_interval(text: str) -> tuple[float, float, str, str]:
    text = text.strip()
    left = "closed" if text[0] == "[" else "open"
    right = "closed" if text[-1] == "]" else "open"
    body = text[1:-1]
    a, b = body.split(",")
    return float(a), float(b), left, right


def apply_rule_description(df: pd.DataFrame, description: str) -> pd.Series:
    if not description or description == "TRUE":
        return pd.Series(True, index=df.index)
    parts = [p.strip() for p in str(description).split(" AND ") if p.strip()]
    mask = pd.Series(True, index=df.index)
    for p in parts:
        if " in " in p and ("[" in p or "(" in p):
            feat, interval = p.split(" in ", 1)
            feat = feat.strip()
            if feat not in df.columns:
                return pd.Series(False, index=df.index)
            lo, hi, left, right = _parse_interval(interval.strip())
            s = pd.to_numeric(df[feat], errors="coerce")
            ok = s.notna()
            ok &= s >= lo if left == "closed" else s > lo
            ok &= s <= hi if right == "closed" else s < hi
            mask &= ok
        elif "<=" in p:
            feat, thr = p.split("<=", 1)
            feat = feat.strip()
            if feat not in df.columns:
                return pd.Series(False, index=df.index)
            mask &= pd.to_numeric(df[feat], errors="coerce") <= float(thr)
        elif ">=" in p:
            feat, thr = p.split(">=", 1)
            feat = feat.strip()
            if feat not in df.columns:
                return pd.Series(False, index=df.index)
            mask &= pd.to_numeric(df[feat], errors="coerce") >= float(thr)
        elif ">" in p:
            feat, thr = p.split(">", 1)
            feat = feat.strip()
            if feat not in df.columns:
                return pd.Series(False, index=df.index)
            mask &= pd.to_numeric(df[feat], errors="coerce") > float(thr)
        elif "<" in p:
            feat, thr = p.split("<", 1)
            feat = feat.strip()
            if feat not in df.columns:
                return pd.Series(False, index=df.index)
            mask &= pd.to_numeric(df[feat], errors="coerce") < float(thr)
        else:
            mask &= False
    return mask.fillna(False)


def rule_reject_mask(df: pd.DataFrame, rule: Optional[dict[str, Any]]) -> pd.Series:
    """True = reject (block) this entry."""
    if not rule:
        return pd.Series(False, index=df.index)
    if "mask_trade_ids" in rule:
        ids = set(str(x) for x in rule["mask_trade_ids"])
        return df["trade_id"].astype(str).isin(ids)
    specs = rule.get("specs") or []
    if specs:
        mask = pd.Series(True, index=df.index)
        for sp in specs:
            if not isinstance(sp, dict):
                continue
            feat = sp.get("feature")
            if not feat or feat not in df.columns:
                return pd.Series(False, index=df.index)
            s = pd.to_numeric(df[feat], errors="coerce")
            ok = s.notna()
            lo = sp.get("lo")
            hi = sp.get("hi")
            if lo is not None:
                ok &= s >= float(lo) if sp.get("left_closed", True) else s > float(lo)
            if hi is not None:
                ok &= s <= float(hi) if sp.get("right_closed", True) else s < float(hi)
            mask &= ok
        return mask.fillna(False)
    desc = rule.get("description") or rule.get("rule_description") or rule.get("rule")
    if desc:
        return apply_rule_description(df, str(desc))
    return pd.Series(False, index=df.index)


def _nested_get(blob: dict[str, Any], dotted: str) -> Any:
    cur: Any = blob
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _rule_score(r: dict[str, Any]) -> float:
    for path in (
        ("confirmation", "net_pnl_delta"),
        ("net_pnl_delta",),
        ("confirmation", "net_proxy_pnl"),
        ("discovery_score",),
        ("score",),
    ):
        cur: Any = r
        ok = True
        for p in path:
            if not isinstance(cur, dict) or p not in cur:
                ok = False
                break
            cur = cur[p]
        if ok and cur is not None:
            try:
                return float(cur)
            except (TypeError, ValueError):
                pass
    return -1e18


def pick_best_rule(blob: dict[str, Any], keys: list[str]) -> Optional[dict[str, Any]]:
    for k in keys:
        v = _nested_get(blob, k) if "." in k else blob.get(k)
        if isinstance(v, dict) and (v.get("description") or v.get("specs") or v.get("rule_id")):
            # dict may be a container with confirmed_rules
            if "confirmed_rules" in v and isinstance(v["confirmed_rules"], list):
                v = v["confirmed_rules"]
            else:
                return v
        if isinstance(v, list) and v:
            confirmed = [r for r in v if isinstance(r, dict) and r.get("confirmed") is True]
            if not confirmed:
                confirmed = [r for r in v if isinstance(r, dict) and r.get("confirmed", True)]
            pool = confirmed or [r for r in v if isinstance(r, dict)]
            if not pool:
                continue
            return max(pool, key=_rule_score)
    return None


def ensure_rule_feature_aliases(df: pd.DataFrame) -> pd.DataFrame:
    """Expose feat_* columns under bare names for reject rule descriptions."""
    out = df
    for c in list(df.columns):
        if c.startswith("feat_"):
            bare = c[len("feat_") :]
            if bare not in out.columns:
                out[bare] = out[c]
            else:
                # fill gaps from feat_ when bare is mostly null
                bare_s = pd.to_numeric(out[bare], errors="coerce")
                feat_s = pd.to_numeric(out[c], errors="coerce")
                out[bare] = bare_s.fillna(feat_s)
    # panel momentum / imbalance convenience
    if "imbalance" not in out.columns and "entry_order_book_imbalance" in out.columns:
        out["imbalance"] = pd.to_numeric(out["entry_order_book_imbalance"], errors="coerce")
    return out


def load_joined(latest_n: int = TARGET_DAYS) -> pd.DataFrame:
    if not PANEL_PQ.is_file():
        raise SystemExit(f"missing {PANEL_PQ}")
    if not FEAT_PQ.is_file():
        raise SystemExit(f"missing {FEAT_PQ}")
    panel = pd.read_parquet(PANEL_PQ)
    feat = pd.read_parquet(FEAT_PQ)
    feat_cols = [
        c
        for c in feat.columns
        if c
        not in {
            "trading_date",
            "session",
            "session_id",
            "symbol",
            "entry_time",
            "label_primary",
            "label_winner_a",
            "label_winner_b",
            "label_stop",
            "label_no_progress",
            "pnl_pct",
        }
    ]
    # keep trade_id + feature numerics
    keep = ["trade_id"] + [c for c in feat_cols if c != "trade_id"]
    f2 = feat[keep].copy()
    # rename feature spread if colliding - panel already has spread_bps; prefer feature attach
    rename = {}
    for c in f2.columns:
        if c == "trade_id":
            continue
        if c in panel.columns and c in ("spread_bps",):
            rename[c] = f"feat_{c}"
        elif c in panel.columns and c not in ("trade_id",):
            rename[c] = f"feat_{c}"
    f2 = f2.rename(columns=rename)
    df = panel.merge(f2, on="trade_id", how="left")
    # unify feature names used by score
    if "feat_spread_bps" in df.columns:
        df["spread_bps_feat"] = pd.to_numeric(df["feat_spread_bps"], errors="coerce")
    else:
        df["spread_bps_feat"] = pd.to_numeric(df.get("spread_bps"), errors="coerce")
    for src, dst in (("ret_60", "ret_60"), ("slope_60", "slope_60"), ("feat_ret_60", "ret_60"), ("feat_slope_60", "slope_60")):
        if src in df.columns and dst not in df.columns:
            df[dst] = pd.to_numeric(df[src], errors="coerce")
        elif src in df.columns:
            df[dst] = pd.to_numeric(df[src], errors="coerce").fillna(pd.to_numeric(df.get(dst), errors="coerce"))
    days = sorted(df["trading_date"].astype(str).unique())
    if len(days) > latest_n:
        use = set(days[-latest_n:])
        df = df[df["trading_date"].astype(str).isin(use)].copy()
    df["trading_date"] = df["trading_date"].astype(str)
    df["entry_dt"] = df["entry_time"].map(_parse_ts)
    df["exit_dt"] = df["exit_time"].map(_parse_ts)
    # 30m horizon proxy when exit missing
    df["exit_dt_eff"] = [
        ex if ex is not None else (en + HORIZON_PROXY if en is not None else None)
        for en, ex in zip(df["entry_dt"], df["exit_dt"])
    ]
    df["pnl_pct"] = pd.to_numeric(df["pnl_pct"], errors="coerce")
    yen = []
    for _, r in df.iterrows():
        ep, xp = _num(r.get("entry_price")), _num(r.get("exit_price"))
        if ep is not None and xp is not None and ep > 0:
            yen.append(compute_pnl_yen_100(ep, xp) - COST)
        else:
            # proxy from pct * entry * shares / 100
            pct = _num(r.get("pnl_pct"))
            if ep is not None and pct is not None:
                yen.append(ep * (pct / 100.0) * SHARES - COST)
            else:
                yen.append(np.nan)
    df["pnl_yen_100"] = yen
    df["sel_score"] = compute_sel_score(df)
    df = df.sort_values(["trading_date", "entry_dt", "symbol", "trade_id"]).reset_index(drop=True)
    return df


def compute_sel_score(df: pd.DataFrame) -> pd.Series:
    ret = pd.to_numeric(df.get("ret_60"), errors="coerce").fillna(0.0)
    slope = pd.to_numeric(df.get("slope_60"), errors="coerce").fillna(0.0)
    spread = pd.to_numeric(df.get("spread_bps_feat"), errors="coerce")
    if spread.isna().all() and "spread_bps" in df.columns:
        spread = pd.to_numeric(df["spread_bps"], errors="coerce")
    spread = spread.fillna(0.0).abs() / 100.0
    # optional boosts if present
    imb = pd.to_numeric(df.get("imbalance"), errors="coerce")
    if imb is None or (isinstance(imb, pd.Series) and imb.isna().all()):
        imb = pd.to_numeric(df.get("feat_imbalance"), errors="coerce")
    if imb is not None and not imb.isna().all():
        score = ret + slope - spread + imb.fillna(0.0) * 0.1
    else:
        score = ret + slope - spread
    return score


def metrics(df: pd.DataFrame, *, label: str) -> dict[str, Any]:
    if df is None or len(df) == 0:
        return {
            "label": label,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "total_pnl_pct": 0.0,
            "total_pnl_yen_100": 0.0,
            "pf": None,
            "winrate": None,
            "avg_pnl_pct": None,
            "avg_pnl_yen_100": None,
            "max_dd_pnl_pct": None,
            "max_dd_yen_100": None,
            "stop_rate": None,
            "np_rate": None,
            "symbol_hhi": None,
            "day_hhi": None,
            "n_days": 0,
            "n_symbols": 0,
        }
    sub = df.copy()
    pnl = pd.to_numeric(sub["pnl_pct"], errors="coerce").fillna(0.0)
    yen = pd.to_numeric(sub["pnl_yen_100"], errors="coerce")
    wins = int((pnl > 0).sum())
    losses = int((pnl < 0).sum())
    gp = float(pnl[pnl > 0].sum())
    gl = float((-pnl[pnl < 0]).sum())
    if gl > 0:
        pf: Any = gp / gl
    elif gp > 0:
        pf = "inf"
    else:
        pf = None
    # max DD on time-ordered cumulative
    order = sub.sort_values(["entry_dt", "trade_id"])
    cum = pd.to_numeric(order["pnl_pct"], errors="coerce").fillna(0.0).cumsum()
    dd = float((cum - cum.cummax()).min()) if len(cum) else 0.0
    cum_y = pd.to_numeric(order["pnl_yen_100"], errors="coerce").fillna(0.0).cumsum()
    dd_y = float((cum_y - cum_y.cummax()).min()) if len(cum_y) else 0.0
    stop = sub["stop_hit"].fillna(False).astype(bool) if "stop_hit" in sub.columns else pd.Series(False, index=sub.index)
    if "label_stop" in sub.columns:
        stop = stop | sub["label_stop"].fillna(False).astype(bool)
    np_x = (
        sub["no_progress_exit"].fillna(False).astype(bool)
        if "no_progress_exit" in sub.columns
        else pd.Series(False, index=sub.index)
    )
    if "label_no_progress" in sub.columns:
        np_x = np_x | sub["label_no_progress"].fillna(False).astype(bool)
    sym_counts = sub["symbol"].astype(str).value_counts()
    day_counts = sub["trading_date"].astype(str).value_counts()
    n = float(len(sub))
    sym_hhi = float(((sym_counts / n) ** 2).sum()) if n else None
    day_hhi = float(((day_counts / n) ** 2).sum()) if n else None
    yen_ok = yen.dropna()
    return {
        "label": label,
        "trades": int(len(sub)),
        "wins": wins,
        "losses": losses,
        "total_pnl_pct": float(pnl.sum()),
        "total_pnl_yen_100": float(yen_ok.sum()) if len(yen_ok) else None,
        "pf": pf if pf == "inf" else (float(pf) if pf is not None else None),
        "winrate": float(wins / n) if n else None,
        "avg_pnl_pct": float(pnl.mean()) if n else None,
        "avg_pnl_yen_100": float(yen_ok.mean()) if len(yen_ok) else None,
        "max_dd_pnl_pct": dd,
        "max_dd_yen_100": dd_y,
        "stop_rate": float(stop.mean()) if n else None,
        "np_rate": float(np_x.mean()) if n else None,
        "symbol_hhi": sym_hhi,
        "day_hhi": day_hhi,
        "n_days": int(sub["trading_date"].nunique()),
        "n_symbols": int(sub["symbol"].nunique()),
    }


def am_pm_split(df: pd.DataFrame, label: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for sess in ("am", "pm"):
        sub = df[df["session"].astype(str).str.lower() == sess]
        out[sess] = metrics(sub, label=f"{label}_{sess}")
    # stability: sign agreement of total_pnl and PF>1
    am_pnl = out["am"].get("total_pnl_pct") or 0.0
    pm_pnl = out["pm"].get("total_pnl_pct") or 0.0
    am_pf = out["am"].get("pf")
    pm_pf = out["pm"].get("pf")

    def _pf_ok(x: Any) -> Optional[bool]:
        if x is None:
            return None
        if x == "inf":
            return True
        try:
            return float(x) > 1.0
        except (TypeError, ValueError):
            return None

    out["stability"] = {
        "pnl_sign_agree": (am_pnl >= 0) == (pm_pnl >= 0),
        "pnl_delta_am_minus_pm": float(am_pnl - pm_pnl),
        "pf_both_gt_1": bool(_pf_ok(am_pf) and _pf_ok(pm_pf)),
        "winrate_am": out["am"].get("winrate"),
        "winrate_pm": out["pm"].get("winrate"),
        "stop_rate_am": out["am"].get("stop_rate"),
        "stop_rate_pm": out["pm"].get("stop_rate"),
    }
    return out


def sim_cap5(df: pd.DataFrame, *, reject_mask: Optional[pd.Series] = None) -> pd.DataFrame:
    """CAP=5, no duplicate symbol, FIFO by actual/proxy exit; chronological accept."""
    if df.empty:
        return df.copy()
    rej = reject_mask.reindex(df.index).fillna(False) if reject_mask is not None else pd.Series(False, index=df.index)
    accepted_idx: list[Any] = []
    # open: list of (exit_dt, symbol)
    open_pos: list[tuple[datetime, str]] = []
    for idx, row in df.sort_values(["entry_dt", "trade_id"]).iterrows():
        if rej.loc[idx]:
            continue
        en = row["entry_dt"]
        if en is None:
            continue
        open_pos = [(ex, sy) for ex, sy in open_pos if ex is not None and ex > en]
        if len(open_pos) >= CAP:
            continue
        sym = str(row["symbol"])
        if any(sy == sym for _, sy in open_pos):
            continue
        ex = row["exit_dt_eff"]
        if ex is None:
            ex = en + HORIZON_PROXY
        open_pos.append((ex, sym))
        accepted_idx.append(idx)
    return df.loc[accepted_idx].copy()


def selection_only(df: pd.DataFrame, counts: dict[str, int]) -> pd.DataFrame:
    """Same trade count/day as baseline, pick highest sel_score."""
    parts: list[pd.DataFrame] = []
    for day, g in df.groupby("trading_date", sort=True):
        k = int(counts.get(str(day), 0))
        if k <= 0:
            continue
        gg = g.sort_values(["sel_score", "entry_dt"], ascending=[False, True])
        parts.append(gg.head(k))
    if not parts:
        return df.iloc[0:0].copy()
    return pd.concat(parts, ignore_index=False).sort_values(["trading_date", "entry_dt"]).reset_index(drop=True)


def reject_and_fill(
    df: pd.DataFrame,
    *,
    reject_mask: pd.Series,
    day_slot_counts: dict[str, int],
) -> pd.DataFrame:
    """Cap5-style accept with rejects; fill empty day slots with next ranked same-day candidates."""
    # First pass: Cap5 with rejects
    base_acc = sim_cap5(df, reject_mask=reject_mask)
    taken = set(base_acc["trade_id"].astype(str))
    parts: list[pd.DataFrame] = [base_acc]
    for day, k_target in day_slot_counts.items():
        have = base_acc[base_acc["trading_date"].astype(str) == str(day)]
        need = int(k_target) - int(len(have))
        if need <= 0:
            continue
        cand = df[
            (df["trading_date"].astype(str) == str(day))
            & (~df["trade_id"].astype(str).isin(taken))
            & (~reject_mask.reindex(df.index).fillna(False))
        ].sort_values(["sel_score", "entry_dt"], ascending=[False, True])
        # fill preferring candidates near gap times: score rank is enough for research stub
        fill = cand.head(need)
        # still respect no-dup symbol within filled day set when possible
        kept_rows = []
        open_syms = set(have["symbol"].astype(str))
        for _, r in fill.iterrows():
            if len(kept_rows) >= need:
                break
            sy = str(r["symbol"])
            if sy in open_syms:
                continue
            kept_rows.append(r)
            open_syms.add(sy)
            taken.add(str(r["trade_id"]))
        if kept_rows:
            parts.append(pd.DataFrame(kept_rows))
    out = pd.concat(parts, ignore_index=True) if parts else df.iloc[0:0].copy()
    # Final Cap5 pass to enforce concurrency after fills
    return sim_cap5(out.sort_values(["entry_dt", "trade_id"]).reset_index(drop=True))


def watch50_ranking_sim_0717() -> Optional[dict[str, Any]]:
    if not SNAP_0717.is_file():
        return {"available": False, "path": str(SNAP_0717)}
    snap = pd.read_parquet(SNAP_0717)
    ret = pd.to_numeric(snap.get("ret_60s"), errors="coerce").fillna(0.0)
    slope = pd.to_numeric(snap.get("slope_60s"), errors="coerce").fillna(0.0)
    spread = pd.to_numeric(snap.get("spread_bps"), errors="coerce").fillna(0.0).abs() / 100.0
    snap = snap.copy()
    snap["sel_score"] = ret + slope - spread
    snap["proxy_pnl"] = pd.to_numeric(snap.get("future_30m_return"), errors="coerce")
    # sample every ~60s grid if t0_epoch present: take top-5 symbols per timestamp (CAP proxy)
    if "t0_epoch" not in snap.columns:
        return {"available": True, "note": "no t0_epoch", "n_rows": int(len(snap))}
    # thin to reduce cost: unique epochs
    epochs = sorted(snap["t0_epoch"].dropna().unique())
    # stride ~2 min
    if len(epochs) > 200:
        stride = max(1, len(epochs) // 200)
        epochs = epochs[::stride]
    picks: list[dict[str, Any]] = []
    for ep in epochs:
        g = snap[snap["t0_epoch"] == ep].nlargest(CAP, "sel_score")
        for _, r in g.iterrows():
            picks.append(
                {
                    "t0_epoch": float(ep),
                    "symbol": r.get("symbol"),
                    "session": r.get("session"),
                    "sel_score": float(r["sel_score"]),
                    "proxy_pnl": _num(r.get("proxy_pnl")),
                    "future_30m_mfe": _num(r.get("future_30m_mfe")),
                    "future_30m_mae": _num(r.get("future_30m_mae")),
                }
            )
    pdf = pd.DataFrame(picks)
    if pdf.empty:
        return {"available": True, "n_picks": 0}
    pnl = pd.to_numeric(pdf["proxy_pnl"], errors="coerce").dropna()
    gp = float(pnl[pnl > 0].sum()) if len(pnl) else 0.0
    gl = float((-pnl[pnl < 0]).sum()) if len(pnl) else 0.0
    pf: Any
    if gl > 0:
        pf = gp / gl
    elif gp > 0:
        pf = "inf"
    else:
        pf = None
    return {
        "available": True,
        "day": "20260717",
        "n_grid_epochs": int(len(epochs)),
        "n_picks": int(len(pdf)),
        "cap": CAP,
        "total_proxy_pnl_pct": float(pnl.sum()) if len(pnl) else None,
        "avg_proxy_pnl_pct": float(pnl.mean()) if len(pnl) else None,
        "pf_proxy": pf,
        "winrate_proxy": float((pnl > 0).mean()) if len(pnl) else None,
        "note": "Ranking sim on watch50 snapshots; proxy=future_30m_return; CAP top-5/score per grid epoch; research-only.",
    }


def impact_decomposition(
    df: pd.DataFrame,
    *,
    stop_rule: Optional[dict[str, Any]],
    np_rule: Optional[dict[str, Any]],
    winner_rule: Optional[dict[str, Any]],
    baseline_cap5: pd.DataFrame,
) -> dict[str, Any]:
    """Plumbing N/A; each component alone vs combined when rules exist."""
    out: dict[str, Any] = {
        "A_plumbing_only": {
            "status": "N/A",
            "note": "W43F plumbing is runtime/DQ; not estimable from entry_panel counterfactual alone.",
        }
    }
    base_m = metrics(baseline_cap5, label="cap5_baseline")
    out["baseline_cap5"] = base_m

    def _alone(name: str, rule: Optional[dict[str, Any]], kind: str) -> dict[str, Any]:
        if not rule:
            return {"status": "skipped", "reason": f"no_{kind}_rule"}
        rej = rule_reject_mask(df, rule)
        # winner trigger = keep mask (inverse reject)
        if kind == "winner_trigger":
            # keep only rows matching trigger; Cap5 on survivors
            keep = rej  # description matched => candidate
            if not keep.any():
                return {
                    "status": "applied_empty",
                    "rule_id": rule.get("rule_id"),
                    "description": rule.get("description"),
                    "note": "trigger matched 0 panel rows (features may differ from watch50 rule space)",
                }
            sim = sim_cap5(df[keep])
            return {
                "status": "ok",
                "rule_id": rule.get("rule_id"),
                "description": rule.get("description"),
                "matched": int(keep.sum()),
                "metrics": metrics(sim, label=name),
                "delta_total_pnl_pct_vs_cap5": float(
                    metrics(sim, label=name)["total_pnl_pct"] - (base_m["total_pnl_pct"] or 0)
                ),
            }
        sim = sim_cap5(df, reject_mask=rej)
        m = metrics(sim, label=name)
        return {
            "status": "ok",
            "rule_id": rule.get("rule_id"),
            "description": rule.get("description"),
            "rejected": int(rej.sum()),
            "metrics": m,
            "delta_total_pnl_pct_vs_cap5": float(m["total_pnl_pct"] - (base_m["total_pnl_pct"] or 0)),
        }

    out["B_winner_trigger_only"] = _alone("winner_only", winner_rule, "winner_trigger")
    out["C_stop_reject_only"] = _alone("stop_only", stop_rule, "stop")
    out["D_np_reject_only"] = _alone("np_only", np_rule, "np")
    out["E_pullback_only"] = {"status": "skipped", "reason": "no_pullback_rules_in_w47_inputs"}
    out["F_reentry_only"] = {"status": "skipped", "reason": "no_reentry_rules_in_w47_inputs"}

    # combined reject (stop OR np)
    if stop_rule or np_rule:
        rej = pd.Series(False, index=df.index)
        if stop_rule:
            rej = rej | rule_reject_mask(df, stop_rule)
        if np_rule:
            rej = rej | rule_reject_mask(df, np_rule)
        sim = sim_cap5(df, reject_mask=rej)
        day_counts = baseline_cap5.groupby('trading_date').size().astype(int).to_dict()
        sim_fill = reject_and_fill(
            df, reject_mask=rej, day_slot_counts={str(k): int(v) for k, v in day_counts.items()}
        )
        m = metrics(sim, label='combined_reject_cap5')
        m_fill = metrics(sim_fill, label='combined_reject_fill')
        out['G_combined'] = {
            'status': 'ok',
            'components': {
                'stop': bool(stop_rule),
                'np': bool(np_rule),
                'winner_trigger': bool(winner_rule),
            },
            'cap5_reject_only': m,
            'cap5_reject_fill': m_fill,
            'delta_total_pnl_pct_vs_cap5_reject_only': float(m['total_pnl_pct'] - (base_m['total_pnl_pct'] or 0)),
            'delta_total_pnl_pct_vs_cap5_reject_fill': float(
                m_fill['total_pnl_pct'] - (base_m['total_pnl_pct'] or 0)
            ),
        }
    elif winner_rule:
        out['G_combined'] = {
            'status': 'partial',
            'note': 'winner_rule present but stop/np rejects absent; see B_winner_trigger_only',
            'components': {'stop': False, 'np': False, 'winner_trigger': True},
        }
    else:
        out['G_combined'] = {'status': 'skipped', 'reason': 'no_component_rules'}

    # contribution ranking stubs
    contrib = []
    for key, label in (
        ("B_winner_trigger_only", "winner_trigger"),
        ("C_stop_reject_only", "stop_reject"),
        ("D_np_reject_only", "np_reject"),
        ("G_combined", "combined"),
    ):
        block = out.get(key) or {}
        if block.get("status") != "ok":
            continue
        if key == "G_combined":
            delta = block.get("delta_total_pnl_pct_vs_cap5_reject_fill")
        else:
            delta = block.get("delta_total_pnl_pct_vs_cap5")
        contrib.append({"component": label, "delta_total_pnl_pct": delta})
    contrib.sort(key=lambda x: (x["delta_total_pnl_pct"] is not None, x["delta_total_pnl_pct"] or -1e18), reverse=True)
    out["contribution_rank"] = contrib
    return out


def main() -> int:
    TMP.mkdir(parents=True, exist_ok=True)
    print("W47 portfolio impact sim - loading panel+features...", flush=True)
    df = ensure_rule_feature_aliases(load_joined(TARGET_DAYS))
    days = sorted(df["trading_date"].astype(str).unique())
    print(f"  rows={len(df)} days={len(days)} ({days[0]}..{days[-1]})", flush=True)

    winner_blob: dict[str, Any] = {}
    if WINNER_JSON.is_file():
        winner_blob = json.loads(WINNER_JSON.read_text(encoding="utf-8"))
        print(f"  winner_trigger_results: confirmed_rules_count={winner_blob.get('confirmed_rules_count')}", flush=True)
    else:
        print("  winner_trigger_results: missing", flush=True)

    reject_blob: dict[str, Any] = {}
    reject_present = REJECT_JSON.is_file()
    if reject_present:
        reject_blob = json.loads(REJECT_JSON.read_text(encoding="utf-8"))
        print(
            "  stop_np_reject_results: loaded "
            f"stop_confirmed={reject_blob.get('confirmed_stop_count')} "
            f"np_confirmed={reject_blob.get('confirmed_np_count')}",
            flush=True,
        )
    else:
        print("  stop_np_reject_results: missing - skip reject arms", flush=True)

    stop_rule = (
        pick_best_rule(
            reject_blob,
            [
                "stop_search.confirmed_rules",
                "stop_search.top_candidates",
                "best_stop_reject",
                "best_stop_rule",
                "confirmed_stop_rules",
                "stop_confirmed_rules",
            ],
        )
        if reject_present
        else None
    )
    np_rule = (
        pick_best_rule(
            reject_blob,
            [
                "np_search.confirmed_rules",
                "np_search.top_candidates",
                "best_np_reject",
                "best_no_progress_reject",
                "best_np_rule",
                "confirmed_np_rules",
                "np_confirmed_rules",
            ],
        )
        if reject_present
        else None
    )
    winner_rule = pick_best_rule(
        winner_blob,
        ["confirmed_rules", "stage2_promoted_rules", "best_rule"],
    )
    # winner rules use watch50 features - likely 0 matches on entry panel; keep for stub honesty
    if winner_rule and not any(
        f in df.columns for f in (winner_rule.get("features") or [])
    ):
        # still keep description attempt
        pass

    # A baseline
    m_a = metrics(df, label="A_baseline_pbv2_actual")
    print(f"A baseline trades={m_a['trades']} pnl_pct={m_a['total_pnl_pct']:.3f} PF={m_a['pf']}", flush=True)

    day_counts = df.groupby("trading_date").size().astype(int).to_dict()
    day_counts = {str(k): int(v) for k, v in day_counts.items()}

    # B selection-only
    sel = selection_only(df, day_counts)
    m_b = metrics(sel, label="B_selection_only")
    # identity note
    same_ids = set(sel["trade_id"].astype(str)) == set(df["trade_id"].astype(str))
    print(f"B selection-only trades={m_b['trades']} identity_set={same_ids}", flush=True)

    # C Cap5
    cap5 = sim_cap5(df)
    m_c = metrics(cap5, label="C_cap5_portfolio")
    print(f"C Cap5 trades={m_c['trades']} pnl_pct={m_c['total_pnl_pct']:.3f} PF={m_c['pf']}", flush=True)

    # D Reject+fill
    d_block: dict[str, Any]
    if stop_rule or np_rule:
        rej = pd.Series(False, index=df.index)
        if stop_rule:
            rej = rej | rule_reject_mask(df, stop_rule)
        if np_rule:
            rej = rej | rule_reject_mask(df, np_rule)
        cap5_counts = cap5.groupby("trading_date").size().astype(int).to_dict()
        filled = reject_and_fill(
            df,
            reject_mask=rej,
            day_slot_counts={str(k): int(v) for k, v in cap5_counts.items()},
        )
        m_d = metrics(filled, label="D_reject_fill")
        d_block = {
            "status": "ok",
            "stop_rule_id": (stop_rule or {}).get("rule_id"),
            "np_rule_id": (np_rule or {}).get("rule_id"),
            "rejected_n": int(rej.sum()),
            "metrics": m_d,
            "am_pm": am_pm_split(filled, "D_reject_fill"),
        }
        print(f"D reject+fill trades={m_d['trades']} rejected={int(rej.sum())}", flush=True)
        best_combined_df = filled
    else:
        d_block = {
            "status": "skipped",
            "reason": "stop_np_reject_results.json missing or no usable rules",
        }
        best_combined_df = cap5
        print("D reject+fill skipped", flush=True)

    # Also: selection-only with K = Cap5 day counts (non-identity upper bound)
    cap5_day_counts = {str(k): int(v) for k, v in cap5.groupby("trading_date").size().astype(int).to_dict().items()}
    sel_cap = selection_only(df, cap5_day_counts)
    m_b_cap = metrics(sel_cap, label="B2_selection_only_match_cap5_count")

    decomp = impact_decomposition(
        df,
        stop_rule=stop_rule,
        np_rule=np_rule,
        winner_rule=winner_rule,
        baseline_cap5=cap5,
    )

    ranking_0717 = watch50_ranking_sim_0717()
    print(f"watch50 20260717 ranking: {ranking_0717.get('n_picks') if ranking_0717 else None}", flush=True)

    am_pm_base = am_pm_split(df, "A_baseline")
    am_pm_best = am_pm_split(best_combined_df, "best_combined")

    # pick best combined arm by total_pnl_pct among available
    candidates = [
        ("A_baseline", df, m_a),
        ("B_selection_only", sel, m_b),
        ("B2_selection_cap5_count", sel_cap, m_b_cap),
        ("C_cap5", cap5, m_c),
    ]
    if d_block.get("status") == "ok":
        candidates.append(("D_reject_fill", best_combined_df, d_block["metrics"]))
    if (decomp.get("G_combined") or {}).get("status") == "ok":
        # metrics already inside
        pass
    best_name, best_df, best_m = max(
        candidates,
        key=lambda t: (t[2].get("total_pnl_pct") is not None, t[2].get("total_pnl_pct") or -1e18),
    )

    result = {
        "phase": "W47_portfolio_impact_sim",
        "runtime_trading_conditions_modified": False,
        "paper_overwritten": False,
        "cost": COST,
        "shares": SHARES,
        "cap": CAP,
        "horizon_proxy_minutes": 30,
        "score_formula": "ret_60 + slope_60 - abs(spread_bps)/100 (+ 0.1*imbalance if present)",
        "days_used": days,
        "n_days": len(days),
        "n_rows_panel": int(len(df)),
        "inputs": {
            "entry_panel": str(PANEL_PQ),
            "entry_features": str(FEAT_PQ),
            "winner_trigger_results": str(WINNER_JSON) if WINNER_JSON.is_file() else None,
            "stop_np_reject_results": str(REJECT_JSON) if reject_present else None,
            "watch50_20260717": str(SNAP_0717) if SNAP_0717.is_file() else None,
        },
        "simulations": {
            "A_baseline_pbv2_actual": {
                "status": "ok",
                "metrics": m_a,
                "am_pm": am_pm_base,
            },
            "B_selection_only": {
                "status": "ok",
                "metrics": m_b,
                "same_trade_set_as_baseline": same_ids,
                "note": (
                    "Per-day top-K by score with K=baseline day count. "
                    "When candidate pool equals official entries, set equals baseline."
                ),
                "am_pm": am_pm_split(sel, "B_selection_only"),
            },
            "B2_selection_only_match_cap5_count": {
                "status": "ok",
                "metrics": m_b_cap,
                "note": "Top-K by score with K=Cap5-accepted count/day (oracle selection upper bound vs FIFO Cap5).",
                "am_pm": am_pm_split(sel_cap, "B2"),
            },
            "C_cap5_portfolio": {
                "status": "ok",
                "metrics": m_c,
                "rules": {
                    "shares": SHARES,
                    "cap": CAP,
                    "no_duplicate_symbol": True,
                    "exit": "actual_exit_time_or_30m_proxy",
                    "cost": COST,
                    "pnl": "gross_100_share_pnl_yen.compute_pnl_yen_100",
                },
                "am_pm": am_pm_split(cap5, "C_cap5"),
            },
            "D_reject_fill": d_block,
        },
        "impact_decomposition": decomp,
        "watch50_ranking_sim_20260717": ranking_0717,
        "am_pm_stability": {
            "baseline": am_pm_base.get("stability"),
            "best_combined_arm": best_name,
            "best_combined": am_pm_best.get("stability"),
            "best_combined_metrics": best_m,
        },
        "rules_applied": {
            "stop_rule": (
                {"rule_id": stop_rule.get("rule_id"), "description": stop_rule.get("description")}
                if stop_rule
                else None
            ),
            "np_rule": (
                {"rule_id": np_rule.get("rule_id"), "description": np_rule.get("description")}
                if np_rule
                else None
            ),
            "winner_rule": (
                {
                    "rule_id": winner_rule.get("rule_id"),
                    "description": winner_rule.get("description"),
                    "note": "watch50 feature space; panel match may be empty",
                }
                if winner_rule
                else None
            ),
        },
    }
    _wj(OUT_JSON, result)
    print(f"WROTE {OUT_JSON}", flush=True)
    print(
        json.dumps(
            {
                "A_trades": m_a["trades"],
                "A_pnl": m_a["total_pnl_pct"],
                "B_pnl": m_b["total_pnl_pct"],
                "C_trades": m_c["trades"],
                "C_pnl": m_c["total_pnl_pct"],
                "D_status": d_block.get("status"),
                "best_arm": best_name,
                "best_pnl": best_m.get("total_pnl_pct"),
                "out": str(OUT_JSON),
            },
            ensure_ascii=False,
            indent=2,
            default=_json_default,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
