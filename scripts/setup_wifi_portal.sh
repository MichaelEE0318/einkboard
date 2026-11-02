#!/usr/bin/env bash
# Wi-Fi 開箱設定（熱點＋設定頁）需要的系統設定。已經裝好的 Pi 跑一次：
#   cd ~/einkboard && git pull && sudo ./scripts/setup_wifi_portal.sh
set -euo pipefail
APP_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV=/opt/einkboard-venv
RUN_USER="${SUDO_USER:-$(logname 2>/dev/null || echo pi)}"
[ "$(id -u)" -eq 0 ] || { echo "請用 sudo 執行"; exit 1; }

systemctl is-active --quiet NetworkManager || { echo "這台沒有用 NetworkManager（需要 Raspberry Pi OS Bookworm 以後）"; exit 1; }

echo "==> dnsmasq（熱點發 IP 用）"
dpkg -s dnsmasq-base >/dev/null 2>&1 || apt-get install -y dnsmasq-base

echo "==> 允許 $RUN_USER 控制 NetworkManager（polkit）"
cat > /etc/polkit-1/rules.d/50-einkboard-network.rules <<RULE
// einkboard：讓看板服務能掃描 Wi-Fi、開熱點、存 Wi-Fi 設定
polkit.addRule(function(action, subject) {
    if (action.id.indexOf("org.freedesktop.NetworkManager.") == 0 && subject.user == "$RUN_USER") {
        return polkit.Result.YES;
    }
});
RULE

echo "==> 熱點模式：所有網址都導向看板（手機才會自動跳出設定頁）"
mkdir -p /etc/NetworkManager/dnsmasq-shared.d
cat > /etc/NetworkManager/dnsmasq-shared.d/einkboard-captive.conf <<CONF
# 只在 NetworkManager 的熱點（shared）模式生效
address=/#/10.42.0.1
CONF

echo "==> Python 套件（QR Code）"
"$VENV/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> 更新 systemd 服務（port 80、不等網路）"
sed -e "s#@APP_DIR@#$APP_DIR#g" -e "s#@VENV@#$VENV#g" -e "s#@USER@#$RUN_USER#g" \
    "$APP_DIR/scripts/einkboard.service" > /etc/systemd/system/einkboard.service
systemctl daemon-reload

echo "==> 檢查權限"
if sudo -u "$RUN_USER" nmcli -t -f SSID device wifi list --rescan no >/dev/null 2>&1; then
    echo "   OK：$RUN_USER 可以使用 nmcli"
else
    echo "   警告：$RUN_USER 還不能使用 nmcli，請重開機後再試"
fi
echo
echo "完成。重新啟動服務：sudo systemctl restart einkboard"
