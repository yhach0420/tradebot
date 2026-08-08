"""Canonical JPX tick size — exact OTHER-table logic from low_price_risk_review.jpx_tick_size_yen.

Imported via local copy to avoid research.low_price_risk_review side-effect import chain
(small_paper_performance_review → … → src.kabu_signal_engine) in research PYTHONPATH.
"""
from __future__ import annotations

CANONICAL_SOURCE = "research.low_price_risk_review.jpx_tick_size_yen"
CANONICAL_FILE = "src/research/low_price_risk_review.py"


def jpx_tick_size_yen(price: float, *, narrow_topix500: bool = False) -> float:
    """JPX quotation unit (Other Issues table; default for non-TOPIX500 small caps)."""
    p = float(price)
    if p <= 0:
        return 1.0
    if narrow_topix500:
        if p <= 1000:
            return 0.1
        if p <= 10000:
            return 1.0
        if p <= 100000:
            return 10.0
        if p <= 300000:
            return 50.0
        if p <= 1000000:
            return 100.0
        if p <= 3000000:
            return 500.0
        if p <= 10000000:
            return 1000.0
        if p <= 30000000:
            return 5000.0
        return 10000.0
    if p <= 3000:
        return 1.0
    if p <= 5000:
        return 5.0
    if p <= 30000:
        return 10.0
    if p <= 50000:
        return 50.0
    if p <= 300000:
        return 100.0
    if p <= 500000:
        return 500.0
    if p <= 3000000:
        return 1000.0
    if p <= 5000000:
        return 5000.0
    if p <= 30000000:
        return 10000.0
    return 100000.0
