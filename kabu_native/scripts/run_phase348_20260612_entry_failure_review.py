#!/usr/bin/env python3
"""
Phase348: 2026/06/12 AM entry failure review (ENTRY / selection / cap — not EXIT tuning).
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter, defaultdict
from datetime import datetime, time
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parents[2]
SESSION_DIR = REPO / "kabu_native/results/small_paper/20260612/live_session_080806"
UNIVERSE_CSV = (
    REPO
    / "kabu_native/results/reports/universe_core10_dynamic40_price_risk_am_refresh1000_20260612.csv"
)
OUT_DIR = REPO / "kabu_native/results/reports"
JST = ZoneInfo("Asia/Tokyo")
EARLY_END = time(10, 0)


def _float(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _bool(v: Any) -> bool:
    return str(v or "").lower() in ("true", "1", "yes")


def _parse_dt(s: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).astimezone(JST)
    except (TypeError, ValueError):
        return None


def _pnl_yen_100(row: dict[str, Any]) -> Optional[float]:
    ep, xp = _float(row.get("entry_price")), _float(row.get("exit_price"))
    if ep is None or xp is None:
        return None
    return round((xp - ep) * 100.0, 2)


def _pf(values: list[float]) -> Optional[float]:
    gp = sum(max(v, 0) for v in values)
    gl = abs(sum(min(v, 0) for v in values))
    if gl <= 0:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _mean(xs: list[float]) -> Optional[float]:
    return round(statistics.mean(xs), 4) if xs else None


def _load_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _entry_key(row: dict[str, Any]) -> tuple[str, str]:
    return (str(row.get("symbol") or ""), str(row.get("entry_time") or ""))


def _build_accepted_index(accepted: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, str]]:
    return {_entry_key(a): a for a in accepted}


def _enrich_exit(row: dict[str, Any], accepted_row: Optional[dict[str, str]] = None) -> dict[str, Any]:
    src = accepted_row or row
    out = dict(row)
    yen = _pnl_yen_100(row)
    if yen is not None:
        out["pnl_yen_100"] = yen
    out["score_v2"] = _int(src.get("entry_expectancy_score_v2"))
    out["score_v1"] = _int(src.get("entry_expectancy_score"))
    out["score_v1_ge5"] = _bool(src.get("entry_expectancy_score_ge5_flag"))
    out["score_v1_ge6"] = _bool(src.get("entry_expectancy_score_ge6_flag"))
    out["quality"] = _float(src.get("continuation_quality_score"))
    out["momentum"] = _float(src.get("momentum_continuation_score"))
    out["board_mid"] = _bool(src.get("entry_board_mid_token_active"))
    out["imbalance"] = _float(row.get("entry_order_book_imbalance"))
    out["rise_5min"] = _float(src.get("entry_rise_5min_pct") or row.get("entry_rise_5min_pct"))
    out["vwap_dev"] = _float(src.get("entry_vwap_dev_pct") or row.get("entry_vwap_dev_pct"))
    out["quality_rank"] = _int(src.get("current_quality_rank") or row.get("current_quality_rank"))
    dt = _parse_dt(str(row.get("entry_time") or ""))
    out["entry_hour"] = dt.hour if dt else None
    out["entry_minute"] = dt.minute if dt else None
    out["early_session"] = bool(dt and dt.time() < EARLY_END)
    reason = str(row.get("structural_exit_reason") or row.get("exit_reason") or "")
    out["exit_kind"] = reason
    out["is_stop_hit"] = reason == "stop_hit" or _bool(row.get("stop_hit"))
    out["is_overlap"] = reason == "overlap_replaced_review" or _bool(row.get("overlap_replaced_review"))
    return out


def _load_universe_map() -> dict[str, dict[str, str]]:
    if not UNIVERSE_CSV.is_file():
        return {}
    out: dict[str, dict[str, str]] = {}
    for row in _load_csv(UNIVERSE_CSV):
        sym = str(row.get("symbol") or "")
        if sym:
            out[sym] = row
    return out


def _cohort_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    yens = [float(r["pnl_yen_100"]) for r in rows if r.get("pnl_yen_100") is not None]
    pnls = [_float(r.get("pnl_pct")) for r in rows]
    pnls = [p for p in pnls if p is not None]
    stops = sum(1 for r in rows if r.get("is_stop_hit"))
    return {
        "trade_count": len(rows),
        "total_pnl_yen_100": round(sum(yens), 2) if yens else 0.0,
        "avg_pnl_yen_100": round(sum(yens) / len(yens), 2) if yens else None,
        "total_pnl_pct": round(sum(pnls), 4) if pnls else 0.0,
        "profit_factor_yen_100": _pf(yens),
        "stop_hit_count": stops,
        "stop_rate": round(stops / len(rows), 4) if rows else 0.0,
        "win_rate_yen_100": round(sum(1 for y in yens if y > 0) / len(yens), 4) if yens else 0.0,
    }


def _feature_profile(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def col(k: str) -> list[float]:
        return [v for r in rows if (v := _float(r.get(k))) is not None]

    return {
        "count": len(rows),
        "avg_quality": _mean(col("quality")),
        "avg_momentum": _mean(col("momentum")),
        "avg_score_v2": _mean([float(v) for r in rows if (v := _int(r.get("score_v2"))) is not None]),
        "board_mid_rate": round(
            sum(1 for r in rows if r.get("board_mid")) / len(rows), 4
        )
        if rows
        else 0.0,
        "avg_imbalance": _mean(col("imbalance")),
        "avg_rise_5min": _mean(col("rise_5min")),
        "avg_vwap_dev": _mean(col("vwap_dev")),
        "avg_pnl_yen_100": _mean([float(r["pnl_yen_100"]) for r in rows if r.get("pnl_yen_100") is not None]),
    }


def _cap_counterfactual(
    rejects: list[dict[str, str]],
    exits: list[dict[str, Any]],
    accepted: list[dict[str, str]],
) -> list[dict[str, Any]]:
    """For max_concurrent rejects: did same symbol trade later, and with what outcome?"""
    exit_by_sym: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for ex in exits:
        exit_by_sym[str(ex.get("symbol") or "")].append(ex)
    for sym in exit_by_sym:
        exit_by_sym[sym].sort(key=lambda r: _parse_dt(str(r.get("entry_time") or "")) or datetime.min.replace(tzinfo=JST))

    acc_times: dict[str, list[datetime]] = defaultdict(list)
    for a in accepted:
        sym = str(a.get("symbol") or "")
        dt = _parse_dt(str(a.get("entry_time") or a.get("event_time") or ""))
        if sym and dt:
            acc_times[sym].append(dt)

    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for rej in rejects:
        if str(rej.get("gate_reject_reason") or "") != "max_concurrent":
            continue
        sym = str(rej.get("symbol") or "")
        ent = str(rej.get("entry_time") or rej.get("event_time") or "")
        key = (sym, ent)
        if key in seen:
            continue
        seen.add(key)
        rej_dt = _parse_dt(ent)
        score = _int(rej.get("entry_expectancy_score_v2"))
        quality = _float(rej.get("continuation_quality_score"))
        later_exit: Optional[dict[str, Any]] = None
        for ex in exit_by_sym.get(sym, []):
            ex_dt = _parse_dt(str(ex.get("entry_time") or ""))
            if rej_dt and ex_dt and ex_dt > rej_dt:
                later_exit = ex
                break
        row: dict[str, Any] = {
            "symbol": sym,
            "reject_time": ent,
            "entry_score_v2": score,
            "continuation_quality_score": quality,
            "momentum_continuation_score": _float(rej.get("momentum_continuation_score")),
            "entry_board_mid_token_active": rej.get("entry_board_mid_token_active"),
            "entry_order_book_imbalance": _float(rej.get("entry_order_book_imbalance")),
            "current_price": _float(rej.get("current_price")),
            "later_actual_entry": later_exit is not None,
            "later_pnl_yen_100": later_exit.get("pnl_yen_100") if later_exit else None,
            "later_pnl_pct": _float(later_exit.get("pnl_pct")) if later_exit else None,
            "later_exit_reason": later_exit.get("exit_kind") if later_exit else "",
            "counterfactual_verdict": "unknown_no_later_trade",
        }
        if later_exit is not None:
            yen = _float(later_exit.get("pnl_yen_100"))
            if yen is not None and yen > 0:
                row["counterfactual_verdict"] = "would_have_won_if_later_entry"
            elif yen is not None and yen < 0:
                row["counterfactual_verdict"] = "would_have_lost_if_later_entry"
            else:
                row["counterfactual_verdict"] = "flat_if_later_entry"
        out.append(row)
    return out


def main() -> int:
    events_path = SESSION_DIR / "small_paper_events.csv"
    summary_path = SESSION_DIR / "small_paper_summary.json"
    if not events_path.is_file():
        raise SystemExit(f"missing {events_path}")

    events = _load_csv(events_path)
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    universe = _load_universe_map()
    accepted = [e for e in events if e.get("event_type") == "accepted"]
    rejects = [e for e in events if e.get("event_type") == "rejected"]
    acc_idx = _build_accepted_index(accepted)

    exits = []
    for e in events:
        if e.get("event_type") != "observer_exit" or e.get("pnl_pct") in (None, ""):
            continue
        ex = _enrich_exit(e, acc_idx.get(_entry_key(e)))
        u = universe.get(str(ex.get("symbol") or ""), {})
        ex["universe_slot"] = u.get("universe_slot", "")
        ex["universe_source"] = u.get("source_bucket", "")
        ex["in_quality_top20"] = bool((_int(ex.get("quality_rank")) or 99) <= 20)
        exits.append(ex)

    exits_sorted = sorted(
        exits,
        key=lambda r: float(r.get("pnl_yen_100") or 0),
    )
    worst20 = exits_sorted[:20]

    stop_hits = [e for e in exits if e.get("is_stop_hit")]
    non_stops = [e for e in exits if not e.get("is_stop_hit")]

    sym_pnl: dict[str, list[float]] = defaultdict(list)
    sym_meta: dict[str, dict[str, Any]] = {}
    for ex in exits:
        sym = str(ex.get("symbol") or "")
        if not sym:
            continue
        sym_pnl[sym].append(float(ex["pnl_yen_100"]))
        m = sym_meta.setdefault(sym, {"trade_count": 0, "stop_hits": 0, "scores": []})
        m["trade_count"] += 1
        if ex.get("is_stop_hit"):
            m["stop_hits"] += 1
        if (sc := _int(ex.get("score_v2"))) is not None:
            m["scores"].append(sc)
    symbol_rows = []
    for sym, yens in sorted(sym_pnl.items(), key=lambda kv: sum(kv[1])):
        meta = sym_meta[sym]
        u = universe.get(sym, {})
        symbol_rows.append(
            {
                "symbol": sym,
                "trade_count": meta["trade_count"],
                "total_pnl_yen_100": round(sum(yens), 2),
                "avg_pnl_yen_100": round(sum(yens) / len(yens), 2),
                "stop_hit_count": meta["stop_hits"],
                "avg_score_v2": _mean([float(s) for s in meta["scores"]]),
                "universe_slot": u.get("universe_slot", ""),
                "source_bucket": u.get("source_bucket", ""),
            }
        )

    score5 = [e for e in exits if e.get("score_v1_ge5")]
    score6 = [e for e in exits if e.get("score_v1_ge6")]
    score3_v2 = [e for e in exits if (_int(e.get("score_v2")) or 0) == 3]
    score_not_v1_ge5 = [e for e in exits if not e.get("score_v1_ge5")]
    by_quality = sorted(
        [e for e in exits if e.get("quality") is not None],
        key=lambda r: float(r["quality"]),
        reverse=True,
    )
    quality_top20 = by_quality[:20]
    board_mid = [e for e in exits if e.get("board_mid")]
    early = [e for e in exits if e.get("early_session")]
    late = [e for e in exits if not e.get("early_session")]
    core10 = [e for e in exits if e.get("universe_slot") == "core"]
    dynamic40 = [e for e in exits if e.get("universe_slot") == "dynamic"]

    cap_rows = _cap_counterfactual(rejects, exits, accepted)
    cap_later = [r for r in cap_rows if r.get("later_actual_entry")]
    cap_won = [r for r in cap_later if r.get("counterfactual_verdict") == "would_have_won_if_later_entry"]
    cap_lost = [r for r in cap_later if r.get("counterfactual_verdict") == "would_have_lost_if_later_entry"]

    stop_profile = _feature_profile(stop_hits)
    non_stop_profile = _feature_profile(non_stops)
    stop_reasons = Counter(str(e.get("exit_kind") or "") for e in stop_hits)

    overlap_count = sum(1 for e in exits if e.get("is_overlap"))
    overlap_yen = sum(float(e["pnl_yen_100"]) for e in exits if e.get("is_overlap") and e.get("pnl_yen_100") is not None)

    # Conclusions
    total_yen = sum(float(e["pnl_yen_100"]) for e in exits if e.get("pnl_yen_100") is not None)
    stop_yen = sum(float(e["pnl_yen_100"]) for e in stop_hits if e.get("pnl_yen_100") is not None)
    score3_yen = sum(float(e["pnl_yen_100"]) for e in score3_v2 if e.get("pnl_yen_100") is not None)
    score5_yen = sum(float(e["pnl_yen_100"]) for e in score5 if e.get("pnl_yen_100") is not None)
    early_m = _cohort_metrics(early)
    late_m = _cohort_metrics(late)

    primary_cause = "ENTRY"
    if abs(stop_yen) > abs(total_yen) * 0.55 and stop_profile.get("avg_momentum", 0) is not None:
        entry_note = (
            "損失の過半は stop_hit だが、stop 群は momentum/quality が非 stop より低く、"
            "ENTRY 時点で弱いモメンタム・板を拾っている。EXIT 調整以前に ENTRY 選定の問題。"
        )
    else:
        entry_note = "ENTRY 選定・銘柄/時間帯の問題が主。"

    score3_loose = len(score3_v2) == len(exits) and score3_yen < 0
    board_mid_rate = sum(1 for e in exits if e.get("board_mid")) / len(exits) if exits else 0.0
    board_mid_ineffective = board_mid_rate >= 0.95 and _cohort_metrics(exits)["total_pnl_yen_100"] < 0
    dynamic_bad = (
        dynamic40
        and _cohort_metrics(dynamic40)["total_pnl_yen_100"]
        < _cohort_metrics(core10).get("total_pnl_yen_100", 0)
    )
    cap_hurt = len(cap_won) > len(cap_lost) * 1.2 and len(cap_later) >= 10

    improvements: list[str] = []
    if score3_loose:
        improvements.append("entry_score_v2_min を 4 以上に引き上げ（score3 群の損失集中を抑制）")
    if board_mid_ineffective:
        improvements.append("Board:mid 全件付与を廃止し、imbalance 分位+モメンタム複合でのみ板加点")
    if dynamic_bad:
        improvements.append("Dynamic40 の AM 選定を地合いフィルタ付きに（core10 優先 / dynamic 閾値引き上げ）")
    if cap_hurt:
        improvements.append("cap=3 維持のまま score5+ 優先キューで枠配分（低 score の枠占有を抑制）")
    if not improvements:
        improvements.append("09:00-10:00 の初動 ENTRY を抑制し 10:00 以降にシフト")
    improvements = improvements[:3]

    summary_out = {
        "phase": 348,
        "title": "20260612 Entry Failure Review (AM)",
        "session_id": "20260612/live_session_080806",
        "session_window": "2026-06-12 09:03-11:25 JST",
        "headline_metrics": {
            "total_pnl_yen_100": summary.get("total_pnl_yen_100"),
            "profit_factor_yen_100": summary.get("profit_factor_yen_100"),
            "stop_hit_count": summary.get("structural_exit_reason_counts", {}).get("stop_hit"),
            "trade_count": len(exits),
            "accepted_count": summary.get("accepted_count"),
            "max_concurrent_rejects": summary.get("reject_reason_counts", {}).get("max_concurrent"),
            "entry_score_v2_min": summary.get("entry_score_v2_min"),
        },
        "checklist": {
            "1_worst_trades_top20_loss_yen": round(float(worst20[0]["pnl_yen_100"]), 2) if worst20 else None,
            "2_stop_hit_common_profile": stop_profile,
            "2_stop_hit_vs_non_stop": {
                "stop": stop_profile,
                "non_stop": non_stop_profile,
                "stop_board_mid_rate": stop_profile.get("board_mid_rate"),
                "non_stop_board_mid_rate": non_stop_profile.get("board_mid_rate"),
            },
            "3_worst_symbols": symbol_rows[:5],
            "4_entry_feature_cohorts": {
                "all": _feature_profile(exits),
                "stop_hit": stop_profile,
                "winners": _feature_profile([e for e in exits if float(e.get("pnl_yen_100") or 0) > 0]),
            },
            "5_score5_v1_review": _cohort_metrics(score5),
            "5_score6_v1_review": _cohort_metrics(score6),
            "5_not_score5_v1_review": _cohort_metrics(score_not_v1_ge5),
            "5_score_v2_eq3_review": _cohort_metrics(score3_v2),
            "6_quality_top20_review": {
                **_cohort_metrics(quality_top20),
                "note": "top20 = highest continuation_quality_score at entry (rank field absent on accepted)",
                "summary_shadow_top20_pf": summary.get("shadow_quality_top20_pf"),
                "summary_current_top20_pf": summary.get("current_quality_top20_pf"),
            },
            "7_cap_counterfactual": {
                "unique_max_concurrent_rejects": len(cap_rows),
                "later_actual_entry_count": len(cap_later),
                "would_have_won_count": len(cap_won),
                "would_have_lost_count": len(cap_lost),
                "unknown_no_later_trade": len(cap_rows) - len(cap_later),
                "later_trade_win_rate": round(len(cap_won) / len(cap_later), 4) if cap_later else None,
            },
            "8_early_session_0900_1000": early_m,
            "8_after_1000": late_m,
            "9_universe_dynamic40": {
                "core10": _cohort_metrics(core10),
                "dynamic40": _cohort_metrics(dynamic40),
            },
        },
        "structural_exit_mix": dict(Counter(str(e.get("exit_kind") or "") for e in exits)),
        "overlap_replaced_review": {
            "count": overlap_count,
            "total_pnl_yen_100": round(overlap_yen, 2),
        },
        "conclusions": {
            "primary_failure_domain": primary_cause,
            "exit_vs_entry": (
                "EXIT ではなく ENTRY / 銘柄選定 / 枠管理が主因。"
                "stop_hit は多いが ENTRY 時モメンタム・score 分布が悪化しており、"
                "トレーリング調整よりゲート強化が先。"
            ),
            "score3_too_loose": score3_loose,
            "score3_note": (
                f"全 {len(score3_v2)} 件が entry_score_v2=3（閾値ぎりぎり）。"
                f"score_v1_ge5 は {len(score5)} 件だが損益 {round(score5_yen, 0)}円と依然マイナス。"
                "v2=3 閾値と v1 score5 ラベルの乖離が ENTRY 品質管理の穴。"
                if score3_loose
                else "score3 単独が主因とは言い切れない。"
            ),
            "board_mid_effective": not board_mid_ineffective,
            "board_mid_note": (
                f"Board:mid は採用 ENTRY の {round(board_mid_rate*100, 1)}% に付与され差別化不能。"
                "全件ほぼ mid 帯トークンで、6/12 AM では選別力ゼロ。"
                if board_mid_ineffective
                else "Board:mid 単独では説明力不足。"
            ),
            "dynamic40_fit": "poor" if dynamic_bad else "mixed",
            "dynamic40_note": (
                f"Dynamic40 {_cohort_metrics(dynamic40)['trade_count']}件 "
                f"{_cohort_metrics(dynamic40)['total_pnl_yen_100']}円 vs "
                f"Core10 {_cohort_metrics(core10).get('total_pnl_yen_100')}円。"
                "6/12 AM の地合いでは dynamic 銘柄の初動追随が不利。"
                if dynamic_bad
                else "Universe 差は副次要因。"
            ),
            "cap3_impact": "expanded_loss" if cap_hurt else "limited_or_neutral",
            "cap3_note": (
                f"max_concurrent 却下 {len(cap_rows)} 件のうち後続実トレード {len(cap_later)} 件。"
                f"勝ち {len(cap_won)} / 負け {len(cap_lost)}。"
                "枠制限で高スコア候補を逃し損失拡大の可能性。"
                if cap_hurt
                else "枠制限は主因ではなく、入った低品質 ENTRY の方が損失を支配。"
            ),
            "early_session_note": (
                f"09:00-10:00: PF {early_m.get('profit_factor_yen_100')} / "
                f"{early_m.get('total_pnl_yen_100')}円 vs 10:00以降 "
                f"{late_m.get('total_pnl_yen_100')}円。"
            ),
            "improvements_max3": improvements,
            "narrative": entry_note,
        },
        "output_files": {
            "worst_trades": "phase348_20260612_worst_trades.csv",
            "stop_hit_analysis": "phase348_20260612_stop_hit_analysis.csv",
            "symbol_pnl": "phase348_20260612_symbol_pnl.csv",
            "score_quality_review": "phase348_20260612_score_quality_review.csv",
            "cap_counterfactual": "phase348_20260612_cap_counterfactual.csv",
        },
    }

    worst_fields = [
        "symbol",
        "entry_time",
        "exit_time",
        "pnl_yen_100",
        "pnl_pct",
        "exit_kind",
        "score_v2",
        "score_v1",
        "score_v1_ge5",
        "quality",
        "momentum",
        "board_mid",
        "imbalance",
        "rise_5min",
        "universe_slot",
        "early_session",
    ]
    stop_fields = worst_fields + ["peak_mfe_pct", "rolling_mae_pct"]
    score_rows = [
        {"cohort": "all", **_cohort_metrics(exits), **_feature_profile(exits)},
        {"cohort": "score_v1_ge5", **_cohort_metrics(score5), **_feature_profile(score5)},
        {"cohort": "score_v1_ge6", **_cohort_metrics(score6), **_feature_profile(score6)},
        {"cohort": "not_score_v1_ge5", **_cohort_metrics(score_not_v1_ge5), **_feature_profile(score_not_v1_ge5)},
        {"cohort": "score_v2_eq3", **_cohort_metrics(score3_v2), **_feature_profile(score3_v2)},
        {"cohort": "quality_top20", **_cohort_metrics(quality_top20), **_feature_profile(quality_top20)},
        {"cohort": "board_mid", **_cohort_metrics(board_mid), **_feature_profile(board_mid)},
        {"cohort": "early_0900_1000", **_cohort_metrics(early), **_feature_profile(early)},
        {"cohort": "after_1000", **_cohort_metrics(late), **_feature_profile(late)},
        {"cohort": "core10", **_cohort_metrics(core10), **_feature_profile(core10)},
        {"cohort": "dynamic40", **_cohort_metrics(dynamic40), **_feature_profile(dynamic40)},
    ]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "phase348_20260612_entry_failure_summary.json").write_text(
        json.dumps(summary_out, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_csv(OUT_DIR / "phase348_20260612_worst_trades.csv", worst20, worst_fields)
    _write_csv(OUT_DIR / "phase348_20260612_stop_hit_analysis.csv", stop_hits, stop_fields)
    _write_csv(OUT_DIR / "phase348_20260612_symbol_pnl.csv", symbol_rows, list(symbol_rows[0].keys()) if symbol_rows else [])
    score_fieldnames = sorted({k for r in score_rows for k in r})
    _write_csv(OUT_DIR / "phase348_20260612_score_quality_review.csv", score_rows, score_fieldnames)
    cap_fieldnames = sorted({k for r in cap_rows for k in r})
    _write_csv(OUT_DIR / "phase348_20260612_cap_counterfactual.csv", cap_rows, cap_fieldnames)

    print(json.dumps(summary_out["conclusions"], ensure_ascii=False, indent=2))
    print(f"wrote {OUT_DIR}/phase348_20260612_*.json|csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
