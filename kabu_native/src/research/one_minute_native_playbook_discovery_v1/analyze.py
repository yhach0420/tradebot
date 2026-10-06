"""Native 1-minute atlas and complete playbooks. Discovery only. No paid data. No 5-minute grid."""
from __future__ import annotations

import gc
from typing import Any

from research.identify_minimum_missing_external_causal_information_v1.live import inspect_live_now
from research.one_minute_native_playbook_discovery_v1 import (
    ATLAS_ID,
    CASE_ATLAS,
    CASE_BIND,
    CASE_NONE,
    CASE_PLAYBOOKS,
    HM1_ID,
    NEXT_BIND,
    NEXT_FREEZE_REVIEW,
    NEXT_REASSESS,
    NEXT_SELECT,
    PARENT_VERDICT,
)
from research.one_minute_native_playbook_discovery_v1.atlas import behavior_groups, sector_profiles, sequence_table, symbol_profiles
from research.one_minute_native_playbook_discovery_v1.bind import bind_prior
from research.one_minute_native_playbook_discovery_v1.episodes import build_episodes
from research.one_minute_native_playbook_discovery_v1.panel import build_native_events
from research.one_minute_native_playbook_discovery_v1.playbooks import build_playbooks


def _sector_of(bind: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for sym, row in dict(bind.get("by_symbol") or {}).items():
        out[str(sym)] = str(row.get("tse33_name") or row.get("sector33_name") or "")
    return out


def decide(*, bind_ok: bool, stable_seq_n: int, promoted_n: int) -> dict[str, Any]:
    if not bind_ok:
        return {
            "CASE": "BIND",
            "VERDICT": CASE_BIND,
            "NEXT": NEXT_BIND,
            "INTERPRETATION": "Prior split/block/105/catalog-stop bind failed.",
        }
    if int(promoted_n) >= 1:
        return {
            "CASE": "PLAYBOOKS",
            "VERDICT": CASE_PLAYBOOKS,
            "NEXT": NEXT_FREEZE_REVIEW,
            "INTERPRETATION": "Native 1-minute path atlas produced complete playbooks that clear execution-tax scale on Discovery D1–D4. Frozen Validation remains closed. HM1 was not retuned. No new paid data.",
        }
    if int(stable_seq_n) >= 1:
        return {
            "CASE": "ATLAS",
            "VERDICT": CASE_ATLAS,
            "NEXT": NEXT_SELECT,
            "INTERPRETATION": "ONE_MINUTE_BEHAVIOR_ATLAS_V1 is ready. Recurring native sequences exist, but no complete playbook cleared X1 / D1–D4 / occupancy. Do not promote ENTRY-only. Do not open Frozen Validation. Do not buy data.",
        }
    return {
        "CASE": "NONE",
        "VERDICT": CASE_NONE,
        "NEXT": NEXT_REASSESS,
        "INTERPRETATION": "No stable native 1-minute path mechanism survived Discovery D1–D4. Do not invent indicator grids. Do not resume paid external catalog expansion.",
    }


def _strip_fwd(episodes: list[dict[str, Any]]) -> None:
    for e in episodes:
        e.pop("fwd_bars", None)


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    print(f"BIND ok={bind.get('ok')} pool={bind.get('research_pool_n')} cme={bind.get('cme_verdict')}", flush=True)
    split = dict(bind.get("split") or {})
    blocks = dict(bind.get("blocks") or {})
    disc = set(str(d) for d in list(split.get("discovery_dates") or []))
    conf = set(str(d) for d in list(split.get("confirmation_dates") or []))
    val = set(str(d) for d in list(split.get("frozen_validation_dates") or []))
    date_to_block = dict(blocks.get("date_to_block") or {})
    symbols = list(bind.get("symbols") or [])
    sector_of = _sector_of(bind)
    native = {"ok": False, "events": [], "opening": [], "lead_min": [], "event_n": 0, "day_n": 0}
    eps_pack = {"episodes": [], "episode_n": 0, "raw_event_n": 0}
    sym_rows: list[dict[str, Any]] = []
    sec_rows: list[dict[str, Any]] = []
    seq_rows: list[dict[str, Any]] = []
    groups: dict[str, Any] = {"groups": [], "n": 0}
    plays = {"proposals": [], "promoted": [], "promoted_n": 0}
    if bind.get("ok"):
        native = build_native_events(
            symbols=symbols,
            sector_of=sector_of,
            allowed=disc,
            forbidden=conf | val,
            date_to_block=date_to_block,
        )
        print(f"EVENTS n={native.get('event_n')} days={native.get('day_n')}", flush=True)
        eps_pack = build_episodes(list(native.get("events") or []))
        print(f"EPISODES n={eps_pack.get('episode_n')}", flush=True)
        native["events"] = []
        gc.collect()
        episodes = list(eps_pack.get("episodes") or [])
        sym_rows = symbol_profiles(
            episodes=episodes,
            opening=list(native.get("opening") or []),
            lead_min=list(native.get("lead_min") or []),
            sector_of=sector_of,
            symbols=symbols,
        )
        sec_rows = sector_profiles(sym_rows, episodes)
        seq_rows = sequence_table(episodes)
        groups = behavior_groups(sym_rows)
        print("ATLAS_DONE", flush=True)
        plays = build_playbooks(episodes=episodes, seq_rows=seq_rows, date_to_block=date_to_block)
        print(f"PLAYBOOKS promoted={plays.get('promoted_n')}", flush=True)
        _strip_fwd(episodes)
        eps_pack["episodes"] = []
        gc.collect()
    live = inspect_live_now()
    stable = [r for r in seq_rows if r.get("d1_d4_x0_agree") and int(r.get("n") or 0) >= 80]
    decision = decide(bind_ok=bool(bind.get("ok")), stable_seq_n=len(stable), promoted_n=int(plays.get("promoted_n") or 0))
    leaders = [r["symbol"] for r in sym_rows if (r.get("lead_minus_lag") or 0) > 0.02 and (r.get("lead_frac") or 0) >= 0.08]
    laggards = [r["symbol"] for r in sym_rows if (r.get("lead_minus_lag") or 0) < -0.02 and (r.get("lag_frac") or 0) >= 0.08]
    cont = [r["symbol"] for r in sym_rows if r.get("behavior_group") == "CONTINUATION"]
    meanrev = [r["symbol"] for r in sym_rows if r.get("behavior_group") == "MEAN_REVERSION"]
    opening = [r["symbol"] for r in sym_rows if r.get("behavior_group") == "OPENING_MOMENTUM"]
    catchup = [r["symbol"] for r in sym_rows if r.get("behavior_group") == "SECTOR_LAGGARD_CATCHUP"]
    unsupported = [r["symbol"] for r in sym_rows if r.get("behavior_group") == "IDIOSYNCRATIC" or int(r.get("episode_n") or 0) < 20]
    fav_seq = sorted(seq_rows, key=lambda r: (-(r.get("favor_first_p") or 0), -(r.get("n") or 0)))
    fail_seq = sorted(seq_rows, key=lambda r: ((r.get("favor_first_p") or 1), -(r.get("n") or 0)))
    return {
        "atlas_id": ATLAS_ID,
        "parent_verdict_accepted": PARENT_VERDICT,
        "bind": {k: v for k, v in bind.items() if k not in {"by_symbol", "split"}},
        "split_sha256": (bind.get("split") or {}).get("split_sha256"),
        "block_sha256": (bind.get("blocks") or {}).get("block_sha256"),
        "native_meta": {k: native.get(k) for k in ("ok", "day_n", "event_n", "used_5m_grid", "bar_start")},
        "episode_meta": {k: eps_pack.get(k) for k in ("episode_n", "raw_event_n", "gap_min", "future_in_boundary")},
        "symbol_profiles": sym_rows,
        "sector_profiles": sec_rows,
        "sequences": seq_rows,
        "stable_sequences": stable,
        "behavior_groups": groups,
        "playbooks": plays,
        "hm1_reference": bind.get("hm1_reference"),
        "leaders": leaders,
        "laggards": laggards,
        "continuation_symbols": cont,
        "mean_reversion_symbols": meanrev,
        "opening_symbols": opening,
        "catchup_symbols": catchup,
        "unsupported_symbols": unsupported,
        "best_favor_sequences": fav_seq[:8],
        "worst_favor_sequences": fail_seq[:8],
        "external_optional": {
            "fx_map": bind.get("fx_response_map_v1"),
            "hkg_closed_as": bind.get("hkg_closed_as"),
            "required_for_playbook": False,
            "tested_as_condition": False,
        },
        "live_20260914": live,
        "kabu_50_applied": False,
        "old_confirmation_used_to_design": False,
        "frozen_validation_opened": False,
        "hm1_tuned": False,
        "new_paid_data": False,
        "five_minute_grid": False,
        "playbooks_built": int(plays.get("promoted_n") or 0) > 0,
        "purchase": False,
        "decision": decision,
        "hm1_id": HM1_ID,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    plays = dict(report.get("playbooks") or {})
    promoted = list(plays.get("promoted") or [])
    seq = list(report.get("stable_sequences") or [])
    groups = dict(report.get("behavior_groups") or {})
    hm1 = dict(report.get("hm1_reference") or {})
    econ0 = dict((promoted[0].get("economics") or {}) if promoted else {})
    hm1_x0 = hm1.get("mean_x0_bps")
    best_x0 = econ0.get("mean_x0_bps")
    hm1_still = None
    if hm1_x0 is not None and best_x0 is not None:
        hm1_still = float(hm1_x0) >= float(best_x0)
    elif hm1_x0 is not None and not promoted:
        hm1_still = True
    return {
        "stable_symbol_sector_behavior_mechanisms_n": len(seq),
        "stable_sequences": [r.get("sequence") for r in seq],
        "stocks_repeatedly_lead_sector": report.get("leaders"),
        "stocks_repeatedly_lag_then_catch_up": report.get("catchup_symbols") or report.get("laggards"),
        "continuation_type": report.get("continuation_symbols"),
        "mean_reversion_type": report.get("mean_reversion_symbols"),
        "stable_opening_behavior": report.get("opening_symbols"),
        "largest_favorable_first_sequences": [
            {"sequence": r.get("sequence"), "favor_first_p": r.get("favor_first_p"), "n": r.get("n")}
            for r in list(report.get("best_favor_sequences") or [])[:5]
        ],
        "consistently_failing_sequences": [
            {"sequence": r.get("sequence"), "favor_first_p": r.get("favor_first_p"), "n": r.get("n")}
            for r in list(report.get("worst_favor_sequences") or [])[:5]
        ],
        "behavior_groups_n": groups.get("n"),
        "behavior_group_members": groups.get("members"),
        "complete_playbook_candidates_clearing_execution_n": int(plays.get("promoted_n") or 0),
        "promoted_ids": [(p.get("spec") or {}).get("candidate_id") for p in promoted],
        "X0": econ0.get("mean_x0_bps"),
        "X1": econ0.get("mean_x1_bps"),
        "PF": econ0.get("profit_factor"),
        "trade_N": econ0.get("trade_n"),
        "D1_D4": econ0.get("block_mean_x0"),
        "HM1_remains_competitive": hm1_still,
        "HM1_reference_x0": hm1.get("mean_x0_bps"),
        "HM1_reference_x1": hm1.get("mean_x1_bps"),
        "unsupported_stock_n": len(list(report.get("unsupported_symbols") or [])),
        "unsupported_stocks": report.get("unsupported_symbols"),
        "external_driver_required_for_any_playbook": False,
        "ENTRY_playbooks_built": bool(report.get("playbooks_built")),
        "Frozen_Validation_opened": False,
        "Old_Confirmation_used_to_design": False,
        "New_paid_data": False,
        "five_minute_grid": False,
        "submit_cancel_live": "0/0/0",
        "atlas_id": report.get("atlas_id"),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
    }
