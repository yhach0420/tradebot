"""V26 coverage + EXIT primitive support. No ENTRY-only economics. No combination policy."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Optional

from research.simple_tech_entry_family.v13_analyze import set_hash
from research.simple_tech_redesign.v22_analyze import fill_tuples_e4
from research.simple_tech_redesign.v26_spec import (
    ADDED_OPP_MFE_POS_FRAC_MIN,
    B1_SIGNAL_N_EXPECTED,
    CORE_E4_FILL_N_EXPECTED,
    CORE_GOOD_FALSE_MAX,
    CORE_GOOD_MIN_N_FOR_RATE,
    CORRECTED_EVALUABLE_N_EXPECTED,
    CORRECTED_FILL_HASH_EXPECTED,
    CORRECTED_UNEVALUABLE_N_EXPECTED,
    E4_NONFILL_N_EXPECTED,
    MATERIAL_ADDED_MIN_N,
    N_DEV_DAYS,
    PATH_TYPES,
    PICK_ORDER,
    PRIMITIVES,
    SUPPORT_CAPTURE_MIN,
    SUPPORT_DIP_FALSE_MAX,
    SUPPORT_GOOD_FALSE_MAX,
    SUPPORT_MIN_BAD_N,
    SUPPORT_MIN_DAYS,
    SUPPORT_MIN_GOOD_N,
    SUPPORT_MIN_SYMBOLS,
    TF_PREFERENCE,
    TFS,
)


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _mean(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(sum(xs) / float(len(xs)))


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    ys = sorted(xs)
    n = len(ys)
    mid = n // 2
    if n % 2:
        return float(ys[mid])
    return float(ys[mid - 1] + ys[mid]) / 2.0


def _rate(n: int, d: int) -> Optional[float]:
    if int(d) <= 0:
        return None
    return float(n) / float(d)


def _fills(rows: list[dict[str, Any]], role: Optional[str] = None) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not r.get("actual_filled"):
            continue
        if role is not None and str(r.get("fill_role") or "") != role:
            continue
        out.append(r)
    return out


def path_summary(fills: list[dict[str, Any]]) -> dict[str, Any]:
    types = Counter(str(r.get("path_type") or "OTHER") for r in fills)
    mfes = [float(dict(r.get("fill_path") or {}).get("mfe")) for r in fills if _finite(dict(r.get("fill_path") or {}).get("mfe"))]
    maes = [float(dict(r.get("fill_path") or {}).get("mae")) for r in fills if _finite(dict(r.get("fill_path") or {}).get("mae"))]
    m60s = [
        float(dict(r.get("fill_path") or {}).get("bid_markout_60"))
        for r in fills
        if _finite(dict(r.get("fill_path") or {}).get("bid_markout_60"))
    ]
    days = {str(r.get("date") or "") for r in fills}
    syms = {str(r.get("symbol") or "").replace(".T", "") for r in fills}
    return {
        "N": len(fills),
        "DISTINCT_DAYS": len(days),
        "DISTINCT_SYMBOLS": len(syms),
        "PATH_TYPE_N": {k: int(types.get(k) or 0) for k in PATH_TYPES},
        "MFE_MEAN": _mean(mfes),
        "MFE_MEDIAN": _median(mfes),
        "MAE_MEAN": _mean(maes),
        "MAE_MEDIAN": _median(maes),
        "MFE_POS_N": sum(1 for x in mfes if x > 0.0),
        "MFE_POS_FRAC": _rate(sum(1 for x in mfes if x > 0.0), len(mfes)),
        "BID60_MEAN": _mean(m60s),
        "BID60_MEDIAN": _median(m60s),
        "BID60_NEG_FRAC": _rate(sum(1 for x in m60s if x < 0.0), len(m60s)),
    }


def _fired(row: dict[str, Any], tf: str, pid: str) -> bool:
    ev = dict(dict(row.get("exit_eval") or {}).get(tf) or {}).get(pid) or {}
    return bool(ev.get("observable")) and bool(ev.get("fired"))


def _obs(row: dict[str, Any], tf: str, pid: str) -> bool:
    ev = dict(dict(row.get("exit_eval") or {}).get(tf) or {}).get(pid) or {}
    return bool(ev.get("observable"))


def primitive_pack(fills: list[dict[str, Any]], *, tf: str, pid: str) -> dict[str, Any]:
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in fills:
        if _obs(r, tf, pid):
            by_type[str(r.get("path_type") or "OTHER")].append(r)
    bad = by_type["EARLY_FAILURE"] + by_type["PROFIT_THEN_FAILURE"]
    good = by_type["GOOD_CONTINUATION"]
    dip = by_type["DIP_THEN_RECOVERY"]
    bad_cap = sum(1 for r in bad if _fired(r, tf, pid))
    good_fx = sum(1 for r in good if _fired(r, tf, pid))
    dip_fx = sum(1 for r in dip if _fired(r, tf, pid))
    ptf_cap = sum(1 for r in by_type["PROFIT_THEN_FAILURE"] if _fired(r, tf, pid))
    ef_cap = sum(1 for r in by_type["EARLY_FAILURE"] if _fired(r, tf, pid))
    fire_days = {str(r.get("date") or "") for r in fills if _fired(r, tf, pid)}
    obs_days = {str(r.get("date") or "") for r in fills if _obs(r, tf, pid)}
    fire_syms = {str(r.get("symbol") or "").replace(".T", "") for r in fills if _fired(r, tf, pid)}
    obs_syms = {str(r.get("symbol") or "").replace(".T", "") for r in fills if _obs(r, tf, pid)}
    core = [r for r in fills if str(r.get("fill_role") or "") == "CORE" and _obs(r, tf, pid)]
    core_good = [r for r in core if str(r.get("path_type") or "") == "GOOD_CONTINUATION"]
    core_good_fx = sum(1 for r in core_good if _fired(r, tf, pid))
    capture = _rate(bad_cap, len(bad))
    good_false = _rate(good_fx, len(good))
    dip_false = _rate(dip_fx, len(dip))
    core_good_false = _rate(core_good_fx, len(core_good))
    days_ok = len(obs_days) >= int(SUPPORT_MIN_DAYS)
    syms_ok = len(obs_syms) >= int(SUPPORT_MIN_SYMBOLS)
    bad_ok = len(bad) >= int(SUPPORT_MIN_BAD_N)
    good_ok = len(good) >= int(SUPPORT_MIN_GOOD_N)
    capture_ok = capture is not None and float(capture) >= float(SUPPORT_CAPTURE_MIN)
    preserve_ok = good_false is not None and float(good_false) <= float(SUPPORT_GOOD_FALSE_MAX)
    dip_ok = (dip_false is None) or (float(dip_false) <= float(SUPPORT_DIP_FALSE_MAX))
    if len(core_good) >= int(CORE_GOOD_MIN_N_FOR_RATE):
        core_ok = core_good_false is not None and float(core_good_false) <= float(CORE_GOOD_FALSE_MAX)
    else:
        core_ok = int(core_good_fx) <= 2
    supported = bool(days_ok and syms_ok and bad_ok and good_ok and capture_ok and preserve_ok and dip_ok and core_ok)
    return {
        "tf": tf,
        "primitive": pid,
        "observable_n": sum(1 for r in fills if _obs(r, tf, pid)),
        "fired_n": sum(1 for r in fills if _fired(r, tf, pid)),
        "obs_days": len(obs_days),
        "fire_days": len(fire_days),
        "obs_symbols": len(obs_syms),
        "fire_symbols": len(fire_syms),
        "EARLY_FAILURE_N": len(by_type["EARLY_FAILURE"]),
        "EARLY_FAILURE_CAPTURE_N": int(ef_cap),
        "PROFIT_THEN_FAILURE_N": len(by_type["PROFIT_THEN_FAILURE"]),
        "PROFIT_THEN_FAILURE_CAPTURE_N": int(ptf_cap),
        "BAD_N": len(bad),
        "BAD_CAPTURE_N": int(bad_cap),
        "FAILURE_CAPTURE": capture,
        "GOOD_CONTINUATION_N": len(good),
        "GOOD_FALSE_EXIT_N": int(good_fx),
        "GOOD_CONTINUATION_FALSE_EXIT": good_false,
        "DIP_THEN_RECOVERY_N": len(dip),
        "DIP_FALSE_EXIT_N": int(dip_fx),
        "DIP_THEN_RECOVERY_FALSE_EXIT": dip_false,
        "CORE_GOOD_N": len(core_good),
        "CORE_GOOD_FALSE_EXIT_N": int(core_good_fx),
        "CORE_GOOD_FALSE_EXIT": core_good_false,
        "SUPPORTED": bool(supported),
        "SUPPORT_GATES": {
            "days_ok": days_ok,
            "symbols_ok": syms_ok,
            "bad_ok": bad_ok,
            "good_ok": good_ok,
            "capture_ok": capture_ok,
            "preserve_ok": preserve_ok,
            "dip_ok": dip_ok,
            "core_ok": core_ok,
        },
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    evaluable = [r for r in rows if r.get("executable_signal")]
    uneval = [r for r in rows if not r.get("executable_signal")]
    core = _fills(rows, "CORE")
    added = _fills(rows, "ADDED")
    all_fills = _fills(rows)
    e4_nonfill = [r for r in evaluable if not r.get("e4_filled")]
    fallback_eligible = [r for r in e4_nonfill if bool(dict(r.get("ask_fallback") or {}).get("eligible"))]
    by_day = Counter(str(r.get("date") or "") for r in all_fills)
    types = Counter(str(r.get("path_type") or "OTHER") for r in all_fills)
    primitives: dict[str, dict[str, Any]] = {}
    by_tf: dict[str, list[dict[str, Any]]] = {tf: [] for tf in TFS}
    supported: list[dict[str, Any]] = []
    for tf in TFS:
        for pid in PRIMITIVES:
            pack = primitive_pack(all_fills, tf=tf, pid=pid)
            primitives[f"{tf}::{pid}"] = pack
            by_tf[tf].append(pack)
            if pack.get("SUPPORTED"):
                supported.append(pack)
    primary = None
    for pid in PICK_ORDER:
        for tf in TF_PREFERENCE:
            pack = primitives.get(f"{tf}::{pid}") or {}
            if pack.get("SUPPORTED"):
                primary = {"primitive": pid, "tf": tf}
                break
        if primary is not None:
            break
    core_preserve = None
    if primary is not None:
        core_preserve = primitives[f"{primary['tf']}::{primary['primitive']}"].get("SUPPORT_GATES", {}).get("core_ok")
    fill_hash = set_hash(fill_tuples_e4(rows))
    return {
        "SIGNAL_N": len(rows),
        "EXECUTION_EVALUABLE_N": len(evaluable),
        "EXECUTION_UNEVALUABLE_N": len(uneval),
        "CORE_E4_FILL_N": len(core),
        "E4_NONFILL_N": len(e4_nonfill),
        "ASK_FALLBACK_ELIGIBLE_N": len(fallback_eligible),
        "ASK_FALLBACK_FILL_N": len(added),
        "TOTAL_FILL_N": len(all_fills),
        "ADDED_FILL_N": len(added),
        "FILLS_PER_DAY": (float(len(all_fills)) / float(N_DEV_DAYS)) if N_DEV_DAYS else None,
        "FILLS_BY_DAY": dict(sorted(by_day.items())),
        "CORE_PATH_SUMMARY": path_summary(core),
        "ADDED_PATH_SUMMARY": path_summary(added),
        "PATH_TYPE_N": {k: int(types.get(k) or 0) for k in PATH_TYPES},
        "GOOD_CONTINUATION_N": int(types.get("GOOD_CONTINUATION") or 0),
        "EARLY_FAILURE_N": int(types.get("EARLY_FAILURE") or 0),
        "DIP_THEN_RECOVERY_N": int(types.get("DIP_THEN_RECOVERY") or 0),
        "PROFIT_THEN_FAILURE_N": int(types.get("PROFIT_THEN_FAILURE") or 0),
        "1M_EXIT_PRIMITIVES": by_tf["1m"],
        "3M_EXIT_PRIMITIVES": by_tf["3m"],
        "5M_EXIT_PRIMITIVES": by_tf["5m"],
        "SUPPORTED_EXIT_PRIMITIVES": [
            {"primitive": p["primitive"], "tf": p["tf"], "FAILURE_CAPTURE": p.get("FAILURE_CAPTURE"), "GOOD_CONTINUATION_FALSE_EXIT": p.get("GOOD_CONTINUATION_FALSE_EXIT")}
            for p in supported
        ],
        "PRIMARY_EXIT_PRIMITIVE": primary,
        "CORE_WINNER_PRESERVATION": bool(core_preserve) if core_preserve is not None else False,
        "CORRECTED_FILL_HASH": fill_hash,
        "primitives": primitives,
    }


def decide(
    summary: dict[str, Any],
    *,
    signal_parity: bool,
    core_hash_ok: bool,
    leak_ok: bool,
    ni_ok: bool,
    future_n: int,
) -> dict[str, Any]:
    counts_ok = (
        int(summary.get("SIGNAL_N") or 0) == int(B1_SIGNAL_N_EXPECTED)
        and int(summary.get("EXECUTION_EVALUABLE_N") or 0) == int(CORRECTED_EVALUABLE_N_EXPECTED)
        and int(summary.get("EXECUTION_UNEVALUABLE_N") or 0) == int(CORRECTED_UNEVALUABLE_N_EXPECTED)
        and int(summary.get("CORE_E4_FILL_N") or 0) == int(CORE_E4_FILL_N_EXPECTED)
        and int(summary.get("E4_NONFILL_N") or 0) == int(E4_NONFILL_N_EXPECTED)
    )
    hash_ok = str(summary.get("CORRECTED_FILL_HASH") or "") == CORRECTED_FILL_HASH_EXPECTED and bool(core_hash_ok)
    causality_ok = int(future_n) == 0 and bool(leak_ok) and bool(ni_ok)
    added_n = int(summary.get("ADDED_FILL_N") or 0)
    material = added_n >= int(MATERIAL_ADDED_MIN_N)
    added = dict(summary.get("ADDED_PATH_SUMMARY") or {})
    mfe_pos_frac = added.get("MFE_POS_FRAC")
    mfe_med = added.get("MFE_MEDIAN")
    opp_ok = added_n > 0 and (
        (mfe_pos_frac is not None and float(mfe_pos_frac) >= float(ADDED_OPP_MFE_POS_FRAC_MIN))
        or (_finite(mfe_med) and float(mfe_med) > 0.0)
    )
    supported = list(summary.get("SUPPORTED_EXIT_PRIMITIVES") or [])
    primary = summary.get("PRIMARY_EXIT_PRIMITIVE")
    core_ok = bool(summary.get("CORE_WINNER_PRESERVATION")) if primary is not None else False
    mechanism = bool(material and supported and primary is not None and core_ok)
    if (not causality_ok) or (not signal_parity) or (not counts_ok) or (not hash_ok):
        case = "E"
        verdict = "SIMPLE_TECH_V26_INVALID"
        next_step = "STOP. Integrity, causality, or V25 baseline parity failed."
    elif not material:
        case = "C"
        verdict = "SIMPLE_TECH_V26_COVERAGE_EXPANSION_INSUFFICIENT"
        next_step = "STOP. Ask fallback did not add a material fill count."
    elif not opp_ok:
        case = "D"
        verdict = "SIMPLE_TECH_V26_ADDED_ENTRY_QUALITY_TOO_LOW"
        next_step = "STOP. Added fills lack meaningful MFE opportunity."
    elif not mechanism:
        case = "B"
        verdict = "SIMPLE_TECH_V26_COVERAGE_GAIN_EXIT_SEPARATION_FAILED"
        next_step = "STOP. Coverage rose but no EXIT primitive separated failure from CORE winners on multiple days."
    else:
        case = "A"
        verdict = "SIMPLE_TECH_V26_JOINT_COVERAGE_EXIT_MECHANISM_FOUND"
        next_step = (
            "V27: freeze E4_THEN_ASK_CROSS_W5 coverage execution and precommit one simple technical EXIT "
            f"from PRIMARY_EXIT_PRIMITIVE={primary}. Actual joint portfolio replay. No mixed-TF combination. "
            "No ENTRY-only precision return. Sizing still later."
        )
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": next_step,
        "V25_BASELINE_PARITY": bool(signal_parity and counts_ok and hash_ok),
        "JOINT_COVERAGE_EXIT_MECHANISM_FOUND": bool(case == "A"),
        "MATERIAL_ADDED": bool(material),
        "ADDED_OPP_OK": bool(opp_ok),
        "CORE_WINNER_PRESERVATION": bool(core_ok) if primary is not None else False,
        "PRIMARY_EXIT_PRIMITIVE": primary,
        "SUPPORTED_EXIT_PRIMITIVES": supported,
        "ENTRY_SIGNAL_CHANGED": False,
        "TRUE_OOS": False,
        "Q1_MATERIAL_COVERAGE": bool(material),
        "Q2_ADDED_MFE_OPPORTUNITY": bool(opp_ok),
        "Q3_FAILURE_IDENTIFIABLE": bool(supported),
        "Q4_CORE_WINNERS_PRESERVED": bool(core_ok) if primary is not None else False,
        "Q5_JOINT_ARCHITECTURE_VALUE": bool(case == "A"),
    }
