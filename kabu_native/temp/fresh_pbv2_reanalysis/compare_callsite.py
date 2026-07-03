"""Show the call site of _maybe_try_or_overlay_entry / PBv2 decision flow per commit."""
import subprocess

def get(commit):
    if commit == "working":
        return open("src/small_paper/pilot_runner.py", encoding="utf-8").read().splitlines()
    out = subprocess.run(["git", "show", f"{commit}:kabu_native/src/small_paper/pilot_runner.py"],
                         capture_output=True).stdout.decode("utf-8", errors="replace")
    return out.splitlines()

for label in ["f50c5a7", "924bb1e", "working"]:
    src = get(label)
    print("=" * 70)
    print(label, f"({len(src)} lines)")
    for i, l in enumerate(src):
        if "_maybe_try_or_overlay_entry(" in l and "def " not in l:
            lo, hi = max(0, i - 30), min(len(src), i + 12)
            for j in range(lo, hi):
                print(f"  {j+1:5d}|{src[j]}")
            print()
