"""
既存の results/ 直下に散らばった replay/sweep 出力を
  results/YYYYMMDD/<元フォルダ名>/
に移動する。paper_trade は results/paper_trade/YYYYMMDD/ のまま触らない。

Loose ファイルは同一 run の接尾辞を剥がしてサブフォルダにまとめる。
symbol_scores_latest.json と、既に 8 桁日付だけのバケットフォルダはスキップ。
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

RE_YMD = re.compile(r"(20\d{6})_\d{6}")
RE_YMD_ONLY = re.compile(r"20\d{6}")


def extract_ymd(name: str) -> str | None:
    m = RE_YMD.search(name)
    if m:
        return m.group(1)
    m2 = RE_YMD_ONLY.search(name)
    if m2:
        return m2.group(0)
    return None


def strip_to_run_key(filename: str) -> str:
    for suf in (
        "_symbol_scores.json",
        "_signals.csv",
        ".json",
        ".txt",
        ".csv",
    ):
        if filename.endswith(suf):
            return filename[: -len(suf)]
    return filename


def is_date_bucket(name: str) -> bool:
    return bool(re.fullmatch(r"20\d{6}", name))


def migrate(*, results_root: Path, dry_run: bool) -> int:
    if not results_root.is_dir():
        print(f"ERROR: not a directory: {results_root}", file=sys.stderr)
        return 2

    moved = 0
    skipped = 0

    entries = sorted(results_root.iterdir(), key=lambda p: p.name.lower())

    # 1) ディレクトリ（バケット・paper_trade・未日付付き run フォルダ）
    dirs = [p for p in entries if p.is_dir()]
    files = [p for p in entries if p.is_file()]

    for src in dirs:
        name = src.name
        if name.startswith("."):
            skipped += 1
            continue
        if name == "paper_trade":
            print(f"[skip] paper_trade/ （構成維持）")
            skipped += 1
            continue
        if is_date_bucket(name):
            print(f"[skip] 日付バケット: {name}/")
            skipped += 1
            continue

        ymd = extract_ymd(name)
        if not ymd:
            print(f"[WARN] 日付を推定できずスキップ: {src}")
            skipped += 1
            continue

        dest_dir = results_root / ymd
        dest = dest_dir / name
        if dest.resolve() == src.resolve():
            skipped += 1
            continue
        if dest.exists():
            print(f"[WARN] 宛先が既に存在: {dest}")
            skipped += 1
            continue

        print(f"{'[dry-run] ' if dry_run else ''}MOVE DIR\n  {src}\n  -> {dest}")
        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dest))
        moved += 1

    # 2) ルートファイル → YYYYMM/DD/<run_key>/
    by_bucket: dict[tuple[str, str], list[Path]] = {}
    for src in files:
        name = src.name
        if name.startswith("."):
            skipped += 1
            continue
        if name == "symbol_scores_latest.json":
            print(f"[skip] {name}（ルート維持）")
            skipped += 1
            continue

        ymd = extract_ymd(name)
        if not ymd:
            print(f"[WARN] 日付を推定できずスキップ（ファイル）: {src}")
            skipped += 1
            continue

        run_key = strip_to_run_key(name)
        if not run_key:
            run_key = src.stem
        by_bucket.setdefault((ymd, run_key), []).append(src)

    for (ymd, run_key), paths in sorted(by_bucket.items()):
        for src in paths:
            dest_dir = results_root / ymd / run_key
            # ルートの vwap_sweep_summary_<stamp>.txt は日付配下の vwap_sweep_<stamp>/ へ（互換コピーと同じ塊）
            m_v = re.fullmatch(r"vwap_sweep_summary_(20\d{6}_\d{6})\.txt", src.name)
            if m_v:
                sweep_dir = results_root / ymd / f"vwap_sweep_{m_v.group(1)}"
                if sweep_dir.is_dir():
                    dest_dir = sweep_dir
            dest = dest_dir / src.name
            if dest.exists():
                print(f"[WARN] スキップ（ファイル既存）: {dest}")
                skipped += 1
                continue
            print(f"{'[dry-run] ' if dry_run else ''}MOVE FILE\n  {src}\n  -> {dest}")
            if not dry_run:
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
            moved += 1

    print(f"\n完了: moved={moved} skipped={skipped} dry_run={dry_run}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="results/ 直下を日付フォルダ配下へ移動")
    ap.add_argument(
        "--results-root",
        type=Path,
        default=None,
        help="デフォルト: リポジトリの results/",
    )
    ap.add_argument("--dry-run", action="store_true", help="移動せず表示のみ")
    args = ap.parse_args()

    script_dir = Path(__file__).resolve().parent.parent
    root = args.results_root or (script_dir / "results")
    return migrate(results_root=root.resolve(), dry_run=bool(args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
