"""Prove live futures timestamp semantics. No HM1 outcome join. Not a strategy."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from research.am_c0_indicator_exit.isolation import _tail_jsonl_last
from research.new_causal_information_acquisition_v1.isolation import CONTEXT_ROOT

JST = ZoneInfo("Asia/Tokyo")
SEMANTICS_DAYS = ("20260911", "20260914")
SAMPLE_N = 400


def _parse_ts(raw: Any) -> datetime | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def _sample_jsonl(path: Path, n: int = SAMPLE_N) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    try:
        with path.open("r", encoding="utf-8") as fh:
            for i, line in enumerate(fh):
                if i >= n:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if isinstance(rec, dict):
                    rows.append(rec)
    except Exception:
        return rows
    return rows


def _lags(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lags = []
    n_price = 0
    n_null_price = 0
    sessions = set()
    for rec in rows:
        recv = _parse_ts(rec.get("received_at"))
        px_t = _parse_ts(rec.get("CurrentPriceTime"))
        px = rec.get("CurrentPrice")
        if px is None:
            n_null_price += 1
        else:
            n_price += 1
        if recv is not None and px_t is not None:
            lags.append((recv - px_t).total_seconds())
        if rec.get("TradingSession") is not None:
            sessions.add(str(rec.get("TradingSession")))
    lags_s = sorted(lags)
    def _q(p: float) -> float | None:
        if not lags_s:
            return None
        i = min(len(lags_s) - 1, max(0, int(p * (len(lags_s) - 1))))
        return float(lags_s[i])
    return {
        "sample_n": len(rows),
        "finite_current_price_n": n_price,
        "null_current_price_n": n_null_price,
        "lag_n": len(lags_s),
        "lag_sec_p50": _q(0.50),
        "lag_sec_p90": _q(0.90),
        "lag_sec_min": float(lags_s[0]) if lags_s else None,
        "lag_sec_max": float(lags_s[-1]) if lags_s else None,
        "availability_clock": "received_at",
        "source_clock_candidate": "CurrentPriceTime",
        "timezone": "Asia/Tokyo_via_received_at_offset",
        "trading_sessions_seen": sorted(sessions),
        "preopen_currentprice_often_null": n_null_price > 0,
        "did_not_join_stock_outcomes": True,
        "did_not_use_as_hm1_redesign": True,
    }


def prove_live_futures_semantics() -> dict[str, Any]:
    out: dict[str, Any] = {"days": {}, "historical_true_futures_semantics_proven": False, "prospective_semantics_sampled": False}
    for day in SEMANTICS_DAYS:
        root = CONTEXT_ROOT / day / "futures"
        nk = root / "nk225mini.jsonl"
        tx = root / "topix.jsonl"
        nk_rows = _sample_jsonl(nk)
        tx_rows = _sample_jsonl(tx)
        nk_tail = _tail_jsonl_last(nk)
        tx_tail = _tail_jsonl_last(tx)
        row = {
            "day": day,
            "nk_exists": nk.is_file(),
            "topix_exists": tx.is_file(),
            "nk_size": int(nk.stat().st_size) if nk.is_file() else 0,
            "topix_size": int(tx.stat().st_size) if tx.is_file() else 0,
            "nk": _lags(nk_rows),
            "topix": _lags(tx_rows),
            "nk_tail_received_at": nk_tail.get("received_at"),
            "topix_tail_received_at": tx_tail.get("received_at"),
            "bar_start_end_not_applicable_to_push_prints": True,
            "contract_roll": "live_resolved_symbol_near_month_not_autospliced",
            "missing_data": "push_omits_quiet_intervals; empty minutes not synthesized",
            "joined_to_hm1_outcomes": False,
            "enough_to_claim_stable_strategy": False,
        }
        out["days"][day] = row
        if nk_rows or tx_rows:
            out["prospective_semantics_sampled"] = True
    out["note"] = (
        "20260911 and 20260914 prove print clocks for prospective true futures only. "
        "They are not a Discovery historical test and must not redesign HM1."
    )
    out["one_or_two_days_not_enough"] = True
    return out
