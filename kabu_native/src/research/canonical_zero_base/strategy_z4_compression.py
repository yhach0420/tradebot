from research.canonical_zero_base.strategies_core import scan_triggers
from research.canonical_zero_base.strategy_contract import Z4

STRATEGY_ID = "Z4"
CONTRACT = Z4


def scan(events, thr):
    return scan_triggers(events, "Z4", thr=thr)
