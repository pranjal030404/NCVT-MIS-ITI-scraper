"""Input reading, column detection, output writing, and validation report."""

import os
import re
from collections import OrderedDict

import pandas as pd

from .logutil import get_logger

logger = get_logger()

# ITI codes look like PR09900478 -> two letters + digits.
_CODE_PATTERN = re.compile(r"^[A-Za-z]{2}\d{4,}$")


def load_input(path):
    """Load the export file (CSV or Excel) as a DataFrame with str dtype."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".xlsx", ".xls"):
        df = pd.read_excel(path, dtype=str)
    elif ext == ".csv":
        df = pd.read_csv(path, dtype=str, low_memory=False)
    else:
        raise ValueError(f"Unsupported input format: {ext!r} (use .csv/.xlsx)")
    return df


def detect_code_col(df):
    """Locate the ITI Code column.

    Prefers a column whose name contains 'code'; confirms by sampling values
    against the PR09900478-look pattern.  Returns the column name or None.
    """
    candidates = []
    for col in df.columns:
        if re.search(r"\bcode\b|iti\s*-?\s*cod", str(col).lower()):
            candidates.append(col)
    for col in candidates:
        sample = df[col].dropna().astype(str).head(50)
        if not sample.empty and sample.str.match(_CODE_PATTERN).mean() > 0.5:
            return col
    # Last resort: scan all columns for the code pattern.
    for col in df.columns:
        sample = df[col].dropna().astype(str).head(50)
        if not sample.empty and sample.str.match(_CODE_PATTERN).mean() > 0.5:
            return col
    return None


def detect_id_col(df):
    """Locate a numeric Detail-page ID column if one exists.

    Returns the column name if a column explicitly looks like an ITI id, or
    None.  Many exports only carry the ITI Code; the caller must handle None
    (mapping step required for every row).
    """
    for col in df.columns:
        if re.search(r"\biti\s*id\b|\bid\b", str(col).lower()):
            return col
    return None


def inspect_input(path):
    """Return (df, code_col, id_col); logs the column report.

    Raises ValueError if no usable ITI Code column is found.
    """
    df = load_input(path)
    code_col = detect_code_col(df)
    id_col = detect_id_col(df)

    logger.info("Input file : %s", path)
    logger.info("Rows       : %d", len(df))
    logger.info("Columns    : %s", ", ".join(map(str, df.columns)))

    if code_col is None:
        raise ValueError(
            "No ITI Code column detected. Use --code-col to name it explicitly."
        )
    sample = df[code_col].dropna().astype(str).head(3).tolist()
    logger.info("ITI Code column detected: %r (sample: %s)", code_col, sample)

    if id_col is None:
        logger.info(
            "Numeric ID column detected: NONE -> mapping step required for ALL rows "
            "before fetch."
        )
    else:
        logger.info("Numeric ID column detected: %r (sample: %s)",
                    id_col, df[id_col].dropna().head(3).tolist())
    return df, code_col, id_col


def build_trade_rows(records):
    """Expand parsed records into denormalized one-row-per-trade list.

    ``records`` is a list of parsed detail dicts (main keys + 'trades' list).
    Returns a list of dicts; main fields are repeated on each trade row, and
    every trade field is prefixed with ``trade_``.  ITIs with no parseable
    trade table produce one row with blank trade_* fields.
    """
    rows = []
    for rec in records:
        trades = rec.pop("trades", []) or []
        main = OrderedDict(
            (k, v) for k, v in rec.items() if k not in ("trades",)
        )
        for tr in trades:
            row = OrderedDict(main)
            for k, v in tr.items():
                row.setdefault("trade_" + k, v)
            rows.append(row)
        if not trades:
            row = OrderedDict(main)
            rows.append(row)
    return rows


def write_combined_csv(rows, path):
    """Write denormalized rows to a CSV; empty df still creates the file."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    df = pd.DataFrame(rows)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return df


def write_failed_csv(failures, path):
    """Write failed (code, iti_id, reason) rows to a CSV for later review."""
    if failures:
        pd.DataFrame(failures, columns=["iti_code", "iti_id", "reason"]).to_csv(
            path, index=False, encoding="utf-8-sig"
        )
    else:
        with open(path, "w", encoding="utf-8") as f:
            f.write("iti_code,iti_id,reason\n")


def validate_coverage(input_codes, out_df, failed_codes, code_col="iti_code"):
    """Report input vs output coverage.

    Returns a dict of counts.  Logs each dropped code individually.
    """
    input_set = set(input_codes)
    output_set = set(out_df[code_col].dropna().astype(str)) if len(out_df) else set()
    failed_set = set(failed_codes)

    missing = input_set - output_set - failed_set
    summary = {
        "input_total": len(input_set),
        "output_unique": len(output_set),
        "failed": len(failed_set),
        "unaccounted": len(missing),
    }
    logger.info(
        "Coverage: input=%d unique output=%d failed=%d still-missing=%d",
        summary["input_total"], summary["output_unique"],
        summary["failed"], summary["unaccounted"],
    )
    if missing:
        for code in sorted(missing):
            logger.warning("Dropped / not accounted for: %s", code)
    return summary