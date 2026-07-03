import os, shutil, datetime

out = "results/reports/fresh_pbv2_reanalysis"
lines = []
total = 0
for n in sorted(os.listdir(out)):
    p = os.path.join(out, n)
    sz = os.path.getsize(p)
    total += sz
    lines.append(f"KEEP  {n}  {sz} bytes")
lines.append(f"TOTAL outputs: {total} bytes ({total/1e6:.2f} MB)")

tmp = "temp/fresh_pbv2_reanalysis"
tmp_total = 0
removed = []
for root, dirs, files in os.walk(tmp):
    for f in files:
        p = os.path.join(root, f)
        tmp_total += os.path.getsize(p)
lines.append(f"TEMP dir before cleanup: {tmp_total} bytes ({tmp_total/1e6:.2f} MB)")

# remove intermediate aggregates (largest part); keep analysis scripts for reproducibility
agg = os.path.join(tmp, "agg")
if os.path.isdir(agg):
    sz = sum(os.path.getsize(os.path.join(agg, f)) for f in os.listdir(agg))
    shutil.rmtree(agg)
    removed.append(f"REMOVED temp/fresh_pbv2_reanalysis/agg ({sz} bytes)")
for f in os.listdir(tmp):
    p = os.path.join(tmp, f)
    if os.path.isfile(p) and not f.endswith(".py"):
        sz = os.path.getsize(p)
        os.remove(p)
        removed.append(f"REMOVED temp/fresh_pbv2_reanalysis/{f} ({sz} bytes)")
lines += removed
lines.append("KEPT: temp/fresh_pbv2_reanalysis/*.py (analysis scripts, few KB, reproducibility)")

import ctypes
free = ctypes.c_ulonglong(0); tot = ctypes.c_ulonglong(0)
ctypes.windll.kernel32.GetDiskFreeSpaceExW("C:\\", None, ctypes.byref(tot), ctypes.byref(free))
used_pct = 100 * (1 - free.value / tot.value)
lines.append(f"DISK C: usage after cleanup: {used_pct:.1f}% (was 90.8% before analysis started; 76% constraint pre-violated by existing data, this analysis added <{total/1e6:.1f}MB)")
lines.insert(0, f"cleanup_log generated {datetime.datetime.now().isoformat()}")

with open(os.path.join(out, "cleanup_log.txt"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")
print("\n".join(lines))
