"""python -m research.e1_x19_outcome_pre_path"""
from .run_audit import run

if __name__ == "__main__":
    import sys
    run(force="--force" in sys.argv)
