"""Episode magnitude discovery. Discovery design only. Frozen Validation closed. CS1–CS3 not rescued."""
from __future__ import annotations

import gc
from typing import Any

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.causal_path_to_complete_strategy_v1.events import build_universe, freeze_event_ids, strip_runtime
from research.causal_path_to_complete_strategy_v1.live_status import inspect_live_20260914
from research.causal_path_to_complete_strategy_v1.paths import attach_forward_paths
from research.higher_magnitude_state_discovery_v1 import (
    ANALYSIS_ID,
    CASE_BIND,
    CASE_FOUND,
    CASE_LIMIT,
    CASE_RANKED,
    EPISODE_GAP_MIN,
    FROZEN_VALIDATION_OPENED,
    KABU_50_APPLIED,
    NEXT_BIND,
    NEXT_EXTERNAL,
    NEXT_FREEZE_RANKED,
    NEXT_SECONDARY,
    PARENT_VERDICT,
    REJECTED_CS,
)
from research.higher_magnitude_state_discovery_v1.bind import bind_prior
from research.higher_magnitude_state_discovery_v1.candidates import build_candidates
from research.higher_magnitude_state_discovery_v1.confirm import secondary_confirmation
from research.higher_magnitude_state_discovery_v1.episodes import build_episodes, freeze_episode_ids
from research.higher_magnitude_state_discovery_v1.ranking import absolute_vs_rank, attach_ranks, rank_response
from research.higher_magnitude_state_discovery_v1.sequences import archetype_magnitude, diagnostic_model, sequence_increment


def _sector_of(bind: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        out[str(sym)] = str(row.get("tse33_name") or "")
    return out


def decide(
    *,
    bind_ok: bool,
    promoted_n: int,
    ranking_provided: bool,
    rank_ordered: bool,
    secondary_ran: bool,
    secondary_any_pass: bool | None,
) -> dict[str, Any]:
    if not bind_ok:
        return {
            "CASE": "BIND",
            "VERDICT": CASE_BIND,
            "NEXT": NEXT_BIND,
            "INTERPRETATION": "Prior split/block/CS-reject bind failed. No new design.",
        }
    if int(promoted_n) >= 1 and (not secondary_ran or secondary_any_pass):
        if ranking_provided and rank_ordered:
            return {
                "CASE": "RANKED",
                "VERDICT": CASE_RANKED,
                "NEXT": NEXT_FREEZE_RANKED,
                "INTERPRETATION": "Cross-sectional ranking of contemporaneous episodes supplied the missing magnitude. Frozen Validation remains closed.",
            }
        return {
            "CASE": "FOUND",
            "VERDICT": CASE_FOUND,
            "NEXT": NEXT_SECONDARY,
            "INTERPRETATION": "A high-magnitude complete-strategy episode candidate survived Discovery. Frozen Validation remains closed.",
        }
    return {
        "CASE": "LIMIT",
        "VERDICT": CASE_LIMIT,
        "NEXT": NEXT_EXTERNAL,
        "INTERPRETATION": "Episode sequences and contemporaneous ranking still cannot clear execution-tax-scale magnitude on the 105-name stock panel. Do not mine more technical thresholds. Identify missing external causal information next.",
    }


def _pipeline_dates(symbols: list[str], sector_of: dict[str, str], allowed: set[str], forbidden: set[str]) -> dict[str, Any]:
    minutes = load_minutes(symbols=symbols, allowed_dates=allowed, forbidden_dates=forbidden)
    if minutes["date"].isin(list(forbidden)).any():
        raise RuntimeError("forbidden_partition_loaded")
    uni = build_universe(minutes=minutes, sector_of=sector_of, forbidden_dates=forbidden)
    ev = list(uni.get("events") or [])
    eps_pack = build_episodes(ev, gap_min=EPISODE_GAP_MIN)
    sha_e = freeze_episode_ids(list(eps_pack.get("episodes") or []))
    if sha_e != eps_pack.get("episode_id_sha256"):
        raise RuntimeError("episode_id_mismatch")
    attach_ranks(list(eps_pack.get("episodes") or []))
    attach_forward_paths(list(eps_pack.get("episodes") or []))
    if freeze_episode_ids(list(eps_pack.get("episodes") or [])) != sha_e:
        raise RuntimeError("episode_identities_changed_after_outcomes")
    strip_runtime(list(eps_pack.get("episodes") or []))
    fam = dict(uni.get("families") or {})
    raw_n = int(uni.get("event_n") or 0)
    event_sha = freeze_event_ids(ev) if ev else ""
    del ev
    del minutes
    gc.collect()
    return {"uni_ok": uni.get("ok"), "raw_n": raw_n, "families": fam, "event_sha": event_sha, "episodes": eps_pack}


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    print(f"BIND ok={bind.get('ok')} causal_path={bind.get('causal_path_verdict')} cs_promoted={bind.get('cs1_cs3_promoted_n')}", flush=True)
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = set(str(d) for d in list(split.get("discovery_dates") or []))
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    pack: dict[str, Any] = {"episodes": {"episodes": [], "unique_episode_n": 0, "raw_event_n": 0}}
    seq = {}
    arch = {}
    rank = {}
    absr = {}
    diag = {}
    cand = {"proposals": [], "promoted": [], "promoted_n": 0, "ranking_provided_magnitude": False}
    secondary = {"ran": False, "any_passed": False, "label": "SECONDARY_CONFIRMATION_NOT_PRISTINE", "rows": []}
    if bind.get("ok"):
        symbols = list(bind.get("symbols") or [])
        sector_of = _sector_of(bind)
        print(f"LOAD_DISCOVERY n={len(disc)} forbidden_conf={len(conf)} forbidden_val={len(val)}", flush=True)
        pack = _pipeline_dates(symbols, sector_of, disc, conf | val)
        eps = list((pack.get("episodes") or {}).get("episodes") or [])
        print(f"EPISODES n={(pack.get('episodes') or {}).get('unique_episode_n')} raw={pack.get('raw_n')}", flush=True)
        seq = sequence_increment(eps)
        arch = archetype_magnitude(eps)
        rank = rank_response(eps)
        absr = absolute_vs_rank(episodes=eps)
        diag = diagnostic_model(eps)
        print("SEQ_RANK_DONE", flush=True)
        cand = build_candidates(episodes=eps, date_to_block=dict(blocks.get("date_to_block") or {}), rank_ordered=bool(rank.get("ordered_rank_response")))
        print(f"CANDIDATES promoted={cand.get('promoted_n')}", flush=True)
        promoted = list(cand.get("promoted") or [])
        if promoted:
            print("LOAD_CONFIRMATION", flush=True)
            conf_pack = _pipeline_dates(symbols, sector_of, conf, val)
            conf_eps = list((conf_pack.get("episodes") or {}).get("episodes") or [])
            secondary = secondary_confirmation(episodes=conf_eps, candidates=promoted)
            cand["promoted"] = [c for c in promoted if not (c.get("promotion") or {}).get("secondary_rejected")]
            cand["promoted_n"] = len(cand["promoted"])
            print(f"SECONDARY any_pass={secondary.get('any_passed')} remain={cand.get('promoted_n')}", flush=True)
            del conf_eps
    live = inspect_live_20260914()
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        promoted_n=int(cand.get("promoted_n") or 0),
        ranking_provided=bool(cand.get("ranking_provided_magnitude")),
        rank_ordered=bool(rank.get("ordered_rank_response")),
        secondary_ran=bool(secondary.get("ran")),
        secondary_any_pass=bool(secondary.get("any_passed")) if secondary.get("ran") else None,
    )
    eps_meta = {k: v for k, v in dict(pack.get("episodes") or {}).items() if k != "episodes"}
    proposals = list(cand.get("proposals") or [])
    return {
        "program_id": "COMPLETE_CAUSAL_STRATEGY_RESEARCH_V1",
        "parent_verdict_accepted": PARENT_VERDICT,
        "rejected_cs": list(REJECTED_CS),
        "cs1_cs3_rescued": False,
        "bind": {k: v for k, v in bind.items() if k not in {"by_symbol", "blocks"}},
        "split": split,
        "blocks": {
            **{k: v for k, v in blocks.items() if k not in {"dates", "date_to_block", "blocks"}},
            "blocks": [{kk: vv for kk, vv in b.items() if kk != "dates"} for b in list(blocks.get("blocks") or [])],
        },
        "raw_events": {"n": pack.get("raw_n"), "families": pack.get("families"), "event_sha": pack.get("event_sha")},
        "episodes": eps_meta,
        "sequence_increment": seq,
        "archetype_magnitude": arch,
        "rank_response": rank,
        "absolute_vs_rank": absr,
        "diagnostic": diag,
        "candidates": cand,
        "proposal_summaries": [
            {
                "candidate_id": (p.get("spec") or {}).get("candidate_id"),
                "archetype": (p.get("spec") or {}).get("archetype"),
                "promoted": (p.get("promotion") or {}).get("promoted"),
                "fail_reasons": (p.get("promotion") or {}).get("fail_reasons"),
                "ranked": p.get("discovery_economics"),
                "absolute": p.get("absolute_economics"),
                "top1": p.get("rank_top1_diagnostic"),
            }
            for p in proposals
        ],
        "secondary_confirmation": secondary,
        "frozen_validation": {
            "opened": bool(FROZEN_VALIDATION_OPENED),
            "accessed": False,
            "status": "CLOSED",
            "n": len(val),
            "first_last": [sorted(val)[0], sorted(val)[-1]] if val else None,
        },
        "live_20260914": live,
        "kabu_50_applied": bool(KABU_50_APPLIED),
        "episode_gap_min": EPISODE_GAP_MIN,
        "episode_length_grid_searched": False,
        "old_confirmation_used_to_design": False,
        "future_used_for_episode_boundary": False,
        "decision": decision,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    eps = dict(report.get("episodes") or {})
    seq = dict(report.get("sequence_increment") or {})
    rank = dict(report.get("rank_response") or {})
    cand = dict(report.get("candidates") or {})
    sec = dict(report.get("secondary_confirmation") or {})
    live = dict(report.get("live_20260914") or {})
    d = dict(report.get("decision") or {})
    promoted = list(cand.get("promoted") or [])
    arch = dict(report.get("archetype_magnitude") or {})
    best_gross = None
    for row in list(seq.get("by_class") or []):
        v = row.get("day_mean_x0_h15")
        if v is None:
            continue
        if best_gross is None or float(v) > float(best_gross[1]):
            best_gross = (row.get("sequence_class"), float(v))
    best_cs_x0 = None
    best_cs_x1 = None
    for p in list(cand.get("proposals") or []):
        econ = dict(p.get("discovery_economics") or {})
        if econ.get("mean_x0_bps") is None:
            continue
        if best_cs_x0 is None or float(econ["mean_x0_bps"]) > best_cs_x0:
            best_cs_x0 = float(econ["mean_x0_bps"])
            best_cs_x1 = econ.get("mean_x1_bps")

    def _c(c: dict[str, Any] | None) -> dict[str, Any] | None:
        if not c:
            return None
        spec = dict(c.get("spec") or {})
        econ = dict(c.get("discovery_economics") or {})
        return {
            "id": spec.get("candidate_id"),
            "thesis": spec.get("thesis"),
            "event_sequence": spec.get("event_sequence"),
            "ranking_rule": spec.get("ranking_rule"),
            "ENTRY": spec.get("entry_availability_time"),
            "EXIT": spec.get("technical_invalidation"),
            "D1_D4": econ.get("block_mean_x0"),
            "X0": econ.get("mean_x0_bps"),
            "X1": econ.get("mean_x1_bps"),
            "PF": econ.get("profit_factor"),
            "DD": econ.get("max_dd_daily_mean_bps"),
        }

    return {
        "raw_event_n": (report.get("raw_events") or {}).get("n"),
        "unique_episode_n": eps.get("unique_episode_n"),
        "overlapping_events_deduplicated": eps.get("dedup_rule"),
        "future_outcomes_affected_episode_definition": False,
        "largest_causal_magnitude_sequences": seq.get("largest_class"),
        "sequence_order_adds_value": seq.get("sequence_order_adds_value"),
        "cross_sectional_ranking_ordered": rank.get("ordered_rank_response"),
        "best_gross_x0_opportunity_magnitude": None if best_gross is None else {"class": best_gross[0], "day_mean_x0_h15": best_gross[1]},
        "best_complete_strategy_x0": best_cs_x0,
        "best_complete_strategy_x1": best_cs_x1,
        "any_state_exceeds_prior_1_6bps": bool(
            (best_gross is not None and best_gross[1] > 6.0)
            or any(bool((arch.get(k) or {}).get("material_vs_prior")) for k in arch)
            or (best_cs_x0 is not None and best_cs_x0 > 6.0)
        ),
        "sector_specific_mechanism": [
            {"id": (p.get("spec") or {}).get("candidate_id"), "top_sector": (p.get("discovery_economics") or {}).get("top_sector"), "share": (p.get("discovery_economics") or {}).get("top_sector_share_of_positive_bps")}
            for p in list(cand.get("proposals") or [])
            if (p.get("discovery_economics") or {}).get("sector_specific_strategy")
        ],
        "complete_strategy_candidates_n": cand.get("promoted_n"),
        "candidate_1": _c(promoted[0]) if len(promoted) > 0 else None,
        "candidate_2": _c(promoted[1]) if len(promoted) > 1 else None,
        "old_confirmation_used_to_design": False,
        "secondary_confirmation_run": bool(sec.get("ran")),
        "secondary_confirmation_result": {"any_passed": sec.get("any_passed"), "certifies": False},
        "frozen_validation_opened": False,
        "kabu_50_applied": False,
        "live_20260914": {
            "classification": live.get("classification"),
            "futures": live.get("futures_capture_running"),
            "breadth": live.get("breadth_capture_running"),
            "fed_into_discovery": live.get("fed_into_discovery_tuning"),
        },
        "submit_cancel_live": "0/0/0",
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
        "ANALYSIS_ID": ANALYSIS_ID,
        "cs1_cs3_rescued": False,
        "episode_gap_min": 15,
        "episode_length_grid_searched": False,
    }
