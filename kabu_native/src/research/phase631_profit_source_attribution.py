"""
Phase631: Profit Source Attribution (winner analysis, research only).

Quantifies which entry/outcome features associate with profit across the
Phase630 runtime-parity replay fixtures (2026-06-25 .. 2026-07-01).

No ENTRY/EXIT/PBv2/OR/Freshness logic changes.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

HERE = Path(__file__).resolve()
NATIVE_ROOT = HERE.parents[2]
REPO_ROOT = NATIVE_ROOT.parent

DAYS = ("2026-06-25", "2026-06-29", "2026-06-30", "2026-07-01")
REPLAY_ROOT = NATIVE_ROOT / "results" / "small_paper" / "_phase630" / "current"
REPORT_DIR = NATIVE_ROOT / "results" / "reports" / "phase631_profit_source_attribution"
PHASE631_VERDICT = "phase631_profit_source_done"
PHASE631_FAIL = "phase631_profit_source_failed"

# Entry-time features (predictive)
ENTRY_FEATURES: tuple[tuple[str, str, str], ...] = (
    # (feature_id, event_key, family)
    ("momentum_score", "entry_momentum_score", "Momentum"),
    ("momentum_continuation", "entry_momentum_continuation_score", "Momentum"),
    ("momentum_continuation_raw", "momentum_continuation_score", "Momentum"),
    ("continuation_quality", "continuation_quality_score", "Momentum"),
    ("board_imbalance", "entry_order_book_imbalance", "Board"),
    ("board_mid_token", "entry_board_mid_token_active", "Board"),
    ("price_age_sec", "price_age_sec", "Freshness"),
    ("board_age_sec", "board_age_sec", "Freshness"),
    ("update_count_before_entry", "update_count_before_entry", "Board更新頻度"),
    ("trading_value", "trading_value", "Trading Value"),
    ("turnover_proxy", "turnover_proxy", "Volume"),
    ("spread_bps", "spread_bps", "Spread"),
    ("minutes_from_open", "minutes_from_open", "Entry時刻"),
    ("entry_expectancy_score_v2", "entry_expectancy_score_v2", "Score"),
    ("entry_expectancy_score", "entry_expectancy_score", "Score"),
    ("entry_vwap_dev_pct", "entry_vwap_dev_pct", "Momentum"),
    ("day_high_distance_pct", "day_high_distance_pct", "Momentum"),
    ("entry_rise_5min_pct", "entry_rise_5min_pct", "Momentum"),
    ("entry_rise_10min_pct", "entry_rise_10min_pct", "Momentum"),
    ("entry_rolling_mfe_pct", "entry_rolling_mfe_pct", "MFE"),
)

# Outcome features (descriptive of winners/losers)
OUTCOME_FEATURES: tuple[tuple[str, str, str], ...] = (
    ("peak_mfe_pct", "peak_mfe_pct", "MFE"),
    ("rolling_mfe_pct", "rolling_mfe_pct", "MFE"),
    ("rolling_mae_pct", "rolling_mae_pct", "MAE"),
    ("hold_sec_market", "hold_sec_market", "Holding時間"),
    ("pnl_pct", "pnl_pct", "PnL"),
)

CAT_FEATURES: tuple[tuple[str, str, str], ...] = (
    ("price_freshness_source", "price_freshness_source", "Freshness"),
    ("exit_reason", "exit_reason", "Exit理由"),
    ("entry_type", "entry_type", "EntryType"),
    ("trading_value_band", "trading_value_band", "Trading Value"),
)


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return x


def _parse_iso(ts: Any) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _minutes_from_open(entry_time: Any) -> Optional[float]:
    dt = _parse_iso(entry_time)
    if dt is None:
        return None
    open_dt = dt.replace(hour=9, minute=0, second=0, microsecond=0)
    return (dt - open_dt).total_seconds() / 60.0


def _pnl_yen_100(entry_price: Any, exit_price: Any, pnl_pct: Any = None) -> Optional[float]:
    ep = _num(entry_price)
    xp = _num(exit_price)
    if ep is not None and xp is not None and ep > 0:
        return round((xp - ep) * 100.0, 2)
    pct = _num(pnl_pct)
    if ep is not None and pct is not None and ep > 0:
        return round(ep * (pct / 100.0) * 100.0, 2)
    return None


def _entry_pool(entry_type: Any) -> str:
    et = str(entry_type or "PBV2").strip().upper()
    if "OR" in et:
        return "OR"
    return "PBV2"


def _iter_jsonl(fp: Path) -> Iterable[dict[str, Any]]:
    with fp.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_trades_for_day(day_dir: Path, day: str) -> list[dict[str, Any]]:
    events_fp = day_dir / "small_paper_events.jsonl"
    if not events_fp.is_file():
        return []

    accepted_by_key: dict[tuple[Any, Any], dict[str, Any]] = {}
    for e in _iter_jsonl(events_fp):
        if e.get("event_type") != "accepted":
            continue
        key = (e.get("symbol"), e.get("entry_time") or e.get("message_index"))
        accepted_by_key[key] = e

    trades: list[dict[str, Any]] = []
    for e in _iter_jsonl(events_fp):
        if e.get("event_type") != "observer_exit":
            continue
        sym = e.get("symbol")
        entry_time = e.get("entry_time")
        acc = accepted_by_key.get((sym, entry_time))
        if acc is None:
            # fallback: latest accepted for symbol before this exit in file order is hard;
            # use exit-carried entry fields.
            acc = {}

        entry_price = e.get("entry_price") or acc.get("entry_price") or acc.get("current_price")
        exit_price = e.get("exit_price") or e.get("current_price")
        pnl_pct = _num(e.get("pnl_pct"))
        pnl_yen = _pnl_yen_100(entry_price, exit_price, pnl_pct)
        if pnl_yen is None:
            continue

        # Market hold: prefer accepted virtual window; else None (wall-clock hold_sec is invalid).
        hold_market = None
        et = _parse_iso(acc.get("entry_time") or entry_time)
        xt = _parse_iso(acc.get("exit_time"))
        if et is not None and xt is not None:
            hold_market = max(0.0, (xt - et).total_seconds())

        row: dict[str, Any] = {
            "day": day,
            "symbol": sym,
            "entry_time": entry_time or acc.get("entry_time"),
            "entry_type": acc.get("entry_type") or e.get("entry_type") or "PBV2",
            "entry_pool": _entry_pool(acc.get("entry_type") or e.get("entry_type")),
            "exit_reason": e.get("exit_reason") or e.get("structural_exit_reason") or "",
            "pnl_yen_100": pnl_yen,
            "pnl_pct": pnl_pct if pnl_pct is not None else 0.0,
            "peak_mfe_pct": _num(e.get("peak_mfe_pct")),
            "rolling_mfe_pct": _num(e.get("rolling_mfe_pct") if e.get("rolling_mfe_pct") is not None else acc.get("rolling_mfe_pct")),
            "rolling_mae_pct": _num(e.get("rolling_mae_pct") if e.get("rolling_mae_pct") is not None else acc.get("rolling_mae_pct")),
            "hold_sec_market": hold_market,
            "minutes_from_open": _minutes_from_open(acc.get("entry_time") or entry_time),
        }
        src = {**e, **acc}
        for fid, key, _fam in ENTRY_FEATURES:
            if fid == "minutes_from_open":
                continue
            row[fid] = _num(src.get(key)) if not isinstance(src.get(key), str) else (
                1.0 if src.get(key) in (True, "True", "true", 1, "1") else
                0.0 if src.get(key) in (False, "False", "false", 0, "0") else _num(src.get(key))
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


def load_all_trades(replay_root: Path = REPLAY_ROOT) -> list[dict[str, Any]]:
    trades: list[dict[str, Any]] = []
    for day in DAYS:
        day_key = day.replace("-", "")
        day_dir = replay_root / day_key
        trades.extend(load_trades_for_day(day_dir, day))
    return trades


def _mean(xs: Sequence[float]) -> Optional[float]:
    return statistics.fmean(xs) if xs else None


def _std(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return 0.0
    return statistics.pstdev(xs)


def _cohens_d(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    if not a or not b:
        return None
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    sa, sb = _std(a), _std(b)
    # pooled std
    na, nb = len(a), len(b)
    pooled = math.sqrt(((na * sa * sa) + (nb * sb * sb)) / max(1, na + nb))
    if pooled <= 1e-12:
        return 0.0
    return (ma - mb) / pooled


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    denx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    deny = math.sqrt(sum((y - my) ** 2 for y in ys))
    if denx <= 1e-12 or deny <= 1e-12:
        return 0.0
    return num / (denx * deny)


def _quantile_cut(trades: Sequence[Mapping[str, Any]], lo: float, hi: float) -> list[dict[str, Any]]:
    """Return trades whose pnl rank percentile is in [lo, hi) (0..1)."""
    if not trades:
        return []
    ordered = sorted(trades, key=lambda t: float(t["pnl_yen_100"]))
    n = len(ordered)
    i0 = int(n * lo)
    i1 = int(n * hi)
    if i1 <= i0:
        i1 = min(n, i0 + 1)
    return list(ordered[i0:i1])


def _feature_vals(trades: Sequence[Mapping[str, Any]], fid: str) -> list[float]:
    out: list[float] = []
    for t in trades:
        v = _num(t.get(fid))
        if v is not None:
            out.append(v)
    return out


def analyze_numeric_features(
    trades: Sequence[Mapping[str, Any]],
    *,
    top: Sequence[Mapping[str, Any]],
    bottom: Sequence[Mapping[str, Any]],
    feature_defs: Sequence[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fid, _key, family in feature_defs:
        all_pairs = [
            (float(t["pnl_yen_100"]), v)
            for t in trades
            if (v := _num(t.get(fid))) is not None
        ]
        if len(all_pairs) < 10:
            continue
        pnls = [p for p, _ in all_pairs]
        vals = [v for _, v in all_pairs]
        top_v = _feature_vals(top, fid)
        bot_v = _feature_vals(bottom, fid)
        mean_all = _mean(vals)
        mean_top = _mean(top_v)
        mean_bot = _mean(bot_v)
        d = _cohens_d(top_v, bot_v)
        corr = _pearson(vals, pnls)
        # contribution: positive => higher feature associates with higher profit
        contrib = 0.0
        if d is not None:
            contrib += float(d)
        if corr is not None:
            contrib += float(corr)
        # mean gap normalized
        gap = None
        if mean_top is not None and mean_bot is not None:
            gap = mean_top - mean_bot
            s = _std(vals) or 1.0
            contrib += gap / s
        rows.append(
            {
                "feature": fid,
                "family": family,
                "n": len(vals),
                "mean_all": round(mean_all, 6) if mean_all is not None else None,
                "mean_top30": round(mean_top, 6) if mean_top is not None else None,
                "mean_bottom30": round(mean_bot, 6) if mean_bot is not None else None,
                "mean_gap_top_minus_bottom": round(gap, 6) if gap is not None else None,
                "cohens_d_top_vs_bottom": round(d, 6) if d is not None else None,
                "corr_with_pnl_yen_100": round(corr, 6) if corr is not None else None,
                "contribution_score": round(contrib, 6),
            }
        )
    rows.sort(key=lambda r: float(r["contribution_score"]), reverse=True)
    return rows


def analyze_categorical(
    trades: Sequence[Mapping[str, Any]],
    *,
    top: Sequence[Mapping[str, Any]],
    bottom: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fid, _key, family in CAT_FEATURES:
        top_c = Counter(str(t.get(fid) or "") for t in top)
        bot_c = Counter(str(t.get(fid) or "") for t in bottom)
        all_c = Counter(str(t.get(fid) or "") for t in trades)
        n_top, n_bot, n_all = max(1, len(top)), max(1, len(bottom)), max(1, len(trades))
        keys = set(top_c) | set(bot_c) | set(all_c)
        for k in keys:
            if not k:
                continue
            pt = top_c[k] / n_top
            pb = bot_c[k] / n_bot
            pa = all_c[k] / n_all
            # lift in winners vs losers
            lift = (pt + 1e-9) / (pb + 1e-9)
            # avg pnl for this category
            pnls = [float(t["pnl_yen_100"]) for t in trades if str(t.get(fid) or "") == k]
            avg_pnl = _mean(pnls)
            rows.append(
                {
                    "feature": f"{fid}={k}",
                    "family": family,
                    "n": all_c[k],
                    "share_top30": round(pt, 6),
                    "share_bottom30": round(pb, 6),
                    "share_all": round(pa, 6),
                    "winner_loser_lift": round(lift, 6),
                    "avg_pnl_yen_100": round(avg_pnl, 4) if avg_pnl is not None else None,
                    "contribution_score": round(math.log(lift) + (avg_pnl or 0) / 1000.0, 6),
                }
            )
    rows.sort(key=lambda r: float(r["contribution_score"]), reverse=True)
    return rows


def winner_loser_distribution(
    trades: Sequence[Mapping[str, Any]],
    top: Sequence[Mapping[str, Any]],
    bottom: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for fid, _key, family in list(ENTRY_FEATURES) + list(OUTCOME_FEATURES):
        for cohort, label in ((top, "top30"), (bottom, "bottom30"), (trades, "all")):
            vals = _feature_vals(cohort, fid)
            if not vals:
                continue
            rows.append(
                {
                    "feature": fid,
                    "family": family,
                    "cohort": label,
                    "n": len(vals),
                    "mean": round(statistics.fmean(vals), 6),
                    "p25": round(sorted(vals)[max(0, int(len(vals) * 0.25) - 1)], 6),
                    "p50": round(statistics.median(vals), 6),
                    "p75": round(sorted(vals)[min(len(vals) - 1, int(len(vals) * 0.75))], 6),
                }
            )
    # exit reasons
    for cohort, label in ((top, "top30"), (bottom, "bottom30"), (trades, "all")):
        c = Counter(str(t.get("exit_reason") or "") for t in cohort)
        n = max(1, len(cohort))
        for reason, cnt in c.most_common(20):
            rows.append(
                {
                    "feature": f"exit_reason={reason}",
                    "family": "Exit理由",
                    "cohort": label,
                    "n": cnt,
                    "mean": round(cnt / n, 6),
                    "p25": None,
                    "p50": None,
                    "p75": None,
                }
            )
    return rows


def _improvement_ranking(feature_rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Expected improvement if we bias entries toward winner-like feature values."""
    out: list[dict[str, Any]] = []
    for r in feature_rows:
        score = float(r.get("contribution_score") or 0)
        gap = r.get("mean_gap_top_minus_bottom")
        corr = r.get("corr_with_pnl_yen_100")
        # expected yen lift proxy: contribution * |gap| scale
        expect = score * (abs(float(gap)) if gap is not None else abs(float(corr or 0)))
        out.append(
            {
                "feature": r["feature"],
                "family": r["family"],
                "contribution_score": r["contribution_score"],
                "expected_improvement_proxy": round(expect, 6),
                "direction": "increase_feature" if score > 0 else "decrease_feature",
                "mean_top30": r.get("mean_top30"),
                "mean_bottom30": r.get("mean_bottom30"),
            }
        )
    out.sort(key=lambda x: abs(float(x["expected_improvement_proxy"])), reverse=True)
    return out


def _write_csv(fp: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    fp.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        fp.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                fields.append(k)
    with fp.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def run(replay_root: Path = REPLAY_ROOT) -> dict[str, Any]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    trades = load_all_trades(replay_root)
    if len(trades) < 50:
        report = {
            "phase": "phase631_profit_source_attribution",
            "verdict": PHASE631_FAIL,
            "error": f"insufficient trades: {len(trades)}",
            "replay_root": str(replay_root),
        }
        (REPORT_DIR / "phase631_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return report

    top = _quantile_cut(trades, 0.70, 1.01)  # top 30% by pnl
    # top 30% = highest pnl => percentile 70-100
    bottom = _quantile_cut(trades, 0.0, 0.30)

    pbv2 = [t for t in trades if t["entry_pool"] == "PBV2"]
    or_tr = [t for t in trades if t["entry_pool"] == "OR"]
    top_pbv2 = _quantile_cut(pbv2, 0.70, 1.01)
    bot_pbv2 = _quantile_cut(pbv2, 0.0, 0.30)
    top_or = _quantile_cut(or_tr, 0.70, 1.01) if len(or_tr) >= 20 else or_tr
    bot_or = _quantile_cut(or_tr, 0.0, 0.30) if len(or_tr) >= 20 else []

    all_feat_defs = list(ENTRY_FEATURES) + list(OUTCOME_FEATURES)
    importance = analyze_numeric_features(trades, top=top, bottom=bottom, feature_defs=all_feat_defs)
    cat_rows = analyze_categorical(trades, top=top, bottom=bottom)

    # Combined ranking (numeric + categorical)
    ranking: list[dict[str, Any]] = []
    for r in importance:
        ranking.append(
            {
                "rank": 0,
                "feature": r["feature"],
                "family": r["family"],
                "kind": "numeric",
                "contribution_score": r["contribution_score"],
                "corr_with_pnl_yen_100": r.get("corr_with_pnl_yen_100"),
                "cohens_d_top_vs_bottom": r.get("cohens_d_top_vs_bottom"),
                "mean_top30": r.get("mean_top30"),
                "mean_bottom30": r.get("mean_bottom30"),
            }
        )
    for r in cat_rows:
        ranking.append(
            {
                "rank": 0,
                "feature": r["feature"],
                "family": r["family"],
                "kind": "categorical",
                "contribution_score": r["contribution_score"],
                "corr_with_pnl_yen_100": None,
                "cohens_d_top_vs_bottom": None,
                "mean_top30": r.get("share_top30"),
                "mean_bottom30": r.get("share_bottom30"),
            }
        )
    ranking.sort(key=lambda r: float(r["contribution_score"]), reverse=True)
    for i, r in enumerate(ranking, start=1):
        r["rank"] = i

    help_top20 = ranking[:20]
    hurt_sorted = sorted(ranking, key=lambda r: float(r["contribution_score"]))
    hurt_top20 = [r for r in hurt_sorted if float(r["contribution_score"]) < 0][:20]
    for i, r in enumerate(hurt_top20, start=1):
        r["rank"] = i

    pbv2_imp = analyze_numeric_features(
        pbv2, top=top_pbv2, bottom=bot_pbv2, feature_defs=ENTRY_FEATURES
    )
    or_imp = (
        analyze_numeric_features(or_tr, top=top_or, bottom=bot_or, feature_defs=ENTRY_FEATURES)
        if len(or_tr) >= 10
        else []
    )

    # PBv2 strongest = highest contribution among entry features on PBv2 pool
    pbv2_strong = pbv2_imp[:10] if pbv2_imp else []
    # OR profitable features
    or_strong = [r for r in or_imp if float(r["contribution_score"]) > 0][:10]

    # Recommend add: entry features strong for profit but not already primary score drivers
    # Primary score drivers already in PBv2: entry_expectancy_score_v2, momentum, board mid
    already_core = {
        "entry_expectancy_score_v2",
        "entry_expectancy_score",
        "momentum_score",
        "momentum_continuation",
        "momentum_continuation_raw",
        "board_mid_token",
        "continuation_quality",
    }
    add_candidates = [
        r
        for r in pbv2_imp
        if float(r["contribution_score"]) > 0.02 and r["feature"] not in already_core
    ][:10]
    # Weaken/tighten: entry features that hurt profit when high among PBv2 accepts.
    # Negative score => prefer lower values (e.g. price_age_sec: tighten freshness;
    # momentum_score: avoid chase / weaken high-momentum preference).
    remove_candidates = [
        r for r in pbv2_imp if float(r["contribution_score"]) < -0.02
    ][:10]

    # Actionable improvement ranking uses entry features only (not outcome MFE/MAE/pnl).
    entry_importance = [r for r in importance if r["feature"] in {f[0] for f in ENTRY_FEATURES}]
    improvement = _improvement_ranking(entry_importance)

    dist = winner_loser_distribution(trades, top, bottom)

    # totals
    total_pnl = sum(float(t["pnl_yen_100"]) for t in trades)
    top_pnl = sum(float(t["pnl_yen_100"]) for t in top)
    bot_pnl = sum(float(t["pnl_yen_100"]) for t in bottom)

    _write_csv(REPORT_DIR / "profit_source_ranking.csv", ranking)
    _write_csv(REPORT_DIR / "profit_feature_importance.csv", importance)
    _write_csv(REPORT_DIR / "winner_vs_loser_distribution.csv", dist)

    # also write pool-specific importance for transparency
    _write_csv(REPORT_DIR / "pbv2_feature_importance.csv", pbv2_imp)
    _write_csv(REPORT_DIR / "or_feature_importance.csv", or_imp)
    _write_csv(REPORT_DIR / "profit_improvement_ranking.csv", improvement)

    answers = {
        "1_profit_contributing_features_top20": [
            {"feature": r["feature"], "family": r["family"], "score": r["contribution_score"]}
            for r in help_top20
        ],
        "2_profit_reducing_features_top20": [
            {"feature": r["feature"], "family": r["family"], "score": r["contribution_score"]}
            for r in hurt_top20
        ],
        "3_strongest_pbv2_features": [
            {"feature": r["feature"], "family": r["family"], "score": r["contribution_score"]}
            for r in pbv2_strong
        ],
        "4_or_profit_features": [
            {"feature": r["feature"], "family": r["family"], "score": r["contribution_score"]}
            for r in or_strong
        ],
        "5_add_to_pbv2": [
            {
                "feature": r["feature"],
                "family": r["family"],
                "score": r["contribution_score"],
                "rationale": "positive contribution among PBv2 accepts; not a primary score driver",
            }
            for r in add_candidates
        ],
        "6_remove_or_weaken_in_pbv2": [
            {
                "feature": r["feature"],
                "family": r["family"],
                "score": r["contribution_score"],
                "action": (
                    "tighten_max"
                    if r["feature"] in ("price_age_sec", "board_age_sec", "spread_bps")
                    else "weaken_high_preference"
                ),
                "rationale": (
                    "higher values associate with worse pnl among PBv2 accepts; "
                    "prefer lower values (tighten cap or weaken high-side preference)"
                ),
            }
            for r in remove_candidates
        ],
        "7_profit_improvement_expectation_ranking": improvement[:20],
    }

    report = {
        "phase": "phase631_profit_source_attribution",
        "verdict": PHASE631_VERDICT,
        "replay_root": str(replay_root),
        "days": list(DAYS),
        "trade_counts": {
            "all": len(trades),
            "top30": len(top),
            "bottom30": len(bottom),
            "pbv2": len(pbv2),
            "or": len(or_tr),
        },
        "pnl_yen_100": {
            "all": round(total_pnl, 2),
            "top30": round(top_pnl, 2),
            "bottom30": round(bot_pnl, 2),
            "pbv2": round(sum(float(t["pnl_yen_100"]) for t in pbv2), 2),
            "or": round(sum(float(t["pnl_yen_100"]) for t in or_tr), 2),
        },
        "mandatory_answers": answers,
        "artifacts": {
            "profit_source_ranking": str(REPORT_DIR / "profit_source_ranking.csv"),
            "profit_feature_importance": str(REPORT_DIR / "profit_feature_importance.csv"),
            "winner_vs_loser_distribution": str(REPORT_DIR / "winner_vs_loser_distribution.csv"),
            "phase631_report": str(REPORT_DIR / "phase631_report.json"),
        },
    }
    (REPORT_DIR / "phase631_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report


def main() -> int:
    report = run()
    print(f"verdict={report.get('verdict')}", flush=True)
    print(f"trades={report.get('trade_counts')}", flush=True)
    ans = report.get("mandatory_answers") or {}
    print("TOP5 contribute:", [x["feature"] for x in (ans.get("1_profit_contributing_features_top20") or [])[:5]], flush=True)
    print("TOP5 reduce:", [x["feature"] for x in (ans.get("2_profit_reducing_features_top20") or [])[:5]], flush=True)
    print(f"report={REPORT_DIR / 'phase631_report.json'}", flush=True)
    return 0 if report.get("verdict") == PHASE631_VERDICT else 1


if __name__ == "__main__":
    raise SystemExit(main())
