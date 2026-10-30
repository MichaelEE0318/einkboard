"""Data sources. Each fetcher runs in a background thread, keeps the last good value,
and never raises into the display loop — no network just means slightly older data."""
import datetime as dt
import json
import logging
import os
import threading
import time
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger(__name__)
UA = {"User-Agent": "einkboard/1.0 (Raspberry Pi desk display)"}


class Source:
    """Polls `fetch()` every `interval` seconds; `.data` is the last good result."""

    def __init__(self, interval):
        self.interval = interval
        self.data = None
        self.updated = 0.0
        self.error = None
        self._stop = threading.Event()

    def fetch(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def poll_once(self):
        try:
            self.data = self.fetch()
            self.updated = time.time()
            self.error = None
        except Exception as e:  # keep the old data, remember why
            self.error = str(e)
            log.warning("%s: %s", type(self).__name__, e)

    def wait(self):
        return self.interval

    @property
    def stale(self):
        return self.data is None or time.time() - self.updated > max(self.interval, self.wait()) * 3

    def start(self):
        def run():
            while not self._stop.is_set():
                self.poll_once()
                # retry sooner after a failure (boot before Wi-Fi is up is common)
                self._stop.wait(60 if self.error else self.wait())
        threading.Thread(target=run, daemon=True, name=type(self).__name__).start()
        return self

    def stop(self):
        self._stop.set()


# ------------------------------------------------------------------ weather
# WMO weather code -> (中文, icon key)
WMO = {
    0: ("晴", "sun"), 1: ("晴時多雲", "partly"), 2: ("多雲", "partly"), 3: ("陰", "cloud"),
    45: ("霧", "fog"), 48: ("霧", "fog"),
    51: ("毛毛雨", "rain"), 53: ("毛毛雨", "rain"), 55: ("毛毛雨", "rain"),
    56: ("凍雨", "rain"), 57: ("凍雨", "rain"),
    61: ("小雨", "rain"), 63: ("中雨", "rain"), 65: ("大雨", "rain"),
    66: ("凍雨", "rain"), 67: ("凍雨", "rain"),
    71: ("小雪", "snow"), 73: ("中雪", "snow"), 75: ("大雪", "snow"), 77: ("雪", "snow"),
    80: ("陣雨", "rain"), 81: ("陣雨", "rain"), 82: ("豪雨", "rain"),
    85: ("陣雪", "snow"), 86: ("陣雪", "snow"),
    95: ("雷雨", "storm"), 96: ("雷雨", "storm"), 99: ("雷雨", "storm"),
}


def wmo(code):
    return WMO.get(int(code) if code is not None else -1, ("--", "cloud"))


def parse_weather(j, now=None):
    cur, daily, hourly = j["current"], j["daily"], j["hourly"]
    desc, icon = wmo(cur.get("weather_code"))
    out = {
        "temp": cur.get("temperature_2m"),
        "feels": cur.get("apparent_temperature"),
        "rh": cur.get("relative_humidity_2m"),
        "desc": desc, "icon": icon,
        "hi": daily["temperature_2m_max"][0], "lo": daily["temperature_2m_min"][0],
        "rain": daily["precipitation_probability_max"][0],
        "hours": [],
        "tomorrow": None,
    }
    if len(daily["temperature_2m_max"]) > 1:
        out["tomorrow"] = {"hi": daily["temperature_2m_max"][1], "lo": daily["temperature_2m_min"][1],
                           "rain": daily["precipitation_probability_max"][1],
                           "desc": wmo((daily.get("weather_code") or [None, None])[1])[0]}
    # next 4 slots every 3 h, starting from the next whole hour
    now = now or dt.datetime.fromisoformat(cur["time"])
    times = [dt.datetime.fromisoformat(t) for t in hourly["time"]]
    start = next((i for i, t in enumerate(times) if t > now), None)
    if start is not None:
        for i in range(start + 2, min(start + 2 + 12, len(times)), 3):
            out["hours"].append({
                "h": times[i].hour, "temp": hourly["temperature_2m"][i],
                "rain": hourly["precipitation_probability"][i],
                "icon": wmo(hourly["weather_code"][i])[1],
            })
    return out


class Weather(Source):
    URL = "https://api.open-meteo.com/v1/forecast"

    def __init__(self, cfg):
        super().__init__(cfg["weather"]["refresh_minutes"] * 60)
        self.loc = cfg["location"]

    def fetch(self):
        r = requests.get(self.URL, headers=UA, timeout=15, params={
            "latitude": self.loc["lat"], "longitude": self.loc["lon"], "timezone": self.loc["tz"],
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,weather_code",
            "hourly": "temperature_2m,precipitation_probability,weather_code",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
            "forecast_days": 2,
        })
        r.raise_for_status()
        return parse_weather(r.json())


# ------------------------------------------------------------------ Taipower
# 台電供電燈號（以備轉容量率 / 備轉容量判斷）
LIGHTS = [  # (key, 中文, 說明)
    ("green", "綠燈", "供電充裕"),
    ("yellow", "黃燈", "供電吃緊"),
    ("orange", "橘燈", "供電警戒"),
    ("red", "紅燈", "限電警戒"),
    ("black", "黑燈", "限電準備"),
]
LIGHT = {k: (zh, note) for k, zh, note in LIGHTS}
_IND = {"G": "green", "Y": "yellow", "O": "orange", "R": "red", "B": "black"}


def light_for(rate, reserve_mw=None):
    """備轉容量率 ≥10% 綠、6~10% 黃、<6% 橘；備轉容量 <90 萬瓩 紅、<50 萬瓩 黑。"""
    if reserve_mw is not None:
        if reserve_mw < 500:
            return "black"
        if reserve_mw < 900:
            return "red"
    if rate is None:
        return None
    return "green" if rate >= 10 else "yellow" if rate >= 6 else "orange"


def _num(v):
    if v is None:
        return None
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except ValueError:
        return None


def parse_power(j):
    """Taipower open data d006020 (今日電力資訊). Loads are in 萬瓩 (10 MW)."""
    recs = j["records"] if isinstance(j, dict) and "records" in j else j
    if isinstance(recs, dict):
        recs = [recs]
    # Taipower splits one snapshot into several records (now / today's forecast / yesterday / supply
    # capacity) — merge them into one dict.
    r = {}
    for rec in recs:
        if isinstance(rec, dict):
            r.update(rec)
    load = _num(r.get("curr_load"))
    cap = _num(r.get("real_hr_maxi_sply_capacity"))
    util = _num(r.get("curr_util_rate"))
    if util is None and load and cap:
        util = load / cap * 100
    fore_rate = _num(r.get("fore_peak_resv_rate"))
    fore_cap = _num(r.get("fore_peak_resv_capacity"))
    ind = str(r.get("fore_peak_resv_indicator") or "").strip().upper()[:1]
    light = _IND.get(ind) or light_for(fore_rate, fore_cap * 10 if fore_cap is not None else None)
    return {
        "load": load,                     # 目前用電 萬瓩
        "cap": cap,                       # 目前供電能力 萬瓩
        "util": util,                     # 使用率 %
        "fore_peak_load": _num(r.get("fore_peak_dema_load")),
        "fore_cap": _num(r.get("fore_maxi_sply_capacity")),
        "fore_rate": fore_rate,           # 今日預估尖峰備轉容量率 %
        "fore_resv": fore_cap,            # 今日預估尖峰備轉容量 萬瓩
        "fore_hours": r.get("fore_peak_hour_range") or "",
        "light": light,
        "yday_rate": _num(r.get("yday_peak_resv_rate")),
        "publish": str(r.get("publish_time") or ""),
    }


class Power(Source):
    def __init__(self, cfg):
        super().__init__(cfg["power"]["refresh_minutes"] * 60)
        self.url = cfg["power"]["url"]

    def fetch(self):
        r = requests.get(self.url, headers=UA, timeout=15)
        r.raise_for_status()
        return parse_power(r.json())


# ------------------------------------------------------------------ market: TW stocks + FX
TW = ZoneInfo("Asia/Taipei")


def market_open(now):
    """台股盤中：週一到週五 09:00–13:30（不含國定假日，假日只是多抓幾次而已）。"""
    now = now.astimezone(TW)
    return now.weekday() < 5 and 9 * 60 <= now.hour * 60 + now.minute <= 13 * 60 + 35


def _px(v):
    """MIS price strings: '1035.0000', '-' (no trade yet), or '1035.0000_1030.0000_' (bid/ask ladders)."""
    if v is None:
        return None
    v = str(v).split("_")[0].strip()
    try:
        x = float(v)
        return x if x > 0 else None
    except ValueError:
        return None


# 指數在 MIS 的全名太長，畫面上用簡稱
SHORT_NAMES = {"t00": "加權指數", "o00": "櫃買指數", "FRMSA": "寶島指數"}


def parse_mis(j):
    """TWSE MIS getStockInfo.jsp -> {code: {...}}"""
    out = {}
    for r in j.get("msgArray", []):
        prev = _px(r.get("y"))
        # 'z' is '-' between trades; fall back to the last trade 'pz', best bid, best ask, then open
        last = _px(r.get("z")) or _px(r.get("pz")) or _px(r.get("b")) or _px(r.get("a")) or _px(r.get("o"))
        chg = (last - prev) if (last is not None and prev) else None
        out[r.get("c")] = {
            "code": r.get("c"), "name": SHORT_NAMES.get(r.get("c"), (r.get("n") or "").strip()),
            "price": last, "prev": prev,
            "chg": chg, "pct": (chg / prev * 100) if chg is not None else None,
            "high": _px(r.get("h")), "low": _px(r.get("l")),
            "vol": _num(r.get("v")),                  # 張
            "time": r.get("t") or "",
        }
    return out


def roc_date(v):
    """'1151028' (民國 115/10/28) -> date(2026, 10, 28); also accepts '115/10/28' and '20261028'."""
    v = str(v or "").replace("/", "").strip()
    try:
        if len(v) == 7:
            return dt.date(int(v[:3]) + 1911, int(v[3:5]), int(v[5:]))
        if len(v) == 8:
            return dt.date(int(v[:4]), int(v[4:6]), int(v[6:]))
    except ValueError:
        pass
    return None


def _first(r, *keys):
    for k in keys:
        if r.get(k) not in (None, "", "--", "---"):
            return r[k]
    return None


def parse_openapi_close(rows, codes):
    """TWSE STOCK_DAY_ALL / TPEx mainboard close quotes -> {code: quote} for the wanted codes only."""
    out = {}
    for r in rows:
        code = str(_first(r, "Code", "SecuritiesCompanyCode") or "").strip()
        if code not in codes:
            continue
        close = _num(_first(r, "ClosingPrice", "Close"))
        chg = _num(_first(r, "Change"))
        prev = (close - chg) if (close is not None and chg is not None) else None
        day = roc_date(_first(r, "Date"))
        out[code] = {
            "code": code, "name": str(_first(r, "Name", "CompanyName") or "").strip(), "price": close, "prev": prev,
            "chg": chg, "pct": (chg / prev * 100) if (chg is not None and prev) else None,
            "high": _num(_first(r, "HighestPrice", "High")), "low": _num(_first(r, "LowestPrice", "Low")),
            "vol": None, "time": "", "date": day.isoformat() if day else "",
        }
    return out


def parse_erapi(j):
    """open.er-api.com /v6/latest/TWD -> {CCY: TWD per 1 unit}"""
    if j.get("result") not in (None, "success"):
        raise ValueError(j.get("error-type", "er-api error"))
    return {k: 1 / v for k, v in j["rates"].items() if v}


class Market(Source):
    """台股三層退路：MIS 即時 → 證交所/櫃買 OpenAPI 收盤 → 硬碟快取。匯率用 open.er-api（每日）。

    data["src"]: "mis" 即時、"close" 官方收盤價、"cache" 讀自硬碟（網路全掛）
    """
    MIS = "https://mis.twse.com.tw/stock/api/getStockInfo.jsp"
    TWSE_CLOSE = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
    TPEX_CLOSE = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
    ERAPI = "https://open.er-api.com/v6/latest/TWD"

    def __init__(self, cfg):
        m = cfg["market"]
        super().__init__(60)
        self.stocks = [str(s) for s in m["stocks"]]
        self.fx = m["fx"]
        self.fx_every = m["fx_refresh_minutes"] * 60
        self.fx_rates, self.fx_time = {}, 0.0
        self.close_quotes, self.close_time = {}, 0.0
        self.history = {}                       # code -> [(minute_of_day, price)] for today's sparkline
        self.history_day = None
        self.cache_path = os.path.join(cfg["cache_dir"], "market.json")
        self.saved_at = 0.0
        self.session = requests.Session()
        self.session.headers.update(UA)
        self.load_cache()

    # ---------------- tier 1: MIS
    def _ex_ch(self):
        # 上市 tse_、上櫃 otc_ 都查，MIS 只會回傳存在的那一個
        return "|".join(f"{ex}_{c}.tw" for c in self.stocks for ex in ("tse", "otc"))

    def fetch_mis(self):
        if not self.stocks:
            return {}
        r = self.session.get(self.MIS, timeout=10, params={
            "ex_ch": self._ex_ch(), "json": 1, "delay": 0, "_": int(time.time() * 1000)})
        r.raise_for_status()
        if "json" not in r.headers.get("Content-Type", "json") and not r.text.lstrip().startswith("{"):
            raise ValueError("MIS returned a non-JSON page (blocked?)")
        return parse_mis(r.json())

    # ---------------- tier 2: official close (cached 3 h; the files only change once a day)
    def fetch_close(self, force=False):
        if not force and self.close_quotes and time.time() - self.close_time < 3 * 3600:
            return self.close_quotes
        out, errors = {}, []
        for url in (self.TWSE_CLOSE, self.TPEX_CLOSE):
            missing = set(self.stocks) - set(out)
            if not missing:
                break
            try:
                r = self.session.get(url, timeout=30)
                r.raise_for_status()
                out.update(parse_openapi_close(r.json(), missing))
            except Exception as e:
                errors.append(f"{url.split('/')[2]}: {e}")
        if not out and errors:
            raise IOError("; ".join(errors))
        self.close_quotes, self.close_time = out, time.time()
        return out

    # ---------------- FX
    def fetch_fx(self):
        r = self.session.get(self.ERAPI, timeout=15)
        r.raise_for_status()
        return parse_erapi(r.json())

    # ---------------- intraday history (for the sparkline)
    def record(self, quotes, now):
        if now.date() != self.history_day:
            self.history, self.history_day = {}, now.date()
        if not market_open(now):
            return
        m = now.hour * 60 + now.minute
        for code, q in quotes.items():
            if q["price"] is None:
                continue
            h = self.history.setdefault(code, [])
            if not h or h[-1][0] != m:
                h.append((m, q["price"]))

    # ---------------- the poll
    def fetch(self):
        now = dt.datetime.now(TW)
        quotes, src, errs = {}, "mis", []
        try:
            quotes = {c: q for c, q in self.fetch_mis().items() if q["price"] is not None}
        except Exception as e:
            errs.append(f"MIS: {e}")
        if set(self.stocks) - set(quotes):       # MIS 掛了或少了幾檔 → 官方收盤價補
            try:
                close = self.fetch_close(force=bool(errs) and not self.close_quotes)
                for c in self.stocks:
                    if c not in quotes and c in close:
                        quotes[c] = close[c]
                if errs:
                    src = "close"
            except Exception as e:
                errs.append(f"OpenAPI: {e}")
        if not quotes:
            raise IOError(" | ".join(errs) or "no quotes")
        if errs:
            log.info("market fallback: %s", " | ".join(errs))
        self.record({c: q for c, q in quotes.items() if src == "mis"}, now)
        if self.fx and time.time() - self.fx_time > self.fx_every:
            try:
                self.fx_rates, self.fx_time = self.fetch_fx(), time.time()
            except Exception as e:
                log.warning("fx: %s", e)
        data = {"quotes": {c: quotes[c] for c in self.stocks if c in quotes},
                "fx": {c: self.fx_rates.get(c) for c in self.fx}, "fx_src": "參考匯率",
                "open": market_open(now) and src == "mis", "src": src,
                "history": self.history, "fetched": now.isoformat(timespec="minutes")}
        self.save_cache(data)
        return data

    # ---------------- tier 3: disk cache (survives reboots and total outages)
    def save_cache(self, data, every=300):
        if time.time() - self.saved_at < every:
            return
        try:
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"data": data, "history_day": str(self.history_day or ""),
                           "fx": self.fx_rates, "fx_time": self.fx_time}, f, ensure_ascii=False)
            os.replace(tmp, self.cache_path)          # atomic: a power cut never leaves half a file
            self.saved_at = time.time()
        except OSError as e:
            log.warning("cache save: %s", e)

    def load_cache(self):
        try:
            with open(self.cache_path, encoding="utf-8") as f:
                c = json.load(f)
        except (OSError, ValueError):
            return
        d = c.get("data") or {}
        today = dt.datetime.now(TW).date().isoformat()
        if c.get("history_day") == today:              # same trading day: keep the sparkline going
            self.history = {k: [tuple(p) for p in v] for k, v in (d.get("history") or {}).items()}
            self.history_day = dt.date.fromisoformat(today)
        self.fx_rates, self.fx_time = c.get("fx") or {}, 0.0   # show old FX now, refresh at first poll
        d.update(src="cache", open=False, history=self.history)
        self.data = d                                  # updated stays 0 -> page shows 離線 until a live poll

    def wait(self):
        return 60 if market_open(dt.datetime.now(TW)) else 30 * 60
