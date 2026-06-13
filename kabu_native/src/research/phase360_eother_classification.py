"""
Phase360: E_other low-MFE stop_hit deep classification and guard discovery.
"""

from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase357_actual_exit_audit import MAX_DAY, MIN_DAY, _universe_group
from research.phase358_low_mfe_stophit_forensic import (
    LOW_MFE_THRESHOLD_PCT,
    _float,
    _is_low_mfe_stop,
    _pf,
    classify_forensic_pattern,
    load_session_forensic_trades,
)

JST = ZoneInfo("Asia/Tokyo")

E_OTHER_PATTERN = "E_other"

TIME_BUCKETS = (
    ("09:00-09:30", (9, 0), (9, 30)),
    ("09:30-10:00", (9, 30), (10, 0)),
    ("10:00-11:30", (10, 0), (11, 30)),
    ("12:30-14:00", (12, 30), (14, 0)),
    ("14:00-15:00", (14, 0), (15, 0)),
)

TRADE_FIELDS = [
    "session_id",
    "day_key",
    "session_kind",
    "universe_group",
    "universe_slot",
    "symbol",
    "entry_time",
    "entry_time_bucket",
    "exit_time",
    "pnl_yen_100",
    "pnl_pct",
    "peak_mfe_pct",
    "exit_reason_canonical",
    "forensic_pattern",
    "eother_cluster",
    "entry_momentum_score",
    "entry_rise_5min_pct",
    "entry_rise_10min_pct",
    "entry_vwap_dev_pct",
    "entry_imbalance_pct",
    "entry_imbalance_pctile",
    "price_range_position",
    "day_high_distance_pct",
    "trading_value",
    "turnover_proxy",
    "board_dynamic_tier",
    "entry_quality",
    "intraday_range_pct",
]


def _parse_entry_dt(entry_time: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(entry_time).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except (TypeError, ValueError):
        return None


def entry_time_bucket(entry_time: str) -> str:
    dt = _parse_entry_dt(entry_time)
    if dt is None:
        return "other"
    t = (dt.hour, dt.minute)
    for label, start, end in TIME_BUCKETS:
        if start <= t <= end:
            return label
    return "other"


def _price_range_position(near_high_pct: Optional[float], intraday_range_pct: Optional[float]) -> Optional[float]:
    """0=range low proxy, 1=at day high. Uses distance-below-high vs intraday range."""
    if near_high_pct is None:
        return None
    if intraday_range_pct is not None and intraday_range_pct > 0:
        return round(max(0.0, min(1.0, 1.0 - near_high_pct / intraday_range_pct)), 4)
    return round(max(0.0, min(1.0, 1.0 - near_high_pct / 10.0)), 4)


def enrich_eother_trade(trade: Mapping[str, Any], acc: Mapping[str, str]) -> dict[str, Any]:
    near_high = _float(acc.get("entry_near_day_high_pct") or trade.get("entry_near_day_high_pct"))
    intraday_range = _float(acc.get("intraday_range_pct") or trade.get("intraday_range_pct"))
    mom = _float(
        acc.get("entry_momentum_continuation_score")
        or acc.get("momentum_continuation_score")
        or trade.get("momentum_continuation_score")
    )
    imb = _float(acc.get("entry_order_book_imbalance") or trade.get("entry_order_book_imbalance"))
    imb_pctile = _float(acc.get("entry_imbalance_percentile") or trade.get("entry_imbalance_percentile"))
    rise5 = _float(acc.get("entry_rise_5min_pct") or trade.get("entry_rise_5min_pct"))
    rise10 = _float(acc.get("entry_rise_10min_pct") or trade.get("entry_rise_10min_pct"))
    vwap_dev = _float(acc.get("entry_vwap_dev_pct") or trade.get("entry_vwap_dev_pct"))
    entry_time = str(trade.get("entry_time") or "")

    row = {
        **dict(trade),
        "universe_group": trade.get("universe_group") or _universe_group(trade),
        "entry_momentum_score": mom,
        "entry_rise_5min_pct": rise5,
        "entry_rise_10min_pct": rise10,
        "entry_vwap_dev_pct": vwap_dev,
        "entry_imbalance_pct": imb,
        "entry_imbalance_pctile": imb_pctile,
        "day_high_distance_pct": near_high,
        "price_range_position": _price_range_position(near_high, intraday_range),
        "intraday_range_pct": intraday_range,
        "entry_time_bucket": entry_time_bucket(entry_time),
        "trading_value": _float(acc.get("trading_value") or trade.get("trading_value")),
        "turnover_proxy": _float(acc.get("turnover_proxy") or trade.get("turnover_proxy")),
    }
    return row


def _is_board_low(trade: Mapping[str, Any]) -> bool:
    tier = str(trade.get("board_dynamic_tier") or "")
    if tier == "board_low":
        return True
    pctile = _float(trade.get("entry_imbalance_pctile"))
    return pctile is not None and pctile < 25.0


def _is_weak_momentum(trade: Mapping[str, Any], threshold: float = 0.30) -> bool:
    mom = _float(trade.get("entry_momentum_score"))
    return mom is not None and mom < threshold


def _is_near_day_high(trade: Mapping[str, Any], threshold: float = 1.5) -> bool:
    dist = _float(trade.get("day_high_distance_pct"))
    return dist is not None and dist <= threshold


def _is_early_open(trade: Mapping[str, Any]) -> bool:
    return trade.get("entry_time_bucket") == "09:00-09:30"


def _is_negative_vwap_not_pullback(trade: Mapping[str, Any]) -> bool:
    vwap = _float(trade.get("entry_vwap_dev_pct"))
    rise5 = _float(trade.get("entry_rise_5min_pct"))
    if vwap is None or vwap >= 0:
        return False
    if rise5 is not None and rise5 < 0:
        return False
    return True


def _is_flat_rise_weak(trade: Mapping[str, Any]) -> bool:
    rise5 = _float(trade.get("entry_rise_5min_pct"))
    mom = _float(trade.get("entry_momentum_score"))
    if rise5 is None or rise5 < 0 or rise5 >= 0.3:
        return False
    return mom is None or mom < 0.30


def _is_high_range_top(trade: Mapping[str, Any]) -> bool:
    pos = _float(trade.get("price_range_position"))
    return pos is not None and pos >= 0.85


CLUSTER_GUARDS: list[dict[str, Any]] = [
    {
        "cluster_id": "C01_board_low",
        "label": "board_low tier or imbalance pctile<25",
        "priority": 1,
        "match": _is_board_low,
        "guard_rule": "board_dynamic_tier==board_low OR entry_imbalance_percentile<25",
    },
    {
        "cluster_id": "C02_early_open_board_low",
        "label": "09:00-09:30 AND board_low",
        "priority": 2,
        "match": lambda t: _is_early_open(t) and _is_board_low(t),
        "guard_rule": "entry_time 09:00-09:30 AND (board_low OR imb_pctile<25)",
    },
    {
        "cluster_id": "C03_near_day_high_low_mom",
        "label": "near day high (<=1.5%) AND momentum<0.30",
        "priority": 3,
        "match": lambda t: _is_near_day_high(t) and _is_weak_momentum(t),
        "guard_rule": "day_high_distance_pct<=1.5 AND entry_momentum_score<0.30",
    },
    {
        "cluster_id": "C04_negative_vwap_not_pullback",
        "label": "vwap_dev<0 without rise5<0 (not B)",
        "priority": 4,
        "match": _is_negative_vwap_not_pullback,
        "guard_rule": "entry_vwap_dev_pct<0 AND NOT(entry_rise_5min<0)",
    },
    {
        "cluster_id": "C05_flat_rise_weak_momentum",
        "label": "0<=rise5<0.3% AND momentum<0.30",
        "priority": 5,
        "match": _is_flat_rise_weak,
        "guard_rule": "0<=entry_rise_5min<0.3 AND entry_momentum_score<0.30",
    },
    {
        "cluster_id": "C06_weak_momentum",
        "label": "momentum<0.30",
        "priority": 6,
        "match": _is_weak_momentum,
        "guard_rule": "entry_momentum_score<0.30",
    },
    {
        "cluster_id": "C07_early_open",
        "label": "09:00-09:30 entry",
        "priority": 7,
        "match": _is_early_open,
        "guard_rule": "entry_time 09:00-09:30",
    },
    {
        "cluster_id": "C08_high_range_top",
        "label": "price_range_position>=0.85",
        "priority": 8,
        "match": _is_high_range_top,
        "guard_rule": "price_range_position>=0.85",
    },
    {
        "cluster_id": "C09_dynamic40_residual",
        "label": "Dynamic40 E_other residual",
        "priority": 9,
        "match": lambda t: str(t.get("universe_group") or "") == "dynamic40",
        "guard_rule": "universe_group==dynamic40",
    },
    {
        "cluster_id": "C10_residual",
        "label": "unclassified E_other residual",
        "priority": 99,
        "match": lambda _t: True,
        "guard_rule": "none",
    },
]


def assign_cluster(trade: Mapping[str, Any]) -> str:
    for spec in sorted(CLUSTER_GUARDS, key=lambda x: x["priority"]):
        if spec["cluster_id"] == "C10_residual":
            continue
        fn: Callable[[Mapping[str, Any]], bool] = spec["match"]
        if fn(trade):
            return str(spec["cluster_id"])
    return "C10_residual"


def cluster_guard_match(trade: Mapping[str, Any], cluster_id: str) -> bool:
    for spec in CLUSTER_GUARDS:
        if spec["cluster_id"] == cluster_id:
            fn: Callable[[Mapping[str, Any]], bool] = spec["match"]
            return bool(fn(trade))
    return False


def _counterfactual(
    all_trades: Sequence[Mapping[str, Any]],
    *,
    cluster_id: str,
) -> dict[str, Any]:
    removed = [t for t in all_trades if cluster_guard_match(t, cluster_id)]
    kept = [t for t in all_trades if not cluster_guard_match(t, cluster_id)]
    actual_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in all_trades]
    new_yens = [float(_float(t.get("pnl_yen_100")) or 0.0) for t in kept]
    actual_total = round(sum(actual_yens), 2)
    new_total = round(sum(new_yens), 2)
    removed_pnl = round(sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in removed), 2)
    eother_removed = [
        t
        for t in removed
        if str(t.get("forensic_pattern") or "") == E_OTHER_PATTERN and _is_low_mfe_stop(t)
    ]
    return {
        "cluster_id": cluster_id,
        "removed_trades": len(removed),
        "removed_pnl_yen_100": removed_pnl,
        "eother_low_mfe_removed_count": len(eother_removed),
        "eother_low_mfe_removed_pnl": round(
            sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in eother_removed), 2
        ),
        "delta_yen": round(new_total - actual_total, 2),
        "actual_pf": _pf(actual_yens),
        "shadow_pf": _pf(new_yens),
        "delta_pf": (
            round((_pf(new_yens) or 0) - (_pf(actual_yens) or 0), 4)
            if _pf(new_yens) is not None and _pf(actual_yens) is not None
            and _pf(new_yens) != float("inf")
            and _pf(actual_yens) != float("inf")
            else None
        ),
    }


def load_session_eother_trades(session_meta: Mapping[str, Any], *, reports_dir: Path) -> dict[str, Any]:
    from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

    base = load_session_forensic_trades(session_meta, reports_dir=reports_dir)
    if base.get("error"):
        return {**base, "eother_trades": [], "all_kept_enriched": []}

    sess_dir = Path(str(session_meta["session_dir"]))
    accepted: dict[tuple[str, str], dict[str, str]] = {}
    for row in _stream_events_csv(sess_dir / "small_paper_events.csv"):
        if row.get("event_type") == "accepted":
            accepted[(row.get("symbol", ""), row.get("entry_time", ""))] = row

    all_enriched: list[dict[str, Any]] = []
    eother: list[dict[str, Any]] = []
    for trade in base.get("forensic_all_kept") or []:
        key = (trade.get("symbol", ""), trade.get("entry_time", ""))
        acc = accepted.get(key, {})
        row = enrich_eother_trade(trade, acc)
        row["eother_cluster"] = assign_cluster(row)
        all_enriched.append(row)
        if (
            _is_low_mfe_stop(row)
            and str(row.get("forensic_pattern") or classify_forensic_pattern(row)) == E_OTHER_PATTERN
        ):
            eother.append(row)

    return {
        **base,
        "eother_trades": eother,
        "all_kept_enriched": all_enriched,
        "error": "",
    }


@dataclass
class Phase360EotherClassification:
    reports_dir: Path
    eother_trades: list[dict[str, Any]] = field(default_factory=list)
    all_kept_trades: list[dict[str, Any]] = field(default_factory=list)
    sessions_loaded: int = 0

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase360_eother_classification_summary.json",
            "clusters": self.reports_dir / "phase360_eother_clusters.csv",
            "counterfactual": self.reports_dir / "phase360_eother_counterfactual.csv",
            "top_symbols": self.reports_dir / "phase360_eother_top_symbols.csv",
            "trades": self.reports_dir / "phase360_eother_trades.csv",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.sessions_loaded += 1
        self.eother_trades.extend(result.get("eother_trades") or [])
        self.all_kept_trades.extend(result.get("all_kept_enriched") or [])

    def _cluster_stats(self) -> list[dict[str, Any]]:
        acc: dict[str, dict[str, Any]] = defaultdict(
            lambda: {
                "count": 0,
                "total_pnl_yen_100": 0.0,
                "symbols": Counter(),
                "time_buckets": Counter(),
                "universe": Counter(),
                "feature_sums": defaultdict(float),
                "feature_counts": defaultdict(int),
            }
        )
        feature_keys = (
            "entry_momentum_score",
            "entry_rise_5min_pct",
            "entry_vwap_dev_pct",
            "entry_imbalance_pctile",
            "day_high_distance_pct",
            "price_range_position",
            "trading_value",
            "turnover_proxy",
        )
        for t in self.eother_trades:
            cid = str(t.get("eother_cluster") or assign_cluster(t))
            acc[cid]["count"] += 1
            acc[cid]["total_pnl_yen_100"] += float(_float(t.get("pnl_yen_100")) or 0.0)
            acc[cid]["symbols"][str(t.get("symbol") or "")] += 1
            acc[cid]["time_buckets"][str(t.get("entry_time_bucket") or "")] += 1
            acc[cid]["universe"][str(t.get("universe_group") or "")] += 1
            for fk in feature_keys:
                v = _float(t.get(fk))
                if v is not None:
                    acc[cid]["feature_sums"][fk] += v
                    acc[cid]["feature_counts"][fk] += 1

        total_loss = sum(
            float(_float(t.get("pnl_yen_100")) or 0.0)
            for t in self.eother_trades
            if float(_float(t.get("pnl_yen_100")) or 0.0) < 0
        )
        rows = []
        spec_by_id = {s["cluster_id"]: s for s in CLUSTER_GUARDS}
        for cid in sorted(acc.keys(), key=lambda c: acc[c]["total_pnl_yen_100"]):
            v = acc[cid]
            spec = spec_by_id.get(cid, {})
            avg_features = {
                fk: round(v["feature_sums"][fk] / v["feature_counts"][fk], 4)
                for fk in v["feature_sums"]
                if v["feature_counts"][fk]
            }
            rows.append(
                {
                    "cluster_id": cid,
                    "label": spec.get("label", ""),
                    "guard_rule": spec.get("guard_rule", ""),
                    "count": v["count"],
                    "total_pnl_yen_100": round(v["total_pnl_yen_100"], 2),
                    "avg_pnl_yen_100": round(v["total_pnl_yen_100"] / v["count"], 2)
                    if v["count"]
                    else 0.0,
                    "share_of_eother_loss": round(v["total_pnl_yen_100"] / total_loss, 4)
                    if total_loss < 0 and v["total_pnl_yen_100"] < 0
                    else None,
                    "top_symbol": v["symbols"].most_common(1)[0][0] if v["symbols"] else "",
                    "top_symbol_count": v["symbols"].most_common(1)[0][1] if v["symbols"] else 0,
                    "dominant_time_bucket": v["time_buckets"].most_common(1)[0][0]
                    if v["time_buckets"]
                    else "",
                    "dynamic40_count": v["universe"].get("dynamic40", 0),
                    "core10_count": v["universe"].get("core10", 0),
                    "avg_entry_momentum_score": avg_features.get("entry_momentum_score"),
                    "avg_entry_rise_5min_pct": avg_features.get("entry_rise_5min_pct"),
                    "avg_entry_vwap_dev_pct": avg_features.get("entry_vwap_dev_pct"),
                    "avg_entry_imbalance_pctile": avg_features.get("entry_imbalance_pctile"),
                    "avg_day_high_distance_pct": avg_features.get("day_high_distance_pct"),
                    "avg_price_range_position": avg_features.get("price_range_position"),
                    "avg_trading_value": avg_features.get("trading_value"),
                    "avg_turnover_proxy": avg_features.get("turnover_proxy"),
                }
            )
        return rows

    def _axis_breakdown(self) -> dict[str, Any]:
        def _bucket_stats(trades: Sequence[Mapping[str, Any]], key: str) -> list[dict[str, Any]]:
            acc: dict[str, dict[str, float]] = defaultdict(lambda: {"count": 0, "pnl": 0.0})
            for t in trades:
                b = str(t.get(key) or "missing")
                acc[b]["count"] += 1
                acc[b]["pnl"] += float(_float(t.get("pnl_yen_100")) or 0.0)
            return [
                {"bucket": b, "count": int(v["count"]), "total_pnl_yen_100": round(v["pnl"], 2)}
                for b, v in sorted(acc.items(), key=lambda x: x[1]["pnl"])
            ]

        mom_bins = []
        for t in self.eother_trades:
            m = _float(t.get("entry_momentum_score"))
            if m is None:
                label = "missing"
            elif m < 0.25:
                label = "<0.25"
            elif m < 0.35:
                label = "0.25-0.35"
            else:
                label = ">=0.35"
            mom_bins.append({**t, "_mom_bin": label})
        return {
            "time_bucket": _bucket_stats(self.eother_trades, "entry_time_bucket"),
            "universe_group": _bucket_stats(self.eother_trades, "universe_group"),
            "board_tier": _bucket_stats(self.eother_trades, "board_dynamic_tier"),
            "momentum_bin": _bucket_stats(mom_bins, "_mom_bin"),
        }

    def _top_symbols(self) -> list[dict[str, Any]]:
        acc: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "total_pnl_yen_100": 0.0, "clusters": Counter()}
        )
        for t in self.eother_trades:
            sym = str(t.get("symbol") or "")
            acc[sym]["count"] += 1
            acc[sym]["total_pnl_yen_100"] += float(_float(t.get("pnl_yen_100")) or 0.0)
            acc[sym]["clusters"][str(t.get("eother_cluster") or "")] += 1
        rows = []
        for sym, v in sorted(acc.items(), key=lambda x: x[1]["total_pnl_yen_100"]):
            rows.append(
                {
                    "symbol": sym,
                    "count": v["count"],
                    "total_pnl_yen_100": round(v["total_pnl_yen_100"], 2),
                    "avg_pnl_yen_100": round(v["total_pnl_yen_100"] / v["count"], 2),
                    "dominant_cluster": v["clusters"].most_common(1)[0][0] if v["clusters"] else "",
                    "cluster_counts": dict(v["clusters"]),
                }
            )
        return rows

    def _counterfactual_rows(self) -> list[dict[str, Any]]:
        rows = []
        for spec in CLUSTER_GUARDS:
            cid = spec["cluster_id"]
            if cid == "C10_residual":
                continue
            cf = _counterfactual(self.all_kept_trades, cluster_id=cid)
            cf["label"] = spec.get("label", "")
            cf["guard_rule"] = spec.get("guard_rule", "")
            rows.append(cf)
        rows.sort(key=lambda r: float(r.get("delta_yen") or 0.0), reverse=True)
        return rows

    def _pick_best_guard(self, cf_rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        candidates = [
            r
            for r in cf_rows
            if float(r.get("delta_yen") or 0.0) > 0
            and float(r.get("removed_pnl_yen_100") or 0.0) < 0
            and int(r.get("eother_low_mfe_removed_count") or 0) > 0
        ]
        if not candidates:
            candidates = sorted(cf_rows, key=lambda r: float(r.get("delta_yen") or 0.0), reverse=True)
        best = max(
            candidates,
            key=lambda r: (
                float(r.get("delta_yen") or 0.0),
                abs(float(r.get("eother_low_mfe_removed_pnl") or 0.0)),
            ),
        )
        spec = next((s for s in CLUSTER_GUARDS if s["cluster_id"] == best["cluster_id"]), {})
        return {
            "cluster_id": best["cluster_id"],
            "label": spec.get("label", best.get("label")),
            "guard_rule": spec.get("guard_rule", best.get("guard_rule")),
            "delta_yen": best.get("delta_yen"),
            "delta_pf": best.get("delta_pf"),
            "removed_trades": best.get("removed_trades"),
            "removed_pnl_yen_100": best.get("removed_pnl_yen_100"),
            "eother_low_mfe_removed_count": best.get("eother_low_mfe_removed_count"),
            "eother_low_mfe_removed_pnl": best.get("eother_low_mfe_removed_pnl"),
            "rationale": (
                "Highest positive delta_yen on Phase355 population among guards "
                "that remove net-losing trades and capture E_other low-MFE stops."
            ),
        }

    def build_summary(self) -> dict[str, Any]:
        total_pnl = round(
            sum(float(_float(t.get("pnl_yen_100")) or 0.0) for t in self.eother_trades), 2
        )
        cluster_rows = self._cluster_stats()
        cf_rows = self._counterfactual_rows()
        best = self._pick_best_guard(cf_rows)
        worst_cluster = min(cluster_rows, key=lambda r: r["total_pnl_yen_100"], default={})
        return {
            "phase": 360,
            "title": "eother_low_mfe_stophit_deep_classification",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": "phase355_post_excluded",
            "filter": {
                "forensic_pattern": E_OTHER_PATTERN,
                "exit_reason": "stop_hit",
                "peak_mfe_pct_lt": LOW_MFE_THRESHOLD_PCT,
            },
            "date_range": {"min_day": MIN_DAY, "max_day": MAX_DAY},
            "sessions_loaded": self.sessions_loaded,
            "eother_low_mfe_count": len(self.eother_trades),
            "eother_low_mfe_total_pnl_yen_100": total_pnl,
            "axis_breakdown": self._axis_breakdown(),
            "top_loss_cluster": worst_cluster,
            "top_features": {
                "highest_loss_time_bucket": max(
                    self._axis_breakdown()["time_bucket"],
                    key=lambda x: abs(min(0, x["total_pnl_yen_100"])),
                    default={},
                ),
                "highest_loss_universe": min(
                    self._axis_breakdown()["universe_group"],
                    key=lambda x: x["total_pnl_yen_100"],
                    default={},
                ),
                "highest_loss_board_tier": min(
                    self._axis_breakdown()["board_tier"],
                    key=lambda x: x["total_pnl_yen_100"],
                    default={},
                ),
                "highest_loss_momentum_bin": min(
                    self._axis_breakdown()["momentum_bin"],
                    key=lambda x: x["total_pnl_yen_100"],
                    default={},
                ),
            },
            "counterfactual_ranking": cf_rows[:5],
            "best_entry_guard_candidate": best,
            "cluster_definitions": [
                {
                    "cluster_id": s["cluster_id"],
                    "label": s["label"],
                    "guard_rule": s["guard_rule"],
                    "priority": s["priority"],
                }
                for s in CLUSTER_GUARDS
            ],
        }

    def finalize_outputs(self) -> dict[str, str]:
        paths = self.paths()
        summary = self.build_summary()
        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        cluster_rows = self._cluster_stats()
        self._write_csv(paths["clusters"], cluster_rows)
        self._write_csv(paths["counterfactual"], self._counterfactual_rows())
        sym_rows = self._top_symbols()
        if sym_rows:
            fields = sorted({k for r in sym_rows for k in r if k != "cluster_counts"})
            with paths["top_symbols"].open("w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
                w.writeheader()
                for row in sym_rows:
                    flat = dict(row)
                    cc = flat.pop("cluster_counts", None)
                    if isinstance(cc, dict):
                        for k, v in cc.items():
                            flat[f"cluster_{k}"] = v
                    w.writerow(flat)
        self._write_csv(paths["trades"], self.eother_trades, TRADE_FIELDS)
        return {k: str(v) for k, v in paths.items()}

    def _write_csv(
        self,
        path: Path,
        rows: Sequence[Mapping[str, Any]],
        fieldnames: Optional[Sequence[str]] = None,
    ) -> None:
        if not rows:
            path.write_text("", encoding="utf-8")
            return
        fields = list(fieldnames) if fieldnames else sorted({k for r in rows for k in r})
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for row in rows:
                w.writerow(row)
