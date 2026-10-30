"""E-ink output. Partial refresh most of the time; a full refresh every N updates clears ghosting."""
import importlib
import logging
import os

log = logging.getLogger(__name__)


def make_epd(e):
    """eink.driver: 'heltec290' (Heltec 2.9", built-in waveform) or any waveshare_epd module name."""
    if e["driver"] == "heltec290":
        from .heltec290 import EPD
        return EPD(rst=17 if e.get("has_reset_pin") else None, invert=e.get("invert", False),
                   partial_mode=e.get("partial_mode", "gx_cc"))
    return importlib.import_module(f"waveshare_epd.{e['driver']}").EPD()


class Eink:
    def __init__(self, cfg):
        e = cfg["eink"]
        self.epd = make_epd(e)
        self.full_every = e["full_refresh_every"]
        self.rotate = e["rotate180"]
        # 沒有 RST 腳的模組（例如 Heltec 7 腳版）：深度睡眠只能靠硬體重置喚醒，所以永遠不進深度睡眠。
        # 電子紙不通電也會保留畫面，控制器待機電流只有微安級，差別不大。
        self.can_sleep = e.get("has_reset_pin", True)
        self.page_passes = e.get("page_change_passes", 2)
        self.count = 0
        self.asleep = True
        self.last = None

    def show(self, img, force_full=False):
        if self.rotate:
            img = img.rotate(180)
        if img.tobytes() == (self.last.tobytes() if self.last else None) and not force_full:
            return False                               # nothing changed: don't touch the panel
        buf = self.epd.getbuffer(img)                 # driver rotates landscape 296x128
        if force_full or self.asleep or (self.full_every and self.count % self.full_every == 0):
            self.epd.init()
            self.epd.display_Base(buf)                # full refresh (flashes once)
            self.asleep = False
        else:
            passes = 1
            if self.last is not None and self.page_passes > 1:
                old, new = self.last.tobytes(), img.tobytes()
                changed = sum(a != b for a, b in zip(old, new)) / max(1, len(new))
                if changed > 0.15:                     # whole-page change: drive twice to clear the ghost
                    passes = self.page_passes
            try:
                self.epd.display_Partial(buf, passes=passes)
            except TypeError:                          # Waveshare drivers take a single argument
                self.epd.display_Partial(buf)
        self.count += 1
        self.last = img
        return True

    def sleep(self):
        """Deep sleep keeps the current picture with zero power; next show() does a full refresh.
        Without a RST pin we only stop refreshing (the panel keeps the picture anyway)."""
        if not self.asleep:
            if self.can_sleep:
                self.epd.sleep()
            self.asleep = True

    def clear(self):
        self.epd.init()
        self.epd.Clear(0xFF)
        if self.can_sleep:
            self.epd.sleep()
        self.asleep = True


class SimDisplay:
    """No hardware: writes /tmp/einkboard/eink.png (and keeps count of full/partial refreshes)."""

    def __init__(self, cfg):
        self.dir = cfg["sim_dir"]
        os.makedirs(self.dir, exist_ok=True)
        self.full_every = cfg["eink"]["full_refresh_every"]
        self.count, self.fulls, self.asleep, self.last = 0, 0, True, None

    def show(self, img, force_full=False):
        if self.last is not None and img.tobytes() == self.last.tobytes() and not force_full:
            return False
        if force_full or self.asleep or (self.full_every and self.count % self.full_every == 0):
            self.fulls += 1
            self.asleep = False
        self.count += 1
        self.last = img
        img.save(os.path.join(self.dir, "eink.png"))
        return True

    def sleep(self):
        self.asleep = True

    def clear(self):
        self.asleep = True
