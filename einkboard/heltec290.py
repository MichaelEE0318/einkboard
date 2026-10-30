"""Heltec 2.9" e-paper (DEPG0290BxS800, SSD1680, 128x296) — minimal driver for Raspberry Pi.

Why not Waveshare's epd2in9_V2: it loads a custom waveform (LUT) tuned for Waveshare glass,
which on the Heltec panel gives inverted colours, heavy ghosting and broken partial refresh.
Heltec's own code only soft-resets and uses the panel's built-in (OTP) waveform; so does this.

Same interface as waveshare_epd.EPD: init / display_Base / display_Partial / getbuffer / Clear / sleep.
Wiring (BCM): DC=25, BUSY=24, CS=CE0 (spidev0.0), optional RST=17 (the 7-pin Heltec board has none).
"""
import logging
import time

log = logging.getLogger(__name__)

WIDTH, HEIGHT = 128, 296            # panel memory: 128 px (16 bytes) wide, 296 rows


class EPD:
    width, height = WIDTH, HEIGHT
    UPDATE_FULL = 0xF7               # clock+analog on, load temp, display mode 1 (full waveform), power off
    UPDATE_PART = 0xFF               # same, display mode 2 (partial waveform from OTP)

    def __init__(self, dc=25, busy=24, rst=None, spi_bus=0, spi_dev=0, hz=2_000_000, invert=False):
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

    def _wait(self, timeout=15):
        t0 = time.time()
        while self.busy.value:                      # SSD1680: BUSY high = busy
            if time.time() - t0 > timeout:
                log.warning("e-paper BUSY stuck high for %ss (check BUSY wiring on Pin 18)", timeout)
                return False
            time.sleep(0.01)
        return True

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
        self._cmd(0x01, (HEIGHT - 1) & 0xFF, (HEIGHT - 1) >> 8, 0x00)   # gate lines 296
        self._cmd(0x3C, 0x05)                       # border: follow white
        self._cmd(0x21, 0x00, 0x80)                 # display update control: normal RAM, source S8–S167
        self._cmd(0x18, 0x80)                       # internal temperature sensor
        self._window_full()
        self._wait()
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

    def _update(self, mode):
        self._cmd(0x22, mode)
        self._cmd(0x20)
        self._wait()

    def display_Base(self, buf):
        """Full refresh (flashes): new image into both RAMs so the next partial has a clean baseline."""
        self._write(0x24, buf)
        self._write(0x26, buf)
        self._update(self.UPDATE_FULL)

    def display(self, buf):
        self.display_Base(buf)

    def display_Partial(self, buf):
        """Partial refresh (no flash): panel diffs 0x24 (new) against 0x26 (previous)."""
        self._write(0x24, buf)
        self._update(self.UPDATE_PART)
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
