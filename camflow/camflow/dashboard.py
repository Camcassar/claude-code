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
<style>
  :root { --bg:#0d1017; --card:#161b26; --text:#e8ecf4; --muted:#9aa4b8;
          --accent:#ee4236; --accent-soft:rgba(238,66,54,0.2); --green:#34c77b; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { background:var(--bg); color:var(--text); padding:32px 20px 60px;
         font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text",sans-serif;
         max-width:760px; margin:0 auto; }
  h1 { font-size:26px; display:flex; align-items:center; gap:12px; margin-bottom:24px; }
  h1 .dot { width:34px; height:34px; border-radius:50%; background:var(--accent);
            color:#fff; font-size:13px; font-weight:800; display:flex;
            align-items:center; justify-content:center; }
  h2 { font-size:13px; text-transform:uppercase; letter-spacing:1.2px;
       color:var(--muted); margin-bottom:12px; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
          gap:12px; margin-bottom:24px; }
  .stat { background:var(--card); border:1px solid rgba(255,255,255,0.07);
          border-radius:12px; padding:16px; }
  .stat .v { font-size:26px; font-weight:800; }
  .stat .l { color:var(--muted); font-size:12.5px; margin-top:4px; }
  .card { background:var(--card); border:1px solid rgba(255,255,255,0.07);
          border-radius:12px; padding:20px; margin-bottom:20px; }
  .chips { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:12px; }
  .chip { background:var(--accent-soft); color:var(--text); border-radius:16px;
          padding:5px 12px; font-size:14px; display:flex; gap:8px; align-items:center; }
  .chip button { background:none; border:none; color:var(--muted); cursor:pointer; font-size:14px; }
  .row { display:flex; gap:8px; }
  input { background:#0a0d13; border:1px solid rgba(255,255,255,0.1); color:var(--text);
          border-radius:8px; padding:9px 12px; font-size:14px; flex:1; }
  button.add { background:var(--accent); color:#fff; border:none; border-radius:8px;
               padding:9px 16px; font-weight:600; cursor:pointer; }
  table { width:100%; border-collapse:collapse; margin-bottom:12px; font-size:14px; }
  td { padding:7px 8px; border-bottom:1px solid rgba(255,255,255,0.06); }
  td.arrow { color:var(--muted); width:30px; text-align:center; }
  td.del { width:30px; } td.del button { background:none; border:none; color:var(--muted); cursor:pointer; }
  .recent { font-size:14px; color:var(--muted); line-height:1.7; }
  .recent b { color:var(--text); font-weight:600; }
  .hint { color:var(--muted); font-size:13px; margin-top:10px; line-height:1.5; }
  code { background:#0a0d13; border-radius:5px; padding:2px 6px; font-size:12.5px; color:var(--green); }
</style></head><body>
<h1><span class="dot">CC</span>CamFlow Dashboard</h1>

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
  document.getElementById('recent').innerHTML = st.recent.map(r =>
    `<div><b>${r.time}</b> — ${esc(r.text)}</div>`).join('') || 'No dictations yet.';
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
