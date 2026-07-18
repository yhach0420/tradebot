# Pullback Volume Persistence Forward Logger (Phase687W57)

Observe-only Forward logger for paper trading. Validates Phase687W55–W56 hypotheses:

- PullbackMisread hit × `volume_persistence` **high** → healthy pullback / re-rise bias  
- PullbackMisread hit × `volume_persistence` **low** → collapse / loss bias  

## Absolute constraints

- Does **not** Reject / Permit / change ENTRY rank / change GateDecision  
- Does **not** alter PBv2, PullbackMisread Shadow predicate, or Cost-Aware Entry Shadow  
- AM/PM, symbol, and sector are labels only (not decision inputs)  
- Thresholds are frozen (no Discovery retune)

## Enable

Paper Runtime (`run_paper_trade.bat` / live pilot): **default ON** (Phase687W58).

```bat
REM explicit OFF only when needed
set PULLBACK_VOLUME_FORWARD=0
set COST_AWARE_ENTRY_SHADOW=0
```

Replay / unit tests: default OFF unless env/config sets ON.

## Frozen thresholds (Phase687W56 Discovery)

| Bucket | Rule |
|--------|------|
| high | `vol_persistence_300s >= 0.2782069767789509` |
| low | `vol_persistence_300s <= 0.12710349962769918` |
| mid | otherwise |
| missing | feature unavailable |

`vol_persistence_300s` SoT (w43c): `mean(diff(volume) > 0)` over ~300s. Higher = sustaining participation.

Board (`imbalance_chg_60s`) is explanatory only → `improving` / `worsening` / `flat` / `missing`.

## Outputs

```text
results/forward/pullback_volume/
  pullback_volume_forward_YYYYMMDD.jsonl          # SoT (1 row / candidate)
  pullback_volume_forward_summary_YYYYMMDD.json
  pullback_volume_forward_summary_YYYYMMDD.csv
  pullback_volume_forward_cumulative.json         # rebuilt from day JSONL
  pullback_volume_forward_cumulative.csv
```

Rebuild:

```bash
python scripts/build_pullback_volume_forward_summary.py
python scripts/check_pullback_volume_forward_preflight.py
```

## Discord (short audit only)

```text
[Pullback Volume Forward]
hits: N
vol_high: N / healthy XX%
vol_low: N / collapse XX%
board↓×vol_low: N
status: collecting
```

No adopt / Reject / Permit wording.

## Forward sample gate (before any Runtime/Shadow candidacy)

- `volume_high` n ≥ 50 and `volume_low` n ≥ 50  
- ≥ 10 trading days, ≥ 20 symbols  
- max sector share ≤ 50%  
- Board×Volume major cells: each n ≥ 30 (informational; thresholds stay frozen)

## Scripts / module

| Path | Role |
|------|------|
| `src/small_paper/pullback_volume_forward_logger.py` | Core logger |
| `scripts/build_pullback_volume_forward_summary.py` | Day + cumulative rebuild |
| `scripts/check_pullback_volume_forward_preflight.py` | Preflight |

## Verdicts (end of Forward)

- `PULLBACK_VOLUME_FORWARD_CONFIRMED`  
- `PULLBACK_VOLUME_REJECT_READY`  
- `PULLBACK_VOLUME_PERMIT_READY`  
- `PULLBACK_VOLUME_FORWARD_NOT_STABLE`  
- `PULLBACK_VOLUME_INSUFFICIENT_SAMPLE`  
