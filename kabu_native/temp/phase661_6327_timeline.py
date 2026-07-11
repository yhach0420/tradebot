"""Quick timeline extractor for Phase661 structural exit holding audit."""
import json
import sys
from datetime import datetime
from pathlib import Path

SESSION = Path(
    r"c:\Users\yhach\Documents\tradebotfile\kabu_native\results\small_paper\20260707\live_session_122539"
)
SYMBOL = sys.argv[1] if len(sys.argv) > 1 else "6327.T"


def parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


def main() -> None:
    path = SESSION / "small_paper_events.jsonl"
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        e = json.loads(line)
        if e.get("symbol") != SYMBOL:
            continue
        if e.get("event_type") in ("accepted", "observer_exit", "observer_take"):
            rows.append(e)

    print(f"=== {SYMBOL} accepted/exit timeline ({SESSION.name}) ===")
    prev_accept_evt: datetime | None = None
    for e in rows:
        evt = parse_ts(e.get("event_time"))
        ent = e.get("entry_time")
        hold = e.get("hold_sec") or e.get("hold_duration_sec")
        delta_accept = ""
        if e["event_type"] == "observer_exit" and prev_accept_evt and evt:
            delta_accept = f" delta_from_last_accept={ (evt - prev_accept_evt).total_seconds():.1f}s"
        if e["event_type"] == "accepted" and evt:
            prev_accept_evt = evt
        print(
            f"{e.get('event_time')} | {e['event_type']:14} | mi={e.get('message_index')} "
            f"| entry_time={ent} | hold_sec={hold} | exit_reason={e.get('exit_reason')} "
            f"| price_age_sec={e.get('price_age_sec')} | fresh={e.get('price_freshness_source')}"
            f"{delta_accept}"
        )


if __name__ == "__main__":
    main()
