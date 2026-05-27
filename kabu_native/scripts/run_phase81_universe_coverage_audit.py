#!/usr/bin/env python3
"""
Phase 81: Universe coverage audit — candidate/accepted/reject bias by symbol and market cap.
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
ROOT = Path(__file__).resolve().parents[2]

# JPY TotalMarketValue (kabu PUSH) — 大型 / 中型 / 小型
TIER_LARGE_JPY = 500_000_000_000  # 5000億円以上
TIER_MID_JPY = 100_000_000_000  # 1000億円以上（未満は小型）

QUALITY_RANK_BINS = (
    (0.0, 0.55, "below_gate"),
    (0.55, 0.70, "0.55_0.70"),
    (0.70, 0.80, "0.70_0.80"),
    (0.80, 1.01, "ge_0.80"),
)


def _bootstrap() -> None:
    native = ROOT / "kabu_native"
    for p in (native / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return sum(wins) / gl


def market_cap_tier(market_cap_jpy: Optional[float]) -> str:
    if market_cap_jpy is None or market_cap_jpy <= 0:
        return "unknown"
    if market_cap_jpy >= TIER_LARGE_JPY:
        return "large"
    if market_cap_jpy >= TIER_MID_JPY:
        return "mid"
    return "small"


def tier_label_ja(tier: str) -> str:
    return {"large": "大型", "mid": "中型", "small": "小型", "unknown": "不明"}.get(tier, tier)


def load_universe(universe_path: Path) -> list[str]:
    symbols: list[str] = []
    with universe_path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            sym = str(row.get("symbol") or "").strip()
            if sym and not sym.endswith(".T"):
                sym = f"{sym}.T"
            if sym:
                symbols.append(sym)
    return sorted(set(symbols))


def load_market_caps_from_push(push_dir: Path) -> dict[str, float]:
    """First tick TotalMarketValue per symbol file."""
    out: dict[str, float] = {}
    if not push_dir.is_dir():
        return out
    for path in sorted(push_dir.glob("*.jsonl")):
        sym = path.stem if path.stem.endswith(".T") else f"{path.stem}.T"
        try:
            line = path.read_text(encoding="utf-8").splitlines()[0]
            rec = json.loads(line)
            mv = (rec.get("payload") or {}).get("TotalMarketValue")
            if isinstance(mv, (int, float)) and mv > 0:
                out[sym] = float(mv)
        except (IndexError, json.JSONDecodeError, OSError):
            continue
    return out


def load_events(session_dir: Path) -> list[dict[str, Any]]:
    jsonl = session_dir / "small_paper_events.jsonl"
    if jsonl.is_file():
        rows = []
        with jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        return rows
    csv_path = session_dir / "small_paper_events.csv"
    if not csv_path.is_file():
        return []
    with csv_path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_structural_trades(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "structural_trades.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _float_q(val: Any) -> Optional[float]:
    try:
        if val is None or val == "":
            return None
        return float(val)
    except (TypeError, ValueError):
        return None


def quality_rank_bin(score: Optional[float]) -> str:
    if score is None:
        return "unknown"
    for lo, hi, label in QUALITY_RANK_BINS:
        if lo <= score < hi:
            return label
    return "unknown"


@dataclass
class SymStats:
    symbol: str
    market_cap_jpy: Optional[float] = None
    market_cap_tier: str = "unknown"
    candidate: int = 0
    accepted: int = 0
    rejected: int = 0
    reject_max_concurrent: int = 0
    reject_low_quality: int = 0
    reject_outside_window: int = 0
    reject_other: int = 0
    quality_scores: list[float] = field(default_factory=list)
    accepted_quality: list[float] = field(default_factory=list)

    @property
    def accepted_rate(self) -> Optional[float]:
        if self.candidate <= 0:
            return None
        return self.accepted / self.candidate


def aggregate_by_symbol(
    events: Sequence[Mapping[str, Any]],
    *,
    sym_tiers: dict[str, str],
    sym_caps: dict[str, float],
) -> dict[str, SymStats]:
    stats: dict[str, SymStats] = {}
    for ev in events:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        st = stats.setdefault(
            sym,
            SymStats(
                symbol=sym,
                market_cap_jpy=sym_caps.get(sym),
                market_cap_tier=sym_tiers.get(sym, "unknown"),
            ),
        )
        et = str(ev.get("event_type") or "")
        q = _float_q(ev.get("continuation_quality_score"))
        if et == "candidate":
            st.candidate += 1
            if q is not None:
                st.quality_scores.append(q)
        elif et == "accepted":
            st.accepted += 1
            if q is not None:
                st.accepted_quality.append(q)
        elif et == "rejected":
            st.rejected += 1
            reason = str(ev.get("gate_reject_reason") or "")
            if reason == "max_concurrent":
                st.reject_max_concurrent += 1
            elif reason == "low_quality":
                st.reject_low_quality += 1
            elif reason == "outside_allowed_trading_window":
                st.reject_outside_window += 1
            else:
                st.reject_other += 1
    return stats


def tier_aggregate(stats: dict[str, SymStats]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[SymStats]] = defaultdict(list)
    for st in stats.values():
        buckets[st.market_cap_tier].append(st)
    out: dict[str, dict[str, Any]] = {}
    for tier, rows in sorted(buckets.items()):
        cand = sum(s.candidate for s in rows)
        acc = sum(s.accepted for s in rows)
        rej = sum(s.rejected for s in rows)
        cap_rej = sum(s.reject_max_concurrent for s in rows)
        all_q = [q for s in rows for q in s.quality_scores]
        acc_q = [q for s in rows for q in s.accepted_quality]
        sym_with_cand = sum(1 for s in rows if s.candidate > 0)
        out[tier] = {
            "tier": tier,
            "tier_ja": tier_label_ja(tier),
            "symbol_count": len(rows),
            "symbols_with_candidates": sym_with_cand,
            "candidate_count": cand,
            "accepted_count": acc,
            "rejected_count": rej,
            "accepted_rate": round(acc / cand, 4) if cand else None,
            "reject_max_concurrent_count": cap_rej,
            "cap3_reject_share_of_rejects": round(cap_rej / rej, 4) if rej else None,
            "mean_quality_all_candidates": round(statistics.mean(all_q), 4) if all_q else None,
            "mean_quality_accepted": round(statistics.mean(acc_q), 4) if acc_q else None,
        }
    return out


def structural_pnl_by_tier(
    trades: Sequence[Mapping[str, Any]],
    sym_tiers: dict[str, str],
) -> dict[str, dict[str, Any]]:
    by_tier: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        sym = str(t.get("symbol") or "")
        pnl = _float_q(t.get("realized_pnl_pct"))
        if pnl is None:
            continue
        by_tier[sym_tiers.get(sym, "unknown")].append(pnl)
    out: dict[str, dict[str, Any]] = {}
    for tier, pnls in sorted(by_tier.items()):
        pf = _profit_factor(pnls)
        out[tier] = {
            "tier": tier,
            "tier_ja": tier_label_ja(tier),
            "trade_count": len(pnls),
            "structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
            "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
            "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
        }
    return out


def quality_rank_distribution(events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_et: dict[str, Counter[str]] = defaultdict(Counter)
    for ev in events:
        et = str(ev.get("event_type") or "")
        if et not in ("candidate", "accepted", "rejected"):
            continue
        by_et[et][quality_rank_bin(_float_q(ev.get("continuation_quality_score")))] += 1
    rows: list[dict[str, Any]] = []
    for et in ("candidate", "accepted", "rejected"):
        total = sum(by_et[et].values()) or 1
        for _lo, _hi, label in QUALITY_RANK_BINS:
            cnt = by_et[et][label]
            rows.append(
                {
                    "event_type": et,
                    "quality_rank_bin": label,
                    "count": cnt,
                    "share": round(cnt / total, 4),
                }
            )
        if by_et[et]["unknown"]:
            cnt = by_et[et]["unknown"]
            rows.append(
                {
                    "event_type": et,
                    "quality_rank_bin": "unknown",
                    "count": cnt,
                    "share": round(cnt / total, 4),
                }
            )
    return rows


def cap3_rejection_rows(stats: dict[str, SymStats]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sym, st in sorted(stats.items()):
        if st.reject_max_concurrent <= 0 and st.candidate <= 0:
            continue
        rows.append(
            {
                "symbol": sym,
                "market_cap_tier": st.market_cap_tier,
                "tier_ja": tier_label_ja(st.market_cap_tier),
                "candidate_count": st.candidate,
                "accepted_count": st.accepted,
                "rejected_count": st.rejected,
                "reject_max_concurrent": st.reject_max_concurrent,
                "cap3_share_of_symbol_rejects": round(
                    st.reject_max_concurrent / st.rejected, 4
                )
                if st.rejected
                else None,
                "mean_quality": round(statistics.mean(st.quality_scores), 4)
                if st.quality_scores
                else None,
            }
        )
    rows.sort(key=lambda r: (-int(r["reject_max_concurrent"]), -int(r["candidate_count"])))
    return rows


def decide_verdict(
    *,
    universe_size: int,
    push_symbol_count: int,
    candidate_symbol_count: int,
    accepted_symbol_count: int,
    tier_stats: dict[str, dict[str, Any]],
    total_max_concurrent_rejects: int,
    total_rejects: int,
) -> tuple[str, str]:
    """Return (verdict, rationale)."""
    notes: list[str] = []

    push_cov = push_symbol_count / universe_size if universe_size else 0
    cand_cov = candidate_symbol_count / universe_size if universe_size else 0
    if push_cov < 0.85 or cand_cov < 0.7:
        notes.append(
            f"universe {universe_size} vs push {push_symbol_count} ({push_cov:.0%}) "
            f"vs candidate symbols {candidate_symbol_count} ({cand_cov:.0%})"
        )
        return (
            "push_source_biased",
            "Not all universe symbols receive comparable PUSH/candidate flow. "
            + "; ".join(notes),
        )

    large = tier_stats.get("large") or {}
    small = tier_stats.get("small") or {}
    mid = tier_stats.get("mid") or {}

    lq = large.get("mean_quality_all_candidates")
    sq = small.get("mean_quality_all_candidates") or mid.get("mean_quality_all_candidates")
    lar = large.get("accepted_rate")
    sar = small.get("accepted_rate") or mid.get("accepted_rate")

    if (
        lq is not None
        and sq is not None
        and lar is not None
        and sar is not None
        and lq - sq >= 0.08
        and lar - sar >= 0.15
    ):
        return (
            "quality_bias_large_cap",
            f"Large-cap mean quality {lq} vs smaller {sq}; accepted rate {lar} vs {sar}",
        )

    cap_share = total_max_concurrent_rejects / total_rejects if total_rejects else 0

    total_acc = sum(t.get("accepted_count") or 0 for t in tier_stats.values())
    acc_large_share = (large.get("accepted_count") or 0) / max(1, total_acc)
    if acc_large_share >= 0.85:
        return (
            "quality_bias_large_cap",
            f"{acc_large_share:.0%} of accepted events are large-cap "
            f"({large.get('accepted_count')}/{total_acc}); "
            f"mean quality large {lq} vs mid/small {sq}",
        )

    if cap_share >= 0.08 and total_max_concurrent_rejects >= 500:
        return (
            "cap_saturation_bias",
            f"max_concurrent rejects {total_max_concurrent_rejects} ({cap_share:.0%} of rejects); "
            "concurrent cap=3 binds after quality pass",
        )

    return (
        "no_significant_bias",
        "Universe and push coverage aligned; no dominant large-cap quality or cap-only bias detected",
    )


def run_audit(
    *,
    session_dir: Path,
    universe_path: Path,
    push_dir: Optional[Path],
) -> dict[str, Any]:
    universe = load_universe(universe_path)
    sym_caps = load_market_caps_from_push(push_dir) if push_dir else {}
    for sym in universe:
        sym_caps.setdefault(sym, sym_caps.get(sym))
    sym_tiers = {sym: market_cap_tier(sym_caps.get(sym)) for sym in universe}

    events = load_events(session_dir)
    trades = load_structural_trades(session_dir)
    stats = aggregate_by_symbol(events, sym_tiers=sym_tiers, sym_caps=sym_caps)

    push_symbols = set(sym_caps.keys()) & set(universe)
    candidate_symbols = {s for s, st in stats.items() if st.candidate > 0}
    accepted_symbols = {s for s, st in stats.items() if st.accepted > 0}

    tier_stats = tier_aggregate(stats)
    structural_by_tier = structural_pnl_by_tier(trades, sym_tiers)

    total_cand = sum(1 for e in events if e.get("event_type") == "candidate")
    total_acc = sum(1 for e in events if e.get("event_type") == "accepted")
    total_rej = sum(1 for e in events if e.get("event_type") == "rejected")
    max_conc = sum(
        1
        for e in events
        if e.get("event_type") == "rejected" and e.get("gate_reject_reason") == "max_concurrent"
    )

    reject_reasons = Counter(
        str(e.get("gate_reject_reason") or "")
        for e in events
        if e.get("event_type") == "rejected"
    )

    verdict, rationale = decide_verdict(
        universe_size=len(universe),
        push_symbol_count=len(push_symbols),
        candidate_symbol_count=len(candidate_symbols),
        accepted_symbol_count=len(accepted_symbols),
        tier_stats=tier_stats,
        total_max_concurrent_rejects=max_conc,
        total_rejects=total_rej,
    )

    tier_compare: dict[str, Any] = {}
    for tier in ("large", "mid", "small"):
        ts = tier_stats.get(tier) or {}
        st = structural_by_tier.get(tier) or {}
        tier_compare[tier] = {
            "tier_ja": tier_label_ja(tier),
            **{k: ts.get(k) for k in ts},
            "structural_pf": st.get("structural_pf"),
            "structural_avg_pnl_pct": st.get("avg_pnl_pct"),
            "structural_win_rate": st.get("win_rate"),
            "structural_trade_count": st.get("trade_count"),
        }

    return {
        "phase": 81,
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "session_dir": str(session_dir),
        "universe_path": str(universe_path),
        "push_dir": str(push_dir) if push_dir else None,
        "marketcap_tier_thresholds_jpy": {
            "large_min": TIER_LARGE_JPY,
            "mid_min": TIER_MID_JPY,
            "small_below": TIER_MID_JPY,
        },
        "universe_coverage": {
            "universe_symbol_count": len(universe),
            "push_jsonl_symbol_count": len(push_symbols),
            "candidate_symbol_count": len(candidate_symbols),
            "accepted_symbol_count": len(accepted_symbols),
            "push_coverage_pct": round(100 * len(push_symbols) / len(universe), 2)
            if universe
            else None,
            "candidate_coverage_pct": round(100 * len(candidate_symbols) / len(universe), 2)
            if universe
            else None,
        },
        "event_totals": {
            "candidate": total_cand,
            "accepted": total_acc,
            "rejected": total_rej,
            "accepted_rate": round(total_acc / total_cand, 4) if total_cand else None,
            "reject_reason_counts": dict(reject_reasons),
            "reject_max_concurrent": max_conc,
            "reject_max_concurrent_share": round(max_conc / total_rej, 4) if total_rej else None,
        },
        "tier_comparison": tier_compare,
        "quality_rank_distribution": quality_rank_distribution(events),
        "verdict": verdict,
        "rationale": rationale,
    }


def symbol_distribution_rows(stats: dict[str, SymStats]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sym, st in sorted(stats.items()):
        rows.append(
            {
                "symbol": sym,
                "market_cap_jpy": st.market_cap_jpy,
                "market_cap_tier": st.market_cap_tier,
                "tier_ja": tier_label_ja(st.market_cap_tier),
                "candidate_count": st.candidate,
                "accepted_count": st.accepted,
                "rejected_count": st.rejected,
                "accepted_rate": round(st.accepted_rate, 4) if st.accepted_rate is not None else None,
                "reject_max_concurrent": st.reject_max_concurrent,
                "reject_low_quality": st.reject_low_quality,
                "reject_outside_window": st.reject_outside_window,
                "mean_quality": round(statistics.mean(st.quality_scores), 4)
                if st.quality_scores
                else None,
                "mean_quality_accepted": round(statistics.mean(st.accepted_quality), 4)
                if st.accepted_quality
                else None,
            }
        )
    rows.sort(key=lambda r: (-(r["accepted_count"] or 0), -(r["candidate_count"] or 0)))
    return rows


def marketcap_distribution_rows(
    tier_stats: dict[str, dict[str, Any]],
    universe: Sequence[str],
    sym_tiers: dict[str, str],
) -> list[dict[str, Any]]:
    tier_sym_count = Counter(sym_tiers.get(s, "unknown") for s in universe)
    rows: list[dict[str, Any]] = []
    for tier, agg in sorted(tier_stats.items()):
        rows.append(
            {
                "market_cap_tier": tier,
                "tier_ja": tier_label_ja(tier),
                "universe_symbols": tier_sym_count.get(tier, 0),
                **agg,
            }
        )
    return rows


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main() -> int:
    _bootstrap()
    parser = argparse.ArgumentParser(description="Phase81 universe coverage audit")
    parser.add_argument(
        "--session-dir",
        type=Path,
        default=ROOT
        / "kabu_native/results/small_paper/20260519/live_full_session_081047",
    )
    parser.add_argument(
        "--universe",
        type=Path,
        default=ROOT / "kabu_native/data/universe/universe_intraday_full.csv",
    )
    parser.add_argument(
        "--push-dir",
        type=Path,
        default=None,
        help="push_jsonl day dir (default: infer from session dir YYYYMMDD)",
    )
    args = parser.parse_args()

    session_dir = args.session_dir if args.session_dir.is_absolute() else ROOT / args.session_dir
    universe_path = args.universe if args.universe.is_absolute() else ROOT / args.universe
    push_dir = args.push_dir
    if push_dir is None:
        day = session_dir.parent.name
        if len(day) == 8 and day.isdigit():
            push_dir = (
                ROOT
                / "kabu_native/data/push_jsonl"
                / f"{day[:4]}-{day[4:6]}-{day[6:8]}"
            )
    if push_dir and not push_dir.is_absolute():
        push_dir = ROOT / push_dir

    if not session_dir.is_dir():
        print(f"session dir not found: {session_dir}", file=sys.stderr)
        return 2

    audit = run_audit(session_dir=session_dir, universe_path=universe_path, push_dir=push_dir)
    events = load_events(session_dir)
    sym_caps = load_market_caps_from_push(push_dir) if push_dir else {}
    universe = load_universe(universe_path)
    sym_tiers = {sym: market_cap_tier(sym_caps.get(sym)) for sym in universe}
    stats = aggregate_by_symbol(events, sym_tiers=sym_tiers, sym_caps=sym_caps)
    tier_stats = tier_aggregate(stats)

    sym_rows = symbol_distribution_rows(stats)
    cap_rows = marketcap_distribution_rows(tier_stats, universe, sym_tiers)
    cap3_rows = cap3_rejection_rows(stats)

    trades = load_structural_trades(session_dir)
    liquidity_audit: dict[str, Any] = {}
    if trades and push_dir and push_dir.is_dir():
        from small_paper.accepted_liquidity_metrics import (
            build_accepted_trade_rows,
            build_liquidity_comparison,
        )

        trade_liq_rows = build_accepted_trade_rows(
            trades, push_dir=push_dir, sym_caps=sym_caps
        )
        liquidity_audit = build_liquidity_comparison(trade_liq_rows)
        audit["accepted_liquidity_comparison"] = liquidity_audit
        write_csv(session_dir / "phase81_accepted_trade_liquidity.csv", trade_liq_rows)
        write_csv(
            session_dir / "phase81_accepted_liquidity_by_tier.csv",
            liquidity_audit.get("by_market_cap_tier") or [],
        )
        write_csv(
            session_dir / "phase81_accepted_liquidity_win_loss.csv",
            liquidity_audit.get("by_trade_outcome") or [],
        )
        write_csv(
            session_dir / "phase81_accepted_liquidity_tier_outcome.csv",
            liquidity_audit.get("by_tier_and_outcome") or [],
        )

    out_json = session_dir / "phase81_universe_coverage_audit.json"
    out_json.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(session_dir / "phase81_symbol_distribution.csv", sym_rows)
    write_csv(session_dir / "phase81_marketcap_distribution.csv", cap_rows)
    write_csv(session_dir / "phase81_cap3_rejection_analysis.csv", cap3_rows)

    print(json.dumps(audit, ensure_ascii=False, indent=2))
    print(f"\nWrote {out_json}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
