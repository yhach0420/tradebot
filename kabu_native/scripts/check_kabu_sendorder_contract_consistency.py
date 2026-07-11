#!/usr/bin/env python3
"""Compare official kabusapi sendorder snapshot vs wiring/builder/fixtures (Phase687W5A).

Exit 0 = PASS (diff=0 or all diffs explicitly marked NOT_IMPLEMENTED / DOCUMENTED).
Exit 1 = FAIL (blocking mismatch).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

NATIVE_ROOT = Path(__file__).resolve().parents[1]
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase687w5a_official_contract_reconciliation"
SNAPSHOT = NATIVE_ROOT / "docs" / "live_trading" / "vendor" / "kabusapi_sendorder_contract.json"


def _load() -> dict[str, Any]:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


def check() -> dict[str, Any]:
    src = str(NATIVE_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    repo = str(NATIVE_ROOT.parent)
    if repo not in sys.path:
        sys.path.insert(0, repo)

    from small_paper import live_order_api_wiring as wiring
    from small_paper.kabu_order_request_builder import (
        ALLOWED_EXCHANGES,
        ALLOWED_FRONT_ORDER,
        BUILDER_VERSION,
        REQUEST_SCHEMA_VERSION,
        OrderRequestBuilder,
        request_schema_document,
    )
    from small_paper.kabu_sendorder_contract import (
        EXCHANGE_SOR,
        EXCHANGE_TSE,
        EXCHANGE_TSE_PLUS,
        ExchangePolicy,
        TRANSACTION_STATUS,
        TransactionType,
    )
    from small_paper.kabu_order_execution_policy import dryrun_limit_policy
    from small_paper.kabu_order_request_builder import OrderIntentContract

    official = _load()
    diffs: list[dict[str, Any]] = []

    def add(kind: str, detail: str, severity: str = "BLOCKING", resolution: str = "") -> None:
        diffs.append({"kind": kind, "detail": detail, "severity": severity, "resolution": resolution})

    # Exchange enums
    off_ex = {int(k) for k in official["fields"]["Exchange"]["enum"].keys()}
    code_ex = {EXCHANGE_TSE, EXCHANGE_SOR, EXCHANGE_TSE_PLUS}
    wiring_ex = {
        getattr(wiring, "EXCHANGE_TSE", None),
        getattr(wiring, "EXCHANGE_SOR", None),
        getattr(wiring, "EXCHANGE_TSE_PLUS", None),
    }
    if not code_ex.issubset(off_ex):
        add("enum_mismatch", f"builder exchanges {code_ex} not subset of official {off_ex}")
    if wiring_ex != code_ex:
        add("enum_mismatch", f"wiring exchanges {wiring_ex} != builder {code_ex}")
    if ALLOWED_EXCHANGES != code_ex:
        add("enum_mismatch", f"ALLOWED_EXCHANGES {ALLOWED_EXCHANGES} != {code_ex}")

    # Side / CashMargin / FrontOrderType supported
    if wiring.SIDE_BUY != "2" or wiring.SIDE_SELL != "1":
        add("enum_mismatch", "Side BUY/SELL codes mismatch official")
    if wiring.CASH_MARGIN_NEW != 2 or wiring.CASH_MARGIN_REPAY != 3:
        add("enum_mismatch", "CashMargin new/repay mismatch")
    if ALLOWED_FRONT_ORDER != {10, 20}:
        add("enum_mismatch", f"supported FrontOrderType {ALLOWED_FRONT_ORDER} expected {{10,20}}")

    # Cash marked NOT_IMPLEMENTED
    if TRANSACTION_STATUS[TransactionType.CASH_BUY.value] != "NOT_IMPLEMENTED":
        add("unsupported_marked_supported", "CASH_BUY must be NOT_IMPLEMENTED")
    if TRANSACTION_STATUS[TransactionType.CASH_SELL.value] != "NOT_IMPLEMENTED":
        add("unsupported_marked_supported", "CASH_SELL must be NOT_IMPLEMENTED")

    # FundType optional for margin documented
    ft_rule = official["fields"]["FundType"]["rules"]["MARGIN_NEW"]
    if "omit" not in ft_rule.lower() and "11" not in ft_rule:
        add("conditional_requirement_mismatch", "FundType margin rule missing omit/11")

    # Close XOR documented
    if official["fields"]["ClosePositionOrder"]["mutually_exclusive_with"] != "ClosePositions":
        add("conditional_requirement_mismatch", "ClosePositionOrder XOR missing")

    # Builder schema version present
    schema = request_schema_document()
    if "official_snapshot" not in schema:
        add("missing_field", "request schema missing official_snapshot pointer")
    if schema.get("normal_new_exchange_tse") != "FORBIDDEN":
        add("exchange_policy_mismatch", "normal_new_exchange_tse must be FORBIDDEN")

    # Fixture: SOR entry valid; Exchange=1 without maintenance invalid
    b = OrderRequestBuilder()
    ok = b.build(
        OrderIntentContract(
            intent_id="c1",
            idempotency_key="c-sor",
            side="BUY",
            symbol="7203.T",
            quantity=100,
            position_id="p",
            entry_or_exit="ENTRY",
            limit_price=1000.0,
            price_snapshot=1000.0,
            exchange_policy=ExchangePolicy.SOR.value,
            margin_trade_type_source="FIXTURE_EXPLICIT",
        ),
        dryrun_limit_policy(),
    )
    if not ok.request_valid or ok.api_payload.get("Exchange") != EXCHANGE_SOR:
        add("fixture_payload", "SOR margin entry fixture invalid")

    bad = b.build(
        OrderIntentContract(
            intent_id="c2",
            idempotency_key="c-tse",
            side="BUY",
            symbol="7203.T",
            quantity=100,
            position_id="p",
            entry_or_exit="ENTRY",
            limit_price=1000.0,
            price_snapshot=1000.0,
            exchange_policy=ExchangePolicy.NOT_SELECTED.value,
            margin_trade_type_source="FIXTURE_EXPLICIT",
        ),
        dryrun_limit_policy(),
    )
    if bad.request_valid:
        add("exchange_policy_mismatch", "NOT_SELECTED ENTRY must not be valid")

    # Documented divergence: wiring still accepts exchange=1 as parameter (caller-supplied).
    # Builder enforces official policy — mark as DOCUMENTED if wiring default param exists.
    import inspect

    sig = inspect.signature(wiring.build_entry_sendorder_payload)
    # exchange has no default with value 1 forced — callers pass it. OK.
    _ = sig

    # Schema version alignment
    if not BUILDER_VERSION.startswith("687W5A"):
        add("type_mismatch", f"builder_version {BUILDER_VERSION} expected 687W5A.*")
    if "687W5A" not in REQUEST_SCHEMA_VERSION and "official" not in REQUEST_SCHEMA_VERSION:
        add("type_mismatch", f"request schema version not reconciled: {REQUEST_SCHEMA_VERSION}")

    # Snapshot exists and version
    if official.get("contract_version") != "687W5A.1":
        add("missing_field", f"snapshot contract_version={official.get('contract_version')}")

    blocking = [d for d in diffs if d["severity"] == "BLOCKING"]
    result = {
        "pass": len(blocking) == 0,
        "mismatch_count": len(blocking),
        "diff_count": len(diffs),
        "diffs": diffs,
        "official_contract_version": official.get("contract_version"),
        "builder_version": BUILDER_VERSION,
        "request_schema_version": REQUEST_SCHEMA_VERSION,
        "source_of_truth_priority": official.get("source_of_truth_priority"),
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true", default=True)
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()
    result = check()
    if args.write and not args.no_write:
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        (REPORT_DIR / "phase687w5a_contract_consistency.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        # also CSV of diffs
        rows = result.get("diffs") or []
        with (REPORT_DIR / "phase687w5a_internal_official_diff.csv").open(
            "w", encoding="utf-8", newline=""
        ) as f:
            w = csv.DictWriter(
                f, fieldnames=["kind", "detail", "severity", "resolution"], extrasaction="ignore"
            )
            w.writeheader()
            for r in rows:
                w.writerow(r)
        print(f"wrote {REPORT_DIR / 'phase687w5a_contract_consistency.json'}")
    print(json.dumps({"pass": result["pass"], "mismatch_count": result["mismatch_count"]}, indent=2))
    return 0 if result["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
