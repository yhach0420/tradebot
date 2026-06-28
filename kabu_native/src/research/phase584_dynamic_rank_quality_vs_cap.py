"""
Phase584 — Dynamic Rank Quality vs CAP Competition Analysis (research only).

Separates whether Dynamic26-40 underperformance is due to symbol quality (A) or
CAP competition blocking rank1-25 entries (B). No Runtime / Universe changes.
"""

from __future__ import annotations

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _pf, _write_csv, read_jpx_sector_map
from research.market_sector_heat_universe_shadow import (
    core_symbols_from_universe,
    dynamic_rank_map_from_universe,
    load_universe_csv,
    resolve_am_universe_path,
)
from research.phase382_capital_constrained_backtest import _parse_ts
from research.phase451_entry_shape_tournament import JST, _now_iso
from research.phase524_live_reentry_guard_and_stop_low_mfe import _is_stop_low_mfe, _latest_live_day
from research.phase530_winner_capture_research import _sym_key
from research.phase533_or_profit_source_audit import _num
from research.phase540_no_progress_mfe0_entry_quality import (
    _is_mfe0,
    _load_canonical_trades_for_day,
    _mfe_pct,
)
from research.phase582_universe_optimization_study import (
    PERIOD_START,
    _discover_days,
    _load_day_accepted,
    _load_day_trades,
    _price_band,
)
from research.small_paper_performance_review import _load_events
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir

PHASE584_VERDICT = "phase584_dynamic_rank_quality_vs_cap_done"
CAP = 5
BASELINE_DYNAMIC = 40
RANK_BUCKETS: tuple[tuple[str, int, int], ...] = (
    ("rank_1_5", 1, 5),
    ("rank_6_10", 6, 10),
    ("rank_11_15", 11, 15),
    ("rank_16_20", 16, 20),
    ("rank_21_25", 21, 25),
    ("rank_26_30", 26, 30),
    ("rank_31_35", 31, 35),
    ("rank_36_40", 36, 40),
    ("rank_41_45", 41, 45),
    ("rank_46_50", 46, 50),
)

RANK_QUALITY_FIELDS = [
    "rank_bucket",
    "rank_lo",
    "rank_hi",
    "trades",
    "accepted",
    "win_rate",
    "pnl_yen_100",
    "profit_factor",
    "avg_pnl_yen_100",
    "stop_hit_count",
    "stop_low_mfe_count",
    "avg_mfe_pct",
    "expectancy_yen_100",
]

D2640_FIELDS = [
    "dimension",
    "key",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "pnl_share_pct",
]

CAP_COMP_FIELDS = [
    "metric",
    "value",
    "detail",
]

COUNTERFACTUAL_FIELDS = [
    "scenario",
    "cap",
    "dynamic_rank_max",
    "accepted_count",
    "blocked_count",
    "blocked_rank1_25_count",
    "cap_competition_blocks",
    "pnl_yen_100",
    "profit_factor",
    "delta_accepted_vs_baseline",
    "delta_pnl_vs_baseline",
]

RANK_EXPECTANCY_FIELDS = [
    "dynamic_rank",
    "trades",
    "avg_pnl_yen_100",
    "expectancy_yen_100",
    "profit_factor",
    "win_rate",
    "cumulative_pnl_yen_100",
    "cumulative_expectancy_yen_100",
    "cumulative_pf",
]

RANK_STATS_FIELDS = [
    "cohort",
    "rank_lo",
    "rank_hi",
    "trades",
    "pnl_yen_100",
    "profit_factor",
    "win_rate",
    "avg_mfe_pct",
    "stop_hit_count",
    "stop_low_mfe_count",
    "delta_pf_vs_other",
    "delta_pnl_per_trade_vs_other",
]


def _norm_rank(sym: str, rank_map: Mapping[str, int]) -> Optional[int]:
    s = _sym_key(sym)
    if not s:
        return None
    if s in rank_map:
        return rank_map[s]
    if f"{s}.T" in rank_map:
        return rank_map[f"{s}.T"]
    return None


def _bucket_for_rank(rank: Optional[int]) -> str:
    if rank is None or rank <= 0:
        return "rank_unknown"
    for label, lo, hi in RANK_BUCKETS:
        if lo <= rank <= hi:
            return label
    return "rank_unknown"


def _build_day_rank_maps(reports_dir: Path, days: Sequence[str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for day in days:
        path = resolve_am_universe_path(reports_dir, day)
        if not path:
            continue
        universe = load_universe_csv(path)
        if not universe:
            continue
        rank_map = dynamic_rank_map_from_universe(universe)
        normalized = {_sym_key(k): v for k, v in rank_map.items()}
        out[day] = normalized
    return out


def _build_day_core_sets(reports_dir: Path, days: Sequence[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for day in days:
        path = resolve_am_universe_path(reports_dir, day)
        if not path:
            continue
        universe = load_universe_csv(path)
        if universe:
            out[day] = {_sym_key(s) for s in core_symbols_from_universe(universe)}
    return out


def _enrich_trade(trade: Mapping[str, Any], rank_maps: Mapping[str, Mapping[str, int]], core_sets: Mapping[str, set[str]]) -> dict[str, Any]:
    row = dict(trade)
    day = str(row.get("day") or "")[:8]
    sym = _sym_key(row.get("symbol"))
    core = core_sets.get(day, set())
    if sym in core:
        row["dynamic_rank"] = None
        row["is_dynamic"] = False
    else:
        row["dynamic_rank"] = _norm_rank(sym, rank_maps.get(day, {}))
        row["is_dynamic"] = row["dynamic_rank"] is not None
    row["rank_bucket"] = _bucket_for_rank(row.get("dynamic_rank"))
    px = _num(row.get("entry_price") or row.get("price") or 0)
    row["price_band"] = _price_band(px)
    row["mfe_pct_val"] = _mfe_pct(row)
    return row


def _metrics_bundle(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pnls = [_num(t.get("pnl_yen_100")) for t in trades]
    mfes = [_num(t.get("mfe_pct_val")) for t in trades]
    wins = sum(1 for p in pnls if p > 0)
    total = round(sum(pnls), 2)
    n = len(pnls)
    return {
        "trades": n,
        "pnl_yen_100": total,
        "profit_factor": round(_pf(pnls) or 0.0, 4),
        "win_rate": round(wins / n, 4) if n else 0.0,
        "avg_pnl_yen_100": round(total / n, 2) if n else 0.0,
        "stop_hit_count": sum(1 for t in trades if str(t.get("exit_reason") or "").lower() == "stop_hit"),
        "stop_low_mfe_count": sum(1 for t in trades if _is_stop_low_mfe(t)),
        "mfe0_count": sum(1 for t in trades if _is_mfe0(t)),
        "avg_mfe_pct": round(sum(mfes) / n, 4) if n else 0.0,
        "expectancy_yen_100": round(total / n, 2) if n else 0.0,
    }


def _load_session_accepted_candidates(
    repo_root: Path,
    day: str,
    session_dir: Path,
    trade_pnl_index: Mapping[tuple[str, str], float],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for ev in _load_events(session_dir):
        if str(ev.get("event_type") or "") != "accepted":
            continue
        sym = str(ev.get("symbol") or "")
        entry_time = str(ev.get("entry_time") or "")
        exit_time = str(ev.get("exit_time") or entry_time)
        bucket = str(ev.get("universe_bucket") or ev.get("source_bucket") or ev.get("universe_slot") or "")
        pnl = trade_pnl_index.get((_sym_key(sym), entry_time), 0.0)
        rows.append(
            {
                "day": day,
                "session_dir": str(session_dir),
                "symbol": sym,
                "symbol_key": _sym_key(sym),
                "entry_time": entry_time,
                "exit_time": exit_time,
                "universe_bucket": bucket,
                "is_core": "core" in bucket.lower(),
                "pnl_yen_100": pnl,
            }
        )
    return rows


def _load_session_cap_rejects(session_dir: Path, day: str) -> list[dict[str, Any]]:
    path = session_dir / "entry_scan_audit.jsonl"
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if row.get("audit_type") != "entry_symbol_eval":
                continue
            if str(row.get("reject_reason") or "") != "max_concurrent":
                continue
            rows.append(
                {
                    "day": day,
                    "symbol": str(row.get("symbol") or ""),
                    "symbol_key": _sym_key(row.get("symbol")),
                    "eval_start_ts": str(row.get("eval_start_ts") or ""),
                }
            )
    return rows


def _parse_entry_ts(entry_time: str) -> Optional[datetime]:
    return _parse_ts(entry_time)


def _open_positions_at(
    accepted_timeline: Sequence[Mapping[str, Any]],
    at_ts: datetime,
) -> list[dict[str, Any]]:
    at = at_ts.timestamp()
    open_rows: list[dict[str, Any]] = []
    for row in accepted_timeline:
        ent = _parse_entry_ts(str(row.get("entry_time") or ""))
        ex = _parse_entry_ts(str(row.get("exit_time") or ""))
        if ent is None:
            continue
        ex_ts = ex.timestamp() if ex else ent.timestamp() + 300
        if ent.timestamp() <= at < ex_ts:
            open_rows.append(dict(row))
    return open_rows


@dataclass
class _CapSimResult:
    accepted: list[dict[str, Any]]
    blocked: list[dict[str, Any]]
    cap_competition_blocks: list[dict[str, Any]]


def _simulate_cap(
    candidates: Sequence[Mapping[str, Any]],
    *,
    cap: int = CAP,
    dynamic_rank_max: Optional[int] = None,
    track_competition: bool = False,
) -> _CapSimResult:
    ordered = sorted(
        candidates,
        key=lambda c: _parse_entry_ts(str(c.get("entry_time") or ""))
        or datetime.min.replace(tzinfo=JST),
    )
    open_slots: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    cap_comp: list[dict[str, Any]] = []

    for c in ordered:
        ent = _parse_entry_ts(str(c.get("entry_time") or ""))
        ex = _parse_entry_ts(str(c.get("exit_time") or ""))
        if ent is None:
            continue
        ent_f = ent.timestamp()
        ex_f = ex.timestamp() if ex else ent_f + 300
        open_slots = [s for s in open_slots if (_parse_entry_ts(str(s.get("exit_time") or "")) or ent).timestamp() > ent_f]

        rank = c.get("dynamic_rank")
        is_dynamic = bool(c.get("is_dynamic"))
        if dynamic_rank_max is not None and is_dynamic and rank is not None and rank > dynamic_rank_max:
            continue

        if len(open_slots) >= cap:
            blocked.append(dict(c))
            if track_competition:
                dr = rank if is_dynamic else None
                if dr is not None and dr <= 25:
                    low_rank_open = [
                        s for s in open_slots
                        if s.get("is_dynamic") and s.get("dynamic_rank") is not None and int(s["dynamic_rank"]) >= 26
                    ]
                    if low_rank_open:
                        cap_comp.append(
                            {
                                **dict(c),
                                "open_low_rank_symbols": [s.get("symbol_key") for s in low_rank_open],
                                "open_count": len(open_slots),
                            }
                        )
            continue

        slot = dict(c)
        slot["exit_time"] = str(c.get("exit_time") or "")
        open_slots.append(slot)
        accepted.append(slot)

    return _CapSimResult(accepted=accepted, blocked=blocked, cap_competition_blocks=cap_comp)


def _aggregate_dimension(
    trades: Sequence[Mapping[str, Any]],
    key_fn,
    sector_map: Mapping[str, str],
) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trades:
        groups[str(key_fn(t))].append(t)
    total = sum(_num(t.get("pnl_yen_100")) for t in trades) or 1.0
    rows: list[dict[str, Any]] = []
    for key, grp in sorted(groups.items(), key=lambda kv: sum(_num(t.get("pnl_yen_100")) for t in kv[1]), reverse=True):
        m = _metrics_bundle(grp)
        rows.append(
            {
                "dimension": "sector" if key in sector_map.values() or key != "unknown" else "price_band",
                "key": key,
                "trades": m["trades"],
                "pnl_yen_100": m["pnl_yen_100"],
                "profit_factor": m["profit_factor"],
                "win_rate": m["win_rate"],
                "pnl_share_pct": round(100.0 * m["pnl_yen_100"] / total, 2),
            }
        )
    return rows


@dataclass
class Phase584Job:
    repo_root: Path
    workers: int = 4

    def run(self) -> dict[str, Any]:
        days = _discover_days(self.repo_root)
        end = _latest_live_day(self.repo_root)
        days = [d for d in days if d <= end]
        kabu = resolve_kabu_root(self.repo_root)
        reports_dir = resolve_reports_dir(self.repo_root)
        sector_map = read_jpx_sector_map(kabu)
        rank_maps = _build_day_rank_maps(reports_dir, days)
        core_sets = _build_day_core_sets(reports_dir, days)

        all_trades: list[dict[str, Any]] = []
        all_accepted: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            for fut in as_completed({ex.submit(_load_day_trades, self.repo_root, d): d for d in days}):
                all_trades.extend(fut.result())
            for fut in as_completed({ex.submit(_load_day_accepted, self.repo_root, d): d for d in days}):
                all_accepted.extend(fut.result())

        trade_pnl_index = {
            (_sym_key(t.get("symbol")), str(t.get("entry_time") or "")): _num(t.get("pnl_yen_100"))
            for t in all_trades
        }

        enriched = [_enrich_trade(t, rank_maps, core_sets) for t in all_trades]
        dynamic_trades = [t for t in enriched if t.get("is_dynamic") and t.get("dynamic_rank") is not None]

        # Tag accepted with rank
        accepted_tagged: list[dict[str, Any]] = []
        for a in all_accepted:
            day = str(a.get("day") or "")[:8]
            sym = str(a.get("symbol_key") or "")
            rank = _norm_rank(sym, rank_maps.get(day, {}))
            core = sym in core_sets.get(day, set())
            accepted_tagged.append(
                {
                    **dict(a),
                    "dynamic_rank": rank if not core else None,
                    "is_dynamic": rank is not None and not core,
                    "rank_bucket": _bucket_for_rank(rank if not core else None),
                }
            )

        dynamic_accepted = [a for a in accepted_tagged if a.get("is_dynamic")]

        # Investigation 1 — rank bucket quality
        rank_quality_rows: list[dict[str, Any]] = []
        for label, lo, hi in RANK_BUCKETS:
            bucket_trades = [
                t for t in dynamic_trades
                if t.get("dynamic_rank") is not None and lo <= int(t["dynamic_rank"]) <= hi
            ]
            bucket_accepted = [
                a for a in dynamic_accepted
                if a.get("dynamic_rank") is not None and lo <= int(a["dynamic_rank"]) <= hi
            ]
            m = _metrics_bundle(bucket_trades)
            rank_quality_rows.append(
                {
                    "rank_bucket": label,
                    "rank_lo": lo,
                    "rank_hi": hi,
                    "accepted": len(bucket_accepted),
                    **m,
                }
            )

        # Investigation 2 — ranks 26-40 deep dive
        d2640_trades = [
            t for t in dynamic_trades
            if t.get("dynamic_rank") is not None and 26 <= int(t["dynamic_rank"]) <= 40
        ]
        d2640_metrics = _metrics_bundle(d2640_trades)
        d2640_rows: list[dict[str, Any]] = [
            {
                "dimension": "summary",
                "key": "rank_26_40",
                "trades": d2640_metrics["trades"],
                "pnl_yen_100": d2640_metrics["pnl_yen_100"],
                "profit_factor": d2640_metrics["profit_factor"],
                "win_rate": d2640_metrics["win_rate"],
                "pnl_share_pct": round(
                    100.0 * d2640_metrics["pnl_yen_100"] / (sum(_num(t.get("pnl_yen_100")) for t in dynamic_trades) or 1.0),
                    2,
                ),
            }
        ]
        total_d = sum(_num(t.get("pnl_yen_100")) for t in d2640_trades) or 1.0
        for key_fn, dim in ((lambda t: t.get("price_band"), "price_band"), (lambda t: sector_map.get(_sym_key(t.get("symbol")), "unknown"), "sector")):
            for r in _aggregate_dimension(d2640_trades, key_fn, sector_map):
                r["dimension"] = dim
                d2640_rows.append(r)
        winners = sorted(d2640_trades, key=lambda t: _num(t.get("pnl_yen_100")), reverse=True)[:5]
        losers = sorted(d2640_trades, key=lambda t: _num(t.get("pnl_yen_100")))[:5]
        for t in winners:
            d2640_rows.append(
                {
                    "dimension": "profit_source",
                    "key": f"{t.get('symbol')} rank={t.get('dynamic_rank')}",
                    "trades": 1,
                    "pnl_yen_100": _num(t.get("pnl_yen_100")),
                    "profit_factor": 0.0,
                    "win_rate": 1.0 if _num(t.get("pnl_yen_100")) > 0 else 0.0,
                    "pnl_share_pct": round(100.0 * _num(t.get("pnl_yen_100")) / total_d, 2),
                }
            )
        for t in losers:
            d2640_rows.append(
                {
                    "dimension": "loss_source",
                    "key": f"{t.get('symbol')} rank={t.get('dynamic_rank')}",
                    "trades": 1,
                    "pnl_yen_100": _num(t.get("pnl_yen_100")),
                    "profit_factor": 0.0,
                    "win_rate": 0.0,
                    "pnl_share_pct": round(100.0 * _num(t.get("pnl_yen_100")) / total_d, 2),
                }
            )

        # Build accepted candidates for CAP sim (core + dynamic)
        cap_candidates: list[dict[str, Any]] = []
        cap_rejects_raw: list[dict[str, Any]] = []
        sp = kabu / "results" / "small_paper"
        for day in days:
            day_dir = sp / day
            if not day_dir.is_dir():
                continue
            for sess_dir in sorted(day_dir.glob("live_session_*")):
                cap_candidates.extend(_load_session_accepted_candidates(self.repo_root, day, sess_dir, trade_pnl_index))
                cap_rejects_raw.extend(_load_session_cap_rejects(sess_dir, day))

        for c in cap_candidates:
            day = str(c.get("day") or "")[:8]
            sym = str(c.get("symbol_key") or "")
            if c.get("is_core") or "core" in str(c.get("universe_bucket") or "").lower():
                c["dynamic_rank"] = None
                c["is_dynamic"] = False
            else:
                c["dynamic_rank"] = _norm_rank(sym, rank_maps.get(day, {}))
                c["is_dynamic"] = c["dynamic_rank"] is not None

        # Investigation 3 — CAP competition from audit + simulation
        audit_competition = 0
        audit_rank1_25_cap = 0
        lost_pnl_audit = 0.0
        for rej in cap_rejects_raw:
            day = str(rej.get("day") or "")[:8]
            sym = str(rej.get("symbol_key") or "")
            rank = _norm_rank(sym, rank_maps.get(day, {}))
            if rank is None or rank > 25:
                continue
            audit_rank1_25_cap += 1
            ts = _parse_ts(str(rej.get("eval_start_ts") or ""))
            if ts is None:
                continue
            open_at = _open_positions_at(cap_candidates, ts)
            low_open = [
                s for s in open_at
                if s.get("is_dynamic") and s.get("dynamic_rank") is not None and int(s["dynamic_rank"]) >= 26
            ]
            if low_open and len(open_at) >= CAP:
                audit_competition += 1

        baseline_sim = _simulate_cap(cap_candidates, cap=CAP, dynamic_rank_max=None, track_competition=True)
        top25_sim = _simulate_cap(cap_candidates, cap=CAP, dynamic_rank_max=25, track_competition=False)

        baseline_pnls = [_num(t.get("pnl_yen_100")) for t in baseline_sim.accepted]
        top25_pnls = [_num(t.get("pnl_yen_100")) for t in top25_sim.accepted]
        blocked_rank1_25 = [
            b for b in baseline_sim.blocked
            if b.get("is_dynamic") and b.get("dynamic_rank") is not None and int(b["dynamic_rank"]) <= 25
        ]
        comp_blocks = baseline_sim.cap_competition_blocks
        comp_lost_pnl = sum(_num(b.get("pnl_yen_100")) for b in comp_blocks)

        cap_comp_rows = [
            {"metric": "audit_max_concurrent_total", "value": len(cap_rejects_raw), "detail": "entry_scan_audit reject_reason=max_concurrent"},
            {"metric": "audit_rank1_25_max_concurrent", "value": audit_rank1_25_cap, "detail": "rank1-25 symbols rejected for max_concurrent"},
            {"metric": "audit_cap_competition_with_rank26_40_open", "value": audit_competition, "detail": "rank1-25 cap reject while rank26-40 occupied slot"},
            {"metric": "sim_cap_competition_blocks", "value": len(comp_blocks), "detail": "CAP=5 sim: rank1-25 blocked with rank26-40 open"},
            {"metric": "sim_blocked_rank1_25_total", "value": len(blocked_rank1_25), "detail": "CAP=5 sim blocked rank1-25 entries"},
            {"metric": "sim_cap_competition_lost_pnl_yen_100", "value": round(comp_lost_pnl, 2), "detail": "PnL of sim cap-competition blocked trades (if they had entered)"},
            {"metric": "sim_baseline_accepted", "value": len(baseline_sim.accepted), "detail": "CAP=5 all accepted candidates"},
            {"metric": "sim_top25_only_accepted", "value": len(top25_sim.accepted), "detail": "CAP=5 rank<=25 dynamic only"},
        ]

        # Investigation 4 — counterfactual
        counterfactual_rows = []
        for scenario, sim, rank_max in (
            ("baseline_all_dynamic40", baseline_sim, None),
            ("counterfactual_dynamic25_only", top25_sim, 25),
        ):
            pnls = [_num(t.get("pnl_yen_100")) for t in sim.accepted]
            counterfactual_rows.append(
                {
                    "scenario": scenario,
                    "cap": CAP,
                    "dynamic_rank_max": rank_max if rank_max is not None else 40,
                    "accepted_count": len(sim.accepted),
                    "blocked_count": len(sim.blocked),
                    "blocked_rank1_25_count": len(
                        [b for b in sim.blocked if b.get("is_dynamic") and b.get("dynamic_rank") is not None and int(b["dynamic_rank"]) <= 25]
                    ),
                    "cap_competition_blocks": len(sim.cap_competition_blocks) if scenario.startswith("baseline") else 0,
                    "pnl_yen_100": round(sum(pnls), 2),
                    "profit_factor": round(_pf(pnls) or 0.0, 4),
                    "delta_accepted_vs_baseline": 0,
                    "delta_pnl_vs_baseline": 0.0,
                }
            )
        counterfactual_rows[1]["delta_accepted_vs_baseline"] = counterfactual_rows[1]["accepted_count"] - counterfactual_rows[0]["accepted_count"]
        counterfactual_rows[1]["delta_pnl_vs_baseline"] = round(
            float(counterfactual_rows[1]["pnl_yen_100"]) - float(counterfactual_rows[0]["pnl_yen_100"]), 2
        )

        # Investigation 5 — rank expectancy
        by_rank: dict[int, list[float]] = defaultdict(list)
        for t in dynamic_trades:
            r = t.get("dynamic_rank")
            if r is not None:
                by_rank[int(r)].append(_num(t.get("pnl_yen_100")))
        rank_expectancy_rows: list[dict[str, Any]] = []
        cum_pnl = 0.0
        cum_trades = 0
        for r in range(1, 51):
            pnls = by_rank.get(r, [])
            cum_pnl += sum(pnls)
            cum_trades += len(pnls)
            wins = sum(1 for p in pnls if p > 0)
            rank_expectancy_rows.append(
                {
                    "dynamic_rank": r,
                    "trades": len(pnls),
                    "avg_pnl_yen_100": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
                    "expectancy_yen_100": round(sum(pnls) / len(pnls), 2) if pnls else 0.0,
                    "profit_factor": round(_pf(pnls) or 0.0, 4) if pnls else 0.0,
                    "win_rate": round(wins / len(pnls), 4) if pnls else 0.0,
                    "cumulative_pnl_yen_100": round(cum_pnl, 2),
                    "cumulative_expectancy_yen_100": round(cum_pnl / cum_trades, 2) if cum_trades else 0.0,
                    "cumulative_pf": round(_pf([p for rr in range(1, r + 1) for p in by_rank.get(rr, [])]) or 0.0, 4) if cum_trades else 0.0,
                }
            )

        # Investigation 6 — 1-25 vs 26-50 statistics
        def _cohort_metrics(lo: int, hi: int) -> dict[str, Any]:
            grp = [t for t in dynamic_trades if t.get("dynamic_rank") is not None and lo <= int(t["dynamic_rank"]) <= hi]
            return _metrics_bundle(grp)

        m125 = _cohort_metrics(1, 25)
        m2650 = _cohort_metrics(26, 50)
        rank_stats_rows = []
        for cohort, lo, hi, m in (
            ("rank_1_25", 1, 25, m125),
            ("rank_26_50", 26, 50, m2650),
        ):
            rank_stats_rows.append(
                {
                    "cohort": cohort,
                    "rank_lo": lo,
                    "rank_hi": hi,
                    **m,
                    "delta_pf_vs_other": 0.0,
                    "delta_pnl_per_trade_vs_other": 0.0,
                }
            )
        rank_stats_rows[0]["delta_pf_vs_other"] = round(float(m125["profit_factor"]) - float(m2650["profit_factor"]), 4)
        rank_stats_rows[1]["delta_pf_vs_other"] = round(float(m2650["profit_factor"]) - float(m125["profit_factor"]), 4)
        rank_stats_rows[0]["delta_pnl_per_trade_vs_other"] = round(
            float(m125["avg_pnl_yen_100"]) - float(m2650["avg_pnl_yen_100"]), 2
        )
        rank_stats_rows[1]["delta_pnl_per_trade_vs_other"] = round(
            float(m2650["avg_pnl_yen_100"]) - float(m125["avg_pnl_yen_100"]), 2
        )

        # Mandatory answers
        # Dynamic30 check from rank expectancy cumulative
        cum30 = next((r for r in rank_expectancy_rows if r["dynamic_rank"] == 30), {})
        cum25 = next((r for r in rank_expectancy_rows if r["dynamic_rank"] == 25), {})
        d30_better = float(cum30.get("cumulative_pf") or 0) > float(cum25.get("cumulative_pf") or 0)

        d2640_weak = float(m2650["profit_factor"] or 0) < float(m125["profit_factor"] or 0) and float(m2650["pnl_yen_100"] or 0) < float(m125["pnl_yen_100"] or 0)
        cap_events = int(audit_competition) + len(comp_blocks)
        quality_problem = d2640_weak
        cap_problem = cap_events >= 5 or abs(comp_lost_pnl) >= 10000
        if quality_problem and cap_problem:
            reason = "both_quality_and_cap"
            next_phase = "phase585_universe_and_entry_priority_research"
        elif quality_problem:
            reason = "quality_decay_rank26_plus"
            next_phase = "phase585_universe_size_shadow_adoption"
        elif cap_problem:
            reason = "cap_competition"
            next_phase = "phase585_entry_priority_cap_research"
        else:
            reason = "mixed_inconclusive"
            next_phase = "phase585_universe_monitor"

        mandatory = {
            "1_rank26_40_weak": d2640_weak,
            "2_cap_competition_events": cap_events,
            "3_cap_competition_pnl_loss_yen_100": round(comp_lost_pnl, 2),
            "4_dynamic25_better_reason": reason,
            "5_quality_problem": quality_problem,
            "6_cap_problem": cap_problem,
            "7_dynamic30_improves": d30_better,
            "8_dynamic25_adoption_sufficient": quality_problem and float(m125["pnl_yen_100"] or 0) > abs(float(m2650["pnl_yen_100"] or 0)),
            "9_runtime_change_candidate": False,
            "10_next_phase": next_phase,
            "rank_1_25_pf": m125["profit_factor"],
            "rank_1_25_pnl": m125["pnl_yen_100"],
            "rank_26_40_pf": m2650["profit_factor"],
            "rank_26_40_pnl": m2650["pnl_yen_100"],
            "rank_26_40_trades": m2650["trades"],
            "counterfactual_delta_pnl": counterfactual_rows[1]["delta_pnl_vs_baseline"],
            "counterfactual_delta_accepted": counterfactual_rows[1]["delta_accepted_vs_baseline"],
            "period_start": PERIOD_START,
            "period_end": end,
        }

        return {
            "verdict": PHASE584_VERDICT,
            "all_pass": len(dynamic_trades) > 0,
            "rank_quality_rows": rank_quality_rows,
            "d2640_rows": d2640_rows,
            "cap_comp_rows": cap_comp_rows,
            "counterfactual_rows": counterfactual_rows,
            "rank_expectancy_rows": rank_expectancy_rows,
            "rank_stats_rows": rank_stats_rows,
            "mandatory_answers": mandatory,
            "generated_at": _now_iso(),
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        kabu = resolve_kabu_root(self.repo_root)
        paths = {
            "rank_quality": reports / "phase584_dynamic_rank_quality.csv",
            "d2640": reports / "phase584_dynamic26_40_analysis.csv",
            "cap_comp": reports / "phase584_cap_competition.csv",
            "counterfactual": reports / "phase584_counterfactual.csv",
            "rank_expectancy": reports / "phase584_rank_expectancy.csv",
            "rank_stats": reports / "phase584_rank_statistics.csv",
            "report": reports / "phase584_report.json",
        }
        _write_csv(paths["rank_quality"], RANK_QUALITY_FIELDS, list(result.get("rank_quality_rows") or []))
        _write_csv(paths["d2640"], D2640_FIELDS, list(result.get("d2640_rows") or []))
        _write_csv(paths["cap_comp"], CAP_COMP_FIELDS, list(result.get("cap_comp_rows") or []))
        _write_csv(paths["counterfactual"], COUNTERFACTUAL_FIELDS, list(result.get("counterfactual_rows") or []))
        _write_csv(paths["rank_expectancy"], RANK_EXPECTANCY_FIELDS, list(result.get("rank_expectancy_rows") or []))
        _write_csv(paths["rank_stats"], RANK_STATS_FIELDS, list(result.get("rank_stats_rows") or []))

        slim = {k: v for k, v in result.items() if not k.endswith("_rows")}
        paths["report"].write_text(json.dumps(slim, ensure_ascii=False, indent=2), encoding="utf-8")

        m = result.get("mandatory_answers") or {}
        doc = kabu / "docs" / "operations" / "phase584_dynamic_rank_quality_vs_cap.md"
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(
            "\n".join(
                [
                    "# Phase584 — Dynamic Rank Quality vs CAP Competition",
                    "",
                    f"**Verdict:** `{result.get('verdict')}`",
                    f"**Period:** {m.get('period_start')}–{m.get('period_end')}",
                    "",
                    "## Mandatory answers",
                    "",
                    f"1. Rank26-40 weak: {m.get('1_rank26_40_weak')}",
                    f"2. CAP competition events: {m.get('2_cap_competition_events')}",
                    f"3. CAP competition PnL loss: {m.get('3_cap_competition_pnl_loss_yen_100')}",
                    f"4. Dynamic25 better reason: {m.get('4_dynamic25_better_reason')}",
                    f"5. Quality problem: {m.get('5_quality_problem')}",
                    f"6. CAP problem: {m.get('6_cap_problem')}",
                    f"7. Dynamic30 improves: {m.get('7_dynamic30_improves')}",
                    f"8. Dynamic25 adoption sufficient: {m.get('8_dynamic25_adoption_sufficient')}",
                    f"9. Runtime change candidate: {m.get('9_runtime_change_candidate')}",
                    f"10. Next phase: {m.get('10_next_phase')}",
                    "",
                    "## Rank cohort comparison",
                    "",
                    f"- Rank1-25: PF={m.get('rank_1_25_pf')} PnL={m.get('rank_1_25_pnl')}",
                    f"- Rank26-40: PF={m.get('rank_26_40_pf')} PnL={m.get('rank_26_40_pnl')} trades={m.get('rank_26_40_trades')}",
                    f"- Counterfactual delta PnL (top25 vs all): {m.get('counterfactual_delta_pnl')}",
                ]
            ),
            encoding="utf-8",
        )
        paths["doc"] = doc
        return paths
