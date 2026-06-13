"""Local web dashboard — stats, dictionary, and replacements editor.

Served only on 127.0.0.1 (your Mac); nothing is exposed to the network and
no account/login is needed. Open it from the menu bar or visit
http://localhost:4242 in any browser.
"""

from __future__ import annotations

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PAGE = """<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>CamFlow Dashboard</title>
<link rel="icon" href='data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><circle cx="32" cy="32" r="30" fill="%23ee4236"/><text x="32" y="41" font-family="Arial,sans-serif" font-size="24" font-weight="800" fill="white" text-anchor="middle">CC</text></svg>'>
<style>
  :root { --bg:#0b0e15; --card:#161b26; --card2:#1b2130; --text:#e8ecf4;
          --muted:#9aa4b8; --accent:#ee4236; --accent-soft:rgba(238,66,54,0.18);
          --green:#34c77b; --border:rgba(255,255,255,0.08); }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:radial-gradient(900px 500px at 15% -10%, #1a2233 0%, var(--bg) 55%);
         color:var(--text); padding:36px 20px 72px; min-height:100vh;
         font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",sans-serif;
         max-width:780px; margin:0 auto; }
  h1 { font-size:27px; font-weight:800; display:flex; align-items:center; gap:13px; margin-bottom:6px; }
  h1 .dot { width:38px; height:38px; border-radius:50%;
            background:linear-gradient(160deg,#fa6055,#c41f15);
            box-shadow:0 0 0 6px var(--accent-soft);
            color:#fff; font-size:14px; font-weight:800; display:flex;
            align-items:center; justify-content:center; }
  .sub { color:var(--muted); font-size:14px; margin-bottom:26px; padding-left:51px; }
  h2 { font-size:12.5px; text-transform:uppercase; letter-spacing:1.3px;
       color:var(--muted); margin-bottom:14px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr));
          gap:12px; margin-bottom:26px; }
  .stat { background:var(--card); border:1px solid var(--border); border-radius:14px;
          padding:18px; transition:transform .12s, border-color .12s; }
  .stat:hover { transform:translateY(-2px); border-color:rgba(238,66,54,0.4); }
  .stat .v { font-size:27px; font-weight:800; letter-spacing:-0.5px; }
  .stat .l { color:var(--muted); font-size:12.5px; margin-top:5px; }
  .card { background:var(--card); border:1px solid var(--border);
          border-radius:16px; padding:22px; margin-bottom:20px; }
  .chips { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:14px; }
  .chip { background:var(--accent-soft); color:var(--text); border-radius:16px;
          padding:6px 13px; font-size:14px; display:flex; gap:8px; align-items:center; }
  .chip button { background:none; border:none; color:var(--muted); cursor:pointer; font-size:14px; line-height:1; }
  .row { display:flex; gap:8px; }
  input { background:#0a0d13; border:1px solid var(--border); color:var(--text);
          border-radius:9px; padding:10px 13px; font-size:14px; flex:1; }
  input:focus { outline:none; border-color:var(--accent); }
  button.add { background:var(--accent); color:#fff; border:none; border-radius:9px;
               padding:10px 18px; font-weight:600; cursor:pointer; transition:background .12s; }
  button.add:hover { background:#d63a2f; }
  table { width:100%; border-collapse:collapse; margin-bottom:14px; font-size:14px; }
  td { padding:8px; border-bottom:1px solid var(--border); }
  td.arrow { color:var(--muted); width:30px; text-align:center; }
  td.del { width:30px; } td.del button { background:none; border:none; color:var(--muted); cursor:pointer; }
  .rec { display:flex; align-items:flex-start; gap:12px; padding:11px 13px;
         background:var(--card2); border:1px solid var(--border); border-radius:10px;
         margin-bottom:8px; }
  .rec .t { color:var(--muted); font-size:12.5px; font-variant-numeric:tabular-nums;
            padding-top:2px; min-width:42px; }
  .rec .txt { flex:1; font-size:14.5px; line-height:1.5; }
  .rec .copy { background:var(--accent-soft); color:var(--text); border:none;
               border-radius:7px; padding:5px 11px; font-size:12.5px; font-weight:600;
               cursor:pointer; flex-shrink:0; transition:background .12s; }
  .rec .copy:hover { background:var(--accent); color:#fff; }
  .hint { color:var(--muted); font-size:13px; margin-top:10px; line-height:1.5; }
</style></head><body>
<h1><span class="dot">CC</span>CamFlow</h1>
<div class="sub">Your local dictation dashboard — private to this Mac.</div>

<div class="grid" id="stats"></div>

<div class="card">
  <h2>Dictionary — names &amp; slang Whisper should know</h2>
  <div class="chips" id="dict"></div>
  <div class="row"><input id="dictInput" placeholder="e.g. Camcassar, CamFlow, gonna send it">
  <button class="add" onclick="addDict()">Add</button></div>
  <div class="hint">These words are fed to Whisper as context so it spells them right.</div>
</div>

<div class="card">
  <h2>Replacements — heard → typed</h2>
  <table id="repl"></table>
  <div class="row">
    <input id="replFrom" placeholder="when I say… (e.g. new line)">
    <input id="replTo" placeholder="type this… (use \\n for newline)">
    <button class="add" onclick="addRepl()">Add</button>
  </div>
</div>

<div class="card">
  <h2>Recent dictations</h2>
  <div class="recent" id="recent"></div>
</div>

<script>
async function load() {
  const s = await (await fetch('/api/state')).json();
  const st = s.stats;
  document.getElementById('stats').innerHTML = [
    [st.total_words.toLocaleString(), 'words dictated'],
    [st.words_today.toLocaleString(), 'words today'],
    [st.total_dictations.toLocaleString(), 'dictations'],
    [st.avg_words, 'avg words each'],
    ['~' + st.minutes_saved + ' min', 'typing time saved'],
  ].map(([v,l]) => `<div class="stat"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');
  document.getElementById('dict').innerHTML = s.config.dictionary.map(w =>
    `<span class="chip">${esc(w)}<button onclick="rmDict('${esc(w)}')">✕</button></span>`).join('')
    || '<span class="hint">Nothing yet — add your name, projects, slang.</span>';
  document.getElementById('repl').innerHTML = Object.entries(s.config.replacements).map(([k,v]) =>
    `<tr><td>${esc(k)}</td><td class="arrow">→</td><td>${esc(JSON.stringify(v).slice(1,-1))}</td>
     <td class="del"><button onclick="rmRepl('${esc(k)}')">✕</button></td></tr>`).join('');
  document.getElementById('recent').innerHTML = st.recent.length ? st.recent.map((r,i) =>
    `<div class="rec"><span class="t">${r.time}</span>
     <span class="txt" id="rec${i}">${esc(r.text)}</span>
     <button class="copy" onclick="copyRec(${i},this)">Copy</button></div>`).join('')
    : '<div class="hint">No dictations yet — hold Right Option and talk.</div>';
}
function copyRec(i, btn){
  const t = document.getElementById('rec'+i).textContent;
  navigator.clipboard.writeText(t).then(()=>{ btn.textContent='Copied!'; setTimeout(()=>btn.textContent='Copy',1200); });
}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
async function post(url, body){ await fetch(url,{method:'POST',body:JSON.stringify(body)}); load(); }
function addDict(){ const i=document.getElementById('dictInput'); if(i.value.trim()) post('/api/dictionary',{add:i.value.trim()}); i.value=''; }
function rmDict(w){ post('/api/dictionary',{remove:w}); }
function addRepl(){ const f=document.getElementById('replFrom'), t=document.getElementById('replTo');
  if(f.value.trim()) post('/api/replacements',{add:[f.value.trim(), t.value.replace(/\\\\n/g,'\\n')]}); f.value=''; t.value=''; }
function rmRepl(k){ post('/api/replacements',{remove:k}); }
load(); setInterval(load, 5000);
</script></body></html>
"""


def start_dashboard(config, stats) -> str:
    """Start the dashboard server in a background thread; return its URL."""

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args) -> None:
            pass  # keep the terminal clean for dictation logs

        def _send(self, body: bytes, content_type: str) -> None:
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            password = config.dashboard_password
            if not password:
                return True
            auth = self.headers.get("Authorization", "")
            if auth.startswith("Basic "):
                try:
                    decoded = base64.b64decode(auth[6:]).decode()
                    return decoded.split(":", 1)[-1] == password
                except Exception:
                    pass
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="CamFlow"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def do_GET(self) -> None:
            if not self._authorized():
                return
            if self.path == "/api/state":
                state = {
                    "stats": stats.summary(),
                    "config": {
                        "dictionary": config.dictionary,
                        "replacements": config.replacements,
                        "hotkey": config.hotkey,
                    },
                }
                self._send(json.dumps(state).encode(), "application/json")
            else:
                self._send(PAGE.encode(), "text/html; charset=utf-8")

        def do_POST(self) -> None:
            if not self._authorized():
                return
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                body = {}
            if self.path == "/api/dictionary":
                word = body.get("add")
                if word and word not in config.dictionary:
                    config.dictionary.append(word)
                if body.get("remove") in config.dictionary:
                    config.dictionary.remove(body["remove"])
                config.save_collections()
            elif self.path == "/api/replacements":
                if isinstance(body.get("add"), list) and len(body["add"]) == 2:
                    config.replacements[body["add"][0]] = body["add"][1]
                config.replacements.pop(body.get("remove"), None)
                config.save_collections()
            self._send(b"{}", "application/json")

    server = ThreadingHTTPServer(("127.0.0.1", config.dashboard_port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://localhost:{config.dashboard_port}"
    print(f"dashboard: {url}")
    return url
