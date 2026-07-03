"""Compare OR-overlay decision handling across commits of pilot_runner.py."""
import re, subprocess

def get(commit):
    if commit == "working":
        return open("src/small_paper/pilot_runner.py", encoding="utf-8").read().splitlines()
    out = subprocess.run(["git", "show", f"{commit}:kabu_native/src/small_paper/pilot_runner.py"],
                         capture_output=True).stdout.decode("utf-8", errors="replace")
    return out.splitlines()

PATTERNS = [
    r"def _maybe_try_or_overlay_entry",
    r"pbv2_internal_reason",
    r"evaluate_or_overlay_entry",
    r"freshness_semantics_v2",
    r"core_runtime_mode",
    r"extension_bus|ExtensionBus",
]

for label in ["196a559", "f50c5a7", "924bb1e", "working"]:
    src = get(label)
    print("=" * 70)
    print(label, f"({len(src)} lines)")
    for pat in PATTERNS:
        hits = [i + 1 for i, l in enumerate(src) if re.search(pat, l)]
        print(f"  {'present x' + str(len(hits)) if hits else 'ABSENT   '}  {pat}  {hits[:8]}")
    for i, l in enumerate(src):
        if "def _maybe_try_or_overlay_entry" in l or ("evaluate_or_overlay_entry" in l and "import" not in l and "def " not in l):
            lo, hi = max(0, i - 20), min(len(src), i + 25)
            for j in range(lo, hi):
                print(f"    {j+1:5d}|{src[j]}")
            break
