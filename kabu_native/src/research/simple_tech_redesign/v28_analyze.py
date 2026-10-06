"""V28 ADDED K6 vs session-close control. No K search. No 5m/1m BB PnL ranking."""
from __future__ import annotations

from typing import Any, Optional

from research.am_entry_profit_improvement import ELIGIBLE_DAYS
from research.simple_tech_entry_family.v13_analyze import set_hash
from research.simple_tech_exit_family.v14_analyze import _finite
from research.simple_tech_redesign.v22_analyze import fill_tuples_e4
from research.simple_tech_redesign.v27_analyze import research_fill_tuples
from research.simple_tech_redesign.v28_spec import (
    ADDED_FILL_N_EXPECTED,
    B1_SIGNAL_N_EXPECTED,
    BAD_PATHS,
    CORE_E4_FILL_N_EXPECTED,
    CORRECTED_EVALUABLE_N_EXPECTED,
    CORRECTED_FILL_HASH_EXPECTED,
    DIP_PATH,
    GOOD_PATH,
    MIN_TECH_EXIT_DAYS,
    MIN_TECH_EXIT_SYMBOLS,
    PATH_TYPES,
    PERSISTENCE_K,
    RESEARCH_FILL_SET_HASH_EXPECTED,
    TOTAL_RESEARCH_FILL_N_EXPECTED,
    YEN_PARITY_TOL,
)
from research.simple_tech_strategy.v20_analyze import concentration_yen, pnl_pack
from replay.pnl_yen import summarize_pnl_yen_100


def _fills(rows: list[dict[str, Any]], role: Optional[str] = None) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not r.get("actual_filled"):
            continue
        if role is not None and str(r.get("fill_role") or "") != role:
            continue
        out.append(r)
    return out


def _arm_rows(fills: list[dict[str, Any]], arm: str) -> list[dict[str, Any]]:
    out = []
    for r in fills:
        ex = dict(r.get(arm) or {})
        rec = {
            "date": r.get("date"),
            "symbol": str(r.get("symbol") or "").replace(".T", ""),
            "t0": r.get("t0"),
            "fill_t": r.get("fill_t"),
            "fill_price": r.get("fill_price"),
            "fill_role": r.get("fill_role"),
            "path_type": r.get("path_type"),
            "actual_exit_quote_time": ex.get("exit_t"),
            "exit_bid": ex.get("exit_bid"),
            "pnl_yen_100": ex.get("pnl_yen_100"),
            "exit_reason": ex.get("reason"),
            "exit_miss": bool(ex.get("miss")),
        }
        out.append(rec)
    return out


def _pf_num(v: Any) -> Optional[float]:
    if v is None:
        return None
    if isinstance(v, str) and str(v).lower() == "inf":
        return float("inf")
    if _finite(v):
        return float(v)
    return None


def economics(fills: list[dict[str, Any]], arm: str) -> dict[str, Any]:
    rows = _arm_rows(fills, arm)
    days = list(ELIGIBLE_DAYS)
    pnl = pnl_pack(rows, days)
    conc = concentration_yen(rows, days)
    miss_n = sum(1 for r in rows if r.get("exit_miss"))
    return {
        "TRADE_N": len(rows),
        "EXIT_MISS_N": int(miss_n),
        "TOTAL_PNL_YEN": pnl.get("TOTAL_PNL_YEN_100"),
        "AVG_PNL_YEN": pnl.get("AVG_PNL_PER_TRADE"),
        "MEDIAN_PNL_YEN": pnl.get("MEDIAN_PNL_PER_TRADE"),
        "WIN_RATE": pnl.get("WIN_RATE"),
        "GROSS_PROFIT": pnl.get("GROSS_PROFIT"),
        "GROSS_LOSS": pnl.get("GROSS_LOSS"),
        "PF": pnl.get("PROFIT_FACTOR"),
        "REALIZED_MAX_DD": pnl.get("REALIZED_CLOSE_EQUITY_MAX_DD_YEN"),
        "POSITIVE_DAY_N": conc.get("POSITIVE_DAY_N"),
        "NEGATIVE_DAY_N": conc.get("NEGATIVE_DAY_N"),
        "BEST_DAY": conc.get("BEST_DAY"),
        "BEST_DAY_PNL": conc.get("BEST_DAY_PNL"),
        "WORST_DAY_PNL": conc.get("WORST_DAY_PNL"),
        "EX_BEST_DAY_TOTAL_PNL": conc.get("EX_BEST_DAY_TOTAL_PNL"),
        "EX_TOP3_DAY_TOTAL_PNL": conc.get("EX_TOP3_DAY_TOTAL_PNL"),
        "DROP_TOP_SYMBOL_TOTAL_PNL": conc.get("DROP_TOP_SYMBOL_TOTAL_PNL"),
        "LODO_MIN_TOTAL_PNL": conc.get("LODO_MIN_TOTAL_PNL"),
        "LODO_MEDIAN_TOTAL_PNL": conc.get("LODO_MEDIAN_TOTAL_PNL"),
        "daily": conc.get("daily"),
        "WIN_N": pnl.get("WIN_N"),
        "LOSS_N": pnl.get("LOSS_N"),
    }


def _delta(treat: dict[str, Any], ctrl: dict[str, Any], key: str) -> Optional[float]:
    a, b = treat.get(key), ctrl.get(key)
    if key == "PF":
        ta, tb = _pf_num(a), _pf_num(b)
        if ta is None or tb is None:
            return None
        if ta == float("inf") and tb == float("inf"):
            return 0.0
        if ta == float("inf"):
            return float("inf")
        if tb == float("inf"):
            return float("-inf")
        return float(ta) - float(tb)
    if not _finite(a) or not _finite(b):
        return None
    return float(a) - float(b)


def path_exit_rates(added: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ptype in PATH_TYPES:
        grp = [r for r in added if str(r.get("path_type") or "") == ptype]
        tech = [r for r in grp if str((r.get("treatment_exit") or {}).get("reason") or "") == "TECHNICAL_EXIT"]
        n = len(grp)
        tn = len(tech)
        out[ptype] = {
            "n": n,
            "TECH_EXIT_N": tn,
            "TECH_EXIT_RATE": (float(tn) / float(n)) if n else None,
            "SESSION_CLOSE_EXIT_N": n - tn,
        }
    return out


def path_pnl_delta(added: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for ptype in PATH_TYPES:
        grp = [r for r in added if str(r.get("path_type") or "") == ptype]
        c = summarize_pnl_yen_100(_arm_rows(grp, "control_exit"))
        t = summarize_pnl_yen_100(_arm_rows(grp, "treatment_exit"))
        out[ptype] = {
            "n": len(grp),
            "control_pnl": c.get("total_pnl_yen_100"),
            "treatment_pnl": t.get("total_pnl_yen_100"),
            "delta_pnl": (
                float(t.get("total_pnl_yen_100") or 0.0) - float(c.get("total_pnl_yen_100") or 0.0)
                if grp
                else None
            ),
        }
    return out


def _close_yen(a: Any, b: Any) -> bool:
    if not _finite(a) and not _finite(b):
        return True
    if not _finite(a) or not _finite(b):
        return False
    return abs(float(a) - float(b)) <= float(YEN_PARITY_TOL)


def core_parity(core: list[dict[str, Any]]) -> dict[str, Any]:
    entry_ok = len(core) == int(CORE_E4_FILL_N_EXPECTED)
    exit_ok = True
    pnl_ok = True
    tech_n = 0
    for r in core:
        c = dict(r.get("control_exit") or {})
        t = dict(r.get("treatment_exit") or {})
        if str(t.get("reason") or "") == "TECHNICAL_EXIT":
            tech_n += 1
            exit_ok = False
        if str(c.get("reason") or "") != "SESSION_CLOSE" or str(t.get("reason") or "") != "SESSION_CLOSE":
            exit_ok = False
        ct, tt = c.get("exit_t"), t.get("exit_t")
        if (_finite(ct) or _finite(tt)) and (
            (not _finite(ct)) or (not _finite(tt)) or abs(float(ct) - float(tt)) > 1e-9
        ):
            exit_ok = False
        if not _close_yen(c.get("exit_bid"), t.get("exit_bid")):
            exit_ok = False
        if not _close_yen(c.get("pnl_yen_100"), t.get("pnl_yen_100")):
            pnl_ok = False
    return {
        "CORE_ENTRY_PARITY": bool(entry_ok),
        "CORE_EXIT_CONTROL_PARITY": bool(exit_ok and tech_n == 0),
        "CORE_PNL_PARITY": bool(pnl_ok and exit_ok),
        "CORE_TECH_EXIT_N": int(tech_n),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fills = _fills(rows)
    core = _fills(rows, "CORE")
    added = _fills(rows, "ADDED")
    added_ctrl = economics(added, "control_exit")
    added_treat = economics(added, "treatment_exit")
    core_econ = economics(core, "control_exit")
    core_treat = economics(core, "treatment_exit")
    combined_rows = _arm_rows(core, "control_exit") + _arm_rows(added, "treatment_exit")
    combined = economics(
        [
            {
                **r,
                "control_exit": {
                    "exit_t": r.get("actual_exit_quote_time"),
                    "exit_bid": r.get("exit_bid"),
                    "pnl_yen_100": r.get("pnl_yen_100"),
                    "reason": r.get("exit_reason"),
                    "miss": r.get("exit_miss"),
                },
            }
            for r in combined_rows
        ],
        "control_exit",
    )
    tech = [r for r in added if str((r.get("treatment_exit") or {}).get("reason") or "") == "TECHNICAL_EXIT"]
    sess = [r for r in added if str((r.get("treatment_exit") or {}).get("reason") or "") != "TECHNICAL_EXIT"]
    tech_days = {str(r.get("date") or "") for r in tech}
    tech_syms = {str(r.get("symbol") or "").replace(".T", "") for r in tech}
    k6_n = sum(1 for r in added if (r.get("k6") or {}).get("k6_reached"))
    recovered_n = int(sum(int((r.get("k6") or {}).get("k6_recovered_before_trigger_n") or 0) for r in added))
    episode_n = int(sum(int((r.get("k6") or {}).get("loss_episode_n") or 0) for r in added))
    parity = core_parity(core)
    deltas = {
        "DELTA_TOTAL_PNL": _delta(added_treat, added_ctrl, "TOTAL_PNL_YEN"),
        "DELTA_PF": _delta(added_treat, added_ctrl, "PF"),
        "DELTA_MAX_DD": _delta(added_treat, added_ctrl, "REALIZED_MAX_DD"),
        "DELTA_EX_BEST_DAY": _delta(added_treat, added_ctrl, "EX_BEST_DAY_TOTAL_PNL"),
        "DELTA_EX_TOP3_DAY": _delta(added_treat, added_ctrl, "EX_TOP3_DAY_TOTAL_PNL"),
        "DELTA_DROP_TOP_SYMBOL": _delta(added_treat, added_ctrl, "DROP_TOP_SYMBOL_TOTAL_PNL"),
    }
    rates = path_exit_rates(added)
    path_delta = path_pnl_delta(added)
    return {
        "SIGNAL_N": len(rows),
        "EXECUTION_EVALUABLE_N": sum(1 for r in rows if r.get("executable_signal")),
        "CORE_FILL_N": len(core),
        "ADDED_FILL_N": len(added),
        "TOTAL_RESEARCH_FILL_N": len(fills),
        "CORRECTED_FILL_HASH": set_hash(fill_tuples_e4(rows)),
        "RESEARCH_FILL_SET_HASH": set_hash(research_fill_tuples(rows)),
        "PERSISTENCE_K": int(PERSISTENCE_K),
        "ADDED_N": len(added),
        "TECH_EXIT_N": len(tech),
        "SESSION_CLOSE_EXIT_N": len(sess),
        "TECH_EXIT_DAY_N": len(tech_days),
        "TECH_EXIT_SYMBOL_N": len(tech_syms),
        "LOSS_EPISODE_COUNT": episode_n,
        "K6_REACHED_N": k6_n,
        "K6_RECOVERED_BEFORE_TRIGGER_N": recovered_n,
        "PATH_TYPE_EXIT_RATES": rates,
        "PATH_TYPE_PNL_DELTA": path_delta,
        "ADDED_CONTROL_ECONOMICS": added_ctrl,
        "ADDED_TREATMENT_ECONOMICS": added_treat,
        "CORE_CONTROL_ECONOMICS": core_econ,
        "CORE_TREATMENT_ECONOMICS": core_treat,
        "COMBINED_232_ECONOMICS": combined,
        **deltas,
        **parity,
        "Q1_BAD_TECH_EXIT_RATE": (
            (
                int((rates.get("EARLY_FAILURE") or {}).get("TECH_EXIT_N") or 0)
                + int((rates.get("PROFIT_THEN_FAILURE") or {}).get("TECH_EXIT_N") or 0)
            )
            / float(sum(1 for r in added if str(r.get("path_type") or "") in BAD_PATHS))
            if any(str(r.get("path_type") or "") in BAD_PATHS for r in added)
            else None
        ),
        "Q2_GOOD_TECH_EXIT_RATE": (rates.get(GOOD_PATH) or {}).get("TECH_EXIT_RATE"),
        "Q2_DIP_TECH_EXIT_RATE": (rates.get(DIP_PATH) or {}).get("TECH_EXIT_RATE"),
    }


def decide(
    summary: dict[str, Any],
    *,
    signal_parity: bool,
    fill_identity: bool,
    leak_ok: bool,
    ni_ok: bool,
    core_parity_ok: bool,
    exit_miss_ok: bool,
) -> dict[str, Any]:
    counts_ok = (
        int(summary.get("SIGNAL_N") or 0) == int(B1_SIGNAL_N_EXPECTED)
        and int(summary.get("EXECUTION_EVALUABLE_N") or 0) == int(CORRECTED_EVALUABLE_N_EXPECTED)
        and int(summary.get("CORE_FILL_N") or 0) == int(CORE_E4_FILL_N_EXPECTED)
        and int(summary.get("ADDED_FILL_N") or 0) == int(ADDED_FILL_N_EXPECTED)
        and int(summary.get("TOTAL_RESEARCH_FILL_N") or 0) == int(TOTAL_RESEARCH_FILL_N_EXPECTED)
        and int(summary.get("PERSISTENCE_K") or 0) == int(PERSISTENCE_K)
    )
    hash_ok = (
        str(summary.get("CORRECTED_FILL_HASH") or "") == CORRECTED_FILL_HASH_EXPECTED
        and str(summary.get("RESEARCH_FILL_SET_HASH") or "") == RESEARCH_FILL_SET_HASH_EXPECTED
        and bool(fill_identity)
    )
    integrity = bool(leak_ok and ni_ok and signal_parity and counts_ok and hash_ok and core_parity_ok and exit_miss_ok)
    tech_n = int(summary.get("TECH_EXIT_N") or 0)
    day_n = int(summary.get("TECH_EXIT_DAY_N") or 0)
    sym_n = int(summary.get("TECH_EXIT_SYMBOL_N") or 0)
    d_pnl = summary.get("DELTA_TOTAL_PNL")
    d_pf = summary.get("DELTA_PF")
    d_dd = summary.get("DELTA_MAX_DD")
    d_ex = summary.get("DELTA_EX_BEST_DAY")
    d_top3 = summary.get("DELTA_EX_TOP3_DAY")
    path_delta = dict(summary.get("PATH_TYPE_PNL_DELTA") or {})
    good_dip_delta = float((path_delta.get(GOOD_PATH) or {}).get("delta_pnl") or 0.0) + float(
        (path_delta.get(DIP_PATH) or {}).get("delta_pnl") or 0.0
    )
    good_rate = summary.get("Q2_GOOD_TECH_EXIT_RATE")
    dip_rate = summary.get("Q2_DIP_TECH_EXIT_RATE")
    winner_fired = (float(good_rate or 0.0) > 0.0) or (float(dip_rate or 0.0) > 0.0)
    multi = day_n >= int(MIN_TECH_EXIT_DAYS) and sym_n >= int(MIN_TECH_EXIT_SYMBOLS)
    pnl_material = _finite(d_pnl) and abs(float(d_pnl)) > 1e-9
    pf_material = d_pf is not None and abs(float(d_pf) if d_pf not in (float("inf"), float("-inf")) else 1.0) > 1e-12

    if not integrity:
        case = "E"
        verdict = "SIMPLE_TECH_V28_INVALID"
        next_step = "STOP. Identity, causality, CORE parity, or non-interference failed."
    elif tech_n <= 0 or (not multi) or (not pnl_material and not pf_material):
        case = "D"
        verdict = "SIMPLE_TECH_V28_PERSISTENCE_EXIT_TOO_WEAK"
        next_step = (
            "STOP. K6 one-shot was too rare or too late to move ADDED economics. "
            "Do not retune K on these 18 days."
        )
    elif _finite(d_pnl) and float(d_pnl) < 0.0 and good_dip_delta < 0.0 and winner_fired:
        case = "C"
        verdict = "SIMPLE_TECH_V28_PERSISTENCE_EXIT_WINNER_HARM"
        next_step = (
            "STOP. K6 cut GOOD/DIP winners enough to worsen ADDED economics. "
            "Do not retune K on these 18 days."
        )
    elif (
        _finite(d_pnl)
        and float(d_pnl) > 0.0
        and d_pf is not None
        and float(d_pf) > 0.0
        and _finite(d_dd)
        and float(d_dd) >= 0.0
        and _finite(d_ex)
        and float(d_ex) > 0.0
        and _finite(d_top3)
        and float(d_top3) > 0.0
        and multi
    ):
        case = "A"
        verdict = "SIMPLE_TECH_V28_FALLBACK_PROTECTIVE_EXIT_SUPPORTED"
        next_step = (
            "Keep development candidate E4_THEN_ASK_CROSS_W5 + ADDED_ONLY_3M_EMA_PERSISTENCE_K6. "
            "Not a final EXIT freeze. CORE technical EXIT remains a separate study. Sizing later."
        )
    else:
        case = "B"
        verdict = "SIMPLE_TECH_V28_PROTECTIVE_EXIT_ECONOMICALLY_INSUFFICIENT"
        next_step = (
            "STOP. Protective EXIT did not improve robust ADDED economics under the precommitted gates. "
            "Do not retune K on these 18 days."
        )
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": next_step,
        "V27_FILL_IDENTITY_PARITY": bool(hash_ok and counts_ok),
        "PERSISTENCE_K": int(PERSISTENCE_K),
        "TECH_EXIT_N": tech_n,
        "SESSION_CLOSE_EXIT_N": summary.get("SESSION_CLOSE_EXIT_N"),
        "TECH_EXIT_DAY_N": day_n,
        "TECH_EXIT_SYMBOL_N": sym_n,
        "DELTA_TOTAL_PNL": d_pnl,
        "DELTA_PF": d_pf,
        "DELTA_MAX_DD": d_dd,
        "DELTA_EX_BEST_DAY": d_ex,
        "DELTA_EX_TOP3_DAY": d_top3,
        "DELTA_DROP_TOP_SYMBOL": summary.get("DELTA_DROP_TOP_SYMBOL"),
        "CORE_ENTRY_PARITY": summary.get("CORE_ENTRY_PARITY"),
        "CORE_EXIT_CONTROL_PARITY": summary.get("CORE_EXIT_CONTROL_PARITY"),
        "CORE_PNL_PARITY": summary.get("CORE_PNL_PARITY"),
        "ENTRY_CHANGED": False,
        "SIZING_CHANGED": False,
        "TRUE_OOS": False,
        "EXIT_FROZEN": False,
        "Q1_BAD_CAPTURE": summary.get("Q1_BAD_TECH_EXIT_RATE"),
        "Q2_GOOD_RATE": summary.get("Q2_GOOD_TECH_EXIT_RATE"),
        "Q2_DIP_RATE": summary.get("Q2_DIP_TECH_EXIT_RATE"),
        "Q3_ADDED_IMPROVED": bool(case == "A"),
    }
