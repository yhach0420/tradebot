"""Reconcile USDJPY stock labels without changing measurements. DIRECT sensitivity is not causal usability."""
from __future__ import annotations

from typing import Any

LEAD_FX = "fx_leads"
SIM_NEWS = "simultaneous_common_news"
SIM_ONLY = "simultaneous_only"
NEAR = "unusable_near_contemporaneous"
STOCK_LEADS = "stocks_lead_fx"

USABLE_DIRECT_SYMBOLS_FROZEN = ("6787", "7717", "4092")
OVERNIGHT_PRIMARY_SECTORS = ("輸送用機器", "非鉄金属")


def reconcile_stock_row(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    lead = str(out.get("lead_lag") or "")
    kind = str(out.get("class") or "")
    direct = kind == "DIRECT_STOCK_SENSITIVITY"
    sim_news = lead == SIM_NEWS
    sim_only = lead == SIM_ONLY
    near = lead == NEAR
    causal_lead = lead.startswith(LEAD_FX)
    usable = bool(causal_lead and not sim_news and not sim_only and not near)
    if sim_news:
        usable = False
    out["direct_sensitivity"] = bool(direct)
    out["causal_lead"] = bool(causal_lead)
    out["simultaneous_common_news"] = bool(sim_news)
    out["simultaneous_only"] = bool(sim_only)
    out["near_contemporaneous"] = bool(near)
    out["stock_leads_driver"] = lead == STOCK_LEADS
    out["usable_before_stock_move"] = bool(usable)
    return out


def reconcile_stock_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [reconcile_stock_row(r) for r in rows]


def usable_direct_causal_leads(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if r.get("usable_before_stock_move") and r.get("direct_sensitivity") and r.get("causal_lead")]


def freeze_fx_response_map(*, report: dict[str, Any]) -> dict[str, Any]:
    stocks = reconcile_stock_rows(list(report.get("stock_response") or []))
    usable = usable_direct_causal_leads(stocks)
    pre = list((report.get("counts") or {}).get("preopen_stable_sectors") or [])
    primary = []
    descriptive = []
    for r in pre:
        name = str(r.get("sector") or r.get("name") or "")
        item = {
            "sector": name,
            "open_rho": r.get("open_rho"),
            "open_stable": r.get("open_stable"),
            "open_d1_d4": r.get("open_d1_d4"),
            "m15_rho": r.get("m15_rho"),
            "m15_stable": r.get("m15_stable"),
            "m15_d1_d4": r.get("m15_d1_d4"),
        }
        if name in OVERNIGHT_PRIMARY_SECTORS:
            item["role"] = "PRIMARY_PRESERVED"
            primary.append(item)
        else:
            item["role"] = "DESCRIPTIVE_ONLY"
            descriptive.append(item)
    not_usable_direct = [
        r["symbol"]
        for r in stocks
        if r.get("direct_sensitivity") and not r.get("usable_before_stock_move")
    ]
    return {
        "ANALYSIS_ID": "FX_RESPONSE_MAP_V1",
        "parent_verdict": "USDJPY_SECTOR_SPECIFIC_DRIVER_FOUND_V1",
        "interpretation": (
            "USDJPY produced a meaningful SECTOR-SPECIFIC OVERNIGHT/PREOPEN response "
            "and a small number of stock-level intraday direct-response candidates. "
            "It is not a global intraday driver for all 105 stocks."
        ),
        "not_a_global_intraday_driver": True,
        "ENTRY_rules_built": False,
        "thresholds_optimized": False,
        "FX_OVERNIGHT_OPEN_RESPONSE": {
            "primary_preserved": primary,
            "descriptive_only": descriptive,
        },
        "FX_INTRADAY_DIRECT_CANDIDATES": {
            "symbols": [str(r.get("symbol")) for r in usable],
            "declared": list(USABLE_DIRECT_SYMBOLS_FROZEN),
            "rows": [
                {
                    "symbol": r.get("symbol"),
                    "lead_lag": r.get("lead_lag"),
                    "lead_time_min": r.get("lead_time_min"),
                    "direct_sensitivity": True,
                    "causal_lead": True,
                    "usable_before_stock_move": True,
                    "simultaneous_common_news": False,
                }
                for r in usable
            ],
        },
        "not_usable_despite_direct_sensitivity": not_usable_direct,
        "USABLE_DIRECT_CAUSAL_LEAD_N": len(usable),
        "label_rule": {
            "direct_sensitivity_alone_does_not_imply_causal_usability": True,
            "simultaneous_common_news_forces_usable_before_stock_move_false": True,
            "measurements_unchanged": True,
        },
    }


def reissue_reconciled_reports() -> dict[str, Any]:
    """Patch existing Discovery report labels only. Do not recompute measurements."""
    import json

    from research.usd_jpy_sector_symbol_response_v1.analyze import build_answers
    from research.usd_jpy_sector_symbol_response_v1.isolation import OUT
    from research.usd_jpy_sector_symbol_response_v1.publish import SHEET_ORDER, build_markdown, build_sheets, write_artifacts
    from research.usd_jpy_sector_symbol_response_v1.spec import source_sha256
    from research.fixed_daytrade_universe_v1.secrets import assert_no_secret

    path = OUT / "report.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    stocks = reconcile_stock_rows(list(report.get("stock_response") or []))
    report["stock_response"] = stocks
    usable = usable_direct_causal_leads(stocks)
    direct = [r for r in stocks if r.get("direct_sensitivity")]
    counts = dict(report.get("counts") or {})
    counts["direct_after_market_and_sector_n"] = len(direct)
    counts["USABLE_DIRECT_CAUSAL_LEAD_N"] = len(usable)
    counts["usable_direct_causal_lead_symbols"] = [r.get("symbol") for r in usable]
    counts["direct_symbols"] = [r.get("symbol") for r in direct]
    counts["direct_but_not_usable"] = [r.get("symbol") for r in direct if not r.get("usable_before_stock_move")]
    report["counts"] = counts
    fmap = freeze_fx_response_map(report=report)
    report["fx_response_map_v1"] = fmap
    report["label_reconciliation"] = {
        "measurements_unchanged": True,
        "fields_added": ["direct_sensitivity", "causal_lead", "usable_before_stock_move", "simultaneous_common_news"],
        "USABLE_DIRECT_CAUSAL_LEAD_N": fmap["USABLE_DIRECT_CAUSAL_LEAD_N"],
    }
    hashes = dict(report.get("hashes") or {})
    hashes["SOURCE_SHA256"] = source_sha256()
    report["hashes"] = hashes
    report["answers"] = build_answers(report)
    report["_markdown"] = build_markdown(report)
    assert_no_secret(report, where="report")
    sheets = build_sheets(report)
    assert tuple(sheets.keys()) == SHEET_ORDER
    write_artifacts(report, sheets)
    (OUT / "fx_response_map_v1.json").write_text(json.dumps(fmap, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    map_root = OUT.parent / "fx_response_map_v1"
    map_root.mkdir(parents=True, exist_ok=True)
    (map_root / "fx_response_map_v1.json").write_text(json.dumps(fmap, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return fmap
