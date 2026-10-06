"""Harvest T3 predicate validity post-fill on completed 1m bars."""
from __future__ import annotations

import gc
import json
from typing import Any, Callable, Optional

import numpy as np

from research.am_entry_profit_improvement.publish import json_sanitize
from research.anchor_vs_event_driven.run_comparison import _bare, find_capture_dir
from research.simple_tech_entry_family.harvest import load_day_cache
from research.simple_tech_entry_family.stages import pullback_setup, reversal_rci, trend_up
from research.simple_tech_redesign.entry_thesis_invalidation_rca_spec import PREDICATE_INVENTORY
from research.simple_tech_redesign.exit_lifecycle_harvest import _last_i_at_or_before
from research.simple_tech_redesign.isolation import RESEARCH_CACHE, TODAY
from research.simple_tech_redesign.ptf_post_be_rca_harvest import stream_day
from research.simple_tech_redesign.v24_harvest import _finite

PATH_CACHE = RESEARCH_CACHE / "entry_thesis_invalidation_exit_rca"

PREDICATES: list[tuple[str, Callable[..., bool]]] = [
    ("P1_TREND_UP", trend_up),
    ("P2_PULLBACK_SETUP", pullback_setup),
    ("P3_REVERSAL_RCI", reversal_rci),
]
N_PRED = len(PREDICATES)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except (TypeError, ValueError):
        return None


def _thesis_label(invalid_n: int) -> str:
    if invalid_n <= 0:
        return "ALL_VALID"
    if invalid_n == 1:
        return "PARTIALLY_INVALID"
    if invalid_n >= N_PRED:
        return "FULLY_INVALID"
    return "MULTI_INVALID"


def _pred_valid(ind: dict[str, np.ndarray], i: int, fn: Callable[..., bool]) -> bool:
    try:
        return bool(fn(ind, int(i)))
    except Exception:
        return False


def eval_entry_parity(tf1: dict[str, np.ndarray], t0: float) -> tuple[bool, dict[str, bool], Optional[int]]:
    i_sig = _last_i_at_or_before(tf1, float(t0))
    if i_sig is None:
        return False, {}, None
    states = {pid: _pred_valid(tf1, int(i_sig), fn) for pid, fn in PREDICATES}
    return all(states.values()), states, int(i_sig)


def eval_thesis_post_fill(
    tf1: dict[str, np.ndarray],
    tf3: dict[str, np.ndarray],
    *,
    t0: float,
    fill_t: float,
    sess_end: float,
) -> dict[str, Any]:
    fin = tf1.get("finalize_t")
    out: dict[str, Any] = {
        "entry_parity_ok": False,
        "entry_states": {},
        "signal_bar_i": None,
        "post_fill_bar_n": 0,
        "predicates": {},
        "thesis": {},
        "sequence_order": None,
        "closed_overlap": {},
    }
    ok, entry_states, i_sig = eval_entry_parity(tf1, t0)
    out["entry_parity_ok"] = bool(ok)
    out["entry_states"] = entry_states
    out["signal_bar_i"] = i_sig
    if not ok or i_sig is None or fin is None or int(fin.size) == 0:
        return out

    j0 = int(np.searchsorted(fin, float(fill_t), side="right"))
    bars: list[dict[str, Any]] = []
    for i in range(j0, int(fin.size)):
        ft = float(fin[i]) if _finite(fin[i]) else None
        if ft is None or ft > float(sess_end) + 1e-12:
            if ft is not None and ft > float(sess_end) + 1e-12:
                break
            continue
        inv = {pid: (not _pred_valid(tf1, i, fn)) for pid, fn in PREDICATES}
        invalid_n = sum(1 for v in inv.values() if v)
        bars.append({"i": int(i), "t": ft, "invalid_n": int(invalid_n), "label": _thesis_label(invalid_n), "invalid": inv})

    out["post_fill_bar_n"] = len(bars)
    if not bars:
        return out

    pred_events: dict[str, dict[str, Any]] = {}
    for pid, fn in PREDICATES:
        ev = {
            "valid_to_invalid_n": 0,
            "invalid_to_valid_n": 0,
            "first_invalidation_t": None,
            "first_recovery_t": None,
            "second_invalidation_t": None,
            "had_first_invalidation": False,
            "had_recovery": False,
            "had_second_invalidation": False,
        }
        prev_valid = True
        seen_invalid = False
        seen_recovery = False
        for b in bars:
            valid = not bool(b["invalid"][pid])
            if prev_valid and not valid:
                ev["valid_to_invalid_n"] = int(ev["valid_to_invalid_n"]) + 1
                if not ev["had_first_invalidation"]:
                    ev["first_invalidation_t"] = b["t"]
                    ev["had_first_invalidation"] = True
                elif seen_recovery and not ev["had_second_invalidation"]:
                    ev["second_invalidation_t"] = b["t"]
                    ev["had_second_invalidation"] = True
                seen_invalid = True
            if (not prev_valid) and valid:
                ev["invalid_to_valid_n"] = int(ev["invalid_to_valid_n"]) + 1
                if seen_invalid and not ev["had_recovery"]:
                    ev["first_recovery_t"] = b["t"]
                    ev["had_recovery"] = True
                    seen_recovery = True
            prev_valid = valid
        pred_events[pid] = ev
    out["predicates"] = pred_events

    thesis = {
        "any_first_invalidation": any(v["had_first_invalidation"] for v in pred_events.values()),
        "ever_partially_invalid": any(b["label"] == "PARTIALLY_INVALID" for b in bars),
        "ever_multi_invalid": any(b["label"] in ("MULTI_INVALID", "FULLY_INVALID") for b in bars),
        "ever_fully_invalid": any(b["label"] == "FULLY_INVALID" for b in bars),
        "recovered_to_all_valid": False,
        "multi_before_full_recovery": False,
        "first_multi_invalid_t": None,
        "valid_to_invalid_n": 0,
        "invalid_to_valid_n": 0,
    }
    prev_label = "ALL_VALID"
    saw_multi = False
    saw_recovery_all = False
    for b in bars:
        lab = str(b["label"])
        if lab != prev_label:
            if prev_label == "ALL_VALID" and lab != "ALL_VALID":
                thesis["valid_to_invalid_n"] = int(thesis["valid_to_invalid_n"]) + 1
            if prev_label != "ALL_VALID" and lab == "ALL_VALID":
                thesis["invalid_to_valid_n"] = int(thesis["invalid_to_valid_n"]) + 1
                saw_recovery_all = True
            prev_label = lab
        if lab in ("MULTI_INVALID", "FULLY_INVALID"):
            saw_multi = True
            if thesis["first_multi_invalid_t"] is None:
                thesis["first_multi_invalid_t"] = b["t"]
        if saw_multi and lab == "ALL_VALID":
            thesis["recovered_to_all_valid"] = True
    thesis["multi_before_full_recovery"] = bool(saw_multi and not saw_recovery_all)
    thesis["recovered_to_all_valid"] = bool(saw_recovery_all)
    out["thesis"] = thesis

    first_invalid_pred = None
    first_invalid_t = None
    for pid, ev in pred_events.items():
        t = ev.get("first_invalidation_t")
        if t is None:
            continue
        if first_invalid_t is None or float(t) < float(first_invalid_t) - 1e-12:
            first_invalid_t = float(t)
            first_invalid_pred = pid
    out["first_invalidated_predicate"] = first_invalid_pred

    if thesis["ever_multi_invalid"]:
        out["sequence_order"] = "C" if thesis["recovered_to_all_valid"] else "D"
    elif thesis["any_first_invalidation"]:
        out["sequence_order"] = "A" if thesis["recovered_to_all_valid"] else "B"
    else:
        out["sequence_order"] = "NONE"

    if thesis.get("first_multi_invalid_t") is not None and tf3.get("finalize_t") is not None:
        from research.simple_tech_redesign.exit_lifecycle_harvest import _first_bar_flag
        from research.simple_tech_redesign.v27_harvest import primitive_known

        hit, ht = _first_bar_flag(
            tf3,
            start_t=float(fill_t),
            end_t=None,
            pred=lambda ind, ix: primitive_known("A_EMA_STRUCTURE_LOSS", ind, ix),
        )
        mt = thesis.get("first_multi_invalid_t")
        out["closed_overlap"] = {
            "v27_ema_structure_loss_hit": bool(hit),
            "v27_ema_structure_loss_t": ht,
            "multi_invalid_t": mt,
            "same_bar": bool(hit and ht is not None and mt is not None and abs(float(ht) - float(mt)) <= 60.0),
        }
    return out


def harvest_trade(row: dict[str, Any], packed: dict[str, Any]) -> dict[str, Any]:
    sym = _bare(row.get("symbol"))
    t0 = _f(row.get("t0"))
    fill_t = _f(row.get("fill_time"))
    out = dict(row)
    if t0 is None or fill_t is None:
        out["thesis_ok"] = False
        return out
    tf_by = (packed.get("tf_by_sym") or {}).get(sym) or {}
    tf1 = tf_by.get("tf1") or {}
    tf3 = tf_by.get("tf3") or {}
    path = eval_thesis_post_fill(tf1, tf3, t0=float(t0), fill_t=float(fill_t), sess_end=float(packed["am_end"]))
    out["thesis_ok"] = bool(path.get("entry_parity_ok"))
    out["thesis_path"] = path
    return out


def harvest_day(
    day: str,
    *,
    cohort: str,
    rows: list[dict[str, Any]],
    spec_sha: str,
    today: str = TODAY,
) -> dict[str, Any]:
    path = PATH_CACHE / f"day_{cohort}_{day}.json"
    cached = load_day_cache(path, spec_sha)
    if cached and cached.get("ok"):
        return cached
    if not rows:
        body = {"ok": True, "date": day, "cohort": cohort, "spec_sha": spec_sha, "rows": [], "leak": {}}
        PATH_CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return body
    capture = find_capture_dir(day)
    if capture is None:
        return {"ok": False, "blocker": f"CAPTURE_MISSING:{day}", "date": day}
    symbols = {_bare(r.get("symbol")) for r in rows if _bare(r.get("symbol"))}
    leak = {"FUTURE_QUOTE_CARRYBACK_N": 0, "FUTURE_TIMESTAMP_CARRYBACK_N": 0}
    print(f"{cohort} {day} thesis symbols={len(symbols)} fills={len(rows)}", flush=True)
    packed = stream_day(day, capture, symbols, leak)
    out_rows = [harvest_trade(dict(r), packed) for r in rows]
    body = {"ok": True, "date": day, "cohort": cohort, "spec_sha": spec_sha, "rows": out_rows, "leak": leak}
    PATH_CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(json_sanitize(body), ensure_ascii=False, default=str) + "\n", encoding="utf-8")
    del packed
    gc.collect()
    return body
