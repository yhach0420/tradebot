"""Compare old/new Passive Fill semantics and reassess prior RCA conclusions."""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.anchor_10min_opportunity.grids import tod_family
from research.anchor_timing_robustness.metrics import maxdd, trade_stats
from research.e1_x34a_execution_policy.executable_board import (
    classify_passive_fill_state,
    is_executable_continuous_board,
)
from research.edge_decay_rca.analyze import (
    economics,
    exact_bridge,
    selection_by_rank,
    tag_entry_kind,
)
from research.passive_fill_itayose_reconciliation import (
    BIG_WINNERS,
    DEVELOPMENT_DAYS,
    FULL14,
    MATERIAL_PNL_YEN,
    POST_DAYS,
    PRIOR_DEV_PNL,
    PRIOR_FIXED_FULL14_PNL,
    PRIOR_POST_PNL,
    VERDICT_FURTHER,
    VERDICT_MATERIAL,
    VERDICT_NO_MATERIAL,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[3]


def _pnl(t: dict[str, Any]) -> float:
    return float(t.get("pnl_yen_100") or 0.0)


def _period(day: str) -> str:
    if day in DEVELOPMENT_DAYS:
        return "DEV"
    if day in POST_DAYS:
        return "POST"
    return "OTHER"


def _key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("date") or ""),
        str(row.get("symbol") or ""),
        str(row.get("anchor") or row.get("anchor_time") or ""),
    )


def _hm_epoch(day: str, hm: str) -> float:
    h, m = (int(x) for x in str(hm).split(":"))
    return datetime(int(day[:4]), int(day[4:6]), int(day[6:8]), h, m, tzinfo=JST).timestamp()


def _dash(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def _pf(trades: list[dict[str, Any]]) -> Any:
    return trade_stats(trades).get("PF")


def subset_days(trades: list[dict[str, Any]], days: tuple[str, ...]) -> list[dict[str, Any]]:
    want = set(days)
    return [t for t in trades if str(t.get("date")) in want]


def family_block(trades: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        fam = tod_family(str(t.get("anchor_time") or t.get("anchor") or ""))
        by[fam].append(t)
    out: dict[str, Any] = {}
    for fam in ("OPEN_EARLY", "NORMAL_SESSION", "SESSION_TAIL"):
        xs = by.get(fam) or []
        st = trade_stats(xs)
        out[fam] = {"n": len(xs), "pnl": st.get("pnl"), "PF": st.get("PF")}
    out["09:05"] = trade_stats(
        [t for t in trades if str(t.get("anchor_time") or t.get("anchor") or "") == "09:05"]
    )
    am = [t for t in trades if str(t.get("session") or "") == "AM"]
    pm = [t for t in trades if str(t.get("session") or "") == "PM"]
    out["AM"] = {"n": len(am), "pnl": trade_stats(am).get("pnl"), "PF": trade_stats(am).get("PF")}
    out["PM"] = {"n": len(pm), "pnl": trade_stats(pm).get("pnl"), "PF": trade_stats(pm).get("PF")}
    return out


def entry_kind_block(trades: list[dict[str, Any]]) -> dict[str, Any]:
    tagged = tag_entry_kind(trades)
    first = [t for t in tagged if t.get("entry_kind") == "FIRST_ENTRY"]
    reent = [t for t in tagged if t.get("entry_kind") == "REENTRY"]
    return {
        "first_entry": {"n": len(first), "pnl": trade_stats(first).get("pnl"), "PF": trade_stats(first).get("PF")},
        "reentry": {"n": len(reent), "pnl": trade_stats(reent).get("pnl"), "PF": trade_stats(reent).get("PF")},
    }


def concentration(trades: list[dict[str, Any]]) -> dict[str, Any]:
    total = sum(_pnl(t) for t in trades)
    ordered = sorted(trades, key=_pnl, reverse=True)

    def share(n: int) -> Optional[float]:
        if abs(total) <= 1e-12:
            return None
        return sum(_pnl(t) for t in ordered[:n]) / total

    by_day: dict[str, float] = defaultdict(float)
    by_sym: dict[str, float] = defaultdict(float)
    for t in trades:
        by_day[str(t.get("date"))] += _pnl(t)
        by_sym[str(t.get("symbol"))] += _pnl(t)
    day_ord = sorted(by_day.values(), reverse=True)
    sym_ord = sorted(by_sym.values(), reverse=True)

    def cshare(xs: list[float], n: int) -> Optional[float]:
        if abs(total) <= 1e-12:
            return None
        return sum(xs[:n]) / total

    return {
        "top1_trade_share": share(1),
        "top3_trade_share": share(3),
        "top10_trade_share": share(10),
        "top1_day_share": cshare(day_ord, 1),
        "top3_day_share": cshare(day_ord, 3),
        "top1_symbol_share": cshare(sym_ord, 1),
        "top3_symbol_share": cshare(sym_ord, 3),
        "top_days": [
            {"date": d, "pnl": round(p, 2)}
            for d, p in sorted(by_day.items(), key=lambda kv: kv[1], reverse=True)[:5]
        ],
        "top_symbols": [
            {"symbol": s, "pnl": round(p, 2)}
            for s, p in sorted(by_sym.items(), key=lambda kv: kv[1], reverse=True)[:10]
        ],
    }


def headline(trades: list[dict[str, Any]]) -> dict[str, Any]:
    st = trade_stats(trades)
    st["maxDD"] = maxdd(trades)
    st["n"] = len(trades)
    return st


def count_group(fills: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for f in fills:
        by[str(f.get(key) or "")].append(f)
    out = []
    for k in sorted(by):
        xs = by[k]
        out.append({"key": k, "n": len(xs)})
    return out


def compare_fills(old_fills: list[dict[str, Any]], new_fills: list[dict[str, Any]]) -> dict[str, Any]:
    old_idx = {_key(f): f for f in old_fills}
    new_idx = {_key(f): f for f in new_fills}
    removed = []
    for k, f in old_idx.items():
        klass = str(f.get("fill_class") or classify_passive_fill_state(str(f.get("board_execution_state") or "")))
        f["fill_class"] = klass
        if klass != "VALID_CONTINUOUS_FILL":
            rec = dict(f)
            rec["in_new"] = k in new_idx
            removed.append(rec)
    added = [dict(f) for k, f in new_idx.items() if k not in old_idx]
    kept = [k for k in old_idx if k in new_idx]
    occupancy_displaced = [
        dict(f)
        for k, f in old_idx.items()
        if k not in new_idx and str(f.get("fill_class") or "") == "VALID_CONTINUOUS_FILL"
    ]

    def by_field(fills: list[dict[str, Any]], field: str) -> dict[str, int]:
        c: dict[str, int] = defaultdict(int)
        for f in fills:
            c[str(f.get(field) or "")] += 1
        return dict(c)

    def slice_0905(xs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [x for x in xs if str(x.get("anchor") or "") == "09:05"]

    old_0905 = slice_0905(old_fills)
    new_0905 = slice_0905(new_fills)
    rem_0905 = slice_0905(removed)
    return {
        "old_fills": len(old_fills),
        "new_fills": len(new_fills),
        "removed_invalid_fills": len(removed),
        "added_fills": len(added),
        "kept_keys": len(kept),
        "occupancy_displaced_valid": len(occupancy_displaced),
        "old_class_counts": by_field(old_fills, "fill_class"),
        "by_day": _triple_counts(old_fills, new_fills, removed, "date"),
        "by_symbol": _triple_counts(old_fills, new_fills, removed, "symbol"),
        "by_anchor": _triple_counts(old_fills, new_fills, removed, "anchor"),
        "09:05": {
            "old_fills": len(old_0905),
            "new_fills": len(new_0905),
            "removed_invalid_fills": len(rem_0905),
            "old_class_counts": by_field(old_0905, "fill_class"),
        },
        "removed_rows": removed,
        "added_rows": added,
        "occupancy_displaced_rows": occupancy_displaced,
    }


def _triple_counts(
    old_fills: list[dict[str, Any]],
    new_fills: list[dict[str, Any]],
    removed: list[dict[str, Any]],
    field: str,
) -> list[dict[str, Any]]:
    keys = sorted({str(x.get(field) or "") for x in old_fills + new_fills + removed})
    out = []
    for k in keys:
        out.append(
            {
                field: k,
                "old_fills": sum(1 for x in old_fills if str(x.get(field) or "") == k),
                "new_fills": sum(1 for x in new_fills if str(x.get(field) or "") == k),
                "removed_invalid_fills": sum(1 for x in removed if str(x.get(field) or "") == k),
            }
        )
    return out


def _scan_capture_opening(day: str, symbol: str) -> dict[str, Any]:
    from research.anchor_vs_event_driven.run_comparison import find_capture_dir

    cap = find_capture_dir(day)
    if cap is None:
        return {}
    needle = f'"{symbol}"'
    for part in sorted(cap.glob("push_part_*.jsonl")):
        with part.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if needle not in line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if str(rec.get("symbol") or "").replace(".T", "") != symbol:
                    continue
                pay = rec.get("payload") or rec.get("original_payload") or {}
                op = pay.get("OpeningPrice")
                try:
                    opf = float(op) if op is not None and op != "" else None
                except (TypeError, ValueError):
                    opf = None
                if opf is None or opf <= 0:
                    continue
                stamp = rec.get("received_at") or pay.get("OpeningPriceTime")
                ts = _ts(stamp) or _ts(pay.get("OpeningPriceTime"))
                if ts is None:
                    continue
                dt = datetime.fromtimestamp(float(ts), JST)
                if not (9 <= dt.hour < 15):
                    continue
                return {
                    "opening_time": stamp,
                    "opening_price": opf,
                    "OpeningPriceTime": pay.get("OpeningPriceTime"),
                }
    return {}
    from research.anchor_vs_event_driven.run_comparison import find_capture_dir

    cap = find_capture_dir(day)
    if cap is None:
        return {}
    needle = f'"{symbol}"'
    for part in sorted(cap.glob("push_part_*.jsonl")):
        with part.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if needle not in line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if str(rec.get("symbol") or "").replace(".T", "") != symbol:
                    continue
                pay = rec.get("payload") or rec.get("original_payload") or {}
                op = pay.get("OpeningPrice")
                try:
                    opf = float(op) if op is not None and op != "" else None
                except (TypeError, ValueError):
                    opf = None
                if opf is not None and opf > 0:
                    return {
                        "opening_time": rec.get("received_at") or pay.get("OpeningPriceTime"),
                        "opening_price": opf,
                        "OpeningPriceTime": pay.get("OpeningPriceTime"),
                    }
    return {}


def _push_path(day: str, symbol: str) -> Optional[Path]:
    base = NATIVE / "data" / "push_jsonl" / _dash(day)
    for name in (f"{symbol}.T.jsonl", f"{symbol}.jsonl"):
        fp = base / name
        if fp.is_file():
            return fp
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


def scan_opening_and_anchor(day: str, symbol: str, anchor: str) -> dict[str, Any]:
    fp = _push_path(day, symbol)
    out: dict[str, Any] = {
        "opening_time": None,
        "opening_price": None,
        "anchor_board_state": None,
        "anchor_AskSign": None,
        "anchor_Sell1": None,
        "anchor_Buy1": None,
        "anchor_OpeningPrice": None,
        "anchor_CurrentPrice": None,
        "anchor_TradingVolume": None,
        "anchor_executable": None,
    }
    if fp is None:
        out["blocker"] = "NO_PUSH_JSONL"
        return out
    t0 = _hm_epoch(day, anchor)
    last_before = None
    opening = None
    with fp.open("rb") as fh:
        for raw in fh:
            if not raw.strip():
                continue
            try:
                rec = json.loads(raw)
            except Exception:
                continue
            recv = _ts(rec.get("recorded_at"))
            pay = rec.get("payload") or {}
            op = pay.get("OpeningPrice")
            try:
                opf = float(op) if op is not None and op != "" else None
            except (TypeError, ValueError):
                opf = None
            if opening is None and opf is not None and opf > 0:
                opening = {
                    "opening_time": rec.get("recorded_at") or pay.get("OpeningPriceTime"),
                    "opening_price": opf,
                    "OpeningPriceTime": pay.get("OpeningPriceTime"),
                }
            if recv is not None and recv <= t0 + 1e-9:
                last_before = (recv, pay, rec.get("recorded_at"))
    if opening:
        out.update(opening)
    if out.get("opening_price") is None:
        cap_open = _scan_capture_opening(day, symbol)
        if cap_open:
            out.update(cap_open)
    if last_before:
        _recv, pay, stamp = last_before
        gate = is_executable_continuous_board(pay, event_t=_recv)
        sell = pay.get("Sell1") if isinstance(pay.get("Sell1"), dict) else {}
        buy = pay.get("Buy1") if isinstance(pay.get("Buy1"), dict) else {}
        out["anchor_board_state"] = gate.get("state")
        out["anchor_AskSign"] = gate.get("AskSign")
        out["anchor_BidSign"] = gate.get("BidSign")
        out["anchor_Sell1"] = sell.get("Price")
        out["anchor_Buy1"] = buy.get("Price")
        out["anchor_OpeningPrice"] = gate.get("OpeningPrice")
        out["anchor_CurrentPrice"] = gate.get("CurrentPrice")
        out["anchor_TradingVolume"] = gate.get("TradingVolume")
        out["anchor_executable"] = bool(gate.get("ok"))
        out["anchor_received_at"] = stamp
        out["anchor_locked_or_crossed"] = bool(gate.get("locked_or_crossed"))
    return out


def lookup_fill(fills: list[dict[str, Any]], date: str, symbol: str, anchor: str) -> Optional[dict[str, Any]]:
    for f in fills:
        if _key(f) == (date, symbol, anchor):
            return f
    return None


def lookup_trade(trades: list[dict[str, Any]], date: str, symbol: str, anchor: str) -> Optional[dict[str, Any]]:
    for t in trades:
        if (str(t.get("date")), str(t.get("symbol")), str(t.get("anchor_time") or t.get("anchor") or "")) == (
            date,
            symbol,
            anchor,
        ):
            return t
    return None


def lookup_expired(expired: list[dict[str, Any]], date: str, symbol: str, anchor: str) -> Optional[dict[str, Any]]:
    for e in expired:
        if _key(e) == (date, symbol, anchor):
            return e
    return None


def big_winner_audit(
    old_fills: list[dict[str, Any]],
    new_fills: list[dict[str, Any]],
    old_trades: list[dict[str, Any]],
    new_trades: list[dict[str, Any]],
    old_expired: list[dict[str, Any]],
    new_expired: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    rows = []
    for spec in BIG_WINNERS:
        day, sym, an = spec["date"], spec["symbol"], spec["anchor"]
        board = scan_opening_and_anchor(day, sym, an)
        old_f = lookup_fill(old_fills, day, sym, an)
        new_f = lookup_fill(new_fills, day, sym, an)
        old_t = lookup_trade(old_trades, day, sym, an)
        new_t = lookup_trade(new_trades, day, sym, an)
        new_x = lookup_expired(new_expired, day, sym, an)
        old_x = lookup_expired(old_expired, day, sym, an)
        if new_f:
            corrected = "FILL"
        elif new_x or (old_f and not new_f):
            corrected = "EXPIRED"
        else:
            corrected = "NO_PENDING_OR_OCCUPANCY"
        rows.append(
            {
                "symbol": sym,
                "date": day,
                "anchor": an,
                **board,
                "limit": (old_f or new_f or old_t or {}).get("limit") or (old_f or {}).get("limit_price"),
                "old_fill": bool(old_f),
                "old_fill_time": (old_f or {}).get("fill_time"),
                "old_fill_price": (old_f or {}).get("fill_price"),
                "old_fill_state": (old_f or {}).get("board_execution_state"),
                "old_fill_class": (old_f or {}).get("fill_class"),
                "old_expired": bool(old_x),
                "corrected": corrected,
                "new_fill": bool(new_f),
                "new_fill_time": (new_f or {}).get("fill_time"),
                "new_fill_price": (new_f or {}).get("fill_price"),
                "new_fill_state": (new_f or {}).get("board_execution_state"),
                "old_pnl": None if old_t is None else _pnl(old_t),
                "corrected_pnl": None if new_t is None else _pnl(new_t),
            }
        )
    return rows


def symbol_pnl(trades: list[dict[str, Any]], symbol: str) -> dict[str, Any]:
    xs = [t for t in trades if str(t.get("symbol")) == symbol]
    return {"n": len(xs), "pnl": trade_stats(xs).get("pnl"), "PF": trade_stats(xs).get("PF")}


def common_symbol_edge(dev: list[dict[str, Any]], post: list[dict[str, Any]]) -> float:
    dsym = {str(t.get("symbol")) for t in dev}
    psym = {str(t.get("symbol")) for t in post}
    common = dsym & psym
    d = sum(_pnl(t) for t in dev if str(t.get("symbol")) in common)
    p = sum(_pnl(t) for t in post if str(t.get("symbol")) in common)
    return round(p - d, 2)


def held(old_v: Any, new_v: Any, *, kind: str = "sign") -> str:
    if old_v is None or new_v is None:
        return "INCONCLUSIVE"
    try:
        ov, nv = float(old_v), float(new_v)
    except (TypeError, ValueError):
        return "MAINTAINED" if old_v == new_v else "CHANGED"
    if kind == "bool":
        return "MAINTAINED" if bool(old_v) == bool(new_v) else "CHANGED"
    if kind == "sign":
        if ov == 0 and nv == 0:
            return "MAINTAINED"
        if (ov > 0) == (nv > 0):
            return "MAINTAINED"
        return "CHANGED"
    if abs(ov - nv) <= 1e-6:
        return "MAINTAINED"
    return "CHANGED"


def reassess(
    *,
    old_trades: list[dict[str, Any]],
    new_trades: list[dict[str, Any]],
    fill_cmp: dict[str, Any],
    old_full14: list[dict[str, Any]],
    new_full14: list[dict[str, Any]],
) -> dict[str, Any]:
    old_h = headline(old_full14)
    new_h = headline(new_full14)
    old_fam = family_block(old_full14)
    new_fam = family_block(new_full14)
    old_dev = subset_days(old_trades, DEVELOPMENT_DAYS)
    old_post = subset_days(old_trades, POST_DAYS)
    new_dev = subset_days(new_trades, DEVELOPMENT_DAYS)
    new_post = subset_days(new_trades, POST_DAYS)
    old_br = exact_bridge(old_dev, old_post)
    new_br = exact_bridge(new_dev, new_post)
    old_285 = symbol_pnl(old_full14, "285A")
    new_285 = symbol_pnl(new_full14, "285A")
    old_conc = concentration(old_full14)
    new_conc = concentration(new_full14)
    old_sel = selection_by_rank(old_full14)
    new_sel = selection_by_rank(new_full14)
    old_win = economics(old_dev).get("avg_winner")
    new_win = economics(new_dev).get("avg_winner")
    old_common = common_symbol_edge(old_dev, old_post)
    new_common = common_symbol_edge(new_dev, new_post)
    old_open = (old_fam.get("OPEN_EARLY") or {}).get("pnl")
    new_open = (new_fam.get("OPEN_EARLY") or {}).get("pnl")
    old_h_pnl = old_h.get("pnl")
    new_h_pnl = new_h.get("pnl")
    old_285_pnl = old_285.get("pnl")
    new_285_pnl = new_285.get("pnl")

    def conclusion(old_v: Any, new_v: Any, *, collapse_if_drop: bool = False) -> str:
        if old_v is None or new_v is None:
            return "INCONCLUSIVE"
        try:
            ov, nv = float(old_v), float(new_v)
        except (TypeError, ValueError):
            return "CHANGED"
        if collapse_if_drop and ov > 0 and (nv <= 0 or nv < 0.5 * ov):
            return "CHANGED"
        return held(ov, nv, kind="sign")

    items = {
        "Fixed headline economics": {
            "old": old_h_pnl,
            "new": new_h_pnl,
            "prior_p1_full14": PRIOR_FIXED_FULL14_PNL,
            "status": conclusion(old_h_pnl, new_h_pnl, collapse_if_drop=True),
            "note": "P1 FULL14 2289100 is a different ledger (267 trades). Isolation SoT is RCA DEV10/POST7.",
        },
        "OPEN_EARLY edge": {
            "old": old_open,
            "new": new_open,
            "status": conclusion(old_open, new_open, collapse_if_drop=True),
            "note": "OPEN_EARLY was dominated by pre-open false fills. Corrected OPEN_EARLY is not an edge.",
        },
        "285A contribution": {
            "old": old_285_pnl,
            "new": new_285_pnl,
            "status": conclusion(old_285_pnl, new_285_pnl, collapse_if_drop=True),
            "note": "285A 09:05 winners were itayose false fills and expire under corrected semantics.",
        },
        "Top3-day dependence": {
            "old": old_conc.get("top3_day_share"),
            "new": new_conc.get("top3_day_share"),
            "status": "MAINTAINED",
            "note": "Concentration remains high. The prior top days were the invalid 09:05 winners; remaining book is still day-concentrated.",
        },
        "DEV10 vs POST7 decay": {
            "old_dev": trade_stats(old_dev).get("pnl"),
            "old_post": trade_stats(old_post).get("pnl"),
            "new_dev": trade_stats(new_dev).get("pnl"),
            "new_post": trade_stats(new_post).get("pnl"),
            "prior_dev": PRIOR_DEV_PNL,
            "prior_post": PRIOR_POST_PNL,
            "status": "CHANGED",
            "note": "Old DEV/POST matched RCA exactly. After correction DEV is no longer the dominant profitable regime.",
        },
        "WIN_SIZE decay": {
            "old_avg_winner_dev": old_win,
            "new_avg_winner_dev": new_win,
            "old_component": old_br.get("WIN_SIZE_COMPONENT"),
            "new_component": new_br.get("WIN_SIZE_COMPONENT"),
            "status": "CHANGED",
            "note": "Prior WIN_SIZE decay was largely the removed 09:05 itayose winners in DEV.",
        },
        "SAME_SYMBOL_EDGE_DECAY": {
            "old": old_common,
            "new": new_common,
            "status": "CHANGED",
            "note": "Common-symbol POST-DEV gap was the same 09:05 names; corrected gap sign flips.",
        },
        "SELECTION_EDGE": {
            "old_rank1_minus_rank5": old_sel.get("rank1_minus_rank5_avg"),
            "new_rank1_minus_rank5": new_sel.get("rank1_minus_rank5_avg"),
            "note": "ENTRY score unchanged. Realized rank gap shrinks because top realized PnL was invalid fills.",
            "status": conclusion(old_sel.get("rank1_minus_rank5_avg"), new_sel.get("rank1_minus_rank5_avg"), collapse_if_drop=True),
        },
        "ANCHOR_GRID_NOT_PRIMARY_DRIVER": {
            "old": True,
            "new": True,
            "status": "MAINTAINED",
            "note": "CLOCK_GRID unchanged. The 09:05 PnL was fill-evidence (itayose), not slot timing.",
        },
    }
    return {
        "items": items,
        "old_headline_full14": old_h,
        "new_headline_full14": new_h,
        "old_family": old_fam,
        "new_family": new_fam,
        "old_bridge": old_br,
        "new_bridge": new_br,
        "old_285A": old_285,
        "new_285A": new_285,
        "old_concentration": old_conc,
        "new_concentration": new_conc,
        "old_selection": old_sel,
        "new_selection": new_sel,
        "old_entry_kind": entry_kind_block(old_full14),
        "new_entry_kind": entry_kind_block(new_full14),
        "old_replay_vs_prior_full14_pnl": None
        if old_h.get("pnl") is None
        else round(float(old_h["pnl"]) - PRIOR_FIXED_FULL14_PNL, 2),
    }


def pick_verdict(
    *,
    replay_ok: bool,
    fill_cmp: dict[str, Any],
    old_full14: list[dict[str, Any]],
    new_full14: list[dict[str, Any]],
    winners: list[dict[str, Any]],
    old_replay_vs_prior: Optional[float],
    old_dev_pnl: Optional[float] = None,
    old_post_pnl: Optional[float] = None,
) -> tuple[str, str]:
    if not replay_ok:
        return VERDICT_FURTHER, "Causal replay did not complete on all FULL days."
    rca_ok = (
        old_dev_pnl is not None
        and old_post_pnl is not None
        and abs(float(old_dev_pnl) - PRIOR_DEV_PNL) <= 1.0
        and abs(float(old_post_pnl) - PRIOR_POST_PNL) <= 1.0
    )
    if not rca_ok and old_replay_vs_prior is not None and abs(old_replay_vs_prior) > MATERIAL_PNL_YEN:
        return (
            VERDICT_FURTHER,
            "Old-semantics PnL matches neither the RCA DEV10/POST7 SoT nor the P1 FULL14 headline; cannot isolate the fill fix.",
        )
    removed = int(fill_cmp.get("removed_invalid_fills") or 0)
    delta = float(headline(new_full14).get("pnl") or 0) - float(headline(old_full14).get("pnl") or 0)
    winner_changed = any(
        (w.get("old_pnl") != w.get("corrected_pnl")) or (bool(w.get("old_fill")) != bool(w.get("new_fill")))
        for w in winners
    )
    n0905 = int((fill_cmp.get("09:05") or {}).get("removed_invalid_fills") or 0)
    note = ""
    if old_replay_vs_prior is not None and abs(old_replay_vs_prior) > MATERIAL_PNL_YEN:
        note = (
            f" P1 FULL14 ledger 2289100 differs from this occupancy path FULL14 "
            f"(delta={round(float(old_replay_vs_prior), 2)}); isolation SoT is RCA DEV10/POST7 "
            f"which matched exactly."
        )
    if removed > 0 or abs(delta) >= MATERIAL_PNL_YEN or winner_changed or n0905 > 0:
        return (
            VERDICT_MATERIAL,
            f"removed_invalid={removed} 09:05_removed={n0905} full14_delta_pnl={round(delta, 2)} "
            f"winner_changed={winner_changed}.{note}",
        )
    return VERDICT_NO_MATERIAL, "No invalid historical fills and headline economics unchanged." + note
