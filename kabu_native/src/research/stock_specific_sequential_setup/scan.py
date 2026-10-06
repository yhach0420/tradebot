"""Load the sealed 105-universe minute panel and accumulate causal episodes."""
from __future__ import annotations

from collections import defaultdict
from typing import Any

import numpy as np
import pyarrow.dataset as ds

from research.causal_driver_pb1.datasets.universe import load_research_observation_universe
from research.causal_driver_pb1.phase2_precommit.stock_semantics import minute_parquet_path
from research.stock_specific_sequential_setup import C1_LAST, DEV_FIRST, FAMILIES, FV_FIRST, HORIZONS
from research.stock_specific_sequential_setup.episodes import walk_session


def _blank() -> dict[str, list]:
    return {
        "date": [],
        "symbol": [],
        "ret3": [],
        "ret5": [],
        "ret10": [],
        "mfe5": [],
        "mae5": [],
        "room": [],
        "vol_pb_ratio": [],
        "vol_re_ratio": [],
        "rci_impulse": [],
        "rci_min": [],
        "rci_signal": [],
    }


def _session_slices(minutes: np.ndarray) -> list[tuple[int, int]]:
    n = int(minutes.size)
    if n == 0:
        return []
    cuts = [0]
    for i in range(1, n):
        if int(minutes[i]) - int(minutes[i - 1]) > 30:
            cuts.append(i)
    cuts.append(n)
    return [(cuts[k], cuts[k + 1]) for k in range(len(cuts) - 1) if cuts[k + 1] - cuts[k] >= 24]


def _num(values) -> np.ndarray:
    return np.asarray(values, dtype=float)


def scan() -> dict[str, Any]:
    universe = load_research_observation_universe()
    signals = {name: _blank() for name in FAMILIES}
    stages = {name: {"ret5": [], "ret3": [], "ret10": []} for name in ("TREND_CONTEXT", "IMPULSE", "PULLBACK", "STABILIZATION")}
    features = defaultdict(list)
    dates: set[str] = set()
    episode_n = impulse_n = pullback_n = stab_n = 0
    rows_read = 0
    max_date = ""
    missing = []
    for idx, symbol in enumerate(universe.ordered_symbols, start=1):
        path = minute_parquet_path(symbol)
        if not path.is_file():
            missing.append(symbol)
            continue
        dataset = ds.dataset(str(path), format="parquet")
        table = dataset.to_table(
            columns=["date", "time_label", "open", "high", "low", "close", "volume", "trading_value"],
            filter=(ds.field("date") >= DEV_FIRST) & (ds.field("date") <= C1_LAST),
        )
        frame = table.to_pandas()
        if frame.empty:
            continue
        frame["date"] = frame["date"].astype(str)
        if frame["date"].ge(FV_FIRST).any() or frame["date"].gt(C1_LAST).any():
            raise RuntimeError("frozen_validation_or_later_rows_loaded")
        rows_read += int(len(frame))
        max_date = max(max_date, str(frame["date"].max()))
        for day, group in frame.groupby("date", sort=True):
            day = str(day)
            if day < DEV_FIRST or day > C1_LAST:
                raise RuntimeError("date_outside_seal")
            dates.add(day)
            group = group.sort_values("time_label")
            labels = [str(x)[:5] for x in group["time_label"].tolist()]
            minutes = np.asarray([int(x[:2]) * 60 + int(x[3:5]) for x in labels], dtype=int)
            opens = _num(group["open"])
            highs = _num(group["high"])
            lows = _num(group["low"])
            closes = _num(group["close"])
            volumes = _num(group["volume"])
            traded = _num(group["trading_value"])
            for lo, hi in _session_slices(minutes):
                walked = walk_session(minutes[lo:hi], opens[lo:hi], highs[lo:hi], lows[lo:hi], closes[lo:hi], volumes[lo:hi], traded[lo:hi])
                for row in walked:
                    episode_n += 1
                    impulse_n += int(row["reached_impulse"])
                    pullback_n += int(row["reached_pullback"])
                    stab_n += int(row["reached_stabilization"])
                    for stage, payload in row["stages"].items():
                        if payload is None or payload.get("ret_5") is None:
                            continue
                        stages[stage]["ret5"].append(float(payload["ret_5"]))
                        if payload.get("ret_3") is not None:
                            stages[stage]["ret3"].append(float(payload["ret_3"]))
                        if payload.get("ret_10") is not None:
                            stages[stage]["ret10"].append(float(payload["ret_10"]))
                    if row["reached_stabilization"] and row["stabilization_ret5"] is not None:
                        label = 1 if float(row["stabilization_ret5"]) > 0 else 0
                        for key in (
                            "pullback_depth_bps",
                            "distance_ema9_bps",
                            "distance_ema21_bps",
                            "distance_vwap_bps",
                            "pullback_duration_bars",
                        ):
                            if row[key] is not None:
                                features[key].append(float(row[key]))
                                features[key + "_up"].append(label)
                        if row["lower_low"] is not None:
                            features["lower_low"].append(float(row["lower_low"]))
                            features["lower_low_up"].append(label)
                        if row["close_below_ema21"] is not None:
                            features["close_below_ema21"].append(float(row["close_below_ema21"]))
                            features["close_below_ema21_up"].append(label)
                    for sig in row["signals"]:
                        bucket = signals[sig["family"]]
                        bucket["date"].append(day)
                        bucket["symbol"].append(symbol)
                        bucket["ret3"].append(sig.get("ret_3"))
                        bucket["ret5"].append(sig.get("ret_5"))
                        bucket["ret10"].append(sig.get("ret_10"))
                        bucket["mfe5"].append(sig.get("mfe_5"))
                        bucket["mae5"].append(sig.get("mae_5"))
                        bucket["room"].append(sig.get("room_to_resistance_bps"))
                        bucket["vol_pb_ratio"].append(sig.get("pullback_impulse_volume_ratio"))
                        bucket["vol_re_ratio"].append(sig.get("reacceleration_pullback_volume_ratio"))
                        bucket["rci_impulse"].append(sig.get("rci_impulse"))
                        bucket["rci_min"].append(sig.get("rci_pullback_min"))
                        bucket["rci_signal"].append(sig.get("rci_signal"))
        if idx % 5 == 0 or idx == len(universe.ordered_symbols):
            print(f"SCAN {idx}/{len(universe.ordered_symbols)} episodes={episode_n} rows={rows_read}", flush=True)
    if missing:
        raise RuntimeError(f"missing_minute_parquet:{missing[:5]}")
    if max_date > C1_LAST or (max_date and max_date >= FV_FIRST):
        raise RuntimeError("max_date_crossed_firewall")
    return {
        "universe_id": universe.universe_id,
        "universe_sha256": universe.source_sha256,
        "universe_n": universe.symbol_count,
        "symbols_order_sha256": universe.symbols_order_sha256,
        "episode_n": episode_n,
        "impulse_n": impulse_n,
        "pullback_n": pullback_n,
        "stabilization_n": stab_n,
        "rows_read": rows_read,
        "max_date": max_date,
        "min_date": min(dates) if dates else None,
        "session_dates": sorted(dates),
        "signals": signals,
        "stages": stages,
        "features": {k: v for k, v in features.items()},
        "prospective_rows_read": 0,
        "new_data_acquired": False,
    }
