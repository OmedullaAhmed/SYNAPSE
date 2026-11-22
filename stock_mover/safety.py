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


# ---------------------------------------------------------------- outbound URLs (ERP, webhooks, alerts, daily downloads)
_BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal", "metadata", "100.100.100.200", "fd00:ec2::254"}


def check_url(url):
    """Only http(s) to a real host; never file:, ftp:, data: or a cloud metadata endpoint. Raises ValueError."""
    from urllib.parse import urlparse
    u = urlparse(str(url).strip())
    if u.scheme not in ("https", "http"):
        raise ValueError(f"Only https:// (or http://) addresses are allowed, not '{u.scheme or 'none'}:'")
    host = (u.hostname or "").lower()
    if not host or host in _BLOCKED_HOSTS or host.startswith("169.254."):
        raise ValueError(f"Address not allowed: {host or 'missing host'}")
    return url


def urlopen(req, timeout=30):
    import urllib.request
    check_url(req.full_url if hasattr(req, "full_url") else req)
    return urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - scheme and host checked above
