"""Question A–D summaries and date/symbol-aware bootstrap. 5pp continuation is not the gate."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.support_resistance_first_interaction_matched_causal_test_v1 import BOOT_N, BOOT_SEED, EVAL_BLOCKS, MIN_DAY_N, MIN_PAIR_N, MIN_SYMBOL_N, PRIMARY_METRIC


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _rate(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = [r.get(key) for r in rows if r.get(key) is not None]
    if not xs:
        return None
    return float(np.mean([1.0 if x else 0.0 for x in xs]))


def _med(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = [float(r[key]) for r in rows if _finite(r.get(key))]
    if not xs:
        return None
    return float(np.median(xs))


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = [float(r[key]) for r in rows if _finite(r.get(key))]
    if not xs:
        return None
    return float(np.mean(xs))


def pair_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tr = rows
    ct = [r for r in rows if r.get("matched")]
    def pack(prefix: str, src: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "n": len(src),
            "median_mfe": _med(src, f"{prefix}mfe_bps"),
            "median_mae": _med(src, f"{prefix}mae_bps"),
            "median_end": _med(src, f"{prefix}end_bps"),
            "mean_end": _mean(src, f"{prefix}end_bps"),
            "mfe_before_mae": _rate(src, f"{prefix}mfe_before_mae"),
            "p20_before_m20": _rate(src, f"{prefix}p20_before_m20"),
            "p40_before_m20": _rate(src, f"{prefix}p40_before_m20"),
            "p80_before_m30": _rate(src, f"{prefix}p80_before_m30"),
            "median_time_to_failure": _med(src, f"{prefix}time_to_failure_min"),
            "median_time_to_extension": _med(src, f"{prefix}time_to_extension_min"),
            "median_payoff_asymmetry": _med(src, f"{prefix}payoff_asymmetry"),
        }

    a_all = pack("tr_", tr)
    a, b = pack("tr_", ct), pack("ct_", ct)
    def gap(k: str) -> float | None:
        if a.get(k) is None or b.get(k) is None:
            return None
        return float(a[k]) - float(b[k])

    return {
        "treatment_all": a_all,
        "treatment": a,
        "matched_control": b,
        "treatment_n": len(tr),
        "matched_n": len(ct),
        "match_rate": (len(ct) / len(tr)) if tr else None,
        "day_n": len({str(r.get("date")) for r in ct}),
        "symbol_n": len({str(r.get("symbol")) for r in ct}),
        "treatment_day_n": len({str(r.get("date")) for r in tr}),
        "treatment_symbol_n": len({str(r.get("symbol")) for r in tr}),
        "gap_p20_before_m20": gap("p20_before_m20"),
        "gap_p40_before_m20": gap("p40_before_m20"),
        "gap_median_mfe": gap("median_mfe"),
        "gap_median_end": gap("median_end"),
        "gap_mfe_before_mae": gap("mfe_before_mae"),
        "primary_metric": PRIMARY_METRIC,
        "causal_contrast_on_matched_pairs_only": True,
        "five_pp_continuation_not_used": True,
    }


def _cluster_boot(rows: list[dict[str, Any]], *, key: str, cluster: str, n: int, seed: int) -> dict[str, Any]:
    matched = [r for r in rows if r.get("matched")]
    if len(matched) < 10:
        return {"n": len(matched), "ci95_lo": None, "ci95_hi": None, "p50": None}
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in matched:
        by[str(r.get(cluster) or "")].append(r)
    names = [k for k in by.keys() if k]
    if not names:
        return {"n": len(matched), "ci95_lo": None, "ci95_hi": None, "p50": None}
    rng = np.random.default_rng(int(seed))
    diffs = []
    for _ in range(int(n)):
        draw = rng.choice(names, size=len(names), replace=True)
        sample = [row for d in draw for row in by[str(d)]]
        if not sample:
            continue
        if key in {"p20_before_m20", "p40_before_m20", "p80_before_m30", "mfe_before_mae"}:
            tr = _rate(sample, "tr_" + key)
            ct = _rate(sample, "ct_" + key)
        else:
            tr = _med(sample, "tr_" + key)
            ct = _med(sample, "ct_" + key)
        if tr is None or ct is None:
            continue
        diffs.append(float(tr) - float(ct))
    if not diffs:
        return {"n": len(matched), "ci95_lo": None, "ci95_hi": None, "p50": None}
    arr = np.asarray(diffs, dtype=float)
    return {
        "n": len(matched),
        "boot_n": int(n),
        "cluster": cluster,
        "p50": float(np.median(arr)),
        "ci95_lo": float(np.percentile(arr, 2.5)),
        "ci95_hi": float(np.percentile(arr, 97.5)),
        "excludes_zero": bool(np.percentile(arr, 2.5) > 0 or np.percentile(arr, 97.5) < 0),
        "positive": bool(np.median(arr) > 0 and np.percentile(arr, 2.5) > 0),
    }


def evaluate_question(rows: list[dict[str, Any]], *, question: str) -> dict[str, Any]:
    eval_rows = [r for r in rows if str(r.get("block") or "") in EVAL_BLOCKS]
    d1 = [r for r in rows if str(r.get("block") or "") == "D1"]
    st = pair_stats(eval_rows)
    boot_date = _cluster_boot(eval_rows, key=PRIMARY_METRIC, cluster="date", n=BOOT_N, seed=BOOT_SEED)
    boot_sym = _cluster_boot(eval_rows, key=PRIMARY_METRIC, cluster="symbol", n=BOOT_N, seed=BOOT_SEED + 1)
    n_ok = (
        int(st.get("matched_n") or 0) >= MIN_PAIR_N
        and int(st.get("day_n") or 0) >= MIN_DAY_N
        and int(st.get("symbol_n") or 0) >= MIN_SYMBOL_N
    )
    sep = bool(n_ok and boot_date.get("excludes_zero"))
    fav = bool(n_ok and boot_date.get("positive"))
    return {
        "question": question,
        "eval_blocks": list(EVAL_BLOCKS),
        "d2_d4": st,
        "d1": pair_stats(d1),
        "bootstrap_date": boot_date,
        "bootstrap_symbol": boot_sym,
        "powered": n_ok,
        "separation_on_primary_metric": sep,
        "favorable_on_primary_metric": fav,
        "primary_metric": PRIMARY_METRIC,
        "five_pp_continuation_not_used": True,
    }


def decide_from_questions(qs: dict[str, dict[str, Any]]) -> dict[str, Any]:
    primary_ids = ("A", "B", "C", "D")
    powered = [qid for qid in primary_ids if (qs.get(qid) or {}).get("powered")]
    seps = [qid for qid in primary_ids if (qs.get(qid) or {}).get("separation_on_primary_metric")]
    return {
        "powered_questions": powered,
        "separated_questions": seps,
        "any_primary_separation": bool(seps),
        "any_powered": bool(powered),
        "secondary_must_not_influence_primary": True,
    }
