"""Residual occupancy-control damage inventory. No EXIT rule. No threshold search."""
from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from research.simple_tech_redesign.exit_residual_rca_spec import (
    CONCENTRATION_WARN,
    DEV_CONTROL_ADDED_N_EXPECTED,
    DEV_CONTROL_CORE_N_EXPECTED,
    DEV_CONTROL_FILL_N_EXPECTED,
    DEV_CONTROL_PNL_EXPECTED,
    DOMINANCE_RATIO,
    FAILURE_CLASSES,
    FWD_CLASS_MIN_N,
    FWD_CONTROL_ADDED_N_EXPECTED,
    FWD_CONTROL_CORE_N_EXPECTED,
    FWD_CONTROL_FILL_N_EXPECTED,
    FWD_CONTROL_PNL_EXPECTED,
    MATERIAL_VS_WINNER_FRAC,
    NOT_MATERIAL_FRAC,
    PROTECTED_CLASSES,
    PROVEN_FAILURE_CLASSES,
    RANK_METRICS,
    RESIDUAL_CLASSES,
    YEN_PARITY_TOL,
)

EPS = 1e-12
RANK_FIELD_MAP = {
    "GROSS_TERMINAL_LOSS": "GROSS_TERMINAL_LOSS",
    "PEAK_TO_CLOSE_GIVEBACK": "peak_to_close_giveback",
    "BELOW_BE_TERMINAL_LOSS": "below_be_terminal_loss",
}

assert set(RANK_FIELD_MAP) == set(RANK_METRICS)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except (TypeError, ValueError):
        return None


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    if n % 2:
        return float(s[n // 2])
    return 0.5 * (s[n // 2 - 1] + s[n // 2])


def _sum(xs: list[float]) -> float:
    return float(sum(xs)) if xs else 0.0


def identity_ok(body: dict[str, Any], *, cohort: str) -> tuple[bool, dict[str, Any]]:
    if cohort == "DEVELOPMENT":
        exp = {
            "fill_n": DEV_CONTROL_FILL_N_EXPECTED,
            "core_n": DEV_CONTROL_CORE_N_EXPECTED,
            "added_n": DEV_CONTROL_ADDED_N_EXPECTED,
            "pnl": DEV_CONTROL_PNL_EXPECTED,
        }
    else:
        exp = {
            "fill_n": FWD_CONTROL_FILL_N_EXPECTED,
            "core_n": FWD_CONTROL_CORE_N_EXPECTED,
            "added_n": FWD_CONTROL_ADDED_N_EXPECTED,
            "pnl": FWD_CONTROL_PNL_EXPECTED,
        }
    got = {
        "fill_n": int(body.get("control_fill_n") or 0),
        "core_n": int(body.get("control_core_n") or 0),
        "added_n": int(body.get("control_added_n") or 0),
        "pnl": float(body.get("control_total_pnl") or 0.0),
    }
    ok = (
        got["fill_n"] == int(exp["fill_n"])
        and got["core_n"] == int(exp["core_n"])
        and got["added_n"] == int(exp["added_n"])
        and abs(float(got["pnl"]) - float(exp["pnl"])) <= float(YEN_PARITY_TOL)
        and bool(body.get("occupancy_sot_ok"))
        and bool(body.get("leftover_ok"))
        and int(len(list(body.get("rows") or []))) == int(exp["fill_n"])
    )
    return bool(ok), {"got": got, "expected": exp, "ok": bool(ok)}


def class_pack(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls = [float(x) for r in rows if (x := _f(r.get("session_close_pnl"))) is not None]
    peaks = [float(x) for r in rows if (x := _f(r.get("peak_executable_pnl"))) is not None]
    gives = [float(x) for r in rows if (x := _f(r.get("peak_to_close_giveback"))) is not None]
    below = [float(x) for r in rows if (x := _f(r.get("below_be_terminal_loss"))) is not None]
    be_below = [
        float(x)
        for r in rows
        if r.get("break_even_reached") and (x := _f(r.get("below_be_terminal_loss"))) is not None
    ]
    losses = [p for p in pnls if p < -EPS]
    mins = [float(x) for r in rows if (x := _f(r.get("temporary_negative_excursion"))) is not None]
    days = Counter(str(r.get("date") or "") for r in rows)
    syms = Counter(str(r.get("symbol") or "") for r in rows)
    loss_by_day: dict[str, float] = {}
    loss_by_sym: dict[str, float] = {}
    for r in rows:
        g = _f(r.get("below_be_terminal_loss"))
        if g is None:
            continue
        loss_by_day[str(r.get("date") or "")] = loss_by_day.get(str(r.get("date") or ""), 0.0) + float(g)
        loss_by_sym[str(r.get("symbol") or "")] = loss_by_sym.get(str(r.get("symbol") or ""), 0.0) + float(g)
    gross = _sum(below)
    top_day = max(loss_by_day.items(), key=lambda kv: kv[1]) if loss_by_day else ("", 0.0)
    top_sym = max(loss_by_sym.items(), key=lambda kv: kv[1]) if loss_by_sym else ("", 0.0)
    day_share = (float(top_day[1]) / gross) if gross > EPS else None
    sym_share = (float(top_sym[1]) / gross) if gross > EPS else None
    worst = min(rows, key=lambda r: float(_f(r.get("session_close_pnl")) or 0.0), default=None)
    return {
        "trade_n": len(rows),
        "CORE_n": sum(1 for r in rows if str(r.get("fill_role") or "") == "CORE"),
        "ADDED_n": sum(1 for r in rows if str(r.get("fill_role") or "") == "ADDED"),
        "session_close_total_pnl": _sum(pnls),
        "median_pnl": _median(pnls),
        "gross_loss": gross,
        "GROSS_TERMINAL_LOSS": gross,
        "peak_executable_profit": _sum([p for p in peaks if p > EPS]),
        "peak_executable_pnl_total": _sum(peaks),
        "median_peak": _median(peaks),
        "peak_to_close_giveback": _sum(gives),
        "median_giveback": _median(gives),
        "below_be_terminal_loss": gross,
        "below_be_after_be": _sum(be_below),
        "positive_peak_reached_n": sum(1 for r in rows if r.get("positive_peak_reached")),
        "break_even_reached_n": sum(1 for r in rows if r.get("break_even_reached")),
        "positive_trade_n": sum(1 for p in pnls if p > EPS),
        "loss_trade_n": len(losses),
        "worst_trade_pnl": _f(worst.get("session_close_pnl")) if worst else None,
        "worst_trade_id": (worst or {}).get("trade_id") or (
            f"{(worst or {}).get('date')}|{(worst or {}).get('symbol')}|{(worst or {}).get('t0')}" if worst else None
        ),
        "temporary_negative_excursion_total": _sum(mins),
        "median_temporary_negative_excursion": _median(mins),
        "day_n": len([k for k in days if k]),
        "top_day": top_day[0],
        "top_day_loss": float(top_day[1]),
        "top_day_share": day_share,
        "top_day_warn": bool(day_share is not None and day_share > float(CONCENTRATION_WARN) + EPS),
        "top_symbol": top_sym[0],
        "top_symbol_loss": float(top_sym[1]),
        "top_symbol_share": sym_share,
        "top_symbol_warn": bool(sym_share is not None and sym_share > float(CONCENTRATION_WARN) + EPS),
        "day_counts": dict(days),
        "symbol_counts": dict(syms),
        "path_types": dict(Counter(str(r.get("path_type") or "OTHER") for r in rows)),
    }


def _ranking_value(pack: dict[str, Any], *, metric: str, scope: str, class_name: str) -> float:
    assert metric in RANK_FIELD_MAP, f"{scope}:{class_name}: unknown ranking metric {metric}"
    field = RANK_FIELD_MAP[metric]
    assert field in pack, f"{scope}:{class_name}: missing aggregate field {field}"
    value = _f(pack[field])
    assert value is not None, f"{scope}:{class_name}: non-numeric aggregate field {field}"
    ranking_value = float(value)
    assert ranking_value == float(pack[field]), (
        f"{scope}:{class_name}:{metric}: ranking value {ranking_value} "
        f"!= by_class[{field}] {pack[field]}"
    )
    assert not (abs(float(pack[field])) > EPS and abs(ranking_value) <= EPS), (
        f"{scope}:{class_name}:{metric}: nonzero aggregate hidden as ranking zero"
    )
    return ranking_value


def _rank_map(
    packs: dict[str, dict[str, Any]], metric: str, *, scope: str
) -> list[dict[str, Any]]:
    items = []
    for name in FAILURE_CLASSES:
        assert name in packs, f"{scope}: missing failure class {name}"
        p = dict(packs[name])
        field = RANK_FIELD_MAP[metric]
        value = _ranking_value(p, metric=metric, scope=scope, class_name=name)
        items.append(
            {
                "class": name,
                "value": value,
                "aggregate_field": field,
                "n": int(p.get("trade_n") or 0),
            }
        )
    items.sort(key=lambda e: (-float(e["value"]), str(e["class"])))
    out = []
    for i, e in enumerate(items, start=1):
        rec = dict(e)
        rec["rank"] = i
        out.append(rec)
    for rec in out:
        source = packs[str(rec["class"])]
        assert float(rec["value"]) == float(source[str(rec["aggregate_field"])]), (
            f"{scope}:{rec['class']}:{metric}: ranked value does not match class aggregate"
        )
    return out


def rankings(by_class: dict[str, Any], *, scope: str) -> dict[str, Any]:
    out = {}
    for metric in RANK_METRICS:
        ranked = _rank_map(by_class, metric, scope=scope)
        out[metric] = {
            "aggregate_field": RANK_FIELD_MAP[metric],
            "order": ranked,
            "rank1": ranked[0]["class"] if ranked else None,
            "rank1_value": ranked[0]["value"] if ranked else None,
        }
    return out


def assert_class_partition(
    by_class: dict[str, dict[str, Any]],
    core_by_class: dict[str, dict[str, Any]],
    added_by_class: dict[str, dict[str, Any]],
    *,
    cohort: str,
) -> dict[str, Any]:
    checks = 0
    for name in FAILURE_CLASSES:
        total = by_class[name]
        core = core_by_class[name]
        added = added_by_class[name]
        assert int(total["trade_n"]) == int(core["trade_n"]) + int(added["trade_n"]), (
            f"{cohort}:{name}: trade_n total != CORE + ADDED"
        )
        checks += 1
        for metric, field in RANK_FIELD_MAP.items():
            total_value = _ranking_value(
                total, metric=metric, scope=f"{cohort}/ALL", class_name=name
            )
            core_value = _ranking_value(
                core, metric=metric, scope=f"{cohort}/CORE", class_name=name
            )
            added_value = _ranking_value(
                added, metric=metric, scope=f"{cohort}/ADDED", class_name=name
            )
            assert abs(total_value - core_value - added_value) <= EPS, (
                f"{cohort}:{name}:{field}: total {total_value} "
                f"!= CORE {core_value} + ADDED {added_value}"
            )
            checks += 1
    return {
        "ok": True,
        "cohort": cohort,
        "checked_failure_class_n": len(FAILURE_CLASSES),
        "checked_metric_n": len(RANK_FIELD_MAP),
        "assertion_n": checks,
    }


def cohort_pack(body: dict[str, Any], *, cohort: str) -> dict[str, Any]:
    rows = list(body.get("rows") or [])
    by_class = {c: class_pack([r for r in rows if str(r.get("residual_class") or "") == c]) for c in RESIDUAL_CLASSES}
    proven_rows = [r for r in rows if str(r.get("residual_class") or "") in PROVEN_FAILURE_CLASSES]
    winner_rows = [r for r in rows if str(r.get("residual_class") or "") in PROTECTED_CLASSES]
    failure_rows = [r for r in rows if str(r.get("residual_class") or "") in FAILURE_CLASSES]
    core_rows = [r for r in rows if str(r.get("fill_role") or "") == "CORE"]
    added_rows = [r for r in rows if str(r.get("fill_role") or "") == "ADDED"]
    core_fail = {c: class_pack([r for r in core_rows if str(r.get("residual_class") or "") == c]) for c in FAILURE_CLASSES}
    added_fail = {c: class_pack([r for r in added_rows if str(r.get("residual_class") or "") == c]) for c in FAILURE_CLASSES}
    id_ok, ident = identity_ok(body, cohort=cohort)
    partition_check = assert_class_partition(
        by_class, core_fail, added_fail, cohort=cohort
    )
    ranks = rankings(by_class, scope=f"{cohort}/ALL")
    core_ranks = rankings(core_fail, scope=f"{cohort}/CORE")
    added_ranks = rankings(added_fail, scope=f"{cohort}/ADDED")
    proven = class_pack(proven_rows)
    winners = class_pack(winner_rows)
    u = dict(by_class.get("U_EARLY_NEVER_BE") or {})
    p_early = dict(by_class.get("P_EARLY_AFTER_BE") or {})
    ptf = dict(by_class.get("P_PROFIT_THEN_FAILURE") or {})
    return {
        "cohort": cohort,
        "days": list(body.get("days") or []),
        "identity": ident,
        "identity_ok": id_ok,
        "occupancy_sot_ok": bool(body.get("occupancy_sot_ok")),
        "leftover_ok": bool(body.get("leftover_ok")),
        "ALL": class_pack(rows),
        "by_class": by_class,
        "PROVEN_FAILURE_DAMAGE": proven,
        "WINNER_PROTECTION_BURDEN": winners,
        "FAILURE_ONLY": class_pack(failure_rows),
        "CORE": class_pack(core_rows),
        "ADDED": class_pack(added_rows),
        "CORE_FAILURE_RANKS": core_ranks,
        "ADDED_FAILURE_RANKS": added_ranks,
        "rankings": ranks,
        "RANKING_FIELD_MAP": dict(RANK_FIELD_MAP),
        "RANKING_INTEGRITY": partition_check,
        "U_gross": float(u.get("GROSS_TERMINAL_LOSS") or 0.0),
        "P_EARLY_gross": float(p_early.get("GROSS_TERMINAL_LOSS") or 0.0),
        "PTF_gross": float(ptf.get("GROSS_TERMINAL_LOSS") or 0.0),
        "PROVEN_gross": float(proven.get("GROSS_TERMINAL_LOSS") or 0.0),
        "WINNER_total_pnl": float(winners.get("session_close_total_pnl") or 0.0),
        "rows": rows,
        "leak": dict(body.get("leak") or {}),
    }


def _dominant(a: float, b: float) -> str:
    if a > b * float(DOMINANCE_RATIO) and a > b + EPS:
        return "A"
    if b > a * float(DOMINANCE_RATIO) and b > a + EPS:
        return "B"
    if a > b + EPS:
        return "A_WEAK"
    if b > a + EPS:
        return "B_WEAK"
    return "TIE"


def questions(dev: dict[str, Any], fwd: dict[str, Any]) -> dict[str, Any]:
    d_rank = str(((dev.get("rankings") or {}).get("GROSS_TERMINAL_LOSS") or {}).get("rank1") or "")
    f_rank = str(((fwd.get("rankings") or {}).get("GROSS_TERMINAL_LOSS") or {}).get("rank1") or "")
    d_u, d_p = float(dev.get("U_gross") or 0.0), float(dev.get("PROVEN_gross") or 0.0)
    f_u, f_p = float(fwd.get("U_gross") or 0.0), float(fwd.get("PROVEN_gross") or 0.0)
    winner = float(dev.get("WINNER_total_pnl") or 0.0)
    proven_vs_u = _dominant(d_p, d_u)
    return {
        "Q1_max_is_U_EARLY_NEVER_BE": d_rank == "U_EARLY_NEVER_BE",
        "Q2_max_is_P_EARLY_AFTER_BE": d_rank == "P_EARLY_AFTER_BE",
        "Q3_max_is_P_PROFIT_THEN_FAILURE": d_rank == "P_PROFIT_THEN_FAILURE",
        "Q4_PROVEN_materially_larger_than_U": proven_vs_u == "A",
        "Q5_DEV_FWD_rank1_agree": d_rank == f_rank and bool(d_rank),
        "Q5_DEV_FWD_family_agree": (d_p >= d_u) == (f_p >= f_u),
        "Q6_target_material_vs_winner": bool(
            max(d_p, d_u) + EPS >= float(MATERIAL_VS_WINNER_FRAC) * max(winner, 0.0) if winner > EPS else max(d_p, d_u) > EPS
        ),
        "DEV_rank1": d_rank,
        "FWD_rank1": f_rank,
        "DEV_U_gross": d_u,
        "DEV_PROVEN_gross": d_p,
        "FWD_U_gross": f_u,
        "FWD_PROVEN_gross": f_p,
        "DEV_WINNER_total_pnl": winner,
        "proven_vs_u_dev": proven_vs_u,
        "proven_vs_u_fwd": _dominant(f_p, f_u),
    }


def decide(dev: dict[str, Any], fwd: dict[str, Any], *, leak_ok: bool, ni_ok: bool, identity_ok_flag: bool) -> dict[str, Any]:
    if not leak_ok or not ni_ok or not identity_ok_flag:
        return {
            "CASE": "E",
            "VERDICT": "SIMPLE_TECH_EXIT_RESIDUAL_LOSS_ARCHITECTURE_INVALID",
            "NEXT": "STOP. Identity, occupancy SoT, integrity, or non-interference failed. FAIL CLOSED.",
            "TRUE_OOS": False,
            "CERTIFIED": False,
            "NEW_EXIT_RULE": False,
            "PRIMARY_NEXT_EXIT_TARGET": None,
        }
    q = questions(dev, fwd)
    d_rank = str(q.get("DEV_rank1") or "")
    f_rank = str(q.get("FWD_rank1") or "")
    d_u, d_p = float(q.get("DEV_U_gross") or 0.0), float(q.get("DEV_PROVEN_gross") or 0.0)
    f_u, f_p = float(q.get("FWD_U_gross") or 0.0), float(q.get("FWD_PROVEN_gross") or 0.0)
    winner = float(q.get("DEV_WINNER_total_pnl") or 0.0)
    fail_max = max(d_u, d_p)
    combined = d_u + d_p
    material = bool(
        fail_max + EPS >= float(MATERIAL_VS_WINNER_FRAC) * winner if winner > EPS else fail_max > EPS
    )
    not_material = bool(
        winner > EPS
        and fail_max + EPS < float(NOT_MATERIAL_FRAC) * winner
        and combined + EPS < 0.15 * winner
    )
    fwd_p_n = int(((fwd.get("by_class") or {}).get("P_EARLY_AFTER_BE") or {}).get("trade_n") or 0) + int(
        ((fwd.get("by_class") or {}).get("P_PROFIT_THEN_FAILURE") or {}).get("trade_n") or 0
    )
    fwd_u_n = int(((fwd.get("by_class") or {}).get("U_EARLY_NEVER_BE") or {}).get("trade_n") or 0)
    fwd_evaluable = (fwd_p_n + fwd_u_n) >= int(FWD_CLASS_MIN_N)
    fwd_reverse = bool(fwd_evaluable and _dominant(f_u, f_p) == "A" and _dominant(d_p, d_u) == "A")
    fwd_reverse_u = bool(fwd_evaluable and _dominant(f_p, f_u) == "A" and _dominant(d_u, d_p) == "A")
    family_contradiction = bool(fwd_reverse or fwd_reverse_u)
    proven_dom = _dominant(d_p, d_u)
    reasons = []
    if not_material:
        case, verdict = "D", "SIMPLE_TECH_EXIT_RESIDUAL_NOT_PRIMARY_BOTTLENECK"
        nxt = "STOP. Residual Technical EXIT damage is not the primary bottleneck versus winner contribution. Stop EXIT research."
        target = None
        reasons.append("not_material_vs_winner")
    elif proven_dom == "A" and (not family_contradiction) and material:
        case, verdict = "A", "SIMPLE_TECH_EXIT_RESIDUAL_PROVEN_BRANCH_PRIORITY"
        nxt = (
            "STOP. Next run: Branch P / PROVEN failure mechanism discovery only. "
            "Do not merge P_EARLY_AFTER_BE and PTF into one EXIT rule. No EXIT policy in this run."
        )
        target = d_rank if d_rank in PROVEN_FAILURE_CLASSES else "PROVEN_FAILURE"
        reasons.append("proven_dominant_dev_fwd_not_reversed")
    elif (_dominant(d_u, d_p) == "A") and material:
        case, verdict = "B", "SIMPLE_TECH_EXIT_RESIDUAL_UNPROVEN_PRIORITY_BUT_ARCHITECTURE_CLOSED"
        nxt = (
            "STOP. U_EARLY_NEVER_BE is the largest residual, but Branch U architecture is closed. "
            "Do not resume BB/EMA/RCI variants. A new independent architecture would be required."
        )
        target = "U_EARLY_NEVER_BE"
        reasons.append("u_dominant_but_architecture_closed")
    else:
        case, verdict = "C", "SIMPLE_TECH_EXIT_RESIDUAL_MIXED_ARCHITECTURE"
        nxt = "STOP. Residual damage is mixed across U / P-EARLY / PTF. Do not add a local EXIT."
        target = None
        reasons.append("mixed_or_fwd_contradiction")
        if family_contradiction:
            reasons.append("dev_fwd_family_contradiction")
        if proven_dom in {"A_WEAK", "B_WEAK", "TIE"}:
            reasons.append("no_1.25x_family_dominance")
    return {
        "CASE": case,
        "VERDICT": verdict,
        "NEXT": nxt,
        "TRUE_OOS": False,
        "CERTIFIED": False,
        "NEW_EXIT_RULE": False,
        "PRIMARY_NEXT_EXIT_TARGET": target,
        "DEV_rank1_GROSS_TERMINAL_LOSS": d_rank,
        "FWD_rank1_GROSS_TERMINAL_LOSS": f_rank,
        "DEV_U_gross": d_u,
        "DEV_PROVEN_gross": d_p,
        "FWD_U_gross": f_u,
        "FWD_PROVEN_gross": f_p,
        "DEV_WINNER_total_pnl": winner,
        "material_vs_winner": material,
        "family_contradiction": family_contradiction,
        "questions": q,
        "reasons": reasons,
        "rank_agreement_gross": q.get("Q5_DEV_FWD_rank1_agree"),
        "family_agreement": q.get("Q5_DEV_FWD_family_agree"),
    }
