import os
import subprocess
import sys
from pathlib import Path

NATIVE = Path(__file__).resolve().parents[1]
os.environ["PYTHONPATH"] = str(NATIVE / "src") + os.pathsep + str(NATIVE.parent)
os.environ["MARKET_INGRESS_V2"] = "1"
os.environ["TRADEBOT_DEMO_PUSH_E2E"] = "1"
cmd = [sys.executable, str(NATIVE / "scripts" / "run_tomorrow_paper_e2e_demo_check.py")]
print("LAUNCH", cmd, flush=True)
r = subprocess.run(cmd, cwd=str(NATIVE))
print("DONE", r.returncode, flush=True)
sys.exit(r.returncode)
