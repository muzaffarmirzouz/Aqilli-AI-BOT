"""Kanal uchun 1080x1350 namoz vaqtlari posteri (Pillow)."""
import io
import math
import os
from datetime import date

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import AYAH_SOURCE, AYAH_TEXT, FONT_DIR, PRAYER_KEYS, TAKBIR

W, H = 1080, 1350
K = 2  # supersampling: 2x chizib, keyin kichraytiramiz (silliq chiziqlar uchun)

MONTHS = ["YANVAR", "FEVRAL", "MART", "APREL", "MAY", "IYUN", "IYUL", "AVGUST",
          "SENTYABR", "OKTYABR", "NOYABR", "DEKABR"]
DAYS = ["DUSHANBA", "SESHANBA", "CHORSHANBA", "PAYSHANBA", "JUMA", "SHANBA", "YAKSHANBA"]
NAMES = ["BOMDOD", "QUYOSH", "PESHIN", "ASR", "SHOM", "XUFTON"]

BG1, BG2 = (20, 82, 78), (5, 26, 27)
GOLD = (227, 184, 101)
TXT = (245, 240, 228)
MUT = (245, 240, 228, 158)
PAN = (255, 255, 255, 14)
PLINE = (227, 184, 101, 90)
PAT = (227, 184, 101, 18)
CHIP_TXT = (26, 20, 5)

# ---------------- shriftlar ----------------
_FONT_FILES = {
    "display": ("Unbounded.ttf", 700),
    "num": ("BarlowCondensed-Bold.ttf", None),
    "body": ("Manrope.ttf", 700),
    "body800": ("Manrope.ttf", 800),
    "body600": ("Manrope.ttf", 600),
}
_FALLBACK = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf",
]
_fcache: dict = {}


def font(role: str, size: int) -> ImageFont.FreeTypeFont:
    key = (role, size)
    if key in _fcache:
        return _fcache[key]
    fname, wght = _FONT_FILES[role]
    path = os.path.join(FONT_DIR, fname)
    f = None
    if os.path.exists(path):
        try:
            f = ImageFont.truetype(path, size * K)
            if wght:
                try:
                    f.set_variation_by_axes([wght])
                except Exception:
                    pass
        except Exception:
            f = None
    if f is None:
        for p in (_FALLBACK[1] if role == "num" else _FALLBACK[0], _FALLBACK[0]):
            if os.path.exists(p):
                f = ImageFont.truetype(p, size * K)
                break
        else:
            f = ImageFont.load_default()
    _fcache[key] = f
    return f


# ---------------- yordamchilar ----------------
def s(v):
    return int(round(v * K))


def text_w(d, txt, f, sp=0):
    return d.textlength(txt, font=f) + sp * K * max(len(txt) - 1, 0)


def spaced(d, x, y, txt, f, fill, sp=0, anchor="l"):
    """Harflar orasini kengaytirib yozadi. y — baseline (logik px)."""
    w = text_w(d, txt, f, sp)
    cx = s(x) - (w / 2 if anchor == "c" else w if anchor == "r" else 0)
    if not sp:
        d.text((cx, s(y)), txt, font=f, fill=fill, anchor="ls")
        return w / K
    for ch in txt:
        d.text((cx, s(y)), ch, font=f, fill=fill, anchor="ls")
        cx += d.textlength(ch, font=f) + sp * K
    return w / K


def rr(d, x, y, w, h, r, fill=None, outline=None, width=1):
    d.rounded_rectangle([s(x), s(y), s(x + w), s(y + h)], radius=s(r), fill=fill,
                        outline=outline, width=s(width) if outline else 0)


def star_pts(cx, cy, r):
    pts = []
    for i in range(16):
        a = math.pi / 8 * i - math.pi / 2
        rad = r * 0.62 if i % 2 else r
        pts.append((s(cx + rad * math.cos(a)), s(cy + rad * math.sin(a))))
    return pts


def wrap(d, text, f, maxw):
    words, lines, line = text.split(), [], ""
    for w_ in words:
        t = f"{line} {w_}".strip()
        if d.textlength(t, font=f) > s(maxw) and line:
            lines.append(line)
            line = w_
        else:
            line = t
    if line:
        lines.append(line)
    return lines


def mins(t):
    h, m = t.split(":")
    return int(h) * 60 + int(m)


# ---------------- ikonkalar ----------------
def icon(d, kind, cx, cy, c, bgc):
    lw = s(3)

    def ray(r1, r2, a):
        d.line([(s(cx + r1 * math.cos(a)), s(cy + r1 * math.sin(a))),
                (s(cx + r2 * math.cos(a)), s(cy + r2 * math.sin(a)))], fill=c, width=lw)

    def disc(x, y, r, col):
        d.ellipse([s(x - r), s(y - r), s(x + r), s(y + r)], fill=col)

    if kind in ("bomdod", "xufton"):
        disc(cx, cy, 13, c)
        disc(cx + 7, cy - 5, 10.5, bgc)
        if kind == "xufton":
            d.polygon(star_pts(cx + 12, cy - 9, 5), fill=c)
        else:
            d.line([(s(cx - 17), s(cy + 17)), (s(cx + 17), s(cy + 17))], fill=c, width=lw)
    elif kind in ("quyosh", "shom"):
        d.pieslice([s(cx - 9), s(cy - 1), s(cx + 9), s(cy + 17)], 180, 360, fill=c)
        d.line([(s(cx - 18), s(cy + 8)), (s(cx + 18), s(cy + 8))], fill=c, width=lw)
        for i in range(5):
            a = math.pi * (1.08 + 0.84 * i / 4)
            r1, r2 = 13, 18
            d.line([(s(cx + r1 * math.cos(a)), s(cy + 8 + r1 * math.sin(a))),
                    (s(cx + r2 * math.cos(a)), s(cy + 8 + r2 * math.sin(a)))], fill=c, width=lw)
        tip = cy - 21 if kind == "quyosh" else cy - 9
        d.line([(s(cx), s(cy - 21)), (s(cx), s(cy - 9))], fill=c, width=lw)
        dy = 4 if kind == "quyosh" else -4
        d.line([(s(cx - 4), s(tip + dy)), (s(cx), s(tip)), (s(cx + 4), s(tip + dy))], fill=c, width=lw)
    elif kind == "peshin":
        disc(cx, cy, 8, c)
        for i in range(8):
            ray(12, 17, math.pi / 4 * i)
    elif kind == "asr":
        disc(cx + 5, cy + 3, 8, c)
        for i in range(4):
            a = math.pi * (0.9 + 0.7 * i / 3)
            d.line([(s(cx + 5 + 12 * math.cos(a)), s(cy + 3 + 12 * math.sin(a))),
                    (s(cx + 5 + 17 * math.cos(a)), s(cy + 3 + 17 * math.sin(a)))], fill=c, width=lw)
        d.line([(s(cx - 18), s(cy + 18)), (s(cx + 18), s(cy + 18))], fill=c, width=lw)


def weather_icon(d, code, cx, cy, c):
    code = int(code or 0)
    lw = s(3)
    sun = code in (0, 1, 2)
    cloud = code >= 2
    if sun:
        ox, oy = (cx - 8, cy - 8) if cloud else (cx, cy)
        d.ellipse([s(ox - 11), s(oy - 11), s(ox + 11), s(oy + 11)], fill=c)
        for i in range(8):
            a = math.pi / 4 * i
            d.line([(s(ox + 16 * math.cos(a)), s(oy + 16 * math.sin(a))),
                    (s(ox + 22 * math.cos(a)), s(oy + 22 * math.sin(a)))], fill=c, width=lw)
    if cloud:
        col = (240, 236, 226)
        for (x, y, r) in ((cx - 10, cy + 6, 11), (cx + 4, cy - 1, 15), (cx + 17, cy + 7, 10)):
            d.ellipse([s(x - r), s(y - r), s(x + r), s(y + r)], fill=col)
        d.rounded_rectangle([s(cx - 21), s(cy + 4), s(cx + 27), s(cy + 17)], radius=s(6), fill=col)
    if code >= 51 and code not in (71, 73, 75, 77, 85, 86):
        for x in (cx - 10, cx + 2, cx + 14):
            d.line([(s(x), s(cy + 22)), (s(x - 4), s(cy + 32))], fill=(130, 190, 255), width=lw)
    if code in (71, 73, 75, 77, 85, 86):
        for x in (cx - 10, cx + 2, cx + 14):
            d.ellipse([s(x - 3), s(cy + 24), s(x + 3), s(cy + 30)], fill=(255, 255, 255))
    if code >= 95:
        d.line([(s(cx + 2), s(cy + 18)), (s(cx - 4), s(cy + 28)), (s(cx + 4), s(cy + 28)),
                (s(cx - 2), s(cy + 38))], fill=GOLD, width=lw)


# ---------------- asosiy chizish ----------------
def render(*, d: date, city: str, times: dict, hijri: str = "", brand: str = "",
           footer: str = "", weather: dict | None = None, usd: dict | None = None,
           weather_text: str = "") -> bytes:
    SW, SH = s(W), s(H)
    # fon gradienti
    grad = Image.new("RGB", (1, 256))
    for i in range(256):
        t = i / 255
        grad.putpixel((0, i), tuple(int(BG1[j] + (BG2[j] - BG1[j]) * t) for j in range(3)))
    img = grad.resize((SW, SH), Image.BILINEAR).convert("RGBA")

    # yuqori o'ngdagi iliq nur
    glow = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([s(W * 0.78 - 420), s(120 - 420), s(W * 0.78 + 420), s(120 + 420)], fill=(227, 184, 101, 55))
    glow = glow.filter(ImageFilter.GaussianBlur(s(160)))
    img = Image.alpha_composite(img, glow)

    # girih naqsh
    pat = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pat)
    S_ = 96
    y = -S_ / 2
    while y < H + S_:
        x = -S_ / 2
        while x < W + S_:
            pts = star_pts(x, y, S_ * 0.42)
            pd.line(pts + [pts[0]], fill=PAT, width=s(1.5))
            c = S_ * 0.16 * math.sqrt(2)
            sq = [(s(x + S_ / 2), s(y + S_ / 2 - c)), (s(x + S_ / 2 + c), s(y + S_ / 2)),
                  (s(x + S_ / 2), s(y + S_ / 2 + c)), (s(x + S_ / 2 - c), s(y + S_ / 2))]
            pd.line(sq + [sq[0]], fill=PAT, width=s(1.5))
            x += S_
        y += S_
    img = Image.alpha_composite(img, pat)

    # pastki qorong'ilik
    vg = Image.new("L", (1, 256))
    for i in range(256):
        t = max(0, (i / 255 - 0.55) / 0.45)
        vg.putpixel((0, i), int(90 * t))
    shade = Image.new("RGBA", (SW, SH), (0, 0, 0, 255))
    shade.putalpha(vg.resize((SW, SH)))
    img = Image.alpha_composite(img, shade)

    lay = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    dr = ImageDraw.Draw(lay)
    M = 72

    # --- sarlavha: brend belgisi + nomi
    brand = (brand or "").strip()
    rr(dr, M, 64, 84, 84, 18, fill=GOLD)
    words = [w for w in brand.replace(".", " ").split() if w[:1].isalnum()]
    ini = "".join(w[0] for w in words[:2]).upper() or "NV"
    if brand.upper().startswith("NAMANGANLIKLAR"):
        ini = "NG"
    dr.text((s(M + 42), s(106)), ini, font=font("display", 32 if len(ini) > 1 else 40), fill=CHIP_TXT, anchor="mm")
    if brand:
        bf = font("display", 22)
        b = brand.upper()
        while text_w(dr, b, bf, 1) / K > W - 2 * M - 104 - 260 and len(b) > 4:
            b = b[:-2].rstrip() + "…" if not b.endswith("…") else b[:-2] + "…"
        spaced(dr, M + 104, 114, b, bf, TXT, 1)

    # --- shahar
    cty = city.upper()
    cf = font("body800", 22)
    cw = text_w(dr, cty, cf, 2.5) / K + 70
    rr(dr, W - M - cw, 78, cw, 56, 28, fill=PAN, outline=PLINE, width=2)
    px, py = W - M - cw + 30, 104
    dr.pieslice([s(px - 8), s(py - 11), s(px + 8), s(py + 5)], 180, 360, fill=GOLD)
    dr.polygon([(s(px - 8), s(py - 3)), (s(px + 8), s(py - 3)), (s(px), s(py + 11))], fill=GOLD)
    dr.ellipse([s(px - 3), s(py - 6), s(px + 3), s(py)], fill=BG2)
    spaced(dr, W - M - cw + 48, 115, cty, cf, TXT, 2.5)

    # --- sarlavha va sana
    spaced(dr, M, 236, "NAMOZ VAQTLARI", font("body800", 24), GOLD, 6)
    big = f"{d.day}-{MONTHS[d.month - 1]}"
    bf = font("display", 78)
    dr.text((s(M - 3), s(322)), big, font=bf, fill=TXT, anchor="ls")
    bw = dr.textlength(big, font=bf) / K
    dr.text((s(M + bw + 20), s(322)), str(d.year), font=font("display", 34), fill=GOLD, anchor="ls")
    lx = M + spaced(dr, M, 370, DAYS[d.weekday()], font("body800", 24), TXT, 3)
    if hijri:
        dr.ellipse([s(lx + 16), s(358), s(lx + 24), s(366)], fill=GOLD)
        dr.text((s(lx + 38), s(370)), f"{hijri} h.", font=font("body600", 24), fill=MUT, anchor="ls")

    # --- quyosh yo'li
    T = [mins(times[k]) for k in PRAYER_KEYS]
    hy, x0, x1, A = 496, M + 10, W - M - 10, 100
    tmin, tmax = T[0] - 25, T[5] + 25
    X = lambda t: x0 + (x1 - x0) * (t - tmin) / (tmax - tmin)

    def Y(t):
        f = (t - T[1]) / (T[4] - T[1])
        return hy if f <= 0 or f >= 1 else hy - A * math.sin(math.pi * f)

    xx = x0
    while xx < x1:
        dr.line([(s(xx), s(hy)), (s(xx + 2), s(hy))], fill=PLINE, width=s(2))
        xx += 10
    arc = [(s(X(t)), s(Y(t))) for t in range(T[1], T[4] + 1, 3)] + [(s(X(T[4])), s(hy))]
    fill_l = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    ImageDraw.Draw(fill_l).polygon([(s(X(T[1])), s(hy))] + arc, fill=(227, 184, 101, 40))
    lay = Image.alpha_composite(lay, fill_l)
    dr = ImageDraw.Draw(lay)
    dr.line(arc, fill=GOLD, width=s(3), joint="curve")
    for i, k in enumerate(PRAYER_KEYS):
        x, y_ = X(T[i]), Y(T[i])
        night = i in (0, 5)
        if night:
            dr.ellipse([s(x - 8), s(y_ - 8), s(x + 8), s(y_ + 8)], fill=BG2, outline=GOLD, width=s(3))
        else:
            dr.ellipse([s(x - 16), s(y_ - 16), s(x + 16), s(y_ + 16)], outline=PLINE, width=s(2))
            dr.ellipse([s(x - 9), s(y_ - 9), s(x + 9), s(y_ + 9)], fill=GOLD)
        spaced(dr, x, hy - 20 if night else hy + 34, NAMES[i], font("body800", 15), MUT, 1.5, "c")

    # --- vaqt kartalari
    gy, gap, th = 562, 18, 138
    tw = (W - 2 * M - gap) / 2
    card_bg = tuple(int(BG1[j] * 0.75 + BG2[j] * 0.25) for j in range(3))
    for i, k in enumerate(PRAYER_KEYS):
        x = M + (i % 2) * (tw + gap)
        y_ = gy + (i // 2) * (th + gap)
        rr(dr, x, y_, tw, th, 22, fill=PAN, outline=PLINE, width=1.5)
        icon(dr, k, x + 46, y_ + 42, GOLD, card_bg)
        spaced(dr, x + 80, y_ + 51, NAMES[i], font("display", 22), TXT, 2)
        tk = TAKBIR[i] if i < len(TAKBIR) else 0
        lbl = f"TAKBIR +{tk}"
        lf = font("body800", 19)
        cw_ = text_w(dr, lbl, lf, 1.2) / K + 32 if tk else 0
        room = tw - 30 - (text_w(dr, "TAKBIR +40", lf, 1.2) / K + 32 + 46)  # hamma kartada bir xil o'lcham
        size = 96
        while size > 50 and dr.textlength(times[k], font=font("num", size)) / K > room:
            size -= 4
        dr.text((s(x + 30), s(y_ + 124)), times[k], font=font("num", size), fill=(255, 255, 255), anchor="ls")
        if tk:
            cx_, cy_ = x + tw - 26 - cw_, y_ + 80
            rr(dr, cx_, cy_, cw_, 38, 19, fill=GOLD)
            spaced(dr, cx_ + 16, cy_ + 26, lbl, lf, CHIP_TXT, 1.2)

    # --- oyat
    yy = gy + 3 * th + 2 * gap + 48
    af = font("body600", 23)
    for i, line in enumerate(wrap(dr, f"“{AYAH_TEXT}”", af, W - 2 * M)[:2]):
        dr.text((s(W / 2), s(yy + i * 33)), line, font=af, fill=TXT, anchor="ms")
        last = i
    yy += (last + 1) * 33
    spaced(dr, W / 2, yy + 2, AYAH_SOURCE.upper(), font("body800", 16), GOLD, 2, "c")

    # --- pastki ma'lumot: ob-havo va dollar
    by, bh = H - 60 - 132, 132
    bw_ = (W - 2 * M - gap) / 2
    rr(dr, M, by, bw_, bh, 22, fill=PAN, outline=PLINE, width=1.5)
    rr(dr, M + bw_ + gap, by, bw_, bh, 22, fill=PAN, outline=PLINE, width=1.5)
    spaced(dr, M + 28, by + 36, "OB-HAVO", font("body800", 15), GOLD, 2)
    if weather:
        weather_icon(dr, weather["code"], M + 58, by + 70, GOLD)
        dr.text((s(M + 108), s(by + 100)), f"{weather['tmax']:+d}°", font=font("num", 56), fill=(255, 255, 255), anchor="ls")
        tw2 = dr.textlength(f"{weather['tmax']:+d}°", font=font("num", 56)) / K
        dr.text((s(M + 116 + tw2), s(by + 100)), f"/ {weather['tmin']:+d}°", font=font("num", 32), fill=MUT, anchor="ls")
        if weather_text:
            dr.text((s(M + 28), s(by + 122)), weather_text, font=font("body600", 16), fill=MUT, anchor="ls")
    else:
        dr.text((s(M + 28), s(by + 90)), "—", font=font("num", 48), fill=MUT, anchor="ls")
    ux = M + bw_ + gap
    spaced(dr, ux + 28, by + 36, "1 USD (MB)", font("body800", 15), GOLD, 2)
    if usd:
        val = f"{usd['rate']:,.2f}".replace(",", " ")
        dr.text((s(ux + 28), s(by + 100)), val, font=font("num", 56), fill=(255, 255, 255), anchor="ls")
        vw = dr.textlength(val, font=font("num", 56)) / K
        dr.text((s(ux + 36 + vw), s(by + 100)), "so'm", font=font("body600", 20), fill=MUT, anchor="ls")
        diff = usd.get("diff", 0)
        col = (120, 220, 150) if diff > 0 else (255, 140, 120) if diff < 0 else MUT
        arrow = "▲" if diff > 0 else "▼" if diff < 0 else "•"
        dr.text((s(ux + 28), s(by + 122)), f"{arrow} {diff:+.2f}  ·  {usd.get('date', '')}",
                font=font("body600", 16), fill=col, anchor="ls")
    else:
        dr.text((s(ux + 28), s(by + 90)), "—", font=font("num", 48), fill=MUT, anchor="ls")

    if footer:
        spaced(dr, W / 2, H - 26, footer.upper(), font("body800", 16), MUT, 2, "c")

    img = Image.alpha_composite(img, lay).convert("RGB").resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=93, optimize=True)
    return buf.getvalue()
