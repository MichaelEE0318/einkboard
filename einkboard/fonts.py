"""Font lookup with fallbacks (Raspberry Pi OS ships DejaVu; CJK needs fonts-noto-cjk)."""
import functools
import os

from PIL import ImageFont

LATIN = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
]
LATIN_BOLD = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]
CJK = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]
CJK_BOLD = [
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Black.ttc",
] + CJK

# fonts with embedded 12/13 px bitmaps -> crisp Chinese on a 1-bit OLED
CJK_PIXEL = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]

_extra_cjk = []


def set_cjk_font(path):
    if path:
        _extra_cjk.insert(0, path)


def _pick(paths):
    for p in paths:
        if os.path.exists(p):
            return p
    return None


@functools.lru_cache(maxsize=16)
def pixel_cjk(size=12):
    """Chinese for the OLED: WenQuanYi Zen Hei bitmaps, falling back to Noto Sans TC."""
    path = _pick(CJK_PIXEL)
    if path:
        return ImageFont.truetype(path, size)
    return get(size, cjk=True)


@functools.lru_cache(maxsize=64)
def get(size, bold=False, cjk=False):
    if cjk:
        paths = _extra_cjk + (CJK_BOLD if bold else CJK)
    else:
        paths = LATIN_BOLD if bold else LATIN
    path = _pick(paths) or _pick(LATIN)
    if path is None:
        return ImageFont.load_default()
    # Noto CJK .ttc faces: 0 JP, 1 KR, 2 SC, 3 TC, 4 HK -> use TC for Traditional Chinese
    index = 3 if "NotoSansCJK-" in path and path.endswith(".ttc") else 0
    try:
        return ImageFont.truetype(path, size, index=index)
    except OSError:
        return ImageFont.truetype(path, size)
