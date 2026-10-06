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


def weather_icon(d, code, cx, cy, c, cloud_col=(240, 236, 226), z=1.0):
    """Ob-havo belgisi. z — kattalashtirish koeffitsiyenti."""
    code = int(code or 0)
    lw = s(3 * z)
    P = lambda x, y: (s(cx + x * z), s(cy + y * z))
    sun = code in (0, 1, 2)
    cloud = code >= 2
    if sun:
        ox, oy = (-8, -8) if cloud else (0, 0)
        d.ellipse([*P(ox - 11, oy - 11), *P(ox + 11, oy + 11)], fill=c)
        for i in range(8):
            a = math.pi / 4 * i
            d.line([P(ox + 16 * math.cos(a), oy + 16 * math.sin(a)),
                    P(ox + 22 * math.cos(a), oy + 22 * math.sin(a))], fill=c, width=lw)
    if cloud:
        for (x, y, r) in ((-10, 6, 11), (4, -1, 15), (17, 7, 10)):
            d.ellipse([*P(x - r, y - r), *P(x + r, y + r)], fill=cloud_col)
        d.rounded_rectangle([*P(-21, 4), *P(27, 17)], radius=s(6 * z), fill=cloud_col)
    snow = code in (71, 73, 75, 77, 85, 86)
    if code >= 51 and not snow:
        for x in (-10, 2, 14):
            d.line([P(x, 22), P(x - 4, 32)], fill=(130, 190, 255), width=lw)
    if snow:
        for x in (-10, 2, 14):
            d.ellipse([*P(x - 3, 24), *P(x + 3, 30)], fill=(255, 255, 255))
    if code >= 95:
        d.line([P(2, 18), P(-4, 28), P(4, 28), P(-2, 38)], fill=GOLD, width=lw)


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


class Frame:
    """Umumiy ramka: fon, naqsh, logotip, shahar, sarlavha va sana, reklama joyi."""

    def __init__(self, *, theme, city, brand, logo, logo_tint, label, d: date):
        T_ = THEMES.get(theme, THEMES[DEFAULT_THEME])
        self.T = T_
        self.ACC, self.TXT, self.TIME = T_["acc"], T_["txt"], T_["time"]
        self.MUT = _rgba(self.TXT, 165)
        self.PLINE = _rgba(self.ACC, 100)
        self.CARD = T_["card"]
        self.CHIP_TXT = T_["b2"] if T_["dark"] else (255, 248, 232)
        self.GOOD = (120, 220, 150) if T_["dark"] else (30, 140, 70)
        self.BAD = (255, 140, 120) if T_["dark"] else (190, 50, 40)
        self.CLOUD = (240, 236, 226) if T_["dark"] else (175, 165, 145)
        self.card_rgb = (tuple(int(T_["b1"][j] * 0.7 + T_["b2"][j] * 0.3) for j in range(3))
                         if T_["dark"] else (252, 248, 238))
        self.M = M = 56
        SW, SH = s(W), s(H)

        grad = Image.new("RGB", (1, 256))
        for i in range(256):
            t = i / 255
            grad.putpixel((0, i), tuple(int(T_["b1"][j] + (T_["b2"][j] - T_["b1"][j]) * t) for j in range(3)))
        img = grad.resize((SW, SH), Image.BILINEAR).convert("RGBA")
        glow = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
        ImageDraw.Draw(glow).ellipse([s(W * 0.8 - 420), s(-300), s(W * 0.8 + 420), s(540)], fill=_rgba(self.ACC, 50))
        img = Image.alpha_composite(img, glow.filter(ImageFilter.GaussianBlur(s(160))))
        pat = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
        pd = ImageDraw.Draw(pat)
        for yy in range(-48, H + 96, 96):
            for xx in range(-48, W + 96, 96):
                pts = star_pts(xx, yy, 96 * 0.42)
                pd.line(pts + [pts[0]], fill=_rgba(self.ACC, 20), width=s(1.5))
        self.img = Image.alpha_composite(img, pat)
        self.lay = Image.new("RGBA", (SW, SH), (0, 0, 0, 0))
        self.dr = dr = ImageDraw.Draw(self.lay)

        # sarlavha: logotip + brend | shahar
        brand = (brand or "").strip()
        logo_im = _load_logo(logo, logo_tint, self.TXT) if logo else None
        if logo_im is not None:
            lh = 80
            lw = min(220, logo_im.width * lh / logo_im.height)
            lh = lw * logo_im.height / logo_im.width
            li = logo_im.resize((s(lw), s(lh)), Image.LANCZOS)
            self.lay.paste(li, (s(M), s(86 - lh / 2)), li)
            self.dr = dr = ImageDraw.Draw(self.lay)
            brand_x = M + lw + 20
        else:
            rr(dr, M, 48, 76, 76, 18, fill=self.ACC)
            words = [w for w in brand.replace(".", " ").split() if w[:1].isalnum()]
            ini = "".join(w[0] for w in words[:2]).upper() or "NV"
            dr.text((s(M + 38), s(87)), ini, font=font("display", 30), fill=self.CHIP_TXT, anchor="mm")
            brand_x = M + 96
        cty = city.upper()
        cf = font("body800", 22)
        cw = text_w(dr, cty, cf, 2.5) / K + 66
        rr(dr, W - M - cw, 58, cw, 56, 28, fill=self.CARD, outline=self.PLINE, width=2)
        px, py = W - M - cw + 28, 84
        dr.pieslice([s(px - 8), s(py - 11), s(px + 8), s(py + 5)], 180, 360, fill=self.ACC)
        dr.polygon([(s(px - 8), s(py - 3)), (s(px + 8), s(py - 3)), (s(px), s(py + 11))], fill=self.ACC)
        spaced(dr, W - M - cw + 46, 95, cty, cf, self.TXT, 2.5)
        if brand:
            bf = font("display", 22)
            b = brand.upper()
            maxw = W - M - cw - 24 - brand_x
            while text_w(dr, b, bf, 1) / K > maxw and len(b) > 4:
                b = b[:-2].rstrip("…") + "…"
            spaced(dr, brand_x, 95, b, bf, self.TXT, 1)

        # bo'lim nomi va sana
        spaced(dr, M, 186, label, font("body800", 24), self.ACC, 6)
        big = f"{d.day}-{MONTHS[d.month - 1]}"
        bf = font("display", 66)
        dr.text((s(M - 2), s(262)), big, font=bf, fill=self.TXT, anchor="ls")
        bw = dr.textlength(big, font=bf) / K
        dr.text((s(M + bw + 18), s(262)), str(d.year), font=font("display", 30), fill=self.ACC, anchor="ls")
        dr.text((s(W - M), s(262)), DAYS[d.weekday()], font=font("body800", 26), fill=self.TXT, anchor="rs")
        self.top = 292

    def card(self, x, y, w, h, r=26):
        rr(self.dr, x, y, w, h, r, fill=self.CARD, outline=self.PLINE, width=2)

    def ad(self, ay, ad_text="", ad_contact="", ad_image=None, footer=""):
        dr, M = self.dr, self.M
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
                self.lay.paste(im, (s(ax), s(ay)), mask)
                self.dr = dr = ImageDraw.Draw(self.lay)
            except Exception:
                ad_image = None
        if not ad_image:
            rr(dr, ax, ay, aw, ah, 22, fill=self.CARD)
            for (x0, y0, x1) in ((ax + 22, ay, ax + aw - 22), (ax + 22, ay + ah, ax + aw - 22)):
                xx = x0
                while xx < x1:
                    dr.line([(s(xx), s(y0)), (s(min(xx + 12, x1)), s(y0))], fill=self.PLINE, width=s(2))
                    xx += 22
            for x0 in (ax, ax + aw):
                yy = ay + 22
                while yy < ay + ah - 22:
                    dr.line([(s(x0), s(yy)), (s(x0), s(min(yy + 12, ay + ah - 22)))], fill=self.PLINE, width=s(2))
                    yy += 22
            txt = ad_text or "Reklamangiz uchun joy"
            size = 32
            while dr.textlength(txt, font=font("display", size)) / K > aw - 60 and size > 16:
                size -= 2
            dr.text((s(W / 2), s(ay + ah / 2 + (2 if ad_contact else 12))), txt, font=font("display", size),
                    fill=self.TXT, anchor="ms")
            if ad_contact:
                spaced(dr, W / 2, ay + ah / 2 + 40, f"BOG'LANISH: {ad_contact}".upper(), font("body800", 18),
                       self.ACC, 1.5, "c")
        elif ad_text or ad_contact:
            band = Image.new("RGBA", self.lay.size, (0, 0, 0, 0))
            ImageDraw.Draw(band).rounded_rectangle([s(ax), s(ay + ah - 52), s(ax + aw), s(ay + ah)],
                                                   radius=s(22), fill=(0, 0, 0, 150))
            self.lay = Image.alpha_composite(self.lay, band)
            self.dr = dr = ImageDraw.Draw(self.lay)
            line = "  ·  ".join(x for x in (ad_text, ad_contact) if x)
            dr.text((s(W / 2), s(ay + ah - 18)), line, font=font("body800", 20), fill=(255, 255, 255), anchor="ms")
        if footer:
            spaced(dr, W / 2, H - 12, footer.upper(), font("body800", 14), self.MUT, 2, "c")

    def finish(self) -> bytes:
        img = Image.alpha_composite(self.img, self.lay).convert("RGB").resize((W, H), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=93, optimize=True)
        return buf.getvalue()


def _money(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ")


# ======================= 1. NAMOZ VAQTLARI =======================
def render(*, d: date, city: str, times: dict, hijri: str = "", brand: str = "",
           footer: str = "", weather: dict | None = None, usd: dict | None = None,
           weather_text: str = "", theme: str = DEFAULT_THEME, ad_text: str = "",
           ad_contact: str = "", ad_image: bytes | None = None,
           logo: bytes | None = None, logo_tint: bool = False) -> bytes:
    F = Frame(theme=theme, city=city, brand=brand, logo=logo, logo_tint=logo_tint, label="NAMOZ VAQTLARI", d=d)
    dr, M, TXT, TIME, ACC = F.dr, F.M, F.TXT, F.TIME, F.ACC

    gy, gap, th = F.top, 14, 214
    tw = (W - 2 * M - gap) / 2
    nf = font("display", 24)
    num_size = 168
    while num_size > 80 and max(dr.textlength(times[k], font=font("num", num_size)) for k in PRAYER_KEYS) / K > tw - 48:
        num_size -= 4
    nfont = font("num", num_size)
    for i, k in enumerate(PRAYER_KEYS):
        x = M + (i % 2) * (tw + gap)
        y = gy + (i // 2) * (th + gap)
        F.card(x, y, tw, th)
        icon(dr, k, x + 44, y + 44, ACC, F.card_rgb)
        spaced(dr, x + 78, y + 53, NAMES[i], nf, TXT, 2)
        tk = TAKBIR[i] if i < len(TAKBIR) else 0
        if tk:
            lbl = f"TAKBIR +{tk}"
            lf = font("body800", 18)
            cw_ = text_w(dr, lbl, lf, 1) / K + 28
            rr(dr, x + tw - 22 - cw_, y + 22, cw_, 38, 19, fill=ACC)
            spaced(dr, x + tw - 22 - cw_ + 14, y + 48, lbl, lf, F.CHIP_TXT, 1)
        dr.text((s(x + tw / 2), s(y + th - 26)), times[k], font=nfont, fill=TIME, anchor="ms")

    # ob-havo va dollar qatori
    iy, ih = gy + 3 * th + 2 * gap + 14, 96
    F.card(M, iy, W - 2 * M, ih, 22)
    mid = W / 2
    dr.line([(s(mid), s(iy + 18)), (s(mid), s(iy + ih - 18))], fill=F.PLINE, width=s(2))
    if weather:
        weather_icon(dr, weather["code"], M + 50, iy + 40, ACC, F.CLOUD)
        t1 = f"{weather['tmax']:+d}°"
        f1 = font("num", 52)
        dr.text((s(M + 100), s(iy + 66)), t1, font=f1, fill=TIME, anchor="ls")
        w1 = dr.textlength(t1, font=f1) / K
        dr.text((s(M + 108 + w1), s(iy + 66)), f"/ {weather['tmin']:+d}°", font=font("num", 30), fill=F.MUT, anchor="ls")
        if weather_text:
            wt, wf = weather_text, font("body600", 16)
            while dr.textlength(wt, font=wf) / K > mid - M - 120 and len(wt) > 6:
                wt = wt[:-2].rstrip() + "…"
            dr.text((s(M + 100), s(iy + 86)), wt, font=wf, fill=F.MUT, anchor="ls")
    else:
        dr.text((s(M + 28), s(iy + 62)), "Ob-havo: —", font=font("body800", 22), fill=F.MUT, anchor="ls")
    ux = mid + 28
    spaced(dr, ux, iy + 30, "1 USD · MARKAZIY BANK", font("body800", 14), ACC, 1.5)
    if usd:
        val = _money(usd["rate"])
        f2 = font("num", 48)
        dr.text((s(ux), s(iy + 76)), val, font=f2, fill=TIME, anchor="ls")
        vw = dr.textlength(val, font=f2) / K
        diff = usd.get("diff", 0)
        col = F.GOOD if diff > 0 else F.BAD if diff < 0 else F.MUT
        arrow = "▲" if diff > 0 else "▼" if diff < 0 else "•"
        dr.text((s(ux + vw + 10), s(iy + 76)), f"so'm  {arrow}{abs(diff):.2f}", font=font("body800", 17), fill=col, anchor="ls")
    else:
        dr.text((s(ux), s(iy + 70)), "—", font=font("num", 40), fill=F.MUT, anchor="ls")

    F.ad(iy + ih + 14, ad_text, ad_contact, ad_image, footer)
    return F.finish()


# ======================= 2. OB-HAVO =======================
WDAYS_SHORT = ["DU", "SE", "CHOR", "PAY", "JUMA", "SHAN", "YAK"]


def render_weather(*, d: date, city: str, day: dict, desc: str, next_days: list[tuple[date, dict]] = (),
                   brand: str = "", theme: str = DEFAULT_THEME, ad_text: str = "", ad_contact: str = "",
                   ad_image: bytes | None = None, logo: bytes | None = None, logo_tint: bool = False) -> bytes:
    """day: {'code','tmax','tmin','rain','wind','feels'}; next_days: [(sana, day), ...]"""
    F = Frame(theme=theme, city=city, brand=brand, logo=logo, logo_tint=logo_tint, label="OB-HAVO MA'LUMOTI", d=d)
    dr, M, TXT, TIME, ACC = F.dr, F.M, F.TXT, F.TIME, F.ACC
    full = W - 2 * M

    # asosiy karta: katta belgi + harorat
    y0, hh = F.top, 420
    F.card(M, y0, full, hh)
    weather_icon(dr, day["code"], M + 190, y0 + 175, ACC, F.CLOUD, z=4.4)
    t1 = f"{day['tmax']:+d}°"
    f1 = font("num", 230)
    tx = M + 380
    dr.text((s(tx), s(y0 + 270)), t1, font=f1, fill=TIME, anchor="ls")
    spaced(dr, tx + 6, y0 + 70, "KUNDUZI", font("body800", 20), ACC, 3)
    ds = 30
    while dr.textlength(desc, font=font("display", ds)) / K > 340 and ds > 16:
        ds -= 2
    dr.text((s(M + 190), s(y0 + 372)), desc, font=font("display", ds), fill=TXT, anchor="ms")
    spaced(dr, tx + 6, y0 + 330, "KECHASI", font("body800", 20), ACC, 3)
    dr.text((s(tx + 6), s(y0 + 390)), f"{day['tmin']:+d}°", font=font("num", 64), fill=TXT, anchor="ls")

    # 3 ta tafsilot
    y1, h1, gap = y0 + hh + 14, 170, 14
    cw = (full - 2 * gap) / 3
    items = [
        ("YOG'IN EHTIMOLI", f"{day['rain']}%" if day.get("rain") is not None else "—"),
        ("SHAMOL", f"{round(day['wind'])} m/s" if day.get("wind") is not None else "—"),
        ("HIS QILINADI", f"{day['feels']:+d}°" if day.get("feels") is not None else "—"),
    ]
    for i, (lbl, val) in enumerate(items):
        x = M + i * (cw + gap)
        F.card(x, y1, cw, h1, 22)
        spaced(dr, x + cw / 2, y1 + 48, lbl, font("body800", 17), ACC, 2, "c")
        vs = 88
        while dr.textlength(val, font=font("num", vs)) / K > cw - 30 and vs > 40:
            vs -= 4
        dr.text((s(x + cw / 2), s(y1 + 136)), val, font=font("num", vs), fill=TIME, anchor="ms")

    # keyingi kunlar
    y2, h2 = y1 + h1 + 14, 170
    nd = list(next_days)[:3]
    if nd:
        cw2 = (full - (len(nd) - 1) * gap) / len(nd)
        for i, (dd, dy) in enumerate(nd):
            x = M + i * (cw2 + gap)
            F.card(x, y2, cw2, h2, 22)
            spaced(dr, x + 26, y2 + 42, f"{dd.day}-{MONTHS[dd.month - 1][:3]} · {DAYS[dd.weekday()][:4]}",
                   font("body800", 17), ACC, 1.5)
            weather_icon(dr, dy["code"], x + 58, y2 + 100, ACC, F.CLOUD, z=1.3)
            a_, b_ = f"{dy['tmax']:+d}°", f"{dy['tmin']:+d}°"
            fs = 64
            while dr.textlength(a_, font=font("num", fs)) / K > cw2 - 130 and fs > 30:
                fs -= 4
            dr.text((s(x + 112), s(y2 + 112)), a_, font=font("num", fs), fill=TIME, anchor="ls")
            dr.text((s(x + 114), s(y2 + 148)), f"kechasi {b_}", font=font("body800", 18), fill=F.MUT, anchor="ls")
        y_ad = y2 + h2 + 14
    else:
        y_ad = y1 + h1 + 14

    F.ad(y_ad, ad_text, ad_contact, ad_image)
    return F.finish()


# ======================= 3. VALYUTA KURSI =======================
CCY_NAMES = {"USD": "AQSH DOLLARI", "EUR": "YEVRO", "RUB": "ROSSIYA RUBLI"}


def render_rates(*, d: date, city: str, rates: dict, brand: str = "", theme: str = DEFAULT_THEME,
                 ad_text: str = "", ad_contact: str = "", ad_image: bytes | None = None,
                 logo: bytes | None = None, logo_tint: bool = False) -> bytes:
    """rates: {'USD': {'rate','diff','date'}, 'EUR': ..., 'RUB': ...}"""
    F = Frame(theme=theme, city=city, brand=brand, logo=logo, logo_tint=logo_tint, label="VALYUTA KURSI", d=d)
    dr, M, TXT, TIME, ACC = F.dr, F.M, F.TXT, F.TIME, F.ACC
    full = W - 2 * M

    def diff_chip(x, y, diff, size=22, anchor="l"):
        col = F.GOOD if diff > 0 else F.BAD if diff < 0 else F.MUT
        arrow = "▲" if diff > 0 else "▼" if diff < 0 else "•"
        dr.text((s(x), s(y)), f"{arrow} {abs(diff):.2f}", font=font("body800", size), fill=col,
                anchor="ls" if anchor == "l" else "rs")

    # asosiy: dollar
    y0, hh = F.top, 460
    F.card(M, y0, full, hh)
    usd = rates.get("USD")
    spaced(dr, M + 40, y0 + 64, "1 " + CCY_NAMES["USD"], font("display", 26), TXT, 2)
    rr(dr, W - M - 40 - 92, y0 + 34, 92, 44, 22, fill=ACC)
    dr.text((s(W - M - 40 - 46), s(y0 + 57)), "USD", font=font("body800", 22), fill=F.CHIP_TXT, anchor="mm")
    if usd:
        val = _money(usd["rate"])
        size = 230
        while dr.textlength(val, font=font("num", size)) / K > full - 80 and size > 100:
            size -= 6
        dr.text((s(W / 2), s(y0 + 310)), val, font=font("num", size), fill=TIME, anchor="ms")
        dr.text((s(M + 40), s(y0 + 410)), "so'm", font=font("display", 30), fill=F.MUT, anchor="ls")
        diff_chip(W - M - 40, y0 + 410, usd.get("diff", 0), 28, "r")
    else:
        dr.text((s(W / 2), s(y0 + 260)), "—", font=font("num", 160), fill=F.MUT, anchor="ms")

    # yevro va rubl
    y1, h1, gap = y0 + hh + 14, 210, 14
    cw = (full - gap) / 2
    for i, c in enumerate(("EUR", "RUB")):
        x = M + i * (cw + gap)
        F.card(x, y1, cw, h1, 22)
        spaced(dr, x + 30, y1 + 48, f"1 {CCY_NAMES[c]}", font("body800", 18), ACC, 2)
        r = rates.get(c)
        if r:
            v = _money(r["rate"])
            fs = 100
            while dr.textlength(v, font=font("num", fs)) / K > cw - 60 and fs > 40:
                fs -= 4
            dr.text((s(x + 30), s(y1 + 150)), v, font=font("num", fs), fill=TIME, anchor="ls")
            diff_chip(x + 32, y1 + 188, r.get("diff", 0), 18)
            dr.text((s(x + cw - 30), s(y1 + 188)), "so'm", font=font("body800", 18), fill=F.MUT, anchor="rs")
        else:
            dr.text((s(x + 30), s(y1 + 140)), "—", font=font("num", 80), fill=F.MUT, anchor="ls")

    # izoh
    y2 = y1 + h1 + 14
    date_s = (usd or next(iter(rates.values()), {})).get("date", "")
    note = f"O'zbekiston Respublikasi Markaziy banki kursi · {date_s} dan amal qiladi" if date_s else \
        "O'zbekiston Respublikasi Markaziy banki kursi"
    F.card(M, y2, full, 64, 20)
    dr.text((s(W / 2), s(y2 + 41)), note, font=font("body600", 19), fill=F.MUT, anchor="ms")

    F.ad(y2 + 64 + 14, ad_text, ad_contact, ad_image)
    return F.finish()
