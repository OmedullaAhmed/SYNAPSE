"""Output safety: text that a spreadsheet would run as a formula (=, +, -, @, tab, CR at the start) is prefixed with an
apostrophe in every CSV / Excel file StockFlow writes, so a product name like '=HYPERLINK(...)' from a POS export stays text."""
import re

import pandas as pd

_RISK = re.compile(r"^[=+\-@\t\r]")
_NUM = re.compile(r"^[+-]?(\d+([.,]\d+)*|\d*\.\d+)([eE][+-]?\d+)?$")


def cell(v):
    if isinstance(v, str) and _RISK.match(v) and not _NUM.match(v.strip()):
        return "'" + v
    return v


def frame(df):
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return df
    out = df.copy()
    for c in out.columns:
        if out[c].dtype == object or pd.api.types.is_string_dtype(out[c]):
            out[c] = out[c].map(cell)
    out.columns = [cell(str(c)) for c in out.columns]
    return out


def csv(df, **kw):
    kw.setdefault("index", False)
    return frame(df).to_csv(**kw).encode()
