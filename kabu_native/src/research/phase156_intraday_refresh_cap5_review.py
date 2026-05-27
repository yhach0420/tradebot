"""
Phase 156: Intraday universe refresh (10:00 / 14:30) + cap=5 design review (shadow only).

Counterfactual cap simulation with entry_price_risk_guard on candidates.
Refresh scenarios use a conservative proxy when historical refresh-universe CSVs are absent.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.exposure_cap_whatif_review import (
    PHASE53_MIN_QUALITY,
    _simulate_cap_scenario,
)
from research.mfe_mae_exit_review import discover_sessions, load_structural_trades, parse_ts
from research.runtime_pilot_policy_review import _build_price_index, _candidates_from_events
from research.small_paper_performance_review import _load_events, _load_json, _profit_factor
from small_paper.entry_price_risk_guard import (
    EntryPriceRiskGuardConfig,
    EntryPriceRiskGuardState,
)

PHASE156_CAPS = (3, 5, 7)
REFRESH_AM = time(10, 0)
REFRESH_PM = time(14, 30)
REFRESH_CAPTURE_RATE = 0.15
MIN_SESSIONS = 4
REGISTER_LIMIT = 50
CORE10_SIZE = 10


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def _entry_local_time(entry_time: str) -> Optional[time]:
    try:
        return datetime.fromisoformat(str(entry_time).replace("Z", "+00:00")).timetz().replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _in_post_refresh_window(entry_time: str) -> bool:
    t = _entry_local_time(entry_time)
    if t is None:
        return False
    am2 = time(10, 0) <= t <= time(11, 23)
    pm2 = time(14, 30) <= t <= time(15, 20)
    return am2 or pm2


def _guard_reject_candidate(row: Mapping[str, Any]) -> bool:
    px = float(row.get("current_price") or row.get("entry_price") or 0)
    st = EntryPriceRiskGuardState(
        config=EntryPriceRiskGuardConfig(
            enabled=True,
            min_entry_price=50.0,
            max_tick_ratio_pct=5.0,
            shadow_only=True,
        )
    )
    chk = st.check({"symbol": row.get("symbol"), "entry_price": px})
    return chk.blocked


def _operational_candidate(row: Mapping[str, Any], *, min_quality: float) -> bool:
    q = float(row.get("continuation_quality_score") or 0)
    if q < min_quality:
        return False
    ds = row.get("daytrade_suitability_score")
    dt = row.get("daytrade_suitability_threshold")
    if ds is not None and dt is not None:
        try:
            if float(ds) < float(dt):
                return False
        except (TypeError, ValueError):
            pass
    return True


def _filter_price_risk_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    min_quality: float = PHASE53_MIN_QUALITY,
) -> tuple[list[dict[str, Any]], int, int]:
    out: list[dict[str, Any]] = []
    guard_rejects = 0
    operational_skips = 0
    for row in candidates:
        if not _operational_candidate(row, min_quality=min_quality):
            operational_skips += 1
            continue
        if _guard_reject_candidate(row):
            guard_rejects += 1
            continue
        out.append(dict(row))
    return out, guard_rejects, operational_skips


def _structural_metrics(session_dir: Path) -> dict[str, Any]:
    trades = load_structural_trades(session_dir / "structural_trades.csv")
    pnls = [float(t.get("realized_pnl_pct") or 0) for t in trades]
    overlap = sum(1 for t in trades if str(t.get("close_reason") or "") == "overlap_replaced_review")
    stop_hit = sum(1 for t in trades if str(t.get("close_reason") or "") == "stop_hit")
    return {
        "structural_trade_count": len(trades),
        "structural_overlap_replaced_count": overlap,
        "structural_stop_hit_count": stop_hit,
        "structural_total_pnl_pct": round(sum(pnls), 4) if pnls else 0.0,
        "structural_pf": _profit_factor(pnls),
    }


def _cap_row_metrics(
    result: Any,
    *,
    session_id: str,
    structural: Mapping[str, Any],
) -> dict[str, Any]:
    m = dict(result.metrics)
    pnls = [float(r.get("realized_pnl_pct") or 0) for r in result.accepted_rows]
    open_samples = [int(s.get("open_count") or 0) for s in getattr(result, "saturation_snapshots", []) or []]
    if not open_samples and m.get("peak_open_slots") is not None:
        open_samples = [int(m.get("peak_open_slots") or 0)]
    return {
        "session_id": session_id,
        "max_concurrent": result.max_concurrent,
        "accepted_count": int(m.get("accepted_count") or 0),
        "rejected_max_concurrent": int(m.get("max_concurrent_reject_count") or 0),
        "total_pnl_proxy": round(sum(pnls), 4) if pnls else 0.0,
        "pf_proxy": m.get("profit_factor"),
        "overlap_replaced_proxy_count": int(m.get("same_symbol_overlap_accept_count") or 0),
        "max_drawdown_proxy": m.get("drawdown_proxy_pct"),
        "stop_hit_count": structural.get("structural_stop_hit_count"),
        "avg_open_positions": round(statistics.mean(open_samples), 4) if open_samples else None,
        "peak_open_positions": max(open_samples) if open_samples else int(m.get("peak_open_slots") or 0),
        "max_concurrent_reject_would_be_pnl_sum": m.get("max_concurrent_reject_would_be_pnl_sum"),
        "entry_guard_note": "candidates filtered price>=50 tick<=5%",
    }


def _aggregate_cap_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_cap: dict[int, list[Mapping[str, Any]]] = defaultdict(list)
    for r in rows:
        by_cap[int(r["max_concurrent"])].append(r)
    cap3_pnl = sum(float(r.get("total_pnl_proxy") or 0) for r in by_cap.get(3, []))
    cap3_overlap = sum(int(r.get("overlap_replaced_proxy_count") or 0) for r in by_cap.get(3, []))
    out: list[dict[str, Any]] = []
    for cap in PHASE156_CAPS:
        group = by_cap.get(cap, [])
        if not group:
            continue
        pnl = sum(float(r.get("total_pnl_proxy") or 0) for r in group)
        overlap = sum(int(r.get("overlap_replaced_proxy_count") or 0) for r in group)
        pf_vals = [float(r["pf_proxy"]) for r in group if r.get("pf_proxy") is not None]
        avg_open = [float(r["avg_open_positions"]) for r in group if r.get("avg_open_positions") is not None]
        peak_open = [int(r["peak_open_positions"]) for r in group if r.get("peak_open_positions") is not None]
        dd_vals = [float(r["max_drawdown_proxy"]) for r in group if r.get("max_drawdown_proxy") is not None]
        out.append(
            {
                "max_concurrent": cap,
                "session_count": len(group),
                "accepted_count": sum(int(r.get("accepted_count") or 0) for r in group),
                "rejected_max_concurrent": sum(int(r.get("rejected_max_concurrent") or 0) for r in group),
                "total_pnl_proxy": round(pnl, 4),
                "total_pnl_delta_vs_cap3": round(pnl - cap3_pnl, 4) if cap != 3 else 0.0,
                "pf_proxy_mean": round(statistics.mean(pf_vals), 4) if pf_vals else None,
                "overlap_replaced_proxy_count": overlap,
                "overlap_delta_vs_cap3": overlap - cap3_overlap if cap != 3 else 0,
                "max_drawdown_proxy_worst": round(min(dd_vals), 4) if dd_vals else None,
                "stop_hit_count_sum": sum(int(r.get("stop_hit_count") or 0) for r in group),
                "avg_open_positions_mean": round(statistics.mean(avg_open), 4) if avg_open else None,
                "peak_open_positions_max": max(peak_open) if peak_open else None,
                "price_risk_entry_guard": True,
            }
        )
    return out


def _first_seen_refresh_symbols(candidates: Sequence[Mapping[str, Any]]) -> dict[str, set[str]]:
    pre_am: set[str] = set()
    pre_pm: set[str] = set()
    am_refresh_new: set[str] = set()
    pm_refresh_new: set[str] = set()
    for row in candidates:
        sym = str(row.get("symbol") or "")
        if not sym:
            continue
        t = _entry_local_time(str(row.get("entry_time") or ""))
        if t is None:
            continue
        if t < REFRESH_AM:
            pre_am.add(sym)
        elif time(10, 0) <= t <= time(11, 23):
            if sym not in pre_am:
                am_refresh_new.add(sym)
        if t < time(12, 33):
            continue
        if time(12, 33) <= t < REFRESH_PM:
            pre_pm.add(sym)
        elif time(14, 30) <= t <= time(15, 20):
            if sym not in pre_pm and sym not in pre_am:
                pm_refresh_new.add(sym)
    return {
        "am_refresh_new_symbols": am_refresh_new,
        "pm_refresh_new_symbols": pm_refresh_new,
    }


def _post_refresh_mc_proxy(
    mc_rejects: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    post = [r for r in mc_rejects if _in_post_refresh_window(str(r.get("entry_time") or ""))]
    would = [float(r.get("would_be_pnl_pct") or 0) for r in post]
    positive = [w for w in would if w > 0]
    return {
        "post_refresh_mc_reject_count": len(post),
        "post_refresh_mc_reject_would_be_pnl_sum": round(sum(would), 4) if would else 0.0,
        "post_refresh_mc_reject_positive_would_be_pnl_sum": round(sum(positive), 4) if positive else 0.0,
        "refresh_uplift_pnl_proxy": round(sum(positive) * REFRESH_CAPTURE_RATE, 4) if positive else 0.0,
    }


@dataclass
class ScenarioAggregate:
    scenario_id: str
    label: str
    max_concurrent: int
    intraday_refresh: bool
    per_session: list[dict[str, Any]] = field(default_factory=list)

    def aggregate(self) -> dict[str, Any]:
        if not self.per_session:
            return {"scenario_id": self.scenario_id, "session_count": 0}
        pnl = sum(float(r.get("total_pnl_proxy") or 0) for r in self.per_session)
        uplift = sum(float(r.get("refresh_uplift_pnl_proxy") or 0) for r in self.per_session)
        accepted = sum(int(r.get("accepted_count") or 0) for r in self.per_session)
        rejected_mc = sum(int(r.get("rejected_max_concurrent") or 0) for r in self.per_session)
        overlap = sum(int(r.get("overlap_replaced_proxy_count") or 0) for r in self.per_session)
        pf_vals = [float(r["pf_proxy"]) for r in self.per_session if r.get("pf_proxy") is not None]
        dd_vals = [float(r["max_drawdown_proxy"]) for r in self.per_session if r.get("max_drawdown_proxy") is not None]
        avg_open = [float(r["avg_open_positions"]) for r in self.per_session if r.get("avg_open_positions") is not None]
        peak_open = [int(r["peak_open_positions"]) for r in self.per_session if r.get("peak_open_positions") is not None]
        return {
            "scenario_id": self.scenario_id,
            "label": self.label,
            "max_concurrent": self.max_concurrent,
            "intraday_refresh": self.intraday_refresh,
            "session_count": len(self.per_session),
            "accepted_count": accepted,
            "rejected_max_concurrent": rejected_mc,
            "total_pnl_proxy": round(pnl, 4),
            "refresh_uplift_pnl_proxy": round(uplift, 4),
            "total_pnl_with_refresh_proxy": round(pnl + uplift, 4) if self.intraday_refresh else round(pnl, 4),
            "pf_proxy_mean": round(statistics.mean(pf_vals), 4) if pf_vals else None,
            "overlap_replaced_proxy_count": overlap,
            "max_drawdown_proxy_worst": round(min(dd_vals), 4) if dd_vals else None,
            "stop_hit_count_sum": sum(int(r.get("stop_hit_count") or 0) for r in self.per_session),
            "avg_open_positions_mean": round(statistics.mean(avg_open), 4) if avg_open else None,
            "peak_open_positions_max": max(peak_open) if peak_open else None,
        }


def _register_plan_issue(
    *,
    open_count: int,
    core10: int = CORE10_SIZE,
    dynamic_fill: int = 40,
) -> Optional[str]:
    total_naive = open_count + core10 + dynamic_fill
    if open_count > REGISTER_LIMIT:
        return "open_symbols_exceed_register_limit"
    if total_naive > REGISTER_LIMIT and open_count + core10 > REGISTER_LIMIT:
        return "open_plus_core_exceeds_limit_dynamic_must_shrink"
    return None


def determine_verdict(
    *,
    cap_agg: Sequence[Mapping[str, Any]],
    scenarios: Sequence[Mapping[str, Any]],
    session_count: int,
    register_issues: Sequence[str],
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if session_count < MIN_SESSIONS:
        return "more_data_needed", [f"sessions={session_count} < {MIN_SESSIONS}"]
    if register_issues:
        return "open_position_register_issue", register_issues

    cap3 = next((r for r in cap_agg if int(r["max_concurrent"]) == 3), None)
    cap5 = next((r for r in cap_agg if int(r["max_concurrent"]) == 5), None)
    cap7 = next((r for r in cap_agg if int(r["max_concurrent"]) == 7), None)
    s0 = next((r for r in scenarios if r["scenario_id"] == "S0"), None)
    s1 = next((r for r in scenarios if r["scenario_id"] == "S1"), None)
    s2 = next((r for r in scenarios if r["scenario_id"] == "S2"), None)
    s3 = next((r for r in scenarios if r["scenario_id"] == "S3"), None)

    if not cap3 or not cap5:
        return "more_data_needed", ["missing cap3/cap5 aggregate"]

    cap5_pnl_delta = float(cap5.get("total_pnl_delta_vs_cap3") or 0)
    cap5_overlap_delta = int(cap5.get("overlap_delta_vs_cap3") or 0)
    cap5_pf = float(cap5.get("pf_proxy_mean") or 0)
    cap3_pf = float(cap3.get("pf_proxy_mean") or 0)

    refresh_uplift_s2 = float(s2.get("refresh_uplift_pnl_proxy") or 0) if s2 else 0.0
    refresh_uplift_s3 = float(s3.get("refresh_uplift_pnl_proxy") or 0) if s3 else 0.0
    cap5_vs_cap3_pnl = float(s1.get("total_pnl_proxy") or 0) - float(s0.get("total_pnl_proxy") or 0) if s1 and s0 else cap5_pnl_delta

    notes.append(f"cap5_pnl_delta_vs_cap3={cap5_pnl_delta}")
    notes.append(f"cap5_overlap_delta={cap5_overlap_delta}")
    notes.append(f"refresh_uplift_s2={refresh_uplift_s2}")

    cap5_promising = (
        cap5_pnl_delta > 0.3
        and cap5_pf >= cap3_pf * 0.95
        and cap5_overlap_delta <= 15
    )
    refresh_material = max(refresh_uplift_s2, refresh_uplift_s3) > 0.5

    if cap5_overlap_delta > 15:
        if refresh_material:
            return "refresh_promising_cap3_enough", notes + [
                f"cap5 overlap_proxy_delta={cap5_overlap_delta} too high",
                "refresh post-window MC proxy positive but keep cap3 until overlap policy",
            ]
        return "more_data_needed", notes + [
            f"cap5 overlap_proxy_delta={cap5_overlap_delta} too high vs cap3",
            "need position sizing / overlap policy before cap5 shadow live",
        ]

    if cap5_promising and not refresh_material and cap5_overlap_delta <= 8:
        return "cap5_promising_refresh_not_needed", notes + [
            "cap5 improves pnl proxy; intraday refresh uplift proxy small",
            "recommend cap5 shadow config before refresh implementation",
        ]

    if refresh_material and not cap5_promising:
        return "refresh_promising_cap3_enough", notes + [
            "refresh post-window MC proxy material but cap5 not clearly better",
        ]

    if cap5_promising and cap5_overlap_delta <= 12:
        return "refresh_cap5_shadow_ready", notes + [
            "cap5 promising under price-risk guard; proceed cap5 shadow yaml",
            "implement refresh only with open-symbol register carry + candidate1 exit policy",
        ]

    if cap7 and float(cap7.get("total_pnl_delta_vs_cap3") or 0) > cap5_pnl_delta + 1.0:
        return "more_data_needed", notes + ["cap7 still adds pnl; need sizing model before cap5 live"]

    return "more_data_needed", notes + ["mixed cap/refresh signals; extend live shadow sessions"]


def build_register_design_md() -> str:
    return """# Phase 156: Kabu register refresh design (review only)

## Policy (candidate 1 — recommended first)

- At 10:00 and 14:30: regenerate universe CSV (Core10 + Dynamic40, price-risk filtered).
- **Do not** force-exit positions dropped from the new universe.
- **New entries only** use the latest universe.
- Session close windows unchanged (AM ~11:25, PM ~15:23).

## Register sequence at each refresh

1. Build target symbol set (priority merge, max 50).
2. `PUT /unregister/all` (full PUSH table — Phase 155).
3. `PUT /register` with merged list (≤50).

## Merge priority (must not drop open symbols)

| Priority | Bucket | Notes |
|----------|--------|-------|
| 1 | `open_symbols` | All currently held; required for exit monitoring |
| 2 | Core10 | Always include after open set |
| 3 | Dynamic fill | Trim from bottom of rank until total ≤ 50 |

If `len(open) + len(core10) > 50`: shrink Dynamic to zero first; if still >50, document alert (`open_position_register_issue`) — should not happen with cap≤5.

## Universe CSV naming

- `universe_core10_dynamic40_price_risk_am_YYYYMMDD.csv`
- `universe_core10_dynamic40_price_risk_am_refresh1000_YYYYMMDD.csv`
- `universe_core10_dynamic40_price_risk_pm_YYYYMMDD.csv`
- `universe_core10_dynamic40_price_risk_pm_refresh1430_YYYYMMDD.csv`

Columns: `refresh_time`, `universe_slot`, `source_bucket`, `is_open_position_carried`, `price_risk_flag`, `tick_ratio_pct`.

## Safety constraints (unchanged for shadow)

- `order_enabled=false`, `paper_only=true`
- Production YAML not modified in Phase 156
- `safety.check_max_concurrent` still caps at 3 until shadow-only relaxation lands

## Daily runner timing (proposed, not wired)

| Slot | Time (JST) | Action |
|------|------------|--------|
| AM initial | 09:00 | universe + register |
| AM refresh | 10:00 | refresh CSV + register merge |
| PM initial | 12:25 | universe + register |
| PM refresh | 14:30 | refresh CSV + register merge |
"""


def build_recommendation_md(
    *,
    verdict: str,
    notes: Sequence[str],
    cap_agg: Sequence[Mapping[str, Any]],
    scenarios: Sequence[Mapping[str, Any]],
) -> str:
    lines = [
        "# Phase 156 recommendation",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        "## Summary",
        "",
        "- Review-only: no production YAML, no live refresh wiring.",
        "- Entry policy: price-risk universe filter + `entry_price_risk_guard` on candidates.",
        "- Refresh exit policy: **candidate 1** (hold positions; update new-entry universe only).",
        "- Register: always include `open_symbols` before Core10/Dynamic trim to 50.",
        "",
        "## Cap comparison (price-risk guarded candidates)",
        "",
    ]
    for row in cap_agg:
        lines.append(
            f"- cap={row['max_concurrent']}: accepted={row['accepted_count']}, "
            f"pnl_proxy={row['total_pnl_proxy']}, pf_mean={row.get('pf_proxy_mean')}, "
            f"overlap_proxy={row['overlap_replaced_proxy_count']}, "
            f"delta_vs_cap3={row.get('total_pnl_delta_vs_cap3', 0)}"
        )
    lines.extend(["", "## Scenario what-if (S0–S3)", ""])
    for row in scenarios:
        lines.append(
            f"- {row['scenario_id']} ({row['label']}): pnl_proxy={row['total_pnl_proxy']}, "
            f"with_refresh={row.get('total_pnl_with_refresh_proxy')}, "
            f"uplift={row.get('refresh_uplift_pnl_proxy', 0)}"
        )
    lines.extend(["", "## Notes", ""])
    for n in notes:
        lines.append(f"- {n}")
    lines.extend(
        [
            "",
            "## Methodology caveats",
            "",
            "- Cap/overlap metrics are **counterfactual ExposureGate** sims (virtual-hold PnL), not live fills.",
            "- Refresh uplift uses **15% of positive** post-10:00/14:30 MC-reject would-be PnL (upper-bound proxy).",
            "- True refresh benefit needs `*_refresh1000_*` / `*_refresh1430_*` universe CSVs + shadow days.",
            "",
            "## Next steps",
            "",
            "1. Shadow YAML added: `small_paper_pilot_q070_cap5_entry_price_risk_guard_shadow.yaml` (do not wire prod).",
            "2. Implement register merge (open_symbols > Core10 > Dynamic) before any refresh live trial.",
            "3. Implement refresh CSV writers + daily_runner feature flag (candidate 1 exit policy).",
            "4. Run cap5 shadow only after overlap policy review; refresh before cap5 if verdict C holds.",
        ]
    )
    return "\n".join(lines) + "\n"


def analyze_phase156(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
    min_quality: float = PHASE53_MIN_QUALITY,
) -> dict[str, Any]:
    per_cap_rows: list[dict[str, Any]] = []
    scenarios = {
        "S0": ScenarioAggregate("S0", "AM/PM only cap3", 3, False),
        "S1": ScenarioAggregate("S1", "AM/PM only cap5", 5, False),
        "S2": ScenarioAggregate("S2", "10:00/14:30 refresh proxy cap3", 3, True),
        "S3": ScenarioAggregate("S3", "10:00/14:30 refresh proxy cap5", 5, True),
    }
    register_issues: list[str] = []
    refresh_symbol_stats: list[dict[str, Any]] = []

    for sdir in session_dirs:
        sdir = Path(sdir)
        events = _load_events(sdir)
        if not events:
            continue
        summary = _load_json(sdir / "small_paper_summary.json")
        session_id = str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        structural = _structural_metrics(sdir)

        raw_candidates = _candidates_from_events(events)
        candidates, guard_rejects, operational_skips = _filter_price_risk_candidates(
            raw_candidates, min_quality=min_quality
        )
        price_index = _build_price_index(events)
        profile = str(summary.get("profile") or getattr(pilot_config, "profile", ""))
        allowed_windows = pilot_config.allowed_windows() if pilot_config else None

        sym_stats = _first_seen_refresh_symbols(candidates)
        refresh_symbol_stats.append({"session_id": session_id, **{k: len(v) for k, v in sym_stats.items()}})

        open_positions_path = sdir / "small_paper_positions.csv"
        open_count = 0
        if open_positions_path.is_file():
            with open_positions_path.open(encoding="utf-8", newline="") as f:
                open_count = sum(1 for row in csv.DictReader(f) if str(row.get("status") or "").lower() == "open")
        peak_open = max(open_count, int(structural.get("structural_trade_count") or 0) // 20)
        issue = _register_plan_issue(open_count=peak_open)
        if issue:
            register_issues.append(f"{session_id}:{issue}")

        cap_results: dict[int, Any] = {}
        for cap in PHASE156_CAPS:
            result = _simulate_cap_scenario(
                candidates,
                min_quality=min_quality,
                max_concurrent=cap,
                profile=profile,
                price_index=price_index,
                allowed_windows=allowed_windows,
            )
            cap_results[cap] = result
            per_cap_rows.append(_cap_row_metrics(result, session_id=session_id, structural=structural))

        for sid, sc in scenarios.items():
            cap = sc.max_concurrent
            row = _cap_row_metrics(cap_results[cap], session_id=session_id, structural=structural)
            if sc.intraday_refresh:
                proxy = _post_refresh_mc_proxy(cap_results[cap].mc_reject_rows)
                row.update(proxy)
            sc.per_session.append(row)

    cap_aggregate = _aggregate_cap_rows(per_cap_rows)
    scenario_rows = [sc.aggregate() for sc in scenarios.values()]
    verdict, notes = determine_verdict(
        cap_agg=cap_aggregate,
        scenarios=scenario_rows,
        session_count=len({r["session_id"] for r in per_cap_rows if r.get("max_concurrent") == 3}),
        register_issues=register_issues,
    )

    design = {
        "phase": 156,
        "review_only": True,
        "production_yaml_modified": False,
        "order_enabled": False,
        "paper_only": True,
        "verdict": verdict,
        "verdict_options": {
            "A": "refresh_cap5_shadow_ready",
            "B": "cap5_promising_refresh_not_needed",
            "C": "refresh_promising_cap3_enough",
            "D": "open_position_register_issue",
            "E": "more_data_needed",
        },
        "verdict_notes": notes,
        "session_count": len({r["session_id"] for r in per_cap_rows}),
        "constraints": {
            "core10_maintained": True,
            "price_risk_universe_filter": True,
            "entry_price_risk_guard": True,
            "refresh_exit_policy": "candidate_1_no_forced_exit",
            "register_open_symbols_priority": True,
        },
        "universe_refresh_schedule_proposed": {
            "am_initial": "09:00",
            "am_refresh": "10:00",
            "pm_initial": "12:25",
            "pm_refresh": "14:30",
        },
        "caps_compared": list(PHASE156_CAPS),
        "cap_aggregate": cap_aggregate,
        "scenarios": scenario_rows,
        "refresh_symbol_stats": refresh_symbol_stats,
        "register_issues": register_issues,
        "methodology": {
            "cap_simulation": "ExposureGate counterfactual with virtual_hold pnl",
            "candidate_pool": (
                f"q>={min_quality}, daytrade_suitability pass, entry_price_risk_guard"
            ),
            "entry_guard": "reject candidates price<50 or tick_ratio>5% before sim",
            "refresh_proxy": (
                f"post_refresh_window positive MC-reject would_be_pnl * {REFRESH_CAPTURE_RATE}; "
                "true refresh benefit requires refresh-time universe CSVs"
            ),
            "overlap_proxy": "same_symbol_overlap_accept_count",
            "structural_overlap": "overlap_replaced_review from structural_trades.csv",
        },
        "shadow_config_proposed": "small_paper_pilot_q070_cap5_entry_price_risk_guard_shadow.yaml",
    }
    return {
        "design": design,
        "per_cap_rows": per_cap_rows,
        "scenario_rows": scenario_rows,
        "register_issues": register_issues,
        "verdict": verdict,
        "verdict_notes": notes,
    }


def write_phase156_outputs(
    result: Mapping[str, Any],
    *,
    reports_dir: Path,
    docs_dir: Path,
) -> dict[str, str]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    docs_dir.mkdir(parents=True, exist_ok=True)
    design = result["design"]

    json_path = reports_dir / "phase156_intraday_refresh_cap5_design.json"
    whatif_csv = reports_dir / "phase156_refresh_policy_whatif.csv"
    cap_csv = reports_dir / "phase156_cap3_cap5_comparison.csv"
    reg_md = docs_dir / "phase156_register_refresh_design.md"
    rec_md = docs_dir / "phase156_recommendation.md"

    json_path.write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(whatif_csv, result["scenario_rows"])
    _write_csv(cap_csv, design["cap_aggregate"])
    reg_md.write_text(build_register_design_md(), encoding="utf-8")
    rec_md.write_text(
        build_recommendation_md(
            verdict=str(result["verdict"]),
            notes=result["verdict_notes"],
            cap_agg=design["cap_aggregate"],
            scenarios=design["scenarios"],
        ),
        encoding="utf-8",
    )
    return {
        "json": str(json_path),
        "whatif_csv": str(whatif_csv),
        "cap_csv": str(cap_csv),
        "register_md": str(reg_md),
        "recommendation_md": str(rec_md),
    }
