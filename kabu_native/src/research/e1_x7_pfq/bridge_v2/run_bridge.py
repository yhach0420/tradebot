"""E1_X7 PFQ Realizability Bridge Audit V2 orchestrator."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from research.e1_x6_fcrr.replay import _universe_from_manifest, load_day_events, load_source_manifest
from research.e1_x6_provisional.util import sha256_obj
from research.e1_x6_taer.exit_joint_audit import load_entry_observations
from research.e1_x6_taer.failure_source.clusters import load_episodes
from research.e1_x7_pfq.candidates import passes_candidate
from research.e1_x7_pfq.config import DAYS
from research.e1_x7_pfq.feature_contract import run_phase0_audit
from research.e1_x7_pfq.joint import replay_pair
from research.e1_x7_pfq.run_study import _load_pullback_universe

from . import (
    ANALYSIS_ID,
    FROZEN_PAIRS,
    FROZEN_THRESHOLDS,
    HARD_EXITS,
    KNOWN_COUNTS,
    SOFT_EXITS,
    SOURCE_RUN,
)
from .classify import classify_failure, counterfactual_after_soft_exit, scan_hard_times
from .paths import (
    adverse_before,
    build_event_time_path,
    build_fixed_grid_path,
    first_touch_bundle,
)
from .precommit import build_precommit
from .stats import (
    FT_KEYS,
    bootstrap_difference,
    compare_sets,
    entry_path_supported,
    observation_density_proxy,
    rate_plus_first,
    volatility_proxy_only,
)

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[4]
SOURCE_STORE = Path.home() / "e1x6_research_store" / "e1_x7_pfq" / SOURCE_RUN
PUBLISH = NATIVE / "results" / "research" / "e1_x7_pfq_bridge_v2"


def _session_of(ts: datetime) -> str:
    return "AM" if ts.hour < 12 else "PM"


def _safety() -> dict[str, Any]:
    return {
        "submit_cancel_live": "0/0/0",
        "mainline_changed": False,
        "production_yaml_changed": False,
        "e1_x5_changed": False,
        "pbv2_changed": False,
        "taer_v1_changed": False,
        "pfq_conditions_changed": False,
        "unused_data_used": False,
        "prospective": False,
        "shadow": False,
        "forward": False,
        "paper": False,
        "discord": False,
    }


def _strip_points(path: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in path.items() if k != "points"}
    return out


def _bid_ask_stream(events: list, *, sym: str, session: str) -> list[tuple[float, float, float, float]]:
    """(t, bid, ask, mid) same symbol+session only."""
    out = []
    for t, s, row in events:
        if s != sym:
            continue
        if _session_of(row["ts"]) != session:
            continue
        bid, ask = float(row["bid"]), float(row["ask"])
        out.append((float(t), bid, ask, 0.5 * (bid + ask)))
    return out


def _load_frozen_pair_summary() -> dict[str, Any]:
    rep = json.loads((SOURCE_STORE / "report.json").read_text(encoding="utf-8"))
    return rep.get("pairs") or {}


def _pair_summary_from_replay(res: dict[str, Any]) -> dict[str, Any]:
    return {
        "n_pass": res["n_pass"],
        "pnl": res["pnl"],
        "pf": res["pf"],
        "exit_reason_counts": res["exit_reason_counts"],
        "day_pnl": res["day_pnl"],
    }


def _approx_eq(a: Any, b: Any, tol: float = 1e-6) -> bool:
    if a is None and b is None:
        return True
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= tol
    if isinstance(a, dict) and isinstance(b, dict):
        if set(a) != set(b):
            return False
        return all(_approx_eq(a[k], b[k], tol) for k in a)
    return a == b


def _enrichment_with_bootstrap(cand: list, parent: list, mode: str) -> dict[str, Any]:
    base = compare_sets(cand, parent, mode=mode)
    for k in FT_KEYS:
        mkey = f"{k}_rate"
        boot = bootstrap_difference(
            cand, parent, mode=mode, metric_key=mkey,
            rate_fn=lambda rows, kk=k, md=mode: rate_plus_first(rows, kk, md),
        )
        base["metrics"][mkey]["bootstrap"] = boot
    return base


def _concentration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"top_symbol": None, "top_share": 0.0, "ex_top1_n": 0, "symbol_share": {}}
    c = Counter(r["symbol"] for r in rows)
    n = len(rows)
    top, top_n = c.most_common(1)[0]
    return {
        "top_symbol": top,
        "top_share": top_n / n,
        "ex_top1_n": n - top_n,
        "symbol_share": {s: v / n for s, v in c.most_common()},
    }


def _build_episode_rows(
    audits: list[dict],
    entry_by: dict,
    events_by_day: dict,
    thr: dict,
) -> tuple[list[dict], dict]:
    rows = []
    identity_fail = None
    for a in audits:
        eid = a["episode_id"]
        src = entry_by.get(eid) or {}
        entry_t = float(src.get("entry_t") or a.get("decision_time") or a.get("entry_time"))
        entry_ask = float(src.get("entry_ask") or a.get("entry_ask") or 0)
        day = a["day"]
        sym = a["symbol"]
        session = a.get("session") or src.get("session")
        reclaim = float(src.get("reclaim_level") or entry_ask)
        pb = src.get("pullback_low")
        stream = _bid_ask_stream(events_by_day[day], sym=sym, session=session)
        bid_events = [(t, b) for t, b, _a, _m in stream]
        # session end from first non-session event after entry
        session_end = entry_t + 10_000.0
        for t, s, row in events_by_day[day]:
            if s != sym:
                continue
            if float(t) < entry_t - 1e-12:
                continue
            if _session_of(row["ts"]) != session:
                session_end = float(t)
                break
        else:
            same = [t for t, s, row in events_by_day[day] if s == sym and _session_of(row["ts"]) == session]
            if same:
                session_end = max(same) + 1e-6

        hard = scan_hard_times(
            events_by_day[day],
            sym=sym, session=session, entry_t=entry_t, entry_ask=entry_ask,
            reclaim_level=reclaim, pullback_low=pb,
        )
        end_t = entry_t + 300.0
        ev = build_event_time_path(
            entry_ask=entry_ask, entry_t=entry_t, end_t=end_t,
            session_end=session_end, bid_events=bid_events,
        )
        fg = build_fixed_grid_path(
            entry_ask=entry_ask, entry_t=entry_t, end_t=end_t,
            session_end=session_end, bid_events=bid_events,
        )
        ev_ft = first_touch_bundle(ev)
        fg_ft = first_touch_bundle(fg)
        path_ok = bool(ev.get("evaluable") or fg.get("evaluable"))
        mem = {
            "PFQ_UPDATE_Q70": passes_candidate(a, "PFQ_UPDATE_Q70", thr),
            "PFQ_FLOW_Q30": passes_candidate(a, "PFQ_FLOW_Q30", thr),
            "PFQ_JOINT": passes_candidate(a, "PFQ_JOINT", thr),
        }
        update_parent = a.get("price_update_count_10s") is not None and path_ok
        flow_parent = bool(a.get("ratio_valid")) and int(a.get("classified_trade_count_30s") or 0) >= 3 and path_ok
        joint_parent = update_parent and flow_parent

        row = {
            "episode_id": eid,
            "cluster_id": a.get("cluster_id") or src.get("cluster_id"),
            "day": day,
            "session": session,
            "symbol": sym,
            "entry_time": entry_t,
            "entry_ask": entry_ask,
            "entry_best_ask": entry_ask,
            "reclaim_level": reclaim,
            "pullback_low": pb,
            "price_update_count_10s": a.get("price_update_count_10s"),
            "uptick_volume_ratio_30s": a.get("uptick_volume_ratio_30s"),
            "ratio_valid": a.get("ratio_valid"),
            "classified_trade_count_30s": a.get("classified_trade_count_30s"),
            "membership": mem,
            "update_eligible_parent": update_parent,
            "flow_eligible_parent": flow_parent,
            "joint_eligible_parent": joint_parent,
            "path_evaluable": path_ok,
            "event_time": _strip_points(ev),
            "fixed_grid": _strip_points(fg),
            "event_time_ft": ev_ft,
            "fixed_grid_ft": fg_ft,
            # aliases for stats helpers
            "event_time_ft_alias": True,
            "hard": hard,
            "event_plus5_before_minus10": ev_ft["plus5_vs_minus10"] == "PLUS_FIRST",
            "fixed_plus5_before_minus10": fg_ft["plus5_vs_minus10"] == "PLUS_FIRST",
            "event_plus5_before_minus15": ev_ft["plus5_vs_minus15"] == "PLUS_FIRST",
            "fixed_plus5_before_minus15": fg_ft["plus5_vs_minus15"] == "PLUS_FIRST",
            "event_plus10_before_minus10": ev_ft["plus10_vs_minus10"] == "PLUS_FIRST",
            "fixed_plus10_before_minus10": fg_ft["plus10_vs_minus10"] == "PLUS_FIRST",
            "event_plus10_before_minus15": ev_ft["plus10_vs_minus15"] == "PLUS_FIRST",
            "fixed_plus10_before_minus15": fg_ft["plus10_vs_minus15"] == "PLUS_FIRST",
            "time_to_plus5": fg.get("time_to_net_plus5_sec"),
            "time_to_plus10": fg.get("time_to_net_plus10_sec"),
            "adverse_before_plus5": adverse_before(ev.get("points") or [], ev.get("t_plus5")),
            "adverse_before_plus10": adverse_before(ev.get("points") or [], ev.get("t_plus10")),
            "reclaim_break_time": hard.get("reclaim_break_time"),
            "pullback_low_break_time": hard.get("pullback_low_break_time"),
            "hard_stop_time": hard.get("hard_stop_time"),
            "session_end_time": hard.get("session_end_time"),
            "max_hold_deadline": hard.get("max_hold_deadline"),
            "_bid_events": bid_events,
            "_ev_points": ev.get("points"),
            "_fg_points": fg.get("points"),
        }
        # stats module expects mode_ft key naming: event_time / fixed_grid with _ft
        row["event_time_ft"] = ev_ft  # already
        # rate_plus_first looks up f"{mode}_ft" — mode is "event_time" or "fixed_grid"
        rows.append(row)

    # fix aliases for compare_sets: it uses r.get(f"{mode}_ft")
    for r in rows:
        r["event_time"] = r["event_time"]  # path dict
        # rate_net_reached uses r.get(mode) for path — mode event_time/fixed_grid OK
        pass

    return rows, identity_fail


def run_once(*, label: str = "A") -> dict[str, Any]:
    run_id = f"e1x7_pfq_bridge_v2_{datetime.now(JST).strftime('%Y%m%d_%H%M%S')}_{label}"
    store = Path.home() / "e1x6_research_store" / "e1_x7_pfq_bridge_v2" / run_id
    store.mkdir(parents=True, exist_ok=True)

    print(f"=== [{label}] Precommit (before outcomes) ===", flush=True)
    precommit = build_precommit()
    (store / "precommit.json").write_text(json.dumps(precommit, indent=2), encoding="utf-8")
    print("precommit_sha", precommit["precommit_sha256"], flush=True)

    print(f"=== [{label}] Load events + universe ===", flush=True)
    sm = load_source_manifest()
    events_by_day = {}
    for day in DAYS:
        print("  preload", day, flush=True)
        events_by_day[day] = load_day_events(day, _universe_from_manifest(sm, day))

    universe = _load_pullback_universe()
    episodes_raw, _, _ = load_episodes()
    ep_by = {e["episode_id"]: e for e in episodes_raw}
    audits, phase0 = run_phase0_audit(universe, events_by_day, ep_by)

    entries_all, _ = load_entry_observations()
    entry_by = {e["episode_id"]: e for e in entries_all if e["setup_type"] == "PULLBACK_RECLAIM"}
    for a in audits:
        e = entry_by.get(a["episode_id"])
        if e:
            a["entry_ask"] = e["entry_ask"]
            a["reclaim_level"] = e.get("reclaim_level")
            a["pullback_low"] = e.get("pullback_low")
            a["entry_time"] = e["entry_t"]
            a["decision_time"] = e["entry_t"]
            a["cluster_id"] = a.get("cluster_id") or e.get("cluster_id")

    thr = dict(FROZEN_THRESHOLDS)
    thr["price_update_count_10s_q70"] = FROZEN_THRESHOLDS["price_update_count_10s_q70"]
    thr["uptick_volume_ratio_30s_q30"] = FROZEN_THRESHOLDS["uptick_volume_ratio_30s_q30"]

    print(f"=== [{label}] Identity lock ===", flush=True)
    n_all = len(audits)
    mem_counts = {
        "ALL_PULLBACK": n_all,
        "PFQ_UPDATE_Q70": sum(1 for a in audits if passes_candidate(a, "PFQ_UPDATE_Q70", thr)),
        "PFQ_FLOW_Q30": sum(1 for a in audits if passes_candidate(a, "PFQ_FLOW_Q30", thr)),
        "PFQ_JOINT": sum(1 for a in audits if passes_candidate(a, "PFQ_JOINT", thr)),
    }
    if mem_counts != KNOWN_COUNTS:
        return {
            "run_id": run_id,
            "verdict": "E1_X7_PFQ_BRIDGE_IDENTITY_MISMATCH",
            "mem_counts": mem_counts,
            "known": KNOWN_COUNTS,
            "precommit": precommit,
            "safety": _safety(),
            "stop": True,
        }

    episode_ids = sorted(a["episode_id"] for a in audits)
    cluster_ids = sorted({str(a.get("cluster_id")) for a in audits})
    membership = {
        cid: sorted(a["episode_id"] for a in audits if passes_candidate(a, cid, thr))
        for cid in ("PFQ_UPDATE_Q70", "PFQ_FLOW_Q30", "PFQ_JOINT")
    }
    feature_table = sorted(
        (
            a["episode_id"],
            a.get("price_update_count_10s"),
            a.get("uptick_volume_ratio_30s"),
            a.get("ratio_valid"),
            a.get("classified_trade_count_30s"),
        )
        for a in audits
    )
    identity = {
        "episode_identity_sha": sha256_obj(episode_ids),
        "cluster_identity_sha": sha256_obj(cluster_ids),
        "candidate_membership_sha": sha256_obj(membership),
        "feature_table_sha": sha256_obj(feature_table),
        "path_source_sha": sha256_obj({"days": list(DAYS), "source_run": SOURCE_RUN, "contract": "canonical_board"}),
        "counts": mem_counts,
    }
    print("identity", identity["episode_identity_sha"][:16], flush=True)

    print(f"=== [{label}] Event-time + fixed-grid paths ===", flush=True)
    rows, _ = _build_episode_rows(audits, entry_by, events_by_day, thr)
    # attach mode keys expected by stats
    for r in rows:
        r["event_time_ft"] = r["event_time_ft"]
        r["fixed_grid_ft"] = r["fixed_grid_ft"]

    print(f"=== [{label}] Matched parents + enrichment ===", flush=True)
    parents = {
        "UPDATE_ELIGIBLE_PARENT": [r for r in rows if r["update_eligible_parent"]],
        "FLOW_ELIGIBLE_PARENT": [r for r in rows if r["flow_eligible_parent"]],
        "JOINT_ELIGIBLE_PARENT": [r for r in rows if r["joint_eligible_parent"]],
        "ALL_PULLBACK": rows,
    }
    cands = {
        "PFQ_UPDATE_Q70": [r for r in rows if r["membership"]["PFQ_UPDATE_Q70"]],
        "PFQ_FLOW_Q30": [r for r in rows if r["membership"]["PFQ_FLOW_Q30"]],
        "PFQ_JOINT": [r for r in rows if r["membership"]["PFQ_JOINT"]],
    }
    matched_map = {
        "PFQ_UPDATE_Q70": "UPDATE_ELIGIBLE_PARENT",
        "PFQ_FLOW_Q30": "FLOW_ELIGIBLE_PARENT",
        "PFQ_JOINT": "JOINT_ELIGIBLE_PARENT",
    }
    enrichment: dict[str, Any] = {}
    for cid, parent_name in matched_map.items():
        enrichment[cid] = {
            "matched_parent": parent_name,
            "n_candidate": len(cands[cid]),
            "n_parent": len(parents[parent_name]),
            "event_time": _enrichment_with_bootstrap(cands[cid], parents[parent_name], "event_time"),
            "fixed_grid": _enrichment_with_bootstrap(cands[cid], parents[parent_name], "fixed_grid"),
            "all_pullback_ref": {
                "event_time": compare_sets(cands[cid], parents["ALL_PULLBACK"], mode="event_time"),
                "fixed_grid": compare_sets(cands[cid], parents["ALL_PULLBACK"], mode="fixed_grid"),
            },
            "concentration": _concentration(cands[cid]),
        }

    print(f"=== [{label}] Replay 4 frozen pairs ===", flush=True)
    # Build entry dicts for replay (same fields as original)
    def to_entries(cid: str) -> list[dict]:
        out = []
        for r in cands[cid]:
            out.append({
                "episode_id": r["episode_id"],
                "cluster_id": r["cluster_id"],
                "day": r["day"],
                "session": r["session"],
                "symbol": r["symbol"],
                "entry_time": r["entry_time"],
                "entry_ask": r["entry_ask"],
                "reclaim_level": r["reclaim_level"],
                "pullback_low": r["pullback_low"],
                "price_update_count_10s": r["price_update_count_10s"],
                "path_complete": r["path_evaluable"],
                "ratio_valid": r["ratio_valid"],
            })
        return out

    frozen = _load_frozen_pair_summary()
    joint_results = {}
    joint_trades = []
    for cid, xc in FROZEN_PAIRS:
        res = replay_pair(to_entries(cid), candidate_id=cid, exit_candidate=xc, events_by_day=events_by_day)
        joint_results[f"{cid}|{xc}"] = res
        for t in res["trades"]:
            joint_trades.append({
                "pair_id": t["pair_id"],
                "candidate_id": t["candidate_id"],
                "exit_candidate": t["exit_candidate"],
                "episode_id": t["episode_id"],
                "cluster_id": t.get("cluster_id"),
                "day": t["day"],
                "session": t.get("session"),
                "symbol": t["symbol"],
                "entry_time": t.get("entry_time"),
                "exit_time": t.get("exit_time"),
                "hold_sec": t.get("hold_sec"),
                "entry_best_ask": t.get("entry_ask"),
                "exit_best_bid": t.get("exit_bid"),
                "exit_net_pnl_bps": t.get("net_bps"),
                "exit_net_pnl_yen": t.get("net_pnl_yen"),
                "exit_reason": t.get("exit_reason"),
                "integrity_status": t.get("integrity_status"),
            })

    # Identity match vs frozen
    mismatch = []
    for pid, res in joint_results.items():
        fr = frozen.get(pid) or {}
        got = _pair_summary_from_replay(res)
        exp = {
            "n_pass": fr.get("n_pass"),
            "pnl": fr.get("pnl"),
            "pf": fr.get("pf"),
            "exit_reason_counts": fr.get("exit_reason_counts"),
            "day_pnl": fr.get("day_pnl"),
        }
        if not (
            got["n_pass"] == exp["n_pass"]
            and _approx_eq(got["pnl"], exp["pnl"], 1e-4)
            and _approx_eq(got["pf"], exp["pf"], 1e-9)
            and got["exit_reason_counts"] == exp["exit_reason_counts"]
            and _approx_eq(got["day_pnl"], exp["day_pnl"], 1e-4)
        ):
            mismatch.append({"pair_id": pid, "got": got, "expected": exp})

    if mismatch:
        return {
            "run_id": run_id,
            "verdict": "E1_X7_PFQ_JOINT_REPLAY_IDENTITY_MISMATCH",
            "mismatch": mismatch,
            "identity": identity,
            "precommit": precommit,
            "safety": _safety(),
            "stop": True,
        }

    print(f"=== [{label}] Counterfactual + failure classification ===", flush=True)
    by_ep = {r["episode_id"]: r for r in rows}
    failure_rows = []
    capture_rows = []
    cf_rows = []
    for jt in joint_trades:
        if jt.get("integrity_status") != "PASS":
            continue
        r = by_ep.get(jt["episode_id"])
        if not r:
            continue
        hard = r["hard"]
        cf = counterfactual_after_soft_exit(
            exit_reason=jt["exit_reason"],
            exit_time=float(jt["exit_time"]),
            entry_ask=float(r["entry_ask"]),
            hard=hard,
            bid_events=r["_bid_events"],
        )
        cf_rows.append({**cf, "episode_id": jt["episode_id"], "pair_id": jt["pair_id"], "exit_reason": jt["exit_reason"]})
        cls = classify_failure(
            path=r["fixed_grid"],
            ft=r["fixed_grid_ft"],
            hard=hard,
            trade={**jt, "net_bps": jt.get("exit_net_pnl_bps")},
            cf=cf,
        )
        failure_rows.append({
            "pair_id": jt["pair_id"],
            "candidate_id": jt["candidate_id"],
            "episode_id": jt["episode_id"],
            "day": jt["day"],
            "symbol": jt["symbol"],
            "exit_reason": jt["exit_reason"],
            "failure_class": cls,
            "is_hard_exit": jt["exit_reason"] in HARD_EXITS,
            "is_soft_exit": jt["exit_reason"] in SOFT_EXITS,
        })
        best = r["fixed_grid"].get("best_net_pnl_bps_300s")
        realized = jt.get("exit_net_pnl_bps")
        if best is not None and float(best) > 0 and realized is not None:
            capture_rows.append({
                "pair_id": jt["pair_id"],
                "episode_id": jt["episode_id"],
                "best_net_pnl_bps_300s": float(best),
                "realized_net_pnl_bps": float(realized),
                "missed_bps": float(best) - float(realized),
                "capture_ratio": float(realized) / float(best),
            })

    # Path quality sheet rows (lightweight)
    path_quality = []
    for r in rows:
        for cid, flag in r["membership"].items():
            if not flag:
                continue
            path_quality.append({
                "candidate_id": cid,
                "episode_id": r["episode_id"],
                "cluster_id": r["cluster_id"],
                "day": r["day"],
                "session": r["session"],
                "symbol": r["symbol"],
                "event_plus5_before_minus10": r["event_plus5_before_minus10"],
                "fixed_plus5_before_minus10": r["fixed_plus5_before_minus10"],
                "event_plus5_before_minus15": r["event_plus5_before_minus15"],
                "fixed_plus5_before_minus15": r["fixed_plus5_before_minus15"],
                "event_plus10_before_minus10": r["event_plus10_before_minus10"],
                "fixed_plus10_before_minus10": r["fixed_plus10_before_minus10"],
                "event_plus10_before_minus15": r["event_plus10_before_minus15"],
                "fixed_plus10_before_minus15": r["fixed_plus10_before_minus15"],
                "time_to_plus5": r["time_to_plus5"],
                "time_to_plus10": r["time_to_plus10"],
                "adverse_before_plus5": r["adverse_before_plus5"],
                "adverse_before_plus10": r["adverse_before_plus10"],
                "reclaim_break_time": r["reclaim_break_time"],
                "pullback_low_break_time": r["pullback_low_break_time"],
                "hard_stop_time": r["hard_stop_time"],
                "session_end_time": r["session_end_time"],
                "max_hold_deadline": r["max_hold_deadline"],
            })

    print(f"=== [{label}] Verdict ===", flush=True)
    verdict = _decide_verdict(enrichment, failure_rows, cands)

    # SHAs for determinism
    det_payload = {
        "identity": identity,
        "membership": membership,
        "matched_parent_counts": {k: len(v) for k, v in parents.items()},
        "event_outcomes": [
            (r["episode_id"], r["event_time"].get("best_net_pnl_bps_300s"), r["event_time_ft"])
            for r in sorted(rows, key=lambda x: x["episode_id"])
        ],
        "fixed_outcomes": [
            (r["episode_id"], r["fixed_grid"].get("best_net_pnl_bps_300s"), r["fixed_grid_ft"])
            for r in sorted(rows, key=lambda x: x["episode_id"])
        ],
        "first_touch": [
            (r["episode_id"], r["event_time_ft"], r["fixed_grid_ft"])
            for r in sorted(rows, key=lambda x: x["episode_id"])
        ],
        "counterfactual": sorted(
            [(c["episode_id"], c["pair_id"], c.get("label")) for c in cf_rows]
        ),
        "failures": sorted(
            [(f["pair_id"], f["episode_id"], f["failure_class"]) for f in failure_rows]
        ),
        "verdict": verdict["verdict"],
    }
    shas = {
        "identity_sha": identity["episode_identity_sha"],
        "candidate_membership_sha": identity["candidate_membership_sha"],
        "matched_parent_sha": sha256_obj(det_payload["matched_parent_counts"]),
        "event_time_outcome_sha": sha256_obj(det_payload["event_outcomes"]),
        "fixed_grid_outcome_sha": sha256_obj(det_payload["fixed_outcomes"]),
        "first_touch_sha": sha256_obj(det_payload["first_touch"]),
        "counterfactual_sha": sha256_obj(det_payload["counterfactual"]),
        "failure_classification_sha": sha256_obj(det_payload["failures"]),
        "verdict": verdict["verdict"],
    }

    # drop heavy caches before return
    for r in rows:
        r.pop("_bid_events", None)
        r.pop("_ev_points", None)
        r.pop("_fg_points", None)

    report = {
        "analysis_id": ANALYSIS_ID,
        "run_id": run_id,
        "label": label,
        "source_run": SOURCE_RUN,
        "generated_at_jst": datetime.now(JST).isoformat(),
        "precommit": precommit,
        "identity": identity,
        "phase0_status": phase0.get("status"),
        "pfq_joint": {
            "label": "PFQ_DESIGN_SUPPORT_INSUFFICIENT",
            "actual_support": 41,
            "precommitted_minimum": 50,
            "economic_pairs_run": False,
        },
        "prospective": {"status": "BLOCKED_PENDING_REALIZABILITY_BRIDGE_AUDIT"},
        "matched_parents": {k: len(v) for k, v in parents.items()},
        "candidate_enrichment": enrichment,
        "joint_replay": {
            pid: {k: v for k, v in res.items() if k != "trades"}
            for pid, res in joint_results.items()
        },
        "joint_replay_identity": "MATCH",
        "failure_class_counts": dict(Counter(f["failure_class"] for f in failure_rows)),
        "hard_exit_trade_counts": dict(Counter(f["exit_reason"] for f in failure_rows if f["is_hard_exit"])),
        "soft_exit_trade_counts": dict(Counter(f["exit_reason"] for f in failure_rows if f["is_soft_exit"])),
        "verdict_detail": verdict,
        "verdict": verdict["verdict"],
        "determinism_shas": shas,
        "safety": _safety(),
        "stop": True,
        "artifacts": {
            "path_quality_n": len(path_quality),
            "joint_trades_n": len(joint_trades),
            "failure_n": len(failure_rows),
            "capture_n": len(capture_rows),
            "cf_n": len(cf_rows),
        },
        "_sheets": {
            "PathQuality": path_quality,
            "JointTrades": joint_trades,
            "FailureClassification": failure_rows,
            "CaptureMetrics": capture_rows,
            "Counterfactual": cf_rows,
            "EventTimeOutcome": [
                {"episode_id": r["episode_id"], **r["event_time"], **{f"ft_{k}": v for k, v in r["event_time_ft"].items()}}
                for r in rows
            ],
            "FixedGridOutcome": [
                {"episode_id": r["episode_id"], **r["fixed_grid"], **{f"ft_{k}": v for k, v in r["fixed_grid_ft"].items()}}
                for r in rows
            ],
        },
    }
    (store / "report_raw.json").write_text(
        json.dumps({k: v for k, v in report.items() if k != "_sheets"}, indent=2, default=str),
        encoding="utf-8",
    )
    return report


def _decide_verdict(enrichment: dict, failure_rows: list, cands: dict) -> dict[str, Any]:
    # Evaluate primary candidates UPDATE and FLOW (reachable); JOINT reference only
    density_hits = []
    vol_hits = []
    entry_support = {}
    for cid in ("PFQ_UPDATE_Q70", "PFQ_FLOW_Q30", "PFQ_JOINT"):
        en = enrichment[cid]
        if observation_density_proxy(en["event_time"], en["fixed_grid"]):
            density_hits.append(cid)
        if volatility_proxy_only(en["fixed_grid"], en["event_time"]):
            vol_hits.append(cid)
        ok, reasons = entry_path_supported(en)
        entry_support[cid] = {"supported": ok, "reasons": reasons}

    # Prefer density / vol proxy if ANY reachable candidate shows it without fixed-grid entry support
    any_entry = any(entry_support[c]["supported"] for c in ("PFQ_UPDATE_Q70", "PFQ_FLOW_Q30"))

    if density_hits and not any_entry:
        return {
            "verdict": "E1_X7_PFQ_OBSERVATION_DENSITY_PROXY_ONLY",
            "pfq_close": True,
            "exit_revision": False,
            "density_hits": density_hits,
            "entry_support": entry_support,
        }
    if vol_hits and not any_entry:
        return {
            "verdict": "E1_X7_PFQ_FEATURES_VOLATILITY_PROXY_ONLY",
            "pfq_close": True,
            "exit_revision": False,
            "vol_hits": vol_hits,
            "entry_support": entry_support,
        }

    if any_entry:
        # EXIT limitation evaluation on supported candidates' trades
        supported_cids = [c for c in ("PFQ_UPDATE_Q70", "PFQ_FLOW_Q30") if entry_support[c]["supported"]]
        repairable = [
            f for f in failure_rows
            if f["candidate_id"] in supported_cids
            and f["failure_class"] in (
                "SOFT_EXIT_PREMATURE",
                "PLUS5_REACHED_BEFORE_EXIT_GIVEN_BACK_TO_NONPOSITIVE",
            )
        ]
        n_rep = len(repairable)
        days = {f["day"] for f in repairable}
        # oracle +5 and realized loss episodes among supported
        oracle_loss = [
            f for f in failure_rows
            if f["candidate_id"] in supported_cids
            and f["failure_class"] in (
                "SOFT_EXIT_PREMATURE",
                "PLUS5_REACHED_BEFORE_EXIT_GIVEN_BACK_TO_NONPOSITIVE",
                "PLUS5_REACHED_BEFORE_EXIT_CAPTURED_POSITIVE",
                "PLUS10_REACHED_BEFORE_EXIT_CAPTURED_LT_PLUS5",
                "ENTRY_PATH_FAILURE_MINUS10_FIRST",
                "ENTRY_PATH_FAILURE_MINUS15_FIRST",
                "NO_EXECUTABLE_OPPORTUNITY",
                "HARD_INVALIDATION_BEFORE_PLUS5",
                "OTHER",
            )
        ]
        # Spec: repairable failure >= 50% of (oracle +5 AND realized loss) episodes
        oracle_plus5_realized_loss = [
            f for f in failure_rows
            if f["candidate_id"] in supported_cids
            and f["failure_class"] == "PLUS5_REACHED_BEFORE_EXIT_GIVEN_BACK_TO_NONPOSITIVE"
        ]
        # broaden: episodes where path had +5 opportunity but realized <=0
        denom = [
            f for f in failure_rows
            if f["candidate_id"] in supported_cids
            and f["failure_class"] in (
                "PLUS5_REACHED_BEFORE_EXIT_GIVEN_BACK_TO_NONPOSITIVE",
                "SOFT_EXIT_PREMATURE",
                "PLUS10_REACHED_BEFORE_EXIT_CAPTURED_LT_PLUS5",
            )
        ]
        mech = Counter(f["failure_class"] for f in repairable)
        top_mech, top_n = (mech.most_common(1)[0] if mech else (None, 0))
        frac_of_denom = (n_rep / len(denom)) if denom else 0.0
        mech_frac = (top_n / n_rep) if n_rep else 0.0

        exit_ok = (
            n_rep >= 20
            and len(days) >= 5
            and frac_of_denom >= 0.50
            and mech_frac >= 0.50
            and all(entry_support[c]["supported"] for c in supported_cids[:1])  # at least one already
        )
        # Also require bootstrap/day already in entry_support
        if exit_ok:
            return {
                "verdict": "E1_X7_PFQ_ENTRY_SUPPORTED_EXIT_CAPTURE_LIMITATION",
                "pfq_close": False,
                "exit_revision": True,
                "exit_revision_implemented": False,
                "entry_support": entry_support,
                "repairable_n": n_rep,
                "repairable_days": len(days),
                "top_mechanism": top_mech,
                "mech_frac": mech_frac,
                "frac_of_denom": frac_of_denom,
            }
        return {
            "verdict": "E1_X7_PFQ_ENTRY_PATH_SUPPORTED_NO_REPAIRABLE_EXIT_PATTERN",
            "pfq_close": True,
            "exit_revision": False,
            "entry_support": entry_support,
            "repairable_n": n_rep,
            "repairable_days": len(days),
            "top_mechanism": top_mech,
            "mech_frac": mech_frac,
            "frac_of_denom": frac_of_denom,
        }

    # No entry support: check if oracle-looking rates exist without realizability
    oracle_only = False
    for cid in ("PFQ_UPDATE_Q70", "PFQ_FLOW_Q30"):
        en = enrichment[cid]
        for mode in ("event_time", "fixed_grid"):
            m = en[mode]["metrics"]
            for name in ("net_plus5_rate", "net_plus10_rate"):
                d = (m.get(name) or {}).get("difference")
                if d is not None and d > 0:
                    oracle_only = True
    if oracle_only:
        return {
            "verdict": "E1_X7_PFQ_ORACLE_ONLY_NOT_REALIZABLE",
            "pfq_close": True,
            "exit_revision": False,
            "entry_support": entry_support,
        }
    return {
        "verdict": "E1_X7_NO_REALIZABLE_ENTRY_EXIT_PAIR",
        "pfq_close": True,
        "exit_revision": False,
        "entry_support": entry_support,
    }
