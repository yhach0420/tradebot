"""
Phase497 — Near Day High Decomposition (research only).

Winner vs loser feature comparison within MST_near_day_high flagged PBv2 accepted trades.
No Runtime / YAML / Entry / Exit / Order changes.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _build_price_index_to, _now_iso
from research.phase463_trend_pullback_population_tournament import (
    _board_bucket,
    _fill_close_proxy_shadows,
)
from research.phase464_pre_gate_archetype_audit import _vwap_above_ratio
from research.phase465b_trend_gate_redesign import _cohens_d, _high_update_age, _mi_median_split
from research.phase473_trend_entry_architecture import _entry_block, _rise, pass_pbv2
from research.phase476_pre_breakout_gate_replay import _ensure_enriched, _load_replay_pool
from research.phase483_stop_low_mfe_root_cause_audit import _ks_stat
from research.phase484_stop_low_mfe_feature_discovery import (
    _board_features,
    _compute_base_features,
    _load_day_event_snaps,
)
from research.phase488_current_runtime_replay import (
    REPLAY_MODE,
    _filter_period,
    _filter_replay_pool_safe,
    _simulate_runtime_replay,
)
from research.phase493_global_entry_failure_audit import (
    PERIOD_END,
    PERIOD_START,
    _enrich_trade_row,
    _exit_reason,
    _is_loser,
    _is_winner,
)
from research.phase496_mst_near_high_optimization import _distance_from_day_high
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

NEAR_HIGH_THRESHOLD = 1.0
TOP_N = 10

# Group W: near-high profitable (strict winner OR pnl>0 non-loser)
# Group L: near-high loser (stop_hit / no_progress)

EXISTING_FEATURES = (
    "r5", "r10", "r15", "r30",
    "vwap_dev_pct", "vwap_structure_score",
    "board_tier_ordinal", "board_imbalance",
    "high_update_age", "high_update_count",
    "day_high_distance", "momentum",
)

NEW_FEATURES = (
    "high_age_vs_distance",
    "time_since_last_high",
    "recent_high_failure_count",
    "vwap_above_duration",
    "r5_after_high",
    "r10_after_high",
    "board_change_after_high",
    "high_break_retry_count",
    "high_stall_duration",
    "near_high_decay_score",
)

ALL_FEATURES = EXISTING_FEATURES + NEW_FEATURES

DISCOVERY_FIELDS = [
    "position_key", "symbol", "day", "cohort", "exit_reason", "pnl_yen", "mfe_pct",
    *ALL_FEATURES,
]

RANKING_FIELDS = [
    "rank", "feature_id", "feature_type", "is_new",
    "group_w_mean", "group_w_median", "group_l_mean", "group_l_median",
    "missing_rate_w", "missing_rate_l",
    "cohens_d", "ks_statistic", "mutual_information", "feature_direction",
    "loo_min_abs_d", "loo_median_abs_d", "loo_stable_days_pct", "loo_robust",
    "exclude_6976_abs_d", "exclude_top_day_abs_d",
]


def _float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _board_tier_ordinal(tier: str) -> Optional[float]:
    t = str(tier or "").lower()
    if "high" in t:
        return 2.0
    if "mid" in t:
        return 1.0
    if "low" in t:
        return 0.0
    return None


def _is_near_high(row: Mapping[str, Any]) -> bool:
    d = _float(row.get("day_high_distance"))
    if d is None:
        tr = row.get("_trade") or row
        d = _distance_from_day_high(tr)
    return d is not None and d <= NEAR_HIGH_THRESHOLD


def _is_group_w(row: Mapping[str, Any]) -> bool:
    if _is_loser(row):
        return False
    if _is_winner(row):
        return True
    return float(row.get("pnl_yen") or 0) > 0


def _is_group_l(row: Mapping[str, Any]) -> bool:
    return _is_loser(row)


def _feature_direction(wm: Optional[float], lm: Optional[float]) -> str:
    if wm is None or lm is None:
        return "unknown"
    if lm > wm:
        return "higher_in_loser"
    if lm < wm:
        return "lower_in_loser"
    return "equal"


def _compute_derived(
    row: Mapping[str, Any],
    tr: Mapping[str, Any],
    *,
    board_feats: Mapping[str, Optional[float]],
) -> dict[str, Optional[float]]:
    dhd = _float(row.get("day_high_distance"))
    age = _float(row.get("high_update_age"))
    hu30 = _float(row.get("high_update_count"))
    r5 = _float(row.get("r5"))
    r10 = _float(row.get("r10"))
    r15 = _float(row.get("r15"))
    vwap_above = _vwap_above_ratio(tr)
    cat = _float(tr.get("consecutive_above_ticks"))
    bc5 = board_feats.get("D1_board_change_5m")
    bc10 = board_feats.get("D2_board_change_10m")

    out: dict[str, Optional[float]] = {}
    out["high_age_vs_distance"] = (
        round(age / max(dhd or 0.05, 0.05), 6) if age is not None and dhd is not None else None
    )
    out["time_since_last_high"] = age
    out["recent_high_failure_count"] = (
        round(max(0.0, (hu30 or 0) - 1.0), 6) if hu30 is not None else None
    )
    out["vwap_above_duration"] = (
        round((cat or 0) * (vwap_above or 0), 6)
        if cat is not None and vwap_above is not None
        else None
    )
    out["r5_after_high"] = r5
    out["r10_after_high"] = r10
    out["board_change_after_high"] = (
        round(bc10 - bc5, 6) if bc10 is not None and bc5 is not None else None
    )
    out["high_break_retry_count"] = hu30
    if age is not None and r10 is not None:
        if r10 <= 0:
            out["high_stall_duration"] = round(age / max(abs(r10), 0.05), 6)
        else:
            out["high_stall_duration"] = round(age * (1.0 - min(r10, 2.0) / 2.0), 6)
    else:
        out["high_stall_duration"] = None
    out["near_high_decay_score"] = (
        round((r15 - r5) / max(dhd or 0.05, 0.05), 6)
        if r15 is not None and r5 is not None and dhd is not None
        else None
    )
    return out


def _rank_features(
    rows: Sequence[Mapping[str, Any]],
    *,
    days: Sequence[str],
) -> list[dict[str, Any]]:
    w_rows = [r for r in rows if r.get("cohort") == "W"]
    l_rows = [r for r in rows if r.get("cohort") == "L"]
    ranking: list[dict[str, Any]] = []

    day_pnl = defaultdict(float)
    for r in rows:
        day_pnl[str(r["day"])] += float(r.get("pnl_yen") or 0)
    top_day = max(day_pnl, key=lambda d: abs(day_pnl[d])) if day_pnl else ""

    for feat in ALL_FEATURES:
        wv = [float(r[feat]) for r in w_rows if r.get(feat) is not None]
        lv = [float(r[feat]) for r in l_rows if r.get(feat) is not None]
        if not wv and not lv:
            continue
        wm = statistics.mean(wv) if wv else None
        lm = statistics.mean(lv) if lv else None
        d = _cohens_d(lv, wv)
        ks = _ks_stat(lv, wv)
        mi = _mi_median_split(wv, lv) if wv and lv else None

        loo_ds: list[float] = []
        stable = 0
        for day in days:
            sub_w = [r for r in w_rows if r.get("day") != day]
            sub_l = [r for r in l_rows if r.get("day") != day]
            sw = [float(r[feat]) for r in sub_w if r.get(feat) is not None]
            sl = [float(r[feat]) for r in sub_l if r.get(feat) is not None]
            if len(sw) < 2 or len(sl) < 2:
                continue
            ld = abs(float(_cohens_d(sl, sw) or 0))
            loo_ds.append(ld)
            if ld >= 0.15:
                stable += 1
        n_loo = len(loo_ds) or 1
        loo_min = min(loo_ds) if loo_ds else 0.0
        loo_med = statistics.median(loo_ds) if loo_ds else 0.0

        ex6976_w = [r for r in w_rows if str(r.get("symbol")) != "6976"]
        ex6976_l = [r for r in l_rows if str(r.get("symbol")) != "6976"]
        ex6976_d = abs(
            float(
                _cohens_d(
                    [float(r[feat]) for r in ex6976_l if r.get(feat) is not None],
                    [float(r[feat]) for r in ex6976_w if r.get(feat) is not None],
                )
                or 0
            )
        )

        ex_day_w = [r for r in w_rows if str(r.get("day")) != top_day]
        ex_day_l = [r for r in l_rows if str(r.get("day")) != top_day]
        ex_day_d = abs(
            float(
                _cohens_d(
                    [float(r[feat]) for r in ex_day_l if r.get(feat) is not None],
                    [float(r[feat]) for r in ex_day_w if r.get(feat) is not None],
                )
                or 0
            )
        )

        ranking.append(
            {
                "feature_id": feat,
                "feature_type": "new" if feat in NEW_FEATURES else "existing",
                "is_new": feat in NEW_FEATURES,
                "group_w_mean": round(wm, 6) if wm is not None else None,
                "group_w_median": round(statistics.median(wv), 6) if wv else None,
                "group_l_mean": round(lm, 6) if lm is not None else None,
                "group_l_median": round(statistics.median(lv), 6) if lv else None,
                "missing_rate_w": round(sum(1 for r in w_rows if r.get(feat) is None) / max(1, len(w_rows)), 4),
                "missing_rate_l": round(sum(1 for r in l_rows if r.get(feat) is None) / max(1, len(l_rows)), 4),
                "cohens_d": d,
                "ks_statistic": ks,
                "mutual_information": mi,
                "feature_direction": _feature_direction(wm, lm),
                "loo_min_abs_d": round(loo_min, 6),
                "loo_median_abs_d": round(loo_med, 6),
                "loo_stable_days_pct": round(stable / n_loo, 4),
                "loo_robust": loo_min >= 0.12 and abs(float(d or 0)) >= 0.20,
                "exclude_6976_abs_d": round(ex6976_d, 6),
                "exclude_top_day_abs_d": round(ex_day_d, 6),
            }
        )

    ranking.sort(key=lambda r: abs(float(r.get("cohens_d") or 0)), reverse=True)
    for i, row in enumerate(ranking, start=1):
        row["rank"] = i
    return ranking


def _pattern_summary(rows: Sequence[Mapping[str, Any]], *, cohort: str, top_feats: Sequence[str]) -> dict[str, Any]:
    grp = [r for r in rows if r.get("cohort") == cohort]
    if not grp:
        return {"cohort": cohort, "count": 0}
    out: dict[str, Any] = {
        "cohort": cohort,
        "count": len(grp),
        "total_pnl": round(sum(float(r["pnl_yen"]) for r in grp), 2),
        "stop_hit_rate": round(sum(1 for r in grp if _exit_reason(r) == "stop_hit") / len(grp), 4),
        "median_mfe": round(
            statistics.median([float(r["mfe_pct"]) for r in grp if r.get("mfe_pct") is not None]), 4
        )
        if any(r.get("mfe_pct") is not None for r in grp)
        else None,
    }
    for feat in top_feats[:5]:
        vals = [_float(r.get(feat)) for r in grp]
        vals_n = [v for v in vals if v is not None]
        out[f"median_{feat}"] = round(statistics.median(vals_n), 4) if vals_n else None
    return out


def _verdict(
    *,
    ranking: Sequence[Mapping[str, Any]],
    top_new: Sequence[Mapping[str, Any]],
    dep_6976: bool,
    loo_ok: bool,
) -> str:
    if not ranking:
        return "no_additional_signal"
    top = ranking[0]
    top_d = abs(float(top.get("cohens_d") or 0))
    top_new_d = abs(float(top_new[0].get("cohens_d") or 0)) if top_new else 0.0
    if dep_6976 and top_d > 0.5:
        return "overfit_feature"
    if (top_d >= 0.28 or top_new_d >= 0.25) and loo_ok:
        return "new_feature_found"
    if top_d < 0.18:
        return "no_additional_signal"
    if not loo_ok:
        return "overfit_feature"
    return "new_feature_found" if top_new_d >= 0.20 else "no_additional_signal"


def run_phase497(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    reports = resolve_reports_dir(repo_root)
    price_idx = _build_price_index_to(kabu, period_end=PERIOD_END)

    replay_pool, runtime_shadows = _load_replay_pool(reports)
    replay_pool = _filter_period(replay_pool, start=PERIOD_START, end=PERIOD_END)
    runtime_shadows = _fill_close_proxy_shadows(replay_pool, runtime_shadows, price_idx=price_idx)
    replay_pool = _filter_replay_pool_safe(replay_pool, runtime_shadows)
    _ensure_enriched(replay_pool, price_idx=price_idx)

    baseline_state = _simulate_runtime_replay(
        replay_pool,
        runtime_shadows,
        mode=f"{REPLAY_MODE}_phase497",
        entry_block_fn=_entry_block(pass_pbv2),
        initial_equity=1_500_000.0,
    )

    days_needed = sorted({str(log.get("day") or "")[:8] for log in baseline_state.trade_log})
    day_snaps = {day: _load_day_event_snaps(kabu, day) for day in days_needed}

    rows: list[dict[str, Any]] = []
    for log in baseline_state.trade_log:
        base = _enrich_trade_row(log)
        tr = dict(base.get("_trade") or {})
        base["day_high_distance"] = _float(base.get("day_high_distance")) or _distance_from_day_high(tr)
        if not _is_near_high(base):
            continue
        if _is_group_w(base):
            cohort = "W"
        elif _is_group_l(base):
            cohort = "L"
        else:
            continue

        base_feats = _compute_base_features(tr)
        board_feats = _board_features(tr, day_snaps.get(str(base["day"])[:8], {}))
        r5, r10, r15, r30 = _rise(tr, 5), _rise(tr, 10), _rise(tr, 15), _rise(tr, 30)
        tier = _board_bucket(tr)

        rec = {
            "position_key": base["position_key"],
            "symbol": base["symbol"],
            "day": base["day"],
            "cohort": cohort,
            "exit_reason": base.get("exit_reason"),
            "pnl_yen": base.get("pnl_yen"),
            "mfe_pct": base.get("mfe_pct"),
            "r5": r5,
            "r10": r10,
            "r15": r15,
            "r30": r30,
            "vwap_dev_pct": base_feats.get("B1_vwap_dev_pct"),
            "vwap_structure_score": _float(tr.get("vwap_structure_score")),
            "board_tier_ordinal": _board_tier_ordinal(tier),
            "board_imbalance": base.get("board_imbalance"),
            "high_update_age": _high_update_age(tr),
            "high_update_count": _float(tr.get("high_update_count_30m")),
            "day_high_distance": base.get("day_high_distance"),
            "momentum": _float(tr.get("momentum_continuation_score")),
        }
        rec.update(_compute_derived(rec, tr, board_feats=board_feats))
        rows.append(rec)

    days = sorted({str(r["day"]) for r in rows})
    ranking = _rank_features(rows, days=days)
    new_ranked = [r for r in ranking if r.get("is_new")]
    # Ensure all 10 derived features appear in ranking output
    ranked_ids = {r["feature_id"] for r in new_ranked}
    for fid in NEW_FEATURES:
        if fid not in ranked_ids:
            new_ranked.append(
                {
                    "feature_id": fid,
                    "feature_type": "new",
                    "is_new": True,
                    "cohens_d": None,
                    "feature_direction": "insufficient_data",
                }
            )
    new_ranked.sort(key=lambda r: abs(float(r.get("cohens_d") or 0)), reverse=True)
    top10_new = new_ranked[:TOP_N]
    for i, row in enumerate(top10_new, start=1):
        row["rank"] = i

    top_feat = ranking[0] if ranking else {}
    top_new = top10_new[0] if top10_new else {}
    top5_names = [r["feature_id"] for r in ranking[:5]]

    w_pattern = _pattern_summary(rows, cohort="W", top_feats=top5_names)
    l_pattern = _pattern_summary(rows, cohort="L", top_feats=top5_names)

    sym6976 = [r for r in rows if str(r.get("symbol")) == "6976"]
    dep_6976 = bool(sym6976) and abs(float(top_feat.get("cohens_d") or 0)) > 0.3 and abs(
        float(top_feat.get("exclude_6976_abs_d") or 0) - float(top_feat.get("cohens_d") or 0)
    ) > 0.15

    loo_ok = float(top_feat.get("loo_stable_days_pct") or 0) >= 0.6

    # Loser / winner narrative patterns
    l_med = l_pattern
    w_med = w_pattern
    loser_pattern = (
        f"elevated {top_feat.get('feature_id')} (L median vs W), "
        f"stop_hit_rate={l_med.get('stop_hit_rate')}, "
        f"low mfe median={l_med.get('median_mfe')}"
    )
    winner_pattern = (
        f"lower near-high decay / fresher high context, "
        f"positive r5/r10 medians, median_mfe={w_med.get('median_mfe')}"
    )

    verdict = _verdict(ranking=ranking, top_new=top10_new, dep_6976=dep_6976, loo_ok=loo_ok)

    mandatory = {
        "1_max_separation_feature": top_feat.get("feature_id"),
        "1_max_cohens_d": top_feat.get("cohens_d"),
        "2_loser_primary_pattern": loser_pattern,
        "3_winner_primary_pattern": winner_pattern,
        "4_reproducibility": (
            f"LOO stable {top_feat.get('loo_stable_days_pct')} min_d={top_feat.get('loo_min_abs_d')}"
            if loo_ok
            else f"LOO weak stable_pct={top_feat.get('loo_stable_days_pct')}"
        ),
        "5_6976_dependent": dep_6976,
        "6_overfit_risk": "high" if dep_6976 or not loo_ok else "moderate" if len(rows) < 40 else "low",
        "7_top10_new_features": [r["feature_id"] for r in top10_new],
        "8_replay_candidate": (
            f"soft gate on {top_new.get('feature_id')} (Phase496 5-10% band + decomposition threshold)"
            if verdict == "new_feature_found"
            else "none — existing near-high distance sufficient"
        ),
        "9_shadow_candidate": (
            [r["feature_id"] for r in top10_new[:3]]
            if top10_new
            else ["near_high_decay_score", "high_age_vs_distance"]
        ),
        "10_runtime_candidate": False,
        "11_next_action": (
            f"Forward-shadow log top3: {[r['feature_id'] for r in top10_new[:3]]}; "
            f"replay Phase498 combined gate {top_new.get('feature_id')} + dhd<=0.33"
            if verdict == "new_feature_found"
            else "Continue dhd soft gate shadow only; decomposition adds marginal signal"
        ),
        "verdict": verdict,
        "near_high_w_count": sum(1 for r in rows if r.get("cohort") == "W"),
        "near_high_l_count": sum(1 for r in rows if r.get("cohort") == "L"),
        "w_pattern_detail": w_pattern,
        "l_pattern_detail": l_pattern,
    }

    return {
        "generated_at": _now_iso(),
        "period_start": PERIOD_START,
        "period_end": PERIOD_END,
        "near_high_threshold": NEAR_HIGH_THRESHOLD,
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "_discovery_rows": rows,
        "_ranking": ranking,
        "_top10_new": top10_new,
    }


@dataclass
class Phase497Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase497(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        doc_root = self.repo_root / "kabu_native"
        if not (doc_root / "docs").is_dir():
            doc_root = self.repo_root
        paths = {
            "discovery": reports / "phase497_near_high_decomposition.csv",
            "ranking": reports / "phase497_near_high_feature_ranking.csv",
            "summary": reports / "phase497_summary.json",
            "report": doc_root / "docs" / "operations" / "phase497_near_high_decomposition.md",
        }
        _write_csv(paths["discovery"], DISCOVERY_FIELDS, list(result.get("_discovery_rows") or []))
        _write_csv(paths["ranking"], RANKING_FIELDS, list(result.get("_top10_new") or []))
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        payload["all_feature_ranking"] = list(result.get("_ranking") or [])
        paths["summary"].write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        self._write_report(paths["report"], result)
        return paths

    def _write_report(self, report: Path, result: Mapping[str, Any]) -> None:
        m = result.get("mandatory_answers") or {}
        lines = [
            "# Phase497 — Near Day High Decomposition",
            "",
            f"**Verdict:** `{result.get('verdict')}`",
            f"**Period:** {result.get('period_start')} — {result.get('period_end')}",
            f"**Near-high filter:** day_high_distance <= {result.get('near_high_threshold')}",
            "",
            "## 必須回答",
            "",
        ]
        for key in (
            "1_max_separation_feature", "1_max_cohens_d", "2_loser_primary_pattern",
            "3_winner_primary_pattern", "4_reproducibility", "5_6976_dependent",
            "6_overfit_risk", "7_top10_new_features", "8_replay_candidate",
            "9_shadow_candidate", "10_runtime_candidate", "11_next_action",
        ):
            lines.append(f"- **{key}:** {m.get(key)}")
        lines.extend(
            [
                "",
                f"- **near_high W/L:** {m.get('near_high_w_count')} W / {m.get('near_high_l_count')} L",
                "",
                "## Top10 new features",
                "",
                "```json",
                json.dumps(result.get("_top10_new"), indent=2, ensure_ascii=False, default=str),
                "```",
            ]
        )
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text("\n".join(lines), encoding="utf-8")
