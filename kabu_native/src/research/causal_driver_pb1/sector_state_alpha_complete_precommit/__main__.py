from __future__ import annotations

import json

from research.causal_driver_pb1.sector_state_alpha_complete_precommit.isolation import assert_write_root
from research.causal_driver_pb1.sector_state_alpha_complete_precommit.publish import build, publish


def main() -> int:
    assert_write_root()
    result = build()
    published = publish(result)
    print(json.dumps({"VERDICT": published["verdict"], "NEXT": result["next"], "NEW_ALPHA_COMPLETE_STRATEGY_SHA256": published["sha"]}))
    return 0 if "READY" in published["verdict"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
