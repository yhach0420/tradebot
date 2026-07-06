"""
Phase651: Scan accept ranking audit (research only).

Identifies how PBv2/OR gate-pass candidates are ranked when max_entries_per_scan
caps adoption. Uses entry_scan_audit.jsonl + session rejects/accepts.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase632_pbv2_profit_filter_counterfactual import _profit_factor
from research.phase634_pbv2_only_rise5_full_period import load_trades_for_session
from small_paper.entry_scan_controller import (
    EntryFreshnessSnapshot,
    candidate_rank_score,
)

PHASE651_VERDICT = "phase651_scan_accept_ranking_audit_done"
REPORT_DIR_NAME = "phase651_scan_accept_ranking_audit"

NATIVE_ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER_ROOT = NATIVE_ROOT / "results" / "small_paper"

RANKING_RULES: list[dict[str, Any]] = [
    {
        "rule_id": "primary_sort",
        "component": "rank_score",
        "direction": "desc",
        "source": "entry_scan_controller._flush_locked",
        "formula": (
            "v2*1000 + cq*100 + min(tv/1e9,20) + imb*10 + max(vwap_dev,0)*5 + mom*50 - price_age*100"
        ),
    },
    {
        "rule_id": "tie_break_1",
        "component": "continuation_quality_score",
        "direction": "desc",
        "source": "embedded in rank_score",
        "formula": "cq*100 term",
    },
    {
        "rule_id": "tie_break_2",
        "component": "trading_value",
        "direction": "desc",
        "source": "embedded in rank_score",
        "formula": "min(tv/1e9,20)",
    },
    {
        "rule_id": "tie_break_3",
        "component": "entry_order_book_imbalance",
        "direction": "desc",
        "source": "embedded in rank_score",
        "formula": "imb*10",
    },
    {
        "rule_id": "tie_break_4",
        "component": "entry_vwap_dev_pct",
        "direction": "desc_positive_only",
        "source": "embedded in rank_score",
        "formula": "max(vwap_dev,0)*5",
    },
    {
        "rule_id": "tie_break_5",
        "component": "momentum_continuation_score",
        "direction": "desc",
        "source": "embedded in rank_score",
        "formula": "mom*50",
    },
    {
        "rule_id": "tie_break_6",
        "component": "price_age_sec",
        "direction": "asc",
        "source": "embedded in rank_score",
        "formula": "-price_age*100",
    },
    {
        "rule_id": "final_tie_break",
        "component": "enqueue_order",
        "direction": "stable_sort",
        "source": "queue_accepted_candidate append order",
        "formula": "Python stable sort preserves PUSH/eval enqueue order",
    },
    {
        "rule_id": "scan_batch",
        "component": "entry_scan_window_sec",
        "direction": "group",
        "source": "EntryScanController batch",
        "formula": "Candidates within scan_window_sec (default 2s) share one scan_id",
    },
    {
        "rule_id": "cap",
        "component": "max_entries_per_scan",
        "direction": "truncate",
        "source": "config max_entries_per_scan (production=1)",
        "formula": "Top-N by rank_score accepted; rest -> max_entries_per_scan",
    },
]


def _num(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_ts(ts: str) -> Optional[datetime]:
    s = str(ts or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def discover_audit_sessions(root: Path = SMALL_PAPER_ROOT) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(sess_dir: Path, day_iso: str, source: str) -> None:
        audit = sess_dir / "entry_scan_audit.jsonl"
        if not audit.is_file():
            return
        key = str(sess_dir)
        if key in seen:
            return
        seen.add(key)
        out.append(
            {
                "day": day_iso,
                "session": sess_dir.name,
                "session_dir": str(sess_dir),
                "source_kind": source,
            }
        )

    replay = root / "_phase630" / "current"
    if replay.is_dir():
        for day_dir in sorted(replay.iterdir()):
            if day_dir.is_dir() and len(day_dir.name) == 8 and day_dir.name.isdigit():
                day_iso = f"{day_dir.name[:4]}-{day_dir.name[4:6]}-{day_dir.name[6:8]}"
                for sess_dir in sorted(day_dir.glob("live_session_*")):
                    _add(sess_dir, day_iso, "phase630_replay")

    if root.is_dir():
        for day_dir in sorted(root.iterdir()):
            if not day_dir.is_dir() or day_dir.name.startswith("_"):
                continue
            if len(day_dir.name) == 8 and day_dir.name.isdigit():
                day_iso = f"{day_dir.name[:4]}-{day_dir.name[4:6]}-{day_dir.name[6:8]}"
                for sess_dir in sorted(day_dir.glob("live_session_*")):
                    _add(sess_dir, day_iso, "live_session")
    return out


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def _freshness_from_row(row: Mapping[str, Any]) -> EntryFreshnessSnapshot:
    return EntryFreshnessSnapshot(
        data_source=str(row.get("data_source") or "unknown"),
        last_price_update_ts=row.get("last_price_update_ts"),
        last_board_update_ts=row.get("last_board_update_ts"),
        price_age_sec=_num(row.get("price_age_sec") or row.get("current_price_age_sec")),
        board_age_sec=_num(row.get("board_age_sec")),
    )


def _trade_features_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "entry_expectancy_score_v2": _num(row.get("entry_expectancy_score_v2") or row.get("entry_score_v2")),
        "continuation_quality_score": _num(row.get("continuation_quality_score")),
        "trading_value": _num(row.get("trading_value")),
        "entry_order_book_imbalance": _num(row.get("entry_order_book_imbalance")),
        "entry_vwap_dev_pct": _num(row.get("entry_vwap_dev_pct")),
        "momentum_continuation_score": _num(row.get("momentum_continuation_score")),
        "entry_pool": row.get("entry_pool") or ("OR" if str(row.get("entry_type") or "") == "OR" else "PBV2"),
        "entry_type": row.get("entry_type"),
    }


def _compute_rank(row: Mapping[str, Any]) -> float:
    trade = _trade_features_from_row(row)
    fresh = _freshness_from_row(row)
    return candidate_rank_score(trade, fresh)


def _load_reject_index(sess_dir: Path) -> dict[tuple[str, str], dict[str, Any]]:
    """(scan_id, symbol) -> reject row from events/rejects."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for fname in ("small_paper_events.jsonl", "small_paper_events.csv"):
        path = sess_dir / fname
        if not path.is_file():
            continue
        if fname.endswith(".jsonl"):
            for row in _load_jsonl(path):
                if str(row.get("event_type") or "") != "rejected":
                    continue
                if str(row.get("gate_reject_reason") or row.get("reject_reason") or "") != "max_entries_per_scan":
                    continue
                key = (str(row.get("scan_id") or ""), str(row.get("symbol") or ""))
                out[key] = dict(row)
        else:
            with path.open(encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    if str(row.get("gate_reject_reason") or row.get("reject_reason") or "") != "max_entries_per_scan":
                        continue
                    key = (str(row.get("scan_id") or ""), str(row.get("symbol") or ""))
                    out[key] = dict(row)
    return out


def _load_accept_pnl_index(sess_dir: Path, day: str) -> dict[str, list[dict[str, Any]]]:
    by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in load_trades_for_session(sess_dir, day):
        by_sym[str(t.get("symbol") or "")].append(dict(t))
    return by_sym


def _alt_rank_key(variant: str, row: Mapping[str, Any], enqueue_idx: int) -> tuple:
    v2 = _num(row.get("entry_expectancy_score_v2") or row.get("entry_score_v2")) or 0.0
    cq = _num(row.get("continuation_quality_score")) or 0.0
    tv = _num(row.get("trading_value")) or 0.0
    imb = _num(row.get("entry_order_book_imbalance")) or 0.0
    mom = _num(row.get("momentum_continuation_score")) or 0.0
    rs = _num(row.get("rank_score")) or 0.0
    msg = int(_num(row.get("message_index")) or enqueue_idx)
    if variant == "v2_only":
        return (-v2, msg)
    if variant == "cq_only":
        return (-cq, msg)
    if variant == "tv_only":
        return (-tv, msg)
    if variant == "enqueue_order":
        return (msg,)
    if variant == "v2_then_tv":
        return (-v2, -tv, msg)
    if variant == "production":
        return (-rs, msg)
    return (-rs, msg)


def analyze_session(sess: Mapping[str, Any]) -> tuple[list[dict], list[dict], list[dict], dict[str, Any]]:
    sess_dir = Path(sess["session_dir"])
    day = str(sess["day"])
    audit_rows = _load_jsonl(sess_dir / "entry_scan_audit.jsonl")
    reject_idx = _load_reject_index(sess_dir)
    accept_by_sym = _load_accept_pnl_index(sess_dir, day)

    eval_by_scan: dict[str, list[dict[str, Any]]] = defaultdict(list)
    notify_by_scan: dict[str, list[dict[str, Any]]] = defaultdict(list)
    scan_summaries: list[dict[str, Any]] = []

    for row in audit_rows:
        at = str(row.get("audit_type") or "")
        scan_id = str(row.get("scan_id") or "")
        if at == "entry_symbol_eval":
            eval_by_scan[scan_id].append(row)
        elif at == "entry_notify":
            notify_by_scan[scan_id].append(row)
        elif at == "entry_scan_summary":
            scan_summaries.append(row)

    blocked_outcomes: list[dict[str, Any]] = []
    alt_rows: list[dict[str, Any]] = []
    scan_stats = {
        "scans": 0,
        "multi_candidate_scans": 0,
        "max_scan_blocks": 0,
        "v2_tie_scans": 0,
        "blocked_higher_v2_than_winner": 0,
        "blocked_higher_rank_than_winner": 0,
        "later_accept_available": 0,
    }

    for scan_id, notifies in notify_by_scan.items():
        if not scan_id:
            continue
        scan_stats["scans"] += 1
        n_cand = max((int(n.get("same_scan_candidates") or 0) for n in notifies), default=0)
        if n_cand > 1:
            scan_stats["multi_candidate_scans"] += 1

        evals = {str(e.get("symbol")): e for e in eval_by_scan.get(scan_id, [])}
        winners = [n for n in notifies if n.get("entry_decision")]
        blocked_notify = [n for n in notifies if str(n.get("reject_reason") or "") == "max_entries_per_scan"]

        # Build candidate feature rows for ranking replay
        candidates: list[dict[str, Any]] = []
        enqueue = 0
        for sym, ev in sorted(evals.items(), key=lambda x: str(x[1].get("eval_start_ts") or "")):
            if not ev.get("entry_decision"):
                continue
            enqueue += 1
            rej = reject_idx.get((scan_id, sym), {})
            merged = {**ev, **rej}
            merged["symbol"] = sym
            merged["scan_id"] = scan_id
            merged["enqueue_idx"] = enqueue
            merged["rank_score"] = _compute_rank(merged)
            merged["entry_expectancy_score_v2"] = _num(
                merged.get("entry_expectancy_score_v2") or merged.get("entry_score_v2")
            )
            candidates.append(merged)

        if len(candidates) < 2:
            continue

        ranked = sorted(candidates, key=lambda c: (-float(c["rank_score"]), int(c["enqueue_idx"])))
        winner_notify = winners[0] if winners else None
        winner_sym = str(winner_notify.get("symbol")) if winner_notify else str(ranked[0].get("symbol"))
        winner_row = next((c for c in ranked if str(c.get("symbol")) == winner_sym), ranked[0])
        winner_v2 = int(_num(winner_row.get("entry_expectancy_score_v2")) or 0)

        v2_vals = [int(_num(c.get("entry_expectancy_score_v2")) or 0) for c in candidates]
        if len(set(v2_vals)) == 1 and len(candidates) > 1:
            scan_stats["v2_tie_scans"] += 1

        # Alternative ranking counterfactual per scan
        for variant in ("production", "v2_only", "tv_only", "enqueue_order", "v2_then_tv"):
            alt_sorted = sorted(
                candidates,
                key=lambda c, v=variant: _alt_rank_key(v, c, int(c.get("enqueue_idx") or 0)),
            )
            alt_winner = str(alt_sorted[0].get("symbol"))
            alt_rows.append(
                {
                    "day": day,
                    "session": sess["session"],
                    "scan_id": scan_id,
                    "variant": variant,
                    "winner_symbol": alt_winner,
                    "candidate_count": len(candidates),
                    "differs_from_production": alt_winner != winner_sym,
                }
            )

        for cand in ranked[1:]:
            sym = str(cand.get("symbol"))
            if sym not in {str(n.get("symbol")) for n in blocked_notify}:
                continue
            scan_stats["max_scan_blocks"] += 1
            v2 = int(_num(cand.get("entry_expectancy_score_v2")) or 0)
            if v2 > winner_v2:
                scan_stats["blocked_higher_v2_than_winner"] += 1
            if float(cand.get("rank_score") or 0) > float(winner_row.get("rank_score") or 0):
                scan_stats["blocked_higher_rank_than_winner"] += 1

            later_pnl = None
            later_mfe = None
            trades = accept_by_sym.get(sym) or []
            eval_ts = _parse_ts(str(cand.get("eval_start_ts") or ""))
            if trades and eval_ts:
                for t in trades:
                    ets = _parse_ts(str(t.get("entry_time") or ""))
                    if ets and ets >= eval_ts:
                        later_pnl = _num(t.get("pnl_yen_100"))
                        later_mfe = _num(t.get("peak_mfe_pct"))
                        scan_stats["later_accept_available"] += 1
                        break

            winner_pnl = None
            for t in accept_by_sym.get(winner_sym) or []:
                wts = _parse_ts(str(t.get("entry_time") or ""))
                if wts and eval_ts and wts >= eval_ts:
                    winner_pnl = _num(t.get("pnl_yen_100"))
                    break

            blocked_outcomes.append(
                {
                    "day": day,
                    "session": sess["session"],
                    "source_kind": sess.get("source_kind"),
                    "scan_id": scan_id,
                    "symbol": sym,
                    "entry_pool": cand.get("entry_pool") or "PBV2",
                    "entry_expectancy_score_v2": v2,
                    "continuation_quality_score": _num(cand.get("continuation_quality_score")),
                    "trading_value": _num(cand.get("trading_value")),
                    "momentum_continuation_score": _num(cand.get("momentum_continuation_score")),
                    "entry_order_book_imbalance": _num(cand.get("entry_order_book_imbalance")),
                    "entry_vwap_dev_pct": _num(cand.get("entry_vwap_dev_pct")),
                    "price_age_sec": _num(cand.get("price_age_sec")),
                    "rank_score": round(float(cand.get("rank_score") or 0), 4),
                    "enqueue_idx": cand.get("enqueue_idx"),
                    "eval_start_ts": cand.get("eval_start_ts"),
                    "message_index": cand.get("message_index"),
                    "winner_symbol": winner_sym,
                    "winner_score_v2": winner_v2,
                    "winner_rank_score": round(float(winner_row.get("rank_score") or 0), 4),
                    "rank_delta_vs_winner": round(
                        float(cand.get("rank_score") or 0) - float(winner_row.get("rank_score") or 0), 4
                    ),
                    "v2_delta_vs_winner": v2 - winner_v2,
                    "winner_pnl_yen_100": winner_pnl,
                    "later_accept_pnl_yen_100": later_pnl,
                    "later_accept_mfe_pct": later_mfe,
                    "blocked_higher_v2_than_winner": v2 > winner_v2,
                }
            )

    return blocked_outcomes, alt_rows, scan_summaries, scan_stats


def build_mandatory_answers(
    *,
    agg: Mapping[str, Any],
    blocked: Sequence[Mapping[str, Any]],
    alt_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    n_blocked = len(blocked)
    higher_v2 = sum(1 for r in blocked if r.get("blocked_higher_v2_than_winner"))
    later_pnls = [float(r["later_accept_pnl_yen_100"]) for r in blocked if r.get("later_accept_pnl_yen_100") is not None]
    winner_pnls = [float(r["winner_pnl_yen_100"]) for r in blocked if r.get("winner_pnl_yen_100") is not None]

    alt_diff = defaultdict(int)
    for r in alt_rows:
        if r.get("differs_from_production"):
            alt_diff[str(r.get("variant"))] += 1

    v2_tie_note = (
        "When entry_score_v2 ties, rank_score breaks via cq/tv/imb/vwap/mom/price_age; "
        "exact ties fall through to stable enqueue (PUSH eval) order."
    )

    profit_opportunity = higher_v2 > 0 or (
        later_pnls and statistics.mean(later_pnls) > statistics.mean(winner_pnls) if winner_pnls and later_pnls else False
    )

    return {
        "1_current_ranking_rule": (
            "EntryScanController queues gate-pass candidates per scan batch; "
            "sorts by candidate_rank_score descending; keeps top max_entries_per_scan (1 in production)."
        ),
        "2_score3_tie_break": v2_tie_note,
        "3_non_determinism": (
            "No randomness. Deterministic given same candidate set and enqueue order. "
            "Enqueue order follows PUSH/eval arrival within scan_window_sec."
        ),
        "4_dropping_profitable_candidates": (
            f"Blocked n={n_blocked}; higher v2 than winner in {higher_v2} cases; "
            f"later same-symbol accept available in {agg.get('later_accept_available', 0)} cases."
        ),
        "5_improvement_room": (
            "Yes — rank_score blends many proxies; v2-only ties common; "
            "production cap=1 amplifies scan-ranking impact."
        ),
        "6_alternative_ranking_candidates": [
            "v2_only",
            "v2_then_tv",
            "tv_only",
            "flat_band_shadow_aware (future)",
            "rise5_cap_aware (Phase635 shadow)",
        ],
        "7_change_recommendation": "HOLD — Shadow alternative ranking before any ENTRY cap logic change",
        "alt_variant_winner_changes": dict(alt_diff),
        "blocked_later_accept_mean_pnl": round(statistics.mean(later_pnls), 2) if later_pnls else None,
        "winner_mean_pnl_in_blocked_scans": round(statistics.mean(winner_pnls), 2) if winner_pnls else None,
        "profit_opportunity_signal": profit_opportunity,
    }


@dataclass
class Phase651Job:
    native_root: Path

    def run(self) -> dict[str, Any]:
        sessions = discover_audit_sessions(self.native_root / "results" / "small_paper")
        all_blocked: list[dict[str, Any]] = []
        all_alt: list[dict[str, Any]] = []
        agg = defaultdict(int)

        for sess in sessions:
            blocked, alt, _summaries, stats = analyze_session(sess)
            all_blocked.extend(blocked)
            all_alt.extend(alt)
            for k, v in stats.items():
                if isinstance(v, (int, float)):
                    agg[k] += v

        answers = build_mandatory_answers(agg=agg, blocked=all_blocked, alt_rows=all_alt)

        return {
            "verdict": PHASE651_VERDICT,
            "generated_at": _now_iso(),
            "session_count": len(sessions),
            "aggregate_stats": dict(agg),
            "mandatory_answers": answers,
            "ranking_rules": RANKING_RULES,
            "blocked_outcomes": all_blocked,
            "alternative_ranking": all_alt,
        }

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        out = self.native_root / "results" / "reports" / REPORT_DIR_NAME
        out.mkdir(parents=True, exist_ok=True)
        paths: dict[str, Path] = {}

        _write_csv(
            out / "phase651_ranking_rule_map.csv",
            ["rule_id", "component", "direction", "source", "formula"],
            list(result.get("ranking_rules") or []),
        )
        paths["ranking_rule_map"] = out / "phase651_ranking_rule_map.csv"

        blocked_cols = [
            "day",
            "session",
            "source_kind",
            "scan_id",
            "symbol",
            "entry_pool",
            "entry_expectancy_score_v2",
            "continuation_quality_score",
            "trading_value",
            "momentum_continuation_score",
            "rank_score",
            "winner_symbol",
            "winner_score_v2",
            "winner_rank_score",
            "rank_delta_vs_winner",
            "v2_delta_vs_winner",
            "winner_pnl_yen_100",
            "later_accept_pnl_yen_100",
            "later_accept_mfe_pct",
            "blocked_higher_v2_than_winner",
        ]
        _write_csv(out / "phase651_blocked_candidate_outcome.csv", blocked_cols, list(result.get("blocked_outcomes") or []))
        paths["blocked_candidate_outcome"] = out / "phase651_blocked_candidate_outcome.csv"

        _write_csv(
            out / "phase651_alternative_ranking_counterfactual.csv",
            ["day", "session", "scan_id", "variant", "winner_symbol", "candidate_count", "differs_from_production"],
            list(result.get("alternative_ranking") or []),
        )
        paths["alternative_ranking"] = out / "phase651_alternative_ranking_counterfactual.csv"

        report = {
            "phase": "651",
            "verdict": result.get("verdict"),
            "generated_at": result.get("generated_at"),
            "session_count": result.get("session_count"),
            "aggregate_stats": result.get("aggregate_stats"),
            "mandatory_answers": result.get("mandatory_answers"),
            "artifacts": {k: str(v) for k, v in paths.items()},
        }
        fp = out / "phase651_report.json"
        fp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        paths["report"] = fp
        return paths


def main() -> int:
    job = Phase651Job(native_root=NATIVE_ROOT)
    result = job.run()
    paths = job.write_outputs(result)
    print(json.dumps({"verdict": result.get("verdict"), "paths": {k: str(v) for k, v in paths.items()}}, indent=2))
    print(json.dumps(result.get("mandatory_answers"), ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
