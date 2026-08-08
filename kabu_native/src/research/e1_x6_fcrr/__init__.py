"""E1_X6_FCRR research-only package (Flow Confirmed Reclaim & Retention).

Independent Watch50 evaluation. Does NOT patch E1_X5 / PBv2 / production YAML.
"""
from __future__ import annotations

from .config import CANDIDATE_FAMILY, CANDIDATE_IDS, DOCUMENT_ID, DOCUMENT_VERSION

__all__ = [
    "CANDIDATE_FAMILY",
    "CANDIDATE_IDS",
    "DOCUMENT_ID",
    "DOCUMENT_VERSION",
]
