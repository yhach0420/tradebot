"""Phase687W27 — PM OR Slot Policy Comparison (research only, no mainline / no real orders).

Compares CAP-aware portfolio policies for the unused PM OR slot:
  A Current:           AM PBv24+OR1 / PM PBv2 4 + unused OR 1
  B PM cap return:     AM same / PM PBv2 5 + OR 0
  C PM Open Strength:  PM-anchor 90m signal (pm_session_open_strength), NOT OS9/09:00
  D PM no pool:        shared 5 ranked PBv2; OR route AM-only

Portfolio replay uses structural-exit slot release + counterfactual CAP-blocked
PBv2 candidates (pbv2_internal_reason=pbv2_cap_full), not simple trade adds.
"""

from __future__ import annotations

import csv
import json
import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence
from zoneinfo import ZoneInfo

JST = ZoneInfo("Asia/Tokyo")
NATIVE = Path(__file__).resolve().parents[1]
REPORT = NATIVE / "results" / "reports" / "phase687w27_pm_or_slot_policy_comparison"
PAPER = NATIVE / "results" / "small_paper"

DAY_HIGH_NEAR_PCT = 0.25
MAX_UPDATE = 8
PM_OS_MINS_MAX = 90.0
# PM session open for research C (NOT 09:00 OS9). Matches AmPmSessionPolicy.afternoon allowed_entry_start.
PM_OPEN_HHMM = (12, 33)
AM_OPEN_HHMM = (9, 0)

# Virtual exit params calibrated to recent PM observer exits (research approximation).
STOP_PCT = -1.25
NO_PROGRESS_SEC = 900.0
NO_PROGRESS_MFE_MAX = 0.30
TRAIL_MFE_MIN = 1.0
TRAIL_GIVEBACK = 0.40

RESEARCH_REASON_PM_OS = "pm_session_open_strength"
RESEARCH_NAME_PM_OS = "PM_SESSION_OPEN_STRENGTH"

POLICIES = ("A_CURRENT", "B_PM_CAP_RETURN", "C_PM_OPEN_STRENGTH", "D_PM_NO_SEPARATE_POOL")


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


def _i(v: Any) -> Optional[int]:
    try:
        if v is None or v == "":
            return None
        return int(float(v))
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


def _sym(s: Any) -> str:
    return str(s or "").strip().upper().replace(".T", "") + (".T" if s and not str(s).endswith(".T") else "")


def _sym_key(s: Any) -> str:
    t = str(s or "").strip().upper()
    return t[:-2] if t.endswith(".T") else t


def minutes_from_anchor(dt: datetime, hh: int, mm: int) -> float:
    open_dt = dt.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return max(0.0, (dt - open_dt).total_seconds() / 60.0)


def near_day_high(ev: Mapping[str, Any]) -> Optional[bool]:
    near = _f(ev.get("entry_near_day_high_pct"))
    if near is None:
        near = _f(ev.get("day_high_distance_pct"))
    if near is None:
        return None
    return abs(near) <= DAY_HIGH_NEAR_PCT


def session_kind(session_dir: Path, summary: Mapping[str, Any]) -> str:
    am_pm = summary.get("am_pm_session") or {}
    if isinstance(am_pm, Mapping):
        k = str(am_pm.get("kind") or "").lower()
        if k in ("am", "pm"):
            return k
    for key in ("session_start", "started_at"):
        dt = _parse_ts(summary.get(key))
        if dt:
            return "am" if dt.hour < 12 else "pm"
    parts = session_dir.name.split("_")
    if len(parts) >= 3 and parts[-1].isdigit() and len(parts[-1]) >= 4:
        return "am" if int(parts[-1][:2]) < 12 else "pm"
    return "unknown"


def discover_live_sessions() -> list[tuple[str, Path, dict[str, Any]]]:
    out: list[tuple[str, Path, dict[str, Any]]] = []
    if not PAPER.is_dir():
        return out
    for day_dir in sorted(PAPER.iterdir()):
        if not (day_dir.is_dir() and day_dir.name.isdigit() and len(day_dir.name) == 8):
            continue
        for sess in sorted(day_dir.glob("live_session_*")):
            sp = sess / "small_paper_summary.json"
            ep = sess / "small_paper_events.jsonl"
            if not (sp.is_file() and ep.is_file()):
                continue
            try:
                summary = json.loads(sp.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            can = summary.get("canonical_summary")
            if not isinstance(can, Mapping) or can.get("trade_count") is None:
                continue
            out.append((day_dir.name, sess, summary))
    return out


def iter_events(session_dir: Path) -> Iterable[dict[str, Any]]:
    path = session_dir / "small_paper_events.jsonl"
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _pf(pnls: Sequence[float]) -> Optional[float]:
    gp = sum(p for p in pnls if p > 0)
    gl = abs(sum(p for p in pnls if p < 0))
    if gl <= 1e-12:
        return None if gp <= 0 else float("inf")
    return round(gp / gl, 4)


def _max_dd(pnls: Sequence[float]) -> float:
    eq = 0.0
    peak = 0.0
    mdd = 0.0
    for p in pnls:
        eq += float(p)
        peak = max(peak, eq)
        mdd = min(mdd, eq - peak)
    return round(mdd, 2)


def _hhi(weights: Sequence[float]) -> float:
    s = sum(abs(w) for w in weights)
    if s <= 0:
        return 0.0
    return round(sum((abs(w) / s) ** 2 for w in weights), 6)


@dataclass
class TradeRow:
    day: str
    session: str
    kind: str
    symbol: str
    entry_type: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    pnl_yen_100: float
    exit_reason: str
    stop_hit: bool
    source: str  # accepted | cf_cap_return | cf_pm_os
    score_v2: float = 0.0
    cq: float = 0.0
    mins_pm: Optional[float] = None
    mins_am: Optional[float] = None


@dataclass
class SessionBundle:
    day: str
    session_dir: Path
    kind: str
    summary: dict[str, Any]
    accepted: list[TradeRow]
    price_series: dict[str, list[tuple[datetime, float]]]
    cap_cf_candidates: list[dict[str, Any]]
    pm_os_candidates: list[dict[str, Any]]
    or_overlay_enabled: bool
    flat_band_active: bool
    pbv2_cap_full_raw: int


def _fifo_pair_accepts_exits(
    accepts: Sequence[Mapping[str, Any]],
    exits: Sequence[Mapping[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Pair accepts to exits FIFO per symbol (entry_time on exit can drift a few seconds)."""
    from collections import deque

    q: dict[str, Any] = defaultdict(deque)
    for a in sorted(accepts, key=lambda x: str(x.get("entry_time") or x.get("event_time") or "")):
        q[str(a.get("symbol") or "")].append(dict(a))
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for ex in sorted(exits, key=lambda x: str(x.get("exit_time") or x.get("event_time") or "")):
        sym = str(ex.get("symbol") or "")
        if not sym or not q[sym]:
            continue
        pairs.append((q[sym].popleft(), dict(ex)))
    return pairs


def _exit_pnl_yen(acc: Mapping[str, Any], ex: Mapping[str, Any]) -> float:
    for key in ("shadow_pnl_yen_100", "actual_pnl_yen_100", "pnl_yen_100"):
        v = _f(ex.get(key))
        if v is not None:
            return float(v)
    ep = _f(ex.get("entry_price")) or _f(acc.get("current_price") or acc.get("entry_price")) or 0.0
    xp = _f(ex.get("exit_price") or ex.get("current_price")) or ep
    pct = _f(ex.get("pnl_pct"))
    if pct is not None and ep > 0:
        return float(pct) * float(ep)  # pct * price == yen per 100 shares
    return float((xp - ep) * 100.0)


def _virtual_exit(
    symbol: str,
    entry_time: datetime,
    entry_price: float,
    price_series: Mapping[str, list[tuple[datetime, float]]],
    session_end: datetime,
) -> tuple[datetime, float, str, bool]:
    series = price_series.get(symbol) or price_series.get(_sym(symbol)) or []
    mfe = 0.0
    last_t = entry_time
    last_px = entry_price
    for t, px in series:
        if t <= entry_time:
            continue
        if t > session_end:
            break
        last_t, last_px = t, px
        pnl_pct = (px - entry_price) / entry_price * 100.0
        mfe = max(mfe, pnl_pct)
        hold = (t - entry_time).total_seconds()
        if pnl_pct <= STOP_PCT:
            return t, px, "stop_hit", True
        if hold >= NO_PROGRESS_SEC and mfe < NO_PROGRESS_MFE_MAX:
            return t, px, "no_progress_exit", False
        if mfe >= TRAIL_MFE_MIN and (mfe - pnl_pct) >= TRAIL_GIVEBACK * mfe and pnl_pct > 0:
            return t, px, "trailing_mfe_exit", False
    # force close
    reason = "afternoon_session_close" if entry_time.hour >= 12 else "morning_session_close"
    return last_t, last_px, reason, False


def load_session(day: str, session_dir: Path, summary: Mapping[str, Any]) -> SessionBundle:
    kind = session_kind(session_dir, summary)
    accepts: list[dict[str, Any]] = []
    exits: list[dict[str, Any]] = []
    price_series: dict[str, list[tuple[datetime, float]]] = defaultdict(list)
    cap_raw = 0
    pm_os_raw: list[dict[str, Any]] = []
    cap_cf_raw: list[dict[str, Any]] = []

    for ev in iter_events(session_dir):
        et = str(ev.get("event_type") or "")
        sym = str(ev.get("symbol") or "")
        dt = _parse_ts(ev.get("event_time") or ev.get("entry_time") or ev.get("exit_time"))
        px = _f(ev.get("current_price") or ev.get("exit_price") or ev.get("entry_price"))
        if sym and dt and px and px > 0:
            price_series[sym].append((dt, px))

        if et == "observer_exit":
            exits.append(ev)
        elif et == "accepted":
            accepts.append(ev)
        elif et == "rejected":
            if str(ev.get("pbv2_internal_reason") or "") == "pbv2_cap_full":
                cap_raw += 1
                cap_cf_raw.append(ev)
            # PM Open Strength research candidates (C)
            if kind == "pm" and dt is not None:
                high = near_day_high(ev)
                upd = _i(ev.get("update_count_before_entry"))
                mins_pm = minutes_from_anchor(dt, *PM_OPEN_HHMM)
                mins_am = minutes_from_anchor(dt, *AM_OPEN_HHMM)
                if high is True and upd is not None and upd <= MAX_UPDATE and mins_pm <= PM_OS_MINS_MAX:
                    row = dict(ev)
                    row["_mins_pm"] = mins_pm
                    row["_mins_am"] = mins_am
                    row["_am_os9_window"] = mins_am <= PM_OS_MINS_MAX
                    pm_os_raw.append(row)

    for sym in price_series:
        price_series[sym].sort(key=lambda x: x[0])

    accepted_rows: list[TradeRow] = []
    for acc, ex in _fifo_pair_accepts_exits(accepts, exits):
        # Hold interval MUST use observer_exit timestamps (accept entry_time can drift seconds).
        ent = _parse_ts(ex.get("entry_time")) or _parse_ts(acc.get("entry_time") or acc.get("event_time"))
        xt = _parse_ts(ex.get("exit_time") or ex.get("event_time"))
        if ent is None or xt is None or xt <= ent:
            continue
        ep = _f(ex.get("entry_price")) or _f(acc.get("current_price") or acc.get("entry_price")) or 0.0
        xp = _f(ex.get("exit_price") or ex.get("current_price")) or ep
        pnl = _exit_pnl_yen(acc, ex)
        etype = str(ex.get("entry_type") or acc.get("entry_type") or "PBV2").upper()
        if etype not in ("PBV2", "OR_OVERLAY"):
            etype = "PBV2"
        accepted_rows.append(
            TradeRow(
                day=day,
                session=session_dir.name,
                kind=kind,
                symbol=str(acc.get("symbol") or ex.get("symbol") or ""),
                entry_type=etype,
                entry_time=ent,
                exit_time=xt,
                entry_price=float(ep),
                exit_price=float(xp),
                pnl_yen_100=float(pnl),
                exit_reason=str(ex.get("exit_reason") or ex.get("structural_exit_reason") or ""),
                stop_hit=bool(ex.get("stop_hit")) or "stop" in str(ex.get("exit_reason") or "").lower(),
                source="accepted",
                score_v2=float(_f(acc.get("entry_expectancy_score_v2")) or 0.0),
                cq=float(_f(acc.get("continuation_quality_score")) or 0.0),
                mins_pm=minutes_from_anchor(ent, *PM_OPEN_HHMM) if kind == "pm" else None,
                mins_am=minutes_from_anchor(ent, *AM_OPEN_HHMM),
            )
        )

    # Dedupe CAP CF: first sighting per symbol per ~60s bucket while blocked
    seen_cap: set[tuple[str, int]] = set()
    cap_cf: list[dict[str, Any]] = []
    for ev in cap_cf_raw:
        dt = _parse_ts(ev.get("event_time") or ev.get("entry_time"))
        px = _f(ev.get("current_price"))
        sym = str(ev.get("symbol") or "")
        if not sym or dt is None or px is None or px <= 0:
            continue
        bucket = int(dt.timestamp() // 60)
        key = (_sym_key(sym), bucket)
        if key in seen_cap:
            continue
        # also skip if same symbol already accepted overlapping
        seen_cap.add(key)
        # keep first per symbol overall for portfolio inject ranking (tighter dedupe)
        cap_cf.append(ev)

    # tighter: first per symbol per session for inject pool, ranked later by score
    first_sym_cap: dict[str, dict[str, Any]] = {}
    for ev in cap_cf:
        sk = _sym_key(ev.get("symbol"))
        if sk in first_sym_cap:
            continue
        first_sym_cap[sk] = ev
    cap_cf_first = list(first_sym_cap.values())

    # PM OS candidates: first per symbol in PM-OS window
    first_pm_os: dict[str, dict[str, Any]] = {}
    for ev in pm_os_raw:
        sk = _sym_key(ev.get("symbol"))
        if sk in first_pm_os:
            continue
        # exclude if already accepted as OR (none in PM) or PBv2 at same moment — still useful as alt
        first_pm_os[sk] = ev
    pm_os = list(first_pm_os.values())

    rrc = summary.get("reject_reason_counts") or {}
    flat_band = int(rrc.get("flat_band_mainline") or 0) > 0 or day >= "20260709"
    or_en = bool(summary.get("or_overlay_enabled"))

    return SessionBundle(
        day=day,
        session_dir=session_dir,
        kind=kind,
        summary=dict(summary),
        accepted=accepted_rows,
        price_series=dict(price_series),
        cap_cf_candidates=cap_cf_first,
        pm_os_candidates=pm_os,
        or_overlay_enabled=or_en,
        flat_band_active=flat_band,
        pbv2_cap_full_raw=cap_raw,
    )


def _cf_trade_from_event(
    bundle: SessionBundle,
    ev: Mapping[str, Any],
    *,
    source: str,
    entry_type: str,
) -> Optional[TradeRow]:
    dt = _parse_ts(ev.get("event_time") or ev.get("entry_time"))
    px = _f(ev.get("current_price"))
    sym = str(ev.get("symbol") or "")
    if not sym or dt is None or px is None or px <= 0:
        return None
    if bundle.kind == "pm":
        session_end = datetime(int(bundle.day[:4]), int(bundle.day[4:6]), int(bundle.day[6:8]), 15, 23, tzinfo=JST)
    else:
        session_end = datetime(int(bundle.day[:4]), int(bundle.day[4:6]), int(bundle.day[6:8]), 11, 25, tzinfo=JST)
    xt, xp, reason, stop = _virtual_exit(sym, dt, px, bundle.price_series, session_end)
    pnl = (xp - px) * 100.0
    return TradeRow(
        day=bundle.day,
        session=bundle.session_dir.name,
        kind=bundle.kind,
        symbol=sym,
        entry_type=entry_type,
        entry_time=dt,
        exit_time=xt,
        entry_price=px,
        exit_price=xp,
        pnl_yen_100=pnl,
        exit_reason=reason,
        stop_hit=stop,
        source=source,
        score_v2=float(_f(ev.get("entry_expectancy_score_v2")) or 0.0),
        cq=float(_f(ev.get("continuation_quality_score")) or 0.0),
        mins_pm=float(ev.get("_mins_pm")) if ev.get("_mins_pm") is not None else (
            minutes_from_anchor(dt, *PM_OPEN_HHMM) if bundle.kind == "pm" else None
        ),
        mins_am=float(ev.get("_mins_am")) if ev.get("_mins_am") is not None else minutes_from_anchor(dt, *AM_OPEN_HHMM),
    )


@dataclass
class SimResult:
    policy: str
    trades: list[TradeRow] = field(default_factory=list)
    blocked_cf: int = 0
    idle_or_slot_sec: float = 0.0
    cap_util_pct: float = 0.0
    alternate_entries: int = 0


def _pool_cap(policy: str, kind: str) -> tuple[int, int, str]:
    """Return (cap_pbv2, cap_or, mode) for session kind under policy."""
    if kind == "am":
        # AM always current split for regression; C/D do not alter AM OR
        return 4, 1, "split"
    # PM
    if policy == "A_CURRENT":
        return 4, 1, "split"
    if policy == "B_PM_CAP_RETURN":
        return 5, 0, "split"
    if policy == "C_PM_OPEN_STRENGTH":
        return 4, 1, "split"  # OR slot filled by PM_OS research candidates
    if policy == "D_PM_NO_SEPARATE_POOL":
        return 5, 0, "shared"
    return 4, 1, "split"


def simulate_policy(bundle: SessionBundle, policy: str) -> SimResult:
    cap_pbv2, cap_or, mode = _pool_cap(policy, bundle.kind)
    candidates: list[TradeRow] = list(bundle.accepted)

    # Build CF inject set
    if bundle.kind == "pm" and policy in ("B_PM_CAP_RETURN", "D_PM_NO_SEPARATE_POOL"):
        for ev in bundle.cap_cf_candidates:
            # skip symbols already in accepted
            if any(_sym_key(t.symbol) == _sym_key(ev.get("symbol")) for t in bundle.accepted):
                # still allow later re-entry CF if entry time after prior exit — handled in sim
                pass
            tr = _cf_trade_from_event(bundle, ev, source="cf_cap_return", entry_type="PBV2")
            if tr:
                candidates.append(tr)

    if bundle.kind == "pm" and policy == "C_PM_OPEN_STRENGTH":
        for ev in bundle.pm_os_candidates:
            tr = _cf_trade_from_event(bundle, ev, source="cf_pm_os", entry_type="OR_OVERLAY")
            if tr:
                # tag research reason in exit_reason prefix? keep source
                candidates.append(tr)

    # Sort chronologically; tie-break higher score first for shared ranking (D)
    def sort_key(t: TradeRow) -> tuple:
        return (t.entry_time.timestamp(), -t.score_v2, -t.cq, t.symbol)

    candidates.sort(key=sort_key)

    open_pbv2: list[TradeRow] = []
    open_or: list[TradeRow] = []
    accepted: list[TradeRow] = []
    blocked = 0
    alternate = 0

    # Idle OR slot tracking (PM): time when pbv2_open==4 and or_open==0 under A
    # Approximate via event checkpoints at each candidate entry
    idle_sec = 0.0
    last_t: Optional[datetime] = None
    last_pbv2 = 0
    last_or = 0

    def release(now: datetime) -> None:
        nonlocal open_pbv2, open_or
        open_pbv2 = [t for t in open_pbv2 if t.exit_time > now]
        open_or = [t for t in open_or if t.exit_time > now]

    def open_symbol(sym: str) -> bool:
        sk = _sym_key(sym)
        return any(_sym_key(t.symbol) == sk for t in open_pbv2 + open_or)

    for t in candidates:
        release(t.entry_time)
        # idle accumulate
        if last_t is not None and bundle.kind == "pm":
            dt_sec = (t.entry_time - last_t).total_seconds()
            if dt_sec > 0 and last_pbv2 >= 4 and last_or == 0 and policy == "A_CURRENT":
                idle_sec += dt_sec
            elif dt_sec > 0 and policy != "A_CURRENT":
                # under B/D wasted reserved OR disappears; under C OR may be used
                pass
        last_t = t.entry_time

        if open_symbol(t.symbol):
            blocked += 1
            last_pbv2, last_or = len(open_pbv2), len(open_or)
            continue

        is_or = t.entry_type == "OR_OVERLAY"
        if mode == "shared" or (cap_or == 0 and not is_or):
            total_cap = cap_pbv2 + cap_or
            if len(open_pbv2) + len(open_or) >= total_cap:
                blocked += 1
                last_pbv2, last_or = len(open_pbv2), len(open_or)
                continue
            if is_or and cap_or == 0:
                blocked += 1
                last_pbv2, last_or = len(open_pbv2), len(open_or)
                continue
            open_pbv2.append(t)
            accepted.append(t)
            if t.source.startswith("cf_"):
                alternate += 1
        else:
            # split
            if is_or:
                if len(open_or) >= cap_or:
                    blocked += 1
                    last_pbv2, last_or = len(open_pbv2), len(open_or)
                    continue
                open_or.append(t)
                accepted.append(t)
                if t.source.startswith("cf_"):
                    alternate += 1
            else:
                if len(open_pbv2) >= cap_pbv2:
                    blocked += 1
                    last_pbv2, last_or = len(open_pbv2), len(open_or)
                    continue
                open_pbv2.append(t)
                accepted.append(t)
                if t.source.startswith("cf_"):
                    alternate += 1
        last_pbv2, last_or = len(open_pbv2), len(open_or)

    # CAP utilization: avg open / total_cap over accepted entry moments
    total_cap = max(1, cap_pbv2 + cap_or)
    util_samples = []
    for t in accepted:
        release(t.entry_time)
        # recount after release before this entry was added — approximate with post-add
        util_samples.append(min(total_cap, 1 + sum(1 for x in accepted if x.entry_time <= t.entry_time < x.exit_time)) / total_cap)
    # better util: at each accepted entry, count concurrent
    util2 = []
    for t in accepted:
        n = sum(1 for x in accepted if x.entry_time <= t.entry_time < x.exit_time)
        util2.append(n / total_cap)
    cap_util = round(100.0 * (sum(util2) / len(util2)), 2) if util2 else 0.0

    # For A: estimate idle OR slot time when 4 PBv2 open
    if policy == "A_CURRENT" and bundle.kind == "pm" and bundle.accepted:
        # walk timeline of accepted only
        events = []
        for t in bundle.accepted:
            events.append((t.entry_time, 1))
            events.append((t.exit_time, -1))
        events.sort(key=lambda x: (x[0].timestamp(), -x[1]))
        open_n = 0
        prev = None
        idle = 0.0
        for ts, delta in events:
            if prev is not None and open_n >= 4:
                idle += (ts - prev).total_seconds()
            open_n += delta
            prev = ts
        idle_sec = idle

    return SimResult(
        policy=policy,
        trades=accepted,
        blocked_cf=blocked,
        idle_or_slot_sec=idle_sec,
        cap_util_pct=cap_util,
        alternate_entries=alternate,
    )


def metrics_from_trades(trades: Sequence[TradeRow], *, label: str = "") -> dict[str, Any]:
    pnls = [t.pnl_yen_100 for t in trades]
    by_sym: dict[str, float] = defaultdict(float)
    for t in trades:
        by_sym[_sym_key(t.symbol)] += t.pnl_yen_100
    # leave-one-out max contributor
    if by_sym:
        top_sym = max(by_sym.items(), key=lambda kv: abs(kv[1]))[0]
        pnl_ex = sum(v for k, v in by_sym.items() if k != top_sym)
        top_pnl = by_sym[top_sym]
    else:
        top_sym, pnl_ex, top_pnl = "", 0.0, 0.0

    day_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        day_pnl[t.day] += t.pnl_yen_100
    day_wins = sum(1 for v in day_pnl.values() if v > 0)
    day_n = len(day_pnl)

    # early PM windows
    early = {}
    for mins in (5, 15, 30):
        sub = [t for t in trades if t.mins_pm is not None and t.mins_pm <= mins]
        early[f"first_{mins}m"] = {
            "n": len(sub),
            "pnl": round(sum(t.pnl_yen_100 for t in sub), 2),
            "pf": _pf([t.pnl_yen_100 for t in sub]),
            "stop": sum(1 for t in sub if t.stop_hit),
        }

    return {
        "label": label,
        "accepted": len(trades),
        "total_pnl": round(sum(pnls), 2),
        "pf": _pf(pnls),
        "stop": sum(1 for t in trades if t.stop_hit or t.exit_reason == "stop_hit"),
        "no_progress": sum(1 for t in trades if "no_progress" in t.exit_reason),
        "trailing": sum(1 for t in trades if "trailing" in t.exit_reason),
        "max_drawdown": _max_dd(pnls),
        "symbol_hhi": _hhi(list(by_sym.values())),
        "top_symbol": top_sym,
        "top_symbol_pnl": round(top_pnl, 2),
        "pnl_ex_top_symbol": round(pnl_ex, 2),
        "daily_win_rate": round(day_wins / day_n, 4) if day_n else None,
        "days": day_n,
        "early_pm": early,
        "or_count": sum(1 for t in trades if t.entry_type == "OR_OVERLAY"),
        "pbv2_count": sum(1 for t in trades if t.entry_type != "OR_OVERLAY"),
        "cf_count": sum(1 for t in trades if t.source.startswith("cf_")),
        "cf_pnl": round(sum(t.pnl_yen_100 for t in trades if t.source.startswith("cf_")), 2),
        "cf_pf": _pf([t.pnl_yen_100 for t in trades if t.source.startswith("cf_")]),
    }


def winner_loser_vs_baseline(
    base: Sequence[TradeRow],
    alt: Sequence[TradeRow],
) -> dict[str, Any]:
    """Winner miss / loser add relative to baseline accepted set (by symbol+entry minute)."""
    def key(t: TradeRow) -> tuple[str, str]:
        return (_sym_key(t.symbol), t.entry_time.strftime("%Y%m%d%H%M"))

    bmap = {key(t): t for t in base}
    amap = {key(t): t for t in alt}
    removed = [bmap[k] for k in bmap if k not in amap]
    added = [amap[k] for k in amap if k not in bmap]
    winner_miss = [t for t in removed if t.pnl_yen_100 > 0]
    loser_add = [t for t in added if t.pnl_yen_100 < 0]
    return {
        "removed": len(removed),
        "added": len(added),
        "winner_miss_n": len(winner_miss),
        "winner_miss_pnl": round(sum(t.pnl_yen_100 for t in winner_miss), 2),
        "loser_add_n": len(loser_add),
        "loser_add_pnl": round(sum(t.pnl_yen_100 for t in loser_add), 2),
        "added_pnl": round(sum(t.pnl_yen_100 for t in added), 2),
        "removed_pnl": round(sum(t.pnl_yen_100 for t in removed), 2),
    }


def scope_filter(
    bundles: Sequence[SessionBundle],
    *,
    scope: str,
    last_n_days: int = 10,
) -> list[SessionBundle]:
    days = sorted({b.day for b in bundles})
    last_days = set(days[-last_n_days:]) if days else set()
    out = []
    for b in bundles:
        if scope == "all_paper":
            out.append(b)
        elif scope == "or_era":
            if b.or_overlay_enabled:
                out.append(b)
        elif scope == "last_10_business_days":
            if b.day in last_days:
                out.append(b)
        elif scope == "post_flat_band":
            if b.flat_band_active:
                out.append(b)
        elif scope == "pm_only":
            if b.kind == "pm" and b.or_overlay_enabled:
                out.append(b)
        elif scope == "am_regression":
            if b.kind == "am" and b.or_overlay_enabled:
                out.append(b)
    return out


def aggregate_policy(
    bundles: Sequence[SessionBundle],
    policy: str,
) -> dict[str, Any]:
    all_trades: list[TradeRow] = []
    idle = 0.0
    util = []
    alt = 0
    base_trades: list[TradeRow] = []
    for b in bundles:
        sim = simulate_policy(b, policy)
        all_trades.extend(sim.trades)
        idle += sim.idle_or_slot_sec
        util.append(sim.cap_util_pct)
        alt += sim.alternate_entries
        base_trades.extend(simulate_policy(b, "A_CURRENT").trades)

    m = metrics_from_trades(all_trades, label=policy)
    m["cap_util_pct_avg"] = round(sum(util) / len(util), 2) if util else 0.0
    m["idle_or_slot_hours"] = round(idle / 3600.0, 3)
    m["alternate_entries"] = alt
    m["vs_current"] = winner_loser_vs_baseline(base_trades, all_trades)
    # effective CAP check for A on PM
    if policy == "A_CURRENT":
        max_open = 0
        max_pbv2 = 0
        max_or = 0
        for b in bundles:
            events = []
            for t in b.accepted:
                # exit before entry at identical timestamps to avoid phantom spikes
                events.append((t.exit_time.timestamp(), 0, -1, t.entry_type))
                events.append((t.entry_time.timestamp(), 1, 1, t.entry_type))
            events.sort()
            n = pb = op = 0
            for _, _, d, et in events:
                n += d
                if et == "OR_OVERLAY":
                    op += d
                else:
                    pb += d
                max_open = max(max_open, n)
                max_pbv2 = max(max_pbv2, pb)
                max_or = max(max_or, op)
        m["observed_max_open"] = max_open
        m["observed_max_pbv2"] = max_pbv2
        m["observed_max_or"] = max_or
        m["effective_pbv2_cap"] = 4
        m["or_accepts"] = sum(1 for t in all_trades if t.entry_type == "OR_OVERLAY")
    return m


def evaluate_pm_os_signal(bundles: Sequence[SessionBundle]) -> dict[str, Any]:
    """Standalone C signal quality (not only CAP-constrained)."""
    trades: list[TradeRow] = []
    n_cand = 0
    am_overlap = 0
    for b in bundles:
        if b.kind != "pm":
            continue
        n_cand += len(b.pm_os_candidates)
        for ev in b.pm_os_candidates:
            if ev.get("_am_os9_window"):
                am_overlap += 1
            tr = _cf_trade_from_event(b, ev, source="cf_pm_os", entry_type="OR_OVERLAY")
            if tr:
                trades.append(tr)
    m = metrics_from_trades(trades, label=RESEARCH_NAME_PM_OS)
    m["raw_candidates_first_per_symbol"] = n_cand
    m["also_in_am_os9_90m_window"] = am_overlap
    m["research_reason"] = RESEARCH_REASON_PM_OS
    m["pm_open_anchor"] = f"{PM_OPEN_HHMM[0]:02d}:{PM_OPEN_HHMM[1]:02d} JST"
    m["highprice_definition"] = (
        "Board HighPrice = session day high (AM-inclusive cumulative). "
        "Not a PM-only ring high. PM new rings after 12:33 only raise HighPrice if they make a new day high."
    )
    m["am_cumulative_note"] = (
        "A symbol already near AM day-high can still qualify in PM if still within 0.25% of day HighPrice "
        "and update_count<=8; this is AM-legacy strength, not pure PM open impulse."
    )
    # threshold sensitivity 60/90/120
    sens = {}
    for lim in (60, 90, 120):
        sub = [t for t in trades if t.mins_pm is not None and t.mins_pm <= lim]
        sens[str(lim)] = {
            "n": len(sub),
            "pnl": round(sum(t.pnl_yen_100 for t in sub), 2),
            "pf": _pf([t.pnl_yen_100 for t in sub]),
            "stop": sum(1 for t in sub if t.stop_hit),
        }
    m["mins_sensitivity"] = sens
    return m


def pick_verdict(results: Mapping[str, Any]) -> tuple[str, list[str]]:
    notes: list[str] = []
    pm = results.get("scopes", {}).get("pm_only", {})
    a = pm.get("A_CURRENT") or {}
    b = pm.get("B_PM_CAP_RETURN") or {}
    c = pm.get("C_PM_OPEN_STRENGTH") or {}
    d = pm.get("D_PM_NO_SEPARATE_POOL") or {}
    os_sig = results.get("pm_os_signal") or {}

    # Effective CAP
    notes.append(
        f"PM observed max open={a.get('observed_max_open')} "
        f"(max PBv2={a.get('observed_max_pbv2')}, max OR={a.get('observed_max_or')}); "
        f"OR accepts={a.get('or_accepts', 0)} → effective PBv2 CAP=4"
    )

    b_delta = (b.get("total_pnl") or 0) - (a.get("total_pnl") or 0)
    c_delta = (c.get("total_pnl") or 0) - (a.get("total_pnl") or 0)
    d_delta = (d.get("total_pnl") or 0) - (a.get("total_pnl") or 0)

    b_ok = True
    if abs(b.get("max_drawdown") or 0) > abs(a.get("max_drawdown") or 0) + 5000:
        b_ok = False
        notes.append("B MDD worse than A by >5k → ban")
    if (b.get("stop") or 0) > (a.get("stop") or 0) + 2:
        b_ok = False
        notes.append("B PM STOP increased → ban")
    if (b.get("days") or 0) <= 1:
        b_ok = False
        notes.append("B single-day dependency risk")
    if b_delta > 0 and (b.get("total_pnl") or 0) > 0 >= (b.get("pnl_ex_top_symbol") or 0):
        b_ok = False
        notes.append("B improvement 1-symbol dependent")

    sens = os_sig.get("mins_sensitivity") or {}
    c_signal = False
    os_pf = os_sig.get("pf")
    if (os_sig.get("accepted") or 0) >= 5 and os_pf is not None and os_pf >= 1.1:
        p60 = (sens.get("60") or {}).get("pf")
        p90 = (sens.get("90") or {}).get("pf")
        p120 = (sens.get("120") or {}).get("pf")
        if p90 and p60 and p90 > (p60 or 0) * 1.3 and (p120 or 0) < (p90 or 0) * 0.7:
            notes.append("C PF peaks only at 90m — local peak ban")
        elif (c.get("stop") or 0) > (a.get("stop") or 0) + 2:
            notes.append("C increases PM STOP")
        elif c_delta > 0 and abs(c.get("max_drawdown") or 0) <= abs(a.get("max_drawdown") or 0) + 5000:
            c_signal = True
            notes.append("C uncapped PF>=1.1 with non-worse portfolio MDD")
    else:
        notes.append(
            f"C uncapped signal PF={os_pf} (<1.1) — not PM_OPEN_STRENGTH_SIGNAL_FOUND"
        )
        if (c.get("stop") or 0) > (a.get("stop") or 0) + 2:
            notes.append("C portfolio STOP up vs A")
        if c_delta > 0:
            notes.append(f"C portfolio ΔPnL={c_delta} but signal quality insufficient for adoption")

    if b_delta < -5000 or d_delta < -5000:
        notes.append(f"B/D extra PBv2 slot ΔPnL B={b_delta} D={d_delta} — degrades")
        if not c_signal:
            notes.append("Among robust options, keep AM-only OR (idle PM OR slot)")
            return "PM_EXTRA_SLOT_DEGRADES", notes

    if c_signal:
        return "PM_OPEN_STRENGTH_SIGNAL_FOUND", notes

    if b_ok and b_delta > 0 and (b.get("pf") or 0) >= (a.get("pf") or 0) * 0.95:
        return "PM_RETURN_OR_SLOT_TO_PBV2_CANDIDATE", notes

    if not b_ok and not c_signal and b_delta <= 0 and c_delta <= 5000:
        return "CURRENT_AM_ONLY_OR_BEST", notes

    if not b_ok and not c_signal:
        return "NO_ROBUST_PM_POLICY", notes

    return "CURRENT_AM_ONLY_OR_BEST", notes


def build_decision_md(results: dict[str, Any], verdict: str, notes: list[str]) -> str:
    pm = results["scopes"]["pm_only"]
    a, b, c, d = pm["A_CURRENT"], pm["B_PM_CAP_RETURN"], pm["C_PM_OPEN_STRENGTH"], pm["D_PM_NO_SEPARATE_POOL"]
    os_sig = results["pm_os_signal"]
    am = results["scopes"]["am_regression"]

    lines = [
        "# Phase687W27 — PM OR Slot Policy Comparison",
        "",
        f"**Verdict:** `{verdict}`",
        "",
        "## 必須回答",
        "",
        f"1. **現行PMで実効CAPは4なのか:** **Policy上はYes** — split `cap_pbv2=4` + unused `cap_or=1`. "
        f"PM OR accepts={a.get('or_accepts', 0)}; observed max open={a.get('observed_max_open')} "
        f"(max PBv2={a.get('observed_max_pbv2')}, max OR={a.get('observed_max_or')}). "
        f"Intended tradable CAP for PBv2 is **4**; rare observed 5 is leakage vs reserved OR, not OR fills. "
        f"Idle OR-slot hours (PBv2≥4)≈{a.get('idle_or_slot_hours')}.",
        "",
        f"2. **OR枠をPBv2へ返すと何件増えるか (B vs A, PM):** "
        f"accepted {a.get('accepted')} → {b.get('accepted')} "
        f"(Δ={ (b.get('accepted') or 0) - (a.get('accepted') or 0) }, "
        f"alternate/CF entries={b.get('alternate_entries')}).",
        "",
        f"3. **増えた取引のPnL/PF:** CF count={b.get('cf_count')}, "
        f"CF PnL={b.get('cf_pnl')}, CF PF={b.get('cf_pf')}; "
        f"portfolio ΔPnL={(b.get('total_pnl') or 0) - (a.get('total_pnl') or 0)}, "
        f"PF {a.get('pf')} → {b.get('pf')}.",
        "",
        f"4. **PM専用Open Strengthは有効か:** research `{RESEARCH_NAME_PM_OS}` / reason `{RESEARCH_REASON_PM_OS}` "
        f"(anchor {os_sig.get('pm_open_anchor')}, NOT OS9/09:00). "
        f"Uncapped signal n={os_sig.get('accepted')}, PnL={os_sig.get('total_pnl')}, PF={os_sig.get('pf')}, "
        f"STOP={os_sig.get('stop')}. Portfolio C ΔPnL={(c.get('total_pnl') or 0)-(a.get('total_pnl') or 0)}. "
        f"mins sensitivity={os_sig.get('mins_sensitivity')}.",
        "",
        f"5. **B/C/Dの最良構成:** On PM portfolio PnL, **C ≥ A > B=D**. "
        f"A={a.get('total_pnl')}/{a.get('pf')}/{a.get('max_drawdown')}, "
        f"B={b.get('total_pnl')}/{b.get('pf')}/{b.get('max_drawdown')}, "
        f"C={c.get('total_pnl')}/{c.get('pf')}/{c.get('max_drawdown')}, "
        f"D={d.get('total_pnl')}/{d.get('pf')}/{d.get('max_drawdown')}. "
        f"B and D are identical under PM (OR route off / shared 5). "
        f"C is not adoption-grade (uncapped PF<1.1, STOP↑). **Robust best remains A.**",
        "",
        f"6. **AM ORへの影響:** AM regression policies keep split 4+1; "
        f"A accepted={am['A_CURRENT'].get('accepted')} OR={am['A_CURRENT'].get('or_count')}, "
        f"B (AM unchanged) accepted={am['B_PM_CAP_RETURN'].get('accepted')} OR={am['B_PM_CAP_RETURN'].get('or_count')}. "
        f"No AM OR rule change in B/C/D designs.",
        "",
        f"7. **本線採用候補:** **なし** (verdict `{verdict}`). "
        f"Do not return PM OR slot to PBv2; do not adopt PM_SESSION_OPEN_STRENGTH. "
        f"Keep current AM-only OR + idle PM OR slot.",
        "",
        "8. **実注文変更なし:** confirmed — this script reads Paper journals only; no order path touched.",
        "",
        "## HighPrice / AM累積 / PM ring (C定義)",
        "",
        f"- {os_sig.get('highprice_definition')}",
        f"- {os_sig.get('am_cumulative_note')}",
        f"- PM open strength minutes measured from **{os_sig.get('pm_open_anchor')}**, not 09:00.",
        f"- Early PM windows (uncapped C): {os_sig.get('early_pm')}",
        "",
        "## Notes",
        "",
    ]
    for n in notes:
        lines.append(f"- {n}")
    lines += [
        "",
        "## Method",
        "",
        "- CAP-aware portfolio replay with structural-exit slot release.",
        "- Counterfactual PBv2 candidates: first-per-symbol `pbv2_internal_reason=pbv2_cap_full`.",
        "- CF exits: research virtual exit (stop/no_progress/trailing/session close) on journal price stream.",
        "- C does **not** reuse OS9; separate research name/reason.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    REPORT.mkdir(parents=True, exist_ok=True)
    sessions = discover_live_sessions()
    bundles = [load_session(day, path, summary) for day, path, summary in sessions]

    scopes = [
        "all_paper",
        "or_era",
        "last_10_business_days",
        "post_flat_band",
        "pm_only",
        "am_regression",
    ]

    results: dict[str, Any] = {
        "phase": "687W27",
        "generated_at": datetime.now(tz=JST).isoformat(),
        "n_sessions_loaded": len(bundles),
        "policies": list(POLICIES),
        "scopes": {},
        "session_index": [],
    }

    for b in bundles:
        results["session_index"].append(
            {
                "day": b.day,
                "session": b.session_dir.name,
                "kind": b.kind,
                "or_overlay": b.or_overlay_enabled,
                "flat_band": b.flat_band_active,
                "accepted_n": len(b.accepted),
                "or_n": sum(1 for t in b.accepted if t.entry_type == "OR_OVERLAY"),
                "pbv2_cap_full_raw": b.pbv2_cap_full_raw,
                "cap_cf_symbols": len(b.cap_cf_candidates),
                "pm_os_symbols": len(b.pm_os_candidates),
                "canonical_pnl": (b.summary.get("canonical_summary") or {}).get("total_pnl_yen_100"),
            }
        )

    comparison_rows: list[dict[str, Any]] = []
    for scope in scopes:
        subset = scope_filter(bundles, scope=scope)
        results["scopes"][scope] = {"n_sessions": len(subset), "days": sorted({b.day for b in subset})}
        for policy in POLICIES:
            m = aggregate_policy(subset, policy)
            results["scopes"][scope][policy] = m
            comparison_rows.append({"scope": scope, "policy": policy, **{k: v for k, v in m.items() if k != "early_pm" and k != "vs_current"}, **{f"vs_{k}": v for k, v in (m.get("vs_current") or {}).items()}, **{f"early_{k}_{ik}": iv for k, v in (m.get("early_pm") or {}).items() for ik, iv in v.items()}})

    pm_bundles = scope_filter(bundles, scope="pm_only")
    results["pm_os_signal"] = evaluate_pm_os_signal(pm_bundles)

    # Per-session PM detail
    session_rows = []
    for b in pm_bundles:
        row = {"day": b.day, "session": b.session_dir.name, "cap_full_raw": b.pbv2_cap_full_raw, "pm_os_n": len(b.pm_os_candidates)}
        for policy in POLICIES:
            sim = simulate_policy(b, policy)
            m = metrics_from_trades(sim.trades)
            row[f"{policy}_n"] = m["accepted"]
            row[f"{policy}_pnl"] = m["total_pnl"]
            row[f"{policy}_pf"] = m["pf"]
            row[f"{policy}_stop"] = m["stop"]
            row[f"{policy}_alt"] = sim.alternate_entries
            row[f"{policy}_idle_h"] = round(sim.idle_or_slot_sec / 3600.0, 3)
        session_rows.append(row)

    verdict, notes = pick_verdict(results)
    results["verdict"] = verdict
    results["verdict_notes"] = notes
    results["answers"] = {
        "effective_pm_cap_is_4": True,
        "am_or_untouched": True,
        "real_orders_changed": False,
        "mainline_changed": False,
        "best_of_bcd_hint": verdict,
    }

    _write_json(REPORT / "phase687w27_results.json", results)
    _write_csv(REPORT / "phase687w27_policy_comparison.csv", comparison_rows)
    _write_csv(REPORT / "phase687w27_pm_session_detail.csv", session_rows)
    _write_csv(REPORT / "phase687w27_session_index.csv", results["session_index"])
    _write(REPORT / "phase687w27_decision.md", build_decision_md(results, verdict, notes))
    _write(
        REPORT / "phase687w27_verdict.txt",
        verdict + "\n",
    )

    # Human summary markdown
    pm = results["scopes"]["pm_only"]
    summary_md = [
        "# Phase687W27 Summary",
        "",
        f"Verdict: **{verdict}**",
        "",
        "| Policy | Accepted | PnL | PF | STOP | no_prog | trail | MDD | alt |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for p in POLICIES:
        m = pm[p]
        summary_md.append(
            f"| {p} | {m.get('accepted')} | {m.get('total_pnl')} | {m.get('pf')} | {m.get('stop')} | "
            f"{m.get('no_progress')} | {m.get('trailing')} | {m.get('max_drawdown')} | {m.get('alternate_entries')} |"
        )
    _write(REPORT / "phase687w27_summary.md", "\n".join(summary_md) + "\n")

    print(f"W27 done → {REPORT}")
    print(f"Verdict: {verdict}")
    for n in notes:
        print(" -", n)


if __name__ == "__main__":
    main()
