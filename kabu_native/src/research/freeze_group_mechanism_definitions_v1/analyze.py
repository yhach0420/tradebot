"""Freeze BG_CONT_VWAP unchanged. Profit-source RCA only. No promotion. No Frozen Validation."""
from __future__ import annotations

import gc
from typing import Any

from research.freeze_group_mechanism_definitions_v1 import (
    CASE_BIND,
    CASE_ENTRY,
    CASE_EXIT,
    CASE_MIXED,
    CASE_SYMBOL,
    CASE_TAIL,
    EXPECTED_PARENT_TRADE_N,
    EXPECTED_PARENT_X0,
    EXPECTED_PARENT_X1,
    FROZEN_MECHANISM_ID,
    ISOLATE_SYMBOL,
    NEXT_BIND,
    NEXT_EXIT,
    NEXT_GROUP,
    NEXT_MIXED,
    NEXT_NO_EXIT,
    NEXT_TAIL,
    PARENT_VERDICT,
    RCA_ID,
)
from research.freeze_group_mechanism_definitions_v1.bind import bind_prior
from research.freeze_group_mechanism_definitions_v1.freeze import CLOSED_PAIRS, frozen_spec, mechanism_hashes
from research.freeze_group_mechanism_definitions_v1.ledger import replay_ledger
from research.freeze_group_mechanism_definitions_v1.rca import (
    annotate_trade,
    block_evolution,
    cost_headroom,
    exit_analysis,
    giveback_summary,
    group_membership_table,
    path_class_table,
    root_cause,
    subset_econ,
    symbol_contribution,
    winner_concentration,
)
from research.freeze_group_mechanism_definitions_v1.spec import source_sha256
from research.freeze_group_mechanism_definitions_v1.walk import walk_vwap_reclaim
from research.behavior_group_sequence_mechanism_v1.prequential import maps_for_blocks
from research.identify_minimum_missing_external_causal_information_v1.live import inspect_live_now


def _sector_of(bind: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        out[str(sym)] = str(row.get("tse33_name") or row.get("sector33_name") or "")
    return out


def decide(*, bind_ok: bool, label: str) -> dict[str, Any]:
    if not bind_ok:
        return {"CASE": "BIND", "VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "INTERPRETATION": "Parent freeze/split/block bind failed."}
    if label == "A_ENTRY_INSUFFICIENT":
        return {
            "CASE": "ENTRY",
            "VERDICT": CASE_ENTRY,
            "NEXT": NEXT_NO_EXIT,
            "INTERPRETATION": "Most losers never develop meaningful MFE. EXIT research is not the next lever. Do not promote. Do not open Frozen Validation.",
        }
    if label == "B_EXIT_GIVEBACK":
        return {
            "CASE": "EXIT",
            "VERDICT": CASE_EXIT,
            "NEXT": NEXT_EXIT,
            "INTERPRETATION": "Trades often develop MFE then VWAP-loss gives it back. A small number of thesis-aligned EXIT hypotheses may be designed next. Do not bolt V27 automatically. Do not promote.",
        }
    if label == "C_WINNER_TAIL_DEPENDENCE":
        return {
            "CASE": "TAIL",
            "VERDICT": CASE_TAIL,
            "NEXT": NEXT_TAIL,
            "INTERPRETATION": "Positive mean is mainly a small number of extreme winners. Assess tail stability without deleting losers post hoc. Do not promote.",
        }
    if label == "D_SYMBOL_CONCENTRATION":
        return {
            "CASE": "SYMBOL",
            "VERDICT": CASE_SYMBOL,
            "NEXT": NEXT_GROUP,
            "INTERPRETATION": "Gross edge is concentrated in one symbol. Do not exclude it. Assess whether the mechanism is genuinely group-level. Do not promote.",
        }
    return {
        "CASE": "MIXED",
        "VERDICT": CASE_MIXED,
        "NEXT": NEXT_MIXED,
        "INTERPRETATION": "Multiple deficiencies. Prioritize the largest causal deficiency only. Do not invent subgroups, symbol filters, or new ENTRY searches. Do not promote.",
    }


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    print(f"BIND ok={bind.get('ok')} parent={bind.get('parent_verdict')}", flush=True)
    spec = frozen_spec()
    hashes = mechanism_hashes(source_sha=source_sha256())
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = set(str(d) for d in list(split.get("discovery_dates") or []))
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    date_to_block = dict(blocks.get("date_to_block") or {})
    symbols = list(bind.get("symbols") or [])
    sector_of = _sector_of(bind)
    walked = {"ok": False, "events": [], "daily": [], "event_n": 0}
    maps = {"rows": [], "by_eval_block": {}, "definitions": []}
    econ: dict[str, Any] = {"trade_n": 0, "trades": []}
    rca: dict[str, Any] = {}
    if bind.get("ok"):
        walked = walk_vwap_reclaim(
            symbols=symbols,
            sector_of=sector_of,
            allowed=disc,
            forbidden=conf | val,
            date_to_block=date_to_block,
        )
        print(f"VWAP_EVENTS n={walked.get('event_n')}", flush=True)
        maps = maps_for_blocks(daily=list(walked.get("daily") or []), blocks=list(blocks.get("blocks") or []))
        by_eval = dict(maps.get("by_eval_block") or {})
        events = list(walked.get("events") or [])
        for e in events:
            blk = str(e.get("block") or "")
            e["preq_group"] = (by_eval.get(blk) or {}).get(str(e.get("symbol")))
            e["full_discovery_group_used"] = False
        econ = replay_ledger(events, spec)
        print(f"LEDGER n={econ.get('trade_n')} x0={econ.get('mean_x0_bps')}", flush=True)
        trades = [annotate_trade(t) for t in list(econ.get("trades") or [])]
        for t in trades:
            t.pop("fwd_bars", None)
        econ["trades"] = trades
        events.clear()
        walked["events"] = []
        gc.collect()
        sym_rows = symbol_contribution(trades)
        with_all = subset_econ(trades, label="ALL_486")
        no8136 = subset_econ([t for t in trades if str(t["symbol"]) != ISOLATE_SYMBOL], label="EXCLUDE_8136_DIAGNOSTIC")
        only8136 = subset_econ([t for t in trades if str(t["symbol"]) == ISOLATE_SYMBOL], label="ONLY_8136_DIAGNOSTIC")
        win = winner_concentration(trades)
        gb = giveback_summary(trades)
        ex = exit_analysis(trades)
        cost = cost_headroom(trades)
        traded_syms = sorted({str(t["symbol"]) for t in trades})
        memb = group_membership_table(traded_syms, by_eval)
        memb_map = {r["symbol"]: {"D2": r["D2"], "D3": r["D3"], "D4": r["D4"]} for r in memb}
        blocks_evo = block_evolution(trades, {s: memb_map.get(s) or {} for s in traded_syms})
        paths = path_class_table(trades)
        cause = root_cause(trades, win=win, sym_rows=sym_rows, with8136=with_all, without8136=no8136)
        replay_match = bool(
            int(econ.get("trade_n") or 0) == EXPECTED_PARENT_TRADE_N
            and econ.get("mean_x0_bps") is not None
            and abs(float(econ["mean_x0_bps"]) - EXPECTED_PARENT_X0) < 1e-6
        )
        rca = {
            "symbol_contribution": sym_rows,
            "with_all": with_all,
            "exclude_8136": no8136,
            "only_8136": only8136,
            "winner_concentration": win,
            "giveback": gb,
            "exits": ex,
            "cost_headroom": cost,
            "group_membership": memb,
            "block_evolution": blocks_evo,
            "entry_path_classes": paths,
            "root_cause": cause,
            "positive_gross_symbols": [r["symbol"] for r in sym_rows if (r.get("total_gross_contribution") or 0) > 0],
            "replay_matches_parent_486": replay_match,
            "parent_x0": EXPECTED_PARENT_X0,
            "parent_x1": EXPECTED_PARENT_X1,
        }
        walked["daily"] = []
        gc.collect()
    live = inspect_live_now()
    cause = dict((rca.get("root_cause") or {}))
    decision = decide(bind_ok=bool(bind.get("ok")), label=str(cause.get("label") or ""))
    return {
        "parent_verdict_accepted": PARENT_VERDICT,
        "rca_id": RCA_ID,
        "frozen_mechanism_id": FROZEN_MECHANISM_ID,
        "bind": {k: v for k, v in bind.items() if k not in {"by_symbol", "split"}},
        "split_sha256": (bind.get("split") or {}).get("split_sha256"),
        "block_sha256": (bind.get("blocks") or {}).get("block_sha256"),
        "frozen_spec": spec,
        "mechanism_hashes": hashes,
        "closed_pairs": list(CLOSED_PAIRS),
        "walk_meta": {k: walked.get(k) for k in ("ok", "day_n", "event_n", "by_sequence", "rule_changed")},
        "economics": {k: econ.get(k) for k in econ if k != "trades"},
        "ledger": list(econ.get("trades") or []),
        "rca": rca,
        "hm1_reference": bind.get("hm1_reference"),
        "prequential": {
            "d1_characterization_only": True,
            "full_discovery_group_leakage": False,
            "definitions": maps.get("definitions"),
        },
        "live_20260914": live,
        "kabu_50_applied": False,
        "old_confirmation_used_to_design": False,
        "frozen_validation_opened": False,
        "hm1_tuned": False,
        "new_paid_data": False,
        "five_minute_grid": False,
        "strategy_parameter_changed": False,
        "promoted": False,
        "no_8136_strategy": False,
        "v27_bolted": False,
        "purchase": False,
        "decision": decision,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    rca = dict(report.get("rca") or {})
    cause = dict(rca.get("root_cause") or {})
    hashes = dict(report.get("mechanism_hashes") or {})
    econ = dict(report.get("economics") or {})
    win = dict(rca.get("winner_concentration") or {})
    gb = dict(rca.get("giveback") or {})
    ex = dict(rca.get("exits") or {})
    no8136 = dict(rca.get("exclude_8136") or {})
    evo = list(rca.get("block_evolution") or [])
    d4 = next((r for r in evo if r.get("block") == "D4"), {})
    d2 = next((r for r in evo if r.get("block") == "D2"), {})
    hm1 = dict(report.get("hm1_reference") or {})
    path = list(rca.get("entry_path_classes") or [])
    stall_n = next((p.get("n") for p in path if p.get("entry_path_class") == "STALL_NO_PROGRESS"), 0)
    composition = None
    if d2 and d4:
        share2 = d2.get("share_8136_trades")
        share4 = d4.get("share_8136_trades")
        win2 = d2.get("mean_winner_bps")
        win4 = d4.get("mean_winner_bps")
        if share4 is not None and share2 is not None and win4 is not None and win2 is not None:
            if abs(float(share4) - float(share2)) > 0.10 or abs(float(win4) - float(win2)) > 10:
                composition = "composition_and_or_winner_size_change"
            elif float(d4.get("mean_x0_bps") or 0) > float(d2.get("mean_x0_bps") or 0) + 5:
                composition = "stronger_mechanism_or_fatter_right_tail"
            else:
                composition = "mixed_or_small_sample_block_noise"
    drop5 = (win.get("drop_top_5pct") or {}).get("mean_x0_bps")
    stable_wo_tail = bool(drop5 is not None and float(drop5) > 0)
    return {
        "Was_BG_CONT_VWAP_frozen_unchanged": True,
        "Exact_mechanism_hash": hashes.get("mechanism_hash"),
        "spec_sha256": hashes.get("spec_sha256"),
        "group_classification_code_sha256": hashes.get("group_classification_code_sha256"),
        "entry_code_sha256": hashes.get("entry_code_sha256"),
        "exit_code_sha256": hashes.get("exit_code_sha256"),
        "Did_any_strategy_parameter_change": False,
        "replay_matches_parent_486": rca.get("replay_matches_parent_486"),
        "trade_n": econ.get("trade_n"),
        "How_many_symbols_actually_contribute_positive_gross_edge": len(list(rca.get("positive_gross_symbols") or [])),
        "positive_gross_symbols": rca.get("positive_gross_symbols"),
        "Is_8136_dominant": cause.get("D_flag"),
        "Result_excluding_8136_diagnosis_only": no8136,
        "Top_1pct_winner_contribution_of_positive_PnL": win.get("share_positive_pnl_top_1pct"),
        "Top_5pct_winner_contribution_of_positive_PnL": win.get("share_positive_pnl_top_5pct"),
        "Top_10pct_winner_contribution_of_positive_PnL": win.get("share_positive_pnl_top_10pct"),
        "losers_never_profitable_frac": gb.get("fraction_never_profitable"),
        "losers_profitable_then_lose_frac": gb.get("fraction_profitable_then_loss"),
        "stall_n": stall_n,
        "Average_giveback": gb.get("average_giveback"),
        "Median_giveback": gb.get("median_giveback"),
        "winner_giveback": gb.get("winner_giveback"),
        "loser_giveback": gb.get("loser_giveback"),
        "Does_VWAP_LOSS_primarily": cause.get("vwap_loss_role"),
        "vwap_loss_n": ex.get("vwap_loss_n"),
        "Why_median_negative_while_mean_positive": "right-skew: low hit rate, negative median, fat positive tail (LARGE_WINNER / top percent of trades)",
        "Why_D4_reaches_plus_17_95": d4,
        "Composition_change_or_stronger_mechanism": composition,
        "Is_edge_stable_without_top_few_winners": stable_wo_tail,
        "mean_x0_without_top_5pct": drop5,
        "Root_cause": cause.get("label"),
        "Root_cause_primary": cause.get("primary"),
        "Is_EXIT_research_justified_next": cause.get("exit_research_justified"),
        "Any_Complete_Strategy_promoted": False,
        "Frozen_Validation_opened": False,
        "Old_Confirmation_used_to_design": False,
        "New_paid_data": False,
        "Kabu50": False,
        "submit_cancel_live": "0/0/0",
        "HM1_X0": hm1.get("mean_x0_bps"),
        "HM1_X1": hm1.get("mean_x1_bps"),
        "BG_X0": econ.get("mean_x0_bps"),
        "BG_X1": econ.get("mean_x1_bps"),
        "V27_bolted": False,
        "NO_8136_strategy_created": False,
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
    }
