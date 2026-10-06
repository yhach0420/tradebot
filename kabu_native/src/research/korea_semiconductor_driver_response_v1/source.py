"""Audit Korea minute sources. Do not purchase. Do not invent Jetta codes. Do not fabricate a Korea proxy."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from research.korea_semiconductor_driver_response_v1 import (
    DISCOVERY_FROM,
    DISCOVERY_TO,
    KOSPI200,
    SAMSUNG,
    SK_HYNIX,
    STATUS_BLOCKED,
    STATUS_NATIVE,
    STATUS_PROXY,
)
from research.korea_semiconductor_driver_response_v1.isolation import CACHE, DATACUBE, NATIVE
from research.usd_jpy_sector_symbol_response_v1.source import JETTA

UA = "kabu_native-korea-semiconductor-causal-research/1"
TIMEOUT = 30
KRX_IX_LIST = "https://data.krx.co.kr/contents/MDC/DATA/datasale/index.cmd?prodType=IX&viewNm=dataProdList"
KRX_ST_LIST = "https://data.krx.co.kr/contents/MDC/DATA/datasale/index.cmd?prodType=ST&viewNm=dataProdList"
KRX_BUY = "https://openapi.krx.co.kr/contents/OPP/DATA/OPPDATA001.jsp"
KRX_HOME = "https://data.krx.co.kr/contents/MDC/MAIN/main/index.cmd"
DATA_GO_KR = "https://www.data.go.kr/en/data/15094807/openapi.do"
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"


def _env(*names: str) -> bool:
    return any(bool(str(os.environ.get(n) or "").strip()) for n in names)


def _local_n(rel: str) -> int:
    root = NATIVE / rel
    if not root.exists():
        return 0
    return sum(1 for p in root.rglob("*") if p.is_file())


def _http_get(url: str, *, timeout: int = TIMEOUT, ua: str = UA) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "application/json,text/html,*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return {
                "ok": 200 <= int(resp.status) < 300,
                "status": int(resp.status),
                "url": url,
                "n": len(raw),
                "content_type": resp.headers.get("Content-Type"),
                "body": raw,
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read() if exc.fp else b""
        return {"ok": False, "status": int(exc.code), "url": url, "n": len(raw), "reason": str(exc.reason), "body": raw}
    except Exception as exc:
        return {"ok": False, "status": None, "url": url, "n": 0, "reason": f"{type(exc).__name__}:{exc}", "body": b""}


def _strip_html(html: str) -> str:
    html = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    html = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.I)
    html = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", html).strip()


def _jetta_catalog() -> dict[str, Any]:
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / "jetta_instruments.json"
    if path.is_file() and path.stat().st_size > 1000:
        payload = json.loads(path.read_text(encoding="utf-8"))
        cached = True
    else:
        got = _http_get(f"{JETTA}/instruments", timeout=40)
        if not got.get("ok"):
            return {"ok": False, "status": got.get("status"), "reason": got.get("reason"), "n_instruments": 0}
        payload = json.loads(got["body"].decode("utf-8"))
        path.write_text(json.dumps({"instruments": payload.get("instruments") or [], "groups": payload.get("groups") or []}), encoding="utf-8")
        cached = False
    inst = list(payload.get("instruments") or [])
    groups = {g.get("id"): g for g in list(payload.get("groups") or [])}
    kr = [x for x in inst if str(x.get("countryCode") or "").upper() in ("KR", "KO", "KOR")]
    needles = ("KOSPI", "KOSDAQ", "KOREA", "SAMSUNG", "HYNIX", "005930", "000660", "KR200", "KRX", "SEOUL", "SK H")
    hits = []
    for x in inst:
        blob = " ".join(str(x.get(k) or "") for k in ("code", "name", "description")).upper()
        if any(n in blob for n in needles):
            hits.append(
                {
                    "code": x.get("code"),
                    "name": x.get("name"),
                    "description": x.get("description"),
                    "countryCode": x.get("countryCode"),
                    "group": (groups.get(x.get("groupId")) or {}).get("code"),
                }
            )
    asia_idx = []
    for x in inst:
        code = str(x.get("code") or "")
        if ".IDX-" not in code:
            continue
        asia_idx.append(
            {
                "code": code,
                "description": x.get("description"),
                "countryCode": x.get("countryCode"),
                "group": (groups.get(x.get("groupId")) or {}).get("code"),
            }
        )
    stock_countries = sorted(
        {
            str((groups.get(x.get("groupId")) or {}).get("countryCode") or x.get("countryCode") or "")
            for x in inst
            if (groups.get(x.get("groupId")) or {}).get("parentId") == 7
        }
    )
    false_hits = [h for h in hits if str(h.get("countryCode") or "") not in ("KR", "KO")]
    korea_true = [h for h in hits if str(h.get("countryCode") or "") in ("KR", "KO")]
    tsm = [x for x in inst if str(x.get("code") or "") == "TSM.US-USD"]

    def _rec(x: dict[str, Any]) -> dict[str, Any]:
        return {
            "code": x.get("code"),
            "name": x.get("name"),
            "description": x.get("description"),
            "countryCode": x.get("countryCode"),
            "group": (groups.get(x.get("groupId")) or {}).get("code"),
        }

    tw_country = [_rec(x) for x in inst if str(x.get("countryCode") or "").upper() in ("TW", "TWN")]
    tw_text = []
    for x in inst:
        blob = " ".join(str(x.get(k) or "") for k in ("code", "name", "description")).upper()
        if any(n in blob for n in ("TAIWAN", "TAIEX", "TSMC", "2330", "TWSE", "TAI.IDX")):
            tw_text.append(_rec(x))
    hkg = [_rec(x) for x in inst if str(x.get("code") or "").startswith("HKG.")]
    chi = [_rec(x) for x in inst if str(x.get("code") or "").startswith("CHI.")]
    oil = []
    rates = []
    for x in inst:
        blob = " ".join(str(x.get(k) or "") for k in ("code", "name", "description")).upper()
        if any(n in blob for n in ("OIL.CMD", "BRENT", "LIGHT.CMD", "WTI")):
            oil.append(_rec(x))
        if any(n in blob for n in ("TNOTE", "BUND", "JGB", "US10Y", "TREASURY", "USTBOND")):
            rates.append(_rec(x))
    taiex_native = any(
        "TAIEX" in str(x.get("description") or "").upper() or str(x.get("code") or "").startswith("TAI.IDX") for x in inst
    )
    samsung_present = any(
        "005930" in str(h.get("code") or "") or "SAMSUNG" in str(h.get("name") or "").upper() for h in korea_true
    )
    sk_present = any("000660" in str(h.get("code") or "") or "HYNIX" in str(h.get("name") or "").upper() for h in korea_true)
    return {
        "ok": True,
        "cached": cached,
        "n_instruments": len(inst),
        "kr_country_n": len(kr),
        "korea_true_hits": korea_true,
        "text_hits_including_false_positives": hits,
        "false_positive_hits": false_hits,
        "stock_cfd_country_codes": stock_countries,
        "korea_in_stock_cfd_countries": "KR" in stock_countries,
        "asia_idx": asia_idx,
        "kospi_idx_present": any("KOSPI" in str(x.get("description") or "").upper() or "KOSPI" in str(x.get("code") or "").upper() for x in asia_idx),
        "samsung_present": samsung_present,
        "sk_hynix_present": sk_present,
        "tsm_us_adr_present": bool(tsm),
        "tsm_us_adr_note": "TSM.US-USD is a US-hours ADR, not native KRX/Taiwan cash, and is not a Korea driver.",
        "did_not_invent_symbol_codes": True,
        "did_not_use_false_positives": True,
        "hkg_chi_are_not_korea_proxies": True,
        "next_gap_catalog": {
            "hkg": hkg,
            "chi_a50": chi,
            "taiwan_country_hits": tw_country,
            "taiwan_text_hits": tw_text[:24],
            "taiwan_cash_native_on_jetta": bool(tw_country) or taiex_native,
            "tsm_us_adr": [_rec(x) for x in tsm],
            "oil": oil[:24],
            "rates": rates[:24],
        },
    }


def _krx_marketplace() -> dict[str, Any]:
    ix = _http_get(KRX_IX_LIST, ua=BROWSER_UA)
    st = _http_get(KRX_ST_LIST, ua=BROWSER_UA)
    buy = _http_get(KRX_BUY, ua=BROWSER_UA)
    home = _http_get(KRX_HOME, ua=BROWSER_UA)
    ix_html = (ix.get("body") or b"").decode("utf-8", "replace")
    st_html = (st.get("body") or b"").decode("utf-8", "replace")
    buy_html = (buy.get("body") or b"").decode("utf-8", "replace")
    ix_ids = re.findall(r"prodSpecId=([A-Z0-9]+)", ix_html)
    st_ids = re.findall(r"prodSpecId=([A-Z0-9]+)", st_html)
    ix_text = _strip_html(ix_html)
    st_text = _strip_html(st_html)
    return {
        "home_http": home.get("status"),
        "ix_list_http": ix.get("status"),
        "st_list_http": st.get("status"),
        "buy_guide_http": buy.get("status"),
        "ix_product_ids": ix_ids,
        "st_product_ids": st_ids,
        "ix_list_text": ix_text[:1200],
        "st_list_text": st_text[:1200],
        "intraday_1m_index_product": "IX1002" if "IX1002" in ix_ids else None,
        "intraday_1m_stock_product": "ST1002" if "ST1002" in st_ids else None,
        "index_1m_10m_mentioned": ("1분" in ix_text and "10분" in ix_text),
        "stock_1m_10m_mentioned": ("1분" in st_text and "10분" in st_text),
        "purchase_required": True,
        "free_1m_available": False,
        "login_required_to_buy": True,
        "academic_discount_mentioned": "50%" in _strip_html(buy_html),
        "exact_price_krw": None,
        "exact_price_note": "Price is shown after product selection / login / cart. Not purchased. Not scraped from a paywall cart.",
        "license": "KRX Data Marketplace paid historical products; tick/quote feeds are member/paid.",
        "urls": {"ix_list": KRX_IX_LIST, "st_list": KRX_ST_LIST, "buy_guide": KRX_BUY, "home": KRX_HOME},
        "did_not_purchase": True,
        "did_not_bypass_license": True,
    }


def _local_korea_hits() -> list[dict[str, Any]]:
    needles = ("kospi", "korea", "005930", "000660", "hynix", "krx")
    hits: list[dict[str, Any]] = []
    roots = [
        NATIVE / "data" / "reference",
        NATIVE / "data" / "external",
        NATIVE / "data" / "vendor",
        NATIVE / "data" / "market_data",
    ]
    for root in roots:
        if not root.exists():
            continue
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        stack = list(entries)
        depth = {root: 0}
        while stack and len(hits) < 40:
            p = stack.pop()
            d = int(depth.get(p.parent, 0))
            name = p.name.lower()
            rel = str(p.relative_to(NATIVE)).replace("\\", "/").lower()
            if p.is_file() and (any(n in name for n in needles) or any(n in rel for n in ("/korea/", "/krx/", "/kospi"))):
                hits.append({"path": str(p.relative_to(NATIVE)).replace("\\", "/"), "bytes": p.stat().st_size})
            if p.is_dir() and d < 2:
                try:
                    kids = list(p.iterdir())
                except OSError:
                    kids = []
                for c in kids:
                    depth[c] = d + 1
                    stack.append(c)
    return hits


def documented_krx_session() -> dict[str, Any]:
    """Official KRX cash hours. Not proven from bars because no minute history was obtained."""
    return {
        "timezone": "Asia/Seoul",
        "dst": False,
        "same_offset_as_jst": True,
        "utc_offset_hours": 9,
        "regular_open": "09:00",
        "regular_close": "15:30",
        "lunch_break": None,
        "opening_auction": "08:30-09:00_documented",
        "closing_auction": "15:20-15:30_documented",
        "special_sessions": "not_enumerated_without_native_calendar_file",
        "holiday_calendar_loaded": False,
        "proven_from_bars": False,
        "do_not_forward_fill_closed_bars": True,
        "korea_cash_does_not_provide_us_style_overnight_to_japan_open": True,
        "primary_role_if_data_existed": "SIMULTANEOUS_SESSION_INTRADAY_LEAD",
        "note": "Session table is documented from KRX public market hours, not proven from acquired bars.",
    }


def probe_sources() -> dict[str, Any]:
    print("KOREA_SOURCE_PROBE", flush=True)
    jetta = _jetta_catalog()
    krx = _krx_marketplace()
    databento = _env("DATABENTO_API_KEY", "DATABENTO_KEY")
    kis = _env("KIS_APP_KEY", "KIS_APP_SECRET", "KRX_API_KEY")
    dukas = _env("DUKASCOPY_USER", "DUKASCOPY_PASSWORD")
    local_kr = _local_n("data/reference/historical_panel/korea")
    local_hits = _local_korea_hits()
    datacube_n = sum(1 for p in DATACUBE.rglob("*") if p.is_file()) if DATACUBE.exists() else 0
    jquants_foreign = False
    korea_jetta_codes = [h.get("code") for h in list(jetta.get("korea_true_hits") or []) if h.get("code")]
    kospi_on_jetta = bool(jetta.get("kospi_idx_present"))
    native_ok = False
    proxy_ok = bool(korea_jetta_codes or kospi_on_jetta)
    status = STATUS_BLOCKED if not native_ok and not proxy_ok else (STATUS_PROXY if proxy_ok else STATUS_NATIVE)
    rows = [
        {
            "instrument": KOSPI200,
            "source": "KRX_Data_Marketplace",
            "product": krx.get("intraday_1m_index_product"),
            "free_or_paid": "paid",
            "history_range": "vendor_after_purchase",
            "frequency": "1min_and_10min_intraday_index_product_listed",
            "timestamp_semantics": "unproven",
            "timezone": "KST_no_DST_if_native",
            "bar_semantics": "unproven",
            "login_requirement": True,
            "license": "KRX_paid",
            "purchase_required": True,
            "runtime_realtime": "not_entitled",
            "found": False,
            "current_entitlement": False,
        },
        {
            "instrument": SAMSUNG,
            "source": "KRX_Data_Marketplace",
            "product": krx.get("intraday_1m_stock_product"),
            "free_or_paid": "paid",
            "history_range": "vendor_after_purchase",
            "frequency": "1min_and_10min_intraday_stock_product_listed",
            "timestamp_semantics": "unproven",
            "timezone": "KST_no_DST_if_native",
            "bar_semantics": "unproven",
            "login_requirement": True,
            "license": "KRX_paid",
            "purchase_required": True,
            "runtime_realtime": "not_entitled",
            "found": False,
            "current_entitlement": False,
        },
        {
            "instrument": SK_HYNIX,
            "source": "KRX_Data_Marketplace",
            "product": krx.get("intraday_1m_stock_product"),
            "free_or_paid": "paid",
            "history_range": "vendor_after_purchase",
            "frequency": "1min_and_10min_intraday_stock_product_listed",
            "timestamp_semantics": "unproven",
            "timezone": "KST_no_DST_if_native",
            "bar_semantics": "unproven",
            "login_requirement": True,
            "license": "KRX_paid",
            "purchase_required": True,
            "runtime_realtime": "not_entitled",
            "found": False,
            "current_entitlement": False,
        },
        {
            "instrument": "KOSPI200_Samsung_SKhynix",
            "source": "Dukascopy_Jetta",
            "free_or_paid": "free_if_listed",
            "history_range": f"{DISCOVERY_FROM}-{DISCOVERY_TO}_would_have_been_used",
            "frequency": "1min_if_listed",
            "found": False,
            "n_instruments_catalog": jetta.get("n_instruments"),
            "kr_country_n": jetta.get("kr_country_n"),
            "kospi_idx_present": jetta.get("kospi_idx_present"),
            "did_not_invent_codes": True,
        },
        {
            "instrument": "data.go.kr_GetMarketIndexInfoService",
            "source": DATA_GO_KR,
            "free_or_paid": "free_public_api",
            "frequency": "daily_not_1min",
            "found": False,
            "usable_as_intraday_driver": False,
            "note": "Public FSC/KRX index API is daily, updated next business day. Not a 1-minute causal clock.",
        },
        {
            "instrument": "local_korea_panel",
            "source": "data/reference/historical_panel/korea",
            "file_n": local_kr,
            "found": local_kr > 0,
            "name_hits": local_hits,
        },
        {
            "instrument": "J-Quants",
            "source": "existing_japan_equity_entitlement",
            "found": False,
            "note": "Japan cash equities only. No KRX stocks.",
            "foreign_minute_endpoint_used": jquants_foreign,
        },
    ]
    go_kr = _http_get(DATA_GO_KR, ua=BROWSER_UA)
    return {
        "status": status,
        "research_mode": "SOURCE_AUDIT_ONLY_NO_FABRICATED_PROXY",
        "native_krx_available": native_ok,
        "proxy_available": proxy_ok,
        "jetta_korea_codes_found": korea_jetta_codes,
        "did_not_fabricate_korea_proxy": True,
        "did_not_use_hkg_or_chi_as_korea_proxy": True,
        "data_go_kr_http": go_kr.get("status"),
        "data_go_kr_note": "Public FSC/KRX index API is daily, not 1-minute.",
        "local_korea_name_hits": local_hits,
        "did_not_reinterpret_nq_proxy_as_cme": True,
        "purchase": False,
        "any_purchase": False,
        "additional_purchase_required": True,
        "yfinance_used": False,
        "login_credentials_present": bool(dukas or databento or kis),
        "databento_credentials_present": bool(databento),
        "kis_krx_credentials_present": bool(kis),
        "datacube_file_n": datacube_n,
        "datacube_purchased": False,
        "krx_purchased": False,
        "jetta": jetta,
        "krx": krx,
        "session_documented": documented_krx_session(),
        "required_history": {"from": DISCOVERY_FROM, "to": DISCOVERY_TO, "plus_prior_day_for_overnight_if_used": True},
        "required_instruments": [KOSPI200, SAMSUNG, SK_HYNIX],
        "next_free_asia_session_instruments_on_jetta": [
            x for x in list(jetta.get("asia_idx") or []) if str(x.get("code") or "").startswith(("HKG.", "CHI."))
        ],
        "rows": rows,
    }
