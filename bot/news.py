"""Yangilik rasmi (Namanganliklar.uz shabloni): rasm + sarlavha -> tayyor post rasmi.

Uslublar:
  "full"  — rasm butun maydonda, pastda quyuq gradient ustida katta sarlavha
  "panel" — rasm tepada, pastda to'q ko'k panelda sarlavha (klassik ko'rinish)
Sarlavhada *yulduzcha* ichidagi so'zlar urg'u rangida chiqadi.
"""
import io
import re
from datetime import date

from PIL import Image, ImageDraw, ImageFilter, ImageOps

from .poster import ASSETS, brand_mark
import os
from PIL import ImageFont

# Inter Display — lotin va o'zbek kirill (Қ Ғ Ҳ Ў ʻ) harflarini to'liq qo'llaydi
_NEWS_FONTS = {
    "head": "InterDisplay-ExtraBold.otf",
    "bold": "InterDisplay-Bold.otf",
    "semi": "Inter-SemiBold.otf",
}
_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
_fc: dict = {}


def font(role: str, size: int):
    key = (role, size)
    if key not in _fc:
        path = os.path.join(ASSETS, "fonts", _NEWS_FONTS.get(role, _NEWS_FONTS["bold"]))
        try:
            _fc[key] = ImageFont.truetype(path, size * K)
        except OSError:
            _fc[key] = ImageFont.truetype(_FALLBACK, size * K)
    return _fc[key]

# Namanganliklar.uz brend ranglari (logotipdan olingan)
NAVY = (33, 59, 92)        # logodagi to'q ko'k
DEEP = (14, 28, 48)        # fon uchun yanada to'qroq ko'k
RED = (255, 78, 68)        # logodagi qizil
HL = (255, 108, 98)        # urg'u so'zlar (to'q fonda o'qiladigan qizil)
WHITE = (255, 255, 255)
K = 2  # supersampling

SIZES = {"kvadrat": (1080, 1080), "vertikal": (1080, 1350)}


def _s(v):
    return int(round(v * K))


def _tokens(text: str):
    """'Bu *muhim* xabar' -> [('BU', False), ('MUHIM', True), ('XABAR', False)]"""
    out = []
    for i, part in enumerate(re.split(r"\*", text)):
        for w in part.split():
            out.append((w, i % 2 == 1))
    return out


def _layout(dr, tokens, fnt, maxw):
    """So'zlarni qatorlarga bo'ladi: [[(so'z, urg'u), ...], ...]"""
    lines, cur, cw = [], [], 0
    sp = dr.textlength(" ", font=fnt)
    for w, hl in tokens:
        ww = dr.textlength(w, font=fnt)
        if cur and cw + sp + ww > maxw:
            lines.append(cur)
            cur, cw = [], 0
        cw = cw + (sp if cur else 0) + ww
        cur.append((w, hl))
    if cur:
        lines.append(cur)
    return lines


def _fit(dr, text, role, maxw, max_lines, start, minimum):
    tokens = [(w.upper(), hl) for w, hl in _tokens(text)]
    size = start
    while size >= minimum:
        f = font(role, size)
        lines = _layout(dr, tokens, f, _s(maxw))
        if len(lines) <= max_lines and all(dr.textlength(w, font=f) <= _s(maxw) for w, _ in tokens):
            return f, size, lines
        size -= 2
    f = font(role, minimum)
    return f, minimum, _layout(dr, tokens, f, _s(maxw))


def _draw_lines(canvas, lines, f, x, y, line_h, color, accent, align="left", width=0, shadow=True):
    """Matnni yumshoq soya bilan chizadi. Yangi canvas qaytaradi."""
    def paint(dr, fill_main, fill_acc, dx=0, dy=0):
        sp = dr.textlength(" ", font=f)
        for i, line in enumerate(lines):
            lw = sum(dr.textlength(w, font=f) for w, _ in line) + sp * (len(line) - 1)
            cx = _s(x) if align == "left" else _s(x) + (_s(width) - lw) / 2
            by = _s(y + i * line_h)
            for w, hl in line:
                dr.text((cx + dx, by + dy), w, font=f, fill=fill_acc if hl else fill_main, anchor="ls")
                cx += dr.textlength(w, font=f) + sp

    if shadow:
        sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        paint(ImageDraw.Draw(sh), (0, 0, 0, 200), (0, 0, 0, 200), 0, _s(3))
        sh = sh.filter(ImageFilter.GaussianBlur(_s(7)))
        canvas = Image.alpha_composite(canvas, sh)
    paint(ImageDraw.Draw(canvas), color, accent)
    return canvas


def _cover(photo: Image.Image, w: int, h: int, top_bias: float = 0.35) -> Image.Image:
    """Rasmni maydonga to'liq to'ldirib qirqadi (yuzlar odatda tepada — biroz yuqoriroq qirqiladi)."""
    photo = ImageOps.exif_transpose(photo).convert("RGB")
    sc = max(w / photo.width, h / photo.height)
    im = photo.resize((max(1, round(photo.width * sc)), max(1, round(photo.height * sc))), Image.LANCZOS)
    left = (im.width - w) // 2
    top = int((im.height - h) * top_bias)
    return im.crop((left, top, left + w, top + h))


def _sharpen(im: Image.Image, scale: float) -> Image.Image:
    """Kattalashtirilgan rasmni biroz tiniqlashtiradi."""
    if scale <= 1.15:
        return im
    return im.filter(ImageFilter.UnsharpMask(radius=max(1, round(scale * 1.2)), percent=70, threshold=2))


def _photo_layer(photo: Image.Image, w: int, h: int) -> Image.Image:
    """Butun maydon uchun fon rasmi.
    Gorizontal rasm (masalan saytdagi 800x450) kvadratga qirqilib, 2.4 barobar cho'zilsa xira chiqadi.
    Shuning uchun u kenglik bo'yicha joylanadi (kam kattalashadi, tiniq qoladi),
    pastki qismi toza to'q ko'k fonga silliq o'tadi."""
    photo = ImageOps.exif_transpose(photo).convert("RGB")
    ratio = photo.width / photo.height
    fh = round(w / ratio)
    if ratio > 1.2 and fh < h * 0.8:
        # ostki qism — xiralashtirilgan rasm emas, toza to'q ko'k fon
        bg = Image.new("RGB", (w, h), DEEP)
        sharp = _sharpen(photo.resize((w, fh), Image.LANCZOS), w / photo.width)
        feather = max(1, int(fh * 0.28))
        mask = Image.new("L", (w, fh), 255)
        md = ImageDraw.Draw(mask)
        for i in range(feather):
            md.line([(0, fh - 1 - i), (w, fh - 1 - i)], fill=int(255 * i / feather))
        bg.paste(sharp, (0, 0), mask)
        return bg, fh
    scale = max(w / photo.width, h / photo.height)
    return _sharpen(_cover(photo, w, h, 0.3), scale), None


def _brand_pill(base: Image.Image, x, y, h):
    """Oq kapsula: rangli NG belgisi + NAMANGANLIKLAR.UZ. Kengligini qaytaradi."""
    dr = ImageDraw.Draw(base)
    f = font("head", int(h * 0.40))
    t1, t2 = "NAMANGANLIKLAR", ".UZ"
    logo = brand_mark(dark=False)
    lh = h * 0.60
    lw = logo.width * lh / logo.height if logo else 0
    tw = (dr.textlength(t1, font=f) + dr.textlength(t2, font=f)) / K
    pad = h * 0.40
    gap = h * 0.24 if logo else 0
    w = pad + lw + gap + tw + pad
    dr.rounded_rectangle([_s(x), _s(y), _s(x + w), _s(y + h)], radius=_s(h / 2), fill=WHITE)
    cx = x + pad
    if logo:
        li = logo.resize((_s(lw), _s(lh)), Image.LANCZOS)
        base.alpha_composite(li, (_s(cx), _s(y + (h - lh) / 2)))
        cx += lw + gap
    dr = ImageDraw.Draw(base)
    dr.text((_s(cx), _s(y + h / 2)), t1, font=f, fill=NAVY, anchor="lm")
    cx += dr.textlength(t1, font=f) / K
    dr.text((_s(cx), _s(y + h / 2)), t2, font=f, fill=RED, anchor="lm")
    return w


SLOGAN = os.getenv("NEWS_SLOGAN", "Тезкор, Холис, Ишончли ахборот манбаи")


def _slogan(dr, x, y, size, align="r", name=True):
    """«Namanganliklar.uz · Тезкор, Холис, Ишончли ахборот манбаи» (sayt nomi lotinda, shior kirillda)."""
    fb, fs = font("bold", size), font("semi", size)
    parts = ([("Namanganliklar", fb, WHITE), (".uz", fb, HL), ("  ·  ", fs, (255, 255, 255, 140))] if name else []) \
        + [(SLOGAN, fs, (255, 255, 255, 215))]
    total = sum(dr.textlength(t, font=f) for t, f, _ in parts)
    cx = _s(x) - (total if align == "r" else total / 2 if align == "c" else 0)
    for t, f, col in parts:
        dr.text((cx, _s(y)), t, font=f, fill=col, anchor="ls")
        cx += dr.textlength(t, font=f)


def render_news(photo_bytes: bytes, text: str, style: str = "full", fmt: str = "kvadrat",
                d: date | None = None, tag: str = "") -> bytes:
    W, H = SIZES.get(fmt, SIZES["kvadrat"])
    photo = Image.open(io.BytesIO(photo_bytes))
    text = " ".join(text.split())
    d = d or date.today()

    style = "full"  # faqat «To'liq rasm» uslubi qoldirildi
    if style == "panel":
        ph = int(H * (0.56 if fmt == "kvadrat" else 0.6))
        canvas = Image.new("RGBA", (_s(W), _s(H)), DEEP + (255,))
        canvas.paste(_cover(photo, _s(W), _s(ph + 40)), (0, 0))
        # panel: yuqoridan pastga to'q ko'k gradient, tepa chetida qizil chiziq
        py = ph - 10
        pg = Image.new("RGB", (1, 256))
        for i in range(256):
            t = i / 255
            pg.putpixel((0, i), tuple(int(NAVY[j] + (DEEP[j] - NAVY[j]) * t) for j in range(3)))
        panel = pg.resize((_s(W), _s(H - py))).convert("RGBA")
        mask = Image.new("L", panel.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, panel.width - 1, panel.height + _s(60)], radius=_s(44), fill=255)
        canvas.paste(panel, (0, _s(py)), mask)
        dr = ImageDraw.Draw(canvas)
        dr.rounded_rectangle([_s(W / 2 - 60), _s(py + 18), _s(W / 2 + 60), _s(py + 26)], radius=_s(4), fill=RED)
        # teg (ixtiyoriy)
        top_text = py + 70
        if tag:
            tf = font("bold", 22)
            tw = dr.textlength(tag.upper(), font=tf) / K + 44
            dr.rounded_rectangle([_s(W / 2 - tw / 2), _s(py - 24), _s(W / 2 + tw / 2), _s(py + 24)],
                                 radius=_s(24), fill=RED)
            dr.text((_s(W / 2), _s(py)), tag.upper(), font=tf, fill=WHITE, anchor="mm")
        bar_h = 80
        bar_y = H - 70 - bar_h
        avail = bar_y - 30 - top_text
        f, size, lines = _fit(dr, text, "head", W - 160, 4, 44, 24)
        lh = size * 1.24
        block = lh * len(lines)
        ty = top_text + max(0, (avail - block) / 2) + size * 0.9
        canvas = _draw_lines(canvas, lines, f, 80, ty, lh, WHITE, HL, "center", W - 160)
        dr = ImageDraw.Draw(canvas)
        # pastki brend kapsulasi (markazda)
        tmp = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        pw = _brand_pill(tmp, 0, 0, bar_h)
        canvas.alpha_composite(tmp.crop((0, 0, _s(pw), _s(bar_h))), (_s((W - pw) / 2), _s(bar_y)))
        dr = ImageDraw.Draw(canvas)
        _slogan(dr, W / 2, H - 28, 17, "c", name=False)
    else:  # "full"
        layer, photo_h = _photo_layer(photo, _s(W), _s(H))
        canvas = layer.convert("RGBA")
        # pastki gradient
        grad = Image.new("L", (1, 256))
        start = 0.38
        for i in range(256):
            t = i / 255
            a = 0 if t < start else min(1, (t - start) / (1 - start) * 1.25)
            grad.putpixel((0, i), int(255 * (a ** 1.3) * 0.96))
        shade = Image.new("RGBA", canvas.size, DEEP + (255,))
        shade.putalpha(grad.resize(canvas.size))
        canvas = Image.alpha_composite(canvas, shade)
        # yuqori chap: brend kapsulasi
        top = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        _brand_pill(top, 48, 44, 68)
        sh = top.split()[3].filter(ImageFilter.GaussianBlur(_s(10))).point(lambda v: int(v * 0.35))
        shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 255))
        shadow.putalpha(sh)
        canvas = Image.alpha_composite(canvas, shadow)
        canvas = Image.alpha_composite(canvas, top)
        dr = ImageDraw.Draw(canvas)

        f, size, lines = _fit(dr, text, "head", W - 150, 5 if fmt == "vertikal" else 4, 54, 26)
        lh = size * 1.2
        foot = H - 56
        ty_last = foot - 58
        ty0 = ty_last - lh * (len(lines) - 1)
        if photo_h:
            # gorizontal rasm: sarlavhani rasm osti va pastki chiziq oralig'ining o'rtasiga qo'yamiz
            area_top = photo_h / K * 0.86
            area_bot = foot - 22
            tag_h = 60 if tag else 0
            block = size * 0.86 + lh * (len(lines) - 1) + size * 0.12 + tag_h
            ty0 = area_top + (area_bot - area_top - block) / 2 + tag_h + size * 0.86
            ty0 = min(ty0, foot - 58 - lh * (len(lines) - 1))  # pastki chiziqqa tegib ketmasin
            ty_last = ty0 + lh * (len(lines) - 1)
        # urg'u chizig'i
        dr.rounded_rectangle([_s(48), _s(ty0 - size * 0.86), _s(56), _s(ty_last + size * 0.12)],
                             radius=_s(4), fill=RED)
        canvas = _draw_lines(canvas, lines, f, 76, ty0, lh, WHITE, HL)
        dr = ImageDraw.Draw(canvas)
        # teg sarlavha ustida
        if tag:
            tf = font("semi", 21)
            tw = dr.textlength(tag.upper(), font=tf) / K + 36
            yy = ty0 - size - 40
            dr.rounded_rectangle([_s(76), _s(yy - 20), _s(76 + tw), _s(yy + 20)], radius=_s(8), fill=RED)
            dr.text((_s(76 + tw / 2), _s(yy)), tag.upper(), font=tf, fill=WHITE, anchor="mm")
        # pastki qator
        dr.line([(_s(48), _s(foot - 22)), (_s(W - 48), _s(foot - 22))], fill=(255, 255, 255, 70), width=_s(2))
        dr.text((_s(48), _s(foot + 8)), d.strftime("%d.%m.%Y"), font=font("semi", 21),
                fill=(255, 255, 255, 200), anchor="ls")
        # o'ng tomonda: «Namanganliklar.uz · Тезкор, Холис, Ишончли ахборот манбаи»
        _slogan(dr, W - 48, foot + 8, 17, "r")

    out = canvas.convert("RGB").resize((W, H), Image.LANCZOS)
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=94, optimize=True)
    return buf.getvalue()
