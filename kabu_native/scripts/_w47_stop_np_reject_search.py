#!/usr/bin/env python3
"""W47: STOP / NO_PROGRESS reject search on official ENTRY panel (research-only).

Reads entry_panel + entry_features under _w47_tmp/, searches 2/3-feature quantile
extreme AND rules and DecisionTree depth<=3 reject rules. Discovery (older 10d)
vs Confirmation (newer 10d). Does NOT modify Runtime / YAML / trading conditions.
"""

from __future__ import annotations

import json
import math
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed
from itertools import combinations
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from sklearn.tree import DecisionTreeClassifier, _tree

warnings.filterwarnings("ignore", category=UserWarning)

NATIVE = Path(__file__).resolve().parents[1]
TMP = NATIVE / "results" / "research" / "pre_entry_market_state" / "_w47_tmp"
PANEL_PQ = TMP / "entry_panel.parquet"
FEAT_PQ = TMP / "entry_features.parquet"
OUT_JSON = TMP / "stop_np_reject_results.json"

MAX_WORKERS = 4
TARGET_DAYS = 20
DISC_DAYS = 10
CONF_DAYS = 10
Q_BINS = 5
MIN_RULE_N = 20
MIN_BLOCKED = 5
TOP_CANDIDATES = 80
WINNER_SACRIFICE_MAX = 0.10
DAY_NON_WORSE_MIN = 0.70

FEATURE_COLS = [
    "ret_30",
    "ret_60",
    "ret_120",
    "ret_300",
    "slope_60",
    "slope_120",
    "spread_bps",
    "imbalance",
    "seconds_since",
    "push_points",
    "momentum",
    "momentum_continuation_score",
    "score_v2",
    "entry_order_book_imbalance",
    "continuation_quality_score",
]


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _f(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def load_joined() -> pd.DataFrame:
    panel = pd.read_parquet(PANEL_PQ)
    feat = pd.read_parquet(FEAT_PQ)
    # trade keys: trade_id (+ trading_date/session/symbol as sanity)
    key = "trade_id"
    feat_keep = [c for c in feat.columns if c == key or c in FEATURE_COLS or c == "feature_ok"]
    feat_sub = feat[feat_keep].copy()
    # avoid duplicate label/pnl columns from features
    overlap = [c for c in feat_sub.columns if c != key and c in panel.columns]
    feat_sub = feat_sub.drop(columns=overlap, errors="ignore")
    df = panel.merge(feat_sub, on=key, how="inner", validate="one_to_one")
    if "feature_ok" in df.columns:
        df = df[df["feature_ok"].fillna(False)].copy()
    df["trading_date"] = df["trading_date"].astype(str).str.replace("-", "", regex=False)
    df["pnl_pct"] = pd.to_numeric(df["pnl_pct"], errors="coerce").fillna(0.0)
    df["hold_sec"] = pd.to_numeric(df.get("hold_sec"), errors="coerce")
    df["is_stop"] = df["label_stop"].astype(bool)
    df["is_np"] = df["label_no_progress"].astype(bool)
    df["is_winner"] = df["label_winner_b"].astype(bool) | df["label_winner_a"].astype(bool)
    return df


def select_days(df: pd.DataFrame) -> tuple[pd.DataFrame, list[str], list[str], list[str]]:
    days = sorted(df["trading_date"].unique())
    if len(days) > TARGET_DAYS:
        days = days[-TARGET_DAYS:]
    df = df[df["trading_date"].isin(days)].copy()
    # older 10 Discovery / newer 10 Confirmation
    if len(days) >= DISC_DAYS + CONF_DAYS:
        disc_days = days[:DISC_DAYS]
        conf_days = days[-CONF_DAYS:]
        # if odd overlap (shouldn't with 20), keep disjoint
        if set(disc_days) & set(conf_days):
            mid = len(days) // 2
            disc_days = days[:mid]
            conf_days = days[mid:]
    else:
        mid = max(1, len(days) // 2)
        disc_days = days[:mid]
        conf_days = days[mid:] if mid < len(days) else days[-1:]
        if set(disc_days) & set(conf_days) and len(days) >= 2:
            disc_days = days[:mid]
            conf_days = days[mid:]
    return df, list(days), list(disc_days), list(conf_days)


def available_features(df: pd.DataFrame) -> list[str]:
    out = []
    for f in FEATURE_COLS:
        if f not in df.columns:
            continue
        s = pd.to_numeric(df[f], errors="coerce")
        if s.notna().sum() < 50:
            continue
        if float(s.std(skipna=True) or 0) < 1e-12:
            continue
        out.append(f)
    return out


def _bin_extreme_specs(df: pd.DataFrame, feat: str) -> list[dict[str, Any]]:
    s = pd.to_numeric(df[feat], errors="coerce")
    try:
        cats = pd.qcut(s, q=Q_BINS, duplicates="drop")
    except ValueError:
        return []
    levels = list(cats.cat.categories) if hasattr(cats, "cat") else []
    if len(levels) < 2:
        return []
    out: list[dict[str, Any]] = []
    for side, iv in (("low", levels[0]), ("high", levels[-1])):
        closed = getattr(iv, "closed", "right")
        left_closed = closed in ("left", "both")
        right_closed = closed in ("right", "both")
        left_b = "[" if left_closed else "("
        right_b = "]" if right_closed else ")"
        out.append(
            {
                "feature": feat,
                "side": side,
                "name": f"{feat}:q_{side}",
                "lo": float(iv.left),
                "hi": float(iv.right),
                "left_closed": left_closed,
                "right_closed": right_closed,
                "description": f"{feat} in {left_b}{float(iv.left):.6g}, {float(iv.right):.6g}{right_b}",
            }
        )
    return out


def _apply_interval_spec(df: pd.DataFrame, spec: dict[str, Any]) -> pd.Series:
    s = pd.to_numeric(df[spec["feature"]], errors="coerce")
    ok = s.notna()
    if spec["left_closed"]:
        ok &= s >= spec["lo"]
    else:
        ok &= s > spec["lo"]
    if spec["right_closed"]:
        ok &= s <= spec["hi"]
    else:
        ok &= s < spec["hi"]
    return ok.fillna(False)


def apply_rule_description(df: pd.DataFrame, description: str) -> pd.Series:
    if description == "TRUE":
        return pd.Series(True, index=df.index)
    parts = [p.strip() for p in description.split(" AND ") if p.strip()]
    mask = pd.Series(True, index=df.index)
    for p in parts:
        if " in " in p and ("[" in p or "(" in p):
            feat, interval = p.split(" in ", 1)
            feat = feat.strip()
            text = interval.strip()
            left_closed = text[0] == "["
            right_closed = text[-1] == "]"
            body = text[1:-1]
            a, b = body.split(",")
            lo, hi = float(a), float(b)
            s = pd.to_numeric(df[feat], errors="coerce")
            ok = s.notna()
            ok &= s >= lo if left_closed else s > lo
            ok &= s <= hi if right_closed else s < hi
            mask &= ok
        elif "<=" in p:
            feat, thr = p.split("<=", 1)
            mask &= pd.to_numeric(df[feat.strip()], errors="coerce") <= float(thr)
        elif ">" in p:
            feat, thr = p.split(">", 1)
            mask &= pd.to_numeric(df[feat.strip()], errors="coerce") > float(thr)
        else:
            mask &= False
    return mask.fillna(False)


def rule_mask(df: pd.DataFrame, rule: dict[str, Any]) -> pd.Series:
    specs = rule.get("specs") or []
    if specs:
        mask = pd.Series(True, index=df.index)
        for sp in specs:
            mask &= _apply_interval_spec(df, sp)
        return mask.fillna(False)
    return apply_rule_description(df, rule["description"])


def eval_reject(
    df: pd.DataFrame,
    block: pd.Series,
    *,
    target: str,
) -> dict[str, Any]:
    """Counterfactual metrics when blocking (rejecting) trades matching `block`."""
    block = block.reindex(df.index, fill_value=False).fillna(False).astype(bool)
    n = int(len(df))
    n_block = int(block.sum())
    kept = ~block
    pnl = df["pnl_pct"]
    is_stop = df["is_stop"]
    is_np = df["is_np"]
    is_win = df["is_winner"]

    blocked = df.loc[block]
    blocked_pnl = pnl.loc[block]
    blocked_stop = int((is_stop & block).sum())
    blocked_np = int((is_np & block).sum())
    blocked_winner = int((is_win & block).sum())

    # losses avoided / gains missed
    neg = blocked_pnl < 0
    pos = blocked_pnl > 0
    pnl_saved = float((-blocked_pnl.loc[neg]).sum()) if neg.any() else 0.0
    pnl_lost = float(blocked_pnl.loc[pos].sum()) if pos.any() else 0.0
    net_pnl_delta = float((-blocked_pnl).sum()) if n_block else 0.0

    base_stop_rate = float(is_stop.mean()) if n else None
    base_np_rate = float(is_np.mean()) if n else None
    stop_rate_after = float(is_stop.loc[kept].mean()) if int(kept.sum()) else None
    np_rate_after = float(is_np.loc[kept].mean()) if int(kept.sum()) else None
    n_winners = int(is_win.sum())
    winner_sacrifice_rate = float(blocked_winner / n_winners) if n_winners else 0.0

    # day-level net pnl delta stability
    day_ok = 0
    day_n = 0
    day_deltas: list[dict[str, Any]] = []
    for day, g in df.groupby("trading_date", sort=True):
        b = block.loc[g.index]
        if not bool(b.any()):
            # no blocks that day → net delta 0 → non-worse
            day_n += 1
            day_ok += 1
            day_deltas.append({"trading_date": str(day), "net_pnl_delta": 0.0, "n_blocked": 0})
            continue
        dlt = float((-g.loc[b, "pnl_pct"]).sum())
        day_n += 1
        if dlt >= -1e-12:
            day_ok += 1
        day_deltas.append({"trading_date": str(day), "net_pnl_delta": dlt, "n_blocked": int(b.sum())})
    day_non_worse_frac = float(day_ok / day_n) if day_n else None

    if n_block and "hold_sec" in blocked.columns:
        if target == "STOP":
            hold_target = blocked.loc[blocked["is_stop"], "hold_sec"] / 60.0
        else:
            hold_target = blocked.loc[blocked["is_np"], "hold_sec"] / 60.0
        mean_hold_min_blocked_target = _f(hold_target.mean()) if len(hold_target) else None
    else:
        mean_hold_min_blocked_target = None
    # CAP occupancy proxy for NP: mean hold minutes of blocked NP trades
    blocked_np_hold = df.loc[block & is_np, "hold_sec"] / 60.0
    mean_hold_min_blocked_np = _f(blocked_np_hold.mean()) if len(blocked_np_hold) else None

    return {
        "n": n,
        "n_blocked": n_block,
        "n_kept": int(kept.sum()),
        "blocked_stop": blocked_stop,
        "blocked_np": blocked_np,
        "blocked_winner": blocked_winner,
        "pnl_saved": pnl_saved,
        "pnl_lost": pnl_lost,
        "net_pnl_delta": net_pnl_delta,
        "base_stop_rate": base_stop_rate,
        "base_np_rate": base_np_rate,
        "stop_rate_after": stop_rate_after,
        "np_rate_after": np_rate_after,
        "winner_sacrifice_rate": winner_sacrifice_rate,
        "day_non_worse_frac": day_non_worse_frac,
        "day_non_worse_n": day_ok,
        "day_n": day_n,
        "day_deltas": day_deltas,
        "mean_hold_min_blocked_target": mean_hold_min_blocked_target,
        "mean_hold_min_blocked_np": mean_hold_min_blocked_np,
        "cap_occupancy_reduction_proxy_mean_hold_min_blocked_np": mean_hold_min_blocked_np,
    }


def build_quantile_and_rules(disc: pd.DataFrame, features: list[str]) -> list[dict[str, Any]]:
    extremes: dict[str, list[dict[str, Any]]] = {f: _bin_extreme_specs(disc, f) for f in features}
    rules: list[dict[str, Any]] = []

    def _pack(specs: list[dict[str, Any]], kind: str) -> dict[str, Any]:
        desc = " AND ".join(s["description"] for s in specs)
        rid = "AND::" + "::".join(s["name"] for s in specs)
        slim = [
            {k: s[k] for k in ("feature", "side", "lo", "hi", "left_closed", "right_closed", "description")}
            for s in specs
        ]
        return {
            "rule_id": rid,
            "features": [s["feature"] for s in specs],
            "description": desc,
            "specs": slim,
            "kind": kind,
        }

    for f1, f2 in combinations(features, 2):
        for s1 in extremes.get(f1, []):
            for s2 in extremes.get(f2, []):
                rules.append(_pack([s1, s2], "two_feature_and"))

    for f1, f2, f3 in combinations(features, 3):
        for s1 in extremes.get(f1, []):
            for s2 in extremes.get(f2, []):
                for s3 in extremes.get(f3, []):
                    rules.append(_pack([s1, s2, s3], "three_feature_and"))
    return rules


def extract_tree_reject_rules(clf: DecisionTreeClassifier, feature_names: list[str]) -> list[str]:
    """Paths where majority class is positive (reject target)."""
    tree = clf.tree_
    feature_name = [
        feature_names[i] if i != _tree.TREE_UNDEFINED else "undefined"
        for i in tree.feature
    ]
    rules: list[str] = []

    def recurse(node: int, path: list[str]) -> None:
        if tree.feature[node] != _tree.TREE_UNDEFINED:
            name = feature_name[node]
            thr = tree.threshold[node]
            recurse(tree.children_left[node], path + [f"{name} <= {thr:.6g}"])
            recurse(tree.children_right[node], path + [f"{name} > {thr:.6g}"])
        else:
            vals = tree.value[node][0]
            # with class_weight='balanced', value is weighted; use n_node_samples
            n_samples = int(tree.n_node_samples[node])
            if len(vals) > 1 and vals[1] > vals[0] and n_samples >= 5:
                rules.append(" AND ".join(path) if path else "TRUE")

    recurse(0, [])
    return rules[:20]


def build_tree_rules(disc: pd.DataFrame, features: list[str], y_col: str) -> list[dict[str, Any]]:
    use = [f for f in features if f in disc.columns][:20]
    X = disc[use].apply(pd.to_numeric, errors="coerce")
    y = disc[y_col].astype(int)
    med = X.median()
    X = X.fillna(med)
    if y.nunique() < 2 or len(disc) < 40:
        return []
    dt = DecisionTreeClassifier(
        max_depth=3,
        min_samples_leaf=15,
        class_weight="balanced",
        random_state=42,
    )
    dt.fit(X, y)
    descs = extract_tree_reject_rules(dt, use)
    out = []
    for i, desc in enumerate(descs):
        out.append(
            {
                "rule_id": f"TREE::{y_col}::{i}",
                "features": use,
                "description": desc,
                "specs": [],
                "kind": "decision_tree_depth3",
                "impute_median": {f: float(med[f]) for f in use if pd.notna(med[f])},
            }
        )
    return out


def _score_discovery(ev: dict[str, Any], target: str) -> float:
    """Rank candidates on Discovery: prefer blocking bad outcomes with positive net pnl."""
    if (ev.get("n_blocked") or 0) < MIN_BLOCKED:
        return -1e18
    net = ev.get("net_pnl_delta") or 0.0
    sac = ev.get("winner_sacrifice_rate") or 1.0
    if sac > WINNER_SACRIFICE_MAX + 1e-12:
        # still keep for inspection but rank lower
        net = net - 50.0 * (sac - WINNER_SACRIFICE_MAX)
    if target == "STOP":
        blocked_good = ev.get("blocked_stop") or 0
        rate_base = ev.get("base_stop_rate") or 0
        rate_aft = ev.get("stop_rate_after")
        improve = (rate_base - rate_aft) if rate_aft is not None else 0.0
    else:
        blocked_good = ev.get("blocked_np") or 0
        rate_base = ev.get("base_np_rate") or 0
        rate_aft = ev.get("np_rate_after")
        improve = (rate_base - rate_aft) if rate_aft is not None else 0.0
    return float(net) + 0.5 * blocked_good + 20.0 * improve - 5.0 * sac * math.sqrt(max(ev["n_blocked"], 1))


def confirm_stop(ev_c: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    ok = True
    if (ev_c.get("n_blocked") or 0) < MIN_BLOCKED:
        ok = False
        reasons.append("conf_n_blocked_low")
    if not ((ev_c.get("net_pnl_delta") or 0) > 0):
        ok = False
        reasons.append("net_pnl_delta_not_positive")
    base = ev_c.get("base_stop_rate")
    aft = ev_c.get("stop_rate_after")
    if base is None or aft is None or not (aft < base - 1e-12):
        ok = False
        reasons.append("stop_rate_not_improved")
    if (ev_c.get("winner_sacrifice_rate") or 1) > WINNER_SACRIFICE_MAX + 1e-12:
        ok = False
        reasons.append("winner_sacrifice_gt_10pct")
    if (ev_c.get("day_non_worse_frac") or 0) < DAY_NON_WORSE_MIN - 1e-12:
        ok = False
        reasons.append("day_non_worse_lt_70pct")
    return ok, reasons


def confirm_np(ev_c: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    ok = True
    if (ev_c.get("n_blocked") or 0) < MIN_BLOCKED:
        ok = False
        reasons.append("conf_n_blocked_low")
    base = ev_c.get("base_np_rate")
    aft = ev_c.get("np_rate_after")
    if base is None or aft is None or not (aft < base - 1e-12):
        ok = False
        reasons.append("np_rate_not_improved")
    if not ((ev_c.get("net_pnl_delta") or 0) > 0):
        ok = False
        reasons.append("net_pnl_delta_not_positive")
    if (ev_c.get("winner_sacrifice_rate") or 1) > WINNER_SACRIFICE_MAX + 1e-12:
        ok = False
        reasons.append("winner_sacrifice_gt_10pct")
    return ok, reasons


def _eval_rule_pair(
    rule: dict[str, Any],
    disc: pd.DataFrame,
    conf: pd.DataFrame,
    target: str,
) -> dict[str, Any]:
    m_d = rule_mask(disc, rule)
    m_c = rule_mask(conf, rule)
    ev_d = eval_reject(disc, m_d, target=target)
    ev_c = eval_reject(conf, m_c, target=target)
    ev_d_slim = {k: v for k, v in ev_d.items() if k != "day_deltas"}
    ev_c_slim = dict(ev_c)
    if target == "STOP":
        confirmed, reasons = confirm_stop(ev_c)
    else:
        confirmed, reasons = confirm_np(ev_c)
    score = _score_discovery(ev_d, target)
    return {
        **{k: rule[k] for k in ("rule_id", "features", "description", "kind", "specs")},
        "target": target,
        "discovery": ev_d_slim,
        "confirmation": ev_c_slim,
        "confirmed": confirmed,
        "reject_reasons": reasons,
        "discovery_score": score,
    }


def search_target(
    disc: pd.DataFrame,
    conf: pd.DataFrame,
    features: list[str],
    target: str,
) -> dict[str, Any]:
    y_col = "is_stop" if target == "STOP" else "is_np"
    print(f"[{target}] building quantile AND rules on Discovery n={len(disc)}...", flush=True)
    q_rules = build_quantile_and_rules(disc, features)
    print(f"[{target}] quantile candidates={len(q_rules)}", flush=True)
    print(f"[{target}] fitting DecisionTree depth<=3...", flush=True)
    t_rules = build_tree_rules(disc, features, y_col)
    print(f"[{target}] tree rules={len(t_rules)}", flush=True)
    all_rules = q_rules + t_rules

    # Pre-filter on Discovery (cheap) then confirm shortlist with workers
    pre: list[tuple[float, dict[str, Any]]] = []
    for rule in all_rules:
        m = rule_mask(disc, rule)
        if int(m.sum()) < MIN_BLOCKED:
            continue
        ev = eval_reject(disc, m, target=target)
        hits = ev["blocked_stop"] if target == "STOP" else ev["blocked_np"]
        if hits < 2:
            continue
        sc = _score_discovery(ev, target)
        pre.append((sc, rule))
    pre.sort(key=lambda x: x[0], reverse=True)
    shortlist = [r for _, r in pre[:TOP_CANDIDATES]]
    print(f"[{target}] shortlist after Discovery filter={len(shortlist)} / {len(pre)}", flush=True)

    results: list[dict[str, Any]] = []
    if not shortlist:
        return {
            "target": target,
            "n_candidates_raw": len(all_rules),
            "n_shortlist": 0,
            "n_confirmed": 0,
            "confirmed_rules": [],
            "top_candidates": [],
        }

    workers = min(MAX_WORKERS, len(shortlist))
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_eval_rule_pair, rule, disc, conf, target) for rule in shortlist]
        for fut in as_completed(futs):
            results.append(fut.result())

    results.sort(key=lambda r: (r["confirmed"], r.get("discovery_score") or -1e18), reverse=True)
    confirmed = [r for r in results if r["confirmed"]]
    top = []
    for r in results[:30]:
        row = dict(r)
        conf_m = dict(row.get("confirmation") or {})
        if "day_deltas" in conf_m:
            conf_m["day_deltas_n"] = len(conf_m["day_deltas"])
            del conf_m["day_deltas"]
        row["confirmation"] = conf_m
        top.append(row)

    return {
        "target": target,
        "n_candidates_raw": len(all_rules),
        "n_quantile_rules": len(q_rules),
        "n_tree_rules": len(t_rules),
        "n_discovery_pass": len(pre),
        "n_shortlist": len(shortlist),
        "n_confirmed": len(confirmed),
        "confirmed_rules": confirmed,
        "top_candidates": top,
    }


def main() -> int:
    print("W47 STOP/NP reject search (research-only)", flush=True)
    if not PANEL_PQ.is_file() or not FEAT_PQ.is_file():
        err = {
            "error": "missing_inputs",
            "panel": str(PANEL_PQ),
            "features": str(FEAT_PQ),
            "confirmed_stop_count": 0,
            "confirmed_np_count": 0,
        }
        _wj(OUT_JSON, err)
        print("CONFIRMED_STOP=0 CONFIRMED_NP=0", flush=True)
        return 1

    df = load_joined()
    df, days, disc_days, conf_days = select_days(df)
    disc = df[df["trading_date"].isin(disc_days)].copy()
    conf = df[df["trading_date"].isin(conf_days)].copy()
    features = available_features(disc)
    print(
        f"joined n={len(df)} days={len(days)} features={features}",
        flush=True,
    )
    print(f"Discovery days={disc_days} n={len(disc)}", flush=True)
    print(f"Confirmation days={conf_days} n={len(conf)}", flush=True)
    print(
        f"label counts all: STOP={int(df['is_stop'].sum())} NP={int(df['is_np'].sum())} "
        f"WIN={int(df['is_winner'].sum())}",
        flush=True,
    )

    stop_res = search_target(disc, conf, features, "STOP")
    np_res = search_target(disc, conf, features, "NO_PROGRESS")

    # CAP occupancy summary from best confirmed NP rules
    cap_proxy = []
    for r in np_res.get("confirmed_rules") or []:
        c = r.get("confirmation") or {}
        cap_proxy.append(
            {
                "rule_id": r.get("rule_id"),
                "mean_hold_min_blocked_np": c.get("mean_hold_min_blocked_np"),
                "blocked_np": c.get("blocked_np"),
                "net_pnl_delta": c.get("net_pnl_delta"),
            }
        )
    if not cap_proxy:
        # still report from best Discovery-scoring NP shortlist top
        for r in (np_res.get("top_candidates") or [])[:5]:
            c = r.get("confirmation") or {}
            cap_proxy.append(
                {
                    "rule_id": r.get("rule_id"),
                    "mean_hold_min_blocked_np": c.get(
                        "cap_occupancy_reduction_proxy_mean_hold_min_blocked_np",
                        c.get("mean_hold_min_blocked_np"),
                    ),
                    "blocked_np": c.get("blocked_np"),
                    "net_pnl_delta": c.get("net_pnl_delta"),
                    "confirmed": r.get("confirmed"),
                }
            )

    result = {
        "phase": "W47_stop_np_reject_search",
        "runtime_trading_conditions_modified": False,
        "inputs": {"panel": str(PANEL_PQ), "features": str(FEAT_PQ)},
        "join_key": "trade_id",
        "n_joined": int(len(df)),
        "days_used": days,
        "n_days": len(days),
        "target_days": TARGET_DAYS,
        "insufficient_20_days_note": (
            f"Only {len(days)} trading days available; using all."
            if len(days) < TARGET_DAYS
            else None
        ),
        "discovery_days": disc_days,
        "confirmation_days": conf_days,
        "n_discovery": int(len(disc)),
        "n_confirmation": int(len(conf)),
        "features": features,
        "label_defs": {
            "STOP": "label_stop from entry_panel",
            "NO_PROGRESS": "label_no_progress from entry_panel",
            "WINNER": "label_winner_a OR label_winner_b",
        },
        "confirm_criteria": {
            "STOP": {
                "net_pnl_delta": ">0",
                "stop_rate": "improves (after < base)",
                "winner_sacrifice_rate": f"<={WINNER_SACRIFICE_MAX}",
                "day_non_worse_frac": f">={DAY_NON_WORSE_MIN}",
            },
            "NO_PROGRESS": {
                "np_rate": "improves (after < base)",
                "net_pnl_delta": ">0",
                "winner_sacrifice_rate": f"<={WINNER_SACRIFICE_MAX}",
            },
        },
        "stop_search": stop_res,
        "np_search": np_res,
        "cap_occupancy_reduction_proxy": {
            "definition": "mean hold_minutes of blocked NO_PROGRESS trades under reject rule",
            "confirmed_np_rules": cap_proxy,
        },
        "confirmed_stop_count": int(stop_res.get("n_confirmed") or 0),
        "confirmed_np_count": int(np_res.get("n_confirmed") or 0),
    }
    _wj(OUT_JSON, result)
    print(
        f"CONFIRMED_STOP={result['confirmed_stop_count']} "
        f"CONFIRMED_NP={result['confirmed_np_count']}",
        flush=True,
    )
    print(f"wrote {OUT_JSON}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
