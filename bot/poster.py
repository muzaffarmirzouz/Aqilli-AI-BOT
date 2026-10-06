"""Kanal uchun 1080x1350 namoz vaqtlari posteri (Pillow)."""
import io
import math
import os
from datetime import date

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .config import FONT_DIR, PRAYER_KEYS, TAKBIR

W, H = 1080, 1350
K = 2  # supersampling: 2x chizib, keyin kichraytiramiz (silliq chiziqlar uchun)

MONTHS = ["YANVAR", "FEVRAL", "MART", "APREL", "MAY", "IYUN", "IYUL", "AVGUST",
          "SENTYABR", "OKTYABR", "NOYABR", "DEKABR"]
DAYS = ["DUSHANBA", "SESHANBA", "CHORSHANBA", "PAYSHANBA", "JUMA", "SHANBA", "YAKSHANBA"]
NAMES = ["BOMDOD", "QUYOSH", "PESHIN", "ASR", "SHOM", "XUFTON"]

GOLD = (227, 184, 101)

# Rang uslublari: fon (yuqori, past), urg'u, matn, vaqt raqamlari, karta foni
THEMES = {
    "zumrad": dict(title="💚 Zumrad", b1=(20, 82, 78), b2=(5, 26, 27), acc=(227, 184, 101),
                   txt=(245, 240, 228), time=(255, 255, 255), card=(255, 255, 255, 18), dark=True),
    "kok":    dict(title="💙 Tungi ko'k", b1=(28, 47, 120), b2=(6, 12, 38), acc=(242, 196, 107),
                   txt=(242, 244, 251), time=(255, 255, 255), card=(255, 255, 255, 20), dark=True),
    "bordo":  dict(title="❤️ Bordo", b1=(110, 22, 40), b2=(32, 6, 12), acc=(240, 196, 120),
                   txt=(250, 238, 232), time=(255, 255, 255), card=(255, 255, 255, 18), dark=True),
    "qahva":  dict(title="🤎 Qahva", b1=(83, 48, 29), b2=(20, 10, 5), acc=(233, 184, 114),
                   txt=(247, 237, 226), time=(255, 255, 255), card=(255, 255, 255, 18), dark=True),
    "qora":   dict(title="🖤 Qora", b1=(38, 38, 42), b2=(8, 8, 10), acc=(230, 190, 110),
                   txt=(240, 240, 240), time=(255, 255, 255), card=(255, 255, 255, 16), dark=True),
    "oltin":  dict(title="🤍 Oq-oltin", b1=(251, 245, 232), b2=(230, 213, 174), acc=(140, 93, 16),
                   txt=(39, 27, 8), time=(24, 52, 49), card=(255, 255, 255, 150), dark=False),
}
DEFAULT_THEME = "zumrad"


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


def weather_icon(d, code, cx, cy, c, cloud_col=(240, 236, 226)):
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
        col = cloud_col
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
DEFAULT_LOGO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "assets", "namanganliklar_logo.png")


def default_logo() -> bytes | None:
    try:
        with open(DEFAULT_LOGO, "rb") as f:
            return f.read()
    except OSError:
        return None


def _load_logo(data: bytes, tint: bool, color) -> Image.Image | None:
    """Logotipni RGBA ga o'tkazadi. tint=True — oq belgini mavzu rangiga bo'yaydi."""
    try:
        im = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None
    if tint:
        solid = Image.new("RGBA", im.size, (*color[:3], 255))
        solid.putalpha(im.getchannel("A"))
        return solid
    if im.getchannel("A").getextrema()[0] == 255:
        # shaffof emas (JPEG) — burchaklarini yumaloqlaymiz
        mask = Image.new("L", im.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, im.width - 1, im.height - 1],
                                               radius=min(im.size) // 6, fill=255)
        im.putalpha(mask)
    return im


def _rgba(c, a):
    return (c[0], c[1], c[2], a)


def render(*, d: date, city: str, times: dict, hijri: str = "", brand: str = "",
           footer: str = "", weather: dict | None = None, usd: dict | None = None,
           weather_text: str = "", theme: str = DEFAULT_THEME, ad_text: str = "",
           ad_contact: str = "", ad_image: bytes | None = None,
           logo: bytes | None = None, logo_tint: bool = False) -> bytes:
    T_ = THEMES.get(theme, THEMES[DEFAULT_THEME])
    ACC, TXT, TIME = T_["acc"], T_["txt"], T_["time"]
    MUT = _rgba(TXT, 165)
    PLINE = _rgba(ACC, 100)
    PAT = _rgba(ACC, 20)
    CHIP_TXT = T_["b2"] if T_["dark"] else (255, 248, 232)
    SW, SH = s(W), s(H)

    # fon gradienti
    grad = Image.new("RGB", (1, 256))
    for i in range(256):
        t = i / 255
        grad.putpixel((0, i), tuple(int(T_["b1"][j] + (T_["b2"][j] - T_["b1"][j]) * t) for j in range(3)))
    img = grad.resize((SW, SH), Image.BILINEAR).convert("RGBA")
    glow = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([s(W * 0.8 - 420), s(-300), s(W * 0.8 + 420), s(540)], fill=_rgba(ACC, 50))
    img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(s(160))))

    # girih naqsh
    pat = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    pd = ImageDraw.Draw(pat)
    S_ = 96
    for yy in range(-48, H + S_, S_):
        for xx in range(-48, W + S_, S_):
            pts = star_pts(xx, yy, S_ * 0.42)
            pd.line(pts + [pts[0]], fill=PAT, width=s(1.5))
    img = Image.alpha_composite(img, pat)

    lay = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
    dr = ImageDraw.Draw(lay)
    M = 56

    # --- 1. Sarlavha: brend belgisi + nomi | shahar
    brand = (brand or "").strip()
    logo_im = _load_logo(logo, logo_tint, TXT) if logo else None
    if logo_im is not None:
        lh = 80
        lw = min(220, logo_im.width * lh / logo_im.height)
        lh = lw * logo_im.height / logo_im.width
        li = logo_im.resize((s(lw), s(lh)), Image.LANCZOS)
        lay.paste(li, (s(M), s(86 - lh / 2)), li)
        dr = ImageDraw.Draw(lay)
        brand_x = M + lw + 20
    else:
        rr(dr, M, 48, 76, 76, 18, fill=ACC)
        words = [w for w in brand.replace(".", " ").split() if w[:1].isalnum()]
        ini = "".join(w[0] for w in words[:2]).upper() or "NV"
        dr.text((s(M + 38), s(87)), ini, font=font("display", 30 if len(ini) > 1 else 36), fill=CHIP_TXT, anchor="mm")
        brand_x = M + 96
    cty = city.upper()
    cf = font("body800", 22)
    cw = text_w(dr, cty, cf, 2.5) / K + 66
    rr(dr, W - M - cw, 58, cw, 56, 28, fill=T_["card"], outline=PLINE, width=2)
    px, py = W - M - cw + 28, 84
    dr.pieslice([s(px - 8), s(py - 11), s(px + 8), s(py + 5)], 180, 360, fill=ACC)
    dr.polygon([(s(px - 8), s(py - 3)), (s(px + 8), s(py - 3)), (s(px), s(py + 11))], fill=ACC)
    spaced(dr, W - M - cw + 46, 95, cty, cf, TXT, 2.5)
    if brand:
        bf = font("display", 22)
        b = brand.upper()
        maxw = W - M - cw - 24 - brand_x
        while text_w(dr, b, bf, 1) / K > maxw and len(b) > 4:
            b = b[:-2].rstrip("…") + "…"
        spaced(dr, brand_x, 95, b, bf, TXT, 1)

    # --- 2. Sana
    spaced(dr, M, 186, "NAMOZ VAQTLARI", font("body800", 24), ACC, 6)
    big = f"{d.day}-{MONTHS[d.month - 1]}"
    bf = font("display", 66)
    dr.text((s(M - 2), s(262)), big, font=bf, fill=TXT, anchor="ls")
    bw = dr.textlength(big, font=bf) / K
    dr.text((s(M + bw + 18), s(262)), str(d.year), font=font("display", 30), fill=ACC, anchor="ls")
    day_s = DAYS[d.weekday()]
    dr.text((s(W - M), s(262)), day_s, font=font("body800", 26), fill=TXT, anchor="rs")
    if hijri:
        dr.text((s(W - M), s(222)), f"{hijri} h.", font=font("body600", 20), fill=MUT, anchor="rs")

    # --- 3. Vaqt kartalari (asosiy, eng katta qism)
    gy, gap, th = 292, 14, 214
    tw = (W - 2 * M - gap) / 2
    nf = font("display", 24)
    # barcha kartalar uchun bir xil raqam o'lchami
    num_size = 168
    while num_size > 80 and max(dr.textlength(times[k], font=font("num", num_size)) for k in PRAYER_KEYS) / K > tw - 48:
        num_size -= 4
    nfont = font("num", num_size)
    card_rgb = tuple(int(T_["b1"][j] * 0.7 + T_["b2"][j] * 0.3) for j in range(3))
    if not T_["dark"]:
        card_rgb = (252, 248, 238)
    for i, k in enumerate(PRAYER_KEYS):
        x = M + (i % 2) * (tw + gap)
        y = gy + (i // 2) * (th + gap)
        rr(dr, x, y, tw, th, 26, fill=T_["card"], outline=PLINE, width=2)
        icon(dr, k, x + 44, y + 44, ACC, card_rgb)
        spaced(dr, x + 78, y + 53, NAMES[i], nf, TXT, 2)
        tk = TAKBIR[i] if i < len(TAKBIR) else 0
        if tk:
            lbl = f"TAKBIR +{tk}"
            lf = font("body800", 18)
            cw_ = text_w(dr, lbl, lf, 1) / K + 28
            rr(dr, x + tw - 22 - cw_, y + 22, cw_, 38, 19, fill=ACC)
            spaced(dr, x + tw - 22 - cw_ + 14, y + 48, lbl, lf, CHIP_TXT, 1)
        dr.text((s(x + tw / 2), s(y + th - 26)), times[k], font=nfont, fill=TIME, anchor="ms")

    # --- 4. Ob-havo va dollar (bitta qator)
    iy, ih = gy + 3 * th + 2 * gap + 14, 96
    rr(dr, M, iy, W - 2 * M, ih, 22, fill=T_["card"], outline=PLINE, width=2)
    mid = W / 2
    dr.line([(s(mid), s(iy + 18)), (s(mid), s(iy + ih - 18))], fill=PLINE, width=s(2))
    if weather:
        weather_icon(dr, weather["code"], M + 50, iy + 40, ACC, (240, 236, 226) if T_["dark"] else (175, 165, 145))
        t1 = f"{weather['tmax']:+d}°"
        f1 = font("num", 52)
        dr.text((s(M + 100), s(iy + 66)), t1, font=f1, fill=TIME, anchor="ls")
        w1 = dr.textlength(t1, font=f1) / K
        dr.text((s(M + 108 + w1), s(iy + 66)), f"/ {weather['tmin']:+d}°", font=font("num", 30), fill=MUT, anchor="ls")
        if weather_text:
            wt = weather_text
            wf = font("body600", 16)
            while dr.textlength(wt, font=wf) / K > mid - M - 120 and len(wt) > 6:
                wt = wt[:-2].rstrip() + "…"
            dr.text((s(M + 100), s(iy + 86)), wt, font=wf, fill=MUT, anchor="ls")
    else:
        dr.text((s(M + 28), s(iy + 62)), "Ob-havo: —", font=font("body800", 22), fill=MUT, anchor="ls")
    ux = mid + 28
    spaced(dr, ux, iy + 30, "1 USD · MARKAZIY BANK", font("body800", 14), ACC, 1.5)
    if usd:
        val = f"{usd['rate']:,.2f}".replace(",", " ")
        f2 = font("num", 48)
        dr.text((s(ux), s(iy + 76)), val, font=f2, fill=TIME, anchor="ls")
        vw = dr.textlength(val, font=f2) / K
        diff = usd.get("diff", 0)
        good, bad = (40, 150, 80) if not T_["dark"] else (120, 220, 150), (200, 60, 50) if not T_["dark"] else (255, 140, 120)
        col = good if diff > 0 else bad if diff < 0 else MUT
        arrow = "▲" if diff > 0 else "▼" if diff < 0 else "•"
        dr.text((s(ux + vw + 10), s(iy + 76)), f"so'm  {arrow}{abs(diff):.2f}", font=font("body800", 17), fill=col, anchor="ls")
    else:
        dr.text((s(ux), s(iy + 70)), "—", font=font("num", 40), fill=MUT, anchor="ls")

    # --- 5. Reklama joyi
    ay = iy + ih + 14
    ah = H - 40 - ay
    ax, aw = M, W - 2 * M
    if ad_image:
        try:
            im = Image.open(io.BytesIO(ad_image)).convert("RGB")
            sc = max(s(aw) / im.width, s(ah) / im.height)
            im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))), Image.LANCZOS)
            lft, top = (im.width - s(aw)) // 2, (im.height - s(ah)) // 2
            im = im.crop((lft, top, lft + s(aw), top + s(ah)))
            mask = Image.new("L", (s(aw), s(ah)), 0)
            ImageDraw.Draw(mask).rounded_rectangle([0, 0, s(aw) - 1, s(ah) - 1], radius=s(22), fill=255)
            lay.paste(im, (s(ax), s(ay)), mask)
            dr = ImageDraw.Draw(lay)
            ad_text = ad_text if ad_text else ""
        except Exception:
            ad_image = None
    if not ad_image:
        rr(dr, ax, ay, aw, ah, 22, fill=T_["card"])
        # uzuq chiziqli chegara
        per = 0
        for (x0, y0, x1, y1) in ((ax + 22, ay, ax + aw - 22, ay), (ax + 22, ay + ah, ax + aw - 22, ay + ah)):
            xx = x0
            while xx < x1:
                dr.line([(s(xx), s(y0)), (s(min(xx + 12, x1)), s(y1))], fill=PLINE, width=s(2))
                xx += 22
        for x0 in (ax, ax + aw):
            yy = ay + 22
            while yy < ay + ah - 22:
                dr.line([(s(x0), s(yy)), (s(x0), s(min(yy + 12, ay + ah - 22)))], fill=PLINE, width=s(2))
                yy += 22
        txt = ad_text or "Reklamangiz uchun joy"
        af = font("display", 32)
        while dr.textlength(txt, font=af) / K > aw - 60 and af.size > 30:
            af = font("display", int(af.size / K) - 2)
        dr.text((s(W / 2), s(ay + ah / 2 + (2 if ad_contact else 12))), txt, font=af, fill=TXT, anchor="ms")
        if ad_contact:
            spaced(dr, W / 2, ay + ah / 2 + 40, f"BOG'LANISH: {ad_contact}".upper(), font("body800", 18), ACC, 1.5, "c")
    elif ad_text or ad_contact:
        # rasm ustiga pastki yozuv
        band = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
        ImageDraw.Draw(band).rounded_rectangle([s(ax), s(ay + ah - 52), s(ax + aw), s(ay + ah)], radius=s(22), fill=(0, 0, 0, 150))
        lay = Image.alpha_composite(lay, band)
        dr = ImageDraw.Draw(lay)
        line = "  ·  ".join(x for x in (ad_text, ad_contact) if x)
        dr.text((s(W / 2), s(ay + ah - 18)), line, font=font("body800", 20), fill=(255, 255, 255), anchor="ms")

    if footer:
        spaced(dr, W / 2, H - 12, footer.upper(), font("body800", 14), MUT, 2, "c")

    img = Image.alpha_composite(img, lay).convert("RGB").resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=93, optimize=True)
    return buf.getvalue()
