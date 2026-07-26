from research.canonical_zero_base.strategies_core import scan_triggers
from research.canonical_zero_base.strategy_contract import Z1

STRATEGY_ID = "Z1"
CONTRACT = Z1


def scan(events, thr):
    return scan_triggers(events, "Z1", thr=thr)
