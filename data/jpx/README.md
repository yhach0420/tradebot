# JPX symbol master (Phase 98+)

**Full setup guide:** [kabu_native/docs/jpx_symbol_master_setup.md](../kabu_native/docs/jpx_symbol_master_setup.md)

## Build from JPX raw export

1. Download **東証上場銘柄一覧** (Excel) from [JPX](https://www.jpx.co.jp/markets/statistics-equities/misc/01.html).
2. Save as `data/jpx/raw/listed_issues.xlsx` (UTF-8 CSV also supported).
3. Run:

```bash
python kabu_native/scripts/build_jpx_symbol_master.py
python kabu_native/scripts/build_dynamic_universe.py --skip-kabu
```

## Output files

| File | Content |
|------|---------|
| `all_symbols.csv` | All parsed rows |
| `tradable_symbols.csv` | Prime + Standard + Growth ordinary shares (**default for dynamic universe**) |
| `prime_symbols.csv` | Prime only |
| `standard_symbols.csv` | Standard only |
| `growth_symbols.csv` | Growth only |

Columns: `symbol`, `exchange`, `market`, `name`, `sector_33_code`, `sector_33_name`, `scale_category`, `is_etf`, `is_reit`, `is_active`.

`exchange` is always `1` for kabu station TSE.

## Sample data

`data/jpx/raw/jpx_listed_issues_sample.csv` is a **small fixture** for parser tests only.  
Use `build_jpx_symbol_master.py --allow-sample` for dev; production requires `listed_issues.xlsx` (500+ tradable).

```bash
python kabu_native/scripts/run_phase100_jpx_master_setup_check.py
```

## Dynamic universe

- **Scope:** Tradable ordinary shares on Prime, Standard, and Growth (no ETF/REIT/preferred/foreign).
- **Selection score:** `trading_value`, `change_previous_close_pct`, liquidity, spread, price — **no market-segment weighting**.
- **Not** hardcoded per symbol (6613 / 3905 etc.).

```bash
python kabu_native/scripts/build_dynamic_universe.py --symbol-master data/jpx/tradable_symbols.csv
python kabu_native/scripts/build_dynamic_universe.py --symbol-master data/jpx/standard_symbols.csv
```
