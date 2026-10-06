"""Causal external features at frozen stock decision_time. available_at <= T. No 30s fake from 1m bars."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pandas as pd
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
LOOKBACKS = (60, 180, 300)
SLOPE_SEC = (180, 300)


def _dt(date: str, hhmm: str) -> datetime | None:
    try:
        hh, mm = str(hhmm).split(":")[:2]
        return datetime(int(date[:4]), int(date[4:6]), int(date[6:8]), int(hh), int(mm), tzinfo=JST)
    except Exception:
        return None


def _parse_iso(raw: Any) -> datetime | None:
    if raw is None or (isinstance(raw, float) and raw != raw):
        return None
    text = str(raw)
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def _bps(a: float, b: float) -> float | None:
    if b is None or a is None:
        return None
    try:
        af, bf = float(a), float(b)
    except (TypeError, ValueError):
        return None
    if bf == 0 or af != af or bf != bf:
        return None
    return (af / bf - 1.0) * 10_000.0


def _sign(x: float | None) -> int | None:
    if x is None or x != x:
        return None
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def _index_proxy(df: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    by_day: dict[str, list[dict[str, Any]]] = {}
    if df is None or df.empty:
        return by_day
    for rec in df.to_dict("records"):
        day = str(rec.get("date"))
        avail = _parse_iso(rec.get("available_at_jst"))
        start = _parse_iso(rec.get("bar_start_jst"))
        try:
            close = float(rec.get("close"))
        except (TypeError, ValueError):
            continue
        if avail is None or close != close:
            continue
        by_day.setdefault(day, []).append(
            {
                "time_label": str(rec.get("time_label")),
                "close": close,
                "available_at": avail,
                "bar_start": start,
                "source_timestamp": rec.get("bar_start_jst"),
            }
        )
    for day, xs in by_day.items():
        xs.sort(key=lambda r: r["available_at"])
    return by_day


def features_at_T(*, bars: list[dict[str, Any]], decision: datetime) -> dict[str, Any]:
    usable = [b for b in bars if b["available_at"] <= decision]
    empty = {
        "source_timestamp": None,
        "available_at": None,
        "stock_decision_time": decision.isoformat(),
        "lag_sec": None,
        "ret_30s_bps": None,
        "ret_30s_unavailable_reason": "UNAVAILABLE_ON_MINUTE_PROXY",
        "ret_60s_bps": None,
        "ret_180s_bps": None,
        "ret_300s_bps": None,
        "slope_180s": None,
        "slope_300s": None,
        "used_future_print": False,
        "used_same_bar_unavailable_at_T": False,
        "causal_ok": True,
    }
    if not usable:
        return empty
    last = usable[-1]
    out = dict(empty)
    out["source_timestamp"] = last.get("source_timestamp")
    out["available_at"] = last["available_at"].isoformat()
    out["lag_sec"] = float((decision - last["available_at"]).total_seconds())
    by_start = {b["bar_start"]: b for b in usable if b.get("bar_start") is not None}
    last_start = last.get("bar_start")
    for sec in LOOKBACKS:
        if last_start is None:
            continue
        want = last_start - timedelta(seconds=int(sec))
        prev = by_start.get(want)
        if prev is None:
            continue
        if prev["available_at"] > decision:
            out["used_same_bar_unavailable_at_T"] = True
            continue
        out[f"ret_{sec}s_bps"] = _bps(last["close"], prev["close"])
    for sec in SLOPE_SEC:
        ret = out.get(f"ret_{sec}s_bps")
        if ret is None:
            continue
        out[f"slope_{sec}s"] = float(ret) / (float(sec) / 60.0)
    return out


def attach_external_features(
    *,
    trades: list[dict[str, Any]],
    nk_df: pd.DataFrame,
    tx_df: pd.DataFrame,
    source_label: str,
) -> list[dict[str, Any]]:
    nk_by = _index_proxy(nk_df)
    tx_by = _index_proxy(tx_df)
    out = []
    for t in trades:
        day = str(t["date"])
        hh = str(t.get("decision_time") or t.get("event_time"))
        decision = _dt(day, hh)
        rec = dict(t)
        rec["external_source_label"] = source_label
        rec["proxy_not_futures"] = source_label == "PROXY_NOT_FUTURES"
        rec["true_futures"] = source_label == "TRUE_FUTURES"
        if decision is None:
            rec["external_join_ok"] = False
            rec["causal_ok"] = False
            out.append(rec)
            continue
        nk = features_at_T(bars=nk_by.get(day, []), decision=decision)
        tx = features_at_T(bars=tx_by.get(day, []), decision=decision)
        rec["nk"] = nk
        rec["topix"] = tx
        rec["stock_decision_time"] = decision.isoformat()
        rec["nk_ret_30s_bps"] = None
        rec["nk_ret_30s_unavailable"] = True
        rec["nk_ret_60s_bps"] = nk.get("ret_60s_bps")
        rec["nk_ret_180s_bps"] = nk.get("ret_180s_bps")
        rec["nk_ret_300s_bps"] = nk.get("ret_300s_bps")
        rec["nk_slope_180s"] = nk.get("slope_180s")
        rec["nk_slope_300s"] = nk.get("slope_300s")
        rec["topix_ret_30s_bps"] = None
        rec["topix_ret_60s_bps"] = tx.get("ret_60s_bps")
        rec["topix_ret_180s_bps"] = tx.get("ret_180s_bps")
        rec["topix_ret_300s_bps"] = tx.get("ret_300s_bps")
        rec["topix_slope_180s"] = tx.get("slope_180s")
        rec["topix_slope_300s"] = tx.get("slope_300s")
        nk60 = nk.get("ret_60s_bps")
        tx60 = tx.get("ret_60s_bps")
        rec["nk_minus_topix_60s_bps"] = (float(nk60) - float(tx60)) if nk60 is not None and tx60 is not None else None
        ns, ts = _sign(nk60), _sign(tx60)
        rec["nk_sign_60s"] = ns
        rec["topix_sign_60s"] = ts
        if ns is None or ts is None:
            rec["agreement_state"] = "missing"
        elif ns == 1 and ts == 1:
            rec["agreement_state"] = "both_up"
        elif ns == -1 and ts == -1:
            rec["agreement_state"] = "both_down"
        elif ns == 1 and ts == -1:
            rec["agreement_state"] = "nk_up_topix_down"
        elif ns == -1 and ts == 1:
            rec["agreement_state"] = "nk_down_topix_up"
        elif ns == 0 and ts == 0:
            rec["agreement_state"] = "both_neutral"
        else:
            rec["agreement_state"] = "partial_neutral"
        rec["external_join_ok"] = nk.get("available_at") is not None or tx.get("available_at") is not None
        rec["causal_ok"] = bool(nk.get("causal_ok") and tx.get("causal_ok") and not nk.get("used_same_bar_unavailable_at_T") and not tx.get("used_same_bar_unavailable_at_T"))
        rec["lag_sec"] = nk.get("lag_sec")
        rec["source_timestamp"] = nk.get("source_timestamp")
        rec["available_at"] = nk.get("available_at")
        out.append(rec)
    return out
