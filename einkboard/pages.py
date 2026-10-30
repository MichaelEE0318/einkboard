"""296x128 1-bit pages. Every page is a pure function of (data, now) -> PIL image, so it can be
previewed and tested on any computer."""
import datetime as dt

from PIL import Image, ImageDraw

from . import fonts
from .sources import LIGHT

W, H = 296, 128
BLACK, WHITE = 0, 255
WEEK = "一二三四五六日"


def canvas():
    img = Image.new("1", (W, H), WHITE)
    return img, ImageDraw.Draw(img)


def cjk(size, bold=False):
    return fonts.get(size, bold=bold, cjk=True)


def num(size, bold=True):
    return fonts.get(size, bold=bold)


def fmt(v, nd=0, dash="--"):
    return dash if v is None else f"{v:,.{nd}f}"


def text_r(d, xr, y, s, font, fill=BLACK):
    d.text((xr - d.textlength(s, font=font), y), s, font=font, fill=fill)


def fit(d, s, font, width):
    """Trim with an ellipsis so CJK titles never run off the panel."""
    if d.textlength(s, font=font) <= width:
        return s
    while s and d.textlength(s + "…", font=font) > width:
        s = s[:-1]
    return s + "…"


def stale_tag(d, x, y):
    f = cjk(12, True)
    w = d.textlength("離線", font=f)
    d.rounded_rectangle([x, y, x + w + 8, y + 16], radius=4, fill=BLACK)
    d.text((x + 4, y - 1), "離線", font=f, fill=WHITE)


# ------------------------------------------------------------------ weather icons
def icon(d, kind, cx, cy, r):
    """Simple line icons drawn with primitives (no icon font needed)."""
    def sun(x, y, rr):
        d.ellipse([x - rr, y - rr, x + rr, y + rr], outline=BLACK, width=2)
        import math
        for a in range(0, 360, 45):
            c, s = math.cos(math.radians(a)), math.sin(math.radians(a))
            d.line([x + c * rr * 1.45, y + s * rr * 1.45, x + c * rr * 1.95, y + s * rr * 1.95], fill=BLACK, width=2)

    def cloud(x, y, rr, fill=WHITE):
        parts = [(x - rr * 0.55, y + rr * 0.1, rr * 0.55), (x + rr * 0.05, y - rr * 0.25, rr * 0.75),
                 (x + rr * 0.65, y + rr * 0.15, rr * 0.5)]
        for px, py, pr in parts:            # outline pass
            d.ellipse([px - pr - 2, py - pr - 2, px + pr + 2, py + pr + 2], fill=BLACK)
        d.rectangle([x - rr * 0.55, y + rr * 0.1, x + rr * 0.65, y + rr * 0.65 + 2], fill=BLACK)
        for px, py, pr in parts:            # fill pass
            d.ellipse([px - pr, py - pr, px + pr, py + pr], fill=fill)
        d.rectangle([x - rr * 0.55, y + rr * 0.1, x + rr * 0.65, y + rr * 0.65], fill=fill)

    if kind == "sun":
        sun(cx, cy, r * 0.5)
    elif kind == "partly":
        sun(cx - r * 0.3, cy - r * 0.3, r * 0.35)
        cloud(cx + r * 0.1, cy + r * 0.15, r * 0.6)
    elif kind in ("cloud", "fog"):
        cloud(cx, cy - r * 0.1, r * 0.75)
        if kind == "fog":
            for i in range(3):
                y = cy + r * 0.55 + i * 5
                d.line([cx - r * 0.7, y, cx + r * 0.7, y], fill=BLACK, width=2)
    elif kind in ("rain", "storm", "snow"):
        cloud(cx, cy - r * 0.3, r * 0.7)
        for i, dx in enumerate((-0.45, 0, 0.45)):
            x, y = cx + dx * r, cy + r * 0.5
            if kind == "snow":
                d.text((x - 4, y - 6), "*", font=num(14), fill=BLACK)
            elif kind == "storm" and i == 1:
                d.line([x + 3, y - 2, x - 3, y + 7, x + 3, y + 7, x - 2, y + 15], fill=BLACK, width=2)
            else:
                d.line([x + 2, y, x - 2, y + 9], fill=BLACK, width=2)


# ------------------------------------------------------------------ page 1: clock + weather
def page_clock(now, wx, loc_name="", stale=False):
    img, d = canvas()
    # left: clock
    hm = now.strftime("%H:%M")
    size = 56
    while size > 30 and d.textlength(hm, font=num(size)) > 146:   # 字寬依字型自動縮，不壓到右半邊
        size -= 2
    d.text((6, 26 - size * 0.58), hm, font=num(size), fill=BLACK)
    d.text((9, 60), f"{now.month}月{now.day}日  週{WEEK[now.weekday()]}", font=cjk(16, True), fill=BLACK)
    d.line([8, 86, 150, 86], fill=BLACK)
    # forecast strip (next 12 h, every 3 h)
    hours = (wx or {}).get("hours") or []
    for i, h in enumerate(hours[:4]):
        x = 8 + i * 36
        d.text((x + 3, 89), f"{h['h']:02d}時", font=cjk(10), fill=BLACK)
        d.text((x + 3, 101), f"{fmt(h['temp'])}°", font=num(12), fill=BLACK)
        d.text((x + 3, 114), f"{fmt(h['rain'])}%", font=num(10, False), fill=BLACK)

    d.line([158, 6, 158, H - 6], fill=BLACK, width=2)

    # right: weather now
    x0 = 166
    if not wx:
        d.text((x0, 50), "天氣資料載入中…", font=cjk(13), fill=BLACK)
        return img
    icon(d, wx["icon"], x0 + 22, 28, 24)
    d.text((x0 + 52, 2), f"{fmt(wx['temp'])}°", font=num(36), fill=BLACK)
    d.text((x0, 56), fit(d, f"{loc_name} {wx['desc']}", cjk(14, True), W - x0 - 4), font=cjk(14, True), fill=BLACK)
    d.text((x0, 76), f"最高 {fmt(wx['hi'])}°  最低 {fmt(wx['lo'])}°", font=cjk(12), fill=BLACK)
    d.text((x0, 93), f"體感 {fmt(wx['feels'])}°  濕度 {fmt(wx['rh'])}%", font=cjk(12), fill=BLACK)
    # rain bar
    rain = wx.get("rain") or 0
    d.text((x0, 110), "降雨", font=cjk(11), fill=BLACK)
    bx0, bx1 = x0 + 28, W - 34
    d.rectangle([bx0, 113, bx1, 122], outline=BLACK)
    d.rectangle([bx0, 113, bx0 + (bx1 - bx0) * min(rain, 100) / 100, 122], fill=BLACK)
    text_r(d, W - 4, 109, f"{fmt(rain)}%", num(12))
    if stale:
        stale_tag(d, W - 34, 2)
    return img


# ------------------------------------------------------------------ page 2: Taipower
def page_power(now, pw, stale=False):
    img, d = canvas()
    d.text((6, 1), "台電供電資訊", font=cjk(15, True), fill=BLACK)
    if not pw:
        d.text((6, 50), "台電資料載入中…", font=cjk(14), fill=BLACK)
        return img
    pub = pw.get("publish") or ""
    text_r(d, W - 6, 4, (pub[-5:] if len(pub) >= 5 else pub) + " 更新", cjk(10))

    # left: today's peak reserve forecast + light
    zh, note = LIGHT.get(pw["light"], ("--", ""))
    d.text((6, 24), "今日尖峰備轉容量率", font=cjk(11), fill=BLACK)
    d.text((4, 34), f"{fmt(pw['fore_rate'], 1)}", font=num(40), fill=BLACK)
    rx = 6 + d.textlength(fmt(pw["fore_rate"], 1), font=num(40))
    d.text((rx + 2, 55), "%", font=num(16), fill=BLACK)
    # light badge: filled = needs attention (non-green)
    lf = cjk(13, True)
    bw = d.textlength(f"{zh} {note}", font=lf) + 12
    alert = pw["light"] not in ("green", None)
    d.rounded_rectangle([6, 84, 6 + bw, 102], radius=9, fill=BLACK if alert else WHITE, outline=BLACK, width=2)
    d.text((12, 83), f"{zh} {note}", font=lf, fill=WHITE if alert else BLACK)
    resv = pw.get("fore_resv")
    sub = f"備轉 {fmt(resv)} 萬瓩" + (f"  {pw['fore_hours']}" if pw.get("fore_hours") else "")
    d.text((6, 107), fit(d, sub, cjk(10), 146), font=cjk(10), fill=BLACK)

    d.line([158, 24, 158, H - 6], fill=BLACK, width=2)

    # right: real-time
    x0 = 166
    d.text((x0, 24), "目前用電", font=cjk(11), fill=BLACK)
    d.text((x0, 36), fmt(pw["load"], 0), font=num(24), fill=BLACK)
    text_r(d, W - 6, 46, "萬瓩", cjk(11))
    d.text((x0, 66), f"供電能力 {fmt(pw['cap'], 0)} 萬瓩", font=cjk(11), fill=BLACK)
    util = pw.get("util")
    d.text((x0, 84), "使用率", font=cjk(11), fill=BLACK)
    text_r(d, W - 6, 82, f"{fmt(util, 1)}%", num(14))
    bx0, bx1 = x0, W - 6
    d.rectangle([bx0, 102, bx1, 112], outline=BLACK)
    if util is not None:
        d.rectangle([bx0, 102, bx0 + (bx1 - bx0) * min(util, 100) / 100, 112], fill=BLACK)
    for t in (90, 94):                 # 參考刻度：使用率 90% / 94% ≈ 備轉 10% / 6%
        tx = bx0 + (bx1 - bx0) * t / 100
        d.polygon([(tx - 3, 97), (tx + 3, 97), (tx, 101)], fill=BLACK)
    if pw.get("yday_rate") is not None:
        d.text((x0, 115), f"昨日尖峰備轉 {fmt(pw['yday_rate'], 1)}%", font=cjk(9), fill=BLACK)
    if stale:
        stale_tag(d, 112, 3)
    return img


# ------------------------------------------------------------------ page 3: stocks + FX
FX_NAME = {"USD": "美元", "JPY": "日圓", "VND": "越南盾", "EUR": "歐元", "CNY": "人民幣", "HKD": "港幣",
           "KRW": "韓元", "GBP": "英鎊", "AUD": "澳幣", "SGD": "新幣", "THB": "泰銖"}


def price_str(v):
    if v is None:
        return "--"
    if v >= 1000:
        return f"{v:,.0f}"
    return f"{v:,.2f}".rstrip("0").rstrip(".")      # 412.5 / 189.85 / 23.1（ETF 跳動單位 0.05）


def fx_str(code, rate):
    """TWD per unit; tiny currencies (VND, KRW...) are shown per 1,000 / 10,000 units."""
    if rate is None:
        return code, "--"
    if rate < 0.01:
        return f"{code}/萬", f"{rate * 10000:.2f}"
    if rate < 0.1:
        return f"{code}/千", f"{rate * 1000:.2f}"
    return code, f"{rate:.4f}" if rate < 1 else f"{rate:.2f}"


def sparkline(d, pts, x0, y0, x1, y1, prev=None):
    """Today's prices (minute_of_day, price) between 09:00 and 13:30; dotted line = previous close."""
    if not pts or len(pts) < 2 or pts[-1][0] - pts[0][0] < 15:
        return                                  # under 15 min of data (e.g. just restarted): skip, it looks broken
    lo = min(p for _, p in pts)
    hi = max(p for _, p in pts)
    if prev:
        lo, hi = min(lo, prev), max(hi, prev)
    if hi - lo < 1e-9:
        hi = lo + 1
    t0, t1 = 9 * 60, 13 * 60 + 30
    xy = lambda m, p: (x0 + (x1 - x0) * (min(max(m, t0), t1) - t0) / (t1 - t0), y1 - (y1 - y0) * (p - lo) / (hi - lo))
    if prev:
        py = xy(t0, prev)[1]
        for x in range(int(x0), int(x1), 3):
            d.point([x, py], fill=BLACK)
    d.line([xy(m, p) for m, p in pts], fill=BLACK, width=1)


def page_market(now, mk, stale=False):
    img, d = canvas()
    d.text((6, 1), "台股．匯率", font=cjk(15, True), fill=BLACK)
    if not mk:
        d.text((6, 50), "行情資料載入中…", font=cjk(14), fill=BLACK)
        return img
    quotes = list(mk["quotes"].values())
    t = max((q.get("time") or "" for q in quotes), default="")
    src = mk.get("src", "mis")
    if mk.get("open"):
        status = f"盤中 {t[:5]}"
    elif src == "close" or (quotes and all(q.get("date") for q in quotes)):
        day = max((q.get("date") or "" for q in quotes), default="")
        status = f"收盤 {int(day[5:7])}/{int(day[8:10])}" if len(day) == 10 else "收盤"
    else:
        status = "收盤" if quotes else ""
    if status:
        f = cjk(11, True)
        w = d.textlength(status, font=f)
        if mk.get("open"):
            d.rounded_rectangle([W - w - 14, 3, W - 4, 18], radius=7, fill=BLACK)
            d.text((W - w - 9, 2), status, font=f, fill=WHITE)
        else:
            text_r(d, W - 6, 3, status, f)
    d.line([6, 21, W - 6, 21], fill=BLACK)

    rows = quotes[:4]
    fx = [(c, r) for c, r in (mk.get("fx") or {}).items()][:3]
    area = (92 if fx else H - 2) - 24
    rh = min(24, area // max(1, len(rows)))
    y = 24
    for q in rows:
        name = fit(d, q["name"] or q["code"], cjk(13, True), 72)
        d.text((6, y - 1), name, font=cjk(13, True), fill=BLACK)
        d.text((6 + d.textlength(name, font=cjk(13, True)) + 3, y + 3), q["code"], font=num(9, False), fill=BLACK)
        sparkline(d, (mk.get("history") or {}).get(q["code"]), 112, y + 2, 158, y + rh - 5, q["prev"])
        text_r(d, 222, y, price_str(q["price"]), num(14))
        pct = q["pct"]
        if pct is None:
            text_r(d, W - 8, y + 1, "--", num(12))
        else:
            arrow = "▲" if pct > 0 else "▼" if pct < 0 else ""
            s = f"{arrow}{abs(pct):.2f}%"
            f = cjk(12 if rh >= 21 else 10, True)          # 4 rows -> smaller text so it fits the box
            bx0, by0, by1 = W - 66, y, y + rh - 3
            l, t, r, b = d.textbbox((0, 0), s, font=f)
            ty = (by0 + by1) / 2 - (t + b) / 2              # centre the glyphs vertically in the box
            if pct > 0:                    # 漲：實心（台灣紅色的位置）
                d.rounded_rectangle([bx0, by0, W - 4, by1], radius=3, fill=BLACK)
                text_r(d, W - 8, ty, s, f, fill=WHITE)
            else:                          # 跌 / 平：空心
                d.rounded_rectangle([bx0, by0, W - 4, by1], radius=3, outline=BLACK)
                text_r(d, W - 8, ty, s, f)
        y += rh
    if not rows:
        d.text((6, 40), "在 config.yaml 的 market.stocks 加股票代號", font=cjk(12), fill=BLACK)

    if fx:
        d.line([6, 94, W - 6, 94], fill=BLACK)
        cw = (W - 12) / len(fx)
        for i, (code, rate) in enumerate(fx):
            x = 6 + i * cw
            label, val = fx_str(code, rate)
            d.text((x, 97), FX_NAME.get(code, code), font=cjk(10), fill=BLACK)
            d.text((x + d.textlength(FX_NAME.get(code, code), font=cjk(10)) + 3, 97), label, font=cjk(9), fill=BLACK)
            d.text((x, 109), val, font=num(14), fill=BLACK)
            if i:
                d.line([x - 5, 98, x - 5, 124], fill=BLACK)
    if stale:
        stale_tag(d, 96, 3)
    return img


# ------------------------------------------------------------------ night / boot
def page_night(now, wx=None):
    img, d = canvas()
    d.text((W // 2 - 40, 18), "晚安", font=cjk(36, True), fill=BLACK)
    msg = f"{now.month}/{now.day} 週{WEEK[now.weekday()]}"
    if wx and wx.get("tomorrow"):
        t = wx["tomorrow"]
        msg += f"   明天 {t['desc']} {fmt(t['lo'])}°~{fmt(t['hi'])}°"
    d.text((W // 2 - d.textlength(msg, font=cjk(13)) / 2, 76), msg, font=cjk(13), fill=BLACK)
    d.text((W // 2 - d.textlength("夜間暫停更新", font=cjk(10)) / 2, 104), "夜間暫停更新", font=cjk(10), fill=BLACK)
    return img


def page_boot(ip=""):
    img, d = canvas()
    d.text((8, 20), "電子紙看板 啟動中", font=cjk(20, True), fill=BLACK)
    d.text((8, 60), "正在連線取得天氣、台電、股價匯率…", font=cjk(12), fill=BLACK)
    if ip:
        d.text((8, 90), f"IP {ip}", font=num(14), fill=BLACK)
    return img
