"""E1_X12 orchestrator — accumulate risk-only history; no policy adopt/reject."""
from __future__ import annotations

import json
from datetime import datetime, time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.e1_x6_provisional.util import sha256_file, sha256_obj

from . import (
    ANALYSIS_ID,
    DOCUMENT_ID,
    FORBIDDEN_OUTPUT_COLUMNS,
    MAX_CONCURRENT,
    POLICY_FRACTIONS,
    POLICY_ID,
    SOURCE_V2,
    SOURCE_V2_VERDICT,
    STATUS_ALREADY_USED,
    STATUS_ALPHA_RESERVED,
    STATUS_RISK_ONLY,
    STATUS_UNCLASSIFIED,
    TARGET_VALID_DAYS,
)
from .manifests import manifests_from_x10, panel_day_reconciliation
from .registry import build_date_registry

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]
V2_DIR = NATIVE / "results" / "research" / "e1_x11_policy_gate_v2"
PUBLISH = NATIVE / "results" / "research" / "e1_x12_risk_history"
PKG = Path(__file__).resolve().parent
MARKET_OPEN = time(9, 0)


def _safety() -> dict[str, Any]:
    return {
        "submit_cancel_live": "0/0/0",
        "mainline_decision_logic_changed": False,
        "production_yaml_changed": False,
        "entry_changed": False,
        "exit_changed": False,
        "universe_changed": False,
        "prospective_consumed": False,
        "shadow": False,
        "forward": False,
        "discord": False,
        "push_routing_changed": False,
        "paper_behavior_changed": False,
    }


def _before_market_open() -> bool:
    return datetime.now(JST).time() < MARKET_OPEN


def _pnl_independence() -> dict[str, Any]:
    hits = []
    check_files = ("manifests.py", "registry.py", "publish.py")
    for name in check_files:
        fp = PKG / name
        text = fp.read_text(encoding="utf-8")
        for col in ("profit_factor", "passes_candidate", "entry_score", "net_pnl"):
            if col in text:
                hits.append({"file": name, "token": col})
    return {"status": "PASS" if not hits else "FAIL", "hits": hits[:20]}


def run_once(*, label: str = "A") -> dict[str, Any]:
    run_id = f"e1x12_riskhist_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}_{label}"

    v2 = json.loads((V2_DIR / "report.json").read_text(encoding="utf-8"))
    if SOURCE_V2 not in str(v2.get("run_id")) or v2.get("verdict") != SOURCE_V2_VERDICT:
        return {
            "analysis_id": ANALYSIS_ID,
            "run_id": run_id,
            "verdict": "E1_X12_SOURCE_IDENTITY_MISMATCH",
            "determinism_shas": {"verdict": "E1_X12_SOURCE_IDENTITY_MISMATCH"},
            "safety": _safety(),
            "stop": True,
            "_sheets": {},
        }

    # Precommit new RISK_INFRASTRUCTURE_ONLY day only before open
    today = datetime.now(JST).strftime("%Y%m%d")
    collection_precommits = []
    newly_classified = []
    if _before_market_open() and today >= "20260805" and today not in ("20260803",):
        # only if unclassified / not alpha reserved — will be applied in registry
        pre = {
            "date": today,
            "status": STATUS_RISK_ONLY,
            "precommit_at_jst": datetime.now(JST).isoformat(),
            "before_market_open": True,
            "note": "classified before open; alpha_use_allowed=false forever for this date",
        }
        pre["precommit_sha256"] = sha256_obj(pre)
        collection_precommits.append(pre)
        newly_classified.append(today)

    registry = build_date_registry(newly_risk_only=newly_classified)
    # Ensure we never opened alpha reserved / unclassified
    opened_days = []
    for day, row in registry["by_date"].items():
        if row["status"] == STATUS_ALREADY_USED:
            opened_days.append(day)  # via X10 derived sheets only
        elif row["status"] == STATUS_RISK_ONLY and day != today:
            # completed risk-only days would be opened for aggregation
            opened_days.append(day)
        elif row["status"] == STATUS_RISK_ONLY and day == today:
            # day not complete — do not aggregate yet
            pass
        elif row["status"] in (STATUS_ALPHA_RESERVED, STATUS_UNCLASSIFIED):
            assert not row["raw_open_allowed"]

    panel = panel_day_reconciliation()
    manifests, sym_rows = manifests_from_x10()

    # Daily quality sheet
    quality_rows = [{
        "date": m["date"],
        "quality_status": m["quality_status"],
        "quality_reasons": m["quality_reasons"],
        "counted_toward_20": m["quality_status"] == "RISK_HISTORY_DAY_VALID",
    } for m in manifests]

    valid_days = [m["date"] for m in manifests if m["quality_status"] == "RISK_HISTORY_DAY_VALID"]
    invalid_days = [m["date"] for m in manifests if m["quality_status"] != "RISK_HISTORY_DAY_VALID"]
    # today's risk-only not yet valid
    pending_today = today if today in newly_classified else None

    n_valid = len(valid_days)
    remaining = max(0, TARGET_VALID_DAYS - n_valid)

    # asof recurring / policy-evaluable from V2 (reference only while accumulating)
    history_coverage = {
        "status": "RISK_HISTORY_ACCUMULATING",
        "risk_history_days_valid": n_valid,
        "risk_history_days_invalid": len(invalid_days),
        "latest_valid_day": valid_days[-1] if valid_days else None,
        "target_valid_days": TARGET_VALID_DAYS,
        "days_remaining_to_20": remaining,
        "pending_intraday_risk_only": pending_today,
        "policy_verdict_deferred": True,
        "recalibration_gate": {
            "valid_ge_20": n_valid >= 20,
            "next_run_when_ready": "E1_X11_FIXED100_POLICY_CALIBRATION_FINAL",
            "reuse_v2_policy_and_gates": True,
        },
    }

    symbols_observed = sorted({r["symbol"] for r in sym_rows})
    asof_recurring_ref = list(v2.get("global_recurring_reference") or [])

    capital_base_status = {
        "decided_during_collection": False,
        "configured_risk_capital_cap_yen": None,
        "future_formula": "min(configured_risk_capital_cap_yen, verified StockAccountWallet)",
        "buying_power_rejected": True,
        "margin_wallet_unconditional_rejected": True,
        "scenario_optimization_rejected": True,
    }
    special_quote_status = {
        "status": "DYNAMIC_SPECIAL_QUOTE_GUARD_NOT_READY",
        "separated_from_history_collection": True,
        "offline_investigation_allowed": ["broker API field check", "raw payload field check", "existing schema check"],
        "no_invented_substitute": True,
    }

    # Alpha reserved untouched
    alpha_rows = [r for r in registry["rows"] if r["status"] == STATUS_ALPHA_RESERVED]
    unclassified = [r for r in registry["rows"] if r["status"] == STATUS_UNCLASSIFIED]

    pnl = _pnl_independence()
    # verify no forbidden columns in sym_rows
    for r in sym_rows[:1]:
        for col in FORBIDDEN_OUTPUT_COLUMNS:
            assert col not in r

    status = "RISK_HISTORY_ACCUMULATING"
    next_step = (
        f"continue RISK_INFRASTRUCTURE_ONLY collection; {remaining} valid days remaining to 20; "
        "do not open ALPHA_PROSPECTIVE_RESERVED or UNCLASSIFIED; no policy freeze yet"
    )

    det = {
        "registry_sha": registry["registry_sha256"],
        "panel_reconciliation_sha": sha256_obj(panel),
        "manifest_sha": sha256_obj([(m["date"], m["quality_status"], m["symbols_n"]) for m in manifests]),
        "symbol_day_risk_sha": sha256_obj([(r["date"], r["symbol"], r.get("spread_p95_yen_100"), r.get("one_lot_notional_yen")) for r in sym_rows]),
        "history_coverage_sha": sha256_obj(history_coverage),
        "policy_fractions_sha": sha256_obj(POLICY_FRACTIONS),
        "status": status,
    }

    report = {
        "analysis_id": ANALYSIS_ID,
        "document_id": DOCUMENT_ID,
        "run_id": run_id,
        "label": label,
        "generated_at_jst": datetime.now(JST).isoformat(),
        "source_v2": SOURCE_V2,
        "source_v2_verdict": SOURCE_V2_VERDICT,
        "status": status,
        "newly_classified_date": newly_classified[0] if newly_classified else None,
        "collection_precommits": collection_precommits,
        "date_registry": registry,
        "panel_day_reconciliation": panel,
        "history_coverage": history_coverage,
        "valid_history_days": valid_days,
        "invalid_history_days": invalid_days,
        "days_remaining_to_20": remaining,
        "symbols_observed_n": len(symbols_observed),
        "asof_recurring_reference_n": len(asof_recurring_ref),
        "policy_evaluable_days_reference": v2.get("policy_evaluable_days"),
        "warmup_days_reference": v2.get("warmup_days"),
        "alpha_reserved_untouched": alpha_rows,
        "unclassified_do_not_open": unclassified,
        "policy_frozen_unchanged": {
            "POLICY_ID": POLICY_ID,
            "fractions": POLICY_FRACTIONS,
            "max_concurrent": MAX_CONCURRENT,
            "adjusted_from_distribution": False,
        },
        "capital_base_status": capital_base_status,
        "special_quote_status": special_quote_status,
        "pnl_independence": pnl,
        "daily_manifests": manifests,
        "verdict": status,  # accumulating — not a policy adopt/reject
        "verdict_detail": {
            "status": status,
            "next": next_step,
            "valid_days": n_valid,
            "remaining": remaining,
            "newly_classified": newly_classified,
            "registry_sha256": registry["registry_sha256"],
        },
        "determinism_shas": det,
        "safety": _safety(),
        "stop": True,
        "_sheets": {
            "DateRegistry": registry["rows"],
            "CollectionPrecommits": collection_precommits or [{"note": "none_this_run"}],
            "DailyManifest": manifests,
            "DailyQuality": quality_rows,
            "SymbolDayRisk": sym_rows,
            "HistoryCoverage": [history_coverage],
            "AsOfRecurring": [{"symbol": s, "reference": "v2_global_recurring"} for s in asof_recurring_ref],
            "PolicyEvaluableDays": (
                [{"date": d, "role": "policy_evaluable"} for d in (v2.get("policy_evaluable_days") or [])]
                + [{"date": d, "role": "warmup"} for d in (v2.get("warmup_days") or [])]
                + [{"date": "20260721", "role": "bootstrap"}]
            ),
            "CapitalBaseStatus": [capital_base_status],
            "SpecialQuoteStatus": [special_quote_status],
        },
    }
    return report
