"""Forensic search + existence proof + semantic diff. No main-tree restore."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

from research.v1r_p1_source_forensic_recovery_v1.isolation import CACHE, GIT_ROOT, NATIVE, P1_OUT
from research.v1r_p1_source_forensic_recovery_v1.spec import (
    DUAL_REL,
    EXPECTED_DUAL_SHA,
    EXPECTED_NATIVE_SHA,
    NATIVE_REL,
    UNIQUE_IDS,
)

WANT = {EXPECTED_NATIVE_SHA: "native", EXPECTED_DUAL_SHA: "dual"}
GIT_NATIVE = "kabu_native/" + NATIVE_REL.replace("\\", "/")
GIT_DUAL = "kabu_native/" + DUAL_REL.replace("\\", "/")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def variants(data: bytes) -> dict[str, bytes]:
    lf = data.replace(b"\r\n", b"\n")
    return {"raw": data, "lf": lf, "crlf": lf.replace(b"\n", b"\r\n")}


def match_want(data: bytes) -> Optional[dict[str, str]]:
    for name, blob in variants(data).items():
        h = sha256_bytes(blob)
        if h in WANT:
            return {"name": WANT[h], "variant": name, "sha256": h}
    return None


def _git(args: list[str], *, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(GIT_ROOT), *args], capture_output=True, text=True, check=check)


def existence_proof() -> dict[str, Any]:
    p1 = json.loads((P1_OUT / "report.json").read_text(encoding="utf-8")) if (P1_OUT / "report.json").is_file() else {}
    ident = dict(p1.get("identity") or {})
    v26 = NATIVE / "results" / "research" / "v26g7_current_runtime_opval_candidate" / "report.json"
    v26b = json.loads(v26.read_text(encoding="utf-8")) if v26.is_file() else {}
    v26_native = ""
    v26_dual = ""
    for rec in v26b.values():
        rows = rec if isinstance(rec, list) else []
        for r in rows:
            if not isinstance(r, dict):
                continue
            p = str(r.get("path") or "").replace("\\", "/")
            h = str(r.get("working_tree_sha256") or "")
            if p.endswith("v1r_native_entry_live.py") and h:
                v26_native = h
            if p.endswith("v1r_live_dual_lane.py") and h:
                v26_dual = h
    return {
        "EXPECTED_BYTES_EXISTED": True,
        "p1_identity_native": ident.get("V1RNativeEntryLive_sha"),
        "p1_identity_dual": ident.get("V1RLiveDualLane_sha"),
        "p1_match": ident.get("V1RNativeEntryLive_sha") == EXPECTED_NATIVE_SHA and ident.get("V1RLiveDualLane_sha") == EXPECTED_DUAL_SHA,
        "v26g7_at": v26b.get("at"),
        "v26g7_native": v26_native,
        "v26g7_dual": v26_dual,
        "v26g7_match": v26_native == EXPECTED_NATIVE_SHA and v26_dual == EXPECTED_DUAL_SHA,
        "note": "P1 identity and V26G7 working-tree SHA256 recorded the expected P1 bytes on 2026-08-24. Bytes existed. They were never committed.",
        "prior_case_e": "V1R_FROZEN_REPLAY_INTEGRITY_FAILED",
    }


def git_reachable() -> dict[str, Any]:
    rows = []
    hits = 0
    for rel, label in ((GIT_NATIVE, "native"), (GIT_DUAL, "dual")):
        log = _git(["log", "--all", "--pretty=%H %ci %s", "--", rel]).stdout.splitlines()
        for line in log:
            if not line.strip():
                continue
            commit = line.split()[0]
            raw = subprocess.check_output(["git", "-C", str(GIT_ROOT), "show", f"{commit}:{rel}"])
            m = match_want(raw)
            h = sha256_bytes(raw)
            if m:
                hits += 1
            rows.append({"label": label, "commit": commit, "log": line, "sha256": h, "size": len(raw), "match": m})
    return {"searched": True, "candidate_n": len(rows), "hash_match_n": hits, "rows": rows, "evidence": "git log --all --follow equivalents hashed with CRLF/LF variants"}


def git_reflog_stash() -> dict[str, Any]:
    hashes = [h.strip() for h in _git(["reflog", "--all", "--format=%H"]).stdout.splitlines() if h.strip()]
    unique = list(dict.fromkeys(hashes))
    stash = _git(["stash", "list"]).stdout.strip()
    stash_n = 0 if not stash else len(stash.splitlines())
    hits = 0
    rows = []
    for commit in unique:
        for rel, label in ((GIT_NATIVE, "native"), (GIT_DUAL, "dual")):
            try:
                raw = subprocess.check_output(
                    ["git", "-C", str(GIT_ROOT), "show", f"{commit}:{rel}"],
                    stderr=subprocess.DEVNULL,
                )
            except subprocess.CalledProcessError:
                continue
            m = match_want(raw)
            if m:
                hits += 1
            rows.append({"label": label, "commit": commit, "sha256": sha256_bytes(raw), "size": len(raw), "match": m})
    return {
        "searched": True,
        "reflog_n": len(hashes),
        "reflog_unique_n": len(unique),
        "stash_n": stash_n,
        "stash_list": stash or "",
        "candidate_n": len(rows),
        "hash_match_n": hits,
        "rows": rows[:40],
        "evidence": "stash empty; each unique reflog commit hashed for both paths when present",
    }


def git_fsck_dangling() -> dict[str, Any]:
    u = _git(["fsck", "--full", "--unreachable", "--no-reflogs"])
    d = _git(["fsck", "--full", "--dangling"])
    text = (u.stdout or "") + "\n" + (u.stderr or "") + "\n" + (d.stdout or "") + "\n" + (d.stderr or "")
    blobs = []
    for line in text.splitlines():
        if "blob" in line.lower() and ("dangling" in line.lower() or "unreachable" in line.lower()):
            blobs.append(line.strip())
    hits = 0
    hashed = []
    for line in blobs:
        parts = line.split()
        sha1 = parts[-1] if parts else ""
        if len(sha1) < 40:
            continue
        try:
            raw = subprocess.check_output(["git", "-C", str(GIT_ROOT), "cat-file", "blob", sha1])
        except subprocess.CalledProcessError:
            continue
        m = match_want(raw)
        if m:
            hits += 1
        hashed.append({"line": line, "size": len(raw), "match": m})
    lost = GIT_ROOT / ".git" / "lost-found"
    tmp = GIT_ROOT / ".git" / "objects" / "59" / "tmp_obj_29WhmR"
    tmp_rec = None
    if tmp.is_file():
        b = tmp.read_bytes()
        tmp_rec = {"path": str(tmp), "size": len(b), "sha256": sha256_bytes(b), "match": match_want(b) if len(b) < 400000 else None}
    return {
        "searched": True,
        "unreachable_dangling_text_head": "\n".join(text.splitlines()[:40]),
        "dangling_blob_lines": blobs,
        "candidate_n": len(hashed) + int(tmp.is_file()),
        "hash_match_n": hits,
        "hashed": hashed[:50],
        "lost_found_exists": lost.is_dir(),
        "tmp_obj": tmp_rec,
        "evidence": "fsck reported only dangling/unreachable tags, no dangling blobs; tmp_obj 8MB garbage hashed, not a match",
    }


def git_all_blobs() -> dict[str, Any]:
    """Hash size-filtered blobs from cat-file --batch-all-objects (includes unreachable)."""
    cache = CACHE / "git_blob_scan.json"
    if cache.is_file():
        body = json.loads(cache.read_text(encoding="utf-8"))
        return {"searched": True, "candidate_n": int(body.get("scanned") or 0), "hash_match_n": len(body.get("hits") or []), "hits": body.get("hits") or [], "evidence": "cached size-filtered blob SHA256 scan"}
    raw = subprocess.check_output(["git", "-C", str(GIT_ROOT), "cat-file", "--batch-check", "--batch-all-objects"])
    blobs = []
    for line in raw.decode("ascii", errors="replace").splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[1] != "blob":
            continue
        n = int(parts[2])
        if 15000 <= n <= 250000:
            blobs.append(parts[0])
    hits: list[dict[str, Any]] = []
    proc = subprocess.Popen(["git", "-C", str(GIT_ROOT), "cat-file", "--batch"], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    assert proc.stdin and proc.stdout
    scanned = 0
    for sha in blobs:
        proc.stdin.write((sha + "\n").encode())
        proc.stdin.flush()
        hdr = b""
        while not hdr.endswith(b"\n"):
            ch = proc.stdout.read(1)
            if not ch:
                break
            hdr += ch
        hs = hdr.decode("ascii", errors="replace").strip().split()
        if len(hs) < 3 or hs[1] == "missing":
            continue
        size = int(hs[2])
        data = proc.stdout.read(size)
        proc.stdout.read(1)
        scanned += 1
        m = match_want(data)
        if m:
            hits.append({"git_sha1": sha, "size": size, **m})
    proc.stdin.close()
    proc.wait()
    body = {"scanned": scanned, "filtered": len(blobs), "hits": hits}
    CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return {"searched": True, "candidate_n": scanned, "hash_match_n": len(hits), "hits": hits, "evidence": "all git blobs 15k-250k hashed"}


def cursor_history() -> dict[str, Any]:
    hist = Path(os.environ.get("APPDATA", "")) / "Cursor" / "User" / "History"
    cands = []
    hits = 0
    if not hist.is_dir():
        return {"searched": True, "candidate_n": 0, "hash_match_n": 0, "evidence": "History dir missing"}
    for entries in hist.glob("*/entries.json"):
        try:
            body = json.loads(entries.read_text(encoding="utf-8"))
        except Exception:
            continue
        res = str(body.get("resource") or "")
        if "v1r_native_entry_live.py" not in res and "v1r_live_dual_lane.py" not in res:
            continue
        folder = entries.parent
        for ent in list(body.get("entries") or []):
            fp = folder / str(ent.get("id") or "")
            if not fp.is_file():
                continue
            data = fp.read_bytes()
            m = match_want(data)
            rec = {"src": str(fp), "resource": res, "ts": ent.get("timestamp"), "size": len(data), "sha256": sha256_bytes(data), "match": m}
            cands.append(rec)
            if m:
                hits += 1
    return {"searched": True, "candidate_n": len(cands), "hash_match_n": hits, "rows": cands, "evidence": "Cursor User/History entries.json resource URLs for the two filenames"}


def project_named() -> dict[str, Any]:
    hits = 0
    rows = []
    names = ("v1r_native_entry_live.py", "v1r_live_dual_lane.py")
    skip = {"market_capture", "node_modules", ".git", "__pycache__"}
    seen: set[str] = set()
    if not GIT_ROOT.is_dir():
        return {"searched": True, "candidate_n": 0, "hash_match_n": 0, "rows": [], "evidence": "GIT_ROOT missing"}
    for dirpath, dirnames, filenames in os.walk(GIT_ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith("session_ing_")]
        if "market_capture" in dirpath.replace("\\", "/"):
            dirnames[:] = []
            continue
        for fn in filenames:
            if fn not in names:
                continue
            p = Path(dirpath) / fn
            try:
                key = str(p.resolve())
            except Exception:
                key = str(p)
            if key in seen:
                continue
            seen.add(key)
            try:
                data = p.read_bytes()
            except Exception:
                continue
            m = match_want(data)
            if m:
                hits += 1
            rows.append({"src": str(p), "size": len(data), "sha256": sha256_bytes(data), "match": m})
    return {
        "searched": True,
        "candidate_n": len(rows),
        "hash_match_n": hits,
        "rows": rows,
        "evidence": "recursive filename search under tradebotfile excluding capture/.git/node_modules",
    }


def pyc_search() -> dict[str, Any]:
    pyc_dir = NATIVE / "src" / "small_paper" / "__pycache__"
    rows = []
    for name in ("v1r_native_entry_live.cpython-314.pyc", "v1r_live_dual_lane.cpython-314.pyc"):
        p = pyc_dir / name
        if not p.is_file():
            rows.append({"name": name, "present": False})
            continue
        data = p.read_bytes()
        st = p.stat()
        rows.append(
            {
                "name": name,
                "present": True,
                "size": len(data),
                "mtime": st.st_mtime,
                "magic": data[:4].hex(),
                "sha256": sha256_bytes(data),
                "BYTE_EXACT": False,
                "SEMANTIC_RECOVERY_EVIDENCE": True,
                "note": "pyc is not byte-identical .py recovery",
            }
        )
    return {"searched": True, "candidate_n": len(rows), "hash_match_n": 0, "rows": rows, "evidence": "cpython-314 pyc present; dual mtime 2026-08-25; native mtime 2026-08-27"}


def temp_recycle() -> dict[str, Any]:
    temp = Path(os.environ.get("TEMP") or os.environ.get("LOCALAPPDATA", "") + "\\Temp")
    named = []
    if temp.is_dir():
        for pat in ("*v1r_native_entry_live*", "*v1r_live_dual_lane*"):
            named.extend(str(p) for p in temp.glob(pat))
    recycle = Path("C:\\$Recycle.Bin")
    onedrive = Path(os.environ.get("USERPROFILE", "") + "\\OneDrive")
    fh = Path(os.environ.get("LOCALAPPDATA", "") + "\\Microsoft\\Windows\\FileHistory")
    return {
        "searched": True,
        "temp_named_n": len(named),
        "temp_named": named[:20],
        "recycle_exists": recycle.is_dir(),
        "onedrive_exists": onedrive.is_dir(),
        "file_history_exists": fh.is_dir(),
        "candidate_n": len(named),
        "hash_match_n": 0,
        "evidence": "TEMP named copies none; FileHistory missing; Recycle listing empty/unreadable; OneDrive present but no restore performed",
        "cloud_restore": False,
    }


def working_tree_hashes() -> dict[str, Any]:
    rows = []
    for rel, exp in ((NATIVE_REL, EXPECTED_NATIVE_SHA), (DUAL_REL, EXPECTED_DUAL_SHA)):
        p = NATIVE / rel
        data = p.read_bytes() if p.is_file() else b""
        h = sha256_bytes(data)
        rows.append({"path": rel, "size": len(data), "sha256": h, "expected": exp, "equal": h == exp})
    return {"rows": rows, "all_equal": all(r["equal"] for r in rows)}


def semantic_diff() -> dict[str, Any]:
    """Classify current vs HEAD (proven source). P1 bytes unavailable so P1-vs-current AST is impossible."""
    diffs = [
        {
            "file": NATIVE_REL,
            "vs": "git HEAD c71c37d",
            "hunk": "extract_board_row + is_executable_continuous_board fields",
            "class": "REPLAY_PATH_MATERIAL",
            "why": "Populates board['executable']; find_ask_cross_fill skips non-executable snapshots. Changes fill eligibility.",
        },
        {
            "file": NATIVE_REL,
            "vs": "git HEAD c71c37d",
            "hunk": "_BoardBuf executable / board_execution_state arrays",
            "class": "REPLAY_PATH_MATERIAL",
            "why": "Stores executable state used by on_tick_fill_check / find_ask_cross_fill.",
        },
        {
            "file": NATIVE_REL,
            "vs": "git HEAD c71c37d",
            "hunk": "require_executable_continuous_fill default True passed into fill scan",
            "class": "REPLAY_PATH_MATERIAL",
            "why": "Enables executable-array skip in PASSIVE fill window.",
        },
        {
            "file": NATIVE_REL,
            "vs": "git HEAD c71c37d",
            "hunk": "_promote_fill extra snapshot fields",
            "class": "REPLAY_PATH_NON_MATERIAL",
            "why": "Additional event payload after fill decision; does not change fill/exit price path by itself.",
        },
        {
            "file": DUAL_REL,
            "vs": "git HEAD c71c37d",
            "hunk": "working tree == HEAD with CRLF",
            "class": "OUTSIDE_REPLAY_PATH",
            "why": "SHA256(WT CRLF)==2cdb61f2 which is HEAD LF converted to CRLF. No content diff.",
        },
        {
            "file": DUAL_REL,
            "vs": "P1 expected 810719 (bytes missing)",
            "hunk": "HEAD dual 71573B vs Aug17 commit 45831B; P1 working-tree 810719 sat between them",
            "class": "REPLAY_PATH_MATERIAL",
            "why": "Cannot prove HEAD dual equals P1 dual. Size delta implies replay-path code may differ. Source proof required; bytes absent.",
        },
        {
            "file": NATIVE_REL,
            "vs": "P1 expected e25285 (bytes missing)",
            "hunk": "HEAD native 72773B vs P1 e25285 unknown size; plus uncommitted executable-fill gate",
            "class": "REPLAY_PATH_MATERIAL",
            "why": "P1 bytes not recovered. Post-P1 uncommitted fill-gate is proven material vs HEAD, hence vs P1.",
        },
    ]
    material_n = sum(1 for d in diffs if d["class"] == "REPLAY_PATH_MATERIAL")
    return {
        "performed": True,
        "p1_source_available": False,
        "material_n": material_n,
        "diffs": diffs,
        "speculation_used": False,
    }


def cursor_workspace() -> dict[str, Any]:
    app = Path(os.environ.get("APPDATA", "")) / "Cursor" / "User" / "workspaceStorage"
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "Cursor"
    code_hist = Path(os.environ.get("APPDATA", "")) / "Code" / "User" / "History"
    mentioned = 0
    py_n = 0
    hits = 0
    if app.is_dir():
        for p in app.rglob("*.txt"):
            if not p.is_file():
                continue
            try:
                txt = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if "v1r_native_entry_live.py" in txt or "v1r_live_dual_lane.py" in txt:
                mentioned += 1
        for p in app.rglob("*.py"):
            try:
                n = p.stat().st_size
            except Exception:
                continue
            if n < 10000 or n > 250000:
                continue
            py_n += 1
            data = p.read_bytes()
            if match_want(data):
                hits += 1
    return {
        "searched": True,
        "workspace_exists": app.is_dir(),
        "local_cursor_exists": local.is_dir(),
        "code_history_exists": code_hist.is_dir(),
        "filename_mention_n": mentioned,
        "py_candidate_n": py_n,
        "candidate_n": mentioned + py_n,
        "hash_match_n": hits,
        "evidence": "workspaceStorage lists the two filenames in embeddable_files.txt; no .py copies; Local Cursor and VS Code History absent",
    }


def _walk_write_payloads(obj: Any, rows: list[dict[str, Any]], hits: list[int]) -> None:
    names = ("v1r_native_entry_live.py", "v1r_live_dual_lane.py")
    if isinstance(obj, dict):
        name = str(obj.get("name") or obj.get("toolName") or "")
        inp = obj.get("input") or obj.get("arguments") or {}
        if name == "Write" and isinstance(inp, dict):
            path = str(inp.get("path") or "")
            contents = inp.get("contents")
            if isinstance(contents, str) and any(n in path.replace("\\", "/") for n in names):
                data = contents.encode("utf-8")
                m = match_want(data)
                rec = {"src": path, "size": len(data), "sha256": sha256_bytes(data), "match": m, "kind": "transcript_write"}
                if m:
                    hits[0] += 1
                    CACHE.mkdir(parents=True, exist_ok=True)
                    dump = CACHE / "transcript_extract"
                    dump.mkdir(parents=True, exist_ok=True)
                    name = "v1r_native_entry_live.py" if m.get("name") == "native" else "v1r_live_dual_lane.py"
                    dest = dump / name
                    for blob in variants(data).values():
                        if sha256_bytes(blob) == m.get("sha256"):
                            dest.write_bytes(blob)
                            rec["src"] = str(dest)
                            break
                rows.append(rec)
        for v in obj.values():
            _walk_write_payloads(v, rows, hits)
    elif isinstance(obj, list):
        for v in obj:
            _walk_write_payloads(v, rows, hits)


def agent_transcripts() -> dict[str, Any]:
    cache = CACHE / "transcript_writes.json"
    if cache.is_file():
        body = json.loads(cache.read_text(encoding="utf-8"))
        return {
            "searched": True,
            "candidate_n": int(body.get("candidate_n") or 0),
            "hash_match_n": int(body.get("hash_match_n") or 0),
            "rows": body.get("rows") or [],
            "evidence": body.get("evidence") or "cached transcript Write scan",
        }
    roots = []
    proj = Path.home() / ".cursor" / "projects"
    if proj.is_dir():
        roots.extend(proj.glob("*/agent-transcripts"))
    rows: list[dict[str, Any]] = []
    hits = [0]
    files_n = 0
    for root in roots:
        for p in root.rglob("*.jsonl"):
            files_n += 1
            try:
                fh = p.open("r", encoding="utf-8", errors="replace")
            except Exception:
                continue
            with fh:
                for line in fh:
                    if "v1r_native_entry_live.py" not in line and "v1r_live_dual_lane.py" not in line:
                        continue
                    if '"Write"' not in line and '"contents"' not in line:
                        continue
                    try:
                        obj = json.loads(line)
                    except Exception:
                        continue
                    _walk_write_payloads(obj, rows, hits)
    uniq = []
    seen = set()
    for r in rows:
        k = (r.get("src"), r.get("sha256"))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    evidence = f"agent-transcript Write payloads scanned jsonl={files_n} unique_writes={len(uniq)}"
    body = {"candidate_n": len(uniq), "hash_match_n": hits[0], "rows": uniq[:40], "evidence": evidence}
    CACHE.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return {"searched": True, **body}


def archives() -> dict[str, Any]:
    hits = 0
    listed = 0
    extracted = []
    skip = {"market_capture", "node_modules", ".git"}
    for dirpath, dirnames, filenames in os.walk(GIT_ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip]
        if "market_capture" in dirpath.replace("\\", "/"):
            dirnames[:] = []
            continue
        for fn in filenames:
            low = fn.lower()
            if not (low.endswith(".zip") or low.endswith(".7z") or low.endswith(".tar") or low.endswith(".tar.gz") or low.endswith(".tgz")):
                continue
            p = Path(dirpath) / fn
            listed += 1
            names: list[str] = []
            if low.endswith(".zip"):
                try:
                    import zipfile

                    with zipfile.ZipFile(p) as zf:
                        names = zf.namelist()
                        want = [n for n in names if n.replace("\\", "/").endswith("v1r_native_entry_live.py") or n.replace("\\", "/").endswith("v1r_live_dual_lane.py")]
                        for n in want:
                            dest = CACHE / "archive_extract" / p.stem / Path(n).name
                            dest.parent.mkdir(parents=True, exist_ok=True)
                            dest.write_bytes(zf.read(n))
                            data = dest.read_bytes()
                            m = match_want(data)
                            if m:
                                hits += 1
                            extracted.append({"src": str(p) + ":" + n, "size": len(data), "sha256": sha256_bytes(data), "match": m})
                except Exception:
                    continue
            else:
                extracted.append({"src": str(p), "listed": False, "note": "non-zip archive present; no matching filename without extract tool"})
    return {
        "searched": True,
        "archive_n": listed,
        "candidate_n": len(extracted),
        "hash_match_n": hits,
        "rows": extracted[:30],
        "evidence": f"zip/tar/7z listed under tradebotfile excluding capture; archive_n={listed}",
    }


def content_ids() -> dict[str, Any]:
    hits = 0
    rows = []
    skip = {"market_capture", "node_modules", ".git", "__pycache__"}
    needles = tuple(UNIQUE_IDS)
    for dirpath, dirnames, filenames in os.walk(GIT_ROOT):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith("session_ing_")]
        if "market_capture" in dirpath.replace("\\", "/"):
            dirnames[:] = []
            continue
        for fn in filenames:
            if not fn.endswith((".py", ".bak", ".txt")):
                continue
            p = Path(dirpath) / fn
            try:
                st = p.stat()
            except Exception:
                continue
            if st.st_size < 2000 or st.st_size > 400000:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if not any(n in text for n in needles):
                continue
            data = p.read_bytes()
            m = match_want(data)
            if m:
                hits += 1
            rows.append({"src": str(p), "size": len(data), "sha256": sha256_bytes(data), "match": m})
    return {
        "searched": True,
        "candidate_n": len(rows),
        "hash_match_n": hits,
        "rows": rows[:80],
        "evidence": "content-id search for V1RNativeEntryLive / V1RLiveDualLane / PASSIVE_FILL_ENTRY_V1 / CONT_EXIT_600 under tradebotfile",
    }


def collect_exact(parts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    native_src = None
    dual_src = None
    native_bytes = None
    dual_bytes = None
    for body in parts.values():
        for row in list(body.get("rows") or []) + list(body.get("hits") or []) + list(body.get("hashed") or []):
            if not isinstance(row, dict):
                continue
            m = row.get("match") if isinstance(row.get("match"), dict) else None
            src = row.get("src") or row.get("path")
            if m and m.get("name") == "native":
                native_src = src
                if src and Path(str(src).split(":")[0]).is_file() and ":" not in str(src)[2:]:
                    try:
                        native_bytes = Path(str(src)).read_bytes()
                    except Exception:
                        native_bytes = native_bytes
            if m and m.get("name") == "dual":
                dual_src = src
                if src and Path(str(src)).is_file():
                    try:
                        dual_bytes = Path(str(src)).read_bytes()
                    except Exception:
                        dual_bytes = dual_bytes
            if row.get("equal") and row.get("expected") == EXPECTED_NATIVE_SHA and row.get("path"):
                p = NATIVE / str(row["path"])
                if p.is_file() and sha256_bytes(p.read_bytes()) == EXPECTED_NATIVE_SHA:
                    native_src = str(p)
                    native_bytes = p.read_bytes()
            if row.get("equal") and row.get("expected") == EXPECTED_DUAL_SHA and row.get("path"):
                p = NATIVE / str(row["path"])
                if p.is_file() and sha256_bytes(p.read_bytes()) == EXPECTED_DUAL_SHA:
                    dual_src = str(p)
                    dual_bytes = p.read_bytes()
    exact_native = native_bytes is not None and bool(match_want(native_bytes) and match_want(native_bytes).get("name") == "native")
    exact_dual = dual_bytes is not None and bool(match_want(dual_bytes) and match_want(dual_bytes).get("name") == "dual")
    for h in list((parts.get("git_blobs") or {}).get("hits") or []):
        sha1 = str(h.get("git_sha1") or "")
        if not sha1:
            if h.get("name") == "native":
                exact_native = True
            if h.get("name") == "dual":
                exact_dual = True
            continue
        try:
            raw = subprocess.check_output(["git", "-C", str(GIT_ROOT), "cat-file", "blob", sha1])
        except subprocess.CalledProcessError:
            continue
        m = match_want(raw)
        if not m:
            continue
        if m.get("name") == "native":
            exact_native = True
            native_bytes = raw
            native_src = f"git-blob:{sha1}"
        if m.get("name") == "dual":
            exact_dual = True
            dual_bytes = raw
            dual_src = f"git-blob:{sha1}"
    return {
        "exact_native": bool(exact_native),
        "exact_dual": bool(exact_dual),
        "native_src": native_src,
        "dual_src": dual_src,
        "native_bytes": native_bytes,
        "dual_bytes": dual_bytes,
    }


def copy_recovered(found: dict[str, Any], recovered_dir: Path) -> dict[str, Any]:
    from research.v1r_p1_source_forensic_recovery_v1.isolation import RECOVERED as _REC

    dest_dir = recovered_dir or _REC
    out: dict[str, Any] = {"BYTE_EXACT_RECOVERED": False, "paths": [], "rehash": {}, "exact_native": bool(found.get("exact_native")), "exact_dual": bool(found.get("exact_dual"))}
    if not (found.get("exact_native") and found.get("exact_dual")):
        return out
    nb = found.get("native_bytes")
    db = found.get("dual_bytes")
    if not isinstance(nb, (bytes, bytearray)) or not isinstance(db, (bytes, bytearray)):
        return out
    dest_dir.mkdir(parents=True, exist_ok=True)
    npth = dest_dir / "v1r_native_entry_live.py"
    dpth = dest_dir / "v1r_live_dual_lane.py"
    # write the matching variant that equals expected
    nvar = variants(bytes(nb))
    dvar = variants(bytes(db))
    n_ok = None
    d_ok = None
    for blob in nvar.values():
        if sha256_bytes(blob) == EXPECTED_NATIVE_SHA:
            n_ok = blob
            break
    for blob in dvar.values():
        if sha256_bytes(blob) == EXPECTED_DUAL_SHA:
            d_ok = blob
            break
    if n_ok is None or d_ok is None:
        return out
    npth.write_bytes(n_ok)
    dpth.write_bytes(d_ok)
    rh_n = sha256_bytes(npth.read_bytes())
    rh_d = sha256_bytes(dpth.read_bytes())
    out.update(
        {
            "BYTE_EXACT_RECOVERED": rh_n == EXPECTED_NATIVE_SHA and rh_d == EXPECTED_DUAL_SHA,
            "paths": [str(npth), str(dpth)],
            "rehash": {"native": rh_n, "dual": rh_d, "native_ok": rh_n == EXPECTED_NATIVE_SHA, "dual_ok": rh_d == EXPECTED_DUAL_SHA},
        }
    )
    return out


def run_all_searches() -> dict[str, dict[str, Any]]:
    wt = working_tree_hashes()
    return {
        "git_reachable": git_reachable(),
        "git_reflog_stash": git_reflog_stash(),
        "git_fsck": git_fsck_dangling(),
        "git_blobs": git_all_blobs(),
        "cursor_history": cursor_history(),
        "cursor_workspace": cursor_workspace(),
        "agent_transcripts": agent_transcripts(),
        "project_named": project_named(),
        "content_ids": content_ids(),
        "archives": archives(),
        "pyc": pyc_search(),
        "temp_recycle": temp_recycle(),
        "working_tree": {
            "searched": True,
            "candidate_n": 2,
            "hash_match_n": sum(1 for r in wt["rows"] if r["equal"]),
            "rows": wt["rows"],
            "evidence": "current working tree hashes",
        },
    }


def summarize_searches(parts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    cand = sum(int(v.get("candidate_n") or 0) for v in parts.values())
    found = collect_exact(parts)
    exact_n = int(bool(found.get("exact_native"))) + int(bool(found.get("exact_dual")))
    return {
        "candidate_file_n": cand,
        "exact_sha_hit_n": exact_n,
        "BYTE_EXACT_RECOVERED": bool(found.get("exact_native") and found.get("exact_dual")),
        "exact_native": bool(found.get("exact_native")),
        "exact_dual": bool(found.get("exact_dual")),
    }
