"""Phase 0: schema / file / required-field sufficiency only (no PnL)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from research.e1_x19_outcome_pre_path.population import _build_day, attach_derived
from research.e1_x14_board_independent_signal.ticks import list_day_symbols, load_symbol_ticks
from research.e1_x28_executable_joint.board import load_board_events

from . import BOARD_MAPPING_SHA, STRESS_DAYS

NATIVE = Path(__file__).resolve().parents[3]
OUT = NATIVE / "results" / "research" / "e1_x28d_additional_stress"

# Features used by frozen Specific49 + Family118 (plus derived dispersion).
COHORT_FEATURES = (
    "return_30s", "return_60s", "return_180s", "return_300s",
    "acceleration_30s_vs_prior30s",
    "distance_from_vwap_bps",
    "distance_from_session_high_bps", "distance_from_session_low_bps",
    "drawdown_from_recent_high_bps", "rebound_from_recent_low_bps",
    "higher_low_180s", "lower_low_180s",
    "range_width_60s", "range_width_180s",
    "volume_delta_30s", "volume_delta_60s", "volume_delta_180s",
    "volume_ratio_30s_vs_prior120s",
    "trading_value_delta_60s", "trading_value_delta_180s",
    "volume_percentile_60s", "trading_value_percentile_180s",
    "advancing_symbol_fraction", "declining_symbol_fraction",
    "universe_median_return_60s", "universe_median_return_180s", "universe_median_return_300s",
    "symbol_minus_median_return_60s", "symbol_minus_median_return_180s",
    "cs_return_dispersion_60s",
)


def _dash(day: str) -> str:
    return f"{day[:4]}-{day[4:6]}-{day[6:]}"


def _scan_symbol_fields(day: str, symbol: str, max_lines: int = 5000) -> dict[str, Any]:
    fp = NATIVE / "data" / "push_jsonl" / _dash(day) / f"{symbol}.T.jsonl"
    if not fp.exists():
        fp = NATIVE / "data" / "push_jsonl" / _dash(day) / f"{symbol}.jsonl"
    out = {
        "file_exists": fp.exists(),
        "lines_scanned": 0,
        "has_recorded_at": False,
        "has_CurrentPrice": False,
        "has_CurrentPriceTime": False,
        "has_Sell1_Price": False,
        "has_Sell1_Qty": False,
        "has_Buy1_Price": False,
        "has_Buy1_Qty": False,
        "has_special_quote_key_or_absent_ok": True,
        "has_session_identifiable": False,
    }
    if not fp.exists():
        return out
    for i, line in enumerate(fp.open("rb")):
        if i >= max_lines:
            break
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        out["lines_scanned"] += 1
        if d.get("recorded_at"):
            out["has_recorded_at"] = True
        pay = d.get("payload") or {}
        if pay.get("CurrentPrice") is not None:
            out["has_CurrentPrice"] = True
        if pay.get("CurrentPriceTime"):
            out["has_CurrentPriceTime"] = True
        sell1 = pay.get("Sell1") or {}
        buy1 = pay.get("Buy1") or {}
        if isinstance(sell1, dict) and sell1.get("Price") is not None:
            out["has_Sell1_Price"] = True
        if isinstance(sell1, dict) and sell1.get("Qty") is not None:
            out["has_Sell1_Qty"] = True
        if isinstance(buy1, dict) and buy1.get("Price") is not None:
            out["has_Buy1_Price"] = True
        if isinstance(buy1, dict) and buy1.get("Qty") is not None:
            out["has_Buy1_Qty"] = True
        if pay.get("TradingSession") is not None or d.get("recorded_at"):
            out["has_session_identifiable"] = True
    return out


def _ensure_day_clusters(day: str) -> list[dict[str, Any]]:
    """Build/cache X19 clusters for day. Used for field availability only (no masks/PnL)."""
    OUT.mkdir(parents=True, exist_ok=True)
    cache = OUT / f"_clusters_{day}.jsonl"
    if cache.exists():
        rows = [json.loads(l) for l in cache.read_text(encoding="utf-8").splitlines() if l.strip()]
        print(f"  phase0 load clusters {day} n={len(rows)}", flush=True)
        return rows
    raw = _build_day(day)
    for r in raw:
        r["date"] = day
    with cache.open("w", encoding="utf-8") as f:
        for r in raw:
            f.write(json.dumps(r, default=str) + "\n")
    print(f"  phase0 built clusters {day} n={len(raw)}", flush=True)
    return raw


def _feature_availability_on_clusters(day: str, raw_rows: list[dict[str, Any]]) -> dict[str, Any]:
    derived = attach_derived(raw_rows)
    present = {f: 0 for f in COHORT_FEATURES}
    for r in derived:
        for f in COHORT_FEATURES:
            if r.get(f) is not None:
                present[f] += 1
    missing = [f for f, n in present.items() if n == 0]
    return {
        "ok": len(missing) == 0,
        "cluster_n": len(derived),
        "feature_nonzero_counts": present,
        "missing_features": missing,
        "entry_feature_semantics": "X19_build_day_plus_attach_derived",
        "quote_semantics": "X28_Sell1_ask_Buy1_bid",
        # Explicit blind flags
        "candidate_masks_applied": False,
        "pnl_computed": False,
        "return_computed": False,
        "signal_count_computed": False,
    }


def check_day(day: str) -> dict[str, Any]:
    push_dir = NATIVE / "data" / "push_jsonl" / _dash(day)
    capture_dir = NATIVE / "data" / "market_capture" / day
    syms = list_day_symbols(day)
    result: dict[str, Any] = {
        "date": day,
        "role": "ADDITIONAL_HISTORICAL_STRESS",
        "push_jsonl_dir_exists": push_dir.exists(),
        "symbol_file_count": len(syms),
        "capture_dir_exists": capture_dir.exists(),
        "sufficient": False,
        "failures": [],
        "notes": [],
        "performance_metrics_computed": False,
        "candidate_signal_count_computed": False,
        "pnl_computed": False,
        "return_computed": False,
        "pf_computed": False,
    }

    if not push_dir.exists() or len(syms) == 0:
        result["failures"].append("push_jsonl_missing_or_empty")
        return result

    cc_files = list(capture_dir.rglob("capture_completeness.json")) if capture_dir.exists() else []
    if cc_files:
        try:
            meta = json.loads(cc_files[0].read_text(encoding="utf-8"))
            result["capture_status"] = meta.get("status") or meta.get("capture_status")
            result["research_adoptable"] = meta.get("research_adoptable")
            if meta.get("research_adoptable") is False:
                result["failures"].append("capture_not_research_adoptable")
        except Exception as e:
            result["notes"].append(f"capture_meta_unreadable:{e}")
    else:
        result["notes"].append("capture_completeness_json_absent")

    cp_syms = []
    board_ok_n = 0
    field_samples = []
    for sym in syms:
        board = load_board_events(day, sym)
        if board["t"].size > 0 and board["ask"].size > 0 and board["bid"].size > 0:
            board_ok_n += 1
        # cheap CP detect via first ticks
        ticks = load_symbol_ticks(day, sym)
        has_cp = any(t.get("price") is not None for t in ticks[:30])
        if has_cp:
            cp_syms.append(sym)
            if len(field_samples) < 5:
                field_samples.append({"symbol": sym, **_scan_symbol_fields(day, sym, max_lines=3000)})

    result["board_usable_symbol_n"] = board_ok_n
    result["currentprice_symbol_n"] = len(cp_syms)
    result["field_samples"] = field_samples

    if board_ok_n == 0:
        result["failures"].append("no_board_quotes")
    if len(cp_syms) == 0:
        result["failures"].append("no_CurrentPrice_symbols")

    for sample in field_samples:
        for k in (
            "has_recorded_at", "has_CurrentPrice", "has_CurrentPriceTime",
            "has_Sell1_Price", "has_Sell1_Qty", "has_Buy1_Price", "has_Buy1_Qty",
            "has_session_identifiable",
        ):
            if not sample.get(k):
                result["failures"].append(f"missing_{k}_on_{sample.get('symbol')}")

    result["quote_semantics"] = {
        "entry_ask": "Sell1.Price",
        "exit_bid": "Buy1.Price",
        "board_mapping_sha": BOARD_MAPPING_SHA,
        "loader": "e1_x28_executable_joint.board.load_board_events",
    }

    # Build clusters once (cached) and verify cohort feature columns — still no masks/PnL
    if board_ok_n > 0 and len(cp_syms) > 0:
        print(f"=== phase0 feature availability {day} ===", flush=True)
        raw = _ensure_day_clusters(day)
        feat = _feature_availability_on_clusters(day, raw)
        result["feature_availability"] = {
            "ok": feat["ok"],
            "cluster_n": feat["cluster_n"],
            "missing_features": feat["missing_features"],
            "entry_feature_semantics": feat["entry_feature_semantics"],
            "candidate_masks_applied": False,
            "pnl_computed": False,
        }
        # store nonzero counts without implying performance
        result["feature_availability"]["features_with_values_n"] = sum(
            1 for n in feat["feature_nonzero_counts"].values() if n > 0
        )
        if not feat["ok"]:
            result["failures"].append("entry_feature_semantics_not_reproducible")
    else:
        result["feature_availability"] = {"ok": False}

    result["sufficient"] = (
        result["push_jsonl_dir_exists"]
        and board_ok_n > 0
        and len(cp_syms) > 0
        and bool((result.get("feature_availability") or {}).get("ok"))
        and "entry_feature_semantics_not_reproducible" not in result["failures"]
        and "no_board_quotes" not in result["failures"]
        and "no_CurrentPrice_symbols" not in result["failures"]
        and "push_jsonl_missing_or_empty" not in result["failures"]
        and "capture_not_research_adoptable" not in result["failures"]
    )
    for banned in (
        "pnl", "return", "profit_factor", "candidate_signal_count", "winner",
        "specific_family_delta",
    ):
        result.pop(banned, None)
    return result


def run_phase0() -> dict[str, Any]:
    days = {}
    for d in STRESS_DAYS:
        print(f"=== Phase0 check {d} ===", flush=True)
        days[d] = check_day(d)
    all_ok = all(days[d]["sufficient"] for d in STRESS_DAYS)
    return {
        "phase": "PHASE_0_DATA_SUFFICIENCY",
        "days": days,
        "all_days_sufficient": all_ok,
        "performance_blind": True,
        "no_interpolation": True,
        "no_yahoo_substitute": True,
    }
