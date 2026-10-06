"""Weekend/read-only ranking capability probe. No register. No live 09:05 capture."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.market_breadth_leadership_acquisition_v1 import (
    CASE_CAPACITY,
    CASE_MODE_READY,
    CASE_NOT_USABLE,
    DEFAULT_CADENCE_SEC,
    EXCHANGE_DIVISION,
    FALLBACK_CADENCE_SEC,
    NEXT_CAPACITY,
    NEXT_LIVE,
    NEXT_RETRY_PROBE,
    RANKING_TYPES,
)

from research.market_breadth_leadership_acquisition_v1 import client as ranking_client
from research.market_breadth_leadership_acquisition_v1.client import (
    get_apisoftlimit,
    get_ranking,
    issue_readonly_token,
    mutation_counts,
)
from research.market_breadth_leadership_acquisition_v1.derive import derive_snapshot
from research.market_breadth_leadership_acquisition_v1.isolation import NATIVE, OUT
from research.market_breadth_leadership_acquisition_v1.safety import scan_package_source
from research.new_causal_information_acquisition_v1.spec import standard_config_unchanged

JST = ZoneInfo("Asia/Tokyo")

EXPECTED_ITEM_KEYS = {
    1: ("ChangePercentage", "Symbol"),
    2: ("ChangePercentage", "Symbol"),
    5: ("UpCount", "DownCount", "Symbol"),
    6: ("RapidTradePercentage", "Symbol"),
    7: ("RapidPaymentPercentage", "Symbol"),
    14: ("Category", "CategoryName", "ChangePercentage"),
    15: ("Category", "CategoryName", "ChangePercentage"),
}


def schema_report(ranking_type: int, body: Any) -> dict[str, Any]:
    if not isinstance(body, dict):
        return {
            "type": ranking_type,
            "ok": False,
            "reason": "body_not_object",
            "top_keys": [],
            "item_keys": [],
            "n": 0,
            "empty": True,
        }
    ranking = body.get("Ranking")
    items = ranking if isinstance(ranking, list) else []
    item_keys: list[str] = []
    if items and isinstance(items[0], dict):
        item_keys = sorted(items[0].keys())
    expected = EXPECTED_ITEM_KEYS[ranking_type]
    empty = len(items) == 0
    missing = [k for k in expected if k not in item_keys] if item_keys else list(expected if not empty else [])
    ok = ("Ranking" in body) and (empty or not missing)
    return {
        "type": ranking_type,
        "ok": ok,
        "empty": empty,
        "n": len(items),
        "top_keys": sorted(body.keys()),
        "item_keys": item_keys,
        "expected": list(expected),
        "missing_when_nonempty": missing,
        "Type_field": body.get("Type"),
        "ExchangeDivision_field": body.get("ExchangeDivision"),
    }


def propose_cadence(*, ranking_rows: list[dict[str, Any]], http_429_n: int) -> dict[str, Any]:
    if http_429_n > 0:
        return {
            "cadence_sec": FALLBACK_CADENCE_SEC,
            "reason": "HTTP_429_observed",
            "requests_per_minute": round(60.0 / FALLBACK_CADENCE_SEC * len(RANKING_TYPES), 3),
        }
    return {
        "cadence_sec": DEFAULT_CADENCE_SEC,
        "reason": "7_ranking_GET_per_minute_no_429; apisoftlimit_is_order_qty_not_REST_QPS",
        "requests_per_minute": float(len(RANKING_TYPES)),
    }


def persist_probe(body: dict[str, Any], *, native_root: Optional[Path] = None) -> Path:
    """Capability-proof dump. Not a live ranking tape. Never 20260911 backfill."""
    root = Path(native_root) if native_root else NATIVE
    out = (root / "results" / "research" / "market_breadth_leadership_acquisition_v1") if native_root else OUT
    day = datetime.now(JST).strftime("%Y%m%d")
    dest = out / f"probe_{day}"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "probe.json").write_text(json.dumps(body, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    soft = body.get("apisoftlimit")
    if soft is not None:
        (dest / "apisoftlimit.json").write_text(json.dumps(soft, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    for row in body.get("ranking") or []:
        typ = int(row.get("requested_type") or 0)
        if typ not in RANKING_TYPES:
            continue
        (dest / f"ranking_type{typ}.json").write_text(
            json.dumps(row, ensure_ascii=False, indent=2, default=str) + "\n",
            encoding="utf-8",
        )
    return dest


def run_probe(*, native_root: Optional[Path] = None, persist: bool = True) -> dict[str, Any]:
    root = Path(native_root) if native_root else NATIVE
    clock = datetime.now(JST).isoformat(timespec="seconds")
    std = standard_config_unchanged()
    mutations = mutation_counts()
    source_scan = scan_package_source()
    token_error = ""
    rest = None
    token = ""
    try:
        rest, token = issue_readonly_token(native_root=root)
    except Exception as exc:
        token_error = f"{type(exc).__name__}:{exc}"

    soft = None
    ranking_rows: list[dict[str, Any]] = []
    if rest is not None and token:
        soft = get_apisoftlimit(rest=rest, token=token)
        for typ in RANKING_TYPES:
            ranking_rows.append(get_ranking(rest=rest, token=token, ranking_type=typ, exchange_division=EXCHANGE_DIVISION))

    schemas = {str(r.get("requested_type")): schema_report(int(r.get("requested_type") or 0), r.get("raw")) for r in ranking_rows}
    http_429_n = sum(1 for r in ranking_rows if r.get("http_status") == 429)
    http_error_n = sum(1 for r in ranking_rows if r.get("http_status") not in (200, None) or not r.get("ok"))
    cadence = propose_cadence(ranking_rows=ranking_rows, http_429_n=http_429_n)
    by_type = {int(r["requested_type"]): r.get("raw") for r in ranking_rows if r.get("requested_type") is not None}
    derived = derive_snapshot(by_type=by_type) if by_type else {}

    soft_ok = bool(soft and soft.get("http_status") == 200)
    ranking_ok = bool(ranking_rows) and all(r.get("http_status") == 200 for r in ranking_rows)
    schema_ok = bool(schemas) and all(v.get("ok") for v in schemas.values())

    if http_429_n > 0:
        case, verdict, nxt = "CAPACITY", CASE_CAPACITY, NEXT_CAPACITY
    elif ranking_ok and schema_ok and mutations["register_mutation_n"] == 0 and source_scan.get("ok"):
        case, verdict, nxt = "MODE_READY", CASE_MODE_READY, NEXT_LIVE
    else:
        case, verdict, nxt = "NOT_USABLE", CASE_NOT_USABLE, NEXT_RETRY_PROBE

    body = {
        "clock": clock,
        "token_error": token_error,
        "token_source": ranking_client.LAST_TOKEN_SOURCE,
        "apisoftlimit": soft,
        "ranking": ranking_rows,
        "schemas": schemas,
        "derived_empty_ok": derived,
        "cadence": cadence,
        "http_429_n": http_429_n,
        "http_error_n": http_error_n,
        "soft_ok": soft_ok,
        "ranking_ok": ranking_ok,
        "schema_ok": schema_ok,
        "standard_paper": std,
        "mutations": mutations,
        "source_scan": source_scan,
        "probe_raw_root_note": "results/.../probe_YYYYMMDD only; not data/market_breadth_capture",
        "decision": {
            "CASE": case,
            "VERDICT": verdict,
            "NEXT": nxt,
            "ENTRY": False,
            "EXIT": False,
            "DAY2_FROZEN_PRICE_RETURN_TEST_CHANGED": False,
        },
    }
    if persist:
        body["probe_dir"] = str(persist_probe(body, native_root=root))
    return body
