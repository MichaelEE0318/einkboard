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

if "--modes" in sys.argv:
    # 逐一試三種局部刷新方式，問使用者哪個有效，最後給出要寫進 config.yaml 的設定
    from einkboard.heltec290 import EPD as HEPD
    big = fonts.get(30, bold=True, cjk=True)
    small = fonts.get(16, bold=True, cjk=True)
    results = {}
    for mode in HEPD.PARTIAL_MODES:
        print(f"\n=== 模式 {mode} ===")
        epd = make_epd(dict(ecfg, partial_mode=mode))
        img = Image.new("1", (296, 128), 255)
        d = ImageDraw.Draw(img)
        d.rectangle([0, 0, 295, 127], outline=0, width=3)
        d.text((12, 8), f"模式 {mode}", font=small, fill=0)
        epd.init()
        epd.display_Base(epd.getbuffer(img))
        print("   已全刷（這次會閃，正常）。接下來 3 次局部刷新：")
        for n in range(1, 4):
            d.rectangle([12, 40, 290, 120], fill=255)
            d.text((14, 48), f"第 {n} 次 ▶ {'●' * n}", font=big, fill=0)
            t = time.time()
            epd.display_Partial(epd.getbuffer(img))
            print(f"   第 {n} 次：{time.time() - t:.2f}s")
            time.sleep(0.8)
        changed = input("   螢幕上的「第 1→3 次」有更新嗎？(y/n) ").strip().lower().startswith("y")
        flashed = changed and input("   更新時有黑白閃爍嗎？(y/n) ").strip().lower().startswith("y")
        results[mode] = (changed, flashed)
        if hasattr(epd, "close"):
            epd.close()
    good = [m for m, (c, f) in results.items() if c and not f] or [m for m, (c, f) in results.items() if c]
    print("\n結果：", {m: ("有更新" if c else "沒反應") + ("、會閃" if f else "") for m, (c, f) in results.items()})
    if good:
        print(f"建議使用 {good[0]}，執行：")
        print(f"  grep -q partial_mode config.yaml && sed -i 's/partial_mode: .*/partial_mode: {good[0]}/' config.yaml "
              f"|| sed -i '/^eink:/a\\  partial_mode: {good[0]}' config.yaml")
        print("  sudo systemctl start einkboard")
    else:
        print("三種都沒反應：請拍螢幕照片和排線上的 DEPG0290… 型號給 Claude。")
    sys.exit(0)

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
