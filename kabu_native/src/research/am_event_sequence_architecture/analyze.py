"""Sequence incremental vs frozen bases. CASE A-E. Diagnostics only for SEQ scores."""
from __future__ import annotations

from typing import Any

import numpy as np

from research.am_entry_information_expansion import CLASS_LOSS, CLASS_NEUTRAL, CLASS_WIN, TARGET
from research.am_entry_profit_improvement.metrics import _pf_num
from research.am_entry_temporal_regime_information.analyze import net_pf_dd_beat
from research.am_event_sequence_architecture import B0, B1, S0, S1, SEQ_JOINT_SCORE
from research.canonical_entry_performance_rebase.analyze import _f
from research.entry_objective_redesign_c3.oof import spearman


def _d(a: Any, b: Any) -> Any:
    try:
        return float(a) - float(b)
    except (TypeError, ValueError):
        return None


def pos_minus_neg(arm: dict[str, Any]) -> int:
    return int(arm.get("PAIRED_POS_DAYS") or 0) - int(arm.get("PAIRED_NEG_DAYS") or 0)


def increment_seq_vs_base(seq: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    return {
        "DELTA_NET_SEQ": _d(seq.get("OVERLAY_NET_PNL"), base.get("OVERLAY_NET_PNL")),
        "DELTA_PF_SEQ": _d(_pf_num(seq.get("OVERLAY_PF")), _pf_num(base.get("OVERLAY_PF"))),
        "DELTA_DD_SEQ": _d(seq.get("OVERLAY_MAX_DD"), base.get("OVERLAY_MAX_DD")),
        "DELTA_PAIRED_MEDIAN_SEQ": _d(seq.get("PAIRED_MEDIAN_DAILY_DELTA"), base.get("PAIRED_MEDIAN_DAILY_DELTA")),
        "DELTA_POS_MINUS_NEG_SEQ": float(pos_minus_neg(seq) - pos_minus_neg(base)),
        "DELTA_EX_BEST_SEQ": _d(seq.get("EX_BEST_DAY_PNL_DELTA"), base.get("EX_BEST_DAY_PNL_DELTA")),
        "DELTA_EX_TOP3_SEQ": _d(seq.get("EX_TOP3_DAYS_PNL_DELTA"), base.get("EX_TOP3_DAYS_PNL_DELTA")),
    }


def _pos(v: Any) -> bool:
    try:
        return v is not None and float(v) > 0.0
    except (TypeError, ValueError):
        return False


def _nonworse(v: Any) -> bool:
    try:
        return v is not None and float(v) >= 0.0
    except (TypeError, ValueError):
        return False


def _dd_nonworse(seq: dict[str, Any], base: dict[str, Any]) -> bool:
    try:
        return abs(float(seq.get("OVERLAY_MAX_DD") or 0.0)) <= abs(float(base.get("OVERLAY_MAX_DD") or 0.0))
    except (TypeError, ValueError):
        return False


def pair_incremental_supported(seq: dict[str, Any], base: dict[str, Any]) -> bool:
    inc = increment_seq_vs_base(seq, base)
    econ = (
        _pos(inc.get("DELTA_NET_SEQ"))
        and _pos(inc.get("DELTA_PF_SEQ"))
        and _pos(inc.get("DELTA_PAIRED_MEDIAN_SEQ"))
        and _pos(inc.get("DELTA_POS_MINUS_NEG_SEQ"))
    )
    if not econ:
        return False
    tail_ok = 0
    if _dd_nonworse(seq, base):
        tail_ok += 1
    if _nonworse(inc.get("DELTA_EX_BEST_SEQ")):
        tail_ok += 1
    if _nonworse(inc.get("DELTA_EX_TOP3_SEQ")):
        tail_ok += 1
    return tail_ok >= 2


def pair_degrades(seq: dict[str, Any], base: dict[str, Any]) -> bool:
    inc = increment_seq_vs_base(seq, base)
    net = inc.get("DELTA_NET_SEQ")
    pf = inc.get("DELTA_PF_SEQ")
    try:
        return float(net) < 0.0 and float(pf) < 0.0
    except (TypeError, ValueError):
        return False


def decide_case(
    *,
    integrity_ok: bool,
    base_parity: bool,
    arms: list[dict[str, Any]],
    current_net: float,
    current_pf: float,
    current_dd: float,
    sequence_incremental_supported: bool,
) -> dict[str, Any]:
    if not integrity_ok or not base_parity:
        return {
            "CASE": "E",
            "VERDICT": "AM_EVENT_SEQUENCE_INTEGRITY_FAILED",
            "NEXT": "STOP",
        }
    by = {str(a.get("architecture_id")): a for a in arms}
    s0 = by.get(S0) or {}
    s1 = by.get(S1) or {}
    b0 = by.get(B0) or {}
    b1 = by.get(B1) or {}
    if any(bool(a.get("ARM_PASS")) for a in (s0, s1) if a):
        return {
            "CASE": "A",
            "VERDICT": "AM_EVENT_SEQUENCE_INCREMENTAL_AUGMENT_SUPPORTED",
            "NEXT": "AM_EVENT_SEQUENCE_AUGMENT_FREEZE_REVIEW",
        }
    s_arms = [a for a in (s0, s1) if a]
    any_beat_current = any(net_pf_dd_beat(a, current_net, current_pf, current_dd) for a in s_arms)
    if sequence_incremental_supported and any_beat_current:
        return {
            "CASE": "B",
            "VERDICT": "AM_EVENT_SEQUENCE_EDGE_PARTIAL_NOT_FULLY_ROBUST",
            "NEXT": "AM_ENTRY_ARCHITECTURE_FINAL_REASSESSMENT",
        }
    if sequence_incremental_supported:
        return {
            "CASE": "B",
            "VERDICT": "AM_EVENT_SEQUENCE_EDGE_PARTIAL_NOT_FULLY_ROBUST",
            "NEXT": "AM_ENTRY_ARCHITECTURE_FINAL_REASSESSMENT",
        }
    if pair_degrades(s0, b0) and pair_degrades(s1, b1):
        return {
            "CASE": "D",
            "VERDICT": "AM_EVENT_SEQUENCE_INTEGRATION_DEGRADES_EDGE",
            "NEXT": "AM_ENTRY_ARCHITECTURE_FINAL_REASSESSMENT",
        }
    return {
        "CASE": "C",
        "VERDICT": "AM_EVENT_SEQUENCE_NOT_INCREMENTAL",
        "NEXT": "AM_ENTRY_ARCHITECTURE_FINAL_REASSESSMENT",
    }


def expected_match(arm: dict[str, Any], expected: dict[str, Any], *, abs_tol: float) -> bool:
    if int(arm.get("PAIRED_POS_DAYS") or -1) != int(expected["PAIRED_POS_DAYS"]):
        return False
    if int(arm.get("PAIRED_NEG_DAYS") or -1) != int(expected["PAIRED_NEG_DAYS"]):
        return False
    checks = (
        ("OVERLAY_NET_PNL", abs_tol),
        ("OVERLAY_PF", 1e-12),
        ("OVERLAY_MAX_DD", abs_tol),
        ("PAIRED_MEDIAN_DAILY_DELTA", abs_tol),
        ("EX_BEST_DAY_PNL_DELTA", abs_tol),
        ("EX_TOP3_DAYS_PNL_DELTA", abs_tol),
    )
    for key, tol in checks:
        a = _f(arm.get(key))
        b = _f(expected.get(key))
        if a is None or b is None:
            return False
        if abs(float(a) - float(b)) > float(tol):
            return False
    return True


def _cls_rank(label: Any) -> float | None:
    s = str(label or "")
    if s == CLASS_WIN:
        return 1.0
    if s == CLASS_NEUTRAL:
        return 0.0
    if s == CLASS_LOSS:
        return -1.0
    return None


def seq_class_diagnostic(rows: list[dict[str, Any]]) -> dict[str, Any]:
    xs = []
    ys = []
    med = {CLASS_WIN: [], CLASS_LOSS: [], CLASS_NEUTRAL: []}
    for r in rows:
        sc = _f(r.get(SEQ_JOINT_SCORE))
        lab = str(r.get(TARGET) or "")
        cr = _cls_rank(lab)
        if sc is None or cr is None:
            continue
        xs.append(float(sc))
        ys.append(float(cr))
        if lab in med:
            med[lab].append(float(sc))
    return {
        "SEQ_OOF_SPEARMAN": spearman(xs, ys) if xs else None,
        "SEQ_OOF_N": len(xs),
        "SEQ_WIN_MEDIAN": float(np.median(med[CLASS_WIN])) if med[CLASS_WIN] else None,
        "SEQ_LOSS_MEDIAN": float(np.median(med[CLASS_LOSS])) if med[CLASS_LOSS] else None,
        "SEQ_NEUTRAL_MEDIAN": float(np.median(med[CLASS_NEUTRAL])) if med[CLASS_NEUTRAL] else None,
    }


def _fill_rate(keys: set[str], fills: set[str]) -> float | None:
    if not keys:
        return None
    return float(len(keys & fills) / len(keys))


def _mean_pnl(keys: set[str], pnl: dict[str, float]) -> float | None:
    xs = [pnl[k] for k in keys if k in pnl]
    if not xs:
        return None
    return float(np.mean(xs))


def selection_changed(old_arm: dict[str, Any], new_arm: dict[str, Any], pair_name: str) -> dict[str, Any]:
    old_adm = {str(a.get("row_key") or "") for a in (old_arm.get("augment_admissions") or [])}
    new_adm = {str(a.get("row_key") or "") for a in (new_arm.get("augment_admissions") or [])}
    old_adm.discard("")
    new_adm.discard("")
    old_fill = {str(a.get("row_key") or "") for a in (old_arm.get("augment_fills") or [])}
    new_fill = {str(a.get("row_key") or "") for a in (new_arm.get("augment_fills") or [])}
    old_fill.discard("")
    new_fill.discard("")
    pnl = {}
    for t in list(old_arm.get("augment_trades") or []) + list(new_arm.get("augment_trades") or []):
        k = str(t.get("row_key") or "")
        if k:
            pnl[k] = float(t.get("pnl_yen_100") or 0.0)
    old_only = old_adm - new_adm
    new_only = new_adm - old_adm
    return {
        "pair": pair_name,
        "SELECTION_CHANGED_N": len(old_only | new_only),
        "OLD_ONLY_N": len(old_only),
        "NEW_ONLY_N": len(new_only),
        "OLD_ONLY_FILL_RATE": _fill_rate(old_only, old_fill),
        "NEW_ONLY_FILL_RATE": _fill_rate(new_only, new_fill),
        "OLD_ONLY_COND_PNL": _mean_pnl(old_only, pnl),
        "NEW_ONLY_COND_PNL": _mean_pnl(new_only, pnl),
    }
