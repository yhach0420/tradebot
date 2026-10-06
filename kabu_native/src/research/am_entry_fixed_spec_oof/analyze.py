"""Fixed-spec economic gate, development ranking, CASE A/B/C. No gate relaxation."""
from __future__ import annotations

from typing import Any

from research.am_entry_profit_improvement.metrics import _pf_num, success_gate


def pass_rank_key(row: dict[str, Any]) -> tuple:
    ext = row.get("EX_TOP3_DAYS_PNL_DELTA")
    exb = row.get("EX_BEST_DAY_PNL_DELTA")
    med = row.get("PAIRED_MEDIAN_DAILY_DELTA")
    ext_s = -float(ext) if ext is not None else 1e18
    exb_s = -float(exb) if exb is not None else 1e18
    med_s = -float(med) if med is not None else 1e18
    pf = _pf_num(row.get("PF") if "PF" in row else row.get("profit_factor"))
    pf_s = -1e18 if pf == float("inf") else -pf
    dd = abs(float(row.get("MAX_DD") if "MAX_DD" in row else row.get("max_drawdown_yen_100") or 0.0))
    net = -float(row.get("NET_PNL") if "NET_PNL" in row else row.get("net_pnl_yen_100") or 0.0)
    sid = str(row.get("spec_id") or "")
    return (ext_s, exb_s, med_s, pf_s, dd, net, sid)


def net_and_pf_edge(spec_pack: dict[str, Any], curr: dict[str, Any]) -> bool:
    net_ok = float(spec_pack.get("net_pnl_yen_100") or 0.0) > float(curr.get("net_pnl_yen_100") or 0.0)
    pf_ok = _pf_num(spec_pack.get("profit_factor")) > _pf_num(curr.get("profit_factor"))
    return bool(net_ok and pf_ok)


def decide_case(*, pass_n: int, partial_n: int, integrity_ok: bool) -> dict[str, Any]:
    if not integrity_ok:
        return {
            "CASE": "INTEGRITY",
            "VERDICT": "AM_ENTRY_FIXED_SPEC_INTEGRITY_FAILED",
            "NEXT": "STOP",
        }
    if int(pass_n) >= 1:
        return {
            "CASE": "A",
            "VERDICT": "AM_ENTRY_FIXED_SPEC_DEVELOPMENT_CANDIDATE_FOUND",
            "NEXT": "AM_ENTRY_FIXED_SPEC_FREEZE_REVIEW",
        }
    if int(partial_n) >= 1:
        return {
            "CASE": "B",
            "VERDICT": "AM_ENTRY_FIXED_SPEC_PARTIAL_ECONOMIC_CANDIDATE",
            "NEXT": "AM_ENTRY_FIXED_SPEC_RISK_FAILURE_DECOMPOSITION",
        }
    return {
        "CASE": "C",
        "VERDICT": "AM_ENTRY_27SPEC_SPACE_CONFIRMED_INSUFFICIENT",
        "NEXT": "AM_ENTRY_PROFIT_FAILURE_DECOMPOSITION_V2",
    }


def primary_failure_mechanism(
    *,
    case: str,
    nested: dict[str, Any],
    nested_fill: dict[str, Any],
    current_fill_rate: float | None,
    utility: dict[str, Any],
) -> str:
    if case == "A":
        return "FIXED_SPEC_FULL_ECONOMIC_GATE_PASS"
    if case == "B":
        return "FIXED_SPEC_NET_AND_PF_EDGE_WITHOUT_REMAINING_GATES"
    if case == "INTEGRITY":
        return "INTEGRITY_FAILURE"
    spearman_io = nested.get("INNER_OUTER_SPEARMAN")
    io_weak = spearman_io is None or float(spearman_io) < 0.0
    nested_rate = nested_fill.get("OOF_FILL_RATE")
    fill_lower = (
        nested_rate is not None
        and current_fill_rate is not None
        and float(nested_rate) < float(current_fill_rate)
    )
    price_sp = utility.get("SPEARMAN_ABS_UTILITY_VS_FILL_PRICE")
    price_assoc = price_sp is not None and float(price_sp) > 0.0
    if io_weak and fill_lower:
        return "NESTED_SELECTOR_MISALIGNED_AND_LOW_FILL_NO_SPEC_PNL_PF_EDGE"
    if io_weak:
        return "NESTED_SELECTOR_MISALIGNED_NO_SPEC_PNL_PF_EDGE"
    if fill_lower:
        return "LOW_FILL_COVERAGE_NO_SPEC_PNL_PF_EDGE"
    if price_assoc:
        return "YEN_UTILITY_PRICE_SCALE_ASSOCIATED_NO_SPEC_PNL_PF_EDGE"
    return "NO_FIXED_SPEC_NET_AND_PF_EDGE_VS_CURRENT"


def gate_row(spec_pack: dict[str, Any], curr: dict[str, Any], paired: dict[str, Any], integrity_ok: bool) -> dict[str, Any]:
    g = success_gate(spec_pack, curr, paired, integrity_ok=integrity_ok)
    return {
        "FIXED_SPEC_PASS": bool(g.get("AM_ENTRY_PROFIT_IMPROVEMENT_PASS")),
        **(g.get("gates") or {}),
    }
