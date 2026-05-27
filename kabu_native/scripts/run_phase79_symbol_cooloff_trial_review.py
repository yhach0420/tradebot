#!/usr/bin/env python3
"""
Phase 79: Rolling symbol cooloff trial validation (read-only + optional push-replay).
"""

from __future__ import annotations

import csv
import importlib.util
import json
import statistics
import sys
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

ROOT = Path(__file__).resolve().parents[2]
SMALL_PAPER = ROOT / "kabu_native" / "results" / "small_paper"
OUTPUT_SESSION = SMALL_PAPER / "20260520" / "push_replay_231314"
NATIVE = ROOT / "kabu_native"
COOLOFF_CFG = NATIVE / "configs" / "small_paper_pilot_q070_cap3_mfe_fav_symbol_cooloff.yaml"
BASELINE_CFG = NATIVE / "configs" / "small_paper_pilot_q070_cap3_mfe_fav.yaml"

SESSION_PATHS = [
    "20260518/push_replay_220451",
    "20260519/live_full_session_081047",
    "20260520/push_replay_001932",
    "20260520/live_full_session_080745",
    "20260520/push_replay_231314",
]

PUSH_DIR_BY_SESSION = {
    "20260518/push_replay_220451": NATIVE / "data" / "push_jsonl" / "2026-05-18",
    "20260519/live_full_session_081047": NATIVE / "data" / "push_jsonl" / "2026-05-19",
    "20260520/push_replay_001932": NATIVE / "data" / "push_jsonl" / "2026-05-20",
    "20260520/live_full_session_080745": NATIVE / "data" / "push_jsonl" / "2026-05-20",
    "20260520/push_replay_231314": NATIVE / "data" / "push_jsonl" / "2026-05-20",
}

SYMBOL_CHECK = "5803.T"


def _bootstrap() -> None:
    for p in (NATIVE / "src", ROOT):
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def _load_phase71():
    path = Path(__file__).resolve().parent / "run_phase71_split_momentum_fade_review.py"
    name = "phase71_replay_engine_p79"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _profit_factor(pnls: Sequence[float]) -> Optional[float]:
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gl = abs(sum(losses))
    if gl <= 0:
        return None if not wins else float("inf")
    return sum(wins) / gl


def _summarize_trades(trades: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not trades:
        return {
            "structural_pf": None,
            "avg_pnl": None,
            "win_rate": None,
            "max_loss": None,
            "trade_count": 0,
        }
    pnls = [float(t["realized_pnl_pct"]) for t in trades]
    pf = _profit_factor(pnls)
    return {
        "structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "avg_pnl": round(statistics.mean(pnls), 4),
        "win_rate": round(sum(1 for p in pnls if p > 0) / len(pnls), 4),
        "max_loss": round(min(pnls), 4),
        "trade_count": len(pnls),
    }


def _replay_v1_trades(p71: Any, session_dir: Path) -> list[dict[str, Any]]:
    events_path = session_dir / "small_paper_events.jsonl"
    if not events_path.is_file():
        return []
    events = p71._load_events(events_path)
    session_end = p71._session_end(events)
    sym_states: dict[str, Any] = {}
    active: dict[str, Any] = {}
    completed: list[Any] = []

    def close_act(act: Any, *, close_time: str, close_price: float, reason: str) -> None:
        act.trade.close_time = close_time
        act.trade.close_price = close_price
        act.trade.close_reason = reason
        act.trade.realized_pnl_pct = p71._pnl_pct(act.trade.entry_price, close_price)
        act.trade.hold_duration_sec = round(max(0.0, p71._parse_ts(close_time) - act.entry_ts), 1)
        completed.append(act.trade)

    for ev in events:
        sym = str(ev.get("symbol") or "")
        if not sym:
            continue
        et = str(ev.get("event_type") or "")
        ent_raw = str(ev.get("entry_time") or "")
        ts = p71._parse_ts(ent_raw)
        price = float(ev.get("current_price") or 0)
        if price <= 0:
            continue
        st = sym_states.setdefault(sym, p71.SymState())

        if et == "accepted":
            if sym in active:
                old = active.pop(sym)
                close_act(old, close_time=ent_raw, close_price=price, reason="overlap_replaced_review")
            comps = p71._components(st, ts=ts, price=price, ev=ev)
            tr = p71.StructuralTrade(sym, ent_raw, price, float(ev.get("continuation_quality_score") or 0))
            active[sym] = p71.ActiveTrade(
                trade=tr,
                entry_ts=ts,
                rich_ticks=[
                    {
                        "price": price,
                        "pnl_pct": 0.0,
                        "quality": comps["quality"],
                        "momentum": comps["momentum"],
                        "favorable": comps["favorable"],
                        "pure_price_momentum": comps["pure_price_momentum"],
                        "vwap_strength": comps["vwap_strength"],
                        "mfe_proxy": comps["mfe_proxy"],
                    }
                ],
            )
        elif et == "candidate" and sym in active:
            act = active[sym]
            comps = p71._components(st, ts=ts, price=price, ev=ev)
            act.rich_ticks.append(
                {
                    "price": price,
                    "pnl_pct": p71._pnl_pct(act.trade.entry_price, price),
                    "quality": comps["quality"],
                    "momentum": comps["momentum"],
                    "favorable": comps["favorable"],
                    "pure_price_momentum": comps["pure_price_momentum"],
                    "vwap_strength": comps["vwap_strength"],
                    "mfe_proxy": comps["mfe_proxy"],
                }
            )
            sig = p71.simulate_combined_split(
                act.rich_ticks,
                act.trade.entry_price,
                momentum_mode="legacy",
                ratio=0.85,
                allow_session_end=False,
            )
            if sig:
                _, reason, _ = sig
                close_act(act, close_time=ent_raw, close_price=price, reason=reason)
                active.pop(sym, None)

    for act in list(active.values()):
        last_px = act.rich_ticks[-1]["price"] if act.rich_ticks else act.trade.entry_price
        close_act(act, close_time=session_end, close_price=float(last_px), reason="session_end")

    return [
        {
            "symbol": t.symbol,
            "entry_time": t.entry_time,
            "realized_pnl_pct": t.realized_pnl_pct,
            "hold_duration_sec": t.hold_duration_sec,
            "close_reason": t.close_reason,
        }
        for t in completed
    ]


def _evaluate_session(
    session_id: str,
    trades: Sequence[Mapping[str, Any]],
    events: Sequence[Mapping[str, Any]],
    excluded: set[str],
    *,
    policy_id: str,
    cooloff_state: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    trade_by_entry = {(t["symbol"], t["entry_time"]): t for t in trades}
    gate_accepts = 0
    rejected = 0
    missed_winners = 0
    avoided_losers = 0
    cases: list[dict[str, Any]] = []
    blocked_entries: set[tuple[str, str]] = set()

    for ev in events:
        if str(ev.get("event_type")) != "accepted":
            continue
        sym = str(ev.get("symbol") or "")
        ent = str(ev.get("entry_time") or "")
        gate_accepts += 1
        if sym not in excluded:
            continue
        rejected += 1
        blocked_entries.add((sym, ent))
        tr = trade_by_entry.get((sym, ent))
        pnl = float(tr["realized_pnl_pct"]) if tr else None
        prior = cooloff_state.prior_stats.get(sym) if cooloff_state else None
        if pnl is not None and pnl > 0:
            missed_winners += 1
            outcome = "missed_winner"
        elif pnl is not None:
            avoided_losers += 1
            outcome = "avoided_loser"
        else:
            outcome = "no_matching_trade"
        cases.append(
            {
                "session_id": session_id,
                "policy_id": policy_id,
                "symbol": sym,
                "entry_time": ent,
                "realized_pnl_pct_if_traded": pnl,
                "outcome": outcome,
                "prior_avg_pnl": round(prior.avg_pnl_pct, 6) if prior else None,
                "prior_trades": prior.trades if prior else 0,
                "symbol_cooloff_reason": "symbol_cooloff",
            }
        )

    kept = [t for t in trades if (t["symbol"], t["entry_time"]) not in blocked_entries]
    metrics = _summarize_trades(kept)
    kept_pnls = [float(t["realized_pnl_pct"]) for t in kept]
    row = {
        "session_id": session_id,
        "policy_id": policy_id,
        "symbol_cooloff_count": len(excluded),
        "excluded_symbols": "|".join(sorted(excluded)),
        "symbol_cooloff_source_sessions": (
            "|".join(cooloff_state.source_sessions) if cooloff_state else ""
        ),
        "gate_accept_events": gate_accepts,
        "accepted_count": gate_accepts - rejected,
        "rejected_by_symbol_cooloff": rejected,
        "missed_winners": missed_winners,
        "avoided_losers": avoided_losers,
        "total_pnl_pct": round(sum(kept_pnls), 4) if kept_pnls else 0.0,
        **metrics,
        "_kept_pnls": kept_pnls,
    }
    return row, cases


def _aggregate_oos(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    all_pnls: list[float] = []
    for r in rows:
        all_pnls.extend(r.get("_kept_pnls") or [])
    pf = _profit_factor(all_pnls) if all_pnls else None
    return {
        "oos_session_count": len(rows),
        "aggregate_structural_pf": round(pf, 4) if pf not in (None, float("inf")) else pf,
        "aggregate_trade_count": sum(int(r.get("trade_count") or 0) for r in rows),
        "aggregate_rejected_by_symbol_cooloff": sum(
            int(r.get("rejected_by_symbol_cooloff") or 0) for r in rows
        ),
        "aggregate_missed_winners": sum(int(r.get("missed_winners") or 0) for r in rows),
        "aggregate_avoided_losers": sum(int(r.get("avoided_losers") or 0) for r in rows),
    }


def _recommend(
    no_filter: Mapping[str, Any],
    cooloff: Mapping[str, Any],
    *,
    session_count: int,
    sym_5803: Mapping[str, Any],
) -> tuple[str, str]:
    oos_n = int(cooloff.get("oos_session_count") or 0)
    if session_count < 4 or oos_n < 2:
        return (
            "collect_more_sessions",
            f"{session_count} sessions, {oos_n} OOS-evaluable",
        )
    pf_a = float(no_filter.get("aggregate_structural_pf") or 0)
    pf_c = float(cooloff.get("aggregate_structural_pf") or 0)
    avoided = int(cooloff.get("aggregate_avoided_losers") or 0)
    missed = int(cooloff.get("aggregate_missed_winners") or 0)
    if pf_c > pf_a + 0.03 and avoided >= missed:
        return (
            "promote_symbol_cooloff_trial",
            f"OOS aggregate PF {pf_c} vs no_filter {pf_a}; "
            f"avoided_losers={avoided} missed_winners={missed}",
        )
    if pf_c <= pf_a:
        return "keep_current_universe", f"cooloff PF {pf_c} <= no_filter {pf_a}"
    return (
        "inconclusive",
        f"PF gain {pf_c} vs {pf_a} but missed_winners={missed} vs avoided={avoided}",
    )


def main() -> int:
    _bootstrap()
    from small_paper.config import load_pilot_config
    from small_paper.symbol_cooloff import (
        build_symbol_cooloff_state,
        prior_stats_rows,
        validate_prior_only_sources,
    )

    cooloff_pilot = load_pilot_config(COOLOFF_CFG)
    p71 = _load_phase71()

    sessions: list[dict[str, Any]] = []
    for rel in SESSION_PATHS:
        sdir = SMALL_PAPER / rel
        if not sdir.is_dir():
            continue
        trades = _replay_v1_trades(p71, sdir)
        events = p71._load_events(sdir / "small_paper_events.jsonl") if (sdir / "small_paper_events.jsonl").is_file() else []
        sessions.append(
            {
                "session_id": rel,
                "session_dir": sdir,
                "trades": trades,
                "events": events,
                "trade_count": len(trades),
            }
        )

    comparison_rows: list[dict[str, Any]] = []
    list_rows: list[dict[str, Any]] = []
    rejected_cases: list[dict[str, Any]] = []
    cooloff_oos_rows: list[dict[str, Any]] = []
    no_filter_oos_rows: list[dict[str, Any]] = []

    for i, sess in enumerate(sessions):
        sid = sess["session_id"]
        cooloff_state = build_symbol_cooloff_state(
            cooloff_pilot,
            repo_root=ROOT,
            run_session_key=sid,
        )
        prior_errs = (
            validate_prior_only_sources(cooloff_state, run_session_key=sid)
            if cooloff_state
            else []
        )
        excluded: set[str] = set()
        if cooloff_state:
            excluded = set(cooloff_state.cooloff_symbols)
            for ps in prior_stats_rows(cooloff_state.prior_stats):
                list_rows.append(
                    {
                        "session_id": sid,
                        "symbol": ps["symbol"],
                        "on_cooloff_list": ps["symbol"] in excluded,
                        "prior_trades": ps["trades"],
                        "prior_avg_pnl_pct": ps["avg_pnl_pct"],
                        "prior_total_pnl_pct": ps["total_pnl_pct"],
                        "prior_structural_pf": ps["structural_pf"],
                        "source_sessions": ps["sessions"],
                    }
                )

        nf_row, _ = _evaluate_session(
            sid,
            sess["trades"],
            sess["events"],
            set(),
            policy_id="no_filter",
            cooloff_state=None,
        )
        co_row, cases = _evaluate_session(
            sid,
            sess["trades"],
            sess["events"],
            excluded,
            policy_id="symbol_cooloff_trial",
            cooloff_state=cooloff_state,
        )
        rejected_cases.extend(cases)

        for base_row, policy in ((nf_row, "no_filter"), (co_row, "symbol_cooloff_trial")):
            out = {k: v for k, v in base_row.items() if k != "_kept_pnls"}
            out["policy_id"] = policy
            out["oos_eligible"] = i >= 1
            comparison_rows.append(out)

        if i >= 1:
            no_filter_oos_rows.append(nf_row)
            cooloff_oos_rows.append(co_row)

        if sid in (
            "20260520/live_full_session_080745",
            "20260520/push_replay_231314",
        ):
            st5803 = cooloff_state.prior_stats.get(SYMBOL_CHECK) if cooloff_state else None
            list_rows.append(
                {
                    "session_id": sid,
                    "symbol": SYMBOL_CHECK,
                    "on_cooloff_list": SYMBOL_CHECK in excluded,
                    "prior_trades": st5803.trades if st5803 else 0,
                    "prior_avg_pnl_pct": round(st5803.avg_pnl_pct, 6) if st5803 else None,
                    "prior_total_pnl_pct": round(st5803.total_pnl_pct, 4) if st5803 else None,
                    "prior_structural_pf": st5803.profit_factor if st5803 else None,
                    "source_sessions": "|".join(st5803.session_ids) if st5803 else "",
                    "note": "5803_check_session",
                }
            )

    agg_nf = _aggregate_oos(no_filter_oos_rows)
    agg_co = _aggregate_oos(cooloff_oos_rows)
    agg_nf["policy_id"] = "no_filter"
    agg_co["policy_id"] = "symbol_cooloff_trial"

    sym_5803_080745 = next(
        (r for r in list_rows if r.get("session_id") == "20260520/live_full_session_080745" and r.get("symbol") == SYMBOL_CHECK),
        {},
    )
    sym_5803_231314 = next(
        (r for r in list_rows if r.get("session_id") == "20260520/push_replay_231314" and r.get("symbol") == SYMBOL_CHECK),
        {},
    )

    decision, rationale = _recommend(
        agg_nf,
        agg_co,
        session_count=len(sessions),
        sym_5803={"080745": sym_5803_080745, "231314": sym_5803_231314},
    )

    review = {
        "phase": 79,
        "generated_at": __import__("datetime").datetime.now(
            __import__("zoneinfo").ZoneInfo("Asia/Tokyo")
        ).isoformat(timespec="seconds"),
        "cooloff_config": str(COOLOFF_CFG.relative_to(ROOT)),
        "baseline_config": str(BASELINE_CFG.relative_to(ROOT)),
        "sessions_evaluated": [s["session_id"] for s in sessions],
        "aggregate_oos": {"no_filter": agg_nf, "symbol_cooloff_trial": agg_co},
        "phase78_reference_oos_pf": {
            "no_filter": 1.168,
            "rule_D_prior_avg_pnl": 1.616,
        },
        "symbol_5803_checks": {
            "live_full_session_080745": sym_5803_080745,
            "push_replay_231314": sym_5803_231314,
        },
        "decision": decision,
        "rationale": rationale,
        "note_5803": (
            "Rule D excludes when prior avg_pnl < 0 with trades>=5. "
            "5803 may remain tradable on 231314 if earlier sessions were net positive."
        ),
    }

    OUTPUT_SESSION.mkdir(parents=True, exist_ok=True)
    (OUTPUT_SESSION / "phase79_symbol_cooloff_trial_review.json").write_text(
        json.dumps(review, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    comp_fields = [
        "session_id",
        "policy_id",
        "oos_eligible",
        "structural_pf",
        "trade_count",
        "avg_pnl",
        "win_rate",
        "max_loss",
        "total_pnl_pct",
        "rejected_by_symbol_cooloff",
        "avoided_losers",
        "missed_winners",
        "symbol_cooloff_count",
        "excluded_symbols",
    ]
    with (OUTPUT_SESSION / "phase79_symbol_cooloff_policy_comparison.csv").open(
        "w", encoding="utf-8", newline=""
    ) as f:
        w = csv.DictWriter(f, fieldnames=comp_fields, extrasaction="ignore")
        w.writeheader()
        for r in comparison_rows:
            w.writerow(r)
        w.writerow({**agg_nf, "session_id": "OOS_AGGREGATE", "oos_eligible": True})
        w.writerow({**agg_co, "session_id": "OOS_AGGREGATE", "oos_eligible": True})

    list_fields = [
        "session_id",
        "symbol",
        "on_cooloff_list",
        "prior_trades",
        "prior_avg_pnl_pct",
        "prior_total_pnl_pct",
        "prior_structural_pf",
        "source_sessions",
        "note",
    ]
    with (OUTPUT_SESSION / "phase79_symbol_cooloff_lists.csv").open(
        "w", encoding="utf-8", newline=""
    ) as f:
        w = csv.DictWriter(f, fieldnames=list_fields, extrasaction="ignore")
        w.writeheader()
        for r in list_rows:
            w.writerow(r)

    if rejected_cases:
        with (OUTPUT_SESSION / "phase79_symbol_cooloff_rejected_cases.csv").open(
            "w", encoding="utf-8", newline=""
        ) as f:
            w = csv.DictWriter(f, fieldnames=list(rejected_cases[0].keys()))
            w.writeheader()
            for case in rejected_cases:
                w.writerow(case)

    print(json.dumps(review, ensure_ascii=False, indent=2))
    print(f"Wrote phase79 outputs under {OUTPUT_SESSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
