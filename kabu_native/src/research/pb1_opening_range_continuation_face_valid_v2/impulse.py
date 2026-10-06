"""09:00–09:14 PATH semantics. Opening impulse is a path, not 09:14 vs midpoint alone."""
from __future__ import annotations

from typing import Any

from research.one_minute_native_playbook_discovery_v1.states import to_min
from research.pb1_opening_range_continuation_face_valid_v2 import (
    CLEAN_NET15_FRAC,
    CLEAN_NET5_OPPOSE_FRAC,
    FAILED_SPIKE_FRAC,
    OR_END,
    OR_START,
    RANGE_NET15_FRAC,
    RANGE_TWO_SIDED_FRAC,
)


def _finite(x: Any) -> bool:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return False
    return f == f


def opening_path(times: list[str], o: Any, h: Any, l: Any, c: Any, session_idx: list[int]) -> dict[str, Any]:
    bars: list[tuple[str, float, float, float, float]] = []
    for i in session_idx:
        t = str(times[i])
        if t < OR_START or t > OR_END:
            continue
        if not (_finite(o[i]) and _finite(h[i]) and _finite(l[i]) and _finite(c[i])):
            continue
        bars.append((t, float(o[i]), float(h[i]), float(l[i]), float(c[i])))
    empty = {
        "ok": False,
        "open_0900": float("nan"),
        "close_0904": float("nan"),
        "close_0914": float("nan"),
        "first_or_high_t": None,
        "first_or_low_t": None,
        "mfe_up": float("nan"),
        "mae_dn": float("nan"),
        "net_5m": float("nan"),
        "net_or15": float("nan"),
        "or_range": float("nan"),
    }
    if len(bars) < 15 or bars[0][0] != OR_START or bars[-1][0] != OR_END:
        return empty
    open_px = bars[0][1]
    close_0904 = next((b[4] for b in bars if b[0] == "09:04"), float("nan"))
    close_0914 = bars[-1][4]
    or_high = max(b[2] for b in bars)
    or_low = min(b[3] for b in bars)
    first_high = next((b[0] for b in bars if b[2] == or_high), None)
    first_low = next((b[0] for b in bars if b[3] == or_low), None)
    mfe_up = or_high - open_px
    mae_dn = open_px - or_low
    return {
        "ok": True,
        "open_0900": open_px,
        "close_0904": close_0904,
        "close_0914": close_0914,
        "first_or_high_t": first_high,
        "first_or_low_t": first_low,
        "mfe_up": float(mfe_up),
        "mae_dn": float(mae_dn),
        "net_5m": float(close_0904 - open_px) if _finite(close_0904) else float("nan"),
        "net_or15": float(close_0914 - open_px) if _finite(close_0914) else float("nan"),
        "or_high": float(or_high),
        "or_low": float(or_low),
        "or_range": float(or_high - or_low),
        "or_mid": float(0.5 * (or_high + or_low)),
    }


def _tord(a: str | None, b: str | None) -> int | None:
    if not a or not b:
        return None
    ia, ib = to_min(a), to_min(b)
    if ia is None or ib is None:
        return None
    if ia < ib:
        return -1
    if ia > ib:
        return 1
    return 0


def classify_opening(path: dict[str, Any]) -> dict[str, Any]:
    """CLEAN_OPENING_IMPULSE / FAILED_OPEN_REVERSAL / RANGE_OPEN / AMBIGUOUS_OPEN. No returns."""
    if not path.get("ok"):
        return {"state": "AMBIGUOUS_OPEN", "DIR": 0, "reason": "or_incomplete"}
    rng = path.get("or_range")
    if not _finite(rng) or float(rng) <= 0:
        return {"state": "AMBIGUOUS_OPEN", "DIR": 0, "reason": "or_range_zero"}
    rng = float(rng)
    net15 = float(path["net_or15"]) / rng if _finite(path.get("net_or15")) else float("nan")
    net5 = float(path["net_5m"]) / rng if _finite(path.get("net_5m")) else float("nan")
    mfe = float(path["mfe_up"]) / rng if _finite(path.get("mfe_up")) else float("nan")
    mae = float(path["mae_dn"]) / rng if _finite(path.get("mae_dn")) else float("nan")
    c15 = path.get("close_0914")
    mid = path.get("or_mid")
    order = _tord(path.get("first_or_high_t"), path.get("first_or_low_t"))

    # Spike one way first, then finish the OR on the other side.
    if (
        _finite(mfe)
        and mfe >= float(FAILED_SPIKE_FRAC)
        and order == -1
        and _finite(net15)
        and net15 < 0
    ):
        return {"state": "FAILED_OPEN_REVERSAL", "DIR": 0, "reason": "up_spike_then_reverse", "net15_frac": net15}
    if (
        _finite(mae)
        and mae >= float(FAILED_SPIKE_FRAC)
        and order == 1
        and _finite(net15)
        and net15 > 0
    ):
        return {"state": "FAILED_OPEN_REVERSAL", "DIR": 0, "reason": "down_spike_then_reverse", "net15_frac": net15}

    if (
        _finite(net15)
        and abs(net15) <= float(RANGE_NET15_FRAC)
        and _finite(mfe)
        and _finite(mae)
        and mfe >= float(RANGE_TWO_SIDED_FRAC)
        and mae >= float(RANGE_TWO_SIDED_FRAC)
    ):
        return {"state": "RANGE_OPEN", "DIR": 0, "reason": "two_sided_small_net", "net15_frac": net15}

    if (
        _finite(net15)
        and net15 > float(CLEAN_NET15_FRAC)
        and _finite(net5)
        and net5 >= -float(CLEAN_NET5_OPPOSE_FRAC)
        and _finite(mfe)
        and _finite(mae)
        and mfe >= mae
        and _finite(c15)
        and _finite(mid)
        and float(c15) >= float(mid)
    ):
        return {"state": "CLEAN_OPENING_IMPULSE", "DIR": 1, "reason": "bull_path_consistent", "net15_frac": net15}
    if (
        _finite(net15)
        and net15 < -float(CLEAN_NET15_FRAC)
        and _finite(net5)
        and net5 <= float(CLEAN_NET5_OPPOSE_FRAC)
        and _finite(mfe)
        and _finite(mae)
        and mae >= mfe
        and _finite(c15)
        and _finite(mid)
        and float(c15) <= float(mid)
    ):
        return {"state": "CLEAN_OPENING_IMPULSE", "DIR": -1, "reason": "bear_path_consistent", "net15_frac": net15}

    return {"state": "AMBIGUOUS_OPEN", "DIR": 0, "reason": "not_clean_not_failed_not_range", "net15_frac": net15}
