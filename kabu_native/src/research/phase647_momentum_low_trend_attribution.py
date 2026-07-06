"""
Phase647: Momentum Low Trend Attribution (research only).

Classifies PBv2 momentum_low entries by trend regime at entry; compares PnL;
counterfactual trend exclusions. No ENTRY/YAML changes.
"""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase631_profit_source_attribution import _num
from research.phase632_pbv2_profit_filter_counterfactual import _max_drawdown, _metrics, _profit_factor
from research.phase634_pbv2_only_rise5_full_period import load_trades_for_session

PHASE647_VERDICT = "phase647_momentum_low_trend_attribution_done"
REPORT_DIR_NAME = "phase647_momentum_low_trend_attribution"
MOMENTUM_LOW_CUTOFF = 0.2546

TREND_UP = "Up Trend"
TREND_SIDEWAYS = "Sideways"
TREND_DOWN = "Down Trend"
TREND_STRONG_DOWN = "Strong Down Trend"
TREND_LABELS = (TREND_UP, TREND_SIDEWAYS, TREND_DOWN, TREND_STRONG_DOWN)

NATIVE_ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER_ROOT = NATIVE_ROOT / "results" / "small_paper"
SUMCO_SYMBOL = "3436.T"
SUMCO_DAY = "2026-07-06"


def discover_phase647_sessions(root: Path = SMALL_PAPER_ROOT) -> list[dict[str, Any]]:
    """Replay (_phase630/current) + production live_session_* only."""
    out: list[dict[str, Any]] = []
    if not root.is_dir():
        return out

    def _add(day_dir: Path, source: str) -> None:
        if not day_dir.is_dir() or len(day_dir.name) != 8 or not day_dir.name.isdigit():
            return
        day_iso = f"{day_dir.name[:4]}-{day_dir.name[4:6]}-{day_dir.name[6:8]}"
        for sess_dir in sorted(day_dir.glob("live_session_*")):
            if not sess_dir.is_dir():
                continue
            summary = sess_dir / "small_paper_summary.json"
            if summary.is_file():
                try:
                    if str(json.loads(summary.read_text(encoding="utf-8")).get("source") or "") == "push-replay":
                        continue
                except json.JSONDecodeError:
                    pass
            if not (
                (sess_dir / "small_paper_events.jsonl").is_file()
                or (sess_dir / "small_paper_events.csv").is_file()
            ):
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
                    "source_kind": source,
                    "trade_count": len(trades),
                }
            )

    replay = root / "_phase630" / "current"
    if replay.is_dir():
        for day_dir in sorted(replay.iterdir()):
            _add(day_dir, "phase630_replay")

    for day_dir in sorted(root.iterdir()):
        if day_dir.name.startswith("_"):
            continue
        _add(day_dir, "live_session")

    return out


def load_all_trades(root: Path = SMALL_PAPER_ROOT) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sessions = discover_phase647_sessions(root)
    trades: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for sess in sessions:
        day = str(sess["day"])
        sess_dir = Path(sess["session_dir"])
        for t in load_trades_for_session(sess_dir, day):
            t["source_kind"] = sess["source_kind"]
            key = (day, str(t.get("session") or ""), str(t.get("symbol") or ""), str(t.get("entry_time") or ""))
            if key in seen:
                continue
            seen.add(key)
            trades.append(t)
    trades.sort(key=lambda r: (str(r.get("day") or ""), str(r.get("entry_time") or ""), str(r.get("symbol") or "")))
    return trades, sessions


def is_pbv2_momentum_low(row: Mapping[str, Any]) -> bool:
    if str(row.get("entry_pool") or "") != "PBV2":
        return False
    mc = _num(row.get("momentum_continuation"))
    if mc is None:
        mc = _num(row.get("momentum_continuation_raw"))
    if mc is None:
        return False
    return mc <= MOMENTUM_LOW_CUTOFF + 1e-9


def _trend_features(row: Mapping[str, Any]) -> dict[str, Any]:
    r5 = _num(row.get("entry_rise_5min_pct"))
    r10 = _num(row.get("entry_rise_10min_pct"))
    vwap = _num(row.get("entry_vwap_dev_pct"))
    dhd = _num(row.get("day_high_distance_pct"))
    mom = _num(row.get("momentum_continuation")) or _num(row.get("momentum_continuation_raw"))
    # Proxies for requested features (entry-time observable)
    ema5_slope_proxy = r5
    ema10_slope_proxy = r10
    vwap_slope_proxy = vwap
    reg5_slope_proxy = r5
    reg10_slope_proxy = r10
    higher_high_proxy = 1.0 if (r5 is not None and r5 > 0 and r10 is not None and r10 > 0) else 0.0
    higher_low_proxy = 1.0 if (r5 is not None and r5 > -0.1) else 0.0
    new_low_proxy = 1.0 if (r5 is not None and r5 < -0.3) or (r10 is not None and r10 < -0.5) else 0.0
    price_position_proxy = dhd
    return {
        "entry_rise_5min_pct": r5,
        "entry_rise_10min_pct": r10,
        "entry_vwap_dev_pct": vwap,
        "day_high_distance_pct": dhd,
        "momentum_continuation_score": mom,
        "ema5_slope_proxy": ema5_slope_proxy,
        "ema10_slope_proxy": ema10_slope_proxy,
        "vwap_slope_proxy": vwap_slope_proxy,
        "reg5_slope_proxy": reg5_slope_proxy,
        "reg10_slope_proxy": reg10_slope_proxy,
        "higher_high_proxy": higher_high_proxy,
        "higher_low_proxy": higher_low_proxy,
        "new_low_count_proxy": new_low_proxy,
        "price_position_proxy": price_position_proxy,
    }


def classify_trend(row: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    """Classify ENTRY trend using entry-time features only."""
    f = _trend_features(row)
    r5 = f["entry_rise_5min_pct"]
    r10 = f["entry_rise_10min_pct"]
    vwap = f["entry_vwap_dev_pct"]

    label = TREND_SIDEWAYS
    # Strong down: sharp recent decline
    if (r5 is not None and r5 <= -0.5) or (
        r5 is not None and r10 is not None and r5 <= -0.3 and r10 <= -0.8
    ):
        label = TREND_STRONG_DOWN
    elif (r5 is not None and r5 < -0.05) and (r10 is None or r10 <= 0.0):
        label = TREND_DOWN
    elif (r10 is not None and r10 < -0.2) and (r5 is None or r5 < 0.1):
        label = TREND_DOWN
    elif (
        r5 is not None
        and r10 is not None
        and r5 > 0.05
        and r10 > 0.0
        and (vwap is None or vwap >= -0.5)
    ):
        label = TREND_UP

    f["trend_label"] = label
    return label, f


def enrich_momentum_low_trades(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        if not is_pbv2_momentum_low(t):
            continue
        label, feats = classify_trend(t)
        row = dict(t)
        row.update(feats)
        row["trend_label"] = label
        out.append(row)
    return out


def trend_bucket_metrics(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    by_label: dict[str, list[dict[str, Any]]] = {lb: [] for lb in TREND_LABELS}
    for t in trades:
        by_label.setdefault(str(t.get("trend_label") or TREND_SIDEWAYS), []).append(dict(t))
    for label in TREND_LABELS:
        subset = by_label.get(label) or []
        if not subset:
            rows.append({"trend_label": label, "entry_count": 0})
            continue
        pnls = [float(t["pnl_yen_100"]) for t in subset]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        pf = _profit_factor(pnls)
        chrono = sorted(subset, key=lambda x: (str(x.get("entry_time") or ""), str(x.get("symbol") or "")))
        mfe = [_num(t.get("peak_mfe_pct")) for t in subset]
        mae = [_num(t.get("rolling_mae_pct")) for t in subset]
        rows.append(
            {
                "trend_label": label,
                "entry_count": len(subset),
                "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
                "profit_factor": (
                    None if pf is None else (999.0 if pf == float("inf") else round(float(pf), 4))
                ),
                "avg_win_yen_100": round(statistics.fmean(wins), 2) if wins else None,
                "avg_loss_yen_100": round(statistics.fmean(losses), 2) if losses else None,
                "avg_mfe_pct": round(statistics.fmean([x for x in mfe if x is not None]), 4)
                if any(x is not None for x in mfe)
                else None,
                "avg_mae_pct": round(statistics.fmean([x for x in mae if x is not None]), 4)
                if any(x is not None for x in mae)
                else None,
                "pnl_yen_100": round(sum(pnls), 2),
                "max_dd_yen_100": _max_drawdown([float(t["pnl_yen_100"]) for t in chrono]),
            }
        )
    total_pnl = sum(float(t["pnl_yen_100"]) for t in trades)
    for r in rows:
        pnl = float(r.get("pnl_yen_100") or 0)
        r["pnl_share_pct"] = round(100.0 * pnl / total_pnl, 2) if total_pnl else None
    return rows


def counterfactual_exclude(
    trades: Sequence[Mapping[str, Any]], *, exclude_labels: set[str]
) -> dict[str, Any]:
    baseline = _metrics(list(trades))
    kept = [dict(t) for t in trades if str(t.get("trend_label") or "") not in exclude_labels]
    blocked = [dict(t) for t in trades if str(t.get("trend_label") or "") in exclude_labels]
    m = _metrics(kept)
    wrong_win = [t for t in blocked if float(t["pnl_yen_100"]) > 0]
    rescued_loss = [t for t in blocked if float(t["pnl_yen_100"]) < 0]
    base_pf = baseline.get("profit_factor")
    cur_pf = m.get("profit_factor")
    delta_pf = None
    if isinstance(base_pf, (int, float)) and isinstance(cur_pf, (int, float)):
        if base_pf != 999.0 and cur_pf != 999.0:
            delta_pf = round(float(cur_pf) - float(base_pf), 4)
    return {
        "exclude_labels": sorted(exclude_labels),
        "baseline_entry_count": baseline["entry_count"],
        "kept_entry_count": m["entry_count"],
        "entry_reduction_pct": round(
            100.0 * (baseline["entry_count"] - m["entry_count"]) / max(1, baseline["entry_count"]),
            2,
        ),
        "delta_pnl_yen_100": round(float(m["pnl_yen_100"]) - float(baseline["pnl_yen_100"]), 2),
        "delta_pf": delta_pf,
        "delta_max_dd_yen_100": round(
            float(m["max_dd_yen_100"]) - float(baseline["max_dd_yen_100"]), 2
        ),
        "wrongly_blocked_winners": len(wrong_win),
        "wrongly_blocked_winners_pnl": round(sum(float(t["pnl_yen_100"]) for t in wrong_win), 2),
        "rescued_losers": len(rescued_loss),
        "rescued_losers_pnl": round(sum(float(t["pnl_yen_100"]) for t in rescued_loss), 2),
        **{k: m[k] for k in ("pnl_yen_100", "profit_factor", "win_rate", "max_dd_yen_100")},
    }


def feature_importance_by_trend(trades: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    feature_keys = [
        "entry_rise_5min_pct",
        "entry_rise_10min_pct",
        "entry_vwap_dev_pct",
        "day_high_distance_pct",
        "momentum_continuation_score",
        "ema5_slope_proxy",
        "ema10_slope_proxy",
        "price_position_proxy",
    ]
    rows: list[dict[str, Any]] = []
    for fk in feature_keys:
        by_trend: dict[str, list[float]] = {lb: [] for lb in TREND_LABELS}
        pnls: list[float] = []
        vals: list[float] = []
        for t in trades:
            v = _num(t.get(fk))
            if v is None:
                continue
            lb = str(t.get("trend_label") or TREND_SIDEWAYS)
            by_trend.setdefault(lb, []).append(v)
            pnls.append(float(t["pnl_yen_100"]))
            vals.append(v)
        if len(vals) < 10:
            continue
        mx = statistics.fmean(vals)
        my = statistics.fmean(pnls)
        num = sum((x - mx) * (y - my) for x, y in zip(vals, pnls))
        denx = math.sqrt(sum((x - mx) ** 2 for x in vals))
        deny = math.sqrt(sum((y - my) ** 2 for y in pnls))
        corr = num / (denx * deny) if denx > 1e-12 and deny > 1e-12 else 0.0
        spread = None
        means = [statistics.fmean(by_trend[lb]) for lb in TREND_LABELS if by_trend.get(lb)]
        if len(means) >= 2:
            spread = max(means) - min(means)
        rows.append(
            {
                "feature": fk,
                "n": len(vals),
                "corr_with_pnl": round(corr, 4),
                "mean_across_trades": round(mx, 4),
                "trend_mean_spread": round(spread, 4) if spread is not None else None,
                "mean_up_trend": round(statistics.fmean(by_trend[TREND_UP]), 4) if by_trend[TREND_UP] else None,
                "mean_sideways": round(statistics.fmean(by_trend[TREND_SIDEWAYS]), 4)
                if by_trend[TREND_SIDEWAYS]
                else None,
                "mean_down_trend": round(statistics.fmean(by_trend[TREND_DOWN]), 4)
                if by_trend[TREND_DOWN]
                else None,
                "mean_strong_down": round(statistics.fmean(by_trend[TREND_STRONG_DOWN]), 4)
                if by_trend[TREND_STRONG_DOWN]
                else None,
            }
        )
    rows.sort(key=lambda r: abs(float(r.get("corr_with_pnl") or 0)), reverse=True)
    return rows


def build_sumco_case_study(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    sumco = [
        t for t in trades
        if str(t.get("symbol") or "") == SUMCO_SYMBOL and str(t.get("day") or "") == SUMCO_DAY
    ]
    lines = [
        f"# SUMCO ({SUMCO_SYMBOL}) case study — {SUMCO_DAY}",
        "",
    ]
    if not sumco:
        lines.extend(["No PBv2 momentum_low closed trade found for SUMCO on this day.", ""])
        return {"found": False, "markdown": "\n".join(lines), "trades": []}

    for t in sorted(sumco, key=lambda x: str(x.get("entry_time") or "")):
        label, feats = classify_trend(t)
        lines.extend(
            [
                f"## Entry {t.get('entry_time')}",
                f"- Trend classification: **{label}**",
                f"- PnL (100 shares): {t.get('pnl_yen_100')} yen ({t.get('pnl_pct')}%)",
                f"- Exit: {t.get('exit_reason')}",
                f"- momentum_continuation_score: {feats.get('momentum_continuation_score')}",
                f"- entry_rise_5min_pct: {feats.get('entry_rise_5min_pct')}",
                f"- entry_rise_10min_pct: {feats.get('entry_rise_10min_pct')}",
                f"- entry_vwap_dev_pct: {feats.get('entry_vwap_dev_pct')}",
                f"- day_high_distance_pct: {feats.get('day_high_distance_pct')}",
                "",
                "### Why PBv2 passed",
                "- `momentum_continuation_score` <= 0.2546 (Momentum:low token for score v2 >= 3)",
                "- Board mid/high token + score v2 threshold met at entry scan",
                "- OR overlay did not take this slot (PBv2 pool accept)",
                "",
                "### Down-trend exclusion counterfactual",
                f"- Blocked by Down-only filter: {label in (TREND_DOWN,)}",
                f"- Blocked by Strong-Down filter: {label == TREND_STRONG_DOWN}",
                f"- Blocked by Down+Strong-Down filter: {label in (TREND_DOWN, TREND_STRONG_DOWN)}",
                "",
            ]
        )
    return {"found": True, "markdown": "\n".join(lines), "trades": sumco}


def build_mandatory_answers(
    *,
    mom_trades: Sequence[Mapping[str, Any]],
    trend_rows: Sequence[Mapping[str, Any]],
    cf_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    total = len(mom_trades)
    by_label = {r["trend_label"]: r for r in trend_rows}
    down_n = int(by_label.get(TREND_DOWN, {}).get("entry_count") or 0)
    strong_n = int(by_label.get(TREND_STRONG_DOWN, {}).get("entry_count") or 0)
    up_n = int(by_label.get(TREND_UP, {}).get("entry_count") or 0)
    down_share = round(100.0 * (down_n + strong_n) / max(1, total), 1)

    profit_src = max(trend_rows, key=lambda r: float(r.get("pnl_yen_100") or 0), default={})
    loss_src = min(trend_rows, key=lambda r: float(r.get("pnl_yen_100") or 0), default={})

    cf_map = {",".join(r.get("exclude_labels") or []): r for r in cf_rows}
    cf_down = next((r for r in cf_rows if r.get("exclude_labels") == [TREND_DOWN]), {})
    cf_strong = next((r for r in cf_rows if r.get("exclude_labels") == [TREND_STRONG_DOWN]), {})
    cf_both = next(
        (r for r in cf_rows if set(r.get("exclude_labels") or []) == {TREND_DOWN, TREND_STRONG_DOWN}),
        {},
    )

    # Adoption heuristic
    dd_both = float(cf_both.get("delta_max_dd_yen_100") or 0)
    pnl_both = float(cf_both.get("delta_pnl_yen_100") or 0)
    if down_share >= 40 and pnl_both > 0 and dd_both >= 0:
        recommendation = "HOLD — research trend gate shadow only; high down-trend share but mixed counterfactual"
    elif pnl_both > 5000 and dd_both > 1000:
        recommendation = "HOLD — promising counterfactual; needs out-of-sample paper days"
    elif pnl_both < -2000:
        recommendation = "Reject — excluding down trends hurts aggregate PnL"
    else:
        recommendation = "HOLD — insufficient edge for production gate; continue monitoring"

    return {
        "1_momentum_low_reality": (
            f"PBv2 momentum_low entries (score<={MOMENTUM_LOW_CUTOFF}): n={total}. "
            f"Down+StrongDown share={down_share}%. "
            f"Up={up_n}, Sideways={by_label.get(TREND_SIDEWAYS,{}).get('entry_count',0)}."
        ),
        "2_trend_pnl_comparison": trend_rows,
        "3_counterfactual": {
            "exclude_down_only": cf_down,
            "exclude_strong_down_only": cf_strong,
            "exclude_down_and_strong_down": cf_both,
        },
        "4_pbv2_additive_value": (
            "Momentum Low captures both pullback (Up/Sideways) and decline entries; "
            f"profit source skew: {profit_src.get('trend_label')} "
            f"(PnL {profit_src.get('pnl_yen_100')}); "
            f"loss source skew: {loss_src.get('trend_label')} "
            f"(PnL {loss_src.get('pnl_yen_100')})."
        ),
        "5_overfit_risk": (
            "Medium — trend labels use entry-time rise/VWAP proxies on limited session count; "
            "thresholds not walk-forward validated. Counterfactual is in-sample only."
        ),
        "6_recommendation": recommendation,
        "pullback_vs_decline": {
            "pullback_like_share_pct": round(100.0 * (up_n + int(by_label.get(TREND_SIDEWAYS, {}).get("entry_count") or 0)) / max(1, total), 1),
            "decline_like_share_pct": down_share,
        },
    }


@dataclass
class Phase647Job:
    native_root: Path

    def run(self) -> dict[str, Any]:
        root = self.native_root / "results" / "small_paper"
        all_trades, sessions = load_all_trades(root)
        mom_trades = enrich_momentum_low_trades(all_trades)
        trend_rows = trend_bucket_metrics(mom_trades)
        cf_rows = [
            counterfactual_exclude(mom_trades, exclude_labels={TREND_DOWN}),
            counterfactual_exclude(mom_trades, exclude_labels={TREND_STRONG_DOWN}),
            counterfactual_exclude(mom_trades, exclude_labels={TREND_DOWN, TREND_STRONG_DOWN}),
        ]
        feat_rows = feature_importance_by_trend(mom_trades)
        sumco = build_sumco_case_study(mom_trades)
        answers = build_mandatory_answers(
            mom_trades=mom_trades, trend_rows=trend_rows, cf_rows=cf_rows
        )
        dist_rows = [
            {
                "day": t.get("day"),
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "trend_label": t.get("trend_label"),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "entry_rise_5min_pct": t.get("entry_rise_5min_pct"),
                "entry_rise_10min_pct": t.get("entry_rise_10min_pct"),
                "entry_vwap_dev_pct": t.get("entry_vwap_dev_pct"),
                "momentum_continuation_score": t.get("momentum_continuation_score"),
                "source_kind": t.get("source_kind"),
            }
            for t in mom_trades
        ]
        return {
            "verdict": PHASE647_VERDICT,
            "generated_at": _now_iso(),
            "session_count": len(sessions),
            "all_trade_count": len(all_trades),
            "momentum_low_pbv2_count": len(mom_trades),
            "mandatory_answers": answers,
            "trend_distribution": dist_rows,
            "trend_metrics": trend_rows,
            "counterfactual": cf_rows,
            "feature_importance": feat_rows,
            "sumco": sumco,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        out = self.native_root / "results" / "reports" / REPORT_DIR_NAME
        out.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}

        _write_csv(
            out / "trend_distribution.csv",
            [
                "day",
                "symbol",
                "entry_time",
                "trend_label",
                "pnl_yen_100",
                "entry_rise_5min_pct",
                "entry_rise_10min_pct",
                "entry_vwap_dev_pct",
                "momentum_continuation_score",
                "source_kind",
            ],
            list(result.get("trend_distribution") or []),
        )
        paths["trend_distribution"] = out / "trend_distribution.csv"

        _write_csv(
            out / "trend_counterfactual.csv",
            [
                "exclude_labels",
                "baseline_entry_count",
                "kept_entry_count",
                "entry_reduction_pct",
                "delta_pnl_yen_100",
                "delta_pf",
                "delta_max_dd_yen_100",
                "wrongly_blocked_winners",
                "wrongly_blocked_winners_pnl",
                "rescued_losers",
                "rescued_losers_pnl",
                "pnl_yen_100",
                "profit_factor",
                "win_rate",
                "max_dd_yen_100",
            ],
            [
                {**r, "exclude_labels": "|".join(r.get("exclude_labels") or [])}
                for r in (result.get("counterfactual") or [])
            ],
        )
        paths["trend_counterfactual"] = out / "trend_counterfactual.csv"

        _write_csv(
            out / "trend_feature_importance.csv",
            [
                "feature",
                "n",
                "corr_with_pnl",
                "mean_across_trades",
                "trend_mean_spread",
                "mean_up_trend",
                "mean_sideways",
                "mean_down_trend",
                "mean_strong_down",
            ],
            list(result.get("feature_importance") or []),
        )
        paths["feature_importance"] = out / "trend_feature_importance.csv"

        sumco_md = (result.get("sumco") or {}).get("markdown") or ""
        (out / "sumco_case_study.md").write_text(sumco_md, encoding="utf-8")
        paths["sumco"] = out / "sumco_case_study.md"

        report = {
            "phase": "647",
            "verdict": result.get("verdict"),
            "generated_at": result.get("generated_at"),
            "session_count": result.get("session_count"),
            "all_trade_count": result.get("all_trade_count"),
            "momentum_low_pbv2_count": result.get("momentum_low_pbv2_count"),
            "mandatory_answers": result.get("mandatory_answers"),
            "trend_metrics": result.get("trend_metrics"),
            "counterfactual": result.get("counterfactual"),
            "artifacts": {k: str(v) for k, v in paths.items()},
        }
        report_fp = out / "phase647_report.json"
        report_fp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["report"] = report_fp
        return paths


def main() -> int:
    job = Phase647Job(native_root=NATIVE_ROOT)
    result = job.run()
    paths = job.write_outputs(result)
    print(json.dumps({"verdict": result.get("verdict"), "paths": {k: str(v) for k, v in paths.items()}}, indent=2))
    print(json.dumps(result.get("mandatory_answers"), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
