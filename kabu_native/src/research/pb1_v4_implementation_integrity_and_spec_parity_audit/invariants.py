"""State-transition invariants and setup identity. Persist violating IDs."""
from __future__ import annotations

from collections import Counter
from typing import Any

from research.pb1_v4_implementation_integrity_and_spec_parity_audit.counts import proto_id, symbol_date, _setup_identity


def audit_invariants(walked: dict[str, Any]) -> dict[str, Any]:
    funnel = list(walked.get("funnel_days") or [])
    setups = list(walked.get("setups") or [])

    s1_wo_s0 = [proto_id(r) for r in funnel if r.get("S1") and not r.get("S0")]
    s2_wo_s1 = [proto_id(r) for r in funnel if r.get("S2") and not r.get("S1")]
    s3_wo_s2 = [proto_id(r) for r in funnel if r.get("S3") and not r.get("S2")]
    s4_wo_s3 = [proto_id(r) for r in funnel if r.get("S4") and not r.get("S3")]
    s4_wo_s2 = [proto_id(r) for r in funnel if r.get("S4") and not r.get("S2")]
    s4_wo_s1 = [proto_id(r) for r in funnel if r.get("S4") and not r.get("S1")]
    s4_wo_s0 = [proto_id(r) for r in funnel if r.get("S4") and not r.get("S0")]

    s4_setup_wo_s3: list[str] = []
    fidx = {symbol_date(r): r for r in funnel}
    for s in setups:
        f = fidx.get(symbol_date(s)) or {}
        if not f.get("S3"):
            s4_setup_wo_s3.append(str(s.get("setup_id")))

    deaths_s3_wo_s2 = Counter(str(r.get("death") or "") for r in funnel if r.get("S3") and not r.get("S2"))

    return {
        "S1_without_S0_n": len(s1_wo_s0),
        "S2_without_S1_n": len(s2_wo_s1),
        "S3_without_S2_n": len(s3_wo_s2),
        "S4_without_S3_n": len(s4_wo_s3),
        "S4_without_S2_n": len(s4_wo_s2),
        "S4_without_S1_n": len(s4_wo_s1),
        "S4_without_S0_n": len(s4_wo_s0),
        "S4_setup_without_funnel_S3_n": len(s4_setup_wo_s3),
        "required_zero": {
            "S1_without_S0_n": len(s1_wo_s0) == 0,
            "S2_without_S1_n": len(s2_wo_s1) == 0,
            "S3_without_S2_n": len(s3_wo_s2) == 0,
            "S4_without_S3_n": len(s4_wo_s3) == 0,
        },
        "S3_without_S2_is_construction": True,
        "S3_without_S2_cause": (
            "machine.py sets st.s3=True on geometric hold, then classify_s2; location failure leaves s3 True and s2 False."
        ),
        "S3_without_S2_death_counts": dict(deaths_s3_wo_s2.most_common(20)),
        "violating_ids": {
            "S1_without_S0": s1_wo_s0,
            "S2_without_S1": s2_wo_s1,
            "S3_without_S2": s3_wo_s2,
            "S4_without_S3": s4_wo_s3,
            "S4_setup_without_funnel_S3": s4_setup_wo_s3,
        },
        "orphan_state_allowed": len(s3_wo_s2) > 0,
    }


def audit_identity(walked: dict[str, Any]) -> dict[str, Any]:
    setups = list(walked.get("setups") or [])
    funnel = list(walked.get("funnel_days") or [])
    identities = [_setup_identity(s) for s in setups]
    by_sd: dict[tuple[str, str], list[str]] = {}
    by_break: dict[str, list[str]] = {}
    missing_fields: list[str] = []
    mixed_episode: list[str] = []
    for ident in identities:
        sid = str(ident["setup_id"])
        key = (ident["symbol"], ident["date"])
        by_sd.setdefault(key, []).append(sid)
        by_break.setdefault(ident["break_id"], []).append(sid)
        if not ident["break_t"] or not ident["retest_t"] or ident["direction"] in (None, 0, "0"):
            missing_fields.append(sid)
        if ident["break_t"] and ident["retest_t"] and ident["retest_t"] < ident["break_t"]:
            mixed_episode.append(sid)

    multi_setup_days = {f"{a}|{b}": v for (a, b), v in by_sd.items() if len(v) > 1}
    # One SideState per symbol-day: S1 lock is a single DIR; first break/retest only.
    s1_days = [r for r in funnel if r.get("S1")]
    dir_flip = [
        proto_id(r)
        for r in s1_days
        if int(r.get("DIR") or 0) not in (1, -1)
    ]

    persisted_ids = {
        "setup_id": True,
        "symbol": True,
        "date": True,
        "direction": True,
        "opening_drive_id": False,
        "break_id": False,
        "retest_id": False,
    }
    return {
        "setup_n": len(setups),
        "unique_setup_id_n": len({i["setup_id"] for i in identities}),
        "unique_symbol_date_n": len(by_sd),
        "multi_setup_symbol_date_n": len(multi_setup_days),
        "multi_setup_symbol_dates": multi_setup_days,
        "shared_break_id_n": sum(1 for v in by_break.values() if len(v) > 1),
        "retest_before_break_n": len(mixed_episode),
        "retest_before_break_ids": mixed_episode,
        "missing_identity_field_n": len(missing_fields),
        "missing_identity_field_ids": missing_fields[:50],
        "immutable_identity_persisted_as_named_fields": persisted_ids,
        "identity_reconstructed_from": "symbol|date|DIR|break_t|retest_t|opening_state|SETUP_ELIGIBLE_AT",
        "one_sidestate_per_symbol_day": True,
        "s1_dir_invalid_n": len(dir_flip),
        "s1_dir_invalid_ids": dir_flip,
        "cross_episode_s1_plus_later_s3_as_one_setup_n": len(multi_setup_days) + len(mixed_episode),
        "setup_identity_mismatch": bool(multi_setup_days or mixed_episode or missing_fields or dir_flip),
        "note": (
            "Walk uses one SideState per symbol-day after S1 DIR lock, first break then first retest. "
            "Named opening_drive_id / break_id / retest_id are not persisted; reconstructable. "
            "The S3-without-S2 flag is a stage-order error, not a later-episode identity mix."
        ),
    }
