"""Phase E–G: Precommit + sealed 20260804 prospective evaluation."""
from __future__ import annotations

import hashlib
import json
import pickle
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np

from research.e1_x19_outcome_pre_path.population import _build_day, attach_derived
from research.e1_x21_entry_factory_exit_benchmark.factory import decision_mask
from research.e1_x22_actual_exit_factory.evaluate import ExitTradeMatrix, REASON_CODES, REASON_TO_I
from research.e1_x22_actual_exit_factory.exits import EXIT_SPECS, simulate_exit_on_path
from research.e1_x22_actual_exit_factory.paths import build_path_cache, session_end_epoch

from . import (
    ACTUAL_EXITS,
    BUNDLE_ID,
    TARGET_DAY,
    TARGET_ROLE,
    TIE_BREAK_RULE,
    TOUCH_EPS,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x23_diversified_bundle"


def _sha_obj(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def make_precommit(
    bundle_pairs: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    alias_rows: list[dict[str, Any]],
    *,
    raw_opened_before: bool,
) -> dict[str, Any]:
    cand_by = {c["candidate_id"]: c for c in candidates}
    alias_by = {a["candidate_id"]: a for a in alias_rows}
    exit_impl = Path(__file__).resolve().parents[1] / "e1_x22_actual_exit_factory" / "exits.py"
    path_impl = Path(__file__).resolve().parents[1] / "e1_x22_actual_exit_factory" / "paths.py"

    pair_list = []
    for p in bundle_pairs:
        cid = p["candidate_id"]
        c = cand_by[cid]
        a = alias_by[cid]
        pair_list.append({
            "pair_id": p["pair_id"],
            "candidate_id": cid,
            "actual_exit_id": p["actual_exit_id"],
            "decision_mask_sha256": a["decision_mask_sha256"],
            "logic_depth": p["logic_depth"],
            "component_family_signature": p["component_family_signature"],
            "retention_band": p["retention_band"],
            "period_bundle_tag": (p.get("period_tags") or {}).get("bundle_tag"),
            "candidate_spec": {
                "feature_name": c.get("feature_name"),
                "rule_type": c.get("rule_type"),
                "threshold": c.get("threshold"),
                "op": c.get("op"),
                "parents": c.get("parents"),
                "implementation_id": c.get("implementation_id"),
                "n_features": c.get("n_features"),
            },
        })

    exit_specs = {
        eid: {f: getattr(EXIT_SPECS[eid], f) for f in EXIT_SPECS[eid].__dataclass_fields__}
        for eid in ACTUAL_EXITS
    }
    body = {
        "bundle_id": BUNDLE_ID,
        "pair_list": pair_list,
        "exit_specifications": exit_specs,
        "exit_priority": "hard_stop > profit_target > trailing > no_progress > max_hold > session_close",
        "exit_tie_break_rule": TIE_BREAK_RULE,
        "touch_eps": TOUCH_EPS,
        "exit_implementation_sha256": _file_sha(exit_impl),
        "path_reconstruction_implementation_sha256": _file_sha(path_impl),
        "created_at_jst": datetime.now(JST).isoformat(),
        "20260804_raw_opened_before_precommit": raw_opened_before,
        "20260804_outcome_inspected_before_precommit": False,
        "registry_20260804": "ALPHA_PROSPECTIVE_RESERVED",
    }
    body["bundle_sha256"] = _sha_obj({k: v for k, v in body.items() if k != "bundle_sha256"})
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "_precommit.json").write_text(json.dumps(body, indent=2, default=str), encoding="utf-8")
    return body


def build_prospective_population() -> list[dict[str, Any]]:
    """Build 20260804 clusters with same X19 contract (once)."""
    cache = OUT / "_clusters_20260804.jsonl"
    if cache.exists():
        rows = [json.loads(l) for l in cache.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"=== loaded 20260804 clusters n={len(rows)} ===", flush=True)
        return attach_derived(rows)
    raw = _build_day(TARGET_DAY)
    for r in raw:
        r["date"] = TARGET_DAY
    cache.write_text("\n".join(json.dumps(r, default=str) for r in raw), encoding="utf-8")
    return attach_derived(raw)


def _apply_mask_on_day(day_rows: list[dict[str, Any]], cand: dict[str, Any], parent_cands: dict[str, dict]) -> np.ndarray:
    if cand.get("n_features", 1) == 1:
        return decision_mask(day_rows, cand)
    parents = cand.get("parents") or []
    m = np.ones(len(day_rows), dtype=bool)
    for pid in parents:
        m &= decision_mask(day_rows, parent_cands[pid])
    return m


def simulate_day_exits(day_rows: list[dict[str, Any]], cache: dict[str, Any]) -> dict[str, ExitTradeMatrix]:
    n = len(day_rows)
    out = {eid: ExitTradeMatrix(n) for eid in ACTUAL_EXITS}
    for i, r in enumerate(day_rows):
        tarr = cache["times"][i]
        parr = cache["prices"][i]
        if tarr.size == 0 or r.get("CurrentPrice") is None:
            continue
        for eid in ACTUAL_EXITS:
            tr = simulate_exit_on_path(
                exit_id=eid,
                entry_epoch=float(r["grid_epoch"]),
                entry_price=float(r["CurrentPrice"]),
                date=r["date"],
                session=r["session"],
                times=tarr,
                prices=parr,
            )
            if tr is None:
                continue
            m = out[eid]
            m.valid[i] = True
            m.pnl[i] = tr["gross_reference_pnl_yen_100"]
            m.ret_bps[i] = tr["reference_return_bps"]
            m.hold[i] = tr["hold_sec"]
            m.reason[i] = REASON_TO_I.get(tr["exit_reason"], -1)
            m.mfe[i] = tr["MFE_at_exit_bps"]
            m.mae[i] = tr["MAE_at_exit_bps"]
            m.entry_px[i] = tr["entry_price"]
            m.exit_px[i] = tr["exit_price"]
            m.entry_t[i] = tr["entry_time_epoch"]
            m.exit_t[i] = tr["exit_time_epoch"]
    return out


def _agg(mat: ExitTradeMatrix, mask: np.ndarray, day_rows: list[dict[str, Any]]) -> dict[str, Any]:
    idx = np.where(mask & mat.valid)[0]
    n = int(idx.size)
    if n == 0:
        return {"trades": 0, "wins": 0, "losses": 0, "avg_reference_pnl_yen_100": None,
                "median_reference_pnl_yen_100": None, "avg_return_bps": None,
                "profit_factor_reference": None, "worst_trade": None,
                "max_drawdown_reference_yen_100": 0.0, "exit_reason_counts": {},
                "avg_hold_sec": None, "hard_stop_rate": None}
    pnls = mat.pnl[idx]
    rets = mat.ret_bps[idx]
    holds = mat.hold[idx]
    wins = int(np.sum(pnls > 0))
    losses = int(np.sum(pnls < 0))
    gp = float(np.sum(pnls[pnls > 0])) if wins else 0.0
    gl = float(abs(np.sum(pnls[pnls < 0]))) if losses else 0.0
    order = np.argsort(idx)
    cum = np.cumsum(pnls[order])
    peak = np.maximum.accumulate(cum)
    max_dd = float(np.min(cum - peak))
    reasons = {}
    for code, name in enumerate(REASON_CODES):
        c = int(np.sum(mat.reason[idx] == code))
        if c:
            reasons[name] = c
    hard = reasons.get("hard_stop", 0) / n
    return {
        "trades": n,
        "wins": wins,
        "losses": losses,
        "avg_reference_pnl_yen_100": float(np.mean(pnls)),
        "median_reference_pnl_yen_100": float(np.median(pnls)),
        "avg_return_bps": float(np.mean(rets)),
        "profit_factor_reference": (gp / gl) if gl > 0 else (float("inf") if gp > 0 else None),
        "worst_trade": float(np.min(pnls)),
        "max_drawdown_reference_yen_100": max_dd,
        "exit_reason_counts": reasons,
        "avg_hold_sec": float(np.mean(holds)),
        "hard_stop_rate": hard,
        "symbols": len({day_rows[i]["symbol"] for i in idx}),
        "sessions": len({day_rows[i]["session"] for i in idx}),
    }


def judge_prospective(pair_m: dict[str, Any], base_m: dict[str, Any]) -> str:
    if (pair_m.get("trades") or 0) < 10:
        return "PROSPECTIVE_SUPPORT_INSUFFICIENT"

    def better(a, b, higher_better=True):
        if a is None or b is None:
            return False
        return a > b if higher_better else a < b

    checks = [
        better(pair_m.get("avg_return_bps"), base_m.get("avg_return_bps"), True),
        better(pair_m.get("profit_factor_reference"), base_m.get("profit_factor_reference"), True),
        better(pair_m.get("worst_trade"), base_m.get("worst_trade"), True),
        better(pair_m.get("max_drawdown_reference_yen_100"), base_m.get("max_drawdown_reference_yen_100"), True),
        better(pair_m.get("hard_stop_rate"), base_m.get("hard_stop_rate"), False),
    ]
    n_imp = sum(1 for c in checks if c)
    n_worse = sum(1 for c in checks if c is False)
    # recount: checks that are False aren't necessarily worse if None
    improvements = n_imp
    if improvements >= 2:
        return "PROSPECTIVE_SUPPORTED"
    if improvements == 1:
        return "PROSPECTIVE_MIXED"
    # zero improvements — check if mixed directions among available
    return "PROSPECTIVE_FAILED"


def evaluate_prospective(
    day_rows: list[dict[str, Any]],
    bundle_pairs: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    print(f"=== path cache {TARGET_DAY} n={len(day_rows)} ===", flush=True)
    # reuse path builder but only for this day's rows; write separate cache
    # Temporarily monkey via meta_key isolation: build with use_disk False then save
    from research.e1_x22_actual_exit_factory import paths as pathmod
    # Build inline slim cache for day only
    cache = _build_day_path_cache(day_rows)
    mats = simulate_day_exits(day_rows, cache)
    parent_cands = {c["candidate_id"]: c for c in candidates if c.get("n_features", 1) == 1}
    all_cands = {c["candidate_id"]: c for c in candidates}

    baselines = {}
    full = np.ones(len(day_rows), dtype=bool)
    for eid in ACTUAL_EXITS:
        baselines[eid] = _agg(mats[eid], full, day_rows)

    results = []
    status_counts = defaultdict_int = __import__("collections").Counter()
    for p in bundle_pairs:
        cid = p["candidate_id"]
        eid = p["actual_exit_id"]
        cand = all_cands[cid]
        mask = _apply_mask_on_day(day_rows, cand, parent_cands)
        missing = sum(1 for i, r in enumerate(day_rows) if not mask[i] and _feat_missing(cand, r, parent_cands))
        allowed = int(mask.sum())
        rejected = len(day_rows) - allowed - missing
        m = _agg(mats[eid], mask, day_rows)
        st = judge_prospective(m, baselines[eid])
        status_counts[st] += 1
        results.append({
            "pair_id": p["pair_id"],
            "candidate_id": cid,
            "actual_exit_id": eid,
            "logic_depth": p["logic_depth"],
            "component_family_signature": p["component_family_signature"],
            "retention_band": p["retention_band"],
            "period_bundle_tag": (p.get("period_tags") or {}).get("bundle_tag"),
            "entry_support": allowed,
            "feature_missing": missing,
            "entry_rejected": rejected,
            "metrics": m,
            "baseline": baselines[eid],
            "status": st,
        })
    return {
        "day": TARGET_DAY,
        "role": TARGET_ROLE,
        "population_n": len(day_rows),
        "baselines": baselines,
        "results": results,
        "status_counts": dict(status_counts),
    }


def _feat_missing(cand, r, parents):
    if cand.get("n_features", 1) == 1:
        return r.get(cand["feature_name"]) is None
    for pid in cand.get("parents") or []:
        if r.get(parents[pid]["feature_name"]) is None:
            return True
    return False


def _build_day_path_cache(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Path cache for a single-day population (20260804)."""
    from research.e1_x22_actual_exit_factory.paths import _load_price_events, _dash
    from concurrent.futures import ThreadPoolExecutor, as_completed

    by_key: dict[tuple[str, str], list[int]] = {}
    for i, r in enumerate(rows):
        by_key.setdefault((r["date"], r["symbol"]), []).append(i)
    jobs = sorted(by_key.keys())
    tick_map = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = {ex.submit(_load_price_events, d, s): (d, s) for d, s in jobs}
        for fut in as_completed(futs):
            d, s = futs[fut]
            tick_map[(d, s)] = fut.result()

    times = [np.empty(0) for _ in rows]
    prices = [np.empty(0) for _ in rows]
    for (day, sym), idxs in by_key.items():
        tarr, parr = tick_map[(day, sym)]
        for i in idxs:
            r = rows[i]
            g = float(r["grid_epoch"])
            sess_end = session_end_epoch(day, r["session"])
            lim_t = min(g + 300.0, sess_end)
            if tarr.size == 0:
                continue
            i0 = int(np.searchsorted(tarr, g, side="right") - 1)
            if i0 < 0:
                continue
            i1 = int(np.searchsorted(tarr, lim_t, side="right") - 1)
            if i1 < i0:
                continue
            sl_t = tarr[i0: i1 + 1]
            sl_p = parr[i0: i1 + 1]
            keep = sl_t <= sess_end + 1e-9
            times[i] = sl_t[keep]
            prices[i] = sl_p[keep]
    return {"times": times, "prices": prices}


def summarize_prospective(pros: dict[str, Any]) -> dict[str, Any]:
    results = pros["results"]
    sc = pros["status_counts"]

    def by_key(key_fn):
        buckets = {}
        for r in results:
            k = key_fn(r)
            buckets.setdefault(k, __import__("collections").Counter())[r["status"]] += 1
        return {k: dict(v) for k, v in buckets.items()}

    answers = {
        "ALL_PERIOD_POSITIVE_still_strong": _rate(results, "ALL_PERIOD_POSITIVE", "PROSPECTIVE_SUPPORTED"),
        "EVALUATION_REVERSED_reversed_again": _rate(results, "EVALUATION_REVERSED", "PROSPECTIVE_FAILED"),
        "STRESS_REVERSED_on_20260804": _breakdown(results, "STRESS_REVERSED"),
        "single_vs_two_feature": {
            "SINGLE": _breakdown_depth(results, "SINGLE"),
            "TWO_FEATURE": _breakdown_depth(results, "TWO_FEATURE"),
        },
        "exit_reproducibility": by_key(lambda r: r["actual_exit_id"]),
        "retention_stability": by_key(lambda r: r["retention_band"]),
    }
    return {
        "status_counts": sc,
        "by_logic_depth": by_key(lambda r: r["logic_depth"]),
        "by_family_signature": by_key(lambda r: r["component_family_signature"]),
        "by_exit": by_key(lambda r: r["actual_exit_id"]),
        "by_retention": by_key(lambda r: r["retention_band"]),
        "by_period_tag": by_key(lambda r: r.get("period_bundle_tag")),
        "required_answers": answers,
    }


def _rate(results, tag, status):
    sub = [r for r in results if r.get("period_bundle_tag") == tag]
    if not sub:
        return {"n": 0, "rate": None}
    n_s = sum(1 for r in sub if r["status"] == status)
    return {"n": len(sub), "count": n_s, "rate": n_s / len(sub)}


def _breakdown(results, tag):
    sub = [r for r in results if r.get("period_bundle_tag") == tag]
    c = __import__("collections").Counter(r["status"] for r in sub)
    return {"n": len(sub), "status": dict(c)}


def _breakdown_depth(results, depth):
    sub = [r for r in results if r["logic_depth"] == depth]
    c = __import__("collections").Counter(r["status"] for r in sub)
    return {"n": len(sub), "status": dict(c)}
