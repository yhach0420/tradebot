"""
Phase375: Dynamic40 rank quality improvement shadow validation.

Walk-forward shadow re-ranking only — no ENTRY/EXIT or live universe changes.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

from research.phase374_dynamic40_universe_quality_review import (
    PRODUCTION_STACK_MIN_DAY,
    RANK_BUCKETS,
    _metrics_from_trades,
    _norm_symbol,
    _pf,
    classify_symbol_quality,
    discover_session_roots,
    discover_sessions_for_phase374,
    load_session_phase374,
    rank_bucket,
)

JST = ZoneInfo("Asia/Tokyo")

VARIANTS = (
    "A_baseline",
    "B_harmful_penalty",
    "C_stophit_penalty",
    "D_low_mfe_penalty",
    "E_dead_watch_penalty",
    "F_hybrid_quality_score",
)

PORTFOLIOS = (
    ("dynamic40_40", 40, 1, "all"),
    ("top30", 30, 1, "all"),
    ("trade_candidates20", 20, 1, "all"),
    ("backup20_watch", 40, 21, "backup_only"),
)

PENALTY_RULES: dict[str, dict[str, Any]] = {
    "B_harmful_penalty": {
        "harmful_watch_penalty": 0.35,
        "min_entry_for_stophit": 3,
    },
    "C_stophit_penalty": {
        "stophit_rate_multiplier": 0.30,
        "min_entry_for_stophit": 3,
    },
    "D_low_mfe_penalty": {
        "low_mfe_threshold_pct": 0.3,
        "low_mfe_penalty": 0.25,
        "min_entry_for_mfe": 3,
    },
    "E_dead_watch_penalty": {
        "dead_watch_penalty": 0.20,
        "min_monitored_days": 2,
    },
    "F_hybrid_quality_score": {
        "harmful_watch_penalty": 0.20,
        "stophit_rate_multiplier": 0.20,
        "low_mfe_threshold_pct": 0.3,
        "low_mfe_penalty": 0.15,
        "dead_watch_penalty": 0.10,
        "min_entry_for_stophit": 3,
        "min_entry_for_mfe": 3,
        "min_monitored_days": 2,
    },
}

BY_VARIANT_FIELDS = [
    "variant",
    "portfolio",
    "population",
    "monitored_symbol_count",
    "entry_count",
    "total_pnl_yen_100",
    "profit_factor",
    "stop_hit_rate",
    "avg_mfe_pct",
    "dead_watch_count",
    "harmful_watch_count",
    "profitable_core_count",
    "delta_pnl_vs_baseline",
    "delta_pf_vs_baseline",
]

BY_DAY_FIELDS = [
    "day_key",
    "variant",
    "portfolio",
    "population",
    "monitored_symbol_count",
    "entry_count",
    "total_pnl_yen_100",
    "profit_factor",
    "stop_hit_rate",
]

RANK_BUCKET_FIELDS = [
    "variant",
    "portfolio",
    "population",
    "rank_bucket",
    "entry_count",
    "total_pnl_yen_100",
    "profit_factor",
    "stop_hit_rate",
    "avg_mfe_pct",
]


@dataclass
class SymbolState:
    entry_count: int = 0
    monitored_day_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    pnl_yens: list[float] = field(default_factory=list)
    stop_hit_count: int = 0
    mfe_pcts: list[float] = field(default_factory=list)
    hold_secs: list[float] = field(default_factory=list)

    def quality_row(self) -> dict[str, Any]:
        from research.phase374_dynamic40_universe_quality_review import _float as f

        n = self.entry_count
        stops = self.stop_hit_count
        mfes = self.mfe_pcts
        holds = self.hold_secs
        total_yen = round(sum(self.pnl_yens), 2) if self.pnl_yens else None
        return {
            "entry_count": n,
            "session_count_monitored": self.monitored_day_count,
            "profit_factor": _pf(self.pnl_yens),
            "total_pnl_yen_100": total_yen,
            "stop_hit_rate": round(stops / n, 4) if n else None,
            "avg_mfe_pct": round(sum(mfes) / len(mfes), 4) if mfes else None,
            "avg_hold_minutes": round(sum(holds) / len(holds) / 60.0, 2) if holds else None,
        }

    def ingest_trade(self, trade: Mapping[str, Any]) -> None:
        from research.phase374_dynamic40_universe_quality_review import _float as f

        self.entry_count += 1
        yen = f(trade.get("pnl_yen_100"))
        if yen is not None:
            self.pnl_yens.append(float(yen))
            if yen > 0:
                self.win_count += 1
            elif yen < 0:
                self.loss_count += 1
        mfe = f(trade.get("peak_mfe_pct"))
        if mfe is not None:
            self.mfe_pcts.append(float(mfe))
        hold = f(trade.get("hold_sec"))
        if hold is not None:
            self.hold_secs.append(float(hold))
        if str(trade.get("exit_reason_canonical") or "") == "stop_hit":
            self.stop_hit_count += 1

    def ingest_monitored_day(self) -> None:
        self.monitored_day_count += 1


def _float(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def compute_penalty(
    variant: str,
    symbol: str,
    history: Mapping[str, SymbolState],
    rules: Mapping[str, Any],
) -> float:
    if variant == "A_baseline":
        return 0.0
    st = history.get(symbol)
    if st is None:
        return 0.0
    row = st.quality_row()
    qclass = classify_symbol_quality(row)
    penalty = 0.0

    if variant in ("B_harmful_penalty", "F_hybrid_quality_score"):
        if qclass == "harmful_watch":
            penalty += float(rules.get("harmful_watch_penalty") or 0.0)

    if variant in ("C_stophit_penalty", "F_hybrid_quality_score"):
        min_e = int(rules.get("min_entry_for_stophit") or 3)
        stop_rate = _float(row.get("stop_hit_rate"))
        if st.entry_count >= min_e and stop_rate is not None:
            penalty += float(rules.get("stophit_rate_multiplier") or 0.0) * float(stop_rate)

    if variant in ("D_low_mfe_penalty", "F_hybrid_quality_score"):
        min_e = int(rules.get("min_entry_for_mfe") or 3)
        avg_mfe = _float(row.get("avg_mfe_pct"))
        thr = float(rules.get("low_mfe_threshold_pct") or 0.3)
        if st.entry_count >= min_e and avg_mfe is not None and avg_mfe < thr:
            penalty += float(rules.get("low_mfe_penalty") or 0.0)

    if variant in ("E_dead_watch_penalty", "F_hybrid_quality_score"):
        min_mon = int(rules.get("min_monitored_days") or 2)
        if st.monitored_day_count >= min_mon and st.entry_count == 0:
            penalty += float(rules.get("dead_watch_penalty") or 0.0)

    return min(max(penalty, 0.0), 0.85)


def load_features_candidate_pool(
    day: str,
    reports_dir: Path,
    *,
    core_symbols: set[str],
    top_n: int = 120,
) -> dict[str, dict[str, Any]]:
    path = reports_dir / f"features_{day}.csv"
    if not path.is_file():
        return {}
    scored: list[tuple[float, dict[str, Any]]] = []
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = _norm_symbol(str(row.get("symbol") or ""))
            if not sym or sym in core_symbols:
                continue
            vl = _float(row.get("volatility_liquidity_score"))
            if vl is None:
                continue
            scored.append((float(vl), {"symbol": sym, "base_vol_liq": float(vl), "baseline_dynamic_rank": None}))
    scored.sort(key=lambda x: (-x[0], x[1]["symbol"]))
    out: dict[str, dict[str, Any]] = {}
    for _, item in scored[:top_n]:
        out[item["symbol"]] = item
    return out


def build_day_candidate_pool(
    day_sessions: Sequence[Mapping[str, Any]],
    *,
    day: str,
    reports_dir: Path,
) -> dict[str, dict[str, Any]]:
    core_symbols: set[str] = set()
    for sr in day_sessions:
        sess_dir = Path(str((sr.get("session_meta") or {}).get("session_dir") or sr.get("session_dir") or ""))
        if not sess_dir.is_dir():
            continue
        from research.phase374_dynamic40_universe_quality_review import (
            _universe_path_candidates,
            load_universe_csv,
        )

        summary = (sr.get("session_meta") or {}).get("summary") or {}
        session_kind = str(sr.get("session_kind") or "am")
        for candidate in _universe_path_candidates(day, session_kind, summary, reports_dir):
            uni = load_universe_csv(candidate)
            if not uni:
                continue
            for row in uni.values():
                sym = _norm_symbol(str(row.get("symbol") or ""))
                if str(row.get("universe_slot") or "").lower() == "core":
                    core_symbols.add(sym)
            break

    pool = load_features_candidate_pool(day, reports_dir, core_symbols=core_symbols)
    for sr in day_sessions:
        for sym, meta in (sr.get("dynamic_monitored") or {}).items():
            sym_n = _norm_symbol(sym)
            if not sym_n:
                continue
            vl = _float(meta.get("volatility_liquidity_score"))
            baseline_rank = _float(meta.get("dynamic_rank"))
            prev = pool.get(sym_n)
            entry = {
                "symbol": sym_n,
                "base_vol_liq": vl if vl is not None else (_float((prev or {}).get("base_vol_liq"))),
                "baseline_dynamic_rank": int(baseline_rank) if baseline_rank is not None else None,
            }
            if entry["base_vol_liq"] is None:
                continue
            pool[sym_n] = entry
    return pool


def shadow_rank_day(
    *,
    variant: str,
    pool: Mapping[str, Mapping[str, Any]],
    history: Mapping[str, SymbolState],
) -> dict[str, dict[str, Any]]:
    rules = PENALTY_RULES.get(variant, {})
    work_pool = pool
    if variant == "A_baseline":
        work_pool = {
            sym: row
            for sym, row in pool.items()
            if row.get("baseline_dynamic_rank") is not None
        }

    ranked: list[tuple[float, float, str]] = []
    for sym, row in work_pool.items():
        base = _float(row.get("base_vol_liq")) or 0.0
        if variant == "A_baseline":
            br = _float(row.get("baseline_dynamic_rank")) or 9999.0
            ranked.append((br, base, sym))
            continue
        penalty = compute_penalty(variant, sym, history, rules)
        adjusted = base * (1.0 - penalty)
        ranked.append((adjusted, base, sym))

    if variant == "A_baseline":
        ranked.sort(key=lambda x: (x[0], -x[1], x[2]))
    else:
        ranked.sort(key=lambda x: (-x[0], -x[1], x[2]))

    out: dict[str, dict[str, Any]] = {}
    for i, (_, base, sym) in enumerate(ranked, start=1):
        penalty = 0.0 if variant == "A_baseline" else compute_penalty(variant, sym, history, rules)
        out[sym] = {
            "symbol": sym,
            "shadow_rank": i,
            "rank_bucket": rank_bucket(i),
            "base_vol_liq": base,
            "penalty": round(penalty, 4),
            "adjusted_score": round(base * (1.0 - penalty), 4) if variant != "A_baseline" else base,
            "quality_class_past": classify_symbol_quality((history.get(sym) or SymbolState()).quality_row()),
        }
    return out


def portfolio_symbols(
    shadow_ranks: Mapping[str, Mapping[str, Any]],
    *,
    portfolio: str,
    top_n: int,
    min_rank: int,
    scope: str,
) -> set[str]:
    if scope == "backup_only":
        return {
            sym
            for sym, meta in shadow_ranks.items()
            if min_rank <= int(meta.get("shadow_rank") or 0) <= top_n
        }
    return {
        sym
        for sym, meta in shadow_ranks.items()
        if min_rank <= int(meta.get("shadow_rank") or 0) <= top_n
    }


def _quality_counts(shadow_ranks: Mapping[str, Mapping[str, Any]], selected: set[str]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for sym in selected:
        q = str((shadow_ranks.get(sym) or {}).get("quality_class_past") or "unclassified")
        if q == "dead_watch":
            counts["dead_watch_count"] += 1
        elif q == "harmful_watch":
            counts["harmful_watch_count"] += 1
        elif q == "profitable_core":
            counts["profitable_core_count"] += 1
    return dict(counts)


def _filter_trades(
    trades: Sequence[Mapping[str, Any]],
    *,
    selected: set[str],
    scope: str,
    shadow_ranks: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in trades:
        sym = _norm_symbol(str(t.get("symbol") or ""))
        if sym not in selected:
            continue
        if scope == "backup_only":
            rank = int((shadow_ranks.get(sym) or {}).get("shadow_rank") or 0)
            if rank <= 20:
                continue
        row = dict(t)
        row["shadow_rank"] = (shadow_ranks.get(sym) or {}).get("shadow_rank")
        row["rank_bucket"] = (shadow_ranks.get(sym) or {}).get("rank_bucket")
        out.append(row)
    return out


@dataclass
class Phase375Dynamic40RankQualityShadow:
    reports_dir: Path
    session_results: list[dict[str, Any]] = field(default_factory=list)
    repo_root: Path = field(default_factory=lambda: Path("."))

    def paths(self) -> dict[str, Path]:
        return {
            "summary": self.reports_dir / "phase375_dynamic40_rank_quality_shadow_summary.json",
            "by_variant": self.reports_dir / "phase375_dynamic40_rank_quality_by_variant.csv",
            "by_day": self.reports_dir / "phase375_dynamic40_rank_quality_by_day.csv",
            "rank_bucket": self.reports_dir / "phase375_dynamic40_rank_quality_rank_bucket.csv",
            "recommendation": self.reports_dir
            / "phase375_dynamic40_rank_quality_recommendation.md",
        }

    def ingest_session(self, result: Mapping[str, Any]) -> None:
        if result.get("error"):
            return
        self.session_results.append(dict(result))

    def run_walk_forward(self) -> dict[str, Any]:
        by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for sr in self.session_results:
            day = str(sr.get("day_key") or "")
            if day:
                by_day[day].append(sr)

        days = sorted(by_day.keys())
        history: dict[str, SymbolState] = defaultdict(SymbolState)

        accum: dict[tuple[str, str, str], dict[str, Any]] = defaultdict(
            lambda: {
                "monitored_symbols": set(),
                "trades": [],
                "prod_trades": [],
                "days": 0,
            }
        )
        day_rows: list[dict[str, Any]] = []
        bucket_accum: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)

        for day in days:
            day_sessions = by_day[day]
            pool = build_day_candidate_pool(
                day_sessions, day=day, reports_dir=self.reports_dir
            )

            for variant in VARIANTS:
                shadow = shadow_rank_day(variant=variant, pool=pool, history=history)
                for portfolio_id, top_n, min_rank, scope in PORTFOLIOS:
                    selected = portfolio_symbols(
                        shadow,
                        portfolio=portfolio_id,
                        top_n=top_n,
                        min_rank=min_rank,
                        scope=scope,
                    )
                    qcounts = _quality_counts(shadow, selected)
                    for population, trade_key in (
                        ("all", "trades"),
                        ("production_stack", "production_trades"),
                    ):
                        if population == "production_stack" and day < PRODUCTION_STACK_MIN_DAY:
                            continue
                        day_trades: list[dict[str, Any]] = []
                        for sr in day_sessions:
                            raw = [
                                t
                                for t in sr.get(trade_key) or []
                                if t.get("universe_group") == "dynamic40"
                            ]
                            day_trades.extend(
                                _filter_trades(
                                    raw,
                                    selected=selected,
                                    scope=scope,
                                    shadow_ranks=shadow,
                                )
                            )
                        metrics = _metrics_from_trades(day_trades)
                        key = (variant, portfolio_id, population)
                        slot = accum[key]
                        slot["days"] += 1
                        slot["monitored_symbols"].update(selected)
                        slot["trades"].extend(day_trades)
                        for k, v in qcounts.items():
                            slot[k] = int(slot.get(k, 0)) + int(v)
                        day_rows.append(
                            {
                                "day_key": day,
                                "variant": variant,
                                "portfolio": portfolio_id,
                                "population": population,
                                "monitored_symbol_count": len(selected),
                                "entry_count": metrics.get("entry_count"),
                                "total_pnl_yen_100": metrics.get("total_pnl_yen_100"),
                                "profit_factor": metrics.get("profit_factor"),
                                "stop_hit_rate": metrics.get("stop_hit_rate"),
                            }
                        )
                        for t in day_trades:
                            b = str(t.get("rank_bucket") or "rank_unknown")
                            bucket_accum[(variant, portfolio_id, population, b)].append(dict(t))

            # Update history AFTER day evaluation (no future leak)
            monitored_today = set(pool.keys())
            for sym in monitored_today:
                history[sym].ingest_monitored_day()
            for sr in day_sessions:
                for t in sr.get("trades") or []:
                    if t.get("universe_group") != "dynamic40":
                        continue
                    sym = _norm_symbol(str(t.get("symbol") or ""))
                    if sym in monitored_today:
                        history[sym].ingest_trade(t)

        variant_rows: list[dict[str, Any]] = []
        rank_bucket_rows: list[dict[str, Any]] = []
        baseline_pnl: dict[tuple[str, str], float] = {}
        baseline_pf: dict[tuple[str, str], Optional[float]] = {}

        for key, slot in accum.items():
            variant, portfolio_id, population = key
            metrics = _metrics_from_trades(slot["trades"])
            row = {
                "variant": variant,
                "portfolio": portfolio_id,
                "population": population,
                "monitored_symbol_count": len(slot["monitored_symbols"]),
                "entry_count": metrics.get("entry_count"),
                "total_pnl_yen_100": metrics.get("total_pnl_yen_100"),
                "profit_factor": metrics.get("profit_factor"),
                "stop_hit_rate": metrics.get("stop_hit_rate"),
                "avg_mfe_pct": metrics.get("avg_mfe_pct"),
                "dead_watch_count": int(slot.get("dead_watch_count") or 0),
                "harmful_watch_count": int(slot.get("harmful_watch_count") or 0),
                "profitable_core_count": int(slot.get("profitable_core_count") or 0),
            }
            if variant == "A_baseline":
                baseline_pnl[(portfolio_id, population)] = _float(row.get("total_pnl_yen_100")) or 0.0
                baseline_pf[(portfolio_id, population)] = _float(row.get("profit_factor"))
            variant_rows.append(row)

        for row in variant_rows:
            pop = str(row.get("population") or "")
            port = str(row.get("portfolio") or "")
            base = baseline_pnl.get((port, pop), 0.0)
            pnl = _float(row.get("total_pnl_yen_100")) or 0.0
            row["delta_pnl_vs_baseline"] = round(pnl - base, 2) if row.get("variant") != "A_baseline" else 0.0
            bpf = baseline_pf.get((port, pop))
            pf = _float(row.get("profit_factor"))
            if row.get("variant") == "A_baseline":
                row["delta_pf_vs_baseline"] = 0.0
            elif bpf is not None and pf is not None and bpf != float("inf") and pf != float("inf"):
                row["delta_pf_vs_baseline"] = round(pf - bpf, 4)
            else:
                row["delta_pf_vs_baseline"] = None

        for bkey, trades in bucket_accum.items():
            variant, portfolio_id, population, bucket_id = bkey
            metrics = _metrics_from_trades(trades)
            rank_bucket_rows.append(
                {
                    "variant": variant,
                    "portfolio": portfolio_id,
                    "population": population,
                    "rank_bucket": bucket_id,
                    "entry_count": metrics.get("entry_count"),
                    "total_pnl_yen_100": metrics.get("total_pnl_yen_100"),
                    "profit_factor": metrics.get("profit_factor"),
                    "stop_hit_rate": metrics.get("stop_hit_rate"),
                    "avg_mfe_pct": metrics.get("avg_mfe_pct"),
                }
            )

        return {
            "variant_rows": variant_rows,
            "day_rows": day_rows,
            "rank_bucket_rows": rank_bucket_rows,
            "days_evaluated": len(days),
        }

    def _pick_best(
        self, rows: Sequence[Mapping[str, Any]], *, population: str, portfolio: Optional[str] = None
    ) -> dict[str, Any]:
        candidates = [
            r
            for r in rows
            if str(r.get("population") or "") == population
            and str(r.get("variant") or "") != "A_baseline"
            and (portfolio is None or str(r.get("portfolio") or "") == portfolio)
        ]
        if not candidates:
            return {"variant": None, "portfolio": None, "delta_pnl": None, "adoptable": False}
        best = max(candidates, key=lambda r: _float(r.get("delta_pnl_vs_baseline")) or -1e18)
        delta = _float(best.get("delta_pnl_vs_baseline")) or 0.0
        return {
            "variant": best.get("variant"),
            "portfolio": best.get("portfolio"),
            "delta_pnl": delta,
            "delta_pf": best.get("delta_pf_vs_baseline"),
            "adoptable": delta > 0,
        }

    def build_recommendation_md(self, summary: Mapping[str, Any]) -> str:
        verdict = summary.get("verdict") or {}
        lines = [
            "# Phase375 Dynamic40 Rank Quality Shadow Recommendation",
            "",
            "## Verdict",
            "",
            f"- **Full dynamic40_40 replace feasible?** {verdict.get('improvement_feasible')}",
            f"- **Partial sub-portfolio improvement?** {verdict.get('partial_improvement_sub_portfolio')}",
            f"- **Best production (dynamic40_40):** {verdict.get('best_variant_production_dynamic40_40')}",
            f"- **Best production (any portfolio):** {verdict.get('best_variant_production_any_portfolio')} / {verdict.get('best_portfolio_production')}",
            f"- **Adopt recommendation:** {verdict.get('adopt_recommendation')}",
            "",
            "## Notes",
            "",
            "- Walk-forward only: each day uses past-day performance for penalties.",
            "- Rank 31-40 preserved in portfolios; simple bottom-cut is not tested.",
            "- Shadow re-ranking does not change live ENTRY/EXIT.",
            "",
            "## Variant comparison (dynamic40_40)",
            "",
        ]
        for pop in ("all", "production_stack"):
            lines.append(f"### population={pop}")
            for row in summary.get("by_variant") or []:
                if row.get("portfolio") != "dynamic40_40" or row.get("population") != pop:
                    continue
                lines.append(
                    f"- {row.get('variant')}: pnl={row.get('total_pnl_yen_100')} "
                    f"pf={row.get('profit_factor')} delta_pnl={row.get('delta_pnl_vs_baseline')} "
                    f"harmful={row.get('harmful_watch_count')} dead={row.get('dead_watch_count')}"
                )
            lines.append("")
        if not verdict.get("improvement_feasible"):
            lines.append("## Conclusion")
            lines.append("")
            lines.append("ランキング品質改善は現状困難 — どの候補も baseline dynamic40_40 を")
            lines.append("有意に上回らない。追加の特徴量または別軸の選定改善が必要。")
            lines.append("")
        return "\n".join(lines)

    def finalize_outputs(
        self,
        *,
        wall_runtime_sec: float,
        sessions_discovered: int,
        sessions_evaluated: int,
    ) -> dict[str, Path]:
        paths = self.paths()
        wf = self.run_walk_forward()
        best_all_40 = self._pick_best(wf["variant_rows"], population="all", portfolio="dynamic40_40")
        best_prod_40 = self._pick_best(
            wf["variant_rows"], population="production_stack", portfolio="dynamic40_40"
        )
        best_prod_any = self._pick_best(wf["variant_rows"], population="production_stack")
        improvement = bool(
            best_prod_40.get("adoptable") or best_all_40.get("adoptable")
        )
        partial_improvement = bool(best_prod_any.get("adoptable"))
        adopt = "reject"
        if improvement:
            adopt = str(best_prod_40.get("variant") or best_all_40.get("variant") or "reject")
        elif partial_improvement:
            adopt = (
                f"shadow_only_{best_prod_any.get('variant')}_{best_prod_any.get('portfolio')}"
            )

        summary = {
            "phase": 375,
            "title": "Dynamic40 rank quality improvement shadow validation",
            "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
            "population": {
                "sessions_discovered": sessions_discovered,
                "sessions_evaluated": sessions_evaluated,
                "days_evaluated": wf["days_evaluated"],
                "production_stack_min_day": PRODUCTION_STACK_MIN_DAY,
            },
            "penalty_rules": PENALTY_RULES,
            "variants": list(VARIANTS),
            "portfolios": [
                {"portfolio": p, "top_n": n, "scope": s} for p, n, _, s in PORTFOLIOS
            ],
            "by_variant": wf["variant_rows"],
            "by_day_sample": wf["day_rows"][:50],
            "rank_bucket_summary": wf["rank_bucket_rows"],
            "verdict": {
                "improvement_feasible": improvement,
                "partial_improvement_sub_portfolio": partial_improvement,
                "best_variant_all_dynamic40_40": best_all_40.get("variant"),
                "best_variant_production_dynamic40_40": best_prod_40.get("variant"),
                "best_variant_production_any_portfolio": best_prod_any.get("variant"),
                "best_portfolio_production": best_prod_any.get("portfolio"),
                "best_delta_pnl_production_dynamic40_40": best_prod_40.get("delta_pnl"),
                "best_delta_pnl_production_any": best_prod_any.get("delta_pnl"),
                "adopt_recommendation": adopt,
                "ranking_improvement_difficult": not improvement,
                "full_dynamic40_40_replace_rejected": not improvement,
            },
            "wall_runtime_sec": round(wall_runtime_sec, 2),
        }

        paths["summary"].write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        self._write_csv(paths["by_variant"], wf["variant_rows"], BY_VARIANT_FIELDS)
        self._write_csv(paths["by_day"], wf["day_rows"], BY_DAY_FIELDS)
        self._write_csv(paths["rank_bucket"], wf["rank_bucket_rows"], RANK_BUCKET_FIELDS)
        paths["recommendation"].write_text(
            self.build_recommendation_md(summary), encoding="utf-8"
        )
        return paths

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                w.writerow(r)
