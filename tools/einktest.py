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

from einkboard import config  # noqa: E402
from einkboard.display import make_epd  # noqa: E402

ecfg = config.load(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config.yaml"))["eink"]
args = [a for a in sys.argv[1:] if not a.startswith("--")]
if args:
    ecfg["driver"] = args[0]                   # e.g. python -m tools.einktest epd2in9_V2
if "--invert" in sys.argv:
    ecfg["invert"] = True
print(f"einktest：驅動 {ecfg['driver']}（BUSY=GPIO24 DC=GPIO25 CS=GPIO8；RST 沒接也可以）")
import subprocess  # noqa: E402

for svc in ("einkboard", "airstation"):   # 背景服務會佔住 GPIO，測試前先提醒
    if subprocess.run(["systemctl", "is-active", "--quiet", svc]).returncode == 0:
        print(f"⚠ {svc} 服務正在執行，會佔住螢幕腳位。請先執行：sudo systemctl stop {svc}")
        sys.exit(1)
try:
    epd = make_epd(ecfg)
except Exception as e:  # lgpio.error: 'GPIO busy'
    if "busy" in str(e).lower():
        print("GPIO 被其他程式佔用。用這行找出是誰：sudo lsof /dev/gpiochip* 或 systemctl list-units --state=running")
        sys.exit(1)
    raise

img = Image.new("1", (296, 128), 255)
d = ImageDraw.Draw(img)
d.rectangle([0, 0, 295, 127], outline=0, width=3)
d.text((12, 10), "電子紙測試 OK", font=fonts.get(26, bold=True, cjk=True), fill=0)
for i in range(8):
    d.rectangle([12 + i * 34, 60, 40 + i * 34, 88], fill=0 if i % 2 == 0 else 255, outline=0)
print("1/3 全刷（會閃幾下，約 3 秒）…")
epd.init()
t = time.time()
epd.display_Base(epd.getbuffer(img))
diag = getattr(epd, "last_busy", None)
if diag:
    print(f"   全刷 {time.time() - t:.1f}s，BUSY {'有拉高 ✓' if diag[0] else '沒反應 ✗（檢查 Pin 18）'}")

print("2/3 局部刷新 5 次（不應該閃）…")
for n in range(1, 6):
    d.rectangle([12, 96, 290, 122], fill=255)
    d.text((14, 96), f"partial {n}/5", font=fonts.get(20, bold=True), fill=0)
    t = time.time()
    epd.display_Partial(epd.getbuffer(img))
    diag = getattr(epd, "last_busy", None)
    if diag:
        print(f"   局部 {n}/5：{time.time() - t:.2f}s，BUSY {'有拉高 ✓' if diag[0] else '沒反應 ✗'}")
    time.sleep(1)

input("看到白底黑字的「電子紙測試 OK」和 partial 5/5 了嗎？（黑白顛倒就加 --invert 再跑一次）按 Enter 清除畫面…")
print("3/3 清除畫面")
epd.init()
epd.Clear(0xFF)
if "--sleep" in sys.argv and ecfg.get("has_reset_pin"):      # 只有接了 RST 的模組才能深度睡眠，否則下次會叫不醒（要拔電重開）
    epd.sleep()
print("完成")
