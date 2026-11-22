"""Standardised ingestion of POS, stock and product-master exports.

Any POS / ERP / WMS export (CSV or Excel, any header names, Indian or ISO dates, "₹1,234.00" numbers) is turned into
StockFlow's standard sheets (schema.TABLES) before planning:

  1. detect   which standard sheet a file is (by file name, then by its columns)
  2. map      its headers to standard columns: saved mapping profile > exact name > known synonyms > close spelling
  3. clean    ids trimmed (and upper-cased), dates to YYYY-MM-DD (day-first detected), numbers stripped of currency
              symbols and commas, (12) read as -12, exact duplicate rows removed, return flags turned into negative qty
  4. check    data-quality rules per sheet and across sheets (unknown SKUs/stores, stale stock, negative stock …)
  5. write    standard CSVs to a folder the engine reads, plus a report; every file is logged in the database

    python ingest.py RAW_DIR OUT_DIR [--profile NAME]
"""
import argparse
import difflib
import io
import json
import re
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

import schema

# --------------------------------------------------------------------------- vocabulary
# Generic header words seen in POS, ERP and WMS exports. Matching ignores case, spaces and punctuation.
SYN = {
    "store_id": ["store", "store code", "store no", "site", "site code", "site id", "branch", "branch code", "outlet", "outlet code",
                 "shop", "shop code", "location code", "loc code", "plant", "counter", "pos location", "store number"],
    "location_id": ["location", "location id", "location code", "store code", "store", "site", "site code", "warehouse", "wh code",
                    "branch", "plant", "storage location", "godown", "bin location"],
    "warehouse_id": ["warehouse", "warehouse code", "wh", "wh code", "dc", "dc code", "depot", "godown", "receiving warehouse"],
    "sku_id": ["sku", "sku code", "item code", "itemcode", "item id", "item no", "barcode", "ean", "upc", "article", "article code",
               "variant sku", "variant id", "material", "material code", "product code", "item sku"],
    "style_id": ["style", "style code", "style no", "design", "design code", "design no", "parent sku", "product id", "article no",
                 "model", "model no", "generic article"],
    "style_name": ["style name", "design name", "product name", "item name", "item description", "description", "title", "product title",
                   "article name"],
    "category": ["category", "product type", "department", "dept", "class", "sub category", "subcategory", "product category",
                 "merchandise category", "item group", "group"],
    "size": ["size", "size code", "variant size", "option2", "size name"],
    "colour": ["color", "colour", "shade", "colour name", "color name", "option1"],
    "fit": ["fit", "cut", "silhouette"],
    "mrp": ["mrp", "tag price", "retail price", "rsp", "price", "list price", "selling price incl tax", "max retail price"],
    "cost_price": ["cost", "cost price", "landed cost", "purchase price", "unit cost", "cogs", "wsp", "buying price"],
    "launch_date": ["launch date", "first sale date", "intro date", "launched on"],
    "season_end": ["season end", "season end date", "end of season", "eoss date"],
    "date": ["bill date", "invoice date", "transaction date", "txn date", "sale date", "sales date", "order date", "posting date",
             "created at", "doc date", "date"],
    "qty": ["quantity", "qty", "qty sold", "sold qty", "sale qty", "sales qty", "units", "units sold", "pcs", "pieces", "bill qty",
            "net qty"],
    "net_value": ["net amount", "net sales", "net value", "amount", "line total", "line amount", "total", "net sale value",
                  "taxable value incl tax", "gross amount", "sale value", "value"],
    "as_of_date": ["as of", "as on", "stock date", "snapshot date", "closing date", "date"],
    "on_hand": ["on hand", "soh", "stock on hand", "qty on hand", "closing stock", "closing qty", "available qty", "available",
                "stock qty", "balance qty", "physical stock", "inventory", "stock"],
    "in_transit": ["in transit", "transit qty", "git", "goods in transit", "intransit"],
    "oldest_stock_received_on": ["oldest received", "first grn date", "grn date", "received on", "inward date", "oldest stock date"],
    "stock_status": ["status", "stock status", "stock type", "bin status", "condition"],
    "city": ["city", "town"],
    "region": ["region", "state", "province"],
    "zone": ["zone", "area", "cluster"],
    "tier": ["tier", "grade", "store grade", "class", "store class"],
    "capacity_units": ["capacity", "capacity units", "max stock", "store capacity", "fixture capacity"],
    "store_type": ["store type", "format", "channel", "type"],
    "expected_date": ["expected date", "due date", "eta", "delivery date", "po delivery date"],
    "week_ending": ["week ending", "week end", "week", "week end date"],
    "days_in_stock": ["days in stock", "in stock days", "days available"],
}
# words that mark a returned line in a sales export
RETURN_WORDS = {"return", "returned", "refund", "sales return", "rtn", "credit note", "cn"}
RETURN_COLS = ["type", "transaction type", "txn type", "line type", "doc type", "bill type", "is return", "return flag"]

ID_COLS = {"store_id", "location_id", "warehouse_id", "sku_id", "style_id", "manager_id", "home_warehouse", "to_id", "from_id"}
DATE_COLS = {"date", "as_of_date", "expected_date", "week_ending", "launch_date", "season_end", "oldest_stock_received_on", "opened_on"}
NUM_COLS = {"qty", "net_value", "on_hand", "in_transit", "mrp", "cost_price", "capacity_units", "days_in_stock", "closing_on_hand",
            "lead_days", "cost_per_unit", "cost_per_trip", "min_display_units"}
MASTER = {"stores", "warehouses", "items", "calendar", "lanes", "min_display", "protected_styles", "size_sets", "managers"}
LEGACY = {"bills", "bill_lines", "lead_times"}          # older layouts the planner converts itself
FILE_HINTS = {"stores": ["store", "site", "branch", "outlet", "location master"], "items": ["item", "product", "sku", "article", "master"],
              "sales": ["sale", "bill", "invoice", "pos", "transaction"], "stock_snapshot": ["stock", "soh", "inventory", "closing"],
              "stock_weekly": ["weekly", "in_stock", "instock"], "inbound": ["inbound", "po", "asn", "grn due"], "warehouses": ["warehouse", "dc"]}


def key(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()


def std_cols(sheet):
    return [c for c, *_ in schema.TABLES[sheet][2]] if sheet in schema.TABLES else []


def required(sheet):
    return [c for c, need, *_ in schema.TABLES[sheet][2] if need == schema.R] if sheet in schema.TABLES else []


# --------------------------------------------------------------------------- 1. detect
def detect_sheet(name, columns):
    """Best standard sheet for a file, or None. Exact sheet names win; then header coverage of required columns."""
    stem = key(Path(name).stem).replace(" ", "_")
    stem = schema.TABLE_ALIASES.get(stem, stem)
    if stem in schema.TABLES:
        return stem
    best, score = None, 0.0
    for t in schema.TABLES:
        m = suggest_mapping(t, columns)
        req = required(t)
        cov = sum(1 for c in req if c in m.values()) / max(len(req), 1)
        hint = 0.15 if any(h in key(name) for h in FILE_HINTS.get(t, [])) else 0
        s = cov + hint + 0.01 * len(m)
        if cov >= 0.6 and s > score:
            best, score = t, s
    return best


# --------------------------------------------------------------------------- 2. map
def suggest_mapping(sheet, columns, profile=None):
    """{source header: standard column}. profile = {standard column: source header} saved earlier."""
    cols = list(columns)
    ks = {c: key(c) for c in cols}
    out, used = {}, set()
    profile = profile or {}
    for std, src in profile.items():
        if src in cols and std in std_cols(sheet) and src not in out:
            out[src], used = std, used | {std}
    targets = [c for c in std_cols(sheet) if c not in used]
    # exact (after normalising) first, then synonyms, then close spelling
    for std in targets:
        for c in cols:
            if c not in out and ks[c] in (key(std), key(std).replace(" id", ""), *[key(a) for a, b in schema.TABLES[sheet][3].items() if b == std]):
                out[c] = std
                used.add(std)
                break
    for std in [t for t in targets if t not in used]:
        for syn in SYN.get(std, []):
            hit = next((c for c in cols if c not in out and ks[c] == key(syn)), None)
            if hit:
                out[hit] = std
                used.add(std)
                break
    for std in [t for t in targets if t not in used]:
        pool = [c for c in cols if c not in out]
        cands = [key(std)] + [key(s) for s in SYN.get(std, [])]
        best = max(((difflib.SequenceMatcher(None, ks[c], cd).ratio(), c) for c in pool for cd in cands), default=(0, None))
        if best[0] >= 0.86:
            out[best[1]] = std
            used.add(std)
    return out


# --------------------------------------------------------------------------- 3. clean
_NUM_JUNK = re.compile(r"[₹$€£,\s]|rs\.?|inr|pcs|nos|units", re.I)


def clean_number(s):
    s = s.astype(str).str.strip()
    neg = s.str.match(r"^\(.*\)$")
    s = s.str.replace(r"^\((.*)\)$", r"\1", regex=True).str.replace(_NUM_JUNK, "", regex=True)
    s = s.str.replace(r"-$", "", regex=True).where(~s.str.endswith("-"), "-" + s.str.rstrip("-"))
    v = pd.to_numeric(s.replace({"": np.nan, "-": np.nan, "nan": np.nan, "None": np.nan}), errors="coerce")
    return v.where(~neg, -v)


def clean_date(s):
    """Returns (ISO strings, share unreadable). Day-first is chosen when any value only parses that way (31/10/2026)."""
    raw = s.astype(str).str.strip().replace({"nan": "", "NaT": "", "None": ""})
    nonblank = raw != ""
    if not nonblank.any():
        return raw, 0.0
    num = pd.to_numeric(raw.where(nonblank), errors="coerce")
    if num.notna().mean() > 0.9 and num.dropna().between(20000, 80000).all():        # Excel serial days
        d = pd.to_datetime("1899-12-30") + pd.to_timedelta(num, unit="D")
    else:
        sample = raw[nonblank].head(500)
        parts = sample.str.extract(r"^(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{2,4})")
        dayfirst = bool(parts[0].notna().any() and (pd.to_numeric(parts[0], errors="coerce") > 12).any())
        if parts[0].notna().mean() > 0.5 and not dayfirst and not (pd.to_numeric(parts[1], errors="coerce") > 12).any():
            dayfirst = True                                                          # ambiguous 01/02/2026: Indian default
        d = pd.to_datetime(raw.where(nonblank), errors="coerce", dayfirst=dayfirst, format="mixed")
    bad = float((d.isna() & nonblank).sum() / max(nonblank.sum(), 1))
    return d.dt.strftime("%Y-%m-%d").fillna(""), bad


def standardise(df, sheet, mapping, upper_ids=True):
    """Rename to standard columns, clean values. Returns (frame, notes)."""
    notes = []
    df = df.rename(columns=mapping)
    keep = [c for c in df.columns if c in std_cols(sheet)]
    extra = [c for c in df.columns if c not in keep]
    # return flags in sales exports
    if sheet == "sales" and "qty" in df:
        flag = next((c for c in extra if key(c) in RETURN_COLS), None)
        if flag:
            ret = df[flag].astype(str).map(key).isin(RETURN_WORDS) | df[flag].astype(str).str.strip().isin(["1", "Y", "y", "true", "TRUE"])
            q = clean_number(df["qty"])
            df["qty"] = q.where(~ret | (q < 0), -q.abs())
            if "net_value" in df:
                v = clean_number(df["net_value"])
                df["net_value"] = v.where(~ret | (v < 0), -v.abs())
            notes.append(f"{int(ret.sum()):,} return lines (column '{flag}') stored as negative quantity")
    df = df[keep].copy()
    for c in df.columns:
        if c in ID_COLS:
            v = df[c].astype(str).str.strip().str.replace(r"\.0$", "", regex=True).replace({"nan": "", "None": ""})
            df[c] = v.str.upper() if upper_ids else v
        elif c in DATE_COLS:
            df[c], bad = clean_date(df[c])
            if bad > 0:
                notes.append(f"{bad:.1%} of '{c}' could not be read as dates")
        elif c in NUM_COLS:
            v = clean_number(df[c])
            df[c] = v.map(lambda x: "" if pd.isna(x) else (str(int(x)) if float(x).is_integer() else f"{x:.2f}"))
        else:
            df[c] = df[c].astype(str).str.strip().replace({"nan": "", "None": ""})
    if sheet in MASTER:                       # identical sales or stock lines can be real; master rows can't
        n = len(df)
        df = df.drop_duplicates()
        if len(df) < n:
            notes.append(f"{n - len(df):,} exact duplicate rows removed")
    return df, notes


# --------------------------------------------------------------------------- 4. check
def _issue(kind, sev, sheet, title, detail="", count=0, ref=""):
    return dict(kind=kind, severity=sev, sheet=sheet, title=title, detail=detail, count=int(count), ref=ref or sheet)


def check(T, cfg=None, today=None, stale=True):
    """Data-quality rules. T = {sheet: standardised frame}. Returns a list of issues (most severe first)."""
    cfg = cfg or schema.DEFAULTS
    today = pd.Timestamp(today or datetime.now().date())
    out = []
    for t, df in T.items():
        miss = [c for c in required(t) if c not in df]
        if miss:
            out.append(_issue("missing_column", "high", t, f"{t}: missing {', '.join(miss)}", "Map these columns or add them to the export."))
        for c in [c for c in ID_COLS if c in df]:
            blank = int((df[c] == "").sum())
            if blank:
                out.append(_issue("blank_id", "high", t, f"{t}: {blank:,} rows with no {c}", "These rows cannot be used.", blank, f"{t}.{c}"))
    sn, it, so, sa = T.get("stock_snapshot"), T.get("items"), T.get("stores"), T.get("sales")
    if sn is not None and "on_hand" in sn:
        oh = pd.to_numeric(sn.on_hand, errors="coerce")
        neg = sn[oh < 0]
        if len(neg):
            out.append(_issue("negative_stock", "high", "stock_snapshot", f"{len(neg):,} negative stock rows",
                              "Negative on-hand usually means unposted receipts or billing without stock. Treated as 0.",
                              len(neg), ", ".join(neg.location_id.astype(str).unique()[:8]) if "location_id" in neg else ""))
        if {"location_id", "sku_id"} <= set(sn):
            kcols = ["location_id", "sku_id"] + (["stock_status"] if "stock_status" in sn else [])
            dup = int(sn.duplicated(kcols).sum())
            if dup:
                out.append(_issue("duplicate_stock", "medium", "stock_snapshot", f"{dup:,} repeated location + SKU rows",
                                  "Quantities are added together. Check the export is not doubled.", dup))
        if "as_of_date" in sn:
            d = pd.to_datetime(sn.as_of_date, errors="coerce").max()
            stale_after = int(cfg.get("integration", {}).get("stale_stock_days", 2))
            if stale and pd.notna(d) and (today - d).days > stale_after:
                out.append(_issue("stale_stock", "high", "stock_snapshot", f"Stock is {(today - d).days} days old (as of {d:%d %b})",
                                  f"Plans should use yesterday's closing stock. Older than {stale_after} days is flagged.", (today - d).days))
            if sn.as_of_date.nunique() > 1:
                out.append(_issue("mixed_dates", "medium", "stock_snapshot", f"Stock file has {sn.as_of_date.nunique()} different dates",
                                  "Some locations sent an older file. The latest date is used as the planning date."))
    if it is not None and "sku_id" in it:
        if "mrp" in it:
            z = int((pd.to_numeric(it.mrp, errors="coerce").fillna(0) <= 0).sum())
            if z:
                out.append(_issue("no_price", "medium", "items", f"{z:,} SKUs without a price", "They get no value in the plan.", z))
        dup = int(it.sku_id.duplicated().sum())
        if dup:
            out.append(_issue("duplicate_sku", "medium", "items", f"{dup:,} SKUs listed twice in the product master", "The first row is used.", dup))
        known = set(it.sku_id)
        for t in ("stock_snapshot", "sales"):
            df = T.get(t)
            if df is not None and "sku_id" in df:
                unk = df[~df.sku_id.isin(known) & (df.sku_id != "")]
                if len(unk):
                    out.append(_issue("unknown_sku", "high" if t == "stock_snapshot" else "medium", t,
                                      f"{unk.sku_id.nunique():,} SKUs in {t} are not in the product master",
                                      "Add them to items so their stock and sales count. Examples: " + ", ".join(unk.sku_id.unique()[:6]),
                                      unk.sku_id.nunique()))
    if so is not None and "store_id" in so:
        stores = set(so.store_id)
        whs = set(T["warehouses"].warehouse_id) if "warehouses" in T and "warehouse_id" in T["warehouses"] else set()
        if sa is not None and "store_id" in sa:
            unk = set(sa.store_id) - stores - {""}
            if unk:
                out.append(_issue("unknown_store", "medium", "sales", f"Sales from {len(unk)} store codes not in the store list",
                                  ", ".join(sorted(unk)[:8]), len(unk)))
        if sn is not None and "location_id" in sn:
            locs = set(sn.location_id)
            nostock = stores - locs
            if nostock:
                out.append(_issue("store_no_stock", "high", "stock_snapshot", f"{len(nostock)} stores sent no stock",
                                  "Their file may be missing; they get no refills. " + ", ".join(sorted(nostock)[:8]), len(nostock),
                                  ", ".join(sorted(nostock)[:8])))
            if whs and not (whs & locs):
                out.append(_issue("warehouse_no_stock", "high", "stock_snapshot", "No warehouse stock in the stock file",
                                  "Warehouse-to-store refills are impossible today."))
    if sa is not None and "date" in sa:
        d = pd.to_datetime(sa.date, errors="coerce")
        if d.notna().any():
            span = pd.date_range(d.min(), d.max())
            gaps = span.difference(d.dropna().dt.normalize().unique())
            if len(gaps) > 2:
                out.append(_issue("sales_gap", "medium", "sales", f"No sales at all on {len(gaps)} days between {d.min():%d %b} and {d.max():%d %b}",
                                  "Usually a missing POS export. First missing day: " + f"{gaps.min():%d %b %Y}", len(gaps)))
            fut = int((d > today + pd.Timedelta(days=1)).sum())
            if fut:
                out.append(_issue("future_date", "medium", "sales", f"{fut:,} sales lines dated in the future", "Check the date format.", fut))
            if sn is not None and "as_of_date" in sn:
                sd = pd.to_datetime(sn.as_of_date, errors="coerce").max()
                if pd.notna(sd) and d.max() < sd - pd.Timedelta(days=2):
                    out.append(_issue("sales_behind", "high", "sales", f"Sales stop on {d.max():%d %b}, stock is from {sd:%d %b}",
                                      "Recent sales are missing, so demand will look too low."))
    order = {"high": 0, "medium": 1, "low": 2}
    return sorted(out, key=lambda x: order.get(x["severity"], 3))


# --------------------------------------------------------------------------- read raw files
def read_raw(path):
    """{name: frame} for a file or folder of CSV / Excel exports (all as text)."""
    path = Path(path)
    out = {}
    files = [path] if path.is_file() else sorted(p for p in path.rglob("*") if p.is_file())
    for f in files:
        suf = f.suffix.lower()
        try:
            if suf in (".csv", ".txt", ".tsv"):
                raw = f.read_bytes()
                for enc in ("utf-8-sig", "cp1252", "latin-1"):
                    try:
                        txt = raw.decode(enc)
                        break
                    except UnicodeDecodeError:
                        continue
                sep = "\t" if suf == ".tsv" or txt[:2000].count("\t") > txt[:2000].count(",") else \
                      ";" if txt[:2000].count(";") > txt[:2000].count(",") else ","
                out[f.name] = pd.read_csv(io.StringIO(txt), dtype=str, keep_default_na=False, sep=sep)
            elif suf in (".xlsx", ".xlsm", ".xls"):
                for tab, df in pd.read_excel(f, sheet_name=None, dtype=str, keep_default_na=False).items():
                    out[f"{f.name}#{tab}" if len(df.columns) else f.name] = df
        except Exception as e:  # noqa: BLE001
            out[f.name] = e
    return out


def _header_row(df):
    """Exports often have title rows above the header: pick the first row that looks like headers."""
    if len(df) and sum(1 for c in df.columns if str(c).startswith("Unnamed")) > len(df.columns) / 2:
        for i in range(min(15, len(df))):
            row = df.iloc[i].astype(str)
            if (row != "").sum() >= max(2, len(df.columns) * 0.5):
                d = df.iloc[i + 1:].copy()
                d.columns = [c if c else f"col{j}" for j, c in enumerate(row)]
                return d.reset_index(drop=True)
    return df


# --------------------------------------------------------------------------- 5. run
def run(src, out_dir, profiles=None, cfg=None, user="", log=True, source_label="upload", today=None, stale=True):
    """Standardise everything in src into out_dir. Returns a report dict."""
    cfg = cfg or schema.DEFAULTS
    profiles = profiles if profiles is not None else load_profiles()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    upper = bool(cfg.get("integration", {}).get("uppercase_ids", True))
    T, files, passthrough = {}, [], []
    for name, df in read_raw(src).items():
        if Path(name.split("#")[0]).name.lower() == "config.json":
            continue
        if isinstance(df, Exception):
            files.append(dict(file=name, sheet=None, status="unreadable", notes=[f"{type(df).__name__}: {df}"], rows_in=0, rows_out=0, mapping={}))
            continue
        df = _header_row(df)
        stem = key(Path(name.split("#")[-1] if "#" in name else name).stem).replace(" ", "_")
        stem = schema.TABLE_ALIASES.get(stem, stem)
        if stem in schema.IGNORED:
            files.append(dict(file=name, sheet=None, status="ignored", rows_in=len(df), rows_out=0, mapping={},
                              notes=["not needed for planning (may hold personal data); not read"]))
            continue
        sheet = None if stem in LEGACY else detect_sheet(name.split("#")[-1] if "#" in name else name, df.columns)
        if sheet is None:
            if stem in LEGACY:
                passthrough.append(stem)
                df.columns = [key(c).replace(" ", "_") for c in df.columns]
                for c in [c for c in df.columns if c in ID_COLS | {"bill_id"}]:
                    v = df[c].astype(str).str.strip()
                    df[c] = v.str.upper() if upper else v
                df.to_csv(out_dir / f"{stem}.csv", index=False)
                files.append(dict(file=name, sheet=stem, status="passed through", notes=["read by the planner as is"],
                                  rows_in=len(df), rows_out=len(df), mapping={}))
            else:
                files.append(dict(file=name, sheet=None, status="not recognised", rows_in=len(df), rows_out=0, mapping={},
                                  notes=[f"Columns: {', '.join(map(str, df.columns[:12]))}. Rename the file to a sheet name "
                                         "(e.g. sales.csv) or save a mapping profile."]))
            continue
        prof = (profiles.get(sheet) or {})
        mapping = suggest_mapping(sheet, df.columns, prof)
        clean, notes = standardise(df, sheet, mapping, upper)
        if sheet in T:                                                  # several files for one sheet (one per store / per day)
            T[sheet] = pd.concat([T[sheet], clean], ignore_index=True)
        else:
            T[sheet] = clean
        files.append(dict(file=name, sheet=sheet, status="ok", rows_in=len(df), rows_out=len(clean), notes=notes,
                          mapping={v: k for k, v in mapping.items()},
                          unmapped=[c for c in df.columns if c not in mapping]))
    for t, df in T.items():
        (df.drop_duplicates() if t in MASTER else df).to_csv(out_dir / f"{t}.csv", index=False)
    issues = check(T, cfg, today, stale)
    rep = dict(at=datetime.now().isoformat(timespec="seconds"), sheets={t: len(v) for t, v in T.items()}, files=files, issues=issues,
               missing=[t for t, (need, *_) in schema.TABLES.items() if need == schema.R and t not in T and not
                        (t == "sales" and {"bills", "bill_lines"} <= set(passthrough))])
    (out_dir / "_ingestion_report.json").write_text(json.dumps(rep, indent=1, default=str))
    if log:
        _log(rep, user, source_label)
    return rep


def _log(rep, user, source):
    try:
        import db
        rows = [dict(at=datetime.now(), by=user, source=source[:200], profile="", sheet=f.get("sheet") or "", file=str(f["file"])[:300],
                     rows_in=f.get("rows_in", 0), rows_out=f.get("rows_out", 0), rejected=max(0, f.get("rows_in", 0) - f.get("rows_out", 0)),
                     mapping=f.get("mapping") or {}, issues=f.get("notes") or [], status=f["status"][:12]) for f in rep["files"]]
        db.insert_rows("ingestions", rows)
    except Exception:  # noqa: BLE001 - logging never blocks planning
        pass


# --------------------------------------------------------------------------- mapping profiles (saved in the database)
def load_profiles():
    try:
        import db
        return db.get_setting("mapping_profiles", {}) or {}
    except Exception:  # noqa: BLE001
        return {}


def save_profile(sheet, mapping, user=""):
    """mapping = {standard column: source header}."""
    import db
    p = load_profiles()
    p[sheet] = {k: v for k, v in mapping.items() if v}
    db.upsert_setting("mapping_profiles", p, user)
    return p


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("src")
    ap.add_argument("out")
    a = ap.parse_args()
    rep = run(a.src, a.out, log=False)
    for f in rep["files"]:
        print(f"{f['status']:>15}  {f['file']} -> {f.get('sheet')}  {f.get('rows_out', 0):,} rows  {'; '.join(f.get('notes', []))}")
    for i in rep["issues"]:
        print(f"[{i['severity']}] {i['title']}")
    if rep["missing"]:
        print("MISSING:", ", ".join(rep["missing"]))


if __name__ == "__main__":
    main()
