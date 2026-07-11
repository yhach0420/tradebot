"""Run Monday Active Runtime Gate from tests/runtime_gate_manifest.json (Windows-safe)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    native = Path(__file__).resolve().parents[1]
    manifest_path = native / "tests" / "runtime_gate_manifest.json"
    man = json.loads(manifest_path.read_text(encoding="utf-8"))
    nodes = [str(native / n) if not Path(n).is_absolute() else n for n in man.get("nodes") or []]
    # Prefer relative paths from native cwd
    rel = list(man.get("nodes") or [])
    env = dict(**{**__import__("os").environ})
    env["PYTHONPATH"] = f"{native / 'src'};{native.parent}"
    cmd = [sys.executable, "-m", "pytest", "-q", "--tb=line", *rel]
    print("runtime_gate nodes:", len(rel), flush=True)
    r = subprocess.run(cmd, cwd=str(native), env=env)
    return int(r.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
