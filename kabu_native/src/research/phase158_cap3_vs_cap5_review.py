"""
Phase 158: cap3 vs cap5 re-evaluation under price-risk + entry guard (shadow only).

Separates:
  - cap_evaluation: virtual-hold counterfactual (ExposureGate on filtered candidates)
  - exit_evaluation: structural exit replay on candidate stream (combined v1)
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.cap3_entry_replay import (
    SCENARIO_CURRENT,
    SimPosition,
    _close_position,
    _cross_symbol_cooldown_blocks,
    _process_post_fade_tick,
    _profit_factor,
    _sync_gate_slots,
)
from research.cap_sensitivity_review import _newly_accepted_keys
from research.exposure_cap_whatif_review import PHASE53_MIN_QUALITY, _simulate_cap_scenario
from research.exposure_gate import REJECT_MAX_CONCURRENT, ExposureGate, ExposureGateConfig
from research.fade_switch_policy_review import FADE_EXIT_REASONS
from research.mfe_mae_exit_review import discover_sessions, parse_ts, pnl_pct
from research.phase156_intraday_refresh_cap5_review import (
    _filter_price_risk_candidates,
)
from research.research_exit_criteria import _as_float
from research.runtime_pilot_policy_review import (
    _build_price_index,
    _candidates_from_events,
    _trade_from_candidate,
)
from research.small_paper_performance_review import _load_events, _load_json
from research.structural_exit_policies import (
    POLICY_COMBINED_STRUCTURAL_EXIT_V1,
    combined_exit_signal_on_latest_tick,
    tick_from_candidate,
)
from research.structural_observer_review import _session_end_time

PHASE158_CAPS = (3, 5)
MIN_SESSIONS = 4
MIN_CAP5_ONLY_TRADES = 8
OVERLAP_EXIT_REASON = "overlap_replaced_review"


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


def _trade_key(row: Mapping[str, Any]) -> tuple[str, str]:
    return (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))


def _mfe_mae_from_ticks(pos: SimPosition) -> tuple[Optional[float], Optional[float]]:
    if not pos.rich_ticks or pos.entry_price <= 0:
        return None, None
    pnls = [pnl_pct(pos.entry_price, float(t.get("price") or pos.entry_price)) for t in pos.rich_ticks]
    return round(max(pnls), 4), round(min(pnls), 4)


def _cap_metrics(rows: Sequence[Mapping[str, Any]], *, label: str) -> dict[str, Any]:
    pnls = [float(r.get("realized_pnl_pct") or 0) for r in rows]
    wins = [p for p in pnls if p > 0]
    mfes = [
        float(r.get("rolling_mfe_pct") or r.get("max_favorable_excursion_pct") or 0)
        for r in rows
        if _as_float(r.get("rolling_mfe_pct") or r.get("max_favorable_excursion_pct")) is not None
    ]
    maes = [
        float(r.get("rolling_mae_pct") or r.get("max_adverse_excursion_pct") or 0)
        for r in rows
        if _as_float(r.get("rolling_mae_pct") or r.get("max_adverse_excursion_pct")) is not None
    ]
    return {
        "bucket": label,
        "trade_count": len(rows),
        "total_pnl_proxy": round(sum(pnls), 4) if pnls else 0.0,
        "avg_pnl_proxy": round(statistics.mean(pnls), 4) if pnls else None,
        "pf_proxy": _profit_factor(pnls),
        "win_rate": round(len(wins) / len(pnls), 4) if pnls else None,
        "max_loss_pct": round(min(pnls), 4) if pnls else None,
        "max_gain_pct": round(max(pnls), 4) if pnls else None,
        "mfe_pct_mean_at_entry_proxy": round(statistics.mean(mfes), 4) if mfes else None,
        "mae_pct_mean_at_entry_proxy": round(statistics.mean(maes), 4) if maes else None,
    }


def _exit_metrics_from_positions(
    positions: Sequence[SimPosition],
    *,
    label: str,
) -> dict[str, Any]:
    pnls = [p.realized_pnl_pct for p in positions]
    mfes: list[float] = []
    maes: list[float] = []
    for p in positions:
        mfe, mae = _mfe_mae_from_ticks(p)
        if mfe is not None:
            mfes.append(mfe)
        if mae is not None:
            maes.append(mae)
    reasons = Counter(p.close_reason for p in positions)
    overlap = reasons.get(OVERLAP_EXIT_REASON, 0)
    fade = sum(reasons.get(r, 0) for r in FADE_EXIT_REASONS)
    return {
        "bucket": label,
        "trade_count": len(positions),
        "total_pnl_pct": round(sum(pnls), 4) if pnls else 0.0,
        "avg_pnl_pct": round(statistics.mean(pnls), 4) if pnls else None,
        "pf": _profit_factor(pnls),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4) if pnls else None,
        "mfe_pct_mean": round(statistics.mean(mfes), 4) if mfes else None,
        "mae_pct_mean": round(statistics.mean(maes), 4) if maes else None,
        "mfe_capture_mean": (
            round(statistics.mean(p.realized_pnl_pct / m if m > 0 else 0 for p, m in zip(positions, mfes) if m and m > 0), 4)
            if mfes and any(m > 0 for m in mfes)
            else None
        ),
        "exit_reason_counts": dict(reasons),
        "overlap_replaced_count": overlap,
        "overlap_replaced_rate_pct": round(100.0 * overlap / max(1, len(positions)), 2),
        "fade_exit_count": fade,
        "fade_exit_rate_pct": round(100.0 * fade / max(1, len(positions)), 2),
        "stop_hit_count": reasons.get("stop_hit", 0),
    }


def simulate_cap_exit_from_candidates(
    candidates: Sequence[Mapping[str, Any]],
    *,
    session_id: str,
    max_concurrent: int,
    profile: str,
    exit_cfg: Any,
    session_end: str,
    min_quality: float = PHASE53_MIN_QUALITY,
) -> list[SimPosition]:
    """Candidate-stream cap replay with structural combined v1 exits (review only)."""
    from research.cap3_entry_replay import Cap3ReplayResult

    gate = ExposureGate(
        ExposureGateConfig(
            profile=profile,
            min_continuation_quality=min_quality,
            max_concurrent_positions=max_concurrent,
            reject_below_quality=True,
            min_above_median_quality=0.42,
        )
    )
    result = Cap3ReplayResult(scenario=SCENARIO_CURRENT, session_id=session_id)
    ordered = sorted(candidates, key=lambda e: int(e.get("message_index") or 0))
    open_positions: list[SimPosition] = []
    post_fade: dict[str, Any] = {}
    recent_fades: list[Any] = []
    session_end_ts = parse_ts(session_end)

    def try_close(pos: SimPosition, row: Mapping[str, Any], ts: float) -> None:
        tick = tick_from_candidate(
            dict(row), pos.entry_price, float(row.get("continuation_quality_score") or 0)
        )
        tick["ts_epoch"] = ts
        pos.rich_ticks.append(tick)
        sig = combined_exit_signal_on_latest_tick(pos.rich_ticks, pos.entry_price, exit_cfg)
        if not sig:
            return
        pnl_val, reason, close_px = sig
        if reason in FADE_EXIT_REASONS or reason == "stop_hit":
            if pos in open_positions:
                open_positions.remove(pos)
            pf = _close_position(
                pos,
                close_time=str(row.get("entry_time") or ""),
                close_ts=ts,
                close_price=close_px,
                reason=reason,
                result=result,
            )
            if pf:
                post_fade[pos.symbol] = pf
                recent_fades.append(pf)
            return
        if pos in open_positions:
            open_positions.remove(pos)
        _close_position(
            pos,
            close_time=str(row.get("entry_time") or ""),
            close_ts=ts,
            close_price=close_px,
            reason=reason,
            result=result,
        )

    for row in ordered:
        sym = str(row.get("symbol") or "")
        ent_raw = str(row.get("entry_time") or "")
        ts = parse_ts(ent_raw)
        price = float(row.get("current_price") or 0)
        if not sym:
            continue

        for pos in list(open_positions):
            if pos.symbol == sym and pos.is_open:
                try_close(pos, row, ts)

        if sym in post_fade and not post_fade[sym].released and price > 0:
            _process_post_fade_tick(
                post_fade[sym],
                price=price,
                momentum=_as_float(row.get("momentum_continuation_score")),
                scenario=SCENARIO_CURRENT,
            )

        if price <= 0:
            continue

        trade = _trade_from_candidate(row)
        _sync_gate_slots(gate, open_positions, horizon_ts=ts)
        decision = gate.evaluate_entry(trade)
        if not decision.accept:
            continue

        ob = {p.symbol: p for p in open_positions if p.is_open}
        if sym in ob:
            old = ob[sym]
            open_positions.remove(old)
            old.replaced_by_overlap = True
            _close_position(
                old,
                close_time=ent_raw,
                close_ts=ts,
                close_price=price,
                reason=OVERLAP_EXIT_REASON,
                result=result,
            )

        blocked, _, _ = _cross_symbol_cooldown_blocks(
            post_fade, new_symbol=sym, scenario=SCENARIO_CURRENT
        )
        if blocked:
            continue

        open_positions.append(
            SimPosition(symbol=sym, entry_time=ent_raw, entry_ts=ts, entry_price=price)
        )

    for pos in list(open_positions):
        if not pos.is_open:
            continue
        close_px = pos.entry_price
        if pos.rich_ticks:
            close_px = float(pos.rich_ticks[-1].get("price") or close_px)
        open_positions.remove(pos)
        _close_position(
            pos,
            close_time=session_end,
            close_ts=session_end_ts,
            close_price=close_px,
            reason="session_end",
            result=result,
        )

    return result.closed_positions


def _position_rows(positions: Sequence[SimPosition], *, bucket: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for p in positions:
        mfe, mae = _mfe_mae_from_ticks(p)
        rows.append(
            {
                "bucket": bucket,
                "symbol": p.symbol,
                "entry_time": p.entry_time,
                "entry_price": p.entry_price,
                "close_time": p.close_time,
                "close_reason": p.close_reason,
                "realized_pnl_pct": p.realized_pnl_pct,
                "mfe_pct": mfe,
                "mae_pct": mae,
                "replaced_by_overlap": p.replaced_by_overlap,
            }
        )
    return rows


def determine_verdict(
    *,
    cap_eval: Mapping[str, Any],
    exit_eval: Mapping[str, Any],
    session_count: int,
) -> tuple[str, list[str], dict[str, str]]:
    notes: list[str] = []
    cap5_only_count = int(cap_eval.get("cap5_only_trade_count") or 0)
    if session_count < MIN_SESSIONS:
        return "insufficient_data", notes + [f"sessions={session_count}"], {}
    if cap5_only_count < MIN_CAP5_ONLY_TRADES:
        return "insufficient_data", notes + [f"cap5_only_trades={cap5_only_count}"], {}

    cap3 = cap_eval.get("cap3_adopted") or {}
    cap5o = cap_eval.get("cap5_only") or {}
    ex3 = exit_eval.get("cap3_adopted") or {}
    ex5o = exit_eval.get("cap5_only") or {}

    cap3_pf = float(cap3.get("pf_proxy") or 0)
    cap5o_pf = float(cap5o.get("pf_proxy") or 0)
    cap5o_pnl = float(cap5o.get("total_pnl_proxy") or 0)
    cap_delta = float(cap_eval.get("cap5_only_pnl_delta_vs_cap3_increment") or 0)

    ex5_overlap = float(ex5o.get("overlap_replaced_rate_pct") or 0)
    ex5_fade = float(ex5o.get("fade_exit_rate_pct") or 0)
    ex5_pf = float(ex5o.get("pf") or 0) if ex5o.get("pf") is not None else 0.0
    ex3_pf = float(ex3.get("pf") or 0) if ex3.get("pf") is not None else 0.0

    notes.append(f"cap3_pf={cap3_pf} cap5_only_pf={cap5o_pf} cap5_only_pnl={cap5o_pnl}")
    notes.append(f"exit cap5_only overlap%={ex5_overlap} fade%={ex5_fade} exit_pf={ex5_pf}")

    cap_verdict = "cap3_preferred"
    if cap5o_pf and cap5o_pf >= 1.1 and cap5o_pnl > 0.5 and cap_delta > 0:
        cap_verdict = "cap5_promising"
    elif cap5o_pf and cap5o_pf >= cap3_pf * 0.98 and cap5o_pnl > 0:
        cap_verdict = "cap5_marginal"

    exit_verdict = "exit_ok"
    if ex5_overlap > 20 or ex5_fade + ex5_overlap > 35:
        exit_verdict = "exit_degraded"
    if ex5_pf and ex5_pf < 1.0:
        exit_verdict = "exit_degraded"
    if ex5_pf and ex3_pf and ex5_pf < ex3_pf * 0.85:
        exit_verdict = "exit_degraded"

    layers = {"cap_layer": cap_verdict, "exit_layer": exit_verdict}

    if exit_verdict == "exit_degraded":
        return "exit_fix_needed_before_judgement", notes + ["exit quality blocks cap judgement"], layers

    if cap_verdict == "cap5_promising":
        return "cap5_promising", notes + ["cap increment promising under current exit policy"], layers

    return "cap3_preferred", notes + ["cap5 incremental weak or exit-neutral; keep cap3"], layers


def analyze_phase158(
    session_dirs: Sequence[Path],
    *,
    pilot_config: Any,
    min_quality: float = PHASE53_MIN_QUALITY,
) -> dict[str, Any]:
    from small_paper.discord_notifier import observer_tracker_config_from_pilot

    cap3_rows_all: list[dict[str, Any]] = []
    cap5_only_rows_all: list[dict[str, Any]] = []
    cap3_exit_all: list[SimPosition] = []
    cap5_only_exit_all: list[SimPosition] = []
    per_session: list[dict[str, Any]] = []

    exit_cfg = observer_tracker_config_from_pilot(pilot_config)
    exit_cfg.structural_exit_policy = POLICY_COMBINED_STRUCTURAL_EXIT_V1
    profile = str(getattr(pilot_config, "profile", ""))

    for sdir in session_dirs:
        sdir = Path(sdir)
        events = _load_events(sdir)
        if not events:
            continue
        summary = _load_json(sdir / "small_paper_summary.json")
        session_id = str(sdir.relative_to(sdir.parent.parent)) if sdir.parent.parent else sdir.name
        candidates, _, _ = _filter_price_risk_candidates(events, min_quality=min_quality)
        if not candidates:
            continue
        price_index = _build_price_index(events)
        allowed = pilot_config.allowed_windows() if pilot_config else None
        session_end = _session_end_time(events)

        r3 = _simulate_cap_scenario(
            candidates,
            min_quality=min_quality,
            max_concurrent=3,
            profile=profile,
            price_index=price_index,
            allowed_windows=allowed,
        )
        r5 = _simulate_cap_scenario(
            candidates,
            min_quality=min_quality,
            max_concurrent=5,
            profile=profile,
            price_index=price_index,
            allowed_windows=allowed,
        )
        cap5_only = _newly_accepted_keys(r3.accepted_rows, r5.accepted_rows)

        for r in r3.accepted_rows:
            cap3_rows_all.append({**dict(r), "session_id": session_id, "bucket": "cap3_adopted"})
        for r in cap5_only:
            cap5_only_rows_all.append({**dict(r), "session_id": session_id, "bucket": "cap5_only"})

        closed3 = simulate_cap_exit_from_candidates(
            candidates,
            session_id=session_id,
            max_concurrent=3,
            profile=profile,
            exit_cfg=exit_cfg,
            session_end=session_end,
            min_quality=min_quality,
        )
        closed5 = simulate_cap_exit_from_candidates(
            candidates,
            session_id=session_id,
            max_concurrent=5,
            profile=profile,
            exit_cfg=exit_cfg,
            session_end=session_end,
            min_quality=min_quality,
        )
        keys5_only = {_trade_key({"symbol": r["symbol"], "entry_time": r["entry_time"]}) for r in cap5_only}
        cap3_exit_all.extend(closed3)
        cap5_only_exit_all.extend(
            p for p in closed5 if _trade_key({"symbol": p.symbol, "entry_time": p.entry_time}) in keys5_only
        )

        per_session.append(
            {
                "session_id": session_id,
                "cap3_accepted": len(r3.accepted_rows),
                "cap5_accepted": len(r5.accepted_rows),
                "cap5_only": len(cap5_only),
                "cap3_exit_closed": len(closed3),
                "cap5_only_exit_closed": sum(
                    1
                    for p in closed5
                    if _trade_key({"symbol": p.symbol, "entry_time": p.entry_time}) in keys5_only
                ),
            }
        )

    cap_eval = {
        "cap3_adopted": _cap_metrics(cap3_rows_all, label="cap3_adopted"),
        "cap5_only": _cap_metrics(cap5_only_rows_all, label="cap5_only"),
        "cap5_only_pnl_delta_vs_cap3_increment": round(
            float(_cap_metrics(cap5_only_rows_all, label="cap5_only").get("total_pnl_proxy") or 0),
            4,
        ),
        "cap5_only_trade_count": len(cap5_only_rows_all),
        "methodology_cap": (
            "ExposureGate counterfactual virtual_hold on candidates filtered by "
            "q>=0.70, daytrade_suitability, entry_price_risk_guard"
        ),
    }
    exit_eval = {
        "cap3_adopted": _exit_metrics_from_positions(cap3_exit_all, label="cap3_adopted"),
        "cap5_only": _exit_metrics_from_positions(cap5_only_exit_all, label="cap5_only"),
        "methodology_exit": (
            "structural combined_exit_v1 replay on candidate stream; "
            "cap5_only subset = entries accepted at cap5 but not cap3"
        ),
    }

    session_count = len(per_session)
    verdict, notes, layers = determine_verdict(
        cap_eval=cap_eval,
        exit_eval=exit_eval,
        session_count=session_count,
    )

    trade_rows = _position_rows(cap3_exit_all, bucket="cap3_adopted") + _position_rows(
        cap5_only_exit_all, bucket="cap5_only"
    )

    return {
        "verdict": verdict,
        "verdict_notes": notes,
        "evaluation_layers": layers,
        "session_count": session_count,
        "per_session": per_session,
        "cap_evaluation": cap_eval,
        "exit_evaluation": exit_eval,
        "cap3_adopted_cap_rows": cap3_rows_all,
        "cap5_only_cap_rows": cap5_only_rows_all,
        "exit_trade_rows": trade_rows,
        "constraints": {
            "review_only": True,
            "production_yaml_modified": False,
            "price_risk_universe": True,
            "entry_price_risk_guard": True,
            "intraday_refresh_context": True,
            "max_concurrent_production": 3,
        },
        "verdict_options": {
            "A": "cap5_promising",
            "B": "cap3_preferred",
            "C": "exit_fix_needed_before_judgement",
            "D": "insufficient_data",
        },
    }


def write_phase158_outputs(result: Mapping[str, Any], *, reports_dir: Path) -> dict[str, str]:
    reports_dir.mkdir(parents=True, exist_ok=True)
    json_path = reports_dir / "phase158_cap3_vs_cap5_review.json"
    summary_csv = reports_dir / "phase158_cap3_vs_cap5_summary.csv"
    cap3_csv = reports_dir / "phase158_cap3_adopted_trades.csv"
    cap5_csv = reports_dir / "phase158_cap5_only_trades.csv"

    design = {k: v for k, v in result.items() if not k.endswith("_rows")}
    json_path.write_text(json.dumps(design, ensure_ascii=False, indent=2), encoding="utf-8")

    summary_rows = [
        {**result["cap_evaluation"]["cap3_adopted"], "layer": "cap", "metric_set": "virtual_hold"},
        {**result["cap_evaluation"]["cap5_only"], "layer": "cap", "metric_set": "virtual_hold"},
        {**result["exit_evaluation"]["cap3_adopted"], "layer": "exit", "metric_set": "structural_replay"},
        {**result["exit_evaluation"]["cap5_only"], "layer": "exit", "metric_set": "structural_replay"},
    ]
    _write_csv(summary_csv, summary_rows)
    exit_rows = result.get("exit_trade_rows") or []
    _write_csv(cap3_csv, [r for r in exit_rows if r.get("bucket") == "cap3_adopted"])
    _write_csv(cap5_csv, [r for r in exit_rows if r.get("bucket") == "cap5_only"])

    return {
        "json": str(json_path),
        "summary_csv": str(summary_csv),
        "cap3_trades_csv": str(cap3_csv),
        "cap5_only_trades_csv": str(cap5_csv),
    }
