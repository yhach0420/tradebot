"""Full-day sufficiency probe for 20260805-07 using existing loaders (no PnL)."""
from pathlib import Path
from research.e1_x14_board_independent_signal.ticks import list_day_symbols, load_symbol_ticks
from research.e1_x28_executable_joint.board import load_board_events
from research.e1_x22_actual_exit_factory.paths import _load_price_events

days = ["20260805", "20260806", "20260807"]
for day in days:
    syms = list_day_symbols(day)
    board_ok = price_ok = tick_ok = both = 0
    board_only = price_only = neither = 0
    board_events = price_events = tick_events = 0
    thin_board = thin_price = 0
    for i, sym in enumerate(syms):
        b = load_board_events(day, sym)
        t, p = _load_price_events(day, sym)
        ticks = load_symbol_ticks(day, sym)
        be, pe, te = b["t"].size, t.size, len(ticks)
        board_events += be
        price_events += pe
        tick_events += te
        b_ok = be > 0
        p_ok = pe > 0
        t_ok = te > 0
        board_ok += int(b_ok)
        price_ok += int(p_ok)
        tick_ok += int(t_ok)
        if b_ok and p_ok:
            both += 1
        elif b_ok:
            board_only += 1
        elif p_ok:
            price_only += 1
        else:
            neither += 1
        if 0 < be < 100:
            thin_board += 1
        if 0 < pe < 100:
            thin_price += 1
        if (i + 1) % 30 == 0:
            print(f"  {day} progress {i+1}/{len(syms)}", flush=True)
    print(
        f"{day}: symbols={len(syms)} board_ok={board_ok} price_ok={price_ok} "
        f"tick_ok={tick_ok} both={both} board_only={board_only} price_only={price_only} neither={neither}"
    )
    print(
        f"  events board={board_events} price={price_events} tick={tick_events} "
        f"thin_board(<100)={thin_board} thin_price(<100)={thin_price}"
    )
