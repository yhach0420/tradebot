from research.canonical_zero_base.strategies_core import scan_triggers
from research.canonical_zero_base.strategy_contract import Z2

STRATEGY_ID = "Z2"
CONTRACT = Z2


def scan(events, thr):
    return scan_triggers(events, "Z2", thr=thr)
