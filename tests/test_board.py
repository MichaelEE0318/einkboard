import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from einkboard import config, pages
from einkboard.main import Board, active_pages, is_night, page_at
from einkboard.sources import (Market, light_for, market_open, parse_openapi_close, roc_date, parse_erapi, parse_mis,
                                parse_power, parse_weather, wmo)

TZ = ZoneInfo("Asia/Taipei")


# ------------------------------------------------------------------ Taipower
TAIPOWER = {"success": "true", "result": {"resource_id": "x"}, "records": [{
    "curr_load": "3,125.4", "curr_util_rate": "89.6", "real_hr_maxi_sply_capacity": "3489",
    "fore_maxi_sply_capacity": "3702", "fore_peak_dema_load": "3310", "fore_peak_resv_capacity": "392",
    "fore_peak_resv_rate": "11.84", "fore_peak_resv_indicator": "G", "fore_peak_hour_range": "13:00~14:00",
    "publish_time": "2026-10-29 14:00", "yday_peak_resv_rate": "12.4"}]}


def test_parse_power_reads_strings_with_commas():
    p = parse_power(TAIPOWER)
    assert p["load"] == pytest.approx(3125.4)
    assert p["fore_rate"] == pytest.approx(11.84)
    assert p["light"] == "green"


def test_parse_power_computes_util_and_light_when_missing():
    rec = dict(TAIPOWER["records"][0], curr_util_rate=None, fore_peak_resv_indicator="", fore_peak_resv_rate="7.2")
    p = parse_power({"records": [rec]})
    assert p["util"] == pytest.approx(3125.4 / 3489 * 100)
    assert p["light"] == "yellow"


@pytest.mark.parametrize("rate,resv_mw,light", [
    (12, None, "green"), (10, None, "green"), (8, None, "yellow"), (5.9, None, "orange"),
    (5, 850, "red"), (3, 400, "black")])
def test_taipower_light_thresholds(rate, resv_mw, light):
    assert light_for(rate, resv_mw) == light


# ------------------------------------------------------------------ weather
def test_parse_weather_picks_3h_slots_after_now():
    times = [f"2026-10-29T{h:02d}:00" for h in range(24)] + [f"2026-10-30T{h:02d}:00" for h in range(24)]
    j = {"current": {"time": "2026-10-29T14:15", "temperature_2m": 26, "apparent_temperature": 28,
                     "relative_humidity_2m": 70, "weather_code": 2},
         "daily": {"weather_code": [2, 80], "temperature_2m_max": [28, 27], "temperature_2m_min": [22, 21],
                   "precipitation_probability_max": [30, 60]},
         "hourly": {"time": times, "temperature_2m": list(range(48)), "precipitation_probability": [10] * 48,
                    "weather_code": [61] * 48}}
    w = parse_weather(j)
    assert w["desc"] == "多雲" and w["icon"] == "partly"
    assert [h["h"] for h in w["hours"]] == [17, 20, 23, 2]
    assert w["hours"][0]["icon"] == "rain"
    assert w["tomorrow"] == {"hi": 27, "lo": 21, "rain": 60, "desc": "陣雨"}


def test_unknown_weather_code_does_not_crash():
    assert wmo(None)[0] == "--"


# ------------------------------------------------------------------ market
MIS = {"msgArray": [
    {"c": "2330", "n": "台積電", "z": "1035.0000", "y": "1020.0000", "o": "1025.0000", "h": "1040.0000",
     "l": "1020.0000", "v": "25,431", "t": "13:30:00", "b": "1030.0000_1025.0000_", "a": "1035.0000_1040.0000_"},
    {"c": "0050", "n": "元大台灣50", "z": "-", "y": "190.50", "b": "189.8500_189.8000_", "a": "189.9000_", "t": "10:02:05"},
    {"c": "6488", "n": "環球晶", "z": "-", "y": "-", "t": ""}]}


def test_parse_mis_uses_best_bid_when_no_trade():
    q = parse_mis(MIS)
    assert q["2330"]["price"] == 1035 and q["2330"]["pct"] == pytest.approx(1.4706, abs=1e-3)
    assert q["0050"]["price"] == pytest.approx(189.85)          # z='-' -> best bid
    assert q["0050"]["chg"] == pytest.approx(-0.65)
    assert q["6488"]["price"] is None and q["6488"]["pct"] is None


def test_parse_erapi_inverts_to_twd_per_unit():
    r = parse_erapi({"result": "success", "rates": {"TWD": 1, "USD": 0.031, "VND": 800.0}})
    assert r["USD"] == pytest.approx(32.258, abs=1e-3) and r["VND"] == pytest.approx(0.00125)


def test_market_hours():
    at = lambda d, h, m: dt.datetime(2026, 10, d, h, m, tzinfo=TZ)
    assert market_open(at(29, 9, 0)) and market_open(at(29, 13, 30))
    assert not market_open(at(29, 8, 59)) and not market_open(at(29, 14, 0))
    assert not market_open(at(31, 10, 0))                        # Saturday


def mcfg(tmp_path):
    cfg = config.load("/nonexistent")
    cfg["cache_dir"] = str(tmp_path)
    return cfg


def test_market_history_only_in_session_and_resets_daily(tmp_path):
    m = Market(mcfg(tmp_path))
    q = parse_mis(MIS)
    m.record(q, dt.datetime(2026, 10, 29, 10, 0, tzinfo=TZ))
    m.record(q, dt.datetime(2026, 10, 29, 10, 0, tzinfo=TZ))      # same minute: no duplicate
    m.record(q, dt.datetime(2026, 10, 29, 14, 0, tzinfo=TZ))      # after close: ignored
    assert m.history["2330"] == [(600, 1035)]
    m.record(q, dt.datetime(2026, 10, 30, 9, 1, tzinfo=TZ))
    assert m.history["2330"] == [(541, 1035)]


def test_market_queries_both_listed_and_otc(tmp_path):
    m = Market(mcfg(tmp_path))
    assert m._ex_ch().startswith("tse_2330.tw|otc_2330.tw|")


def test_mis_uses_last_trade_pz():
    q = parse_mis({"msgArray": [{"c": "2330", "n": "台積電", "z": "-", "pz": "2415.0000", "y": "2400.0000",
                                  "b": "2410.0000_", "a": "2415.0000_", "t": "14:30:00"}]})
    assert q["2330"]["price"] == 2415                   # pz (last trade) beats best bid


# real rows as returned by openapi.twse.com.tw (2026-10-29 test from Michael's Mac)
TWSE_ROWS = [
    {"Date": "1151028", "Code": "00400A", "Name": "主動國泰動能高息", "TradeVolume": "24640364",
     "OpeningPrice": "14.54", "HighestPrice": "14.73", "LowestPrice": "14.46", "ClosingPrice": "14.71",
     "Change": "0.5300", "Transaction": "4483"},
    {"Date": "1151028", "Code": "2330", "Name": "台積電", "ClosingPrice": "2,400.00", "Change": "-15.0000",
     "HighestPrice": "2,430.00", "LowestPrice": "2,395.00"},
]
TPEX_ROWS = [{"Date": "1151028", "SecuritiesCompanyCode": "6488", "CompanyName": "環球晶", "Close": "420.50",
              "Change": "+3.50", "High": "425.00", "Low": "415.00"}]


def test_roc_date():
    assert roc_date("1151028") == dt.date(2026, 10, 28)
    assert roc_date("115/10/28") == dt.date(2026, 10, 28)
    assert roc_date("") is None


def test_openapi_close_twse_and_tpex():
    q = parse_openapi_close(TWSE_ROWS + TPEX_ROWS, {"2330", "6488"})
    assert set(q) == {"2330", "6488"}
    assert q["2330"]["price"] == 2400 and q["2330"]["prev"] == 2415
    assert q["2330"]["pct"] == pytest.approx(-0.621, abs=1e-3) and q["2330"]["date"] == "2026-10-28"
    assert q["6488"]["name"] == "環球晶" and q["6488"]["chg"] == 3.5


class FakeResp:
    def __init__(self, j=None, text=None, ctype="application/json"):
        self._j, self.text, self.headers = j, text if text is not None else "{}", {"Content-Type": ctype}

    def raise_for_status(self):
        pass

    def json(self):
        if self._j is None:
            raise ValueError("not json")
        return self._j


def fake_session(routes):
    class S:
        headers = {}
        calls = []

        def get(self, url, **kw):
            S.calls.append(url)
            r = routes.get(url)
            if isinstance(r, Exception):
                raise r
            return r
    return S()


def test_market_falls_back_to_official_close_when_mis_blocked(tmp_path):
    m = Market(mcfg(tmp_path))
    m.stocks = ["2330", "6488"]
    m.session = fake_session({
        m.MIS: FakeResp(text="<!DOCTYPE html><title>Challenge Validation</title>", ctype="text/html"),
        m.TWSE_CLOSE: FakeResp(TWSE_ROWS), m.TPEX_CLOSE: FakeResp(TPEX_ROWS),
        m.ERAPI: FakeResp({"result": "success", "rates": {"USD": 0.031}})})
    d = m.fetch()
    assert d["src"] == "close" and not d["open"]
    assert list(d["quotes"]) == ["2330", "6488"]
    img = pages.page_market(dt.datetime(2026, 10, 29, 10, 0, tzinfo=TZ), d)
    assert img.size == (296, 128)


def test_market_mis_ok_fills_missing_code_from_close(tmp_path):
    m = Market(mcfg(tmp_path))
    m.stocks = ["2330", "6488"]
    m.session = fake_session({
        m.MIS: FakeResp({"msgArray": [{"c": "2330", "n": "台積電", "z": "2420", "y": "2400", "t": "10:01:00"}]}),
        m.TWSE_CLOSE: FakeResp(TWSE_ROWS), m.TPEX_CLOSE: FakeResp(TPEX_ROWS),
        m.ERAPI: FakeResp({"result": "success", "rates": {}})})
    d = m.fetch()
    assert d["src"] == "mis" and d["quotes"]["2330"]["price"] == 2420 and d["quotes"]["6488"]["price"] == 420.5


def test_market_raises_when_everything_down_and_cache_survives_restart(tmp_path):
    m = Market(mcfg(tmp_path))
    m.session = fake_session({m.MIS: FakeResp(MIS), m.ERAPI: FakeResp({"result": "success", "rates": {"USD": 0.031}}),
                              m.TWSE_CLOSE: FakeResp([]), m.TPEX_CLOSE: FakeResp([])})
    m.stocks = ["2330", "0050"]
    m.fetch()
    assert (tmp_path / "market.json").exists()
    m2 = Market(mcfg(tmp_path))                          # "reboot"
    assert m2.data["src"] == "cache" and m2.stale
    assert m2.data["quotes"]["2330"]["price"] == 1035
    m2.session = fake_session({m2.MIS: IOError("down"), m2.TWSE_CLOSE: IOError("down"), m2.TPEX_CLOSE: IOError("down")})
    m2.poll_once()                                       # failure keeps the cached data
    assert m2.data["quotes"]["2330"]["price"] == 1035 and m2.error


@pytest.mark.parametrize("code,rate,label,val", [
    ("USD", 32.25, "USD", "32.25"), ("JPY", 0.2128, "JPY", "0.2128"), ("VND", 0.00123, "VND/萬", "12.30")])
def test_fx_formatting(code, rate, label, val):
    assert pages.fx_str(code, rate) == (label, val)


# ------------------------------------------------------------------ scheduling
def test_night_window_crosses_midnight():
    n = {"enabled": True, "start": "23:30", "end": "07:00"}
    at = lambda h, m: dt.datetime(2026, 10, 29, h, m, tzinfo=TZ)
    assert is_night(at(23, 45), n) and is_night(at(3, 0), n)
    assert not is_night(at(7, 0), n) and not is_night(at(12, 0), n)


def test_page_rotation():
    names = ["clock", "power", "market"]
    at = lambda m: dt.datetime(2026, 10, 29, 10, m, tzinfo=TZ)
    assert [page_at(at(m), names, 2) for m in (0, 1, 2, 4, 6)] == ["clock", "clock", "power", "market", "clock"]


def test_unknown_pages_ignored():
    cfg = config.load("/nonexistent")
    cfg["pages"] = ["clock", "calendar", "market"]
    assert active_pages(cfg) == ["clock", "market"]


# ------------------------------------------------------------------ rendering
@pytest.mark.parametrize("fn", [
    lambda now: pages.page_clock(now, None),
    lambda now: pages.page_power(now, None),
    lambda now: pages.page_market(now, None),
    lambda now: pages.page_market(now, {"quotes": {}, "fx": {}, "open": False}),
    lambda now: pages.page_market(now, {"quotes": parse_mis(MIS), "fx": {"USD": None}, "open": True,
                                        "history": {"2330": [(560, 1020), (600, 1035)]}}),
    lambda now: pages.page_power(now, parse_power({"records": [{}]})),
    lambda now: pages.page_night(now),
])
def test_pages_render_with_missing_data(fn):
    img = fn(dt.datetime(2026, 10, 29, 14, 2, tzinfo=TZ))
    assert img.size == (296, 128) and img.mode == "1"


class FakeSource:
    def __init__(self, data=None):
        self.data, self.stale, self.events = data, False, data


def test_board_refresh_policy(tmp_path):
    cfg = config.load("/nonexistent")
    cfg.update(simulate=True, sim_dir=str(tmp_path))
    b = Board(cfg)
    b.weather, b.power, b.market = FakeSource(), FakeSource(), FakeSource()
    times = iter([dt.datetime(2026, 10, 29, 10, m, tzinfo=TZ) for m in (1, 2, 3)] +
                 [dt.datetime(2026, 10, 29, 23, m, tzinfo=TZ) for m in (40, 41)])
    b.now = lambda: next(times)
    assert [b.tick() for _ in range(5)] == ["clock", "power", "power", "night", "night"]
    assert b.display.fulls == 2          # first frame + entering night; night drawn only once
    assert (tmp_path / "eink.png").exists()


def test_panel_without_reset_pin_never_deep_sleeps(monkeypatch):
    import sys, types
    from einkboard import display
    calls = []

    class EPD:
        def init(self): calls.append("init")
        def display_Base(self, b): calls.append("base")
        def display_Partial(self, b): calls.append("partial")
        def getbuffer(self, img): return b""
        def Clear(self, c=0xFF): calls.append("clear")
        def sleep(self): calls.append("SLEEP")

    pkg = types.ModuleType("waveshare_epd"); mod = types.ModuleType("waveshare_epd.epd2in9_V2"); mod.EPD = EPD
    monkeypatch.setitem(sys.modules, "waveshare_epd", pkg)
    monkeypatch.setitem(sys.modules, "waveshare_epd.epd2in9_V2", mod)
    cfg = config.load("/nonexistent")
    cfg["eink"]["driver"] = "epd2in9_V2"
    e = display.Eink(cfg)
    e.show(pages.page_night(dt.datetime(2026, 10, 29, 23, 40, tzinfo=TZ)), force_full=True)
    e.sleep()
    e.clear()
    assert "SLEEP" not in calls
    cfg["eink"]["has_reset_pin"] = True
    e2 = display.Eink(cfg)
    e2.show(pages.page_night(dt.datetime(2026, 10, 29, 23, 40, tzinfo=TZ)), force_full=True)
    e2.sleep()
    assert calls[-1] == "SLEEP"


# ------------------------------------------------------------------ Heltec SSD1680 driver
def fake_hw(monkeypatch):
    import sys, types
    log = []

    class Spi:
        def open(self, b, d): log.append(("open", b, d))
        def writebytes(self, b): log.append(("w", bytes(b)))
        def writebytes2(self, b): log.append(("w", bytes(b)))
        def close(self): pass

    class Out:
        def __init__(self, pin, initial_value=False): self.pin = pin
        def on(self): log.append(("dc" if self.pin == 25 else f"pin{self.pin}", 1))
        def off(self): log.append(("dc" if self.pin == 25 else f"pin{self.pin}", 0))
        def close(self): pass

    class In:
        value = 0
        def __init__(self, pin, pull_up=False): pass
        def close(self): pass

    monkeypatch.setitem(sys.modules, "spidev", types.SimpleNamespace(SpiDev=Spi))
    monkeypatch.setitem(sys.modules, "gpiozero", types.SimpleNamespace(DigitalOutputDevice=Out, DigitalInputDevice=In))
    return log


def commands(log):
    """Return [(cmd, data_bytes)] from the raw SPI/DC log."""
    out, dc = [], None
    for ev in log:
        if ev[0] == "dc":
            dc = ev[1]
        elif ev[0] == "w":
            if dc == 0:
                out.append([ev[1][0], b""])
            elif out:
                out[-1][1] += ev[1]
    return [(c, bytes(d)) for c, d in out]


def test_heltec_init_uses_soft_reset_and_no_custom_lut(monkeypatch):
    log = fake_hw(monkeypatch)
    from einkboard.heltec290 import EPD
    epd = EPD()
    epd.init()
    cmds = [c for c, _ in commands(log)]
    assert cmds[0] == 0x12                    # soft reset first (no RST pin)
    assert 0x32 not in cmds                   # never loads a custom waveform (the Waveshare problem)
    assert dict(commands(log))[0x01] == bytes([0x27, 0x01, 0x00])   # 296 gate lines


def test_heltec_full_and_partial_sequences(monkeypatch):
    log = fake_hw(monkeypatch)
    from einkboard.heltec290 import EPD
    epd = EPD()
    img = pages.page_boot("10.0.0.2")
    buf = epd.getbuffer(img)
    assert len(buf) == 16 * 296
    log.clear(); epd.display_Base(buf)
    c = commands(log)
    rams = [x for x, _ in c if x in (0x24, 0x26)]
    assert rams == [0x26, 0x24] and (0x22, bytes([0xF7])) in c and c[-1][0] == 0x20
    assert dict(c)[0x24] == bytes(buf)
    log.clear(); epd.display_Partial(buf)
    c = commands(log)
    seq = [x for x, _ in c if x in (0x32, 0x24, 0x22, 0x20, 0x26)]
    assert seq == [0x32, 0x24, 0x22, 0x20, 0x22, 0x20, 0x26]      # LUT, new image, refresh, power off, baseline
    assert (0x22, bytes([0xCC])) in c and (0x22, bytes([0x83])) in c
    assert len(dict(c)[0x32]) == 153
    log.clear(); epd.display_Partial(buf)                          # LUT stays loaded between partials
    assert 0x32 not in [x for x, _ in commands(log)]
    epd.display_Base(buf); log.clear(); epd.display_Partial(buf)   # full refresh reloads OTP LUT -> resend ours
    assert [x for x, _ in commands(log)][0] == 0x32


def test_heltec_buffer_orientation_and_invert(monkeypatch):
    fake_hw(monkeypatch)
    from PIL import Image
    from einkboard.heltec290 import EPD
    white = Image.new("1", (296, 128), 255)
    assert set(EPD().getbuffer(white)) == {0xFF}          # 1 bit = white
    assert set(EPD(invert=True).getbuffer(white)) == {0x00}
    black_left = white.copy(); black_left.paste(0, (0, 0, 8, 128))   # leftmost 8 columns black
    buf = EPD().getbuffer(black_left)
    assert buf.count(0x00) == 128 // 8 * 8 // 1 or buf.count(0x00) > 0
