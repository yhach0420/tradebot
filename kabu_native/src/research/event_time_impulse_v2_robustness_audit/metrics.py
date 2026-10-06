"""Summaries for the robustness audit. No strategy change."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import numpy as np

from research.event_time_impulse_complete_strategy_v2.identity import bind
from research.event_time_impulse_v2_robustness_audit.latency import LATENCIES_MS
from research.symbol_setup_baseline_complete_strategy_precommit.contract import EXTENSION17, ORIGINAL18

JST = ZoneInfo("Asia/Tokyo")
HOLD_EDGES = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
HOLD_LABELS = ("<100ms", "100-250ms", "250-500ms", "500ms-1s", "1-2s", "2-5s", "5-10s", "10-30s", "30-60s", ">60s")
ORDINAL = ((1, 1, "1st"), (2, 2, "2nd"), (3, 3, "3rd"), (4, 5, "4-5"), (6, 10, "6-10"), (11, 20, "11-20"), (21, 10**9, "21+"))
GAPS = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0)
FRICTION = (0.0, 0.25, 0.5, 1.0, 2.0)
BASE_N = 11902
BASE_PNL = 1189150.0
BASE_PF = 1.409303686366296


def _pf(yen: np.ndarray) -> Optional[float]:
    profit = float(yen[yen > 0].sum()) if yen.size else 0.0
    loss = float(yen[yen < 0].sum()) if yen.size else 0.0
    if loss == 0:
        return None if profit == 0 else float("inf")
    return profit / abs(loss)


def _pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    priced = [row for row in rows if row.get("pnl_yen") is not None]
    yen = np.asarray([float(row["pnl_yen"]) for row in priced], dtype=float)
    bps = np.asarray([float(row["bps"]) for row in priced], dtype=float)
    if yen.size == 0:
        return {"trade_n": 0, "pnl": 0.0, "pf": None, "mean_yen": None, "median_yen": None, "mean_bps": None, "median_bps": None, "win_rate": None, "mean_hold": None, "median_hold": None, "mfe_mean": None, "mae_mean": None}
    hold = np.asarray([float(row["hold_sec"]) for row in priced if row.get("hold_sec") is not None], dtype=float)
    mfe = np.asarray([float(row["mfe"]) for row in priced if row.get("mfe") is not None], dtype=float)
    mae = np.asarray([float(row["mae"]) for row in priced if row.get("mae") is not None], dtype=float)
    return {
        "trade_n": int(yen.size),
        "pnl": float(yen.sum()),
        "pf": _pf(yen),
        "mean_yen": float(yen.mean()),
        "median_yen": float(np.median(yen)),
        "mean_bps": float(bps.mean()),
        "median_bps": float(np.median(bps)),
        "win_rate": float(np.mean(yen > 0)),
        "mean_hold": None if hold.size == 0 else float(hold.mean()),
        "median_hold": None if hold.size == 0 else float(np.median(hold)),
        "mfe_mean": None if mfe.size == 0 else float(mfe.mean()),
        "mae_mean": None if mae.size == 0 else float(mae.mean()),
    }


def _fold_of(dates: list[str]) -> dict[str, int]:
    cuts = [int(round(i * len(dates) / 3)) for i in range(4)]
    out = {}
    for fold, (lo, hi) in enumerate(zip(cuts, cuts[1:])):
        for day in dates[lo:hi]:
            out[day] = fold
    return out


def _slice_pack(rows: list[dict[str, Any]], dates: list[str]) -> dict[str, Any]:
    fold = _fold_of(dates)
    original = set(ORIGINAL18)
    out = {
        "all": _pack(rows),
        "ORIGINAL18": _pack([row for row in rows if row["date"] in original]),
        "EXTENSION17": _pack([row for row in rows if row["date"] in set(EXTENSION17)]),
        "folds": [],
        "reasons": {},
    }
    for fold_id in range(3):
        one = _pack([row for row in rows if fold.get(row["date"]) == fold_id])
        one["fold"] = fold_id
        out["folds"].append(one)
    for reason in ("BREAK_SUPPORT_FAILURE", "IMPULSE_EXHAUSTED", "SESSION_FLAT", "FAIL_CLOSE_INVALID_DATA"):
        out["reasons"][reason] = _pack([row for row in rows if row.get("reason") == reason])
    return out


def _hold_buckets(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for label, lo, hi in zip(HOLD_LABELS, (0.0, *HOLD_EDGES), (*HOLD_EDGES, float("inf"))):
        group = [row for row in rows if row.get("hold_sec") is not None and lo <= float(row["hold_sec"]) < hi]
        out.append({"bucket": label, **_pack(group)})
    return out


def _ordinal(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in sorted(rows, key=lambda item: (item["date"], item["symbol"], float(item["entry_t"]))):
        by_key.setdefault((row["date"], row["symbol"]), []).append(row)
    grouped = {label: [] for _a, _b, label in ORDINAL}
    for group in by_key.values():
        for nth, row in enumerate(group, start=1):
            for lo, hi, label in ORDINAL:
                if lo <= nth <= hi:
                    grouped[label].append(row)
                    break
    return [{"ordinal": label, **_pack(grouped[label])} for _lo, _hi, label in ORDINAL]


def _gaps(rows: list[dict[str, Any]]) -> dict[str, Any]:
    gaps = []
    per_day = []
    for key_rows in _groups(rows).values():
        ordered = sorted(key_rows, key=lambda item: float(item["entry_t"]))
        per_day.append(len(ordered))
        for prev, nxt in zip(ordered, ordered[1:]):
            gaps.append(float(nxt["entry_t"]) - float(prev["exit_t"] if prev.get("exit_t") is not None else prev["entry_t"]))
    arr = np.asarray(gaps, dtype=float) if gaps else np.asarray([], dtype=float)
    counts = {f"within_{gap}": int(np.sum(arr <= gap)) if arr.size else 0 for gap in GAPS}
    day_n = np.asarray(per_day, dtype=float) if per_day else np.asarray([0.0])
    return {
        "gap_n": int(arr.size),
        "counts": counts,
        "max_trades_symbol_day": int(day_n.max()) if day_n.size else 0,
        "median_trades_symbol_day": float(np.median(day_n)),
        "q90": float(np.quantile(day_n, 0.90)),
        "q95": float(np.quantile(day_n, 0.95)),
        "q99": float(np.quantile(day_n, 0.99)),
    }


def _groups(rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    out: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        out.setdefault((row["date"], row["symbol"]), []).append(row)
    return out


def _ratchet_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    labels = ("0", "1", "2", "3", "4+")
    out = []
    for label in labels:
        if label == "4+":
            group = [row for row in rows if int(row.get("ratchets") or 0) >= 4]
        else:
            group = [row for row in rows if int(row.get("ratchets") or 0) == int(label)]
        out.append({"ratchets": label, **_pack(group)})
    return out


def _friction(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for bps in FRICTION:
        adjusted = []
        for row in rows:
            if row.get("pnl_yen") is None or row.get("entry_px") is None:
                continue
            cost = float(row["entry_px"]) * (bps / 10000.0) * 100.0
            item = dict(row)
            item["pnl_yen"] = float(row["pnl_yen"]) - cost
            item["bps"] = float(row["bps"]) - bps
            adjusted.append(item)
        out.append({"friction_bps": bps, **_pack(adjusted)})
    return out


def _daily(rows: list[dict[str, Any]], dates: list[str]) -> dict[str, Any]:
    pnl = {day: 0.0 for day in dates}
    for row in rows:
        if row.get("pnl_yen") is not None:
            pnl[row["date"]] = pnl.get(row["date"], 0.0) + float(row["pnl_yen"])
    series = np.asarray([pnl[day] for day in dates], dtype=float)
    order = np.argsort(series)
    worst = [{"date": dates[int(i)], "pnl": float(series[int(i)])} for i in order[:5]]
    best_i = int(np.argmax(series)) if series.size else 0

    def _roll(window: int) -> list[float]:
        if series.size < window:
            return []
        return [float(np.sum(series[i : i + window])) for i in range(series.size - window + 1)]

    return {
        "positive_day_n": int(np.sum(series > 0)),
        "negative_day_n": int(np.sum(series < 0)),
        "flat_day_n": int(np.sum(series == 0)),
        "positive_day_rate": float(np.mean(series > 0)) if series.size else 0.0,
        "mean": float(series.mean()) if series.size else 0.0,
        "median": float(np.median(series)) if series.size else 0.0,
        "worst_day": worst[0] if worst else None,
        "best_day": {"date": dates[best_i], "pnl": float(series[best_i])} if series.size else None,
        "five_worst": worst,
        "rolling_5": _roll(5),
        "rolling_10": _roll(10),
    }


def _clock(rows: list[dict[str, Any]]) -> dict[str, Any]:
    blocks: dict[str, list[dict[str, Any]]] = {}
    sessions: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        sessions.setdefault(str(row.get("session") or ""), []).append(row)
        stamp = row.get("entry_t")
        if stamp is None:
            continue
        local = datetime.fromtimestamp(float(stamp), JST)
        minute = (local.minute // 30) * 30
        blocks.setdefault(f"{local.hour:02d}:{minute:02d}", []).append(row)
    return {
        "session": {key: _pack(value) for key, value in sessions.items()},
        "clock_30m": {key: _pack(blocks[key]) for key in sorted(blocks)},
    }


def _tails(rows: list[dict[str, Any]], dates: list[str]) -> dict[str, Any]:
    priced = [row for row in rows if row.get("pnl_yen") is not None]
    by_pnl = sorted(priced, key=lambda row: float(row["pnl_yen"]), reverse=True)
    day_pnl: dict[str, float] = {}
    sym_pnl: dict[str, float] = {}
    for row in priced:
        day_pnl[row["date"]] = day_pnl.get(row["date"], 0.0) + float(row["pnl_yen"])
        sym_pnl[row["symbol"]] = sym_pnl.get(row["symbol"], 0.0) + float(row["pnl_yen"])
    best_days = [day for day, _pnl in sorted(day_pnl.items(), key=lambda item: item[1], reverse=True)]
    best_syms = [sym for sym, _pnl in sorted(sym_pnl.items(), key=lambda item: item[1], reverse=True)]

    def _without(drop_rows: set[int]) -> dict[str, Any]:
        return _pack([row for i, row in enumerate(priced) if i not in drop_rows])

    index = {id(row): i for i, row in enumerate(priced)}
    out = {}
    for n, label in ((1, "drop_top1_trade"), (5, "drop_top5_trades"), (10, "drop_top10_trades")):
        out[label] = _without({index[id(row)] for row in by_pnl[:n]})
    out["drop_best_day"] = _pack([row for row in priced if row["date"] != best_days[0]]) if best_days else _pack([])
    out["drop_top3_days"] = _pack([row for row in priced if row["date"] not in set(best_days[:3])])
    out["drop_top_symbol"] = _pack([row for row in priced if row["symbol"] != best_syms[0]]) if best_syms else _pack([])
    out["drop_top3_symbols"] = _pack([row for row in priced if row["symbol"] not in set(best_syms[:3])])
    return out


def _share(rows: list[dict[str, Any]], key: str) -> dict[str, Any]:
    positive = [(str(row[key]), float(row["pnl_yen"])) for row in rows if row.get("pnl_yen") is not None and float(row["pnl_yen"]) > 0]
    total = sum(value for _name, value in positive)
    if total <= 0:
        return {"top": None, "share": None}
    best = -1.0
    name = None
    for item in {item for item, _value in positive}:
        got = sum(value for label, value in positive if label == item)
        if got > best:
            best = got
            name = item
    return {"top": name, "share": best / total}


def _verdict(parity: bool, latency: dict[str, Any], first: dict[str, Any], reentry: dict[str, Any], extension_by_latency: dict[str, Any]) -> str:
    if not parity:
        return "V2_ROBUSTNESS_AUDIT_BASELINE_PARITY_FAIL"
    pnl_250 = float((latency.get("250") or {}).get("pnl") or 0.0)
    pnl_500 = float((latency.get("500") or {}).get("pnl") or 0.0)
    ext_250 = float(((extension_by_latency.get("250") or {}).get("pnl")) or 0.0)
    if pnl_250 <= 0 and pnl_500 <= 0:
        return "EVENT_TIME_IMPULSE_V2_ZERO_LATENCY_EDGE_ONLY"
    re_pnl = float(reentry.get("pnl") or 0.0)
    first_pnl = float(first.get("pnl") or 0.0)
    total = re_pnl + first_pnl
    if total > 0 and re_pnl / total >= 0.80 and first_pnl <= 0:
        return "EVENT_TIME_IMPULSE_V2_REENTRY_CHATTER_DEPENDENT"
    if ext_250 <= 0:
        return "EVENT_TIME_IMPULSE_V2_TEMPORALLY_UNSTABLE"
    if pnl_250 > 0 and ext_250 > 0:
        return "EVENT_TIME_IMPULSE_V2_ROBUSTNESS_SUPPORTED"
    return "EVENT_TIME_IMPULSE_V2_ROBUSTNESS_NOT_SUPPORTED"


def build(scanned: dict[str, Any]) -> dict[str, Any]:
    baseline = [row for row in scanned["baseline"] if row.get("pnl_yen") is not None]
    base = _pack(baseline)
    parity = bool(base["trade_n"] == BASE_N and abs(float(base["pnl"]) - BASE_PNL) < 1e-6 and base["pf"] is not None and abs(float(base["pf"]) - BASE_PF) < 1e-9)
    bound = bind()
    sha_ok = (
        bound["execution_sha256"] == "5add98f88b750cabf032a32c62b15c2c524283f7f69f940b5474ca9c026f8f09"
        and bound["exit_sha256"] == "917c7f18c2b1e53316f34b972e5f7cbdcdd32f3ce217d027d1c429c1bc095cb8"
        and bound["strategy_sha256"] == "1d5ac587d4a67a7da4de9def64a6471428a136e4e30e1d26ca904983a8bcf505"
    )
    latency = {}
    extension = {}
    original = {}
    for ms in LATENCIES_MS:
        rows = [row for row in scanned["latency"][ms] if row.get("pnl_yen") is not None]
        key = str(ms)
        latency[key] = {**_slice_pack(rows, scanned["dates"]), "counts": scanned["latency_counts"][ms]}
        extension[key] = latency[key]["EXTENSION17"]
        original[key] = latency[key]["ORIGINAL18"]
    first = _pack([row for row in baseline if not row.get("reentry")])
    reentry = _pack([row for row in baseline if row.get("reentry")])
    verdict = _verdict(parity and sha_ok, {key: value["all"] for key, value in latency.items()}, first, reentry, extension)
    symbol_rows = {}
    sector_rows = {}
    for row in baseline:
        symbol_rows.setdefault(row["symbol"], []).append(row)
        sector_rows.setdefault(str(row.get("sector") or ""), []).append(row)
    return {
        "verdict": verdict,
        "next": "STOP",
        "parity": {"exact": parity and sha_ok, "trade_n": base["trade_n"], "pnl": base["pnl"], "pf": base["pf"], "sha_ok": sha_ok},
        "baseline": _slice_pack(baseline, scanned["dates"]),
        "hold_buckets": _hold_buckets(baseline),
        "first_entry": first,
        "reentry": reentry,
        "reentry_ordinal": _ordinal(baseline),
        "chatter": _gaps(baseline),
        "ratchet": _ratchet_groups(baseline),
        "friction": _friction(baseline),
        "daily": _daily(baseline, scanned["dates"]),
        "daily_by_latency": {str(ms): _daily([row for row in scanned["latency"][ms] if row.get("pnl_yen") is not None], scanned["dates"]) for ms in LATENCIES_MS},
        "latency": latency,
        "original_by_latency": original,
        "extension_by_latency": extension,
        "clock": _clock(baseline),
        "symbol": {key: _pack(value) for key, value in sorted(symbol_rows.items(), key=lambda item: _pack(item[1])["pnl"], reverse=True)[:15]},
        "sector": {key: _pack(value) for key, value in sector_rows.items()},
        "tails": _tails(baseline, scanned["dates"]),
        "concentration": {
            "trade": _share([{"pnl_yen": row["pnl_yen"], "trade": str(i)} for i, row in enumerate(baseline)], "trade"),
            "day": _share(baseline, "date"),
            "symbol": _share(baseline, "symbol"),
        },
        "opportunity": {
            "unique_symbol_day_n": len({(row["date"], row["symbol"]) for row in baseline}),
            "parent_full_episode_n": 11930,
            "completed_trade_n": base["trade_n"],
            "first_entry_n": first["trade_n"],
            "reentry_n": reentry["trade_n"],
            "new_episode_reentry_n": reentry["trade_n"],
            "one_signal_per_parent_episode": True,
        },
        "identity": bound,
    }
