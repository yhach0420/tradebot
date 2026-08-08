"""python -m research.e1_x18_vwap_failure_source"""
from .run_audit import run

if __name__ == "__main__":
    import sys
    run(force_context="--force" in sys.argv)
