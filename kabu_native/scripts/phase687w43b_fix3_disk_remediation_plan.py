#!/usr/bin/env python3
"""Phase687W43B-FIX3: Disk remediation plan to reach 75% (DRY-RUN ONLY).

Never deletes, moves, compresses, or empties recycle bin.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

NATIVE = Path(__file__).resolve().parents[1]
REPO = NATIVE.parent  # tradebotfile
OUT = NATIVE / "results" / "reports"
USER = Path(r"C:\Users\yhach")
CURSOR = USER / "AppData" / "Roaming" / "Cursor"
VIDEOS = USER / "Videos"
TEMP = Path(os.environ.get("TEMP", str(USER / "AppData" / "Local" / "Temp")))
RECYCLE = Path(r"C:\$Recycle.Bin")
DOWNLOADS = USER / "Downloads"
PIP = USER / "AppData" / "Local" / "pip"

TOTAL_GB = 1999.22653184
USED_GB = 1675.972509696
TARGET_PCT = 75.0
TARGET_USED_GB = TOTAL_GB * TARGET_PCT / 100.0  # ~1499.42
NEED_GB = USED_GB - TARGET_USED_GB  # ~176.55
SAFE_NEED_GB = 180.0

JST_NOW = datetime.now().astimezone()


def _wj(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _wc(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    cols: list[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in cols})


def _wm(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")


def _run_git(*args: str) -> str:
    try:
        r = subprocess.run(
            ["git", "-C", str(REPO), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        return (r.stdout or "") + (("\n" + r.stderr) if r.stderr else "")
    except Exception as exc:
        return f"ERROR: {exc}"


def dir_size(path: Path, *, max_files: int = 500_000, skip_names: Optional[set[str]] = None) -> tuple[int, int, Optional[float]]:
    """Return (bytes, file_count, mtime_epoch_max)."""
    skip_names = skip_names or set()
    total = 0
    n = 0
    mtime = 0.0
    if not path.exists():
        return 0, 0, None
    if path.is_file():
        try:
            st = path.stat()
            return st.st_size, 1, st.st_mtime
        except OSError:
            return 0, 0, None
    try:
        for root, dirs, files in os.walk(path, onerror=lambda _e: None):
            dirs[:] = [d for d in dirs if d not in skip_names and d.lower() not in {"system volume information"}]
            for name in files:
                n += 1
                if n > max_files:
                    return total, n, mtime or None
                fp = Path(root) / name
                try:
                    st = fp.stat()
                    total += st.st_size
                    if st.st_mtime > mtime:
                        mtime = st.st_mtime
                except OSError:
                    pass
    except OSError:
        pass
    return total, n, mtime or None


def fmt_mtime(epoch: Optional[float]) -> str:
    if not epoch:
        return ""
    return datetime.fromtimestamp(epoch).isoformat(timespec="seconds")


def gb(n: int | float) -> float:
    return round(float(n) / 1e9, 3)


def used_pct_after(free_gb: float) -> float:
    return round(100.0 * max(0.0, USED_GB - free_gb) / TOTAL_GB, 2)


# ---------------------------------------------------------------------------
# P0-1 tradebotfile depth-4 inventory
# ---------------------------------------------------------------------------

PROTECTED_PATTERNS = [
    (re.compile(r"[\\/]\.git([\\/]|$)"), "git_objects", "削除禁止"),
    (re.compile(r"market_capture", re.I), "canonical_raw_capture", "削除禁止"),
    (re.compile(r"pre_entry_market_state", re.I), "w43_parquet", "削除禁止"),
    (re.compile(r"session_seal|recovery", re.I), "session_seal_recovery", "削除禁止"),
    (re.compile(r"configs[\\/].*\.ya?ml$", re.I), "active_config", "削除禁止"),
    (re.compile(r"results[\\/]reports[\\/]", re.I), "adoption_reports", "保護（採用根拠）"),
]


def paper_keep_days() -> set[str]:
    root = NATIVE / "results" / "small_paper"
    if not root.is_dir():
        return set()
    days = sorted([p.name for p in root.iterdir() if p.is_dir() and p.name.isdigit()], reverse=True)
    return set(days[:20])


def classify_tradebot(path: Path, keep_days: set[str]) -> dict[str, Any]:
    s = str(path).replace("/", "\\")
    low = s.lower()
    name = path.name.lower()

    for rx, cls, risk in PROTECTED_PATTERNS:
        if rx.search(s):
            return {
                "classification": cls,
                "regenerable": False,
                "delete_risk": "FORBIDDEN",
                "currently_referenced": True,
                "recreation_command": "",
                "TradeBot impact": risk,
            }

    # latest 20 paper days
    m = re.search(r"small_paper[\\/](\d{8})", s, re.I)
    if m:
        day = m.group(1)
        if day in keep_days:
            return {
                "classification": "paper_canonical_recent20",
                "regenerable": False,
                "delete_risk": "FORBIDDEN",
                "currently_referenced": True,
                "recreation_command": "",
                "TradeBot impact": "最新20営業日 canonical — 削除禁止",
            }
        return {
            "classification": "paper_session_archive_candidate",
            "regenerable": False,
            "delete_risk": "medium",
            "currently_referenced": False,
            "recreation_command": "外部退避後も canonical index をC側に残す",
            "TradeBot impact": "旧Paper — 削除せず外部退避推奨 (Plan B)",
        }

    if any(x in low for x in ("_w", "worktree", "phase629_head_worktree", "tradebotfile-phase")):
        return {
            "classification": "git_worktree_or_orphan",
            "regenerable": True,
            "delete_risk": "medium",
            "currently_referenced": False,
            "recreation_command": "git worktree add <path> <branch>",
            "TradeBot impact": "本線resultsは別。worktree再作成可",
        }

    if "__pycache__" in low or name == ".pytest_cache" or ".pytest_cache" in low:
        return {
            "classification": "python_cache",
            "regenerable": True,
            "delete_risk": "low",
            "currently_referenced": False,
            "recreation_command": "pytest / python import で再生成",
            "TradeBot impact": "なし",
        }

    if any(x in low for x in ("\\temp\\", "\\tmp\\", "ms_slim_", "demo_workspace", "demo_push")):
        return {
            "classification": "temp_or_demo",
            "regenerable": True,
            "delete_risk": "low",
            "currently_referenced": False,
            "recreation_command": "該当Phaseスクリプト再実行",
            "TradeBot impact": "なし",
        }

    if "results\\cache" in low or low.endswith("\\cache") or "\\results\\cache\\" in low:
        return {
            "classification": "old_cache",
            "regenerable": True,
            "delete_risk": "low",
            "currently_referenced": False,
            "recreation_command": "feature/vol cache rebuild",
            "TradeBot impact": "初回再計算が遅くなる程度",
        }

    if "push_jsonl" in low:
        return {
            "classification": "push_jsonl_raw_archive",
            "regenerable": False,
            "delete_risk": "high",
            "currently_referenced": False,
            "recreation_command": "ライブ再取得不可 — 外部退避のみ",
            "TradeBot impact": "raw PUSH。削除禁止。Plan B 外部退避候補（indexはC残し）",
        }

    if "logic_lab" in low:
        return {
            "classification": "old_replay_research",
            "regenerable": True,
            "delete_risk": "medium",
            "currently_referenced": False,
            "recreation_command": "logic_lab / 該当研究スクリプト再実行",
            "TradeBot impact": "研究中間。結論レポートは reports に残す",
        }

    if "recovery_quarantine" in low:
        return {
            "classification": "recovery_quarantine",
            "regenerable": False,
            "delete_risk": "high",
            "currently_referenced": True,
            "recreation_command": "",
            "TradeBot impact": "Recovery関連 — 削除禁止（確認後のみarchive）",
        }

    if any(x in low for x in ("phase551_", "phase558_", "phase588_", "phase540_", "replay")):
        return {
            "classification": "old_replay_research",
            "regenerable": True,
            "delete_risk": "medium",
            "currently_referenced": False,
            "recreation_command": "該当Phase replay スクリプト再実行",
            "TradeBot impact": "中間出力のみ。結論レポートは reports に残す",
        }

    if name in {".venv", "venv", "env"} or "\\site-packages\\" in low:
        return {
            "classification": "python_venv",
            "regenerable": True,
            "delete_risk": "high",
            "currently_referenced": "unknown",
            "recreation_command": "python -m venv .venv && pip install -r requirements.txt",
            "TradeBot impact": "使用中venvなら実行不可になる — 要確認",
        }

    if "\\build\\" in low or "\\dist\\" in low or name.endswith(".egg-info"):
        return {
            "classification": "build_artifact",
            "regenerable": True,
            "delete_risk": "low",
            "currently_referenced": False,
            "recreation_command": "build 再実行",
            "TradeBot impact": "なし",
        }

    return {
        "classification": "tradebot_other",
        "regenerable": False,
        "delete_risk": "high",
        "currently_referenced": "unknown",
        "recreation_command": "",
        "TradeBot impact": "要個別確認",
    }


def is_git_tracked(path: Path) -> Optional[bool]:
    try:
        rel = path.relative_to(REPO)
    except ValueError:
        return None
    r = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--error-unmatch", str(rel)],
        capture_output=True,
        timeout=30,
    )
    return r.returncode == 0


def inventory_tradebot_depth4() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Single-walk aggregate to depth<=4 under tradebotfile."""
    keep = paper_keep_days()
    # key -> stats; keys are paths at depth 0..4
    agg: dict[str, dict[str, Any]] = {}
    candidates: list[dict[str, Any]] = []

    def bump(path_str: str, size: int, mtime: float) -> None:
        slot = agg.setdefault(
            path_str,
            {"path": path_str, "size_bytes": 0, "file_count": 0, "last_modified": 0.0},
        )
        slot["size_bytes"] += size
        slot["file_count"] += 1
        if mtime > slot["last_modified"]:
            slot["last_modified"] = mtime

    for root, dirs, files in os.walk(REPO, onerror=lambda _e: None):
        root_p = Path(root)
        try:
            parts = root_p.relative_to(REPO).parts
        except ValueError:
            continue
        # Size .git as a single depth-1 bucket; do not walk objects
        if parts and parts[0] == ".git":
            dirs.clear()
            continue

        # Prefer walking; for very deep non-interesting trees, still attribute to depth4 key
        interesting = any(
            x in str(Path(*parts)).lower()
            for x in (
                "worktree",
                "_w",
                "__pycache__",
                ".pytest_cache",
                "cache",
                "temp",
                "demo_",
                "ms_slim",
                "phase551",
                "phase558",
                "phase588",
                "phase540",
                "small_paper",
                "research",
                "results",
                "kabu_native",
            )
        )
        if len(parts) >= 6 and not interesting:
            dirs.clear()

        for name in files:
            fp = root_p / name
            try:
                st = fp.stat()
            except OSError:
                continue
            size, mtime = st.st_size, st.st_mtime
            # attribute to every ancestor depth 0..min(4,len)
            bump(str(REPO), size, mtime)
            for d in range(1, min(4, len(parts)) + 1):
                bump(str(REPO.joinpath(*parts[:d])), size, mtime)
            if len(parts) >= 4:
                # file under deeper path already counted in depth4 prefix
                pass
            elif len(parts) < 4:
                # also count the file's own path if file sits within depth4
                bump(str(fp), size, mtime)

    # .git size once
    git_dir = REPO / ".git"
    if git_dir.exists():
        sz, n, mt = dir_size(git_dir, max_files=250_000)
        agg[str(git_dir)] = {
            "path": str(git_dir),
            "size_bytes": sz,
            "file_count": n,
            "last_modified": mt or 0.0,
        }
        # add into repo total if not already walked
        slot = agg.setdefault(
            str(REPO),
            {"path": str(REPO), "size_bytes": 0, "file_count": 0, "last_modified": 0.0},
        )
        # avoid double-count if walk skipped .git — add git size to repo total
        # (walk skipped .git contents, so safe to add)
        slot["size_bytes"] += sz
        slot["file_count"] += n
        if mt and mt > slot["last_modified"]:
            slot["last_modified"] = mt

    inv_rows: list[dict[str, Any]] = []
    for key, slot in agg.items():
        p = Path(key)
        try:
            rel_parts = p.relative_to(REPO).parts if p != REPO else ()
        except ValueError:
            continue
        depth = len(rel_parts)
        if depth > 4 and p.is_file():
            continue
        if depth > 4:
            continue
        meta = classify_tradebot(p, keep)
        sz = int(slot["size_bytes"])
        inv_rows.append(
            {
                "path": key,
                "depth": depth,
                "size_gb": gb(sz),
                "size_bytes": sz,
                "file_count": slot["file_count"],
                "last_modified": fmt_mtime(slot["last_modified"] or None),
                **meta,
                "git_tracked": True if (rel_parts and rel_parts[0] == ".git") else "",
            }
        )
        if meta["delete_risk"] != "FORBIDDEN" and meta["regenerable"] and sz >= 1_000_000:
            candidates.append(
                {
                    "path": key,
                    "size_gb": gb(sz),
                    "size_bytes": sz,
                    "last_modified": fmt_mtime(slot["last_modified"] or None),
                    "classification": meta["classification"],
                    "regenerable": True,
                    "git_tracked": False,
                    "currently_referenced": meta["currently_referenced"],
                    "delete_risk": meta["delete_risk"],
                    "recreation_command": meta["recreation_command"],
                    "TradeBot impact": meta["TradeBot impact"],
                }
            )
        if meta["classification"] == "paper_session_archive_candidate" and sz >= 1_000_000:
            candidates.append(
                {
                    "path": key,
                    "size_gb": gb(sz),
                    "size_bytes": sz,
                    "last_modified": fmt_mtime(slot["last_modified"] or None),
                    "classification": meta["classification"],
                    "regenerable": False,
                    "git_tracked": False,
                    "currently_referenced": False,
                    "delete_risk": "medium",
                    "recreation_command": meta["recreation_command"],
                    "TradeBot impact": meta["TradeBot impact"],
                    "plan": "B_archive",
                }
            )

    inv_rows.sort(key=lambda r: (-int(r["size_bytes"]), int(r["depth"])))
    cand_map = {c["path"]: c for c in candidates}
    candidates = sorted(cand_map.values(), key=lambda r: -int(r["size_bytes"]))
    return inv_rows, candidates


def add_known_tradebot_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = paper_keep_days()
    extras = [
        REPO / "_w14_shadow_replay",
        REPO / "phase629_head_worktree",
        Path(r"C:\Users\yhach\Documents\tradebotfile-phase629-baseline"),
        NATIVE / "results" / "cache",
        NATIVE / "temp",
        NATIVE / ".pytest_cache",
    ]
    # orphan-like dirs next to repo
    parent = REPO.parent
    for p in parent.glob("tradebotfile*"):
        if p.resolve() != REPO.resolve():
            extras.append(p)
    for p in parent.glob("_w*"):
        extras.append(p)
    # old paper days
    paper = NATIVE / "results" / "small_paper"
    if paper.is_dir():
        for d in paper.iterdir():
            if d.is_dir() and d.name.isdigit() and d.name not in keep:
                extras.append(d)
    # old research dumps / logic_lab / push_jsonl
    extras.append(NATIVE / "data" / "push_jsonl")
    research = NATIVE / "results" / "research"
    if research.is_dir():
        for d in research.iterdir():
            if d.is_dir() and any(
                x in d.name
                for x in ("phase551", "phase558", "phase588", "phase540", "replay", "logic_lab")
            ):
                extras.append(d)
    # demo workspaces
    reports = NATIVE / "results" / "reports"
    if reports.is_dir():
        for p in reports.rglob("demo_workspace"):
            extras.append(p)
        for p in reports.rglob("demo_push_e2e"):
            extras.append(p)

    by_path = {c["path"]: c for c in candidates}
    for p in extras:
        if not p.exists():
            continue
        key = str(p)
        if key in by_path:
            continue
        sz, n, mt = dir_size(p, max_files=250_000)
        if sz < 100_000:
            continue
        meta = classify_tradebot(p, keep)
        by_path[key] = {
            "path": key,
            "size_gb": gb(sz),
            "size_bytes": sz,
            "last_modified": fmt_mtime(mt),
            "classification": meta["classification"],
            "regenerable": meta["regenerable"],
            "git_tracked": False,
            "currently_referenced": meta["currently_referenced"],
            "delete_risk": meta["delete_risk"],
            "recreation_command": meta["recreation_command"],
            "TradeBot impact": meta["TradeBot impact"],
        }
    return sorted(by_path.values(), key=lambda r: -int(r["size_bytes"]))


# ---------------------------------------------------------------------------
# P0-2 Git audit
# ---------------------------------------------------------------------------

def git_audit() -> dict[str, Any]:
    porcelain = _run_git("worktree", "list", "--porcelain")
    count_objects = _run_git("count-objects", "-vH")
    merged = _run_git("branch", "--merged")
    no_merged = _run_git("branch", "--no-merged")

    registered: list[dict[str, Any]] = []
    cur: dict[str, Any] = {}
    for line in porcelain.splitlines():
        line = line.strip()
        if not line:
            if cur:
                registered.append(cur)
                cur = {}
            continue
        if line.startswith("worktree "):
            cur = {"path": line.split(" ", 1)[1], "head": "", "branch": "", "detached": False}
        elif line.startswith("HEAD "):
            cur["head"] = line.split(" ", 1)[1]
        elif line.startswith("branch "):
            cur["branch"] = line.split(" ", 1)[1]
        elif line == "detached":
            cur["detached"] = True
    if cur:
        registered.append(cur)

    # discover orphan-like directories
    orphans = []
    for p in list(REPO.glob("_w*")) + list(REPO.glob("*worktree*")):
        orphans.append(str(p))
    sibling = Path(r"C:\Users\yhach\Documents\tradebotfile-phase629-baseline")
    if sibling.exists():
        orphans.append(str(sibling))

    reg_paths = {str(Path(r["path"]).resolve()).lower() for r in registered if r.get("path")}
    classified = []
    for r in registered:
        p = Path(r["path"])
        exists = p.exists()
        sz, _, mt = dir_size(p, max_files=150_000) if exists else (0, 0, None)
        is_main = Path(r["path"]).resolve() == REPO.resolve()
        classified.append(
            {
                **r,
                "exists": exists,
                "size_gb": gb(sz),
                "last_modified": fmt_mtime(mt),
                "category": "active worktree" if is_main else ("registered but unused" if exists else "registered missing"),
                "removable_after_archive": (not is_main) and exists,
                ".git_direct_delete_forbidden": True,
            }
        )

    orphan_rows = []
    for op in orphans:
        p = Path(op)
        key = str(p.resolve()).lower() if p.exists() else op.lower()
        if key in reg_paths:
            cat = "active worktree" if Path(op).resolve() == REPO.resolve() else "registered but unused"
        else:
            cat = "orphan directory"
        sz, _, mt = dir_size(p, max_files=150_000) if p.exists() else (0, 0, None)
        orphan_rows.append(
            {
                "path": op,
                "category": cat,
                "size_gb": gb(sz),
                "last_modified": fmt_mtime(mt),
                "removable_after_archive": cat in ("orphan directory", "registered but unused"),
            }
        )

    return {
        "worktree_list_porcelain": porcelain,
        "count_objects": count_objects,
        "branch_merged": merged,
        "branch_no_merged": no_merged,
        "registered_worktrees": classified,
        "directory_scan": orphan_rows,
        "note": "No worktree remove executed. Never delete .git directly.",
        "git_garbage_tmp_obj": "7.71 MiB garbage tmp_obj noted by count-objects (not deleted)",
    }


# ---------------------------------------------------------------------------
# P0-3 Cursor inventory
# ---------------------------------------------------------------------------

CURSOR_SAFE = {"cache", "cacheddata", "code cache", "gpucache", "logs", "crashpad", "crash reports", "blob_storage"}
CURSOR_STOP_OK = {"cache", "cacheddata", "code cache", "gpucache"}
CURSOR_HISTORY = {"workspacesstorage", "workspaceStorage", "globalstorage", "User"}
CURSOR_AGENT = {"checkpoints", "agent", "chat", "composer", "anysphere", "cursor-agent"}


def classify_cursor(path: Path, rel: str) -> str:
    low = rel.lower().replace("\\", "/")
    base = path.name.lower()
    if base in {"cache", "cacheddata", "code cache", "gpucache"} or any(
        x in low for x in ("/cache", "cacheddata", "/code cache", "/gpucache")
    ):
        if "globalstorage" in low or "workspacestorage" in low:
            pass  # fall through
        else:
            return "安全に再生成可能"
    if base in {"logs", "crashpad"} or "crash" in low or low.endswith("/logs") or "/logs/" in low:
        return "Cursor停止後なら削除可能"
    if "state.vscdb" in low or base.startswith("state.vscdb"):
        return "TradeBot Agent履歴依存"
    if "checkpoint" in low or "agent" in low or "chat" in low or "composer" in low or "cursor-commits" in low:
        return "TradeBot Agent履歴依存"
    if "workspacestorage" in low or "globalstorage" in low:
        return "履歴消失リスクあり"
    if "snapshots" in low:
        return "履歴消失リスクあり"
    if low.startswith("user/") or base in {"preferences", "settings.json", "keybindings.json", "user"}:
        return "設定消失リスクあり"
    if "indexed" in low or "index" in low:
        return "Cursor停止後なら削除可能"
    return "要個別確認"


def inventory_cursor() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not CURSOR.exists():
        return rows
    # top-level + important children
    targets: list[Path] = [CURSOR]
    try:
        for child in CURSOR.iterdir():
            targets.append(child)
            if child.is_dir() and child.name in {"User", "Cache", "CachedData", "Code Cache", "GPUCache", "logs", "Crashpad", "blob_storage", "partitions", "Local Storage", "Session Storage"}:
                try:
                    for c2 in list(child.iterdir())[:40]:
                        targets.append(c2)
                except OSError:
                    pass
            if child.name == "User" and child.is_dir():
                for sub in ("globalStorage", "workspaceStorage", "History", "snippets"):
                    sp = child / sub
                    if sp.exists():
                        targets.append(sp)
                        if sp.is_dir() and sub in ("globalStorage", "workspaceStorage"):
                            try:
                                for c2 in list(sp.iterdir())[:40]:
                                    targets.append(c2)
                            except OSError:
                                pass
                for db_name in ("state.vscdb", "state.vscdb.backup", "state.vscdb-wal", "state.vscdb-shm"):
                    dbp = child / "globalStorage" / db_name
                    if dbp.exists():
                        targets.append(dbp)
    except OSError:
        pass

    seen = set()
    for p in targets:
        key = str(p)
        if key in seen:
            continue
        seen.add(key)
        try:
            rel = str(p.relative_to(CURSOR))
        except ValueError:
            rel = p.name
        sz, n, mt = dir_size(p, max_files=200_000)
        cls = classify_cursor(p, rel)
        rows.append(
            {
                "path": str(p),
                "rel": rel,
                "size_gb": gb(sz),
                "size_bytes": sz,
                "file_count": n,
                "last_modified": fmt_mtime(mt),
                "classification": cls,
                "bulk_delete_forbidden": cls
                in {"履歴消失リスクあり", "設定消失リスクあり", "TradeBot Agent履歴依存", "削除禁止"},
                "plan_a_safe": cls == "安全に再生成可能",
            }
        )
    rows.sort(key=lambda r: -int(r["size_bytes"]))
    return rows


# ---------------------------------------------------------------------------
# P0-4 Videos
# ---------------------------------------------------------------------------

def file_sha256_prefix(path: Path, *, limit: int = 8 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            left = limit
            while left > 0:
                chunk = fh.read(min(1024 * 1024, left))
                if not chunk:
                    break
                h.update(chunk)
                left -= len(chunk)
            # if small file, hash is complete; for large, also mix size+mtime
            st = path.stat()
            if st.st_size > limit:
                h.update(str(st.st_size).encode())
                h.update(b"|partial")
        return h.hexdigest()
    except OSError:
        return ""


def inventory_videos() -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Returns (top100_csv_rows, summary, plan_c_move_rows_until_180gb)."""
    files: list[dict[str, Any]] = []
    if not VIDEOS.exists():
        return [], {"error": "Videos path missing"}, []
    cutoff_30 = JST_NOW - timedelta(days=30)
    cutoff_90 = JST_NOW - timedelta(days=90)
    for root, dirs, names in os.walk(VIDEOS, onerror=lambda _e: None):
        for name in names:
            fp = Path(root) / name
            try:
                st = fp.stat()
            except OSError:
                continue
            mt = datetime.fromtimestamp(st.st_mtime).astimezone()
            files.append(
                {
                    "path": str(fp),
                    "size_bytes": st.st_size,
                    "size_gb": gb(st.st_size),
                    "ext": fp.suffix.lower(),
                    "last_modified": mt.isoformat(timespec="seconds"),
                    "mtime": st.st_mtime,
                    "stale_30d": mt < cutoff_30,
                    "stale_90d": mt < cutoff_90,
                }
            )
    files.sort(key=lambda x: -x["size_bytes"])
    top100 = files[:100]

    # duplicate detection among top200 by size then partial hash
    by_size: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for f in files[:500]:
        by_size[int(f["size_bytes"])].append(f)
    dup_groups = []
    for sz, group in by_size.items():
        if len(group) < 2 or sz < 10_000_000:
            continue
        hashes: dict[str, list[str]] = defaultdict(list)
        for g in group[:10]:
            digest = file_sha256_prefix(Path(g["path"]))
            if digest:
                hashes[digest].append(g["path"])
        for digest, paths in hashes.items():
            if len(paths) >= 2:
                dup_groups.append({"size_bytes": sz, "sha256_partial": digest, "paths": paths})

    def enrich(f: dict[str, Any]) -> dict[str, Any]:
        return {
            **{k: f[k] for k in ("path", "size_gb", "size_bytes", "ext", "last_modified", "stale_30d", "stale_90d")},
            "identical_hash_duplicate": any(f["path"] in g["paths"] for g in dup_groups),
            "external_drive_archive_candidate": True,
            "cloud_archive_candidate": f["size_gb"] <= 20,
            "compression_estimate_gb": round(f["size_gb"] * 0.05, 3),
            "delete_forbidden": True,
            "operation_suggested": "external_move_copy_then_verify",
        }

    move_rows_top100 = [enrich(f) for f in top100]

    # Plan C selection until >= 180GB
    plan_c_rows: list[dict[str, Any]] = []
    cum = 0.0
    for f in files:
        plan_c_rows.append(enrich(f))
        cum += float(f["size_gb"])
        if cum >= SAFE_NEED_GB:
            break

    summary = {
        "total_files_scanned": len(files),
        "total_size_gb": gb(sum(f["size_bytes"] for f in files)),
        "stale_30d_count": sum(1 for f in files if f["stale_30d"]),
        "stale_90d_count": sum(1 for f in files if f["stale_90d"]),
        "stale_90d_size_gb": gb(sum(f["size_bytes"] for f in files if f["stale_90d"])),
        "duplicate_groups": dup_groups[:50],
        "files_to_move_for_180gb": len(plan_c_rows),
        "cumulative_gb_for_180_target": round(cum, 3),
        "compression_note": "Videos already compressed (mp4/mkv/mov); compression savings typically <10%",
        "delete_forbidden": True,
    }
    return move_rows_top100, summary, plan_c_rows


# ---------------------------------------------------------------------------
# P0-5 other safe
# ---------------------------------------------------------------------------

def inventory_other_safe() -> list[dict[str, Any]]:
    rows = []
    for path, cls, risk, regenerable in [
        (TEMP, "temp_files", "low", True),
        (RECYCLE, "recycle_bin", "medium", False),
        (DOWNLOADS, "downloads_review_only", "high", False),
        (PIP, "pip_cache", "low", True),
    ]:
        sz, n, mt = dir_size(path, max_files=300_000) if path.exists() else (0, 0, None)
        rows.append(
            {
                "path": str(path),
                "size_gb": gb(sz),
                "size_bytes": sz,
                "file_count": n,
                "last_modified": fmt_mtime(mt),
                "classification": cls,
                "regenerable": regenerable,
                "delete_risk": risk,
                "plan_a_eligible": cls in ("temp_files", "recycle_bin", "pip_cache"),
                "downloads_is_review_only": cls == "downloads_review_only",
                "executed": False,
            }
        )
    return rows


# ---------------------------------------------------------------------------
# Plans + manifest
# ---------------------------------------------------------------------------

def build_plans(
    tradebot_cands: list[dict[str, Any]],
    cursor_rows: list[dict[str, Any]],
    other_rows: list[dict[str, Any]],
    video_rows: list[dict[str, Any]],
    video_summary: dict[str, Any],
    git_info: dict[str, Any],
) -> tuple[dict, dict, dict, list[dict]]:
    # Plan A regenerable
    a_items = []
    for r in other_rows:
        if r.get("plan_a_eligible"):
            a_items.append(
                {
                    "path": r["path"],
                    "size_gb": r["size_gb"],
                    "size_bytes": r["size_bytes"],
                    "classification": r["classification"],
                    "regenerable": True,
                    "operation": "delete",
                    "delete_risk": r["delete_risk"],
                }
            )
    for r in cursor_rows:
        if r.get("plan_a_safe") and r["path"] != str(CURSOR) and r.get("size_bytes", 0) > 0:
            # only top-level safe caches, avoid double-count children if parent listed
            rel = str(r.get("rel") or "")
            if rel.count("\\") + rel.count("/") <= 1 or r["rel"] in {
                "Cache",
                "CachedData",
                "Code Cache",
                "GPUCache",
                "logs",
                "Crashpad",
                "blob_storage",
            }:
                a_items.append(
                    {
                        "path": r["path"],
                        "size_gb": r["size_gb"],
                        "size_bytes": r["size_bytes"],
                        "classification": "cursor_safe_cache",
                        "regenerable": True,
                        "operation": "delete_after_cursor_quit",
                        "delete_risk": "low",
                    }
                )
    for r in tradebot_cands:
        if r.get("regenerable") and r.get("delete_risk") in ("low", "medium"):
            # old_replay_research / logic_lab は Plan B（外部退避）へ。Aと二重計上しない。
            if r.get("classification") in {
                "git_worktree_or_orphan",
                "python_cache",
                "temp_or_demo",
                "old_cache",
                "build_artifact",
            }:
                a_items.append(
                    {
                        "path": r["path"],
                        "size_gb": r["size_gb"],
                        "size_bytes": r["size_bytes"],
                        "classification": r["classification"],
                        "regenerable": True,
                        "operation": "delete_or_worktree_remove",
                        "delete_risk": r["delete_risk"],
                        "recreation_command": r.get("recreation_command"),
                    }
                )

    # dedupe overlapping paths (prefer more specific / avoid parent+child double count)
    a_items = _dedupe_paths(a_items)
    a_gb = sum(float(x["size_gb"]) for x in a_items)
    plan_a = {
        "name": "Plan A — regenerable only",
        "items": a_items,
        "reducible_gb": round(a_gb, 3),
        "used_pct_after": used_pct_after(a_gb),
        "reaches_75pct": a_gb >= NEED_GB,
        "reaches_safe_180": a_gb >= SAFE_NEED_GB,
        "executed": False,
        "approval_required": True,
    }

    # Plan B archive old paper + old research (move, not delete)
    b_items = []
    for r in tradebot_cands:
        if r.get("classification") in {
            "paper_session_archive_candidate",
            "old_replay_research",
            "push_jsonl_raw_archive",
        } or r.get("plan") == "B_archive":
            b_items.append(
                {
                    "path": r["path"],
                    "size_gb": r["size_gb"],
                    "size_bytes": r["size_bytes"],
                    "classification": r["classification"],
                    "regenerable": r.get("regenerable", False),
                    "operation": "external_archive_move",
                    "destination_path": f"E:/TradeBotArchive/{Path(r['path']).name}",
                    "delete_risk": "high" if r.get("classification") == "push_jsonl_raw_archive" else "medium",
                    "canonical_index_remains_on_c": True,
                }
            )
    b_items = _dedupe_paths(b_items)
    b_gb = sum(float(x["size_gb"]) for x in b_items)
    plan_b = {
        "name": "Plan B — TradeBot old data external archive (no delete of canonical)",
        "items": b_items,
        "archive_gb": round(b_gb, 3),
        "combined_with_a_gb": round(a_gb + b_gb, 3),
        "used_pct_after_a_plus_b": used_pct_after(a_gb + b_gb),
        "reaches_75pct_with_a": (a_gb + b_gb) >= NEED_GB,
        "reaches_safe_180_with_a": (a_gb + b_gb) >= SAFE_NEED_GB,
        "note": "Move derived/old sessions only. Keep canonical index + seal manifests on C:.",
        "executed": False,
        "approval_required": True,
    }

    # Plan C videos — video_rows here must already be the 180GB selection list
    c_items = []
    for r in video_rows:
        c_items.append(
            {
                "path": r["path"],
                "size_gb": r["size_gb"],
                "size_bytes": r["size_bytes"],
                "ext": r.get("ext"),
                "last_modified": r.get("last_modified"),
                "operation": "external_move",
                "destination_path": f"E:/VideoArchive/{Path(r['path']).name}",
                "delete_forbidden_until_hash_ok": True,
            }
        )
    plan_c = {
        "name": "Plan C — Videos external archive (no TradeBot touch)",
        "items": c_items,
        "selected_gb": round(sum(float(x["size_gb"]) for x in c_items), 3),
        "selected_file_count": len(c_items),
        "total_videos_gb": video_summary.get("total_size_gb"),
        "gb_to_move_for_75pct_alone": round(NEED_GB, 3),
        "gb_to_move_for_safe_180_alone": SAFE_NEED_GB,
        "gb_to_move_if_plan_a_done_first": round(max(0.0, NEED_GB - a_gb), 3),
        "used_pct_after_180_video_move": used_pct_after(SAFE_NEED_GB),
        "used_pct_after_selected": used_pct_after(sum(float(x["size_gb"]) for x in c_items)),
        "delete_forbidden": True,
        "executed": False,
        "approval_required": True,
    }

    # Execution manifest (dry-run rows)
    manifest = []
    for it in a_items:
        manifest.append(
            {
                "plan": "A",
                "operation": it["operation"],
                "source_path": it["path"],
                "destination_path": "",
                "size_bytes": it["size_bytes"],
                "sha256": "",
                "regenerable": True,
                "rollback_method": "IRREVERSIBLE_DELETE — recreate via recreation_command / app rebuild",
                "risk": it.get("delete_risk", "low"),
                "approval_required": True,
                "executed": False,
            }
        )
    for it in b_items:
        manifest.append(
            {
                "plan": "B",
                "operation": "copy_then_hash_then_delete_source_SEPARATE_PHASE",
                "source_path": it["path"],
                "destination_path": it.get("destination_path", ""),
                "size_bytes": it["size_bytes"],
                "sha256": "COMPUTE_AT_EXECUTION",
                "regenerable": it.get("regenerable", False),
                "rollback_method": "copy back from destination after hash verify; source delete only in later approved phase",
                "risk": "medium",
                "approval_required": True,
                "executed": False,
                "move_sequence": "copy → SHA256 match → destination exists → (later phase) source delete",
            }
        )
    for it in c_items:
        manifest.append(
            {
                "plan": "C",
                "operation": "copy_then_hash_then_delete_source_SEPARATE_PHASE",
                "source_path": it["path"],
                "destination_path": it.get("destination_path", ""),
                "size_bytes": it["size_bytes"],
                "sha256": "COMPUTE_AT_EXECUTION",
                "regenerable": False,
                "rollback_method": "copy back from external archive; NEVER delete without hash OK",
                "risk": "high_user_media",
                "approval_required": True,
                "executed": False,
                "move_sequence": "copy → SHA256 match → destination exists → (later phase) source delete",
            }
        )

    return plan_a, plan_b, plan_c, manifest


def _dedupe_paths(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop a path if an ancestor path is already included (avoid double-count)."""
    items = sorted(items, key=lambda x: str(x["path"]).count("\\") + str(x["path"]).count("/"))
    kept: list[dict[str, Any]] = []
    kept_paths: list[str] = []
    for it in sorted(items, key=lambda x: -int(x.get("size_bytes") or 0)):
        p = str(Path(it["path"])).lower()
        if any(p == k or p.startswith(k.rstrip("\\") + "\\") for k in kept_paths):
            continue
        # also skip if this is parent of already kept? prefer children accuracy: if child kept, skip parent
        if any(k.startswith(p.rstrip("\\") + "\\") for k in kept_paths):
            continue
        kept.append(it)
        kept_paths.append(p)
    return kept


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    print("FIX3 dry-run inventory starting...", flush=True)
    print("P0-1 tradebotfile...", flush=True)
    tb_inv, tb_cands = inventory_tradebot_depth4()
    tb_cands = add_known_tradebot_candidates(tb_cands)
    print(f"  inventory rows={len(tb_inv)} candidates={len(tb_cands)}", flush=True)

    print("P0-2 git...", flush=True)
    git_info = git_audit()

    print("P0-3 cursor...", flush=True)
    cursor_rows = inventory_cursor()
    print(f"  cursor rows={len(cursor_rows)}", flush=True)

    print("P0-4 videos...", flush=True)
    video_top100, video_sum, video_plan_c = inventory_videos()
    print(
        f"  video top100={len(video_top100)} plan_c={len(video_plan_c)} "
        f"total_gb={video_sum.get('total_size_gb')}",
        flush=True,
    )

    print("P0-5 other safe...", flush=True)
    other_rows = inventory_other_safe()

    print("P0-6/7 plans+manifest...", flush=True)
    plan_a, plan_b, plan_c, manifest = build_plans(
        tb_cands, cursor_rows, other_rows, video_plan_c, video_sum, git_info
    )

    # Deep inventory combined
    deep = []
    for r in tb_inv[:200]:
        deep.append({**r, "source": "tradebotfile"})
    for r in cursor_rows[:100]:
        deep.append({**r, "source": "cursor"})
    for r in other_rows:
        deep.append({**r, "source": "other_safe"})
    deep.sort(key=lambda x: -int(x.get("size_bytes") or 0))

    # Answers — largest meaningful paths (depth 1..3), exclude repo root rollup
    tb_ranked = [
        r
        for r in tb_inv
        if 1 <= int(r.get("depth") or 0) <= 3 and Path(r["path"]).name != ".git"
    ]
    tb_ranked.sort(key=lambda r: -int(r["size_bytes"]))
    # prefer unique non-ancestor-dominated list for readability
    tb_top20 = []
    for r in tb_ranked:
        tb_top20.append(
            {
                "path": r["path"],
                "size_gb": r["size_gb"],
                "depth": r.get("depth"),
                "classification": r.get("classification"),
            }
        )
        if len(tb_top20) >= 20:
            break

    # Cursor: include User breakdown (globalStorage / state.vscdb)
    cur_ranked = sorted(cursor_rows, key=lambda r: -int(r.get("size_bytes") or 0))
    cursor_top20 = [
        {
            "path": r["path"],
            "size_gb": r["size_gb"],
            "classification": r.get("classification"),
            "bulk_delete_forbidden": r.get("bulk_delete_forbidden"),
        }
        for r in cur_ranked[:20]
    ]

    safe_regen_gb = round(
        plan_a["reducible_gb"]
        + sum(float(r["size_gb"]) for r in cursor_rows if r.get("classification") == "Cursor停止後なら削除可能" and "/" not in str(r.get("rel")).replace("\\", "/") and str(r.get("rel")) not in {"", "."}),
        3,
    )
    # avoid double count — use plan A as safe regenerable primary
    safe_regen_gb = plan_a["reducible_gb"]

    no_canonical_gb = round(
        plan_a["reducible_gb"]
        + sum(
            float(x["size_gb"])
            for x in plan_b["items"]
            if x.get("classification") == "old_replay_research"
        ),
        3,
    )
    # TradeBot canonical untouched = Plan A (no paper) + cursor safe + temp + regenerable worktrees + old research archive
    plan_a_no_paper = plan_a["reducible_gb"]
    # Plan B paper is archive not delete — also "canonical untouched" if index remains
    without_touching_canonical = round(plan_a["reducible_gb"] + plan_b["archive_gb"], 3)

    video_gb_for_75 = round(NEED_GB, 3)
    if plan_a["reducible_gb"] > 0:
        video_after_a = round(max(0.0, NEED_GB - plan_a["reducible_gb"]), 3)
    else:
        video_after_a = video_gb_for_75

    reaches_a = plan_a["reaches_75pct"]
    reaches_ab = plan_b["reaches_75pct_with_a"]

    if reaches_a:
        verdicts = ["PLAN_A_SUFFICIENT", "SAFE_180GB_PLAN_FOUND", "APPROVAL_REQUIRED"]
    elif reaches_ab:
        verdicts = ["PLAN_B_REQUIRED", "SAFE_180GB_PLAN_FOUND", "APPROVAL_REQUIRED"]
    elif float(plan_c.get("selected_gb") or 0) >= NEED_GB:
        verdicts = ["VIDEO_ARCHIVE_REQUIRED", "SAFE_180GB_PLAN_FOUND", "APPROVAL_REQUIRED"]
    else:
        verdicts = ["NO_SAFE_180GB_PLAN", "APPROVAL_REQUIRED"]
    # Note when A+B helps but still short
    if (not reaches_a) and (not reaches_ab) and plan_b["combined_with_a_gb"] >= 100:
        if "VIDEO_ARCHIVE_REQUIRED" in verdicts:
            pass  # primary path is videos
        verdicts = list(dict.fromkeys(verdicts + ["PLAN_B_REQUIRED"]))

    # safest fastest method
    if video_sum.get("total_size_gb", 0) >= SAFE_NEED_GB:
        safest = (
            "Plan C: Videos を外部ドライブへ copy→hash→(承認後)元削除。"
            "TradeBot/Cursor履歴に触れず、単一ディレクトリで180GB確保が最速。"
        )
    elif reaches_ab:
        safest = "Plan A の Temp/cache/worktree を先に承認削除し、不足分を Plan B 外部退避。"
    else:
        safest = "複合: Plan A + Plan B archive + Videos 不足分。"

    answers = {
        "1_tradebotfile_top20": tb_top20,
        "2_cursor_top20": cursor_top20,
        "3_safe_regenerable_total_gb": safe_regen_gb,
        "4_reducible_without_touching_canonical_gb": without_touching_canonical,
        "5_plan_a_reaches_75": reaches_a,
        "5_plan_a_gb": plan_a["reducible_gb"],
        "5_plan_a_used_pct_after": plan_a["used_pct_after"],
        "6_plan_a_plus_b_reaches_75": reaches_ab,
        "6_combined_gb": plan_b["combined_with_a_gb"],
        "6_used_pct_after": plan_b["used_pct_after_a_plus_b"],
        "7_videos_gb_to_move_for_75": video_gb_for_75,
        "7_videos_gb_if_plan_a_first": video_after_a,
        "8_safest_fastest_method": safest,
        "9_continue_research_free_space_method": (
            "Plan C で Videos 180GB+ を外部退避し C: を ~75% へ。"
            "TradeBot canonical / W43 parquet / 最新20日 Paper / reports はCに残す。"
            "追加で Plan A の regenerable を掃除すると研究用の再計算バッファも確保。"
        ),
        "10_no_delete_move_compress_executed": True,
    }

    report = {
        "phase": "Phase687W43B-FIX3",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "dry_run": True,
        "executed_operations": [],
        "disk": {
            "total_gb": round(TOTAL_GB, 2),
            "used_gb": round(USED_GB, 2),
            "used_pct": round(100.0 * USED_GB / TOTAL_GB, 2),
            "target_pct": TARGET_PCT,
            "target_used_gb": round(TARGET_USED_GB, 2),
            "need_gb": round(NEED_GB, 2),
            "safe_need_gb": SAFE_NEED_GB,
            "prior_0_323gb_insufficient": True,
        },
        "verdicts": verdicts,
        "required_answers": answers,
        "plan_a_summary": {
            "reducible_gb": plan_a["reducible_gb"],
            "used_pct_after": plan_a["used_pct_after"],
            "reaches_75": plan_a["reaches_75pct"],
        },
        "plan_b_summary": {
            "archive_gb": plan_b["archive_gb"],
            "combined_gb": plan_b["combined_with_a_gb"],
            "reaches_75_with_a": plan_b["reaches_75pct_with_a"],
        },
        "plan_c_summary": {
            "selected_gb": plan_c["selected_gb"],
            "videos_total_gb": plan_c["total_videos_gb"],
            "gb_for_75_alone": plan_c["gb_to_move_for_75pct_alone"],
        },
        "git_worktree_count": len(git_info.get("registered_worktrees") or []),
        "paper_keep_days": sorted(paper_keep_days(), reverse=True),
    }

    # write outputs
    _wc(OUT / "w43b_fix3_disk_deep_inventory.csv", deep)
    _wc(OUT / "w43b_fix3_tradebot_inventory.csv", tb_inv)
    # also write candidates into tradebot inventory file? separate — merge cand note into tradebot file already has classifications
    _wc(
        OUT / "w43b_fix3_tradebot_inventory.csv",
        tb_inv
        + [
            {**c, "depth": "", "file_count": "", "row_kind": "candidate"}
            for c in tb_cands
        ],
    )
    _wc(OUT / "w43b_fix3_cursor_inventory.csv", cursor_rows)
    _wc(OUT / "w43b_fix3_video_move_candidates.csv", video_top100)
    _wj(OUT / "w43b_fix3_git_worktree_audit.json", git_info)
    _wj(OUT / "w43b_fix3_plan_a.json", plan_a)
    _wj(OUT / "w43b_fix3_plan_b.json", plan_b)
    _wj(OUT / "w43b_fix3_plan_c.json", plan_c)
    _wc(OUT / "w43b_fix3_execution_manifest.csv", manifest)
    _wj(OUT / "w43b_fix3_report.json", report)

    md = f"""# Phase687W43B-FIX3 — Disk Remediation Plan (DRY-RUN)

## Verdict
`{' | '.join(verdicts)}`

**No delete / move / compress was executed.**

## Disk
| metric | value |
|--------|------:|
| total | {report['disk']['total_gb']} GB |
| used | {report['disk']['used_gb']} GB |
| used % | {report['disk']['used_pct']}% |
| 75% cap | {report['disk']['target_used_gb']} GB |
| need | {report['disk']['need_gb']} GB |
| safe target | {SAFE_NEED_GB} GB |

## Required answers

1. **tradebotfile top20**  
{json.dumps(tb_top20, ensure_ascii=False, indent=2)}

2. **Cursor top20**  
{json.dumps(cursor_top20, ensure_ascii=False, indent=2)}

3. **Safe regenerable total** — `{safe_regen_gb} GB` (Plan A)

4. **Without touching canonical** — `{without_touching_canonical} GB` (Plan A regenerable + Plan B archive of old/derived; indexes stay on C:)

5. **Plan A alone → 75%?** — `{reaches_a}` (`{plan_a['reducible_gb']} GB` → `{plan_a['used_pct_after']}%`)

6. **Plan A+B → 75%?** — `{reaches_ab}` (`{plan_b['combined_with_a_gb']} GB` → `{plan_b['used_pct_after_a_plus_b']}%`)

7. **Videos GB to move for 75%** — `{video_gb_for_75} GB` alone; `{video_after_a} GB` if Plan A applied first

8. **Safest / fastest** — {safest}

9. **Keep research going** — {answers['9_continue_research_free_space_method']}

10. **Nothing executed** — `{answers['10_no_delete_move_compress_executed']}`

## Plans
- Plan A regenerable: `{plan_a['reducible_gb']} GB`
- Plan B archive: `{plan_b['archive_gb']} GB`
- Plan C videos selected: `{plan_c['selected_gb']} GB` / library `{plan_c['total_videos_gb']} GB`

## Move sequence (approved later phase)
```
copy → SHA256 match → destination exists → (separate approved phase) source delete
```

## Artifacts
All under `results/reports/w43b_fix3_*`
"""
    _wm(OUT / "w43b_fix3_report.md", md)

    print(
        json.dumps(
            {
                "verdicts": verdicts,
                "plan_a_gb": plan_a["reducible_gb"],
                "plan_b_gb": plan_b["archive_gb"],
                "plan_c_gb": plan_c["selected_gb"],
                "executed": False,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
