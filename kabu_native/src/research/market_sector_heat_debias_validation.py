"""
Phase248-SectorHeat-Debias-Validation: debias Phase247 Top3 sector heat advantage.

Observation only — no Runtime / Universe / Entry changes.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import (
    MarketSectorHeatObservation,
    TOP_SECTOR_COUNT,
    _float,
    _int,
    _write_csv,
)
from research.market_sector_heat_diagnostics import (
    build_baseline_comparison,
    load_phase246_inputs,
    next_day_return,
    sector_rows_by_day_from_csv,
)

JST = ZoneInfo("Asia/Tokyo")

DOMINANT_SECTORS = ("非鉄金属", "情報・通信業", "電気機器")
SECTOR_CAP_PCTS = (0.20, 0.30, 0.40)

LEAVE_ONE_OUT_FIELDS = [
    "excluded_sector",
    "signal_count",
    "top3_next_day_positive_rate",
    "top3_vs_all_delta",
    "avg_return_delta",
    "top3_next_day_avg_return_pct",
    "all_sectors_next_day_positive_rate",
]

CAPPED_VALIDATION_FIELDS = [
    "cap_pct",
    "max_adoptions_per_sector",
    "signal_count",
    "continuation_rate",
    "avg_return_pct",
    "all_sectors_next_day_positive_rate",
    "baseline_positive_rate_delta",
    "avg_return_delta_pct",
]

RANK_HEAT_PROFILE_FIELDS = [
    "rank",
    "signal_count",
    "signal_day_heat_score_avg",
    "signal_day_return_pct_avg",
    "signal_day_pm_return_pct_1400_1530_avg",
    "next_day_return_pct_avg",
    "next_day_positive_rate",
]


def _validation_day_stats(
    validation_day: str,
    sector_rows_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, Optional[float]]:
    day_rows = sector_rows_by_day.get(validation_day) or {}
    rets = [_float(row.get("daily_return_pct")) for row in day_rows.values()]
    rets = [r for r in rets if r is not None]
    if not rets:
        return {"median": None, "mean": None, "positive_rate": None}
    return {
        "median": round(statistics.median(rets), 4),
        "mean": round(statistics.mean(rets), 4),
        "positive_rate": round(sum(1 for r in rets if r > 0) / len(rets), 4),
    }


def build_sector_neutral_validation(
    tomorrow_top3: Sequence[Mapping[str, Any]],
    *,
    sector_rows_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    beat_median = 0
    beat_mean = 0
    total = 0
    excess_median: list[float] = []
    excess_mean: list[float] = []
    daily_rows: list[dict[str, Any]] = []

    by_validation_day: dict[str, list[dict[str, Any]]] = {}
    for row in tomorrow_top3:
        sector = str(row.get("sector_33_name") or "")
        validation_day = str(row.get("validation_day") or "")
        if not sector or not validation_day:
            continue
        ret = next_day_return(sector, validation_day, sector_rows_by_day)
        if ret is None:
            continue
        by_validation_day.setdefault(validation_day, []).append(
            {"sector": sector, "next_day_return_pct": ret}
        )

    for validation_day in sorted(by_validation_day):
        stats = _validation_day_stats(validation_day, sector_rows_by_day)
        median = stats["median"]
        mean = stats["mean"]
        if median is None or mean is None:
            continue
        day_beats_median = 0
        day_beats_mean = 0
        day_total = 0
        for item in by_validation_day[validation_day]:
            ret = float(item["next_day_return_pct"])
            total += 1
            day_total += 1
            excess_median.append(ret - median)
            excess_mean.append(ret - mean)
            if ret > median:
                beat_median += 1
                day_beats_median += 1
            if ret > mean:
                beat_mean += 1
                day_beats_mean += 1
        daily_rows.append(
            {
                "validation_day": validation_day,
                "all_sector_median_pct": median,
                "all_sector_mean_pct": mean,
                "top3_signal_count": day_total,
                "beat_median_count": day_beats_median,
                "beat_mean_count": day_beats_mean,
                "beat_median_rate": round(day_beats_median / day_total, 4) if day_total else None,
                "beat_mean_rate": round(day_beats_mean / day_total, 4) if day_total else None,
                "avg_excess_vs_median_pct": round(
                    statistics.mean([float(x["next_day_return_pct"]) - median for x in by_validation_day[validation_day]]),
                    4,
                ),
                "avg_excess_vs_mean_pct": round(
                    statistics.mean([float(x["next_day_return_pct"]) - mean for x in by_validation_day[validation_day]]),
                    4,
                ),
            }
        )

    return {
        "signal_count": total,
        "beat_median_rate": round(beat_median / total, 4) if total else None,
        "beat_mean_rate": round(beat_mean / total, 4) if total else None,
        "avg_excess_vs_median_pct": round(statistics.mean(excess_median), 4) if excess_median else None,
        "avg_excess_vs_mean_pct": round(statistics.mean(excess_mean), 4) if excess_mean else None,
        "by_validation_day": daily_rows,
    }


def filter_top3_excluding_sector(
    tomorrow_top3: Sequence[Mapping[str, Any]],
    excluded_sector: Optional[str],
) -> list[dict[str, Any]]:
    if not excluded_sector:
        return [dict(row) for row in tomorrow_top3]
    return [
        dict(row)
        for row in tomorrow_top3
        if str(row.get("sector_33_name") or "") != excluded_sector
    ]


def build_leave_one_sector_out_rows(
    tomorrow_top3: Sequence[Mapping[str, Any]],
    *,
    sector_rows_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
    dominant_sectors: Sequence[str] = DOMINANT_SECTORS,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    candidates = ["__none__", *dominant_sectors]
    seen: set[str] = set()
    for excluded in candidates:
        if excluded in seen:
            continue
        seen.add(excluded)
        excluded_sector = None if excluded == "__none__" else excluded
        filtered = filter_top3_excluding_sector(tomorrow_top3, excluded_sector)
        baseline = build_baseline_comparison(filtered, sector_rows_by_day=sector_rows_by_day)
        rows.append(
            {
                "excluded_sector": excluded if excluded != "__none__" else "none",
                "signal_count": len(filtered),
                "top3_next_day_positive_rate": baseline.get("top3_next_day_positive_rate"),
                "top3_vs_all_delta": baseline.get("top3_vs_all_sectors_positive_rate_delta"),
                "avg_return_delta": baseline.get("top3_vs_all_sectors_avg_return_delta"),
                "top3_next_day_avg_return_pct": baseline.get("top3_next_day_avg_return_pct"),
                "all_sectors_next_day_positive_rate": baseline.get("all_sectors_next_day_positive_rate"),
            }
        )
    return rows


def apply_sector_adoption_cap(
    tomorrow_top3: Sequence[Mapping[str, Any]],
    cap_pct: float,
) -> tuple[list[dict[str, Any]], int]:
    rows = sorted(
        [dict(row) for row in tomorrow_top3],
        key=lambda r: (str(r.get("signal_day") or ""), _int(r.get("rank"))),
    )
    total = len(rows)
    max_per_sector = max(1, int(total * cap_pct))
    counts: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for row in rows:
        sector = str(row.get("sector_33_name") or "")
        if not sector:
            continue
        if counts.get(sector, 0) >= max_per_sector:
            continue
        counts[sector] = counts.get(sector, 0) + 1
        out.append(row)
    return out, max_per_sector


def build_capped_sector_validation_rows(
    tomorrow_top3: Sequence[Mapping[str, Any]],
    *,
    sector_rows_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
    cap_pcts: Sequence[float] = SECTOR_CAP_PCTS,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    uncapped_baseline = build_baseline_comparison(
        tomorrow_top3, sector_rows_by_day=sector_rows_by_day
    )
    rows.append(
        {
            "cap_pct": "uncapped",
            "max_adoptions_per_sector": None,
            "signal_count": len(tomorrow_top3),
            "continuation_rate": uncapped_baseline.get("top3_next_day_positive_rate"),
            "avg_return_pct": uncapped_baseline.get("top3_next_day_avg_return_pct"),
            "all_sectors_next_day_positive_rate": uncapped_baseline.get(
                "all_sectors_next_day_positive_rate"
            ),
            "baseline_positive_rate_delta": uncapped_baseline.get(
                "top3_vs_all_sectors_positive_rate_delta"
            ),
            "avg_return_delta_pct": uncapped_baseline.get("top3_vs_all_sectors_avg_return_delta"),
        }
    )

    for cap_pct in cap_pcts:
        capped, max_adoptions = apply_sector_adoption_cap(tomorrow_top3, cap_pct)
        baseline = build_baseline_comparison(capped, sector_rows_by_day=sector_rows_by_day)
        rows.append(
            {
                "cap_pct": cap_pct,
                "max_adoptions_per_sector": max_adoptions,
                "signal_count": len(capped),
                "continuation_rate": baseline.get("top3_next_day_positive_rate"),
                "avg_return_pct": baseline.get("top3_next_day_avg_return_pct"),
                "all_sectors_next_day_positive_rate": baseline.get(
                    "all_sectors_next_day_positive_rate"
                ),
                "baseline_positive_rate_delta": baseline.get(
                    "top3_vs_all_sectors_positive_rate_delta"
                ),
                "avg_return_delta_pct": baseline.get("top3_vs_all_sectors_avg_return_delta"),
            }
        )
    return rows


def build_rank_heat_profile_rows(
    tomorrow_top3: Sequence[Mapping[str, Any]],
    *,
    sector_rows_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    by_rank: dict[int, dict[str, list[float]]] = {
        1: {"heat": [], "ret": [], "pm": [], "next": []},
        2: {"heat": [], "ret": [], "pm": [], "next": []},
        3: {"heat": [], "ret": [], "pm": [], "next": []},
    }
    positive_by_rank: dict[int, int] = {1: 0, 2: 0, 3: 0}
    count_by_rank: dict[int, int] = {1: 0, 2: 0, 3: 0}

    for row in tomorrow_top3:
        rank = _int(row.get("rank"))
        if rank not in (1, 2, 3):
            continue
        heat = _float(row.get("heat_score"))
        ret = _float(row.get("daily_return_pct"))
        pm = _float(row.get("pm_return_pct_1400_1530"))
        sector = str(row.get("sector_33_name") or "")
        validation_day = str(row.get("validation_day") or "")
        next_ret = next_day_return(sector, validation_day, sector_rows_by_day)

        if heat is not None:
            by_rank[rank]["heat"].append(heat)
        if ret is not None:
            by_rank[rank]["ret"].append(ret)
        if pm is not None:
            by_rank[rank]["pm"].append(pm)
        if next_ret is not None:
            by_rank[rank]["next"].append(next_ret)
            count_by_rank[rank] += 1
            if next_ret > 0:
                positive_by_rank[rank] += 1

    out: list[dict[str, Any]] = []
    for rank in (1, 2, 3):
        bucket = by_rank[rank]
        n_next = count_by_rank[rank]
        out.append(
            {
                "rank": rank,
                "signal_count": len(tomorrow_top3) // TOP_SECTOR_COUNT if tomorrow_top3 else 0,
                "signal_day_heat_score_avg": round(statistics.mean(bucket["heat"]), 4)
                if bucket["heat"]
                else None,
                "signal_day_return_pct_avg": round(statistics.mean(bucket["ret"]), 4)
                if bucket["ret"]
                else None,
                "signal_day_pm_return_pct_1400_1530_avg": round(statistics.mean(bucket["pm"]), 4)
                if bucket["pm"]
                else None,
                "next_day_return_pct_avg": round(statistics.mean(bucket["next"]), 4)
                if bucket["next"]
                else None,
                "next_day_positive_rate": round(positive_by_rank[rank] / n_next, 4) if n_next else None,
            }
        )
        out[-1]["signal_count"] = max(
            len(bucket["heat"]),
            len(bucket["ret"]),
            len(bucket["pm"]),
            n_next,
        )
    return out


def build_debias_verdict(
    *,
    sector_neutral: Mapping[str, Any],
    leave_one_out: Sequence[Mapping[str, Any]],
    capped_rows: Sequence[Mapping[str, Any]],
    rank_profile: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    baseline_row = next((r for r in leave_one_out if r.get("excluded_sector") == "none"), {})
    full_delta = _float(baseline_row.get("top3_vs_all_delta"))
    beat_median = _float(sector_neutral.get("beat_median_rate"))
    beat_mean = _float(sector_neutral.get("beat_mean_rate"))

    rank1 = next((r for r in rank_profile if _int(r.get("rank")) == 1), {})
    rank2 = next((r for r in rank_profile if _int(r.get("rank")) == 2), {})
    rank2_beats_rank1_positive = (
        _float(rank2.get("next_day_positive_rate")) is not None
        and _float(rank1.get("next_day_positive_rate")) is not None
        and _float(rank2.get("next_day_positive_rate")) > _float(rank1.get("next_day_positive_rate"))
    )
    rank2_beats_rank1_return = (
        _float(rank2.get("next_day_return_pct_avg")) is not None
        and _float(rank1.get("next_day_return_pct_avg")) is not None
        and _float(rank2.get("next_day_return_pct_avg")) > _float(rank1.get("next_day_return_pct_avg"))
    )

    capped_20 = next((r for r in capped_rows if r.get("cap_pct") == 0.20), {})
    capped_20_delta = _float(capped_20.get("baseline_positive_rate_delta"))

    dominant_excluded = [
        r
        for r in leave_one_out
        if str(r.get("excluded_sector") or "") in DOMINANT_SECTORS
    ]
    deltas_after_exclude = [_float(r.get("top3_vs_all_delta")) for r in dominant_excluded]
    delta_persists = bool(
        full_delta is not None
        and deltas_after_exclude
        and all(d is not None and d > 0 for d in deltas_after_exclude)
    )

    if beat_median is not None and beat_median >= 0.55 and delta_persists:
        verdict = "edge_partly_sector_neutral"
    elif full_delta is not None and full_delta > 0.15 and not delta_persists:
        verdict = "likely_sector_bias"
    elif capped_20_delta is not None and full_delta is not None and capped_20_delta < full_delta * 0.5:
        verdict = "concentration_driven"
    else:
        verdict = "mixed_review"

    return {
        "verdict": verdict,
        "full_top3_vs_all_delta": full_delta,
        "sector_neutral_beat_median_rate": beat_median,
        "sector_neutral_beat_mean_rate": beat_mean,
        "delta_persists_after_dominant_exclusion": delta_persists,
        "rank2_next_day_beats_rank1_positive_rate": rank2_beats_rank1_positive,
        "rank2_next_day_beats_rank1_avg_return": rank2_beats_rank1_return,
        "rank2_hypothesis_note": (
            "Rank1 tends to have higher signal-day heat/return; if rank2 shows higher next-day "
            "positive rate, rank1 may reflect overextended same-day momentum."
        ),
    }


def build_debias_report_markdown(result: Mapping[str, Any]) -> str:
    neutral = result.get("sector_neutral_validation") or {}
    verdict = result.get("verdict") or {}
    rank_profile = result.get("rank_heat_profile") or []
    lines = [
        "# Phase248 Sector Heat Debias Validation",
        "",
        "Phase247 Top3 優位性がセクター偏重かどうかを検証（Runtime/Universe/Entry 反映なし）。",
        "",
        "## Constraints",
        "",
    ]
    for key, val in (result.get("constraints") or {}).items():
        lines.append(f"- `{key}`: {val}")
    lines.extend(
        [
            "",
            "## Sector-neutral validation",
            "",
            f"- beat median rate: {neutral.get('beat_median_rate')}",
            f"- beat mean rate: {neutral.get('beat_mean_rate')}",
            f"- avg excess vs median: {neutral.get('avg_excess_vs_median_pct')}",
            f"- avg excess vs mean: {neutral.get('avg_excess_vs_mean_pct')}",
            "",
            "## Verdict",
            "",
            f"- verdict: {verdict.get('verdict')}",
            f"- delta persists after dominant exclusion: {verdict.get('delta_persists_after_dominant_exclusion')}",
            f"- rank2 next-day beats rank1 (positive rate): {verdict.get('rank2_next_day_beats_rank1_positive_rate')}",
            f"- rank2 next-day beats rank1 (avg return): {verdict.get('rank2_next_day_beats_rank1_avg_return')}",
            "",
            "## Rank heat profile",
            "",
        ]
    )
    for row in rank_profile:
        lines.append(
            f"- rank{row.get('rank')}: heat={row.get('signal_day_heat_score_avg')} "
            f"signal_ret={row.get('signal_day_return_pct_avg')} "
            f"pm_ret={row.get('signal_day_pm_return_pct_1400_1530_avg')} "
            f"next_ret={row.get('next_day_return_pct_avg')}"
        )
    lines.append("")
    return "\n".join(lines)


def run_debias_validation_from_inputs(
    *,
    tomorrow_top3: Sequence[Mapping[str, Any]],
    sector_rows_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    sector_neutral = build_sector_neutral_validation(
        tomorrow_top3, sector_rows_by_day=sector_rows_by_day
    )
    leave_one_out = build_leave_one_sector_out_rows(
        tomorrow_top3, sector_rows_by_day=sector_rows_by_day
    )
    capped_rows = build_capped_sector_validation_rows(
        tomorrow_top3, sector_rows_by_day=sector_rows_by_day
    )
    rank_profile = build_rank_heat_profile_rows(
        tomorrow_top3, sector_rows_by_day=sector_rows_by_day
    )
    verdict = build_debias_verdict(
        sector_neutral=sector_neutral,
        leave_one_out=leave_one_out,
        capped_rows=capped_rows,
        rank_profile=rank_profile,
    )

    return {
        "phase": "248-SectorHeat-Debias-Validation",
        "title": "Sector heat debias validation",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "purpose": "Verify Top3 continuation edge is not merely dominant-sector bias",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "runtime_reflected": False,
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
        },
        "sector_neutral_validation": sector_neutral,
        "leave_one_sector_out": leave_one_out,
        "capped_sector_validation": capped_rows,
        "rank_heat_profile": rank_profile,
        "verdict": verdict,
        "_leave_one_sector_out_rows": leave_one_out,
        "_capped_sector_validation_rows": capped_rows,
        "_rank_heat_profile_rows": rank_profile,
    }


@dataclass
class MarketSectorHeatDebiasValidation:
    repo_root: Path
    reports_dir: Path
    min_day: Optional[str] = None
    max_day: Optional[str] = None
    regenerate_phase246: bool = False

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase248_sector_heat_debias_summary.json",
            "leave_one_out": self.reports_dir / "phase248_leave_one_sector_out.csv",
            "capped_validation": self.reports_dir / "phase248_sector_capped_validation.csv",
            "rank_heat_profile": self.reports_dir / "phase248_rank_heat_profile.csv",
            "report": self.reports_dir / "phase248_sector_heat_report.md",
        }

    def _load_inputs(self) -> dict[str, Any]:
        if not self.regenerate_phase246:
            loaded = load_phase246_inputs(self.reports_dir)
            if loaded:
                return loaded

        obs = MarketSectorHeatObservation(
            repo_root=self.repo_root,
            reports_dir=self.reports_dir,
            min_day=self.min_day,
            max_day=self.max_day,
        )
        result = obs.run()
        obs.write_outputs(result)
        return {
            "tomorrow_top3": result.get("_tomorrow_top3_rows") or [],
            "sector_rows_by_day": sector_rows_by_day_from_csv(result.get("_by_sector_rows") or []),
        }

    def run(self) -> dict[str, Any]:
        inputs = self._load_inputs()
        result = run_debias_validation_from_inputs(
            tomorrow_top3=inputs.get("tomorrow_top3") or [],
            sector_rows_by_day=inputs.get("sector_rows_by_day") or {},
        )
        result["inputs"] = {
            "phase246_tomorrow_top3": str(self.reports_dir / "phase246_sector_heat_tomorrow_top3.csv"),
            "phase246_by_sector": str(self.reports_dir / "phase246_sector_heat_by_sector.csv"),
            "regenerate_phase246": self.regenerate_phase246,
            "min_day": self.min_day,
            "max_day": self.max_day,
        }
        return result

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        paths = self.paths()
        paths["summary"].parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["summary"].write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_csv(
            paths["leave_one_out"],
            LEAVE_ONE_OUT_FIELDS,
            result.get("_leave_one_sector_out_rows") or [],
        )
        _write_csv(
            paths["capped_validation"],
            CAPPED_VALIDATION_FIELDS,
            result.get("_capped_sector_validation_rows") or [],
        )
        _write_csv(
            paths["rank_heat_profile"],
            RANK_HEAT_PROFILE_FIELDS,
            result.get("_rank_heat_profile_rows") or [],
        )
        paths["report"].write_text(build_debias_report_markdown(payload), encoding="utf-8")
        return paths
