"""V1.1 candidate proposal only. Does not rewrite FIXED_DAYTRADE_UNIVERSE_V1."""
from __future__ import annotations

from typing import Any

from research.fixed_daytrade_universe_composition_audit_v1 import V1_1_ID
from research.fixed_daytrade_universe_composition_audit_v1.load_daily import load_symbol_daily
from research.fixed_daytrade_universe_composition_audit_v1.proxies import series_proxies
from research.fixed_daytrade_universe_composition_audit_v1.stats import median, percentile
from research.fixed_daytrade_universe_v1 import MIN_MEDIAN_VA_JPY, MIN_P20_VA_JPY


def _liquidity(bars_pack: dict[str, Any]) -> dict[str, Any]:
    px = series_proxies(bars_pack.get("bars") or [])
    va = [v for v in px.get("va") or [] if v is not None]
    return {
        "session_n": bars_pack.get("n"),
        "proxy_n": px.get("proxy_n"),
        "median_va": median(va) if va else None,
        "p20_va": percentile(va, 0.20) if va else None,
        "passes_precommitted_floors": bool(
            va
            and (median(va) or 0) >= MIN_MEDIAN_VA_JPY
            and (percentile(va, 0.20) or 0) >= MIN_P20_VA_JPY
        ),
    }


def propose_v1_1(
    *,
    frozen_symbols: list[str],
    master_by: dict[str, dict[str, Any]],
    necessary_missing: list[dict[str, Any]],
    useful_missing: list[dict[str, Any]],
    redundant_clusters: list[dict[str, Any]],
    expand: bool,
) -> dict[str, Any]:
    frozen_set = set(frozen_symbols)
    adds: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not expand:
        return {
            "id": V1_1_ID,
            "proposal_only": True,
            "frozen_v1_unchanged": True,
            "not_frozen": True,
            "expansion_executed": False,
            "add": [],
            "add_n": 0,
            "redundant_in_v1_not_removed": [],
            "proposed_symbol_n_if_accepted_without_drops": len(frozen_symbols),
            "proposed_symbols_if_accepted_without_drops": list(frozen_symbols),
            "9983_6861_not_revived": True,
            "skipped": "expansion_not_recommended",
        }
    for spec in necessary_missing + useful_missing:
        if not expand and spec.get("necessary_for_driver_id") is False:
            # still list candidates when expansion is recommended; when not expanding, skip useful-only
            continue
        codes = list(spec.get("precommitted_candidates") or [])
        liq_rows = []
        for code in codes:
            if code in frozen_set or code in {"9983", "6861"}:
                continue
            m = master_by.get(code) or {}
            if not m:
                liq_rows.append({"symbol": code, "listed": False, "reason": "not_in_master_asof_20260911"})
                continue
            daily = load_symbol_daily(code)
            liq = _liquidity(daily) if daily.get("ok") else {"passes_precommitted_floors": False, "reason": "daily_unavailable"}
            liq_rows.append(
                {
                    "symbol": code,
                    "listed": True,
                    "name_en": m.get("name_en"),
                    "name_ja": m.get("name_ja"),
                    "tse33_name": m.get("sector33_name"),
                    "topix17_name": m.get("sector17_name"),
                    "scale_category": m.get("scale_category"),
                    **liq,
                    "from_cache": daily.get("from_cache"),
                }
            )
        liq_rows.sort(key=lambda r: (-(r.get("median_va") or 0.0), r.get("symbol") or ""))
        chosen = next((r for r in liq_rows if r.get("listed") and r.get("symbol") not in seen), None)
        if chosen is None:
            continue
        seen.add(str(chosen["symbol"]))
        adds.append(
            {
                "symbol": chosen["symbol"],
                "role": spec.get("role"),
                "missing_factor": spec.get("factor"),
                "missing_side": spec.get("side"),
                "proposed_sector_tse33": spec.get("tse33"),
                "why_useful": spec.get("why"),
                "necessary_for_driver_id": spec.get("necessary_for_driver_id"),
                "liquidity": {k: chosen.get(k) for k in ("median_va", "p20_va", "passes_precommitted_floors", "session_n")},
                "name_en": chosen.get("name_en"),
                "scale_category": chosen.get("scale_category"),
                "alternates_not_selected": [c for c in (spec.get("alternate_candidates") or ()) if c != chosen["symbol"]],
                "not_added_to_v1": True,
            }
        )
    redundant = []
    for cl in redundant_clusters:
        for sym in cl.get("redundant_candidates_not_removed") or []:
            redundant.append(
                {
                    "symbol": sym,
                    "tse33": cl.get("tse33"),
                    "keep_instead": cl.get("keep_liquidity_anchor"),
                    "action": "not_removed_from_v1",
                }
            )
    proposed_symbols = list(frozen_symbols) + [a["symbol"] for a in adds]
    return {
        "id": V1_1_ID,
        "proposal_only": True,
        "frozen_v1_unchanged": True,
        "not_frozen": True,
        "expansion_executed": False,
        "add": adds,
        "add_n": len(adds),
        "redundant_in_v1_not_removed": redundant,
        "proposed_symbol_n_if_accepted_without_drops": len(proposed_symbols),
        "proposed_symbols_if_accepted_without_drops": proposed_symbols,
        "9983_6861_not_revived": True,
    }


assert V1_1_ID.endswith("V1_1_CANDIDATE")
