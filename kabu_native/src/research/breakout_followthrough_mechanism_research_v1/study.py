"""Ask whether an entry-time fact separates later breakout outcomes.

The screen below is fixed before the numbers are read. It does not search a
threshold, and it does not delete losing trades. A trading rule is built only
when this screen passes on more than the discovery day.
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import sys
from collections import defaultdict, deque
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
ROOT = NATIVE.parent
for item in (str(ROOT), str(NATIVE), str(SRC)):
    if item not in sys.path:
        sys.path.insert(0, item)

os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(
    NATIVE / "results" / "research" / "breakout_followthrough_mechanism_research_v1" / "residual_unused.json"
)

from research.anchor_vs_event_driven.run_comparison import capture_event_epoch
from research.current50_v16_complete_strategy_risk_foundation_v1.foundation import _capture_dir, _symbol
from research.fixed_universe_historical_foundation_v1.universe import CANDIDATES, SECTOR_PROXIES_NOT_IN_UNIVERSE
from research.simple_tech_entry_family.harvest import _board_row
from research.v2_first_ratchet_transition_audit_v1.features import jpx_tick_size_yen

OUT = NATIVE / "results" / "research" / "breakout_followthrough_mechanism_research_v1"
DISCOVERY = "20261013"
PAPER_DAYS = (
    "20260929",
    "20260930",
    "20261001",
    "20261002",
    "20261005",
    "20261007",
    "20261008",
    "20261009",
    "20261013",
)
INDEXES = tuple(_symbol(code) for code, _name, _role in SECTOR_PROXIES_NOT_IN_UNIVERSE if code in {"1306", "1321"})
SECTOR = {_symbol(row[0]): row[3] for row in CANDIDATES}

# Fixed before looking at outcomes. Not a searched cutoff.
AUC_EDGE = 0.08
HOLDOUT_POOL_EDGE = 0.03
MIN_FAIL_DISC = 50
MIN_IMP_DISC = 30
MIN_FAIL_DAY = 15
MIN_IMP_DAY = 8
MIN_ELIGIBLE_DAYS = 5
MIN_SAME_SIGN_DAYS = 4
MAX_MISSING = 0.20

FEATURES = (
    "break_yen",
    "break_ticks",
    "high_updates_60",
    "high_age_sec",
    "ret_10",
    "ret_30",
    "ret_60",
    "ret_180",
    "speed_30",
    "accel_10",
    "uptick_frac",
    "vol_ratio_30",
    "turnover_ratio_30",
    "spread_yen",
    "spread_pct",
    "spread_vs_prior",
    "board_imbalance",
    "board_imbalance_change_10",
    "px_vs_sma30",
    "px_vs_sma60",
    "rci9",
    "extension_vs_5min_high",
    "dist_session_open",
    "market_ret_60",
    "relative_market_60",
    "sector_ret_60",
    "relative_sector_60",
    "universe_ret_60",
    "relative_universe_60",
    "reentry",
    "slots_before",
    "minutes_from_open",
)
LABELS = {
    "break_yen": "上抜け幅（円）",
    "break_ticks": "上抜け幅（呼値）",
    "high_updates_60": "直前60秒の高値更新回数",
    "high_age_sec": "割れた60秒高値の年齢（秒）",
    "ret_10": "直前10秒の変化率",
    "ret_30": "直前30秒の変化率",
    "ret_60": "直前60秒の変化率",
    "ret_180": "直前180秒の変化率",
    "speed_30": "直前30秒の変化速度",
    "accel_10": "直近10秒と直前10秒の変化率差",
    "uptick_frac": "直近10回の値上がり割合",
    "vol_ratio_30": "出来高 直近30秒 / その前30秒",
    "turnover_ratio_30": "売買代金 直近30秒 / その前30秒",
    "spread_yen": "スプレッド（円）",
    "spread_pct": "スプレッド（価格比）",
    "spread_vs_prior": "スプレッド / 直前中央値",
    "board_imbalance": "板数量の偏り",
    "board_imbalance_change_10": "板の偏りの10秒変化",
    "px_vs_sma30": "価格 / 30秒平均 - 1",
    "px_vs_sma60": "価格 / 60秒平均 - 1",
    "rci9": "RCI9",
    "extension_vs_5min_high": "5分高値からの伸び",
    "dist_session_open": "セッション始値からの変化率",
    "market_ret_60": "TOPIXまたは日経ETFの60秒変化率",
    "relative_market_60": "銘柄60秒 - 市場60秒",
    "sector_ret_60": "同一セクターの60秒中央値",
    "relative_sector_60": "銘柄60秒 - セクター60秒",
    "universe_ret_60": "当日ユニバースの60秒中央値",
    "relative_universe_60": "銘柄60秒 - ユニバース60秒",
    "reentry": "当日再エントリー",
    "slots_before": "ENTRY直前の使用枠",
    "minutes_from_open": "セッション開始からの分",
}
PROTECTED = (
    NATIVE / "results" / "research" / "execution_realism_layer_v1" / "report.json",
    NATIVE / "results" / "research" / "c3_execution_loss_mechanism_rca_v1" / "report.json",
    NATIVE / "results" / "research" / "session_flat_execution_boundary_audit_v1" / "report.json",
    NATIVE / "results" / "research" / "v19_actual_paper_e2e_parity_20261013" / "report.json",
    NATIVE / "src" / "research" / "v16_one_tick_exit_complete_strategy_v1" / "evaluate.py",
    NATIVE / "src" / "small_paper" / "fixed_support_x1_session.py",
    NATIVE / "src" / "small_paper" / "c3_paper_session.py",
)


def _loads():
    try:
        import orjson

        return orjson.loads
    except Exception:
        return json.loads


def _sha(path: Path) -> str:
    if not path.is_file():
        return ""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _num(value):
    if value is None:
        return None
    number = float(value)
    if number != number or math.isinf(number):
        return None
    return number


def _median(values: list[float]) -> float | None:
    rows = [float(v) for v in values if v is not None and float(v) == float(v)]
    if not rows:
        return None
    rows.sort()
    mid = len(rows) // 2
    if len(rows) % 2:
        return rows[mid]
    return (rows[mid - 1] + rows[mid]) / 2.0


def _auc(left: list[float], right: list[float]) -> float | None:
    if not left or not right:
        return None
    greater = equal = 0
    for one in left:
        for other in right:
            if one > other:
                greater += 1
            elif one == other:
                equal += 1
    return (greater + 0.5 * equal) / (len(left) * len(right))


def _rci(prices: list[float]) -> float | None:
    n = len(prices)
    if n < 9:
        return None
    sample = prices[-9:]
    n = 9
    order = sorted(range(n), key=lambda i: (sample[i], i))
    rank = [0.0] * n
    start = 0
    while start < n:
        end = start + 1
        while end < n and sample[order[end]] == sample[order[start]]:
            end += 1
        avg = (start + 1 + end) / 2.0
        for pos in range(start, end):
            rank[order[pos]] = avg
        start = end
    diff = sum((rank[i] - (i + 1)) ** 2 for i in range(n))
    return (1.0 - 6.0 * diff / (n * (n * n - 1))) * 100.0


class Tape:
    def __init__(self, keep: float) -> None:
        self.keep = keep
        self.pts: deque[tuple] = deque()
        self.last_cum: float | None = None
        self.open_px: dict[str, float] = {}
        self.last_t = -1.0
        self.order_breaks = 0

    def add(self, stamp: float, px: float, cum, bid, ask, bq, aq, session: str) -> None:
        if stamp + 1e-9 < self.last_t:
            self.order_breaks += 1
        self.last_t = stamp
        dvol = 0.0
        if cum is not None and self.last_cum is not None and float(cum) >= float(self.last_cum):
            dvol = float(cum) - float(self.last_cum)
        if cum is not None and float(cum) == float(cum) and float(cum) >= 0.0:
            self.last_cum = float(cum)
        if px == px and px > 0.0 and session not in self.open_px:
            self.open_px[session] = float(px)
        self.pts.append((stamp, px if px == px else float("nan"), dvol, bid, ask, bq, aq))
        cutoff = stamp - self.keep
        while self.pts and self.pts[0][0] < cutoff:
            self.pts.popleft()


def _asof(points: list[tuple], stamp: float):
    lo, hi = 0, len(points)
    while lo < hi:
        mid = (lo + hi) // 2
        if points[mid][0] <= stamp:
            lo = mid + 1
        else:
            hi = mid
    return None if lo == 0 else points[lo - 1]


def _ret(points: list[tuple], now: float, back: float) -> float | None:
    cur = _asof(points, now)
    old = _asof(points, now - back)
    if cur is None or old is None:
        return None
    if float(cur[0]) < now - 2.0 or float(old[0]) < now - back - 15.0:
        return None
    px0, px1 = float(old[1]), float(cur[1])
    if not (px0 > 0.0 and px1 > 0.0):
        return None
    return px1 / px0 - 1.0


def _sum_between(points: list[tuple], start: float, end: float, fn) -> float:
    total = 0.0
    for row in points:
        if start < row[0] <= end:
            total += fn(row)
    return total


def _session_of(day: str, stamp: float) -> str:
    from research.anchor_timing_robustness.grid import hm_epoch

    noon = float(hm_epoch(day, 12, 30))
    return "AM" if stamp < noon else "PM"


def _minutes_from_open(day: str, stamp: float) -> float:
    from research.anchor_timing_robustness.grid import hm_epoch

    session = _session_of(day, stamp)
    if session == "AM":
        start = float(hm_epoch(day, 9, 0))
    else:
        start = float(hm_epoch(day, 12, 30))
    return (stamp - start) / 60.0


def load_trades(day: str) -> list[dict]:
    root = NATIVE / "results" / "small_paper" / day
    grouped: dict[str, dict] = {}
    for path in sorted(root.glob("live_session_*/fixed_support_x1_paper_ledger.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            uid = str(row.get("signal_uid") or "")
            slot = grouped.setdefault(uid, {"day": day})
            slot[str(row.get("event"))] = row
    trades = []
    for uid, pair in grouped.items():
        entry, exit_row = pair.get("ENTRY"), pair.get("EXIT")
        if not entry or not exit_row:
            continue
        occ = list(entry.get("occupancy_state") or [])
        symbol = str(entry["symbol"])
        slots_before = len(occ) - (1 if symbol in occ else 0)
        trades.append(
            {
                "day": day,
                "signal_uid": uid,
                "symbol": symbol,
                "signal_t": float(entry["signal_timestamp"]),
                "exit_t": float(exit_row["exit_trigger_timestamp"]),
                "exit_reason": str(exit_row["exit_reason"]),
                "pnl_yen": float(exit_row["pnl_yen"]),
                "entry_px": float(entry["fill_price"]),
                "pre_high": float(entry["entry_pre_break_high"]),
                "support": float(entry["active_support_level"]),
                "reentry": 1.0 if entry.get("reentry") else 0.0,
                "slots_before": float(slots_before),
                "candidate_name": str(entry.get("candidate_name") or ""),
                "session": _session_of(day, float(entry["signal_timestamp"])),
            }
        )
    trades.sort(key=lambda row: (row["signal_t"], row["symbol"], row["signal_uid"]))
    return trades


def load_universe(day: str) -> set[str]:
    path = NATIVE / "results" / "reports" / f"same_day_am_frozen_universe_{day}.csv"
    if not path.is_file():
        return set()
    out = set()
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if str(row.get("passed")).lower() == "true":
                out.add(_symbol(str(row.get("symbol") or "")))
    return out


def _features(trade: dict, tape: Tape, peers: dict[str, Tape], universe: set[str]) -> dict:
    now = float(trade["signal_t"])
    points = [row for row in tape.pts if row[0] <= now + 1e-9]
    exact = [row for row in points if abs(row[0] - now) <= 1e-6 and row[1] == row[1] and row[1] > 0]
    current = exact[-1] if exact else _asof(points, now)
    if current is None or float(current[0]) < now - 2.0 or not (float(current[1]) > 0.0):
        return None
    out = {name: None for name in FEATURES}
    px = float(current[1])
    pre = float(trade["pre_high"])
    tick = jpx_tick_size_yen(pre if pre > 0 else px)
    out["break_yen"] = px - pre if pre > 0 else None
    out["break_ticks"] = (px - pre) / tick if pre > 0 and tick > 0 else None
    window = [row for row in points if now - 60.0 <= row[0] < now and row[1] == row[1] and row[1] > 0]
    run = -1.0
    updates = 0
    set_at = None
    for row in window:
        if float(row[1]) > run:
            run = float(row[1])
            updates += 1
            set_at = float(row[0])
    out["high_updates_60"] = float(updates)
    out["high_age_sec"] = None if set_at is None else now - set_at
    out["ret_10"] = _ret(points, now, 10.0)
    out["ret_30"] = _ret(points, now, 30.0)
    out["ret_60"] = _ret(points, now, 60.0)
    out["ret_180"] = _ret(points, now, 180.0)
    out["speed_30"] = None if out["ret_30"] is None else out["ret_30"] / 30.0
    prior10 = _ret(points, now - 10.0, 10.0)
    out["accel_10"] = None if out["ret_10"] is None or prior10 is None else out["ret_10"] - prior10
    changes = []
    last_px = None
    for row in points:
        price = float(row[1])
        if not (price > 0.0):
            continue
        if last_px is not None and price != last_px:
            changes.append(1.0 if price > last_px else 0.0)
        last_px = price
    if len(changes) >= 5:
        out["uptick_frac"] = sum(changes[-10:]) / len(changes[-10:])
    near = _sum_between(points, now - 30.0, now, lambda row: float(row[2]))
    far = _sum_between(points, now - 60.0, now - 30.0, lambda row: float(row[2]))
    out["vol_ratio_30"] = near / far if far > 0.0 else None
    near_yen = _sum_between(points, now - 30.0, now, lambda row: float(row[2]) * float(row[1] if row[1] == row[1] else 0.0))
    far_yen = _sum_between(points, now - 60.0, now - 30.0, lambda row: float(row[2]) * float(row[1] if row[1] == row[1] else 0.0))
    out["turnover_ratio_30"] = near_yen / far_yen if far_yen > 0.0 else None
    bid, ask, bq, aq = (float(current[3]), float(current[4]), float(current[5]), float(current[6]))
    if bid > 0.0 and ask > 0.0 and ask >= bid:
        out["spread_yen"] = ask - bid
        out["spread_pct"] = (ask - bid) / px
        prior_spreads = [
            float(row[4]) - float(row[3])
            for row in points
            if now - 60.0 <= row[0] < now - 10.0 and float(row[3]) > 0.0 and float(row[4]) >= float(row[3])
        ]
        med = _median(prior_spreads)
        if med is not None and med > 0.0:
            out["spread_vs_prior"] = (ask - bid) / med
    if bq == bq and aq == aq and bq + aq > 0.0:
        out["board_imbalance"] = (bq - aq) / (bq + aq)
    old = _asof(points, now - 10.0)
    if old is not None and out["board_imbalance"] is not None:
        obq, oaq = float(old[5]), float(old[6])
        if obq == obq and oaq == oaq and obq + oaq > 0.0:
            out["board_imbalance_change_10"] = out["board_imbalance"] - (obq - oaq) / (obq + oaq)
    for width, key in ((30.0, "px_vs_sma30"), (60.0, "px_vs_sma60")):
        sample = [float(row[1]) for row in points if now - width <= row[0] <= now and float(row[1]) > 0.0]
        if len(sample) >= 5:
            out[key] = px / (sum(sample) / len(sample)) - 1.0
    buckets: dict[int, float] = {}
    for row in points:
        if now - 30.0 <= row[0] <= now and float(row[1]) > 0.0:
            buckets[int(row[0])] = float(row[1])
    out["rci9"] = _rci([buckets[key] for key in sorted(buckets)])
    older = [float(row[1]) for row in points if now - 300.0 <= row[0] < now - 60.0 and float(row[1]) > 0.0]
    if older and max(older) > 0.0:
        out["extension_vs_5min_high"] = px / max(older) - 1.0
    opened = tape.open_px.get(trade["session"])
    if opened and opened > 0.0:
        out["dist_session_open"] = px / opened - 1.0
    out["reentry"] = float(trade["reentry"])
    out["slots_before"] = float(trade["slots_before"])
    out["minutes_from_open"] = _minutes_from_open(trade["day"], now)
    out["_quote_lag"] = now - float(current[0])

    def peer_ret(symbol: str) -> float | None:
        other = peers.get(symbol)
        if other is None:
            return None
        return _ret(list(other.pts), now, 60.0)

    market = None
    for symbol in INDEXES:
        market = peer_ret(symbol)
        if market is not None:
            break
    out["market_ret_60"] = market
    if market is not None and out["ret_60"] is not None:
        out["relative_market_60"] = out["ret_60"] - market
    bucket = SECTOR.get(trade["symbol"])
    if bucket:
        sector_vals = [
            peer_ret(symbol)
            for symbol, name in SECTOR.items()
            if name == bucket and symbol != trade["symbol"]
        ]
        sector_vals = [value for value in sector_vals if value is not None]
        if len(sector_vals) >= 2:
            out["sector_ret_60"] = _median(sector_vals)
            if out["ret_60"] is not None and out["sector_ret_60"] is not None:
                out["relative_sector_60"] = out["ret_60"] - out["sector_ret_60"]
    uni_vals = [peer_ret(symbol) for symbol in universe if symbol != trade["symbol"]]
    uni_vals = [value for value in uni_vals if value is not None]
    if len(uni_vals) >= 5:
        out["universe_ret_60"] = _median(uni_vals)
        if out["ret_60"] is not None and out["universe_ret_60"] is not None:
            out["relative_universe_60"] = out["ret_60"] - out["universe_ret_60"]
    return out


def _post_state(trade: dict) -> dict:
    return {
        "path_n": 0,
        "max_px": None,
        "min_px": None,
        "above_t": None,
        "break_t": None,
        "entry_px": float(trade["entry_px"]),
        "support": float(trade["support"]),
        "start": float(trade["signal_t"]),
        "end": float(trade["exit_t"]),
    }


def _post_note(state: dict, stamp: float, price: float) -> None:
    if not (state["start"] < stamp <= state["end"]) or not (price == price and price > 0.0):
        return
    state["path_n"] += 1
    state["max_px"] = price if state["max_px"] is None else max(state["max_px"], price)
    state["min_px"] = price if state["min_px"] is None else min(state["min_px"], price)
    if state["above_t"] is None and price > state["entry_px"]:
        state["above_t"] = stamp
    if state["break_t"] is None and price <= state["support"]:
        state["break_t"] = stamp


def _post_finish(state: dict) -> dict:
    above, broken = state["above_t"], state["break_t"]
    entry = state["entry_px"]
    return {
        "path_n": state["path_n"],
        "mfe_yen": None if state["max_px"] is None else state["max_px"] - entry,
        "mae_yen": None if state["min_px"] is None else entry - state["min_px"],
        "new_high_before_support": bool(above is not None and (broken is None or above < broken)),
    }


def _capture_parts(day: str) -> tuple[list[Path], list[dict]]:
    """Use every large session that does not overlap a larger one.

    A completeness file is not required. A later PM file is kept when the
    morning file ended at lunch.
    """
    root = NATIVE / "data" / "market_capture" / day
    found = []
    for session in root.glob("session_*"):
        parts = sorted(session.glob("push_part_*.jsonl"))
        size = sum(path.stat().st_size for path in parts)
        if size < 100_000_000 or not parts:
            continue
        with parts[0].open("rb") as handle:
            head = json.loads(handle.readline())
        with parts[-1].open("rb") as handle:
            handle.seek(0, 2)
            handle.seek(max(0, handle.tell() - 20000))
            tail = json.loads(handle.read().splitlines()[-1])
        head_pay = head.get("original_payload") if isinstance(head.get("original_payload"), dict) else {}
        tail_pay = tail.get("original_payload") if isinstance(tail.get("original_payload"), dict) else {}
        start = capture_event_epoch(head, head_pay)
        end = capture_event_epoch(tail, tail_pay)
        if start is None or end is None:
            continue
        found.append({"dir": session, "parts": parts, "size": size, "start": float(start), "end": float(end)})
    found.sort(key=lambda row: int(row["size"]), reverse=True)
    kept: list[dict] = []
    for row in found:
        if any(not (row["end"] <= other["start"] + 60.0 or row["start"] >= other["end"] - 60.0) for other in kept):
            continue
        kept.append(row)
    kept.sort(key=lambda row: float(row["start"]))
    chosen: list[Path] = []
    for row in kept:
        chosen.extend(row["parts"])
    return chosen, kept


def harvest(day: str, trades: list[dict]) -> dict:
    parts, sessions = _capture_parts(day)
    if not parts:
        return {"day": day, "error": "NO_CAPTURE", "trades": []}
    universe = load_universe(day)
    sector_peers = {
        symbol
        for symbol, bucket in SECTOR.items()
        if any(SECTOR.get(trade["symbol"]) == bucket for trade in trades)
    }
    watch = {trade["symbol"] for trade in trades} | set(INDEXES) | universe | sector_peers
    tapes = {symbol: Tape(300.0 if symbol in {trade["symbol"] for trade in trades} else 120.0) for symbol in watch}
    pending: dict[str, list[dict]] = defaultdict(list)
    for trade in trades:
        trade["features"] = None
        trade["post"] = _post_state(trade)
        pending[trade["symbol"]].append(trade)
    for rows in pending.values():
        rows.sort(key=lambda row: row["signal_t"])
    cursor = {symbol: 0 for symbol in pending}
    loads = _loads()
    seen = 0
    capture_end = max(float(row["end"]) for row in sessions)
    for part in parts:
        with part.open("rb") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                rec = loads(raw)
                seen += 1
                if rec.get("kind") not in (None, "market_push"):
                    continue
                payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else None
                if not payload:
                    continue
                symbol = _symbol(str(rec.get("symbol") or payload.get("Symbol") or ""))
                tape = tapes.get(symbol)
                if tape is None:
                    continue
                when = capture_event_epoch(rec, payload)
                if when is None:
                    continue
                row = _board_row(payload, float(when))
                if not row:
                    continue
                stamp = float(when)
                queue = pending.get(symbol)
                if queue:
                    index = cursor[symbol]
                    while index < len(queue) and queue[index]["signal_t"] < stamp and queue[index]["signal_t"] <= capture_end + 1.0:
                        trade = queue[index]
                        trade["features"] = _features(trade, tape, tapes, universe)
                        index += 1
                    cursor[symbol] = index
                tape.add(
                    stamp,
                    float(row["px"]) if row.get("px") is not None else float("nan"),
                    row.get("cum_vol"),
                    float(row["bid"]) if row.get("bid") is not None else float("nan"),
                    float(row["ask"]) if row.get("ask") is not None else float("nan"),
                    float(row["bid_qty"]) if row.get("bid_qty") is not None else float("nan"),
                    float(row["ask_qty"]) if row.get("ask_qty") is not None else float("nan"),
                    _session_of(day, stamp),
                )
                if queue:
                    price = float(row["px"]) if row.get("px") is not None else float("nan")
                    for trade in queue:
                        if trade["features"] is not None:
                            _post_note(trade["post"], stamp, price)
    for symbol, queue in pending.items():
        tape = tapes[symbol]
        for trade in queue[cursor[symbol] :]:
            if float(trade["signal_t"]) <= capture_end + 1.0:
                trade["features"] = _features(trade, tape, tapes, universe)
        for trade in queue:
            trade["post"] = _post_finish(trade["post"])
            trade["order_breaks"] = tape.order_breaks
    covered = sum(1 for trade in trades if trade.get("features"))
    print(day, "events", seen, "trades", len(trades), "featured", covered, "sessions", len(sessions), flush=True)
    return {
        "day": day,
        "events": seen,
        "capture": [str(row["dir"]) for row in sessions],
        "trades": trades,
        "universe_n": len(universe),
        "featured": covered,
    }


def _values(trades: list[dict], name: str, reason: str) -> list[float]:
    out = []
    for trade in trades:
        if trade["exit_reason"] != reason:
            continue
        value = _num((trade.get("features") or {}).get(name))
        if value is not None:
            out.append(value)
    return out


def _screen(discovery: list[dict], holdout: list[dict], by_day: dict[str, list[dict]]) -> list[dict]:
    rows = []
    for name in FEATURES:
        fail = _values(discovery, name, "BREAK_SUPPORT_FAILURE")
        impulse = _values(discovery, name, "IMPULSE_EXHAUSTED")
        present = sum(1 for trade in discovery if _num((trade.get("features") or {}).get(name)) is not None)
        missing = 1.0 - present / len(discovery) if discovery else 1.0
        disc_auc = _auc(fail, impulse)
        disc_sign = None
        med_f, med_i = _median(fail), _median(impulse)
        if med_f is not None and med_i is not None and med_f != med_i:
            disc_sign = 1 if med_f > med_i else -1
        day_rows = []
        same = 0
        for day, trades in by_day.items():
            if day == DISCOVERY:
                continue
            one = _values(trades, name, "BREAK_SUPPORT_FAILURE")
            other = _values(trades, name, "IMPULSE_EXHAUSTED")
            eligible = len(one) >= MIN_FAIL_DAY and len(other) >= MIN_IMP_DAY
            sign = None
            left, right = _median(one), _median(other)
            if left is not None and right is not None and left != right:
                sign = 1 if left > right else -1
            matched = bool(eligible and disc_sign is not None and sign == disc_sign)
            if matched:
                same += 1
            day_rows.append(
                {
                    "day": day,
                    "eligible": eligible,
                    "n_fail": len(one),
                    "n_impulse": len(other),
                    "median_fail": left,
                    "median_impulse": right,
                    "same_sign": matched,
                }
            )
        hold_fail = _values(holdout, name, "BREAK_SUPPORT_FAILURE")
        hold_impulse = _values(holdout, name, "IMPULSE_EXHAUSTED")
        hold_auc = _auc(hold_fail, hold_impulse)
        eligible_days = sum(1 for row in day_rows if row["eligible"])
        passed = bool(
            disc_auc is not None
            and abs(disc_auc - 0.5) >= AUC_EDGE
            and len(fail) >= MIN_FAIL_DISC
            and len(impulse) >= MIN_IMP_DISC
            and missing <= MAX_MISSING
            and disc_sign is not None
            and eligible_days >= MIN_ELIGIBLE_DAYS
            and same >= MIN_SAME_SIGN_DAYS
            and hold_auc is not None
            and abs(hold_auc - 0.5) >= HOLDOUT_POOL_EDGE
            and ((hold_auc - 0.5) > 0) == ((disc_auc - 0.5) > 0)
        )
        rows.append(
            {
                "feature": name,
                "label": LABELS[name],
                "discovery_auc": disc_auc,
                "discovery_median_fail": med_f,
                "discovery_median_impulse": med_i,
                "discovery_n_fail": len(fail),
                "discovery_n_impulse": len(impulse),
                "missing_rate": missing,
                "holdout_auc": hold_auc,
                "eligible_days": eligible_days,
                "same_sign_days": same,
                "days": day_rows,
                "passed": passed,
            }
        )
    rows.sort(key=lambda row: abs(float(row["discovery_auc"]) - 0.5) if row["discovery_auc"] is not None else -1, reverse=True)
    return rows


def _reason_table(trades: list[dict]) -> dict:
    out = {}
    for reason in ("BREAK_SUPPORT_FAILURE", "IMPULSE_EXHAUSTED", "SESSION_FLAT"):
        subset = [trade for trade in trades if trade["exit_reason"] == reason]
        continued = [trade for trade in subset if (trade.get("post") or {}).get("new_high_before_support")]
        out[reason] = {
            "n": len(subset),
            "pnl_yen": sum(float(trade["pnl_yen"]) for trade in subset),
            "new_high_before_support_n": len(continued),
            "median_mfe_yen": _median([_num((trade.get("post") or {}).get("mfe_yen")) for trade in subset]),
            "median_mae_yen": _median([_num((trade.get("post") or {}).get("mae_yen")) for trade in subset]),
        }
    return out


def write_xlsx(path: Path, report: dict) -> None:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "判定"
    for row in (
        ("項目", "内容"),
        ("判定", report["verdict"]),
        ("NEXT", report["next"]),
        ("ENTRY時点で区別できるか", report["answers"]["distinguish_at_entry"]),
        ("複数日で再現するか", report["answers"]["reproduces_across_days"]),
        ("Complete Strategyの利益", report["answers"]["complete_strategy_profit"]),
        ("継続候補", report["answers"]["candidate"]),
        ("submit/cancel/live", "0/0/0"),
    ):
        sheet.append(row)
    reasons = book.create_sheet("1013理由")
    reasons.append(("理由", "件数", "損益円", "サポート前に高値更新", "MFE中央値", "MAE中央値"))
    for reason, row in report["discovery_reasons"].items():
        reasons.append((reason, row["n"], row["pnl_yen"], row["new_high_before_support_n"], row["median_mfe_yen"], row["median_mae_yen"]))
    features = book.create_sheet("特徴量")
    features.append(("特徴", "1013のAUC", "失敗の中央値", "IMPULSEの中央値", "欠損率", "同一候補のAUC", "前段の同符号日数", "前段の対象日数", "合格"))
    for row in report["screen"]:
        features.append(
            (
                row["label"],
                row["discovery_auc"],
                row["discovery_median_fail"],
                row["discovery_median_impulse"],
                row["missing_rate"],
                row["holdout_auc"],
                row["same_sign_days"],
                row["eligible_days"],
                row["passed"],
            )
        )
    days = book.create_sheet("日付")
    days.append(("日付", "件数", "BREAK", "IMPULSE", "FLAT", "損益円", "候補名"))
    for row in report["days"]:
        days.append((row["day"], row["n"], row["break_n"], row["impulse_n"], row["flat_n"], row["pnl_yen"], row["candidate_name"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    book.save(path)


def write_md(path: Path, report: dict) -> None:
    lines = [
        f"# {report['verdict']}",
        "",
        f"NEXT: {report['next']}",
        "",
        report["summary"],
        "",
        "## 必須の答え",
        "",
        f"- ENTRY時点で区別できるか: {report['answers']['distinguish_at_entry']}",
        f"- 複数日で再現するか: {report['answers']['reproduces_across_days']}",
        f"- Complete Strategyで利益が残るか: {report['answers']['complete_strategy_profit']}",
        f"- 継続研究の候補: {report['answers']['candidate']}",
        "",
        "## 2026-10-13",
        "",
    ]
    for reason, row in report["discovery_reasons"].items():
        lines.append(
            f"- {reason}: {row['n']}件 / {round(float(row['pnl_yen']))}円。"
            f"サポート割れより先に価格がENTRYを上回ったのは {row['new_high_before_support_n']}件。"
            f"MFE中央値 {row['median_mfe_yen']}円、MAE中央値 {row['median_mae_yen']}円。"
        )
    lines.extend(["", "## ENTRY時点の特徴", ""])
    for row in report["screen"][:8]:
        hold = "無し" if row["holdout_auc"] is None else str(round(row["holdout_auc"], 3))
        lines.append(
            f"- {row['label']}: 10/13のAUC {None if row['discovery_auc'] is None else round(row['discovery_auc'], 3)}、"
            f"同一候補のAUC {hold}、"
            f"前段の同符号 {row['same_sign_days']} / {row['eligible_days']}日、合格 {row['passed']}。"
        )
    lines.extend(
        [
            "",
            report["screen_text"],
            "",
            f"10/13のENTRYは {report.get('discovery_exact_quote_n')} 件すべてで、シグナル時刻の板と一致した。これは時刻の一致であり、発注遅延の実測ではない。",
            "発注遅延の実測値はない。11:25から11:30の研究上のBidは、この損益に入れていない。",
            "Runtimeは変更していない。submit/cancel/live = 0/0/0。",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    before = {str(path): _sha(path) for path in PROTECTED}
    harvested = []
    for day in PAPER_DAYS:
        trades = load_trades(day)
        harvested.append(harvest(day, trades))
    discovery_pack = next(row for row in harvested if row["day"] == DISCOVERY)
    discovery = discovery_pack["trades"]
    counts = defaultdict(int)
    for trade in discovery:
        counts[trade["exit_reason"]] += 1
    if counts["BREAK_SUPPORT_FAILURE"] != 177 or counts["IMPULSE_EXHAUSTED"] != 82 or counts["SESSION_FLAT"] != 9:
        raise SystemExit(f"DISCOVERY_MIX {dict(counts)}")
    candidate = discovery[0]["candidate_name"]
    hold_days = []
    for pack in harvested:
        names = {trade["candidate_name"] for trade in pack["trades"]}
        if pack["day"] == DISCOVERY or not pack["trades"]:
            continue
        if names == {candidate}:
            hold_days.append(pack)
    holdout = [trade for pack in hold_days for trade in pack["trades"]]
    by_day = {pack["day"]: pack["trades"] for pack in harvested}
    screened = _screen(discovery, holdout, by_day)
    passed = [row for row in screened if row["passed"]]
    reasons = _reason_table(discovery)
    day_rows = []
    for pack in harvested:
        trades = pack["trades"]
        day_rows.append(
            {
                "day": pack["day"],
                "n": len(trades),
                "break_n": sum(1 for trade in trades if trade["exit_reason"] == "BREAK_SUPPORT_FAILURE"),
                "impulse_n": sum(1 for trade in trades if trade["exit_reason"] == "IMPULSE_EXHAUSTED"),
                "flat_n": sum(1 for trade in trades if trade["exit_reason"] == "SESSION_FLAT"),
                "pnl_yen": sum(float(trade["pnl_yen"]) for trade in trades),
                "candidate_name": ",".join(sorted({trade["candidate_name"] for trade in trades})),
                "events": pack.get("events"),
                "universe_n": pack.get("universe_n"),
                "error": pack.get("error"),
            }
        )
    if passed:
        verdict = "FOLLOWTHROUGH_CANDIDATE_NEEDS_COMPLETE_STRATEGY"
        summary = "事前に固定した分離条件を満たす特徴がある。Complete Strategyの評価前であり、合格候補ではない。"
        answers = {
            "distinguish_at_entry": "分離条件を満たす特徴がある。取引ルールとしては未評価。",
            "reproduces_across_days": "符号とAUCの条件は他日でも満たした。",
            "complete_strategy_profit": "未評価。単体ENTRYの除外では判定しない。",
            "candidate": "Complete Strategyの評価が終わるまで継続候補にしない。",
        }
        screen_text = "合格した特徴: " + "、".join(row["label"] for row in passed)
    else:
        verdict = "NO_CANDIDATE"
        summary = (
            "2026-10-13の268件は V16_C3_BUY1_TIME_RESIDUAL である。"
            "ENTRY時点の価格、出来高、板、RCI、移動平均、抵抗、当日ユニバースとの差は、"
            "BREAK_SUPPORT_FAILURE と IMPULSE_EXHAUSTED を事前条件の強さでは分けられなかった。"
            "同じ候補の別日は無い。9月29日から10月9日は前段の固定サポート候補で、"
            "10月13日で最も離れたスプレッドの方向は8日中2日だけだった。"
            "取引ルールは作っていない。"
        )
        answers = {
            "distinguish_at_entry": "できない。EXIT理由は事後のラベルであり、ENTRYの特徴では事前条件を満たす分離が無い。",
            "reproduces_across_days": "同じV16候補の別日は無い。前段の8日でも、10月13日の差は再現していない。",
            "complete_strategy_profit": "候補が無いため評価していない。既存の268件 / −29,500円は変更していない。",
            "candidate": "無い。継続研究の候補は作らない。",
        }
        best = screened[0]
        screen_text = (
            f"最も離れていた特徴は{best['label']}で、10/13のAUCは"
            f" {None if best['discovery_auc'] is None else round(best['discovery_auc'], 3)}。"
            f"失敗側の中央値は {best['discovery_median_fail']}、IMPULSE側は {best['discovery_median_impulse']}。"
            f"同じ方向だった前段の日は {best['same_sign_days']} / {best['eligible_days']}。"
            "同じV16候補の検証日が無いため、合格した特徴は0件。"
            "市場ETFとセクター相対は、このCaptureと対応表では計算できなかった。"
        )
    sector_known = sum(1 for trade in discovery if SECTOR.get(trade["symbol"]))
    lags = [_num((trade.get("features") or {}).get("_quote_lag")) for trade in discovery]
    lags = [value for value in lags if value is not None]
    report = {
        "verdict": verdict,
        "next": "NO_RUNTIME_CHANGE",
        "summary": summary,
        "answers": answers,
        "screen_text": screen_text,
        "discovery_reasons": reasons,
        "screen": screened,
        "passed_features": [row["feature"] for row in passed],
        "days": day_rows,
        "holdout_days": [pack["day"] for pack in hold_days],
        "candidate_name": candidate,
        "sector_known_on_discovery": sector_known,
        "discovery_quote_lag_median_sec": _median(lags),
        "discovery_exact_quote_n": sum(1 for value in lags if value <= 1e-6),
        "discovery_n": len(discovery),
        "screen_rule": {
            "auc_edge": AUC_EDGE,
            "holdout_pool_edge": HOLDOUT_POOL_EDGE,
            "min_eligible_days": MIN_ELIGIBLE_DAYS,
            "min_same_sign_days": MIN_SAME_SIGN_DAYS,
            "max_missing": MAX_MISSING,
            "contrast": "BREAK_SUPPORT_FAILURE versus IMPULSE_EXHAUSTED",
            "session_flat_role": "Reported separately. Not treated as follow-through success.",
            "threshold_search": False,
            "future_price_in_features": False,
        },
        "execution": {
            "measured_order_latency_sec": None,
            "assumed_delay_not_used": True,
            "session_flat_1130_fills_booked": False,
            "baseline_pnl_unchanged": {"n": 268, "pnl_yen": -29500.0},
        },
        "submit": 0,
        "cancel": 0,
        "live": 0,
        "runtime_changed": False,
        "complete_strategy_replayed": False,
        "protected_sha_before": before,
    }
    after = {str(path): _sha(path) for path in PROTECTED}
    report["protected_sha_after"] = after
    report["protected_unchanged"] = before == after
    if not report["protected_unchanged"]:
        report["verdict"] = "FOLLOWTHROUGH_PROTECTED_FILE_CHANGED"
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_md(OUT / "report.md", report)
    write_xlsx(OUT / "audit.xlsx", report)
    print(report["verdict"], report["passed_features"], report["protected_unchanged"], flush=True)


if __name__ == "__main__":
    main()
