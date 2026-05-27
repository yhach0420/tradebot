"""
Parse JPX listed-issues export into tradable / per-market symbol master CSVs.
"""

from __future__ import annotations

import csv
import re
import shutil
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping, Optional, Sequence

ExcelKind = Literal["ole2_xls", "ooxml_xlsx", "unknown"]

OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
ZIP_MAGIC = b"PK\x03\x04"

# 4桁数字、または東証の英字付きコード（例: 130A）
CODE_RE = re.compile(r"^(?:\d{4}|\d{3}[A-Z])$", re.IGNORECASE)
SUPERVISORY_RE = re.compile(r"監理|整理|注意|停止|上場廃止")

MASTER_CSV_FIELDS = (
    "symbol",
    "exchange",
    "market",
    "name",
    "sector_33_code",
    "sector_33_name",
    "scale_category",
    "is_etf",
    "is_reit",
    "is_active",
)

# JPX「市場・商品区分」→ normalized market (no score bias; classification only)
MARKET_PRIME = "prime"
MARKET_STANDARD = "standard"
MARKET_GROWTH = "growth"
MARKET_OTHER = "other"

TRADABLE_MARKETS = frozenset({MARKET_PRIME, MARKET_STANDARD, MARKET_GROWTH})

# JPX 東証上場銘柄一覧 — tradable 市場・商品区分（完全一致のみ）
TRADABLE_PRODUCT_LABELS: dict[str, str] = {
    "プライム（内国株式）": MARKET_PRIME,
    "スタンダード（内国株式）": MARKET_STANDARD,
    "グロース（内国株式）": MARKET_GROWTH,
}

TRADABLE_PRODUCTION_MIN = 500

OFFICIAL_RAW_FILENAMES = (
    "listed_issues.xlsx",
    "listed_issues.xls",
    "listed_issues.csv",
    "東証上場銘柄一覧.xlsx",
    "東証上場銘柄一覧.xls",
)

SAMPLE_RAW_FILENAMES = frozenset(
    {
        "jpx_listed_issues_sample.csv",
        "listed_issues_sample.csv",
        "sample_listed_issues.csv",
    }
)

RAW_SEARCH_HINT = (
    "Place JPX 東証上場銘柄一覧 Excel at: data/jpx/raw/listed_issues.xlsx "
    "(see kabu_native/docs/jpx_symbol_master_setup.md)"
)

EXCLUDE_PRODUCT_KEYWORDS = (
    "ETF",
    "ETN",
    "REIT",
    "インフラ",
    "ベンチャー",
    "カントリー",
    "出資証券",
    "外国株式",
    "PRO MARKET",
    "PRO Market",
    "優先",
)

COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "code": ("コード", "銘柄コード", "code", "symbol", "証券コード"),
    "name": ("銘柄名", "name", "銘柄名称", "会社名"),
    "market_product": (
        "市場・商品区分",
        "市場・商品",
        "market_product",
        "market",
        "市場区分",
    ),
    "sector_33_code": ("33業種コード", "sector_33_code", "業種コード"),
    "sector_33_name": ("33業種区分", "33業種", "sector_33_name", "業種名"),
    "scale_category": ("規模区分", "scale_category", "規模"),
}


@dataclass
class ParsedRawRow:
    code: str
    name: str
    market_product: str
    sector_33_code: str
    sector_33_name: str
    scale_category: str


@dataclass
class SymbolMasterRecord:
    symbol: str
    exchange: int
    market: str
    name: str
    sector_33_code: str
    sector_33_name: str
    scale_category: str
    is_etf: bool
    is_reit: bool
    is_active: bool
    exclude_reason: str = ""

    @property
    def symbol_key(self) -> str:
        return f"{self.symbol.replace('.T', '')}@{self.exchange}"

    def to_csv_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "exchange": self.exchange,
            "market": self.market,
            "name": self.name,
            "sector_33_code": self.sector_33_code,
            "sector_33_name": self.sector_33_name,
            "scale_category": self.scale_category,
            "is_etf": "true" if self.is_etf else "false",
            "is_reit": "true" if self.is_reit else "false",
            "is_active": "true" if self.is_active else "false",
        }


@dataclass
class RawFileCandidate:
    path: Path
    name: str
    mtime: float
    is_sample: bool
    is_official_name: bool


@dataclass
class RawInputResolution:
    path: Optional[Path]
    raw_file_found: bool
    sample_only: bool
    candidates: list[RawFileCandidate]
    message: str


@dataclass
class JpxBuildResult:
    verdict: str
    all_rows: list[SymbolMasterRecord] = field(default_factory=list)
    tradable_rows: list[SymbolMasterRecord] = field(default_factory=list)
    prime_rows: list[SymbolMasterRecord] = field(default_factory=list)
    standard_rows: list[SymbolMasterRecord] = field(default_factory=list)
    growth_rows: list[SymbolMasterRecord] = field(default_factory=list)
    excluded_market_counts: dict[str, int] = field(default_factory=dict)
    tradable_symbol_count: int = 0
    input_path: Optional[str] = None
    missing_columns: list[str] = field(default_factory=list)
    error: Optional[str] = None
    sample_only: bool = False
    sample_or_incomplete_master_warning: bool = False
    market_distribution: dict[str, int] = field(default_factory=dict)
    market_distribution_tradable: dict[str, int] = field(default_factory=dict)
    optional_diagnostics: dict[str, Any] = field(default_factory=dict)
    excel_detected_kind: Optional[str] = None
    excel_load_method: Optional[str] = None
    input_row_count: int = 0


def _normalize_header(h: str) -> str:
    return str(h).strip().replace("\ufeff", "")


def map_columns(fieldnames: Sequence[str]) -> tuple[dict[str, str], list[str]]:
    """Map logical keys to actual CSV column names. Returns (mapping, missing_required)."""
    norm_to_actual = {_normalize_header(f): f for f in fieldnames}
    mapping: dict[str, str] = {}
    missing: list[str] = []
    for key, aliases in COLUMN_ALIASES.items():
        found = None
        for alias in aliases:
            if alias in norm_to_actual:
                found = norm_to_actual[alias]
                break
            for nh, actual in norm_to_actual.items():
                if nh.lower() == alias.lower():
                    found = actual
                    break
            if found:
                break
        if found:
            mapping[key] = found
        elif key in ("code", "name", "market_product"):
            missing.append(key)
    return mapping, missing


def detect_excel_kind(path: Path) -> ExcelKind:
    """Detect container format from magic bytes (not file extension)."""
    try:
        with path.open("rb") as f:
            head = f.read(8)
    except OSError:
        return "unknown"
    if head.startswith(OLE2_MAGIC):
        return "ole2_xls"
    if head.startswith(ZIP_MAGIC):
        return "ooxml_xlsx"
    return "unknown"


def normalize_code_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return f"{int(value):04d}"
    s = str(value).strip()
    if s.lower() in ("nan", "none", ""):
        return ""
    if s in ("コード", "code", "Code"):
        return ""
    if "." in s:
        left = s.split(".")[0]
        if left.isdigit():
            s = left
    s = s.replace(".T", "").split("@")[0].strip()
    if s.isdigit():
        return s.zfill(4)
    m = re.fullmatch(r"(\d{3})([a-z])", s, re.IGNORECASE)
    if m:
        return f"{m.group(1)}{m.group(2).upper()}"
    return s


def normalize_market_from_product(market_product: str) -> str:
    mp = (market_product or "").strip()
    if mp in TRADABLE_PRODUCT_LABELS:
        return TRADABLE_PRODUCT_LABELS[mp]
    return MARKET_OTHER


def classify_product_flags(market_product: str, name: str) -> tuple[bool, bool, str]:
    """Returns is_etf, is_reit, exclude_reason (empty if none)."""
    mp = market_product or ""
    nm = name or ""
    combined = f"{mp} {nm}"
    if any(k in combined for k in ("ETF", "ETN", "上場投信")):
        return True, False, "etf_etn"
    if any(k in combined for k in ("REIT", "リート")):
        return False, True, "reit"
    if any(k in combined for k in ("インフラ", "ベンチャー", "カントリー", "出資証券")):
        return False, False, "fund_or_other_product"
    if "外国株式" in mp:
        return False, False, "foreign_listing"
    if "PRO" in mp.upper() or "プロマーケット" in mp:
        return False, False, "pro_market"
    if "優先" in combined:
        return False, False, "preferred_stock"
    return False, False, ""


def build_record_from_raw(row: ParsedRawRow) -> SymbolMasterRecord:
    code = normalize_code_cell(row.code)
    symbol = f"{code}.T"
    market = normalize_market_from_product(row.market_product)
    is_etf, is_reit, product_excl = classify_product_flags(row.market_product, row.name)

    exclude_reason = product_excl
    if not CODE_RE.match(code):
        exclude_reason = exclude_reason or "invalid_code_not_4digit"
    if SUPERVISORY_RE.search(row.name):
        exclude_reason = exclude_reason or "supervisory_or_halted_name"

    is_tradable_market = market in TRADABLE_MARKETS
    is_active = (
        is_tradable_market
        and not exclude_reason
        and not is_etf
        and not is_reit
    )

    return SymbolMasterRecord(
        symbol=symbol,
        exchange=1,
        market=market,
        name=row.name,
        sector_33_code=row.sector_33_code,
        sector_33_name=row.sector_33_name,
        scale_category=row.scale_category,
        is_etf=is_etf,
        is_reit=is_reit,
        is_active=is_active,
        exclude_reason=exclude_reason or ("" if is_active else "not_tradable_market"),
    )


def read_raw_csv(path: Path) -> tuple[list[ParsedRawRow], list[str]]:
    for enc in ("utf-8-sig", "utf-8", "cp932"):
        try:
            with path.open(encoding=enc, newline="") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    return [], ["empty_csv"]
                col_map, missing = map_columns(reader.fieldnames)
                if missing:
                    return [], missing
                rows: list[ParsedRawRow] = []
                for row in reader:
                    code = normalize_code_cell(row.get(col_map["code"]))
                    if not code or not CODE_RE.match(code):
                        continue
                    rows.append(
                        ParsedRawRow(
                            code=code,
                            name=str(row.get(col_map["name"]) or "").strip(),
                            market_product=str(row.get(col_map["market_product"]) or "").strip(),
                            sector_33_code=str(row.get(col_map.get("sector_33_code", ""), "") or "").strip(),
                            sector_33_name=str(row.get(col_map.get("sector_33_name", ""), "") or "").strip(),
                            scale_category=str(row.get(col_map.get("scale_category", ""), "") or "").strip(),
                        )
                    )
                return rows, []
        except UnicodeDecodeError:
            continue
    return [], ["encoding_error"]


def _find_libreoffice_soffice() -> Optional[str]:
    found = shutil.which("soffice")
    if found:
        return found
    for candidate in (
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ):
        if Path(candidate).is_file():
            return candidate
    return None


def _convert_to_ooxml_via_libreoffice(src: Path) -> tuple[Optional[Path], Optional[str]]:
    soffice = _find_libreoffice_soffice()
    if not soffice:
        return None, "libreoffice_not_found"
    out_dir = Path(tempfile.mkdtemp(prefix="jpx_convert_"))
    try:
        proc = subprocess.run(
            [
                soffice,
                "--headless",
                "--convert-to",
                "xlsx",
                "--outdir",
                str(out_dir),
                str(src.resolve()),
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        if proc.returncode != 0:
            return None, f"libreoffice_exit_{proc.returncode}:{proc.stderr[:200]}"
        converted = out_dir / f"{src.stem}.xlsx"
        if not converted.is_file():
            candidates = list(out_dir.glob("*.xlsx"))
            if not candidates:
                return None, "libreoffice_no_output"
            converted = candidates[0]
        return converted, None
    except subprocess.TimeoutExpired:
        return None, "libreoffice_timeout"
    except OSError as e:
        return None, f"libreoffice_error:{e}"


def _read_excel_with_pandas(path: Path, *, kind: ExcelKind) -> tuple[Any, str]:
    import pandas as pd

    sheet = "Sheet1"
    if kind == "ole2_xls":
        try:
            df = pd.read_excel(path, sheet_name=sheet, dtype=str, engine="xlrd")
            return df, "pandas_xlrd_sheet1"
        except Exception:
            try:
                df = pd.read_excel(path, sheet_name=0, dtype=str, engine="xlrd")
                return df, "pandas_xlrd_sheet0"
            except Exception as e:
                raise e
    try:
        df = pd.read_excel(path, sheet_name=sheet, dtype=str, engine="openpyxl")
        return df, "pandas_openpyxl_sheet1"
    except Exception:
        df = pd.read_excel(path, sheet_name=0, dtype=str, engine="openpyxl")
        return df, "pandas_openpyxl_sheet0"


def load_excel_dataframe(path: Path) -> tuple[Any, str, ExcelKind]:
    """Returns (dataframe, load_method, detected_kind)."""
    try:
        import pandas as pd  # noqa: F401
    except ImportError as e:
        raise RuntimeError("pandas_required_for_excel") from e

    kind = detect_excel_kind(path)
    errors: list[str] = []

    if kind in ("ole2_xls", "ooxml_xlsx"):
        try:
            df, method = _read_excel_with_pandas(path, kind=kind)
            return df, method, kind
        except Exception as e:
            errors.append(f"{kind}:{e}")

    # Extension-based fallback when magic unknown
    if kind == "unknown":
        for attempt_kind in ("ole2_xls", "ooxml_xlsx"):
            try:
                df, method = _read_excel_with_pandas(path, kind=attempt_kind)
                return df, method, attempt_kind
            except Exception as e:
                errors.append(f"try_{attempt_kind}:{e}")

    converted, conv_err = _convert_to_ooxml_via_libreoffice(path)
    if converted and converted.is_file():
        try:
            df, method = _read_excel_with_pandas(converted, kind="ooxml_xlsx")
            return df, f"libreoffice_convert+{method}", detect_excel_kind(converted)
        except Exception as e:
            errors.append(f"libreoffice_read:{e}")
    elif conv_err:
        errors.append(conv_err)

    raise RuntimeError(";".join(errors) or "excel_read_failed")


def _dataframe_to_parsed_rows(df: Any) -> tuple[list[ParsedRawRow], list[str]]:
    df.columns = [_normalize_header(c) for c in df.columns]
    col_map, missing = map_columns(list(df.columns))
    if missing:
        return [], missing

    rows: list[ParsedRawRow] = []
    for _, series in df.iterrows():
        row = series.to_dict()
        code = normalize_code_cell(row.get(col_map["code"]))
        if not code or not CODE_RE.match(code):
            continue
        mp = str(row.get(col_map["market_product"]) or "").strip()
        if mp.lower() in ("nan", "none", ""):
            continue
        rows.append(
            ParsedRawRow(
                code=code,
                name=str(row.get(col_map["name"]) or "").strip(),
                market_product=mp,
                sector_33_code=str(row.get(col_map.get("sector_33_code", ""), "") or "").strip(),
                sector_33_name=str(row.get(col_map.get("sector_33_name", ""), "") or "").strip(),
                scale_category=str(row.get(col_map.get("scale_category", ""), "") or "").strip(),
            )
        )
    return rows, []


def read_raw_excel(path: Path) -> tuple[list[ParsedRawRow], list[str], dict[str, str]]:
    meta: dict[str, str] = {"excel_detected_kind": detect_excel_kind(path)}
    try:
        df, method, kind = load_excel_dataframe(path)
        meta["excel_detected_kind"] = kind
        meta["excel_load_method"] = method
        rows, err = _dataframe_to_parsed_rows(df)
        meta["input_row_count"] = str(len(rows))
        if err:
            return [], err, meta
        return rows, [], meta
    except RuntimeError as e:
        msg = str(e)
        if "pandas_required" in msg:
            return [], ["pandas_required_for_excel"], meta
        return [], [f"excel_read_error:{msg}"], meta


def is_sample_raw_file(path: Path) -> bool:
    return path.name.lower() in {s.lower() for s in SAMPLE_RAW_FILENAMES}


def discover_raw_candidates(raw_dir: Path) -> list[RawFileCandidate]:
    if not raw_dir.is_dir():
        return []
    exts = {".xlsx", ".xls", ".csv"}
    out: list[RawFileCandidate] = []
    for p in raw_dir.iterdir():
        if not p.is_file() or p.suffix.lower() not in exts:
            continue
        out.append(
            RawFileCandidate(
                path=p,
                name=p.name,
                mtime=p.stat().st_mtime,
                is_sample=is_sample_raw_file(p),
                is_official_name=p.name in OFFICIAL_RAW_FILENAMES
                or p.name.lower() in {x.lower() for x in OFFICIAL_RAW_FILENAMES},
            )
        )
    return sorted(out, key=lambda c: c.mtime, reverse=True)


def resolve_raw_input(
    repo_root: Path,
    *,
    explicit: Optional[Path] = None,
    allow_sample: bool = False,
) -> RawInputResolution:
    raw_dir = repo_root / "data" / "jpx" / "raw"
    candidates = discover_raw_candidates(raw_dir)

    if explicit is not None:
        p = explicit if explicit.is_absolute() else repo_root / explicit
        if p.is_file():
            return RawInputResolution(
                path=p,
                raw_file_found=True,
                sample_only=is_sample_raw_file(p),
                candidates=candidates,
                message=f"explicit: {p.name}",
            )
        return RawInputResolution(
            path=None,
            raw_file_found=False,
            sample_only=False,
            candidates=candidates,
            message=f"explicit path not found: {p}",
        )

    for name in OFFICIAL_RAW_FILENAMES:
        p = raw_dir / name
        if p.is_file():
            return RawInputResolution(
                path=p,
                raw_file_found=True,
                sample_only=False,
                candidates=candidates,
                message=f"official filename: {name}",
            )

    non_sample = [c for c in candidates if not c.is_sample]
    if non_sample:
        best = non_sample[0]
        return RawInputResolution(
            path=best.path,
            raw_file_found=True,
            sample_only=False,
            candidates=candidates,
            message=f"latest non-sample raw file: {best.name}",
        )

    if allow_sample:
        sample_cands = [c for c in candidates if c.is_sample]
        if sample_cands:
            best = sample_cands[0]
            return RawInputResolution(
                path=best.path,
                raw_file_found=True,
                sample_only=True,
                candidates=candidates,
                message=f"sample fallback: {best.name}",
            )

    names = [c.name for c in candidates]
    msg = RAW_SEARCH_HINT
    if names:
        msg += f" Found in raw/: {', '.join(names)} (none are official listed_issues.*)."
    return RawInputResolution(
        path=None,
        raw_file_found=False,
        sample_only=False,
        candidates=candidates,
        message=msg,
    )


def optional_focus_diagnostics(tradable: Sequence[SymbolMasterRecord]) -> dict[str, Any]:
    """Read-only checks for known focus tickers; does not add symbols."""
    by_sym = {r.symbol: r for r in tradable}
    out: dict[str, Any] = {}
    for sym in ("6613.T", "3905.T"):
        rec = by_sym.get(sym)
        out[sym] = {
            "in_tradable_master": rec is not None,
            "market": rec.market if rec else None,
            "note": "diagnostic only; not used for inclusion",
        }
    return out


def parse_jpx_listed_file(path: Path) -> JpxBuildResult:
    if not path.is_file():
        return JpxBuildResult(verdict="need_raw_jpx_file", input_path=str(path))

    excel_meta: dict[str, str] = {}
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xls") or detect_excel_kind(path) != "unknown":
        raw_rows, err, excel_meta = read_raw_excel(path)
    elif suffix == ".csv":
        raw_rows, err = read_raw_csv(path)
    else:
        raw_rows, err = read_raw_csv(path)
        if err:
            raw_rows, err, excel_meta = read_raw_excel(path)

    if err:
        if any(
            e in err
            for e in (
                "code",
                "name",
                "market_product",
                "pandas_required",
                "excel_read",
                "encoding",
            )
        ):
            return JpxBuildResult(
                verdict="parser_needs_column_mapping",
                input_path=str(path),
                missing_columns=err,
                error=";".join(err),
            )
        return JpxBuildResult(
            verdict="parser_needs_column_mapping",
            input_path=str(path),
            missing_columns=err,
        )

    if not raw_rows:
        return JpxBuildResult(
            verdict="parser_needs_column_mapping",
            input_path=str(path),
            error="no_data_rows",
        )

    all_records = [build_record_from_raw(r) for r in raw_rows]
    excluded_counter: Counter[str] = Counter()
    tradable: list[SymbolMasterRecord] = []

    for rec in all_records:
        if not rec.is_active:
            reason = rec.exclude_reason or rec.market or "unknown"
            excluded_counter[reason] += 1

    tradable = [r for r in all_records if r.is_active]
    prime = [r for r in tradable if r.market == MARKET_PRIME]
    standard = [r for r in tradable if r.market == MARKET_STANDARD]
    growth = [r for r in tradable if r.market == MARKET_GROWTH]

    sample_only = is_sample_raw_file(path)
    tradable_count = len(tradable)
    incomplete = tradable_count < TRADABLE_PRODUCTION_MIN

    return JpxBuildResult(
        verdict="tradable_symbol_master_ready",
        all_rows=all_records,
        tradable_rows=tradable,
        prime_rows=prime,
        standard_rows=standard,
        growth_rows=growth,
        excluded_market_counts=dict(excluded_counter),
        tradable_symbol_count=tradable_count,
        input_path=str(path),
        sample_only=sample_only,
        sample_or_incomplete_master_warning=sample_only or incomplete,
        market_distribution=market_distribution(all_records),
        market_distribution_tradable=market_distribution(tradable),
        optional_diagnostics=optional_focus_diagnostics(tradable),
        excel_detected_kind=excel_meta.get("excel_detected_kind"),
        excel_load_method=excel_meta.get("excel_load_method"),
        input_row_count=int(excel_meta.get("input_row_count") or len(raw_rows)),
    )


def write_master_csv(path: Path, rows: Sequence[SymbolMasterRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(MASTER_CSV_FIELDS))
        w.writeheader()
        for r in rows:
            w.writerow(r.to_csv_dict())


def market_distribution(rows: Sequence[SymbolMasterRecord]) -> dict[str, int]:
    c: Counter[str] = Counter()
    for r in rows:
        c[r.market] += 1
    return dict(c)


def write_all_outputs(repo_root: Path, result: JpxBuildResult) -> dict[str, str]:
    jpx_dir = repo_root / "data" / "jpx"
    paths = {
        "all_symbols": jpx_dir / "all_symbols.csv",
        "prime_symbols": jpx_dir / "prime_symbols.csv",
        "standard_symbols": jpx_dir / "standard_symbols.csv",
        "growth_symbols": jpx_dir / "growth_symbols.csv",
        "tradable_symbols": jpx_dir / "tradable_symbols.csv",
    }
    write_master_csv(paths["all_symbols"], result.all_rows)
    write_master_csv(paths["tradable_symbols"], result.tradable_rows)
    write_master_csv(paths["prime_symbols"], result.prime_rows)
    write_master_csv(paths["standard_symbols"], result.standard_rows)
    write_master_csv(paths["growth_symbols"], result.growth_rows)
    return {k: str(v.relative_to(repo_root)) for k, v in paths.items()}
