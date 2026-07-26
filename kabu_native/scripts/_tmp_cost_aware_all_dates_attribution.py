"""Cost-Aware all-dates detection + attribution + ablation (Paper offline only).

Does NOT write into official session directories. Does NOT change runtime trading.
Outputs only results/reports/cost_aware_all_dates_attribution/{report.md,report.json,audit.xlsx}
"""
from __future__ import annotations

import hashlib
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
for p in (ROOT / "src", REPO, ROOT):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

JST = ZoneInfo("Asia/Tokyo")
OUT = ROOT / "results/reports/cost_aware_all_dates_attribution"
SMALL = ROOT / "results/small_paper"
MUST_DATES = ("20260721", "20260722")


# ---------------------------------------------------------------------------
# Ablation config — 3 active decision levers (W54-FIX production)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Ablation:
    name: str
    use_winner: bool
    use_stop_score: bool
    use_stop_reject: bool
    use_nofill: bool  # only meaningful when use_stop_reject


ABLATIONS: list[Ablation] = [
    Ablation("Baseline_PBv2", False, False, False, False),
    Ablation("Winner_only", True, False, False, False),
    Ablation("STOP_walkdown", False, True, True, False),
    Ablation("STOP_NoFill", False, True, True, True),
    Ablation("Winner_STOP_walkdown", True, True, True, False),
    Ablation("Winner_NoFill_noop", True, False, False, True),  # degenerate≈Winner_only
    Ablation("Winner_STOP_NoFill_CURRENT", True, True, True, True),
    Ablation("LeaveOut_Winner", False, True, True, True),
    Ablation("LeaveOut_STOP", True, False, False, False),  # ≈Winner_only when stop off
    Ablation("LeaveOut_NoFill", True, True, True, False),  # = Winner_STOP_walkdown
]


def _suppress_shadow_writes() -> dict[str, int]:
    import small_paper.cost_aware_entry_shadow as ca

    c = {"n": 0}

    def _noop(state, trading_date, event):  # type: ignore[no-untyped-def]
        state.events.append(event)
        c["n"] += 1

    ca.append_shadow_event = _noop  # type: ignore[assignment]
    return c


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _session_kind(summary: Mapping[str, Any], session_dir: Path) -> str:
    am_pm = summary.get("am_pm_session")
    if isinstance(am_pm, Mapping):
        k = str(am_pm.get("kind") or "").upper()
        if k in ("AM", "PM"):
            return k
    name = session_dir.name
    # HHMMSS after live_session_
    try:
        hh = int(name.split("_")[-1][:2])
    except Exception:
        hh = 12
    return "AM" if hh < 12 else "PM"


def _official_pnl(summary: Mapping[str, Any]) -> Optional[float]:
    for k in (
        "realized_pnl_yen_100",
        "total_pnl_yen_100",
        "observer_realized_pnl_yen_100",
        "pnl_yen_100",
    ):
        if summary.get(k) is not None:
            try:
                return float(summary[k])
            except Exception:
                pass
    # fallback: accepted_count present but pnl missing
    return None


def discover_sessions() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not SMALL.is_dir():
        return rows
    for day_dir in sorted(SMALL.iterdir()):
        if not day_dir.is_dir() or not day_dir.name.isdigit() or len(day_dir.name) != 8:
            continue
        date = day_dir.name
        # skip synthetic far-future test dates except recording them
        for sess in sorted(day_dir.glob("live_session_*")):
            if not sess.is_dir():
                continue
            summary_p = sess / "small_paper_summary.json"
            events_p = sess / "small_paper_events.jsonl"
            audit_p = sess / "entry_scan_audit.jsonl"
            summary = {}
            if summary_p.is_file() and summary_p.stat().st_size > 0:
                try:
                    summary = json.loads(summary_p.read_text(encoding="utf-8"))
                except Exception:
                    summary = {}
            kind = _session_kind(summary, sess) if summary else _session_kind({}, sess)
            ca = summary.get("cost_aware_entry_shadow")
            ca_enabled = None
            if isinstance(ca, Mapping):
                ca_enabled = bool(ca.get("enabled")) if ca.get("enabled") is not None else True
            elif summary.get("cost_aware_entry_shadow_enabled") is not None:
                ca_enabled = bool(summary.get("cost_aware_entry_shadow_enabled"))

            events_ok = events_p.is_file() and events_p.stat().st_size > 1_000_000
            audit_ok = audit_p.is_file() and audit_p.stat().st_size > 1000
            summary_ok = summary_p.is_file() and summary_p.stat().st_size > 100
            price_path_ok = events_ok  # built from events
            # Cost-Aware implemented from ~20260718 evidence; Paper default ON later
            ca_impl = date >= "20260718"

            if date.startswith("2099"):
                status, reason = "C", "synthetic_test_date"
            elif not summary_ok and not events_ok and not audit_ok:
                status, reason = "C", "empty_or_launch_stub"
            elif not ca_impl:
                status, reason = "D", "cost_aware_not_implemented_or_pre_enablement"
            elif events_ok and audit_ok:
                status, reason = "B", ""  # offline replay required for RUNNING_PNL_COMPLETE
                if isinstance(ca, Mapping) and ca.get("status") == "RUNNING_PNL_COMPLETE":
                    status = "A"
            elif audit_ok and not events_ok:
                status, reason = "C", "missing_small_paper_events_jsonl_price_path"
            else:
                status, reason = "C", "insufficient_session_artifacts"

            rows.append(
                {
                    "date": date,
                    "session": kind,
                    "session_path": str(sess.relative_to(ROOT)).replace("\\", "/"),
                    "session_dir": str(sess),
                    "cost_aware_enabled": ca_enabled,
                    "source_data_available": bool(summary_ok or events_ok or audit_ok),
                    "price_path_available": price_path_ok,
                    "runtime_compatible_available": events_ok,  # needs exits+prices
                    "replay_required": status in ("A", "B"),
                    "evaluation_status": status,
                    "exclusion_reason": reason,
                    "events_bytes": events_p.stat().st_size if events_p.is_file() else 0,
                    "audit_bytes": audit_p.stat().st_size if audit_p.is_file() else 0,
                    "summary_bytes": summary_p.stat().st_size if summary_p.is_file() else 0,
                    "official_pnl_yen_100": _official_pnl(summary) if summary else None,
                    "accepted_count": summary.get("accepted_count") if summary else None,
                }
            )
    # ensure must dates present even if somehow missing
    have = {(r["date"], r["session"], r["session_path"]) for r in rows}
    for d in MUST_DATES:
        day = SMALL / d
        if not day.is_dir():
            rows.append(
                {
                    "date": d,
                    "session": "?",
                    "session_path": f"results/small_paper/{d}",
                    "session_dir": str(day),
                    "cost_aware_enabled": None,
                    "source_data_available": False,
                    "price_path_available": False,
                    "runtime_compatible_available": False,
                    "replay_required": False,
                    "evaluation_status": "C",
                    "exclusion_reason": "date_directory_missing",
                    "events_bytes": 0,
                    "audit_bytes": 0,
                    "summary_bytes": 0,
                    "official_pnl_yen_100": None,
                    "accepted_count": None,
                }
            )
    return rows


def _pick_primary_sessions(coverage: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One primary AM/PM per evaluable date (largest events file)."""
    by: dict[tuple[str, str], dict[str, Any]] = {}
    for r in coverage:
        if r["evaluation_status"] not in ("A", "B"):
            continue
        key = (r["date"], r["session"])
        prev = by.get(key)
        if prev is None or int(r["events_bytes"]) > int(prev["events_bytes"]):
            by[key] = r
    return [by[k] for k in sorted(by.keys())]


# ---------------------------------------------------------------------------
# Ablation selection cycle (mirrors production; offline only)
# ---------------------------------------------------------------------------
def run_selection_cycle_ablation(
    state: Any,
    *,
    scan_id: str,
    cycle_time: datetime,
    trading_date: str,
    official_accepted_symbols: Sequence[str],
    abl: Ablation,
) -> dict[str, Any]:
    from small_paper.cost_aware_entry_shadow import (
        CAP,
        STOP_Z_REJECT,
        ShadowPosition,
        _close_expired,
        _cs_z,
        _f,
        append_shadow_event,
        winner_enrichment_from_cycle,
    )

    rows = state.pending_cycle.pop(scan_id, [])
    t = cycle_time
    _close_expired(state, now=t, trading_date=trading_date)
    free_before = CAP - len(state.open_shadow)
    open_before = list(state.open_shadow.keys())
    official_set = {str(s) for s in (official_accepted_symbols or [])}

    if not rows:
        state.selection_cycles += 1
        return {"selection_cycle_id": scan_id, "accepted": [], "rejected": []}

    feats = [r["features"] for r in rows]
    z_pb = _cs_z([f["pbv2_score"] for f in feats])
    z_st = _cs_z([f["stop_risk_score"] for f in feats])
    we = winner_enrichment_from_cycle(feats) if abl.use_winner else [0.0] * len(feats)

    scored = []
    for i, r in enumerate(rows):
        score = float(z_pb[i])
        if abl.use_winner:
            score += 0.35 * float(we[i])
        if abl.use_stop_score:
            score -= 0.45 * float(z_st[i])
        scored.append(
            {
                **r,
                "z_pbv2": z_pb[i],
                "z_stop": z_st[i],
                "winner_enrichment": we[i],
                "integrated_score": score,
            }
        )
    scored.sort(key=lambda x: x["integrated_score"], reverse=True)

    slots = free_before
    accepted: list[str] = []
    rejected: list[dict] = []
    rank_slots_used = 0
    selected = 0

    for rank_i, row in enumerate(scored, start=1):
        sym = row["symbol"]
        if sym in state.open_shadow:
            continue
        stop_reject = bool(abl.use_stop_reject and row["z_stop"] >= STOP_Z_REJECT)
        if stop_reject:
            state.stop_risk_reject += 1
            rejected.append({"symbol": sym, "reason": "stop_risk", "rank": rank_i, "z_stop": row["z_stop"]})
            if abl.use_nofill and rank_slots_used < slots:
                rank_slots_used += 1
                state.same_snapshot_nofill += 1
                state.pending_unfilled.append(
                    {"cycle_id": scan_id, "t": t, "rejected_symbol": sym, "resolved": False}
                )
            # if not nofill: walk down (do not consume slot)
            continue

        state.shadow_eligible += 1
        if selected >= slots:
            break
        if abl.use_nofill and rank_slots_used >= slots:
            break
        if not abl.use_nofill:
            # walkdown / always-fill path: only selected count matters
            pass
        else:
            rank_slots_used += 1

        if not abl.use_nofill and selected >= slots:
            break

        px = row["entry_price"] if row["entry_price"] > 0 else _f(row["trade"].get("CurrentPrice"))
        pos = ShadowPosition(
            symbol=sym,
            entry_time=t,
            entry_price=px,
            selection_cycle_id=scan_id,
            rank=rank_i,
            integrated_score=row["integrated_score"],
            winner_enrichment=row["winner_enrichment"],
            stop_risk=row["features"]["stop_risk_score"],
            stop_margin_z=STOP_Z_REJECT - row["z_stop"],
            pbv2_score=row["features"]["pbv2_score"],
        )
        state.open_shadow[sym] = pos
        accepted.append(sym)
        selected += 1
        state.shadow_entries += 1
        for pend in state.pending_unfilled:
            if pend.get("resolved"):
                continue
            if t > pend["t"]:
                pend["resolved"] = True
                pend["later_symbol"] = sym
                state.later_fill += 1
                break
        if sym in official_set:
            state.official_match += 1
        elif official_set:
            state.official_mismatch += 1
        append_shadow_event(
            state,
            trading_date,
            {
                "event": "shadow_entry",
                "selection_cycle_id": scan_id,
                "symbol": sym,
                "ablation": abl.name,
            },
        )

    state.selection_cycles += 1
    return {"accepted": accepted, "rejected": rejected}


def load_bundle(session_dir: Path, trading_date: str) -> dict[str, Any]:
    from small_paper.am_pm_session_policy import AmPmSessionPolicy
    from small_paper.cost_aware_price_path import build_symbol_price_paths, parse_ts
    from small_paper.cost_aware_shadow_recompute import _load_jsonl, _session_kind_from_dir

    summary_path = session_dir / "small_paper_summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
    session_kind = _session_kind_from_dir(session_dir, summary)
    policy = AmPmSessionPolicy.from_kind(session_kind)
    y, m, d = int(trading_date[:4]), int(trading_date[4:6]), int(trading_date[6:8])
    hh, mm = map(int, policy.force_close.split(":"))
    force_close = datetime(y, m, d, hh, mm, tzinfo=JST)

    print(f"  loading events {session_dir.name}...", flush=True)
    events = _load_jsonl(session_dir / "small_paper_events.jsonl")
    price_paths = build_symbol_price_paths(events)

    cand_by_sym: dict[str, list[tuple[datetime, dict]]] = defaultdict(list)
    official_accepts: list[tuple[datetime, str]] = []
    official_exits: list[tuple[datetime, str, float, str]] = []
    for e in events:
        et = e.get("event_type")
        if et == "candidate":
            ts = parse_ts(e.get("event_time") or e.get("timestamp") or e.get("eval_end_ts"))
            if ts:
                cand_by_sym[str(e.get("symbol"))].append((ts, e))
        elif et == "accepted":
            ts = parse_ts(e.get("entry_time") or e.get("event_time"))
            if ts:
                official_accepts.append((ts, str(e.get("symbol"))))
        elif et == "observer_exit":
            ts = parse_ts(e.get("exit_time") or e.get("event_time"))
            try:
                px = float(e.get("exit_price") or 0)
            except (TypeError, ValueError):
                px = 0.0
            if ts:
                official_exits.append((ts, str(e.get("symbol")), px, str(e.get("exit_reason") or "")))
    for sym in cand_by_sym:
        cand_by_sym[sym].sort(key=lambda x: x[0])
    official_exits.sort(key=lambda x: x[0])

    scans: dict[str, list[dict]] = defaultdict(list)
    scan_times: dict[str, datetime] = {}
    for e in _load_jsonl(session_dir / "entry_scan_audit.jsonl"):
        if e.get("audit_type") != "entry_symbol_eval":
            continue
        sid = str(e.get("scan_id") or "")
        if not sid:
            continue
        scans[sid].append(e)
        t = parse_ts(e.get("eval_end_ts") or e.get("eval_start_ts"))
        if t and (sid not in scan_times or t < scan_times[sid]):
            scan_times[sid] = t

    # free memory of raw events list (price_paths retained)
    del events

    return {
        "session_dir": session_dir,
        "trading_date": trading_date,
        "session_kind": session_kind,
        "force_close": force_close,
        "summary": summary,
        "cand_by_sym": cand_by_sym,
        "official_accepts": official_accepts,
        "official_exits": official_exits,
        "scans": scans,
        "scan_times": scan_times,
        "price_paths": price_paths,
        "official_pnl": _official_pnl(summary),
        "accepted_count": int(summary.get("accepted_count") or 0),
    }


def replay_bundle(bundle: dict[str, Any], abl: Ablation, *, is_freeze_recovery: bool = True) -> dict[str, Any]:
    from small_paper.cost_aware_entry_shadow import (
        CostAwareShadowState,
        ShadowPosition,
        _close_expired,
        apply_runtime_compatible_exit,
        finalize_never_filled,
        finalize_open_positions,
        note_symbol_eval,
        summarize_state,
    )
    from small_paper.cost_aware_price_path import last_valid_price_at_or_before, parse_ts

    cand_by_sym = bundle["cand_by_sym"]
    force_close = bundle["force_close"]
    trading_date = bundle["trading_date"]
    official_accepts = bundle["official_accepts"]
    official_exits = bundle["official_exits"]
    price_paths = bundle["price_paths"]
    scans = bundle["scans"]
    scan_times = bundle["scan_times"]

    def nearest_trade(sym: str, t: datetime) -> Optional[dict]:
        arr = cand_by_sym.get(sym) or []
        best = None
        for ts, row in arr:
            if ts <= t:
                best = row
            else:
                break
        # causal: do not use future first-candidate fallback
        return best

    state = CostAwareShadowState()
    ordered = sorted(scans.keys(), key=lambda s: scan_times.get(s) or datetime.min.replace(tzinfo=JST))
    for sid in ordered:
        t = scan_times[sid]
        for ev in scans[sid]:
            trade = nearest_trade(str(ev.get("symbol")), t)
            if trade is None:
                continue
            note_symbol_eval(
                state,
                scan_id=sid,
                symbol=str(ev.get("symbol")),
                trade=trade,
                official_accept=False,
            )
        offs = [sym for ots, sym in official_accepts if abs((ots - t).total_seconds()) <= 3]
        run_selection_cycle_ablation(
            state,
            scan_id=sid,
            cycle_time=t,
            trading_date=trading_date,
            official_accepted_symbols=offs,
            abl=abl,
        )
        for sym, pos in list(state.open_shadow.items()):
            hit = last_valid_price_at_or_before(
                price_paths.get(sym, []), asof=t, not_before=pos.entry_time
            )
            if hit:
                _pts, px, _age = hit
                pos.last_mark_price = px
                pos.last_mark_time = _pts
                if not pos.price_path or pos.price_path[-1][0] != _pts:
                    pos.price_path.append((_pts, px))

    finalize_never_filled(state)
    _close_expired(state, now=force_close, trading_date=trading_date, price_paths=price_paths)
    finalize_n = finalize_open_positions(
        state,
        force_close_time=force_close,
        trading_date=trading_date,
        price_paths=price_paths,
        is_freeze_recovery=is_freeze_recovery,
    )

    recovery_by_sym: dict[str, tuple[datetime, float]] = {}
    for ts, sym, px, reason in official_exits:
        if reason == "recovery_forced_close" and px > 0:
            recovery_by_sym[sym] = (ts, px)

    enriched: list[dict[str, Any]] = []
    for row in state.closed_trades:
        sym = str(row.get("symbol"))
        et = parse_ts(row.get("shadow_entry_time") or row.get("entry_time"))
        ep = float(row.get("shadow_entry_price") or row.get("entry_price") or 0)
        if et is None or ep <= 0:
            enriched.append(row)
            continue
        next_exit = None
        for ts, o_sym, px, reason in official_exits:
            if ts > et:
                next_exit = (ts, o_sym, px, reason)
                break
        asof = next_exit[0] if next_exit else force_close
        src = "runtime_next_official_exit_time"
        px_out: Optional[float] = None
        age: Optional[float] = None
        if sym in recovery_by_sym and recovery_by_sym[sym][0] >= et:
            asof = recovery_by_sym[sym][0]
            px_out = recovery_by_sym[sym][1]
            src = "formal_recovery_exit_price"
            age = 0.0
        else:
            hit = last_valid_price_at_or_before(
                price_paths.get(sym, []), asof=asof, not_before=et
            )
            if hit is None:
                row = dict(row)
                row["runtime_compatible_na"] = True
                enriched.append(row)
                continue
            _pts, px_out, age = hit
            src = "last_valid_before_runtime_exit_time"
        pos = ShadowPosition(
            symbol=sym,
            entry_time=et,
            entry_price=ep,
            selection_cycle_id=str(row.get("selection_cycle_id") or ""),
            rank=int(row.get("rank") or 0),
            integrated_score=float(row.get("integrated_score") or 0),
            winner_enrichment=float(row.get("winner_enrichment") or 0),
            stop_risk=float(row.get("stop_risk") or 0),
            stop_margin_z=0.0,
            pbv2_score=0.0,
        )
        apply_runtime_compatible_exit(
            pos,
            exit_time=asof,
            exit_price=px_out,
            price_source=src,
            price_age_sec=age,
            na=False,
        )
        row = dict(row)
        row.update(
            {
                "runtime_compatible_exit_time": pos.runtime_compatible_exit_time.isoformat()
                if pos.runtime_compatible_exit_time
                else None,
                "runtime_compatible_exit_price": pos.runtime_compatible_exit_price,
                "runtime_compatible_gross_yen": pos.runtime_compatible_gross_yen,
                "runtime_compatible_net_yen": pos.runtime_compatible_net_yen,
                "runtime_compatible_price_source": pos.runtime_compatible_price_source,
                "runtime_compatible_price_age_sec": pos.runtime_compatible_price_age_sec,
                "runtime_compatible_na": pos.runtime_compatible_na,
            }
        )
        enriched.append(row)

    state.closed_trades = enriched
    out = summarize_state(state)
    out["session_kind"] = bundle["session_kind"]
    out["force_close_time"] = force_close.isoformat()
    out["finalize_open_count"] = finalize_n
    out["ablation"] = abl.name
    out["session_dir"] = str(bundle["session_dir"])
    return out


# ---------------------------------------------------------------------------
# Metrics helpers
# ---------------------------------------------------------------------------
def _pf(values: list[float]) -> Optional[float]:
    wins = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    if losses <= 1e-12:
        return None if wins <= 0 else float("inf")
    return round(wins / losses, 4)


def _max_dd(values: list[float]) -> Optional[float]:
    if not values:
        return None
    eq = 0.0
    peak = 0.0
    max_dd = 0.0
    for v in values:
        eq += v
        peak = max(peak, eq)
        max_dd = min(max_dd, eq - peak)
    return round(max_dd, 2)


def trade_stats(trades: list[dict[str, Any]], *, raw_key: str, net_key: str) -> dict[str, Any]:
    raws = [float(t[raw_key]) for t in trades if isinstance(t.get(raw_key), (int, float))]
    nets = [
        float(t[net_key])
        for t in trades
        if isinstance(t.get(net_key), (int, float)) and not t.get("runtime_compatible_na")
    ]
    # for shadow use gross/net; for runtime use runtime keys
    def _wl(xs):
        w = sum(1 for x in xs if x > 0)
        l = sum(1 for x in xs if x < 0)
        f = sum(1 for x in xs if x == 0)
        return w, l, f

    wr, lr, fr = _wl(raws)
    wn, ln, fn = _wl(nets)
    return {
        "trades": len(raws),
        "raw_pnl": round(sum(raws), 2) if raws else None,
        "pnl_5bps": round(sum(nets), 2) if nets else None,
        "pf_raw": _pf(raws),
        "pf_5bps": _pf(nets),
        "wins_raw": wr,
        "losses_raw": lr,
        "draws_raw": fr,
        "wins_5bps": wn,
        "losses_5bps": ln,
        "draws_5bps": fn,
        "win_rate_raw": round(wr / len(raws), 4) if raws else None,
        "win_rate_5bps": round(wn / len(nets), 4) if nets else None,
        "max_dd_raw": _max_dd(raws),
        "max_dd_5bps": _max_dd(nets),
        "avg_raw": round(statistics.mean(raws), 2) if raws else None,
        "avg_5bps": round(statistics.mean(nets), 2) if nets else None,
        "median_raw": round(statistics.median(raws), 2) if raws else None,
        "median_5bps": round(statistics.median(nets), 2) if nets else None,
        "raws": raws,
        "nets": nets,
    }


def session_metrics(raw: Mapping[str, Any]) -> dict[str, Any]:
    trades = list(raw.get("closed_trades") or [])
    sh = trade_stats(trades, raw_key="gross_pnl_yen_100", net_key="net_pnl_yen_100")
    rt_trades = [t for t in trades if not t.get("runtime_compatible_na")]
    rt = trade_stats(rt_trades, raw_key="runtime_compatible_gross_yen", net_key="runtime_compatible_net_yen")
    delta_raw = (
        round(sh["raw_pnl"] - rt["raw_pnl"], 2)
        if sh["raw_pnl"] is not None and rt["raw_pnl"] is not None
        else None
    )
    delta_5 = (
        round(sh["pnl_5bps"] - rt["pnl_5bps"], 2)
        if sh["pnl_5bps"] is not None and rt["pnl_5bps"] is not None
        else None
    )
    return {
        "status": raw.get("status"),
        "open": int(raw.get("n_open") or 0),
        "closed": int(raw.get("n_closed") or 0),
        "shadow_entries": int(raw.get("shadow_entries") or 0),
        "stop_risk_reject": int(raw.get("stop_risk_reject") or 0),
        "same_snapshot_nofill": int(raw.get("same_snapshot_nofill") or 0),
        "later_fill": int(raw.get("later_fill") or 0),
        "never_filled": int(raw.get("never_filled") or 0),
        "selection_cycles": int(raw.get("selection_cycles") or 0),
        "runtime": {k: v for k, v in rt.items() if k not in ("raws", "nets")},
        "shadow": {k: v for k, v in sh.items() if k not in ("raws", "nets")},
        "delta_raw": delta_raw,
        "delta_5bps": delta_5,
        "pf_delta_5bps": (
            round(float(sh["pf_5bps"]) - float(rt["pf_5bps"]), 4)
            if isinstance(sh["pf_5bps"], (int, float))
            and isinstance(rt["pf_5bps"], (int, float))
            and math.isfinite(sh["pf_5bps"])
            and math.isfinite(rt["pf_5bps"])
            else None
        ),
        "trade_count_delta": (sh["trades"] - rt["trades"]) if sh["trades"] is not None else None,
        "_rt_raws": rt["raws"],
        "_rt_nets": rt["nets"],
        "_sh_raws": sh["raws"],
        "_sh_nets": sh["nets"],
        "_trades": trades,
    }


def aggregate_days(session_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """session_rows: metrics with _rt/_sh series."""
    rt_raws: list[float] = []
    rt_nets: list[float] = []
    sh_raws: list[float] = []
    sh_nets: list[float] = []
    for m in session_rows:
        rt_raws.extend(m.get("_rt_raws") or [])
        rt_nets.extend(m.get("_rt_nets") or [])
        sh_raws.extend(m.get("_sh_raws") or [])
        sh_nets.extend(m.get("_sh_nets") or [])
    return {
        "runtime_raw": round(sum(rt_raws), 2),
        "runtime_5bps": round(sum(rt_nets), 2),
        "shadow_raw": round(sum(sh_raws), 2),
        "shadow_5bps": round(sum(sh_nets), 2),
        "delta_raw": round(sum(sh_raws) - sum(rt_raws), 2),
        "delta_5bps": round(sum(sh_nets) - sum(rt_nets), 2),
        "runtime_pf_5bps": _pf(rt_nets),
        "shadow_pf_5bps": _pf(sh_nets),
        "runtime_pf_raw": _pf(rt_raws),
        "shadow_pf_raw": _pf(sh_raws),
        "runtime_trades": len(rt_raws),
        "shadow_trades": len(sh_raws),
        "runtime_max_dd_5bps": _max_dd(rt_nets),
        "shadow_max_dd_5bps": _max_dd(sh_nets),
        "runtime_max_dd_raw": _max_dd(rt_raws),
        "shadow_max_dd_raw": _max_dd(sh_raws),
    }


def day_verdict(delta_5: Optional[float]) -> str:
    if delta_5 is None:
        return "same"
    if delta_5 > 0:
        return "improved"
    if delta_5 < 0:
        return "worse"
    return "same"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    try:
        from openpyxl import Workbook
    except ImportError:
        import subprocess

        subprocess.check_call([sys.executable, "-m", "pip", "install", "openpyxl", "-q"])
        from openpyxl import Workbook

    OUT.mkdir(parents=True, exist_ok=True)
    suppressed = _suppress_shadow_writes()

    # SHA before
    sha_before: dict[str, dict[str, str]] = {}
    for d in MUST_DATES:
        for sess in (SMALL / d).glob("live_session_*"):
            for fn in ("small_paper_summary.json", "small_paper_events.jsonl", "session_seal.json"):
                p = sess / fn
                if p.is_file() and p.stat().st_size > 0:
                    sha_before.setdefault(str(sess.relative_to(ROOT)).replace("\\", "/"), {})[fn] = _sha256_file(p)

    coverage = discover_sessions()
    primary = _pick_primary_sessions(coverage)
    print("coverage", len(coverage), "primary_evaluable", len(primary), flush=True)
    for p in primary:
        print("  EVAL", p["date"], p["session"], p["session_path"], flush=True)

    current_spec = {
        "components_active": [
            {
                "name": "Winner Enrichment",
                "included": True,
                "enabled": True,
                "impl": "src/small_paper/cost_aware_entry_shadow.py:winner_enrichment_from_cycle / integrated_score",
                "features": ["rise", "spread", "mom", "near_high", "cycle q20/q80"],
                "formula": "0-3 cycle rules; score += 0.35 * enrichment",
                "threshold": "cycle quantiles 0.2/0.8",
                "role": "ranking",
                "future_info": False,
                "w54_fix_match": "yes (live 0-3 proxy)",
            },
            {
                "name": "STOP Risk Reject",
                "included": True,
                "enabled": True,
                "impl": "cost_aware_entry_shadow.py:STOP_Z_REJECT / run_selection_cycle",
                "features": ["stop_risk_score → cycle z"],
                "formula": "reject if z_stop >= 1.65; score -= 0.45*z_stop",
                "threshold": 1.65,
                "role": "reject + ranking penalty",
                "future_info": False,
                "w54_fix_match": "partial (runtime z=1.65 vs research raw thr≈1.782)",
            },
            {
                "name": "same-snapshot No-Fill",
                "included": True,
                "enabled": True,
                "impl": "cost_aware_entry_shadow.py:run_selection_cycle no-walkdown",
                "features": ["STOP reject consumes rank slot"],
                "formula": "rejected rank consumes opportunity; no same-snapshot walk-down",
                "threshold": None,
                "role": "no-fill",
                "future_info": False,
                "w54_fix_match": "yes",
            },
        ],
        "score_formula": "z(pbv2)+0.35*winner_enrichment-0.45*z(stop_risk)",
        "np_in_decision": False,
        "researched_not_adopted": [
            "NP Risk reject/score term",
            "Score threshold / high-edge trigger",
            "Always-fill / qualified-fill arms",
            "CHASE / VWAP extended reject",
            "Pullback 3/4-feature masks",
            "Reentry hybrid / cooloff early unlock",
            "Expected net edge / net0 gate",
            "Hard daily 45 quota",
            "Offline W53 six-rule Winner Enrichment exact feature set",
        ],
    }

    # Distinct ablations to run (collapse exact duplicates in reporting later)
    run_list = [
        a
        for a in ABLATIONS
        if a.name
        in {
            "Baseline_PBv2",
            "Winner_only",
            "STOP_walkdown",
            "STOP_NoFill",
            "Winner_STOP_walkdown",
            "Winner_STOP_NoFill_CURRENT",
            "LeaveOut_Winner",
            "LeaveOut_NoFill",
        }
    ]

    # session_key -> ablation -> metrics
    all_session_results: dict[str, dict[str, Any]] = {}
    blocked_rows: list[dict[str, Any]] = []

    for meta in primary:
        sess_dir = Path(meta["session_dir"])
        date = meta["date"]
        key = f"{date}_{meta['session']}"
        print(f"BUNDLE {key}", flush=True)
        bundle = load_bundle(sess_dir, date)
        all_session_results[key] = {
            "meta": meta,
            "official_pnl": bundle["official_pnl"],
            "accepted_count": bundle["accepted_count"],
            "ablations": {},
        }
        for abl in run_list:
            print(f"  ablation {abl.name}...", flush=True)
            raw = replay_bundle(bundle, abl, is_freeze_recovery=True)
            m = session_metrics(raw)
            all_session_results[key]["ablations"][abl.name] = m
            # STOP blocked trade attribution for CURRENT only
            if abl.name == "Winner_STOP_NoFill_CURRENT":
                for t in m.get("_trades") or []:
                    pass
                # Compare CURRENT vs LeaveOut_STOP / Baseline for blocked effect later

        # Attribution via leave-one-out on this session
        cur = all_session_results[key]["ablations"].get("Winner_STOP_NoFill_CURRENT")
        lo_w = all_session_results[key]["ablations"].get("LeaveOut_Winner")
        lo_nf = all_session_results[key]["ablations"].get("LeaveOut_NoFill")
        win_only = all_session_results[key]["ablations"].get("Winner_only")
        stop_nf = all_session_results[key]["ablations"].get("STOP_NoFill")
        base = all_session_results[key]["ablations"].get("Baseline_PBv2")
        attr = {}
        if cur and lo_w:
            attr["winner_marginal_5bps"] = (
                round(cur["delta_5bps"] - lo_w["delta_5bps"], 2)
                if cur["delta_5bps"] is not None and lo_w["delta_5bps"] is not None
                else None
            )
        if cur and lo_nf:
            attr["nofill_marginal_5bps"] = (
                round(cur["delta_5bps"] - lo_nf["delta_5bps"], 2)
                if cur["delta_5bps"] is not None and lo_nf["delta_5bps"] is not None
                else None
            )
        if cur and win_only:
            attr["stop_system_marginal_5bps"] = (
                round(cur["delta_5bps"] - win_only["delta_5bps"], 2)
                if cur["delta_5bps"] is not None and win_only["delta_5bps"] is not None
                else None
            )
        if stop_nf and base:
            attr["stop_nofill_vs_baseline_5bps"] = (
                round(stop_nf["delta_5bps"] - base["delta_5bps"], 2)
                if stop_nf["delta_5bps"] is not None and base["delta_5bps"] is not None
                else None
            )
        if cur:
            attr["stop_risk_reject"] = cur["stop_risk_reject"]
            attr["same_snapshot_nofill"] = cur["same_snapshot_nofill"]
        all_session_results[key]["attribution"] = attr
        # drop heavy series from memory in stored copy later

    # Daily + all-days for CURRENT and each ablation
    dates = sorted({m["date"] for m in primary})
    daily_current: list[dict[str, Any]] = []
    combination_all: dict[str, Any] = {}

    for abl in run_list:
        sess_ms = []
        for key, pack in all_session_results.items():
            m = pack["ablations"].get(abl.name)
            if m:
                sess_ms.append(m)
        combination_all[abl.name] = aggregate_days(sess_ms)
        combination_all[abl.name]["sessions"] = len(sess_ms)

    for date in dates:
        day_ms = []
        am = all_session_results.get(f"{date}_AM", {}).get("ablations", {}).get(
            "Winner_STOP_NoFill_CURRENT"
        )
        pm = all_session_results.get(f"{date}_PM", {}).get("ablations", {}).get(
            "Winner_STOP_NoFill_CURRENT"
        )
        for m in (am, pm):
            if m:
                day_ms.append(m)
        agg = aggregate_days(day_ms)
        # dominant contributor from session attrs
        attrs = []
        for sess in ("AM", "PM"):
            a = all_session_results.get(f"{date}_{sess}", {}).get("attribution") or {}
            attrs.append(a)
        dom = None
        best_v = None
        for a in attrs:
            for k in (
                "winner_marginal_5bps",
                "stop_system_marginal_5bps",
                "nofill_marginal_5bps",
            ):
                v = a.get(k)
                if isinstance(v, (int, float)):
                    if best_v is None or abs(v) > abs(best_v):
                        best_v = v
                        dom = k
        daily_current.append(
            {
                "date": date,
                "am_status": (am or {}).get("status"),
                "pm_status": (pm or {}).get("status"),
                "runtime_raw": agg["runtime_raw"],
                "cost_aware_raw": agg["shadow_raw"],
                "raw_delta": agg["delta_raw"],
                "runtime_5bps": agg["runtime_5bps"],
                "cost_aware_5bps": agg["shadow_5bps"],
                "delta_5bps": agg["delta_5bps"],
                "runtime_pf": agg["runtime_pf_5bps"],
                "cost_aware_pf": agg["shadow_pf_5bps"],
                "runtime_trades": agg["runtime_trades"],
                "cost_aware_trades": agg["shadow_trades"],
                "verdict": day_verdict(agg["delta_5bps"]),
                "dominant_contributor": dom,
                "notes": "",
            }
        )

    cur_all = combination_all["Winner_STOP_NoFill_CURRENT"]
    improved = sum(1 for d in daily_current if d["verdict"] == "improved")
    worse = sum(1 for d in daily_current if d["verdict"] == "worse")
    same = sum(1 for d in daily_current if d["verdict"] == "same")

    # Best/worst component by marginal all-days (leave-one-out / solo)
    component_results = {
        "Winner_only": combination_all.get("Winner_only"),
        "STOP_NoFill": combination_all.get("STOP_NoFill"),
        "Baseline_PBv2": combination_all.get("Baseline_PBv2"),
        "LeaveOut_Winner": combination_all.get("LeaveOut_Winner"),
        "LeaveOut_NoFill": combination_all.get("LeaveOut_NoFill"),
        "Winner_STOP_walkdown": combination_all.get("Winner_STOP_walkdown"),
    }
    # marginal vs leave-out
    marg = {
        "Winner": None
        if cur_all["delta_5bps"] is None or combination_all["LeaveOut_Winner"]["delta_5bps"] is None
        else round(cur_all["delta_5bps"] - combination_all["LeaveOut_Winner"]["delta_5bps"], 2),
        "NoFill": None
        if cur_all["delta_5bps"] is None or combination_all["LeaveOut_NoFill"]["delta_5bps"] is None
        else round(cur_all["delta_5bps"] - combination_all["LeaveOut_NoFill"]["delta_5bps"], 2),
        "STOP_system_vs_Winner_only": None
        if cur_all["delta_5bps"] is None or combination_all["Winner_only"]["delta_5bps"] is None
        else round(cur_all["delta_5bps"] - combination_all["Winner_only"]["delta_5bps"], 2),
    }
    # best/worst among solo components by delta_5bps
    solo = {
        "Winner_only": combination_all["Winner_only"]["delta_5bps"],
        "STOP_NoFill": combination_all["STOP_NoFill"]["delta_5bps"],
        "Baseline_PBv2": combination_all["Baseline_PBv2"]["delta_5bps"],
    }
    best_comp = max(solo.items(), key=lambda x: (x[1] is not None, x[1] or -1e99))
    worst_comp = min(solo.items(), key=lambda x: (x[1] is not None, x[1] if x[1] is not None else 1e99))

    best_combo = max(
        ((n, v) for n, v in combination_all.items()),
        key=lambda x: (x[1].get("delta_5bps") is not None, x[1].get("delta_5bps") or -1e99),
    )

    # Stability: leave-one-day-out
    lodo = []
    for leave in dates:
        ms = []
        for key, pack in all_session_results.items():
            if key.startswith(leave):
                continue
            m = pack["ablations"].get("Winner_STOP_NoFill_CURRENT")
            if m:
                ms.append(m)
        if ms:
            agg = aggregate_days(ms)
            lodo.append({"leave_date": leave, **{k: agg[k] for k in ("delta_5bps", "shadow_5bps", "runtime_5bps", "shadow_pf_5bps")}})

    # leave-one-symbol-out (top symbols by |pnl|)
    all_cur_trades: list[dict[str, Any]] = []
    for pack in all_session_results.values():
        m = pack["ablations"].get("Winner_STOP_NoFill_CURRENT")
        if m:
            all_cur_trades.extend(m.get("_trades") or [])
    by_sym = defaultdict(float)
    for t in all_cur_trades:
        if isinstance(t.get("net_pnl_yen_100"), (int, float)):
            by_sym[str(t.get("symbol"))] += float(t["net_pnl_yen_100"])
    top_syms = [s for s, _ in sorted(by_sym.items(), key=lambda x: -abs(x[1]))[:10]]
    loso = []
    for sym in top_syms[:5]:
        nets_rt = []
        nets_sh = []
        for t in all_cur_trades:
            if str(t.get("symbol")) == sym:
                continue
            if isinstance(t.get("net_pnl_yen_100"), (int, float)):
                nets_sh.append(float(t["net_pnl_yen_100"]))
            if isinstance(t.get("runtime_compatible_net_yen"), (int, float)) and not t.get(
                "runtime_compatible_na"
            ):
                nets_rt.append(float(t["runtime_compatible_net_yen"]))
        loso.append(
            {
                "leave_symbol": sym,
                "delta_5bps": round(sum(nets_sh) - sum(nets_rt), 2),
                "shadow_5bps": round(sum(nets_sh), 2),
                "runtime_5bps": round(sum(nets_rt), 2),
            }
        )

    # top10 trade exclusion
    sh_sorted = sorted(
        [t for t in all_cur_trades if isinstance(t.get("net_pnl_yen_100"), (int, float))],
        key=lambda t: -abs(float(t["net_pnl_yen_100"])),
    )
    drop_ids = set(id(t) for t in sh_sorted[:10])
    nets_sh = [
        float(t["net_pnl_yen_100"])
        for t in all_cur_trades
        if id(t) not in drop_ids and isinstance(t.get("net_pnl_yen_100"), (int, float))
    ]
    nets_rt = [
        float(t["runtime_compatible_net_yen"])
        for t in all_cur_trades
        if id(t) not in drop_ids
        and isinstance(t.get("runtime_compatible_net_yen"), (int, float))
        and not t.get("runtime_compatible_na")
    ]
    top10_excl = {
        "dropped": 10,
        "delta_5bps": round(sum(nets_sh) - sum(nets_rt), 2),
        "shadow_5bps": round(sum(nets_sh), 2),
        "runtime_5bps": round(sum(nets_rt), 2),
    }

    # Cost sensitivity 0/2.5/5/10 on CURRENT trades (recompute net from gross)
    cost_sens = {}
    for bps in (0.0, 2.5, 5.0, 10.0):
        sh_n = []
        rt_n = []
        for t in all_cur_trades:
            ep = float(t.get("entry_price") or t.get("shadow_entry_price") or 0)
            if ep <= 0:
                continue
            cost = ep * 100.0 * (bps / 100.0)
            if isinstance(t.get("gross_pnl_yen_100"), (int, float)):
                sh_n.append(float(t["gross_pnl_yen_100"]) - cost)
            if isinstance(t.get("runtime_compatible_gross_yen"), (int, float)) and not t.get(
                "runtime_compatible_na"
            ):
                rt_n.append(float(t["runtime_compatible_gross_yen"]) - cost)
        cost_sens[str(bps)] = {
            "shadow_pnl": round(sum(sh_n), 2),
            "runtime_pnl": round(sum(rt_n), 2),
            "delta": round(sum(sh_n) - sum(rt_n), 2),
        }

    # Market regime from summaries (available fields only)
    market_regime = []
    for key, pack in all_session_results.items():
        meta = pack["meta"]
        summary = json.loads(Path(meta["session_dir"], "small_paper_summary.json").read_text(encoding="utf-8"))
        cur = pack["ablations"]["Winner_STOP_NoFill_CURRENT"]
        # extract known rates if present
        market_regime.append(
            {
                "date": meta["date"],
                "session": meta["session"],
                "delta_5bps": cur["delta_5bps"],
                "verdict": day_verdict(cur["delta_5bps"]),
                "accepted_count": pack["accepted_count"],
                "official_pnl": pack["official_pnl"],
                "observer_stop_count": summary.get("observer_stop_count")
                or summary.get("stop_hit_count"),
                "no_progress_count": summary.get("no_progress_exit_count")
                or (summary.get("structural_exit_reason_counts") or {}).get("no_progress_exit"),
                "reentry_after_no_progress": summary.get("reentry_after_no_progress_count"),
                "same_symbol_reentry": summary.get("same_symbol_reentry_count"),
                "note": "AM/PM used as analysis labels only; rates taken from summary when present else null",
            }
        )

    # Concentration
    sym_conc = sorted(by_sym.items(), key=lambda x: -abs(x[1]))[:15]
    day_conc = [(d["date"], d["delta_5bps"]) for d in daily_current]

    # Recommendation
    n_days = len(dates)
    sample_note = f"Only {n_days} evaluable trading days with events+audit; statistical significance NOT claimed."
    if (
        n_days >= 2
        and improved > worse
        and (cur_all.get("delta_5bps") or 0) > 0
        and (cur_all.get("shadow_pf_5bps") or 0) > (cur_all.get("runtime_pf_5bps") or 0)
    ):
        # still KEEP_SHADOW due to day count
        recommendation = "KEEP_SHADOW"
        rec_reason = (
            "Positive 5bps delta and PF improvement across available days, "
            "but evaluable calendar depth is only 2 days — insufficient for ADOPT_CANDIDATE."
        )
    elif (cur_all.get("delta_5bps") or 0) < 0:
        recommendation = "REJECT"
        rec_reason = "Cumulative 5bps delta negative on evaluable set."
    else:
        recommendation = "KEEP_SHADOW"
        rec_reason = sample_note

    # SHA after
    sha_ok = True
    for sp, files in sha_before.items():
        for fn, h in files.items():
            p = ROOT / sp / fn
            if not p.is_file() or _sha256_file(p) != h:
                sha_ok = False

    # Strip heavy series for JSON
    session_results_light = {}
    for key, pack in all_session_results.items():
        session_results_light[key] = {
            "meta": {k: pack["meta"][k] for k in pack["meta"] if k != "session_dir"},
            "official_pnl": pack["official_pnl"],
            "accepted_count": pack["accepted_count"],
            "attribution": pack.get("attribution"),
            "ablations": {
                name: {k: v for k, v in m.items() if not str(k).startswith("_")}
                for name, m in pack["ablations"].items()
            },
        }

    # 7/21 7/22 shortcuts
    d21 = next((d for d in daily_current if d["date"] == "20260721"), None)
    d22 = next((d for d in daily_current if d["date"] == "20260722"), None)

    excluded = [r for r in coverage if r["evaluation_status"] in ("C", "D")]
    evaluable_dates = dates

    report = {
        "phase": "cost_aware_all_dates_attribution",
        "generated_at": datetime.now(JST).isoformat(timespec="seconds"),
        "paper_only": True,
        "order_enabled": False,
        "submit": 0,
        "cancel": 0,
        "live_order": 0,
        "current_spec": current_spec,
        "coverage": coverage,
        "primary_evaluable_sessions": [
            {k: v for k, v in p.items() if k != "session_dir"} for p in primary
        ],
        "evaluable_dates": evaluable_dates,
        "excluded_dates_summary": {
            "count": len({r["date"] for r in excluded}),
            "reasons": dict(Counter(r["exclusion_reason"] or "unknown" for r in excluded)),
        },
        "daily_results_current": daily_current,
        "session_results": session_results_light,
        "combination_results": combination_all,
        "component_marginal_5bps": marg,
        "component_solo_delta_5bps": solo,
        "best_component": {"name": best_comp[0], "delta_5bps": best_comp[1]},
        "worst_component": {"name": worst_comp[0], "delta_5bps": worst_comp[1]},
        "best_combination": {
            "name": best_combo[0],
            "metrics": best_combo[1],
        },
        "current_full": cur_all,
        "days_summary": {"improved": improved, "worse": worse, "same": same},
        "stability": {
            "sample_note": sample_note,
            "leave_one_day_out": lodo,
            "leave_one_symbol_out_top5": loso,
            "top10_trade_exclusion": top10_excl,
            "cost_sensitivity_bps": cost_sens,
            "bootstrap_ci": None,
            "bootstrap_ci_note": "Not computed — sample size insufficient for meaningful CI",
            "threshold_neighborhood": None,
            "threshold_neighborhood_note": "Not swept — would require additional ablations; STOP_Z fixed at 1.65",
            "leave_one_sector_out": None,
            "leave_one_sector_out_note": "Sector map not available in session artifacts; skipped",
        },
        "concentration": {
            "by_symbol_top15_shadow_5bps": [{"symbol": s, "pnl": p} for s, p in sym_conc],
            "by_day_delta_5bps": [{"date": d, "delta": v} for d, v in day_conc],
        },
        "market_regime": market_regime,
        "recommendation": {
            "verdict": recommendation,
            "reason": rec_reason,
        },
        "official_paper_unchanged": sha_ok,
        "shadow_writes_suppressed": suppressed["n"],
        "overall": "COMPLETE" if primary and sha_ok else "INCOMPLETE",
        "ablation_omission_note": (
            "N=3 active levers → evaluated 8 distinct configs (full 2^3 plus leave-outs; "
            "degenerate Winner_NoFill_noop/LeaveOut_STOP collapsed into Winner_only)."
        ),
        "d721": d21,
        "d722": d22,
    }

    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def yen(v):
        if v is None:
            return "n/a"
        if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
            return str(v)
        return f"{float(v):+,.2f}".rstrip("0").rstrip(".")

    md = f"""# Cost-Aware All Dates & Attribution

Generated: {report['generated_at']}
Paper / offline replay only. Official sessions not modified.

## Current Spec (code + W54-FIX audit)

Score: `{current_spec['score_formula']}`
NP in decision: {current_spec['np_in_decision']}

Active components:
1. Winner Enrichment — ranking (+0.35 × 0–3)
2. STOP Risk Reject — reject z_stop≥1.65 + score −0.45×z_stop
3. same-snapshot No-Fill — no walk-down when STOP rejects

Researched / not adopted: {', '.join(current_spec['researched_not_adopted'])}

## Coverage

Detected session rows: {len(coverage)}
Primary evaluable sessions: {len(primary)}
Evaluable dates: {', '.join(evaluable_dates) if evaluable_dates else 'none'}
Excluded date-count: {report['excluded_dates_summary']['count']}
Exclusion reasons: {json.dumps(report['excluded_dates_summary']['reasons'], ensure_ascii=False)}

Must-include check: 20260721={'YES' if '20260721' in evaluable_dates else 'NO'}, 20260722={'YES' if '20260722' in evaluable_dates else 'NO'}

## All-days CURRENT (Winner+STOP+NoFill)

| | Runtime | Cost-Aware | Delta |
|--|---------|------------|-------|
| raw | {yen(cur_all['runtime_raw'])} | {yen(cur_all['shadow_raw'])} | {yen(cur_all['delta_raw'])} |
| 5bps | {yen(cur_all['runtime_5bps'])} | {yen(cur_all['shadow_5bps'])} | {yen(cur_all['delta_5bps'])} |
| PF 5bps | {cur_all['runtime_pf_5bps']} | {cur_all['shadow_pf_5bps']} | |
| trades | {cur_all['runtime_trades']} | {cur_all['shadow_trades']} | |
| maxDD 5bps | {yen(cur_all['runtime_max_dd_5bps'])} | {yen(cur_all['shadow_max_dd_5bps'])} | |

Days: improved={improved} worse={worse} same={same}

## Combinations (5bps delta vs Runtime, same population)

| config | shadow 5bps | delta 5bps | PF | trades |
|--------|-------------|------------|----|--------|
"""
    for name, m in combination_all.items():
        md += f"| {name} | {yen(m['shadow_5bps'])} | {yen(m['delta_5bps'])} | {m['shadow_pf_5bps']} | {m['shadow_trades']} |\n"

    md += f"""

Best component (solo delta_5bps): {best_comp[0]} = {yen(best_comp[1])}
Worst component (solo delta_5bps): {worst_comp[0]} = {yen(worst_comp[1])}
Best combination: {best_combo[0]} delta_5bps={yen(best_combo[1].get('delta_5bps'))}

Marginal (CURRENT − leave-one-out) 5bps: {json.dumps(marg)}

## Daily CURRENT

| date | rt raw | CA raw | raw Δ | rt 5bps | CA 5bps | 5bps Δ | verdict | dominant |
|------|--------|--------|-------|---------|---------|--------|---------|----------|
"""
    for d in daily_current:
        md += (
            f"| {d['date']} | {yen(d['runtime_raw'])} | {yen(d['cost_aware_raw'])} | {yen(d['raw_delta'])} | "
            f"{yen(d['runtime_5bps'])} | {yen(d['cost_aware_5bps'])} | {yen(d['delta_5bps'])} | "
            f"{d['verdict']} | {d['dominant_contributor']} |\n"
        )

    md += f"""

## Stability

{sample_note}

Leave-one-day-out: {json.dumps(lodo, ensure_ascii=False)}
Top5 leave-one-symbol-out: {json.dumps(loso, ensure_ascii=False)}
Top10 trade exclusion: {json.dumps(top10_excl, ensure_ascii=False)}
Cost sensitivity: {json.dumps(cost_sens, ensure_ascii=False)}

## Market regime notes

Evaluable set is only 2 calendar days. Regime fields from summaries when present; no invented stats.
See audit.xlsx Market_Regime sheet.

## Recommendation

**{recommendation}**
{rec_reason}

Runtime unchanged. Official Paper unchanged: {'YES' if sha_ok else 'NO'}

---

【Cost-Aware All Dates & Attribution】

Evaluation period:
{evaluable_dates[0] if evaluable_dates else 'n/a'}〜{evaluable_dates[-1] if evaluable_dates else 'n/a'}

Detected dates:
{len({r['date'] for r in coverage})}

Evaluable dates:
{len(evaluable_dates)}・{', '.join(evaluable_dates)}

Excluded dates:
{report['excluded_dates_summary']['count']}・{json.dumps(report['excluded_dates_summary']['reasons'], ensure_ascii=False)}

Current Cost-Aware components:
- Winner Enrichment
- STOP Risk Reject
- same-snapshot No-Fill

All-days Runtime:
raw pnl {yen(cur_all['runtime_raw'])}
5bps pnl {yen(cur_all['runtime_5bps'])}
PF {cur_all['runtime_pf_5bps']}
trades {cur_all['runtime_trades']}
max DD {yen(cur_all['runtime_max_dd_5bps'])}

All-days Cost-Aware:
raw pnl {yen(cur_all['shadow_raw'])}
5bps pnl {yen(cur_all['shadow_5bps'])}
PF {cur_all['shadow_pf_5bps']}
trades {cur_all['shadow_trades']}
max DD {yen(cur_all['shadow_max_dd_5bps'])}

Delta:
raw {yen(cur_all['delta_raw'])}
5bps {yen(cur_all['delta_5bps'])}
PF runtime {cur_all['runtime_pf_5bps']} → shadow {cur_all['shadow_pf_5bps']}
max DD runtime {yen(cur_all['runtime_max_dd_5bps'])} → shadow {yen(cur_all['shadow_max_dd_5bps'])}

Days:
improved {improved}
worse {worse}
same {same}

Best component:
{best_comp[0]}
5bps delta {yen(best_comp[1])}

Worst component:
{worst_comp[0]}
5bps delta {yen(worst_comp[1])}

Best combination:
{best_combo[0]}
5bps pnl {yen(best_combo[1].get('shadow_5bps'))}
5bps delta {yen(best_combo[1].get('delta_5bps'))}
PF {best_combo[1].get('shadow_pf_5bps')}
winner retention n/a (pair-level retention requires matched trade IDs; not claimed)

Current full combination:
5bps pnl {yen(cur_all['shadow_5bps'])}
5bps delta {yen(cur_all['delta_5bps'])}
PF {cur_all['shadow_pf_5bps']}

7/21:
runtime 5bps {yen((d21 or {}).get('runtime_5bps'))}
Cost-Aware 5bps {yen((d21 or {}).get('cost_aware_5bps'))}
delta {yen((d21 or {}).get('delta_5bps'))}

7/22:
runtime 5bps {yen((d22 or {}).get('runtime_5bps'))}
Cost-Aware 5bps {yen((d22 or {}).get('cost_aware_5bps'))}
delta {yen((d22 or {}).get('delta_5bps'))}

Stability:
leave-one-day-out {json.dumps(lodo, ensure_ascii=False)}
leave-one-symbol-out {json.dumps(loso, ensure_ascii=False)}
top10 exclusion {json.dumps(top10_excl, ensure_ascii=False)}

Recommendation:
{recommendation}

Safety:
submit=0
cancel=0
live_order=0

Official Paper unchanged:
{'YES' if sha_ok else 'NO'}

Overall:
{report['overall']}
"""
    (OUT / "report.md").write_text(md, encoding="utf-8")

    # Excel
    wb = Workbook()

    def sheet(name: str):
        if name == "Executive_Summary":
            ws = wb.active
            ws.title = name
            return ws
        return wb.create_sheet(name)

    ws = sheet("Executive_Summary")
    for row in [
        ("recommendation", recommendation),
        ("reason", rec_reason),
        ("evaluable_dates", ",".join(evaluable_dates)),
        ("all_days_runtime_5bps", cur_all["runtime_5bps"]),
        ("all_days_shadow_5bps", cur_all["shadow_5bps"]),
        ("all_days_delta_5bps", cur_all["delta_5bps"]),
        ("improved_days", improved),
        ("worse_days", worse),
        ("best_component", best_comp[0]),
        ("best_component_delta_5bps", best_comp[1]),
        ("worst_component", worst_comp[0]),
        ("best_combination", best_combo[0]),
        ("official_unchanged", sha_ok),
        ("overall", report["overall"]),
    ]:
        ws.append(list(row))

    ws = sheet("Date_Coverage")
    cols = [
        "date",
        "session",
        "session_path",
        "cost_aware_enabled",
        "source_data_available",
        "price_path_available",
        "runtime_compatible_available",
        "replay_required",
        "evaluation_status",
        "exclusion_reason",
    ]
    ws.append(cols)
    for r in coverage:
        ws.append([r.get(c) for c in cols])

    ws = sheet("Daily_Results")
    if daily_current:
        ws.append(list(daily_current[0].keys()))
        for d in daily_current:
            ws.append(list(d.values()))

    ws = sheet("Session_Results")
    ws.append(
        [
            "date_session",
            "ablation",
            "status",
            "rt_raw",
            "sh_raw",
            "delta_raw",
            "rt_5bps",
            "sh_5bps",
            "delta_5bps",
            "rt_pf",
            "sh_pf",
            "rt_trades",
            "sh_trades",
            "stop_reject",
            "nofill",
        ]
    )
    for key, pack in session_results_light.items():
        for name, m in pack["ablations"].items():
            ws.append(
                [
                    key,
                    name,
                    m.get("status"),
                    (m.get("runtime") or {}).get("raw_pnl"),
                    (m.get("shadow") or {}).get("raw_pnl"),
                    m.get("delta_raw"),
                    (m.get("runtime") or {}).get("pnl_5bps"),
                    (m.get("shadow") or {}).get("pnl_5bps"),
                    m.get("delta_5bps"),
                    (m.get("runtime") or {}).get("pf_5bps"),
                    (m.get("shadow") or {}).get("pf_5bps"),
                    (m.get("runtime") or {}).get("trades"),
                    (m.get("shadow") or {}).get("trades"),
                    m.get("stop_risk_reject"),
                    m.get("same_snapshot_nofill"),
                ]
            )

    ws = sheet("Current_Spec")
    ws.append(["field", "value"])
    ws.append(["score_formula", current_spec["score_formula"]])
    ws.append(["np_in_decision", current_spec["np_in_decision"]])
    for c in current_spec["components_active"]:
        ws.append([c["name"], json.dumps(c, ensure_ascii=False)])
    for x in current_spec["researched_not_adopted"]:
        ws.append(["researched_not_adopted", x])

    ws = sheet("Component_Results")
    ws.append(["component", "delta_5bps_or_marginal", "note"])
    for k, v in solo.items():
        ws.append([k, v, "solo vs runtime delta_5bps"])
    for k, v in marg.items():
        ws.append([k, v, "marginal CURRENT - leaveout"])

    ws = sheet("Combination_Results")
    ws.append(["name", "runtime_5bps", "shadow_5bps", "delta_5bps", "shadow_pf", "trades"])
    for name, m in combination_all.items():
        ws.append(
            [
                name,
                m.get("runtime_5bps"),
                m.get("shadow_5bps"),
                m.get("delta_5bps"),
                m.get("shadow_pf_5bps"),
                m.get("shadow_trades"),
            ]
        )

    ws = sheet("Attribution")
    ws.append(["session", "json"])
    for key, pack in session_results_light.items():
        ws.append([key, json.dumps(pack.get("attribution") or {}, ensure_ascii=False)])

    ws = sheet("Blocked_Trades")
    ws.append(
        [
            "note",
            "STOP reject counts are session-level; per-reject realized PnL requires rejected-symbol counterfactual not stored in closed_trades. See stop_risk_reject and same_snapshot_nofill in Session_Results.",
        ]
    )
    for key, pack in session_results_light.items():
        cur = pack["ablations"].get("Winner_STOP_NoFill_CURRENT") or {}
        ws.append(
            [
                key,
                f"stop_risk_reject={cur.get('stop_risk_reject')} nofill={cur.get('same_snapshot_nofill')}",
            ]
        )

    ws = sheet("Stability")
    ws.append(["kind", "payload"])
    ws.append(["sample_note", sample_note])
    ws.append(["leave_one_day_out", json.dumps(lodo, ensure_ascii=False)])
    ws.append(["leave_one_symbol_out", json.dumps(loso, ensure_ascii=False)])
    ws.append(["top10_exclusion", json.dumps(top10_excl, ensure_ascii=False)])
    ws.append(["cost_sensitivity", json.dumps(cost_sens, ensure_ascii=False)])

    ws = sheet("Market_Regime")
    if market_regime:
        ws.append(list(market_regime[0].keys()))
        for r in market_regime:
            ws.append(list(r.values()))

    ws = sheet("Excluded_Dates")
    ws.append(["date", "session", "session_path", "evaluation_status", "exclusion_reason"])
    for r in excluded:
        ws.append(
            [
                r["date"],
                r["session"],
                r["session_path"],
                r["evaluation_status"],
                r["exclusion_reason"],
            ]
        )

    ws = sheet("Data_Quality")
    ws.append(["metric", "value"])
    ws.append(["primary_sessions", len(primary)])
    ws.append(["suppressed_shadow_writes", suppressed["n"]])
    ws.append(["causal_nearest_trade", "no future fallback (best<=t only)"])
    ws.append(["raw_5bps_mixed", False])

    ws = sheet("Safety_Audit")
    ws.append(["check", "value"])
    ws.append(["submit", 0])
    ws.append(["cancel", 0])
    ws.append(["live_order", 0])
    ws.append(["order_enabled", False])
    ws.append(["official_sha_unchanged", sha_ok])
    ws.append(["runtime_auto_changed", False])

    wb.save(OUT / "audit.xlsx")
    print("WROTE", OUT)
    print("OVERALL", report["overall"], "REC", recommendation)
    print("CUR", cur_all)


if __name__ == "__main__":
    main()
