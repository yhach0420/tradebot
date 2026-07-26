"""Flow delay analysis — diagnostic only, no strategy."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

from research.ueia_economic_gate_and_flow_delay.constants import COST_BPS, DELAYS_SEC
from research.upward_edge_identification_audit.constants import BARRIERS
from research.upward_edge_identification_audit.labels import label_first_passage
from research.upward_edge_identification_audit.loader import Tick, exec_entry_ok
from research.upward_edge_identification_audit.samples import Sample
from research.ueia_economic_gate_and_flow_delay.scoring import FittedModel, _score_samples


def _bps(a: float, b: float) -> float:
    return (b - a) / a * 10000.0 if a > 0 else 0.0


def _bid_return_at(ticks: Sequence[Tick], i0: int, entry_ask: float, sec: float) -> Optional[float]:
    t0 = ticks[i0].ts
    last = None
    for j in range(i0 + 1, len(ticks)):
        dt = (ticks[j].ts - t0).total_seconds()
        if dt > sec:
            break
        b = ticks[j].board.canonical_best_bid
        if b and b > 0:
            last = float(b)
    if last is None:
        return None
    return _bps(entry_ask, last)


def first_ask_at_or_after(ticks: Sequence[Tick], i_start: int, t_target: datetime) -> Optional[tuple[int, float]]:
    """First usable canonical ask at/after t_target — no future cherry-pick."""
    for j in range(i_start, len(ticks)):
        if ticks[j].ts < t_target:
            continue
        if exec_entry_ok(ticks[j]):
            return j, float(ticks[j].board.canonical_best_ask)
    return None


def estimate_flow_timestamps(
    ticks: Sequence[Tick],
    sample: Sample,
    model: FittedModel,
    scores_by_idx: dict[int, float],
) -> dict[str, Any]:
    """Causal timestamps around sample; no future for defining t_flow_start."""
    i = sample.idx
    t_sample = sample.event_time
    # t_flow_start: look back up to 30s for buy ratio rising above 0.58 with buy trades
    t_flow = None
    buy_v = sell_v = 0.0
    for j in range(i, -1, -1):
        if (t_sample - ticks[j].ts).total_seconds() > 30:
            break
        if ticks[j].volume_delta and ticks[j].volume_delta > 0:
            if ticks[j].trade_side == "BUY":
                buy_v += ticks[j].volume_delta
            elif ticks[j].trade_side == "SELL":
                sell_v += ticks[j].volume_delta
    # walk forward from i-30s to find first time buy dominant
    j0 = i
    while j0 > 0 and (t_sample - ticks[j0].ts).total_seconds() <= 30:
        j0 -= 1
    run_buy = run_sell = 0.0
    for j in range(j0, i + 1):
        if ticks[j].volume_delta and ticks[j].volume_delta > 0:
            if ticks[j].trade_side == "BUY":
                run_buy += ticks[j].volume_delta
            elif ticks[j].trade_side == "SELL":
                run_sell += ticks[j].volume_delta
        tot = run_buy + run_sell
        if tot >= 100 and run_buy / tot >= 0.58 and t_flow is None:
            t_flow = ticks[j].ts
            break
    if t_flow is None:
        t_flow = t_sample

    # t_score_cross: first index <= sample where score >= threshold (using precomputed if available)
    thr = model.fixed_threshold
    t_cross = t_sample
    if thr is not None:
        for j in range(max(0, i - 200), i + 1):
            sc = scores_by_idx.get(j)
            if sc is not None and sc >= thr:
                t_cross = ticks[j].ts
                break

    # t_price_response: first ask step-up or bid follow after t_flow
    t_resp = None
    prev_ask = prev_bid = None
    for j in range(j0, min(len(ticks), i + 50)):
        if ticks[j].ts < t_flow:
            continue
        ask = ticks[j].board.canonical_best_ask
        bid = ticks[j].board.canonical_best_bid
        if prev_ask is not None and ask is not None and ask > prev_ask:
            t_resp = ticks[j].ts
            break
        if prev_bid is not None and bid is not None and bid > prev_bid:
            t_resp = ticks[j].ts
            break
        prev_ask, prev_bid = ask, bid

    # consumed bps from t_flow-5s to t_sample (diagnostic)
    t_pre = t_flow - timedelta(seconds=5)
    ask_pre = None
    for j in range(0, i + 1):
        if ticks[j].ts >= t_pre and exec_entry_ok(ticks[j]):
            ask_pre = float(ticks[j].board.canonical_best_ask)
            break
    consumed = _bps(ask_pre, sample.entry_ask) if ask_pre else None

    return {
        "t_flow_start": t_flow,
        "t_score_cross": t_cross,
        "t_sample": t_sample,
        "t_price_response": t_resp,
        "flow_to_cross_sec": (t_cross - t_flow).total_seconds() if t_flow and t_cross else None,
        "consumed_before_sample_bps": consumed,
    }


def evaluate_delay(
    ticks: Sequence[Tick],
    sample: Sample,
    barrier: str,
    delay_sec: float,
    *,
    next_event: bool = False,
) -> Optional[dict[str, Any]]:
    i = sample.idx
    t0 = sample.event_time
    if next_event:
        if i + 1 >= len(ticks):
            return None
        hit = first_ask_at_or_after(ticks, i + 1, ticks[i + 1].ts)
    else:
        hit = first_ask_at_or_after(ticks, i, t0 + timedelta(seconds=delay_sec))
    if hit is None:
        return {"selectable": False, "delay_sec": delay_sec if not next_event else "next_event"}
    j, ask = hit
    bid = float(ticks[j].board.canonical_best_bid or ask)
    spr = ticks[j].board.canonical_spread_bps
    # price change from signal ask to delayed entry
    signal_to_entry = _bps(sample.entry_ask, ask)
    lab = label_first_passage(ticks, j, sample.sample_id + f"|d{delay_sec}", barrier, ask, bid, spr)
    return {
        "selectable": True,
        "delay_sec": delay_sec if not next_event else "next_event",
        "entry_ask": ask,
        "spread": spr,
        "signal_to_entry_bps": signal_to_entry,
        "already_consumed_bps": signal_to_entry,
        "ret_30": _bid_return_at(ticks, j, ask, 30),
        "ret_60": _bid_return_at(ticks, j, ask, 60),
        "ret_180": _bid_return_at(ticks, j, ask, 180),
        "ret_300": _bid_return_at(ticks, j, ask, 300),
        "MFE_bps": lab.MFE_bps,
        "MAE_bps": lab.MAE_bps,
        "cost_adj": lab.cost_adjusted_return_bps,
        "first_result": lab.first_result,
        "first_hit_sec": lab.first_hit_sec,
        "day": sample.day,
        "symbol": sample.symbol,
    }


def run_delay_analysis(
    selected_train: Sequence[Sample],
    streams: dict[str, list[Tick]],
    model: FittedModel,
    barrier: str,
    *,
    max_n: Optional[int] = 400,
) -> dict[str, Any]:
    sel = list(selected_train)
    if max_n is not None:
        sel = sel[:max_n]
    delays = ["next_event"] + list(DELAYS_SEC)
    bags: dict[Any, list[dict]] = {d: [] for d in delays}
    ts_rows = []
    for s in sel:
        ticks = streams.get(s.stream_key) or []
        if not ticks:
            continue
        # score map sparse: only sample idx
        scores_by_idx = {s.idx: 1.0}  # placeholder; cross uses sample time
        ts = estimate_flow_timestamps(ticks, s, model, scores_by_idx)
        ts_rows.append({"sample_id": s.sample_id, **{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in ts.items()}})
        for d in DELAYS_SEC:
            row = evaluate_delay(ticks, s, barrier, d)
            if row:
                bags[d].append(row)
        ne = evaluate_delay(ticks, s, barrier, 0.0, next_event=True)
        if ne:
            bags["next_event"].append(ne)

    def summarize(rows: list[dict]) -> dict[str, Any]:
        ok = [r for r in rows if r.get("selectable")]
        if not ok:
            return {"n": 0, "selectable_rate": 0.0}
        cadj = [r["cost_adj"] for r in ok if r.get("cost_adj") is not None]
        mfe = [r["MFE_bps"] for r in ok if r.get("MFE_bps") is not None]
        mae = [r["MAE_bps"] for r in ok if r.get("MAE_bps") is not None]
        abs_mae = [abs(x) for x in mae]
        up = sum(1 for r in ok if r.get("first_result") == "UP_FIRST")
        dn = sum(1 for r in ok if r.get("first_result") == "DOWN_FIRST")
        return {
            "n": len(ok),
            "selectable_rate": len(ok) / len(rows) if rows else 0,
            "cost_adj": sum(cadj) / len(cadj) if cadj else None,
            "mfe": sum(mfe) / len(mfe) if mfe else None,
            "mae": sum(mae) / len(mae) if mae else None,
            "mfe_mae": (sum(mfe) / len(mfe)) / (sum(abs_mae) / len(abs_mae)) if mfe and abs_mae and sum(abs_mae) else None,
            "up_rate": up / len(ok),
            "down_rate": dn / len(ok),
        }

    summary = {str(d): summarize(bags[d]) for d in delays}
    # classify
    cadj0 = (summary.get("0.0") or {}).get("cost_adj")
    cadj2 = (summary.get("2.0") or {}).get("cost_adj")
    cadj5 = (summary.get("5.0") or {}).get("cost_adj")
    causes = []
    if cadj0 is not None and cadj0 > 0 and cadj2 is not None and cadj2 <= 0:
        causes.append("EXECUTION_DELAY_SENSITIVE")
    if cadj0 is not None and cadj0 <= 0:
        causes.append("NO_POST_SIGNAL_EDGE")
    if cadj0 is not None and cadj0 > 0 and (cadj5 or 0) > 0:
        causes.append("EDGE_PERSISTS_AFTER_SIGNAL")
    consumed = [r.get("consumed_before_sample_bps") for r in ts_rows if r.get("consumed_before_sample_bps") is not None]
    mean_cons = sum(consumed) / len(consumed) if consumed else None
    if mean_cons is not None and mean_cons > 5.0:
        causes.append("EDGE_CONSUMED_BEFORE_SCORE_CROSS")
    # spread dominated heuristic: if many positive mid-ish but cost neg — approximate via signal_to_entry
    if cadj0 is not None and cadj0 <= 0:
        causes.append("SPREAD_DOMINATED")  # provisional if ask entry kills edge at 0 delay

    best_delay = None
    best_cadj = None
    longest_pos = None
    for d in DELAYS_SEC:
        c = (summary.get(str(d)) or {}).get("cost_adj")
        if c is not None and (best_cadj is None or c > best_cadj):
            best_cadj = c
            best_delay = d
        if c is not None and c > 0:
            longest_pos = d
    flow_cross = [r.get("flow_to_cross_sec") for r in ts_rows if r.get("flow_to_cross_sec") is not None]

    return {
        "n_analyzed": len(sel),
        "timestamps_sample": ts_rows[:50],
        "delay_summary": summary,
        "causes": causes,
        "best_delay": best_delay,
        "best_delay_cost_adj": best_cadj,
        "longest_positive_delay": longest_pos,
        "mean_flow_to_cross_sec": sum(flow_cross) / len(flow_cross) if flow_cross else None,
        "mean_consumed_before_sample_bps": mean_cons,
        "ENTRY_EDGE_CONSUMED": mean_cons is not None and mean_cons > COST_BPS and (cadj0 or 0) <= 0,
        "SPREAD_DOMINATED": "SPREAD_DOMINATED" in causes and (cadj0 or 0) <= 0,
        "NO_POST_SIGNAL_EDGE": (cadj0 or 0) <= 0,
    }
