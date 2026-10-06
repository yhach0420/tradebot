"""Databento / true CME access, metadata cost, coverage. Never purchase. Never download timeseries."""
from __future__ import annotations

import json
import os
from calendar import FRIDAY
from datetime import date, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from research.true_cme_nq_es_cost_and_coverage_audit_v1 import (
    CALENDAR_ES,
    CALENDAR_NQ,
    COST_END_EXCLUSIVE,
    COST_START,
    DATASET,
    DBN_OHLCV_RECORD_BYTES,
    DISCOVERY_FROM,
    DISCOVERY_TO,
    GLOBEX_MINUTES_PER_SESSION,
    PARENT_ES,
    PARENT_NQ,
    SCHEMA,
)
from research.true_cme_nq_es_cost_and_coverage_audit_v1.isolation import DATACUBE, NATIVE, US_FUTURES_DIR

UA = "kabu_native-true-cme-cost-audit/1"
TIMEOUT = 30
HIST_API = "https://hist.databento.com/v0"
ENV_NAMES = ("DATABENTO_API_KEY", "DATABENTO_KEY")
CATALOG_NQ = "https://databento.com/catalog/cme/GLBX.MDP3/futures/NQ"
CATALOG_ES = "https://databento.com/catalog/cme/GLBX.MDP3/futures/ES"
PRICING_URL = "https://databento.com/pricing"
GET_COST_DOCS = "https://databento.com/docs/api-reference-historical/metadata/metadata-get-cost"
OHLCV_DOCS = "https://databento.com/docs/schemas-and-data-formats/ohlcv"
SYMB_DOCS = "https://databento.com/docs/standards-and-conventions"
CME_NQ_SPEC = "https://www.cmegroup.com/trading/equity-index/us-index/e-mini-nasdaq-100.html"

MONTH_CODE = {3: "H", 6: "M", 9: "U", 12: "Z"}
QUARTER_MONTHS = (3, 6, 9, 12)


def _env_present(*names: str) -> bool:
    return any(bool(str(os.environ.get(n) or "").strip()) for n in names)


def _strip_secret_value(raw: str) -> str:
    return str(raw or "").strip().strip('"').strip("'")


def _key_from_dotenv(path) -> str | None:
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        name, _, val = s.partition("=")
        if name.strip() in ENV_NAMES:
            got = _strip_secret_value(val)
            if got:
                return got
    return None


def load_api_key_in_memory() -> tuple[bool, str | None, str]:
    """Return (present, key, source). Key stays in memory only. Never write it."""
    for name in ENV_NAMES:
        got = _strip_secret_value(os.environ.get(name) or "")
        if got:
            return True, got, f"process_env:{name}"
    candidates = [
        NATIVE / ".env",
        NATIVE / ".env.local",
        NATIVE / "config" / ".env",
        NATIVE.parent / ".env",
        NATIVE.parent / ".env.local",
    ]
    for path in candidates:
        got = _key_from_dotenv(path)
        if got:
            return True, got, f"dotenv:{path.name}"
    key_files = [
        NATIVE / "databento.key",
        NATIVE / "config" / "databento.key",
        NATIVE.parent / "databento.key",
    ]
    for path in key_files:
        if path.is_file():
            try:
                got = _strip_secret_value(path.read_text(encoding="utf-8", errors="replace").splitlines()[0] if path.stat().st_size else "")
            except OSError:
                got = ""
            if got:
                return True, got, f"keyfile:{path.name}"
    return False, None, "absent"


def third_friday(year: int, month: int) -> date:
    d = date(year, month, 1)
    delta = (FRIDAY - d.weekday()) % 7
    first = d + timedelta(days=delta)
    return first + timedelta(days=14)


def roll_thursday(expiry: date) -> date:
    """Thursday 8 calendar days before 3rd Friday (standard equity-index roll Thursday)."""
    return expiry - timedelta(days=8)


def raw_symbol(root: str, year: int, month: int) -> str:
    return f"{root}{MONTH_CODE[month]}{str(year)[-1]}"


def front_contract_on(root: str, asof: date) -> str:
    """Front contract using only the predeclared calendar (information known before T)."""
    year = asof.year
    month = QUARTER_MONTHS[min(3, max(0, (asof.month - 1) // 3))]
    for _ in range(8):
        expiry = third_friday(year, month)
        roll = roll_thursday(expiry)
        if asof <= roll:
            return raw_symbol(root, year, month)
        i = QUARTER_MONTHS.index(month)
        if i == 3:
            year, month = year + 1, 3
        else:
            month = QUARTER_MONTHS[i + 1]
    return raw_symbol(root, year, month)


def discovery_front_map(root: str) -> list[dict[str, Any]]:
    start = date(2024, 9, 16)
    end = date(2025, 11, 26)
    out: list[dict[str, Any]] = []
    prev = ""
    d = start
    while d <= end:
        front = front_contract_on(root, d)
        if front != prev:
            code = front[-2]
            month = {v: k for k, v in MONTH_CODE.items()}[code]
            digit = int(front[-1])
            expiry_y = 2020 + digit
            if expiry_y < 2024:
                expiry_y += 10
            expiry = third_friday(expiry_y, month)
            out.append(
                {
                    "front_from": d.isoformat(),
                    "raw_symbol": front,
                    "expiry_third_friday": expiry.isoformat(),
                    "roll_thursday": roll_thursday(expiry).isoformat(),
                    "roll_timestamp": f"{roll_thursday(expiry).isoformat()}T16:00:00-06:00_America/Chicago",
                }
            )
            prev = front
        d += timedelta(days=1)
    for i, row in enumerate(out):
        nxt = out[i + 1]["front_from"] if i + 1 < len(out) else end.isoformat()
        row["front_until_inclusive"] = (date.fromisoformat(nxt) - timedelta(days=1)).isoformat() if i + 1 < len(out) else end.isoformat()
        row["next_contract"] = out[i + 1]["raw_symbol"] if i + 1 < len(out) else None
    return out


def contract_roll_design() -> dict[str, Any]:
    nq_map = discovery_front_map("NQ")
    es_map = discovery_front_map("ES")
    return {
        "do_not_build_series_this_phase": True,
        "local_series_built": False,
        "forbidden": [
            "volume_selected_continuous_if_mapping_uses_future_information",
            "NQ.v.0_as_research_series",
            "NQ.n.0_as_research_series",
            "back_adjusted_continuous_using_future_rolls",
            "cross_contract_unadjusted_return_on_roll_bar",
        ],
        "databento_continuous_notes": {
            "NQ.c.0": "calendar nearest expiry; causal calendar, but stays until expiration rather than the liquidity roll",
            "NQ.n.0": "previous-day open interest rank; uses info available before T but is path-dependent, not predeclared",
            "NQ.v.0": "previous-day volume rank; uses info available before T but is path-dependent, not predeclared; do not use as default",
            "docs": SYMB_DOCS,
        },
        "chosen_rule": "PREDECLARED_CALENDAR_ROLL_THURSDAY_8_CALENDAR_DAYS_BEFORE_THIRD_FRIDAY",
        "contract_identity": "CME Globex raw_symbol e.g. NQZ4 / ESZ4 (root + month code + 1-digit year)",
        "expiry": "3rd Friday of Mar/Jun/Sep/Dec; equity-index termination documented 09:30 America/New_York",
        "roll_timestamp": "16:00 America/Chicago on the Thursday 8 calendar days before 3rd Friday; if that Thursday is a CME holiday, previous Globex weekday 16:00 CT using the exchange calendar published before T",
        "cross_contract_return_policy": "Set the roll-bar return to NA. Do not splice unadjusted prices across contracts. Do not back-adjust. Subsequent returns use only the new front contract's own prices.",
        "information_used_at_T": "contract month list + 3rd-Friday calendar + predeclared holiday calendar. No same-day or future volume/OI.",
        "intended_raw_nq": [r["raw_symbol"] for r in nq_map],
        "intended_raw_es": [r["raw_symbol"] for r in es_map],
        "nq_front_map": nq_map,
        "es_front_map": es_map,
        "start_front_nq": front_contract_on("NQ", date(2024, 9, 16)),
        "start_front_es": front_contract_on("ES", date(2024, 9, 16)),
        "end_front_nq": front_contract_on("NQ", date(2025, 11, 26)),
        "end_front_es": front_contract_on("ES", date(2025, 11, 26)),
    }


def size_estimate() -> dict[str, Any]:
    start = date(2024, 9, 16)
    end = date(2025, 11, 26)
    calendar_days = (end - start).days + 1
    weekdays = sum(1 for i in range(calendar_days) if (start + timedelta(days=i)).weekday() < 5)
    # Globex also prints a Sunday evening session; bound weekday sessions plus ~52 Sundays.
    sunday_sessions = sum(1 for i in range(calendar_days) if (start + timedelta(days=i)).weekday() == 6)
    session_n_lo = weekdays
    session_n_hi = weekdays + sunday_sessions
    bars_front_lo = session_n_lo * GLOBEX_MINUTES_PER_SESSION
    bars_front_hi = session_n_hi * GLOBEX_MINUTES_PER_SESSION
    bytes_front_lo = bars_front_lo * DBN_OHLCV_RECORD_BYTES
    bytes_front_hi = bars_front_hi * DBN_OHLCV_RECORD_BYTES
    n_contracts = max(1, len({r["raw_symbol"] for r in discovery_front_map("NQ")}))
    return {
        "schema": SCHEMA,
        "not_requested": ["mbo", "mbp-10", "mbp-1", "tbbo", "full_depth", "ohlcv-1s"],
        "sufficient_for": ["returns", "short_term_slopes", "realized_volatility", "lead_lag_tests"],
        "discovery_calendar_days": calendar_days,
        "weekday_n": weekdays,
        "sunday_session_n": sunday_sessions,
        "globex_minutes_per_session_bound": GLOBEX_MINUTES_PER_SESSION,
        "dbn_ohlcv_record_bytes_typical": DBN_OHLCV_RECORD_BYTES,
        "nq_front_month_bar_n_bound": {"lo": bars_front_lo, "hi": bars_front_hi},
        "es_front_month_bar_n_bound": {"lo": bars_front_lo, "hi": bars_front_hi},
        "nq_front_month_uncompressed_mb_bound": {"lo": round(bytes_front_lo / 1_000_000, 3), "hi": round(bytes_front_hi / 1_000_000, 3)},
        "es_front_month_uncompressed_mb_bound": {"lo": round(bytes_front_lo / 1_000_000, 3), "hi": round(bytes_front_hi / 1_000_000, 3)},
        "combined_front_month_uncompressed_mb_bound": {
            "lo": round(2 * bytes_front_lo / 1_000_000, 3),
            "hi": round(2 * bytes_front_hi / 1_000_000, 3),
        },
        "parent_all_listed_may_be_larger_by_listed_expiries": n_contracts,
        "size_is_not_a_usd_invoice": True,
        "l0_ohlcv_1m_is_sufficient_for_first_causal_test": True,
    }


def official_pricing_model() -> dict[str, Any]:
    return {
        "vendor": "Databento",
        "pricing_url": PRICING_URL,
        "get_cost_docs": GET_COST_DOCS,
        "historical_usage_based": True,
        "monthly_subscription_required_for_usage_based_historical": False,
        "metadata_symbology_account_management": "free",
        "timeseries_billed": True,
        "metered_by": "uncompressed_size_in_binary_encoding",
        "catalog_rates_quoted_per_mb_on_docs": True,
        "glbx_ohlcv_1m_unit_price_usd": "UNKNOWN_WITHOUT_list_unit_prices",
        "new_team_historical_credit_usd_typical_official_blog": 125,
        "credit_is_not_an_nq_es_invoice": True,
        "public_glbx_get_cost_example_not_this_request": {
            "dataset": DATASET,
            "symbol": "ESM2",
            "schema": "trades_not_ohlcv_1m",
            "start": "2022-06-06T00:00:00",
            "end": "2022-06-10T12:10:00",
            "documented_usd": 2.587353944778,
            "not_our_discovery_range": True,
        },
        "do_not_treat_125_credit_as_exact_nq_es_cost": True,
        "do_not_create_account_this_phase": True,
        "do_not_enter_payment_information": True,
        "do_not_purchase_credits": True,
    }


def _http(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
    hdrs = {"User-Agent": UA, "Accept": "application/json,*/*"}
    if headers:
        hdrs.update(headers)
    req = Request(url, data=data, headers=hdrs, method="POST" if data is not None else "GET")
    try:
        with urlopen(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            return {"ok": 200 <= int(resp.status) < 300, "status": int(resp.status), "body": raw, "url": url}
    except HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        return {"ok": False, "status": int(exc.code), "body": raw, "url": url, "reason": str(exc.reason)}
    except URLError as exc:
        return {"ok": False, "status": None, "body": b"", "url": url, "reason": f"URLError:{exc.reason}"}
    except Exception as exc:
        return {"ok": False, "status": None, "body": b"", "url": url, "reason": f"{type(exc).__name__}"}


def _metadata_post(endpoint: str, fields: dict[str, str], key: str | None) -> dict[str, Any]:
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    if key:
        import base64

        token = base64.b64encode(f"{key}:".encode("utf-8")).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    payload = urlencode(fields).encode("utf-8")
    got = _http(f"{HIST_API}/{endpoint}", data=payload, headers=headers)
    body = got.get("body") or b""
    parsed: Any = None
    try:
        parsed = json.loads(body.decode("utf-8")) if body else None
    except Exception:
        try:
            parsed = float(body.decode("utf-8").strip())
        except Exception:
            parsed = None
    return {
        "ok": bool(got.get("ok")),
        "status": got.get("status"),
        "endpoint": endpoint,
        "parsed": parsed,
        "reason": got.get("reason"),
        "body_len": len(body),
        "did_not_call_timeseries": True,
    }


def metadata_get_cost(key: str, symbols: str, stype_in: str) -> dict[str, Any]:
    rec = _metadata_post(
        "metadata.get_cost",
        {
            "dataset": DATASET,
            "schema": SCHEMA,
            "symbols": symbols,
            "stype_in": stype_in,
            "start": COST_START,
            "end": COST_END_EXCLUSIVE,
        },
        key,
    )
    usd = rec.get("parsed")
    rec["usd"] = float(usd) if isinstance(usd, (int, float)) and rec.get("ok") else None
    rec["symbols"] = symbols
    rec["stype_in"] = stype_in
    rec["dataset"] = DATASET
    rec["schema"] = SCHEMA
    rec["start"] = COST_START
    rec["end_exclusive"] = COST_END_EXCLUSIVE
    return rec


def probe_public_catalog() -> dict[str, Any]:
    nq = _http(CATALOG_NQ)
    es = _http(CATALOG_ES)
    nq_txt = (nq.get("body") or b"").decode("utf-8", errors="replace").lower()
    es_txt = (es.get("body") or b"").decode("utf-8", errors="replace").lower()
    return {
        "nq_catalog_url": CATALOG_NQ,
        "es_catalog_url": CATALOG_ES,
        "nq_http": nq.get("status"),
        "es_http": es.get("status"),
        "nq_mentions_ohlcv_1m": "ohlcv-1m" in nq_txt or "ohlcv" in nq_txt,
        "es_mentions_ohlcv_1m": "ohlcv-1m" in es_txt or "ohlcv" in es_txt,
        "nq_mentions_2010": "2010" in nq_txt,
        "es_mentions_2010": "2010" in es_txt,
        "public_history_since_utc_documented": "2010-06-06",
        "discovery_range_inside_catalog_history": True,
        "js_page_may_hide_details": True,
    }


def _local_hits() -> list[dict[str, Any]]:
    hits: list[dict[str, Any]] = []
    needles = ("glbx", "databento", "us_futures", "nq.c.0", "es.c.0", "ohlcv-1m")
    roots = [
        US_FUTURES_DIR,
        NATIVE / "data" / "databento",
        NATIVE / "data" / "cme",
        NATIVE / "data" / "reference" / "cme",
        NATIVE / "data" / "external" / "databento",
        NATIVE / "data" / "external" / "cme",
    ]
    for root in roots:
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            rel = str(p.relative_to(NATIVE)).replace("\\", "/").lower()
            name = p.name.lower()
            if p.suffix.lower() in {".dbn", ".zst"} or name.endswith(".dbn.zst") or any(n in rel or n in name for n in needles):
                hits.append({"path": str(p.relative_to(NATIVE)).replace("\\", "/"), "bytes": p.stat().st_size})
    data = NATIVE / "data"
    if data.is_dir():
        for p in data.rglob("*.dbn"):
            rel = str(p.relative_to(NATIVE)).replace("\\", "/")
            if not any(h["path"] == rel for h in hits):
                hits.append({"path": rel, "bytes": p.stat().st_size})
        for p in data.rglob("*.dbn.zst"):
            rel = str(p.relative_to(NATIVE)).replace("\\", "/")
            if not any(h["path"] == rel for h in hits):
                hits.append({"path": rel, "bytes": p.stat().st_size})
    # NK/TOPIX capture is not true CME NQ/ES.
    hits = [h for h in hits if "market_context_capture" not in h["path"].lower()]
    hits = [h for h in hits if "daytrade_historical" not in h["path"].lower()]
    return hits[:80]


def scan_local_true_cme() -> dict[str, Any]:
    fut_n = sum(1 for p in US_FUTURES_DIR.rglob("*") if p.is_file()) if US_FUTURES_DIR.exists() else 0
    dc_n = sum(1 for p in DATACUBE.rglob("*") if p.is_file()) if DATACUBE.exists() else 0
    hits = _local_hits()
    present = fut_n > 0 or bool(hits)
    return {
        "LOCAL_TRUE_CME_HISTORY_PRESENT": bool(present),
        "us_futures_dir_exists": US_FUTURES_DIR.exists(),
        "us_futures_file_n": fut_n,
        "datacube_file_n": dc_n,
        "datacube_is_not_true_cme_nq_es": True,
        "dbn_or_named_hits": hits,
        "sufficient_discovery_range_in_local_files": False if not present else "uninspected_empty_or_absent",
        "ohlcv_1m_semantics_proven_from_local_bars": False,
    }


def cme_semantics() -> dict[str, Any]:
    return {
        "dataset": DATASET,
        "schema": SCHEMA,
        "products": ["NQ", "ES"],
        "native_or_proxy": "NATIVE_CME_GLOBEX_MDP3",
        "not": [ "USATECH.IDX-USD", "USA500.IDX-USD", "PROXY_NOT_CME_FUTURES"],
        "ohlcv_docs": OHLCV_DOCS,
        "documented_ts_event": "inclusive_start_of_1m_interval_based_on_trade_ts_recv_unix_ns",
        "documented_bar_interval": "[ts_event, ts_event+60s)",
        "empty_minute_has_no_record": True,
        "project_semantics_required": "BAR_START",
        "available_at_rule": "bar_[T,T+1m)_usable_no_earlier_than_T+1m_after_mapping_ts_event_to_canonical_clock",
        "ohlcv_1m_semantics_proven_from_local_bars": False,
        "ohlcv_1m_semantics_documented": True,
        "timezone_of_ts_event": "UTC_nanoseconds",
        "japan_join": "convert_to_JST_then_asof_available_at",
        "globex_session_documented": "Sun-Fri 17:00-16:00 America/Chicago with daily halt 16:00-17:00 CT",
        "covers_japan_cash_session_if_live": True,
        "covers_japan_0900_via_us_overnight": True,
        "cme_nq_spec_url": CME_NQ_SPEC,
        "l0_sufficient": True,
        "mbo_mbp10_not_requested": True,
        "confirmation_frozen_validation_not_requested": True,
    }


def probe_sources() -> dict[str, Any]:
    print("TRUE_CME_SOURCE_COST_AUDIT metadata_only no_purchase no_timeseries", flush=True)
    present, key, key_source = load_api_key_in_memory()
    local = scan_local_true_cme()
    catalog = probe_public_catalog()
    anon = _metadata_post(
        "metadata.get_cost",
        {
            "dataset": DATASET,
            "schema": SCHEMA,
            "symbols": PARENT_NQ,
            "stype_in": "parent",
            "start": COST_START,
            "end": COST_END_EXCLUSIVE,
        },
        None,
    )
    nq_cost = None
    es_cost = None
    combined_cost = None
    nq_c0 = None
    es_c0 = None
    unit_prices = None
    dataset_range = None
    authenticated_metadata_called = False
    if present and key:
        authenticated_metadata_called = True
        print("DATABENTO_API_KEY_PRESENT=true calling metadata.get_cost only", flush=True)
        nq_cost = metadata_get_cost(key, PARENT_NQ, "parent")
        es_cost = metadata_get_cost(key, PARENT_ES, "parent")
        combined_cost = metadata_get_cost(key, f"{PARENT_NQ},{PARENT_ES}", "parent")
        nq_c0 = metadata_get_cost(key, CALENDAR_NQ, "continuous")
        es_c0 = metadata_get_cost(key, CALENDAR_ES, "continuous")
        unit_prices = _metadata_post("metadata.list_unit_prices", {"dataset": DATASET}, key)
        dataset_range = _metadata_post("metadata.get_dataset_range", {"dataset": DATASET}, key)
        key = None
    else:
        print("DATABENTO_API_KEY_PRESENT=false skipping authenticated metadata", flush=True)

    def _usd(rec: dict[str, Any] | None) -> Any:
        if not rec:
            return None
        return rec.get("usd")

    nq_usd = _usd(nq_cost)
    es_usd = _usd(es_cost)
    comb_usd = _usd(combined_cost)
    if comb_usd is None and nq_usd is not None and es_usd is not None:
        comb_usd = float(nq_usd) + float(es_usd)
    local_ok = bool(local.get("LOCAL_TRUE_CME_HISTORY_PRESENT"))
    if local_ok:
        status = "EXISTING_TRUE_CME_ACCESS"
    elif present:
        status = "DATABENTO_KEY_PRESENT_METADATA_ONLY"
    else:
        status = "DATABENTO_API_KEY_ABSENT_NO_LOCAL_TRUE_CME"
    return {
        "status": status,
        "API_KEY_PRESENT": bool(present),
        "API_KEY_SOURCE_CLASS": key_source.split(":")[0] if present else "absent",
        "LOCAL_TRUE_CME_HISTORY_PRESENT": local_ok,
        "local": local,
        "catalog": catalog,
        "anonymous_get_cost": {
            "status": anon.get("status"),
            "ok": anon.get("ok"),
            "reason": anon.get("reason"),
            "note": "metadata.get_cost is not a public unauthenticated quote",
        },
        "authenticated_metadata_called": authenticated_metadata_called,
        "timeseries_called": False,
        "purchase": False,
        "paid_download": False,
        "account_creation_performed": False,
        "nq_parent_cost": nq_cost,
        "es_parent_cost": es_cost,
        "combined_parent_cost": combined_cost,
        "nq_calendar_c0_cost": nq_c0,
        "es_calendar_c0_cost": es_c0,
        "exact_nq_usd": nq_usd if nq_usd is not None else "UNKNOWN_WITHOUT_DATABENTO_API_KEY",
        "exact_es_usd": es_usd if es_usd is not None else "UNKNOWN_WITHOUT_DATABENTO_API_KEY",
        "exact_combined_usd": comb_usd if comb_usd is not None else "UNKNOWN_WITHOUT_DATABENTO_API_KEY",
        "unit_prices": None if not unit_prices else {"ok": unit_prices.get("ok"), "status": unit_prices.get("status"), "parsed": unit_prices.get("parsed")},
        "dataset_range": None if not dataset_range else {"ok": dataset_range.get("ok"), "status": dataset_range.get("status"), "parsed": dataset_range.get("parsed")},
        "true_nq_1m_catalog": True,
        "true_es_1m_catalog": True,
        "true_nq_1m_local": local_ok,
        "true_es_1m_local": local_ok,
        "historical_range_sufficient_catalog": True,
        "ohlcv_1m_semantics_proven": False,
        "pricing": official_pricing_model(),
        "size": size_estimate(),
        "roll": contract_roll_design(),
        "semantics": cme_semantics(),
        "did_not_call_timeseries": True,
        "did_not_purchase": True,
        "did_not_create_account": True,
    }
