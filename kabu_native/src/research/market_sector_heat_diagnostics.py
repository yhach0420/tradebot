"""
Phase247-SectorHeat-Diagnostics: diagnose Phase246 Top3 sector heat prediction quality.

Observation only — no Runtime / Universe / Entry changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.market_sector_heat import (
    TOP_SECTOR_COUNT,
    MarketSectorHeatObservation,
    _float,
    _int,
    _write_csv,
)

JST = ZoneInfo("Asia/Tokyo")

MIN_TRUSTED_SIGNAL_COUNT = 3
CONCENTRATION_WARNING_THRESHOLD = 0.6

BY_RANK_FIELDS = [
    "rank",
    "signal_count",
    "next_day_positive_count",
    "continuation_rate",
    "next_day_avg_return_pct",
]

BY_SECTOR_VALIDATION_FIELDS = [
    "sector_33_name",
    "top3_adoption_count",
    "rank1_count",
    "rank2_count",
    "rank3_count",
    "appearance_rate",
    "next_day_positive_count",
    "continuation_rate",
    "next_day_avg_return_pct",
    "reference_only",
]


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [dict(row) for row in csv.DictReader(f)]


def sector_rows_by_day_from_csv(rows: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, dict[str, Any]]]:
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        day = str(row.get("day") or "")
        sector = str(row.get("sector_33_name") or "")
        if not day or not sector:
            continue
        out.setdefault(day, {})[sector] = dict(row)
    return out


def next_day_return(
    sector: str,
    validation_day: str,
    sector_rows_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> Optional[float]:
    return _float((sector_rows_by_day.get(validation_day) or {}).get(sector, {}).get("daily_return_pct"))


def build_sector_concentration(
    tomorrow_top3: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    total_slots = len(tomorrow_top3)
    signal_days = sorted({str(r.get("signal_day") or "") for r in tomorrow_top3 if r.get("signal_day")})
    signal_count = len(signal_days)
    by_sector: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "sector_33_name": "",
            "top3_count": 0,
            "rank1_count": 0,
            "rank2_count": 0,
            "rank3_count": 0,
            "appearance_rate": None,
        }
    )
    for row in tomorrow_top3:
        sector = str(row.get("sector_33_name") or "")
        rank = _int(row.get("rank"))
        if not sector:
            continue
        item = by_sector[sector]
        item["sector_33_name"] = sector
        item["top3_count"] += 1
        if rank == 1:
            item["rank1_count"] += 1
        elif rank == 2:
            item["rank2_count"] += 1
        elif rank == 3:
            item["rank3_count"] += 1

    sectors = sorted(by_sector.values(), key=lambda r: (-_int(r.get("top3_count")), str(r.get("sector_33_name"))))
    for item in sectors:
        count = _int(item.get("top3_count"))
        item["appearance_rate"] = round(count / total_slots, 4) if total_slots else None

    top_counts = sorted((_int(s.get("top3_count")) for s in sectors), reverse=True)
    top3_sector_share = round(sum(top_counts[:3]) / total_slots, 4) if total_slots else None
    unique_sectors = len(sectors)

    return {
        "signal_day_count": signal_count,
        "top3_slot_count": total_slots,
        "unique_sectors_in_top3": unique_sectors,
        "top3_sector_slot_share": top3_sector_share,
        "by_sector": sectors,
    }


def build_rank_continuation_rows(
    tomorrow_top3: Sequence[Mapping[str, Any]],
    *,
    sector_rows_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    by_rank: dict[int, list[float]] = {1: [], 2: [], 3: []}
    positive_by_rank: dict[int, int] = {1: 0, 2: 0, 3: 0}
    count_by_rank: dict[int, int] = {1: 0, 2: 0, 3: 0}

    for row in tomorrow_top3:
        rank = _int(row.get("rank"))
        if rank not in (1, 2, 3):
            continue
        sector = str(row.get("sector_33_name") or "")
        validation_day = str(row.get("validation_day") or "")
        ret = next_day_return(sector, validation_day, sector_rows_by_day)
        if ret is None:
            continue
        count_by_rank[rank] += 1
        by_rank[rank].append(ret)
        if ret > 0:
            positive_by_rank[rank] += 1

    out: list[dict[str, Any]] = []
    for rank in (1, 2, 3):
        n = count_by_rank[rank]
        rets = by_rank[rank]
        out.append(
            {
                "rank": rank,
                "signal_count": n,
                "next_day_positive_count": positive_by_rank[rank],
                "continuation_rate": round(positive_by_rank[rank] / n, 4) if n else None,
                "next_day_avg_return_pct": round(statistics.mean(rets), 4) if rets else None,
            }
        )
    return out


def build_baseline_comparison(
    tomorrow_top3: Sequence[Mapping[str, Any]],
    *,
    sector_rows_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
) -> dict[str, Any]:
    validation_days = sorted({str(r.get("validation_day") or "") for r in tomorrow_top3 if r.get("validation_day")})
    all_sector_positive_rates: list[float] = []
    all_sector_returns: list[float] = []

    for day in validation_days:
        day_rows = sector_rows_by_day.get(day) or {}
        rets: list[float] = []
        for row in day_rows.values():
            ret = _float(row.get("daily_return_pct"))
            if ret is not None:
                rets.append(ret)
        if not rets:
            continue
        pos = sum(1 for r in rets if r > 0)
        all_sector_positive_rates.append(pos / len(rets))
        all_sector_returns.extend(rets)

    top3_positive = 0
    top3_total = 0
    top3_returns: list[float] = []
    for row in tomorrow_top3:
        sector = str(row.get("sector_33_name") or "")
        validation_day = str(row.get("validation_day") or "")
        ret = next_day_return(sector, validation_day, sector_rows_by_day)
        if ret is None:
            continue
        top3_total += 1
        top3_returns.append(ret)
        if ret > 0:
            top3_positive += 1

    all_sectors_positive_rate = (
        round(statistics.mean(all_sector_positive_rates), 4) if all_sector_positive_rates else None
    )
    top3_positive_rate = round(top3_positive / top3_total, 4) if top3_total else None
    delta = (
        round(top3_positive_rate - all_sectors_positive_rate, 4)
        if top3_positive_rate is not None and all_sectors_positive_rate is not None
        else None
    )

    return {
        "validation_day_count": len(validation_days),
        "all_sectors_next_day_positive_rate": all_sectors_positive_rate,
        "all_sectors_next_day_avg_return_pct": round(statistics.mean(all_sector_returns), 4)
        if all_sector_returns
        else None,
        "top3_next_day_positive_rate": top3_positive_rate,
        "top3_next_day_avg_return_pct": round(statistics.mean(top3_returns), 4) if top3_returns else None,
        "top3_vs_all_sectors_positive_rate_delta": delta,
        "top3_vs_all_sectors_avg_return_delta": round(
            statistics.mean(top3_returns) - statistics.mean(all_sector_returns), 4
        )
        if top3_returns and all_sector_returns
        else None,
    }


def build_sector_validation_rows(
    tomorrow_top3: Sequence[Mapping[str, Any]],
    *,
    sector_rows_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
    total_top3_slots: int,
    min_trusted_signal_count: int = MIN_TRUSTED_SIGNAL_COUNT,
) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "sector_33_name": "",
            "top3_adoption_count": 0,
            "rank1_count": 0,
            "rank2_count": 0,
            "rank3_count": 0,
            "next_day_positive_count": 0,
            "next_day_returns": [],
        }
    )

    for row in tomorrow_top3:
        sector = str(row.get("sector_33_name") or "")
        rank = _int(row.get("rank"))
        validation_day = str(row.get("validation_day") or "")
        if not sector:
            continue
        item = stats[sector]
        item["sector_33_name"] = sector
        item["top3_adoption_count"] += 1
        if rank == 1:
            item["rank1_count"] += 1
        elif rank == 2:
            item["rank2_count"] += 1
        elif rank == 3:
            item["rank3_count"] += 1
        ret = next_day_return(sector, validation_day, sector_rows_by_day)
        if ret is None:
            continue
        item["next_day_returns"].append(ret)
        if ret > 0:
            item["next_day_positive_count"] += 1

    rows: list[dict[str, Any]] = []
    for sector in sorted(stats):
        item = stats[sector]
        adoption = _int(item.get("top3_adoption_count"))
        rets: list[float] = list(item.get("next_day_returns") or [])
        validated = len(rets)
        rows.append(
            {
                "sector_33_name": sector,
                "top3_adoption_count": adoption,
                "rank1_count": item.get("rank1_count"),
                "rank2_count": item.get("rank2_count"),
                "rank3_count": item.get("rank3_count"),
                "appearance_rate": round(adoption / total_top3_slots, 4) if total_top3_slots else None,
                "next_day_positive_count": item.get("next_day_positive_count"),
                "continuation_rate": round(_int(item.get("next_day_positive_count")) / validated, 4)
                if validated
                else None,
                "next_day_avg_return_pct": round(statistics.mean(rets), 4) if rets else None,
                "reference_only": adoption < min_trusted_signal_count,
            }
        )
    rows.sort(key=lambda r: (-_int(r.get("top3_adoption_count")), str(r.get("sector_33_name"))))
    return rows


def build_overfit_warnings(
    concentration: Mapping[str, Any],
    sector_validation_rows: Sequence[Mapping[str, Any]],
    *,
    min_trusted_signal_count: int = MIN_TRUSTED_SIGNAL_COUNT,
    concentration_threshold: float = CONCENTRATION_WARNING_THRESHOLD,
) -> dict[str, Any]:
    by_sector = list(concentration.get("by_sector") or [])
    total_slots = _int(concentration.get("top3_slot_count"))
    top3_share = _float(concentration.get("top3_sector_slot_share"))
    unique_sectors = _int(concentration.get("unique_sectors_in_top3"))

    dominant_sectors = [
        {
            "sector_33_name": row.get("sector_33_name"),
            "top3_count": row.get("top3_count"),
            "appearance_rate": row.get("appearance_rate"),
        }
        for row in by_sector[:3]
    ]

    reference_only_sectors = [
        str(row.get("sector_33_name"))
        for row in sector_validation_rows
        if row.get("reference_only")
    ]
    trusted_sectors = [
        str(row.get("sector_33_name"))
        for row in sector_validation_rows
        if not row.get("reference_only")
    ]

    concentrated = bool(top3_share is not None and top3_share >= concentration_threshold)
    only_three_sectors = unique_sectors <= TOP_SECTOR_COUNT and total_slots >= TOP_SECTOR_COUNT

    flags: list[str] = []
    if concentrated:
        flags.append("top3_sector_concentration_high")
    if only_three_sectors:
        flags.append("unique_sector_pool_narrow")
    if len(trusted_sectors) < 2:
        flags.append("insufficient_trusted_sector_sample")

    if concentrated and (top3_share or 0) >= 0.8:
        verdict = "likely_sector_bias"
    elif (top3_share or 0) >= concentration_threshold:
        verdict = "watch_concentration"
    elif len(trusted_sectors) >= 3:
        verdict = "diverse_enough_for_review"
    else:
        verdict = "insufficient_sample"

    return {
        "min_trusted_signal_count": min_trusted_signal_count,
        "concentration_warning_threshold": concentration_threshold,
        "top3_sector_slot_share": top3_share,
        "unique_sectors_in_top3": unique_sectors,
        "dominant_sectors": dominant_sectors,
        "reference_only_sectors": reference_only_sectors,
        "trusted_sectors": trusted_sectors,
        "flags": flags,
        "verdict": verdict,
        "note": (
            "Sectors with adoption_count below min_trusted_signal_count are reference_only. "
            "High top3_sector_slot_share suggests heat may reflect persistent sector bias rather than predictive edge."
        ),
    }


def build_diagnostics_report_markdown(result: Mapping[str, Any]) -> str:
    baseline = result.get("baseline_comparison") or {}
    overfit = result.get("overfit_warnings") or {}
    concentration = result.get("sector_concentration") or {}
    by_rank = result.get("by_rank") or []
    lines = [
        "# Phase247 Sector Heat Diagnostics",
        "",
        "Phase246 Top3 の予測品質診断（Runtime/Universe/Entry 反映なし）。",
        "",
        "## Constraints",
        "",
    ]
    for key, val in (result.get("constraints") or {}).items():
        lines.append(f"- `{key}`: {val}")
    lines.extend(
        [
            "",
            "## Sector concentration",
            "",
            f"- signal days: {concentration.get('signal_day_count')}",
            f"- top3 slots: {concentration.get('top3_slot_count')}",
            f"- unique sectors: {concentration.get('unique_sectors_in_top3')}",
            f"- top-3-sector slot share: {concentration.get('top3_sector_slot_share')}",
            "",
            "## Rank continuation",
            "",
        ]
    )
    for row in by_rank:
        lines.append(
            f"- rank{row.get('rank')}: continuation_rate={row.get('continuation_rate')} "
            f"avg_return={row.get('next_day_avg_return_pct')} n={row.get('signal_count')}"
        )
    lines.extend(
        [
            "",
            "## Baseline comparison",
            "",
            f"- all sectors next-day positive rate: {baseline.get('all_sectors_next_day_positive_rate')}",
            f"- top3 next-day positive rate: {baseline.get('top3_next_day_positive_rate')}",
            f"- delta: {baseline.get('top3_vs_all_sectors_positive_rate_delta')}",
            f"- avg return delta: {baseline.get('top3_vs_all_sectors_avg_return_delta')}",
            "",
            "## Overfit warnings",
            "",
            f"- verdict: {overfit.get('verdict')}",
            f"- flags: {', '.join(overfit.get('flags') or []) or 'none'}",
            f"- reference_only sectors: {', '.join(overfit.get('reference_only_sectors') or []) or 'none'}",
            "",
            str(overfit.get("note") or ""),
            "",
        ]
    )
    return "\n".join(lines)


def load_phase246_inputs(reports_dir: Path) -> dict[str, Any]:
    tomorrow_top3 = _read_csv(reports_dir / "phase246_sector_heat_tomorrow_top3.csv")
    by_sector = _read_csv(reports_dir / "phase246_sector_heat_by_sector.csv")
    if not tomorrow_top3 or not by_sector:
        return {}
    for row in tomorrow_top3:
        row["rank"] = _int(row.get("rank"))
    return {
        "tomorrow_top3": tomorrow_top3,
        "sector_rows_by_day": sector_rows_by_day_from_csv(by_sector),
    }


def run_diagnostics_from_inputs(
    *,
    tomorrow_top3: Sequence[Mapping[str, Any]],
    sector_rows_by_day: Mapping[str, Mapping[str, Mapping[str, Any]]],
    min_trusted_signal_count: int = MIN_TRUSTED_SIGNAL_COUNT,
) -> dict[str, Any]:
    concentration = build_sector_concentration(tomorrow_top3)
    by_rank_rows = build_rank_continuation_rows(
        tomorrow_top3, sector_rows_by_day=sector_rows_by_day
    )
    baseline = build_baseline_comparison(
        tomorrow_top3, sector_rows_by_day=sector_rows_by_day
    )
    by_sector_rows = build_sector_validation_rows(
        tomorrow_top3,
        sector_rows_by_day=sector_rows_by_day,
        total_top3_slots=_int(concentration.get("top3_slot_count")),
        min_trusted_signal_count=min_trusted_signal_count,
    )
    overfit = build_overfit_warnings(
        concentration,
        by_sector_rows,
        min_trusted_signal_count=min_trusted_signal_count,
    )

    return {
        "phase": "247-SectorHeat-Diagnostics",
        "title": "Sector heat Top3 quality diagnostics",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "purpose": "Determine whether sector heat has next-day predictive power or reflects sector bias",
        "constraints": {
            "review_only": True,
            "production_changes_forbidden": True,
            "yaml_changes_forbidden": True,
            "runtime_reflected": False,
            "universe_change_forbidden": True,
            "entry_change_forbidden": True,
        },
        "sector_concentration": concentration,
        "by_rank": by_rank_rows,
        "baseline_comparison": baseline,
        "overfit_warnings": overfit,
        "_by_sector_validation_rows": by_sector_rows,
    }


@dataclass
class MarketSectorHeatDiagnostics:
    repo_root: Path
    reports_dir: Path
    min_day: Optional[str] = None
    max_day: Optional[str] = None
    min_trusted_signal_count: int = MIN_TRUSTED_SIGNAL_COUNT
    regenerate_phase246: bool = False

    def paths(self) -> dict[str, Path]:
        return {
            "diagnostics": self.reports_dir / "phase247_sector_heat_diagnostics.json",
            "by_rank": self.reports_dir / "phase247_sector_heat_by_rank.csv",
            "by_sector_validation": self.reports_dir / "phase247_sector_heat_by_sector_validation.csv",
            "report": self.reports_dir / "phase247_sector_heat_report.md",
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
        by_sector_rows = result.get("_by_sector_rows") or []
        return {
            "tomorrow_top3": result.get("_tomorrow_top3_rows") or [],
            "sector_rows_by_day": sector_rows_by_day_from_csv(by_sector_rows),
        }

    def run(self) -> dict[str, Any]:
        inputs = self._load_inputs()
        tomorrow_top3 = inputs.get("tomorrow_top3") or []
        sector_rows_by_day = inputs.get("sector_rows_by_day") or {}
        result = run_diagnostics_from_inputs(
            tomorrow_top3=tomorrow_top3,
            sector_rows_by_day=sector_rows_by_day,
            min_trusted_signal_count=self.min_trusted_signal_count,
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
        paths["diagnostics"].parent.mkdir(parents=True, exist_ok=True)
        payload = {k: v for k, v in result.items() if not k.startswith("_")}
        paths["diagnostics"].write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        _write_csv(paths["by_rank"], BY_RANK_FIELDS, result.get("by_rank") or [])
        _write_csv(
            paths["by_sector_validation"],
            BY_SECTOR_VALIDATION_FIELDS,
            result.get("_by_sector_validation_rows") or [],
        )
        paths["report"].write_text(build_diagnostics_report_markdown(payload), encoding="utf-8")
        return paths
