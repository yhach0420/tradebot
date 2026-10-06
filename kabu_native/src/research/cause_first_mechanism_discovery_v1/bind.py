"""Bind DAYTRADE_HISTORICAL_RESEARCH_FOUNDATION_V2. Do not rewrite it."""
from __future__ import annotations

import json
from typing import Any

from research.cause_first_mechanism_discovery_v1 import PARENT_VERDICT, SEMANTICS
from research.cause_first_mechanism_discovery_v1.isolation import FOUNDATION_OUT


def bind_foundation_v2() -> dict[str, Any]:
    report_path = FOUNDATION_OUT / "report.json"
    pool_path = FOUNDATION_OUT / "research_pool_manifest.json"
    panel_path = FOUNDATION_OUT / "minute_panel_manifest.json"
    missing = [str(p) for p in (report_path, pool_path, panel_path) if not p.is_file()]
    if missing:
        return {"ok": False, "reason": "foundation_artifacts_missing", "missing": missing, "did_not_rewrite_foundation": True}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    pool = json.loads(pool_path.read_text(encoding="utf-8"))
    panel = json.loads(panel_path.read_text(encoding="utf-8"))
    answers = dict(report.get("answers") or {})
    symbols = [str(s) for s in list(pool.get("symbols") or []) if s]
    if not symbols:
        union = list((pool.get("pool") or {}).get("union") or report.get("pool_union_rows") or [])
        symbols = [str(r.get("symbol")) for r in union if isinstance(r, dict) and r.get("symbol")]
    rows = list(report.get("pool_union_rows") or [])
    if not rows:
        rows = list((pool.get("pool") or {}).get("union") or [])
    by_sym = {str(r.get("symbol")): r for r in rows if r.get("symbol")}
    cov = {str(r.get("symbol")): r for r in list(panel.get("coverage") or []) if r.get("symbol")}
    ok = (
        str(answers.get("VERDICT") or report.get("decision", {}).get("VERDICT")) == PARENT_VERDICT
        and str(answers.get("minute_time_semantics") or "") == SEMANTICS
        and int(answers.get("union_research_pool_n") or 0) == 105
        and len(symbols) == 105
        and bool(panel.get("ok"))
        and int(panel.get("ok_n") or 0) == 105
        and bool(answers.get("panel_conditioned_as_of_202609")) is True
        and bool(answers.get("old_v1_modified")) is False
    )
    return {
        "ok": ok,
        "did_not_rewrite_foundation": True,
        "verdict": answers.get("VERDICT"),
        "semantics": answers.get("minute_time_semantics"),
        "union_n": len(symbols),
        "symbols": symbols,
        "by_symbol": by_sym,
        "coverage": cov,
        "history_first": (panel.get("history_first") or (answers.get("actual_history_first_last") or [None, None])[0]),
        "history_last": (panel.get("history_last") or (answers.get("actual_history_first_last") or [None, None])[1]),
        "panel_conditioned_as_of_202609": True,
        "claim_selectable_in_2024_2025": False,
        "validation_label": "UNIVERSE_CONDITIONED_HISTORICAL_VALIDATION",
        "same_bar_close_entry": False,
        "bid_ask_inferred": False,
        "reason": None if ok else "foundation_identity_or_verdict_mismatch",
    }
