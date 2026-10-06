"""Data roles. Economic Confirmation 1/2 stay ECONOMIC_OUTCOME_UNOPENED this task."""
from __future__ import annotations

from typing import Any

from research.pb1_v4_complete_strategy_build_and_economic_validation import (
    DEV_FIRST,
    DEV_LAST,
    FV_FIRST,
    FV_LAST,
    OC_FIRST,
    OC_LAST,
    PROSPECTIVE_FROM,
)


def data_roles(bind: dict[str, Any]) -> dict[str, Any]:
    split = dict(bind.get("split") or {})
    disc = sorted(str(d) for d in list(split.get("discovery_dates") or []))
    conf = sorted(str(d) for d in list(split.get("confirmation_dates") or []) if OC_FIRST <= str(d) <= OC_LAST)
    fv = sorted(str(d) for d in list(split.get("frozen_validation_dates") or []) if FV_FIRST <= str(d) <= FV_LAST)
    leak_disc_conf = [d for d in disc if d >= OC_FIRST]
    leak_conf_fv = [d for d in conf if d >= FV_FIRST]
    ok = (
        bool(disc)
        and disc[0] == DEV_FIRST
        and disc[-1] == DEV_LAST
        and (not conf or (conf[0] == OC_FIRST and conf[-1] == OC_LAST))
        and (not fv or (fv[0] == FV_FIRST and fv[-1] == FV_LAST))
        and not leak_disc_conf
        and not leak_conf_fv
        and not (set(disc) & set(conf))
        and not (set(disc) & set(fv))
        and not (set(conf) & set(fv))
    )
    return {
        "ok": ok,
        "development_dates": disc,
        "development_n": len(disc),
        "development_first": disc[0] if disc else None,
        "development_last": disc[-1] if disc else None,
        "development_role": "BINDING_DEBUG_PARITY_ONLY",
        "development_pnl_is_certification": False,
        "economic_confirmation_1": {
            "first": OC_FIRST,
            "last": OC_LAST,
            "n": len(conf),
            "semantic_exposed": True,
            "PNL_USED": False,
            "MFE_MAE_USED": False,
            "FUTURE_OUTCOME_USED": False,
            "ECONOMIC_OUTCOME_UNOPENED": True,
            "opened_this_task": False,
        },
        "economic_confirmation_2": {
            "first": FV_FIRST,
            "last": FV_LAST,
            "n": len(fv),
            "semantic_exposed": True,
            "PNL_USED": False,
            "MFE_MAE_USED": False,
            "FUTURE_OUTCOME_USED": False,
            "ECONOMIC_OUTCOME_UNOPENED": True,
            "opened_this_task": False,
        },
        "prospective": {
            "from": PROSPECTIVE_FROM,
            "role": "INDEPENDENT_STREAM",
            "opened_this_task": False,
        },
        "forbidden_load_dates": sorted(set(conf) | set(fv) | {d for d in disc + conf + fv if d >= PROSPECTIVE_FROM}),
        "reason": None if ok else "data_role_partition_invalid",
    }
