"""python -m research.e1_x17_vwap_reject_prospective"""
from .run_audit import run

if __name__ == "__main__":
    import sys
    run(force_construct="--force" in sys.argv, allow_rereport="--allow-rereport" in sys.argv)
