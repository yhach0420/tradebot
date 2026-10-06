"""V7 role × native-TF gates and preferred-scale selection. No mixed-TF strategy. No retune."""
from __future__ import annotations

from typing import Any, Optional

from research.simple_tech_entry_family.v4_analyze import _finite, _mean
from research.simple_tech_entry_family.v6_analyze import stage_audit
from research.simple_tech_entry_family.v7_spec import (
    MAJOR_ROLES,
    ROLE_MIN_EXE_FAIL,
    ROLE_MIN_EXE_PASS,
    TF_IDS,
)

SPEED = {"TF1": 0, "TF3": 1, "TF5": 2}
TF_TAG = {"TF1": "1M", "TF3": "3M", "TF5": "5M"}


def _pos_rate(rows: list[dict[str, Any]], key: str) -> Optional[float]:
    vs = [float(r.get(key)) for r in rows if _finite(r.get(key))]
    if not vs:
        return None
    return float(sum(1 for v in vs if v > 1e-12)) / float(len(vs))


def _gt(a: Any, b: Any) -> bool:
    return a is not None and b is not None and float(a) > float(b)


def split_nested(rows: list[dict[str, Any]], pop_key: str | None, pass_key: str) -> dict[str, Any]:
    pop = list(rows) if pop_key is None else [r for r in rows if r.get(pop_key)]
    p_all = [r for r in pop if r.get(pass_key)]
    f_all = [r for r in pop if not r.get(pass_key)]
    exe_p = [r for r in p_all if r.get("executable_signal")]
    exe_f = [r for r in f_all if r.get("executable_signal")]
    return {
        "pop": pop,
        "pass_all": p_all,
        "fail_all": f_all,
        "exe_pass": exe_p,
        "exe_fail": exe_f,
        "pass_n": len(p_all),
        "fail_n": len(f_all),
        "all_n": len(pop),
    }


ROLE_SPLIT = {
    "TREND": {"pop": None, "flag": "s1"},
    "PULLBACK": {"pop": "s1", "flag": "s2"},
    "RCI": {"pop": "s2", "flag": "s3"},
    "PRICE_ACTION": {"pop": "s3", "flag": "s4"},
    "VOLUME": {"pop": None, "flag": "volume"},
    "VOLUME_NESTED": {"pop": "s4", "flag": "s5"},
}


def role_audit(rows: list[dict[str, Any]], role: str, *, integrity_ok: bool) -> dict[str, Any]:
    spec = ROLE_SPLIT[role]
    sp = split_nested(rows, spec["pop"], spec["flag"])
    st = stage_audit(sp["exe_pass"], sp["exe_fail"], all_n=sp["all_n"], pass_n=sp["pass_n"], fail_n=sp["fail_n"])
    p = st.get("PASS") or {}
    f = st.get("FAIL") or {}
    for side, xs in (("PASS", sp["exe_pass"]), ("FAIL", sp["exe_fail"])):
        pack = st[side]
        pack["POSITIVE_RATE_60"] = _pos_rate(xs, "markout_60")
        pack["POSITIVE_RATE_180"] = _pos_rate(xs, "markout_180")
        pack["POSITIVE_RATE_300"] = _pos_rate(xs, "markout_300")
    med180 = _gt(p.get("MARKOUT_180_MEDIAN"), f.get("MARKOUT_180_MEDIAN"))
    med300 = _gt(p.get("MARKOUT_300_MEDIAN"), f.get("MARKOUT_300_MEDIAN"))
    coverage = int(st.get("EXECUTABLE_PASS_N") or 0) >= int(ROLE_MIN_EXE_PASS) and int(st.get("EXECUTABLE_FAIL_N") or 0) >= int(
        ROLE_MIN_EXE_FAIL
    )
    supported = bool(
        integrity_ok
        and coverage
        and bool(st.get("PASS_GT_FAIL_180"))
        and bool(st.get("PASS_GT_FAIL_300"))
        and med180
        and med300
        and bool(st.get("MULTI_DAY"))
        and bool(st.get("EX_BEST_STILL_IMPROVES"))
        and bool(st.get("DROP_TOP_STILL_IMPROVES"))
    )
    st["ROLE"] = role
    st["MEDIAN_PASS_GT_FAIL_180"] = med180
    st["MEDIAN_PASS_GT_FAIL_300"] = med300
    st["COVERAGE_OK"] = coverage
    st["ROLE_SUPPORTED"] = supported
    st["PRE_POP_N"] = sp["all_n"]
    if role == "PRICE_ACTION":
        pre_exe = [r for r in sp["pop"] if r.get("executable_signal")]
        st["PRE_PA_MARKOUT_180_MEAN"] = _mean([r.get("markout_180") for r in pre_exe])
        st["PRE_PA_MARKOUT_300_MEAN"] = _mean([r.get("markout_300") for r in pre_exe])
        st["PA_VS_PRE_180"] = (
            None
            if p.get("MARKOUT_180_MEAN") is None or st["PRE_PA_MARKOUT_180_MEAN"] is None
            else float(p["MARKOUT_180_MEAN"]) - float(st["PRE_PA_MARKOUT_180_MEAN"])
        )
        st["PA_VS_PRE_300"] = (
            None
            if p.get("MARKOUT_300_MEAN") is None or st["PRE_PA_MARKOUT_300_MEAN"] is None
            else float(p["MARKOUT_300_MEAN"]) - float(st["PRE_PA_MARKOUT_300_MEAN"])
        )
    return st


def _rank_key(st: dict[str, Any], tf: str) -> tuple:
    day = int(st.get("DAY_POS") or 0) - int(st.get("DAY_NEG") or 0)
    compared = max(int(st.get("DAY_COMPARED") or 0), 1)
    day_frac = float(st.get("DAY_POS") or 0) / float(compared)
    ex = 1 if st.get("EX_BEST_STILL_IMPROVES") else 0
    e180 = st.get("EFFECT_180")
    e300 = st.get("EFFECT_300")
    cons = 0
    gap = 1e18
    if e180 is not None and e300 is not None:
        if float(e180) > 1e-12 and float(e300) > 1e-12:
            cons = 1
            gap = abs(float(e180) - float(e300))
    return (-day, -day_frac, -ex, -cons, gap, int(SPEED[tf]))


def pick_scale(by_tf: dict[str, dict[str, Any]]) -> str:
    hits = [tf for tf in TF_IDS if (by_tf.get(tf) or {}).get("ROLE_SUPPORTED")]
    if not hits:
        return "NONE"
    if len(hits) == 1:
        return str(hits[0])
    ranked = sorted(hits, key=lambda tf: _rank_key(by_tf[tf], tf))
    best = ranked[0]
    if len(ranked) > 1 and _rank_key(by_tf[ranked[0]], ranked[0]) == _rank_key(by_tf[ranked[1]], ranked[1]):
        return "NO_UNIQUE_SCALE"
    return str(best)


def architecture_decision(pref: dict[str, str], supported_any: dict[str, bool]) -> dict[str, Any]:
    major_set = []
    for role in MAJOR_ROLES:
        p = str(pref.get(role) or "NONE")
        if p not in ("NONE", "NO_UNIQUE_SCALE"):
            major_set.append(p)
    unique_major = sorted(set(major_set))
    conflict = len(unique_major) > 1
    pb, rci, pa = pref.get("PULLBACK"), pref.get("RCI"), pref.get("PRICE_ACTION")
    triple = (pb, rci, pa)
    triple_ok = all(x not in (None, "NONE", "NO_UNIQUE_SCALE") for x in triple) and pb == rci == pa
    any_major = any(bool(supported_any.get(role)) for role in MAJOR_ROLES)
    multi = bool(conflict and (not triple_ok))
    if not any_major:
        verdict = "SIMPLE_TECH_V7_NO_ROLE_SCALE_SUPPORTED"
        nxt = (
            "Timeframe is not the deficiency. Do not add 2m/4m/10m/15m. "
            "Rethink Trend->Pullback->Reversal->Trigger architecture. No mixed-TF strategy. No ENTRY. No EXIT."
        )
        single = None
    elif triple_ok:
        verdict = f"SIMPLE_TECH_V7_SINGLE_TF_{TF_TAG.get(str(pb), str(pb))}"
        nxt = (
            f"Keep single-timeframe architecture on {pb}. "
            "Next: Architecture Role RCA on that scale. Do not mix TFs. No ENTRY. No EXIT."
        )
        single = pb
        multi = False
    elif multi:
        verdict = "SIMPLE_TECH_V7_MULTI_TF_JUSTIFIED"
        nxt = (
            "At least two major roles prefer different native scales. "
            "Next run may precommit a mixed architecture (e.g. slower setup -> faster trigger). "
            "Do not implement mixed TF in this run. No ENTRY. No EXIT."
        )
        single = None
    elif len(unique_major) == 1:
        s = unique_major[0]
        if any(pref.get(r) == "NO_UNIQUE_SCALE" for r in MAJOR_ROLES):
            verdict = "SIMPLE_TECH_V7_NO_UNIQUE_SCALE"
            nxt = "Supported scales do not uniquely resolve per role. Do not pick a winner by mean. No extra TF. No ENTRY. No EXIT."
            single = None
        elif str(pref.get("PULLBACK") or "NONE") == s:
            verdict = f"SIMPLE_TECH_V7_SINGLE_TF_{TF_TAG.get(s, s)}"
            nxt = (
                f"One native scale is preferred among supported major roles ({s}). "
                "Next: Architecture Role RCA on that scale. No mixed TF. No ENTRY. No EXIT."
            )
            single = s
        else:
            verdict = "SIMPLE_TECH_V7_NO_UNIQUE_SCALE"
            nxt = (
                "A major role has a preferred scale but Pullback/RCI/PriceAction do not uniquely support a single architecture scale. "
                "Do not add more timeframes. No ENTRY. No EXIT."
            )
            single = None
    else:
        verdict = "SIMPLE_TECH_V7_NO_UNIQUE_SCALE"
        nxt = "No unique preferred scale. Do not force BEST_TIMEFRAME. No extra TF. No ENTRY. No EXIT."
        single = None
    return {
        "ROLE_SCALE_CONFLICT": bool(conflict),
        "MULTI_TIMEFRAME_ARCHITECTURE_JUSTIFIED": bool(multi),
        "SINGLE_TF_SCALE": single,
        "VERDICT": verdict,
        "NEXT": nxt,
    }


def funnel_of(rows: list[dict[str, Any]]) -> dict[str, int]:
    out = {k: 0 for k in ("s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7")}
    out["s0"] = len(rows)
    for k in ("s1", "s2", "s3", "s4", "s5", "s6", "s7"):
        out[k] = sum(1 for r in rows if r.get(k))
    return out


def slim_stage(st: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in st.items() if k != "day_rows"}
