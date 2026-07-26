"""R0 (current) vs R1 (canonical) differential on raw PUSH capture days."""
from __future__ import annotations

import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterator, Optional

from research.global_quote_semantic_audit.canonical import (
    board_token,
    canonical_depth_imbalance,
    normalize_kabu_board,
    r0_current_from_payload,
    r1_from_canonical,
)
from research.global_quote_semantic_audit.constants import (
    AUDIT_DAYS,
    BOARD_P33,
    BOARD_P66,
    CAPTURE_ROOT,
    SAMPLE_PER_DAY,
    TRACE_MIN,
)


def _iter_payloads(day: str, *, limit: int) -> Iterator[dict[str, Any]]:
    day_dir = CAPTURE_ROOT / day
    if not day_dir.exists():
        return
    files = sorted(day_dir.glob("push_part_*.jsonl"))
    n = 0
    step = 1
    # reservoir-style stride: count lines first if huge — use stride from file sizes
    for fp in files:
        try:
            with fp.open("r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f):
                    if i % max(1, step) != 0:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    op = rec.get("original_payload")
                    if not isinstance(op, dict) or not op:
                        continue
                    yield {
                        "day": day,
                        "symbol": rec.get("symbol") or op.get("Symbol"),
                        "received_at": rec.get("received_at_jst"),
                        "sequence": rec.get("sequence"),
                        "source_file": fp.name,
                        "source_row": i,
                        "payload": op,
                    }
                    n += 1
                    if n >= limit:
                        return
        except Exception:
            continue


def _estimate_stride(day: str, target: int) -> int:
    day_dir = CAPTURE_ROOT / day
    total = 0
    for fp in sorted(day_dir.glob("push_part_*.jsonl"))[:3]:
        try:
            with fp.open("r", encoding="utf-8", errors="ignore") as f:
                for _ in zip(f, range(5000)):
                    total += 1
        except Exception:
            pass
    # rough: if first 5k lines across 3 files, assume large; use stride 20
    if total >= 10000:
        return max(1, 40)
    if total >= 3000:
        return max(1, 10)
    return 1


def _iter_payloads_strided(day: str, *, limit: int) -> Iterator[dict[str, Any]]:
    day_dir = CAPTURE_ROOT / day
    if not day_dir.exists():
        return
    stride = _estimate_stride(day, limit)
    n = 0
    for fp in sorted(day_dir.glob("push_part_*.jsonl")):
        try:
            with fp.open("r", encoding="utf-8", errors="ignore") as f:
                for i, line in enumerate(f):
                    if i % stride != 0:
                        continue
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    op = rec.get("original_payload")
                    if not isinstance(op, dict) or not op:
                        continue
                    # require Buy1+Sell1 for R1 SoT
                    if not isinstance(op.get("Buy1"), dict) or not isinstance(op.get("Sell1"), dict):
                        continue
                    yield {
                        "day": day,
                        "symbol": str(rec.get("symbol") or op.get("Symbol") or ""),
                        "received_at": rec.get("received_at_jst"),
                        "sequence": rec.get("sequence"),
                        "source_file": fp.name,
                        "source_row": i,
                        "payload": op,
                    }
                    n += 1
                    if n >= limit:
                        return
        except Exception:
            continue


def _safe_corr(xs: list[float], ys: list[float]) -> Optional[float]:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    dx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    dy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if dx < 1e-12 or dy < 1e-12:
        return None
    return num / (dx * dy)


def run_r0_r1_diff(days: tuple[str, ...] = AUDIT_DAYS) -> dict[str, Any]:
    traces: list[dict[str, Any]] = []
    day_rows: list[dict[str, Any]] = []
    r0_rows: list[dict[str, Any]] = []
    r1_rows: list[dict[str, Any]] = []
    decision_diffs: list[dict[str, Any]] = []

    total = 0
    mapping_ok = 0  # BidPrice==Sell1 and AskPrice==Buy1
    board_token_flip = 0
    mid_high_r0 = 0
    mid_high_r1 = 0
    both_mid_high = 0
    only_r0 = 0
    only_r1 = 0
    spread_bps_max_abs_diff = 0.0
    imb_pairs_r0: list[float] = []
    imb_pairs_r1: list[float] = []
    top_imb_sum_to_one = 0
    top_imb_n = 0

    for day in days:
        n_day = 0
        flip_day = 0
        for rec in _iter_payloads_strided(day, limit=SAMPLE_PER_DAY):
            op = rec["payload"]
            r0 = r0_current_from_payload(op)
            c = normalize_kabu_board(op)
            depth_imb = canonical_depth_imbalance(op)
            r1 = r1_from_canonical(c, p33=BOARD_P33, p66=BOARD_P66)
            r1_depth_tok = board_token(depth_imb, p33=BOARD_P33, p66=BOARD_P66)
            r0_tok = board_token(r0.get("r0_imbalance_depth"), p33=BOARD_P33, p66=BOARD_P66)
            # Also top-of-book tokens
            r0_top_tok = board_token(r0.get("r0_imbalance_top"), p33=BOARD_P33, p66=BOARD_P66)
            r1_top_tok = board_token(c.canonical_imbalance, p33=BOARD_P33, p66=BOARD_P66)

            buy1 = op.get("Buy1") if isinstance(op.get("Buy1"), dict) else {}
            sell1 = op.get("Sell1") if isinstance(op.get("Sell1"), dict) else {}
            try:
                bp = float(op["BidPrice"]) if op.get("BidPrice") is not None else None
                ap = float(op["AskPrice"]) if op.get("AskPrice") is not None else None
                b1 = float(buy1["Price"]) if buy1.get("Price") is not None else None
                s1 = float(sell1["Price"]) if sell1.get("Price") is not None else None
            except (TypeError, ValueError, KeyError):
                bp = ap = b1 = s1 = None
            if bp is not None and s1 is not None and ap is not None and b1 is not None:
                if abs(bp - s1) < 1e-9 and abs(ap - b1) < 1e-9:
                    mapping_ok += 1

            r0_mh = r0_tok in ("Board:mid", "Board:high")
            r1_mh = r1_depth_tok in ("Board:mid", "Board:high")
            if r0_mh:
                mid_high_r0 += 1
            if r1_mh:
                mid_high_r1 += 1
            if r0_mh and r1_mh:
                both_mid_high += 1
            elif r0_mh and not r1_mh:
                only_r0 += 1
            elif r1_mh and not r0_mh:
                only_r1 += 1

            if r0_tok != r1_depth_tok:
                board_token_flip += 1
                flip_day += 1

            if r0.get("r0_imbalance_top") is not None and c.canonical_imbalance is not None:
                top_imb_n += 1
                s = float(r0["r0_imbalance_top"]) + float(c.canonical_imbalance)
                if abs(s - 1.0) < 1e-6:
                    top_imb_sum_to_one += 1
                imb_pairs_r0.append(float(r0["r0_imbalance_top"]))
                imb_pairs_r1.append(float(c.canonical_imbalance))

            sb0 = r0.get("r0_spread_bps_abs")
            sb1 = c.canonical_spread_bps
            if sb0 is not None and sb1 is not None:
                spread_bps_max_abs_diff = max(spread_bps_max_abs_diff, abs(sb0 - sb1))

            row = {
                "day": day,
                "symbol": rec["symbol"],
                "received_at": rec["received_at"],
                "sequence": rec["sequence"],
                "r0_board_token_depth": r0_tok,
                "r1_board_token_depth": r1_depth_tok,
                "r0_board_token_top": r0_top_tok,
                "r1_board_token_top": r1_top_tok,
                "r0_imbalance_depth": r0.get("r0_imbalance_depth"),
                "r1_imbalance_depth": depth_imb,
                "r0_imbalance_top": r0.get("r0_imbalance_top"),
                "r1_imbalance_top": c.canonical_imbalance,
                "r0_spread_bps": sb0,
                "r1_spread_bps": sb1,
                "r0_spread_signed": r0.get("r0_spread_signed"),
                "r1_spread": c.canonical_spread,
                "r0_mid_high_gate": r0_mh,
                "r1_mid_high_gate": r1_mh,
                "token_flip": r0_tok != r1_depth_tok,
                "gate_flip": r0_mh != r1_mh,
                "quote_valid_r1": c.quote_valid,
                "kabu_bid_eq_sell1": bp is not None and s1 is not None and abs(bp - s1) < 1e-9,
                "kabu_ask_eq_buy1": ap is not None and b1 is not None and abs(ap - b1) < 1e-9,
            }
            total += 1
            n_day += 1

            if row["gate_flip"] or row["token_flip"]:
                if len(traces) < max(TRACE_MIN, 80):
                    traces.append({
                        **row,
                        "canonical_best_bid": c.canonical_best_bid,
                        "canonical_best_ask": c.canonical_best_ask,
                        "kabu_bid_price_raw": c.kabu_bid_price_raw,
                        "kabu_ask_price_raw": c.kabu_ask_price_raw,
                        "source_file": rec["source_file"],
                        "source_row": rec["source_row"],
                    })

            if n_day <= 5:
                r0_rows.append({"day": day, **{k: r0[k] for k in r0}, "board_token": r0_tok})
                r1_rows.append({"day": day, **r1, "r1_imbalance_depth": depth_imb, "r1_board_token_depth": r1_depth_tok})

        day_rows.append({
            "day": day,
            "n_sampled": n_day,
            "token_flips": flip_day,
            "token_flip_rate": (flip_day / n_day) if n_day else None,
        })

    # Synthetic decision / entry / exit / pnl impact proxies (feature-level; not full sim)
    gate_flip_rate = ((only_r0 + only_r1) / total) if total else None
    token_flip_rate = (board_token_flip / total) if total else None
    # Proxy: assume mid/high gate is required — ENTRY set Jaccard
    union = both_mid_high + only_r0 + only_r1
    jaccard = (both_mid_high / union) if union else None

    # EXIT proxy: top-of-book imbalance sign for collapse (delta direction)
    # If top imb is inverted, deterioration deltas flip sign → EXIT timing flips
    exit_direction_inverted = (top_imb_sum_to_one / top_imb_n) > 0.9 if top_imb_n else None

    corr = _safe_corr(imb_pairs_r0, imb_pairs_r1)

    decision_diffs = [
        {
            "metric": "board_token_flip_rate",
            "value": token_flip_rate,
            "n": total,
        },
        {
            "metric": "mid_high_gate_flip_rate",
            "value": gate_flip_rate,
            "only_r0_accept": only_r0,
            "only_r1_accept": only_r1,
            "both_accept": both_mid_high,
            "jaccard_mid_high": jaccard,
        },
        {
            "metric": "top_imbalance_sum_to_one_rate",
            "value": (top_imb_sum_to_one / top_imb_n) if top_imb_n else None,
            "interpretation": "≈1 means R0 top imb is exact complement of canonical (inverted)",
        },
        {
            "metric": "r0_r1_top_imb_corr",
            "value": corr,
            "expected_if_inverted": -1.0,
        },
        {
            "metric": "spread_bps_max_abs_diff",
            "value": spread_bps_max_abs_diff,
            "interpretation": "abs spread should be ~0 diff",
        },
        {
            "metric": "kabu_field_mapping_match_rate",
            "value": (mapping_ok / total) if total else None,
            "interpretation": "BidPrice==Sell1 and AskPrice==Buy1",
        },
    ]

    entry_diff = {
        "n_events": total,
        "r0_mid_high_count": mid_high_r0,
        "r1_mid_high_count": mid_high_r1,
        "only_r0_would_pass_board_gate": only_r0,
        "only_r1_would_pass_board_gate": only_r1,
        "both_pass": both_mid_high,
        "gate_agreement_jaccard": jaccard,
        "note": "Proxy for PBv2 board_mid|high required gate on depth imbalance; not full PBv2 accept sim",
    }
    exit_diff = {
        "top_imbalance_exact_invert_rate": (top_imb_sum_to_one / top_imb_n) if top_imb_n else None,
        "exit_direction_inverted": exit_direction_inverted,
        "board_dynamic_trailing": "tier keys from inverted entry imb → params may differ",
        "realtime_board_exit": "collapse/profit_protect deltas flip sign when imb inverted",
        "note": "Full EXIT clock replay not run; direction inversion is structural",
    }
    pnl_diff = {
        "full_pnl_replay": "NOT_RUN_IN_AUDIT",
        "structural_reason": "Different ENTRY cohort + inverted EXIT board signals ⇒ PnL not transferable",
        "egc_formal_e1_x1": {"n": 639, "pf": 0.3552, "cap5": -698243.98},
        "proxy_entry_set_jaccard": jaccard,
        "implication": "Any board-dependent PF from inverted lineage is not comparable to canonical",
    }

    return {
        "days": list(days),
        "n_total": total,
        "day_summary": day_rows,
        "r0_samples": r0_rows,
        "r1_samples": r1_rows,
        "decision_diff": decision_diffs,
        "entry_diff": entry_diff,
        "exit_diff": exit_diff,
        "pnl_diff": pnl_diff,
        "traces": traces,
        "mapping_ok_rate": (mapping_ok / total) if total else None,
        "token_flip_rate": token_flip_rate,
        "gate_flip_rate": gate_flip_rate,
    }
