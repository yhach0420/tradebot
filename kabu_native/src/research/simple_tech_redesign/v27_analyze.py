"""V27 episode duration / recovery / propagation. No capture-percent gates. No EXIT policy."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np

from research.simple_tech_entry_family.v13_analyze import identity_keys, set_hash
from research.simple_tech_redesign.v22_analyze import _canon_num, fill_tuples_e4
from research.simple_tech_redesign.v27_spec import (
    ADDED_FILL_N_EXPECTED,
    B1_SIGNAL_N_EXPECTED,
    BAD_PATHS,
    CORE_E4_FILL_N_EXPECTED,
    CORRECTED_EVALUABLE_N_EXPECTED,
    CORRECTED_FILL_HASH_EXPECTED,
    DIP_PATH,
    GOOD_PATH,
    MECHANISM_ORDER,
    MIN_BAD_N,
    MIN_CORE_GOOD_N,
    MIN_DAY_PAIR_N,
    MIN_DAYS,
    MIN_DIP_N,
    MIN_GOOD_N,
    MIN_SYMBOLS,
    PATH_TYPES,
    PRIMARY_PRIMITIVES,
    PRIMITIVES,
    PROP_CLASSES,
    RESEARCH_FILL_SET_HASH_EXPECTED,
    TFS,
    TOTAL_RESEARCH_FILL_N_EXPECTED,
)


def _finite(v: Any) -> bool:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return False
    return x == x


def _pct(xs: list[float], q: float) -> Optional[float]:
    if not xs:
        return None
    return float(np.percentile(np.asarray(xs, dtype=float), q))


def dist(xs: list[float]) -> dict[str, Any]:
    ys = [float(x) for x in xs if _finite(x)]
    if not ys:
        return {"n": 0, "min": None, "p25": None, "median": None, "p75": None, "p90": None, "max": None}
    return {
        "n": len(ys),
        "min": float(min(ys)),
        "p25": _pct(ys, 25),
        "median": _pct(ys, 50),
        "p75": _pct(ys, 75),
        "p90": _pct(ys, 90),
        "max": float(max(ys)),
    }


def _rate(n: int, d: int) -> Optional[float]:
    if int(d) <= 0:
        return None
    return float(n) / float(d)


def research_fill_tuples(rows: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
    out = []
    for r in rows:
        if not r.get("actual_filled"):
            continue
        d, s, t0 = identity_keys(r)
        out.append(
            (
                d,
                s,
                t0,
                str(r.get("fill_role") or ""),
                _canon_num(r.get("fill_t")),
                _canon_num(r.get("fill_price")),
                str(r.get("path_type") or ""),
            )
        )
    return sorted(out)


def _fills(rows: list[dict[str, Any]], role: Optional[str] = None) -> list[dict[str, Any]]:
    out = []
    for r in rows:
        if not r.get("actual_filled"):
            continue
        if role is not None and str(r.get("fill_role") or "") != role:
            continue
        out.append(r)
    return out


def _pid_tf(row: dict[str, Any], tf: str, pid: str) -> dict[str, Any]:
    seq = dict(row.get("state_sequence") or {})
    return dict(dict(seq.get("by_tf") or {}).get(tf) or {}).get(pid) or {}


def _obs(row: dict[str, Any], tf: str) -> bool:
    seq = dict(row.get("state_sequence") or {})
    return bool(dict(dict(seq.get("by_tf") or {}).get(tf) or {}).get("observable"))


def _prop(row: dict[str, Any], pid: str) -> dict[str, Any]:
    return dict(dict(dict(row.get("state_sequence") or {}).get("propagation") or {}).get(pid) or {})


def episode_summary(fills: list[dict[str, Any]], *, tf: str, pid: str) -> dict[str, Any]:
    obs = [r for r in fills if _obs(r, tf)]
    by_path: dict[str, list[dict[str, Any]]] = {k: [] for k in PATH_TYPES}
    for r in obs:
        by_path[str(r.get("path_type") or "OTHER")].append(r)
    out: dict[str, Any] = {
        "tf": tf,
        "primitive": pid,
        "observable_n": len(obs),
        "episode_trade_n": 0,
        "occurrence_rate": None,
        "duration": {},
        "recovery_rate": None,
        "non_recovery_rate": None,
        "by_path": {},
    }
    durs_all: list[float] = []
    rec_n = rec_d = 0
    ep_n = 0
    for ptype in PATH_TYPES:
        grp = by_path[ptype]
        durs = []
        rec = 0
        had = 0
        for r in grp:
            pack = _pid_tf(r, tf, pid)
            if int(pack.get("episode_n") or 0) <= 0:
                continue
            had += 1
            if _finite(pack.get("first_duration")):
                durs.append(float(pack["first_duration"]))
                durs_all.append(float(pack["first_duration"]))
            if pack.get("first_recovered") is True:
                rec += 1
        ep_n += had
        rec_n += rec
        rec_d += had
        out["by_path"][ptype] = {
            "n": len(grp),
            "episode_n": had,
            "occurrence_rate": _rate(had, len(grp)),
            "duration": dist(durs),
            "recovery_rate": _rate(rec, had),
            "non_recovery_rate": _rate(had - rec, had),
        }
    out["episode_trade_n"] = ep_n
    out["occurrence_rate"] = _rate(ep_n, len(obs))
    out["duration"] = dist(durs_all)
    out["recovery_rate"] = _rate(rec_n, rec_d)
    out["non_recovery_rate"] = _rate(rec_d - rec_n, rec_d)
    return out


def propagation_summary(fills: list[dict[str, Any]], *, pid: str) -> dict[str, Any]:
    have_1m = [r for r in fills if _finite(_prop(r, pid).get("t_1m"))]
    counts = {k: 0 for k in PROP_CLASSES}
    by_path: dict[str, Any] = {}
    for ptype in PATH_TYPES:
        grp = [r for r in have_1m if str(r.get("path_type") or "OTHER") == ptype]
        pc = {k: 0 for k in PROP_CLASSES}
        for r in grp:
            c = str(_prop(r, pid).get("prop_class") or "")
            if c in pc:
                pc[c] += 1
                counts[c] += 1
        n = len(grp)
        higher = pc["P2_1M_TO_3M"] + pc["P3_1M_TO_5M"] + pc["P4_1M_TO_3M_TO_5M"]
        by_path[ptype] = {
            "n_1m_episode": n,
            "classes": pc,
            "higher_tf_rate": _rate(higher, n),
            "p4_rate": _rate(pc["P4_1M_TO_3M_TO_5M"], n),
        }
    n = len(have_1m)
    higher = counts["P2_1M_TO_3M"] + counts["P3_1M_TO_5M"] + counts["P4_1M_TO_3M_TO_5M"]
    first_tf_n = defaultdict(int)
    for r in fills:
        ft = _prop(r, pid).get("first_tf")
        if ft:
            first_tf_n[str(ft)] += 1
    return {
        "primitive": pid,
        "n_1m_episode": n,
        "classes": counts,
        "higher_tf_rate": _rate(higher, n),
        "p4_rate": _rate(counts["P4_1M_TO_3M_TO_5M"], n),
        "first_tf_n": dict(first_tf_n),
        "by_path": by_path,
    }


def _syms(rows: list[dict[str, Any]]) -> set[str]:
    return {str(r.get("symbol") or "").replace(".T", "") for r in rows}


def _days(rows: list[dict[str, Any]]) -> set[str]:
    return {str(r.get("date") or "") for r in rows}


def _group(fills: list[dict[str, Any]], kinds: tuple[str, ...] | str) -> list[dict[str, Any]]:
    if isinstance(kinds, str):
        kinds = (kinds,)
    return [r for r in fills if str(r.get("path_type") or "") in kinds]


def _day_sign_duration(fills: list[dict[str, Any]], *, tf: str, pid: str, higher_worse: bool) -> dict[str, Any]:
    bad = _group(fills, BAD_PATHS)
    good = _group(fills, GOOD_PATH)
    by_day: dict[str, dict[str, list[float]]] = defaultdict(lambda: {"bad": [], "good": []})
    for r, key in ((x, "bad") for x in bad):
        pack = _pid_tf(r, tf, pid)
        if _finite(pack.get("first_duration")):
            by_day[str(r.get("date"))][key].append(float(pack["first_duration"]))
    for r in good:
        pack = _pid_tf(r, tf, pid)
        if _finite(pack.get("first_duration")):
            by_day[str(r.get("date"))]["good"].append(float(pack["first_duration"]))
    agree = disagree = usable = 0
    for _d, g in by_day.items():
        if len(g["bad"]) < int(MIN_DAY_PAIR_N) or len(g["good"]) < int(MIN_DAY_PAIR_N):
            continue
        usable += 1
        mb = float(np.median(g["bad"]))
        mg = float(np.median(g["good"]))
        if higher_worse:
            if mb > mg:
                agree += 1
            elif mb < mg:
                disagree += 1
        else:
            if mb < mg:
                agree += 1
            elif mb > mg:
                disagree += 1
    return {"usable_days": usable, "agree_days": agree, "disagree_days": disagree}


def _day_sign_rate(fills: list[dict[str, Any]], pred, *, higher_worse: bool) -> dict[str, Any]:
    by_day: dict[str, dict[str, list[int]]] = defaultdict(lambda: {"bad": [], "good": []})
    for r in fills:
        v = pred(r)
        if v is None:
            continue
        ptype = str(r.get("path_type") or "")
        key = "bad" if ptype in BAD_PATHS else ("good" if ptype == GOOD_PATH else None)
        if key is None:
            continue
        by_day[str(r.get("date"))][key].append(1 if v else 0)
    agree = disagree = usable = 0
    for _d, g in by_day.items():
        if len(g["bad"]) < int(MIN_DAY_PAIR_N) or len(g["good"]) < int(MIN_DAY_PAIR_N):
            continue
        usable += 1
        rb = float(sum(g["bad"])) / float(len(g["bad"]))
        rg = float(sum(g["good"])) / float(len(g["good"]))
        if higher_worse:
            if rb > rg:
                agree += 1
            elif rb < rg:
                disagree += 1
        else:
            if rb < rg:
                agree += 1
            elif rb > rg:
                disagree += 1
    return {"usable_days": usable, "agree_days": agree, "disagree_days": disagree}


def _support_pack(
    *,
    name: str,
    direction_ok: bool,
    core_ok: bool,
    added_ok: bool,
    day: dict[str, Any],
    bad_n: int,
    good_n: int,
    dip_n: int,
    core_good_n: int,
    bad_days: int,
    good_days: int,
    bad_syms: int,
    good_syms: int,
    effect: dict[str, Any],
) -> dict[str, Any]:
    floors = (
        int(bad_n) >= int(MIN_BAD_N)
        and int(good_n) >= int(MIN_GOOD_N)
        and int(dip_n) >= int(MIN_DIP_N)
        and int(core_good_n) >= int(MIN_CORE_GOOD_N)
        and int(bad_days) >= int(MIN_DAYS)
        and int(good_days) >= int(MIN_DAYS)
        and int(bad_syms) >= int(MIN_SYMBOLS)
        and int(good_syms) >= int(MIN_SYMBOLS)
    )
    day_ok = int(day.get("usable_days") or 0) >= int(MIN_DAYS) and int(day.get("agree_days") or 0) > int(day.get("disagree_days") or 0)
    supported = bool(floors and direction_ok and core_ok and added_ok and day_ok)
    return {
        "name": name,
        "SUPPORTED": supported,
        "floors_ok": floors,
        "direction_ok": bool(direction_ok),
        "core_ok": bool(core_ok),
        "added_ok": bool(added_ok),
        "day_ok": bool(day_ok),
        "day": day,
        "n": {"bad": bad_n, "good": good_n, "dip": dip_n, "core_good": core_good_n},
        "effect": effect,
    }


def persistence_test(fills: list[dict[str, Any]], *, tf: str, pid: str) -> dict[str, Any]:
    def durs(rs: list[dict[str, Any]]) -> list[float]:
        out = []
        for r in rs:
            pack = _pid_tf(r, tf, pid)
            if _finite(pack.get("first_duration")):
                out.append(float(pack["first_duration"]))
        return out

    bad = durs(_group(fills, BAD_PATHS))
    good = durs(_group(fills, GOOD_PATH))
    dip = durs(_group(fills, DIP_PATH))
    core_good = durs(_group(_fills(fills, "CORE"), GOOD_PATH))
    added_bad = durs(_group(_fills(fills, "ADDED"), BAD_PATHS))
    added_good = durs(_group(_fills(fills, "ADDED"), GOOD_PATH))
    mb, mg, md, mcg = _pct(bad, 50), _pct(good, 50), _pct(dip, 50), _pct(core_good, 50)
    direction = _finite(mb) and _finite(mg) and _finite(md) and float(mb) > float(mg) and float(mb) > float(md)
    core_ok = _finite(mb) and _finite(mcg) and float(mcg) < float(mb)
    added_ok = True
    if len(added_bad) >= int(MIN_BAD_N) and len(added_good) >= 4:
        mab, mag = _pct(added_bad, 50), _pct(added_good, 50)
        added_ok = _finite(mab) and _finite(mag) and float(mab) > float(mag)
    day = _day_sign_duration(fills, tf=tf, pid=pid, higher_worse=True)
    return _support_pack(
        name=f"PERSISTENCE::{tf}::{pid}",
        direction_ok=bool(direction),
        core_ok=bool(core_ok),
        added_ok=bool(added_ok),
        day=day,
        bad_n=len(bad),
        good_n=len(good),
        dip_n=len(dip),
        core_good_n=len(core_good),
        bad_days=len(_days(_group(fills, BAD_PATHS))),
        good_days=len(_days(_group(fills, GOOD_PATH))),
        bad_syms=len(_syms(_group(fills, BAD_PATHS))),
        good_syms=len(_syms(_group(fills, GOOD_PATH))),
        effect={
            "median_bad": mb,
            "median_good": mg,
            "median_dip": md,
            "median_core_good": mcg,
            "median_bad_minus_good": (float(mb) - float(mg)) if _finite(mb) and _finite(mg) else None,
            "p25_bad": _pct(bad, 25),
            "p75_good": _pct(good, 75),
        },
    )


def nonrecovery_test(fills: list[dict[str, Any]], *, tf: str, pid: str) -> dict[str, Any]:
    def rec_rate(rs: list[dict[str, Any]]) -> tuple[Optional[float], int, int]:
        had = rec = 0
        for r in rs:
            pack = _pid_tf(r, tf, pid)
            if int(pack.get("episode_n") or 0) <= 0:
                continue
            had += 1
            if pack.get("first_recovered") is True:
                rec += 1
        return _rate(rec, had), rec, had

    rb, _, nb = rec_rate(_group(fills, BAD_PATHS))
    rg, _, ng = rec_rate(_group(fills, GOOD_PATH))
    rd, _, nd = rec_rate(_group(fills, DIP_PATH))
    rcg, _, ncg = rec_rate(_group(_fills(fills, "CORE"), GOOD_PATH))
    rab, _, nab = rec_rate(_group(_fills(fills, "ADDED"), BAD_PATHS))
    rag, _, nag = rec_rate(_group(_fills(fills, "ADDED"), GOOD_PATH))
    direction = rb is not None and rg is not None and rd is not None and float(rb) < float(rg) and float(rb) < float(rd)
    core_ok = rb is not None and rcg is not None and float(rcg) > float(rb)
    added_ok = True
    if nab >= int(MIN_BAD_N) and nag >= 4 and rab is not None and rag is not None:
        added_ok = float(rab) < float(rag)

    def pred(r: dict[str, Any]) -> Optional[bool]:
        pack = _pid_tf(r, tf, pid)
        if int(pack.get("episode_n") or 0) <= 0:
            return None
        return pack.get("first_recovered") is not True

    day = _day_sign_rate(fills, pred, higher_worse=True)
    return _support_pack(
        name=f"NON_RECOVERY::{tf}::{pid}",
        direction_ok=bool(direction),
        core_ok=bool(core_ok),
        added_ok=bool(added_ok),
        day=day,
        bad_n=nb,
        good_n=ng,
        dip_n=nd,
        core_good_n=ncg,
        bad_days=len(_days(_group(fills, BAD_PATHS))),
        good_days=len(_days(_group(fills, GOOD_PATH))),
        bad_syms=len(_syms(_group(fills, BAD_PATHS))),
        good_syms=len(_syms(_group(fills, GOOD_PATH))),
        effect={
            "recovery_bad": rb,
            "recovery_good": rg,
            "recovery_dip": rd,
            "recovery_core_good": rcg,
            "nonrecovery_bad_minus_good": (1.0 - float(rb)) - (1.0 - float(rg)) if rb is not None and rg is not None else None,
        },
    )


def propagation_test(fills: list[dict[str, Any]], *, pid: str) -> dict[str, Any]:
    def higher(r: dict[str, Any]) -> Optional[bool]:
        c = _prop(r, pid).get("prop_class")
        if not c:
            return None
        return str(c) in ("P2_1M_TO_3M", "P3_1M_TO_5M", "P4_1M_TO_3M_TO_5M")

    def rate(rs: list[dict[str, Any]]) -> tuple[Optional[float], int]:
        vs = [higher(r) for r in rs]
        vs = [v for v in vs if v is not None]
        if not vs:
            return None, 0
        return _rate(sum(1 for v in vs if v), len(vs)), len(vs)

    rb, nb = rate(_group(fills, BAD_PATHS))
    rg, ng = rate(_group(fills, GOOD_PATH))
    rd, nd = rate(_group(fills, DIP_PATH))
    rcg, ncg = rate(_group(_fills(fills, "CORE"), GOOD_PATH))
    rab, nab = rate(_group(_fills(fills, "ADDED"), BAD_PATHS))
    rag, nag = rate(_group(_fills(fills, "ADDED"), GOOD_PATH))
    direction = rb is not None and rg is not None and rd is not None and float(rb) > float(rg) and float(rb) > float(rd)
    core_ok = rb is not None and rcg is not None and float(rcg) < float(rb)
    added_ok = True
    if nab >= int(MIN_BAD_N) and nag >= 4 and rab is not None and rag is not None:
        added_ok = float(rab) > float(rag)
    day = _day_sign_rate(fills, higher, higher_worse=True)
    return _support_pack(
        name=f"PROPAGATION::{pid}",
        direction_ok=bool(direction),
        core_ok=bool(core_ok),
        added_ok=bool(added_ok),
        day=day,
        bad_n=nb,
        good_n=ng,
        dip_n=nd,
        core_good_n=ncg,
        bad_days=len(_days(_group(fills, BAD_PATHS))),
        good_days=len(_days(_group(fills, GOOD_PATH))),
        bad_syms=len(_syms(_group(fills, BAD_PATHS))),
        good_syms=len(_syms(_group(fills, GOOD_PATH))),
        effect={
            "higher_tf_bad": rb,
            "higher_tf_good": rg,
            "higher_tf_dip": rd,
            "higher_tf_core_good": rcg,
            "bad_minus_good": (float(rb) - float(rg)) if rb is not None and rg is not None else None,
        },
    )


def _cohort_seq(fills: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {"N": len(fills), "path_n": {}, "ema": {}, "bb": {}}
    for k in PATH_TYPES:
        out["path_n"][k] = sum(1 for r in fills if str(r.get("path_type") or "") == k)
    for pid, key in (("A_EMA_STRUCTURE_LOSS", "ema"), ("D_BB_STRUCTURE_LOSS", "bb")):
        out[key] = {
            "by_tf": {tf: episode_summary(fills, tf=tf, pid=pid) for tf in TFS},
            "propagation": propagation_summary(fills, pid=pid),
        }
    return out


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    fills = _fills(rows)
    core = _fills(rows, "CORE")
    added = _fills(rows, "ADDED")
    all_ep = []
    for pid in PRIMITIVES:
        for tf in TFS:
            all_ep.append(episode_summary(fills, tf=tf, pid=pid))
    ema_ep = [p for p in all_ep if p["primitive"] == "A_EMA_STRUCTURE_LOSS"]
    bb_ep = [p for p in all_ep if p["primitive"] == "D_BB_STRUCTURE_LOSS"]
    dur_cmp = {}
    rec_cmp = {}
    prop_cmp = {}
    for pid in PRIMARY_PRIMITIVES:
        dur_cmp[pid] = {tf: episode_summary(fills, tf=tf, pid=pid)["by_path"] for tf in TFS}
        rec_cmp[pid] = {
            tf: {pt: episode_summary(fills, tf=tf, pid=pid)["by_path"][pt]["recovery_rate"] for pt in PATH_TYPES}
            for tf in TFS
        }
        prop_cmp[pid] = propagation_summary(fills, pid=pid)["by_path"]
    tests = []
    for pid in PRIMARY_PRIMITIVES:
        for tf in TFS:
            tests.append(("PERSISTENCE", pid, tf, persistence_test(fills, tf=tf, pid=pid)))
            tests.append(("NON_RECOVERY", pid, tf, nonrecovery_test(fills, tf=tf, pid=pid)))
        tests.append(("PROPAGATION", pid, None, propagation_test(fills, pid=pid)))
    supported = []
    flags = {"PERSISTENCE": False, "PROPAGATION": False, "NON_RECOVERY": False}
    for mech, pid, tf, pack in tests:
        if pack.get("SUPPORTED"):
            flags[mech] = True
            supported.append({"mechanism": mech, "primitive": pid, "tf": tf, "name": pack.get("name"), "effect": pack.get("effect")})
    primary = None
    for mech in MECHANISM_ORDER:
        for pid in PRIMARY_PRIMITIVES:
            hits = [s for s in supported if s["mechanism"] == mech and s["primitive"] == pid]
            if hits:
                primary = hits[0]
                break
        if primary is not None:
            break
    return {
        "SIGNAL_N": len(rows),
        "EXECUTION_EVALUABLE_N": sum(1 for r in rows if r.get("executable_signal")),
        "CORE_FILL_N": len(core),
        "ADDED_FILL_N": len(added),
        "TOTAL_RESEARCH_FILL_N": len(fills),
        "CORRECTED_FILL_HASH": set_hash(fill_tuples_e4(rows)),
        "RESEARCH_FILL_SET_HASH": set_hash(research_fill_tuples(rows)),
        "EMA_STRUCTURE_EPISODES": ema_ep,
        "BB_STRUCTURE_EPISODES": bb_ep,
        "ALL_PRIMITIVE_EPISODE_SUMMARY": all_ep,
        "PATH_TYPE_DURATION_COMPARISON": dur_cmp,
        "PATH_TYPE_RECOVERY_COMPARISON": rec_cmp,
        "PATH_TYPE_PROPAGATION_COMPARISON": prop_cmp,
        "CORE_STATE_SEQUENCE_SUMMARY": _cohort_seq(core),
        "ADDED_STATE_SEQUENCE_SUMMARY": _cohort_seq(added),
        "PERSISTENCE_SUPPORTED": bool(flags["PERSISTENCE"]),
        "PROPAGATION_SUPPORTED": bool(flags["PROPAGATION"]),
        "NON_RECOVERY_SUPPORTED": bool(flags["NON_RECOVERY"]),
        "SUPPORTED_EXIT_STATE_MECHANISMS": supported,
        "PRIMARY_EXIT_STATE_MECHANISM": primary,
        "tests": [
            {"mechanism": m, "primitive": p, "tf": t, **pack} for m, p, t, pack in tests
        ],
    }


def decide(
    summary: dict[str, Any],
    *,
    signal_parity: bool,
    fill_identity: bool,
    leak_ok: bool,
    ni_ok: bool,
) -> dict[str, Any]:
    counts_ok = (
        int(summary.get("SIGNAL_N") or 0) == int(B1_SIGNAL_N_EXPECTED)
        and int(summary.get("EXECUTION_EVALUABLE_N") or 0) == int(CORRECTED_EVALUABLE_N_EXPECTED)
        and int(summary.get("CORE_FILL_N") or 0) == int(CORE_E4_FILL_N_EXPECTED)
        and int(summary.get("ADDED_FILL_N") or 0) == int(ADDED_FILL_N_EXPECTED)
        and int(summary.get("TOTAL_RESEARCH_FILL_N") or 0) == int(TOTAL_RESEARCH_FILL_N_EXPECTED)
    )
    hash_ok = (
        str(summary.get("CORRECTED_FILL_HASH") or "") == CORRECTED_FILL_HASH_EXPECTED
        and str(summary.get("RESEARCH_FILL_SET_HASH") or "") == RESEARCH_FILL_SET_HASH_EXPECTED
        and bool(fill_identity)
    )
    integrity = bool(leak_ok and ni_ok and signal_parity and counts_ok and hash_ok)
    supported = list(summary.get("SUPPORTED_EXIT_STATE_MECHANISMS") or [])
    if not integrity:
        case = "C"
        verdict = "SIMPLE_TECH_V27_EXIT_STATE_SEQUENCE_UNRESOLVED"
        next_step = "STOP. Identity, causality, or non-interference failed."
    elif supported:
        case = "A"
        verdict = "SIMPLE_TECH_V27_EXIT_STATE_SEQUENCE_MECHANISM_FOUND"
        next_step = (
            "V28: convert PRIMARY_EXIT_STATE_MECHANISM into one precommitted simple EXIT state machine. "
            "No combination search. No PnL ranking of multiple machines. Coverage remains E4_THEN_ASK_CROSS_W5 research surface."
        )
    else:
        case = "B"
        verdict = "SIMPLE_TECH_V27_EXIT_STATE_SEQUENCE_NOT_SUPPORTED"
        next_step = (
            "STOP. Persistence, propagation, and non-recovery did not separate bad vs GOOD/DIP/CORE-good "
            "with multi-day evidence. Do not keep complicating MA/BB/RCI/Volume EXIT on this same data."
        )
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": next_step,
        "V26_FILL_IDENTITY_PARITY": bool(hash_ok and counts_ok),
        "PERSISTENCE_SUPPORTED": bool(summary.get("PERSISTENCE_SUPPORTED")),
        "PROPAGATION_SUPPORTED": bool(summary.get("PROPAGATION_SUPPORTED")),
        "NON_RECOVERY_SUPPORTED": bool(summary.get("NON_RECOVERY_SUPPORTED")),
        "SUPPORTED_EXIT_STATE_MECHANISMS": supported,
        "PRIMARY_EXIT_STATE_MECHANISM": summary.get("PRIMARY_EXIT_STATE_MECHANISM"),
        "EXIT_POLICY_CREATED": False,
        "ENTRY_CHANGED": False,
        "SIZING_CHANGED": False,
        "TRUE_OOS": False,
        "Q1_DURATION_SEPARATES": bool(summary.get("PERSISTENCE_SUPPORTED")),
        "Q2_PROPAGATION_SEPARATES": bool(summary.get("PROPAGATION_SUPPORTED")),
        "Q3_RECOVERY_SEPARATES": bool(summary.get("NON_RECOVERY_SUPPORTED")),
        "Q4_CORE_GOOD_NOT_DESTROYED": bool(supported),
        "Q5_SEQUENCE_MECHANISM": bool(case == "A"),
    }
