"""BG_CONT_VWAP exit-family tests. Frozen Validation closed. No V27. No TP."""
from __future__ import annotations

import inspect

from research.bg_cont_vwap_largest_causal_deficiency_v1 import (
    CASE_NONE,
    EXPECTED_E0_TRADE_N,
    EXPECTED_MECHANISM_HASH,
    FAVOR_BPS,
    FIVE_MINUTE_GRID,
    FROZEN_VALIDATION_OPENED,
    PARENT_VERDICT,
    PROMOTED,
    TAKE_PROFIT_TESTED,
    V27_BOLTED,
    X1_TAX_BPS,
)
from research.bg_cont_vwap_largest_causal_deficiency_v1.analyze import decide
from research.bg_cont_vwap_largest_causal_deficiency_v1.evaluate import e0_parity
from research.bg_cont_vwap_largest_causal_deficiency_v1.exits import exit_state_machine
from research.bg_cont_vwap_largest_causal_deficiency_v1.isolation import OUT, RCA_OUT, write_overlap_n
from research.bg_cont_vwap_largest_causal_deficiency_v1.publish import SHEET_ORDER


def _fwd(bars: list[tuple]) -> dict:
    px = 100.0
    rows = []
    t = 10 * 60
    for o, h, l, c, above in bars:
        hh = f"{t // 60:02d}:{t % 60:02d}"
        t += 1
        vw = 100.0
        rows.append((hh, o, h, l, c, vw, above, None, 0, 0, 0, 0, ""))
    return {"fwd_bars": rows, "x0_entry_open": px}


def test_constants():
    assert PARENT_VERDICT == "BG_CONT_VWAP_RCA_MIXED_V1"
    assert EXPECTED_MECHANISM_HASH.startswith("ae7c8491")
    assert EXPECTED_E0_TRADE_N == 486
    assert FAVOR_BPS == 8.0
    assert X1_TAX_BPS == 8.0
    assert FROZEN_VALIDATION_OPENED is False
    assert PROMOTED is False
    assert V27_BOLTED is False
    assert TAKE_PROFIT_TESTED is False
    assert FIVE_MINUTE_GRID is False
    assert OUT.name == "bg_cont_vwap_largest_causal_deficiency_v1"
    assert RCA_OUT.name == "freeze_group_mechanism_definitions_v1"
    assert write_overlap_n("", "") == 0
    assert SHEET_ORDER[0] == "Binding"
    assert "Replay_Parity" in SHEET_ORDER
    assert "D2_D3_Core" in SHEET_ORDER
    assert SHEET_ORDER[-1] == "Safety"


def test_state_machine_and_decide():
    assert decide(bind_ok=False, parity_ok=True, winner="E2")["VERDICT"].endswith("BIND_FAILED_V1")
    assert decide(bind_ok=True, parity_ok=False, winner="E2")["VERDICT"].endswith("PARITY_FAILED_V1")
    assert decide(bind_ok=True, parity_ok=True, winner=None)["VERDICT"] == CASE_NONE
    # E1: first loss then reclaim → warning cancelled, later time path continues
    e = _fwd(
        [
            (100.0, 100.2, 99.8, 99.9, False),
            (99.9, 100.3, 99.9, 100.2, True),
            (100.2, 100.4, 100.1, 100.3, True),
        ]
    )
    e1 = exit_state_machine(e, persist2=True, mfe8_grace=False, exit_id="E1")
    assert e1["ok"] is True
    assert e1["warning_cancel_n"] == 1
    assert e1["exit_reason"] != "vwap_loss"
    # E1: two consecutive losses → confirm
    e = _fwd(
        [
            (100.0, 100.1, 99.7, 99.8, False),
            (99.8, 99.9, 99.5, 99.6, False),
        ]
    )
    e1b = exit_state_machine(e, persist2=True, mfe8_grace=False, exit_id="E1")
    assert e1b["exit_reason"] == "vwap_loss_persist2"
    assert e1b["hold_min"] == 1
    # E2 without MFE8: first loss exits like E0
    e = _fwd([(100.0, 100.05, 99.8, 99.9, False)])
    e2 = exit_state_machine(e, persist2=True, mfe8_grace=True, exit_id="E2")
    assert e2["exit_reason"] == "vwap_loss"
    assert e2["mfe8_grace_used"] is False
    assert e2["hold_min"] == 0
    # E2 with MFE8 on same bar (high +10bps) then close below VWAP → warning not immediate exit
    e = _fwd(
        [
            (100.0, 100.12, 99.8, 99.9, False),
            (99.9, 99.95, 99.7, 99.8, False),
        ]
    )
    e2b = exit_state_machine(e, persist2=True, mfe8_grace=True, exit_id="E2")
    assert e2b["mfe8_activated_at"] is not None
    assert e2b["exit_reason"] == "vwap_loss_persist2"
    assert e2b["hold_min"] == 1
    p = e0_parity({"trade_n": 486, "mean_x0_bps": 6.794475682134674})
    assert p["ok"] is True


def test_no_v27_tp_grids():
    import research.bg_cont_vwap_largest_causal_deficiency_v1.analyze as a
    import research.bg_cont_vwap_largest_causal_deficiency_v1.exits as x

    src_a = inspect.getsource(a)
    src_x = inspect.getsource(x)
    assert "import yfinance" not in src_a.lower()
    assert "grid_clocks(" not in src_a
    assert "import ema" not in src_x.lower()
    assert "import rsi" not in src_x.lower()
    assert "take_profit" not in src_x
    assert FAVOR_BPS == 8.0
    assert "6bps" not in src_x
    assert V27_BOLTED is False
