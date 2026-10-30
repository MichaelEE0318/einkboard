#!/usr/bin/env bash
# Raspberry Pi OS (Trixie / Bookworm) 上執行：sudo ./scripts/install.sh
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV=/opt/einkboard-venv
RUN_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
[ "$(id -u)" -eq 0 ] || { echo "請用 sudo 執行"; exit 1; }

echo "==> 系統套件"
apt-get update
apt-get install -y python3-venv python3-dev git fonts-noto-cjk fonts-dejavu-core \
                   python3-gpiozero python3-lgpio python3-spidev libopenjp2-7

echo "==> 開啟 SPI"
raspi-config nonint do_spi 0
usermod -aG gpio,spi "$RUN_USER"

echo "==> Python 虛擬環境"
python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> Waveshare 電子紙驅動"
[ -d /opt/e-Paper ] || git clone --depth 1 https://github.com/waveshare/e-Paper.git /opt/e-Paper
"$VENV/bin/pip" install /opt/e-Paper/RaspberryPi_JetsonNano/python

echo "==> 設定檔"
[ -f "$APP_DIR/config.yaml" ] || cp "$APP_DIR/config.example.yaml" "$APP_DIR/config.yaml"
chown -R "$RUN_USER":"$RUN_USER" "$APP_DIR"

mkdir -p /var/lib/einkboard && chown "$RUN_USER":"$RUN_USER" /var/lib/einkboard

echo "==> systemd 服務"
sed -e "s#@APP_DIR@#$APP_DIR#g" -e "s#@VENV@#$VENV#g" -e "s#@USER@#$RUN_USER#g" \
    "$APP_DIR/scripts/einkboard.service" > /etc/systemd/system/einkboard.service
systemctl daemon-reload
systemctl enable einkboard.service

echo
echo "完成！接下來："
echo "（服務已設為開機自動啟動。重開機後要跑測試，請先 sudo systemctl stop einkboard）"
echo "  1) 測螢幕：   sudo systemctl stop einkboard; $VENV/bin/python -m tools.einktest"
echo "  2) 改設定：   nano $APP_DIR/config.yaml   （座標、行事曆網址）"
echo "  3) 啟動：     sudo systemctl start einkboard"
echo "  4) 看紀錄：   journalctl -u einkboard -f"
