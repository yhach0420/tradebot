"""VCIE source audit — what is measurable from PUSH / events / NP / audit."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.volume_confirmed_impulse_entry.constants import NATIVE, SOT_PBV2_DIR

JST = ZoneInfo("Asia/Tokyo")


@dataclass
class DayPushStats:
    day: str
    n_rows: int = 0
    n_symbols: int = 0
    n_dup_skipped: int = 0
    n_vol_decrease: int = 0
    n_vol_increase: int = 0
    n_same_ts: int = 0
    n_missing_price: int = 0
    n_missing_volume: int = 0
    has_bid_ask: bool = False
    sample_symbols: list[str] = field(default_factory=list)


def list_capture_days(native: Path = NATIVE) -> list[str]:
    root = native / "data" / "market_capture"
    if not root.is_dir():
        return []
    out = []
    for p in sorted(root.iterdir()):
        if p.is_dir() and list(p.glob("push_part_*.jsonl")):
            out.append(p.name)
    return out


def _parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except Exception:
        return None


def audit_push_day(day_dir: Path, *, max_lines: int = 200_000) -> DayPushStats:
    """Lightweight streaming audit (capped lines for speed; full loader does complete pass)."""
    day = day_dir.name
    st = DayPushStats(day=day)
    parts = sorted(day_dir.glob("push_part_*.jsonl"))
    last_vol: dict[str, float] = {}
    last_ts: dict[str, str] = {}
    last_key: dict[str, tuple] = {}
    syms: set[str] = set()
    n = 0
    for part in parts:
        with part.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                n += 1
                if n > max_lines:
                    break
                try:
                    o = json.loads(line)
                except json.JSONDecodeError:
                    continue
                op = o.get("original_payload") if isinstance(o.get("original_payload"), dict) else {}
                sym = str(o.get("symbol") or op.get("Symbol") or "")
                if not sym:
                    continue
                syms.add(sym)
                px = o.get("current_price")
                if px is None:
                    px = op.get("CurrentPrice")
                vol = o.get("trading_volume")
                if vol is None:
                    vol = op.get("TradingVolume")
                if px is None:
                    st.n_missing_price += 1
                if vol is None:
                    st.n_missing_volume += 1
                bid = o.get("bid") if o.get("bid") is not None else op.get("BidPrice")
                ask = o.get("ask") if o.get("ask") is not None else op.get("AskPrice")
                if bid is not None and ask is not None:
                    st.has_bid_ask = True
                recv = str(o.get("received_at_jst") or "")
                key = (sym, recv, px, vol, bid, ask)
                if last_key.get(sym) == key:
                    st.n_dup_skipped += 1
                    continue
                last_key[sym] = key
                if last_ts.get(sym) == recv:
                    st.n_same_ts += 1
                last_ts[sym] = recv
                try:
                    v = float(vol) if vol is not None else None
                except (TypeError, ValueError):
                    v = None
                if v is not None and sym in last_vol:
                    if v > last_vol[sym]:
                        st.n_vol_increase += 1
                    elif v < last_vol[sym]:
                        st.n_vol_decrease += 1
                if v is not None:
                    last_vol[sym] = v
            if n > max_lines:
                break
        if n > max_lines:
            break
    st.n_rows = n
    st.n_symbols = len(syms)
    st.sample_symbols = sorted(syms)[:20]
    return st


def run_source_audit(native: Path = NATIVE) -> dict[str, Any]:
    capture_days = list_capture_days(native)
    sot_days: list[str] = []
    sot_path = SOT_PBV2_DIR / "report.json"
    if sot_path.exists():
        sot = json.loads(sot_path.read_text(encoding="utf-8"))
        sot_days = list(sot.get("trading_days") or [])

    push_stats = []
    for d in capture_days:
        push_stats.append(audit_push_day(native / "data" / "market_capture" / d).__dict__)

    # Field availability matrix
    fields = {
        "event_timestamp": {"source": "PUSH received_at_jst / CurrentPriceTime", "status": "AVAILABLE_ON_CAPTURE_DAYS"},
        "CurrentPrice": {"source": "PUSH original_payload", "status": "AVAILABLE_ON_CAPTURE_DAYS"},
        "CurrentPriceTime": {"source": "PUSH original_payload", "status": "AVAILABLE_ON_CAPTURE_DAYS"},
        "cumulative_volume": {"source": "PUSH TradingVolume", "status": "AVAILABLE_ON_CAPTURE_DAYS"},
        "cumulative_trading_value": {"source": "PUSH TradingValue", "status": "AVAILABLE_ON_CAPTURE_DAYS"},
        "BidPrice_AskPrice": {"source": "PUSH Bid/Ask", "status": "AVAILABLE_ON_CAPTURE_DAYS"},
        "BidQty_AskQty": {"source": "PUSH BidQty/AskQty", "status": "AVAILABLE_ON_CAPTURE_DAYS"},
        "board_timestamp": {"source": "approx CurrentPriceTime / recv", "status": "PARTIAL"},
        "price_update": {"source": "delta CurrentPrice", "status": "DERIVABLE"},
        "board_update": {"source": "delta Bid/Ask/Qty", "status": "DERIVABLE"},
        "tick_direction": {"source": "price change tick rule", "status": "TICK_RULE_INFERRED"},
        "individual_trade_volume": {"source": "not in PUSH print stream", "status": "NOT_AVAILABLE"},
        "execution_at_ask_bid": {"source": "price vs ask/bid quote", "status": "QUOTE_INFERRED"},
        "market_capture_L2": {"source": "data/market_capture", "status": "AVAILABLE_LIMITED_DAYS"},
        "np_pre_entry_features": {"source": "jsonl TradingValue windows", "status": "AVAILABLE_BUT_VALUE_NOT_SHARE_VOL"},
        "entry_scan_audit": {"source": "latency/staleness only", "status": "NO_VOLUME_SERIES"},
        "events_csv_share_volume": {"source": "small_paper_events.csv", "status": "NOT_AVAILABLE"},
    }

    answers = {
        "q1_volume_delta_5_10_30s": {
            "answer": "YES on capture days via cumulative TradingVolume diffs; NO on non-capture days",
            "imputation": "none",
        },
        "q2_same_ts_dup_rewind_missing": {
            "answer": "Duplicates detectable by identical snapshot key; volume decreases treated as reset/DQ; missing px/vol counted",
            "per_day_sample": push_stats,
        },
        "q3_uptick_volume": {
            "answer": "YES via tick-rule on price change × volume_delta (TICK_RULE_INFERRED); no exchange trade-side flag",
        },
        "q4_ask_execution_ratio": {
            "answer": "NOT DIRECT — QUOTE_INFERRED when CurrentPrice==AskPrice; else TICK_RULE_INFERRED; else NOT_EVALUABLE",
            "classes": ["DIRECT", "QUOTE_INFERRED", "TICK_RULE_INFERRED", "NOT_EVALUABLE"],
            "direct_available": False,
        },
        "q5_micro_range_high_breakout": {
            "answer": "YES on PUSH series excluding current bar from level",
        },
        "q6_hold_2tick_or_5_10s": {
            "answer": "YES using subsequent PUSH ticks / elapsed recv time",
        },
        "q7_all_22_sot_days": {
            "answer": "NO — raw PUSH only on capture overlap days",
            "sot_days_n": len(sot_days),
            "capture_days_n": len(capture_days),
            "capture_days": capture_days,
            "sot_days": sot_days,
        },
        "q8_capture_only": {
            "answer": "YES — true volume impulse requires market_capture days only; other days NOT_EVALUABLE for VCIE volume arm",
        },
    }

    gate_ok = len(capture_days) >= 1
    return {
        "fields": fields,
        "answers": answers,
        "capture_days": capture_days,
        "sot_days": sot_days,
        "push_day_stats": push_stats,
        "trade_side_policy": "QUOTE_INFERRED preferred; TICK_RULE_INFERRED fallback; DIRECT unavailable",
        "gate_ok": gate_ok,
        "verdict": "VCIE_SOURCE_AUDIT_PASS" if gate_ok else "VCIE_SOURCE_AUDIT_BLOCKED",
        "notes": (
            f"Raw PUSH available on {len(capture_days)} days only "
            f"({','.join(capture_days)}). SoT has {len(sot_days)} days. "
            "No imputation of missing volume. Full 22-day volume impulse NOT available."
        ),
    }