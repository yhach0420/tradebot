"""Global Quote Semantic Integrity Audit (S0). Research-only; no mainline wiring."""

from research.global_quote_semantic_audit.pipeline import run_audit

__all__ = ["run_audit"]
