"""Phase-0 style field sufficiency: one pass per symbol-day."""
from __future__ import annotations
import json
from pathlib import Path
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

try:
    import orjson
    loads = orjson.loads
except Exception:
    loads = json.loads

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(".")
FEATURE_NEED = [
    "CurrentPrice", "TradingVolume", "TradingValue", "VWAP",
    "CurrentPriceTime", "TradingVolumeTime", "TradingValueTime",
]

def dash(day):
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"

def probe_day(day: str):
    d = NATIVE / "data" / "push_jsonl" / dash(day)
    files = sorted(d.glob("*.jsonl"))
    out = {
        "day": day, "n_files": len(files),
        "sym_board": 0, "sym_price": 0, "sym_both": 0, "sym_board_only": 0,
        "sym_with_qty100": 0, "sym_with_special_field": 0,
        "lines": 0, "cp_lines": 0, "s1_lines": 0, "b1_lines": 0,
        "s1_qty100": 0, "b1_qty100": 0, "with_recorded_at": 0,
        "with_cpt": 0, "missing_files": [],
        "thin_price_syms": [], "board_only_syms": [],
    }
    for fp in files:
        sym = fp.stem[:-2] if fp.stem.endswith(".T") else fp.stem
        board_n = price_n = qty100 = special_seen = 0
        has_cpt = has_rec = 0
        with fp.open("rb") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    obj = loads(line)
                except Exception:
                    continue
                out["lines"] += 1
                if obj.get("recorded_at"):
                    has_rec += 1
                    out["with_recorded_at"] += 1
                pay = obj.get("payload") or {}
                s1 = pay.get("Sell1") if isinstance(pay.get("Sell1"), dict) else {}
                b1 = pay.get("Buy1") if isinstance(pay.get("Buy1"), dict) else {}
                ask = s1.get("Price") if s1 else pay.get("BidPrice")
                bid = b1.get("Price") if b1 else pay.get("AskPrice")
                try:
                    ask_f = float(ask) if ask not in (None, "") else None
                    bid_f = float(bid) if bid not in (None, "") else None
                except Exception:
                    ask_f = bid_f = None
                if ask_f and bid_f and ask_f > 0 and bid_f > 0:
                    board_n += 1
                    out["s1_lines"] += 1
                    out["b1_lines"] += 1
                    aq = s1.get("Qty") if s1 else pay.get("BidQty")
                    bq = b1.get("Qty") if b1 else pay.get("AskQty")
                    try:
                        if aq is not None and float(aq) >= 100 and bq is not None and float(bq) >= 100:
                            qty100 += 1
                            out["s1_qty100"] += 1
                            out["b1_qty100"] += 1
                    except Exception:
                        pass
                sq = pay.get("SpecialQuote")
                if sq is not None:
                    special_seen += 1
                cp = pay.get("CurrentPrice")
                try:
                    cpf = float(cp) if cp not in (None, "") else None
                except Exception:
                    cpf = None
                if cpf and cpf > 0:
                    price_n += 1
                    out["cp_lines"] += 1
                if pay.get("CurrentPriceTime"):
                    has_cpt += 1
                    out["with_cpt"] += 1
        if board_n:
            out["sym_board"] += 1
        if price_n:
            out["sym_price"] += 1
        if board_n and price_n:
            out["sym_both"] += 1
        elif board_n:
            out["sym_board_only"] += 1
            out["board_only_syms"].append(sym)
        if qty100:
            out["sym_with_qty100"] += 1
        if special_seen:
            out["sym_with_special_field"] += 1
        if 0 < price_n < 100:
            out["thin_price_syms"].append(sym)
        if (len(out["board_only_syms"]) + out["sym_both"]) % 40 == 0:
            print(f"  {day} scanned...", flush=True)
    out["board_only_syms"] = out["board_only_syms"][:20]
    out["thin_price_syms"] = out["thin_price_syms"][:20]
    return out

for day in ["20260805", "20260806", "20260807"]:
    print(f"=== probing {day} ===", flush=True)
    r = probe_day(day)
    print(json.dumps({k: v for k, v in r.items() if k not in ("board_only_syms", "thin_price_syms")}, ensure_ascii=False))
    print("board_only_sample", r["board_only_syms"])
    print("thin_price_sample", r["thin_price_syms"])
