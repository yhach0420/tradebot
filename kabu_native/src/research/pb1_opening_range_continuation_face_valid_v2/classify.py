"""Blinded human classification. PATTERN and TRADE are separate. Filled after V2 chart review."""
from __future__ import annotations

from collections import Counter
from typing import Any


def _lab(
    pattern: str,
    trade: str,
    failure: str | None,
    *,
    play: bool,
    brk: bool,
    leave: bool,
    ret: bool,
    hold: bool,
    trig: bool,
    note: str,
) -> dict[str, Any]:
    return {
        "pattern": pattern,
        "trade": trade,
        "failure": failure,
        "PATTERN_FACE_VALID": pattern == "CLEAR_OPENING_CONTINUATION",
        "TRADE_FACE_VALID": trade == "TRADE_FACE_VALID",
        "genuinely_in_play": play,
        "real_break": brk,
        "real_leave": leave,
        "real_retest": ret,
        "real_hold": hold,
        "trigger_recognizable": trig,
        "note": note,
    }


C, Q, N = "CLEAR_OPENING_CONTINUATION", "QUESTIONABLE", "NOT_OPENING_CONTINUATION"
T, TQ, TN = "TRADE_FACE_VALID", "QUESTIONABLE_REWARD_SPACE", "NO_REWARD_SPACE"

# Independent VERIFY sample. Not the V1 24. No PnL.
HUMAN_LABELS: dict[int, dict[str, Any]] = {
    1: _lab(Q, TN, "FAILED_OPEN_REVERSAL", play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Dump then V-recovery through OR_HIGH; late grind break. Not a bullish opening impulse."),
    2: _lab(C, TN, "ALREADY_EXTENDED", play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Clean gap-up drive, OR_HIGH break, leave, first retest. Already into PDH."),
    3: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening drive, 09:15 OR_HIGH break, extension, failed-push trigger."),
    4: _lab(Q, T, "RANGE_NOISE", play=True, brk=False, leave=True, ret=True, hold=True, trig=True, note="Two-sided open then 25m grind; 09:39 close-beyond is late range resolution."),
    5: _lab(C, TN, "ALREADY_EXTENDED", play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Gap-up impulse, pullback, OR_HIGH break/retest. Sitting on PDH."),
    6: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Inside-OR dip then drive; one-bar extension leave, immediate first retest."),
    7: _lab(C, TN, "ALREADY_EXTENDED", play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Violent opening dump, OR_LOW break, long leave, first retest at 30m already extended."),
    8: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening high then dump through OR_LOW at 09:15, leave, reclaim."),
    9: _lab(Q, TN, "MICRO_BREAK", play=True, brk=False, leave=True, ret=True, hold=True, trig=True, note="Tight morning grind along OR_LOW; leak is not a distinctive break."),
    10: _lab(C, TQ, "ALREADY_EXTENDED", play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening dump, 09:15 OR_LOW break, leave, retest. Tight to D5L."),
    11: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening dump, pause, OR_LOW break, 25m leave still inside freshness, failed-push."),
    12: _lab(Q, TN, "MICRO_BREAK", play=True, brk=False, leave=True, ret=True, hold=True, trig=True, note="Dump, bounce, late tiny OR_LOW leak. Already at PDL."),
    13: _lab(C, TN, "ALREADY_EXTENDED", play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Gap-up from OR_LOW through OR_HIGH, leave, retest. Into PDH."),
    14: _lab(Q, TN, "STALE_RETEST", play=True, brk=True, leave=True, ret=False, hold=True, trig=True, note="Choppy open then 09:28 break; first return at 27m after the extension was used."),
    15: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Drive, inside-OR pullback, OR_HIGH break/retest/reclaim."),
    16: _lab(N, T, "FAILED_OPEN_REVERSAL", play=True, brk=False, leave=False, ret=False, hold=True, trig=True, note="Open up, dump to OR_LOW, V-recovery, 09:39 opposite-side break. Not continuation."),
    17: _lab(Q, T, "RANGE_NOISE", play=True, brk=False, leave=True, ret=True, hold=True, trig=True, note="Opening drive then 30m under OR_HIGH; 09:50 break is late range."),
    18: _lab(C, TN, "ALREADY_EXTENDED", play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Small-range opening grind-up, 09:15 break, leave, retest. Into PDH."),
    19: _lab(C, TQ, "ALREADY_EXTENDED", play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening dump, failed bounce, OR_LOW break. PDL immediately ahead."),
    20: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening waterfall, OR_LOW break, leave, later first retest still fresh."),
    21: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening dump, failed bounce, OR_LOW break/retest/reclaim."),
    22: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening dump, inside-OR bounce, second push through OR_LOW."),
    23: _lab(Q, TN, "MICRO_BREAK", play=True, brk=False, leave=False, ret=True, hold=True, trig=True, note="Grind to OR_LOW with one-bar probe leave. Not a distinct extension."),
    24: _lab(C, TN, "ALREADY_EXTENDED", play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening dump, bounce, second push. No room beyond PDL."),
    25: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening drive from OR_LOW through OR_HIGH, leave, first retest."),
    26: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening bid, inside-OR pullback, later same-direction OR_HIGH break."),
    27: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening grind-up, large break bar, one-bar extension leave, failed-push."),
    28: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Open, dip, drive, pause under OR_HIGH, break/retest/reclaim."),
    29: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening impulse, pullback, second push, short leave, retest."),
    30: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening spike then hold, OR_HIGH break, leave, reclaim."),
    31: _lab(C, TN, "ALREADY_EXTENDED", play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening waterfall, bounce, OR_LOW break. Already at PDL."),
    32: _lab(Q, TQ, "MICRO_BREAK", play=True, brk=False, leave=True, ret=True, hold=True, trig=True, note="Opening dump then 20m sit on OR_LOW; 09:39 leak."),
    33: _lab(C, TN, "ALREADY_EXTENDED", play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening dump, small bounce, OR_LOW break. No room."),
    34: _lab(C, TN, "ALREADY_EXTENDED", play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening dump, OR_LOW break, leave, retest. Into PDL."),
    35: _lab(C, TN, "ALREADY_EXTENDED", play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening dump, pause, OR_LOW break. R:R to PDL < 0.50."),
    36: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening dump, bounce, second push through OR_LOW, failed-push trigger."),
    37: _lab(Q, T, "RANGE_NOISE", play=True, brk=False, leave=True, ret=True, hold=True, trig=True, note="Opening spike then 30m stall; 09:46 break is not opening continuation timing."),
    38: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening drive from OR_LOW, pause under OR_HIGH, break/retest."),
    39: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening grind then acceleration, 09:15 break, leave, first retest."),
    40: _lab(Q, TN, "RANGE_NOISE", play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening spike then 20m range; 09:38 break. Already into PDH."),
    41: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening drive, pause, OR_HIGH break, short leave, reclaim."),
    42: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening drive, 09:16 break, 18m leave still fresh, retest."),
    43: _lab(Q, TN, "RANGE_NOISE", play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Dump, bounce, grind to OR_LOW. Two-sided morning, no room."),
    44: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening dump, pause, OR_LOW break, leave, reclaim."),
    45: _lab(Q, T, "RANGE_NOISE", play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Huge first bar then 25m mid-OR chop; 09:39 break is delayed resolution."),
    46: _lab(Q, T, "MICRO_BREAK", play=True, brk=False, leave=True, ret=True, hold=True, trig=True, note="Opening dump then grind; 09:32 tiny OR_LOW leak."),
    47: _lab(N, TQ, "RANGE_NOISE", play=True, brk=False, leave=True, ret=True, hold=True, trig=True, note="40m two-sided range after a mixed open; 09:48 dump is not opening continuation."),
    48: _lab(C, T, None, play=True, brk=True, leave=True, ret=True, hold=True, trig=True, note="Opening dump, pause, 09:15 OR_LOW break, leave, reclaim."),
}


def apply_labels(sample: list[dict[str, Any]]) -> dict[str, Any]:
    labeled: list[dict[str, Any]] = []
    for ev in sample:
        sid = int(ev.get("sample_id") or 0)
        lab = dict(HUMAN_LABELS.get(sid) or {})
        pattern = lab.get("pattern")
        trade = lab.get("trade")
        failure = lab.get("failure")
        row = {
            "sample_id": sid,
            "symbol": ev.get("symbol"),
            "date": ev.get("date"),
            "block": ev.get("block"),
            "direction": ev.get("direction"),
            "in_play_tag": ev.get("in_play"),
            "in_play_reason": ev.get("in_play_reason"),
            "open_state": ev.get("open_state"),
            "room_class": ev.get("room_class"),
            "break_t": ev.get("break_t"),
            "retest_t": ev.get("retest_t"),
            "break_to_retest_minutes": ev.get("break_to_retest_minutes"),
            "away_n": ev.get("away_n"),
            "trigger_primary": ev.get("trigger_primary") or (list(ev.get("trigger_labels") or [])[:1] or [None])[0],
            "chart": f"sample_{sid:02d}_{ev.get('symbol')}_{ev.get('date')}_{ev.get('direction')}.png",
            "pattern": pattern,
            "trade": trade,
            "failure": failure,
            "PATTERN_FACE_VALID": lab.get("PATTERN_FACE_VALID"),
            "TRADE_FACE_VALID": lab.get("TRADE_FACE_VALID"),
            "genuinely_in_play": lab.get("genuinely_in_play"),
            "real_break": lab.get("real_break"),
            "real_leave": lab.get("real_leave"),
            "real_retest": lab.get("real_retest"),
            "real_hold": lab.get("real_hold"),
            "trigger_recognizable": lab.get("trigger_recognizable"),
            "note": lab.get("note"),
            "reviewed": bool(lab),
            "v1_dev_reused": bool(ev.get("v1_dev_reused")),
        }
        labeled.append(row)
    patterns = Counter(r["pattern"] for r in labeled if r["reviewed"])
    trades = Counter(r["trade"] for r in labeled if r["reviewed"])
    fails = Counter(str(r.get("failure") or "NONE") for r in labeled if r["reviewed"])
    fail_only = Counter(str(r.get("failure")) for r in labeled if r["reviewed"] and r.get("pattern") != "CLEAR_OPENING_CONTINUATION")
    n = sum(1 for r in labeled if r["reviewed"])
    clear = int(patterns.get("CLEAR_OPENING_CONTINUATION") or 0)
    q = int(patterns.get("QUESTIONABLE") or 0)
    ni = int(patterns.get("NOT_OPENING_CONTINUATION") or 0)
    trade_ok = int(trades.get("TRADE_FACE_VALID") or 0)
    trade_q = int(trades.get("QUESTIONABLE_REWARD_SPACE") or 0)
    trade_no = int(trades.get("NO_REWARD_SPACE") or 0)
    range_n = int(fails.get("RANGE_NOISE") or 0)
    failed_open_n = int(fails.get("FAILED_OPEN_REVERSAL") or 0)
    stale_n = int(fails.get("STALE_RETEST") or 0)
    most = fail_only.most_common(1)[0][0] if fail_only else None
    return {
        "reviewed_n": n,
        "sample_n": len(labeled),
        "CLEAR_OPENING_CONTINUATION": clear,
        "QUESTIONABLE": q,
        "NOT_OPENING_CONTINUATION": ni,
        "CLEAR_INTENDED_SETUP": clear,
        "NOT_INTENDED_SETUP": ni,
        "clear_share": float(clear / n) if n else None,
        "pattern_face_valid_n": clear,
        "pattern_face_valid_share": float(clear / n) if n else None,
        "trade_face_valid_n": trade_ok,
        "trade_face_valid_share": float(trade_ok / n) if n else None,
        "TRADE_FACE_VALID": trade_ok,
        "QUESTIONABLE_REWARD_SPACE": trade_q,
        "NO_REWARD_SPACE": trade_no,
        "RANGE_NOISE_n": range_n,
        "RANGE_NOISE_share": float(range_n / n) if n else None,
        "FAILED_OPEN_REVERSAL_n": failed_open_n,
        "FAILED_OPEN_REVERSAL_share": float(failed_open_n / n) if n else None,
        "STALE_RETEST_n": stale_n,
        "STALE_RETEST_share": float(stale_n / n) if n else None,
        "failure_counts": dict(fails),
        "most_common_semantic_failure": most,
        "rows": labeled,
        "future_hidden": True,
        "outcome_used": False,
        "v1_sample_reused": False,
    }
