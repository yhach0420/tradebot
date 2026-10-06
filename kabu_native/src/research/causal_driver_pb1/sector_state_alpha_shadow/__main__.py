from __future__ import annotations

import json

from research.causal_driver_pb1.sector_state_alpha_shadow.isolation import assert_write_root
from research.causal_driver_pb1.sector_state_alpha_shadow.publish import build, publish


def main() -> int:
    assert_write_root()
    result = build()
    published = publish(result)
    print(json.dumps({"VERDICT": published["verdict"], "implementation_sha256": published["implementation_sha256"], "NEXT": result["next"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
