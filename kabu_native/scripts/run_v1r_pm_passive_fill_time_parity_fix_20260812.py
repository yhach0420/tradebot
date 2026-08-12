#!/usr/bin/env python
"""Fix verification: V1R Passive Fill time-parity (Capture received_at axis).

No strategy / wait / ask-cross rule changes — runtime clock only.
Uses Frozen find_ask_cross_fill for research side; corrected V1RNativeEntryLive path
for live side (board.t = ingress/Capture received_at).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from research.e1_x28_executable_joint.board import BOARD_FRESHNESS_SEC, MIN_QTY, load_board_events
from research.e1_x34a_execution_policy.arms import find_ask_cross_fill
from small_paper.v1r_native_entry_live import (
    PendingOrder,
    V1RNativeEntryLive,
    board_event_epoch_from_payload,
    extract_board_row,
)
from small_paper.v1r_primary_runtime import WAIT_SEC

JST = ZoneInfo("Asia/Tokyo")
DAY = "20260812"
DAY_DASH = "2026-08-12"
OUT = ROOT / "results" / "research" / "v1r_pm_passive_fill_time_parity_fix_20260812"
OUT.mkdir(parents=True, exist_ok=True)

PM_SESSIONS = [
    "live_session_122528",
    "live_session_125417",
    "live_session_145248",
]
CAPTURE_SESSION = (
    ROOT
    / "data"
    / "market_capture"
    / DAY
    / "session_ing_20260812_27200_1786495165_905008bd"
)


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


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x or x <= 0:
            return None
        return x
    except (TypeError, ValueError):
        return None


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def extract_pm_pendings() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for sess_name in PM_SESSIONS:
        sess = ROOT / "results" / "small_paper" / DAY / sess_name
        native = _load_jsonl(sess / "v1r_native_entry_trace.jsonl")
        pendings = [r for r in native if r.get("kind") == "V1R_ENTRY_PENDING"]
        for p in pendings:
            sym = str(p.get("symbol"))
            anchor = str(p.get("anchor"))
            # signal_time from paired EXPIRED/FILL if present, else minute floor
            hh, mm = map(int, anchor.split(":"))
            t0 = datetime(2026, 8, 12, hh, mm, tzinfo=JST).timestamp()
            for r in native:
                if r.get("symbol") == sym and r.get("anchor") == anchor:
                    if r.get("kind") in ("V1R_EXPIRED", "V1R_FILL") and r.get("signal_time"):
                        t0 = float(r["signal_time"])
                        break
            out.append(
                {
                    "symbol": sym,
                    "anchor": anchor,
                    "limit": float(p.get("limit")),
                    "signal_time": float(t0),
                    "session": sess_name,
                    "expiry_time": float(t0) + float(WAIT_SEC),
                }
            )
    # stable unique by symbol|anchor (first session wins if dup — should not)
    seen = set()
    uniq = []
    for r in out:
        k = (r["symbol"], r["anchor"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return uniq


def load_capture_boards(symbols: set[str]) -> dict[str, dict[str, np.ndarray]]:
    """Build Frozen-shaped boards keyed by symbol using Capture received_at as board.t."""
    acc: dict[str, list[dict[str, Any]]] = {s: [] for s in symbols}
    parts = sorted(CAPTURE_SESSION.glob("push_part_*.jsonl"))
    for part in parts:
        with part.open(encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                # cheap filter
                if not any(f'"{s}"' in line or f":\"{s}\"" in line for s in symbols):
                    # also allow without quotes edge cases
                    if not any(s in line for s in symbols):
                        continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                sym = str(rec.get("symbol") or "").replace(".T", "")
                if sym not in symbols:
                    continue
                recv = _ts(rec.get("received_at"))
                if recv is None:
                    continue
                pay = rec.get("payload") or {}
                row = extract_board_row(dict(pay), float(recv))
                # need both ask+bid finite for research-like boards; fill only needs ask
                acc[sym].append(row)
    out: dict[str, dict[str, np.ndarray]] = {}
    for sym, rows in acc.items():
        if not rows:
            out[sym] = {
                "t": np.empty(0),
                "ask": np.empty(0),
                "bid": np.empty(0),
                "ask_qty": np.empty(0),
                "bid_qty": np.empty(0),
                "special": np.empty(0, dtype=bool),
                "fresh_sec": np.empty(0),
            }
            continue
        rows.sort(key=lambda r: r["t"])
        out[sym] = {
            "t": np.asarray([r["t"] for r in rows], dtype=float),
            "ask": np.asarray([r["ask"] for r in rows], dtype=float),
            "bid": np.asarray([r["bid"] for r in rows], dtype=float),
            "ask_qty": np.asarray([r["ask_qty"] for r in rows], dtype=float),
            "bid_qty": np.asarray([r["bid_qty"] for r in rows], dtype=float),
            "special": np.asarray([r["special"] for r in rows], dtype=bool),
            "fresh_sec": np.asarray([r["fresh_sec"] for r in rows], dtype=float),
        }
    return out


def iter_capture_pushes(symbol: str, t0: float, t1: float) -> list[dict[str, Any]]:
    """Capture pushes for symbol with received_at in [t0-2, t1+2] for replay."""
    rows = []
    for part in sorted(CAPTURE_SESSION.glob("push_part_*.jsonl")):
        with part.open(encoding="utf-8") as f:
            for line in f:
                if symbol not in line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if str(rec.get("symbol") or "").replace(".T", "") != symbol:
                    continue
                recv = _ts(rec.get("received_at"))
                if recv is None:
                    continue
                if recv < t0 - 2 or recv > t1 + 2:
                    continue
                pay = dict(rec.get("payload") or {})
                pay["received_at"] = rec.get("received_at")
                pay["recorded_at"] = rec.get("received_at")
                rows.append({"t": float(recv), "payload": pay, "received_at": rec.get("received_at")})
    rows.sort(key=lambda r: r["t"])
    return rows


def live_replay_pending(p: dict[str, Any], pushes: list[dict[str, Any]]) -> dict[str, Any]:
    """Corrected runtime path: ingest Capture stamps → find_ask_cross_fill via on_tick_fill_check."""
    eng = V1RNativeEntryLive(
        universe=[p["symbol"]],
        score_fn=lambda feats: 0.0,
        model_ser={},
        ready=True,
    )
    eng.pending[p["symbol"]] = PendingOrder(
        symbol=p["symbol"],
        signal_time=float(p["signal_time"]),
        limit_price=float(p["limit"]),
        score=1.0,
        rank=1,
        anchor=p["anchor"],
        session="PM",
        date=DAY,
    )
    outcomes: list[dict[str, Any]] = []
    for push in pushes:
        et = board_event_epoch_from_payload(push["payload"], fallback=push["t"])
        eng.ingest_push(symbol=p["symbol"], payload=push["payload"], event_t=et)
        outcomes.extend(eng.on_tick_fill_check(event_t=et, payload=push["payload"]))
        if p["symbol"] not in eng.pending:
            break
    # if still pending after window pushes, force a past-window tick
    if p["symbol"] in eng.pending:
        past = float(p["signal_time"]) + float(WAIT_SEC) + 0.001
        outcomes.extend(eng.on_tick_fill_check(event_t=past))
    fill_ev = next((o for o in outcomes if o.get("kind") == "V1R_FILL"), None)
    exp_ev = next((o for o in outcomes if o.get("kind") == "V1R_EXPIRED"), None)
    if fill_ev:
        return {
            "result": "FILL",
            "fill_time": float(fill_ev["fill_time"]),
            "fill_price": float(fill_ev["fill_price"]),
            "notify_kinds": [n["kind"] for n in eng.notify_sink],
        }
    return {
        "result": "EXPIRED",
        "fill_time": None,
        "fill_price": None,
        "notify_kinds": [n["kind"] for n in eng.notify_sink],
        "expire": exp_ev,
    }


def iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    return datetime.fromtimestamp(float(ts), JST).isoformat(timespec="milliseconds")


def main() -> int:
    pendings = extract_pm_pendings()
    assert len(pendings) == 40, f"expected 40 PM pendings, got {len(pendings)}"
    symbols = {p["symbol"] for p in pendings}
    print(f"loading Capture boards for {len(symbols)} symbols…", flush=True)
    cap_boards = load_capture_boards(symbols)

    # Also load push_jsonl boards for lineage note
    pj_boards = {s: load_board_events(DAY, s) for s in symbols}

    rows = []
    for i, p in enumerate(pendings, 1):
        sym = p["symbol"]
        t0 = float(p["signal_time"])
        lim = float(p["limit"])
        board = cap_boards[sym]
        research = find_ask_cross_fill(
            board, t0=t0, wait_sec=WAIT_SEC, limit_price=lim, sess_end=t0 + 3600
        )
        pushes = iter_capture_pushes(sym, t0, t0 + WAIT_SEC)
        live = live_replay_pending(p, pushes)

        # push_jsonl research (legacy axis) for lineage
        pj = find_ask_cross_fill(
            pj_boards[sym], t0=t0, wait_sec=WAIT_SEC, limit_price=lim, sess_end=t0 + 3600
        )

        r_res = "FILL" if research.get("filled") else "EXPIRED"
        l_res = live["result"]
        r_ft = float(research["fill_t"]) if research.get("filled") else None
        l_ft = live.get("fill_time")
        r_fp = float(research["fill_price"]) if research.get("filled") else None
        l_fp = live.get("fill_price")
        match = (
            r_res == l_res
            and (
                r_res == "EXPIRED"
                or (
                    r_ft is not None
                    and l_ft is not None
                    and abs(r_ft - l_ft) < 1e-6
                    and r_fp is not None
                    and l_fp is not None
                    and abs(r_fp - l_fp) < 1e-9
                )
            )
        )
        row = {
            "idx": i,
            "symbol": sym,
            "anchor": p["anchor"],
            "limit": lim,
            "signal_time_iso": iso(t0),
            "expiry_iso": iso(t0 + WAIT_SEC),
            "research": r_res,
            "research_fill_time": iso(r_ft),
            "research_fill_price": r_fp,
            "live": l_res,
            "live_fill_time": iso(l_ft),
            "live_fill_price": l_fp,
            "match": match,
            "diff": "MATCH" if match else f"RESEARCH_{r_res}_LIVE_{l_res}",
            "push_jsonl_research": "FILL" if pj.get("filled") else "EXPIRED",
            "push_jsonl_fill_time": iso(float(pj["fill_t"])) if pj.get("filled") else None,
            "capture_push_n": len(pushes),
            "session": p["session"],
        }
        rows.append(row)
        print(
            f"{i:02d} {sym}@{p['anchor']} R={r_res} L={l_res} match={match}",
            flush=True,
        )

    # 285A focused
    m285 = next(r for r in rows if r["symbol"] == "285A" and r["anchor"] == "13:20")

    # timeline for 285A
    t0_285 = datetime(2026, 8, 12, 13, 20, tzinfo=JST).timestamp()
    timeline = []
    for push in iter_capture_pushes("285A", t0_285, t0_285 + 1.0):
        pay = push["payload"]
        s1 = pay.get("Sell1") or {}
        timeline.append(
            {
                "received_at": push["received_at"],
                "Sell1.Price": s1.get("Price"),
                "Sell1.Qty": s1.get("Qty"),
                "CurrentPriceTime": pay.get("CurrentPriceTime"),
                "SpecialQuote": pay.get("SpecialQuote"),
                "board_event_epoch": board_event_epoch_from_payload(pay),
            }
        )

    r_fills = sum(1 for r in rows if r["research"] == "FILL")
    l_fills = sum(1 for r in rows if r["live"] == "FILL")
    matches = sum(1 for r in rows if r["match"])
    diffs = Counter(r["diff"] for r in rows)

    # Historical sample: X34A planned passive rows if available
    hist = {"note": "fill-only parity on Capture axis for 8/12 PM; X34A rates unchanged"}
    x34a = ROOT / "results" / "research" / "e1_x34a_execution_policy" / "report.json"
    if x34a.exists():
        rep = json.loads(x34a.read_text(encoding="utf-8"))
        hist["x34a_passive_fill_rate"] = (
            ((rep.get("passive") or {}).get("full") or {}).get("fill_rate")
            or ((rep.get("arms") or {}).get("PASSIVE_BID_CONSERVATIVE") or {}).get("fill_rate")
        )

    # Downstream: 285A FILL notify + dual-lane admit (no live submit)
    down = {}
    p285 = next(p for p in pendings if p["symbol"] == "285A" and p["anchor"] == "13:20")
    live285 = live_replay_pending(p285, iter_capture_pushes("285A", t0_285, t0_285 + 1.0))
    down["fill"] = live285["result"] == "FILL"
    down["notify_has_FILL"] = "FILL" in live285.get("notify_kinds", [])
    down["fill_price"] = live285.get("fill_price")
    down["fill_time"] = iso(live285.get("fill_time"))
    # Discord routing unit (kind map) without network
    try:
        from notify.v1r_discord_routing import ROUTING_TABLE, V1RNotifyKind

        route = ROUTING_TABLE[V1RNotifyKind.FILL]
        down["discord_FILL_channel"] = route.get("channel")
        down["discord_FILL_is_trade_notify"] = route.get("channel") == "trade-notify"
    except Exception as e:
        down["discord_error"] = f"{type(e).__name__}:{e}"
        down["discord_FILL_expected"] = "trade-notify"

    verdict = (
        "V1R_PASSIVE_FILL_TIME_PARITY_FIXED"
        if matches == 40 and m285["match"] and m285["research"] == "FILL" and m285["live"] == "FILL"
        else "V1R_PASSIVE_FILL_RUNTIME_PARITY_NOT_READY"
    )

    summary = {
        "day": DAY,
        "day_status": "INVALID / OPERATIONAL_VALIDATION_ONLY",
        "verdict": verdict,
        "wait_sec": WAIT_SEC,
        "time_axis": {
            "research_board_t": "Capture received_at (causal ingress)",
            "live_board_t": "Capture/ingress received_at via recorded_at propagation",
            "t0": "anchor minute floor (signal_time)",
            "window": "[t0, t0+wait] inclusive for fill; expire when event_t > lim_t",
            "legacy_push_jsonl_note": "push_jsonl recorded_at was paper wall at async write; lagged Capture",
        },
        "research": {
            "pending": 40,
            "fills": r_fills,
            "expired": 40 - r_fills,
            "fill_rate": r_fills / 40,
        },
        "live": {
            "pending": 40,
            "fills": l_fills,
            "expired": 40 - l_fills,
            "fill_rate": l_fills / 40,
        },
        "matches": matches,
        "diffs": dict(diffs),
        "case_285A_13_20": m285,
        "timeline_285A": timeline[:20],
        "downstream": down,
        "historical_sanity": hist,
        "submit_cancel_live": "0/0/0",
        "strategy_mutation": False,
        "pbv2_fallback": False,
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "parity_rows.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        f"# V1R Passive Fill time-parity fix — {DAY}",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        f"- research fills/expired: {r_fills}/{40 - r_fills}",
        f"- live fills/expired: {l_fills}/{40 - l_fills}",
        f"- MATCH: {matches}/40",
        f"- 285A@13:20: research={m285['research']} live={m285['live']} "
        f"ft_r={m285['research_fill_time']} ft_l={m285['live_fill_time']}",
        f"- submit/cancel/live: 0/0/0",
        "",
        "| # | symbol | anchor | limit | research | live | diff |",
        "|---|--------|--------|------:|----------|------|------|",
    ]
    for r in rows:
        lines.append(
            f"| {r['idx']} | {r['symbol']} | {r['anchor']} | {r['limit']} | "
            f"{r['research']} | {r['live']} | {r['diff']} |"
        )
    (OUT / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"verdict": verdict, "matches": matches, "r_fills": r_fills, "l_fills": l_fills}, indent=2))
    return 0 if verdict == "V1R_PASSIVE_FILL_TIME_PARITY_FIXED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
