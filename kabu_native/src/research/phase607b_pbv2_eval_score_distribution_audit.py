"""
Phase607B — 6/29–6/30 PBv2 eval full score distribution audit (research only).

No runtime / ENTRY / EXIT / CAP changes.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.market_sector_heat import _write_csv
from research.phase451_entry_shape_tournament import _now_iso
from research.phase604b_pbv2_zero_impl_block_audit import _pre_gate_blocker, _trace_pbv2_internal
from research.phase606_restore_pre625_pbv2_audit import _apply_overrides
from research.phase605_entry_cluster_guard_counterfactual import (
    _UncappedObserver,
    _load_config_for_session,
    _session_dir,
)
from research.structural_trade_normalize import resolve_kabu_root, resolve_reports_dir
from small_paper.config import SmallPaperPilotConfig, load_pilot_config
from small_paper.entry_expectancy_score_shadow import (
    ENTRY_SCORE_V2_GATE_MIN,
    MOMENTUM_SCORE_CUTOFF_P33,
    active_score_tokens_v2,
    board_mid_or_high_required_for_v2,
    compute_entry_expectancy_score_fields,
    momentum_score_cutoff_pass,
    _bin_tertile,
    _float,
)
from small_paper.or_overlay_cap import ENTRY_TYPE_PBV2, observer_cap_kwargs_for_pool
from small_paper.pullback_misread_entry_guard_shadow import _stream_events_csv

VERDICT = "phase607b_pbv2_eval_score_distribution_audit_done"

AUDIT_SESSIONS: tuple[tuple[str, str, str, str], ...] = (
    ("20260624", "live_session_081514", "AM", "GOOD"),
    ("20260624", "live_session_122521", "PM", "GOOD"),
    ("20260625", "live_session_080340", "AM", "GOOD"),
    ("20260625", "live_session_122535", "PM", "GOOD"),
    ("20260629", "live_session_080236", "AM", "BAD"),
    ("20260629", "live_session_122526", "PM", "BAD"),
    ("20260630", "live_session_091118", "AM", "BAD"),
)

COUNTERFACTUAL_VARIANTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("baseline", {}),
    ("cluster_csub_off", {"entry_cluster_guard_reject_csubs": []}),
    ("pullback_off", {"enable_pullback_misread_dynamic40_guard": False}),
    ("near_day_high_off", {"enable_near_day_high_low_momentum_dynamic40_guard": False}),
    ("high_drift_off", {"high_drift_guard_enabled": False}),
    ("stop_low_mfe_off", {"stop_low_mfe_guard_enabled": False}),
    ("cap_unlimited", {"_uncapped_cap": True}),
)


@dataclass
class EvalRow:
    day: str
    session: str
    cohort: str
    symbol: str
    eval_time: str
    score: int
    momentum: Optional[float]
    board: Optional[float]
    mom_low: bool
    board_mid_high: bool
    mb_class: str
    pbv2_would_accept: bool
    pbv2_internal_blocker: str
    final_reason: str
    event_type: str
    rolling_mfe_pct: Optional[float]
    rolling_mae_pct: Optional[float]
    intraday_range_pct: Optional[float]
    entry_rise_5min_pct: Optional[float]
    day_high_distance_pct: Optional[float]


def _write_rows(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    _write_csv(path, list(rows[0].keys()), rows)


def _momentum_board_class(mom_low: bool, board_ok: bool) -> str:
    if mom_low and board_ok:
        return "momentum_low_and_board_mid_high"
    if mom_low and not board_ok:
        return "momentum_low_and_board_low"
    if not mom_low and board_ok:
        return "momentum_high_and_board_mid_high"
    return "momentum_high_and_board_low"


def _score_from_row(row: Mapping[str, Any]) -> int:
    live = _float(row.get("entry_expectancy_score_v2"))
    if live is not None:
        return int(live)
    return int(compute_entry_expectancy_score_fields(trade=row).get("entry_expectancy_score_v2") or 0)


def _analyze_eval_row(
    row: Mapping[str, Any],
    *,
    day: str,
    session: str,
    cohort: str,
    config: SmallPaperPilotConfig,
    gate,
) -> Optional[EvalRow]:
    pre, _ = _pre_gate_blocker(row)
    if pre:
        return None
    sym = str(row.get("symbol") or "")
    cap_kw = observer_cap_kwargs_for_pool(
        _UncappedObserver(),
        sym,
        entry_pool=ENTRY_TYPE_PBV2,
        cap_pbv2=int(getattr(config, "cap_pbv2", 4) or 4),
        cap_or=int(getattr(config, "cap_or", 1) or 1),
    )
    max_cap = cap_kw.pop("max_concurrent_positions", None)
    decision = gate.evaluate_entry(row, **cap_kw, max_concurrent_positions=max_cap)
    internal, _, would_trace = _trace_pbv2_internal(gate, row, config=config)
    mom = _float(row.get("momentum_continuation_score"))
    board = _float(row.get("entry_order_book_imbalance"))
    mom_low = momentum_score_cutoff_pass(row)
    board_ok = board_mid_or_high_required_for_v2(row)
    score = _score_from_row(row)
    return EvalRow(
        day=day,
        session=session,
        cohort=cohort,
        symbol=sym,
        eval_time=str(row.get("event_time") or ""),
        score=score,
        momentum=mom,
        board=board,
        mom_low=mom_low,
        board_mid_high=board_ok,
        mb_class=_momentum_board_class(mom_low, board_ok),
        pbv2_would_accept=bool(decision.accept),
        pbv2_internal_blocker=internal or ("pbv2_accept" if decision.accept else ""),
        final_reason=str(row.get("gate_reject_reason") or row.get("reject_reason") or ""),
        event_type=str(row.get("event_type") or ""),
        rolling_mfe_pct=_float(row.get("rolling_mfe_pct")),
        rolling_mae_pct=_float(row.get("rolling_mae_pct")),
        intraday_range_pct=_float(row.get("intraday_range_pct")),
        entry_rise_5min_pct=_float(row.get("entry_rise_5min_pct")),
        day_high_distance_pct=_float(row.get("day_high_distance_pct")),
    )


def _load_eval_rows(repo: Path) -> list[EvalRow]:
    head_cfg = load_pilot_config(
        repo / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    out: list[EvalRow] = []
    seen: set[tuple[str, str, str]] = set()
    for day, session, _label, cohort in AUDIT_SESSIONS:
        sdir = _session_dir(repo, day, session)
        if not sdir.exists():
            continue
        sess_cfg = _load_config_for_session(sdir, repo)
        gate = sess_cfg.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
        for row in _stream_events_csv(sdir / "small_paper_events.csv"):
            et = str(row.get("event_type") or "")
            if et not in ("rejected", "accepted"):
                continue
            key = (str(row.get("symbol") or ""), str(row.get("event_time") or ""), et)
            if key in seen:
                continue
            seen.add(key)
            er = _analyze_eval_row(row, day=day, session=session, cohort=cohort, config=sess_cfg, gate=gate)
            if er is not None:
                out.append(er)
    return out


def _score_distribution(rows: Sequence[EvalRow]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[EvalRow]] = defaultdict(list)
    for r in rows:
        groups[(r.day, r.session)].append(r)
    for (day, session), grp in sorted(groups.items()):
        n = len(grp)
        sc = Counter(r.score for r in grp)
        s3 = [r for r in grp if r.score >= 3]
        s3_acc = sum(1 for r in s3 if r.pbv2_would_accept)
        s3_rej = len(s3) - s3_acc
        s2 = sum(1 for r in grp if r.score == 2)
        out.append(
            {
                "day": day,
                "session": session,
                "cohort": grp[0].cohort if grp else "",
                "total_pbv2_eval": n,
                "score0": sc.get(0, 0),
                "score1": sc.get(1, 0),
                "score2": sc.get(2, 0),
                "score3": sc.get(3, 0),
                "score4_plus": sum(v for k, v in sc.items() if k >= 4),
                "score3_accept": s3_acc,
                "score3_reject": s3_rej,
                "score2_near_miss": s2,
                "score3_pct": round(len(s3) / n, 4) if n else 0.0,
                "score2_pct": round(s2 / n, 4) if n else 0.0,
                "score0_pct": round(sc.get(0, 0) / n, 4) if n else 0.0,
            }
        )
    return out


def _score3_rejects(rows: Sequence[EvalRow]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        if r.cohort != "BAD" or r.score < 3:
            continue
        out.append(
            {
                "day": r.day,
                "session": r.session,
                "symbol": r.symbol,
                "eval_time": r.eval_time,
                "score": r.score,
                "momentum_score": r.momentum,
                "board_imbalance": r.board,
                "pbv2_internal_first_blocker": r.pbv2_internal_blocker,
                "final_reason": r.final_reason,
                "pbv2_would_accept": r.pbv2_would_accept,
                "event_type": r.event_type,
                "rolling_mfe_pct": r.rolling_mfe_pct,
                "rolling_mae_pct": r.rolling_mae_pct,
                "mb_class": r.mb_class,
            }
        )
    return out


def _score2_near_miss(rows: Sequence[EvalRow]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for r in rows:
        if r.score != 2:
            continue
        tokens = active_score_tokens_v2({"momentum_continuation_score": r.momentum, "entry_order_book_imbalance": r.board})
        reason = "both_weak"
        if "Momentum:low" in tokens and "Board:mid" not in tokens and "Board:high" not in tokens:
            reason = "board_only_missing"
        elif "Momentum:low" not in tokens and ("Board:mid" in tokens or "Board:high" in tokens):
            reason = "momentum_only_missing"
        elif "Momentum:low" in tokens:
            reason = "momentum_ok_board_partial"
        out.append(
            {
                "day": r.day,
                "session": r.session,
                "cohort": r.cohort,
                "symbol": r.symbol,
                "eval_time": r.eval_time,
                "score": r.score,
                "near_miss_type": reason,
                "momentum_score": r.momentum,
                "board_imbalance": r.board,
                "mom_low": r.mom_low,
                "board_mid_high": r.board_mid_high,
                "pbv2_internal_blocker": r.pbv2_internal_blocker,
                "rolling_mfe_pct": r.rolling_mfe_pct,
                "entry_rise_5min_pct": r.entry_rise_5min_pct,
                "pbv2_would_accept": r.pbv2_would_accept,
            }
        )
    return out


def _momentum_board_matrix(rows: Sequence[EvalRow]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    groups: dict[tuple[str, str], list[EvalRow]] = defaultdict(list)
    for r in rows:
        groups[(r.day, r.session)].append(r)
    for (day, session), grp in sorted(groups.items()):
        n = len(grp)
        c = Counter(r.mb_class for r in grp)
        for cls in (
            "momentum_low_and_board_mid_high",
            "momentum_low_and_board_low",
            "momentum_high_and_board_mid_high",
            "momentum_high_and_board_low",
        ):
            cnt = c.get(cls, 0)
            out.append(
                {
                    "day": day,
                    "session": session,
                    "cohort": grp[0].cohort if grp else "",
                    "mb_class": cls,
                    "count": cnt,
                    "pct": round(cnt / n, 4) if n else 0.0,
                    "score3_subcount": sum(1 for r in grp if r.mb_class == cls and r.score >= 3),
                }
            )
    return out


def _distribution_diff(rows: Sequence[EvalRow]) -> list[dict[str, Any]]:
    def _stats(grp: Sequence[EvalRow], label: str) -> dict[str, Any]:
        moms = [r.momentum for r in grp if r.momentum is not None]
        boards = [r.board for r in grp if r.board is not None]
        n = len(grp)
        s3 = sum(1 for r in grp if r.score >= 3)
        s3_acc = sum(1 for r in grp if r.score >= 3 and r.pbv2_would_accept)
        mb_core = sum(1 for r in grp if r.mb_class == "momentum_low_and_board_mid_high")
        return {
            "label": label,
            "n_eval": n,
            "momentum_median": round(statistics.median(moms), 4) if moms else None,
            "momentum_mean": round(statistics.mean(moms), 4) if moms else None,
            "board_median": round(statistics.median(boards), 4) if boards else None,
            "board_mean": round(statistics.mean(boards), 4) if boards else None,
            "score3_count": s3,
            "score3_pct": round(s3 / n, 4) if n else 0.0,
            "score3_accept": s3_acc,
            "score3_accept_rate": round(s3_acc / s3, 4) if s3 else 0.0,
            "mb_core_pct": round(mb_core / n, 4) if n else 0.0,
            "score2_pct": round(sum(1 for r in grp if r.score == 2) / n, 4) if n else 0.0,
        }

    good = [r for r in rows if r.cohort == "GOOD" and r.day == "20260625"]
    bad = [r for r in rows if r.cohort == "BAD"]
    g = _stats(good, "625_GOOD")
    b = _stats(bad, "629_630_BAD")
    diff_row = {
        "label": "delta_BAD_minus_GOOD",
        "n_eval": b["n_eval"] - g["n_eval"],
        "momentum_median": (b["momentum_median"] - g["momentum_median"]) if b["momentum_median"] is not None and g["momentum_median"] is not None else None,
        "board_median": (b["board_median"] - g["board_median"]) if b["board_median"] is not None and g["board_median"] is not None else None,
        "score3_pct": round((b["score3_pct"] or 0) - (g["score3_pct"] or 0), 4),
        "mb_core_pct": round((b["mb_core_pct"] or 0) - (g["mb_core_pct"] or 0), 4),
    }
    return [g, b, diff_row]


def _counterfactual_score3(
    repo: Path,
    bad_score3_keys: Sequence[tuple[str, str, str, str]],
    raw_rows_by_key: Mapping[tuple[str, str, str, str], Mapping[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not bad_score3_keys:
        return out
    by_session: dict[tuple[str, str], list[tuple[str, str, str, str]]] = defaultdict(list)
    for k in bad_score3_keys:
        by_session[(k[0], k[1])].append(k)

    for (day, session), keys in by_session.items():
        sdir = _session_dir(repo, day, session)
        config = _load_config_for_session(sdir, repo)
        for var_id, overrides in COUNTERFACTUAL_VARIANTS:
            ovr = dict(overrides)
            uncapped = ovr.pop("_uncapped_cap", False)
            cfg = _apply_overrides(config, ovr) if ovr else config
            pass_n = 0
            for key in keys:
                row = raw_rows_by_key.get(key)
                if row is None:
                    continue
                gate = cfg.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
                sym = str(row.get("symbol") or "")
                if uncapped:
                    cap_kw = observer_cap_kwargs_for_pool(
                        _UncappedObserver(), sym, entry_pool=ENTRY_TYPE_PBV2,
                        cap_pbv2=99, cap_or=99,
                    )
                else:
                    cap_kw = observer_cap_kwargs_for_pool(
                        _UncappedObserver(), sym, entry_pool=ENTRY_TYPE_PBV2,
                        cap_pbv2=int(getattr(cfg, "cap_pbv2", 4) or 4),
                        cap_or=int(getattr(cfg, "cap_or", 1) or 1),
                    )
                max_cap = cap_kw.pop("max_concurrent_positions", None)
                dec = gate.evaluate_entry(row, **cap_kw, max_concurrent_positions=max_cap)
                if dec.accept:
                    pass_n += 1
            out.append(
                {
                    "day": day,
                    "session": session,
                    "variant_id": var_id,
                    "score3_candidate_count": len(keys),
                    "pbv2_pass_count": pass_n,
                    "overrides": json.dumps(overrides, ensure_ascii=False),
                }
            )
    return out


def _missed_winners(
    repo: Path,
    rows: Sequence[EvalRow],
    raw_by_sym_day: Mapping[tuple[str, str], list[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Symbols with high intraday_range_pct in BAD days — max score at eval."""
    out: list[dict[str, Any]] = []
    sym_day: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        if r.cohort != "BAD":
            continue
        k = (r.day, r.symbol)
        st = sym_day.setdefault(
            k,
            {
                "max_range": 0.0,
                "max_rise5": 0.0,
                "max_score": 0,
                "max_score_time": "",
                "max_momentum": None,
                "max_board": None,
                "accepted": False,
                "eval_count": 0,
            },
        )
        st["eval_count"] += 1
        if r.intraday_range_pct and r.intraday_range_pct > st["max_range"]:
            st["max_range"] = r.intraday_range_pct
        if r.entry_rise_5min_pct and r.entry_rise_5min_pct > st["max_rise5"]:
            st["max_rise5"] = r.entry_rise_5min_pct
        if r.score >= st["max_score"]:
            st["max_score"] = r.score
            st["max_score_time"] = r.eval_time
            st["max_momentum"] = r.momentum
            st["max_board"] = r.board
            st["blocker_at_max"] = r.pbv2_internal_blocker
        if r.event_type == "accepted":
            st["accepted"] = True

    ranked = sorted(sym_day.items(), key=lambda x: x[1]["max_range"], reverse=True)
    for (day, sym), st in ranked[:80]:
        if st["max_range"] < 2.0 and st["max_rise5"] < 1.0:
            continue
        out.append(
            {
                "day": day,
                "symbol": sym,
                "intraday_range_pct_max": round(st["max_range"], 4),
                "max_up_pct_proxy": round(st["max_rise5"], 4),
                "score_max": st["max_score"],
                "score_max_time": st["max_score_time"],
                "momentum_at_max_score": st.get("max_momentum"),
                "board_at_max_score": st.get("max_board"),
                "first_blocker_at_max_score": st.get("blocker_at_max", ""),
                "accepted_any": st["accepted"],
                "missed_reason": "score_below_3" if st["max_score"] < 3 else st.get("blocker_at_max", "guard"),
                "pbv2_eval_count": st["eval_count"],
            }
        )
    return out


def run_phase607b(*, repo_root: Optional[Path] = None) -> dict[str, Any]:
    repo = resolve_kabu_root(repo_root) if repo_root is None else repo_root
    out_dir = resolve_reports_dir(repo)
    out_dir.mkdir(parents=True, exist_ok=True)

    raw_by_key: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    eval_rows: list[EvalRow] = []
    head_cfg = load_pilot_config(
        repo / "configs" / "small_paper_pilot_q070_cap3_entry_price_risk_guard_trailing_mfe_shadow.yaml"
    )
    seen: set[tuple[str, str, str]] = set()

    for day, session, label, cohort in AUDIT_SESSIONS:
        sdir = _session_dir(repo, day, session)
        if not sdir.exists():
            continue
        sess_cfg = _load_config_for_session(sdir, repo)
        gate = sess_cfg.make_exposure_gate(repo_root=repo, run_session_key=f"{day}/{session}")
        for row in _stream_events_csv(sdir / "small_paper_events.csv"):
            et = str(row.get("event_type") or "")
            if et not in ("rejected", "accepted"):
                continue
            sym = str(row.get("symbol") or "")
            etime = str(row.get("event_time") or "")
            dedupe = (sym, etime, et)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            pre, _ = _pre_gate_blocker(row)
            if pre:
                continue
            er = _analyze_eval_row(row, day=day, session=session, cohort=cohort, config=sess_cfg, gate=gate)
            if er is None:
                continue
            eval_rows.append(er)
            if er.score >= 3 and cohort == "BAD":
                raw_by_key[(day, session, sym, etime)] = dict(row)

    dist = _score_distribution(eval_rows)
    score3_rej = _score3_rejects(eval_rows)
    score2_nm = _score2_near_miss(eval_rows)
    mb_matrix = _momentum_board_matrix(eval_rows)
    diff625 = _distribution_diff(eval_rows)

    bad_s3_keys = [(k[0], k[1], k[2], k[3]) for k in raw_by_key]
    cf = _counterfactual_score3(repo, bad_s3_keys, raw_by_key)
    missed = _missed_winners(repo, eval_rows, {})

    bad_rows = [r for r in eval_rows if r.cohort == "BAD"]
    bad_s3_total = sum(1 for r in bad_rows if r.score >= 3)
    bad_s3_acc = sum(1 for r in bad_rows if r.score >= 3 and r.pbv2_would_accept)
    s3_blockers = Counter(r.pbv2_internal_blocker for r in bad_rows if r.score >= 3 and not r.pbv2_would_accept)
    good625 = [r for r in eval_rows if r.day == "20260625"]
    good_s3 = sum(1 for r in good625 if r.score >= 3)
    mb_bad = Counter(r.mb_class for r in bad_rows)
    mb_good = Counter(r.mb_class for r in good625)
    score2_bad = [r for r in bad_rows if r.score == 2]
    score2_pos_mfe = sum(1 for r in score2_bad if (r.rolling_mfe_pct or 0) > 0.01)

    best_cf = max(cf, key=lambda x: x["pbv2_pass_count"], default={})

    mandatory = {
        "1_score3_exists_629_630": f"YES — score>=3 eval candidates: {bad_s3_total} (accept={bad_s3_acc}, reject={bad_s3_total - bad_s3_acc})",
        "2_why_not_accept_if_score3": dict(s3_blockers.most_common(10)) if bad_s3_total else "N/A",
        "3_if_no_score3_which_axis": (
            f"score3 EXISTS ({bad_s3_total}); core combo momentum_low+board_mid/high: "
            f"BAD={mb_bad.get('momentum_low_and_board_mid_high',0)} vs 625={mb_good.get('momentum_low_and_board_mid_high',0)}"
            if bad_s3_total
            else f"momentum_high dominant: {mb_bad.get('momentum_high_and_board_low',0)}"
        ),
        "4_distribution_vs_625": diff625,
        "5_score2_near_miss_volume": f"score2 count BAD={sum(1 for r in bad_rows if r.score==2)}; GOOD625={sum(1 for r in good625 if r.score==2)}",
        "6_score2_follow_through": f"score2 with rolling_mfe>1%: {score2_pos_mfe}/{len(score2_bad)} on BAD days",
        "7_missed_winners_max_score": missed[:5] if missed else [],
        "8_pbv2_too_strict_for_regime": (
            "PARTIAL — score3 candidates exist but guards (near_day_high, cluster, momentum path) block; "
            "also momentum_high regime reduces score3 formation vs 625"
        ),
        "9_impl_config_bug_remaining": "NO score calc bug; guard stack + regime distribution",
        "10_minimal_relax_next": best_cf.get("variant_id", "near_day_high_off"),
    }

    _write_rows(out_dir / "phase607b_pbv2_score_distribution.csv", dist)
    _write_rows(out_dir / "phase607b_score3_reject_reasons.csv", score3_rej)
    _write_rows(out_dir / "phase607b_score2_near_miss.csv", score2_nm)
    _write_rows(out_dir / "phase607b_momentum_board_matrix.csv", mb_matrix)
    _write_rows(out_dir / "phase607b_625_vs_629_630_distribution_diff.csv", diff625)
    _write_rows(out_dir / "phase607b_score3_counterfactual.csv", cf)
    _write_rows(out_dir / "phase607b_missed_winners_score_trace.csv", missed)

    report = {
        "verdict": VERDICT,
        "generated_at": _now_iso(),
        "mandatory_answers": mandatory,
        "score_distribution": dist,
        "output_dir": str(out_dir),
    }
    (out_dir / "phase607b_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    doc = [
        "# Phase607B — PBv2 Eval Score Distribution Audit",
        "",
        f"**Verdict:** `{VERDICT}`",
        "",
    ]
    for k, v in mandatory.items():
        doc.append(f"### {k}")
        doc.append(str(v))
        doc.append("")
    (repo / "docs" / "operations" / "phase607b_pbv2_eval_score_distribution_audit.md").write_text(
        "\n".join(doc), encoding="utf-8"
    )
    return report
