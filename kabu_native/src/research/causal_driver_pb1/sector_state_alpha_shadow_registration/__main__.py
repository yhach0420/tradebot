from __future__ import annotations

import json

from research.causal_driver_pb1.sector_state_alpha_shadow_registration.isolation import assert_write_root
from research.causal_driver_pb1.sector_state_alpha_shadow_registration.publish import build, publish


def main() -> int:
    assert_write_root()
    result = build()
    published = publish(result)
    print(json.dumps({"VERDICT": published["verdict"], "NEXT": result["next"], "selected_profile": published["selected_profile"]}))
    return 0 if published["verdict"].endswith("READY_V1") else 1


if __name__ == "__main__":
    raise SystemExit(main())
