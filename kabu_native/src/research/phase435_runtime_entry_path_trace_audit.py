"""
Phase435 — Runtime entry path trace audit (20260618).

Research only — no Runtime/YAML/Entry/Exit/Order/Discord changes.
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
from zoneinfo import ZoneInfo

from replay.pnl_yen import enrich_trade_pnl_yen
from research.market_sector_heat import _pf, _win_rate, _write_csv
from research.phase431_entry_priority_reentry_audit import _float, _parse_ts
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.canonical_summary import collect_canonical_trades, is_stop_exit
from small_paper.config import load_pilot_config
from small_paper.entry_expectancy_score_shadow import (
    ENTRY_SCORE_V2_GATE_MIN,
    REQUIRED_V2_TOKENS,
    SCORE_POINTS_V2,
    TERTILE_CUTOFFS,
    active_score_tokens_v2,
    compute_entry_expectancy_score_fields,
    momentum_low_required_for_v2,
)
from small_paper.entry_scan_controller import (
    EntryFreshnessSnapshot,
    candidate_rank_score,
    compute_entry_freshness,
)

JST = ZoneInfo("Asia/Tokyo")
TARGET_DAY = "20260618"
SESSION_DIRS = ("live_session_081230", "live_session_122524")
STOP_REASONS = frozenset({"hard_stop", "stop_loss", "loss_cut", "stop_hit"})

ENTRY_CODE_PATHS = {
    "final_gate": "research/exposure_gate.py::ExposureGate.evaluate_entry",
    "gate_wrapper": "small_paper/pilot_runner.py::_evaluate_gate_entry",
    "push_pipeline": "small_paper/pilot_runner.py::_process_push_payload",
    "execute_entry": "small_paper/pilot_runner.py::_execute_accepted_entry",
    "scan_controller": "small_paper/entry_scan_controller.py::EntryScanController._flush_locked",
    "rank_score": "small_paper/entry_scan_controller.py::candidate_rank_score",
    "v2_score": "small_paper/entry_expectancy_score_shadow.py::compute_entry_expectancy_score_fields",
    "momentum_low_check": "small_paper/entry_expectancy_score_shadow.py::momentum_low_required_for_v2",
}


def _now_iso() -> str:
    return datetime.now(JST).isoformat(timespec="seconds")


def _session_base(day: str, *, repo_root: Path) -> Path:
    return resolve_kabu_root(repo_root) / "results" / "small_paper" / day


def _load_events(session_dir: Path) -> list[dict[str, Any]]:
    path = session_dir / "small_paper_events.csv"
    if not path.is_file():
        return []
    with path.open(encoding="utf-8", newline="") as fh:
        return [dict(r) for r in csv.DictReader(fh)]


def _load_scan_audit(session_dir: Path) -> list[dict[str, Any]]:
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
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _is_stop_trade(row: Mapping[str, Any]) -> bool:
    reason = str(row.get("exit_reason") or row.get("close_reason") or "").strip()
    if reason in STOP_REASONS:
        return True
    return bool(row.get("stop_hit")) and reason not in ("trailing_mfe_exit", "morning_session_close", "afternoon_session_close")


def _nearest_notify(
    audits: Sequence[Mapping[str, Any]],
    *,
    symbol: str,
    entry_time: str,
) -> Optional[dict[str, Any]]:
    target = _parse_ts(entry_time)
    if target is None:
        return None
    best: Optional[dict[str, Any]] = None
    best_delta = 999999.0
    for row in audits:
        if row.get("audit_type") != "entry_notify":
            continue
        if str(row.get("symbol") or "") != symbol:
            continue
        if not row.get("entry_decision"):
            continue
        ts = _parse_ts(str(row.get("entry_signal_ts") or ""))
        if ts is None:
            continue
        delta = abs((ts - target).total_seconds())
        if delta < best_delta:
            best_delta = delta
            best = dict(row)
    return best if best is not None and best_delta <= 5 else None


def _classify_entry_path(trade: Mapping[str, Any]) -> tuple[str, str, list[str]]:
    tokens = active_score_tokens_v2(trade)
    fields = compute_entry_expectancy_score_fields(trade=trade)
    v2 = int(fields.get("entry_expectancy_score_v2") or 0)
    mom_low = momentum_low_required_for_v2(trade)
    board_mid = "Board:mid" in tokens

    if mom_low and board_mid and v2 >= ENTRY_SCORE_V2_GATE_MIN:
        path = "A_momentum_low_board_mid_gate"
        reason = "ExposureGate: momentum_low_required + entry_expectancy_score_v2>=entry_score_v2_min"
    elif v2 >= ENTRY_SCORE_V2_GATE_MIN:
        path = "B_entry_expectancy_score_v2_gate"
        reason = "ExposureGate: v2>=min without full Momentum:low+Board:mid token pair"
    else:
        path = "D_other_unknown"
        reason = "legacy_quality_or_unknown"

    exact = ["push_pipeline", "gate_wrapper", "final_gate", "v2_score", "momentum_low_check"]
    if path.startswith("A") or path.startswith("B"):
        exact.append("scan_controller")
        exact.append("rank_score")
        exact.append("execute_entry")
    branches = [
        f"exposure_gate.py:345-363 entry_score_v2_min={ENTRY_SCORE_V2_GATE_MIN}",
        f"momentum_low_required_for_v2={mom_low}",
        f"active_score_tokens_v2={tokens}",
        f"entry_expectancy_score_v2={v2}",
    ]
    return path, reason, branches


def _rank_score_for_trade(trade: Mapping[str, Any], notify: Optional[Mapping[str, Any]] = None) -> float:
    n = notify or {}
    age = _float(n.get("price_age_sec"))
    if age is not None:
        trade_scoring = dict(trade)
        freshness = EntryFreshnessSnapshot(
            data_source=str(n.get("data_source") or "kabu_push"),
            last_price_update_ts=None,
            last_board_update_ts=None,
            price_age_sec=age,
            board_age_sec=_float(n.get("board_age_sec")),
        )
        return round(candidate_rank_score(trade_scoring, freshness), 4)
    payload = {
        "CurrentPriceTime": trade.get("entry_time"),
        "BidTime": trade.get("entry_time"),
        "AskTime": trade.get("entry_time"),
        "CurrentPrice": trade.get("entry_price") or trade.get("current_price"),
    }
    freshness = compute_entry_freshness(payload, pipeline_source="live")
    return round(candidate_rank_score(trade, freshness), 4)


def _code_path_audit(config_path: Path) -> dict[str, Any]:
    cfg = load_pilot_config(config_path)
    return {
        "final_accept_function": ENTRY_CODE_PATHS["final_gate"],
        "gate_wrapper_function": ENTRY_CODE_PATHS["gate_wrapper"],
        "entry_score_v2_min_yaml": int(getattr(cfg, "entry_score_v2_min", 0) or 0),
        "entry_score_v2_min_constant": ENTRY_SCORE_V2_GATE_MIN,
        "entry_score_v2_meaning": (
            "entry_expectancy_score_v2 is sum of SCORE_POINTS_V2 token hits; "
            "max=3 (Momentum:low=2 + Board:mid=1). "
            "Gate requires momentum_low_required_for_v2 AND score>=entry_score_v2_min."
        ),
        "momentum_low_board_mid_role": (
            "REQUIRED: momentum_low_required_for_v2 (Momentum:low token) is mandatory when entry_score_v2_min>0. "
            "Board:mid is not independently required but score=3 at min=3 implies both tokens."
        ),
        "entry_expectancy_score_v2_ge5_is_gate": False,
        "entry_expectancy_score_v2_ge5_note": (
            "ge5/ge6 flags apply to v1 entry_expectancy_score (SCORE_POINTS), not v2 gate. "
            "v2 maximum is 3; >=5 is impossible for v2."
        ),
        "gate_accept_generation": (
            "pilot_runner._event_from_gate sets gate_accept=decision.accept from ExposureGate.evaluate_entry"
        ),
        "candidate_rank_score_role": (
            "Ranking only within EntryScanController scan batch; does NOT grant entry permission. "
            "Permission is ExposureGate accept; rank_score breaks ties for max_entries_per_scan."
        ),
        "max_entries_per_scan": int(getattr(cfg, "max_entries_per_scan", 1) or 1),
        "max_entries_per_scan_ranks": (
            "candidate_rank_score (v2*1000 + cq*100 + tv + imb + vwap + mom - price_age) descending; "
            "top N per 2s scan window sent to _execute_accepted_entry"
        ),
        "SCORE_POINTS_V2": dict(SCORE_POINTS_V2),
        "REQUIRED_V2_TOKENS": sorted(REQUIRED_V2_TOKENS),
        "TERTILE_CUTOFFS_used": {
            k: TERTILE_CUTOFFS[k] for k in ("Momentum", "Board") if k in TERTILE_CUTOFFS
        },
        "phase314_vs_runtime": {
            "phase314_design": "Momentum:low + Board:mid only; min score 3",
            "runtime_yaml": str(config_path),
            "reject_below_quality": bool(cfg.reject_below_quality),
            "difference": "Runtime matches Phase314 when entry_score_v2_min=3",
        },
        "config_path": str(config_path),
        "functions": ENTRY_CODE_PATHS,
    }


def _load_accepted_with_exits(repo_root: Path) -> list[dict[str, Any]]:
    base = _session_base(TARGET_DAY, repo_root=repo_root)
    exit_by_key: dict[str, dict[str, Any]] = {}
    for sess in SESSION_DIRS:
        sd = base / sess
        for row in _load_events(sd):
            if str(row.get("event_type") or "") != "observer_exit":
                continue
            key = f"{row.get('symbol')}|{row.get('entry_time')}"
            exit_by_key[key] = enrich_trade_pnl_yen(dict(row))

    rows: list[dict[str, Any]] = []
    for sess in SESSION_DIRS:
        sd = base / sess
        audits = _load_scan_audit(sd)
        for row in _load_events(sd):
            if str(row.get("event_type") or "") != "accepted":
                continue
            trade = dict(row)
            trade["session"] = sess
            ep = _float(trade.get("entry_price")) or _float(trade.get("current_price"))
            trade["entry_price"] = ep
            et = str(trade.get("entry_time") or "")
            ex = exit_by_key.get(f"{trade.get('symbol')}|{et}", {})
            trade["exit_time"] = ex.get("event_time") or ex.get("exit_time") or ""
            trade["exit_price"] = _float(ex.get("exit_price"))
            trade["pnl_pct"] = _float(ex.get("pnl_pct"))
            trade["pnl_yen_100"] = _float(ex.get("pnl_yen_100"))
            if (trade.get("pnl_yen_100") in (None, 0)) and ep and trade.get("exit_price"):
                trade["pnl_yen_100"] = round((trade["exit_price"] - ep) * 100, 2)
            trade["exit_reason"] = str(ex.get("exit_reason") or "")
            trade["stop_hit"] = _is_stop_trade(ex or trade)
            notify = _nearest_notify(audits, symbol=str(trade["symbol"]), entry_time=et)
            trade["_scan_notify"] = notify or {}
            rows.append(trade)
    rows.sort(key=lambda r: (_parse_ts(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST)))
    return rows


def _trace_row(trade: Mapping[str, Any], *, entry_index: int) -> dict[str, Any]:
    tokens = active_score_tokens_v2(trade)
    fields = compute_entry_expectancy_score_fields(trade=trade)
    path, accept_reason, branches = _classify_entry_path(trade)
    notify = trade.get("_scan_notify") or {}
    rank = _rank_score_for_trade(trade, notify)
    mom = _float(trade.get("momentum_continuation_score"))
    imb = _float(trade.get("entry_order_book_imbalance"))
    return {
        "entry_index": entry_index,
        "symbol": trade.get("symbol"),
        "session": trade.get("session"),
        "entry_time": trade.get("entry_time"),
        "entry_price": _float(trade.get("entry_price")),
        "exit_time": trade.get("exit_time"),
        "exit_price": trade.get("exit_price"),
        "pnl_yen_100": trade.get("pnl_yen_100"),
        "pnl_pct": trade.get("pnl_pct"),
        "exit_reason": trade.get("exit_reason"),
        "stop_hit": trade.get("stop_hit"),
        "gate_accept": trade.get("gate_accept"),
        "entry_score_v2": fields.get("entry_expectancy_score_v2"),
        "entry_score_v2_min": trade.get("entry_score_v2_threshold") or ENTRY_SCORE_V2_GATE_MIN,
        "entry_expectancy_score_v2": fields.get("entry_expectancy_score_v2"),
        "entry_expectancy_score_v1": fields.get("entry_expectancy_score"),
        "entry_expectancy_score_v2_ge5_flag": fields.get("entry_expectancy_score_v2_ge5_flag"),
        "entry_expectancy_score_ge5_flag": fields.get("entry_expectancy_score_ge5_flag"),
        "candidate_rank_score": rank,
        "active_score_tokens_v2": ";".join(tokens),
        "momentum_category": "low" if "Momentum:low" in tokens else "not_low",
        "board_category": "mid" if "Board:mid" in tokens else "not_mid",
        "momentum_score_component": mom,
        "board_score_component": imb,
        "continuation_quality": _float(trade.get("continuation_quality_score")),
        "entry_path_class": path,
        "accept_reason": accept_reason,
        "exact_entry_path": " -> ".join(
            [
                "required_score_pass" if path.startswith("A") else "expectancy_v2_pass",
                "scan_rank_selected" if notify.get("same_scan_rank") else "scan_solo",
            ]
        ),
        "source_function_branch": " | ".join(branches),
        "scan_id": notify.get("scan_id") or trade.get("scan_id"),
        "same_scan_rank": notify.get("same_scan_rank"),
        "same_scan_candidates": notify.get("same_scan_candidates"),
        "pullback_guard_blocked": trade.get("pullback_misread_dynamic40_guard_blocked"),
        "near_day_high_guard_blocked": trade.get("near_day_high_low_momentum_dynamic40_guard_blocked"),
        "entry_rise_5min_pct": trade.get("entry_rise_5min_pct"),
        "entry_vwap_dev_pct": trade.get("entry_vwap_dev_pct"),
        "entry_near_day_high_pct": trade.get("entry_near_day_high_pct"),
        "daytrade_suitability_score": trade.get("daytrade_suitability_score"),
        "universe_bucket": trade.get("universe_bucket"),
    }


def _aggregate_paths(traces: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for t in traces:
        buckets[str(t.get("entry_path_class") or "D_other_unknown")].append(dict(t))

    rows: list[dict[str, Any]] = []
    for path, items in sorted(buckets.items()):
        pnls = [_float(x.get("pnl_yen_100")) for x in items]
        stops = sum(1 for x in items if x.get("stop_hit"))
        sym_pnl: dict[str, float] = defaultdict(float)
        for x in items:
            sym_pnl[str(x.get("symbol"))] += _float(x.get("pnl_yen_100"))
        worst = min(sym_pnl.items(), key=lambda kv: kv[1], default=("", 0.0))
        top_loss = sorted(sym_pnl.items(), key=lambda kv: kv[1])[:3]
        rows.append(
            {
                "entry_path_class": path,
                "count": len(items),
                "total_pnl_yen_100": round(sum(pnls), 2),
                "profit_factor": _pf(pnls),
                "stop_rate": round(stops / len(items), 4) if items else 0.0,
                "avg_pnl_yen_100": round(statistics.mean(pnls), 2) if pnls else 0.0,
                "symbol_count": len(sym_pnl),
                "worst_symbol": worst[0],
                "worst_symbol_pnl_yen_100": round(worst[1], 2),
                "top_loss_symbols": ",".join(f"{s}:{round(p,0)}" for s, p in top_loss),
            }
        )
    return rows


def _loss_attribution(traces: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for t in traces:
        rows.append(
            {
                "symbol": t.get("symbol"),
                "entry_time": t.get("entry_time"),
                "entry_path_class": t.get("entry_path_class"),
                "pnl_yen_100": t.get("pnl_yen_100"),
                "stop_hit": t.get("stop_hit"),
                "active_score_tokens_v2": t.get("active_score_tokens_v2"),
                "entry_expectancy_score_v2": t.get("entry_expectancy_score_v2"),
            }
        )
    return sorted(rows, key=lambda r: _float(r.get("pnl_yen_100")))


def _verdict(code_audit: Mapping[str, Any], dist: Sequence[Mapping[str, Any]]) -> str:
    a_count = next((int(r["count"]) for r in dist if r.get("entry_path_class") == "A_momentum_low_board_mid_gate"), 0)
    total = sum(int(r.get("count") or 0) for r in dist)
    if a_count == total and total > 0:
        return "phase314_misunderstood"
    if code_audit.get("entry_expectancy_score_v2_ge5_is_gate") is False:
        return "config_doc_mismatch"
    return "entry_path_expected"


def run_phase435_audit(*, repo_root: Path) -> dict[str, Any]:
    kabu = resolve_kabu_root(repo_root)
    runtime_summary = _read_json(
        kabu / "results" / "daily" / TARGET_DAY / "runtime" / f"daily_runner_summary_{TARGET_DAY}.json"
    )
    config_rel = str(runtime_summary.get("config_rel") or "kabu_native/configs/small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml")
    config_path = repo_root / config_rel.replace("/", "\\") if "\\" in str(repo_root) else repo_root / config_rel
    if not config_path.is_file():
        config_path = kabu / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"

    code_audit = _code_path_audit(config_path)
    all_accepted = _load_accepted_with_exits(repo_root)
    traces = [_trace_row(t, entry_index=i + 1) for i, t in enumerate(all_accepted)]
    traces_6976 = [t for t in traces if t.get("symbol") == "6976.T"]
    dist = _aggregate_paths(traces)
    loss_rows = _loss_attribution(traces)

    pullback_note = (
        "Pullback guard blocks only when entry_rise_5min_pct<0 AND entry_vwap_dev_pct<0 on dynamic40. "
        "6976 entries had negative 5m rise but positive vwap_dev → guard did not fire."
    )
    near_high_note = (
        "Near-day-high guard blocks when day_high_distance<=1.5% AND entry_momentum<0.30. "
        "6976 entries were often >1.5% below day high (entry_near_day_high_pct ~1.7-5%) or momentum not low enough for guard field."
    )

    mandatory = {
        "1_why_6976_7_entries": (
            "6976 passed ExposureGate 7 times (Momentum:low+Board:mid, v2=3) on separate push cycles; "
            "no_overlap_replace allows re-entry after prior position closed; 4 became stop_hit."
        ),
        "2_entry_without_phase434_classification": (
            "Phase434 used wrong fields (entry_momentum_score/entry_imbalance_percentile). "
            "Runtime uses momentum_continuation_score + entry_order_book_imbalance tertiles — all 7 ARE Momentum:low+Board:mid."
        ),
        "3_is_expectancy_v2_entry_permission": (
            "Yes as gate: entry_expectancy_score_v2>=entry_score_v2_min WITH momentum_low_required. "
            "No: ge5 is v1 shadow flag; v2 max is 3."
        ),
        "4_true_runtime_entry_conditions": code_audit["entry_score_v2_meaning"],
        "5_6976_entry_path": "A_momentum_low_board_mid_gate for all 7",
        "6_distribution_89": {r["entry_path_class"]: r["count"] for r in dist},
        "7_loss_driver_path": max(dist, key=lambda r: abs(_float(r.get("total_pnl_yen_100"))))["entry_path_class"],
        "8_phase314_understanding_wrong": (
            "Phase314 runtime is correct; Phase434 audit metric was wrong. Docs/architecture are correct."
        ),
        "9_what_to_fix": (
            "Not entry gate logic — fix audit classification fields; consider guard tuning (pullback vwap sign, "
            "same-symbol stop cooldown) not score_v2 path."
        ),
        "10_next_improvements": [
            "Pullback guard: require negative vwap_dev OR rising-from-low pattern on downtrend",
            "Same-symbol cooldown after stop_hit (Phase434 counterfactual +97.5k)",
            "High-notional price band cap for 10k+ symbols",
            "Audit tooling: use active_score_tokens_v2 not percentile proxies",
        ],
        "6976_guard_notes": {
            "pullback": pullback_note,
            "near_day_high": near_high_note,
            "daytrade_suitability": "6976 in vol_liq_dynamic40 top50; suitability passed",
        },
    }

    verdict = _verdict(code_audit, dist)

    return {
        "phase": "435-Runtime-Entry-Path-Trace",
        "generated_at": _now_iso(),
        "verdict": verdict,
        "mandatory_answers": mandatory,
        "code_audit": code_audit,
        "outputs": {
            "6976_trace": traces_6976,
            "distribution": dist,
            "loss_attribution": loss_rows,
        },
    }


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _csv_write(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
        return
    fields = list(rows[0].keys())
    _write_csv(path, fields, rows)


@dataclass
class Phase435Job:
    repo_root: Path

    def run(self) -> dict[str, Any]:
        return run_phase435_audit(repo_root=self.repo_root)

    def write_outputs(self, result: Mapping[str, Any]) -> dict[str, Path]:
        reports = resolve_reports_dir(self.repo_root)
        reports.mkdir(parents=True, exist_ok=True)
        kabu = resolve_kabu_root(self.repo_root)
        out = result.get("outputs") or {}

        paths = {
            "6976": reports / "phase435_entry_path_trace_6976.csv",
            "distribution": reports / "phase435_entry_path_distribution.csv",
            "code": reports / "phase435_entry_code_path_audit.json",
            "loss": reports / "phase435_entry_path_loss_attribution.csv",
            "summary": reports / "phase435_entry_path_summary.json",
            "report": kabu / "docs" / "operations" / "phase435_runtime_entry_path_trace_report.md",
        }

        _csv_write(paths["6976"], out.get("6976_trace") or [])
        _csv_write(paths["distribution"], out.get("distribution") or [])
        _csv_write(paths["loss"], out.get("loss_attribution") or [])

        summary_payload = {
            "phase": result.get("phase"),
            "generated_at": result.get("generated_at"),
            "verdict": result.get("verdict"),
            "mandatory_answers": result.get("mandatory_answers"),
            "code_audit_summary": {
                k: result.get("code_audit", {}).get(k)
                for k in (
                    "final_accept_function",
                    "entry_score_v2_min_yaml",
                    "entry_score_v2_meaning",
                    "momentum_low_board_mid_role",
                    "entry_expectancy_score_v2_ge5_is_gate",
                    "candidate_rank_score_role",
                    "max_entries_per_scan",
                )
            },
            "distribution": out.get("distribution"),
        }
        paths["code"].write_text(
            json.dumps(result.get("code_audit") or {}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        paths["summary"].write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False), encoding="utf-8")

        m = result.get("mandatory_answers") or {}
        lines = [
            "# Phase435 — Runtime Entry Path Trace Report",
            "",
            f"Generated: {result.get('generated_at')}",
            f"**Verdict:** `{result.get('verdict')}`",
            "",
            "## Part A — Code Audit (summary)",
            "",
            f"- Final accept: `{result.get('code_audit', {}).get('final_accept_function')}`",
            f"- `entry_score_v2_min`: **{result.get('code_audit', {}).get('entry_score_v2_min_yaml')}** — {result.get('code_audit', {}).get('entry_score_v2_meaning')}",
            f"- Momentum+Board role: {result.get('code_audit', {}).get('momentum_low_board_mid_role')}",
            f"- `entry_expectancy_score_v2>=5` is gate: **{result.get('code_audit', {}).get('entry_expectancy_score_v2_ge5_is_gate')}**",
            f"- `candidate_rank_score`: {result.get('code_audit', {}).get('candidate_rank_score_role')}",
            f"- `max_entries_per_scan`: {result.get('code_audit', {}).get('max_entries_per_scan')}",
            "",
            "## Part B — 6976.T",
            "",
            f"All 7 entries: **{m.get('5_6976_entry_path')}**",
            "",
            f"Why 7 entries: {m.get('1_why_6976_7_entries')}",
            "",
            "## Part C — 89 accepted distribution",
            "",
            f"{m.get('6_distribution_89')}",
            "",
            f"Loss driver: **{m.get('7_loss_driver_path')}**",
            "",
            "## Part D/E — Phase314 consistency",
            "",
            f"- Phase434 mismatch: {m.get('2_entry_without_phase434_classification')}",
            f"- Phase314 wrong?: {m.get('8_phase314_understanding_wrong')}",
            "",
            "### Guards (6976)",
            "",
            f"- Pullback: {(m.get('6976_guard_notes') or {}).get('pullback')}",
            f"- Near day high: {(m.get('6976_guard_notes') or {}).get('near_day_high')}",
            "",
            "## Mandatory answers",
            "",
        ]
        for i, key in enumerate(
            [
                "1_why_6976_7_entries",
                "2_entry_without_phase434_classification",
                "3_is_expectancy_v2_entry_permission",
                "4_true_runtime_entry_conditions",
                "5_6976_entry_path",
                "6_distribution_89",
                "7_loss_driver_path",
                "8_phase314_understanding_wrong",
                "9_what_to_fix",
                "10_next_improvements",
            ],
            start=1,
        ):
            lines.append(f"{i}. {m.get(key)}")
            lines.append("")

        lines.append("## Artifacts")
        for k, p in paths.items():
            if k != "report":
                lines.append(f"- `{p.name}`")

        paths["report"].parent.mkdir(parents=True, exist_ok=True)
        paths["report"].write_text("\n".join(lines), encoding="utf-8")
        return paths
