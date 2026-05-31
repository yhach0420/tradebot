#!/usr/bin/env python3
"""
Phase213c: Cohort stability review for Phase213b D (455 trades).

Post-hoc review only — no hard reject, no YAML changes.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import sys
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "kabu_native" / "src"))

from screening.morning_screen import calc_board_imbalance

JST = ZoneInfo("Asia/Tokyo")
BASE = REPO / "kabu_native" / "results" / "small_paper"
PUSH_ROOT = REPO / "kabu_native" / "data" / "push_jsonl"
OUT = REPO / "kabu_native" / "results" / "reports" / "phase213c_board_imbalance_cohort_stability_review.json"

IN_SAMPLE = frozenset(
    {
        "20260519/live_full_session_081047",
        "20260520/live_full_session_080745",
        "20260520/push_replay_001932",
        "20260520/push_replay_231314",
        "20260521/live_full_session_081418",
        "20260522/live_full_session_081229",
        "20260525/live_session_075733",
        "20260529/live_session_075135",
        "20260529/live_session_122541",
        "20260529/push_replay_002526",
        "20260529/push_replay_003645",
    }
)
OOS = frozenset(
    {
        "20260518",
        "20260518/push_replay_205219",
        "20260518/push_replay_212433",
        "20260518/push_replay_220451",
        "20260519/push_replay_225919",
        "20260520/push_replay_002323",
        "20260521/push_replay_004729",
        "20260528/live_session_082247",
        "20260528/live_session_122515",
    }
)
ALL_SESSIONS = sorted(IN_SAMPLE | OOS)

FOCUS_SYMBOLS = ("6203.T", "6659.T", "9348.T", "4888.T")

TV_MIN = 1e8
VWAP_DEV_MAX = 2.5
IMBALANCE_CUTOFF_20PCT = 0.56079  # Phase213b fixed combined p80
EXPECTED_COHORT_N = 455

EntrySnap = tuple[float, float, Optional[float], Optional[float], Optional[float]]
# ts, imbalance, trading_value, vwap, entry_px


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _parse_ts(ts: str) -> float:
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _pf(pnls: list[float]) -> Optional[float]:
    wins = sum(p for p in pnls if p > 0)
    loss = sum(p for p in pnls if p < 0)
    gl = abs(loss)
    if gl <= 0:
        return None if wins <= 0 else float("inf")
    return wins / gl


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
    jsonl = session_dir / "small_paper_events.jsonl"
    rows: list[dict[str, Any]] = []
    if not jsonl.is_file():
        return rows
    with jsonl.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _pair_trades(events: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    accepts: dict[tuple[str, str], dict[str, Any]] = {}
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for ev in events:
        et = ev.get("event_type")
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or "")
        key = (sym, ent)
        if et == "accepted":
            accepts[key] = ev
        elif et == "observer_exit" and key in accepts:
            pairs.append((accepts[key], ev))
    return pairs


def load_structural_trades(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def _load_phase71() -> Any:
    path = REPO / "kabu_native" / "scripts" / "run_phase71_split_momentum_fade_review.py"
    name = "phase71_replay_engine_p213c"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def replay_trades_from_events(p71: Any, session_dir: Path) -> list[dict[str, Any]]:
    """Replay combined_structural_exit_v1 trades from events (Phase78-compatible)."""
    v1_mode = "legacy"
    v1_ratio = 0.85
    events_path = session_dir / "small_paper_events.jsonl"
    if not events_path.is_file():
        return []
    events = p71._load_events(events_path)
    session_end = p71._session_end(events)
    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}
    completed: list[Any] = []

    def close_act(act: Any, *, close_time: str, close_price: float, reason: str) -> None:
        act.trade.close_time = close_time
        act.trade.close_price = close_price
        act.trade.close_reason = reason
        act.trade.realized_pnl_pct = p71._pnl_pct(act.trade.entry_price, close_price)
        act.trade.hold_duration_sec = round(max(0.0, p71._parse_ts(close_time) - act.entry_ts), 1)
        completed.append(act.trade)

    for ev in events:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        et = str(ev.get("event_type") or "")
        ent_raw = str(ev.get("entry_time") or "")
        ts = p71._parse_ts(ent_raw)
        price = float(ev.get("current_price") or 0)
        if price <= 0:
            continue
        st = sym_states.setdefault(sym, p71.SymState())

        if et == "accepted":
            if sym in active:
                old = active.pop(sym)
                close_act(old, close_time=ent_raw, close_price=price, reason="overlap_replaced_review")
            comps = p71._components(st, ts=ts, price=price, ev=ev)
            tr = p71.StructuralTrade(sym, ent_raw, price, float(ev.get("continuation_quality_score") or 0))
            active[sym] = p71.ActiveTrade(
                trade=tr,
                entry_ts=ts,
                rich_ticks=[
                    {
                        "price": price,
                        "pnl_pct": 0.0,
                        "quality": comps["quality"],
                        "momentum": comps["momentum"],
                        "favorable": comps["favorable"],
                        "pure_price_momentum": comps["pure_price_momentum"],
                        "vwap_strength": comps["vwap_strength"],
                        "mfe_proxy": comps["mfe_proxy"],
                    }
                ],
            )
        elif et == "candidate" and sym in active:
            act = active[sym]
            comps = p71._components(st, ts=ts, price=price, ev=ev)
            act.rich_ticks.append(
                {
                    "price": price,
                    "pnl_pct": p71._pnl_pct(act.trade.entry_price, price),
                    "quality": comps["quality"],
                    "momentum": comps["momentum"],
                    "favorable": comps["favorable"],
                    "pure_price_momentum": comps["pure_price_momentum"],
                    "vwap_strength": comps["vwap_strength"],
                    "mfe_proxy": comps["mfe_proxy"],
                }
            )
            sig = p71.simulate_combined_split(
                act.rich_ticks,
                act.trade.entry_price,
                momentum_mode=v1_mode,
                ratio=v1_ratio,
                allow_session_end=False,
            )
            if sig:
                _, reason, _ = sig
                close_act(act, close_time=ent_raw, close_price=price, reason=reason)
                active.pop(sym, None)

    for act in list(active.values()):
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        close_act(act, close_time=session_end, close_price=float(last_px), reason="session_end")

    return [
        {
            "symbol": t.symbol,
            "entry_time": t.entry_time,
            "realized_pnl_pct": t.realized_pnl_pct,
            "close_reason": t.close_reason,
        }
        for t in completed
    ]


def _push_dir_for_day(day_stamp: str) -> Optional[Path]:
    y = f"{day_stamp[:4]}-{day_stamp[4:6]}-{day_stamp[6:8]}"
    p = PUSH_ROOT / y
    return p if p.is_dir() else None


def _push_dir(session_rel: str) -> Optional[Path]:
    day = session_rel.split("/")[0]
    return _push_dir_for_day(day)


def _day_stamp(session_rel: str, entry_time: str) -> str:
    if entry_time:
        try:
            dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
            return dt.astimezone(JST).strftime("%Y%m%d")
        except ValueError:
            pass
    return session_rel.split("/")[0]


def _week_label(day_stamp: str) -> str:
    dt = datetime.strptime(day_stamp, "%Y%m%d").replace(tzinfo=JST)
    iso = dt.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _vwap_dev(entry_px: float, vwap: Optional[float]) -> Optional[float]:
    if vwap and vwap > 0 and entry_px > 0:
        return round((entry_px - vwap) / vwap * 100.0, 4)
    return None


def _load_entry_series(push_dir: Path, symbol: str) -> list[EntrySnap]:
    sym = symbol.replace(".T", "")
    for name in (f"{symbol}.jsonl", f"{sym}.jsonl"):
        path = push_dir / name
        if path.is_file():
            break
    else:
        return []
    out: list[EntrySnap] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = _parse_ts(str(rec.get("recorded_at") or ""))
            payload = rec.get("payload") or {}
            imb = calc_board_imbalance(payload)
            if imb is None:
                continue
            px = _float(payload.get("CurrentPrice")) or _float(payload.get("CalcPrice"))
            out.append(
                (
                    ts,
                    float(imb),
                    _float(payload.get("TradingValue")),
                    _float(payload.get("VWAP")),
                    px,
                )
            )
    out.sort(key=lambda x: x[0])
    return out


def _lookup_at(series: list[EntrySnap], ts: float) -> Optional[EntrySnap]:
    if not series:
        return None
    times = [s[0] for s in series]
    i = bisect_right(times, ts) - 1
    if i < 0:
        return None
    return series[i]


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(r["pnl_pct"]) for r in rows]
    pf = _pf(pnls)
    total = round(sum(pnls), 4)
    return {
        "trade_count": len(rows),
        "profit_factor": round(pf, 4) if pf is not None and pf != float("inf") else pf,
        "total_pnl_pct": total,
        "avg_pnl_pct": round(total / len(pnls), 4) if pnls else None,
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
    }


def _share(part: float, whole: float) -> Optional[float]:
    if whole == 0:
        return None
    return round(100.0 * part / whole, 2)


def _passes_guards(tv: Optional[float], vwap_dev: Optional[float]) -> bool:
    if tv is None or tv < TV_MIN:
        return False
    if vwap_dev is not None and vwap_dev >= VWAP_DEV_MAX:
        return False
    return True


def _in_cohort(row: dict[str, Any]) -> bool:
    if not _passes_guards(row.get("trading_value"), row.get("entry_vwap_dev_pct")):
        return False
    imb = row.get("order_book_imbalance")
    return imb is not None and float(imb) >= IMBALANCE_CUTOFF_20PCT


def _load_session_trades(session_rel: str, p71: Any) -> tuple[list[dict[str, Any]], str]:
    sdir = BASE / session_rel
    if not sdir.is_dir():
        return [], "missing_dir"

    trades: list[dict[str, Any]] = []
    source = ""
    csv_path = sdir / "structural_trades.csv"
    if csv_path.is_file():
        for row in load_structural_trades(csv_path):
            trades.append(
                {
                    "symbol": str(row.get("symbol") or ""),
                    "entry_time": str(row.get("entry_time") or ""),
                    "pnl_pct": _float(row.get("realized_pnl_pct")) or 0.0,
                    "exit_reason": str(row.get("close_reason") or ""),
                }
            )
        source = "structural_trades.csv"
    if not trades:
        review_csv = sdir / "small_paper_trades_review.csv"
        if review_csv.is_file():
            for row in load_structural_trades(review_csv):
                trades.append(
                    {
                        "symbol": str(row.get("symbol") or ""),
                        "entry_time": str(row.get("entry_time") or ""),
                        "pnl_pct": _float(row.get("pnl_pct")) or _float(row.get("realized_pnl_pct")) or 0.0,
                        "exit_reason": str(row.get("exit_reason") or row.get("close_reason") or ""),
                    }
                )
            source = "small_paper_trades_review.csv"
    if not trades:
        raw = replay_trades_from_events(p71, sdir)
        for row in raw:
            trades.append(
                {
                    "symbol": str(row.get("symbol") or ""),
                    "entry_time": str(row.get("entry_time") or ""),
                    "pnl_pct": _float(row.get("realized_pnl_pct")) or _float(row.get("pnl_pct")) or 0.0,
                    "exit_reason": str(row.get("close_reason") or row.get("exit_reason") or ""),
                }
            )
        source = "replayed_v1_from_events" if trades else source
    if not trades:
        events = _load_events(sdir)
        for acc, ex in _pair_trades(events):
            trades.append(
                {
                    "symbol": str(acc.get("symbol") or ""),
                    "entry_time": str(acc.get("entry_time") or ""),
                    "pnl_pct": _float(ex.get("pnl_pct")) or 0.0,
                    "exit_reason": str(ex.get("exit_reason") or ""),
                }
            )
        source = "observer_exit_pairs" if trades else source
    return trades, source


def _enrich_trades(
    session_rel: str,
    trades: list[dict[str, Any]],
    book_cache: dict[tuple[str, str], list[EntrySnap]],
) -> list[dict[str, Any]]:
    push_dir_default = _push_dir(session_rel)
    out: list[dict[str, Any]] = []
    events_path_key = session_rel
    sdir = BASE / session_rel
    accept_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in _load_events(sdir):
        if ev.get("event_type") != "accepted":
            continue
        accept_by_key[(str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))] = ev

    for t in trades:
        sym = str(t.get("symbol") or "")
        entry_time = str(t.get("entry_time") or "")
        entry_ts = _parse_ts(entry_time)
        entry_day = _day_stamp(session_rel, entry_time)
        acc = accept_by_key.get((sym, entry_time), {})
        tv = _float(acc.get("trading_value"))
        vwap_dev = _float(acc.get("entry_vwap_dev_pct"))
        imb: Optional[float] = None

        push_dir = _push_dir_for_day(entry_day) or push_dir_default
        cache_key = (entry_day, sym)
        if push_dir and cache_key not in book_cache:
            book_cache[cache_key] = _load_entry_series(push_dir, sym)
        snap = _lookup_at(book_cache.get(cache_key, []), entry_ts)
        if snap:
            imb = snap[1]
            if tv is None:
                tv = snap[2]
            if vwap_dev is None:
                px = _float(acc.get("current_price")) or _float(acc.get("entry_price")) or snap[4]
                if px:
                    vwap_dev = _vwap_dev(float(px), snap[3])

        row = {
            **t,
            "session_id": session_rel,
            "day_stamp": entry_day,
            "week_label": _week_label(entry_day),
            "cohort_split": "in_sample" if session_rel in IN_SAMPLE else "oos",
            "trading_value": tv,
            "entry_vwap_dev_pct": vwap_dev,
            "order_book_imbalance": imb,
        }
        row["in_phase213b_D_cohort"] = _in_cohort(row)
        out.append(row)
    return out


def _group_metrics(rows: list[dict[str, Any]], key_fn) -> dict[str, Any]:
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        buckets[str(key_fn(r))].append(r)
    out: dict[str, Any] = {}
    for k in sorted(buckets):
        m = _metrics(buckets[k])
        out[k] = {
            **m,
            "trade_share_pct": None,
            "pnl_share_pct": None,
        }
    return out, buckets


def _apply_shares(grouped: dict[str, Any], rows: list[dict[str, Any]], key_fn) -> None:
    total_n = len(rows)
    total_pnl = sum(float(r["pnl_pct"]) for r in rows)
    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        buckets[str(key_fn(r))].append(r)
    for k, rs in buckets.items():
        if k not in grouped:
            continue
        grouped[k]["trade_share_pct"] = _share(len(rs), total_n)
        grouped[k]["pnl_share_pct"] = _share(sum(float(r["pnl_pct"]) for r in rs), total_pnl)


def _top_n_days(daily: dict[str, Any], n: int = 3) -> dict[str, Any]:
    ranked = sorted(daily.items(), key=lambda kv: kv[1]["trade_count"], reverse=True)
    top = ranked[:n]
    total_n = sum(v["trade_count"] for _, v in daily.items())
    total_pnl = sum(v["total_pnl_pct"] for _, v in daily.items())
    top_n = sum(v["trade_count"] for _, v in top)
    top_pnl = sum(v["total_pnl_pct"] for _, v in top)
    return {
        "days": [d for d, _ in top],
        "trade_count": top_n,
        "trade_share_pct": _share(top_n, total_n),
        "total_pnl_pct": round(top_pnl, 4),
        "pnl_share_pct": _share(top_pnl, total_pnl),
        "details": {d: daily[d] for d, _ in top},
    }


def _focus_symbol_daily(cohort: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for sym in FOCUS_SYMBOLS:
        sym_rows = [r for r in cohort if r.get("symbol") == sym]
        daily: dict[str, int] = defaultdict(int)
        daily_pnl: dict[str, float] = defaultdict(float)
        for r in sym_rows:
            d = str(r["day_stamp"])
            daily[d] += 1
            daily_pnl[d] = round(daily_pnl[d] + float(r["pnl_pct"]), 4)
        out[sym] = {
            "trade_count": len(sym_rows),
            "metrics": _metrics(sym_rows) if sym_rows else None,
            "daily_trade_count": dict(sorted(daily.items())),
            "daily_total_pnl_pct": dict(sorted(daily_pnl.items())),
        }
    return out


def _phase213b_daily_from_sessions(session_pf: dict[str, Any]) -> dict[str, Any]:
    """Approximate day-level counts/PnL by session folder date (Phase213b authoritative n=455)."""
    buckets: dict[str, dict[str, float]] = defaultdict(lambda: {"trade_count": 0, "total_pnl_pct": 0.0})
    for sid, sm in session_pf.items():
        n = int(sm.get("trade_count") or 0)
        if n <= 0:
            continue
        day = sid.split("/")[0]
        buckets[day]["trade_count"] += n
        buckets[day]["total_pnl_pct"] = round(
            buckets[day]["total_pnl_pct"] + float(sm.get("total_pnl_pct") or 0.0),
            4,
        )
    total_n = sum(int(v["trade_count"]) for v in buckets.values())
    out: dict[str, Any] = {}
    for day in sorted(buckets):
        row = buckets[day]
        out[day] = {
            "trade_count": int(row["trade_count"]),
            "total_pnl_pct": row["total_pnl_pct"],
            "trade_share_pct": _share(int(row["trade_count"]), total_n),
        }
    return out


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    p71 = _load_phase71()
    book_cache: dict[tuple[str, str], list[EntrySnap]] = {}
    all_enriched: list[dict[str, Any]] = []
    session_load: list[dict[str, Any]] = []

    for session_rel in ALL_SESSIONS:
        trades, source = _load_session_trades(session_rel, p71)
        if not trades:
            session_load.append({"session_id": session_rel, "skipped": True, "reason": "no_trades"})
            continue
        enriched = _enrich_trades(session_rel, trades, book_cache)
        seen_session: set[tuple[str, str]] = set()
        for r in enriched:
            if not r.get("in_phase213b_D_cohort"):
                continue
            key = (str(r.get("symbol") or ""), str(r.get("entry_time") or ""))
            if not key[1] or key in seen_session:
                continue
            seen_session.add(key)
            all_enriched.append(r)
        session_load.append(
            {
                "session_id": session_rel,
                "trades_source": source,
                "trade_count": len(enriched),
                "cohort_D_count": len(seen_session),
            }
        )

    cohort = all_enriched
    cohort_raw_count = sum(x.get("cohort_D_count", 0) for x in session_load if not x.get("skipped"))

    cohort_metrics = _metrics(cohort)

    daily_grouped, _ = _group_metrics(cohort, lambda r: r["day_stamp"])
    _apply_shares(daily_grouped, cohort, lambda r: r["day_stamp"])

    weekly_grouped, _ = _group_metrics(cohort, lambda r: r["week_label"])
    _apply_shares(weekly_grouped, cohort, lambda r: r["week_label"])

    session_grouped, _ = _group_metrics(cohort, lambda r: r["session_id"])
    _apply_shares(session_grouped, cohort, lambda r: r["session_id"])

    top3 = _top_n_days(daily_grouped, 3)

    day_60529 = [r for r in cohort if str(r["day_stamp"]) == "20260529"]
    session_60529 = [r for r in cohort if str(r["session_id"]).startswith("20260529/")]
    dep = {
        "day_20260529": {
            "trade_count": len(day_60529),
            "trade_share_pct": _share(len(day_60529), len(cohort)),
            "metrics": _metrics(day_60529),
            "pnl_share_pct": _share(
                sum(float(r["pnl_pct"]) for r in day_60529),
                cohort_metrics["total_pnl_pct"],
            ),
        },
        "sessions_starting_20260529": {
            "trade_count": len(session_60529),
            "trade_share_pct": _share(len(session_60529), len(cohort)),
            "metrics": _metrics(session_60529),
            "pnl_share_pct": _share(
                sum(float(r["pnl_pct"]) for r in session_60529),
                cohort_metrics["total_pnl_pct"],
            ),
        },
        "live_session_075135_only": _metrics(
            [r for r in cohort if r["session_id"] == "20260529/live_session_075135"]
        ),
        "excluding_20260529_day": _metrics([r for r in cohort if str(r["day_stamp"]) != "20260529"]),
        "excluding_20260529_sessions": _metrics(
            [r for r in cohort if not str(r["session_id"]).startswith("20260529/")]
        ),
    }

    phase213b_ref: dict[str, Any] = {}
    phase213b_path = REPO / "kabu_native/results/reports/phase213b_order_book_robustness_review.json"
    if phase213b_path.is_file():
        phase213b_ref = json.loads(phase213b_path.read_text(encoding="utf-8"))
    ref_d = (phase213b_ref.get("phase213_D_reference_at_20pct") or {}).get("metrics_combined") or {}
    ref_session = ((phase213b_ref.get("tier_sweep") or {}).get("top_20pct") or {}).get("session_pf") or {}
    phase213b_daily = _phase213b_daily_from_sessions(ref_session)
    phase213b_top3 = _top_n_days(
        {k: {**v, "profit_factor": None} for k, v in phase213b_daily.items()},
        3,
    )
    session_parity: dict[str, Any] = {}
    for sid, sm in sorted(ref_session.items()):
        exp = int(sm.get("trade_count") or 0)
        act = int(session_grouped.get(sid, {}).get("trade_count", 0) or 0)
        session_parity[sid] = {
            "phase213b_trade_count": exp,
            "rebuilt_trade_count": act,
            "match": exp == act,
            "phase213b_total_pnl_pct": sm.get("total_pnl_pct"),
            "rebuilt_total_pnl_pct": session_grouped.get(sid, {}).get("total_pnl_pct"),
        }

    parity_ok = len(cohort) == EXPECTED_COHORT_N and abs(
        cohort_metrics["total_pnl_pct"] - float(ref_d.get("total_pnl_pct") or 0)
    ) < 0.1

    report = {
        "phase": "213c",
        "mode": "board_imbalance_cohort_stability_review",
        "reference_cohort": "Phase213b D (low_liq + vwap + imbalance top 20%)",
        "constraints": {
            "review_only": True,
            "hard_reject_forbidden": True,
            "production_yaml_changes_forbidden": True,
            "fixed_scenario_only": True,
        },
        "fixed_parameters": {
            "tv_min": TV_MIN,
            "vwap_dev_exclude_ge_pct": VWAP_DEV_MAX,
            "imbalance_cutoff_top_20pct": IMBALANCE_CUTOFF_20PCT,
            "imbalance_definition": "calc_board_imbalance",
        },
        "cohort_summary": cohort_metrics,
        "cohort_summary_phase213b_reference": ref_d,
        "cohort_rebuild_meta": {
            "cohort_rows_after_session_local_dedupe": len(cohort),
            "cohort_rows_summed_from_sessions": cohort_raw_count,
            "expected_trade_count": EXPECTED_COHORT_N,
        },
        "phase213b_parity": {
            "expected_trade_count": EXPECTED_COHORT_N,
            "actual_trade_count": len(cohort),
            "expected_total_pnl_pct": ref_d.get("total_pnl_pct"),
            "actual_total_pnl_pct": cohort_metrics["total_pnl_pct"],
            "expected_profit_factor": ref_d.get("profit_factor"),
            "actual_profit_factor": cohort_metrics["profit_factor"],
            "parity_ok": parity_ok,
            "session_parity": session_parity,
        },
        "composition": {
            "by_day_trade_share_pct": {
                k: v["trade_share_pct"] for k, v in daily_grouped.items()
            },
            "by_week_trade_share_pct": {
                k: v["trade_share_pct"] for k, v in weekly_grouped.items()
            },
            "by_session_trade_share_pct": {
                k: v["trade_share_pct"] for k, v in session_grouped.items()
            },
        },
        "daily_breakdown": daily_grouped,
        "weekly_breakdown": weekly_grouped,
        "session_breakdown": session_grouped,
        "phase213b_session_breakdown_reference": ref_session,
        "phase213b_daily_by_session_folder_date": phase213b_daily,
        "phase213b_top_3_days_concentration": phase213b_top3,
        "top_3_days_concentration": top3,
        "dependency_20260529": dep,
        "focus_symbols": _focus_symbol_daily(cohort),
        "session_load_log": session_load,
        "verdict": {
            "cohort_rebuilt_ok": parity_ok,
            "use_phase213b_reference_for_exact_455": not parity_ok,
            "top3_days_trade_concentration_pct": top3["trade_share_pct"],
            "top3_days_pnl_concentration_pct": top3["pnl_share_pct"],
            "day_60529_trade_share_pct": dep["day_20260529"]["trade_share_pct"],
            "day_60529_pnl_share_pct": dep["day_20260529"]["pnl_share_pct"],
            "notes": [
                "Daily/weekly breakdown uses deduped (symbol, entry_time) cohort from post-hoc rebuild.",
                "Phase213b session_pf remains authoritative reference when parity_ok is false.",
            ],
        },
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} cohort_n={len(cohort)} parity_ok={parity_ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
