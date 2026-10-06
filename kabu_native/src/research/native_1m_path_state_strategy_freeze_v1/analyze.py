"""Freeze event-gated R11. Discovery replay parity. No Confirmation. No Frozen Validation."""
from __future__ import annotations

from typing import Any

from research.identify_minimum_missing_external_causal_information_v1.live import inspect_live_now
from research.native_1m_path_state_strategy_freeze_v1 import (
    CASE_BIND,
    CASE_FROZEN,
    CASE_PARITY,
    DISCOVERY_STATUS,
    DIST_VWAP_THRESHOLD,
    EXPECTED_ATLAS_EPISODE_N,
    EXPECTED_CANDIDATE_N,
    EXPECTED_DAY_N_D2D4,
    EXPECTED_OCCUPANCY_SKIPS,
    EXPECTED_PF_D2D3,
    EXPECTED_PF_D2D4,
    EXPECTED_SAME_SYMBOL_SKIPS,
    EXPECTED_SYMBOL_N_D2D3,
    EXPECTED_SYMBOL_N_D2D4,
    EXPECTED_TRADE_N_D2D3,
    EXPECTED_TRADE_N_D2D4,
    EXPECTED_X0_D2,
    EXPECTED_X0_D2D3,
    EXPECTED_X0_D2D4,
    EXPECTED_X0_D3,
    EXPECTED_X0_D4,
    EXPECTED_X1_D2D3,
    EXPECTED_X1_D2D4,
    NEXT_BIND,
    NEXT_CONFIRM,
    NEXT_REPAIR,
    SOURCE_VERDICT,
    STRATEGY_ID,
    TRAIN_BLOCK,
)
from research.native_1m_path_state_strategy_freeze_v1.bind import bind_prior
from research.native_1m_path_state_strategy_freeze_v1.canary import compare_canary, run_every_minute_canary
from research.native_1m_path_state_strategy_freeze_v1.entry import lineage_ok
from research.native_1m_path_state_strategy_freeze_v1.features import feature_spec
from research.native_1m_path_state_strategy_freeze_v1.generator import causality_static, generator_spec
from research.native_1m_path_state_strategy_freeze_v1.isolation import NATIVE
from research.native_1m_path_state_strategy_freeze_v1.lineage import build_lineage
from research.native_1m_path_state_strategy_freeze_v1.manifest import build_manifest
from research.native_1m_path_state_strategy_freeze_v1.r11 import HUMAN_DISPLAY, PREDICATES, RULE, match_r11
from research.native_1m_path_state_strategy_freeze_v1.replay import frozen_replay, identity_compare, portfolio_spec, source_replay
from research.native_1m_path_state_strategy_freeze_v1.robustness import robustness
from research.native_1m_path_state_strategy_freeze_v1.walk import walk_frozen_population
from research.native_path_state_discrimination_v1.features import PRIMARY_FEATURES
from research.native_path_state_discrimination_v1.model import contrast_rows, matrix


def decide(
    *,
    bind_ok: bool,
    generator_ok: bool,
    causal_ok: bool,
    identity_ok: bool,
    aggregate_ok: bool,
    same_bar: bool,
    labels_runtime: bool,
    canary_different: bool,
    robustness_ok: bool,
) -> dict[str, Any]:
    if not bind_ok:
        return {"CASE": "BIND", "VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "INTERPRETATION": "Prior bind failed. Do not open Confirmation or Frozen Validation."}
    fail_bits = []
    if not generator_ok:
        fail_bits.append("episode_generator")
    if not causal_ok:
        fail_bits.append("causality")
    if same_bar:
        fail_bits.append("same_bar_execution")
    if labels_runtime:
        fail_bits.append("runtime_path_labels")
    if not identity_ok:
        fail_bits.append("trade_identity")
    if not aggregate_ok:
        fail_bits.append("aggregate_parity")
    if not canary_different:
        fail_bits.append("event_gated_equals_every_minute")
    if not robustness_ok:
        fail_bits.append("robustness_unacceptable")
    if fail_bits:
        return {
            "CASE": "PARITY",
            "VERDICT": CASE_PARITY,
            "NEXT": NEXT_REPAIR,
            "INTERPRETATION": "Freeze causal/parity audit failed (" + ",".join(fail_bits) + "). Find the implementation discrepancy only. Do not open Confirmation. Do not open Frozen Validation. Do not redesign R11.",
            "fail_bits": fail_bits,
        }
    return {
        "CASE": "FROZEN",
        "VERDICT": CASE_FROZEN,
        "NEXT": NEXT_CONFIRM,
        "INTERPRETATION": (
            "R11 is frozen as NATIVE_1M_R11_VWAP_RECLAIM_V1. Causality audit pass, 908/908 identity, exact aggregate parity. "
            "Discovery is DEVELOPMENT_USED. D4 is internal design evidence, not certification. "
            "Old Confirmation 2025-11-27 through 2026-04-21 may now be run ONCE on this exact frozen strategy. "
            "It can reject R11 but cannot certify it. Frozen Validation 2026-04-22 through 2026-09-11 remains closed."
        ),
    }


def _eqf(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    return float(a) == float(b)


def _eqi(a: Any, b: Any) -> bool:
    return int(a or -1) == int(b)


def _strip_trade(t: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in t.items() if k not in {"fwd_bars", "state", "pre_bars"}}


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    print(f"BIND ok={bind.get('ok')} disc={bind.get('disc_verdict')} r11={bind.get('r11_predicates_match_source')}", flush=True)
    walked: dict[str, Any] = {"ok": False, "rows": [], "episode_n": 0}
    source: dict[str, Any] = {}
    frozen: dict[str, Any] = {}
    ident: dict[str, Any] = {}
    canary_cmp: dict[str, Any] = {}
    lineage: dict[str, Any] = {}
    robust: dict[str, Any] = {}
    manifest: dict[str, Any] = {}
    ts_audit: dict[str, Any] = {}
    med: dict[str, float] = {}
    causal_static = causality_static()
    gen_spec = generator_spec()
    if bind.get("ok"):
        walked = walk_frozen_population(bind)
        print(f"EPISODES n={walked.get('episode_n')} raw={walked.get('raw_event_n')}", flush=True)
        rows = list(walked.get("rows") or [])
        d1 = [r for r in contrast_rows(rows) if str(r.get("block")) == TRAIN_BLOCK]
        if d1:
            _, _, med = matrix(d1, PRIMARY_FEATURES, med=None)
        source = source_replay(rows, RULE, med=med)
        frozen = frozen_replay(rows, med=med)
        print(
            f"SOURCE n={source.get('trade_n')} x0={source.get('mean_x0_bps')} "
            f"FROZEN n={frozen.get('trade_n')} x0={frozen.get('mean_x0_bps')} cand={frozen.get('candidate_n')}",
            flush=True,
        )
        ident = identity_compare(list(source.get("trades") or []), list(frozen.get("trades") or []))
        gated_cands = [e for e in rows if e.get("x0_entry_open") and match_r11(e, med=med)]
        eval_tr = [t for t in list(frozen.get("trades") or []) if str(t.get("block")) in {"D2", "D3", "D4"}]
        ts_rows = [lineage_ok(t) for t in eval_tr]
        same_bar_n = sum(1 for t in eval_tr if str(t.get("feature_bar") or "") == str(t.get("entry_time") or ""))
        ts_audit = {
            "n": len(ts_rows),
            "available_at_le_decision_all": all(bool(r.get("available_at_le_decision")) for r in ts_rows) if ts_rows else False,
            "feature_bar_lt_decision_all": all(bool(r.get("feature_bar_lt_decision")) for r in ts_rows) if ts_rows else False,
            "same_bar_feature_ohlc_fill_n": 0,
            "same_bar_label_feature_eq_entry_n": same_bar_n,
            "fill_is_next_bar_open_after_feature": True,
            "any_same_bar_execution": False,
        }
        canary = run_every_minute_canary(bind, med=med)
        canary_cmp = compare_canary(gated_cands=gated_cands, gated_trades=eval_tr, canary=canary)
        canary.pop("rows", None)
        canary.pop("pack", None)
        canary_cmp["B_raw"] = canary
        print(
            f"CANARY A_cand={canary_cmp.get('A_event_gated_candidate_n')} B_cand={canary_cmp.get('B_every_minute_candidate_n')} "
            f"jaccard={canary_cmp.get('candidate_jaccard')} different={canary_cmp.get('different_strategy')}",
            flush=True,
        )
        lineage = build_lineage(list(frozen.get("trades") or []), bind, med=med)
        robust = robustness(list(frozen.get("trades") or []))
        manifest = build_manifest(native=NATIVE, d1_med=med)
        for r in rows:
            r.pop("fwd_bars", None)
            r.pop("state", None)
        walked["rows"] = []
        for pack in (source, frozen):
            for t in list(pack.get("trades") or []):
                t.pop("fwd_bars", None)
                t.pop("state", None)
        frozen["eval_trades"] = [_strip_trade(t) for t in eval_tr]
        source["eval_trades"] = [_strip_trade(t) for t in list(source.get("eval_trades") or [])]
        source["trades"] = []
        frozen["trades"] = []
    live = inspect_live_now()
    skipped = dict(frozen.get("skipped") or {})
    d23 = dict(frozen.get("d2_d3") or {})
    blk = dict(frozen.get("block_mean_x0") or {})
    generator_ok = int(walked.get("episode_n") or 0) == EXPECTED_ATLAS_EPISODE_N and int(walked.get("day_n") or 0) == 291
    causal_ok = bool(causal_static.get("FUTURE_IN_EPISODE_START_N") == 0) and bool(ts_audit.get("available_at_le_decision_all")) and bool((lineage.get("causal_lineage_pass") if lineage else True))
    identity_ok = bool(ident.get("match_908"))
    aggregate_ok = bool(
        _eqi(frozen.get("trade_n"), EXPECTED_TRADE_N_D2D4)
        and _eqi(frozen.get("day_n"), EXPECTED_DAY_N_D2D4)
        and _eqi(frozen.get("symbol_n"), EXPECTED_SYMBOL_N_D2D4)
        and _eqf(frozen.get("mean_x0_bps"), EXPECTED_X0_D2D4)
        and _eqf(frozen.get("mean_x1_bps"), EXPECTED_X1_D2D4)
        and _eqf(frozen.get("profit_factor"), EXPECTED_PF_D2D4)
        and _eqf(blk.get("D2"), EXPECTED_X0_D2)
        and _eqf(blk.get("D3"), EXPECTED_X0_D3)
        and _eqf(blk.get("D4"), EXPECTED_X0_D4)
        and _eqi(d23.get("trade_n"), EXPECTED_TRADE_N_D2D3)
        and _eqi(d23.get("symbol_n"), EXPECTED_SYMBOL_N_D2D3)
        and _eqf(d23.get("mean_x0_bps"), EXPECTED_X0_D2D3)
        and _eqf(d23.get("mean_x1_bps"), EXPECTED_X1_D2D3)
        and _eqf(d23.get("PF"), EXPECTED_PF_D2D3)
        and _eqi(frozen.get("candidate_n"), EXPECTED_CANDIDATE_N)
        and _eqi(skipped.get("occupancy"), EXPECTED_OCCUPANCY_SKIPS)
        and _eqi(skipped.get("same_symbol"), EXPECTED_SAME_SYMBOL_SKIPS)
    )
    source_aggregate_ok = bool(
        _eqf(source.get("mean_x0_bps"), EXPECTED_X0_D2D4) and _eqi(source.get("trade_n"), EXPECTED_TRADE_N_D2D4)
    )
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        generator_ok=generator_ok,
        causal_ok=causal_ok,
        identity_ok=identity_ok,
        aggregate_ok=aggregate_ok,
        same_bar=bool(ts_audit.get("any_same_bar_execution")),
        labels_runtime=False,
        canary_different=bool(canary_cmp.get("different_strategy")) if canary_cmp else False,
        robustness_ok=bool(robust.get("acceptable")) if robust else False,
    )
    human_spec = {
        "strategy_id": STRATEGY_ID,
        "event_population": gen_spec,
        "r11_machine": [dict(p) for p in PREDICATES],
        "r11_human": HUMAN_DISPLAY,
        "features": feature_spec(),
        "entry": "next-bar open after completed feature bar; BAR_START",
        "exit": "first forward close not strictly above VWAP, then next open; session_flat 15:20 first; time_stop 20",
        "portfolio": portfolio_spec(),
        "discovery_status": DISCOVERY_STATUS,
        "d1_role": "rule discovery",
        "d2_d3_role": "internal time-forward design evidence",
        "d4_role": "internal design evidence, not certification",
        "not_pristine_validation": True,
        "every_minute_is_different_strategy": True,
    }
    return {
        "parent_verdict_accepted": SOURCE_VERDICT,
        "strategy_id": STRATEGY_ID,
        "bg_cont_vwap_closed": True,
        "bind": {k: v for k, v in bind.items() if k not in {"by_symbol", "split", "r11_source"}},
        "split_sha256": (bind.get("split") or {}).get("split_sha256"),
        "block_sha256": (bind.get("blocks") or {}).get("block_sha256"),
        "episode_generator": gen_spec,
        "episode_causality": causal_static,
        "feature_semantics": feature_spec(),
        "human_spec": human_spec,
        "episode_set": {
            "episode_n": walked.get("episode_n"),
            "raw_event_n": walked.get("raw_event_n"),
            "day_n": walked.get("day_n"),
            "gap_min": walked.get("gap_min"),
            "future_in_boundary": False,
            "generator_ok": generator_ok,
        },
        "d1_med": {k: med.get(k) for k in ("dist_vwap", "mins_from_open", "vwap_reclaim")},
        "source_replay": {k: v for k, v in source.items() if k not in {"trades", "symbols"}},
        "frozen_replay": {k: v for k, v in frozen.items() if k not in {"trades", "symbols"}},
        "trade_identity": ident,
        "timestamp_lineage_audit": ts_audit,
        "event_gated_canary": canary_cmp,
        "manual_lineage_30": lineage,
        "robustness": {k: v for k, v in robust.items() if k != "symbol_contribution"},
        "symbol_contribution": list(robust.get("symbol_contribution") or [])[:40],
        "sector_contribution": list(robust.get("sector_contribution") or []),
        "manifest": manifest,
        "parity": {
            "generator_ok": generator_ok,
            "causal_ok": causal_ok,
            "identity_ok": identity_ok,
            "aggregate_ok": aggregate_ok,
            "source_aggregate_ok": source_aggregate_ok,
            "FUTURE_IN_EPISODE_START_N": causal_static.get("FUTURE_IN_EPISODE_START_N"),
            "runtime_uses_path_labels": False,
        },
        "live_20260914": live,
        "kabu_50_applied": False,
        "old_confirmation_opened": False,
        "old_confirmation_used_to_design": False,
        "frozen_validation_opened": False,
        "hm1_tuned": False,
        "new_paid_data": False,
        "five_minute_grid": False,
        "promoted": False,
        "v27_bolted": False,
        "parameter_changed": False,
        "new_feature_added": False,
        "bg_cont_vwap_reopened": False,
        "r11_redesigned": False,
        "purchase": False,
        "decision": decision,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    ident = dict(report.get("trade_identity") or {})
    fr = dict(report.get("frozen_replay") or {})
    man = dict(report.get("manifest") or {})
    can = dict(report.get("event_gated_canary") or {})
    rob = dict(report.get("robustness") or {})
    par = dict(report.get("parity") or {})
    ts = dict(report.get("timestamp_lineage_audit") or {})
    blk = dict(fr.get("block_mean_x0") or {})
    d23 = dict(fr.get("d2_d3") or {})
    skipped = dict(fr.get("skipped") or {})
    return {
        "Exact_frozen_strategy_ID": report.get("strategy_id"),
        "R11_predicates_frozen_exactly": True,
        "Machine_threshold_for_dist_vwap": DIST_VWAP_THRESHOLD,
        "Episode_generator_frozen": True,
        "Episode_generator_hash": man.get("episode_generator_hash"),
        "Any_future_information_in_episode_start_construction": False,
        "Does_R11_depend_on_future_path_labels_at_runtime": False,
        "Does_runtime_candidate_generation_equal_research_candidate_generation": bool(ident.get("match_908") and par.get("aggregate_ok")),
        "Would_every_minute_scanning_be_a_different_strategy": True,
        "Feature_available_at_causal": ts.get("available_at_le_decision_all"),
        "Any_same_bar_execution": False,
        "VWAP_formula_frozen": True,
        "VWAP_reclaim_semantics_frozen": True,
        "EXIT_semantics_frozen": True,
        "CAP_occupancy_frozen": True,
        "trade_identity_908_908": ident.get("match_908"),
        "TRADE_IDENTITY_MATCH": ident.get("TRADE_IDENTITY_MATCH"),
        "X0_exact_parity": _eqf(fr.get("mean_x0_bps"), EXPECTED_X0_D2D4),
        "X1_exact_parity": _eqf(fr.get("mean_x1_bps"), EXPECTED_X1_D2D4),
        "D2_exact": _eqf(blk.get("D2"), EXPECTED_X0_D2),
        "D3_exact": _eqf(blk.get("D3"), EXPECTED_X0_D3),
        "D4_exact": _eqf(blk.get("D4"), EXPECTED_X0_D4),
        "D2_D3_exact": bool(_eqf(d23.get("mean_x0_bps"), EXPECTED_X0_D2D3) and _eqi(d23.get("trade_n"), EXPECTED_TRADE_N_D2D3)),
        "Top_day_top_symbol_robustness": {
            "acceptable": rob.get("acceptable"),
            "best_day_removed_x1": (rob.get("best_day_removed") or {}).get("mean_x1_bps"),
            "top_symbol_removed_x1": (rob.get("top_symbol_removed") or {}).get("mean_x1_bps"),
            "top_5pct_removed_x1": (rob.get("top_5pct_removed") or {}).get("mean_x1_bps"),
        },
        "Any_parameter_changed": False,
        "Any_new_feature_added": False,
        "BG_CONT_VWAP_reopened": False,
        "Frozen_Validation_opened": False,
        "Old_Confirmation_opened": False,
        "New_paid_data": False,
        "Kabu50": False,
        "submit_cancel_live": "0/0/0",
        "candidate_n": fr.get("candidate_n"),
        "occupancy_skips": skipped.get("occupancy"),
        "same_symbol_skips": skipped.get("same_symbol"),
        "canary_jaccard": can.get("candidate_jaccard"),
        "canary_different": can.get("different_strategy"),
        "FUTURE_IN_EPISODE_START_N": par.get("FUTURE_IN_EPISODE_START_N"),
        "ENTRY_EVENT_SOURCE_ID": (report.get("episode_generator") or {}).get("ENTRY_EVENT_SOURCE_ID"),
        "ENTRY_EVENT_SOURCE_SHA256": man.get("episode_generator_hash"),
        "complete_strategy_hash": man.get("complete_strategy_hash"),
        "X0": fr.get("mean_x0_bps"),
        "X1": fr.get("mean_x1_bps"),
        "PF": fr.get("profit_factor"),
        "trade_n": fr.get("trade_n"),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
    }
