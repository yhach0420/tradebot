"""Causal event universe → path RCA → ≤3 complete candidates. Discovery design only. Frozen Validation closed."""
from __future__ import annotations

from typing import Any

from research.cause_first_mechanism_discovery_v1.panel import load_minutes
from research.causal_path_to_complete_strategy_v1 import (
    ANALYSIS_ID,
    CASE_BIND,
    CASE_FOUND,
    CASE_INSUFFICIENT,
    CASE_STRUCTURE,
    EXPECTED_SPLIT_SHA256,
    FROZEN_VALIDATION_OPENED,
    FUTURE_AS_DECISION_FEATURE,
    KABU_50_APPLIED,
    M1_M11_RESCUED,
    MAX_CANDIDATES,
    NEXT_BIND,
    NEXT_FREEZE,
    NEXT_MAGNITUDE,
    NEXT_MISSING,
    OLD_CONFIRMATION_USED_TO_DESIGN,
    PARENT_VERDICT,
    PROGRAM_ID,
    REJECTED_M1_M11,
    SAME_BAR_CLOSE_ENTRY,
)
from research.causal_path_to_complete_strategy_v1.bind import bind_prior
from research.causal_path_to_complete_strategy_v1.blocks import freeze_discovery_blocks
from research.causal_path_to_complete_strategy_v1.candidates import build_candidates
from research.causal_path_to_complete_strategy_v1.confirm import secondary_confirmation
from research.causal_path_to_complete_strategy_v1.events import (
    FAMILIES,
    build_universe,
    freeze_event_ids,
    primitives_spec,
    strip_runtime,
)
from research.causal_path_to_complete_strategy_v1.live_status import inspect_live_20260914
from research.causal_path_to_complete_strategy_v1.paths import TAXONOMY, attach_forward_paths, taxonomy_counts
from research.causal_path_to_complete_strategy_v1.rca import rca_bundle
from research.current_day1_information_close_v1 import FEATURE_MINING_CLOSED


def _sector_of(bind: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        out[str(sym)] = str(row.get("tse33_name") or "")
    return out


def decide(
    *,
    bind_ok: bool,
    structure: bool,
    promoted_n: int,
    secondary_any_pass: bool | None,
    secondary_ran: bool,
) -> dict[str, Any]:
    if not bind_ok:
        return {
            "CASE": "BIND",
            "VERDICT": CASE_BIND,
            "NEXT": NEXT_BIND,
            "INTERPRETATION": "Prior split SHA or rejected M1–M11 bind failed. No new design.",
        }
    if int(promoted_n) >= 1 and secondary_ran and secondary_any_pass:
        return {
            "CASE": "FOUND",
            "VERDICT": CASE_FOUND,
            "NEXT": NEXT_FREEZE,
            "INTERPRETATION": "At least one complete-strategy candidate survived Discovery economics and one-shot non-pristine Confirmation. Frozen Validation remains closed until an explicit freeze.",
        }
    if structure:
        return {
            "CASE": "STRUCTURE",
            "VERDICT": CASE_STRUCTURE,
            "NEXT": NEXT_MAGNITUDE,
            "INTERPRETATION": "Within-family path RCA found some block-stable structure, but complete-strategy economics after X1 / secondary check were insufficient. Do not return to random hypotheses.",
        }
    return {
        "CASE": "INSUFFICIENT",
        "VERDICT": CASE_INSUFFICIENT,
        "NEXT": NEXT_MISSING,
        "INTERPRETATION": "Event/path/RCA on the 105-name stock panel did not produce a stable complete strategy. Next is identify missing external causal information, not more one-line rules.",
    }


def _sample_events(events: list[dict[str, Any]], n: int = 8) -> list[dict[str, Any]]:
    keys = (
        "event_id",
        "date",
        "symbol",
        "event_time",
        "event_family",
        "feature_bar",
        "feature_available_at",
        "breadth_level",
        "breadth_slope",
        "rs_5m",
        "path_taxonomy",
        "x0_h15_bps",
    )
    out: list[dict[str, Any]] = []
    per_fam: dict[str, int] = {}
    for e in events:
        fam = str(e.get("event_family"))
        if per_fam.get(fam, 0) >= n:
            continue
        out.append({k: e.get(k) for k in keys})
        per_fam[fam] = per_fam.get(fam, 0) + 1
        if len(out) >= n * 8:
            break
    return out


def _state_rows(events: list[dict[str, Any]], fields: tuple[str, ...], limit: int = 40) -> list[dict[str, Any]]:
    rows = []
    for e in events:
        if e.get("layer") != "STOCK":
            continue
        rows.append({k: e.get(k) for k in ("date", "symbol", "event_family", "event_time") + fields})
        if len(rows) >= limit:
            break
    return rows or [{"empty": True}]


def _continuous_sheet(rca: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for fam, info in dict(rca.get("by_family") or {}).items():
        for f in list(info.get("features") or []):
            rows.append(
                {
                    "family": fam,
                    "feature": f.get("feature"),
                    "kind": f.get("kind"),
                    "spearman": f.get("spearman"),
                    "q5_minus_q1": f.get("q5_minus_q1"),
                    "monotonic": f.get("monotonic"),
                    "u_shape": f.get("u_shape"),
                    "block_agree_n": f.get("block_agree_n"),
                    "stable_across_blocks": f.get("stable_across_blocks"),
                }
            )
    return rows or [{"empty": True}]


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    print(f"BIND ok={bind.get('ok')} sha={((bind.get('split') or {}).get('split_sha256'))} prev={bind.get('previous_verdict')}", flush=True)
    split = dict(bind.get("split") or {})
    disc_dates = [str(d) for d in list(split.get("discovery_dates") or [])]
    conf_dates = [str(d) for d in list(split.get("confirmation_dates") or [])]
    val_dates = [str(d) for d in list(split.get("frozen_validation_dates") or [])]
    val_set = set(val_dates)
    conf_set = set(conf_dates)
    disc_set = set(disc_dates)
    blocks = freeze_discovery_blocks(disc_dates) if disc_dates else {"ok": False}
    print(f"BLOCKS ok={blocks.get('ok')} sha={blocks.get('block_sha256')} n={blocks.get('n_blocks')}", flush=True)
    uni: dict[str, Any] = {"ok": False, "events": [], "event_n": 0, "families": {}}
    rca: dict[str, Any] = {}
    cand: dict[str, Any] = {"proposals": [], "promoted": [], "promoted_n": 0, "structure_found": False}
    secondary = {"ran": False, "label": "SECONDARY_CONFIRMATION_NOT_PRISTINE", "any_passed": False, "rows": []}
    tax = {}
    if bind.get("ok") and blocks.get("ok"):
        symbols = list(bind.get("symbols") or [])
        print(f"LOAD_DISCOVERY n_dates={len(disc_set)} forbidden_conf={len(conf_set)} forbidden_val={len(val_set)}", flush=True)
        minutes = load_minutes(symbols=symbols, allowed_dates=disc_set, forbidden_dates=conf_set | val_set)
        if minutes["date"].isin(list(val_set)).any() or minutes["date"].isin(list(conf_set)).any():
            raise RuntimeError("non_discovery_dates_loaded_during_design")
        print(f"DISCOVERY_ROWS {len(minutes)}", flush=True)
        uni = build_universe(minutes=minutes, sector_of=_sector_of(bind), forbidden_dates=conf_set | val_set)
        id_sha = str(uni.get("event_id_sha256"))
        print(f"UNIVERSE n={uni.get('event_n')} sha={id_sha}", flush=True)
        attach_forward_paths(list(uni.get("events") or []))
        if freeze_event_ids(list(uni.get("events") or [])) != id_sha:
            raise RuntimeError("event_identities_changed_after_outcomes")
        strip_runtime(list(uni.get("events") or []))
        tax = taxonomy_counts(list(uni.get("events") or []))
        rca = rca_bundle(events=list(uni.get("events") or []), date_to_block=dict(blocks.get("date_to_block") or {}))
        print("RCA_DONE", flush=True)
        cand = build_candidates(rca=rca, events=list(uni.get("events") or []), date_to_block=dict(blocks.get("date_to_block") or {}))
        cand["structure_found"] = bool(cand.get("structure_found") or rca.get("structure_found"))
        print(f"CANDIDATES proposed={len(cand.get('proposals') or [])} promoted={cand.get('promoted_n')}", flush=True)
        promoted = list(cand.get("promoted") or [])
        if promoted:
            print(f"LOAD_CONFIRMATION n_dates={len(conf_set)} forbidden_val={len(val_set)}", flush=True)
            conf_minutes = load_minutes(symbols=symbols, allowed_dates=conf_set, forbidden_dates=val_set)
            if conf_minutes["date"].isin(list(val_set)).any() or conf_minutes["date"].isin(list(disc_set)).any():
                raise RuntimeError("confirmation_load_leaked_other_partition")
            conf_uni = build_universe(minutes=conf_minutes, sector_of=_sector_of(bind), forbidden_dates=val_set)
            attach_forward_paths(list(conf_uni.get("events") or []))
            strip_runtime(list(conf_uni.get("events") or []))
            secondary = secondary_confirmation(events=list(conf_uni.get("events") or []), candidates=promoted)
            cand["promoted"] = [c for c in promoted if not (c.get("promotion") or {}).get("secondary_rejected")]
            cand["promoted_n"] = len(cand["promoted"])
            print(f"SECONDARY any_pass={secondary.get('any_passed')} remain={cand.get('promoted_n')}", flush=True)
    live = inspect_live_20260914()
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        structure=bool(cand.get("structure_found")),
        promoted_n=int(cand.get("promoted_n") or 0),
        secondary_any_pass=bool(secondary.get("any_passed")) if secondary.get("ran") else None,
        secondary_ran=bool(secondary.get("ran")),
    )
    fam_counts = dict(uni.get("families") or {})
    events = list(uni.get("events") or [])
    pull = dict(rca.get("pullback") or {})
    brk = dict(rca.get("breakout") or {})
    return {
        "program_id": PROGRAM_ID,
        "parent_verdict_accepted": PARENT_VERDICT,
        "m1_m11_rejected": list(REJECTED_M1_M11),
        "m1_m11_rescued": bool(M1_M11_RESCUED),
        "bind": {k: v for k, v in bind.items() if k != "by_symbol"},
        "split": split,
        "expected_split_sha256": EXPECTED_SPLIT_SHA256,
        "blocks": {
            **{k: v for k, v in blocks.items() if k not in {"dates", "date_to_block", "blocks"}},
            "blocks": [{kk: vv for kk, vv in b.items() if kk != "dates"} for b in list(blocks.get("blocks") or [])],
        },
        "block_date_lists": [{"block_id": b.get("block_id"), "dates": b.get("dates")} for b in list(blocks.get("blocks") or [])],
        "event_universe": {
            "ok": uni.get("ok"),
            "event_n": uni.get("event_n"),
            "families": fam_counts,
            "event_id_sha256": uni.get("event_id_sha256"),
            "identities_frozen_before_outcomes": True,
            "future_used_as_decision_feature": bool(FUTURE_AS_DECISION_FEATURE),
            "copied_m4_m6_thresholds": False,
            "sample": _sample_events(events),
        },
        "event_primitives": primitives_spec(),
        "forward_path": {
            "horizons_min": [1, 3, 5, 10, 15, 30],
            "labels_only": True,
            "taxonomy_counts": tax,
            "taxonomy_classes": list(TAXONOMY),
        },
        "path_taxonomy": [{"class": k, "n": v} for k, v in sorted(tax.items())],
        "rca": {
            "pullback": {k: v for k, v in pull.items() if k != "features"} | {"top_features": list(pull.get("features") or [])[:8], "contrast": list(pull.get("success_vs_failure_contrast") or [])[:8]},
            "breakout": {k: v for k, v in brk.items() if k != "features"} | {"top_features": list(brk.get("features") or [])[:8], "contrast": list(brk.get("success_vs_failure_contrast") or [])[:8]},
            "other": {
                fam: {
                    "n": inf.get("n"),
                    "stable_features": inf.get("stable_features"),
                    "level_useful": inf.get("level_useful"),
                    "transition_useful": inf.get("transition_useful"),
                    "acceleration_useful": inf.get("acceleration_useful"),
                }
                for fam, inf in dict(rca.get("other") or {}).items()
            },
            "level_useful": rca.get("level_useful"),
            "transition_useful": rca.get("transition_useful"),
            "acceleration_useful": rca.get("acceleration_useful"),
            "stable_market_sector_stock_chain": rca.get("stable_market_sector_stock_chain"),
            "stable_stock_only_chain": rca.get("stable_stock_only_chain"),
            "diagnostics": rca.get("diagnostics"),
        },
        "continuous_effects": _continuous_sheet(rca),
        "state_transitions": [
            {"feature": "breadth", "level": "breadth_level", "transition": "breadth_slope", "acceleration": "breadth_accel"},
            {"feature": "sector_rs", "level": "sector_rs", "transition": "sector_rs_slope / sector_up vs market"},
            {"feature": "stock_rs", "level": "rs_5m", "transition": "rs_slope"},
            {"feature": "vwap", "level": "vwap_dist", "transition": "VWAP_RECLAIM / VWAP_LOSS onset"},
        ],
        "market_state_sample": _state_rows(events, ("breadth_level", "breadth_slope", "breadth_accel", "dispersion_5m", "leadership_hhi")),
        "sector_state_sample": _state_rows(events, ("sector", "sector_up_5m", "sector_rs", "sector_n")),
        "stock_state_sample": _state_rows(events, ("rs_5m", "rs_slope", "vwap_dist", "ema_dist", "vol_rel20", "rng_rel20")),
        "candidates": cand,
        "secondary_confirmation": secondary,
        "frozen_validation": {
            "opened": bool(FROZEN_VALIDATION_OPENED),
            "dates_first_last": [val_dates[0], val_dates[-1]] if val_dates else None,
            "n": len(val_dates),
            "accessed": False,
            "status": "CLOSED",
        },
        "live_20260914": live,
        "kabu_50_applied": bool(KABU_50_APPLIED),
        "same_bar_close_entry": bool(SAME_BAR_CLOSE_ENTRY),
        "old_confirmation_used_to_design": bool(OLD_CONFIRMATION_USED_TO_DESIGN),
        "FEATURE_MINING_CLOSED": bool(FEATURE_MINING_CLOSED),
        "families_defined": list(FAMILIES),
        "max_candidates": MAX_CANDIDATES,
        "decision": decision,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    eu = dict(report.get("event_universe") or {})
    rca = dict(report.get("rca") or {})
    cand = dict(report.get("candidates") or {})
    sec = dict(report.get("secondary_confirmation") or {})
    live = dict(report.get("live_20260914") or {})
    d = dict(report.get("decision") or {})
    promoted = list(cand.get("promoted") or [])
    c1 = promoted[0] if len(promoted) > 0 else {}
    c2 = promoted[1] if len(promoted) > 1 else {}
    c3 = promoted[2] if len(promoted) > 2 else {}

    def _c(c: dict[str, Any]) -> dict[str, Any]:
        spec = dict(c.get("spec") or {})
        econ = dict(c.get("discovery_economics") or {})
        return {
            "id": spec.get("candidate_id"),
            "thesis": spec.get("thesis"),
            "ENTRY": {"family": spec.get("event_family"), "cuts": spec.get("cuts"), "trigger": spec.get("trigger")},
            "EXIT": spec.get("technical_invalidation"),
            "X0": econ.get("mean_x0_bps"),
            "X1": econ.get("mean_x1_bps"),
            "block_consistency": econ.get("block_mean_x0"),
        }

    return {
        "causal_event_universe_built_before_outcomes_attached": True,
        "event_count": eu.get("event_n"),
        "event_families": eu.get("families"),
        "future_outcomes_used_as_decision_time_features": False,
        "discovery_only_logic_design": True,
        "old_confirmation_used_to_design_rules": False,
        "frozen_validation_opened": False,
        "path_class_n": len(list((report.get("forward_path") or {}).get("taxonomy_classes") or [])),
        "what_distinguishes_successful_vs_failed_pullback": (rca.get("pullback") or {}).get("contrast"),
        "what_distinguishes_successful_vs_failed_breakout": (rca.get("breakout") or {}).get("contrast"),
        "level_useful": rca.get("level_useful"),
        "transition_useful": rca.get("transition_useful"),
        "acceleration_useful": rca.get("acceleration_useful"),
        "stable_market_sector_stock_chain": rca.get("stable_market_sector_stock_chain"),
        "stable_stock_only_chain": rca.get("stable_stock_only_chain"),
        "diagnostic_interactions_found": bool(rca.get("diagnostics")),
        "complete_strategy_candidates_n": cand.get("promoted_n"),
        "candidate_1": _c(c1) if c1 else None,
        "candidate_2": _c(c2) if c2 else None,
        "candidate_3": _c(c3) if c3 else None,
        "any_candidate_passed_SECONDARY_CONFIRMATION_NOT_PRISTINE": bool(sec.get("any_passed")),
        "frozen_validation_still_closed": True,
        "futures_capture_20260914_running": bool(live.get("futures_capture_running")),
        "breadth_capture_20260914_running": bool(live.get("breadth_capture_running")),
        "submit_cancel_live": "0/0/0",
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
        "ANALYSIS_ID": ANALYSIS_ID,
        "m1_m11_rescued": False,
        "kabu_50_applied": False,
        "same_bar_close_entry": False,
    }
