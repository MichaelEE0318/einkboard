"""Panel check: full refresh test pattern, then 5 partial-refresh counters, then clear + sleep.

    /opt/einkboard-venv/bin/python -m tools.einktest
"""
import importlib
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from PIL import Image, ImageDraw  # noqa: E402

from einkboard import fonts  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
driver = args[0] if args else "epd2in9_V2"
print(f"einktest：使用 waveshare_epd.{driver}（BUSY=GPIO24 DC=GPIO25 CS=GPIO8；RST 沒接也可以）")
epd = importlib.import_module(f"waveshare_epd.{driver}").EPD()

img = Image.new("1", (296, 128), 255)
d = ImageDraw.Draw(img)
d.rectangle([0, 0, 295, 127], outline=0, width=3)
d.text((12, 10), "電子紙測試 OK", font=fonts.get(26, bold=True, cjk=True), fill=0)
for i in range(8):
    d.rectangle([12 + i * 34, 60, 40 + i * 34, 88], fill=0 if i % 2 == 0 else 255, outline=0)
print("1/3 全刷（會閃幾下，約 3 秒）…")
epd.init()
epd.display_Base(epd.getbuffer(img))

print("2/3 局部刷新 5 次（不應該閃）…")
for n in range(1, 6):
    d.rectangle([12, 96, 290, 122], fill=255)
    d.text((14, 96), f"partial {n}/5", font=fonts.get(20, bold=True), fill=0)
    epd.display_Partial(epd.getbuffer(img))
    time.sleep(1)

input("看到「電子紙測試 OK」和 partial 5/5 了嗎？按 Enter 清除畫面…")
print("3/3 清除畫面")
epd.init()
epd.Clear(0xFF)
if "--sleep" in sys.argv:      # 只有接了 RST 的模組才能深度睡眠，否則下次會叫不醒（要拔電重開）
    epd.sleep()
print("完成")
