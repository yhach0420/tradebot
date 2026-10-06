"""Canonical C3 retrain decision, coverage diagnostic, legacy comparison."""
from __future__ import annotations

from typing import Any, Optional

from research.c3_canonical_retrain import LEGACY_MATCHED_TOP3_DELTA
from research.canonical_entry_performance_rebase.analyze import _f, ranking_no_backfill
from research.entry_objective_redesign_c3.analyze import paired_daily
from research.entry_objective_redesign_c3.oof import SCORE_KEY


def spec_features_ok(row: dict[str, Any], feats: list[str]) -> bool:
    return all(_f(row.get(f)) is not None for f in feats)


def matched_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not r.get("executable_at_t0"):
            continue
        if _f(r.get("current_score")) is None:
            continue
        if _f(r.get(SCORE_KEY)) is None:
            continue
        out.append(r)
    return out


def current_actual(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if _f(r.get("current_score")) is not None]


def c3_actual(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("executable_at_t0") and _f(r.get(SCORE_KEY)) is not None]


def common_diagnostic(
    attached: list[dict[str, Any]],
    fits: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    common = []
    for r in attached:
        if not r.get("executable_at_t0"):
            continue
        if _f(r.get("current_score")) is None:
            continue
        fit = fits.get(str(r.get("date"))) or {}
        feats = list(fit.get("features") or [])
        if feats and not spec_features_ok(r, feats):
            continue
        common.append(r)
    native_cur = ranking_no_backfill(current_actual(attached), "current_score")
    native_c3 = ranking_no_backfill(c3_actual(attached), SCORE_KEY)
    common_cur = ranking_no_backfill(common, "current_score")
    common_c3 = ranking_no_backfill(common, SCORE_KEY)
    native_paired = paired_daily(native_cur, native_c3)
    common_paired = paired_daily(common_cur, common_c3)
    native_d = _f(native_paired.get("TOP3_DELTA_MEAN"))
    common_d = _f(common_paired.get("TOP3_DELTA_MEAN"))
    depends = bool(native_d is not None and native_d > 0 and (common_d is None or common_d <= 0))
    contrib = (native_d - common_d) if native_d is not None and common_d is not None else None
    return {
        "COMMON_DIAGNOSTIC_ROWS": len(common),
        "SPEC_NATIVE_TOP3_DELTA": native_d,
        "COMMON_POP_TOP3_DELTA": common_d,
        "COVERAGE_EDGE_CONTRIBUTION": contrib,
        "EDGE_DEPENDS_ON_COVERAGE_SELECTION": depends,
        "native_current": native_cur,
        "native_c3": native_c3,
        "common_current": common_cur,
        "common_c3": common_c3,
        "native_paired": {k: v for k, v in native_paired.items() if k != "days"},
        "common_paired": {k: v for k, v in common_paired.items() if k != "days"},
        "note": (
            "SPEC_NATIVE compares CURRENT on CURRENT-scorable vs C3 on C3-scorable. "
            "COMMON_POP is Exact executable AND CURRENT score AND selected-spec features. Diagnostic only."
        ),
    }


def legacy_compare(*, delta: Optional[float], pos: int, neg: int, ex3: Optional[float]) -> str:
    if delta is None or delta <= 0:
        return "NOT_REPRODUCED"
    leg = float(LEGACY_MATCHED_TOP3_DELTA)
    ratio = float(delta) / leg if leg else None
    if ratio is not None and ratio >= 1.25 and pos > neg:
        return "REPRODUCED_STRONGER"
    if ratio is not None and ratio <= 0.75:
        return "REPRODUCED_WEAKER"
    return "REPRODUCED_SIMILAR"


def oof_exact_converts(oof_pack: dict[str, Any] | None, a2_pack: dict[str, Any] | None) -> bool:
    if not oof_pack or not a2_pack:
        return False
    c3_pnl = _f(oof_pack.get("PnL") if oof_pack.get("PnL") is not None else oof_pack.get("pnl"))
    a2_pnl = _f(a2_pack.get("PnL") if a2_pack.get("PnL") is not None else a2_pack.get("pnl"))
    return bool(c3_pnl is not None and a2_pnl is not None and float(c3_pnl) > float(a2_pnl))


def decide(
    *,
    integrity_ok: bool,
    gate: dict[str, Any],
    exact_ran: bool,
    oof_exact_ran: bool,
    success: dict[str, Any] | None,
    oof_converts: bool,
    adverse: Optional[bool],
) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "VERDICT": "C3_CANONICAL_RETRAIN_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "CASE": None,
            "note": "Causality / TARGET V4 / A0-A2 parity integrity failed. Observed Exact not adopted.",
        }
    if not gate.get("OOF_RANKING_GATE_PASS"):
        if gate.get("core_edge_fail"):
            return {
                "VERDICT": "C3_CANONICAL_EDGE_NOT_REPRODUCED",
                "NEXT_RESEARCH": "ENTRY_OBJECTIVE_MODEL_ARCHITECTURE_RECONSIDERATION",
                "CASE": "A",
                "note": "OOF Top3 edge vs CURRENT did not reproduce on Canonical training. Execution-aware not allowed yet.",
            }
        return {
            "VERDICT": "C3_CANONICAL_EDGE_NOT_ROBUST",
            "NEXT_RESEARCH": "ENTRY_OBJECTIVE_MODEL_ARCHITECTURE_RECONSIDERATION",
            "CASE": "A",
            "note": "Some Top-of-rank improvement appeared, but frozen A-K robustness gates failed. Execution-aware not allowed yet.",
        }
    if not oof_exact_ran or not exact_ran:
        return {
            "VERDICT": "C3_CANONICAL_RETRAIN_INTEGRITY_FAILED",
            "NEXT_RESEARCH": "NONE",
            "CASE": None,
            "note": "OOF ranking gate passed but Exact Dual-Lane did not complete.",
        }
    s = success or {}
    if s.get("HISTORICAL_ENTRY_CANDIDATE"):
        return {
            "VERDICT": "C3_CANONICAL_HISTORICAL_ENTRY_CANDIDATE",
            "NEXT_RESEARCH": "NONE",
            "CASE": "D",
            "note": "HISTORICAL_DEVELOPMENT_CANDIDATE_ONLY. TRUE_OOS=false. Runtime implementation forbidden this run.",
        }
    if not oof_converts and adverse is True:
        return {
            "VERDICT": "C3_CANONICAL_EDGE_EXECUTION_MISMATCH",
            "NEXT_RESEARCH": "EXECUTION_AWARE_OBJECTIVE",
            "CASE": "B",
            "note": "OOF ranking gate passed; OOF-STITCHED Exact failed to convert; fill-conditioned return worse than CURRENT.",
        }
    return {
        "VERDICT": "C3_CANONICAL_EDGE_PORTFOLIO_FAIL",
        "NEXT_RESEARCH": "SEPARATE_CAUSAL_AUDIT",
        "CASE": "C",
        "note": "OOF ranking gate passed and Exact failed, without passive-fill adverse selection. Do not automatically start execution-aware work.",
    }
