"""Small shared helpers."""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any, Optional, Sequence
from zoneinfo import ZoneInfo

from research.pbv2_zero_base_revalidation.constants import COST_BPS, JST_NAME, SHARES

JST = ZoneInfo(JST_NAME)


def fnum(v: Any, default: Optional[float] = None) -> Optional[float]:
    if v is None or v == "":
        return default
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if math.isnan(x) or math.isinf(x):
        return default
    return x


def parse_ts(v: Any) -> Optional[datetime]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        dt = v
    else:
        s = str(v).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=JST)
    return dt.astimezone(JST)


def day_from_ts(dt: datetime) -> str:
    return dt.astimezone(JST).strftime("%Y%m%d")


def yen100(entry: float, exit_px: float) -> float:
    return round((exit_px - entry) * SHARES, 2)


def cost_yen(entry: float) -> float:
    return round(entry * SHARES * (COST_BPS / 100.0), 2)


def pnl_5bps(entry: float, exit_px: float) -> float:
    return round(yen100(entry, exit_px) - cost_yen(entry), 2)


def profit_factor(yens: Sequence[float]) -> Optional[float]:
    gp = sum(y for y in yens if y > 0)
    gl = abs(sum(y for y in yens if y < 0))
    if gl > 1e-12:
        return round(gp / gl, 4)
    if gp > 0:
        return 999.0
    return None


def safe_div(a: float, b: float) -> Optional[float]:
    if abs(b) < 1e-12:
        return None
    return round(a / b, 6)
