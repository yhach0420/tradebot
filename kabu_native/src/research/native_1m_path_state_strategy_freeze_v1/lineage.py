"""Manual causality lineage for 10 D2 + 10 D3 + 10 D4 frozen trades."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1.clock import hhmm_add
from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.native_1m_path_state_strategy_freeze_v1.entry import lineage_ok
from research.native_1m_path_state_strategy_freeze_v1.r11 import DIST_VWAP_THRESHOLD, MINS_FROM_OPEN_THRESHOLD, VWAP_RECLAIM_THRESHOLD, bar_state, match_r11
from research.native_path_state_discrimination_v1.features import mins_from_open
from research.one_minute_native_playbook_discovery_v1.states import prep_symbol


def _sample(trades: list[dict[str, Any]], block: str, n: int = 10) -> list[dict[str, Any]]:
    xs = [t for t in trades if str(t.get("block")) == block]
    xs.sort(key=lambda t: (str(t["date"]), str(t["symbol"]), str(t["event_time"])))
    if len(xs) <= n:
        return xs
    if n <= 1:
        return xs[:1]
    idxs = [int(round(i * (len(xs) - 1) / (n - 1))) for i in range(n)]
    seen = set()
    out = []
    for i in idxs:
        if i not in seen:
            seen.add(i)
            out.append(xs[i])
    return out[:n]


def _bars(rec: dict[str, Any], lo: int, hi: int) -> list[dict[str, Any]]:
    rows = []
    for k in range(max(0, lo), min(rec["n"], hi)):
        rows.append(
            {
                "t": rec["t"][k],
                "open": float(rec["o"][k]) if rec["o"][k] == rec["o"][k] else None,
                "high": float(rec["h"][k]) if rec["h"][k] == rec["h"][k] else None,
                "low": float(rec["l"][k]) if rec["l"][k] == rec["l"][k] else None,
                "close": float(rec["c"][k]) if rec["c"][k] == rec["c"][k] else None,
                "volume": float(rec["v"][k]) if rec["v"][k] == rec["v"][k] else None,
                "vwap": float(rec["vw"][k]) if rec["vw"][k] == rec["vw"][k] else None,
                "above_vwap": bool(rec["c"][k] == rec["c"][k] and rec["vw"][k] == rec["vw"][k] and rec["c"][k] > rec["vw"][k]),
            }
        )
    return rows


def build_lineage(
    trades: list[dict[str, Any]],
    bind: dict[str, Any],
    *,
    med: dict[str, float],
) -> dict[str, Any]:
    samples = _sample(trades, "D2") + _sample(trades, "D3") + _sample(trades, "D4")
    dates = {str(t["date"]) for t in samples}
    symbols = {str(t["symbol"]) for t in samples}
    split = dict(bind.get("split") or {})
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    if dates & (conf | val):
        raise RuntimeError("lineage_forbidden_partition")
    minutes = load_minutes(symbols=sorted(symbols), allowed_dates=dates, forbidden_dates=conf | val)
    minutes["date"] = minutes["date"].astype(str)
    minutes["time_label"] = minutes["time_label"].astype(str).str.slice(0, 5)
    minutes["symbol"] = minutes["symbol"].astype(str)
    recs: dict[tuple[str, str], dict[str, Any]] = {}
    for (day, sym), sg in minutes.groupby(["date", "symbol"], sort=False):
        rec = prep_symbol(sg)
        rec["symbol"] = str(sym)
        recs[(str(day), str(sym))] = rec
    out = []
    for t in samples:
        rec = recs.get((str(t["date"]), str(t["symbol"])))
        feat_t = str(t.get("feature_bar") or "")
        i = None if rec is None else rec["idx"].get(feat_t)
        st = bar_state(rec, i) if rec is not None and i is not None else {}
        prior = None
        if rec is not None and i is not None and i > 0:
            prior = {
                "t": rec["t"][i - 1],
                "close": float(rec["c"][i - 1]) if rec["c"][i - 1] == rec["c"][i - 1] else None,
                "vwap": float(rec["vw"][i - 1]) if rec["vw"][i - 1] == rec["vw"][i - 1] else None,
                "close_le_vwap": bool(rec["c"][i - 1] == rec["c"][i - 1] and rec["vw"][i - 1] == rec["vw"][i - 1] and rec["c"][i - 1] <= rec["vw"][i - 1]),
            }
        r11 = {
            "dist_vwap": st.get("dist_vwap"),
            "dist_vwap_gt": (st.get("dist_vwap") is not None and st.get("dist_vwap") == st.get("dist_vwap") and float(st["dist_vwap"]) > DIST_VWAP_THRESHOLD),
            "mins_from_open": st.get("mins_from_open") if st else mins_from_open(feat_t),
            "mins_le": None,
            "vwap_reclaim": st.get("vwap_reclaim"),
            "reclaim_gt": (st.get("vwap_reclaim") or 0) > VWAP_RECLAIM_THRESHOLD,
            "match": match_r11({"state": st}, med=med) if st else False,
        }
        r11["mins_le"] = bool(r11["mins_from_open"] == r11["mins_from_open"] and float(r11["mins_from_open"]) <= MINS_FROM_OPEN_THRESHOLD)
        fwd = list(t.get("fwd_bars") or [])
        hold = int(t.get("hold_min") or 0)
        subsequent = []
        for row in fwd[: hold + 2]:
            subsequent.append(
                {
                    "t": row[0],
                    "open": row[1],
                    "high": row[2],
                    "low": row[3],
                    "close": row[4],
                    "vwap": row[5],
                    "above_vwap": row[6],
                }
            )
        lin = lineage_ok(t)
        out.append(
            {
                "block": t.get("block"),
                "date": t.get("date"),
                "symbol": t.get("symbol"),
                "raw_minute_bars_before_and_reclaim": _bars(rec, i - 8, i + 1) if rec is not None and i is not None else [],
                "vwap_at_reclaim_bar": None if rec is None or i is None else float(rec["vw"][i]) if rec["vw"][i] == rec["vw"][i] else None,
                "close_at_reclaim_bar": None if rec is None or i is None else float(rec["c"][i]) if rec["c"][i] == rec["c"][i] else None,
                "prior_vwap_state": prior,
                "reclaim_bar": feat_t,
                "feature_available_at": t.get("available_at") or hhmm_add(feat_t, 1),
                "R11_evaluation": r11,
                "ENTRY_fill_time": t.get("entry_time"),
                "ENTRY_fill_price": t.get("entry_price"),
                "subsequent_bars_to_exit": subsequent,
                "EXIT_trigger": t.get("exit_reason"),
                "EXIT_pending": t.get("exit_pending_time"),
                "EXIT_fill_time": t.get("exit_hh"),
                "EXIT_fill_price": t.get("exit_price"),
                "timestamp_lineage": lin,
            }
        )
    n_pass = sum(1 for r in out if (r.get("R11_evaluation") or {}).get("match") and (r.get("timestamp_lineage") or {}).get("available_at_le_decision"))
    return {
        "n": len(out),
        "d2_n": sum(1 for r in out if r.get("block") == "D2"),
        "d3_n": sum(1 for r in out if r.get("block") == "D3"),
        "d4_n": sum(1 for r in out if r.get("block") == "D4"),
        "rows": out,
        "all_match_r11": all(bool((r.get("R11_evaluation") or {}).get("match")) for r in out) if out else False,
        "causal_lineage_pass": bool(out) and n_pass == len(out),
    }
