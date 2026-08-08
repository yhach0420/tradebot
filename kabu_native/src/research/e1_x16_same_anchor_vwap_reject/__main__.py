"""python -m research.e1_x16_same_anchor_vwap_reject"""
from .run_audit import run

if __name__ == "__main__":
    import sys
    run(force_enrich="--force" in sys.argv)
