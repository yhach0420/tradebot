from research.canonical_zero_base.strategies_core import scan_triggers
from research.canonical_zero_base.strategy_contract import Z3

STRATEGY_ID = "Z3"
CONTRACT = Z3


def scan(events, thr):
    return scan_triggers(events, "Z3", thr=thr)
