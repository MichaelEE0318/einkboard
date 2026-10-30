"""Main loop: once a minute (on the minute) pick the page, render, push to the panel."""
import datetime as dt
import logging
import signal
import socket
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

    def _make_display(self):
        from .display import Eink, SimDisplay
        if self.cfg["simulate"]:
            return SimDisplay(self.cfg)
        return Eink(self.cfg)

    def now(self):
        return dt.datetime.now(self.tz)

    def render(self, now):
        if is_night(now, self.cfg["night"]):
            return "night", pages.page_night(now, self.weather.data)
        name = page_at(now, self.pages, self.cfg["rotate_minutes"])
        if name == "power":
            return name, pages.page_power(now, self.power.data, self.power.stale and self.power.data is not None)
        if name == "market":
            return name, pages.page_market(now, self.market.data, self.market.stale and self.market.data is not None)
        return "clock", pages.page_clock(now, self.weather.data, self.cfg["location"]["name"],
                                         self.weather.stale and self.weather.data is not None)

    def tick(self):
        now = self.now()
        name, img = self.render(now)
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
        self.display.show(img, force_full=clean or turn)
        self.last_page = name
        return name

    def run(self):
        ip = lan_ip()
        self.display.show(pages.page_boot(ip), force_full=True)
        for s in (self.weather, self.power, self.market):
            if s:
                s.start()
        time.sleep(8)                                 # give the first fetches a moment
        stop = []
        signal.signal(signal.SIGTERM, lambda *_: stop.append(1))
        while not stop:
            try:
                self.tick()
            except Exception:
                log.exception("tick failed")
            time.sleep(60 - time.time() % 60 + 0.5)    # wake just after each minute boundary
        self.display.sleep()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    Board(config.load()).run()


if __name__ == "__main__":
    main()
