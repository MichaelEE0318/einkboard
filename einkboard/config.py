"""Load config.yaml on top of built-in defaults (missing keys fall back to DEFAULTS)."""
import copy
import os

import yaml

DEFAULTS = {
    "location": {"name": "台北", "lat": 25.0330, "lon": 121.5654, "tz": "Asia/Taipei"},
    "pages": ["clock", "power", "market"],     # 輪播順序
    "rotate_minutes": 5,                       # 每頁停留幾分鐘
    "night": {"enabled": True, "start": "23:30", "end": "07:00"},   # 夜間停止刷新
    "weather": {"refresh_minutes": 15},
    "power": {"refresh_minutes": 10,
              "url": "https://service.taipower.com.tw/data/opendata/apply/file/d006020/001.json"},
    "market": {"stocks": ["2330", "0050", "2308"],   # 台股代號（上市上櫃都可以，最多 4 檔）
               "fx": ["USD", "JPY", "VND"],         # 對新台幣，最多 3 種
               "fx_refresh_minutes": 30},
    "eink": {"driver": "heltec290",           # Heltec 2.9"（用面板內建波形）；Waveshare 模組改 epd2in9_V2
             "full_refresh_every": 0,          # 每 N 次更新全刷一次；0 = 關閉（改用下面兩個設定）
             "full_refresh_minutes": 60,       # 每隔幾分鐘全刷一次清殘影（60 = 整點）
             "page_change_full": True,         # 換頁時全刷（閃一下、完全沒殘影）；false = 換頁也用局部刷新
             "rotate180": False,
             "invert": False,                  # 黑白顛倒時改 True
             "partial_mode": "lut_cc",
             "page_change_passes": 2,          # 換頁（大面積改變）時局部刷新幾次，越多殘影越少
             "page_change_whitewash": False,   # 換頁時先刷成全白再畫新頁（殘影最少，換頁稍慢）
             "lut_tp": 20, "lut_rp": 0,        # 局部波形強度（einktest --ghost 在 Heltec S75A 實測：設定 B）
             "has_reset_pin": False},          # Heltec 7 腳模組沒有 RST：不能進深度睡眠
    "web": {"enabled": True, "port": 8080},    # 設定網頁：http://<Pi 的 IP>:8080
    "cache_dir": "/var/lib/einkboard",         # 行情快取（重開機後保留走勢與最後報價）
    "simulate": False,                         # True：不碰硬體，畫面存成 PNG
    "sim_dir": "/tmp/einkboard",
}


def _merge(base, over):
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def load(path=None):
    cfg = copy.deepcopy(DEFAULTS)
    path = path or os.environ.get("EINKBOARD_CONFIG", "config.yaml")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            _merge(cfg, yaml.safe_load(f))
    return cfg
