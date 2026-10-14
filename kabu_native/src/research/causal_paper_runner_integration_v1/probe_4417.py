"""One-pass probe for the unclassified 4417 denial. Does not write sealed files."""
from __future__ import annotations

import os
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[3]
SRC = NATIVE / "src"
ROOT = NATIVE.parent
for item in (str(ROOT), str(NATIVE), str(SRC)):
    if item not in sys.path:
        sys.path.insert(0, item)

OUT = NATIVE / "results" / "research" / "causal_paper_runner_integration_v1"
os.environ["TRADEBOT_C3_RESIDUAL_PATH"] = str(OUT / "probe_residual.json")

from research.current50_v16_complete_strategy_risk_foundation_v1.foundation import _capture_dir, _symbol
from research.execution_realism_layer_v1.replay import load_universe
from research.current50_v16_capital_risk_policy_comparison_v1.compare import ResearchGrossCapExecutor
from small_paper.paper_execution_causal_v1 import PaperExecutionCausalBook

TARGET = "20261013|PM|4417.T|394"
DAY = "20261013"
PM_WATERMARK = 545280
original = ResearchGrossCapExecutor._on_signal


def wrapped(self, sym, index, event_t, heap, flush_t):
    uid = f"{self.day}|{self.session}|{sym}|{int(index)}"
    if uid != TARGET:
        original(self, sym, index, event_t, heap, flush_t)
        return
    before = {
        "seen": int(self.signal_seen_n),
        "adm": int(self.admission_denied_n),
        "quote": int(self.signal_quote_not_ok_n),
        "invalid": int(self.signal_invalid_ask_n),
        "cap": int(self.counts.get("cap") or 0),
        "same": int(self.counts.get("same_symbol") or 0),
        "occ": int(self.counts.get("occupancy") or 0),
        "gross_denied": int(getattr(self, "gross_cap_denied_n", 0) or 0),
        "closed": bool(self._entries_closed),
        "flat": self.flatten_start,
        "open": {
            name: {"uid": pos.get("signal_uid"), "state": pos.get("exec_state"), "px": pos.get("entry_px") or pos.get("reserve_px")}
            for name, pos in self.open_pos.items()
        },
    }
    st = self.syms[sym]
    quote = {
        "ok": bool(st.ok[index]),
        "ask": float(st.ask_px[index]),
        "pre": float(st.pre_high[index]),
        "ptr": int(st.sig_ptr),
        "sig": int(st.signals[st.sig_ptr]) if st.sig_ptr < len(st.signals) else None,
    }
    original(self, sym, index, event_t, heap, flush_t)
    after = {
        "seen": int(self.signal_seen_n),
        "adm": int(self.admission_denied_n),
        "quote": int(self.signal_quote_not_ok_n),
        "invalid": int(self.signal_invalid_ask_n),
        "cap": int(self.counts.get("cap") or 0),
        "same": int(self.counts.get("same_symbol") or 0),
        "occ": int(self.counts.get("occupancy") or 0),
        "gross_denied": int(getattr(self, "gross_cap_denied_n", 0) or 0),
        "closed": bool(self._entries_closed),
        "open": {
            name: {
                "uid": pos.get("signal_uid"),
                "state": pos.get("exec_state"),
                "index": pos.get("signal_index"),
            }
            for name, pos in self.open_pos.items()
        },
    }
    print({"before": before, "quote": quote, "deltas": {k: after[k] - before[k] for k in ("seen", "adm", "quote", "invalid", "cap", "same", "occ", "gross_denied")}, "opened": "4417.T" in after["open"], "held": after["open"]})
    raise SystemExit(0)


ResearchGrossCapExecutor._on_signal = wrapped
symbols = set(load_universe())
capture = _capture_dir(DAY)
book = PaperExecutionCausalBook()
book.notifier = None
book.ledger_path = None

def admit(symbol: str, _t: float):
    return (symbol in symbols, "SAME_DAY_AM_FROZEN_UNIVERSE" if symbol in symbols else "NOT_IN_DAY_FREEZE")

book.admission = admit
book.bind_causal_schedule(day=DAY, kind="pm")
book.ledger_path = None
try:
    import orjson
    loads = orjson.loads
except Exception:
    import json
    loads = json.loads
for part in sorted(capture.glob("push_part_*.jsonl")):
    with part.open("rb") as handle:
        for raw in handle:
            if not raw.strip():
                continue
            rec = loads(raw)
            if rec.get("kind") not in (None, "market_push"):
                continue
            seq = int(rec.get("sequence") or 0)
            if seq <= PM_WATERMARK:
                continue
            payload = rec.get("original_payload") if isinstance(rec.get("original_payload"), dict) else None
            if not payload:
                continue
            symbol = _symbol(str(rec.get("symbol") or payload.get("Symbol") or ""))
            if not symbol:
                continue
            received = str(rec.get("received_at") or "")
            book.on_market_event({"symbol": symbol, "payload": payload, "received_at": received, "t0_push_received_at": received})
print("NOT_REACHED")
