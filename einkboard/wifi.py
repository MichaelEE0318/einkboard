"""Wi-Fi 開箱設定（熱點＋設定頁）。

開機時：
  1. 沒有存任何 Wi-Fi                     -> 直接開熱點
  2. 存過的 Wi-Fi 掃得到，但密碼連錯 3 次   -> 馬上開熱點（Wi-Fi 密碼改了）
  3. 等 boot_wait 秒都沒看到存過的 Wi-Fi    -> 開熱點（搬到新地方）
已經連上之後就算斷線也不會開熱點（交給 NetworkManager 自己重連）。

熱點模式：電子紙顯示 QR Code（掃了就連上熱點），手機會自動跳出設定頁，選 Wi-Fi、輸入密碼。
熱點期間每 retry_minutes 分鐘、而且沒人在用設定頁時，會關掉熱點檢查一次存過的 Wi-Fi ——
停電後 Pi 常常比路由器先開好，不這樣做就會一直卡在熱點。

只有一顆無線晶片：開熱點時不能同時掃描／連線，所以 Wi-Fi 清單是開熱點前掃好的。
需要：NetworkManager（Raspberry Pi OS Bookworm 以後）、scripts/setup_wifi_portal.sh 裝好的
polkit 規則（讓服務帳號能控制 NetworkManager）與 dnsmasq 導向設定。
"""
import html
import json
import logging
import os
import queue
import secrets
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger(__name__)

AP_CON = "einkboard-setup"
AP_IP = "10.42.0.1"                       # NetworkManager 熱點（shared）的預設位址


# ---------------------------------------------------------------- NetworkManager
def _split(line):
    """nmcli -t 的欄位用 ':' 分隔，欄位裡的 ':' 會被跳脫成 '\\:'."""
    out, cur, esc = [], "", False
    for ch in line:
        if esc:
            cur += ch; esc = False
        elif ch == "\\":
            esc = True
        elif ch == ":":
            out.append(cur); cur = ""
        else:
            cur += ch
    out.append(cur)
    return out


class NM:
    """nmcli 的薄包裝。測試用 FakeNM 取代。"""

    def __init__(self, iface="wlan0"):
        self.iface = iface

    @staticmethod
    def available():
        return shutil.which("nmcli") is not None

    def _run(self, args, timeout=60):
        env = dict(os.environ, LC_ALL="C")
        t0 = time.monotonic()
        try:
            p = subprocess.run(["nmcli"] + args, capture_output=True, text=True, timeout=timeout, env=env)
            rc, out, err = p.returncode, p.stdout, p.stderr
        except subprocess.TimeoutExpired:
            rc, out, err = 124, "", f"timeout after {timeout}s"
        dt = time.monotonic() - t0
        if rc != 0 or dt > 5:
            shown = ["***" if i and (args[i - 1] == "password" or args[i - 1].endswith(".psk")) else x
                     for i, x in enumerate(args)]                      # 不把密碼寫進 log
            log.info("nmcli %s -> %s（%.1fs）%s", " ".join(shown), rc, dt, err.strip()[:160])
        return rc, out, err

    def connected(self):
        """(連上了嗎, 目前的 SSID)"""
        _, out, _ = self._run(["-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"], 15)
        for line in out.splitlines():
            dev, typ, state, con = (_split(line) + ["", "", "", ""])[:4]
            if dev == self.iface:
                # NetworkManager 新版會寫 "connected (externally)" 之類，用 startswith
                return state.startswith("connected") and con != AP_CON, con
        return False, ""

    def saved(self):
        """[(連線名稱, SSID)] —— 存過的 Wi-Fi（不含我們的熱點）"""
        _, out, _ = self._run(["-t", "-f", "NAME,TYPE", "connection", "show"], 15)
        res = []
        for line in out.splitlines():
            name, typ = (_split(line) + [""])[:2]
            if typ in ("802-11-wireless", "wifi") and name != AP_CON:
                _, ssid, _ = self._run(["-g", "802-11-wireless.ssid", "connection", "show", name], 15)
                res.append((name, ssid.strip() or name))
        return res

    def scan(self):
        """[{ssid, signal, secure}]，同名取訊號最強的一個，依訊號排序"""
        _, out, _ = self._run(["-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list",
                               "ifname", self.iface, "--rescan", "yes"], 40)
        best = {}
        for line in out.splitlines():
            ssid, sig, sec = (_split(line) + ["", "", ""])[:3]
            if not ssid:
                continue
            sig = int(sig) if sig.isdigit() else 0
            if ssid not in best or sig > best[ssid]["signal"]:
                best[ssid] = {"ssid": ssid, "signal": sig, "secure": bool(sec.strip()) and sec.strip() != "--"}
        return sorted(best.values(), key=lambda n: -n["signal"])

    @staticmethod
    def _reason(err):
        e = err.lower()
        if "secrets were required" in e or "no secrets" in e or "(7)" in e or "802.1x" in e:
            return "auth"
        if "no network with ssid" in e or "could not be found" in e or "not found" in e:
            return "notfound"
        return "other"

    def up(self, name):
        rc, _, err = self._run(["--wait", "40", "connection", "up", "id", name], 60)
        return rc == 0, ("" if rc == 0 else self._reason(err))

    def connect(self, ssid, password):
        """連新的 Wi-Fi；已經存過同一個 SSID 就改它的密碼。失敗會把東西恢復原狀。
        回傳 (成功?, 原因 auth / notfound / other)"""
        existing = [n for n, s in self.saved() if s == ssid]
        if existing:
            name = existing[0]
            _, old, _ = self._run(["-s", "-g", "802-11-wireless-security.psk", "connection", "show", name], 15)
            old = old.strip()
            if password:
                self._run(["connection", "modify", name, "wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", password], 15)
            ok, why = self.up(name)
            if not ok and old:
                self._run(["connection", "modify", name, "wifi-sec.psk", old], 15)
            return ok, why
        before = {n for n, _ in self.saved()}
        args = ["--wait", "40", "device", "wifi", "connect", ssid, "ifname", self.iface]
        if password:
            args += ["password", password]
        rc, _, err = self._run(args, 60)
        if rc == 0:
            return True, ""
        for n, _ in self.saved():                 # 失敗：刪掉這次新建、連不上的設定
            if n not in before:
                self._run(["connection", "delete", "id", n], 15)
        return False, self._reason(err)

    def hotspot_start(self, ssid, password):
        self._run(["connection", "delete", "id", AP_CON], 15)
        rc, _, err = self._run(["device", "wifi", "hotspot", "ifname", self.iface, "con-name", AP_CON,
                                "ssid", ssid, "band", "bg", "password", password], 40)
        if rc == 0:
            self._run(["connection", "modify", AP_CON, "connection.autoconnect", "no"], 15)
        else:
            log.error("開熱點失敗：%s", err.strip())
        return rc == 0

    def hotspot_stop(self):
        self._run(["connection", "down", "id", AP_CON], 20)


# ---------------------------------------------------------------- 開機檢查
def boot_check(nm, wait=150, auth_limit=3, first_try=20, poll=5, clock=time.monotonic, sleep=time.sleep,
               stop=lambda: False):
    """開機後決定要不要開熱點。回傳 (結果, 細節)：
    ("ok", ssid) / ("no_saved", "") / ("not_found", "") / ("auth", ssid)"""
    t_start = clock()
    ok, ssid = nm.connected()
    if ok:
        return "ok", ssid
    saved = nm.saved()
    log.info("Wi-Fi 還沒連上，存過的：%s", ", ".join(s for _, s in saved) or "（沒有）")
    if not saved:
        return "no_saved", ""
    t0, fails, next_try = clock(), {}, clock() + first_try     # 先讓 NetworkManager 自己連 first_try 秒
    while clock() - t0 < wait and not stop():
        ok, ssid = nm.connected()
        if ok:
            return "ok", ssid
        if clock() >= next_try:
            visible = {n["ssid"] for n in nm.scan()}
            log.info("Wi-Fi 掃描（%.0fs）：看到 %d 個，存過的有 %s", clock() - t_start, len(visible),
                     ", ".join(s for _, s in saved if s in visible) or "0 個")
            for name, s in saved:
                if s not in visible:
                    continue
                ok, why = nm.up(name)
                if ok:
                    return "ok", s
                if why == "auth":
                    fails[s] = fails.get(s, 0) + 1
                    log.warning("Wi-Fi「%s」密碼錯誤（%d/%d）", s, fails[s], auth_limit)
                    if fails[s] >= auth_limit:
                        return "auth", s
            next_try = clock() + 15
        sleep(poll)
    return "not_found", ""


# ---------------------------------------------------------------- 熱點帳密（每台不同，存一次）
def ap_credentials(cache_dir):
    path = os.path.join(cache_dir, "ap.json")
    try:
        with open(path, encoding="utf-8") as f:
            c = json.load(f)
        if c.get("ssid") and len(c.get("password", "")) >= 8:
            return c
    except (OSError, ValueError):
        pass
    suffix = ""
    try:
        with open("/proc/cpuinfo", encoding="ascii", errors="ignore") as f:
            for line in f:
                if line.startswith("Serial"):
                    suffix = line.split(":")[1].strip()[-4:].upper()
    except OSError:
        pass
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"          # 去掉容易看錯的 i l o 0 1
    c = {"ssid": f"EinkBoard-{suffix or secrets.token_hex(2).upper()}",
         "password": "".join(secrets.choice(alphabet) for _ in range(8))}
    try:
        os.makedirs(cache_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(c, f)
        os.chmod(path, 0o600)
    except OSError as e:
        log.warning("ap.json：%s", e)
    return c


def wifi_qr_text(ssid, password):
    esc = lambda s: "".join("\\" + ch if ch in '\\;,:"' else ch for ch in s)
    return f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(password)};;"


# ---------------------------------------------------------------- 設定頁（熱點模式時的網頁）
class Portal:
    """熱點模式的設定網頁。任何網址都回設定頁 —— 手機會以為這是「需要登入的 Wi-Fi」而自動跳出來。"""

    def __init__(self, networks, ap, error=""):
        self.networks, self.ap, self.error = networks, ap, error
        self.submissions = queue.Queue()
        self.last_seen = None                # 還沒人開過設定頁（不能用 0：開機沒多久 monotonic 也很小）
        self.srv = None
        self.port = None

    def start(self, ports=(80, 8080), host="0.0.0.0"):
        portal = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="text/html; charset=utf-8"):
                body = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                portal.last_seen = time.monotonic()
                self._send(200, portal.page())

            def do_POST(self):
                portal.last_seen = time.monotonic()
                n = int(self.headers.get("Content-Length") or 0)
                if n > 4096:
                    return self._send(413, "too large", "text/plain")
                from urllib.parse import parse_qs
                form = parse_qs(self.rfile.read(n).decode("utf-8", "replace"))
                ssid = (form.get("other", [""])[0].strip() or form.get("ssid", [""])[0]).strip()
                pw = form.get("password", [""])[0]
                err = ""
                if not ssid or len(ssid.encode()) > 32:
                    err = "請選擇或輸入 Wi-Fi 名稱"
                elif pw and not 8 <= len(pw) <= 63:
                    err = "Wi-Fi 密碼應該是 8～63 個字元"
                if err:
                    portal.error = err
                    return self._send(200, portal.page())
                portal.submissions.put((ssid, pw))
                self._send(200, portal.done_page(ssid))

        for port in ports:
            try:
                self.srv = ThreadingHTTPServer((host, port), H)
                self.port = self.srv.server_address[1]
                break
            except OSError as e:
                log.warning("設定頁無法使用 port %s：%s", port, e)
        if self.srv:
            self.srv.daemon_threads = True
            threading.Thread(target=self.srv.serve_forever, daemon=True, name="portal").start()
        return self.port

    def stop(self):
        if self.srv:
            self.srv.shutdown()
            self.srv.server_close()
            self.srv = None

    def active(self, within=180):
        """最近有人在用設定頁嗎（有的話先不要關熱點去重試）"""
        return self.last_seen is not None and time.monotonic() - self.last_seen < within

    # ---- HTML
    CSS = """*{box-sizing:border-box}body{margin:0;font:16px/1.6 -apple-system,"PingFang TC","Noto Sans TC",sans-serif;
background:#eef0ec;color:#1c2420;padding:20px}main{max-width:480px;margin:0 auto}
h1{font-size:22px;margin:0 0 4px}.sub{color:#5f6a64;font-size:14px}
.card{background:#fff;border:1px solid #d5dad4;border-radius:12px;padding:16px;margin-top:16px}
.net{display:flex;align-items:center;gap:10px;padding:10px 4px;border-bottom:1px solid #eee}
.net:last-child{border:none}.net span{flex:1}.bars{font-family:monospace;color:#5f6a64}
input[type=password],input[type=text]{font:inherit;width:100%;padding:10px 12px;border:1px solid #d5dad4;border-radius:8px}
label{display:block;margin-top:12px;font-size:14px;color:#5f6a64}
button{font:inherit;width:100%;margin-top:16px;padding:12px;border:none;border-radius:8px;background:#2f5d4a;color:#fff;font-weight:700}
.err{color:#b3261e;font-size:14px;margin-top:8px}
label.net{display:flex;margin:0;font-size:16px;color:#1c2420}"""

    def page(self):
        rows = "".join(
            f'<label class="net"><input type="radio" name="ssid" value="{html.escape(n["ssid"])}"{" checked" if i == 0 else ""}>'
            f'<span>{html.escape(n["ssid"])}{" 🔒" if n["secure"] else ""}</span>'
            f'<span class="bars">{"▂▄▆█"[:max(1, min(4, n["signal"] // 25 + 1))]}</span></label>'
            for i, n in enumerate(self.networks))
        err = f'<div class="err" role="alert">{html.escape(self.error)}</div>' if self.error else ""
        return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>設定 Wi-Fi</title><style>{self.CSS}</style></head>
<body><main><h1>設定 Wi-Fi</h1><div class="sub">選一個 Wi-Fi 並輸入密碼，看板會自己連上。只支援 2.4 GHz。</div>
<form method="post" action="/"><div class="card">{rows or '<div class="sub">沒有掃描到 Wi-Fi，請在下面手動輸入名稱。</div>'}
<label>其他（隱藏的 Wi-Fi 請手動輸入名稱）<input type="text" name="other" maxlength="32" autocomplete="off"></label>
<label>密碼（沒有密碼就留白）<input type="password" name="password" maxlength="63" autocomplete="off"></label>
{err}<button>連線</button></div></form>
<div class="sub" style="margin-top:16px">看板熱點：{html.escape(self.ap["ssid"])}</div></main></body></html>"""

    def done_page(self, ssid):
        return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>連線中</title><style>{self.CSS}</style></head>
<body><main><h1>正在連線到「{html.escape(ssid)}」</h1><div class="card">
<p>熱點會關閉，手機會自動斷線，這是正常的。</p><p>請看電子紙上的結果：</p>
<p>✓ 成功：會顯示設定網頁的網址<br>✗ 失敗：熱點會重新打開，再掃一次 QR Code 重設</p></div></main></body></html>"""


# ---------------------------------------------------------------- 熱點模式主流程
REASONS = {"no_saved": "第一次使用，請設定 Wi-Fi", "not_found": "找不到已設定的 Wi-Fi",
           "auth": "密碼錯誤：{}", "notfound": "找不到：{}", "other": "連不上：{}"}


def run_setup(nm, show, reason, detail="", cache_dir="/var/lib/einkboard", retry_minutes=10,
              stop=lambda: False, clock=time.monotonic, wait=None, ports=(80, 8080)):
    """開熱點等使用者設定，連上 Wi-Fi 才回傳 SSID（或 stop() 為真時回傳 None）。
    show(kind, **info) 負責畫電子紙：kind = "setup" / "connecting" / "connected"."""
    ap = ap_credentials(cache_dir)
    message = REASONS.get(reason, "請設定 Wi-Fi").format(detail)
    form_error = message if reason in ("auth", "notfound", "other") else ""
    while not stop():
        networks = nm.scan()                                 # 開熱點前先掃好清單
        if not nm.hotspot_start(ap["ssid"], ap["password"]):
            show("error", message="無法開啟熱點，請重新插電")
            return None
        portal = Portal(networks, ap, error=form_error)
        port = portal.start(ports)
        url = f"http://{AP_IP}" + ("" if port == 80 else f":{port}")
        show("setup", ap=ap, message=message, url=url)
        log.info("熱點模式：%s（%s），設定頁 %s", ap["ssid"], message, url)
        t_retry = clock() + retry_minutes * 60
        got = None
        while not stop() and got is None:
            try:
                got = portal.submissions.get(timeout=wait if wait is not None else 2)
            except queue.Empty:
                if clock() >= t_retry and not portal.active():
                    break                                   # 去檢查一下存過的 Wi-Fi 是不是回來了
        time.sleep(1.5 if got else 0)                       # 讓「連線中」頁面先送到手機
        portal.stop()
        nm.hotspot_stop()
        if stop():
            return None
        if got:
            ssid, pw = got
            show("connecting", ssid=ssid)
            ok, why = nm.connect(ssid, pw)
            if ok:
                return ssid
            message = form_error = REASONS.get(why, REASONS["other"]).format(ssid)
            log.warning("連線失敗：%s", message)
            continue
        # 熱點開了 retry_minutes 分鐘、沒人在用：關掉熱點找存過的 Wi-Fi（停電後路由器比較慢開機）
        saved = nm.saved()
        visible = {n["ssid"] for n in nm.scan()}
        for name, s in saved:
            if s in visible:
                ok, _ = nm.up(name)
                if ok:
                    return s
    return None
