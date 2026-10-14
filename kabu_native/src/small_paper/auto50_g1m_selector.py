"""Paper handoff of the frozen AUTO50_G1M causal baseline.

Score and the one-lot G filter are the research functions. This module does
not add a rank, a refill rule, or an ENTRY/EXIT change. Complete-strategy
PnL is not claimed here.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from research.auto50_research_capture.mode import eligible_feature
from research.daytrade50_capital_efficient_selector_v2.policy import QTY, one_lot_notional
from universe.core10_dynamic40 import write_universe_csv

SELECTOR_ID = "AUTO50_G1M"
SELECTOR_VERSION = "AUTO50_G1M_CAUSAL_BASELINE_READY_V1"
RESTORE_SELECTOR = "CORE10_DYNAMIC40"
G_YEN = 1_000_000
EXPECTED_N = 50
FAIL_CLOSED = "AUTO50_G1M_FAIL_CLOSED"
MODE_NAME = "universe_selector_mode.json"


def mode_path() -> Path:
    override = os.environ.get("TRADEBOT_UNIVERSE_SELECTOR_MODE_PATH", "").strip()
    if override:
        return Path(override)
    return Path(__file__).with_name(MODE_NAME)


def read_selector_mode(path: Optional[Path] = None) -> str:
    """Disabled or missing mode keeps the conventional Core10+Dynamic40 selector."""
    target = mode_path() if path is None else Path(path)
    if not target.is_file():
        return RESTORE_SELECTOR
    try:
        body = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("UNIVERSE_SELECTOR_MODE_FAIL_CLOSED") from exc
    selected = str(body.get("selected") or "")
    if selected not in {SELECTOR_ID, RESTORE_SELECTOR}:
        raise RuntimeError("UNIVERSE_SELECTOR_MODE_FAIL_CLOSED")
    if not bool(body.get("enabled")):
        return RESTORE_SELECTOR
    return selected


def select_auto50_g1m(
    feature_rows: Sequence[Mapping[str, str]],
    *,
    gross_budget_yen: float = G_YEN,
) -> dict[str, Any]:
    """Research order: eligible score, drop one-lot above G, take 50, else fail closed."""
    if float(gross_budget_yen) != float(G_YEN):
        raise ValueError("AUTO50_G1M G is 1000000")
    eligible: list[dict[str, Any]] = []
    rejected_data = 0
    rejected_budget = 0
    for raw in feature_rows:
        item = eligible_feature(raw)
        if item is None:
            rejected_data += 1
            continue
        notional = one_lot_notional(float(item["prior_close"]))
        if notional > float(gross_budget_yen) + 1e-6:
            rejected_budget += 1
            continue
        item["one_lot_notional"] = notional
        eligible.append(item)
    eligible.sort(key=lambda item: (-float(item["score"]), str(item["symbol"])))
    base = {
        "selector_id": SELECTOR_ID,
        "selector_version": SELECTOR_VERSION,
        "gross_budget_yen": int(G_YEN),
        "qty": QTY,
        "eligible_n": len(eligible),
        "rejected_data_n": rejected_data,
        "rejected_budget_n": rejected_budget,
        "core10_used": False,
        "entry_exit_rules": "UNCHANGED_C3",
        "economic_superiority_claimed": False,
    }
    if len(eligible) < EXPECTED_N:
        return {**base, "status": FAIL_CLOSED, "reason": "UNDER_50", "symbols": [], "rows": eligible}
    chosen = eligible[:EXPECTED_N]
    for index, row in enumerate(chosen, start=1):
        row["rank"] = index
        if float(row["one_lot_notional"]) > float(gross_budget_yen) + 1e-6:
            return {**base, "status": FAIL_CLOSED, "reason": "NOTIONAL_OVER_G", "symbols": [], "rows": []}
    symbols = [str(row["symbol"]) for row in chosen]
    if len(symbols) != len(set(symbols)):
        return {**base, "status": FAIL_CLOSED, "reason": "DUPLICATE_SYMBOL", "symbols": [], "rows": []}
    membership = canonical_membership(symbols)
    return {
        **base,
        "status": "TOP50",
        "reason": "",
        "symbols": symbols,
        "rows": chosen,
        "canonical_membership_sha": membership,
    }


def canonical_membership(symbols: Sequence[str]) -> str:
    raw = json.dumps([str(item) for item in symbols], separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def universe_rows(selected: Mapping[str, Any], *, session: str) -> list[dict[str, Any]]:
    rows = []
    for item in selected.get("rows") or []:
        bare = str(item["symbol"])
        symbol = bare if bare.endswith(".T") else f"{bare}.T"
        code = symbol[:-2]
        rows.append(
            {
                "symbol": symbol,
                "symbol_key": f"{code}@1",
                "exchange": "1",
                "passed": "True",
                "source_bucket": "auto50_g1m",
                "selected_reason": (
                    f"AUTO50_G1M score={float(item['score']):.6f} "
                    f"one_lot={float(item['one_lot_notional']):.0f} "
                    f"prior_close={float(item['prior_close'])}"
                ),
                "universe_slot": "auto50",
                "rank": str(item["rank"]),
                "volatility_liquidity_score": f"{float(item['score']):.6f}",
                "am_pm_session": session,
            }
        )
    return rows


def write_selection(directory: Path, day_stamp: str, selected: Mapping[str, Any], *, session: str) -> dict[str, Any]:
    directory.mkdir(parents=True, exist_ok=True)
    rows = universe_rows(selected, session=session)
    csv_path = directory / f"universe_auto50_g1m_{session}_{day_stamp}.csv"
    write_universe_csv(csv_path, rows)
    identity = {
        "selector_id": SELECTOR_ID,
        "selector_version": SELECTOR_VERSION,
        "trading_date": day_stamp,
        "session": session,
        "status": selected.get("status"),
        "reason": selected.get("reason") or "",
        "gross_budget_yen": G_YEN,
        "qty": QTY,
        "max_concurrent": 5,
        "symbols": [row["symbol"] for row in rows],
        "scores": [
            {
                "symbol": row["symbol"],
                "rank": int(row["rank"]),
                "score": float(row["volatility_liquidity_score"]),
                "one_lot_notional": float(item["one_lot_notional"]),
                "prior_close": float(item["prior_close"]),
                "reason": row["selected_reason"],
            }
            for item, row in zip(selected.get("rows") or [], rows)
        ],
        "canonical_membership_sha": selected.get("canonical_membership_sha") or "",
        "universe_sha256": hashlib.sha256(csv_path.read_bytes()).hexdigest(),
        "eligible_n": selected.get("eligible_n"),
        "rejected_budget_n": selected.get("rejected_budget_n"),
        "rejected_data_n": selected.get("rejected_data_n"),
        "economic_superiority_claimed": False,
        "submit_cancel_live": "0/0/0",
    }
    identity_path = directory / f"auto50_g1m_identity_{session}_{day_stamp}.json"
    identity_path.write_text(json.dumps(identity, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"csv": csv_path, "identity": identity_path, "rows": rows, "body": identity}


def load_symbols(path: Path) -> list[str]:
    if not path.is_file():
        return []
    out = []
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            symbol = str(row.get("symbol") or "").strip()
            if symbol:
                out.append(symbol)
    return out


def pm_matches_am(am_symbols: Sequence[str], pm_symbols: Sequence[str]) -> bool:
    return list(am_symbols) == list(pm_symbols) and len(am_symbols) == EXPECTED_N
