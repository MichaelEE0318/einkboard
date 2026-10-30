"""Settings web page: live controls, preview, status and a config editor.

Runs inside the einkboard process (stdlib only). The web thread never touches the panel: it queues
commands for the main loop, which wakes immediately. Saving the config exits with code 75 and systemd
(Restart=on-failure) starts the service again with the new settings.

No login: anyone on the same network can open it. Keep the Pi on a network you trust, or set
web.enabled: false.
"""
import copy
import io
import json
import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import yaml

from . import config

log = logging.getLogger(__name__)

PAGE_NAMES = {"clock": "時鐘＋天氣", "power": "台電供電", "market": "台股．匯率"}


# ---------------------------------------------------------------- config editing
def editable(cfg):
    """The subset of the config the page shows and edits."""
    e = cfg["eink"]
    return {
        "location": {k: cfg["location"][k] for k in ("name", "lat", "lon")},
        "pages": list(cfg["pages"]),
        "rotate_minutes": cfg["rotate_minutes"],
        "night": dict(cfg["night"]),
        "market": {"stocks": list(cfg["market"]["stocks"]), "fx": list(cfg["market"]["fx"])},
        "eink": {"page_change_full": bool(e.get("page_change_full", True)),
                 "full_refresh_minutes": int(e.get("full_refresh_minutes", 60)),
                 "rotate180": bool(e.get("rotate180", False)), "invert": bool(e.get("invert", False))},
    }


class Invalid(ValueError):
    pass


def _num(v, lo, hi, name, cast=float):
    try:
        x = cast(v)
    except (TypeError, ValueError):
        raise Invalid(f"{name} 要填數字")
    if not lo <= x <= hi:
        raise Invalid(f"{name} 要介於 {lo} 到 {hi}")
    return x


def _hhmm(v, name):
    if not isinstance(v, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", v.strip()):
        raise Invalid(f"{name} 要寫成 HH:MM，例如 23:30")
    return v.strip()


def validate(new):
    """Check the posted settings; returns a clean dict in the editable() shape or raises Invalid."""
    try:
        loc, night, mk, e = new["location"], new["night"], new["market"], new["eink"]
    except (KeyError, TypeError):
        raise Invalid("設定格式不完整")
    pages = [p for p in new.get("pages", []) if p in PAGE_NAMES]
    if not pages:
        raise Invalid("至少要選一個頁面")
    stocks = [str(s).strip() for s in mk.get("stocks", []) if str(s).strip()]
    bad = [s for s in stocks if not re.fullmatch(r"[A-Za-z0-9]{2,8}", s)]
    if bad:
        raise Invalid(f"股票代號格式不對：{'、'.join(bad)}")
    if len(stocks) > 4:
        raise Invalid("股票最多 4 檔（畫面只放得下 4 列）")
    fx = [str(c).strip().upper() for c in mk.get("fx", []) if str(c).strip()]
    if any(not re.fullmatch(r"[A-Z]{3}", c) for c in fx):
        raise Invalid("幣別要用 3 個英文字母，例如 USD、JPY、VND")
    if len(fx) > 3:
        raise Invalid("幣別最多 3 種")
    name = str(loc.get("name", "")).strip()
    if not name or len(name) > 8:
        raise Invalid("地點名稱要 1～8 個字")
    return {
        "location": {"name": name, "lat": _num(loc.get("lat"), -90, 90, "緯度"),
                     "lon": _num(loc.get("lon"), -180, 180, "經度")},
        "pages": pages,
        "rotate_minutes": _num(new.get("rotate_minutes"), 1, 60, "每頁停留分鐘", int),
        "night": {"enabled": bool(night.get("enabled")), "start": _hhmm(night.get("start"), "夜間開始"),
                  "end": _hhmm(night.get("end"), "夜間結束")},
        "market": {"stocks": stocks, "fx": fx},
        "eink": {"page_change_full": bool(e.get("page_change_full")),
                 "full_refresh_minutes": _num(e.get("full_refresh_minutes"), 5, 240, "定時全刷分鐘", int),
                 "rotate180": bool(e.get("rotate180")), "invert": bool(e.get("invert"))},
    }


def validate_sign(a):
    """狀態牌欄位：kind + place / back / note."""
    from .pages import STATUS_KINDS
    if not isinstance(a, dict) or a.get("kind") not in STATUS_KINDS:
        raise Invalid("請選擇狀態：出差中、下班了或暫離")
    kind = a["kind"]
    place, note = str(a.get("place") or "").strip(), str(a.get("note") or "").strip()
    back = str(a.get("back") or "").strip()
    if len(place) > 16:
        raise Invalid("地點最多 16 個字")
    if len(note) > 24:
        raise Invalid("留言最多 24 個字")
    if back:
        if kind == "trip" and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", back):
            raise Invalid("回程日期格式不對")
        if kind != "trip":
            back = _hhmm(back, "回來時間")
    return {"kind": kind, "place": place if kind == "trip" else "", "back": back, "note": note}


def save(path, clean):
    """Merge into the YAML file (keeping keys the page doesn't edit) and write atomically, with a .bak."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raw = {}
    merged = config._merge(copy.deepcopy(raw), clean)
    if os.path.exists(path):
        with open(path, "rb") as f, open(path + ".bak", "wb") as b:
            b.write(f.read())
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("# 由設定網頁儲存（舊版在 config.yaml.bak）\n")
        yaml.safe_dump(merged, f, allow_unicode=True, sort_keys=False)
    os.replace(tmp, path)
    return merged


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    board = None
    config_path = "config.yaml"

    def log_message(self, fmt, *args):          # keep journalctl quiet
        log.debug(fmt, *args)

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False)
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 64 * 1024:
            raise Invalid("資料太大")
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            raise Invalid("資料格式錯誤")

    def do_GET(self):
        b = self.board
        if self.path in ("/", "/index.html"):
            return self._send(200, PAGE_HTML, "text/html; charset=utf-8")
        if self.path == "/api/status":
            return self._send(200, b.status())
        if self.path == "/api/config":
            return self._send(200, {"config": editable(b.cfg), "page_names": PAGE_NAMES})
        if self.path.startswith("/preview.png"):
            img = b.last_img
            if img is None:
                return self._send(404, {"error": "還沒有畫面"})
            buf = io.BytesIO()
            img.convert("L").save(buf, "PNG")
            return self._send(200, buf.getvalue(), "image/png")
        self._send(404, {"error": "not found"})

    def do_POST(self):
        b = self.board
        try:
            data = self._json()
            if self.path == "/api/action":
                action = data.get("action")
                if action not in ("next", "prev", "goto", "pause", "full", "refetch", "sign", "rotate"):
                    raise Invalid("不認得的操作")
                arg = validate_sign(data.get("arg")) if action == "sign" else data.get("arg")
                b.command(action, arg)
                return self._send(200, {"ok": True})
            if self.path == "/api/config":
                clean = validate(data.get("config") or {})
                save(self.config_path, clean)
                if data.get("restart", True):
                    threading.Timer(0.5, b.command, args=("restart",)).start()
                return self._send(200, {"ok": True, "restarting": bool(data.get("restart", True))})
        except Invalid as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:  # never kill the web thread
            log.exception("web error")
            return self._send(500, {"error": f"伺服器錯誤：{e}"})
        self._send(404, {"error": "not found"})


def start_web(board, port, host="0.0.0.0", config_path=None):
    handler = type("BoardHandler", (Handler,), {
        "board": board, "config_path": config_path or os.environ.get("EINKBOARD_CONFIG", "config.yaml")})
    try:
        srv = ThreadingHTTPServer((host, port), handler)
    except OSError as e:
        log.warning("設定網頁無法啟動（port %s）：%s", port, e)
        return None
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True, name="web").start()
    log.info("設定網頁：http://%s:%s", board.ip or "<pi>", srv.server_address[1])
    return srv


# ---------------------------------------------------------------- the page
PAGE_HTML = r"""<!doctype html>
<html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>電子紙看板設定</title>
<style>
:root{--bg:#eef0ec;--card:#fbfbf8;--ink:#1c2420;--muted:#5f6a64;--line:#d5dad4;--accent:#2f5d4a;--accent-ink:#fff;
--ok:#2e7d4f;--warn:#a15c00;--bad:#b3261e;--panel:#e9e7df;--panel-ink:#1a1a1a;
--f:"Noto Sans TC","PingFang TC","Microsoft JhengHei",system-ui,sans-serif;--mono:ui-monospace,Menlo,Consolas,monospace}
@media (prefers-color-scheme:dark){:root{--bg:#121614;--card:#1a201d;--ink:#e3eae6;--muted:#9aa8a0;--line:#2d3632;
--accent:#6fb592;--accent-ink:#0d1a14;--ok:#6fcf97;--warn:#f0b35a;--bad:#f28b82;--panel:#d9d6cc;color-scheme:dark}}
*{box-sizing:border-box}
[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.6 var(--f);
padding:16px max(16px,env(safe-area-inset-right)) 40px max(16px,env(safe-area-inset-left))}
main{max-width:720px;margin:0 auto;display:flex;flex-direction:column;gap:16px}
h1{font-size:22px;margin:4px 0 0;font-weight:700}
h2{font-size:15px;margin:0 0 10px;font-weight:700;letter-spacing:.04em}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px}
.sub{color:var(--muted);font-size:13px}
.screen{background:#3b3f3d;border-radius:10px;padding:10px;display:flex;justify-content:center}
.screen img{width:100%;max-width:592px;image-rendering:pixelated;background:var(--panel);border-radius:2px;aspect-ratio:296/128}
.row{display:flex;flex-wrap:wrap;gap:8px;align-items:center}
button{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--ink);border-radius:8px;padding:8px 14px;cursor:pointer}
button:hover{border-color:var(--muted)}
button:focus-visible,input:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
button.primary{background:var(--accent);color:var(--accent-ink);border-color:var(--accent);font-weight:700}
button.on{background:var(--ink);color:var(--card)}
button:disabled{opacity:.5;cursor:default}
.chip{padding:6px 12px;border-radius:999px;font-size:14px}
.status{display:grid;grid-template-columns:auto 1fr;gap:4px 12px;font-size:14px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;vertical-align:1px}
.ok{background:var(--ok)}.warn{background:var(--warn)}.bad{background:var(--bad)}
.err{color:var(--bad);font-size:13px}
label{display:flex;flex-direction:column;gap:4px;font-size:13px;color:var(--muted)}
input[type=text],input[type=number],input[type=time],input[type=date]{font:15px var(--f);color:var(--ink);background:var(--bg);
border:1px solid var(--line);border-radius:8px;padding:8px 10px;width:100%}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px}
fieldset{border:none;padding:0;margin:0 0 16px;display:flex;flex-direction:column;gap:10px}
legend{font-weight:700;font-size:14px;margin-bottom:6px;padding:0}
.check{flex-direction:row;align-items:center;gap:8px;color:var(--ink);font-size:14px}
.order{display:flex;flex-direction:column;gap:6px}
.order div{display:flex;align-items:center;gap:8px;border:1px solid var(--line);border-radius:8px;padding:6px 10px}
.order span{flex:1}
.order button{padding:2px 8px}
.toast{position:sticky;bottom:12px;align-self:center;background:var(--ink);color:var(--card);padding:8px 14px;border-radius:8px;font-size:14px}
.mono{font-family:var(--mono)}
</style></head><body><main>
<header><h1>電子紙看板</h1><div class="sub" id="where">連線中…</div></header>

<section class="card">
  <div class="screen"><img id="shot" alt="電子紙目前畫面"></div>
  <div class="row" style="margin-top:12px">
    <button onclick="act('prev')" aria-label="上一頁">◀ 上一頁</button>
    <button onclick="act('next')" class="primary">下一頁 ▶</button>
    <button id="pauseBtn" onclick="act('pause')">暫停輪播</button>
    <button onclick="act('full')" title="閃一下，清掉殘影">立即全刷</button>
    <button onclick="act('refetch')">重抓資料</button>
  </div>
  <div class="row" id="gotoRow" style="margin-top:10px"></div>
</section>

<section class="card" id="signCard">
  <h2>狀態牌</h2>
  <p class="sub" style="margin:-6px 0 10px">選一種狀態顯示在看板上，會一直停在那一頁（重開機也一樣），按「回到輪播」才恢復。</p>
  <div class="row" id="signKinds" role="radiogroup" aria-label="狀態"></div>
  <form id="signForm" onsubmit="showSign(event)" style="margin-top:12px;display:flex;flex-direction:column;gap:10px">
    <label id="fPlace">地點（例如 越南 河內）<input id="sPlace" type="text" maxlength="16"></label>
    <label id="fDate">回程日期<input id="sDate" type="date"></label>
    <label id="fTime">回來時間<input id="sTime" type="time"></label>
    <label>留言（選填）<input id="sNote" type="text" maxlength="24"></label>
    <div class="row"><button class="primary" id="signBtn">顯示狀態牌</button>
      <button type="button" onclick="act('rotate')">回到輪播</button><span class="sub" id="signNow"></span></div>
    <div class="err" id="signErr" role="alert"></div>
  </form>
</section>

<section class="card"><h2>狀態</h2><div class="status" id="status"></div></section>

<section class="card">
  <h2>設定</h2>
  <form id="cfg" onsubmit="saveCfg(event)">
    <fieldset><legend>頁面輪播</legend>
      <div class="order" id="order"></div>
      <div class="grid"><label>每頁停留（分鐘）<input id="rotate" type="number" min="1" max="60" required></label></div>
    </fieldset>
    <fieldset><legend>台股．匯率</legend>
      <label>股票代號（逗號分隔，最多 4 檔；t00＝加權指數、o00＝櫃買指數）<input id="stocks" type="text" class="mono" placeholder="t00, 2330, 0050"></label>
      <label>幣別（對新台幣，最多 3 種）<input id="fx" type="text" class="mono" placeholder="USD, JPY, VND"></label>
    </fieldset>
    <fieldset><legend>天氣地點</legend>
      <div class="grid">
        <label>顯示名稱<input id="locname" type="text" maxlength="8" required></label>
        <label>緯度<input id="lat" type="number" step="0.0001" required></label>
        <label>經度<input id="lon" type="number" step="0.0001" required></label>
      </div>
    </fieldset>
    <fieldset><legend>夜間</legend>
      <label class="check"><input id="nightOn" type="checkbox"> 夜間顯示「晚安」並停止刷新</label>
      <div class="grid"><label>開始<input id="nStart" type="time" required></label><label>結束<input id="nEnd" type="time" required></label></div>
    </fieldset>
    <fieldset><legend>螢幕刷新</legend>
      <label class="check"><input id="pcf" type="checkbox"> 換頁時全刷（閃一下，沒有殘影）</label>
      <div class="grid"><label>定時全刷（每幾分鐘）<input id="frm" type="number" min="5" max="240" required></label></div>
      <label class="check"><input id="rot" type="checkbox"> 畫面上下顛倒</label>
      <label class="check"><input id="inv" type="checkbox"> 黑白顛倒</label>
    </fieldset>
    <div class="row"><button class="primary" id="saveBtn">儲存並重新啟動</button><span class="sub">重啟約 15 秒，會全刷一次</span></div>
    <div class="err" id="formErr" role="alert"></div>
  </form>
</section>
<div class="toast" id="toast" hidden></div>
</main>
<script>
const $=id=>document.getElementById(id);
let NAMES={}, ORDER=[], ALL=["clock","power","market"], lastKey="";
function toast(t){const e=$("toast");e.textContent=t;e.hidden=false;clearTimeout(e._t);e._t=setTimeout(()=>e.hidden=true,2500)}
async function api(p,body){const r=await fetch(p,body?{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)}:{});
  const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.error||("HTTP "+r.status));return j}
async function act(a,arg){try{await api("/api/action",{action:a,arg});toast({next:"換到下一頁",prev:"換到上一頁",pause:"已切換輪播",full:"全刷中…",refetch:"重新抓資料中",goto:"切換頁面"}[a]);setTimeout(poll,1500);setTimeout(poll,5000)}catch(e){toast(e.message)}}
const SIGNS={trip:{name:"出差中",note:"急事請 LINE 或手機聯絡",time:""},off:{name:"下班了",note:"",time:"09:00"},away:{name:"暫離",note:"",time:""}};
let KIND="away";
function pickKind(k){KIND=k;$("signKinds").innerHTML=Object.entries(SIGNS).map(([key,v])=>`<button type="button" role="radio" aria-checked="${key===k}" class="chip ${key===k?"on":""}" onclick="pickKind('${key}')">${v.name}</button>`).join("");
  $("fPlace").hidden=k!=="trip";$("fDate").hidden=k!=="trip";$("fTime").hidden=k==="trip";
  $("fTime").firstChild.textContent=k==="off"?"明天幾點回來":"預計幾點回來（選填）";
  if(k==="off"&&!$("sTime").value)$("sTime").value="09:00";if(k==="trip"&&!$("sNote").value)$("sNote").value=SIGNS.trip.note}
async function showSign(ev){ev.preventDefault();$("signErr").textContent="";
  const arg={kind:KIND,place:$("sPlace").value,back:KIND==="trip"?$("sDate").value:$("sTime").value,note:$("sNote").value};
  try{await api("/api/action",{action:"sign",arg});toast(`顯示「${SIGNS[KIND].name}」`);setTimeout(poll,1500);setTimeout(poll,6000)}catch(e){$("signErr").textContent=e.message}}
function ago(t){if(!t)return"尚未成功";const s=Math.round(Date.now()/1000-t);return s<60?s+" 秒前":s<3600?Math.round(s/60)+" 分鐘前":Math.round(s/3600)+" 小時前"}
async function poll(){
  try{const s=await api("/api/status");
    $("where").textContent=`http://${s.ip||location.hostname}:${s.port}　·　${s.now}　·　已運作 ${Math.floor(s.uptime/3600)} 小時 ${Math.floor(s.uptime%3600/60)} 分`;
    $("signNow").textContent=s.sign?`目前顯示：${SIGNS[s.sign.kind].name}`:"目前在輪播";
    $("pauseBtn").textContent=s.paused?"繼續輪播":"暫停輪播";$("pauseBtn").classList.toggle("on",s.paused);
    $("gotoRow").innerHTML=s.pages.map(p=>`<button class="chip ${p===s.page?"on":""}" onclick="act('goto','${p}')">${NAMES[p]||p}</button>`).join("");
    const lab={weather:"天氣",power:"台電",market:"台股．匯率"};
    $("status").innerHTML=`<span>目前畫面</span><span>${s.sign?"狀態牌："+SIGNS[s.sign.kind].name:s.night?"夜間（晚安）":(NAMES[s.page]||s.page||"—")}${s.paused?"（已暫停輪播）":""}</span>`+
      (s.net?`<span>Wi-Fi</span><span><span class="dot ${!s.net.connected?"bad":s.net.internet?"ok":"warn"}"></span>${!s.net.connected?"未連線":`訊號 ${s.net.bars} 格`+(s.net.internet?"":"（連不上網際網路）")}</span>`:"")+
      Object.entries(s.sources).map(([k,v])=>`<span>${lab[k]}</span><span><span class="dot ${v.ok?"ok":v.updated?"warn":"bad"}"></span>${ago(v.updated)}${v.error?`<span class="err">　${v.error.slice(0,80)}</span>`:""}</span>`).join("");
    const key=s.page+s.now+s.paused;if(key!==lastKey){lastKey=key;$("shot").src="/preview.png?"+Date.now()}
  }catch(e){$("where").textContent="連不上看板（重啟中？）"}
}
function renderOrder(){
  $("order").innerHTML=ALL.map(p=>{const on=ORDER.includes(p);return `<div><input type="checkbox" id="pg_${p}" ${on?"checked":""} onchange="togglePage('${p}')" aria-label="${NAMES[p]}"><span>${NAMES[p]}</span>${on?`<button type="button" onclick="move('${p}',-1)" aria-label="上移">↑</button><button type="button" onclick="move('${p}',1)" aria-label="下移">↓</button>`:""}</div>`}).join("")}
function togglePage(p){ORDER=ORDER.includes(p)?ORDER.filter(x=>x!==p):[...ORDER,p];ALL=[...ORDER,...ALL.filter(x=>!ORDER.includes(x))];renderOrder()}
function move(p,d){const i=ORDER.indexOf(p),j=i+d;if(j<0||j>=ORDER.length)return;[ORDER[i],ORDER[j]]=[ORDER[j],ORDER[i]];ALL=[...ORDER,...ALL.filter(x=>!ORDER.includes(x))];renderOrder()}
async function load(){
  const {config:c,page_names}=await api("/api/config");NAMES=page_names;ORDER=[...c.pages];ALL=[...ORDER,...ALL.filter(x=>!ORDER.includes(x))];renderOrder();
  $("rotate").value=c.rotate_minutes;$("stocks").value=c.market.stocks.join(", ");$("fx").value=c.market.fx.join(", ");
  $("locname").value=c.location.name;$("lat").value=c.location.lat;$("lon").value=c.location.lon;
  $("nightOn").checked=c.night.enabled;$("nStart").value=c.night.start;$("nEnd").value=c.night.end;
  $("pcf").checked=c.eink.page_change_full;$("frm").value=c.eink.full_refresh_minutes;$("rot").checked=c.eink.rotate180;$("inv").checked=c.eink.invert;
}
const split=v=>v.split(/[\s,，、]+/).map(x=>x.trim()).filter(Boolean);
async function saveCfg(ev){
  ev.preventDefault();$("formErr").textContent="";
  if(!ORDER.length){$("formErr").textContent="至少要選一個頁面";return}
  const cfg={pages:ORDER,rotate_minutes:+$("rotate").value,
    market:{stocks:split($("stocks").value),fx:split($("fx").value.toUpperCase())},
    location:{name:$("locname").value.trim(),lat:+$("lat").value,lon:+$("lon").value},
    night:{enabled:$("nightOn").checked,start:$("nStart").value,end:$("nEnd").value},
    eink:{page_change_full:$("pcf").checked,full_refresh_minutes:+$("frm").value,rotate180:$("rot").checked,invert:$("inv").checked}};
  $("saveBtn").disabled=true;
  try{await api("/api/config",{config:cfg,restart:true});toast("已儲存，看板重新啟動中…");
    let n=0;const t=setInterval(async()=>{n++;try{await api("/api/status");if(n>3){clearInterval(t);$("saveBtn").disabled=false;toast("已套用新設定");load();poll()}}catch(e){}if(n>40){clearInterval(t);$("saveBtn").disabled=false}},1000)}
  catch(e){$("formErr").textContent=e.message;$("saveBtn").disabled=false}
}
pickKind("away");load().then(poll);setInterval(poll,10000);
</script></body></html>
"""
