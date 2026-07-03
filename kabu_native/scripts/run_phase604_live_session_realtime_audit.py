#!/usr/bin/env python3
"""Phase604: Real-time live_session reject audit (5-min buckets + vs-yesterday delta)."""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

JST = ZoneInfo("Asia/Tokyo")
KEY_REJECTS = (
    "data_stale_price",
    "entry_score_v2_below_threshold",
    "pullback_misread_dynamic40_guard",
    "momentum_low_required",
    "daytrade_suitability",
    "max_concurrent",
    "REJECT_SAME_SYMBOL_OPEN_OVERLAP",
    "or_overlay_not_candidate",
    "or_cap_full",
    "near_day_high_low_momentum_dynamic40_guard",
)


def _parse_ts(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(JST)
    except ValueError:
        return None


def _find_active_session(day_dir: Path) -> Optional[Path]:
    sessions = sorted(day_dir.glob("live_session_*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for sess in sessions:
        if (sess / "small_paper_events.csv").exists():
            return sess
    return None


def _load_rows(sess: Path, asof: datetime) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    path = sess / "small_paper_events.csv"
    if not path.exists():
        return rows
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            ts = _parse_ts(row.get("event_time"))
            if ts is None or ts > asof:
                continue
            rows.append(row)
    return rows


def snapshot(sess: Path, asof: datetime) -> dict[str, Any]:
    rows = _load_rows(sess, asof)
    rej = Counter()
    funnel = Counter()
    pfs = Counter()
    t0 = asof - timedelta(minutes=30)
    pbv2_acc = 0
    or_acc = 0
    board_fb = 0

    for row in rows:
        pfs[row.get("price_freshness_source") or "none"] += 1
        if row.get("fallback_used") in ("True", "true", "1"):
            board_fb += 1
        et = row.get("event_type")
        ts = _parse_ts(row.get("event_time"))
        if et == "accepted":
            pool = str(row.get("entry_type") or row.get("reject_reason") or "").upper()
            if pool == "OR" or row.get("or_o_r003_pass") in ("True", "true"):
                or_acc += 1
            elif pool == "PBV2":
                pbv2_acc += 1
            else:
                or_acc += 1
        elif et == "rejected":
            rr = row.get("gate_reject_reason") or row.get("reject_reason") or "unknown"
            rej[rr] += 1
            if ts and t0 <= ts <= asof:
                funnel[rr] += 1

    open_pos = 0
    pos_path = sess / "small_paper_positions.csv"
    if pos_path.exists():
        with pos_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if not (row.get("exit_time") or "").strip():
                    open_pos += 1

    uni = 50
    cfg_path = sess / "live_session_config.json"
    if cfg_path.exists():
        uni = int(json.loads(cfg_path.read_text(encoding="utf-8")).get("symbol_count") or 50)

    summary_path = sess / "small_paper_summary.json"
    or_entry = pbv2_entry = 0
    if summary_path.exists():
        summ = json.loads(summary_path.read_text(encoding="utf-8"))
        or_entry = int(summ.get("or_entry_count") or summ.get("or_count") or 0)
        pbv2_entry = int(summ.get("pbv2_count") or 0)

    return {
        "asof": asof.isoformat(),
        "session": sess.name,
        "accepted_count": sum(1 for r in rows if r.get("event_type") == "accepted"),
        "current_open_positions": open_pos,
        "reject_top20": rej.most_common(20),
        "key_counts": {k: rej.get(k, 0) for k in KEY_REJECTS},
        "same_symbol_overlap": rej.get("REJECT_SAME_SYMBOL_OPEN_OVERLAP", 0),
        "pbv2_accepted": pbv2_entry or pbv2_acc,
        "or_accepted": or_entry or or_acc,
        "board_fallback_used": max(board_fb, pfs.get("board_fallback", 0)),
        "universe_symbols": uni,
        "last30_reject_funnel": funnel.most_common(20),
        "total_rejects": sum(rej.values()),
        "gate_evaluations": len([r for r in rows if r.get("event_type") in ("candidate", "rejected", "accepted")]),
    }


def delta(today: dict[str, Any], yday: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in KEY_REJECTS:
        d = today["key_counts"].get(k, 0) - yday["key_counts"].get(k, 0)
        if d:
            out[k] = d
    out["accepted_count"] = today["accepted_count"] - yday["accepted_count"]
    out["board_fallback_used"] = today["board_fallback_used"] - yday["board_fallback_used"]
    return out


def write_report(
    *,
    out_dir: Path,
    today: dict[str, Any],
    yday: dict[str, Any],
    d: dict[str, Any],
    verdict: str,
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(tz=JST).strftime("%Y%m%d_%H%M%S")
    payload = {
        "generated_at": datetime.now(tz=JST).isoformat(),
        "verdict": verdict,
        "today": today,
        "yesterday_same_clock": yday,
        "delta": d,
    }
    json_path = out_dir / f"phase604_realtime_{ts}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        f"# Phase604 Realtime Audit — {today['asof']}",
        "",
        f"**Session:** `{today['session']}`",
        "",
        "## Snapshot",
        "",
        f"| Metric | Today | Yesterday Δ |",
        f"|--------|------:|------------:|",
        f"| accepted | {today['accepted_count']} | {d.get('accepted_count', 0):+d} |",
        f"| open positions | {today['current_open_positions']} | — |",
        f"| board_fallback | {today['board_fallback_used']} | {d.get('board_fallback_used', 0):+d} |",
        f"| PBv2 accepted | {today['pbv2_accepted']} | — |",
        f"| OR accepted | {today['or_accepted']} | — |",
        f"| universe | {today['universe_symbols']} | — |",
        "",
        "### Key rejects (today)",
        "",
    ]
    for k in KEY_REJECTS:
        v = today["key_counts"].get(k, 0)
        dv = d.get(k, 0)
        if v or dv:
            md_lines.append(f"- `{k}`: {v} (Δ {dv:+d})")
    md_lines += ["", "### Reject top20", ""]
    for reason, cnt in today["reject_top20"]:
        md_lines.append(f"- `{reason}`: {cnt}")
    md_lines += ["", "### Last 30min funnel", ""]
    for reason, cnt in today["last30_reject_funnel"]:
        md_lines.append(f"- `{reason}`: {cnt}")
    md_lines += ["", f"## Root cause\n\n{verdict}", ""]
    md_path = out_dir / f"phase604_realtime_{ts}.md"
    md_path.write_text("\n".join(md_lines), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8", errors="replace").decode("utf-8"))
    print(f"\nWrote {json_path}\nWrote {md_path}")


def root_cause_verdict(today: dict[str, Any], d: dict[str, Any]) -> str:
    top = today["reject_top20"][0][0] if today["reject_top20"] else "unknown"
    top_cnt = today["reject_top20"][0][1] if today["reject_top20"] else 0
    total = max(today["total_rejects"], 1)
    share = top_cnt / total * 100.0
    d_or = d.get("or_overlay_not_candidate", 0)
    d_stale = d.get("data_stale_price", 0)
    if d_or > 500 and share > 80:
        return (
            f"**`or_overlay_not_candidate` surge (delta {d_or:+d}, {share:.0f}%)** - "
            "PBv2 fail then OR overlay rescue also fails."
            f" vs yesterday `data_stale_price` delta {d_stale:+d} (board_fallback passes {today['board_fallback_used']})"
            " -> evals reach OR gate and get labeled or_overlay_not_candidate."
            f" PBv2 accepted={today['pbv2_accepted']} / OR accepted={today['or_accepted']}."
        )
    if d_stale > 500:
        return f"**`data_stale_price` 急増（Δ{d_stale:+d}）** が主因。"
    return f"**`{top}`** が最多 reject（{share:.0f}%）。"


def run_once(
    *,
    today_dir: Path,
    yday_dir: Path,
    yday_session: Optional[str],
    out_dir: Path,
) -> None:
    asof = datetime.now(tz=JST)
    today_sess = _find_active_session(today_dir)
    if today_sess is None:
        raise SystemExit(f"No active session under {today_dir}")

    yday_sess = yday_dir / yday_session if yday_session else _find_active_session(yday_dir)
    if yday_sess is None or not yday_sess.exists():
        raise SystemExit(f"No comparison session under {yday_dir}")

    yday_asof = asof.replace(
        year=int(yday_dir.name[:4]),
        month=int(yday_dir.name[4:6]),
        day=int(yday_dir.name[6:8]),
    )
    today = snapshot(today_sess, asof)
    yday = snapshot(yday_sess, yday_asof)
    d = delta(today, yday)
    verdict = root_cause_verdict(today, d)
    write_report(out_dir=out_dir, today=today, yday=yday, d=d, verdict=verdict)


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase604 live session realtime audit")
    ap.add_argument("--day", default="20260630")
    ap.add_argument("--compare-day", default="20260629")
    ap.add_argument("--compare-session", default="live_session_080236")
    ap.add_argument("--interval-min", type=float, default=5.0)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()

    base = ROOT / "results" / "small_paper"
    today_dir = base / args.day
    yday_dir = base / args.compare_day
    out_dir = ROOT / "results" / "reports" / "phase604_realtime"

    if args.once:
        run_once(
            today_dir=today_dir,
            yday_dir=yday_dir,
            yday_session=args.compare_session,
            out_dir=out_dir,
        )
        return 0

    while True:
        try:
            run_once(
                today_dir=today_dir,
                yday_dir=yday_dir,
                yday_session=args.compare_session,
                out_dir=out_dir,
            )
        except Exception as exc:
            print(f"phase604 audit error: {exc}", file=sys.stderr)
        time.sleep(max(60.0, args.interval_min * 60.0))


if __name__ == "__main__":
    raise SystemExit(main())
