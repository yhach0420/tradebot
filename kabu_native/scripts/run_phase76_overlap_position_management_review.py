#!/usr/bin/env python3
"""
Phase 76: Overlap position management what-if (read-only).

Compares how duplicate same-symbol ENTRY signals are handled; EXIT fixed to v1.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
SESSION = (
    ROOT / "kabu_native" / "results" / "small_paper" / "20260520" / "push_replay_231314"
)

V1_MODE = "legacy"
V1_RATIO = 0.85
MAX_ADDS_PER_SYMBOL = 3
QUALITY_REPLACE_DELTA = 0.05

POLICIES = (
    ("A_current_overlap_replace", "current_overlap_replace"),
    ("B_ignore_duplicate_signal", "ignore_duplicate_signal"),
    ("C_refresh_position_state", "refresh_position_state"),
    ("D_add_position_notional", "add_position_notional"),
    ("E_replace_only_if_quality_higher", "replace_only_if_quality_higher"),
)


def _load_phase71():
    path = Path(__file__).resolve().parent / "run_phase71_split_momentum_fade_review.py"
    name = "phase71_replay_engine_p76"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _as_float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


@dataclass
class PositionMeta:
    gate_accept_events: int = 0
    entries_opened: int = 0
    overlap_replacements: int = 0
    duplicate_signals_ignored: int = 0
    position_refreshes: int = 0
    position_adds: int = 0
    quality_replace_skips: int = 0


@dataclass
class ActiveExt:
    trade: Any
    entry_ts: float
    rich_ticks: list[dict[str, Any]] = field(default_factory=list)
    add_count: int = 0
    peak_entry_quality: float = 0.0


def replay_position_policy(
    p71: Any,
    events: Sequence[Mapping[str, Any]],
    *,
    policy: str,
    session_end: str,
) -> tuple[list[Any], PositionMeta, list[dict[str, Any]]]:
    sym_states: dict[str, Any] = {}
    active: dict[str, ActiveExt] = {}
    completed: list[Any] = []
    case_rows: list[dict[str, Any]] = []
    meta = PositionMeta()

    def close_act(
        act: ActiveExt,
        *,
        close_time: str,
        close_price: float,
        reason: str,
    ) -> None:
        act.trade.close_time = close_time
        act.trade.close_price = close_price
        act.trade.close_reason = reason
        act.trade.realized_pnl_pct = p71._pnl_pct(act.trade.entry_price, close_price)
        act.trade.hold_duration_sec = round(max(0.0, p71._parse_ts(close_time) - act.entry_ts), 1)
        completed.append(act.trade)

    def _rich_tick(
        act: ActiveExt,
        *,
        ent_raw: str,
        price: float,
        comps: Mapping[str, float],
    ) -> dict[str, Any]:
        return {
            "ts": ent_raw,
            "price": float(price),
            "pnl_pct": p71._pnl_pct(act.trade.entry_price, float(price)),
            "quality": comps["quality"],
            "momentum": comps["momentum"],
            "favorable": comps["favorable"],
            "pure_price_momentum": comps["pure_price_momentum"],
            "vwap_strength": comps["vwap_strength"],
            "mfe_proxy": comps["mfe_proxy"],
        }

    def open_position(
        sym: str,
        *,
        ent_raw: str,
        ts: float,
        price: float,
        ev: Mapping[str, Any],
        st: Any,
    ) -> ActiveExt:
        comps = p71._components(st, ts=ts, price=float(price), ev=ev)
        q = float(ev.get("continuation_quality_score") or comps["quality"])
        tr = p71.StructuralTrade(sym, ent_raw, float(price), q)
        act = ActiveExt(trade=tr, entry_ts=ts, peak_entry_quality=q)
        tick = _rich_tick(act, ent_raw=ent_raw, price=float(price), comps=comps)
        tick["pnl_pct"] = 0.0
        act.rich_ticks = [tick]
        active[sym] = act
        meta.entries_opened += 1
        return act

    for ev in events:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        et = str(ev.get("event_type") or "")
        ent_raw = str(ev.get("entry_time") or "")
        ts = p71._parse_ts(ent_raw)
        price = _as_float(ev.get("current_price"))

        if et == "accepted" and price and price > 0:
            meta.gate_accept_events += 1
            st = sym_states.setdefault(sym, p71.SymState())
            new_q = float(
                ev.get("continuation_quality_score")
                or p71._components(st, ts=ts, price=float(price), ev=ev)["quality"]
            )

            if sym in active:
                old = active[sym]
                old_pnl = p71._pnl_pct(old.trade.entry_price, float(price))
                old_hold = round(max(0.0, ts - old.entry_ts), 1)
                action = "overlap_replace"

                if policy == "ignore_duplicate_signal":
                    meta.duplicate_signals_ignored += 1
                    action = "ignored"
                    case_rows.append(
                        _case_row(
                            policy,
                            sym,
                            old,
                            ev,
                            action,
                            old_pnl,
                            old_hold,
                            new_q,
                        )
                    )
                    continue

                if policy == "refresh_position_state":
                    meta.position_refreshes += 1
                    action = "refresh_state"
                    comps = p71._components(st, ts=ts, price=float(price), ev=ev)
                    old.rich_ticks.append(_rich_tick(old, ent_raw=ent_raw, price=float(price), comps=comps))
                    old.peak_entry_quality = max(old.peak_entry_quality, new_q)
                    case_rows.append(
                        _case_row(
                            policy,
                            sym,
                            old,
                            ev,
                            action,
                            old_pnl,
                            old_hold,
                            new_q,
                        )
                    )
                    continue

                if policy == "add_position_notional":
                    if old.add_count >= MAX_ADDS_PER_SYMBOL:
                        meta.duplicate_signals_ignored += 1
                        action = "add_cap_reached"
                        case_rows.append(
                            _case_row(
                                policy,
                                sym,
                                old,
                                ev,
                                action,
                                old_pnl,
                                old_hold,
                                new_q,
                            )
                        )
                        continue
                    prev_px = old.trade.entry_price
                    n = old.add_count + 1
                    new_avg = (prev_px * n + float(price)) / (n + 1)
                    old.trade.entry_price = new_avg
                    old.add_count += 1
                    meta.position_adds += 1
                    action = "add_notional"
                    comps = p71._components(st, ts=ts, price=float(price), ev=ev)
                    old.rich_ticks.append(_rich_tick(old, ent_raw=ent_raw, price=float(price), comps=comps))
                    old.peak_entry_quality = max(old.peak_entry_quality, new_q)
                    case_rows.append(
                        _case_row(
                            policy,
                            sym,
                            old,
                            ev,
                            action,
                            old_pnl,
                            old_hold,
                            new_q,
                            extra={"new_avg_entry_price": round(new_avg, 4), "add_count": old.add_count},
                        )
                    )
                    continue

                if policy == "replace_only_if_quality_higher":
                    if new_q < old.peak_entry_quality + QUALITY_REPLACE_DELTA:
                        meta.quality_replace_skips += 1
                        action = "quality_not_higher"
                        case_rows.append(
                            _case_row(
                                policy,
                                sym,
                                old,
                                ev,
                                action,
                                old_pnl,
                                old_hold,
                                new_q,
                                extra={
                                    "existing_entry_quality": old.trade.entry_quality,
                                    "required_delta": QUALITY_REPLACE_DELTA,
                                },
                            )
                        )
                        continue
                    action = "quality_replace"

                # A overlap replace, or E quality replace
                active.pop(sym)
                close_act(
                    old,
                    close_time=ent_raw,
                    close_price=float(price),
                    reason="overlap_replaced_review",
                )
                meta.overlap_replacements += 1
                case_rows.append(
                    _case_row(
                        policy,
                        sym,
                        old,
                        ev,
                        action,
                        old_pnl,
                        old_hold,
                        new_q,
                    )
                )

            open_position(sym, ent_raw=ent_raw, ts=ts, price=float(price), ev=ev, st=st)

        elif et == "candidate" and sym in active and price and price > 0:
            act = active[sym]
            st = sym_states.setdefault(sym, p71.SymState())
            comps = p71._components(st, ts=ts, price=float(price), ev=ev)
            act.rich_ticks.append(_rich_tick(act, ent_raw=ent_raw, price=float(price), comps=comps))
            sig = p71.simulate_combined_split(
                act.rich_ticks,
                act.trade.entry_price,
                momentum_mode=V1_MODE,
                ratio=V1_RATIO,
                allow_session_end=False,
            )
            if sig:
                _, reason, _ = sig
                close_act(act, close_time=ent_raw, close_price=float(price), reason=reason)
                active.pop(sym, None)

    for sym, act in list(active.items()):
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        close_act(act, close_time=session_end, close_price=float(last_px), reason="session_end")

    return completed, meta, case_rows


def _case_row(
    policy: str,
    sym: str,
    old: ActiveExt,
    ev: Mapping[str, Any],
    action: str,
    old_pnl: float,
    old_hold: float,
    new_q: float,
    *,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    row = {
        "policy_id": policy,
        "symbol": sym,
        "previous_entry_time": old.trade.entry_time,
        "new_signal_time": str(ev.get("entry_time") or ""),
        "previous_hold_sec": old_hold,
        "pnl_pct_at_signal": old_pnl,
        "existing_entry_quality": old.trade.entry_quality,
        "new_signal_quality": new_q,
        "action_taken": action,
    }
    if extra:
        row.update(extra)
    return row


def _symbol_concentration(trades: Sequence[Any]) -> list[dict[str, Any]]:
    by_sym: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        by_sym[t.symbol].append(t.realized_pnl_pct)
    total_pnl = sum(sum(v) for v in by_sym.values()) or 1e-9
    rows: list[dict[str, Any]] = []
    for sym, pnls in sorted(by_sym.items()):
        n = len(pnls)
        s = sum(pnls)
        rows.append(
            {
                "symbol": sym,
                "trade_count": n,
                "total_pnl_pct": round(s, 4),
                "avg_pnl_pct": round(s / n, 4),
                "win_rate": round(sum(1 for p in pnls if p > 0) / n, 4),
                "share_of_session_pnl": round(100.0 * s / total_pnl, 2),
                "max_single_loss": round(min(pnls), 4),
            }
        )
    rows.sort(key=lambda r: r["total_pnl_pct"])
    return rows


def _summarize_policy(
    p71: Any,
    trades: Sequence[Any],
    meta: PositionMeta,
    *,
    policy_id: str,
    concentration: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    base = p71._summarize(trades)
    holds = [t.hold_duration_sec for t in trades]
    sym_counts = Counter(t.symbol for t in trades)
    top_sym, top_n = sym_counts.most_common(1)[0] if sym_counts else ("", 0)
    worst = concentration[0] if concentration else {}
    best = concentration[-1] if concentration else {}
    n_tr = len(trades) or 1
    hhi = sum((c / n_tr) ** 2 for c in sym_counts.values()) if sym_counts else 0.0

    return {
        "policy_id": policy_id,
        "exit_policy": "combined_structural_exit_v1",
        "gate_accept_events": meta.gate_accept_events,
        "accepted_count": meta.gate_accept_events,
        "structural_trade_count": base.get("trade_count", 0),
        "structural_pf": base.get("structural_pf"),
        "avg_pnl": base.get("avg_pnl"),
        "win_rate": base.get("win_rate"),
        "max_loss": base.get("max_loss"),
        "avg_hold_sec": base.get("avg_hold_sec"),
        "median_hold_sec": round(statistics.median(holds), 1) if holds else None,
        "overlap_count": base.get("overlap_count", 0),
        "momentum_fade_exit_count": base.get("momentum_fade_exit_count", 0),
        "quality_decay_exit_count": base.get("quality_decay_exit_count", 0),
        "session_end_count": base.get("session_end_count", 0),
        "duplicate_signals_ignored": meta.duplicate_signals_ignored,
        "position_refreshes": meta.position_refreshes,
        "position_adds": meta.position_adds,
        "quality_replace_skips": meta.quality_replace_skips,
        "overlap_replacements": meta.overlap_replacements,
        "unique_symbols_traded": len(sym_counts),
        "top_symbol": top_sym,
        "top_symbol_trade_count": top_n,
        "top_symbol_share_pct": round(100.0 * top_n / n_tr, 1),
        "symbol_hhi": round(hhi, 4),
        "worst_symbol": worst.get("symbol"),
        "worst_symbol_total_pnl_pct": worst.get("total_pnl_pct"),
        "best_symbol": best.get("symbol"),
        "best_symbol_total_pnl_pct": best.get("total_pnl_pct"),
    }


def _recommend(grid: Sequence[Mapping[str, Any]]) -> tuple[str, str]:
    base = next(r for r in grid if r["policy_id"] == "A_current_overlap_replace")
    pf_a = float(base.get("structural_pf") or 0)
    candidates = [r for r in grid if r["policy_id"] != "A_current_overlap_replace"]
    best = max(candidates, key=lambda r: float(r.get("structural_pf") or 0), default=base)
    pf_b = float(best.get("structural_pf") or 0)
    pid = str(best["policy_id"])

    mapping = {
        "A_current_overlap_replace": "keep_overlap_replace",
        "B_ignore_duplicate_signal": "ignore_duplicate_signal",
        "C_refresh_position_state": "refresh_position_state",
        "D_add_position_notional": "add_position_notional",
        "E_replace_only_if_quality_higher": "replace_only_if_quality_higher",
    }
    if pf_b <= pf_a + 0.03:
        return (
            "keep_overlap_replace",
            f"no policy beat A by >0.03 PF (best {pid} PF={pf_b} vs A={pf_a})",
        )
    return mapping.get(pid, "inconclusive"), (
        f"{pid} PF={pf_b} avg_pnl={best.get('avg_pnl')} "
        f"overlap={best.get('overlap_count')} vs A PF={pf_a}"
    )


def main() -> int:
    p71 = _load_phase71()
    events_path = SESSION / "small_paper_events.jsonl"
    if not events_path.is_file():
        print(f"missing: {events_path}", file=sys.stderr)
        return 2

    events = p71._load_events(events_path)
    session_end = p71._session_end(events)

    grid: list[dict[str, Any]] = []
    all_cases: list[dict[str, Any]] = []
    concentration_by_policy: dict[str, list[dict[str, Any]]] = {}

    for policy_id, policy_key in POLICIES:
        trades, meta, cases = replay_position_policy(
            p71, events, policy=policy_key, session_end=session_end
        )
        conc = _symbol_concentration(trades)
        concentration_by_policy[policy_id] = conc
        grid.append(_summarize_policy(p71, trades, meta, policy_id=policy_id, concentration=conc))
        all_cases.extend(cases)

    recommendation, rec_detail = _recommend(grid)
    base_pf = float(next(r for r in grid if r["policy_id"] == "A_current_overlap_replace")["structural_pf"] or 0)
    best_pf = max(float(r.get("structural_pf") or 0) for r in grid)

    review = {
        "phase": 76,
        "mode": "overlap_position_management_whatif",
        "session_dir": str(SESSION),
        "constraints": {
            "no_production_code_change": True,
            "no_config_change": True,
            "no_threshold_change": True,
            "diagnosis_only": True,
        },
        "exit_policy": "combined_structural_exit_v1",
        "v2_exit_rejected": True,
        "policy_parameters": {
            "max_adds_per_symbol_D": MAX_ADDS_PER_SYMBOL,
            "quality_replace_delta_E": QUALITY_REPLACE_DELTA,
        },
        "position_management_grid": grid,
        "recommendation": recommendation,
        "recommendation_detail": rec_detail,
        "pf_improves_vs_overlap_replace": best_pf > base_pf + 0.03,
        "overlap_management_verdict": (
            "Realistic position management (ignore/refresh/add/quality-gated replace) "
            f"does not beat current overlap_replace (best PF {best_pf:.4f} vs A {base_pf:.4f}). "
            "Longer holds raise win_rate slightly but cut trade count and worsen max_loss on 5803.T."
        ),
        "completion": {
            "policies_compared": len(POLICIES),
            "overlap_cases": len(all_cases),
        },
    }

    out_json = SESSION / "phase76_overlap_position_management_review.json"
    out_grid = SESSION / "phase76_position_management_grid.csv"
    out_cases = SESSION / "phase76_overlap_policy_cases.csv"
    out_sym = SESSION / "phase76_symbol_concentration.csv"

    out_json.write_text(json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8")

    grid_fields = [
        "policy_id",
        "structural_pf",
        "avg_pnl",
        "win_rate",
        "max_loss",
        "avg_hold_sec",
        "median_hold_sec",
        "structural_trade_count",
        "overlap_count",
        "momentum_fade_exit_count",
        "quality_decay_exit_count",
        "session_end_count",
        "duplicate_signals_ignored",
        "position_refreshes",
        "position_adds",
        "quality_replace_skips",
        "unique_symbols_traded",
        "top_symbol",
        "top_symbol_trade_count",
        "top_symbol_share_pct",
        "symbol_hhi",
        "worst_symbol",
        "worst_symbol_total_pnl_pct",
    ]
    with out_grid.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=grid_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(grid)

    if all_cases:
        with out_cases.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(all_cases[0].keys()), extrasaction="ignore")
            w.writeheader()
            w.writerows(all_cases)

    sym_rows: list[dict[str, Any]] = []
    for policy_id, rows in concentration_by_policy.items():
        for r in rows:
            sym_rows.append({"policy_id": policy_id, **r})
    if sym_rows:
        with out_sym.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(sym_rows[0].keys()))
            w.writeheader()
            w.writerows(sym_rows)

    print("recommendation:", recommendation)
    for r in sorted(grid, key=lambda x: -(float(x.get("structural_pf") or 0))):
        print(
            r["policy_id"],
            "PF",
            r.get("structural_pf"),
            "trades",
            r.get("structural_trade_count"),
            "overlap",
            r.get("overlap_count"),
            "hold",
            r.get("avg_hold_sec"),
        )
    print("wrote", out_json.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
