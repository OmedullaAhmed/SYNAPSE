"""Stock from photos and scans: barcodes, printed count sheets and (optionally) handwritten registers.

    rows, notes = vision.extract(files, catalog, methods=("barcode", "ocr", "ai"))

Each method returns candidate lines {code, name, qty, method, confidence, raw}; they are matched to the product master
(SKU code, barcode, or product name) and returned for a person to review before anything is saved.

  barcode  offline (zxing-cpp). Every barcode read counts as one unit; a label with "x12" next to it is not counted
           as 12, so for cartons scan the carton barcode once per carton and set the pack size in the catalog.
  ocr      offline (Tesseract). Printed count sheets, stock registers, ERP printouts: a line with a known SKU code or
           product name and a number becomes a line. Works best on flat, well-lit, printed pages.
  ai       Claude vision (opt-in by an admin; the image is sent to Anthropic). Reads handwriting, photos at an angle
           and mixed layouts. Needs ANTHROPIC_API_KEY on the server.
"""
import base64
import difflib
import io
import json
import os
import re
from pathlib import Path

import pandas as pd

MAX_SIDE = 2400                      # images are scaled down to this for OCR and AI (enough for A4 text, keeps it fast)
AI_MODEL = os.environ.get("STOCKFLOW_VISION_MODEL", "claude-sonnet-5-5")
IMG_TYPES = (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif")


# --------------------------------------------------------------------------- input
def pages(name, data):
    """PIL images for one uploaded file (every page of a PDF)."""
    from PIL import Image, ImageOps
    if name.lower().endswith(".pdf"):
        import pypdfium2 as pdfium
        doc = pdfium.PdfDocument(data)
        out = [doc[i].render(scale=200 / 72).to_pil() for i in range(min(len(doc), 20))]
    else:
        out = [Image.open(io.BytesIO(data))]
    res = []
    for im in out:
        im = ImageOps.exif_transpose(im).convert("RGB")      # phone photos carry their rotation in EXIF
        if max(im.size) > MAX_SIDE:
            im.thumbnail((MAX_SIDE, MAX_SIDE))
        res.append(im)
    return res


# --------------------------------------------------------------------------- methods
def read_barcodes(im):
    import zxingcpp
    found = []
    for b in zxingcpp.read_barcodes(im):
        if b.valid and b.text:
            found.append(b.text.strip())
    if not found:                                             # small codes in big photos: try a sharper, larger crop grid
        from PIL import ImageOps
        g = ImageOps.autocontrast(im.convert("L"))
        for b in zxingcpp.read_barcodes(g):
            if b.valid and b.text:
                found.append(b.text.strip())
    return found


def ocr_lines(im):
    import pytesseract
    from PIL import ImageOps
    g = ImageOps.autocontrast(im.convert("L"))
    txt = pytesseract.image_to_string(g, config="--psm 6")
    return [ln.strip() for ln in txt.splitlines() if ln.strip()]


def parse_lines(lines, catalog):
    """Text lines -> candidates, using the catalog's codes and names to find the item on each line."""
    codes, exact = code_index(list(catalog.sku_id)) if len(catalog) else ({}, {})
    bars = {k_norm(b): s for s, b in zip(catalog.sku_id, catalog.barcode) if str(b)} if len(catalog) and "barcode" in catalog else {}
    out = []
    for ln in lines:
        toks = [t for t in re.split(r"[\s|,;:]+", ln) if t]
        hit, used = None, None
        for t in toks:
            if k_norm(t) in bars:
                hit, used = bars[k_norm(t)], t
                break
            if k_norm(t) in exact:
                hit, used = exact[k_norm(t)], t
                break
            if len(k_norm(t)) >= 3 and k_fuzzy(t) in codes:
                hit, used = codes[k_fuzzy(t)], t
                break
        if hit is None and exact:                                       # last resort: a code one character off
            for t in toks:
                if len(k_norm(t)) >= 4 and re.search(r"\d", t):
                    near = difflib.get_close_matches(k_fuzzy(t), list(codes), n=1, cutoff=0.86)
                    if near:
                        hit, used = codes[near[0]], t
                        break
        qty, clean = None, True
        for t in reversed([t for t in toks if t is not used]):          # count sheets put the quantity last
            if re.fullmatch(r"\d{1,6}", t):
                qty = int(t)
                break
            core = re.sub(r"^[^\d]+|[^\d]+$", "", t)
            if re.fullmatch(r"\d{1,6}", core) and len(t) - len(core) <= 1:
                qty, clean = int(core), False                           # '37.' or '2}': probably right, flagged for a look
                break
        if qty is None:
            continue
        conf = (0.9 if hit and k_fuzzy(used) in codes or hit and k_norm(used) in bars else 0.6 if hit else 0.5) * (1 if clean else 0.6)
        out.append(dict(code=hit or "", name="" if hit else _strip_nums(ln), qty=qty, method="ocr", confidence=round(conf, 2), raw=ln))
    return out


def _strip_nums(s):
    return re.sub(r"\s+", " ", re.sub(r"\d+", " ", s)).strip(" -.:|")


def ai_read(im, catalog_hint="", api_key=None):
    """Claude vision: returns candidate lines. Raises when no key / the call fails."""
    import anthropic
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()
    prompt = ("This image is a stock count sheet, stock register, delivery note or shelf photo. List every product line and "
              "its counted quantity. Reply with JSON only: {\"lines\": [{\"code\": \"item code or barcode if visible, else empty\", "
              "\"name\": \"product name as written\", \"qty\": integer, \"legible\": true|false}]}. Do not guess quantities you "
              "cannot read: set legible false. " + (f"Known item codes look like: {catalog_hint}." if catalog_hint else ""))
    resp = client.messages.create(model=AI_MODEL, max_tokens=4000, messages=[{"role": "user", "content": [
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(buf.getvalue()).decode()}},
        {"type": "text", "text": prompt}]}])
    text = "".join(getattr(b, "text", "") for b in resp.content)
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0)) if m else {"lines": []}
    out = []
    for ln in data.get("lines", []):
        try:
            q = int(float(str(ln.get("qty", "")).strip()))
        except ValueError:
            continue
        out.append(dict(code=str(ln.get("code", "") or "").strip(), name=str(ln.get("name", "") or "").strip(), qty=q, method="ai",
                        confidence=0.85 if ln.get("legible", True) else 0.3, raw=json.dumps(ln, ensure_ascii=False)[:300]))
    return out


# --------------------------------------------------------------------------- matching
_LOOKALIKE = str.maketrans({"O": "0", "Q": "0", "D": "0", "I": "1", "L": "1", "|": "1", "Z": "2", "S": "5", "B": "8"})


def k_norm(s):
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def code_index(skus):
    """{fuzzy key: sku}, leaving out keys two different SKUs share (those need an exact read)."""
    seen, dup = {}, set()
    for c in skus:
        k = k_fuzzy(c)
        if k in seen and seen[k] != c:
            dup.add(k)
        seen[k] = c
    exact = {k_norm(c): c for c in skus}
    return {k: v for k, v in seen.items() if k not in dup}, exact


def k_fuzzy(s):
    """Code key that survives OCR look-alikes (O/0, I/1, S/5, B/8): used on both the catalog and what was read."""
    return k_norm(s).translate(_LOOKALIKE)


def match(cands, catalog):
    """Attach sku_id to each candidate: exact code, barcode, then product name (close spelling). Unmatched keep sku_id ''."""
    if not cands:
        return pd.DataFrame(columns=["sku_id", "name", "qty", "method", "confidence", "matched_by", "raw"])
    df = pd.DataFrame(cands)
    if catalog is None or catalog.empty:
        df["sku_id"], df["matched_by"] = df.code, "as read"
        return df
    codes, exact = code_index(list(catalog.sku_id))
    bars = {k_norm(b): s for s, b in zip(catalog.sku_id, catalog.get("barcode", pd.Series([""] * len(catalog)))) if str(b)}
    names = {str(n).lower(): s for s, n in zip(catalog.sku_id, catalog.name)}
    name_keys = list(names)
    sku, how, conf = [], [], []
    for r in df.itertuples():
        n = k_norm(r.code)
        if n and n in bars:
            sku.append(bars[n]), how.append("barcode"), conf.append(r.confidence)
        elif n and n in exact:
            sku.append(exact[n]), how.append("code"), conf.append(r.confidence)
        elif n and k_fuzzy(n) in codes:
            sku.append(codes[k_fuzzy(n)]), how.append("code"), conf.append(r.confidence)
        elif r.name:
            best = difflib.get_close_matches(r.name.lower(), name_keys, n=1, cutoff=0.72)
            if best:
                sku.append(names[best[0]]), how.append("name"), conf.append(min(r.confidence, 0.75))
            else:
                sku.append(""), how.append("not found"), conf.append(0.2)
        else:
            sku.append(""), how.append("not found"), conf.append(0.2)
    df["sku_id"], df["matched_by"], df["confidence"] = sku, how, conf
    df["name"] = [names_of(catalog, s) or nm for s, nm in zip(df.sku_id, df.name)]
    return df


def names_of(catalog, sku):
    if not sku or catalog is None or catalog.empty:
        return ""
    hit = catalog[catalog.sku_id == sku]
    return str(hit.name.iloc[0]) if len(hit) else ""


# --------------------------------------------------------------------------- one call
def extract(files, catalog=None, methods=("barcode", "ocr"), api_key=None):
    """files: [(name, bytes)]. Returns (lines frame summed per SKU, per-file notes)."""
    catalog = catalog if catalog is not None else pd.DataFrame(columns=["sku_id", "name", "barcode"])
    hint = ", ".join(catalog.sku_id.head(5)) if len(catalog) else ""
    cands, notes = [], []
    for name, data in files:
        try:
            ims = pages(name, data)
        except Exception as e:  # noqa: BLE001
            notes.append(f"{name}: could not open ({type(e).__name__})")
            continue
        for i, im in enumerate(ims):
            tag = f"{name}" + (f" p{i + 1}" if len(ims) > 1 else "")
            got = 0
            if "barcode" in methods:
                codes = read_barcodes(im)
                for c in codes:
                    cands.append(dict(code=c, name="", qty=1, method="barcode", confidence=0.95, raw=c, file=tag))
                got += len(codes)
            if "ai" in methods:
                try:
                    lines = ai_read(im, hint, api_key)
                    for x in lines:
                        x["file"] = tag
                    cands += lines
                    got += len(lines)
                except Exception as e:  # noqa: BLE001
                    notes.append(f"{tag}: AI reading failed ({type(e).__name__}); used offline reading")
                    methods = tuple(m for m in methods if m != "ai") + ("ocr",)
            if "ocr" in methods and "ai" not in methods:
                try:
                    lines = parse_lines(ocr_lines(im), catalog)
                except Exception as e:  # noqa: BLE001 - e.g. Tesseract not installed on this server
                    notes.append(f"{tag}: printed-text reading is not available on this server ({type(e).__name__}); "
                                 "install tesseract-ocr or use barcodes / AI reading")
                    lines = []
                for x in lines:
                    x["file"] = tag
                cands += lines
                got += len(lines)
            notes.append(f"{tag}: {got} lines read")
    df = match(cands, catalog)
    return df, notes


def summarise(df):
    """One row per SKU: barcodes add up; for sheets the same SKU listed twice is added too (shown in 'lines')."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["sku_id", "name", "qty", "lines", "method", "confidence", "matched_by"])
    ok = df[df.sku_id != ""]
    s = ok.groupby("sku_id").agg(name=("name", "first"), qty=("qty", "sum"), lines=("qty", "size"),
                                 method=("method", lambda x: ", ".join(sorted(set(x)))), confidence=("confidence", "min"),
                                 matched_by=("matched_by", lambda x: ", ".join(sorted(set(x))))).reset_index()
    bad = df[df.sku_id == ""].assign(lines=1)[["sku_id", "name", "qty", "lines", "method", "confidence", "matched_by"]]
    return pd.concat([s, bad], ignore_index=True)


def is_image(name):
    return Path(name).suffix.lower() in IMG_TYPES + (".pdf",)
