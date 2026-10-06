"""Online episode causality audit. No Confirmation. No Frozen Validation. No R11 retune."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.identify_minimum_missing_external_causal_information_v1.live import inspect_live_now
from research.native_1m_path_state_strategy_freeze_v1.r11 import match_r11
from research.native_1m_path_state_strategy_freeze_v1.replay import frozen_replay
from research.r11_online_episode_causality_audit_v1 import (
    CASE_BIND,
    CASE_LOOKAHEAD,
    CASE_OFFLINE,
    CASE_PROVEN,
    EXPECTED_ATLAS_EPISODE_N,
    EXPECTED_CANDIDATE_N,
    EXPECTED_TRADE_N_D2D4,
    EXPECTED_X0_D2D4,
    NEXT_BIND,
    NEXT_CONFIRM,
    NEXT_OFFLINE,
    NEXT_STOP,
    PARENT_VERDICT,
    STRATEGY_ID,
)
from research.r11_online_episode_causality_audit_v1.bind import bind_prior
from research.r11_online_episode_causality_audit_v1.lunch import lunch_exit_audit, timestop_audit
from research.r11_online_episode_causality_audit_v1.stream import cand_key_event, cand_key_full
from research.r11_online_episode_causality_audit_v1.walk import walk_audit


def decide(
    *,
    bind_ok: bool,
    offline_ok: bool,
    online_candidate_parity: bool,
    lookahead_candidate_n: int,
) -> dict[str, Any]:
    if not bind_ok:
        return {"CASE": "BIND", "VERDICT": CASE_BIND, "NEXT": NEXT_BIND, "INTERPRETATION": "Prior freeze bind failed. Do not open Confirmation or Frozen Validation."}
    if not offline_ok:
        return {
            "CASE": "OFFLINE",
            "VERDICT": CASE_OFFLINE,
            "NEXT": NEXT_OFFLINE,
            "INTERPRETATION": "Could not reproduce frozen offline 4149/908. Do not interpret streaming results.",
        }
    if online_candidate_parity and lookahead_candidate_n == 0:
        return {
            "CASE": "PROVEN",
            "VERDICT": CASE_PROVEN,
            "NEXT": NEXT_CONFIRM,
            "INTERPRETATION": "Streaming can emit the official 4149 R11 candidates at the reported decision times without future cluster knowledge. Confirmation may now run once. Frozen Validation remains closed.",
        }
    return {
        "CASE": "LOOKAHEAD",
        "VERDICT": CASE_LOOKAHEAD,
        "NEXT": NEXT_STOP,
        "INTERPRETATION": (
            "Last-event clustering is retrospective. An online process cannot know a cluster's last event at that event's timestamp. "
            "NATIVE_1M_R11_STRATEGY_FROZEN_V1 is offline replay parity, not online causal parity. "
            "Preserve R11 predicates as historical research evidence only. Do not open Confirmation. Do not open Frozen Validation. "
            "Delayed last-event and first-event refractory are diagnostics only; neither is selected or tuned."
        ),
    }


def _eqf(a: Any, b: Any) -> bool:
    if a is None or b is None:
        return False
    return float(a) == float(b)


def _jaccard(a: set, b: set) -> float | None:
    if not a and not b:
        return None
    return len(a & b) / float(len(a | b))


def _strip_pack(pack: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in pack.items() if k not in {"trades", "eval_trades", "symbols"}}
    return out


def build_report_body() -> dict[str, Any]:
    bind = bind_prior()
    print(f"BIND ok={bind.get('ok')} freeze={bind.get('freeze_verdict')}", flush=True)
    med = dict(bind.get("d1_med") or {})
    walked: dict[str, Any] = {"ok": False}
    official: dict[str, Any] = {}
    delayed: dict[str, Any] = {}
    first: dict[str, Any] = {}
    know: dict[str, Any] = {}
    parity: dict[str, Any] = {}
    lunch: dict[str, Any] = {}
    tstop: dict[str, Any] = {}
    samples: list[dict[str, Any]] = []
    if bind.get("ok"):
        walked = walk_audit(bind, med=med)
        rows = list(walked.get("rows") or [])
        print(f"EPISODES n={walked.get('episode_n')} streamed_last={len(list(walked.get('streamed_last') or []))}", flush=True)
        official = frozen_replay(rows, med=med)
        print(f"OFFLINE R11 n={official.get('trade_n')} x0={official.get('mean_x0_bps')} cand={official.get('candidate_n')}", flush=True)
        r11_eps = [e for e in rows if e.get("x0_entry_open") and match_r11(e, med=med)]
        trades = [t for t in list(official.get("trades") or []) if str(t.get("block")) in {"D2", "D3", "D4"}]
        trade_keys = {(str(t["date"]), str(t["symbol"]), str(t["event_time"])) for t in trades}
        viol_ep = [e for e in rows if e.get("LOOKAHEAD_VIOLATION")]
        viol_cand = [e for e in r11_eps if e.get("LOOKAHEAD_VIOLATION")]
        viol_tr = [t for t in trades if (str(t["date"]), str(t["symbol"]), str(t["event_time"])) in {(str(e["date"]), str(e["symbol"]), str(e["event_time"])) for e in viol_cand}]
        lags = [int(e.get("CAUSAL_KNOWABILITY_LAG_MIN") or 0) for e in r11_eps]
        know = {
            "episode_n": len(rows),
            "R11_candidate_n": len(r11_eps),
            "R11_trade_n": len(trades),
            "lookahead_violation_episode_n": len(viol_ep),
            "lookahead_violation_candidate_n": len(viol_cand),
            "lookahead_violation_trade_n": len(viol_tr),
            "known_at_own_timestamp_candidate_n": sum(1 for e in r11_eps if e.get("known_at_own_event_timestamp")),
            "lag_min_counter": dict(Counter(lags)),
            "lag_min_mean_candidates": (sum(lags) / len(lags)) if lags else None,
            "cluster_last_known_at_own_event_timestamp": False,
            "how": (
                "Never, except when the generator cannot emit any later joining onset "
                "(join window truncated by ENTRY_CUTOFF 14:50). Otherwise last+10 minutes of inactivity must be observed."
            ),
            "offline_inspects_later_events": True,
        }
        off_last = {(str(e["date"]), str(e["symbol"]), str(e["event_time"])) for e in rows}
        st_last = {cand_key_event(e) for e in list(walked.get("streamed_last") or [])}
        r11_full = {cand_key_full({**e, "decision_time": e.get("original_decision_time") or e.get("event_time")}) for e in r11_eps}
        stream_full = set()
        same_ts = 0
        for e in list(walked.get("streamed_last") or []):
            k = (
                str(e.get("date")),
                str(e.get("symbol")),
                str(e.get("feature_bar")),
                str(e.get("available_at")),
                str(e.get("decision_time")),
            )
            orig = (
                str(e.get("date")),
                str(e.get("symbol")),
                str(e.get("feature_bar")),
                str(e.get("available_at")),
                str(e.get("original_decision_time")),
            )
            if orig in r11_full and k == orig:
                same_ts += 1
            stream_full.add(k)
        delayed_ok = [e for e in list(walked.get("delayed_r11_rows") or []) if not e.get("delayed_skipped") and e.get("x0_entry_open")]
        delayed = frozen_replay(delayed_ok, med=med, already_matched=True)
        first_rows = [e for e in list(walked.get("first_r11_rows") or []) if e.get("x0_entry_open")]
        first = frozen_replay(first_rows, med=med, already_matched=True)
        print(
            f"DELAYED n={delayed.get('trade_n')} x0={delayed.get('mean_x0_bps')} "
            f"FIRST n={first.get('trade_n')} x0={first.get('mean_x0_bps')}",
            flush=True,
        )
        first_keys = {(str(t["date"]), str(t["symbol"]), str(t["event_time"])) for t in list(first.get("trades") or []) if str(t.get("block")) in {"D2", "D3", "D4"}}
        first_cands = {(str(e["date"]), str(e["symbol"]), str(e["event_time"])) for e in first_rows}
        off_cands = {(str(e["date"]), str(e["symbol"]), str(e["event_time"])) for e in r11_eps}
        lunch = lunch_exit_audit(trades)
        tstop = timestop_audit(trades)
        delayed_eval = [t for t in list(delayed.get("trades") or []) if str(t.get("block")) in {"D2", "D3", "D4"}]
        official_entry = {(str(t["date"]), str(t["symbol"]), str(t.get("entry_time") or t["event_time"])) for t in trades}
        delayed_entry = {(str(t["date"]), str(t["symbol"]), str(t.get("entry_time") or t["event_time"])) for t in delayed_eval}
        official_exit = {(str(t["date"]), str(t["symbol"]), str(t.get("exit_hh")), str(t.get("exit_reason"))) for t in trades}
        delayed_exit = {(str(t["date"]), str(t["symbol"]), str(t.get("exit_hh")), str(t.get("exit_reason"))) for t in delayed_eval}
        parity = {
            "OFFLINE_SOURCE_PARITY": bool(
                int(walked.get("episode_n") or 0) == EXPECTED_ATLAS_EPISODE_N
                and int(official.get("candidate_n") or 0) == EXPECTED_CANDIDATE_N
                and int(official.get("trade_n") or 0) == EXPECTED_TRADE_N_D2D4
                and _eqf(official.get("mean_x0_bps"), EXPECTED_X0_D2D4)
            ),
            "ONLINE_EVENT_PARITY": off_last == st_last,
            "ONLINE_EVENT_PARITY_n_offline": len(off_last),
            "ONLINE_EVENT_PARITY_n_stream": len(st_last),
            "ONLINE_EVENT_PARITY_overlap": len(off_last & st_last),
            "ONLINE_DECISION_TIME_PARITY": bool(same_ts == EXPECTED_CANDIDATE_N),
            "ONLINE_DECISION_TIME_MATCH_N": same_ts,
            "ONLINE_CANDIDATE_PARITY": False,
            "ENTRY_FILL_PARITY": official_entry == delayed_entry and len(official_entry) == EXPECTED_TRADE_N_D2D4,
            "EXIT_PARITY": official_exit == delayed_exit and len(official_exit) == EXPECTED_TRADE_N_D2D4,
            "PORTFOLIO_ORDER_PARITY": False,
            "generic_parity_pass_not_used": True,
        }
        # streaming cannot emit at original decision timestamps
        parity["ONLINE_CANDIDATE_PARITY"] = bool(parity["ONLINE_DECISION_TIME_PARITY"] and same_ts == len(r11_eps) == EXPECTED_CANDIDATE_N)
        r11_eps_sorted = sorted(r11_eps, key=lambda e: (str(e["date"]), str(e["symbol"]), str(e["event_time"])))
        step = max(1, len(r11_eps_sorted) // 24)
        for e in r11_eps_sorted[::step][:24]:
            samples.append(
                {
                    "date": e.get("date"),
                    "symbol": e.get("symbol"),
                    "block": e.get("block"),
                    "original_trigger_event_time": e.get("original_trigger_event_time"),
                    "original_decision_time": e.get("original_decision_time"),
                    "feature_bar": e.get("feature_bar"),
                    "available_at": e.get("available_at"),
                    "cluster_last_event_time": e.get("cluster_last_event_time"),
                    "next_same_symbol_event_time": e.get("next_same_symbol_event_time"),
                    "earliest_time_cluster_last_is_knowable": e.get("earliest_time_cluster_last_is_knowable"),
                    "CAUSAL_KNOWABILITY_LAG_MIN": e.get("CAUSAL_KNOWABILITY_LAG_MIN"),
                    "LOOKAHEAD_VIOLATION": e.get("LOOKAHEAD_VIOLATION"),
                    "could_realtime_emit_at_original_decision_time": False,
                }
            )
        first_eval = [t for t in list(first.get("trades") or []) if str(t.get("block")) in {"D2", "D3", "D4"}]
        know["first_event_canary"] = {
            "streamed_first_onset_n": walked.get("streamed_first_n"),
            "candidate_n": first.get("candidate_n"),
            "trade_n_d2d4": first.get("trade_n"),
            "mean_x0_bps": first.get("mean_x0_bps"),
            "mean_x1_bps": first.get("mean_x1_bps"),
            "profit_factor": first.get("profit_factor"),
            "candidate_overlap_with_frozen_r11": len(first_cands & off_cands),
            "candidate_jaccard_with_frozen_r11": _jaccard(first_cands, off_cands),
            "trade_overlap_d2d4": len(first_keys & trade_keys),
            "not_selected": True,
        }
        know["causal_delayed"] = {
            "candidate_n": delayed.get("candidate_n"),
            "delayed_skipped_n": sum(1 for e in list(walked.get("delayed_r11_rows") or []) if e.get("delayed_skipped")),
            "trade_n_d2d4": delayed.get("trade_n"),
            "mean_x0_bps": delayed.get("mean_x0_bps"),
            "mean_x1_bps": delayed.get("mean_x1_bps"),
            "profit_factor": delayed.get("profit_factor"),
            "not_selected": True,
        }
        for pack in (official, delayed, first):
            for t in list(pack.get("trades") or []):
                t.pop("fwd_bars", None)
                t.pop("state", None)
            pack["trades"] = []
        for e in rows:
            e.pop("fwd_bars", None)
            e.pop("state", None)
            e.pop("first_state", None)
        walked["rows"] = []
        walked["delayed_r11_rows"] = []
        walked["first_r11_rows"] = []
        walked["streamed_last"] = []
    live = inspect_live_now()
    offline_ok = bool((parity or {}).get("OFFLINE_SOURCE_PARITY"))
    online_cand = bool((parity or {}).get("ONLINE_CANDIDATE_PARITY"))
    decision = decide(
        bind_ok=bool(bind.get("ok")),
        offline_ok=offline_ok,
        online_candidate_parity=online_cand,
        lookahead_candidate_n=int((know or {}).get("lookahead_violation_candidate_n") or 0),
    )
    return {
        "parent_verdict_accepted": PARENT_VERDICT,
        "strategy_id": STRATEGY_ID,
        "bind": {k: v for k, v in bind.items() if k not in {"by_symbol", "split"}},
        "episode_set": {
            "episode_n": walked.get("episode_n"),
            "raw_event_n": walked.get("raw_event_n"),
            "day_n": walked.get("day_n"),
            "offline_inspects_later_events_to_choose_trigger": True,
        },
        "knowability": know,
        "parity": parity,
        "offline_official": _strip_pack(official),
        "causal_delayed_replay": _strip_pack(delayed),
        "first_event_refractory_canary": _strip_pack(first),
        "lunch_exit_audit": lunch,
        "timestop_audit": tstop,
        "knowability_samples": samples,
        "live_20260914": live,
        "kabu_50_applied": False,
        "old_confirmation_opened": False,
        "frozen_validation_opened": False,
        "parameter_changed": False,
        "threshold_tuned": False,
        "exit_changed": False,
        "cap_changed": False,
        "selected_delayed_or_first": False,
        "new_paid_data": False,
        "promoted": False,
        "v27_bolted": False,
        "purchase": False,
        "decision": decision,
    }


def build_answers(report: dict[str, Any]) -> dict[str, Any]:
    d = dict(report.get("decision") or {})
    k = dict(report.get("knowability") or {})
    p = dict(report.get("parity") or {})
    delayed = dict(k.get("causal_delayed") or report.get("causal_delayed_replay") or {})
    first = dict(k.get("first_event_canary") or {})
    lunch = dict(report.get("lunch_exit_audit") or {})
    tstop = dict(report.get("timestop_audit") or {})
    cd = dict(report.get("causal_delayed_replay") or {})
    return {
        "Can_the_cluster_last_event_be_known_at_its_own_event_timestamp": False,
        "How": k.get("how"),
        "Does_current_offline_generator_inspect_later_events_to_determine_which_event_survives_clustering": True,
        "lookahead_violation_episode_n": k.get("lookahead_violation_episode_n"),
        "lookahead_violation_candidate_n": k.get("lookahead_violation_candidate_n"),
        "lookahead_violation_trade_n": k.get("lookahead_violation_trade_n"),
        "Can_streaming_reproduce_4149_4149_at_identical_decision_timestamps": bool(p.get("ONLINE_CANDIDATE_PARITY")),
        "Can_it_reproduce_908_908_without_retroactive_event_assignment": bool(
            p.get("ONLINE_CANDIDATE_PARITY") and p.get("ENTRY_FILL_PARITY")
        ),
        "If_not_earliest_causal_decision_timestamp": "original_trigger_event_time + 10 minutes (join window), truncated by 14:50 cutoff",
        "Causal_delayed_R11_trade_n": cd.get("trade_n") or delayed.get("trade_n_d2d4"),
        "Causal_delayed_R11_X0": cd.get("mean_x0_bps") or delayed.get("mean_x0_bps"),
        "Causal_delayed_R11_X1": cd.get("mean_x1_bps") or delayed.get("mean_x1_bps"),
        "Causal_delayed_R11_PF": cd.get("profit_factor") or delayed.get("profit_factor"),
        "First_event_refractory_candidate_n": first.get("candidate_n"),
        "First_event_refractory_trade_n": first.get("trade_n_d2d4"),
        "First_event_overlap_with_frozen_R11": {
            "candidate_overlap": first.get("candidate_overlap_with_frozen_r11"),
            "candidate_jaccard": first.get("candidate_jaccard_with_frozen_r11"),
            "trade_overlap_d2d4": first.get("trade_overlap_d2d4"),
        },
        "Why_session_flat_around_1130": lunch.get("implementation_meaning"),
        "Is_lunch_flatten_part_of_tested_strategy": lunch.get("lunch_flatten_is_part_of_tested_strategy"),
        "Is_time_stop_20_wall_clock_or_observed_bars": "observed_fwd_bars",
        "OFFLINE_SOURCE_PARITY": p.get("OFFLINE_SOURCE_PARITY"),
        "ONLINE_EVENT_PARITY": p.get("ONLINE_EVENT_PARITY"),
        "ONLINE_DECISION_TIME_PARITY": p.get("ONLINE_DECISION_TIME_PARITY"),
        "ENTRY_FILL_PARITY": p.get("ENTRY_FILL_PARITY"),
        "EXIT_PARITY": p.get("EXIT_PARITY"),
        "PORTFOLIO_ORDER_PARITY": p.get("PORTFOLIO_ORDER_PARITY"),
        "ONLINE_CANDIDATE_PARITY": p.get("ONLINE_CANDIDATE_PARITY"),
        "Any_parameter_changed": False,
        "Any_threshold_tuned": False,
        "Old_Confirmation_opened": False,
        "Frozen_Validation_opened": False,
        "submit_cancel_live": "0/0/0",
        "session_flat_exit_count": lunch.get("session_flat_exit_count"),
        "am_close_session_flat_n": lunch.get("am_close_session_flat_n"),
        "session_flat_15_20_n": lunch.get("session_flat_15_20_n"),
        "time_stop_n": tstop.get("time_stop_n"),
        "VERDICT": d.get("VERDICT"),
        "NEXT": d.get("NEXT"),
    }
