#!/usr/bin/env python3
"""Phase687W47 research helper: official-ENTRY outcome panel from small_paper live sessions.

NOT a final deliverable. Writes temp parquet under pre_entry_market_state/_w47_tmp/.
Does not modify Runtime YAML / PBv2.
"""

from __future__ import annotations

import json
import math
import re
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import pandas as pd

NATIVE = Path(__file__).resolve().parents[1]
PAPER = NATIVE / "results" / "small_paper"
OUT_DIR = NATIVE / "results" / "research" / "pre_entry_market_state" / "_w47_tmp"
OUT_PQ = OUT_DIR / "entry_panel.parquet"
JST = ZoneInfo("Asia/Tokyo")
MAX_WORKERS = 4
SHORT_HOLD_SEC = 180.0
NEAREST_MATCH_SEC = 600.0

STOP_REASONS = frozenset({"hard_stop", "stop_loss", "loss_cut", "stop_hit", "stop"})


def _num(x: Any) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _parse_ts(val: Any) -> Optional[datetime]:
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return val if val.tzinfo else val.replace(tzinfo=JST)
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def _truthy(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    if x is None:
        return False
    s = str(x).strip().lower()
    return s in ("1", "true", "yes", "y", "t")


def _is_stop_reason(reason: str) -> bool:
    r = str(reason or "").strip().lower()
    if not r:
        return False
    if r in STOP_REASONS:
        return True
    return "stop" in r and "session" not in r


def iter_session_dirs() -> list[tuple[str, Path]]:
    out: list[tuple[str, Path]] = []
    if not PAPER.is_dir():
        return out
    for day_dir in sorted(PAPER.iterdir()):
        if not day_dir.is_dir():
            continue
        day = day_dir.name
        if not re.fullmatch(r"\d{8}", day):
            continue
        if day.startswith("2099"):
            continue
        for sess in sorted(day_dir.glob("live_session_*")):
            if not sess.is_dir():
                continue
            if "abort" in sess.name.lower():
                continue
            ev = sess / "small_paper_events.jsonl"
            if not ev.is_file():
                continue
            out.append((day, sess))
    return out


def detect_session_kind(sess: Path, entry_dt: Optional[datetime]) -> str:
    if (sess / "small_paper_summary_am.json").is_file() and not (
        sess / "small_paper_summary_pm.json"
    ).is_file():
        return "am"
    if (sess / "small_paper_summary_pm.json").is_file() and not (
        sess / "small_paper_summary_am.json"
    ).is_file():
        return "pm"
    # summary.json session hints
    for name in ("small_paper_summary.json", "small_paper_summary_am.json", "small_paper_summary_pm.json"):
        p = sess / name
        if not p.is_file():
            continue
        try:
            o = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for key in ("session", "session_kind", "session_label", "period"):
            v = str(o.get(key) or "").strip().lower()
            if v in ("am", "morning", "午前"):
                return "am"
            if v in ("pm", "afternoon", "午後"):
                return "pm"
    if entry_dt is not None:
        # JST cash session: AM open~11:30, PM 12:30~close
        hm = entry_dt.hour * 100 + entry_dt.minute
        if hm < 1200:
            return "am"
        return "pm"
    # fallback from session folder start HHMMSS
    m = re.search(r"live_session_(\d{6})", sess.name)
    if m:
        hhmmss = int(m.group(1))
        if hhmmss < 120000:
            return "am"
        return "pm"
    return "unknown"


def load_events(sess: Path) -> list[dict[str, Any]]:
    path = sess / "small_paper_events.jsonl"
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def _entry_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in events:
        et = str(e.get("event_type") or "")
        if et == "accepted":
            if _truthy(e.get("accept_aborted")):
                continue
            out.append(e)
        elif et == "official_entry" or _truthy(e.get("official_entry")):
            out.append(e)
    return out


def _exit_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for e in events:
        if str(e.get("event_type") or "") != "observer_exit":
            continue
        if e.get("notification_only") or e.get("debug_row") or e.get("skipped"):
            continue
        if e.get("shadow_only") or e.get("is_shadow_trade"):
            continue
        reason = str(e.get("exit_reason") or "").lower()
        if "virtual_hold" in reason:
            continue
        out.append(e)
    return out


def _pair_key(row: dict[str, Any]) -> Optional[str]:
    did = row.get("decision_id")
    sym = str(row.get("symbol") or "").strip()
    if did is not None and str(did).strip() and sym:
        return f"{sym}|{did}"
    return None


def pair_entries_exits(
    accepts: list[dict[str, Any]], exits: list[dict[str, Any]]
) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    """Return (accept, exit, match_method) pairs."""
    accepts_s = sorted(
        accepts, key=lambda x: _parse_ts(x.get("entry_time") or x.get("event_time")) or datetime.min.replace(tzinfo=JST)
    )
    exits_s = sorted(
        exits, key=lambda x: _parse_ts(x.get("exit_time") or x.get("event_time")) or datetime.min.replace(tzinfo=JST)
    )

    used_ex: set[int] = set()
    pairs: list[tuple[dict[str, Any], dict[str, Any], str]] = []

    # 1) decision_id exact
    ex_by_did: dict[str, list[int]] = {}
    for i, ex in enumerate(exits_s):
        k = _pair_key(ex)
        if k:
            ex_by_did.setdefault(k, []).append(i)
    for acc in accepts_s:
        k = _pair_key(acc)
        if not k:
            continue
        cand = ex_by_did.get(k) or []
        for i in cand:
            if i in used_ex:
                continue
            used_ex.add(i)
            pairs.append((acc, exits_s[i], "decision_id"))
            break

    paired_acc = {id(a) for a, _, m in pairs if m == "decision_id"}
    remaining_acc = [a for a in accepts_s if id(a) not in paired_acc]

    # 2) nearest symbol match by entry_time proximity
    for acc in remaining_acc:
        sym = str(acc.get("symbol") or "")
        a_et = _parse_ts(acc.get("entry_time") or acc.get("event_time"))
        best_i = None
        best_dt = 1e18
        for i, ex in enumerate(exits_s):
            if i in used_ex:
                continue
            if str(ex.get("symbol") or "") != sym:
                continue
            e_et = _parse_ts(ex.get("entry_time") or ex.get("event_time"))
            if a_et is None or e_et is None:
                dt = 0.0 if a_et is None and e_et is None else 1e9
            else:
                dt = abs((a_et - e_et).total_seconds())
            if dt < best_dt:
                best_dt = dt
                best_i = i
        if best_i is not None and best_dt <= NEAREST_MATCH_SEC:
            used_ex.add(best_i)
            pairs.append((acc, exits_s[best_i], "nearest_symbol"))

    # 3) leftover exits without accept: synthesize accept-from-exit (official exit-only)
    for i, ex in enumerate(exits_s):
        if i in used_ex:
            continue
        # require prices
        if _num(ex.get("entry_price")) is None or _num(ex.get("exit_price")) is None:
            continue
        synth = {
            "event_type": "accepted",
            "symbol": ex.get("symbol"),
            "entry_time": ex.get("entry_time"),
            "event_time": ex.get("entry_time") or ex.get("event_time"),
            "current_price": ex.get("entry_price"),
            "decision_id": ex.get("decision_id"),
            "entry_expectancy_score_v2": ex.get("entry_expectancy_score_v2"),
            "entry_momentum_score": ex.get("entry_momentum_score"),
            "entry_momentum_continuation_score": ex.get("entry_momentum_continuation_score"),
            "momentum_continuation_score": ex.get("momentum_continuation_score"),
            "entry_order_book_imbalance": ex.get("entry_order_book_imbalance"),
            "spread_bps": ex.get("spread_bps"),
        }
        pairs.append((synth, ex, "exit_only"))
        used_ex.add(i)

    return pairs


def label_trade(
    *,
    pnl_pct: Optional[float],
    mfe: Optional[float],
    mae: Optional[float],
    stop_hit: bool,
    exit_reason: str,
    no_progress_exit: bool,
    hold_sec: Optional[float],
) -> dict[str, Any]:
    ret = pnl_pct
    winner_a = bool(mfe is not None and mfe >= 1.0 and ret is not None and ret >= 0.5)
    winner_b = bool(ret is not None and ret > 0.0)
    stop = bool(stop_hit or _is_stop_reason(exit_reason) or (mae is not None and mae <= -1.2))
    short_hold = hold_sec is not None and hold_sec <= SHORT_HOLD_SEC
    np_proxy = bool(
        short_hold
        and mfe is not None
        and mfe < 0.3
        and ret is not None
        and abs(ret) < 0.2
    )
    no_progress = bool(no_progress_exit or np_proxy)

    # primary label priority for convenience
    if stop:
        primary = "STOP"
    elif no_progress:
        primary = "NO_PROGRESS"
    elif winner_a:
        primary = "WINNER_A"
    elif winner_b:
        primary = "WINNER_B"
    else:
        primary = "OTHER"

    return {
        "label_winner_a": winner_a,
        "label_winner_b": winner_b,
        "label_stop": stop,
        "label_no_progress": no_progress,
        "label_primary": primary,
        "np_proxy_short_hold": np_proxy and not no_progress_exit,
    }


def process_session(day: str, sess_path: str) -> list[dict[str, Any]]:
    sess = Path(sess_path)
    events = load_events(sess)
    accepts = _entry_events(events)
    exits = _exit_events(events)
    if not accepts and not exits:
        return []
    pairs = pair_entries_exits(accepts, exits)
    rows: list[dict[str, Any]] = []
    for idx, (acc, ex, method) in enumerate(pairs):
        entry_dt = _parse_ts(ex.get("entry_time") or acc.get("entry_time") or acc.get("event_time"))
        exit_dt = _parse_ts(ex.get("exit_time") or ex.get("event_time"))
        entry_px = _num(ex.get("entry_price")) or _num(acc.get("current_price")) or _num(acc.get("entry_price"))
        exit_px = _num(ex.get("exit_price")) or _num(ex.get("current_price"))
        pnl = _num(ex.get("pnl_pct"))
        if pnl is None and entry_px and exit_px and entry_px > 0:
            pnl = (exit_px / entry_px - 1.0) * 100.0
        mfe = _num(ex.get("peak_mfe_pct"))
        if mfe is None:
            mfe = _num(ex.get("rolling_mfe_pct"))
        mae = _num(ex.get("rolling_mae_pct"))
        if mae is None:
            mae = _num(ex.get("mae_pct"))
        hold = _num(ex.get("hold_sec"))
        if hold is None and entry_dt and exit_dt:
            hold = (exit_dt - entry_dt).total_seconds()
        stop_hit = _truthy(ex.get("stop_hit")) or _is_stop_reason(str(ex.get("exit_reason") or ""))
        no_prog = _truthy(ex.get("no_progress_exit")) or str(ex.get("exit_reason") or "") == "no_progress_exit"
        exit_reason = str(ex.get("exit_reason") or "")
        labels = label_trade(
            pnl_pct=pnl,
            mfe=mfe,
            mae=mae,
            stop_hit=stop_hit,
            exit_reason=exit_reason,
            no_progress_exit=no_prog,
            hold_sec=hold,
        )
        score_v2 = _num(acc.get("entry_expectancy_score_v2"))
        if score_v2 is None:
            score_v2 = _num(acc.get("score_v2"))
        if score_v2 is None:
            score_v2 = _num(ex.get("entry_expectancy_score_v2"))
        momentum = _num(acc.get("entry_momentum_score"))
        if momentum is None:
            momentum = _num(acc.get("momentum_continuation_score"))
        if momentum is None:
            momentum = _num(acc.get("entry_momentum_continuation_score"))
        session = detect_session_kind(sess, entry_dt)
        trade_id = f"{day}|{sess.name}|{acc.get('symbol')}|{idx}|{method}"
        rows.append(
            {
                "trade_id": trade_id,
                "trading_date": day,
                "session": session,
                "session_id": sess.name,
                "symbol": str(acc.get("symbol") or ex.get("symbol") or ""),
                "decision_id": acc.get("decision_id") or ex.get("decision_id"),
                "match_method": method,
                "entry_time": (entry_dt.isoformat() if entry_dt else (ex.get("entry_time") or acc.get("entry_time"))),
                "exit_time": (exit_dt.isoformat() if exit_dt else ex.get("exit_time")),
                "entry_price": entry_px,
                "exit_price": exit_px,
                "exit_reason": exit_reason,
                "stop_hit": stop_hit,
                "no_progress_exit": no_prog,
                "pnl_pct": pnl,
                "mfe_pct": mfe,
                "mae_pct": mae,
                "hold_sec": hold,
                "score_v2": score_v2,
                "momentum": momentum,
                "momentum_continuation_score": _num(acc.get("momentum_continuation_score")),
                "entry_order_book_imbalance": _num(acc.get("entry_order_book_imbalance")),
                "entry_imbalance_percentile": _num(acc.get("entry_imbalance_percentile")),
                "spread_bps": _num(acc.get("spread_bps")),
                "continuation_quality_score": _num(acc.get("continuation_quality_score")),
                "quality_tier": acc.get("quality_tier"),
                "board_age_sec": _num(acc.get("board_age_sec")),
                "entry_board_mid_token_active": _truthy(acc.get("entry_board_mid_token_active")),
                **labels,
            }
        )
    return rows


def _process_day(args: tuple[str, list[str]]) -> list[dict[str, Any]]:
    day, sess_paths = args
    rows: list[dict[str, Any]] = []
    for sp in sess_paths:
        rows.extend(process_session(day, sp))
    return rows


def build_panel() -> pd.DataFrame:
    sess_list = iter_session_dirs()
    by_day: dict[str, list[str]] = {}
    for day, sess in sess_list:
        by_day.setdefault(day, []).append(str(sess))
    tasks = [(d, paths) for d, paths in sorted(by_day.items())]
    rows: list[dict[str, Any]] = []
    workers = min(MAX_WORKERS, max(1, len(tasks)))
    if workers == 1 or len(tasks) <= 1:
        for t in tasks:
            rows.extend(_process_day(t))
    else:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futs = {pool.submit(_process_day, t): t[0] for t in tasks}
            for fut in as_completed(futs):
                rows.extend(fut.result())
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df = df.sort_values(["trading_date", "session", "entry_time", "symbol"]).reset_index(drop=True)
    return df


def summary_json(df: pd.DataFrame) -> dict[str, Any]:
    if df.empty:
        return {
            "n_days": 0,
            "n_trades": 0,
            "stop_rate": None,
            "np_rate": None,
            "winner_b_rate": None,
            "days": [],
            "out_path": str(OUT_PQ),
        }
    n = len(df)
    days = sorted(df["trading_date"].astype(str).unique().tolist())
    return {
        "n_days": int(len(days)),
        "n_trades": int(n),
        "stop_rate": float(df["label_stop"].mean()),
        "np_rate": float(df["label_no_progress"].mean()),
        "winner_b_rate": float(df["label_winner_b"].mean()),
        "winner_a_rate": float(df["label_winner_a"].mean()),
        "days": days,
        "out_path": str(OUT_PQ),
    }


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    df = build_panel()
    df.to_parquet(OUT_PQ, index=False)
    summary = summary_json(df)
    summary["n_rows"] = int(len(df))
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
