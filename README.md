# einkboard — 電子紙桌上看板

Raspberry Pi Zero 2W ＋ 2.9" 電子紙（296×128，epd2in9_V2）。三頁輪播，每頁停 2 分鐘：

1. **時鐘＋天氣**：Open-Meteo，不需要 API key；包含未來 12 小時、每 3 小時一格的預報
2. **台電供電資訊**：台電開放資料「今日電力資訊」，內容有今日尖峰備轉容量率、供電燈號、目前用電與使用率
3. **台股．匯率**：上市、上櫃、ETF 都可以；漲是實心框、跌是空心框。股價有三層退路：
   - **證交所 MIS 即時**：盤中每分鐘更新，並記下今天的走勢畫成小線圖
   - **證交所／櫃買 OpenAPI 官方收盤價**：MIS 抓不到時使用，右上角顯示「收盤 10/28」
   - **硬碟快取**：網路全斷時顯示最後一筆並標「離線」；重開機後當天的走勢也還在

   匯率用 open.er-api 的每日參考匯率。臺銀的牌告網頁有防爬蟲驗證，Pi 抓不到，所以不使用

夜間（預設 23:30–07:00）會顯示「晚安」和明天天氣，然後讓螢幕休眠、停止刷新。

## 接線（電子紙 → Pi Zero 2W）

| 電子紙 | Pi 腳位 | GPIO |
|---|---|---|
| VCC | Pin 1 | 3.3V |
| GND | Pin 6 | GND |
| D/C | Pin 22 | GPIO25 |
| SDI | Pin 19 | GPIO10 (MOSI) |
| CS | Pin 24 | GPIO8 (CE0) |
| CLK | Pin 23 | GPIO11 (SCLK) |
| BUSY | Pin 18 | GPIO24 |

上表是 Heltec 2.9" 7 腳模組（絲印 VCC GND D/C SDI CS CLK BUSY）。SDI＝DIN、D/C＝DC。
這片沒有 RST，`config.yaml` 要保持 `eink.has_reset_pin: false`（預設值），程式就不會讓它進入叫不醒的深度睡眠。
如果改用 Waveshare 8/9 腳模組：RST 接 Pin 11（GPIO17）、PWR 接 Pin 12（GPIO18），並把 has_reset_pin 改 true。

## 安裝

```bash
scp einkboard.zip <你的帳號>@<pi>.local:~      # 在 Mac 執行
unzip einkboard.zip && cd einkboard
sudo ./scripts/install.sh
/opt/einkboard-venv/bin/python -m tools.einktest    # 測螢幕
nano config.yaml                                    # 填座標、股票代號、幣別
sudo systemctl start einkboard
journalctl -u einkboard -f
```

## Wi-Fi 圖示

每一頁右上角都有 Wi-Fi 圖示：扇形 1～3 格是訊號強弱；旁邊出現「!」表示有連上 Wi-Fi 但超過 20 分鐘沒抓到任何資料（連不上網際網路）；
畫上斜線表示沒連上 Wi-Fi。訊號格數有遲滯，訊號在邊界附近跳動時不會讓電子紙一直刷新。

## 設定網頁

開機畫面會顯示網址，例如 `http://172.20.10.3:8080`；手機或電腦跟 Pi 連同一個網路，用瀏覽器打開就能操作：

- **即時控制**：上一頁／下一頁、直接跳到某一頁、暫停輪播、立即全刷（清殘影）、重抓資料
- **預覽**：網頁上顯示電子紙目前的畫面
- **狀態**：各資料來源最後更新時間與錯誤原因
- **設定**：輪播頁面與順序、停留分鐘、股票代號、幣別、天氣地點、夜間時段、刷新方式。按「儲存並重新啟動」會寫回 `config.yaml`（舊版留在 `config.yaml.bak`），程式約 15 秒後自動重啟套用

**狀態牌**：在網頁上選「出差中」「下班了」「暫離」並填欄位，看板會一直停在那一頁（晚上也不換成晚安、重開機也還在），按「回到輪播」才恢復。
- 出差中：地點、回程日（自動倒數「還有 N 天」）、聯絡方式
- 下班了：「明天 / 下週一 09:00 見」＋明天天氣
- 暫離：預計回來時間＋留言

網頁沒有密碼，同一個網路裡的人都打得開。不需要的話在 `config.yaml` 設 `web: {enabled: false}`。

## 不接螢幕先看畫面

```bash
python -m tools.preview           # 用範例資料輸出 preview_pages.png
python -m tools.preview --live    # 抓真實天氣與台電資料
```

## 刷新策略

- 每分鐘局部刷新一次（不會閃）；畫面內容沒變就不刷
- 每 30 次局部刷新、每個整點各全刷一次，用來清殘影
- 網路斷線時保留最後一次的資料，角落會標「離線」；開機時若網路還沒好，每 60 秒重試
- 行情快取每 5 分鐘寫一次 `/var/lib/einkboard/market.json`（先寫暫存檔再改名，斷電也不會壞檔）
