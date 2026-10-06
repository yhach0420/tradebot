"""Canonical continuous-trading board gate for Passive Fill evidence.

Sign / CurrentPriceStatus codes are the kabu STATION OpenAPI BoardSuccess
definitions (kabucom/kabusapi reference/kabu_STATION_API.yaml), not guessed.

Capture always emits OpeningPrice/CurrentPrice/TradingVolume/AskSign keys
(even when null). Synthetic unit-test quotes that omit those keys are treated
as LEGACY_QUOTE_ONLY so existing stripped boards keep prior ask-cross tests.
Present-and-null is pre-open / itayose and is not fill evidence.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")

SIGN_SOURCE = "kabu_STATION_API.yaml BoardSuccess BidSign/AskSign/Sell1.Sign"
STATUS_SOURCE = "kabu_STATION_API.yaml BoardSuccess CurrentPriceStatus"

# 一般気配 — only this is continuous-session quote evidence.
SIGN_GENERAL = "0101"

# Official non-general quote flags. Fill must not use these boards.
SIGN_NONE = "0000"
SIGN_SPECIAL = "0102"  # 特別気配
SIGN_WARNING = "0103"  # 注意気配
SIGN_PREOPEN = "0107"  # 寄前気配
SIGN_SPECIAL_PRE_HALT = "0108"  # 停止前特別気配
SIGN_POST_CLOSE = "0109"  # 引け後気配
SIGN_PREOPEN_NO_MATCH = "0116"  # 寄前気配約定成立ポイントなし
SIGN_PREOPEN_MATCH_POINT = "0117"  # 寄前気配約定成立ポイントあり
SIGN_SEQUENTIAL = "0118"  # 連続約定気配
SIGN_SEQUENTIAL_PRE_HALT = "0119"  # 停止前の連続約定気配
SIGN_WALKING = "0120"  # 買い上がり売り下がり中

PREOPEN_ITAYOSE_SIGNS = frozenset(
    {
        SIGN_PREOPEN,
        SIGN_PREOPEN_NO_MATCH,
        SIGN_PREOPEN_MATCH_POINT,
    }
)
SPECIAL_QUOTE_SIGNS = frozenset(
    {
        SIGN_SPECIAL,
        SIGN_WARNING,
        SIGN_SPECIAL_PRE_HALT,
        SIGN_POST_CLOSE,
        SIGN_SEQUENTIAL,
        SIGN_SEQUENTIAL_PRE_HALT,
        SIGN_WALKING,
    }
)
NON_CONTINUOUS_SIGNS = PREOPEN_ITAYOSE_SIGNS | SPECIAL_QUOTE_SIGNS

# Allow-list: last print is a real session trade.
STATUS_CURRENT = 1  # 現値
STATUS_DISCONTINUOUS_PRINT = 2  # 不連続歩み
EXECUTABLE_PRICE_STATUS = frozenset({STATUS_CURRENT, STATUS_DISCONTINUOUS_PRINT})

# Official blocked / non-continuous statuses.
STATUS_ITAYOSE = 3  # 板寄せ
STATUS_ITAYOSE_MATCH = 23  # 板寄せ約定
STATUS_BLOCKED = frozenset(
    {
        STATUS_ITAYOSE,
        4,  # システム障害
        5,  # 中断
        6,  # 売買停止
        7,  # 売買停止・システム停止解除
        9,  # システム停止
        10,  # 概算値
        11,  # 参考値
        12,  # サーキットブレイク実施中
        16,  # 一時留保中
        18,  # ファイル障害
        20,  # Spread/Strategy
        21,  # ダイナミックサーキットブレイク発動
        STATUS_ITAYOSE_MATCH,
    }
)

OPENING_KEYS = ("OpeningPrice", "OpeningPriceTime")
LAST_TRADE_KEYS = ("CurrentPrice", "CurrentPriceStatus")
VOLUME_KEYS = ("TradingVolume", "TradingVolumeTime")
SIGN_TOP_KEYS = ("AskSign", "BidSign")

STATE_CONTINUOUS = "CONTINUOUS_TRADING"
STATE_LEGACY = "LEGACY_QUOTE_ONLY"
STATE_NOT_OPENED = "NOT_OPENED"
STATE_PREOPEN_ITAYOSE = "PREOPEN_ITAYOSE"
STATE_SPECIAL_QUOTE = "SPECIAL_QUOTE"
STATE_NO_LAST_TRADE = "NO_LAST_TRADE"
STATE_NO_VOLUME = "NO_VOLUME"
STATE_PRICE_STATUS_BLOCKED = "PRICE_STATUS_BLOCKED"
STATE_SPECIAL_QUOTE_FIELD = "SPECIAL_QUOTE_FIELD"


def _norm_sign(v: Any) -> str:
    if v is None or v == "":
        return ""
    s = str(v).strip()
    if s.endswith(".0") and s[:-2].isdigit():
        s = s[:-2]
    if s.isdigit():
        return s.zfill(4)
    return s


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except (TypeError, ValueError):
        return None


def _ts(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        if isinstance(v, (int, float)):
            return float(v)
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST).timestamp()
    except Exception:
        return None


def _has_any(payload: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    return any(k in payload for k in keys)


def _lvl_sign(lvl: Any) -> str:
    if isinstance(lvl, dict):
        return _norm_sign(lvl.get("Sign"))
    return ""


def collect_quote_signs(payload: Mapping[str, Any]) -> dict[str, str]:
    buy1 = payload.get("Buy1") if isinstance(payload.get("Buy1"), dict) else {}
    sell1 = payload.get("Sell1") if isinstance(payload.get("Sell1"), dict) else {}
    return {
        "AskSign": _norm_sign(payload.get("AskSign")),
        "BidSign": _norm_sign(payload.get("BidSign")),
        "Buy1.Sign": _lvl_sign(buy1),
        "Sell1.Sign": _lvl_sign(sell1),
    }


def _sign_state(signs: Mapping[str, str]) -> str:
    vals = [s for s in signs.values() if s and s != SIGN_NONE]
    if any(s in PREOPEN_ITAYOSE_SIGNS for s in vals):
        return STATE_PREOPEN_ITAYOSE
    if any(s in SPECIAL_QUOTE_SIGNS for s in vals):
        return STATE_SPECIAL_QUOTE
    return ""


def is_executable_continuous_board(
    payload: Mapping[str, Any] | None,
    *,
    event_t: Optional[float] = None,
) -> dict[str, Any]:
    """Return whether this kabu board is continuous-session Passive Fill evidence."""
    pay = dict(payload or {})
    signs = collect_quote_signs(pay)
    sq = pay.get("SpecialQuote")
    if sq is None:
        sq = pay.get("special_quote")
    special_field = bool(sq) and str(sq) not in ("", "0", "None", "null", "False", "false")
    opening = _f(pay.get("OpeningPrice"))
    opening_t = _ts(pay.get("OpeningPriceTime"))
    current = _f(pay.get("CurrentPrice"))
    volume = _f(pay.get("TradingVolume"))
    try:
        status_raw = pay.get("CurrentPriceStatus")
        status = int(status_raw) if status_raw is not None and status_raw != "" else None
    except (TypeError, ValueError):
        status = None

    opened = opening is not None and opening > 0
    if opened and opening_t is not None and event_t is not None:
        opened = opening_t <= float(event_t) + 1e-9

    contract = (
        _has_any(pay, OPENING_KEYS)
        or _has_any(pay, LAST_TRADE_KEYS)
        or _has_any(pay, VOLUME_KEYS)
        or _has_any(pay, SIGN_TOP_KEYS)
        or bool(signs["Buy1.Sign"] or signs["Sell1.Sign"])
    )

    out: dict[str, Any] = {
        "ok": False,
        "state": STATE_LEGACY,
        "AskSign": signs["AskSign"],
        "BidSign": signs["BidSign"],
        "Buy1.Sign": signs["Buy1.Sign"],
        "Sell1.Sign": signs["Sell1.Sign"],
        "SpecialQuote": special_field,
        "OpeningPrice": opening,
        "OpeningPriceTime": pay.get("OpeningPriceTime"),
        "CurrentPrice": current,
        "CurrentPriceTime": pay.get("CurrentPriceTime"),
        "CurrentPriceStatus": status,
        "TradingVolume": volume,
        "TradingVolumeTime": pay.get("TradingVolumeTime"),
        "sign_source": SIGN_SOURCE,
        "status_source": STATUS_SOURCE,
        "contract_fields_present": contract,
    }

    bid = _f((pay.get("Buy1") or {}).get("Price") if isinstance(pay.get("Buy1"), dict) else pay.get("BidPrice"))
    ask = _f((pay.get("Sell1") or {}).get("Price") if isinstance(pay.get("Sell1"), dict) else pay.get("AskPrice"))
    out["locked_or_crossed"] = bool(
        bid is not None and ask is not None and bid > 0 and ask > 0 and bid >= ask
    )

    if special_field:
        out["state"] = STATE_SPECIAL_QUOTE_FIELD
        return out

    # Unopened boards (null OpeningPrice on a Capture contract) are not fill evidence,
    # even when AskSign is 特別気配 (0102) — 5801@09:05 class of defect.
    if _has_any(pay, OPENING_KEYS) and not opened:
        out["state"] = STATE_NOT_OPENED
        return out

    sign_st = _sign_state(signs)
    if sign_st:
        out["state"] = sign_st
        return out

    if status in STATUS_BLOCKED:
        out["state"] = STATE_PREOPEN_ITAYOSE if status in {STATUS_ITAYOSE, STATUS_ITAYOSE_MATCH} else STATE_PRICE_STATUS_BLOCKED
        return out

    if not contract:
        out["ok"] = True
        out["state"] = STATE_LEGACY
        return out
    if _has_any(pay, LAST_TRADE_KEYS) and not (current is not None and current > 0):
        out["state"] = STATE_NO_LAST_TRADE
        return out
    if _has_any(pay, VOLUME_KEYS) and not (volume is not None and volume > 0):
        out["state"] = STATE_NO_VOLUME
        return out
    if status is not None and status not in EXECUTABLE_PRICE_STATUS:
        out["state"] = STATE_PRICE_STATUS_BLOCKED
        return out

    out["ok"] = True
    out["state"] = STATE_CONTINUOUS
    return out


def classify_passive_fill_state(state: str) -> str:
    """Map board_execution_state of an old fill onto the reconciliation classes."""
    st = str(state or "")
    if st in (STATE_CONTINUOUS, STATE_LEGACY):
        return "VALID_CONTINUOUS_FILL"
    if st in (STATE_NOT_OPENED, STATE_PREOPEN_ITAYOSE, STATE_NO_LAST_TRADE, STATE_NO_VOLUME):
        return "INVALID_PREOPEN_ITAYOSE_FILL"
    if st in (STATE_SPECIAL_QUOTE, STATE_SPECIAL_QUOTE_FIELD):
        return "INVALID_SPECIAL_QUOTE_FILL"
    return "OTHER_CHANGED_FILL"
