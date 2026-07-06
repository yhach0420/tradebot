"""
Phase634: PBv2-only rise5 cap full-period counterfactual (research only).

Applies entry_rise_5min_pct (and combo_soft) caps to PBv2 accepted trades only;
OR accepted trades are unchanged. Uses all replayable live_session dirs under
results/small_paper (including pre-2026-06-25).

No ENTRY/EXIT/PBv2/OR/Freshness logic changes.
"""

from __future__ import annotations

import csv
import gzip
import json
import math
import shutil
import statistics
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

from research.phase631_profit_source_attribution import (
    CAT_FEATURES,
    ENTRY_FEATURES,
    _entry_pool,
    _minutes_from_open,
    _num,
    _parse_iso,
    _pnl_yen_100,
)
from research.phase632_pbv2_profit_filter_counterfactual import (
    _daily_pnl,
    _max_drawdown,
    _metrics,
    _profit_factor,
)
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

NATIVE_ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER_ROOT = NATIVE_ROOT / "results" / "small_paper"
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase634_pbv2_only_rise5_full_period"
PHASE634_VERDICT = "phase634_pbv2_only_rise5_full_period_done"
PHASE634_FAIL = "phase634_pbv2_only_rise5_full_period_failed"

PRE625_CUTOFF = "2026-06-25"
MAX_WORKERS = 4
DISK_USAGE_MAX_PCT = 76.0
PRICE_AGE_MAX = 5.0
BIG_WINNER_YEN = 5000.0
ENTRY_REDUCTION_MAX = 0.40
DAY_WORSEN_YEN = -20000.0
TOP_SYMBOL_SHARE_MAX = 0.35
BLOCKED_ANALYSIS_MAX_ROWS = 200

NUMERIC_THRESHOLDS = (0.3, 0.5, 0.66, 0.8, 1.0)
PERCENTILE_LABELS = ("p50", "p60", "p70", "p75", "p80", "p85", "p90", "p95")

FilterFn = Callable[[dict[str, Any]], bool]


def _disk_usage_pct(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return 100.0 * (1.0 - usage.free / usage.total)


def _iter_events(session_dir: Path) -> Iterable[dict[str, Any]]:
    jsonl = session_dir / "small_paper_events.jsonl"
    if jsonl.is_file():
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue
        return
    csv_path = session_dir / "small_paper_events.csv"
    if csv_path.is_file():
        yield from _stream_events_csv(csv_path)


def _is_push_replay_session(session_dir: Path) -> bool:
    summary_path = session_dir / "small_paper_summary.json"
    if not summary_path.is_file():
        return False
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        return str(summary.get("source") or "") == "push-replay"
    except (OSError, json.JSONDecodeError):
        return False


def discover_replayable_sessions(root: Path = SMALL_PAPER_ROOT) -> list[dict[str, Any]]:
    """All live_session_* dirs with completed trades (accepted + observer_exit)."""
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out
    for day_dir in sorted(root.iterdir()):
        if not day_dir.is_dir() or not (len(day_dir.name) == 8 and day_dir.name.isdigit()):
            continue
        day_iso = f"{day_dir.name[:4]}-{day_dir.name[4:6]}-{day_dir.name[6:8]}"
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            if not sess_dir.is_dir():
                continue
            if _is_push_replay_session(sess_dir):
                continue
            has_events = (sess_dir / "small_paper_events.jsonl").is_file() or (
                sess_dir / "small_paper_events.csv"
            ).is_file()
            if not has_events:
                continue
            trades = load_trades_for_session(sess_dir, day_iso)
            if not trades:
                continue
            out.append(
                {
                    "day": day_iso,
                    "day_key": day_dir.name,
                    "session": sess_dir.name,
                    "session_dir": str(sess_dir),
                    "trade_count": len(trades),
                    "pre625": day_iso < PRE625_CUTOFF,
                }
            )
    return out


def load_trades_for_session(session_dir: Path, day: str) -> list[dict[str, Any]]:
    if not (
        (session_dir / "small_paper_events.jsonl").is_file()
        or (session_dir / "small_paper_events.csv").is_file()
    ):
        return []

    accepted_by_key: dict[tuple[Any, Any], dict[str, Any]] = {}
    for e in _iter_events(session_dir):
        if e.get("event_type") != "accepted":
            continue
        key = (e.get("symbol"), e.get("entry_time") or e.get("message_index"))
        accepted_by_key[key] = e

    trades: list[dict[str, Any]] = []
    for e in _iter_events(session_dir):
        if e.get("event_type") != "observer_exit":
            continue
        sym = e.get("symbol")
        entry_time = e.get("entry_time")
        acc = accepted_by_key.get((sym, entry_time)) or {}

        entry_price = e.get("entry_price") or acc.get("entry_price") or acc.get("current_price")
        exit_price = e.get("exit_price") or e.get("current_price")
        pnl_pct = _num(e.get("pnl_pct"))
        pnl_yen = _pnl_yen_100(entry_price, exit_price, pnl_pct)
        if pnl_yen is None:
            continue

        hold_market = None
        et = _parse_iso(acc.get("entry_time") or entry_time)
        xt = _parse_iso(acc.get("exit_time"))
        if et is not None and xt is not None:
            hold_market = max(0.0, (xt - et).total_seconds())

        row: dict[str, Any] = {
            "day": day,
            "session": session_dir.name,
            "symbol": sym,
            "entry_time": entry_time or acc.get("entry_time"),
            "entry_type": acc.get("entry_type") or e.get("entry_type") or "PBV2",
            "entry_pool": _entry_pool(acc.get("entry_type") or e.get("entry_type")),
            "exit_reason": e.get("exit_reason") or e.get("structural_exit_reason") or "",
            "pnl_yen_100": pnl_yen,
            "pnl_pct": pnl_pct if pnl_pct is not None else 0.0,
            "peak_mfe_pct": _num(e.get("peak_mfe_pct")),
            "rolling_mfe_pct": _num(
                e.get("rolling_mfe_pct")
                if e.get("rolling_mfe_pct") is not None
                else acc.get("rolling_mfe_pct")
            ),
            "rolling_mae_pct": _num(
                e.get("rolling_mae_pct")
                if e.get("rolling_mae_pct") is not None
                else acc.get("rolling_mae_pct")
            ),
            "hold_sec_market": hold_market,
            "minutes_from_open": _minutes_from_open(acc.get("entry_time") or entry_time),
        }
        src = {**e, **acc}
        for fid, key, _fam in ENTRY_FEATURES:
            if fid == "minutes_from_open":
                continue
            row[fid] = (
                _num(src.get(key))
                if not isinstance(src.get(key), str)
                else (
                    1.0
                    if src.get(key) in (True, "True", "true", 1, "1")
                    else 0.0
                    if src.get(key) in (False, "False", "false", 0, "0")
                    else _num(src.get(key))
                )
            )
            if fid == "board_mid_token":
                row[fid] = 1.0 if src.get(key) in (True, "True", "true", 1, "1") else 0.0
        for fid, key, _fam in CAT_FEATURES:
            if fid == "exit_reason":
                row[fid] = str(row["exit_reason"] or "")
            elif fid == "entry_type":
                row[fid] = str(row["entry_type"] or "")
            else:
                row[fid] = str(src.get(key) or "")
        trades.append(row)
    return trades


def load_all_full_period_trades(root: Path = SMALL_PAPER_ROOT) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sessions = discover_replayable_sessions(root)
    trades: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for sess in sessions:
        day = str(sess["day"])
        sess_dir = Path(sess["session_dir"])
        for t in load_trades_for_session(sess_dir, day):
            key = (day, str(t.get("session") or ""), str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
            if key in seen:
                continue
            seen.add(key)
            trades.append(t)
    trades.sort(key=lambda t: (str(t.get("day") or ""), str(t.get("entry_time") or ""), str(t.get("symbol") or "")))
    return trades, sessions


def _percentile(vals: Sequence[float], pct: float) -> Optional[float]:
    if not vals:
        return None
    ordered = sorted(vals)
    if len(ordered) == 1:
        return round(ordered[0], 4)
    k = (len(ordered) - 1) * (pct / 100.0)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return round(ordered[lo], 4)
    w = k - lo
    return round(ordered[lo] * (1.0 - w) + ordered[hi] * w, 4)


def _rise5_thresholds(trades: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    pbv2_rise5 = [
        float(v)
        for t in trades
        if t.get("entry_pool") == "PBV2"
        for v in [_num(t.get("entry_rise_5min_pct"))]
        if v is not None
    ]
    rows: list[dict[str, Any]] = []
    for label in PERCENTILE_LABELS:
        pct = float(label[1:])
        val = _percentile(pbv2_rise5, pct)
        if val is not None:
            rows.append({"threshold_id": label, "threshold_type": "percentile", "threshold_value": val})
    for val in NUMERIC_THRESHOLDS:
        rows.append(
            {
                "threshold_id": f"num_{val}",
                "threshold_type": "numeric",
                "threshold_value": val,
            }
        )
    # dedupe by value (keep first id)
    seen_vals: set[float] = set()
    deduped: list[dict[str, Any]] = []
    for r in rows:
        v = float(r["threshold_value"])
        if v in seen_vals:
            continue
        seen_vals.add(v)
        deduped.append(r)
    return deduped


def _no_low_liq(t: dict[str, Any]) -> bool:
    band = str(t.get("trading_value_band") or "")
    if band == "lt_1e8":
        return False
    tv = _num(t.get("trading_value"))
    if tv is not None and tv < 1e8:
        return False
    return True


def _pbv2_rise5_keep(t: dict[str, Any], threshold: float) -> bool:
    if t.get("entry_pool") == "OR":
        return True
    rise5 = _num(t.get("entry_rise_5min_pct"))
    if rise5 is None:
        return True
    return rise5 <= threshold


def _pbv2_combo_soft_keep(t: dict[str, Any], threshold: float) -> bool:
    if t.get("entry_pool") == "OR":
        return True
    if not _no_low_liq(t):
        return False
    age = _num(t.get("price_age_sec"))
    if age is not None and age > PRICE_AGE_MAX:
        return False
    rise5 = _num(t.get("entry_rise_5min_pct"))
    if rise5 is not None and rise5 > threshold:
        return False
    return True


def _session_bucket(t: dict[str, Any]) -> str:
    mins = _num(t.get("minutes_from_open"))
    if mins is None:
        return "unknown"
    if mins < 150:
        return "AM"
    if mins >= 210:
        return "PM"
    return "lunch"


@dataclass(frozen=True)
class SweepVariant:
    variant_id: str
    family: str  # baseline | rise5_cap | combo_soft
    threshold_id: str
    threshold_value: Optional[float]
    keep: FilterFn


def _build_sweep_variants(thresholds: Sequence[dict[str, Any]]) -> list[SweepVariant]:
    variants = [
        SweepVariant("baseline", "baseline", "none", None, lambda t: True),
    ]
    for th in thresholds:
        tid = str(th["threshold_id"])
        val = float(th["threshold_value"])
        variants.append(
            SweepVariant(
                f"pbv2_only_rise5_cap_{tid}",
                "rise5_cap",
                tid,
                val,
                lambda t, v=val: _pbv2_rise5_keep(t, v),
            )
        )
        variants.append(
            SweepVariant(
                f"pbv2_only_combo_soft_{tid}",
                "combo_soft",
                tid,
                val,
                lambda t, v=val: _pbv2_combo_soft_keep(t, v),
            )
        )
    return variants


def _apply_variant(
    variant: SweepVariant,
    trades: Sequence[dict[str, Any]],
    baseline_metrics: dict[str, Any],
) -> dict[str, Any]:
    kept = [t for t in trades if variant.keep(t)]
    blocked = [t for t in trades if not variant.keep(t)]
    m = _metrics(kept)
    base_or = [t for t in trades if t.get("entry_pool") == "OR"]
    kept_or = [t for t in kept if t.get("entry_pool") == "OR"]
    base_pbv2 = [t for t in trades if t.get("entry_pool") == "PBV2"]
    kept_pbv2 = [t for t in kept if t.get("entry_pool") == "PBV2"]

    or_count_match = len(base_or) == len(kept_or)
    or_pnl_match = round(sum(float(t["pnl_yen_100"]) for t in base_or), 2) == round(
        sum(float(t["pnl_yen_100"]) for t in kept_or), 2
    )

    wrong_block_winners = [t for t in blocked if float(t["pnl_yen_100"]) > 0]
    rescued_losers = [t for t in blocked if float(t["pnl_yen_100"]) < 0]
    big_winners_blocked = [
        t for t in wrong_block_winners if float(t["pnl_yen_100"]) >= BIG_WINNER_YEN
    ]

    delta_pnl = float(m["pnl_yen_100"]) - float(baseline_metrics["pnl_yen_100"])
    base_pf = baseline_metrics.get("profit_factor_raw")
    cur_pf = m.get("profit_factor_raw")
    delta_pf = None
    if isinstance(base_pf, (int, float)) and isinstance(cur_pf, (int, float)):
        if base_pf != float("inf") and cur_pf != float("inf"):
            delta_pf = round(float(cur_pf) - float(base_pf), 4)

    pbv2_base_m = _metrics(base_pbv2)
    pbv2_kept_m = _metrics(kept_pbv2)

    return {
        "variant_id": variant.variant_id,
        "family": variant.family,
        "threshold_id": variant.threshold_id,
        "threshold_value": variant.threshold_value,
        **{k: v for k, v in m.items() if k != "profit_factor_raw"},
        "profit_factor_raw": m.get("profit_factor_raw"),
        "blocked_count": len(blocked),
        "pbv2_blocked_count": len(base_pbv2) - len(kept_pbv2),
        "or_accepted_unchanged": or_count_match and or_pnl_match,
        "or_count_match": or_count_match,
        "or_pnl_match": or_pnl_match,
        "pbv2_baseline_n": pbv2_base_m["entry_count"],
        "pbv2_kept_n": pbv2_kept_m["entry_count"],
        "pbv2_baseline_pnl": pbv2_base_m["pnl_yen_100"],
        "pbv2_kept_pnl": pbv2_kept_m["pnl_yen_100"],
        "pbv2_delta_pnl": round(float(pbv2_kept_m["pnl_yen_100"]) - float(pbv2_base_m["pnl_yen_100"]), 2),
        "pbv2_baseline_pf": pbv2_base_m["profit_factor"],
        "pbv2_kept_pf": pbv2_kept_m["profit_factor"],
        "pbv2_baseline_dd": pbv2_base_m["max_dd_yen_100"],
        "pbv2_kept_dd": pbv2_kept_m["max_dd_yen_100"],
        "wrongly_blocked_winners": len(wrong_block_winners),
        "wrongly_blocked_winners_pnl_yen_100": round(
            sum(float(t["pnl_yen_100"]) for t in wrong_block_winners), 2
        ),
        "rescued_losers": len(rescued_losers),
        "rescued_losers_pnl_yen_100": round(sum(float(t["pnl_yen_100"]) for t in rescued_losers), 2),
        "blocked_big_winners": len(big_winners_blocked),
        "delta_pnl_yen_100": round(delta_pnl, 2),
        "delta_pf": delta_pf,
        "delta_max_dd_yen_100": round(
            float(m["max_dd_yen_100"]) - float(baseline_metrics["max_dd_yen_100"]), 2
        ),
        "entry_reduction_pct": (
            round(1.0 - len(kept) / len(trades), 4) if trades else None
        ),
        "pbv2_entry_reduction_pct": (
            round(1.0 - len(kept_pbv2) / len(base_pbv2), 4) if base_pbv2 else None
        ),
        "daily_pnl": _daily_pnl(kept),
        "_kept_trades": kept,
        "_blocked_trades": blocked,
        "_blocked_big_winners": big_winners_blocked,
    }


def _slice_metrics(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    m = _metrics(list(trades))
    return {k: v for k, v in m.items() if k != "profit_factor_raw"}


def _delta_slice(label: str, base: Sequence[dict[str, Any]], filt: FilterFn) -> dict[str, Any]:
    kept = [t for t in base if filt(t)]
    mb = _slice_metrics(base)
    mk = _slice_metrics(kept)
    return {
        "slice": label,
        "baseline_n": mb["entry_count"],
        "kept_n": mk["entry_count"],
        "baseline_pnl": mb["pnl_yen_100"],
        "kept_pnl": mk["pnl_yen_100"],
        "delta_pnl": round(float(mk["pnl_yen_100"]) - float(mb["pnl_yen_100"]), 2),
        "baseline_pf": mb["profit_factor"],
        "kept_pf": mk["profit_factor"],
        "baseline_dd": mb["max_dd_yen_100"],
        "kept_dd": mk["max_dd_yen_100"],
        "delta_dd": round(float(mk["max_dd_yen_100"]) - float(mb["max_dd_yen_100"]), 2),
    }


def _write_csv(fp: Path, rows: Sequence[dict[str, Any]], fields: Optional[Sequence[str]] = None) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        fp.write_text("", encoding="utf-8")
        return
    if fields is None:
        fields = []
        seen: set[str] = set()
        for r in rows:
            for k in r:
                if k not in seen:
                    seen.add(k)
                    fields = [*fields, k]
    with fp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(fields), extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    gz = fp.with_suffix(fp.suffix + ".gz")
    with fp.open("rb") as src, gzip.open(gz, "wb") as dst:
        dst.writelines(src)


def run(root: Path = SMALL_PAPER_ROOT) -> dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    disk_pct = _disk_usage_pct(NATIVE_ROOT)
    disk_warning = disk_pct > DISK_USAGE_MAX_PCT
    if disk_warning:
        print(
            f"[phase634] WARN disk_usage={disk_pct:.1f}% > {DISK_USAGE_MAX_PCT}% "
            f"(compact outputs only)",
            flush=True,
        )

    trades, sessions = load_all_full_period_trades(root)
    if len(trades) < 50:
        report = {
            "phase": "phase634_pbv2_only_rise5_full_period",
            "verdict": PHASE634_FAIL,
            "error": f"insufficient trades: {len(trades)}",
        }
        (REPORT_DIR / "phase634_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report

    thresholds = _rise5_thresholds(trades)
    variants = _build_sweep_variants(thresholds)
    baseline_metrics = _metrics(trades)

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(_apply_variant, v, trades, baseline_metrics): v for v in variants}
        for fut in as_completed(futs):
            results.append(fut.result())

    order = {v.variant_id: i for i, v in enumerate(variants)}
    results.sort(key=lambda r: order.get(r["variant_id"], 999))

    baseline_row = next(r for r in results if r["variant_id"] == "baseline")
    sweep_rows = [r for r in results if r["variant_id"] != "baseline"]

    # Rank per family
    rise5_ranked = sorted(
        [r for r in sweep_rows if r["family"] == "rise5_cap"],
        key=lambda r: (
            float(r["delta_pnl_yen_100"]),
            float(r["profit_factor"] or 0),
            -abs(float(r["max_dd_yen_100"])),
        ),
        reverse=True,
    )
    combo_ranked = sorted(
        [r for r in sweep_rows if r["family"] == "combo_soft"],
        key=lambda r: (
            float(r["delta_pnl_yen_100"]),
            float(r["profit_factor"] or 0),
            -abs(float(r["max_dd_yen_100"])),
        ),
        reverse=True,
    )

    cmp_fields = [
        "variant_id",
        "family",
        "threshold_id",
        "threshold_value",
        "entry_count",
        "pbv2_accepted",
        "or_accepted",
        "pnl_yen_100",
        "profit_factor",
        "win_rate",
        "max_dd_yen_100",
        "pbv2_blocked_count",
        "or_accepted_unchanged",
        "pbv2_delta_pnl",
        "pbv2_kept_pf",
        "delta_pnl_yen_100",
        "delta_pf",
        "delta_max_dd_yen_100",
        "entry_reduction_pct",
        "pbv2_entry_reduction_pct",
        "blocked_big_winners",
        "rescued_losers",
    ]
    variant_cmp = [{k: r.get(k) for k in cmp_fields} for r in results]
    _write_csv(REPORT_DIR / "phase634_variant_comparison.csv", variant_cmp, cmp_fields)

    th_fields = [
        "family",
        "threshold_id",
        "threshold_type",
        "threshold_value",
        "entry_count",
        "pbv2_accepted",
        "or_accepted",
        "pnl_yen_100",
        "profit_factor",
        "win_rate",
        "max_dd_yen_100",
        "delta_pnl_yen_100",
        "delta_pf",
        "delta_max_dd_yen_100",
        "pbv2_delta_pnl",
        "pbv2_kept_pf",
        "pbv2_entry_reduction_pct",
        "or_accepted_unchanged",
        "blocked_big_winners",
        "rescued_losers",
    ]
    th_lookup = {str(t["threshold_id"]): t for t in thresholds}
    sweep_out = []
    for r in sweep_rows:
        tid = str(r["threshold_id"])
        meta = th_lookup.get(tid, {})
        sweep_out.append(
            {
                **{k: r.get(k) for k in th_fields},
                "threshold_type": meta.get("threshold_type", ""),
            }
        )
    _write_csv(REPORT_DIR / "phase634_threshold_sweep.csv", sweep_out, th_fields + ["threshold_type"])

    # Daily comparison for baseline + top3 each family
    top_ids = {"baseline"}
    top_ids.update(r["variant_id"] for r in rise5_ranked[:3])
    top_ids.update(r["variant_id"] for r in combo_ranked[:3])
    daily_rows = []
    for r in results:
        if r["variant_id"] not in top_ids:
            continue
        for day, pnl in (r.get("daily_pnl") or {}).items():
            daily_rows.append(
                {
                    "variant_id": r["variant_id"],
                    "family": r["family"],
                    "day": day,
                    "pnl_yen_100": pnl,
                    "entry_count": sum(
                        1 for t in r.get("_kept_trades") or [] if t.get("day") == day
                    ),
                }
            )
    _write_csv(
        REPORT_DIR / "phase634_daily_comparison.csv",
        daily_rows,
        ["variant_id", "family", "day", "pnl_yen_100", "entry_count"],
    )

    # Pre/post 625 for baseline + best each family
    best_rise5 = rise5_ranked[0] if rise5_ranked else None
    best_combo = combo_ranked[0] if combo_ranked else None
    key_variants: list[tuple[str, FilterFn]] = [("baseline", lambda t: True)]
    if best_rise5:
        th = float(best_rise5["threshold_value"])
        key_variants.append(
            (str(best_rise5["variant_id"]), lambda t, v=th: _pbv2_rise5_keep(t, v))
        )
    if best_combo:
        th = float(best_combo["threshold_value"])
        key_variants.append(
            (str(best_combo["variant_id"]), lambda t, v=th: _pbv2_combo_soft_keep(t, v))
        )

    pre625 = [t for t in trades if str(t.get("day") or "") < PRE625_CUTOFF]
    post625 = [t for t in trades if str(t.get("day") or "") >= PRE625_CUTOFF]
    prepost_rows = []
    for vid, fn in key_variants:
        for period, subset in (("pre625", pre625), ("post625", post625)):
            prepost_rows.append({**_delta_slice(f"{period}_{vid}", subset, fn), "variant_id": vid, "period": period})
    _write_csv(
        REPORT_DIR / "phase634_pre625_vs_post625.csv",
        prepost_rows,
        ["variant_id", "period", "slice", "baseline_n", "kept_n", "baseline_pnl", "kept_pnl", "delta_pnl", "baseline_pf", "kept_pf", "baseline_dd", "kept_dd", "delta_dd"],
    )

    # Pool breakdown (baseline + best each)
    pool_rows = []
    for vid, fn in key_variants:
        for pool in ("PBV2", "OR"):
            sub = [t for t in trades if t.get("entry_pool") == pool]
            row = _delta_slice(pool, sub, fn)
            row["variant_id"] = vid
            row["pool"] = pool
            pool_rows.append(row)
    _write_csv(
        REPORT_DIR / "phase634_pool_breakdown.csv",
        pool_rows,
        ["variant_id", "pool", "slice", "baseline_n", "kept_n", "baseline_pnl", "kept_pnl", "delta_pnl", "baseline_pf", "kept_pf"],
    )

    # AM/PM in pool breakdown extension via session bucket on best rise5
    session_rows = []
    if best_rise5:
        th = float(best_rise5["threshold_value"])
        fn = lambda t, v=th: _pbv2_rise5_keep(t, v)
        for sess in ("AM", "PM", "lunch", "unknown"):
            sub = [t for t in trades if _session_bucket(t) == sess]
            if not sub:
                continue
            row = _delta_slice(sess, sub, fn)
            row["variant_id"] = best_rise5["variant_id"]
            session_rows.append(row)

    # Symbol concentration for best rise5 and best combo
    symbol_rows = []
    leave_one_out = []
    for label, best in (("rise5_cap", best_rise5), ("combo_soft", best_combo)):
        if not best:
            continue
        fn = (
            (lambda t, v=float(best["threshold_value"]): _pbv2_rise5_keep(t, v))
            if label == "rise5_cap"
            else (lambda t, v=float(best["threshold_value"]): _pbv2_combo_soft_keep(t, v))
        )
        by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in trades:
            by_sym[str(t.get("symbol") or "")].append(t)
        sym_deltas = []
        for sym, seq in by_sym.items():
            d = _delta_slice(sym, seq, fn)
            d["family"] = label
            d["variant_id"] = best["variant_id"]
            sym_deltas.append(d)
        sym_deltas.sort(key=lambda r: float(r["delta_pnl"]), reverse=True)
        symbol_rows.extend(sym_deltas)
        total_delta = float(best["delta_pnl_yen_100"])
        for r in sym_deltas[:10]:
            if float(r["delta_pnl"]) <= 0:
                continue
            sym = r["slice"]
            base_wo = [t for t in trades if t.get("symbol") != sym]
            kept_wo = [t for t in base_wo if fn(t)]
            d = float(_slice_metrics(kept_wo)["pnl_yen_100"]) - float(_slice_metrics(base_wo)["pnl_yen_100"])
            leave_one_out.append(
                {
                    "family": label,
                    "variant_id": best["variant_id"],
                    "excluded_symbol": sym,
                    "symbol_delta_pnl": r["delta_pnl"],
                    "delta_pnl_without_symbol": round(d, 2),
                    "still_positive": d > 0,
                    "share_of_total_delta": (
                        round(abs(float(r["delta_pnl"])) / abs(total_delta), 4)
                        if abs(total_delta) > 1e-6
                        else None
                    ),
                }
            )

    _write_csv(
        REPORT_DIR / "phase634_symbol_concentration.csv",
        symbol_rows,
        ["family", "variant_id", "slice", "baseline_n", "kept_n", "baseline_pnl", "kept_pnl", "delta_pnl", "baseline_pf", "kept_pf"],
    )

    # Blocked big winners (best rise5 + best combo, capped)
    block_rows = []
    for best in (best_rise5, best_combo):
        if not best:
            continue
        blocked = best.get("_blocked_big_winners") or []
        blocked.sort(key=lambda t: float(t["pnl_yen_100"]), reverse=True)
        for t in blocked[:BLOCKED_ANALYSIS_MAX_ROWS // 2]:
            block_rows.append(
                {
                    "variant_id": best["variant_id"],
                    "family": best["family"],
                    "day": t.get("day"),
                    "symbol": t.get("symbol"),
                    "entry_time": t.get("entry_time"),
                    "entry_pool": t.get("entry_pool"),
                    "pnl_yen_100": t.get("pnl_yen_100"),
                    "entry_rise_5min_pct": t.get("entry_rise_5min_pct"),
                    "price_age_sec": t.get("price_age_sec"),
                    "trading_value_band": t.get("trading_value_band"),
                }
            )
    _write_csv(
        REPORT_DIR / "phase634_blocked_big_winners.csv",
        block_rows,
        ["variant_id", "family", "day", "symbol", "entry_time", "entry_pool", "pnl_yen_100", "entry_rise_5min_pct", "price_age_sec", "trading_value_band"],
    )

    # Adoption criteria on best rise5 (primary candidate)
    def _adoption_for(best: Optional[dict[str, Any]], family: str) -> dict[str, Any]:
        if not best:
            return {"family": family, "adopt": False, "reason": "no_candidate"}
        fn = (
            (lambda t, v=float(best["threshold_value"]): _pbv2_rise5_keep(t, v))
            if family == "rise5_cap"
            else (lambda t, v=float(best["threshold_value"]): _pbv2_combo_soft_keep(t, v))
        )
        kept = [t for t in trades if fn(t)]
        pre_kept = [t for t in pre625 if fn(t)]
        post_kept = [t for t in post625 if fn(t)]
        pre_d = float(_slice_metrics(pre_kept)["pnl_yen_100"]) - float(_slice_metrics(pre625)["pnl_yen_100"])
        post_d = float(_slice_metrics(post_kept)["pnl_yen_100"]) - float(_slice_metrics(post625)["pnl_yen_100"])

        sym_deltas = []
        by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for t in trades:
            by_sym[str(t.get("symbol") or "")].append(t)
        for sym, seq in by_sym.items():
            sym_deltas.append(float(_delta_slice(sym, seq, fn)["delta_pnl"]))
        total_delta = float(best["delta_pnl_yen_100"])
        max_share = 0.0
        if abs(total_delta) > 1e-6:
            max_share = max(abs(d) / abs(total_delta) for d in sym_deltas)

        stable_count = sum(
            1
            for r in sweep_rows
            if r["family"] == family
            and float(r["delta_pnl_yen_100"]) > 0
            and bool(r.get("or_accepted_unchanged"))
            and float(r.get("pbv2_delta_pnl") or 0) > 0
        )

        criteria = {
            "full_period_pnl_improved": float(best["delta_pnl_yen_100"]) > 0,
            "full_period_pf_improved": (best["profit_factor"] or 0) >= (baseline_metrics["profit_factor"] or 0),
            "full_period_dd_improved": float(best["delta_max_dd_yen_100"]) >= 0,
            "pbv2_pnl_improved": float(best.get("pbv2_delta_pnl") or 0) > 0,
            "pbv2_pf_improved": (best.get("pbv2_kept_pf") or 0) >= (best.get("pbv2_baseline_pf") or 0),
            "or_unchanged": bool(best.get("or_accepted_unchanged")),
            "pre625_improved": pre_d > 0,
            "post625_improved": post_d > 0,
            "entry_reduction_ok": float(best.get("entry_reduction_pct") or 0) <= ENTRY_REDUCTION_MAX,
            "no_single_symbol_dependency": max_share <= TOP_SYMBOL_SHARE_MAX,
            "multi_threshold_stable": stable_count >= 3,
        }
        return {
            "family": family,
            "variant_id": best["variant_id"],
            "threshold_id": best["threshold_id"],
            "threshold_value": best["threshold_value"],
            "criteria": criteria,
            "adopt": all(criteria.values()),
            "stable_positive_thresholds": stable_count,
            "max_symbol_delta_share": round(max_share, 4),
            "pre625_delta_pnl": round(pre_d, 2),
            "post625_delta_pnl": round(post_d, 2),
        }

    adoption_rise5 = _adoption_for(best_rise5, "rise5_cap")
    adoption_combo = _adoption_for(best_combo, "combo_soft")

    # Stability: count thresholds with positive delta in each family
    rise5_stable = [
        r["threshold_id"]
        for r in rise5_ranked
        if float(r["delta_pnl_yen_100"]) > 0 and bool(r.get("or_accepted_unchanged"))
    ]
    combo_stable = [
        r["threshold_id"]
        for r in combo_ranked
        if float(r["delta_pnl_yen_100"]) > 0 and bool(r.get("or_accepted_unchanged"))
    ]

    days_included = sorted({str(t.get("day") or "") for t in trades})
    pre625_days = [d for d in days_included if d < PRE625_CUTOFF]
    post625_days = [d for d in days_included if d >= PRE625_CUTOFF]

    public_results = [{k: v for k, v in r.items() if not k.startswith("_")} for r in results]

    report = {
        "phase": "phase634_pbv2_only_rise5_full_period",
        "verdict": PHASE634_VERDICT,
        "data_root": str(root),
        "session_count": len(sessions),
        "sessions": sessions,
        "trade_count_baseline": len(trades),
        "days_included": days_included,
        "pre625_day_count": len(pre625_days),
        "post625_day_count": len(post625_days),
        "includes_pre625": len(pre625_days) > 0,
        "required_days_present": {
            "2026-06-25": "2026-06-25" in days_included,
            "2026-06-29": "2026-06-29" in days_included,
            "2026-06-30": "2026-06-30" in days_included,
            "2026-07-01": "2026-07-01" in days_included,
        },
        "disk_usage_pct": round(disk_pct, 2),
        "disk_warning": disk_warning,
        "max_workers": MAX_WORKERS,
        "rise5_percentile_reference": {
            lbl: next((t["threshold_value"] for t in thresholds if t["threshold_id"] == lbl), None)
            for lbl in PERCENTILE_LABELS
        },
        "baseline": {k: baseline_metrics[k] for k in baseline_metrics if k != "profit_factor_raw"},
        "best_rise5_cap": {k: best_rise5.get(k) for k in cmp_fields if best_rise5} if best_rise5 else None,
        "best_combo_soft": {k: best_combo.get(k) for k in cmp_fields if best_combo} if best_combo else None,
        "adoption": {"rise5_cap": adoption_rise5, "combo_soft": adoption_combo},
        "threshold_stability": {"rise5_cap_positive": rise5_stable, "combo_soft_positive": combo_stable},
        "leave_one_symbol_out": leave_one_out,
        "am_pm_best_rise5": session_rows,
        "variant_results": public_results,
        "mandatory_answers": {
            "1_includes_pre625": len(pre625_days) > 0,
            "2_session_count": len(sessions),
            "2_trade_count": len(trades),
            "3_pbv2_rise5_improves_baseline": bool(adoption_rise5.get("criteria", {}).get("full_period_pnl_improved")),
            "4_pbv2_combo_soft_improves": bool(adoption_combo.get("criteria", {}).get("full_period_pnl_improved")),
            "5_or_unchanged_rise5": bool(best_rise5.get("or_accepted_unchanged")) if best_rise5 else True,
            "5_or_unchanged_combo": bool(best_combo.get("or_accepted_unchanged")) if best_combo else True,
            "6_best_threshold_rise5": (
                {"id": best_rise5["threshold_id"], "value": best_rise5["threshold_value"]} if best_rise5 else None
            ),
            "6_best_threshold_combo": (
                {"id": best_combo["threshold_id"], "value": best_combo["threshold_value"]} if best_combo else None
            ),
            "7_threshold_stable_rise5": rise5_stable,
            "7_threshold_stable_combo": combo_stable,
            "8_pre625_improved_rise5": bool(adoption_rise5.get("criteria", {}).get("pre625_improved")),
            "8_pre625_improved_combo": bool(adoption_combo.get("criteria", {}).get("pre625_improved")),
            "9_post625_improved_rise5": bool(adoption_rise5.get("criteria", {}).get("post625_improved")),
            "9_post625_improved_combo": bool(adoption_combo.get("criteria", {}).get("post625_improved")),
            "10_symbol_dependency_rise5": adoption_rise5.get("max_symbol_delta_share"),
            "10_symbol_dependency_combo": adoption_combo.get("max_symbol_delta_share"),
            "11_implement_rise5": adoption_rise5.get("adopt", False),
            "11_implement_combo": adoption_combo.get("adopt", False),
            "12_implementation_location": (
                "Post-accept PBv2-only overlay in pilot_runner Stage6 (after PBv2 accept, before position open); "
                "OR path bypasses filter. entry_rise_5min_pct cap from accepted event features."
            ),
        },
        "recommendation": _recommendation(adoption_rise5, adoption_combo),
    }
    (REPORT_DIR / "phase634_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def _recommendation(adoption_rise5: dict[str, Any], adoption_combo: dict[str, Any]) -> str:
    if adoption_rise5.get("adopt"):
        return (
            f"ADOPT rise5_cap variant {adoption_rise5.get('variant_id')} "
            f"(threshold={adoption_rise5.get('threshold_value')}) as PBv2 post-accept filter."
        )
    if adoption_rise5.get("criteria", {}).get("full_period_pnl_improved"):
        return (
            f"HOLD — rise5_cap improves PnL ({adoption_rise5.get('variant_id')}) but adoption criteria not all met; "
            "prefer rise5-only over combo_soft."
        )
    return "REJECT — PBv2-only rise5 cap does not improve full-period baseline with OR preserved."


def main() -> int:
    report = run()
    print(json.dumps({"verdict": report.get("verdict"), "trades": report.get("trade_count_baseline")}, ensure_ascii=False))
    return 0 if report.get("verdict") == PHASE634_VERDICT else 1


if __name__ == "__main__":
    raise SystemExit(main())
