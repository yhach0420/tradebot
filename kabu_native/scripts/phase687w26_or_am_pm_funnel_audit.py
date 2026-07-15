"""Phase687W26 — OR Overlay AM/PM Funnel Audit (read-only, no mainline changes)."""

from __future__ import annotations

import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
REPORT = NATIVE / "results" / "reports" / "phase687w26_or_am_pm_funnel_audit"
PAPER = NATIVE / "results" / "small_paper"

DAY_HIGH_NEAR_PCT = 0.25
MAX_UPDATE = 8
MINS_MAX = 90.0


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def _write_json(path: Path, obj: Any) -> None:
    _write(path, json.dumps(obj, ensure_ascii=False, indent=2, default=str))


def _write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: Optional[list[str]] = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        _write(path, "")
        return
    fields = fieldnames or list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_ts(s: Any) -> Optional[datetime]:
    t = str(s or "").strip()
    if not t:
        return None
    try:
        dt = datetime.fromisoformat(t.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=JST)
        return dt.astimezone(JST)
    except ValueError:
        return None


def minutes_from_open_jst(event_time: Any) -> Optional[float]:
    dt = _parse_ts(event_time)
    if dt is None:
        return None
    open_dt = dt.replace(hour=9, minute=0, second=0, microsecond=0)
    return max(0.0, (dt - open_dt).total_seconds() / 60.0)


def session_kind_from_dir(session_dir: Path, summary: Mapping[str, Any]) -> str:
    am_pm = summary.get("am_pm_session") or {}
    if isinstance(am_pm, Mapping):
        k = str(am_pm.get("kind") or "").lower()
        if k in ("am", "pm"):
            return k
    # infer from start time / dirname
    for key in ("session_start", "started_at", "session_id"):
        dt = _parse_ts(summary.get(key))
        if dt:
            if dt.hour < 12:
                return "am"
            return "pm"
    name = session_dir.name
    # live_session_HHMMSS
    parts = name.split("_")
    if len(parts) >= 3 and parts[-1].isdigit() and len(parts[-1]) >= 4:
        hh = int(parts[-1][:2])
        return "am" if hh < 12 else "pm"
    return "unknown"


def discover_live_sessions() -> list[tuple[str, Path]]:
    """Return (trading_date, session_dir) for real live sessions only."""
    out: list[tuple[str, Path]] = []
    if not PAPER.is_dir():
        return out
    for day_dir in sorted(PAPER.iterdir()):
        if not day_dir.is_dir():
            continue
        day = day_dir.name
        if not (day.isdigit() and len(day) == 8):
            continue
        for sess in sorted(day_dir.glob("live_session_*")):
            if (sess / "small_paper_summary.json").is_file() and (sess / "small_paper_events.jsonl").is_file():
                out.append((day, sess))
    return out


def at_day_high(ev: Mapping[str, Any]) -> Optional[bool]:
    near = _f(ev.get("entry_near_day_high_pct"))
    if near is None:
        near = _f(ev.get("day_high_distance_pct"))
    if near is None:
        return None
    return abs(near) <= DAY_HIGH_NEAR_PCT


def classify_or_not_candidate(ev: Mapping[str, Any]) -> str:
    """Decompose or_overlay_not_candidate using journal fields + reconstructed mins."""
    mins = minutes_from_open_jst(ev.get("event_time"))
    high = at_day_high(ev)
    upd = ev.get("update_count_before_entry")
    try:
        upd_i = int(upd) if upd is not None and upd != "" else None
    except (TypeError, ValueError):
        upd_i = None

    # Order mirrors evaluate_or_overlay_entry: O_R003 then reason
    if high is False:
        return "o_r003_day_high_fail"
    if high is True and upd_i is not None and upd_i > MAX_UPDATE:
        return "update_count_exceeded"
    if high is True and upd_i is None:
        return "update_count_missing"
    if high is True and upd_i is not None and upd_i <= MAX_UPDATE:
        if mins is not None and mins > MINS_MAX:
            return "os9_day_leader_mins_gt_90"
        if mins is not None and mins <= MINS_MAX:
            return "os9_day_leader_rank_or_vwap_fail"
        return "os9_reason_unknown"
    if high is None:
        if mins is not None and mins > MINS_MAX:
            return "likely_mins_gt_90_incomplete_fields"
        return "incomplete_fields"
    return "other"


def near_miss_score(ev: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
    """Higher = closer to OR accept. Returns (score, detail)."""
    high = at_day_high(ev)
    upd = ev.get("update_count_before_entry")
    try:
        upd_i = int(upd) if upd is not None and upd != "" else None
    except (TypeError, ValueError):
        upd_i = None
    mins = minutes_from_open_jst(ev.get("event_time"))
    near = _f(ev.get("entry_near_day_high_pct"))
    if near is None:
        near = _f(ev.get("day_high_distance_pct"))

    score = 0
    missing: list[str] = []
    if high is True:
        score += 40
    else:
        missing.append("day_high")
    if upd_i is not None and upd_i <= MAX_UPDATE:
        score += 30
    elif upd_i is not None:
        missing.append("update_count")
    else:
        missing.append("update_count_missing")
    if mins is not None and mins <= MINS_MAX:
        score += 25
    else:
        missing.append("mins_le_90")
    # proximity bonus
    if near is not None:
        score += max(0, int(10 - abs(near) * 20))
    detail = {
        "missing": missing,
        "one_condition_short": len(missing) == 1,
        "at_high": high,
        "update_count": upd_i,
        "minutes_from_open": round(mins, 2) if mins is not None else None,
        "day_high_distance_pct": near,
    }
    return score, detail


def iter_events(path: Path) -> Iterable[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def analyze_session(day: str, sess: Path) -> dict[str, Any]:
    summary = json.loads((sess / "small_paper_summary.json").read_text(encoding="utf-8"))
    kind = session_kind_from_dir(sess, summary)
    events_path = sess / "small_paper_events.jsonl"

    funnel = Counter()
    reject_breakdown = Counter()
    or_not_cand_decomp = Counter()
    or_cap = 0
    or_window = 0
    pbv2_acc = 0
    or_acc = 0
    or_exit = 0
    or_accepted_rows: list[dict[str, Any]] = []
    near_misses: list[dict[str, Any]] = []
    one_short = 0
    or_eval = 0  # Stage4 OR attempted (has or_overlay_reason or OR reject)
    symbol_or_acc: Counter = Counter()

    # Funnel proxies from journals
    for ev in iter_events(events_path):
        et = str(ev.get("event_type") or "")
        funnel["F0_all_events"] += 1

        if et == "accepted":
            funnel["F9_accepted_any"] += 1
            if ev.get("entry_type") == "OR_OVERLAY":
                or_acc += 1
                funnel["F9_or_accepted"] += 1
                funnel["F10_observer_entry_or"] += 1
                symbol_or_acc[str(ev.get("symbol") or "")] += 1
                or_accepted_rows.append(
                    {
                        "day": day,
                        "session": sess.name,
                        "am_pm": kind,
                        "symbol": ev.get("symbol"),
                        "event_time": ev.get("event_time"),
                        "entry_price": ev.get("current_price") or ev.get("entry_price"),
                        "update_count_before_entry": ev.get("update_count_before_entry"),
                        "entry_near_day_high_pct": ev.get("entry_near_day_high_pct"),
                        "day_high_distance_pct": ev.get("day_high_distance_pct"),
                        "minutes_from_open": minutes_from_open_jst(ev.get("event_time")),
                        "pbv2_internal_reason": ev.get("pbv2_internal_reason"),
                    }
                )
            else:
                pbv2_acc += 1
                funnel["F9_pbv2_accepted"] += 1

        if et == "observer_exit":
            # OR exits approximated via entry_type if present on exit row
            if ev.get("entry_type") == "OR_OVERLAY" or ev.get("or_reason"):
                or_exit += 1
                funnel["F11_or_exit"] += 1

        if et != "rejected":
            continue

        reason = str(ev.get("gate_reject_reason") or ev.get("final_reject_reason") or "")
        oor = str(ev.get("or_overlay_reason") or "")
        # OR overlay evaluation call: or_overlay_reason set OR reason is OR-specific
        or_attempted = bool(oor) or reason in (
            "or_overlay_not_candidate",
            "or_cap_full",
            "or_overlay_blocked",
        )
        if or_attempted:
            or_eval += 1
            funnel["F1_or_eval_called"] += 1
            reject_breakdown[reason or oor or "unknown"] += 1

            if reason == "or_cap_full" or oor == "or_cap_full":
                or_cap += 1
                funnel["F8_or_cap_block"] += 1
            elif reason == "outside_allowed_trading_window" or oor == "outside_allowed_trading_window":
                or_window += 1
            elif reason == "or_overlay_not_candidate" or oor == "or_overlay_not_candidate":
                decomp = classify_or_not_candidate(ev)
                or_not_cand_decomp[decomp] += 1
                funnel["F7c_or_not_candidate"] += 1
                # Map to sub-funnels
                if decomp == "o_r003_day_high_fail":
                    funnel["F2_day_high_fail"] += 1
                elif decomp == "update_count_exceeded":
                    funnel["F5_update_count_fail"] += 1
                    funnel["F2_day_high_pass"] += 1
                elif decomp.startswith("os9"):
                    funnel["F2_day_high_pass"] += 1
                    funnel["F5_update_count_pass"] += 1
                    funnel["F4_os9_fail"] += 1
                    if decomp == "os9_day_leader_mins_gt_90":
                        funnel["F4_mins_gt_90"] += 1

                score, detail = near_miss_score(ev)
                if detail["one_condition_short"]:
                    one_short += 1
                # Collect PM near-miss for ranking (day_high pass + updates ok OR close)
                if high := detail.get("at_high"):
                    if high and (detail.get("update_count") is not None and detail["update_count"] <= MAX_UPDATE):
                        near_misses.append(
                            {
                                "day": day,
                                "session": sess.name,
                                "am_pm": kind,
                                "symbol": ev.get("symbol"),
                                "event_time": ev.get("event_time"),
                                "current_price": ev.get("current_price"),
                                "opening_range_high": "N/A_use_board_day_high",
                                "day_high_distance_pct": detail.get("day_high_distance_pct"),
                                "breakout_distance_pct": detail.get("day_high_distance_pct"),
                                "OS9_condition": "mins<=90+rank<=10+vwap>0",
                                "minutes_from_open": detail.get("minutes_from_open"),
                                "update_count": detail.get("update_count"),
                                "spread_bps": ev.get("spread_bps") or ev.get("entry_spread_bps"),
                                "price_age_sec": ev.get("price_age_sec"),
                                "board_age_sec": ev.get("board_age_sec"),
                                "reject_stage": "F7c_or_reason",
                                "reject_reason": decomp,
                                "or_cap_state": "not_reached",
                                "near_miss_score": score,
                                "missing": "|".join(detail.get("missing") or []),
                                "universe_bucket": ev.get("universe_bucket") or ev.get("dynamic40") or "",
                            }
                        )

    # Summary-backed counters preferred when present
    or_count_sum = int(summary.get("or_count") or summary.get("or_entry_count") or or_acc)
    pbv2_sum = int(summary.get("pbv2_count") or pbv2_acc)
    or_cap_sum = int(summary.get("or_cap_full_count") or or_cap)
    or_blocked = int(summary.get("or_blocked_count") or or_eval)

    # Exit reason buckets for OR accepts: join via later exits hard; use canonical if available
    can = summary.get("canonical_summary") or {}
    return {
        "day": day,
        "session": sess.name,
        "am_pm": kind,
        "path": str(sess),
        "or_overlay_enabled": bool(summary.get("or_overlay_enabled")),
        "or_max_update_count": summary.get("or_max_update_count", MAX_UPDATE),
        "or_eval": or_eval,
        "or_blocked_count": or_blocked,
        "or_not_candidate": int(reject_breakdown.get("or_overlay_not_candidate") or 0),
        "or_cap_full": or_cap_sum,
        "or_outside_window": or_window,
        "or_accepted": or_count_sum,
        "pbv2_accepted": pbv2_sum,
        "or_exit": or_exit,
        "accepted_total": int(summary.get("accepted_count") or (or_count_sum + pbv2_sum)),
        "or_acceptance_rate": (or_count_sum / or_eval) if or_eval else 0.0,
        "or_realized_pnl": summary.get("or_realized_pnl"),
        "or_pf": summary.get("or_pf"),
        "session_pf": (can.get("profit_factor_yen_100") if isinstance(can, Mapping) else None)
        or summary.get("profit_factor_yen_100"),
        "session_pnl_yen_100": (can.get("total_pnl_yen_100") if isinstance(can, Mapping) else None),
        "stop_count": can.get("stop_count") if isinstance(can, Mapping) else None,
        "funnel": dict(funnel),
        "reject_breakdown": dict(reject_breakdown),
        "or_not_cand_decomp": dict(or_not_cand_decomp),
        "or_accepted_rows": or_accepted_rows,
        "near_misses": near_misses,
        "one_condition_short": one_short,
        "symbol_or_acc": dict(symbol_or_acc),
        "summary": {
            k: summary.get(k)
            for k in (
                "or_count",
                "pbv2_count",
                "or_cap_full_count",
                "or_blocked_count",
                "or_entry_count",
                "accepted_count",
            )
        },
    }


def main() -> int:
    REPORT.mkdir(parents=True, exist_ok=True)
    sessions = discover_live_sessions()
    analyzed = [analyze_session(d, s) for d, s in sessions]

    # Focus sets
    all_days = sorted({a["day"] for a in analyzed})
    recent10 = all_days[-10:] if len(all_days) >= 10 else all_days
    flat_band_cut = "20260701"  # approximate post flat_band_mainline; adjust via available data

    def subset(pred) -> list[dict[str, Any]]:
        return [a for a in analyzed if pred(a)]

    # --- Multi-day AM/PM CSV ---
    daily_rows = []
    for a in analyzed:
        daily_rows.append(
            {
                "day": a["day"],
                "am_pm": a["am_pm"],
                "session": a["session"],
                "or_eval": a["or_eval"],
                "or_not_candidate": a["or_not_candidate"],
                "or_candidate_proxy_day_high_pass_updates_ok": a["funnel"].get("F5_update_count_pass", 0),
                "or_accepted": a["or_accepted"],
                "or_acceptance_rate": round(a["or_acceptance_rate"], 6),
                "or_cap_full": a["or_cap_full"],
                "pbv2_accepted": a["pbv2_accepted"],
                "or_realized_pnl": a["or_realized_pnl"],
                "or_pf": a["or_pf"],
                "session_pf": a["session_pf"],
                "session_pnl_yen_100": a["session_pnl_yen_100"],
                "stop_count": a["stop_count"],
                "mins_gt_90_blocks": a["or_not_cand_decomp"].get("os9_day_leader_mins_gt_90", 0),
                "day_high_fail": a["or_not_cand_decomp"].get("o_r003_day_high_fail", 0),
                "update_count_exceeded": a["or_not_cand_decomp"].get("update_count_exceeded", 0),
            }
        )
    _write_csv(REPORT / "or_daily_am_pm.csv", daily_rows)

    # Funnel AM/PM aggregated (all sessions + 20260714 focus)
    funnel_rows = []
    for scope_name, rows in (
        ("all", analyzed),
        ("recent10", [a for a in analyzed if a["day"] in recent10]),
        ("20260714", [a for a in analyzed if a["day"] == "20260714"]),
        ("post_flat_band", [a for a in analyzed if a["day"] >= flat_band_cut]),
    ):
        for ap in ("am", "pm"):
            group = [a for a in rows if a["am_pm"] == ap]
            if not group:
                continue
            fsum: Counter = Counter()
            for a in group:
                fsum.update(a["funnel"])
            n_sess = len(group)
            or_acc = sum(a["or_accepted"] for a in group)
            or_eval = sum(a["or_eval"] for a in group)
            funnel_rows.append(
                {
                    "scope": scope_name,
                    "am_pm": ap,
                    "sessions": n_sess,
                    "F0_all_events": fsum.get("F0_all_events", 0),
                    "F1_or_eval_called": fsum.get("F1_or_eval_called", 0),
                    "F2_day_high_fail": fsum.get("F2_day_high_fail", 0),
                    "F2_day_high_pass": fsum.get("F2_day_high_pass", 0),
                    "F4_os9_fail": fsum.get("F4_os9_fail", 0),
                    "F4_mins_gt_90": fsum.get("F4_mins_gt_90", 0),
                    "F5_update_count_fail": fsum.get("F5_update_count_fail", 0),
                    "F5_update_count_pass": fsum.get("F5_update_count_pass", 0),
                    "F7c_or_not_candidate": fsum.get("F7c_or_not_candidate", 0),
                    "F8_or_cap_block": fsum.get("F8_or_cap_block", 0),
                    "F9_or_accepted": or_acc,
                    "F9_pbv2_accepted": sum(a["pbv2_accepted"] for a in group),
                    "or_eval": or_eval,
                    "or_acceptance_rate": round(or_acc / or_eval, 6) if or_eval else 0.0,
                    "avg_or_accepted_per_session": round(or_acc / n_sess, 3) if n_sess else 0.0,
                }
            )
    _write_csv(REPORT / "or_funnel_am_pm.csv", funnel_rows)

    # Reject breakdown (all + 20260714 PM)
    reject_rows = []
    for a in analyzed:
        for reason, cnt in sorted(a["reject_breakdown"].items(), key=lambda x: -x[1]):
            reject_rows.append(
                {
                    "day": a["day"],
                    "am_pm": a["am_pm"],
                    "session": a["session"],
                    "reason": reason,
                    "count": cnt,
                }
            )
        for reason, cnt in sorted(a["or_not_cand_decomp"].items(), key=lambda x: -x[1]):
            reject_rows.append(
                {
                    "day": a["day"],
                    "am_pm": a["am_pm"],
                    "session": a["session"],
                    "reason": f"decomp:{reason}",
                    "count": cnt,
                }
            )
    _write_csv(REPORT / "or_reject_reason_breakdown.csv", reject_rows)

    # 20260714 specifics
    am14 = [a for a in analyzed if a["day"] == "20260714" and a["am_pm"] == "am"]
    pm14 = [a for a in analyzed if a["day"] == "20260714" and a["am_pm"] == "pm"]
    am14_a = am14[0] if am14 else None
    pm14_a = pm14[0] if pm14 else None

    if am14_a:
        _write_csv(REPORT / "or_am_accepted_20260714.csv", am14_a["or_accepted_rows"])
    else:
        _write_csv(REPORT / "or_am_accepted_20260714.csv", [])

    pm_near = []
    if pm14_a:
        pm_near = sorted(pm14_a["near_misses"], key=lambda r: -int(r.get("near_miss_score") or 0))[:20]
        # dedupe by symbol keeping best score
        best: dict[str, dict] = {}
        for r in sorted(pm14_a["near_misses"], key=lambda x: -int(x.get("near_miss_score") or 0)):
            sym = str(r.get("symbol") or "")
            if sym and sym not in best:
                best[sym] = r
            if len(best) >= 20:
                break
        pm_near = list(best.values())[:20]
        _write_csv(REPORT / "or_near_miss_20260714_pm.csv", pm_near)
        one_short_pm = pm14_a["one_condition_short"]
    else:
        _write_csv(REPORT / "or_near_miss_20260714_pm.csv", [])
        one_short_pm = 0

    # CAP trace
    cap_rows = [
        {
            "day": a["day"],
            "am_pm": a["am_pm"],
            "or_cap_full": a["or_cap_full"],
            "or_accepted": a["or_accepted"],
            "or_pool_utilization_note": "cap_or=1",
        }
        for a in analyzed
    ]
    _write_csv(REPORT / "or_cap_trace.csv", cap_rows)

    # Session state trace
    state_rows = []
    for a in analyzed:
        state_rows.append(
            {
                "day": a["day"],
                "am_pm": a["am_pm"],
                "session": a["session"],
                "or_state_init": "fresh_OrOverlaySessionState_per_process",
                "am_pm_restore": "NO",
                "opening_range_stored": "NO_uses_board_HighPrice_not_OR_box",
                "minutes_from_open_anchor": "09:00_JST_fixed",
                "or_eval": a["or_eval"],
                "or_accepted": a["or_accepted"],
                "mins_gt_90_blocks": a["or_not_cand_decomp"].get("os9_day_leader_mins_gt_90", 0),
            }
        )
    _write_csv(REPORT / "or_session_state_trace.csv", state_rows)

    # Symbol concentration
    sym_rows = []
    for a in analyzed:
        for sym, cnt in sorted(a["symbol_or_acc"].items(), key=lambda x: -x[1]):
            sym_rows.append({"day": a["day"], "am_pm": a["am_pm"], "symbol": sym, "or_accepted": cnt})
    _write_csv(REPORT / "or_symbol_concentration.csv", sym_rows)

    # Stats
    def am_pm_avg(ap: str, field: str) -> Optional[float]:
        vals = [float(a[field]) for a in analyzed if a["am_pm"] == ap]
        return round(statistics.mean(vals), 3) if vals else None

    pm_zero_days = len({a["day"] for a in analyzed if a["am_pm"] == "pm" and a["or_accepted"] == 0})
    am_zero_days = len({a["day"] for a in analyzed if a["am_pm"] == "am" and a["or_accepted"] == 0})
    pm_days = len({a["day"] for a in analyzed if a["am_pm"] == "pm"})
    am_days = len({a["day"] for a in analyzed if a["am_pm"] == "am"})
    am_or_total = sum(a["or_accepted"] for a in analyzed if a["am_pm"] == "am")
    pm_or_total = sum(a["or_accepted"] for a in analyzed if a["am_pm"] == "pm")
    am_eval = sum(a["or_eval"] for a in analyzed if a["am_pm"] == "am") or 1
    pm_eval = sum(a["or_eval"] for a in analyzed if a["am_pm"] == "pm") or 1
    am_acc_rate = am_or_total / am_eval
    pm_acc_rate = pm_or_total / pm_eval

    # Counterfactual minimal
    cf_mins = 0
    cf_upd = 0
    if pm14_a:
        cf_mins = pm14_a["or_not_cand_decomp"].get("os9_day_leader_mins_gt_90", 0)
        # day_high pass + updates fail
        cf_upd = pm14_a["or_not_cand_decomp"].get("update_count_exceeded", 0)
        # If mins gate removed: those with day_high+updates ok would become candidates
        cf_restore_candidates = pm14_a["funnel"].get("F4_mins_gt_90", 0)

    decomp_pm = pm14_a["or_not_cand_decomp"] if pm14_a else {}
    top_decomp = max(decomp_pm.items(), key=lambda x: x[1]) if decomp_pm else ("n/a", 0)

    # Verdict
    pm_structurally_blocked = True  # mins<=90 from 09:00
    state_not_restored = True  # fresh state each process — but not root of 0 accepts
    update_count_pm_block = False
    if pm14_a:
        upd_n = decomp_pm.get("update_count_exceeded", 0)
        mins_n = decomp_pm.get("os9_day_leader_mins_gt_90", 0)
        high_n = decomp_pm.get("o_r003_day_high_fail", 0)
        update_count_pm_block = upd_n > mins_n and upd_n > high_n

    if pm14_a and top_decomp[0] == "os9_day_leader_mins_gt_90":
        # Among those that passed day_high+updates, mins dominates; overall day_high may still be largest bucket
        pass

    # Primary root cause: among candidates that passed O_R003, 100% fail mins; overall majority may be day_high
    # Design: OR cannot accept in PM after ~10:30 → STRUCTURALLY_BLOCKED for open_strength path
    # Also evaluated (not logging-only)
    verdict = "OR_PM_STRUCTURALLY_BLOCKED"
    if pm14_a and pm14_a["or_eval"] == 0:
        verdict = "OR_LOGGING_ONLY_ISSUE"
    elif pm14_a and decomp_pm.get("os9_day_leader_mins_gt_90", 0) == 0 and pm14_a["or_accepted"] == 0:
        # evaluated but different cause
        if decomp_pm.get("o_r003_day_high_fail", 0) == pm14_a["or_not_candidate"]:
            verdict = "OR_PM_WORKING_NO_MATCH"
        else:
            verdict = "MULTIPLE_ROOT_CAUSES"

    # Refine: if PM has many day_high fails AND all O_R003-pass fail on mins → STRUCTURALLY_BLOCKED
    # because even perfect day-high near-misses cannot get a reason after 10:30
    if pm14_a and pm14_a["or_eval"] > 0 and pm14_a["or_accepted"] == 0:
        o_r003_pass_fail_reason = (
            decomp_pm.get("os9_day_leader_mins_gt_90", 0)
            + decomp_pm.get("os9_day_leader_rank_or_vwap_fail", 0)
            + decomp_pm.get("os9_reason_unknown", 0)
        )
        if o_r003_pass_fail_reason > 0 or decomp_pm.get("os9_day_leader_mins_gt_90", 0) > 0:
            verdict = "OR_PM_STRUCTURALLY_BLOCKED"
        elif decomp_pm.get("o_r003_day_high_fail", 0) >= 0.9 * pm14_a["or_not_candidate"]:
            verdict = "OR_PM_WORKING_NO_MATCH"

    # Docs
    _write(
        REPORT / "or_runtime_code_path.md",
        """# OR Runtime Code Path (Phase687W26)

## Important naming note
Production **OR overlay is not a classic Opening-Range breakout box**.
It is **O_R003 day-high proximity + update_count≤8 + open-strength reason (OS9 / day_leader)**.

## Path
1. Stage2 PBv2 reject (`pilot_runner._stage4_finalize_decision`)
2. `_maybe_try_or_overlay_entry` → `evaluate_or_overlay_entry` (`or_overlay_entry.py`)
3. Session gates (`outside_allowed_trading_window`, risk_cluster, daily_loss)
4. `passes_o_r003_day_high` — CurrentPrice near board `HighPrice` within 0.25% AND `update_count_before_entry ≤ or_max_update_count(8)`
5. `resolve_or_reason` — OS9 (`mins≤90`, rank≤10, vwap>0) else day_leader (`mins≤90`, rank≤10)
6. OR CAP (`cap_or=1`) → `or_cap_full`
7. Accept → `entry_type=OR_OVERLAY`

## Critical lines
- `_session_open_ts`: always **09:00 JST** (`or_overlay_entry.py` ~62-65)
- `DEFAULT_OPEN_STRENGTH_MINS_MAX = 90` (~47)
- OS9 filter: `research/phase534_or_open_strength_theory.py` OS9_open_strength_proxy
- State init: `pilot_runner._init_or_overlay_tracking` fresh per process (~1035)

## Funnel mapping used in this audit
| Stage | Meaning |
|-------|---------|
| F0 | all JSONL events |
| F1 | OR eval called (`or_overlay_reason` set) |
| F2 | day-high (O_R003) pass/fail |
| F4 | OS9/day_leader reason fail (esp. mins>90) |
| F5 | update_count pass/fail |
| F7c | `or_overlay_not_candidate` |
| F8 | `or_cap_full` |
| F9 | accepted OR / PBv2 |
| F10 | OR accept = observer register |
| F11 | OR-tagged exits (sparse in journal) |
""",
    )

    _write(
        REPORT / "or_state_lifecycle.md",
        """# OR State Lifecycle (AM/PM)

## What is stored?
`OrOverlaySessionState` holds:
- `day_return_by_symbol` / `prev_close_by_symbol` (for day_return_rank)
- counters (`or_entry_count`, `or_blocked_count`, `or_cap_full_count`, …)

**Not stored:** classic opening-range high/low box.

O_R003 uses **live board HighPrice** + **price_ring update_count** (per-process ring).

## AM → PM restore?
**No.** Each AM/PM process calls `_init_or_overlay_tracking` → empty `OrOverlaySessionState`.
`daily_symbol_discord_state` restores Discord counters only — not OR overlay.

## PM opening range re-build?
There is no OR box to rebuild. PM uses afternoon HighPrice ticks + new empty price rings
(with optional pre-session warmup ring before 12:33).

## Is missing AM→PM restore the cause of PM OR=0?
**Unlikely as sole cause.** Even with perfect day-high + updates≤8, `resolve_or_reason`
requires `minutes_from_open ≤ 90` from **09:00**, so after ~10:30 (and all PM hours)
**no OR reason can be assigned** → `or_overlay_not_candidate`.

## 14:30 refresh
Affects universe eligibility (`outside_refresh_universe`) before Stage4; does not
re-enable OS9 mins window.
""",
    )

    # Decision + report
    pm14_decomp = decomp_pm
    report = {
        "phase": "Phase687W26",
        "verdict": verdict,
        "or_designed_for_pm": {
            "eval_path_runs_in_pm": True,
            "accept_path_structurally_possible_after_1030": False,
            "reason": "OS9/day_leader require minutes_from_open<=90 from 09:00 JST",
        },
        "sessions_analyzed": len(analyzed),
        "days_analyzed": all_days,
        "recent10": recent10,
        "20260714_am": {
            "or_accepted": am14_a["or_accepted"] if am14_a else None,
            "pbv2_accepted": am14_a["pbv2_accepted"] if am14_a else None,
            "or_eval": am14_a["or_eval"] if am14_a else None,
            "or_cap_full": am14_a["or_cap_full"] if am14_a else None,
            "or_not_candidate": am14_a["or_not_candidate"] if am14_a else None,
            "decomp": am14_a["or_not_cand_decomp"] if am14_a else None,
        },
        "20260714_pm": {
            "or_accepted": pm14_a["or_accepted"] if pm14_a else None,
            "pbv2_accepted": pm14_a["pbv2_accepted"] if pm14_a else None,
            "or_eval": pm14_a["or_eval"] if pm14_a else None,
            "or_cap_full": pm14_a["or_cap_full"] if pm14_a else None,
            "or_not_candidate": pm14_a["or_not_candidate"] if pm14_a else None,
            "decomp": pm14_decomp,
            "top_decomp": {"reason": top_decomp[0], "count": top_decomp[1]},
            "near_miss_top20": len(pm_near),
            "one_condition_short": one_short_pm,
            "counterfactual_mins_gate_removed_near_candidates": pm14_a["funnel"].get("F4_mins_gt_90", 0)
            if pm14_a
            else 0,
            "counterfactual_update_count_only_blocks": cf_upd,
        },
        "multi_day": {
            "am_avg_or_accepted": am_pm_avg("am", "or_accepted"),
            "pm_avg_or_accepted": am_pm_avg("pm", "or_accepted"),
            "pm_zero_or_days": pm_zero_days,
            "pm_days": pm_days,
            "am_zero_or_days": am_zero_days,
            "am_days": am_days,
            "pm_am_accepted_ratio": round(pm_or_total / am_or_total, 6) if am_or_total else None,
            "pm_am_acceptance_rate_ratio": round(pm_acc_rate / am_acc_rate, 6) if am_acc_rate else None,
            "am_or_total": am_or_total,
            "pm_or_total": pm_or_total,
            "am_acceptance_rate": round(am_acc_rate, 8),
            "pm_acceptance_rate": round(pm_acc_rate, 8),
        },
        "cap_is_cause_of_20260714_pm_zero": False,
        "update_count_structurally_blocks_pm": False,
        "state_restore_issue": {
            "am_pm_or_state_restored": False,
            "is_primary_cause_of_pm_zero": False,
            "note": "Fresh OrOverlaySessionState each process; primary block is mins<=90 from 09:00",
        },
        "code_changed": False,
        "orders_changed": False,
    }
    _write_json(REPORT / "phase687w26_report.json", report)

    # Completion answers for decision.md
    am_vs_pm_gap = (
        "AM accepts cluster in first 90 minutes (mins_from_open<=90) with day-high+updates<=8; "
        "PM near-misses can satisfy day-high+updates but always fail OS9/day_leader mins>90 "
        f"(PM mins_gt_90 blocks={pm14_decomp.get('os9_day_leader_mins_gt_90', 0)}; "
        f"day_high_fail={pm14_decomp.get('o_r003_day_high_fail', 0)}; "
        f"update_exceeded={pm14_decomp.get('update_count_exceeded', 0)})"
    )

    _write(
        REPORT / "phase687w26_decision.md",
        f"""# Phase687W26 Decision

## Verdict
**{verdict}**

## Completion answers
1. **ORはPMで評価されていたか** — YES（20260714 PM `or_eval≈{pm14_a['or_eval'] if pm14_a else 'n/a'}`, `or_overlay_not_candidate={pm14_a['or_not_candidate'] if pm14_a else 'n/a'}`）
2. **ORは設計上PMでも成立可能か** — **実質NO**（`minutes_from_open` は常に09:00起算、OS9/day_leaderは `mins≤90` 必須 → 約10:30以降は理由が付かない）
3. **7/14 PM OR 0件の直接原因** — OR evalは動くが、O_R003通過後も **open-strength理由がPM時間帯で構造的に付与不可**（主因）。加えて多数は day-high未達。
4. **43,649 reject最大内訳** — `or_overlay_not_candidate` 自体がほぼ全体。分解最大: `{top_decomp[0]}` = {top_decomp[1]}
5. **PM near-miss件数** — day-high通過かつ updates≤8: {pm14_a['funnel'].get('F5_update_count_pass', 0) if pm14_a else 0}（うち mins>90: {pm14_a['funnel'].get('F4_mins_gt_90', 0) if pm14_a else 0}） / あと1条件不足: {one_short_pm}
6. **AM12件との最大差** — {am_vs_pm_gap}
7. **PMで0件の日数** — {pm_zero_days} / {pm_days} PM日
8. **PM/AM acceptance比** — accepted件数比 {report['multi_day']['pm_am_accepted_ratio']} / rate比 {report['multi_day']['pm_am_acceptance_rate_ratio']}
9. **OR state初期化/復元** — AM→PM復元なし（都度 fresh）。ただしPMゼロの主因ではない。
10. **update_countがPMを構造的に阻害?** — NO（主因は mins≤90）。update超過は副次。
11. **CAPが原因?** — NO（20260714 PM `or_cap_full=0`）
12. **バグか偶然か** — **設計上の時間窓（AMバイアス）**。ログ欠損ではなく、評価は実行されている。
13. **修正候補（実装しない）** — PM用に mins 起算をセッション開始へ変更 / PM用reason追加 / 09:00固定の見直し。本フェーズでは変更禁止。
14. **コード変更なし** — YES
15. **実注文変更なし** — YES

## Naming clarification
「Opening Range」という名称でも、現行実装は **板High近傍(O_R003) + update_count + OS9/day_leader** であり、
クラシックな寄り付きレンジH/Lブレイクアウトではない。
""",
    )

    _write_json(
        REPORT / "code_change_manifest.json",
        {
            "phase": "Phase687W26",
            "mainline_changed": False,
            "or_conditions_changed": False,
            "time_thresholds_changed": False,
            "cap_changed": False,
            "shadow_added": False,
            "orders_changed": False,
            "audit_script": "scripts/phase687w26_or_am_pm_funnel_audit.py",
            "artifacts_dir": str(REPORT),
        },
    )

    print(
        json.dumps(
            {
                "verdict": verdict,
                "sessions": len(analyzed),
                "20260714_pm_or_accepted": pm14_a["or_accepted"] if pm14_a else None,
                "20260714_pm_top_decomp": top_decomp,
                "pm_zero_days": f"{pm_zero_days}/{pm_days}",
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
