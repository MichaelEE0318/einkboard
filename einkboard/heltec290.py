"""Heltec 2.9" e-paper (DEPG0290BxS800, SSD1680, 128x296) — minimal driver for Raspberry Pi.

Why not Waveshare's epd2in9_V2: it loads a full-refresh waveform (LUT) tuned for Waveshare glass,
which on the Heltec panel gives inverted colours and heavy ghosting.
Full refresh here uses the panel's built-in (OTP) waveform, like Heltec's own code. The panel has no
usable built-in partial waveform, so partial refresh loads the one GxEPD2 uses for this exact panel
(GxEPD2_290_BS, DEPG0290BS): 0x32 <lut_partial>, then 0x22=0xCC — ~0.75 s, no flashing.

Same interface as waveshare_epd.EPD: init / display_Base / display_Partial / getbuffer / Clear / sleep.
Wiring (BCM): DC=25, BUSY=24, CS=CE0 (spidev0.0), optional RST=17 (the 7-pin Heltec board has none).
"""
import logging
import time

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 128, 296            # panel memory: 128 px (16 bytes) wide, 296 rows


class EPD:
    width, height = WIDTH, HEIGHT
    UPDATE_FULL = 0xF7               # clock+analog on, load temp + OTP LUT, display, power off (flashes, ~4 s)
    UPDATE_PART = 0xCC               # display with the LUT already in the register (our partial LUT)
    POWER_OFF = 0x83                 # analog + clock off after a partial update

    # Partial-update waveform for DEPG0290BS (from GxEPD2_290_BS.cpp, GPL-3.0, Jean-Marc Zingg)
    LUT_PARTIAL = bytes([
        0x0, 0x40, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        0x80, 0x80, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        0x40, 0x40, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        0x0, 0x80, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        0x0A, 0x0, 0x0, 0x0, 0x0, 0x0, 0x2,
        0x1, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        0x1, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        0x0, 0x0, 0x0, 0x0, 0x0, 0x0, 0x0,
        0x22, 0x22, 0x22, 0x22, 0x22, 0x22, 0x0, 0x0, 0x0,
    ])

    # partial_mode — Heltec shipped two 2.9" generations with different controllers:
    #   "gx_cc"  : DEPG0290BxS800 (SSD1680): load GxEPD2's partial LUT, refresh 0xCC (~0.75 s, no flash)
    #   "otp_c4" : DEPG0290BxS75A (SSD1675A): Heltec's own method, built-in LUT, refresh 0xC4
    #   "otp_ff" : built-in "display mode 2" (works on both, but flashes like a full refresh)
    #   "lut_cc" / "lut_c4" / "lut_strong": S75A (SSD1675A) with a 70-byte partial LUT (no flashing)
    PARTIAL_MODES = ("gx_cc", "otp_c4", "otp_ff", "lut_cc", "lut_c4", "lut_strong")
    S75A_MODES = ("otp_c4", "lut_cc", "lut_c4", "lut_strong")

    @staticmethod
    def lut_1675(tp=0x0A, rp=0x00):
        """70-byte SSD1675A partial waveform: 5 VS rows x 7 groups + 7 groups x (TP A-D, RP).
        Same voltage pattern as GxEPD2's SSD1680 partial LUT (BW -> VSL, WB -> VSH1, BB/WW hold),
        in the SSD1675A layout (Waveshare 2.13" V2 uses this one). tp = frames, rp = extra repeats."""
        vs = [
            [0x00, 0x40, 0, 0, 0, 0, 0],   # LUT0 BB
            [0x80, 0x80, 0, 0, 0, 0, 0],   # LUT1 BW
            [0x40, 0x40, 0, 0, 0, 0, 0],   # LUT2 WB
            [0x00, 0x80, 0, 0, 0, 0, 0],   # LUT3 WW
            [0x00, 0x00, 0, 0, 0, 0, 0],   # LUT4 VCOM
        ]
        groups = [[tp, 0, 0, 0, rp]] + [[0, 0, 0, 0, 0]] * 6
        return bytes(sum(vs, []) + sum(groups, []))

    def __init__(self, dc=25, busy=24, rst=None, spi_bus=0, spi_dev=0, hz=2_000_000, invert=False,
                 partial_mode="gx_cc"):
        import spidev
        from gpiozero import DigitalInputDevice, DigitalOutputDevice
        self.dc = DigitalOutputDevice(dc)
        self.busy = DigitalInputDevice(busy, pull_up=False)
        self.rst = DigitalOutputDevice(rst, initial_value=True) if rst is not None else None
        self.spi = spidev.SpiDev()
        self.spi.open(spi_bus, spi_dev)
        self.spi.max_speed_hz = hz
        self.spi.mode = 0
        self.invert = invert
        if partial_mode not in self.PARTIAL_MODES:
            raise ValueError(f"partial_mode must be one of {self.PARTIAL_MODES}")
        self.partial_mode = partial_mode
        self.partial_lut = False        # a full refresh reloads the OTP LUT, so reload ours before the next partial
        self.last_busy = (False, 0.0)
        self.prev = None                # last image sent, for multi-pass partial updates

    # ---------------------------------------------------------------- low level
    def _cmd(self, c, *data):
        self.dc.off()
        self.spi.writebytes([c])
        if data:
            self._data(bytes(data))

    def _data(self, buf):
        self.dc.on()
        buf = bytes(buf)
        for i in range(0, len(buf), 4096):          # spidev's default transfer limit
            self.spi.writebytes2(buf[i:i + 4096])

    def _wait(self, min_time=0.0, timeout=15):
        """Wait for BUSY (high = busy). BUSY only rises a moment after a command, so give it 20 ms
        first; and if it never rose at all (BUSY not wired / not working), fall back to min_time
        so the next command can't cut the refresh short."""
        t0 = time.time()
        time.sleep(0.02)
        seen = False
        while self.busy.value:
            seen = True
            if time.time() - t0 > timeout:
                log.warning("e-paper BUSY stuck high for %ss (check BUSY wiring on Pin 18)", timeout)
                break
            time.sleep(0.005)
        if not seen and time.time() - t0 < min_time:
            time.sleep(min_time - (time.time() - t0))
        self.last_busy = (seen, time.time() - t0)   # for einktest diagnostics
        return seen

    def _hw_reset(self):
        if self.rst is not None:
            self.rst.off(); time.sleep(0.01)
            self.rst.on(); time.sleep(0.01)

    def _window_full(self):
        self._cmd(0x11, 0x03)                       # data entry: X+, Y+
        self._cmd(0x44, 0x00, WIDTH // 8 - 1)       # RAM X: 0..15 (bytes)
        self._cmd(0x45, 0x00, 0x00, (HEIGHT - 1) & 0xFF, (HEIGHT - 1) >> 8)   # RAM Y: 0..295
        self._cmd(0x4E, 0x00)
        self._cmd(0x4F, 0x00, 0x00)

    # ---------------------------------------------------------------- public API (Waveshare-compatible)
    def init(self):
        self._hw_reset()
        self._wait()
        self._cmd(0x12)                             # software reset (works without a RST pin)
        time.sleep(0.01)
        self._wait()
        if self.partial_mode in self.S75A_MODES:    # Heltec S75A init: analog / digital block control
            self._cmd(0x74, 0x54)
            self._cmd(0x7E, 0x3B)
        self._cmd(0x01, (HEIGHT - 1) & 0xFF, (HEIGHT - 1) >> 8, 0x00)   # gate lines 296
        self._cmd(0x3C, 0x05)                       # border: follow white
        self._cmd(0x21, 0x00, 0x80)                 # display update control: normal RAM, source S8–S167
        self._cmd(0x18, 0x80)                       # internal temperature sensor
        self._window_full()
        self._wait()
        if self.partial_mode == "otp_c4":           # load temperature + built-in LUT once (Heltec: 0x22=0xB1)
            self._cmd(0x22, 0xB1)
            self._cmd(0x20)
            self._wait(0.2)
        self.partial_lut = False
        return 0

    def getbuffer(self, image):
        """PIL image (landscape 296x128 or portrait 128x296, mode '1': 255 = white) -> panel bytes."""
        img = image.convert("1")
        if img.size == (HEIGHT, WIDTH):             # landscape -> rotate into panel orientation
            img = img.rotate(90, expand=True)
        if img.size != (WIDTH, HEIGHT):
            raise ValueError(f"image must be 296x128 or 128x296, got {img.size}")
        buf = bytearray(img.tobytes("raw"))         # 1 bit/px, MSB first, 1 = white
        if self.invert:
            buf = bytearray(b ^ 0xFF for b in buf)
        return buf

    def _write(self, ram, buf):
        self._window_full()
        self._cmd(ram)
        self._data(buf)

    def _update(self, mode, min_time):
        self._cmd(0x22, mode)
        self._cmd(0x20)
        return self._wait(min_time)

    def display_Base(self, buf):
        """Full refresh (flashes a few times, ~4 s): clears ghosting. Both RAMs get the image so the
        next partial update has a clean baseline."""
        self._write(0x26, buf)
        self._write(0x24, buf)
        self._update(self.UPDATE_FULL, 4.0)
        self.partial_lut = False
        self.prev = bytes(buf)

    def display(self, buf):
        self.display_Base(buf)

    def display_Partial(self, buf, passes=1):
        """Partial refresh (no flash): the panel drives only pixels that differ between 0x24 (new) and
        0x26 (previous). passes > 1 re-drives the same changed pixels again (old image put back into
        0x26), which pushes out the ghost of a whole-page change without a full, flashing refresh."""
        prev = self.prev
        for i in range(max(1, passes)):
            if i and prev is not None:
                self._write(0x26, prev)
            self._partial_once(buf)
        self.prev = bytes(buf)

    def _partial_once(self, buf):
        if self.partial_mode == "otp_c4":
            self._write(0x24, buf)
            self._update(0xC4, 0.8)                 # Heltec HalLcd_Partial_Update()
            self._write(0x26, buf)
            return
        if self.partial_mode in ("lut_cc", "lut_c4", "lut_strong"):
            if not self.partial_lut:
                lut = self.lut_1675(0x10, 0x01) if self.partial_mode == "lut_strong" else self.lut_1675()
                self._cmd(0x32, *lut)
                self.partial_lut = True
            self._write(0x24, buf)
            self._update(0xC4 if self.partial_mode == "lut_c4" else 0xCC, 0.4)
            busy = self.last_busy
            self._cmd(0x22, self.POWER_OFF)
            self._cmd(0x20)
            self._wait(0.15)
            self.last_busy = busy
            self._write(0x26, buf)
            return
        if self.partial_mode == "otp_ff":
            self._write(0x24, buf)
            self._update(0xFF, 2.0)
            self._write(0x26, buf)
            return
        if not self.partial_lut:
            self._cmd(0x32, *self.LUT_PARTIAL)
            self.partial_lut = True
        self._write(0x24, buf)
        self._update(self.UPDATE_PART, 0.8)
        busy = self.last_busy
        self._cmd(0x22, self.POWER_OFF)             # don't leave the high voltage on between minutes
        self._cmd(0x20)
        self._wait(0.15)
        self.last_busy = busy
        self._write(0x26, buf)                      # becomes the "previous" image for the next partial

    def Clear(self, color=0xFF):
        buf = bytes([color]) * (WIDTH // 8 * HEIGHT)
        self.display_Base(buf)

    def sleep(self):
        """Deep sleep. Only call when a RST pin is wired — without it the panel can't be woken."""
        self._cmd(0x10, 0x01)
        time.sleep(0.05)

    def close(self):
        try:
            self.spi.close()
        finally:
            for dev in (self.dc, self.busy, self.rst):
                if dev is not None:
                    dev.close()
