#!/usr/bin/env python3
"""
Phase217: Stop-hit root cause review (review only).

Quantify stop_hit drivers vs trailing_mfe / overlap / PnL groups across IS+OOS.
Fixed thresholds only — no per-day tuning, no hard reject, no YAML changes.
"""

from __future__ import annotations

import importlib.util
import json
import math
import statistics
import sys
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import datetime, time
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / "kabu_native/results/reports/phase217_stop_hit_root_cause_review.json"

FOCUS_SYMBOLS = ("6203.T", "6659.T", "9348.T", "4888.T")
PM_START = time(12, 33)

# Fixed shadow thresholds (Phase179/186/214 — not tuned per session)
TV_MIN = 1e8
TURNOVER_MIN = 0.002
VWAP_DEV_REJECT = 2.5
IMBALANCE_20PCT = 0.560790
IMBALANCE_30PCT = 0.533987
RISE_5MIN_LATE = 1.5
MFE_NEVER_GREEN = 0.3
MFE_HAD_RUN = 0.8

FEATURES: tuple[tuple[str, str], ...] = (
    ("entry_vwap_dev_pct", "entry_vwap_dev_pct"),
    ("entry_order_book_imbalance", "entry_order_book_imbalance"),
    ("trading_value", "trading_value"),
    ("quality", "continuation_quality_score"),
    ("momentum_continuation", "momentum_continuation_score"),
    ("rolling_mfe_pct", "rolling_mfe_pct"),
    ("rolling_mae_pct", "rolling_mae_pct"),
    ("current_price", "current_price"),
    ("tick_ratio_pct", "tick_ratio_pct"),
    ("rise_5min", "entry_rise_5min_pct"),
    ("rise_10min", "entry_rise_10min_pct"),
)

BOOL_FEATURES = (
    ("imbalance_shadow_candidate", "imbalance_shadow_candidate"),
    ("low_liquidity_shadow_rejected", "low_liquidity_shadow_rejected"),
)

CLASS_PRIORITY = (
    "entry_bad_liquidity",
    "entry_bad_vwap",
    "entry_bad_board",
    "entry_late_breakout",
    "exit_too_late",
    "unavoidable_noise",
    "data_insufficient",
)


def _load_phase213c_module() -> Any:
    path = REPO / "kabu_native/scripts/run_phase213c_board_imbalance_cohort_stability_review.py"
    name = "phase213c_loader_p217"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    sys.path[:0] = [str(REPO), str(REPO / "kabu_native" / "src")]
    spec.loader.exec_module(mod)
    return mod


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def _boolish(val: Any) -> bool:
    return str(val or "").lower() in ("true", "1", "yes")


def _vals(rows: list[dict[str, Any]], key: str) -> list[float]:
    out: list[float] = []
    for r in rows:
        v = r.get(key)
        if v is None or v == "":
            continue
        try:
            out.append(float(v))
        except (TypeError, ValueError):
            continue
    return out


def _mean(xs: list[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 6) if xs else None


def _median(xs: list[float]) -> Optional[float]:
    return round(statistics.median(xs), 6) if xs else None


def _stdev(xs: list[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    return round(statistics.stdev(xs), 6)


def _cohen_d(a: list[float], b: list[float]) -> Optional[float]:
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = statistics.mean(a), statistics.mean(b)
    sa, sb = statistics.stdev(a), statistics.stdev(b)
    pooled = math.sqrt(((len(a) - 1) * sa * sa + (len(b) - 1) * sb * sb) / (len(a) + len(b) - 2))
    if pooled <= 1e-12:
        return None
    return round((ma - mb) / pooled, 4)


def _compare_feature(label: str, key: str, a: list[dict], b: list[dict]) -> dict[str, Any]:
    av, bv = _vals(a, key), _vals(b, key)
    ma, mb = _mean(av), _mean(bv)
    return {
        "feature": label,
        "field": key,
        "A_count": len(av),
        "B_count": len(bv),
        "A_mean": ma,
        "B_mean": mb,
        "delta_A_minus_B": round(ma - mb, 6) if ma is not None and mb is not None else None,
        "cohen_d": _cohen_d(av, bv),
        "A_higher": ma is not None and mb is not None and ma > mb,
    }


def _entry_bucket(entry_time: str) -> str:
    try:
        dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
        t = dt.time()
    except ValueError:
        return "unknown"
    if t < time(9, 30):
        return "09:00-09:30"
    if t < time(10, 0):
        return "09:30-10:00"
    if t < time(10, 30):
        return "10:00-10:30"
    if t < time(11, 0):
        return "10:30-11:00"
    if t < time(11, 30):
        return "11:00-11:30"
    if t < PM_START:
        return "11:30-12:33"
    if t < time(13, 30):
        return "13:00-13:30"
    if t < time(14, 0):
        return "13:30-14:00"
    if t < time(14, 30):
        return "14:00-14:30"
    if t < time(15, 0):
        return "14:30-15:00"
    return "15:00+"


def _session_period(entry_time: str) -> str:
    try:
        dt = datetime.fromisoformat(entry_time.replace("Z", "+00:00"))
        return "PM" if dt.time() >= PM_START else "AM"
    except ValueError:
        return "unknown"


def _price_before(ring: list[tuple[float, float]], ts: float, lookback: float) -> Optional[float]:
    target = ts - lookback
    found: Optional[float] = None
    for t, px in ring:
        if t <= target:
            found = px
        elif t > ts:
            break
    return found


def _rise_pct(entry_px: float, prior: Optional[float]) -> Optional[float]:
    if prior is None or prior <= 0 or entry_px <= 0:
        return None
    return round((entry_px - prior) / prior * 100.0, 4)


def _get_price_ring(
    mod: Any,
    cache: dict[tuple[str, str], list[tuple[float, float]]],
    push_dir: Path,
    symbol: str,
) -> list[tuple[float, float]]:
    from small_paper.extended_entry_shadow import append_price_tick

    key = (str(push_dir), symbol.replace(".T", ""))
    if key in cache:
        return cache[key]
    sym = symbol.replace(".T", "")
    path = None
    for name in (f"{symbol}.jsonl", f"{sym}.jsonl"):
        p = push_dir / name
        if p.is_file():
            path = p
            break
    if path is None:
        cache[key] = []
        return []
    ring: list[tuple[float, float]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = mod._parse_ts(str(rec.get("recorded_at") or ""))
            px = mod._float((rec.get("payload") or {}).get("CurrentPrice"))
            if px and px > 0:
                append_price_tick(ring, ts=ts, px=float(px))
    cache[key] = ring
    return ring


def _replay_mfe_map(p71: Any, session_dir: Path) -> dict[tuple[str, str], float]:
    events_path = session_dir / "small_paper_events.jsonl"
    if not events_path.is_file():
        return {}
    events = p71._load_events(events_path)
    session_end = p71._session_end(events)
    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}
    out: dict[tuple[str, str], float] = {}

    def close_act(act: Any, *, close_time: str, close_price: float, reason: str) -> None:
        peak = max((float(t.get("pnl_pct") or 0.0) for t in act.rich_ticks), default=0.0)
        out[(str(act.trade.symbol), str(act.trade.entry_time))] = round(max(0.0, peak), 4)

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
                act.rich_ticks, act.trade.entry_price, momentum_mode="legacy", ratio=0.85, allow_session_end=False
            )
            if sig:
                close_act(act, close_time=ent_raw, close_price=price, reason=sig[1])
                active.pop(sym, None)
    for act in list(active.values()):
        px = act.rich_ticks[-1].get("price", act.trade.entry_price) if act.rich_ticks else act.trade.entry_price
        close_act(act, close_time=session_end, close_price=float(px), reason="session_end")
    return out


def _load_session_full_trades(mod: Any, session_rel: str, p71: Any) -> list[dict[str, Any]]:
    sdir = mod.BASE / session_rel
    if not sdir.is_dir():
        return []
    trades: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(row: dict[str, Any]) -> None:
        key = (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))
        if not key[0] or not key[1] or key in seen:
            return
        seen.add(key)
        trades.append(row)

    for csv_name in ("structural_trades.csv", "small_paper_trades_review.csv"):
        p = sdir / csv_name
        if not p.is_file():
            continue
        for row in mod.load_structural_trades(p):
            add(
                {
                    "symbol": str(row.get("symbol") or ""),
                    "entry_time": str(row.get("entry_time") or ""),
                    "entry_price": mod._float(row.get("entry_price")),
                    "exit_reason": str(row.get("close_reason") or row.get("exit_reason") or ""),
                    "pnl_pct": mod._float(row.get("realized_pnl_pct")) or mod._float(row.get("pnl_pct")) or 0.0,
                    "mfe_pct": mod._float(row.get("mfe_pct")),
                    "mae_pct": mod._float(row.get("mae_pct")),
                    "continuation_quality_score": mod._float(row.get("continuation_quality_score")),
                    "hold_duration_sec": mod._float(row.get("hold_duration_sec")),
                    "trade_source": csv_name,
                }
            )

    if not trades:
        for acc, ex in mod._pair_trades(mod._load_events(sdir)):
            add(
                {
                    "symbol": str(acc.get("symbol") or ""),
                    "entry_time": str(acc.get("entry_time") or ""),
                    "entry_price": mod._float(acc.get("current_price")),
                    "exit_reason": str(ex.get("exit_reason") or ""),
                    "pnl_pct": mod._float(ex.get("pnl_pct")) or 0.0,
                    "mfe_pct": mod._float(ex.get("mfe_pct")) or mod._float(ex.get("peak_mfe_pct")),
                    "mae_pct": mod._float(ex.get("mae_pct")),
                    "continuation_quality_score": mod._float(acc.get("continuation_quality_score")),
                    "hold_duration_sec": mod._float(ex.get("hold_duration_sec")),
                    "trade_source": "observer_exit_pair",
                }
            )

    if not trades:
        for row in mod.replay_trades_from_events(p71, sdir):
            add(
                {
                    "symbol": str(row.get("symbol") or ""),
                    "entry_time": str(row.get("entry_time") or ""),
                    "entry_price": None,
                    "exit_reason": str(row.get("close_reason") or ""),
                    "pnl_pct": mod._float(row.get("realized_pnl_pct")) or 0.0,
                    "mfe_pct": None,
                    "mae_pct": None,
                    "continuation_quality_score": None,
                    "hold_duration_sec": mod._float(row.get("hold_duration_sec")),
                    "trade_source": "replayed_v1",
                }
            )

    replay_mfe = _replay_mfe_map(p71, sdir)
    for t in trades:
        key = (str(t["symbol"]), str(t["entry_time"]))
        if t.get("mfe_pct") is None and key in replay_mfe:
            t["mfe_pct"] = replay_mfe[key]
    return trades


def _enrich_session(
    mod: Any,
    session_rel: str,
    trades: list[dict[str, Any]],
    book_cache: dict[tuple[str, Any], list[Any]],
    ring_cache: dict[tuple[str, str], list[tuple[float, float]]],
) -> list[dict[str, Any]]:
    from small_paper.board_imbalance_shadow import compute_board_imbalance_shadow_fields
    from small_paper.trading_value_shadow_gate import compute_trading_value_shadow_fields
    from small_paper.vwap_shadow_reject import compute_vwap_shadow_reject_fields

    sdir = mod.BASE / session_rel
    events = mod._load_events(sdir)
    accept_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for ev in events:
        if ev.get("event_type") == "accepted":
            accept_by_key[(str(ev.get("symbol") or ""), str(ev.get("entry_time") or ""))] = ev

    imb_samples: list[float] = []
    out: list[dict[str, Any]] = []
    for t in trades:
        sym = str(t.get("symbol") or "")
        entry_time = str(t.get("entry_time") or "")
        key = (sym, entry_time)
        acc = accept_by_key.get(key, {})
        entry_ts = mod._parse_ts(entry_time)
        entry_day = mod._day_stamp(session_rel, entry_time)
        push_dir = mod._push_dir_for_day(entry_day) or mod._push_dir(session_rel)

        entry_px = mod._float(t.get("entry_price")) or mod._float(acc.get("current_price"))
        tv = mod._float(acc.get("trading_value"))
        to = mod._float(acc.get("turnover_proxy"))
        vwap_dev = mod._float(acc.get("entry_vwap_dev_pct"))
        imb = mod._float(acc.get("entry_order_book_imbalance"))

        cache_key = (entry_day, sym)
        if push_dir and cache_key not in book_cache:
            book_cache[cache_key] = mod._load_entry_series(push_dir, sym)
        snap = mod._lookup_at(book_cache.get(cache_key, []), entry_ts)
        payload: dict[str, Any] = {"CurrentPrice": entry_px, "VWAP": None}
        if snap:
            if imb is None:
                imb = snap[1]
            if tv is None:
                tv = snap[2]
            payload["VWAP"] = snap[3]
            if entry_px is None:
                entry_px = snap[4]
            if vwap_dev is None and entry_px and snap[3]:
                vwap_dev = mod._vwap_dev(float(entry_px), snap[3])

        trade_probe = {
            "trading_value": tv,
            "entry_vwap_dev_pct": vwap_dev,
            "turnover_proxy": to,
        }
        if imb is None and push_dir:
            imb_fields = compute_board_imbalance_shadow_fields(
                trade=trade_probe, payload=payload, session_imbalance_samples=imb_samples
            )
        else:
            if imb is not None:
                imb_samples.append(float(imb))
            imb_fields = {
                "entry_order_book_imbalance": imb,
                "imbalance_shadow_candidate": _boolish(acc.get("imbalance_shadow_candidate")),
                "imbalance_shadow_tier": acc.get("imbalance_shadow_tier") or "",
            }
            if not imb_fields["imbalance_shadow_tier"] and imb is not None:
                recomputed = compute_board_imbalance_shadow_fields(
                    trade={**trade_probe, "entry_order_book_imbalance": imb},
                    payload=payload,
                    session_imbalance_samples=[],
                )
                imb_fields["imbalance_shadow_candidate"] = recomputed["imbalance_shadow_candidate"]
                imb_fields["imbalance_shadow_tier"] = recomputed["imbalance_shadow_tier"]

        vwap_fields = {
            "vwap_shadow_reject_candidate": _boolish(acc.get("vwap_shadow_reject_candidate")),
            "entry_vwap_dev_pct": vwap_dev,
        }
        if not acc.get("vwap_shadow_reject_candidate") and entry_px:
            vwap_fields = compute_vwap_shadow_reject_fields(
                payload=payload, entry_px=float(entry_px), entry_vwap_dev_pct=vwap_dev
            )
            vwap_dev = vwap_fields.get("entry_vwap_dev_pct", vwap_dev)

        tv_fields = compute_trading_value_shadow_fields({"trading_value": tv})
        if acc.get("trading_value_band"):
            tv_fields["trading_value_band"] = acc.get("trading_value_band")

        rise_5 = mod._float(acc.get("entry_rise_5min_pct"))
        rise_10 = mod._float(acc.get("entry_rise_10min_pct"))
        if (rise_5 is None or rise_10 is None) and push_dir and entry_px:
            ring = _get_price_ring(mod, ring_cache, push_dir, sym)
            idx = bisect_right([x[0] for x in ring], entry_ts)
            sliced = ring[:idx]
            if rise_5 is None:
                rise_5 = _rise_pct(float(entry_px), _price_before(sliced, entry_ts, 300))
            if rise_10 is None:
                rise_10 = _rise_pct(float(entry_px), _price_before(sliced, entry_ts, 600))

        reason = str(t.get("exit_reason") or "")
        pnl = float(t.get("pnl_pct") or 0.0)
        mfe = mod._float(t.get("mfe_pct"))

        row = {
            **t,
            "session_id": session_rel,
            "day_stamp": entry_day,
            "split": "in_sample" if session_rel in mod.IN_SAMPLE else "oos",
            "entry_vwap_dev_pct": vwap_dev,
            "entry_order_book_imbalance": imb_fields.get("entry_order_book_imbalance", imb),
            "imbalance_shadow_candidate": bool(imb_fields.get("imbalance_shadow_candidate")),
            "imbalance_shadow_tier": imb_fields.get("imbalance_shadow_tier") or "",
            "vwap_shadow_reject_candidate": bool(vwap_fields.get("vwap_shadow_reject_candidate")),
            "trading_value": tv,
            "trading_value_band": tv_fields.get("trading_value_band"),
            "turnover_proxy": to,
            "low_liquidity_shadow_rejected": _boolish(acc.get("low_liquidity_shadow_rejected")),
            "low_liquidity_shadow_reason": acc.get("low_liquidity_shadow_reason") or "",
            "continuation_quality_score": mod._float(t.get("continuation_quality_score"))
            or mod._float(acc.get("continuation_quality_score")),
            "momentum_continuation_score": mod._float(acc.get("momentum_continuation_score")),
            "rolling_mfe_pct": mod._float(acc.get("rolling_mfe_pct")),
            "rolling_mae_pct": mod._float(acc.get("rolling_mae_pct")),
            "current_price": entry_px,
            "tick_ratio_pct": mod._float(acc.get("tick_ratio_pct")),
            "volume_accel_30s_vs_prev30s": mod._float(acc.get("volume_accel_30s_vs_prev30s")),
            "entry_rise_5min_pct": rise_5,
            "entry_rise_10min_pct": rise_10,
            "entry_time_bucket": _entry_bucket(entry_time),
            "session_period": _session_period(entry_time),
            "stop_hit": reason == "stop_hit",
            "trailing_mfe_exit": reason == "trailing_mfe_exit",
            "overlap_replaced": reason == "overlap_replaced_review",
            "profitable": pnl > 0,
            "losing": pnl < 0,
            "mfe_path": (
                "had_mfe_ge_0p8_then_stop"
                if reason == "stop_hit" and mfe is not None and mfe >= MFE_HAD_RUN
                else (
                    "never_green_lt_0p3"
                    if reason == "stop_hit" and mfe is not None and mfe < MFE_NEVER_GREEN
                    else (
                        "mid_mfe_0p3_0p8"
                        if reason == "stop_hit" and mfe is not None
                        else "n_a"
                    )
                )
            ),
        }
        out.append(row)
    return out


def _low_liq_reject(row: dict[str, Any]) -> bool:
    if row.get("low_liquidity_shadow_rejected"):
        return True
    tv = _float(row.get("trading_value"))
    to = _float(row.get("turnover_proxy"))
    if tv is not None and tv < TV_MIN:
        return True
    if to is not None and to < TURNOVER_MIN:
        return True
    return False


def _vwap_reject(row: dict[str, Any]) -> bool:
    if row.get("vwap_shadow_reject_candidate"):
        return True
    dev = _float(row.get("entry_vwap_dev_pct"))
    return dev is not None and dev >= VWAP_DEV_REJECT


def _passes_d_guards(row: dict[str, Any]) -> bool:
    if _low_liq_reject(row) or _vwap_reject(row):
        return False
    imb = _float(row.get("entry_order_book_imbalance"))
    return imb is not None and imb >= IMBALANCE_20PCT


def _composite_guard_reject(row: dict[str, Any]) -> tuple[bool, list[str]]:
    flags: list[str] = []
    if _low_liq_reject(row):
        flags.append("low_liq")
    if _vwap_reject(row):
        flags.append("vwap")
    imb = _float(row.get("entry_order_book_imbalance"))
    if imb is not None and imb < IMBALANCE_30PCT:
        flags.append("low_imbalance")
    return bool(flags), flags


def _classify_stop(row: dict[str, Any]) -> dict[str, Any]:
    tags: list[str] = []
    mfe = _float(row.get("mfe_pct"))
    dev = _float(row.get("entry_vwap_dev_pct"))
    imb = _float(row.get("entry_order_book_imbalance"))
    rise5 = _float(row.get("entry_rise_5min_pct"))

    has_tv = _float(row.get("trading_value")) is not None or row.get("low_liquidity_shadow_rejected") is not None
    has_vwap = dev is not None or row.get("vwap_shadow_reject_candidate") is not None
    has_imb = imb is not None
    if not (has_tv or has_vwap or has_imb):
        primary = "data_insufficient"
        return {
            "tags": [primary],
            "primary": primary,
            "fix_layer": "unknown",
        }

    if _low_liq_reject(row):
        tags.append("entry_bad_liquidity")
    if _vwap_reject(row):
        tags.append("entry_bad_vwap")
    if imb is not None and imb < IMBALANCE_30PCT:
        tags.append("entry_bad_board")
    if rise5 is not None and rise5 >= RISE_5MIN_LATE and dev is not None and dev >= 1.5:
        tags.append("entry_late_breakout")

    if mfe is not None:
        if mfe >= MFE_HAD_RUN:
            tags.append("exit_too_late")
        elif mfe < MFE_NEVER_GREEN:
            tags.append("unavoidable_noise")

    if not tags:
        tags.append("data_insufficient")

    primary = next((c for c in CLASS_PRIORITY if c in tags), tags[0])
    if "exit_too_late" in tags and primary not in (
        "entry_bad_liquidity",
        "entry_bad_vwap",
        "entry_bad_board",
        "entry_late_breakout",
    ):
        fix_layer = "exit"
    elif any(t.startswith("entry_") for t in tags):
        fix_layer = "entry"
    elif "unavoidable_noise" in tags:
        fix_layer = "entry"
    else:
        fix_layer = "unknown"
    return {"tags": tags, "primary": primary, "fix_layer": fix_layer}


def _stop_rate_by_feature(rows: list[dict[str, Any]], key: str) -> Optional[dict[str, Any]]:
    vals = [(r, _float(r.get(key))) for r in rows if _float(r.get(key)) is not None]
    if len(vals) < 20:
        return None
    xs = [v for _, v in vals]
    med = statistics.median(xs)
    hi = [r for r, v in vals if v >= med]
    lo = [r for r, v in vals if v < med]
    hi_stops = sum(1 for r in hi if r.get("stop_hit"))
    lo_stops = sum(1 for r in lo if r.get("stop_hit"))
    return {
        "field": key,
        "median_split": med,
        "high_bucket_n": len(hi),
        "low_bucket_n": len(lo),
        "high_bucket_stop_rate": round(hi_stops / len(hi), 4) if hi else None,
        "low_bucket_stop_rate": round(lo_stops / len(lo), 4) if lo else None,
        "stop_rate_delta_high_minus_low": round(hi_stops / len(hi) - lo_stops / len(lo), 4)
        if hi and lo
        else None,
    }


def _group_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"trade_count": 0}
    n = len(rows)
    stops = sum(1 for r in rows if r.get("stop_hit"))
    trails = sum(1 for r in rows if r.get("trailing_mfe_exit"))
    pnls = [float(r.get("pnl_pct") or 0) for r in rows]
    return {
        "trade_count": n,
        "stop_hit_count": stops,
        "stop_hit_rate": round(stops / n, 4),
        "trailing_mfe_count": trails,
        "trailing_mfe_rate": round(trails / n, 4),
        "avg_pnl_pct": round(sum(pnls) / n, 4),
        "avg_mfe_pct": _mean(_vals(rows, "mfe_pct")),
    }


def _build_all(mod: Any) -> list[dict[str, Any]]:
    p71 = mod._load_phase71()
    book_cache: dict[tuple[str, Any], list[Any]] = {}
    ring_cache: dict[tuple[str, str], list[tuple[float, float]]] = {}
    all_rows: list[dict[str, Any]] = []
    for i, session_rel in enumerate(mod.ALL_SESSIONS, 1):
        trades = _load_session_full_trades(mod, session_rel, p71)
        if not trades:
            print(f"  [{i}/{len(mod.ALL_SESSIONS)}] skip {session_rel}", flush=True)
            continue
        enriched = _enrich_session(mod, session_rel, trades, book_cache, ring_cache)
        all_rows.extend(enriched)
        print(f"  [{i}/{len(mod.ALL_SESSIONS)}] {session_rel} n={len(enriched)}", flush=True)
    return all_rows


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    mod = _load_phase213c_module()
    print("loading trades...", flush=True)
    rows = _build_all(mod)

    grp_a = [r for r in rows if r.get("stop_hit")]
    grp_b = [r for r in rows if r.get("trailing_mfe_exit")]
    grp_c = [r for r in rows if r.get("overlap_replaced")]
    grp_d = [r for r in rows if r.get("profitable")]
    grp_e = [r for r in rows if r.get("losing")]

    # 1. stop_hit vs trailing_mfe feature diff
    ab_compare = [_compare_feature(lbl, key, grp_a, grp_b) for lbl, key in FEATURES]
    ab_bool = []
    for lbl, key in BOOL_FEATURES:
        a_rate = sum(1 for r in grp_a if _boolish(r.get(key))) / max(1, len(grp_a))
        b_rate = sum(1 for r in grp_b if _boolish(r.get(key))) / max(1, len(grp_b))
        ab_bool.append(
            {
                "feature": lbl,
                "A_stop_hit_rate": round(a_rate, 4),
                "B_trailing_mfe_rate": round(b_rate, 4),
                "delta": round(a_rate - b_rate, 4),
            }
        )
    ranked_d = sorted(
        [c for c in ab_compare if c.get("cohen_d") is not None],
        key=lambda x: abs(float(x["cohen_d"])),
        reverse=True,
    )

    # 2. top features explaining stop_hit (median split stop rate)
    non_overlap = [r for r in rows if not r.get("overlap_replaced")]
    stop_rank = sorted(
        [x for x in (_stop_rate_by_feature(non_overlap, key) for _, key in FEATURES) if x],
        key=lambda x: abs(float(x.get("stop_rate_delta_high_minus_low") or 0)),
        reverse=True,
    )

    # 3. guard counterfactual (fixed composite)
    stop_rows = grp_a
    guard_rejected = [r for r in rows if _composite_guard_reject(r)[0]]
    stop_guard = [r for r in stop_rows if _composite_guard_reject(r)[0]]
    guard_cf = {
        "scenario": "fixed_composite_reject: low_liq OR vwap_ge_2p5 OR imbalance_lt_30pct_tier",
        "total_trades": len(rows),
        "total_stop_hit": len(stop_rows),
        "trades_would_reject": len(guard_rejected),
        "stop_hit_would_remove": len(stop_guard),
        "stop_hit_remove_pct": round(100.0 * len(stop_guard) / max(1, len(stop_rows)), 2),
        "remaining_stop_hit": len(stop_rows) - len(stop_guard),
        "d_cohort_pass_only": {
            "description": "pass low_liq+vwap AND imbalance>=20pct (Phase213b D guards)",
            "trade_count": sum(1 for r in rows if _passes_d_guards(r)),
            "stop_hit_count": sum(1 for r in stop_rows if _passes_d_guards(r)),
            "stop_hit_outside_d": sum(1 for r in stop_rows if not _passes_d_guards(r)),
        },
    }

    # 4. focus symbols
    focus: dict[str, Any] = {}
    for sym in FOCUS_SYMBOLS:
        sym_rows = [r for r in rows if r.get("symbol") == sym]
        sym_stops = [r for r in sym_rows if r.get("stop_hit")]
        classified = [_classify_stop(r) for r in sym_stops]
        focus[sym] = {
            "trade_count": len(sym_rows),
            "stop_hit_count": len(sym_stops),
            "stop_hit_rate": round(len(sym_stops) / max(1, len(sym_rows)), 4),
            "primary_cause_counts": dict(Counter(c["primary"] for c in classified)),
            "fix_layer_counts": dict(Counter(c["fix_layer"] for c in classified)),
            "avg_mfe_on_stops": _mean(_vals(sym_stops, "mfe_pct")),
        }

    # 5. AM/PM stop causes
    am_pm: dict[str, Any] = {}
    for period in ("AM", "PM"):
        pr = [r for r in stop_rows if r.get("session_period") == period]
        cls = [_classify_stop(r) for r in pr]
        am_pm[period] = {
            "stop_hit_count": len(pr),
            "primary_cause_counts": dict(Counter(c["primary"] for c in cls)),
            "fix_layer_counts": dict(Counter(c["fix_layer"] for c in cls)),
            "metrics": _group_metrics(pr),
        }

    # 6. MFE path on stops
    mfe_paths = Counter(r.get("mfe_path") for r in stop_rows)
    mfe_path_detail = {
        "never_green_lt_0p3": mfe_paths.get("never_green_lt_0p3", 0),
        "mid_mfe_0p3_0p8": mfe_paths.get("mid_mfe_0p3_0p8", 0),
        "had_mfe_ge_0p8_then_stop": mfe_paths.get("had_mfe_ge_0p8_then_stop", 0),
        "missing_mfe": sum(1 for r in stop_rows if _float(r.get("mfe_pct")) is None),
    }

    # 7. ENTRY vs EXIT classification for all stops
    stop_classified = [_classify_stop(r) for r in stop_rows]
    fix_layers = Counter(c["fix_layer"] for c in stop_classified)
    primary_causes = Counter(c["primary"] for c in stop_classified)

    # entry_time_bucket stop rates
    bucket_stats: dict[str, Any] = {}
    for bucket in sorted({r.get("entry_time_bucket") for r in rows}):
        br = [r for r in rows if r.get("entry_time_bucket") == bucket]
        bucket_stats[str(bucket)] = _group_metrics(br)

    report = {
        "phase": 217,
        "mode": "stop_hit_root_cause_review",
        "constraints": {
            "review_only": True,
            "hard_reject_forbidden": True,
            "entry_change_forbidden": True,
            "production_yaml_changes_forbidden": True,
            "single_day_optimization_forbidden": True,
            "fixed_scenario_only": True,
        },
        "context_from_phase216": (
            "MFE>=3% rare (2 trades); system optimized for small-expectation not big runners."
        ),
        "population": {
            "session_scope": "IS 11 + OOS 9",
            "total_trades": len(rows),
            "comparison_groups": {
                "A_stop_hit": len(grp_a),
                "B_trailing_mfe_exit": len(grp_b),
                "C_overlap_replaced_review": len(grp_c),
                "D_profitable": len(grp_d),
                "E_losing": len(grp_e),
            },
        },
        "1_stop_hit_vs_trailing_mfe_features": {
            "numeric_comparisons": ab_compare,
            "boolean_comparisons": ab_bool,
            "top_by_abs_cohen_d": ranked_d[:10],
        },
        "2_top_stop_hit_explaining_features": {
            "method": "median_split stop_hit_rate delta on non-overlap trades",
            "ranked": stop_rank[:10],
        },
        "3_composite_guard_counterfactual": guard_cf,
        "4_focus_symbol_stop_causes": focus,
        "5_am_pm_stop_cause_shift": am_pm,
        "6_stop_mfe_path": mfe_path_detail,
        "7_stop_fix_layer_classification": {
            "entry_fixable_count": fix_layers.get("entry", 0),
            "exit_fixable_count": fix_layers.get("exit", 0),
            "unknown_count": fix_layers.get("unknown", 0),
            "primary_cause_counts": dict(primary_causes),
            "fix_layer_counts": dict(fix_layers),
            "entry_vs_exit_share_pct": {
                "entry": round(100.0 * fix_layers.get("entry", 0) / max(1, len(stop_rows)), 2),
                "exit": round(100.0 * fix_layers.get("exit", 0) / max(1, len(stop_rows)), 2),
            },
        },
        "entry_time_bucket_metrics": bucket_stats,
        "feature_coverage": {
            k: round(sum(1 for r in rows if r.get(k) is not None and r.get(k) != "") / max(1, len(rows)), 4)
            for k in (
                "entry_vwap_dev_pct",
                "entry_order_book_imbalance",
                "trading_value",
                "tick_ratio_pct",
                "volume_accel_30s_vs_prev30s",
                "entry_rise_5min_pct",
                "low_liquidity_shadow_rejected",
                "imbalance_shadow_candidate",
            )
        },
        "notes": [
            "Composite guard uses fixed Phase179/186/214 thresholds — not tuned per session.",
            "volume_accel_30s_vs_prev30s not logged in most sessions; excluded from rankings when sparse.",
            "exit_too_late = stop_hit with path MFE>=0.8%; unavoidable_noise = MFE<0.3%.",
            "Groups A–E overlap (stop_hit subset of losing exits).",
        ],
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {OUT} trades={len(rows)} stops={len(grp_a)}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
