#!/usr/bin/env python
"""P1-QA: scan Capture prices for selected trades. Does not rewrite P1."""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
JST = ZoneInfo("Asia/Tokyo")

from research.anchor_vs_event_driven.run_comparison import find_capture_dir  # noqa: E402
from small_paper.v1r_live_dual_lane import canonical_symbol_key  # noqa: E402

P1 = ROOT / "results" / "research" / "current_runtime_full_capture_recalc_p1" / "report.json"


def _bid(pay: dict[str, Any]) -> Any:
    b = pay.get("Buy1")
    if isinstance(b, dict):
        return b.get("Price")
    return pay.get("BidPrice")


def _ask(pay: dict[str, Any]) -> Any:
    a = pay.get("Sell1")
    if isinstance(a, dict):
        return a.get("Price")
    return pay.get("AskPrice")


def scan_day(day: str, wanted: set[str], windows: list[tuple[str, float, float]]) -> dict[str, Any]:
    cap = find_capture_dir(day)
    if cap is None:
        return {"ok": False, "reason": "NO_CAPTURE"}
    # windows: (symbol, t0, t1)
    by_sym: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for s, t0, t1 in windows:
        by_sym[s].append((t0, t1))
    # collect ticks in union of windows per symbol
    ticks: dict[str, list[dict[str, Any]]] = defaultdict(list)
    n = 0
    for part in sorted(cap.glob("push_part_*.jsonl")):
        if part.stat().st_size <= 0:
            continue
        with part.open(encoding="utf-8") as fh:
            for line in fh:
                if not line.strip():
                    continue
                n += 1
                rec = json.loads(line)
                if rec.get("kind") not in (None, "market_push"):
                    continue
                sym = canonical_symbol_key(rec.get("symbol"))
                if sym not in wanted:
                    continue
                pay = rec.get("payload") or rec.get("original_payload") or {}
                stamp = rec.get("received_at") or rec.get("event_time") or rec.get("persisted_at")
                if not stamp:
                    continue
                try:
                    et = datetime.fromisoformat(str(stamp)).timestamp()
                except Exception:
                    continue
                wlist = by_sym.get(sym) or []
                if not any(t0 - 2.0 <= et <= t1 + 2.0 for t0, t1 in wlist):
                    continue
                ticks[sym].append(
                    {
                        "t": et,
                        "seq": rec.get("sequence"),
                        "bid": _bid(pay) if isinstance(pay, dict) else None,
                        "ask": _ask(pay) if isinstance(pay, dict) else None,
                        "px": (pay.get("CurrentPrice") if isinstance(pay, dict) else None),
                    }
                )
    return {"ok": True, "capture": str(cap), "n_lines": n, "ticks": ticks}


def window_stats(ticks: list[dict[str, Any]], t0: float, t1: float, fill_px: float) -> dict[str, Any]:
    inside = [x for x in ticks if t0 - 1e-6 <= x["t"] <= t1 + 1e-6]
    bids = [float(x["bid"]) for x in inside if x.get("bid") not in (None, "")]
    asks = [float(x["ask"]) for x in inside if x.get("ask") not in (None, "")]
    pxs = [float(x["px"]) for x in inside if x.get("px") not in (None, "")]
    uniq_b = sorted(set(bids))
    uniq_a = sorted(set(asks))
    uniq_p = sorted(set(pxs))
    last_bid = bids[-1] if bids else None
    first_bid = bids[0] if bids else None
    changed = len(uniq_b) > 1
    return {
        "n_ticks": len(inside),
        "unique_bids": uniq_b[:12],
        "unique_asks": uniq_a[:12],
        "unique_current": uniq_p[:12],
        "n_unique_bid": len(uniq_b),
        "n_unique_ask": len(uniq_a),
        "n_unique_px": len(uniq_p),
        "first_bid": first_bid,
        "last_bid": last_bid,
        "bid_moved": changed,
        "fill_px_in_bids": (abs(float(fill_px) - b) < 1e-9 for b in uniq_b) if False else any(
            abs(float(fill_px) - b) < 1e-6 for b in uniq_b
        ),
        "last_bid_eq_fill": last_bid is not None and abs(float(last_bid) - float(fill_px)) < 1e-6,
    }


def main() -> int:
    body = json.loads(P1.read_text(encoding="utf-8"))
    trades = body["trades"]
    out: dict[str, Any] = {}

    # 0731 all trades
    d31 = [t for t in trades if t["date"] == "20260731"]
    wanted31 = {t["symbol"] for t in d31}
    windows31 = [(t["symbol"], float(t["fill_time"]), float(t["exit_time"])) for t in d31]
    print("scan 20260731", sorted(wanted31), flush=True)
    s31 = scan_day("20260731", wanted31, windows31)
    rows = []
    for t in d31:
        st = window_stats(
            (s31.get("ticks") or {}).get(t["symbol"]) or [],
            float(t["fill_time"]),
            float(t["exit_time"]),
            float(t["fill_price"]),
        )
        pnl = float(t["pnl_yen_100"])
        hs = float(t.get("holding_sec") or 0)
        if hs < 0:
            klass = "SESSION_CLOSE_ORDERING_ZERO_HOLD"
        elif abs(pnl) < 1e-9 and not st["bid_moved"] and st["n_ticks"] > 0:
            klass = "GENUINELY_FLAT_MARKET_DATA"
        elif abs(pnl) < 1e-9 and st["n_ticks"] == 0:
            klass = "STALE_CAPTURE_STATE"
        elif abs(pnl) < 1e-9 and st["bid_moved"] and st["last_bid_eq_fill"]:
            klass = "EXIT_PRICE_FALLBACK"
        elif abs(pnl) < 1e-9 and st["bid_moved"] and not st["last_bid_eq_fill"]:
            klass = "RUNTIME_STATE_NOT_UPDATED"
        elif abs(pnl) < 1e-9:
            klass = "OTHER"
        else:
            klass = "NON_DRAW"
        rows.append({**{k: t[k] for k in t}, "capture": st, "class": klass})
        print(
            t["symbol"], t["anchor_time"], t["session"], t["exit_reason"],
            "pnl", pnl, "ticks", st["n_ticks"], "uniq_bid", st["n_unique_bid"], klass,
            flush=True,
        )
    out["20260731"] = {"capture": s31.get("capture"), "n_lines": s31.get("n_lines"), "rows": rows}

    # Top profit trades (top 5 per day)
    top_days = ["20260722", "20260731", "20260804"]
    top_out = {}
    for day in top_days:
        ts = sorted(
            [t for t in trades if t["date"] == day],
            key=lambda x: -float(x["pnl_yen_100"]),
        )
        pick = ts[:5]
        wanted = {t["symbol"] for t in pick}
        windows = [(t["symbol"], float(t["fill_time"]), float(t["exit_time"])) for t in pick]
        print("scan", day, wanted, flush=True)
        sc = scan_day(day, wanted, windows)
        checked = []
        for t in pick:
            st = window_stats(
                (sc.get("ticks") or {}).get(t["symbol"]) or [],
                float(t["fill_time"]),
                float(t["exit_time"]),
                float(t["fill_price"]),
            )
            calc = (float(t["exit_price"]) - float(t["fill_price"])) * 100.0
            # entry/exit vs first/last bid
            checked.append(
                {
                    "trade": t,
                    "capture": st,
                    "lot100_ok": abs(calc - float(t["pnl_yen_100"])) < 1e-6,
                    "entry_vs_first_bid": st.get("first_bid"),
                    "exit_vs_last_bid": st.get("last_bid"),
                    "entry_match": st.get("first_bid") is not None
                    and abs(float(st["first_bid"]) - float(t["fill_price"])) < 1e-4,
                    "exit_near_last_bid": st.get("last_bid") is not None
                    and abs(float(st["last_bid"]) - float(t["exit_price"])) < 1e-4,
                }
            )
            print(
                day, t["symbol"], t["anchor_time"], t["pnl_yen_100"],
                "entry_match", checked[-1]["entry_match"], "exit_near_last",
                checked[-1]["exit_near_last_bid"], "uniq_bid", st["n_unique_bid"],
                flush=True,
            )
        top_out[day] = {"capture": sc.get("capture"), "checked": checked}
    out["top_days"] = top_out

    dest = ROOT / "results" / "research" / "current_runtime_baseline_semantic_audit_p1qa" / "_scan.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, ensure_ascii=False, default=str), encoding="utf-8")
    print("wrote", dest, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
