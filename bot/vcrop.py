"""Facebook uchun videoni avtomatik qirqish.

Kanal videolarida ko'pincha yotiq video tik (9:16) ramkaga solingan bo'ladi: tepada/pastda qora yoki
oq bo'sh joy, shablon, yozuvlar. Facebook bunday videoni «soxta 9:16» deb kam ko'rsatadi.
Bu modul videoning HARAKATLANAYOTGAN qismini (haqiqiy videoni) topadi va faqat shu qismni qirqib oladi.
Qo'zg'almas joylar (bo'sh joy, shablon, logotip, qotib turgan yozuv) tashlab yuboriladi.
"""
import asyncio
import json
import logging
import os
import tempfile

log = logging.getLogger(__name__)

SAMPLES = 16          # tahlil uchun olinadigan kadrlar soni
SW = 96               # tahlil kadrining eni (kichraytirilgan)
MOTION = 6.0          # piksel "harakatlanmoqda" deyilishi uchun yorug'lik o'zgarishi (0–255)
ROW_SHARE = 0.18      # qator/ustun "faol" bo'lishi uchun undagi harakatli piksellar ulushi
MIN_CUT = 0.10        # kamida 10% maydon qirqilsa — qirqamiz, aks holda asl video
MIN_KEEP = 0.25       # qolgan qism asl maydonning kamida 25% bo'lishi kerak


async def _run(*args, stdin: bytes | None = None) -> bytes:
    p = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE if stdin else None,
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    out, err = await p.communicate(stdin)
    if p.returncode != 0:
        raise RuntimeError(err.decode(errors="ignore")[-300:])
    return out


async def probe(path: str) -> tuple[int, int, float]:
    out = await _run("ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                     "stream=width,height:stream_side_data=rotation:format=duration", "-of", "json", path)
    j = json.loads(out)
    st = j["streams"][0]
    w, h = int(st["width"]), int(st["height"])
    rot = 0
    for sd in st.get("side_data_list") or []:
        rot = int(sd.get("rotation") or 0)
    if abs(rot) in (90, 270):
        w, h = h, w
    return w, h, float(j.get("format", {}).get("duration") or 0)


async def _frames(path: str, w: int, h: int, dur: float) -> tuple[list[bytes], int, int]:
    sh = max(2, round(SW * h / w / 2) * 2)
    fps = SAMPLES / max(dur, 1.0)
    raw = await _run("ffmpeg", "-v", "error", "-i", path, "-vf",
                     f"fps={fps:.5f},scale={SW}:{sh},format=gray", "-frames:v", str(SAMPLES),
                     "-f", "rawvideo", "-")
    n = SW * sh
    return [raw[i:i + n] for i in range(0, len(raw) - n + 1, n)], SW, sh


def active_box(frames: list[bytes], fw: int, fh: int):
    """Harakatlanayotgan sohaning chegarasi (x0, y0, x1, y1) kichik kadr koordinatalarida yoki None."""
    if len(frames) < 4:
        return None
    k = len(frames)
    act = bytearray(fw * fh)
    for i in range(fw * fh):
        lo = hi = frames[0][i]
        for f in frames[1:]:
            v = f[i]
            if v < lo:
                lo = v
            elif v > hi:
                hi = v
        act[i] = 1 if hi - lo > MOTION else 0
    rows = [sum(act[y * fw:(y + 1) * fw]) / fw for y in range(fh)]
    ys = [y for y, r in enumerate(rows) if r >= ROW_SHARE]
    if not ys:
        return None
    y0, y1 = ys[0], ys[-1] + 1
    # ustunlarni faqat video turgan qatorlar ichida tekshiramiz
    hh = y1 - y0
    cols = [sum(act[y * fw + x] for y in range(y0, y1)) / hh for x in range(fw)]
    xs = [x for x, c in enumerate(cols) if c >= ROW_SHARE]
    if not xs:
        return None
    x0, x1 = xs[0], xs[-1] + 1
    # kichik "tishlab olish"larning oldini olamiz: 15% dan kam kesilsa — to'liq o'lcham
    if (x1 - x0) >= fw * 0.85:
        x0, x1 = 0, fw
    if (y1 - y0) >= fh * 0.85:
        y0, y1 = 0, fh
    return x0, y0, x1, y1


async def autocrop(video: bytes) -> tuple[bytes, bool, str]:
    """(video, vertikalmi, izoh). Qirqish kerak bo'lmasa yoki xato bo'lsa — asl video qaytadi."""
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "in.mp4")
        with open(src, "wb") as f:
            f.write(video)
        try:
            w, h, dur = await probe(src)
        except Exception as e:
            log.warning("ffprobe: %s", e)
            return video, False, ""
        vertical = h > w * 1.15
        try:
            frames, fw, fh = await _frames(src, w, h, dur)
            box = await asyncio.to_thread(active_box, frames, fw, fh)
        except Exception as e:
            log.warning("kadr tahlili: %s", e)
            return video, vertical, ""
        if not box:
            return video, vertical, ""
        x0, y0, x1, y1 = box
        sx, sy = w / fw, h / fh
        cx, cy = int(x0 * sx), int(y0 * sy)
        cw, ch = int((x1 - x0) * sx), int((y1 - y0) * sy)
        cw, ch = min(cw, w - cx) // 2 * 2, min(ch, h - cy) // 2 * 2
        keep = (cw * ch) / (w * h)
        if keep > 1 - MIN_CUT or keep < MIN_KEEP or cw < 240 or ch < 240:
            return video, vertical, ""
        dst = os.path.join(td, "out.mp4")
        try:
            await _run("ffmpeg", "-v", "error", "-y", "-i", src, "-vf", f"crop={cw}:{ch}:{cx}:{cy}",
                       "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", dst)
            with open(dst, "rb") as f:
                out = f.read()
        except Exception as e:
            log.warning("qirqish: %s", e)
            return video, vertical, ""
        new_vertical = ch > cw * 1.15
        note = f"✂️ {w}×{h} → {cw}×{ch}"
        return out, new_vertical, note
