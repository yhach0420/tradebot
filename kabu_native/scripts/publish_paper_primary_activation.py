#!/usr/bin/env python3
"""Publish a frozen paper activation only when its pre-paper certification matches.

Does not generate the certification and does not rewrite the manifest.
Refuses without changing the current selector when certification is missing,
failed, stale, or identity-mismatched.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--selector", required=True, type=Path)
    parser.add_argument("--cert-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    native = Path(__file__).resolve().parents[1]
    src = native / "src"
    if str(src) not in sys.path:
        sys.path.insert(0, str(src))
    from small_paper.activation_current_publish import publish_current_activation

    result = publish_current_activation(
        manifest_path=args.manifest,
        selector_path=args.selector,
        cert_dir=args.cert_dir,
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
