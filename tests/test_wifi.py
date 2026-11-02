"""Wi-Fi 開箱設定：開機判斷、熱點流程、設定頁。用 FakeNM 模擬 NetworkManager."""
import json
import threading
import time
import urllib.parse
import urllib.request

import pytest

from einkboard import wifi


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.t += s


class FakeNM:
    def __init__(self, saved=(), visible=(), passwords=None, connected=False, appear_after=None, clock=None):
        self.saved_ = list(saved)                  # [(name, ssid)]
        self.visible = set(visible)
        self.passwords = dict(passwords or {})     # ssid -> correct password（None = 存的密碼是對的）
        self.stored = {s: "stored" for _, s in saved}
        self.is_connected, self.current = connected, ""
        self.appear_after, self.clock = appear_after, clock
        self.log = []

    def _visible(self):
        v = set(self.visible)
        if self.appear_after and self.clock() >= self.appear_after[0]:
            v.add(self.appear_after[1])
        return v

    def connected(self):
        return self.is_connected, self.current

    def saved(self):
        return list(self.saved_)

    def scan(self):
        self.log.append("scan")
        return [{"ssid": s, "signal": 70, "secure": True} for s in sorted(self._visible())]

    def _try(self, ssid, pw):
        if ssid not in self._visible():
            return False, "notfound"
        want = self.passwords.get(ssid)
        if want is not None and pw != want:
            return False, "auth"
        self.is_connected, self.current = True, ssid
        return True, ""

    def up(self, name):
        ssid = dict(self.saved_)[name]
        self.log.append(f"up:{ssid}")
        return self._try(ssid, self.stored.get(ssid))

    def connect(self, ssid, pw):
        self.log.append(f"connect:{ssid}")
        ok, why = self._try(ssid, pw)
        if ok and ssid not in dict((s, n) for n, s in self.saved_):
            self.saved_.append((ssid, ssid))
        if ok:
            self.stored[ssid] = pw
        return ok, why

    def hotspot_start(self, ssid, pw):
        self.log.append("ap_on")
        return True

    def hotspot_stop(self):
        self.log.append("ap_off")


# ---------------------------------------------------------------- 開機判斷
def test_already_connected():
    assert wifi.boot_check(FakeNM(connected=True)) == ("ok", "")


def test_nothing_saved_opens_hotspot_immediately():
    c = Clock()
    assert wifi.boot_check(FakeNM(), clock=c, sleep=c.sleep) == ("no_saved", "")
    assert c.t == 0


def test_saved_wifi_not_around_waits_then_hotspot():
    c = Clock()
    nm = FakeNM(saved=[("家裡", "Home")], visible=["鄰居"], clock=c)
    assert wifi.boot_check(nm, wait=150, clock=c, sleep=c.sleep) == ("not_found", "")
    assert 150 <= c.t < 160


def test_saved_wifi_shows_up_late_but_within_wait():
    c = Clock()
    nm = FakeNM(saved=[("家裡", "Home")], appear_after=(90, "Home"), clock=c)
    assert wifi.boot_check(nm, wait=150, clock=c, sleep=c.sleep) == ("ok", "Home")
    assert c.t < 120


def test_wrong_password_three_times_opens_hotspot_early():
    c = Clock()
    nm = FakeNM(saved=[("家裡", "Home")], visible=["Home"], passwords={"Home": "newpass1"}, clock=c)
    assert wifi.boot_check(nm, wait=150, auth_limit=3, clock=c, sleep=c.sleep) == ("auth", "Home")
    assert nm.log.count("up:Home") == 3 and c.t < 80          # 不用等滿 150 秒


def test_second_saved_network_still_tried_when_first_has_wrong_password():
    c = Clock()
    nm = FakeNM(saved=[("家裡", "Home"), ("公司", "Office")], visible=["Home", "Office"],
                passwords={"Home": "changed!"}, clock=c)
    assert wifi.boot_check(nm, clock=c, sleep=c.sleep) == ("ok", "Office")


# ---------------------------------------------------------------- 熱點流程
def _post(port, ssid="", password="", other=""):
    data = urllib.parse.urlencode({"ssid": ssid, "password": password, "other": other}).encode()
    with urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/", data=data), timeout=5) as r:
        return r.read().decode()


def _run_setup(nm, tmp_path, **kw):
    shown, result = [], {}
    stop = threading.Event()

    def show(kind, **k):
        shown.append((kind, k))

    def go():
        result["ssid"] = wifi.run_setup(nm, show, kw.pop("reason", "not_found"), kw.pop("detail", ""),
                                        cache_dir=str(tmp_path), stop=stop.is_set, ports=(0,), wait=0.05, **kw)
    t = threading.Thread(target=go, daemon=True)
    t.start()
    return t, shown, result, stop


def _portal_port(shown, timeout=3):
    t0 = time.time()
    while time.time() - t0 < timeout:
        for kind, k in reversed(shown):
            if kind == "setup":
                return int(k["url"].rsplit(":", 1)[1])
        time.sleep(0.02)
    raise AssertionError("沒有顯示設定頁")


def test_setup_wrong_password_then_right(tmp_path):
    nm = FakeNM(visible=["Home", "Neighbour"], passwords={"Home": "right-pass"})
    t, shown, result, stop = _run_setup(nm, tmp_path, reason="no_saved")
    port = _portal_port(shown)
    page = urllib.request.urlopen(f"http://127.0.0.1:{port}/hotspot-detect.html", timeout=5).read().decode()
    assert "設定 Wi-Fi" in page and "Home" in page and "Neighbour" in page   # 任何網址都回設定頁
    assert "8～63" in _post(port, "Home", "short")                           # 表單檢查，不關熱點
    assert "正在連線" in _post(port, "Home", "wrong-pass")
    t0 = time.time()
    while sum(1 for k, _ in shown if k == "setup") < 2 and time.time() - t0 < 5:
        time.sleep(0.05)
    setups = [k for kind, k in shown if kind == "setup"]
    assert setups[-1]["message"] == "密碼錯誤：Home"                          # 電子紙顯示原因
    port = _portal_port(shown)
    assert "密碼錯誤：Home" in urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read().decode()
    _post(port, "Home", "right-pass")
    t.join(5)
    assert result["ssid"] == "Home" and nm.is_connected
    assert nm.log.count("ap_on") == 2 and nm.log.count("ap_off") == 2
    ap = json.loads((tmp_path / "ap.json").read_text())
    assert ap["ssid"].startswith("EinkBoard-") and len(ap["password"]) == 8


def test_hidden_network_typed_by_hand(tmp_path):
    nm = FakeNM(visible=["Hidden"])
    t, shown, result, stop = _run_setup(nm, tmp_path)
    _post(_portal_port(shown), "", "", other="Hidden")
    t.join(5)
    assert result["ssid"] == "Hidden"


def test_hotspot_periodically_checks_saved_wifi_after_power_cut(tmp_path):
    """停電後 Pi 比路由器先開機：熱點開著、沒人在設定時，定期回頭找存過的 Wi-Fi."""
    nm = FakeNM(saved=[("家裡", "Home")], visible=[])
    t, shown, result, stop = _run_setup(nm, tmp_path, retry_minutes=0)
    time.sleep(0.3)
    assert result == {} and "ap_on" in nm.log
    nm.visible.add("Home")                                         # 路由器終於開好
    t.join(5)
    assert result["ssid"] == "Home"


def test_hotspot_stays_while_someone_is_using_the_page(tmp_path):
    nm = FakeNM(saved=[("家裡", "Home")], visible=["Home"])
    nm.stored["Home"] = "x"; nm.passwords["Home"] = "y"            # 存的密碼是錯的，回頭重試也連不上
    t, shown, result, stop = _run_setup(nm, tmp_path, retry_minutes=0)
    port = _portal_port(shown)
    urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5).read()
    n = nm.log.count("ap_on")
    time.sleep(0.4)
    assert nm.log.count("ap_on") == n                              # 有人在用 -> 不會關熱點去重試
    stop.set(); t.join(5)
    assert result["ssid"] is None and nm.log[-1] == "ap_off"       # 服務停止時一定關熱點


def test_qr_text_escapes_special_characters():
    assert wifi.wifi_qr_text('a;b', 'p:w"') == r'WIFI:T:WPA;S:a\;b;P:p\:w\";;'


def test_nmcli_field_split():
    assert wifi._split(r"My\:Net:72:WPA2") == ["My:Net", "72", "WPA2"]
    assert wifi.NM._reason("Error: Connection activation failed: Secrets were required, but not provided") == "auth"
    assert wifi.NM._reason("Error: No network with SSID 'x' found.") == "notfound"


def test_setup_pages_render_and_qr_decodes():
    from einkboard import pages
    img = pages.page_setup({"ssid": "EinkBoard-3F2A", "password": "k7mq2xpe"}, "密碼錯誤：Michael 的 iPhone")
    assert img.size == (296, 128)
    cv2 = pytest.importorskip("cv2")
    import numpy as np
    from PIL import Image
    arr = np.pad(np.array(img.convert("L").resize((888, 384), Image.NEAREST)), 40, constant_values=255)
    assert cv2.QRCodeDetector().detectAndDecode(arr)[0] == "WIFI:T:WPA;S:EinkBoard-3F2A;P:k7mq2xpe;;"


def test_nmcli_log_never_contains_password(monkeypatch, caplog):
    import subprocess as sp
    monkeypatch.setattr(sp, "run", lambda *a, **k: sp.CompletedProcess(a[0], 4, "", "Secrets were required"))
    caplog.set_level("INFO")
    nm = wifi.NM()
    nm._run(["--wait", "40", "device", "wifi", "connect", "Home", "ifname", "wlan0", "password", "hunter2pw"])
    nm._run(["connection", "modify", "Home", "wifi-sec.psk", "hunter2pw"])
    assert "hunter2pw" not in caplog.text and "***" in caplog.text


def test_connected_state_variants(monkeypatch):
    nm = wifi.NM()
    for state, want in [("connected", True), ("connected (externally)", True), ("connecting (configuring)", False),
                        ("disconnected", False)]:
        monkeypatch.setattr(nm, "_run", lambda *a, s=state: (0, f"wlan0:wifi:{s}:iPhone\n", ""))
        assert nm.connected() == (want, "iPhone")
