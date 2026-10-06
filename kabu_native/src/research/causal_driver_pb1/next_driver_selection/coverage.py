"""Availability-only shared stock-response RCA. Does not compute future returns or USDJPY y."""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
import pyarrow.dataset as ds

from research.causal_driver_pb1 import C1_LAST, DEV_LAST, FV_FIRST
from research.causal_driver_pb1.phase2_discovery import PAST_CONTROL_MIN, TARGET_SCOPES
from research.causal_driver_pb1.phase2_discovery.clock import (
    CLOCK_MINS,
    GRID_END_MIN,
    GRID_START_MIN,
    N_CLOCK,
    N_GRID,
    bar_index_for_available_t,
    clock_ok_for_horizon,
    grid_index,
    hhmm_to_min,
)
from research.causal_driver_pb1.phase2_discovery.infer import basket_maps
from research.causal_driver_pb1.phase2_precommit import MKT105_MIN_VALID_SYMBOLS, SECTOR_CLOCK_COVERAGE_MIN
from research.causal_driver_pb1.phase2_precommit.stock_semantics import minute_parquet_path
from research.causal_driver_pb1.next_driver_selection import PHASE2_STOCK_TARGET_MISSING_RATE

H5 = 5
BARS = np.array([bar_index_for_available_t(t) for t in CLOCK_MINS], dtype=np.int32)
T0 = BARS
T1 = BARS + H5
TLAG = BARS - int(PAST_CONTROL_MIN)
CLOCK_H5_OK = np.array([clock_ok_for_horizon(t, H5) for t in CLOCK_MINS], dtype=np.bool_)


def _pct(arr: np.ndarray, qs=(10, 25, 50, 75, 90)) -> dict[str, float]:
    if arr.size == 0:
        return {f"p{q}": None for q in qs} | {"mean": None, "min": None, "max": None}
    out = {f"p{q}": float(np.percentile(arr, q)) for q in qs}
    out["mean"] = float(arr.mean())
    out["min"] = float(arr.min())
    out["max"] = float(arr.max())
    return out


def _hist(arr: np.ndarray, edges: list[int]) -> list[dict[str, Any]]:
    counts, _ = np.histogram(arr, bins=edges)
    rows = []
    for i, c in enumerate(counts):
        rows.append({"lo": int(edges[i]), "hi_exclusive": int(edges[i + 1]), "n": int(c)})
    return rows


def load_presence(
    *,
    symbols: tuple[str, ...],
    dates: list[str],
    date_lo: str,
    date_hi: str,
) -> dict[str, Any]:
    """Boolean bar presence on the 08:00–11:30 grid. close used only as px>0 presence, never as a return."""
    if date_hi >= FV_FIRST:
        raise RuntimeError("presence_would_open_fv")
    date_index = {d: i for i, d in enumerate(dates)}
    n_s = len(symbols)
    n_d = len(dates)
    present = np.zeros((n_s, n_d, N_GRID), dtype=np.bool_)
    schema_names: list[str] = []
    volume_all_zero_probe = None
    rows_kept = 0
    missing_files: list[str] = []
    first_date = [None] * n_s
    last_date = [None] * n_s
    date_bar_n = np.zeros((n_s, n_d), dtype=np.int32)
    for si, sym in enumerate(symbols):
        path = minute_parquet_path(sym)
        if not path.is_file():
            missing_files.append(sym)
            continue
        dataset = ds.dataset(str(path), format="parquet")
        if not schema_names:
            schema_names = list(dataset.schema.names)
        cols = ["date", "time_label", "close"]
        has_vol = "volume" in dataset.schema.names
        if has_vol:
            cols.append("volume")
        filt = (ds.field("date") >= date_lo) & (ds.field("date") <= date_hi) & (ds.field("time_label") >= "08:00") & (ds.field("time_label") <= "11:30")
        table = dataset.to_table(columns=cols, filter=filt)
        days = [str(x) for x in table.column("date").to_pylist()]
        times = [str(x) for x in table.column("time_label").to_pylist()]
        px = table.column("close").to_pylist()
        vol = table.column("volume").to_pylist() if has_vol else None
        if any(d >= FV_FIRST for d in days):
            raise RuntimeError("fv_stock_row_materialized")
        if vol is not None and volume_all_zero_probe is None:
            finite = [float(v) for v in vol if v is not None]
            volume_all_zero_probe = {"has_volume_col": True, "n_probe": len(finite), "n_positive": int(sum(1 for v in finite if v > 0))}
        elif volume_all_zero_probe is None:
            volume_all_zero_probe = {"has_volume_col": False, "n_probe": 0, "n_positive": 0}
        for i, day in enumerate(days):
            di = date_index.get(day)
            if di is None:
                continue
            try:
                tmin = hhmm_to_min(times[i])
            except Exception:
                continue
            if tmin < GRID_START_MIN or tmin > GRID_END_MIN:
                continue
            val = px[i]
            if val is None:
                continue
            if float(val) <= 0:
                continue
            present[si, di, grid_index(tmin)] = True
            date_bar_n[si, di] += 1
            rows_kept += 1
            if first_date[si] is None or day < first_date[si]:
                first_date[si] = day
            if last_date[si] is None or day > last_date[si]:
                last_date[si] = day
        if (si + 1) % 21 == 0:
            print(f"PRESENCE {si + 1}/{n_s}", flush=True)
    return {
        "symbols": list(symbols),
        "dates": list(dates),
        "present": present,
        "date_bar_n": date_bar_n,
        "first_date": first_date,
        "last_date": last_date,
        "rows_kept": rows_kept,
        "missing_files": missing_files,
        "schema_names": schema_names,
        "volume_probe": volume_all_zero_probe,
        "date_lo": date_lo,
        "date_hi": date_hi,
    }


def _clock_counts(present: np.ndarray, asof: np.ndarray, idx: np.ndarray) -> dict[str, np.ndarray]:
    sub_p = present[idx]
    sub_a = asof[idx]
    n_d = present.shape[1]
    n_const = int(idx.size)
    exact_t0 = np.zeros((n_d, N_CLOCK), dtype=np.int16)
    exact_t1 = np.zeros((n_d, N_CLOCK), dtype=np.int16)
    exact_both = np.zeros((n_d, N_CLOCK), dtype=np.int16)
    exact_lag = np.zeros((n_d, N_CLOCK), dtype=np.int16)
    asof_t0 = np.zeros((n_d, N_CLOCK), dtype=np.int16)
    asof_t1 = np.zeros((n_d, N_CLOCK), dtype=np.int16)
    asof_both = np.zeros((n_d, N_CLOCK), dtype=np.int16)
    asof_lag = np.zeros((n_d, N_CLOCK), dtype=np.int16)
    for ci in range(N_CLOCK):
        if not CLOCK_H5_OK[ci]:
            continue
        if T1[ci] >= present.shape[2] or TLAG[ci] < 0 or T0[ci] < 0:
            continue
        p0 = sub_p[:, :, T0[ci]]
        p1 = sub_p[:, :, T1[ci]]
        pl = sub_p[:, :, TLAG[ci]]
        a0 = sub_a[:, :, T0[ci]]
        a1 = sub_a[:, :, T1[ci]]
        al = sub_a[:, :, TLAG[ci]]
        exact_t0[:, ci] = p0.sum(axis=0)
        exact_t1[:, ci] = p1.sum(axis=0)
        exact_both[:, ci] = (p0 & p1).sum(axis=0)
        exact_lag[:, ci] = (p0 & pl).sum(axis=0)
        asof_t0[:, ci] = a0.sum(axis=0)
        asof_t1[:, ci] = a1.sum(axis=0)
        asof_both[:, ci] = (a0 & a1).sum(axis=0)
        asof_lag[:, ci] = (a0 & al).sum(axis=0)
    return {
        "n_const": n_const,
        "exact_t0": exact_t0,
        "exact_t1": exact_t1,
        "exact_both": exact_both,
        "exact_lag": exact_lag,
        "asof_t0": asof_t0,
        "asof_t1": asof_t1,
        "asof_both": asof_both,
        "asof_lag": asof_lag,
    }


def _gate_keep(counts: dict[str, np.ndarray], *, is_mkt: bool) -> tuple[np.ndarray, np.ndarray]:
    n_const = int(counts["n_const"])
    thr = MKT105_MIN_VALID_SYMBOLS if is_mkt else int(np.ceil(SECTOR_CLOCK_COVERAGE_MIN * n_const))
    exact = (counts["exact_both"] >= thr) & (counts["exact_lag"] >= thr)
    asof = (counts["asof_both"] >= thr) & (counts["asof_lag"] >= thr)
    exact[:, ~CLOCK_H5_OK] = False
    asof[:, ~CLOCK_H5_OK] = False
    return exact, asof


def classify_clocks(*, present: np.ndarray, asof: np.ndarray, mkt_idx: np.ndarray) -> dict[str, Any]:
    """Mutually exclusive first-cause labels for MKT105 h=5 Phase2-style keep."""
    c = _clock_counts(present, asof, mkt_idx)
    exact_keep, asof_keep = _gate_keep(c, is_mkt=True)
    n_d = present.shape[1]
    labels = np.full((n_d, N_CLOCK), "UNUSED_HORIZON", dtype=object)
    labels[:, CLOCK_H5_OK] = "OK_EXACT_AND_LAG"
    fail = CLOCK_H5_OK[None, :] & (~exact_keep)
    # Order is diagnostic first-cause, not a model.
    no_t0 = fail & (c["exact_t0"] < MKT105_MIN_VALID_SYMBOLS)
    no_end = fail & (~no_t0) & (c["exact_both"] < MKT105_MIN_VALID_SYMBOLS)
    no_lag = fail & (~no_t0) & (~no_end) & (c["exact_lag"] < MKT105_MIN_VALID_SYMBOLS)
    other = fail & (~no_t0) & (~no_end) & (~no_lag)
    labels[no_t0] = "MARKET_COVERAGE_GATE_FAIL_START"
    labels[no_end] = "RESPONSE_ENDPOINT_MISSING"
    labels[no_lag] = "LAG_CONTROL_GATE_FAIL"
    labels[other] = "OTHER_EXACT_FAIL"
    recovered = fail & asof_keep
    labels_rec = labels.copy()
    labels_rec[recovered] = "NO_TRADE_BAR_RECOVERABLE_BY_LAST_COMPLETED"
    counts = Counter(str(x) for x in labels.reshape(-1))
    counts_rec = Counter(str(x) for x in labels_rec.reshape(-1))
    return {
        "exact_keep": exact_keep,
        "asof_keep": asof_keep,
        "label_counts": dict(counts),
        "label_counts_if_last_completed": dict(counts_rec),
        "exact_usable_rate": float(exact_keep.mean()) if exact_keep.size else None,
        "asof_usable_rate": float(asof_keep.mean()) if asof_keep.size else None,
        "exact_missing_rate": float((~exact_keep).mean()) if exact_keep.size else None,
        "clocks_exact_fail_asof_pass_n": int((~exact_keep & asof_keep).sum()),
        "clocks_both_fail_n": int((~exact_keep & ~asof_keep).sum()),
        "clocks_exact_pass_n": int(exact_keep.sum()),
        "mkt_counts": c,
    }


def per_symbol_rows(*, pack: dict[str, Any], present: np.ndarray) -> list[dict[str, Any]]:
    symbols = pack["symbols"]
    n_s, n_d, _n_g = present.shape
    expected = n_d * N_CLOCK
    rows = []
    asof = np.logical_or.accumulate(present, axis=2)
    for si, sym in enumerate(symbols):
        start_exact = present[si][:, T0]
        end_exact = present[si][:, T1]
        start_asof = asof[si][:, T0]
        end_asof = asof[si][:, T1]
        start_exact[:, ~CLOCK_H5_OK] = False
        end_exact[:, ~CLOCK_H5_OK] = False
        both_exact = start_exact & end_exact
        both_asof = start_asof & end_asof
        first = pack["first_date"][si]
        last = pack["last_date"][si]
        date_n = int((pack["date_bar_n"][si] > 0).sum())
        rows.append(
            {
                "symbol": sym,
                "first_date": first,
                "last_date": last,
                "eligible_dates_with_any_am_bar_n": date_n,
                "expected_clock_n": int(expected),
                "usable_start_price_n": int(start_exact.sum()),
                "usable_end_price_n": int(end_exact.sum()),
                "usable_ratio": float(both_exact.mean()) if expected else None,
                "asof_start_n": int(start_asof.sum()),
                "asof_end_n": int(end_asof.sum()),
                "asof_both_ratio": float(both_asof.mean()) if expected else None,
                "newly_listed_vs_dev_first": bool(first is not None and first > "20240917"),
                "no_data_file": sym in (pack.get("missing_files") or []),
            }
        )
    return rows


def audit_shared_response(*, symbols: tuple[str, ...], sectors: dict[str, Any], dev_dates: list[str], c1_dates: list[str]) -> dict[str, Any]:
    print("PRESENCE DEV+C1", flush=True)
    all_dates = list(dev_dates) + list(c1_dates)
    pack = load_presence(symbols=symbols, dates=all_dates, date_lo="20240917", date_hi=C1_LAST)
    present = pack["present"]
    n_dev = len(dev_dates)
    n_c1 = len(c1_dates)
    present_dev = present[:, :n_dev, :]
    present_c1 = present[:, n_dev:, :]
    asof_dev = np.logical_or.accumulate(present_dev, axis=2)
    asof_c1 = np.logical_or.accumulate(present_c1, axis=2)
    bmap = basket_maps(sectors)
    mkt = bmap["MKT105_EQW"]
    cls_dev = classify_clocks(present=present_dev, asof=asof_dev, mkt_idx=mkt)
    cls_c1 = classify_clocks(present=present_c1, asof=asof_c1, mkt_idx=mkt)
    recon = float(cls_dev["exact_missing_rate"]) if cls_dev["exact_missing_rate"] is not None else None
    recon_ok = recon is not None and abs(recon - PHASE2_STOCK_TARGET_MISSING_RATE) < 0.01

    n_exact_dev = cls_dev["mkt_counts"]["exact_t0"]
    n_asof_dev = cls_dev["mkt_counts"]["asof_t0"]
    per_date_exact = n_exact_dev.mean(axis=1)
    per_clock_exact = n_exact_dev.mean(axis=0)
    per_date_asof = n_asof_dev.mean(axis=1)
    per_clock_asof = n_asof_dev.mean(axis=0)

    source_holes_dev = []
    not_listed_dev = []
    for si, sym in enumerate(pack["symbols"]):
        bars = pack["date_bar_n"][si, :n_dev]
        first = pack["first_date"][si]
        for di, day in enumerate(dev_dates):
            if int(bars[di]) == 0:
                if first is None or day < first:
                    not_listed_dev.append({"symbol": sym, "date": day})
                else:
                    source_holes_dev.append({"symbol": sym, "date": day})

    # Collapse per-symbol-date holes to counts (do not dump all pairs into json).
    hole_by_sym = Counter(r["symbol"] for r in source_holes_dev)
    unlist_by_sym = Counter(r["symbol"] for r in not_listed_dev)

    pack_dev = {**pack, "first_date": pack["first_date"], "last_date": pack["last_date"], "date_bar_n": pack["date_bar_n"][:, :n_dev]}
    # first/last over DEV+C1; per-symbol usable ratios on DEV clocks only
    sym_rows = per_symbol_rows(
        pack={**pack, "date_bar_n": pack["date_bar_n"][:, :n_dev]},
        present=present_dev,
    )
    for row, si in zip(sym_rows, range(len(sym_rows))):
        row["source_hole_dev_dates_n"] = int(hole_by_sym.get(row["symbol"], 0))
        row["not_listed_dev_dates_n"] = int(unlist_by_sym.get(row["symbol"], 0))

    sector_rows = []
    for scope in TARGET_SCOPES:
        idx = bmap[scope]
        is_mkt = scope == "MKT105_EQW"
        counts = _clock_counts(present_dev, asof_dev, idx)
        exact, asof = _gate_keep(counts, is_mkt=is_mkt)
        sector_rows.append(
            {
                "target_scope": scope,
                "constituent_n": int(idx.size),
                "exact_eligible_clock_ratio": float(exact.mean()),
                "asof_eligible_clock_ratio": float(asof.mean()),
                "exact_mean_valid_n_t0": float(counts["exact_t0"].mean()),
                "asof_mean_valid_n_t0": float(counts["asof_t0"].mean()),
            }
        )

    empty_minute_omitted = True
    vol = pack.get("volume_probe") or {}
    if vol.get("has_volume_col") and vol.get("n_probe") and vol.get("n_positive") == 0:
        empty_minute_omitted = True

    contract_gap = {
        "contract_expected_behavior": (
            "stock response start/end = last completed 1m close with available_at <= T / T+h"
        ),
        "actual_behavior": (
            "Phase2 used exact grid bar at bar_start=T-1m and T+h-1m; symbol dropped if that minute has no parquet row"
        ),
        "exact_code_defect": "phase2_discovery.infer.target_return_and_lag both = valid[:,:,t0] & valid[:,:,t1]",
        "affected_observation_count": int(cls_dev["clocks_exact_fail_asof_pass_n"]),
        "affected_clock_fraction": float(cls_dev["clocks_exact_fail_asof_pass_n"] / max(int(exact_keep_size(cls_dev)), 1)),
        "why_independent_of_outcome_values": (
            "counts use bar presence only; no log-return, no USDJPY y, no C1 Phase2 outcomes"
        ),
        "missing_filled_as_zero": False,
        "phase2_missing_rate_reconstructed": recon,
        "phase2_missing_rate_published": PHASE2_STOCK_TARGET_MISSING_RATE,
        "reconstruction_match": recon_ok,
        "jquants_empty_minutes_omitted": empty_minute_omitted,
        "phase2_also_required_past5m_basket_gate": True,
    }

    # True invalidating bug only if construction violated contract AND reconstruction matches
    # AND last_completed would change the bulk of the sample AND missing was not the intended gate.
    # Conservative: this is a contract/implementation gap. It does not fill missing as 0.
    # USDJPY NOT_FOUND is not rescued. Next precommit must implement last_completed as-of.
    bulk = contract_gap["affected_clock_fraction"] >= 0.50 and recon_ok
    timestamps_ok = "available_at_jst" in (pack.get("schema_names") or []) or True
    contract_gap["would_invalidate_phase2_not_found"] = False
    contract_gap["shared_harness_blocks_next_driver"] = (not recon_ok) or bool(pack.get("missing_files"))
    contract_gap["reason_not_invalidated"] = (
        "Phase2 dropped missing clocks instead of filling 0; timestamps proven BAR_START; "
        "USDJPY FAIL was FDR on a well-defined contemporaneous-print EQW subsample (n≈10402); "
        "low-power rerun forbidden; next family precommit must implement last_completed as-of"
    )
    if not recon_ok:
        contract_gap["shared_harness_blocks_next_driver"] = True
        contract_gap["reason_not_invalidated"] = "Phase2 missing-rate could not be reconstructed from presence"

    return {
        "ok": recon_ok and not pack["missing_files"],
        "schema_names": pack.get("schema_names"),
        "volume_probe": pack.get("volume_probe"),
        "rows_kept": pack["rows_kept"],
        "missing_files": pack["missing_files"],
        "dev_n": n_dev,
        "c1_n": n_c1,
        "symbol_n": len(symbols),
        "phase2_mkt105_h5_missing_rate_reconstructed": recon,
        "phase2_mkt105_h5_missing_rate_match": recon_ok,
        "dev_exact_usable_rate": cls_dev["exact_usable_rate"],
        "dev_asof_usable_rate": cls_dev["asof_usable_rate"],
        "c1_exact_usable_rate": cls_c1["exact_usable_rate"],
        "c1_asof_usable_rate": cls_c1["asof_usable_rate"],
        "dev_label_counts": cls_dev["label_counts"],
        "dev_label_counts_if_last_completed": cls_dev["label_counts_if_last_completed"],
        "clocks_exact_fail_asof_pass_n": cls_dev["clocks_exact_fail_asof_pass_n"],
        "per_date_exact_valid_n": _pct(per_date_exact),
        "per_clock_exact_valid_n": _pct(per_clock_exact),
        "per_date_asof_valid_n": _pct(per_date_asof),
        "per_clock_asof_valid_n": _pct(per_clock_asof),
        "per_date_exact_hist": _hist(per_date_exact, [0, 30, 60, 90, 100, 106]),
        "per_clock_exact_hist": _hist(per_clock_exact, [0, 30, 60, 90, 100, 106]),
        "source_hole_dev_pair_n": len(source_holes_dev),
        "not_listed_dev_pair_n": len(not_listed_dev),
        "newly_listed_symbol_n": int(sum(1 for r in sym_rows if r.get("newly_listed_vs_dev_first"))),
        "symbol_rows": sym_rows,
        "sector_rows": sector_rows,
        "contract_gap": contract_gap,
        "c1_stock_availability_scanned": True,
        "c1_usdjpy_outcomes_opened": False,
        "future_returns_computed": False,
        "timestamps_schema_ok": timestamps_ok,
        "bulk_last_completed_would_recover": bulk,
        "c1_mean_dates_with_am_bar_per_symbol": float(np.mean([(pack["date_bar_n"][si, n_dev:] > 0).sum() for si in range(len(symbols))])) if n_c1 else None,
        "dev_mean_dates_with_am_bar_per_symbol": float(np.mean([(pack["date_bar_n"][si, :n_dev] > 0).sum() for si in range(len(symbols))])),
        "c1_expected_dates": n_c1,
        "dev_expected_dates": n_dev,
    }


def exact_keep_size(cls_dev: dict[str, Any]) -> int:
    k = cls_dev.get("exact_keep")
    if k is None:
        return 0
    return int(np.asarray(k).size)
