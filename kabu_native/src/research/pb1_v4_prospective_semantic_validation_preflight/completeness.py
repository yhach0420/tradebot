"""Native 1m/5m completeness, ATR20, BAR_START. Synthetic fixtures only here."""
from __future__ import annotations

from typing import Any

from research.multi_touch_daily_zone_1m_price_action_v1.daily import atr20
from research.pb1_v4_machine_implementation.bars5 import build_five_m, five_m_windows_am, is_five_m_close

AM_FIRST = "09:00"
AM_LAST_1M = "11:19"
REQUIRED_1M_N = 140  # 09:00 through 11:19 inclusive
REQUIRED_5M_N = len(five_m_windows_am())


def _hhmm_add(hhmm: str, minutes: int) -> str:
    h = int(hhmm[:2])
    m = int(hhmm[3:5])
    tot = h * 60 + m + int(minutes)
    return f"{tot // 60:02d}:{tot % 60:02d}"


def am_1m_clocks() -> tuple[str, ...]:
    out: list[str] = []
    t = AM_FIRST
    while t <= AM_LAST_1M:
        out.append(t)
        t = _hhmm_add(t, 1)
    return tuple(out)


def synthetic_1m(*, drop: tuple[str, ...] = ()) -> dict[str, Any]:
    rec: dict[str, Any] = {k: [] for k in ("t", "o", "h", "l", "c", "v", "va")}
    px = 1000.0
    skip = set(drop)
    for t in am_1m_clocks():
        if t in skip:
            continue
        rec["t"].append(t)
        rec["o"].append(px)
        rec["h"].append(px + 1.0)
        rec["l"].append(px - 1.0)
        rec["c"].append(px + 0.25)
        rec["v"].append(1000.0)
        rec["va"].append(1.0e6)
        px += 0.25
    return rec


def session_idx(rec: dict[str, Any]) -> list[int]:
    return list(range(len(rec.get("t") or [])))


def bar_start_ok(rec: dict[str, Any]) -> dict[str, Any]:
    times = [str(t)[:5] for t in rec.get("t") or []]
    first = times[0] if times else ""
    five_close = [t for t in times if is_five_m_close(t)]
    windows = five_m_windows_am()
    return {
        "ok": first == AM_FIRST and AM_LAST_1M in times and len(windows) == REQUIRED_5M_N and "09:04" in five_close,
        "first_bar_t": first,
        "convention": "BAR_START",
        "first_1m_is_0900_not_0901": first == AM_FIRST,
        "five_m_close_is_xx04": "09:04" in five_close and "09:00" not in [t for t in five_close],
        "windows_first": windows[0] if windows else None,
        "windows_last": windows[-1] if windows else None,
    }


def check_1m_complete(rec: dict[str, Any]) -> dict[str, Any]:
    times = [str(t)[:5] for t in rec.get("t") or []]
    need = set(am_1m_clocks())
    have = set(times)
    missing = sorted(need - have)
    extra_lunch = [t for t in times if "11:30" <= t < "12:30"]
    return {
        "ok": not missing and len(times) >= REQUIRED_1M_N,
        "have_n": len(times),
        "required_n": REQUIRED_1M_N,
        "missing_n": len(missing),
        "missing_sample": missing[:8],
        "lunch_bars_present": bool(extra_lunch),
    }


def check_5m_complete(rec: dict[str, Any]) -> dict[str, Any]:
    bars = build_five_m(rec, session_idx(rec), through="11:19")
    windows = five_m_windows_am()
    have = {(str(b.get("t0")), str(b.get("t1"))) for b in bars}
    missing = [w for w in windows if w not in have]
    incomplete_n = [b for b in bars if int(b.get("bar_n") or 0) < 5]
    return {
        "ok": not missing and not incomplete_n and len(bars) == REQUIRED_5M_N,
        "have_n": len(bars),
        "required_n": REQUIRED_5M_N,
        "missing": missing[:8],
        "incomplete_5m_n": len(incomplete_n),
        "first": (bars[0].get("t0"), bars[0].get("t1")) if bars else None,
        "last": (bars[-1].get("t0"), bars[-1].get("t1")) if bars else None,
    }


def synthetic_prior_dailies(n: int = 20) -> list[dict[str, Any]]:
    rows = []
    px = 1000.0
    for i in range(n):
        rows.append(
            {
                "date": f"199901{i+1:02d}" if i < 9 else f"199902{i-8:02d}",
                "open": px,
                "high": px + 10.0,
                "low": px - 8.0,
                "close": px + 1.0,
                "range": 18.0,
                "true_range": 18.0,
            }
        )
        px += 1.0
    return rows


def check_atr20(completed: list[dict[str, Any]]) -> dict[str, Any]:
    val = atr20(completed)
    ok = val == val and float(val) > 0
    short = atr20(completed[:19]) if len(completed) >= 19 else float("nan")
    short_nan = not (short == short)
    return {"ok": ok and short_nan, "atr20": float(val) if ok else None, "short_n_is_nan": short_nan, "n": len(completed)}


def quality_gate(rec: dict[str, Any], *, atr: dict[str, Any] | None = None) -> dict[str, Any]:
    c1 = check_1m_complete(rec)
    c5 = check_5m_complete(rec)
    bs = bar_start_ok(rec)
    a = atr if atr is not None else check_atr20(synthetic_prior_dailies(20))
    ok = bool(c1.get("ok") and c5.get("ok") and bs.get("ok") and a.get("ok"))
    return {"ok": ok, "native_1m": c1, "native_5m": c5, "bar_start": bs, "atr20": a}
