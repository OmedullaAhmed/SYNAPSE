"""Render the StockFlow AI (customized for Manyavar) pitch video (1920x1080, 30 fps) with a Windows TTS voice-over and captions.

Run:  python make_video.py            -> StockMover_pitch.mp4
      python make_video.py --frames   -> also saves a preview frame from the middle of each scene
"""
import math
import subprocess
import sys
import wave
from pathlib import Path

import imageio.v2 as imageio
import imageio_ffmpeg
import numpy as np
import qrcode
from PIL import Image, ImageDraw, ImageFont

W, H, FPS = 1920, 1080, 30
D = Path(__file__).resolve().parent
SH, AUD = D / "shots", D / "audio"
AUD.mkdir(exist_ok=True)
VOICE, RATE = "Microsoft Zira Desktop", 0
LIVE, REPO = "synapse-stock-mover.streamlit.app", "github.com/lorenjoseph-create/SYNAPSE"

# Manyavar palette (from manyavar.com). Names kept from v1: TEAL = primary ember orange, ORANGE = maroon,
# SAFFRON = gold, PURPLE = deep brown, WHITE = body ink on cream.
BG = (255, 251, 242)
TEAL, ORANGE, SAFFRON, PURPLE = (224, 78, 28), (102, 20, 46), (212, 160, 23), (81, 29, 0)
WHITE, MUTED, CARD = (34, 34, 34), (98, 98, 98), (255, 255, 255)
CREAM, SAND, GOLD_L, BROWN = (255, 251, 242), (225, 206, 172), (245, 196, 107), (81, 29, 0)
MV_LOGO = D.parent / "SYNAPSE" / "stock_mover" / "assets" / "manyavar.png"

# ----------------------------------------------------------------------------- drawing helpers
_fonts = {}


def F(size, w="r"):
    k = (size, w)
    if k not in _fonts:
        name = {"r": "segoeui.ttf", "b": "segoeuib.ttf", "sb": "seguisb.ttf", "serif": "georgiab.ttf"}[w]
        _fonts[k] = ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)
    return _fonts[k]


def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease(x):
    x = clamp(x)
    return 1 - (1 - x) ** 3


def appear(t, start, dur=0.6):
    return ease((t - start) / dur)


def text(img, xy, s, size, w="r", fill=WHITE, alpha=1.0, anchor="la"):
    if alpha <= 0.01 or not s:
        return
    f = F(size, w)
    l, t, r, b = f.getbbox(s, anchor=anchor)
    mask = Image.new("L", (max(1, r - l), max(1, b - t)), 0)
    ImageDraw.Draw(mask).text((-l, -t), s, font=f, fill=int(255 * clamp(alpha)), anchor=anchor)
    x, y = int(xy[0] + l), int(xy[1] + t)
    img.paste(fill, (x, y, x + mask.width, y + mask.height), mask)


def rich(img, xy, parts, size, alpha=1.0):
    """parts: [(text, weight, color)] drawn left to right on one line."""
    x, y = xy
    for s, w, c in parts:
        text(img, (x, y), s, size, w, c, alpha)
        x += F(size, w).getlength(s)


def width(s, size, w="r"):
    return F(size, w).getlength(s)


def wrap(s, size, maxw, w="r"):
    words, lines, cur = s.split(), [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        if width(trial, size, w) <= maxw:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def para(img, xy, s, size, maxw, w="r", fill=WHITE, alpha=1.0, gap=1.3):
    y = xy[1]
    for line in wrap(s, size, maxw, w):
        text(img, (xy[0], y), line, size, w, fill, alpha)
        y += size * gap
    return y


def rrect(img, box, r, fill, alpha=1.0, outline=None, ow=3):
    x0, y0, x1, y1 = [int(v) for v in box]
    if alpha <= 0.01 or x1 <= x0 or y1 <= y0:
        return
    tile = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
    a = int(255 * clamp(alpha))
    ImageDraw.Draw(tile).rounded_rectangle((0, 0, x1 - x0 - 1, y1 - y0 - 1), r, fill=fill + (a,),
                                           outline=(outline + (a,)) if outline else None, width=ow)
    img.paste(tile, (x0, y0), tile)


def circle(img, c, r, fill, alpha=1.0):
    rrect(img, (c[0] - r, c[1] - r, c[0] + r, c[1] + r), r, fill, alpha)


def arrow(img, p0, p1, color, alpha=1.0, w=6):
    if alpha <= 0.01:
        return
    ang = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
    s = max(12, w * 3.2)
    pts = [p1, (p1[0] - s * math.cos(ang - 0.45), p1[1] - s * math.sin(ang - 0.45)),
           (p1[0] - s * math.cos(ang + 0.45), p1[1] - s * math.sin(ang + 0.45))]
    xs, ys = [p0[0], *(p[0] for p in pts)], [p0[1], *(p[1] for p in pts)]
    ox, oy = int(min(xs)) - w - 2, int(min(ys)) - w - 2
    tile = Image.new("RGBA", (int(max(xs)) - ox + w + 3, int(max(ys)) - oy + w + 3), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    a = int(255 * clamp(alpha))
    sh = lambda p: (p[0] - ox, p[1] - oy)
    d.line([sh(p0), sh(p1)], fill=color + (a,), width=int(w))
    d.polygon([sh(p) for p in pts], fill=color + (a,))
    img.paste(tile, (ox, oy), tile)


def _glow(base, specs):
    y, x = np.mgrid[0:H, 0:W]
    for (cx, cy, rad, col, k) in specs:
        g = np.exp(-(((x - cx) ** 2 + (y - cy) ** 2) / (2 * (rad / 2.2) ** 2)))[..., None]
        base += g * (np.array(col, np.float32) - base) * k
    return base


def _rings(img, cx, cy, n, step, col, a):
    tile = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    for i in range(1, n + 1):
        r = i * step
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col + (a,), width=2)
    img.paste(tile, (0, 0), tile)


def make_bg():
    """Warm cream page, like manyavar.com, with soft gold/ember light and festive rings."""
    base = np.zeros((H, W, 3), np.float32) + np.array(BG, np.float32)
    base = _glow(base, [(200, 100, 1000, (255, 236, 205), 0.9), (1800, 1000, 900, (252, 224, 200), 0.7)])
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    _rings(img, W + 40, -40, 9, 46, (212, 160, 23), 38)
    return img


def make_hero():
    """Festive maroon panel used for the intro and closing cards (matches the app's welcome banner)."""
    y, x = np.mgrid[0:H, 0:W]
    dist = np.sqrt(((x - W) / (W * 1.3)) ** 2 + (y / (H * 1.6)) ** 2)[..., None]
    c0, c1, c2 = np.array([138, 42, 18.0]), np.array([102, 20, 46.0]), np.array([58, 11, 20.0])
    base = np.where(dist < .42, c0 + (c1 - c0) * (dist / .42), c1 + (c2 - c1) * np.clip((dist - .42) / .58, 0, 1))
    img = Image.fromarray(np.clip(base, 0, 255).astype(np.uint8))
    _rings(img, W - 120, 120, 12, 34, GOLD_L, 46)
    d = ImageDraw.Draw(img)
    d.rectangle((22, 22, W - 23, H - 23), outline=(212, 160, 23), width=2)
    d.rectangle((34, 34, W - 35, H - 35), outline=(150, 110, 60), width=1)
    return img


BGIMG, HEROIMG = make_bg(), make_hero()


def _mark(S=512):
    """StockFlow AI mark (same as the app's logo.svg): ember-gold tile with two flow arrows."""
    k = S / 96
    yy, xx = np.mgrid[0:S, 0:S]
    t = ((xx + yy) / (2 * S))[..., None]
    a0, a1, a2 = np.array([245, 166, 35.0]), np.array([233, 88, 39.0]), np.array([201, 67, 26.0])
    grad = np.where(t < .6, a0 + (a1 - a0) * (t / .6), a1 + (a2 - a1) * ((t - .6) / .4))
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    g = Image.fromarray(np.dstack([grad, np.full((S, S), 255.0)]).astype(np.uint8), "RGBA")
    m = Image.new("L", (S, S), 0)
    ImageDraw.Draw(m).rounded_rectangle((0, 0, S - 1, S - 1), int(20 * k), fill=255)
    im.paste(g, (0, 0), m)
    d = ImageDraw.Draw(im)
    s = lambda *p: [v * k for v in p]
    d.line(s(22, 38, 58, 38), fill=(255, 251, 242), width=int(7 * k))
    d.polygon(s(52, 28, 66, 38, 52, 48), fill=(255, 251, 242))
    d.line(s(74, 58, 38, 58), fill=(81, 29, 0), width=int(7 * k))
    d.polygon(s(44, 48, 30, 58, 44, 68), fill=(81, 29, 0))
    d.ellipse(s(69, 17, 79, 27), fill=(255, 243, 221))
    return im


MARK, _sized = _mark(), {}
MVLOGO = Image.open(MV_LOGO).convert("RGBA") if MV_LOGO.exists() else None


def _paste(img, src, x, y, size, alpha):
    key = (id(src), size)
    if key not in _sized:
        _sized[key] = src.resize(size, Image.LANCZOS)
    im = _sized[key]
    a = im.getchannel("A").point(lambda v: int(v * clamp(alpha)))
    img.paste(im.convert("RGB"), (int(x), int(y)), a)


def logo(img, x, y, s, alpha=1.0):
    if alpha > 0.01:
        _paste(img, MARK, x, y, (int(s), int(s)), alpha)


def mv_logo(img, x, y, h, alpha=1.0):
    """Manyavar logo at height h; returns its width."""
    if MVLOGO is None or alpha <= 0.01:
        return 0
    w = int(MVLOGO.width * h / MVLOGO.height)
    _paste(img, MVLOGO, x, y, (w, int(h)), alpha)
    return w


def chip(img, x, y, s, size, color, alpha=1.0, fg=CREAM):
    w = width(s, size, "sb") + size * 1.2
    rrect(img, (x, y, x + w, y + size * 1.75), int(size * 0.875), color, alpha)
    text(img, (x + size * 0.6, y + size * 0.875), s, size, "sb", fg, alpha, anchor="lm")
    return w


def shot(name):
    return Image.open(SH / name).convert("RGB")


def framed(img, src, box, t01, zoom=(1.0, 1.08), focus=(0.5, 0.35), alpha=1.0, radius=22):
    """Ken Burns crop of src into box with a rounded frame."""
    x0, y0, x1, y1 = box
    bw, bh = x1 - x0, y1 - y0
    z = zoom[0] + (zoom[1] - zoom[0]) * ease(t01)
    sw, sh = src.size
    cw = min(sw, sw / z)
    ch = cw * bh / bw
    if ch > sh:
        ch = sh
        cw = ch * bw / bh
    cx = clamp(focus[0] * sw, cw / 2, sw - cw / 2)
    cy = clamp(focus[1] * sh, ch / 2, sh - ch / 2)
    crop = src.crop((int(cx - cw / 2), int(cy - ch / 2), int(cx + cw / 2), int(cy + ch / 2))).resize((bw, bh), Image.BILINEAR)
    rrect(img, (x0 - 10, y0 - 10, x1 + 10, y1 + 10), radius + 8, SAND, alpha)
    mask = Image.new("L", (bw, bh), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, bw - 1, bh - 1), radius, fill=int(255 * clamp(alpha)))
    img.paste(crop, (x0, y0), mask)


def pan(src, box_w, box_h, t01):
    """Vertical pan down a tall screenshot, fit to width."""
    sw, sh = src.size
    scale = box_w / sw
    view_h = box_h / scale
    top = (sh - view_h) * ease(t01) if sh > view_h else 0
    return src.crop((0, int(top), sw, int(top + min(view_h, sh)))).resize((box_w, box_h), Image.BILINEAR)


# ----------------------------------------------------------------------------- scenes
def s_hook(img, t, d):
    a1, a2, a3 = appear(t, 0.2), appear(t, 1.0), appear(t, 2.4)
    text(img, (W / 2, 190 - 20 * (1 - a1)), "Right now, a customer is walking out of a store.", 66, "serif", WHITE, a1, "mm")
    text(img, (W / 2, 280 - 20 * (1 - a2)), "Their size isn't on the shelf.", 66, "serif", TEAL, a2, "mm")

    def store(x, y, label, filled, col, note, ncol, a):
        rrect(img, (x, y, x + 560, y + 360), 26, CARD, a, outline=SAND)
        text(img, (x + 30, y + 40), label, 34, "sb", WHITE, a, "lm")
        for r in range(3):
            yy = y + 90 + r * 85
            rrect(img, (x + 30, yy + 64, x + 530, yy + 70), 3, (200, 178, 145), a)
            for c in range(9):
                xx = x + 40 + c * 54
                if (r * 9 + c) in filled:
                    rrect(img, (xx, yy, xx + 40, yy + 62), 8, col, a)
                else:
                    rrect(img, (xx, yy, xx + 40, yy + 62), 8, CARD, a, outline=(214, 120, 90), ow=2)
        chip(img, x + 30, y + 372, note, 26, ncol, a)

    sa = appear(t, 3.2)
    sb = appear(t, d * 0.55)
    store(290, 420, "Store A  ·  Size L", set(), TEAL, "0 left · customers walk out", ORANGE, sa)
    store(1070, 420, "Store B  ·  Size L", set(range(27)) - {3}, SAFFRON, "26 unsold · gathering dust", (150, 105, 10), sb)
    if t > d * 0.75:
        aa = appear(t, d * 0.75)
        arrow(img, (1060, 600), (870, 600), TEAL, aa, 10)


def s_problem(img, t, d):
    text(img, (W / 2, 230), "Stock in the wrong place.", 80, "serif", WHITE, appear(t, 0.1), "mm")
    text(img, (W / 2, 330), "Every single day.", 80, "serif", TEAL, appear(t, 0.7), "mm")
    stats = [("20", "stores"), ("1", "warehouse"), ("385", "sizes"), ("2.6 L", "sales lines")]
    for i, (n, lab) in enumerate(stats):
        a = appear(t, 1.4 + i * 0.35)
        x = 230 + i * 380
        rrect(img, (x, 470 + 30 * (1 - a), x + 330, 680 + 30 * (1 - a)), 28, CARD, a, outline=SAND)
        text(img, (x + 165, 545 + 30 * (1 - a)), n, 84, "b", TEAL, a, "mm")
        text(img, (x + 165, 630 + 30 * (1 - a)), lab, 34, "r", MUTED, a, "mm")
    a = appear(t, d * 0.55)
    rich(img, (W / 2 - width("Finding it by hand is slow, and the costliest problems stay hidden.", 46) / 2, 770),
         [("Finding it by hand is slow, and the costliest problems stay ", "r", WHITE), ("hidden.", "b", ORANGE)], 46, a)


def s_intro(img, t, d):
    a = appear(t, 0.1, 0.9)
    s = 170
    tw = width("StockFlow AI", 120, "serif")
    x0 = W / 2 - (s + 50 + tw) / 2
    logo(img, x0, 330 - s / 2 - 20 * (1 - a), s, a)
    text(img, (x0 + s + 50, 330), "StockFlow AI", 120, "serif", CREAM, a, "lm")
    text(img, (W / 2, 490), "Right stock.  Right store.  Right size.", 52, "sb", GOLD_L, appear(t, 1.0), "mm")
    a3 = appear(t, 1.6)
    lab = "CUSTOMIZED FOR"
    lw = width(lab, 28, "sb")
    total = lw + 30 + (MVLOGO.width * 92 / MVLOGO.height if MVLOGO else 0)
    x1 = W / 2 - total / 2
    text(img, (x1, 600), lab, 28, "sb", (236, 214, 190), a3, "lm")
    mv_logo(img, x1 + lw + 30, 554, 92, a3)
    a4 = appear(t, d * 0.45)
    rrect(img, (W / 2 - 560, 720, W / 2 + 560, 816), 8, (58, 11, 20), a4, outline=SAFFRON)
    text(img, (W / 2, 768), "Every morning: a ready-to-approve list of stock moves", 40, "sb", CREAM, a4, "mm")


STEPS = [("1", "Real selling speed", "Sales ÷ days actually on the shelf. Zero stock is not zero demand.", TEAL),
         ("2", "Season aware", "Wedding dates sell up to 45% more. Diwali and Chhath built in.", SAFFRON),
         ("3", "Plans every move", "Warehouse → store, store → store, old stock to where it sells, pull back.", ORANGE),
         ("4", "Checks every rule", "Capacity, minimum display, flagship top-20, 30-day cover. Re-checked.", PURPLE)]


def s_how(img, t, d):
    text(img, (W / 2, 150), "How it thinks", 74, "serif", WHITE, appear(t, 0.1), "mm")
    active = min(3, int(t / (d / 4)))
    for i, (n, title, desc, col) in enumerate(STEPS):
        a = appear(t, 0.4 + i * 0.25)
        x, y = 110 + i * 440, 300
        on = i == active
        rrect(img, (x, y, x + 380, y + 520), 30, (255, 243, 221) if on else CARD, a, outline=col if on else SAND, ow=4)
        circle(img, (x + 70, y + 80), 42, col, a)
        text(img, (x + 70, y + 80), n, 46, "b", CREAM, a, "mm")
        para(img, (x + 34, y + 160), title, 42, 320, "b", WHITE, a, 1.15)
        para(img, (x + 34, y + 280), desc, 31, 320, "r", WHITE if on else MUTED, a, 1.35)
        if i < 3:
            arrow(img, (x + 388, y + 260), (x + 432, y + 260), (200, 178, 145), a, 5)
    text(img, (W / 2, 900), "Every move comes with a one-line reason.", 40, "sb", TEAL, appear(t, d * 0.8), "mm")


def count(v, a):
    return v * ease(a)


def s_results(img, t, d):
    text(img, (W / 2, 140), "On Manyavar's data", 74, "serif", WHITE, appear(t, 0.1), "mm")
    tiles = [("Sales protected", lambda k: f"₹{count(1.10, k):.2f} Cr", "1,622 pieces to stores about to run short", TEAL),
             ("Hidden lost sales found", lambda k: f"₹{count(75.9, k):.1f} L", "260 sizes out of stock for 4+ weeks", ORANGE),
             ("Old stock put to work", lambda k: f"₹{count(74.3, k):.1f} L", "513 pieces older than 120 days", SAFFRON),
             ("Business rules passed", lambda k: f"{int(round(count(11, k)))}/11", "re-checked independently after planning", PURPLE)]
    for i, (lab, val, sub, col) in enumerate(tiles):
        start = 0.5 + i * (d * 0.17)
        a = appear(t, start)
        k = clamp((t - start) / 1.6)
        x, y = 160 + (i % 2) * 820, 250 + (i // 2) * 300
        rrect(img, (x, y, x + 780, y + 260), 30, CARD, a, outline=col, ow=3)
        text(img, (x + 44, y + 50), lab.upper(), 28, "sb", MUTED, a, "lm")
        text(img, (x + 40, y + 140), val(k), 96, "b", col, a, "lm")
        text(img, (x + 44, y + 220), sub, 28, "r", MUTED, a, "lm")
    a = appear(t, d * 0.78)
    x = W / 2 - 640
    for s, c in [("1,166 moves planned", BROWN), ("26 data problems found", BROWN), ("every move explained", BROWN)]:
        x += chip(img, x, 900, s, 32, c, a) + 30


def bars(img, x, y, vals, maxv, colw, h, col, a, labels=None):
    for i, v in enumerate(vals):
        bh = h * v / maxv * ease(a * 1.2 - i * 0.12)
        rrect(img, (x + i * (colw + 14), y + h - bh, x + i * (colw + 14) + colw, y + h), 8, col, a)
        if labels:
            text(img, (x + i * (colw + 14) + colw / 2, y + h + 26), labels[i], 24, "r", MUTED, a, "mm")


def s_hidden(img, t, d):
    text(img, (W / 2, 140), "It found what was hidden", 74, "serif", WHITE, appear(t, 0.1), "mm")
    seg = d / 3
    cards = [
        (ORANGE, "MZF-01 · Muzaffarpur", "37%", "shelves full, busiest A-tier store", "32 sizes missing for a month. ₹12 L of sales at risk. Refilled first."),
        (PURPLE, "KNP-03 · Kanpur", "300 → 0", "sherwanis that didn't exist", "Stock file and weekly count disagree. Ignored and flagged for a recount."),
        (TEAL, "KUR-04 · Ivory Chikankari", "8×", "weekly sales in four weeks", "New, not slow: 50 → 401 a week. Restocked from the big delivery."),
    ]
    for i, (col, head, big, sub, desc) in enumerate(cards):
        a = appear(t, 0.4 + i * seg * 0.85)
        x, y = 110 + i * 580, 260 + 30 * (1 - a)
        rrect(img, (x, y, x + 540, y + 640), 30, CARD, a, outline=col, ow=4)
        text(img, (x + 40, y + 55), head, 32, "sb", col, a, "lm")
        text(img, (x + 40, y + 160), big, 110, "b", WHITE, a, "lm")
        text(img, (x + 40, y + 250), sub, 30, "r", MUTED, a, "lm")
        if i == 0:
            rrect(img, (x + 40, y + 300, x + 500, y + 340), 20, SAND, a)
            rrect(img, (x + 40, y + 300, x + 40 + 460 * 0.37 * ease(clamp((t - 0.4) / 1.5)), y + 340), 20, ORANGE, a)
        elif i == 1:
            bars(img, x + 120, y + 290, [300, 0.01], 300, 110, 90, PURPLE, appear(t, 0.4 + seg * 0.85, 1.2), ["file", "count"])
        else:
            bars(img, x + 60, y + 285, [50, 173, 184, 401], 401, 80, 100, TEAL, appear(t, 0.4 + 2 * seg * 0.85, 1.4),
                 ["wk1", "wk2", "wk3", "wk4"])
        para(img, (x + 40, y + 440), desc, 29, 460, "r", WHITE, a, 1.35)


TOUR = [("01_leaders.png", "Leaders see the key insights on one screen", (0.5, 0.45)),
        ("05_problems_more.png", "Every problem explained, with a chart", (0.5, 0.5)),
        ("07_approve.png", "Managers approve whole trips in a few clicks", (0.4, 0.45))]
_cache = {}


def src(name):
    if name not in _cache:
        im = shot(name)
        if "mobile" not in name:  # keep only the app's centred content column
            im = im.crop((320, 0, 1600, im.height))
        _cache[name] = im
    return _cache[name]


def s_tour(img, t, d):
    seg = d / len(TOUR)
    i = min(len(TOUR) - 1, int(t / seg))
    lt = (t - i * seg) / seg
    fade = clamp(min((t - i * seg) / 0.35, (seg - (t - i * seg)) / 0.35)) if i < len(TOUR) - 1 else clamp((t - i * seg) / 0.35)
    name, label, focus = TOUR[i]
    framed(img, src(name), (300, 170, 1620, 912), lt, (1.0, 1.1), focus, fade)
    chip(img, 300, 92, label, 34, TEAL, fade)
    for j in range(len(TOUR)):
        circle(img, (W / 2 - 40 + j * 40, 942), 9, TEAL if j == i else SAND)


def s_chat(img, t, d):
    a = appear(t, 0.1)
    framed(img, src("08_chat.png"), (90, 200, 1250, 853), clamp(t / d), (1.0, 1.03), (0.48, 0.8), a)
    chip(img, 90, 115, "Ask anything about the plan", 32, TEAL, a)
    a2 = appear(t, d * 0.3)
    chip(img, 90, 890, "Off-topic? It politely says no.", 32, ORANGE, a2)
    a3 = appear(t, d * 0.45)
    px, py, pw, ph = 1430, 100, 380, 790
    rrect(img, (px - 18, py - 18, px + pw + 18, py + ph + 18), 60, BROWN, a3)
    rrect(img, (px - 8, py - 8, px + pw + 8, py + ph + 8), 52, (40, 15, 5), a3)
    if a3 > 0.01:
        view = pan(src("10_mobile_leaders.png"), pw, ph, clamp((t - d * 0.45) / (d * 0.5)))
        mask = Image.new("L", (pw, ph), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, pw - 1, ph - 1), 44, fill=int(255 * a3))
        img.paste(view, (px, py), mask)
    chip(img, 1490, 912, "Works on a phone", 30, PURPLE, a3)
    a4 = appear(t, d * 0.7)
    chip(img, 700, 890, "Learns from feedback after every run", 30, BROWN, a4)


QR = None


def s_cta(img, t, d):
    global QR
    if QR is None:
        q = qrcode.QRCode(border=2, box_size=10)
        q.add_data("https://" + LIVE)
        QR = q.make_image(fill_color="black", back_color="white").convert("RGB").resize((300, 300), Image.NEAREST)
    a = appear(t, 0.1, 0.8)
    logo(img, 200, 180, 130, a)
    text(img, (370, 245), "StockFlow AI", 104, "serif", CREAM, a, "lm")
    text(img, (205, 375), "Right stock.  Right store.  Right size.", 50, "sb", GOLD_L, appear(t, 0.8), "lm")
    a1 = appear(t, 1.2)
    text(img, (205, 455), "CUSTOMIZED FOR", 26, "sb", (236, 214, 190), a1, "lm")
    mv_logo(img, 205 + width("CUSTOMIZED FOR", 26, "sb") + 26, 418, 74, a1)
    a2 = appear(t, 1.6)
    text(img, (205, 560), "TRY IT LIVE", 30, "sb", (236, 214, 190), a2, "lm")
    text(img, (205, 625), LIVE, 62, "b", GOLD_L, a2, "lm")
    text(img, (205, 715), "Code + data:  " + REPO, 34, "r", CREAM, appear(t, 2.2), "lm")
    text(img, (205, 790), "Team SYNAPSE  ·  Manyavar AI Hackathon 2026", 30, "r", (214, 190, 170), appear(t, 2.6), "lm")
    a3 = appear(t, 1.8)
    rrect(img, (1430, 330, 1770, 670), 10, (255, 255, 255), a3, outline=SAFFRON)
    if a3 > 0.01:
        m = Image.new("L", QR.size, int(255 * a3))
        img.paste(QR, (1450, 350), m)
    text(img, (1600, 710), "Scan to open the app", 28, "r", (236, 214, 190), a3, "mm")


# ----------------------------------------------------------------------------- script
SCENES = [
    (s_hook, "Right now, a customer is walking out of a store, because their size isn't on the shelf. "
             "And in another store, that same size is gathering dust."),
    (s_problem, "Across twenty stores and a warehouse, stock ends up in the wrong place every single day. "
                "Finding it by hand is slow, and the costliest problems stay hidden."),
    (s_intro, "Meet StockFlow AI, customized for Manyavar. Every morning, it finds stock in the wrong place, "
              "and writes a ready-to-approve list of moves."),
    (s_how, "It measures how fast every size really sells, counting only the days it was on the shelf. "
            "It adds the wedding and festival calendar. It plans warehouse deliveries, store-to-store transfers, "
            "and what to pull back. Then it re-checks every business rule, and explains every move in one line."),
    (s_results, "On Manyavar's data, it protected one point one crore rupees of sales, uncovered seventy-six lakh "
                "of hidden lost sales, put seventy-four lakh of old stock back to work, and passed all eleven business rules."),
    (s_hidden, "And it found what was hidden. A busy store in Muzaffarpur was only thirty-seven percent stocked. "
               "A record claimed three hundred sherwanis that didn't exist. And a new kurta set wasn't slow at all. "
               "Its sales grew eight times in four weeks."),
    (s_tour, "Leaders get the key insights on one screen. Every problem is explained in plain language, with a chart. "
             "And managers approve whole trips in a few clicks."),
    (s_chat, "Anyone can ask questions about the plan, and it politely declines anything else. "
             "It works on a phone, and it learns from feedback after every run."),
    (s_cta, "StockFlow AI. Right stock. Right store. Right size. Try it live today."),
]
CAPTION_FIX = {"twenty stores": "20 stores", "one point one crore rupees": "₹1.1 crore", "seventy-six lakh": "₹76 lakh",
               "seventy-four lakh": "₹74 lakh", "eleven": "11", "thirty-seven percent": "37%",
               "three hundred": "300", "eight times": "8×"}


def tts(i, s):
    out = AUD / f"s{i}.wav"
    ps = (f"Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
          f"$s.SelectVoice('{VOICE}'); $s.Rate={RATE}; "
          f"$f=New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(44100,[System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,[System.Speech.AudioFormat.AudioChannel]::Mono); "
          f"$s.SetOutputToWaveFile('{out}',$f); $s.Speak([IO.File]::ReadAllText('{AUD / f's{i}.txt'}')); $s.Dispose()")
    (AUD / f"s{i}.txt").write_text(s, encoding="utf-8")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True)
    with wave.open(str(out)) as w:
        return w.getnframes() / w.getframerate()


def captions(s, dur, lead):
    for k, v in CAPTION_FIX.items():
        s = s.replace(k, v)
    words = s.split()
    chunks, cur = [], []
    for wd in words:
        cur.append(wd)
        if len(cur) >= 9 or wd.endswith((".", "?", "!")) and len(cur) >= 4:
            chunks.append(" ".join(cur))
            cur = []
    if cur:
        chunks.append(" ".join(cur))
    total = sum(len(c) for c in chunks)
    out, t0 = [], lead
    for c in chunks:
        dt = dur * len(c) / total
        out.append((t0, t0 + dt, c))
        t0 += dt
    return out


def caption(img, txt):
    if not txt:
        return
    size = 36
    w = width(txt, size, "sb")
    rrect(img, (W / 2 - w / 2 - 28, 1000 - 30, W / 2 + w / 2 + 28, 1000 + 30), 8, BROWN, 0.9)
    text(img, (W / 2, 1000), txt, size, "sb", CREAM, 1.0, "mm")


def main():
    preview = "--frames" in sys.argv
    LEAD, TAIL = 0.6, 1.1
    plan, audio, t = [], [], 0.0
    for i, (fn, narration) in enumerate(SCENES):
        dur_a = tts(i, narration)
        dur = LEAD + dur_a + TAIL
        plan.append(dict(fn=fn, start=t, dur=dur, caps=captions(narration, dur_a, t + LEAD)))
        audio.append((t + LEAD, AUD / f"s{i}.wav"))
        t += dur
        print(f"scene {i}: {dur:.1f}s")
    total = t
    print(f"total {total:.1f}s")

    # voice track
    sr = 44100
    track = np.zeros(int(total * sr) + sr, np.int16)
    for start, p in audio:
        with wave.open(str(p)) as w:
            data = np.frombuffer(w.readframes(w.getnframes()), np.int16)
        i0 = int(start * sr)
        track[i0:i0 + len(data)] = data
    with wave.open(str(AUD / "voice.wav"), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr); w.writeframes(track.tobytes())

    silent = D / "_video.mp4"
    writer = imageio.get_writer(silent, fps=FPS, codec="libx264", quality=8, macro_block_size=1,
                                ffmpeg_params=["-pix_fmt", "yuv420p", "-preset", "medium"])
    n = int(total * FPS)
    allcaps = [c for p in plan for c in p["caps"]]
    for f in range(n):
        tt = f / FPS
        sc = next(p for p in reversed(plan) if p["start"] <= tt)
        lt = tt - sc["start"]
        base = HEROIMG if sc["fn"] in (s_intro, s_cta) else BGIMG
        img = base.copy()
        sc["fn"](img, lt, sc["dur"])
        # brand + progress
        if sc["fn"] not in (s_intro, s_cta):
            logo(img, 40, 28, 48, 1.0)
            text(img, (102, 52), "StockFlow AI", 30, "serif", BROWN, 1.0, "lm")
            lw = MVLOGO.width * 40 / MVLOGO.height if MVLOGO else 0
            mv_logo(img, W - 40 - lw, 32, 40, 1.0)
            text(img, (W - 56 - lw, 52), "CUSTOMIZED FOR", 18, "sb", MUTED, 1.0, "rm")
        rrect(img, (0, H - 6, W * tt / total, H), 0, TEAL, 0.9)
        cap = next((c for a, b, c in allcaps if a <= tt < b), "")
        caption(img, cap)
        # dip to background between scenes
        edge = min(lt / 0.35, (sc["dur"] - lt) / 0.35)
        if edge < 1:
            img = Image.blend(base, img, clamp(edge))
        writer.append_data(np.asarray(img))
        if preview and abs(lt - sc["dur"] * 0.7) < 0.5 / FPS:
            img.save(D / f"preview_{plan.index(sc)}.png")
        if f % 300 == 0:
            print(f"frame {f}/{n}")
    writer.close()

    out = D / "StockFlowAI_pitch.mp4"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-i", str(silent), "-i", str(AUD / "voice.wav"),
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest", "-movflags", "+faststart", str(out)], check=True)
    silent.unlink()
    print("done", out, f"{out.stat().st_size / 1e6:.1f} MB", f"{total:.1f}s")


if __name__ == "__main__":
    main()
