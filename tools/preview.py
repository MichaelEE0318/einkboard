"""Render every page with sample data -> preview PNGs (works on any computer, no Pi needed).

    python -m tools.preview            # sample data
    python -m tools.preview --live     # fetch real weather / Taipower data
"""
import datetime as dt
import os
import sys
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from einkboard import config, pages  # noqa: E402
from einkboard.sources import Market, Power, Weather  # noqa: E402

TZ = ZoneInfo("Asia/Taipei")
NOW = dt.datetime(2026, 10, 29, 14, 2, tzinfo=TZ)

WX = {"temp": 26, "feels": 28, "rh": 72, "desc": "晴時多雲", "icon": "partly", "hi": 28, "lo": 22, "rain": 30,
      "hours": [{"h": 16, "temp": 26, "rain": 20, "icon": "partly"}, {"h": 19, "temp": 24, "rain": 30, "icon": "cloud"},
                {"h": 22, "temp": 23, "rain": 40, "icon": "rain"}, {"h": 1, "temp": 22, "rain": 30, "icon": "cloud"}],
      "tomorrow": {"hi": 27, "lo": 21, "rain": 60, "desc": "陣雨"}}
PW = {"load": 3125.4, "cap": 3489.0, "util": 89.6, "fore_peak_load": 3310, "fore_cap": 3702, "fore_rate": 11.8,
      "fore_resv": 392, "fore_hours": "13:00~14:00", "light": "green", "yday_rate": 12.4, "publish": "2026-10-29 14:00"}
def _hist(prev, end, n=60, seed=1):
    import random
    rnd, p, out = random.Random(seed), prev, []
    for k in range(n):
        p += (end - p) / (n - k) + rnd.uniform(-1, 1) * prev * 0.002
        out.append((9 * 60 + k * 4, round(p, 2)))
    return out


MK = {"open": True, "src": "mis", "fx_src": "參考匯率",
      "quotes": {"2330": {"code": "2330", "name": "台積電", "price": 1035, "prev": 1020, "chg": 15, "pct": 1.47, "time": "13:25:00"},
                 "0050": {"code": "0050", "name": "元大台灣50", "price": 189.85, "prev": 190.5, "chg": -0.65, "pct": -0.34, "time": "13:25:00"},
                 "2308": {"code": "2308", "name": "台達電", "price": 412.5, "prev": 405, "chg": 7.5, "pct": 1.85, "time": "13:25:00"}},
      "fx": {"USD": 32.25, "JPY": 0.2128, "VND": 0.00123},
      "history": {"2330": _hist(1020, 1035), "0050": _hist(190.5, 189.85, seed=2), "2308": _hist(405, 412.5, seed=3)}}


def sheet(imgs, labels, scale=2):
    from einkboard import fonts
    f = fonts.get(22, bold=True, cjk=True)
    pad, lab = 24, 36
    w, h = pages.W * scale, pages.H * scale
    out = Image.new("RGB", (w + 2 * pad, len(imgs) * (h + lab + pad) + pad), (236, 236, 232))
    d = ImageDraw.Draw(out)
    for i, (im, t) in enumerate(zip(imgs, labels)):
        y = pad + i * (h + lab + pad)
        d.text((pad, y), t, font=f, fill=(40, 40, 40))
        panel = im.convert("L").point(lambda v: 245 if v else 25).convert("RGB").resize((w, h), Image.NEAREST)
        d.rectangle([pad - 6, y + lab - 6, pad + w + 5, y + lab + h + 5], fill=(60, 60, 60))
        out.paste(panel, (pad, y + lab))
    return out


def main():
    live = "--live" in sys.argv
    wx, pw, mk = WX, PW, MK
    if live:
        cfg = config.load()
        w, p, m = Weather(cfg), Power(cfg), Market(cfg)
        for src in (w, p, m):
            src.poll_once()
        wx, pw, mk = w.data or WX, p.data or PW, m.data or MK
        print("weather:", w.error or "ok", "| power:", p.error or "ok", "| market:", m.error or "ok")
    imgs = [pages.page_clock(NOW, wx, "台北"), pages.page_power(NOW, pw), pages.page_market(NOW, mk),
            pages.page_night(NOW.replace(hour=23, minute=40), wx)]
    out_dir = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else "."
    names = ["clock", "power", "market", "night"]
    for n, im in zip(names, imgs):
        im.save(os.path.join(out_dir, f"page_{n}.png"))
    sheet(imgs, ["① 時鐘＋天氣", "② 台電供電資訊", "③ 台股．匯率（漲＝實心、跌＝空心，線圖為今日走勢）", "夜間（23:30 後停止刷新）"]).save(
        os.path.join(out_dir, "preview_pages.png"))
    print("saved to", os.path.abspath(out_dir))


if __name__ == "__main__":
    main()
