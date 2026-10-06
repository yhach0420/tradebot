"""D1 freeze then D2-D4 evaluation, ablation, matched control. No PnL. No rule repair."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np

from research.native_causal_context_stack_discovery_v1 import (
    BOOT_N,
    BOOT_SEED,
    CASE_FOUND,
    CASE_NO_RULE,
    CASE_PARTIAL,
    CASE_UNSTABLE,
    EVAL_BLOCKS,
    FAMILIES,
    NEXT_RCA,
    NEXT_STOP,
    TRAIN_BLOCK,
)
from research.native_causal_context_stack_discovery_v1.match import find_fail_rule, index_by_symbol
from research.native_causal_context_stack_discovery_v1.tree import family_of, fit_d1, match_rule
from research.native_causal_context_stack_discovery_v1.walk import walk_events


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    xs = [float(r[key]) for r in rows if _finite(r.get(key))]
    if not xs:
        return None
    return float(np.mean(xs))


def _rate(rows: list[dict[str, Any]], key: str = "y_p40") -> float | None:
    xs = [r.get(key) for r in rows if r.get(key) is not None]
    if not xs:
        return None
    return float(np.mean([float(x) for x in xs]))


def _share_max(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    by: dict[str, int] = defaultdict(int)
    for r in rows:
        by[str(r.get(key) or "")] += 1
    if not by:
        return {"top": None, "n": 0, "share": None, "dominated": False}
    top, n = max(by.items(), key=lambda kv: kv[1])
    tot = len(rows)
    share = n / tot if tot else None
    return {"top": top, "n": n, "share": share, "dominated": bool(share is not None and share >= 0.50)}


def _block_rows(rows: list[dict[str, Any]], block: str) -> list[dict[str, Any]]:
    return [r for r in rows if str(r.get("block") or "") == block]


def preds_t(rule: dict[str, Any]) -> list[tuple[str, str, float]]:
    out = []
    for p in list(rule.get("predicates") or []):
        out.append((str(p["feature"]), str(p["op"]), float(p["threshold"])))
    return out


def drop_family(predicates: list[dict[str, Any]], family: str) -> list[tuple[str, str, float]]:
    out = []
    for p in predicates:
        fam = p.get("family") or family_of(str(p.get("feature") or ""))
        if fam == family:
            continue
        out.append((str(p["feature"]), str(p["op"]), float(p["threshold"])))
    return out


def summarize(rows: list[dict[str, Any]], base: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    return {
        "event_n": n,
        "symbol_n": len({str(r.get("symbol") or "") for r in rows}),
        "day_n": len({str(r.get("date") or "") for r in rows}),
        "p40_before_m20": _rate(rows, "y_p40"),
        "base_p40_before_m20": _rate(base, "y_p40"),
        "incremental_gap": (
            None
            if _rate(rows, "y_p40") is None or _rate(base, "y_p40") is None
            else float(_rate(rows, "y_p40")) - float(_rate(base, "y_p40"))
        ),
        "p20_before_m20": _rate(rows, "y_p20"),
        "p80_before_m30": _rate(rows, "y_p80"),
        "median_mfe": float(np.median([float(r["mfe"]) for r in rows if _finite(r.get("mfe"))])) if any(_finite(r.get("mfe")) for r in rows) else None,
        "median_mae": float(np.median([float(r["mae"]) for r in rows if _finite(r.get("mae"))])) if any(_finite(r.get("mae")) for r in rows) else None,
        "ret_5m": _mean(rows, "ret5"),
        "ret_10m": _mean(rows, "ret10"),
        "ret_20m": _mean(rows, "ret20"),
    }


def date_boot_gap(hit: list[dict[str, Any]], base: list[dict[str, Any]]) -> dict[str, Any]:
    by_h: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_b: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in hit:
        by_h[str(r.get("date") or "")].append(r)
    for r in base:
        by_b[str(r.get("date") or "")].append(r)
    dates = [d for d in by_b if d]
    if len(dates) < 8 or not hit:
        return {"n": len(hit), "ci95_lo": None, "ci95_hi": None, "p50": None, "method": "date_cluster"}
    rng = np.random.default_rng(int(BOOT_SEED))
    diffs = []
    for _ in range(int(BOOT_N)):
        draw = rng.choice(dates, size=len(dates), replace=True)
        h = [row for d in draw for row in by_h.get(str(d), ())]
        b = [row for d in draw for row in by_b.get(str(d), ())]
        rh, rb = _rate(h, "y_p40"), _rate(b, "y_p40")
        if rh is None or rb is None:
            continue
        diffs.append(float(rh) - float(rb))
    if not diffs:
        return {"n": len(hit), "ci95_lo": None, "ci95_hi": None, "p50": None, "method": "date_cluster"}
    arr = np.asarray(diffs, dtype=float)
    lo, hi = float(np.percentile(arr, 2.5)), float(np.percentile(arr, 97.5))
    return {
        "n": len(hit),
        "boot_n": int(BOOT_N),
        "method": "date_cluster",
        "p50": float(np.median(arr)),
        "ci95_lo": lo,
        "ci95_hi": hi,
        "excludes_zero": bool(lo > 0 or hi < 0),
        "positive": bool(np.median(arr) > 0 and lo > 0),
    }


def eval_rule(events: list[dict[str, Any]], predicates: list[tuple[str, str, float]], med: dict[str, float]) -> dict[str, Any]:
    by_block: dict[str, Any] = {}
    for b in (TRAIN_BLOCK, *EVAL_BLOCKS):
        base = _block_rows(events, b)
        hit = [r for r in base if match_rule(r, predicates, med)]
        by_block[b] = {**summarize(hit, base), "hit_n": len(hit)}
    eval_base = [r for r in events if str(r.get("block") or "") in EVAL_BLOCKS]
    eval_hit = [r for r in eval_base if match_rule(r, predicates, med)]
    pooled = {**summarize(eval_hit, eval_base), "hit_n": len(eval_hit)}
    gaps = {b: (by_block[b] or {}).get("incremental_gap") for b in EVAL_BLOCKS}
    pos = [b for b in EVAL_BLOCKS if gaps.get(b) is not None and float(gaps[b]) > 0]
    mass = []
    for b in EVAL_BLOCKS:
        g = gaps.get(b)
        n = int((by_block[b] or {}).get("hit_n") or 0)
        mass.append((b, (float(g) * n) if g is not None and float(g) > 0 else 0.0))
    tot = sum(m for _, m in mass)
    top_block_share = (max((m for _, m in mass), default=0.0) / tot) if tot else None
    one_block = bool(top_block_share is not None and top_block_share >= 0.80)
    conc_sym = _share_max(eval_hit, "symbol")
    conc_day = _share_max(eval_hit, "date")
    boot = date_boot_gap(eval_hit, eval_base)
    survives = bool(
        len(pos) == 3
        and not one_block
        and not conc_sym["dominated"]
        and not conc_day["dominated"]
    )
    return {
        "blocks": by_block,
        "d2_d4": pooled,
        "positive_blocks": pos,
        "top_block_uplift_share": top_block_share,
        "one_block_dominated": one_block,
        "one_symbol_dominated": bool(conc_sym["dominated"]),
        "one_day_dominated": bool(conc_day["dominated"]),
        "symbol_conc": conc_sym,
        "day_conc": conc_day,
        "clustered": boot,
        "survives": survives,
        "eval_hit": eval_hit,
        "eval_base": eval_base,
    }


def i2_convergence(rule: dict[str, Any]) -> bool:
    preds = list(rule.get("predicates") or [])
    names = {str(p.get("feature") or "") for p in preds}
    if "breaking_active_zone" not in names:
        return False
    tv = [p for p in preds if str(p.get("feature") or "") == "tv_clock_pctl"]
    if not tv:
        return False
    return any(str(p.get("op") or "") == ">" and float(p.get("threshold") or 0) >= 0.70 for p in tv)


def matched_control(eval_hit: list[dict[str, Any]], eval_base: list[dict[str, Any]], predicates: list[tuple[str, str, float]], med: dict[str, float]) -> dict[str, Any]:
    fail = [r for r in eval_base if not match_rule(r, predicates, med)]
    by = index_by_symbol(fail)
    pairs = []
    for row in eval_hit:
        ctrl = find_fail_rule(row, by.get(str(row.get("symbol") or ""), []))
        if ctrl is None:
            continue
        pairs.append(
            {
                "symbol": row.get("symbol"),
                "date": row.get("date"),
                "block": row.get("block"),
                "tr_y": row.get("y_p40"),
                "ct_y": ctrl.get("y_p40"),
                "tr_mfe": row.get("mfe"),
                "ct_mfe": ctrl.get("mfe"),
            }
        )
    if not pairs:
        return {"matched_n": 0, "match_rate": 0.0 if eval_hit else None, "gap_p40": None}
    tr = _rate(pairs, "tr_y")
    ct = _rate(pairs, "ct_y")
    return {
        "treated_n": len(eval_hit),
        "matched_n": len(pairs),
        "match_rate": (len(pairs) / len(eval_hit)) if eval_hit else None,
        "treatment_p40": tr,
        "control_p40": ct,
        "gap_p40": None if tr is None or ct is None else float(tr) - float(ct),
        "treatment_median_mfe": _mean(pairs, "tr_mfe"),
        "control_median_mfe": _mean(pairs, "ct_mfe"),
        "future_outcomes_not_used_for_matching": True,
    }


def material_drop(orig: float | None, abl: float | None) -> bool:
    if orig is None or abl is None:
        return False
    drop = float(orig) - float(abl)
    return bool(drop > 0 and (float(abl) <= 0 or drop >= 0.3 * abs(float(orig)) or drop >= 0.005))


def decide(pack: dict[str, Any]) -> dict[str, Any]:
    if pack.get("unstable_in_discovery"):
        return {"VERDICT": CASE_UNSTABLE, "NEXT": NEXT_STOP, "surviving_rule_ids": []}
    surviving = list(pack.get("surviving_rule_ids") or [])
    evals = dict(pack.get("rule_eval") or {})
    ci_pos = any(bool(((evals.get(rid) or {}).get("clustered") or {}).get("positive")) for rid in surviving)
    if surviving and ci_pos:
        verd, nxt = CASE_FOUND, NEXT_RCA
    elif surviving:
        verd, nxt = CASE_PARTIAL, NEXT_RCA
    else:
        verd, nxt = CASE_NO_RULE, NEXT_STOP
    return {
        "VERDICT": verd,
        "NEXT": nxt,
        "surviving_rule_ids": surviving,
        "d1_status": "DISCOVERY_RULE_LEARNING_ONLY",
        "d2_d4_status": "LOCKED_INTERNAL_REPLICATION",
        "not_validation": True,
        "i2_not_reopened": True,
        "pnl_optimization": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "detector_retuned": False,
        "d2_d4_rule_modification": False,
    }


def build_report_body(bind: dict[str, Any]) -> dict[str, Any]:
    walked = walk_events(bind)
    if not walked.get("ok"):
        return {"ok": False, "reason": walked.get("reason"), "decision": {"VERDICT": CASE_NO_RULE, "NEXT": NEXT_STOP}}
    events = list(walked.get("events") or [])
    fitted = fit_d1(events)
    med = dict(fitted.get("med") or {})
    rules = list(fitted.get("rules") or [])
    rule_eval: dict[str, Any] = {}
    ablation: dict[str, Any] = {}
    matched: dict[str, Any] = {}
    conc: dict[str, Any] = {}
    surviving: list[str] = []
    for i, rule in enumerate(rules):
        rid = str(rule.get("rule_id") or f"R{i}")
        pt = list((fitted.get("rules_t") or [None])[i] or preds_t(rule))
        ev = eval_rule(events, pt, med)
        slim = {k: v for k, v in ev.items() if k not in {"eval_hit", "eval_base"}}
        rule_eval[rid] = slim
        if ev.get("survives"):
            surviving.append(rid)
        conc[rid] = {
            "symbol": ev.get("symbol_conc"),
            "day": ev.get("day_conc"),
            "sector": _share_max(ev.get("eval_hit") or [], "sector"),
            "block_hit_n": {b: (ev.get("blocks") or {}).get(b, {}).get("hit_n") for b in EVAL_BLOCKS},
        }
        if ev.get("survives"):
            matched[rid] = matched_control(ev.get("eval_hit") or [], ev.get("eval_base") or [], pt, med)
            orig_gap = (ev.get("d2_d4") or {}).get("incremental_gap")
            fam_rows = {}
            for fam in FAMILIES:
                used = fam in set(rule.get("families") or [])
                if not used:
                    fam_rows[fam] = {"used": False, "material": False, "ablated_gap": orig_gap}
                    continue
                apt = drop_family(list(rule.get("predicates") or []), fam)
                aev = eval_rule(events, apt, med)
                ag = (aev.get("d2_d4") or {}).get("incremental_gap")
                fam_rows[fam] = {
                    "used": True,
                    "material": material_drop(orig_gap, ag),
                    "original_gap": orig_gap,
                    "ablated_gap": ag,
                    "ablated_blocks": {b: (aev.get("blocks") or {}).get(b, {}).get("incremental_gap") for b in EVAL_BLOCKS},
                }
            ablation[rid] = fam_rows
    sr_yes = any(bool((ablation.get(rid) or {}).get("sr", {}).get("material")) for rid in surviving)
    tv_yes = any(bool((ablation.get(rid) or {}).get("participation", {}).get("material")) for rid in surviving)
    vwap_yes = any(bool((ablation.get(rid) or {}).get("vwap", {}).get("material")) for rid in surviving)
    trend_yes = any(bool((ablation.get(rid) or {}).get("trend", {}).get("material")) for rid in surviving)
    local_yes = any(bool((ablation.get(rid) or {}).get("local", {}).get("material")) for rid in surviving)
    mkt_yes = any(bool((ablation.get(rid) or {}).get("market_sector", {}).get("material")) for rid in surviving)
    pack = {
        "unstable_in_discovery": bool(fitted.get("unstable_in_discovery")),
        "surviving_rule_ids": surviving,
        "rule_eval": rule_eval,
    }
    decision = decide(pack)
    counts = dict(walked.get("counts") or {})
    by_block_n = {b: int(counts.get(f"{b}_n") or 0) for b in (TRAIN_BLOCK, *EVAL_BLOCKS)}
    d1 = _block_rows(events, TRAIN_BLOCK)
    return {
        "ok": True,
        "identity": {
            "native_event_n": int(counts.get("native_event_n") or 0),
            "kept_event_n": len(events),
            "n_days": walked.get("n_days"),
            "n_symbols_loaded": walked.get("n_symbols_loaded"),
            "d1_n": len(d1),
            "d2_n": by_block_n.get("D2"),
            "d3_n": by_block_n.get("D3"),
            "d4_n": by_block_n.get("D4"),
            "by_block_n": by_block_n,
        },
        "counts": counts,
        "same_bar_entry_n": walked.get("same_bar_entry_n"),
        "FUTURE_EVENT_SELECTION_N": 0,
        "RETROSPECTIVE_CLUSTER_N": 0,
        "tree_learned_only_on_d1": True,
        "d2_d4_rule_modification": False,
        "d1_internal_stability": fitted.get("stability"),
        "d1_tree": {k: v for k, v in dict(fitted.get("fit") or {}).items()},
        "lock": fitted.get("lock"),
        "frozen_rules": rules,
        "frozen_rule_n": len(rules),
        "rule_eval": rule_eval,
        "ablation": ablation,
        "matched_control": matched,
        "concentration": conc,
        "sr_contribution": {
            "material_on_surviving": sr_yes,
            "classification": "SR_AUXILIARY_CONTEXT_SUPPORTED" if sr_yes else "SR_METADATA_NOT_IN_THIS_MECHANISM",
        },
        "participation_contribution": {
            "material_on_surviving": tv_yes,
            "classification": "PARTICIPATION_CONTEXT_SUPPORTED" if tv_yes else "TV_NOT_CARRIED_INTO_LATER_STRATEGY",
        },
        "family_contribution": {
            "sr": sr_yes,
            "participation": tv_yes,
            "vwap": vwap_yes,
            "trend": trend_yes,
            "local": local_yes,
            "market_sector": mkt_yes,
        },
        "i2_convergence": {rid: i2_convergence(r) for rid, r in ((str(x.get("rule_id")), x) for x in rules)},
        "d1_base_rate": (fitted.get("lock") or {}).get("d1_base_rate"),
        "min_leaf": fitted.get("min_leaf"),
        "unstable_in_discovery": bool(fitted.get("unstable_in_discovery")),
        "surviving_rule_ids": surviving,
        "decision": decision,
        "primary_metric": "p40_before_m20",
        "tv_not_required": True,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    ident = dict(report.get("identity") or {})
    rules = list(report.get("frozen_rules") or [])
    ev = dict(report.get("rule_eval") or {})
    fam = dict(report.get("family_contribution") or {})
    conc = dict(report.get("concentration") or {})
    packs = []
    for r in rules:
        rid = str(r.get("rule_id") or "")
        e = dict(ev.get(rid) or {})
        packs.append(
            {
                "rule_id": rid,
                "predicates": r.get("predicate_text"),
                "d1_n": r.get("n_train"),
                "d1_p40": r.get("p40_train"),
                "D2": (e.get("blocks") or {}).get("D2"),
                "D3": (e.get("blocks") or {}).get("D3"),
                "D4": (e.get("blocks") or {}).get("D4"),
                "survives": e.get("survives"),
            }
        )
    while len(packs) < 3:
        packs.append(None)
    return {
        "Base displacement event_n?": ident.get("native_event_n"),
        "D1/D2/D3/D4 counts?": {
            "D1": ident.get("d1_n"),
            "D2": ident.get("d2_n"),
            "D3": ident.get("d3_n"),
            "D4": ident.get("d4_n"),
        },
        "Any future event selection?": False,
        "Any retrospective clustering?": False,
        "Tree learned only on D1?": True,
        "Any D2-D4 rule modification?": False,
        "Frozen rule_n?": report.get("frozen_rule_n"),
        "Rule 1 predicates / counts / effects?": packs[0],
        "Rule 2?": packs[1],
        "Rule 3?": packs[2],
        "Which rules are positive in D2/D3/D4?": d.get("surviving_rule_ids"),
        "Does S/R materially improve a surviving combination?": fam.get("sr"),
        "Does TradingValue?": fam.get("participation"),
        "Does VWAP?": fam.get("vwap"),
        "Does 5m/15m trend?": fam.get("trend"),
        "Does local structure?": fam.get("local"),
        "Does market/sector context?": fam.get("market_sector"),
        "Does any frozen rule converge with the old underpowered I2 lead?": any(dict(report.get("i2_convergence") or {}).values()),
        "Any one-symbol domination?": {rid: (c.get("symbol") or {}).get("dominated") for rid, c in conc.items()},
        "Any one-day domination?": {rid: (c.get("day") or {}).get("dominated") for rid, c in conc.items()},
        "Any PnL optimization?": False,
        "Old Confirmation opened?": False,
        "Frozen Validation opened?": False,
        "Kabu50?": False,
        "submit/cancel/live?": "0/0/0",
        "VERDICT?": d.get("VERDICT"),
        "NEXT?": d.get("NEXT"),
    }
