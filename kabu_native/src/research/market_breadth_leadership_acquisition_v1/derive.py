"""Equity-only leadership / surge / sector state. Raw ranking lists are not filtered."""
from __future__ import annotations

import math
import statistics
from typing import Any, Optional

FORBIDDEN_PRIMARY_KEYS = (
    "VOLUME_SURGE_BREADTH",
    "VALUE_SURGE_BREADTH",
    "SECTOR_BREADTH_ASYMMETRY",
)
PRIMARY_CONTEXT_CANDIDATES = (
    "LEADERSHIP_DIRECTION",
    "LEADERSHIP_STRENGTH",
    "TICK_PRESSURE",
    "VOLUME_SURGE_STATE",
    "VALUE_SURGE_STATE",
    "SECTOR_DIRECTION",
    "SECTOR_DISPERSION",
    "LEADERSHIP_TURNOVER",
)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _items(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, dict):
        return []
    ranking = raw.get("Ranking")
    if not isinstance(ranking, list):
        return []
    return [x for x in ranking if isinstance(x, dict)]


def _mean(xs: list[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def _median(xs: list[float]) -> Optional[float]:
    if not xs:
        return None
    return float(statistics.median(xs))


def _stdev(xs: list[float]) -> Optional[float]:
    if len(xs) < 2:
        return 0.0 if len(xs) == 1 else None
    return float(statistics.pstdev(xs))


def _trimmed_mean(xs: list[float], *, frac: float = 0.10) -> Optional[float]:
    if not xs:
        return None
    s = sorted(xs)
    k = int(len(s) * frac)
    if k * 2 >= len(s):
        return _median(s)
    core = s[k : len(s) - k]
    return _mean(core)


def classify_item(item: dict[str, Any]) -> dict[str, Any]:
    """EQUITY_ONLY from ExchangeName. ETF/ETN excluded from the view. 監理/整理 flagged, not dropped."""
    name = str(item.get("ExchangeName") or "")
    is_etf_etn = ("ETF" in name) or ("ETN" in name)
    is_supervised = "監理" in name
    is_reorg = "整理" in name
    return {
        "ExchangeName": name,
        "is_etf_etn": bool(is_etf_etn),
        "is_supervised": bool(is_supervised),
        "is_reorg": bool(is_reorg),
        "equity_only": (not is_etf_etn),
    }


def equity_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [it for it in items if classify_item(it)["equity_only"]]


def _ordered(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(it: dict[str, Any]) -> float:
        n = _f(it.get("No"))
        return n if n is not None else 1e9

    return sorted(items, key=key)


def _symbols(items: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for it in _ordered(items):
        s = str(it.get("Symbol") or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


def _categories(items: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for it in _ordered(items):
        c = str(it.get("Category") or it.get("CategoryName") or "").strip()
        if not c or c in seen:
            continue
        seen.add(c)
        out.append(c)
    return out


def _overlap_rate(curr: list[str], prev: Optional[list[str]]) -> Optional[float]:
    if prev is None:
        return None
    if not curr:
        return None
    return len(set(curr) & set(prev)) / float(len(curr))


def _new_n(curr: list[str], prev: Optional[list[str]]) -> Optional[int]:
    if prev is None:
        return None
    return int(len(set(curr) - set(prev)))


def _vals(items: list[dict[str, Any]], key: str) -> list[float]:
    return [x for x in (_f(it.get(key)) for it in items) if x is not None]


def _surge_block(
    items: list[dict[str, Any]],
    *,
    surge_key: str,
    prev_items: Optional[list[dict[str, Any]]],
) -> dict[str, Any]:
    eq = equity_items(items)
    xs = _vals(eq, surge_key)
    top10 = _ordered(eq)[:10]
    top10_xs = _vals(top10, surge_key)
    chg = _vals(eq, "ChangePercentage")
    pos_n = sum(1 for x in chg if x > 0)
    curr_sym = _symbols(eq)
    prev_eq = equity_items(prev_items) if prev_items is not None else None
    prev_sym = _symbols(prev_eq) if prev_eq is not None else None
    return {
        "EQUITY_TOP50_N": len(eq),
        "raw_list_n": len(items),
        "etf_etn_n": sum(1 for it in items if classify_item(it)["is_etf_etn"]),
        "MEDIAN_SURGE": _median(xs),
        "TRIMMED_MEAN_SURGE": _trimmed_mean(xs, frac=0.10),
        "TOP10_MEDIAN_SURGE": _median(top10_xs),
        "POSITIVE_CHANGE_RATIO_IN_RANKING": (pos_n / len(chg)) if chg else None,
        "NEW_ENTRANT_N": _new_n(curr_sym, prev_sym),
        "RANK_PERSISTENCE": _overlap_rate(curr_sym, prev_sym),
        "field": surge_key,
    }


def _tick_block(items: list[dict[str, Any]]) -> dict[str, Any]:
    deltas: list[float] = []
    norms: list[float] = []
    eq_norms: list[float] = []
    for it in items:
        u, d = _f(it.get("UpCount")), _f(it.get("DownCount"))
        if u is None or d is None:
            continue
        delta = u - d
        deltas.append(delta)
        denom = max(1.0, u + d)
        nrm = delta / denom
        norms.append(nrm)
        if classify_item(it)["equity_only"]:
            eq_norms.append(nrm)
    return {
        "sum_up_minus_down": sum(deltas) if deltas else None,
        "TICK_NORM_MEDIAN": _median(eq_norms if eq_norms else norms),
        "TICK_NORM_MEDIAN_ALL": _median(norms),
        "n": len(items),
        "equity_n": len(equity_items(items)),
        "formula": "(UpCount-DownCount)/max(1,UpCount+DownCount)",
    }


def _leader_side(
    items: list[dict[str, Any]],
    *,
    prev_items: Optional[list[dict[str, Any]]],
) -> dict[str, Any]:
    eq = equity_items(items)
    chg = _vals(eq, "ChangePercentage")
    top10 = _vals(_ordered(eq)[:10], "ChangePercentage")
    curr_sym = _symbols(eq)
    prev_eq = equity_items(prev_items) if prev_items is not None else None
    prev_sym = _symbols(prev_eq) if prev_eq is not None else None
    flags = [classify_item(it) for it in items]
    return {
        "median_change": _median(chg),
        "top10_median": _median(top10),
        "equity_n": len(eq),
        "raw_list_n": len(items),
        "etf_etn_n": sum(1 for f in flags if f["is_etf_etn"]),
        "supervised_n": sum(1 for f in flags if f["is_supervised"]),
        "reorg_n": sum(1 for f in flags if f["is_reorg"]),
        "new_n": _new_n(curr_sym, prev_sym),
        "rank_persistence": _overlap_rate(curr_sym, prev_sym),
    }


def _sector_universe(t14: list[dict[str, Any]], t15: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Type14/15 are the same 33-sector universe. Prefer Type14 order. Do not treat as two breadths."""
    src = t14 if t14 else t15
    by_cat: dict[str, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for it in _ordered(src):
        cat = str(it.get("Category") or it.get("CategoryName") or "").strip()
        if not cat or cat in by_cat:
            continue
        by_cat[cat] = it
        ordered.append(it)
    return ordered


def _sector_block(
    t14: list[dict[str, Any]],
    t15: list[dict[str, Any]],
    *,
    prev_t14: Optional[list[dict[str, Any]]],
    prev_t15: Optional[list[dict[str, Any]]],
) -> dict[str, Any]:
    uni = _sector_universe(t14, t15)
    chg = _vals(uni, "ChangePercentage")
    pos_n = sum(1 for x in chg if x > 0)
    neg_n = sum(1 for x in chg if x < 0)
    desc = sorted(chg, reverse=True)
    top5 = desc[:5]
    bottom5 = sorted(chg)[:5]
    cats = _categories(uni)
    prev_uni = (
        _sector_universe(prev_t14 or [], prev_t15 or [])
        if (prev_t14 is not None or prev_t15 is not None)
        else None
    )
    prev_cats = _categories(prev_uni) if prev_uni is not None else None
    prev_rank = {c: i for i, c in enumerate(prev_cats or [])}
    now_rank = {c: i for i, c in enumerate(cats)}
    turnover = None
    if prev_cats is not None and cats:
        changed = sum(1 for c in cats if prev_rank.get(c) != now_rank.get(c))
        turnover = changed / float(len(cats))
    top5_now = cats[:5]
    top5_prev = (prev_cats or [])[:5] if prev_cats is not None else None
    return {
        "universe": "ONE_33_SECTOR_UNIVERSE",
        "not": "TWO_INDEPENDENT_BREADTH_UNIVERSES",
        "n": len(uni),
        "sector_positive_n": pos_n,
        "sector_negative_n": neg_n,
        "sector_median_change": _median(chg),
        "top5_sector_median": _median(top5),
        "bottom5_sector_median": _median(bottom5),
        "sector_dispersion": _stdev(chg),
        "sector_leadership_persistence": _overlap_rate(top5_now, top5_prev),
        "sector_rank_turnover": turnover,
        "type14_n": len(t14),
        "type15_n": len(t15),
        "full_list_mean_diff_is_not_a_breadth_metric": True,
    }


def derive_snapshot(
    *,
    by_type: dict[int, Any],
    prev_by_type: Optional[dict[int, Any]] = None,
) -> dict[str, Any]:
    """Leadership / surge / sector state. Not ADVANCE_DECLINE_RATIO. Raw means are not primary."""
    t1 = _items(by_type.get(1))
    t2 = _items(by_type.get(2))
    t5 = _items(by_type.get(5))
    t6 = _items(by_type.get(6))
    t7 = _items(by_type.get(7))
    t14 = _items(by_type.get(14))
    t15 = _items(by_type.get(15))
    prev = prev_by_type
    p1 = _items(prev.get(1)) if prev is not None and 1 in prev else None
    p2 = _items(prev.get(2)) if prev is not None and 2 in prev else None
    p6 = _items(prev.get(6)) if prev is not None and 6 in prev else None
    p7 = _items(prev.get(7)) if prev is not None and 7 in prev else None
    p14 = _items(prev.get(14)) if prev is not None and 14 in prev else None
    p15 = _items(prev.get(15)) if prev is not None and 15 in prev else None

    up = _leader_side(t1, prev_items=p1)
    down = _leader_side(t2, prev_items=p2)
    vol = _surge_block(t6, surge_key="RapidTradePercentage", prev_items=p6)
    val = _surge_block(t7, surge_key="RapidPaymentPercentage", prev_items=p7)
    tick = _tick_block(t5)
    sector = _sector_block(t14, t15, prev_t14=p14, prev_t15=p15)

    up_med = up.get("median_change")
    down_med = down.get("median_change")
    leadership_direction = None
    if up_med is not None and down_med is not None:
        if up_med > abs(down_med):
            leadership_direction = 1
        elif abs(down_med) > up_med:
            leadership_direction = -1
        else:
            leadership_direction = 0
    sec_med = sector.get("sector_median_change")
    sector_direction = None if sec_med is None else (1 if sec_med > 0 else (-1 if sec_med < 0 else 0))
    new_up = up.get("new_n")
    new_down = down.get("new_n")
    turnover = None
    if new_up is not None and new_down is not None:
        denom = max(1, int(up.get("equity_n") or 0) + int(down.get("equity_n") or 0))
        turnover = (int(new_up) + int(new_down)) / float(denom)

    raw_type6_mean = _mean(_vals(t6, "RapidTradePercentage"))
    raw_type7_mean = _mean(_vals(t7, "RapidPaymentPercentage"))

    primary = {
        "UP_LEADER_MEDIAN_CHANGE": up.get("median_change"),
        "DOWN_LEADER_MEDIAN_CHANGE": down.get("median_change"),
        "UP_LEADER_TOP10_MEDIAN": up.get("top10_median"),
        "DOWN_LEADER_TOP10_MEDIAN": down.get("top10_median"),
        "UP_LEADER_EQUITY_N": up.get("equity_n"),
        "DOWN_LEADER_EQUITY_N": down.get("equity_n"),
        "NEW_UP_LEADER_N": new_up,
        "NEW_DOWN_LEADER_N": new_down,
        "TICK_DIRECTION_PRESSURE_SUM": tick.get("sum_up_minus_down"),
        "TICK_NORM_MEDIAN": tick.get("TICK_NORM_MEDIAN"),
        "VOLUME_SURGE_STATE": vol,
        "VALUE_SURGE_STATE": val,
        "SECTOR": sector,
        "LEADERSHIP_DIRECTION": leadership_direction,
        "LEADERSHIP_STRENGTH": up.get("median_change"),
        "TICK_PRESSURE": tick.get("TICK_NORM_MEDIAN"),
        "SECTOR_DIRECTION": sector_direction,
        "SECTOR_DISPERSION": sector.get("sector_dispersion"),
        "LEADERSHIP_TURNOVER": turnover,
    }
    for k in FORBIDDEN_PRIMARY_KEYS:
        assert k not in primary
    return {
        "kind": "LEADERSHIP_SURGE_SECTOR_STATE",
        "view": "EQUITY_ONLY",
        "not": "ADVANCE_DECLINE_RATIO",
        "UP_LEADER": up,
        "DOWN_LEADER": down,
        "TICK": tick,
        **primary,
        "nonprimary_raw": {
            "primary": False,
            "forbidden_as_feature": True,
            "TYPE6_RAW_MEAN": raw_type6_mean,
            "TYPE7_RAW_MEAN": raw_type7_mean,
            "reason": "ETF/ETN extreme RapidTrade/RapidPayment percent tails; Type14/15 full-list mean diff is not breadth",
        },
    }


assert FORBIDDEN_PRIMARY_KEYS == (
    "VOLUME_SURGE_BREADTH",
    "VALUE_SURGE_BREADTH",
    "SECTOR_BREADTH_ASYMMETRY",
)
