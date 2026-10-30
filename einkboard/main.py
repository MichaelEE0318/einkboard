"""Main loop: once a minute (on the minute) pick the page, render, push to the panel."""
import datetime as dt
import json
import logging
import os
import queue
import signal
import socket
import sys
import threading
import time
from zoneinfo import ZoneInfo

from . import config, pages
from .sources import Market, Power, Weather

log = logging.getLogger("einkboard")


def lan_ip():
    """Address of the interface used for outbound traffic (gethostbyname(hostname) gives 127.0.1.1 on Debian).
    UDP connect sends no packet; it only asks the kernel which source address it would use."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return ""


def read_wifi(path="/proc/net/wireless", iface="wlan0"):
    """(connected, link quality 0-70) from the kernel's wireless stats; no network traffic."""
    try:
        with open(path, encoding="ascii") as f:
            for line in f:
                if line.strip().startswith(iface + ":"):
                    q = float(line.split()[2].rstrip("."))
                    return q > 0, q
    except (OSError, ValueError, IndexError):
        pass
    return False, 0.0


class WifiState:
    """Signal -> 1..3 bars with hysteresis, so a signal hovering on a boundary doesn't redraw the panel."""
    EDGES = (25, 45)                    # quality (out of 70): <25 one bar, 25-45 two, >45 three
    HYST = 4

    def __init__(self):
        self.bars = 3

    def update(self, quality):
        lo, hi = self.EDGES
        b = self.bars
        if b == 1 and quality >= lo + self.HYST:
            b = 2
        if b == 2 and quality >= hi + self.HYST:
            b = 3
        if b == 3 and quality < hi - self.HYST:
            b = 2
        if b == 2 and quality < lo - self.HYST:
            b = 1
        self.bars = b
        return b


def _hm(s):
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def is_night(now, night):
    if not night.get("enabled"):
        return False
    t, a, b = now.hour * 60 + now.minute, _hm(night["start"]), _hm(night["end"])
    return a <= t or t < b if a > b else a <= t < b


def active_pages(cfg):
    names = [p for p in cfg["pages"] if p in ("clock", "power", "market")]
    return names or ["clock"]


def page_at(now, names, rotate_minutes):
    slot = (now.hour * 60 + now.minute) // max(1, rotate_minutes)
    return names[slot % len(names)]


class Board:
    def __init__(self, cfg, display=None):
        self.cfg = cfg
        self.tz = ZoneInfo(cfg["location"]["tz"])
        self.weather = Weather(cfg)
        self.power = Power(cfg) if "power" in cfg["pages"] else None
        self.market = Market(cfg) if "market" in cfg["pages"] else None
        self.display = display or self._make_display()
        self.pages = active_pages(cfg)
        self.night_shown = False
        self.last_page = None
        # live control from the web page
        self.commands = queue.Queue()
        self.wake = threading.Event()
        self.offset = 0                 # 下一頁 / 上一頁 shift the rotation
        self.paused = None              # page name while rotation is paused
        self.force_full_next = False
        self.last_img = None
        self.current = None
        self.started = time.time()
        self.restart_requested = False
        self.ip = ""
        self.wifi = WifiState()
        self.wifi_reader = read_wifi
        self.sign_path = os.path.join(cfg["cache_dir"], "sign.json")
        self.sign = self._load_sign()   # 狀態牌（出差中 / 下班了 / 暫離），撐過重開機

    def _make_display(self):
        from .display import Eink, SimDisplay
        if self.cfg["simulate"]:
            return SimDisplay(self.cfg)
        return Eink(self.cfg)

    def now(self):
        return dt.datetime.now(self.tz)

    def page_name(self, now):
        if self.paused:
            return self.paused
        slot = (now.hour * 60 + now.minute) // max(1, self.cfg["rotate_minutes"])
        return self.pages[(slot + self.offset) % len(self.pages)]

    def render_page(self, name, now):
        if name == "power":
            return pages.page_power(now, self.power.data, self.power.stale and self.power.data is not None)
        if name == "market":
            return pages.page_market(now, self.market.data, self.market.stale and self.market.data is not None)
        return pages.page_clock(now, self.weather.data, self.cfg["location"]["name"],
                                self.weather.stale and self.weather.data is not None)

    # ------------------------------------------------------------------ status sign
    def _load_sign(self):
        try:
            with open(self.sign_path, encoding="utf-8") as f:
                sign = json.load(f)
            return sign if sign.get("kind") in pages.STATUS_KINDS else None
        except (OSError, ValueError, AttributeError):
            return None

    def _save_sign(self):
        try:
            os.makedirs(os.path.dirname(self.sign_path), exist_ok=True)
            if self.sign:
                tmp = self.sign_path + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self.sign, f, ensure_ascii=False)
                os.replace(tmp, self.sign_path)
            elif os.path.exists(self.sign_path):
                os.remove(self.sign_path)
        except OSError as e:
            log.warning("sign save: %s", e)

    def render(self, now):
        if self.sign:                                 # a status sign beats rotation and night mode
            return "sign:" + self.sign["kind"], pages.page_status(now, self.sign, self.weather.data)
        if is_night(now, self.cfg["night"]) and not self.paused:
            n = self.cfg["night"]
            if n.get("page") == "off":                 # 夜間改顯示「下班了」
                info = {"kind": "off", "back": n.get("off_back") or "09:00", "note": n.get("off_note") or ""}
                return "night", pages.page_off(now, info, self.weather.data)
            return "night", pages.page_night(now, self.weather.data)
        name = self.page_name(now)
        return name, self.render_page(name, now)

    def internet_ok(self, window=20 * 60):
        """Any data source succeeded recently -> the internet works (Wi-Fi alone isn't enough)."""
        t = max((getattr(s, "updated", 0) or 0) for s in self.sources().values() if s) if any(self.sources().values()) else 0
        return time.time() - t < window or time.time() - self.started < window

    def decorate(self, img):
        connected, quality = self.wifi_reader()
        bars = self.wifi.update(quality) if connected else self.wifi.bars
        self.net = {"connected": connected, "bars": bars, "internet": self.internet_ok(), "quality": quality}
        return pages.draw_wifi(img, bars, connected, self.net["internet"])

    def tick(self):
        now = self.now()
        name, img = self.render(now)
        img = self.decorate(img)
        self.last_img, self.current = img, name
        if name == "night":
            if not self.night_shown:                  # draw once, then let the panel sleep all night
                self.display.show(img, force_full=True)
                self.display.sleep()
                self.night_shown = True
            return name
        self.night_shown = False
        e = self.cfg["eink"]
        every = max(1, int(e.get("full_refresh_minutes", 60)))
        clean = (now.hour * 60 + now.minute) % every == 0          # periodic clean-up of partial-refresh ghosting
        turn = e.get("page_change_full", True) and self.last_page not in (None, name)
        self.display.show(img, force_full=clean or turn or self.force_full_next)
        self.force_full_next = False
        self.last_page = name
        return name

    # ------------------------------------------------------------------ live control (web page)
    def sources(self):
        return {"weather": self.weather, "power": self.power, "market": self.market}

    def command(self, action, arg=None):
        """Called from the web thread: queue it and wake the main loop."""
        self.commands.put((action, arg))
        self.wake.set()

    def handle(self, action, arg=None):
        """Runs on the main loop thread (the only thread that touches the panel)."""
        now = self.now()
        if action == "sign":
            self.sign = dict(arg, since=now.isoformat(timespec="minutes"))
            self._save_sign()
            return True
        if action == "rotate":                       # 回到輪播
            self.sign, self.paused = None, None
            self._save_sign()
            return True
        if action in ("next", "prev", "goto") and self.sign:   # flipping pages also takes the sign down
            self.sign = None
            self._save_sign()
        if action in ("next", "prev"):
            if self.paused:
                i = self.pages.index(self.paused) if self.paused in self.pages else 0
                self.paused = self.pages[(i + (1 if action == "next" else -1)) % len(self.pages)]
            else:
                self.offset += 1 if action == "next" else -1
        elif action == "goto" and arg in self.pages:
            if self.paused:
                self.paused = arg
            else:
                self.offset += (self.pages.index(arg) - self.pages.index(self.page_name(now))) % len(self.pages)
        elif action == "pause":
            self.paused = self.page_name(now) if not self.paused else None
        elif action == "full":
            self.force_full_next = True
        elif action == "refetch":
            for s in self.sources().values():
                if s:
                    threading.Thread(target=s.poll_once, daemon=True).start()
            return False                              # redraw happens once data arrives (next minute)
        elif action == "restart":
            self.restart_requested = True
            return False
        return True

    def status(self):
        now = self.now()
        src = {}
        for k, s in self.sources().items():
            if s:
                src[k] = {"ok": s.data is not None and not s.stale, "updated": getattr(s, "updated", 0),
                          "error": getattr(s, "error", None)}
        return {"page": self.current, "pages": self.pages, "paused": bool(self.paused), "ip": self.ip,
                "sign": self.sign, "net": getattr(self, "net", None),
                "port": self.cfg["web"]["port"], "uptime": int(time.time() - self.started),
                "night": is_night(now, self.cfg["night"]), "now": now.strftime("%H:%M"), "sources": src}

    def _drain(self):
        redraw = False
        while True:
            try:
                action, arg = self.commands.get_nowait()
            except queue.Empty:
                return redraw
            try:
                redraw |= bool(self.handle(action, arg))
            except Exception:
                log.exception("command %s failed", action)

    def run(self):
        self.ip = lan_ip()
        if self.cfg["web"]["enabled"]:
            from .web import start_web
            start_web(self, self.cfg["web"]["port"])
        self.display.show(pages.page_boot(self.ip, self.cfg["web"]["port"] if self.cfg["web"]["enabled"] else None),
                          force_full=True)
        self.last_page = "boot"                       # boot -> first page counts as a page change (full refresh)
        for s in self.sources().values():
            if s:
                s.start()
        time.sleep(8)                                 # give the first fetches a moment
        stop = []
        signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
        while not stop and not self.restart_requested:
            try:
                self._drain()
                self.tick()
            except Exception:
                log.exception("tick failed")
            # sleep until just after the next minute boundary — or until the web page sends a command
            self.wake.wait(60 - time.time() % 60 + 0.5)
            self.wake.clear()
        self.display.sleep()
        return 75 if self.restart_requested else 0


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    code = Board(config.load()).run()
    # exit code 75 = "restart me": systemd's Restart=on-failure brings the service back with the new config
    sys.stdout.flush()
    os._exit(code)


if __name__ == "__main__":
    main()
