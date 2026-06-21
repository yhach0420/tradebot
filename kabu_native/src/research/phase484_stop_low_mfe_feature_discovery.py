"""
Phase484 — Stop Low MFE Feature Discovery Tournament (research only).

Explores derived entry features (momentum decay, VWAP extension, high exhaustion,
board deterioration) for stop_low_mfe vs strong_winner separation.
No replay — entry-level discovery only.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase382_capital_constrained_backtest import _float, _parse_ts, _position_key
from research.phase443_full_runtime_combined_capital_sim import simulate_capacity_replay
from research.phase451_entry_shape_tournament import (
    PERIOD_END,
    PERIOD_START,
    _build_price_index_to,
    _now_iso,
    _optional_float,
)
from research.phase463_trend_pullback_population_tournament import (
    _fill_close_proxy_shadows,
    _filter_replay_pool,
)
from research.phase464_pre_gate_archetype_audit import _vwap_above_ratio, _vwap_dev
from research.phase465b_trend_gate_redesign import (
    _cohens_d,
    _high_update_age,
    _mi_median_split,
)
from research.phase473_trend_entry_architecture import _entry_block, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _ensure_enriched, _load_replay_pool
from research.phase481_stop_low_mfe_reduction_tournament import _build_trade_rows
from research.phase483_stop_low_mfe_root_cause_audit import (
    _cohort_label,
    _ks_stat,
    _pnl_p80,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PERCENTILE_CANDIDATES = (0.25, 0.33, 0.40)
TOP_N = 20

FEATURE_GROUPS = {
    "A_momentum_decay": ("A1_r30_minus_r5", "A2_r15_minus_r5", "A3_r30_over_r5", "A4_momentum_slope"),
    "B_vwap_extension": ("B1_vwap_dev_pct", "B2_vwap_extension_rate", "B3_vwap_reversion_score"),
    "C_high_exhaustion": ("C1_high_update_count_30m", "C2_high_update_count_session", "C3_high_update_density"),
    "D_board_deterioration": ("D1_board_change_5m", "D2_board_change_10m", "D3_board_decay_score"),
}

ALL_FEATURES = tuple(f for group in FEATURE_GROUPS.values() for f in group)

RANKING_FIELDS = [
    "rank",
    "feature_id",
    "feature_group",
    "slm_mean",
    "slm_median",
    "strong_winner_mean",
    "strong_winner_median",
    "missing_rate_slm",
    "missing_rate_sw",
    "cohens_d",
    "ks_statistic",
    "mutual_information",
    "feature_direction",
]

PATTERN_FIELDS = [
    "pattern_id",
    "condition_count",
    "conditions",
    "threshold_summary",
    "slm_capture_rate",
    "strong_winner_fp_rate",
    "separation_score",
    "blocked_stop_low_mfe",
    "blocked_strong_winner",
    "blocked_total",
    "blocked_pnl",
    "expected_delta",
    "rank_by_separation",
]

DISCOVERY_FIELDS = [
    "position_key",
    "cohort",
    "symbol",
    "day",
    "pnl_yen",
    *ALL_FEATURES,
]


def _r(trade: Mapping[str, Any], minutes: int) -> Optional[float]:
    key = f"return_{minutes}min_pct"
    alt = f"entry_rise_{minutes}min_pct"
    return _optional_float(trade.get(key)) or _optional_float(trade.get(alt))


def _momentum_slope(trade: Mapping[str, Any]) -> Optional[float]:
    pts: list[tuple[float, float]] = []
    for m in (5, 10, 15, 30):
        v = _r(trade, m)
        if v is not None:
            pts.append((float(m), float(v)))
    if len(pts) < 2:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    mx = statistics.mean(xs)
    my = statistics.mean(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return round(num / den, 6)


def _safe_ratio(num: Optional[float], den: Optional[float]) -> Optional[float]:
    if num is None or den is None or abs(den) < 1e-9:
        return None
    return round(num / den, 6)


def _vwap_reversion_score(trade: Mapping[str, Any]) -> Optional[float]:
    failed = trade.get("failed_reclaim")
    if failed is None:
        failed = trade.get("vwap_failed_reclaim_flag")
    reclaim = _float(trade.get("reclaim_count_30tick")) or _float(trade.get("vwap_reclaim_count"))
    above = _vwap_above_ratio(trade) or _float(trade.get("vwap_above_ratio_20tick"))
    if failed is None and reclaim is None and above is None:
        return None
    score = float(reclaim or 0) - (2.0 if failed else 0.0) - float(above or 0)
    return round(score, 6)


def _compute_base_features(trade: Mapping[str, Any]) -> dict[str, Optional[float]]:
    r5, r10, r15, r30 = _r(trade, 5), _r(trade, 10), _r(trade, 15), _r(trade, 30)
    hu30 = _float(trade.get("high_update_count_30m"))
    hu_sess = _float(trade.get("high_update_count_session"))
    hu_age = _high_update_age(trade)
    vwap_dev = _vwap_dev(trade)
    vwap_ext = _float(trade.get("vwap_acceleration"))
    if vwap_ext is None and vwap_dev is not None and hu_age is not None and hu_age > 0:
        vwap_ext = round(vwap_dev / hu_age, 6)
    density = None
    if hu30 is not None and hu_age is not None and hu_age > 0:
        density = round(hu30 / hu_age, 6)
    elif hu30 is not None and hu_sess is not None and hu_sess > 0:
        density = round(hu30 / hu_sess, 6)

    return {
        "A1_r30_minus_r5": round(r30 - r5, 6) if r30 is not None and r5 is not None else None,
        "A2_r15_minus_r5": round(r15 - r5, 6) if r15 is not None and r5 is not None else None,
        "A3_r30_over_r5": _safe_ratio(r30, r5),
        "A4_momentum_slope": _momentum_slope(trade),
        "B1_vwap_dev_pct": vwap_dev,
        "B2_vwap_extension_rate": vwap_ext,
        "B3_vwap_reversion_score": _vwap_reversion_score(trade),
        "C1_high_update_count_30m": hu30,
        "C2_high_update_count_session": hu_sess,
        "C3_high_update_density": density,
        "D1_board_change_5m": None,
        "D2_board_change_10m": None,
        "D3_board_decay_score": None,
    }


def _load_day_event_snaps(kabu_root: Path, day: str) -> dict[str, list[tuple[Any, float]]]:
    base = kabu_root / "results" / "small_paper" / day
    out: dict[str, list[tuple[Any, float]]] = defaultdict(list)
    if not base.is_dir():
        return {}
    for sess in sorted(base.iterdir()):
        if not sess.is_dir():
            continue
        path = sess / "small_paper_events.csv"
        if not path.is_file():
            continue
        with path.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                sym = str(row.get("symbol") or "")
                imb = _optional_float(row.get("entry_order_book_imbalance"))
                if not sym or imb is None:
                    continue
                ts = _parse_ts(str(row.get("event_time") or row.get("entry_time") or ""))
                if ts is None:
                    continue
                out[sym].append((ts, float(imb)))
    for sym in out:
        out[sym].sort(key=lambda x: x[0])
    return dict(out)


def _imb_at_or_before(snaps: Sequence[tuple[Any, float]], ts: Any) -> Optional[float]:
    best: Optional[float] = None
    for t, v in snaps:
        if t <= ts:
            best = v
        else:
            break
    return best


def _board_features(
    trade: Mapping[str, Any],
    snaps: dict[str, list[tuple[Any, float]]],
) -> dict[str, Optional[float]]:
    sym = str(trade.get("symbol") or "")
    ent = _parse_ts(str(trade.get("entry_time") or ""))
    if ent is None:
        return {"D1_board_change_5m": None, "D2_board_change_10m": None, "D3_board_decay_score": None}
    series = snaps.get(sym, [])
    if not series:
        return {"D1_board_change_5m": None, "D2_board_change_10m": None, "D3_board_decay_score": None}
    imb_now = _imb_at_or_before(series, ent) or _float(trade.get("entry_order_book_imbalance"))
    if imb_now is None:
        return {"D1_board_change_5m": None, "D2_board_change_10m": None, "D3_board_decay_score": None}
    out: dict[str, Optional[float]] = {}
    decays: list[float] = []
    for minutes, key in ((5, "D1_board_change_5m"), (10, "D2_board_change_10m")):
        past_ts = ent - timedelta(minutes=minutes)
        imb_past = _imb_at_or_before(series, past_ts)
        if imb_past is None:
            out[key] = None
        else:
            ch = round(imb_now - imb_past, 6)
            out[key] = ch
            if ch < 0:
                decays.append(-ch)
    out["D3_board_decay_score"] = round(sum(decays), 6) if decays else 0.0
    return out


def _feature_group(feature_id: str) -> str:
    for group, feats in FEATURE_GROUPS.items():
        if feature_id in feats:
            return group
    return "unknown"


def _feature_direction(slm_mean: Optional[float], sw_mean: Optional[float]) -> str:
    if slm_mean is None or sw_mean is None:
        return "unknown"
    if slm_mean > sw_mean:
        return "higher_in_stop_low_mfe"
    if slm_mean < sw_mean:
        return "lower_in_stop_low_mfe"
    return "equal"


def _rank_features(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    slm = [r for r in rows if r.get("cohort") == "stop_low_mfe"]
    sw = [r for r in rows if r.get("cohort") == "strong_winner"]
    ranking: list[dict[str, Any]] = []

    for feat in ALL_FEATURES:
        slm_vals = [float(r[feat]) for r in slm if r.get(feat) is not None]
        sw_vals = [float(r[feat]) for r in sw if r.get(feat) is not None]
        slm_miss = sum(1 for r in slm if r.get(feat) is None)
        sw_miss = sum(1 for r in sw if r.get(feat) is None)
        d = _cohens_d(slm_vals, sw_vals)
        ks = _ks_stat(slm_vals, sw_vals)
        mi = _mi_median_split(sw_vals, slm_vals) if slm_vals and sw_vals else None
        slm_mean = statistics.mean(slm_vals) if slm_vals else None
        sw_mean = statistics.mean(sw_vals) if sw_vals else None
        ranking.append(
            {
                "feature_id": feat,
                "feature_group": _feature_group(feat),
                "slm_mean": round(slm_mean, 6) if slm_mean is not None else None,
                "slm_median": round(statistics.median(slm_vals), 6) if slm_vals else None,
                "strong_winner_mean": round(sw_mean, 6) if sw_mean is not None else None,
                "strong_winner_median": round(statistics.median(sw_vals), 6) if sw_vals else None,
                "missing_rate_slm": round(slm_miss / len(slm), 4) if slm else 0.0,
                "missing_rate_sw": round(sw_miss / len(sw), 4) if sw else 0.0,
                "cohens_d": round(d, 6) if d is not None else None,
                "ks_statistic": ks,
                "mutual_information": round(mi, 6) if mi is not None else None,
                "feature_direction": _feature_direction(slm_mean, sw_mean),
            }
        )

    ranking.sort(key=lambda r: abs(float(r.get("cohens_d") or 0)), reverse=True)
    for i, row in enumerate(ranking, start=1):
        row["rank"] = i
    return ranking


def _percentile(vals: Sequence[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    idx = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
    return s[idx]


def _reject_fn(key: str, thr: float, direction: str) -> Callable[[Mapping[str, Any]], bool]:
    def fn(r: Mapping[str, Any]) -> bool:
        v = r.get(key)
        if v is None:
            return False
        return float(v) < thr if direction == "lt" else float(v) > thr

    return fn


def _best_threshold(
    key: str,
    rows: Sequence[Mapping[str, Any]],
    slm_keys: set[str],
) -> tuple[float, str, str]:
    vals = [float(r[key]) for r in rows if r.get(key) is not None]
    if not vals:
        return 0.0, f"{key}<=0", "lt"
    slm_vals = [float(r[key]) for r in rows if r.get("position_key") in slm_keys and r.get(key) is not None]
    sw_vals = [
        float(r[key])
        for r in rows
        if r.get("cohort") == "strong_winner" and r.get("position_key") not in slm_keys and r.get(key) is not None
    ]
    direction = "lt" if (statistics.mean(slm_vals) if slm_vals else 0) < (statistics.mean(sw_vals) if sw_vals else 0) else "gt"
    best_thr = _percentile(vals, PERCENTILE_CANDIDATES[0])
    best_p = PERCENTILE_CANDIDATES[0]
    best_score = -1e18

    def _rejects(v: float, thr: float) -> bool:
        return v < thr if direction == "lt" else v > thr

    for p in PERCENTILE_CANDIDATES:
        thr = _percentile(vals, p)
        blocked_slm = blocked_sw = 0
        for r in rows:
            v = r.get(key)
            if v is None or not _rejects(float(v), thr):
                continue
            if r.get("position_key") in slm_keys:
                blocked_slm += 1
            elif r.get("cohort") == "strong_winner":
                blocked_sw += 1
        score = blocked_slm - 0.75 * blocked_sw
        if score > best_score:
            best_score = score
            best_p = p
            best_thr = thr
    op = "<" if direction == "lt" else ">"
    return best_thr, f"{key}{op}{best_thr:.4f}@p{int(best_p * 100)}", direction


def _eval_pattern(
    rows: Sequence[Mapping[str, Any]],
    reject_fns: Sequence[Callable[[Mapping[str, Any]], bool]],
    *,
    pattern_id: str,
    conditions: str,
    threshold_summary: str,
    total_slm: int,
    total_sw: int,
) -> dict[str, Any]:
    slm = sw = 0
    blocked: list[Mapping[str, Any]] = []
    for r in rows:
        if not all(fn(r) for fn in reject_fns):
            continue
        blocked.append(r)
        if r.get("cohort") == "stop_low_mfe":
            slm += 1
        elif r.get("cohort") == "strong_winner":
            sw += 1
    slm_cap = round(slm / total_slm, 4) if total_slm else 0.0
    sw_fp = round(sw / total_sw, 4) if total_sw else 0.0
    blocked_pnl = round(sum(float(r.get("pnl_yen") or 0) for r in blocked), 2)
    return {
        "pattern_id": pattern_id,
        "condition_count": len(reject_fns),
        "conditions": conditions,
        "threshold_summary": threshold_summary,
        "slm_capture_rate": slm_cap,
        "strong_winner_fp_rate": sw_fp,
        "separation_score": round(slm_cap - sw_fp, 4),
        "blocked_stop_low_mfe": slm,
        "blocked_strong_winner": sw,
        "blocked_total": len(blocked),
        "blocked_pnl": blocked_pnl,
        "expected_delta": round(-blocked_pnl, 2),
    }


def _build_patterns(rows: Sequence[Mapping[str, Any]], ranking: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    slm_keys = {r["position_key"] for r in rows if r.get("cohort") == "stop_low_mfe"}
    total_slm = len(slm_keys)
    total_sw = sum(1 for r in rows if r.get("cohort") == "strong_winner")
    thresholds: dict[str, tuple[float, str, str]] = {}
    for feat in ALL_FEATURES:
        if any(r.get(feat) is not None for r in rows):
            thresholds[feat] = _best_threshold(feat, rows, slm_keys)

    patterns: list[dict[str, Any]] = []
    for feat, (thr, summary, direction) in thresholds.items():
        patterns.append(
            _eval_pattern(
                rows,
                [_reject_fn(feat, thr, direction)],
                pattern_id=f"P1_{feat}",
                conditions=summary,
                threshold_summary=summary,
                total_slm=total_slm,
                total_sw=total_sw,
            )
        )

    top_feats = [r["feature_id"] for r in ranking[:8] if r.get("feature_id") in thresholds]
    for i, f1 in enumerate(top_feats):
        for f2 in top_feats[i + 1 :]:
            t1, s1, d1 = thresholds[f1]
            t2, s2, d2 = thresholds[f2]
            patterns.append(
                _eval_pattern(
                    rows,
                    [_reject_fn(f1, t1, d1), _reject_fn(f2, t2, d2)],
                    pattern_id=f"P2_{f1}_{f2}",
                    conditions=f"{s1} AND {s2}",
                    threshold_summary=f"{s1};{s2}",
                    total_slm=total_slm,
                    total_sw=total_sw,
                )
            )

    patterns.sort(
        key=lambda p: (float(p.get("separation_score") or 0), int(p.get("blocked_stop_low_mfe") or 0)),
        reverse=True,
    )
    for i, p in enumerate(patterns, start=1):
        p["rank_by_separation"] = i
    return patterns


def _verdict(
    *,
    ranking: Sequence[Mapping[str, Any]],
    best_pat: Mapping[str, Any],
    phase483_top_d: float = 0.3758,
) -> str:
    top = ranking[0] if ranking else {}
    top_d = abs(float(top.get("cohens_d") or 0))
    sep = float(best_pat.get("separation_score") or 0)
    exp_delta = float(best_pat.get("expected_delta") or 0)
    blocked_sw = int(best_pat.get("blocked_strong_winner") or 0)

    if top_d > phase483_top_d + 0.05 and sep >= 0.12:
        return "new_feature_found"
    if top_d >= 0.30 and sep >= 0.15 and exp_delta > 0 and blocked_sw <= 10:
        return "new_feature_found"
    if top_d < 0.25 and sep < 0.12:
        return "needs_tick_level_feature"
    if top_d >= 0.25 or sep >= 0.12:
        return "new_feature_found"
    return "needs_tick_level_feature"


def run_phase484(*, repo_root: Path, parallel: bool = False, max_workers: int = 2) -> dict[str, Any]:
    del parallel, max_workers
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)
    trade_by_key = {_position_key(t): t for t in replay_pool}

    st = simulate_capacity_replay(
        replay_pool,
        runtime_shadows,
        mode="phase484_pbv2",
        entry_block_fn=_entry_block(pass_pbv2),
        baseline_accepted_keys=set(),
    )
    baseline_rows = _build_trade_rows(st, trade_by_key=trade_by_key, price_idx=price_idx)
    p80 = _pnl_p80(baseline_rows)
    for r in baseline_rows:
        r["cohort"] = _cohort_label(r, pnl_threshold=p80)

    days_needed = sorted({str(r.get("day") or "")[:8] for r in baseline_rows if r.get("day")})
    day_snaps: dict[str, dict[str, list[tuple[Any, float]]]] = {}
    for day in days_needed:
        day_snaps[day] = _load_day_event_snaps(kabu, day)

    discovery_rows: list[dict[str, Any]] = []
    for r in baseline_rows:
        tr = r.get("trade") or r
        feats = _compute_base_features(tr)
        day = str(r.get("day") or "")[:8]
        feats.update(_board_features(tr, day_snaps.get(day, {})))
        discovery_rows.append(
            {
                "position_key": r.get("position_key"),
                "cohort": r.get("cohort"),
                "symbol": r.get("symbol"),
                "day": r.get("day"),
                "pnl_yen": r.get("pnl_yen"),
                **feats,
            }
        )

    ranking = _rank_features(discovery_rows)
    patterns = _build_patterns(discovery_rows, ranking)
    best_pat = patterns[0] if patterns else {}
    best_2 = next((p for p in patterns if int(p.get("condition_count") or 0) == 2), best_pat)
    top = ranking[0] if ranking else {}
    verdict = _verdict(ranking=ranking, best_pat=best_pat)

    mandatory = {
        "1_strongest_feature": top.get("feature_id"),
        "1b_cohens_d": top.get("cohens_d"),
        "1c_ks": top.get("ks_statistic"),
        "1d_mi": top.get("mutual_information"),
        "2_strongest_2condition_pattern": {
            "pattern_id": best_2.get("pattern_id"),
            "conditions": best_2.get("conditions"),
            "separation_score": best_2.get("separation_score"),
        },
        "3_stop_low_mfe_capture": best_pat.get("slm_capture_rate"),
        "4_winner_capture": best_pat.get("strong_winner_fp_rate"),
        "5_expected_delta": best_pat.get("expected_delta"),
        "6_runtime_candidate": verdict == "new_feature_found"
        and float(best_pat.get("expected_delta") or 0) > 0
        and int(best_pat.get("blocked_strong_winner") or 0) <= 12,
        "7_next_actions": _next_actions(verdict, top, best_pat, best_2),
        "verdict": verdict,
        "stop_low_mfe_count": sum(1 for r in discovery_rows if r.get("cohort") == "stop_low_mfe"),
        "strong_winner_count": sum(1 for r in discovery_rows if r.get("cohort") == "strong_winner"),
        "top20_features": ranking[:TOP_N],
        "group_best": _group_best(ranking),
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_discovery_rows": discovery_rows,
        "_ranking_rows": ranking,
        "_pattern_rows": patterns,
    }


def _group_best(ranking: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for group in FEATURE_GROUPS:
        feats = [r for r in ranking if r.get("feature_group") == group]
        if feats:
            out[group] = {"feature_id": feats[0].get("feature_id"), "cohens_d": feats[0].get("cohens_d")}
    return out


def _next_actions(
    verdict: str,
    top: Mapping[str, Any],
    best_pat: Mapping[str, Any],
    best_2: Mapping[str, Any],
) -> list[str]:
    actions = [f"Verdict: {verdict}"]
    actions.append(f"Strongest feature: {top.get('feature_id')} d={top.get('cohens_d')}")
    if verdict == "new_feature_found":
        actions.append(f"Lead 2-condition: {best_2.get('pattern_id')} sep={best_2.get('separation_score')}")
        if float(best_pat.get("expected_delta") or 0) > 0:
            actions.append("Run Phase485 CAP replay on lead pattern before runtime")
        else:
            actions.append("Separation found but expected_delta negative - shadow only")
    else:
        actions.append("Existing derived features insufficient - need tick-level board/VWAP dynamics")
        actions.append("Consider intraday board imbalance stream or sub-minute momentum decay")
    return actions


@dataclass
class Phase484Job:
    repo_root: Path
    parallel: bool = False
    max_workers: int = 2

    def run(self) -> dict[str, Any]:
        return run_phase484(repo_root=self.repo_root, parallel=self.parallel, max_workers=self.max_workers)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        paths = {
            "ranking": reports / "phase484_stop_low_mfe_feature_ranking.csv",
            "patterns": reports / "phase484_stop_low_mfe_patterns.csv",
            "discovery": reports / "phase484_stop_low_mfe_feature_discovery.csv",
            "summary": reports / "phase484_summary.json",
        }
        ranking = list(result.get("_ranking_rows") or [])
        _write_csv(paths["ranking"], RANKING_FIELDS, ranking)
        _write_csv(paths["patterns"], PATTERN_FIELDS, list(result.get("_pattern_rows") or []))
        _write_csv(paths["discovery"], DISCOVERY_FIELDS, list(result.get("_discovery_rows") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        report = doc_root / "docs" / "operations" / "phase484_stop_low_mfe_feature_discovery_tournament.md"
        self._write_report(report, result, ranking[:TOP_N])
        paths["report"] = report
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any], top20: Sequence[Mapping[str, Any]]) -> None:
        m = result.get("mandatory_answers") or {}
        patterns = list(result.get("_pattern_rows") or [])[:8]
        lines = [
            "# Phase484 — Stop Low MFE Feature Discovery Tournament",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')}-{result.get('period_end')}",
            "",
            "## Mandatory answers",
            "",
            f"1. Strongest feature: **{m.get('1_strongest_feature')}** (d={m.get('1b_cohens_d')})",
            f"2. Best 2-condition: **{m.get('2_strongest_2condition_pattern')}**",
            f"3. SLM capture: **{m.get('3_stop_low_mfe_capture')}**",
            f"4. Winner capture: **{m.get('4_winner_capture')}**",
            f"5. Expected delta: **{m.get('5_expected_delta')}**",
            f"6. Runtime candidate: **{m.get('6_runtime_candidate')}**",
            f"7. Next actions: {m.get('7_next_actions')}",
            "",
            "## Top 20 features",
            "",
        ]
        for r in top20:
            lines.append(
                f"- **{r.get('rank')}. {r.get('feature_id')}** ({r.get('feature_group')}): "
                f"d={r.get('cohens_d')} KS={r.get('ks_statistic')} MI={r.get('mutual_information')}"
            )
        lines.extend(["", "## Top patterns", ""])
        for p in patterns:
            lines.append(
                f"- **{p.get('pattern_id')}**: sep {p.get('separation_score')} "
                f"slm {p.get('blocked_stop_low_mfe')} sw {p.get('blocked_strong_winner')} "
                f"delta {p.get('expected_delta')}"
            )
        lines.extend(["", f"**Verdict:** `{result.get('verdict')}`", ""])
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
