"""
Phase 104: probe kabu /board errors with full response bodies (diagnosis only).
"""

from __future__ import annotations

import csv
import json
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from urllib.parse import quote

import requests

ALNUM_CODE_RE = re.compile(r"^\d{3}[A-Z]$", re.IGNORECASE)
KABU_CODE_RE = re.compile(r'"Code"\s*:\s*(\d+)')
KABU_MSG_RE = re.compile(r'"Message"\s*:\s*"([^"]*)"')

ROOT_CAUSE_CATEGORIES = (
    "invalid_symbol",
    "invalid_exchange",
    "board_unavailable",
    "market_closed",
    "unsupported_code",
    "alphanumeric_symbol",
    "request_schema",
    "rate_limit",
    "register_limit_exceeded",
    "other",
)


@dataclass
class BoardProbeResult:
    symbol: str
    exchange: int
    symbol_key: str
    market: str
    http_status: Optional[int]
    kabu_api_code: Optional[int]
    kabu_api_message: str
    response_body: str
    request_method: str
    request_url: str
    request_payload: str
    ok: bool
    root_cause: str
    is_alphanumeric_code: bool


def _parse_kabu_error_body(body: str) -> tuple[Optional[int], str]:
    if not body:
        return None, ""
    try:
        data = json.loads(body)
        if isinstance(data, dict):
            code = data.get("Code")
            msg = str(data.get("Message") or "")
            if code is not None:
                return int(code), msg
    except json.JSONDecodeError:
        pass
    m_code = KABU_CODE_RE.search(body)
    m_msg = KABU_MSG_RE.search(body)
    code = int(m_code.group(1)) if m_code else None
    msg = m_msg.group(1) if m_msg else body[:500]
    return code, msg


def classify_board_root_cause(
    *,
    http_status: Optional[int],
    kabu_code: Optional[int],
    kabu_message: str,
    symbol_code: str,
    exchange: int,
) -> str:
    msg = (kabu_message or "").lower()
    code = symbol_code.upper().split(".")[0].split("@")[0]

    if http_status == 429 or kabu_code == 4001006:
        return "rate_limit"
    if kabu_code in (4001018, 4002006):
        return "register_limit_exceeded"
    if ALNUM_CODE_RE.match(code):
        if kabu_code in (4002001, 4002021, 4002005):
            return "alphanumeric_symbol"
        if "銘柄" in kabu_message or "symbol" in msg:
            return "alphanumeric_symbol"

    if kabu_code == 4002001:
        return "invalid_symbol"
    if kabu_code == 4002021:
        return "market_closed"
    if kabu_code == 4002005:
        return "unsupported_code"
    if kabu_code in (4001010, 4001011, 4001012):
        return "request_schema"

    if any(x in kabu_message for x in ("市場", "Exchange", "exchange")) and kabu_code:
        if "不正" in kabu_message or "invalid" in msg:
            return "invalid_exchange"

    if any(
        x in kabu_message
        for x in (
            "場外",
            "取引時間",
            "開場",
            "市場が開い",
            "有効な銘柄ではない",
            "取引期日",
        )
    ):
        return "market_closed"

    if any(x in kabu_message for x in ("見つから", "登録", "板", "時価")):
        if kabu_code in (4002001, 4002021):
            return "board_unavailable"
        if "登録" in kabu_message:
            return "board_unavailable"

    if http_status == 400 and not kabu_code:
        return "board_unavailable"

    return "other"


def probe_board_raw(
    *,
    symbol_code: str,
    exchange: int,
    token: str,
    base_url: str,
    timeout: float = 30.0,
) -> BoardProbeResult:
    code = symbol_code.upper().replace(".T", "").split("@")[0]
    symbol_key = f"{code}@{exchange}"
    # Path segment: encode @ for safety
    url = f"{base_url.rstrip('/')}/board/{quote(symbol_key, safe='')}"
    headers = {"Content-Type": "application/json", "X-API-KEY": token}
    request_payload = json.dumps(
        {"method": "GET", "headers": {"X-API-KEY": "<redacted>"}, "symbol_key": symbol_key},
        ensure_ascii=False,
    )

    try:
        resp = requests.get(url, headers=headers, timeout=timeout)
        body = resp.text[:4000]
        kabu_code, kabu_msg = _parse_kabu_error_body(body)
        ok = resp.ok
        root = "ok" if ok else classify_board_root_cause(
            http_status=resp.status_code,
            kabu_code=kabu_code,
            kabu_message=kabu_msg,
            symbol_code=code,
            exchange=exchange,
        )
        return BoardProbeResult(
            symbol=f"{code}.T",
            exchange=exchange,
            symbol_key=symbol_key,
            market="",
            http_status=resp.status_code,
            kabu_api_code=kabu_code,
            kabu_api_message=kabu_msg,
            response_body=body,
            request_method="GET",
            request_url=url,
            request_payload=request_payload,
            ok=ok,
            root_cause=root,
            is_alphanumeric_code=bool(ALNUM_CODE_RE.match(code)),
        )
    except requests.RequestException as e:
        body = str(e)
        return BoardProbeResult(
            symbol=f"{code}.T",
            exchange=exchange,
            symbol_key=symbol_key,
            market="",
            http_status=None,
            kabu_api_code=None,
            kabu_api_message=body,
            response_body=body,
            request_method="GET",
            request_url=url,
            request_payload=request_payload,
            ok=False,
            root_cause="other",
            is_alphanumeric_code=bool(ALNUM_CODE_RE.match(code)),
        )


def load_candidate_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def run_board_error_probe(
    candidates: Sequence[Mapping[str, str]],
    *,
    token: str,
    base_url: str,
    delay_sec: float = 0.25,
    max_probe: Optional[int] = None,
) -> list[BoardProbeResult]:
    results: list[BoardProbeResult] = []
    limit = len(candidates) if max_probe is None else min(len(candidates), max_probe)
    for i, row in enumerate(candidates[:limit]):
        sym = str(row.get("symbol") or "").strip()
        code = sym.replace(".T", "").split("@")[0]
        try:
            ex = int(row.get("exchange") or 1)
        except ValueError:
            ex = 1
        r = probe_board_raw(symbol_code=code, exchange=ex, token=token, base_url=base_url)
        r.market = str(row.get("market") or "")
        results.append(r)
        if delay_sec > 0 and i + 1 < limit:
            time.sleep(delay_sec)
    return results


def summarize_probe_results(results: Sequence[BoardProbeResult]) -> dict[str, Any]:
    ok_rows = [r for r in results if r.ok]
    err_rows = [r for r in results if not r.ok]
    http_class = Counter(
        f"http_{r.http_status}_other" if r.http_status and r.http_status != 429 else (
            "http_429_rate_limit" if r.http_status == 429 else "http_unknown"
        )
        for r in err_rows
    )
    root_counts = Counter(r.root_cause for r in err_rows)
    kabu_code_counts = Counter(
        str(r.kabu_api_code) if r.kabu_api_code is not None else "none" for r in err_rows
    )

    def _market_stats(rows: Sequence[BoardProbeResult]) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for market in ("prime", "standard", "growth"):
            sub = [r for r in rows if r.market == market]
            if not sub:
                continue
            ok_n = sum(1 for r in sub if r.ok)
            out[market] = {
                "total": len(sub),
                "success": ok_n,
                "error": len(sub) - ok_n,
                "success_rate": round(ok_n / len(sub), 4),
            }
        return out

    alnum_err = [r for r in err_rows if r.is_alphanumeric_code]
    alnum_ok = [r for r in ok_rows if r.is_alphanumeric_code]
    digit_err = [r for r in err_rows if not r.is_alphanumeric_code]
    digit_ok = [r for r in ok_rows if not r.is_alphanumeric_code]

    success_samples = [
        {
            "symbol": r.symbol,
            "exchange": r.exchange,
            "symbol_key": r.symbol_key,
            "market": r.market,
            "kabu_api_code": r.kabu_api_code,
        }
        for r in ok_rows[:20]
    ]

    return {
        "probe_count": len(results),
        "board_fetch_success_count": len(ok_rows),
        "board_fetch_error_count": len(err_rows),
        "http_error_class_counts": dict(http_class),
        "root_cause_counts": dict(root_counts),
        "kabu_api_code_counts": dict(kabu_code_counts),
        "market_success_rates": _market_stats(results),
        "alphanumeric_code_stats": {
            "in_probe_errors": len(alnum_err),
            "in_probe_success": len(alnum_ok),
            "digit_errors": len(digit_err),
            "digit_success": len(digit_ok),
            "note": "alphanumeric codes use same symbol_key format; master loader may skip them",
        },
        "success_sample": success_samples,
        "failure_vs_success": {
            "success_root_cause": dict(Counter(r.root_cause for r in ok_rows)),
            "error_root_cause": dict(root_counts),
        },
    }


def determine_phase104_verdict(summary: Mapping[str, Any]) -> tuple[str, list[str]]:
    notes: list[str] = []
    rc = summary.get("root_cause_counts") or {}
    http_err = summary.get("http_error_class_counts") or {}
    err_n = int(summary.get("board_fetch_error_count") or 0)
    if err_n == 0:
        return "board_market_availability_issue", ["no errors in probe"]

    top_cause = max(rc.items(), key=lambda x: x[1])[0] if rc else "other"
    top_share = (rc.get(top_cause, 0) / err_n) if err_n else 0

    if rc.get("alphanumeric_symbol", 0) + rc.get("invalid_symbol", 0) > err_n * 0.5:
        notes.append(f"symbol-related errors dominate ({rc})")
        return "board_symbol_format_issue", notes

    if rc.get("invalid_exchange", 0) > err_n * 0.3:
        return "board_exchange_issue", notes

    if rc.get("request_schema", 0) > err_n * 0.3:
        return "board_request_issue", notes

    if (
        rc.get("market_closed", 0) + rc.get("board_unavailable", 0) + rc.get("register_limit_exceeded", 0)
        > err_n * 0.5
    ):
        notes.append(f"availability/register limits ({rc})")
        return "board_market_availability_issue", notes

    if http_err.get("http_400_other", 0) > err_n * 0.5 and rc.get("market_closed", 0) + rc.get(
        "invalid_symbol", 0
    ) > err_n * 0.3:
        return "mixed_rootcause", notes

    if top_share < 0.6:
        return "mixed_rootcause", [f"top cause {top_cause} only {top_share:.1%}"]

    mapping = {
        "invalid_symbol": "board_symbol_format_issue",
        "alphanumeric_symbol": "board_symbol_format_issue",
        "unsupported_code": "board_symbol_format_issue",
        "invalid_exchange": "board_exchange_issue",
        "request_schema": "board_request_issue",
        "market_closed": "board_market_availability_issue",
        "board_unavailable": "board_market_availability_issue",
        "register_limit_exceeded": "board_market_availability_issue",
        "rate_limit": "board_market_availability_issue",
    }
    return mapping.get(top_cause, "mixed_rootcause"), notes


def write_error_examples_csv(path: Path, results: Sequence[BoardProbeResult], *, limit: int = 50) -> None:
    err_rows = [r for r in results if not r.ok][:limit]
    fields = (
        "symbol",
        "exchange",
        "market",
        "symbol_key",
        "http_status",
        "kabu_api_code",
        "kabu_api_message",
        "root_cause",
        "is_alphanumeric_code",
        "request_payload",
        "response_body",
        "request_url",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in err_rows:
            w.writerow(
                {
                    "symbol": r.symbol,
                    "exchange": r.exchange,
                    "market": r.market,
                    "symbol_key": r.symbol_key,
                    "http_status": r.http_status if r.http_status is not None else "",
                    "kabu_api_code": r.kabu_api_code if r.kabu_api_code is not None else "",
                    "kabu_api_message": r.kabu_api_message,
                    "root_cause": r.root_cause,
                    "is_alphanumeric_code": r.is_alphanumeric_code,
                    "request_payload": r.request_payload,
                    "response_body": r.response_body,
                    "request_url": r.request_url,
                }
            )
