#!/usr/bin/env python3
"""
kabu_signal_v1 評価のログ専用プローブ（Discord / paper_trade 非接続）。

- kabu_api_check.py の JSON（current_quote + board_excerpt）を読み込み評価
- 任意で PUSH JSONL を読み込み rolling_high / push_density を付与
- 結果を results/kabu_signal/YYYYMMDD/ に JSON / CSV 保存

例::
    python scripts/kabu_signal_probe.py --api-check-json results/kabu_api/20260516/kabu_api_check_9984_1_121846.json
    python scripts/kabu_signal_probe.py --api-check-json path/to/check.json --push-jsonl path/to/push.jsonl --tier A
    python scripts/kabu_signal_probe.py --glob "results/kabu_api/20260516/kabu_api_check_*.json"
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _project_root() -> Path:
    return Path(__file__).resolve().parents[1]


_ROOT = _project_root()
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from src.kabu_signal_engine import (  # noqa: E402
    PushHistoryRing,
    evaluate_kabu_signal_v1,
    flatten_board_dict,
)


def _load_api_check_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _eval_rest_snapshot(
    payload: dict[str, Any],
    *,
    tier: str,
    source_path: Path,
) -> dict[str, Any]:
    board = flatten_board_dict(payload)
    result, _ = evaluate_kabu_signal_v1(
        board,
        tier=tier,
        rest_fallback=True,
    )
    out = result.to_dict()
    out["source_file"] = str(source_path)
    out["eval_kind"] = "rest_snapshot"
    meta = payload.get("meta")
    if isinstance(meta, dict):
        out["symbol_key"] = meta.get("symbol_key")
    return out


def _eval_with_push_timeline(
    payload: dict[str, Any],
    push_path: Path,
    *,
    tier: str,
    source_path: Path,
    max_timeline_rows: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """JSONL を時系列再生し、最終板 + 各 PUSH 時点の評価行を返す。"""
    import json as _json

    ring = PushHistoryRing()
    tracker = None
    timeline: list[dict[str, Any]] = []

    with push_path.open(encoding="utf-8") as f:
        lines = [ln.strip() for ln in f if ln.strip()]

    for i, line in enumerate(lines):
        if max_timeline_rows > 0 and i >= max_timeline_rows:
            break
        msg = _json.loads(line)
        if not isinstance(msg, dict):
            continue
        ring.add_from_board(msg)
        result, tracker = evaluate_kabu_signal_v1(
            msg,
            push_history=ring,
            breakout_tracker=tracker,
            tier=tier,
        )
        row = result.to_dict()
        row["source_file"] = str(push_path)
        row["eval_kind"] = "push_timeline"
        row["push_seq"] = i
        timeline.append(row)

    board = flatten_board_dict(payload)
    if board:
        ring.add_from_board(board)
    final, tracker = evaluate_kabu_signal_v1(
        board,
        push_history=ring,
        breakout_tracker=tracker,
        tier=tier,
    )
    final_dict = final.to_dict()
    final_dict["source_file"] = str(source_path)
    final_dict["eval_kind"] = "rest_plus_push_final"
    final_dict["push_jsonl"] = str(push_path)
    final_dict["push_messages_replayed"] = len(lines)
    meta = payload.get("meta")
    if isinstance(meta, dict):
        final_dict["symbol_key"] = meta.get("symbol_key")
    return final_dict, timeline


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    if not rows:
        return
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for k in row:
            if k not in seen:
                seen.add(k)
                keys.append(k)

    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            r = dict(row)
            if isinstance(r.get("reject_reasons"), list):
                r["reject_reasons"] = ";".join(r["reject_reasons"])
            w.writerow(r)


def main() -> int:
    root = _project_root()

    parser = argparse.ArgumentParser(description="kabu_signal_v1 ログ専用プローブ")
    parser.add_argument("--api-check-json", type=Path, action="append", help="kabu_api_check 出力 JSON")
    parser.add_argument(
        "--glob",
        type=str,
        default="",
        help='api-check JSON の glob（例: "results/kabu_api/20260516/*.json"）',
    )
    parser.add_argument("--push-jsonl", type=Path, default=None, help="PUSH JSONL（任意）")
    parser.add_argument("--tier", type=str, default="B", choices=("A", "B", "C", "a", "b", "c"))
    parser.add_argument("--out-dir", type=Path, default=None)
    parser.add_argument(
        "--max-timeline-rows",
        type=int,
        default=0,
        help="PUSH 時系列評価の最大行数（0=全行）",
    )
    args = parser.parse_args()

    paths: list[Path] = list(args.api_check_json or [])
    if args.glob:
        paths.extend(sorted(root.glob(args.glob)))

    if not paths:
        parser.error("--api-check-json または --glob が必要です")
        return 2

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    day = datetime.now().strftime("%Y%m%d")
    out_dir = args.out_dir or (root / "results" / "kabu_signal" / day)
    out_dir.mkdir(parents=True, exist_ok=True)

    tier = args.tier.upper()
    all_summaries: list[dict[str, Any]] = []
    all_timeline: list[dict[str, Any]] = []

    for p in paths:
        if not p.is_file():
            print(f"skip missing: {p}", file=sys.stderr)
            continue
        payload = _load_api_check_json(p.resolve())

        rest_row = _eval_rest_snapshot(payload, tier=tier, source_path=p.resolve())
        all_summaries.append(rest_row)

        if args.push_jsonl and args.push_jsonl.is_file():
            final_row, timeline = _eval_with_push_timeline(
                payload,
                args.push_jsonl.resolve(),
                tier=tier,
                source_path=p.resolve(),
                max_timeline_rows=args.max_timeline_rows,
            )
            all_summaries.append(final_row)
            all_timeline.extend(timeline)
        elif args.push_jsonl:
            print(f"push jsonl not found: {args.push_jsonl}", file=sys.stderr)

    json_path = out_dir / f"kabu_signal_probe_{stamp}.json"
    csv_path = out_dir / f"kabu_signal_probe_{stamp}.csv"
    timeline_csv = out_dir / f"kabu_signal_probe_{stamp}_timeline.csv"

    bundle = {
        "profile": "kabu_signal_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "tier_default": tier,
        "note": "ログ専用。notify_*_eligible は算出のみで Discord / paper_trade には送信しない。",
        "summaries": all_summaries,
        "timeline_row_count": len(all_timeline),
    }
    json_path.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(all_summaries, csv_path)
    if all_timeline:
        _write_csv(all_timeline, timeline_csv)

    print(json_path.relative_to(root))
    print(csv_path.relative_to(root))
    if all_timeline:
        print(timeline_csv.relative_to(root))

    for row in all_summaries:
        print(
            f"{row.get('eval_kind')} {row.get('symbol')} "
            f"score={row.get('signal_score')} timing_ok={row.get('timing_ok')} "
            f"rejects={row.get('reject_reasons')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
