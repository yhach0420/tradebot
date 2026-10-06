"""Minimum missing external causal information. HM1 frozen. NK/TOPIX first. Discovery only."""
from __future__ import annotations

import gc
from typing import Any

from research.higher_magnitude_state_discovery_v1.analyze import _pipeline_dates, _sector_of
from research.identify_minimum_missing_external_causal_information_v1 import (
    ANALYSIS_ID,
    CASE_BIND,
    CASE_FUTURES,
    CASE_INSUFFICIENT,
    CASE_PROXY,
    CASE_SOURCE,
    FROZEN_VALIDATION_OPENED,
    HM1_ID,
    KABU_50_APPLIED,
    NEXT_ACCUM,
    NEXT_BIND,
    NEXT_BRIDGE,
    NEXT_BUILD,
    NEXT_USDJPY,
    PARENT_VERDICT,
)
from research.identify_minimum_missing_external_causal_information_v1.bind import bind_prior
from research.identify_minimum_missing_external_causal_information_v1.features import attach_external_features
from research.identify_minimum_missing_external_causal_information_v1.freeze import freeze_anchors
from research.identify_minimum_missing_external_causal_information_v1.incremental import evaluate_incremental
from research.identify_minimum_missing_external_causal_information_v1.live import inspect_live_now
from research.identify_minimum_missing_external_causal_information_v1.proxy import fetch_discovery_proxies, load_proxy_frame
from research.identify_minimum_missing_external_causal_information_v1.semantics import prove_live_futures_semantics
from research.identify_minimum_missing_external_causal_information_v1.source_audit import audit_external_sources


def decide(
    *,
    bind_ok: bool,
    true_futures_hist: bool,
    proxy_hist: bool,
    structure: bool,
) -> dict[str, Any]:
    if not bind_ok:
        return {
            "CASE": "BIND",
            "VERDICT": CASE_BIND,
            "NEXT": NEXT_BIND,
            "INTERPRETATION": "Prior magnitude-limit bind failed. No external design.",
        }
    if true_futures_hist and structure:
        return {
            "CASE": "FUTURES",
            "VERDICT": CASE_FUTURES,
            "NEXT": NEXT_BUILD,
            "INTERPRETATION": "True NK/TOPIX futures history supplied stable incremental direction among frozen HM1 opportunities.",
        }
    if true_futures_hist and not structure:
        return {
            "CASE": "INSUFFICIENT",
            "VERDICT": CASE_INSUFFICIENT,
            "NEXT": NEXT_USDJPY,
            "INTERPRETATION": "True NK/TOPIX futures were historically testable and did not add enough first-move information. Next single family is USDJPY.",
        }
    if (not true_futures_hist) and proxy_hist and structure:
        return {
            "CASE": "PROXY",
            "VERDICT": CASE_PROXY,
            "NEXT": NEXT_ACCUM,
            "INTERPRETATION": "A labeled PROXY_NOT_FUTURES series showed useful incremental structure. True futures validation is still required. Do not claim ETF proves futures lead.",
        }
    return {
        "CASE": "SOURCE",
        "VERDICT": CASE_SOURCE,
        "NEXT": NEXT_BRIDGE,
        "INTERPRETATION": "True historical NK225mini/TOPIX futures 1-minute data are not available under current entitlements. Proxy plus prospective true-futures accumulation is the bridge. Do not return to technical threshold mining. Do not jump to USDJPY until true NK/TOPIX futures are actually tested.",
    }


def _compact_trades(rows: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    keep = (
        "episode_id",
        "date",
        "symbol",
        "decision_time",
        "event_time",
        "cs_rank",
        "block",
        "x0_bps",
        "mfe_before_mae",
        "nk_ret_60s_bps",
        "topix_ret_60s_bps",
        "agreement_state",
        "lag_sec",
    )
    return [{k: r.get(k) for k in keep} for r in rows[:limit]]


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    print(f"BIND ok={bind.get('ok')} higher_mag={bind.get('higher_magnitude_verdict')}", flush=True)
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = set(str(d) for d in list(split.get("discovery_dates") or []))
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    date_to_block = dict(blocks.get("date_to_block") or {})
    disc_sorted = sorted(disc)
    first = disc_sorted[0] if disc_sorted else None
    last = disc_sorted[-1] if disc_sorted else None

    live = inspect_live_now()
    print(
        f"LIVE fut_pid={live.get('futures_pid')} alive={live.get('futures_alive')} "
        f"nk_ts={live.get('nk225mini_latest_timestamp')} nk_n={live.get('nk225mini_row_count')} "
        f"tx_ts={live.get('topix_latest_timestamp')} tx_n={live.get('topix_row_count')} "
        f"br_pid={live.get('breadth_pid')} alive={live.get('breadth_alive')}",
        flush=True,
    )
    audit = audit_external_sources()
    semantics = prove_live_futures_semantics()
    true_hist = bool(
        (audit.get("true_historical_nk225mini_minute") or {}).get("available")
        and (audit.get("true_historical_topix_futures_minute") or {}).get("available")
    )

    pack: dict[str, Any] = {"episodes": {"episodes": [], "unique_episode_n": 0}}
    freeze: dict[str, Any] = {"ok": False, "hm1_trades": [], "hm2_trades": []}
    proxy_fetch: dict[str, Any] = {"ok": False, "label": "PROXY_NOT_FUTURES"}
    incremental: dict[str, Any] = {}
    hm1_joined: list[dict[str, Any]] = []
    if bind.get("ok"):
        symbols = list(bind.get("symbols") or [])
        sector_of = _sector_of(bind)
        print(f"LOAD_DISCOVERY n={len(disc)} forbidden_conf={len(conf)} forbidden_val={len(val)}", flush=True)
        pack = _pipeline_dates(symbols, sector_of, disc, conf | val)
        eps = list((pack.get("episodes") or {}).get("episodes") or [])
        print(f"EPISODES n={(pack.get('episodes') or {}).get('unique_episode_n')}", flush=True)
        freeze = freeze_anchors(episodes=eps, date_to_block=date_to_block)
        print(f"ANCHOR HM1 n={freeze.get('hm1', {}).get('trade_n')} sha={freeze.get('hm1', {}).get('anchor_sha256')}", flush=True)
        del eps
        gc.collect()
        proxy_ok_probe = bool((audit.get("proxy_probe") or {}).get("nk", {}).get("ok") and (audit.get("proxy_probe") or {}).get("topix", {}).get("ok"))
        if (not true_hist) and proxy_ok_probe and first and last:
            print("FETCH_PROXY_DISCOVERY_ONLY", flush=True)
            proxy_fetch = fetch_discovery_proxies(first=first, last=last, allowed_dates=disc)
            if proxy_fetch.get("ok"):
                nk_df = load_proxy_frame("1321", first, last)
                tx_df = load_proxy_frame("1306", first, last)
                hm1_joined = attach_external_features(
                    trades=list(freeze.get("hm1_trades") or []),
                    nk_df=nk_df,
                    tx_df=tx_df,
                    source_label="PROXY_NOT_FUTURES",
                )
                incremental = evaluate_incremental(trades=hm1_joined)
                print(
                    f"INCREMENTAL structure={((incremental.get('structure_precommitted_gate') or {}).get('structure_exists'))} "
                    f"delta_x0={(incremental.get('nk_sign') or {}).get('delta_x0')}",
                    flush=True,
                )
        elif true_hist:
            incremental = {"skipped": True, "reason": "true_futures_hist_path_not_ingested_this_phase_unexpected"}

    structure = bool((incremental.get("structure_precommitted_gate") or {}).get("structure_exists"))
    proxy_hist = bool(proxy_fetch.get("ok"))
    decision = decide(bind_ok=bool(bind.get("ok")), true_futures_hist=true_hist, proxy_hist=proxy_hist, structure=structure)
    unanswered = None
    if decision.get("VERDICT") == CASE_SOURCE:
        unanswered = "Can NK225mini/TOPIX futures distinguish favorable-first vs adverse-first among frozen HM1 opportunities? True historical futures were not testable."
    elif decision.get("VERDICT") == CASE_INSUFFICIENT:
        unanswered = "Is the missing driver currency-sensitive Japan risk/sector rotation (USDJPY) rather than broad-index futures direction?"
    elif decision.get("VERDICT") == CASE_PROXY:
        unanswered = "Does the proxy structure replicate on true NK225mini/TOPIX futures prints accumulated prospectively?"

    hm1_meta = {k: v for k, v in dict(freeze.get("hm1") or {}).items() if k != "episode_ids"}
    hm1_meta["episode_id_n"] = len(list((freeze.get("hm1") or {}).get("episode_ids") or []))
    hm1_meta["episode_ids_head"] = list((freeze.get("hm1") or {}).get("episode_ids") or [])[:8]

    return {
        "program_id": "COMPLETE_CAUSAL_STRATEGY_RESEARCH_V1",
        "parent_verdict_accepted": PARENT_VERDICT,
        "interpretation_narrow": "CURRENT_STOCK_PANEL_DIRECTIONAL_EDGE_DOES_NOT_CLEAR_EXECUTION_SCALE",
        "stock_information_zero_value": False,
        "hm1_id": HM1_ID,
        "bind": {k: v for k, v in bind.items() if k not in {"by_symbol", "blocks"}},
        "split": {k: v for k, v in split.items() if k not in {"discovery_dates", "confirmation_dates", "frozen_validation_dates"}},
        "blocks": {
            **{k: v for k, v in blocks.items() if k not in {"dates", "date_to_block", "blocks"}},
            "blocks": [{kk: vv for kk, vv in b.items() if kk != "dates"} for b in list(blocks.get("blocks") or [])],
        },
        "external_join_anchor": {
            **{k: v for k, v in freeze.items() if k not in {"hm1_trades", "hm2_trades", "hm1"}},
            "hm1": hm1_meta,
            "hm2": freeze.get("hm2"),
        },
        "source_audit": audit,
        "futures_semantics": semantics,
        "proxy_track": proxy_fetch,
        "prospective_track": {
            "label": "PROSPECTIVE_TRUE_FUTURES",
            "days": ["20260911", "20260914"],
            "not_enough_to_claim_stable_strategy": True,
            "not_used_to_redesign_hm1": True,
            "not_conflated_with_proxy": True,
        },
        "incremental": {k: v for k, v in incremental.items() if k != "filtered_if_direction_gate"} | {
            "filtered_if_direction_gate": incremental.get("filtered_if_direction_gate"),
        },
        "sample_joined_trades": _compact_trades(hm1_joined),
        "next_external_decision": {
            "usd_jpy_tested": False,
            "nq_es_tested": False,
            "unanswered_causal_question": unanswered,
            "do_not_add_all_families": True,
        },
        "frozen_validation": {
            "opened": bool(FROZEN_VALIDATION_OPENED),
            "accessed": False,
            "status": "CLOSED",
            "n": len(val),
            "first_last": [sorted(val)[0], sorted(val)[-1]] if val else None,
            "external_outcomes_not_inspected": True,
        },
        "live_20260914": live,
        "kabu_50_applied": bool(KABU_50_APPLIED),
        "old_confirmation_used_to_design": False,
        "hm1_tuned": False,
        "purchase_requested": False,
        "yfinance_used": False,
        "raw_events": {"n": pack.get("raw_n"), "event_sha": pack.get("event_sha")},
        "episodes": {k: v for k, v in dict(pack.get("episodes") or {}).items() if k != "episodes"},
        "decision": decision,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    a_src = dict(report.get("source_audit") or {})
    freeze = dict(report.get("external_join_anchor") or {})
    hm1 = dict(freeze.get("hm1") or {})
    inc = dict(report.get("incremental") or {})
    base = dict(inc.get("base") or {})
    nk = dict(inc.get("nk_sign") or {})
    tx = dict(inc.get("topix_sign") or {})
    gate = dict(inc.get("structure_precommitted_gate") or {})
    live = dict(report.get("live_20260914") or {})
    d = dict(report.get("decision") or {})
    proxy = dict(report.get("proxy_track") or {})
    filtered = dict(inc.get("filtered_if_direction_gate") or {}) if inc.get("filtered_if_direction_gate") else {}
    nk_hist = dict(a_src.get("true_historical_nk225mini_minute") or {})
    tx_hist = dict(a_src.get("true_historical_topix_futures_minute") or {})
    nk_pos = dict(nk.get("pos") or {})
    nk_neg = dict(nk.get("neg") or {})
    structure = bool(gate.get("structure_exists"))
    new_x0 = filtered.get("mean_x0_bps") if filtered else None
    base_x0 = base.get("mean_x0_bps") or (hm1.get("control_replay") or {}).get("mean_x0_bps")
    delta = (float(new_x0) - float(base_x0)) if new_x0 is not None and base_x0 is not None else nk.get("delta_x0")
    return {
        "hm1_stock_side_anchor_frozen": bool(freeze.get("identities_frozen_before_external_join") and hm1.get("spec_frozen")),
        "anchor_sha": hm1.get("anchor_sha256"),
        "historical_nk225mini_minute_available": bool(nk_hist.get("available")),
        "nk_source": nk_hist.get("source"),
        "nk_history": nk_hist.get("history"),
        "nk_timestamp_semantics_proven": bool(nk_hist.get("timestamp_semantics_proven")),
        "historical_topix_futures_minute_available": bool(tx_hist.get("available")),
        "topix_source": tx_hist.get("source"),
        "topix_history": tx_hist.get("history"),
        "topix_timestamp_semantics_proven": bool(tx_hist.get("timestamp_semantics_proven")),
        "additional_purchase_required": bool(a_src.get("additional_purchase_required")),
        "did_not_purchase": True,
        "historical_proxies_available": ["1321_PROXY_NOT_FUTURES", "1306_PROXY_NOT_FUTURES"] if proxy.get("ok") else [],
        "proxies_explicitly_labeled_proxy": bool(proxy.get("label") == "PROXY_NOT_FUTURES"),
        "external_improved_hm1_favorable_first": bool(structure) and (nk.get("delta_p_first") or 0) > 0,
        "base_mfe_before_mae_probability": base.get("p_mfe_before_mae"),
        "external_conditioned_probability": (filtered.get("p_mfe_before_mae") if structure and filtered else nk_pos.get("p_mfe_before_mae")),
        "nk_up_p_mfe_before_mae": nk_pos.get("p_mfe_before_mae"),
        "nk_down_p_mfe_before_mae": nk_neg.get("p_mfe_before_mae"),
        "base_hm1_top1_x0": base_x0,
        "external_conditioned_x0": new_x0 if structure else nk_pos.get("mean_x0_bps"),
        "nk_up_x0": nk_pos.get("mean_x0_bps"),
        "nk_down_x0": nk_neg.get("mean_x0_bps"),
        "delta_x0": delta,
        "delta_x0_is_nk_up_minus_nk_down": not structure,
        "no_promoted_external_filter": not structure,
        "improvement_stable_across_d1_d4": bool(inc.get("d1_d4_stable")),
        "nk_incremental_value": nk.get("delta_x0"),
        "topix_incremental_value": tx.get("delta_x0"),
        "nk_topix_divergence_incremental_value": (inc.get("agreement_states") or {}).get("nk_up_topix_down"),
        "futures_merely_duplicated_stock_sector_momentum": bool((inc.get("duplicate_of_stock_momentum") or {}).get("flag")),
        "simple_causal_role": inc.get("simple_causal_role") if structure else None,
        "DIRECTION_GATE": bool(structure and (inc.get("roles") or {}).get("DIRECTION_GATE")),
        "VETO": bool(structure and (inc.get("roles") or {}).get("VETO")),
        "REGIME": bool(structure and (inc.get("roles") or {}).get("REGIME")),
        "TIE_BREAKER": False,
        "enough_evidence_for_external_conditioned_complete_strategy": False,
        "frozen_validation_opened": False,
        "old_confirmation_used_to_design": False,
        "kabu_50_applied": False,
        "live_20260914": {
            "futures_pid": live.get("futures_pid"),
            "futures_alive": live.get("futures_alive"),
            "nk225mini_latest_timestamp": live.get("nk225mini_latest_timestamp"),
            "nk_row_count": live.get("nk225mini_row_count"),
            "topix_latest_timestamp": live.get("topix_latest_timestamp"),
            "topix_row_count": live.get("topix_row_count"),
            "breadth_pid": live.get("breadth_pid"),
            "breadth_alive": live.get("breadth_alive"),
            "breadth_latest_snapshot": live.get("breadth_latest_snapshot"),
            "breadth_snapshot_count": live.get("breadth_snapshot_count"),
            "breadth_cycles": live.get("breadth_cycles"),
            "recovery": live.get("recovery"),
        },
        "submit_cancel_live": "0/0/0",
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
        "ANALYSIS_ID": ANALYSIS_ID,
        "hm1_tuned": False,
        "true_futures_historically_tested": bool(nk_hist.get("available") and tx_hist.get("available")),
    }
