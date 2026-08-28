#!/usr/bin/env python3
"""
Smart Sync — Windows ランチャー（PyWebView 不使用）
Python の HTTP サーバー経由で SmartSync.html を配信し、
Edge をアプリモード（タブ・アドレスバーなし）で起動する。
"""
import sys
import multiprocessing
multiprocessing.freeze_support()

import os
import json
import base64
import shutil
import socket
import threading
import subprocess
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

# ── パス解決 ──────────────────────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

HTML_PATH = os.path.join(APP_DIR, 'SmartSync.html')
DATA_PATH = os.path.join(APP_DIR, 'data.json')

# ── JS shim: window.pywebview.api を fetch API に変換 ─────────────────────────
SHIM = """<script>
(function(){
  var BASE='http://127.0.0.1:__PORT__';
  function call(method,args){
    return fetch(BASE+'/api/'+method,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify(args||{})
    }).then(function(r){return r.json()}).then(function(d){return d.result});
  }
  window.pywebview={api:{
    save_data:  function(j){return call('save_data',{json_str:j})},
    load_data:  function(){return call('load_data',{})},
    save_excel: function(f,b){return call('save_excel',{filename:f,b64data:b})},
    update_app: function(h,o,n){
      return call('update_app',{html_content:h,old_version:o,new_version:n})
        .then(function(r){
          if(r){setTimeout(function(){location.reload()},800);}
          return r;
        });
    }
  }};
  window.dispatchEvent(new Event('pywebviewready'));
})();
</script>"""

# ── API 実装 ──────────────────────────────────────────────────────────────────
def save_data(json_str):
    try:
        with open(DATA_PATH, 'w', encoding='utf-8') as f:
            f.write(json_str)
        return True
    except Exception as e:
        print('save_data error:', e)
        return False

def load_data():
    try:
        if not os.path.exists(DATA_PATH):
            return None
        with open(DATA_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        print('load_data error:', e)
        return None

def save_excel(filename, b64data):
    try:
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        out_path = os.path.join(desktop, filename)
        with open(out_path, 'wb') as f:
            f.write(base64.b64decode(b64data))
        return out_path
    except Exception as e:
        print('save_excel error:', e)
        return None

def update_app_fn(html_content, old_version, new_version):
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = os.path.join(APP_DIR, f'SmartSync_backup_{old_version}_{ts}.html')
    try:
        shutil.copy2(HTML_PATH, backup)
        with open(HTML_PATH, 'w', encoding='utf-8') as f:
            f.write(html_content)
        print(f'update: v{old_version} → v{new_version}')
        return True
    except Exception as e:
        print('update_app error:', e)
        try:
            shutil.copy2(backup, HTML_PATH)
        except Exception:
            pass
        return False

# ── HTTP サーバー ─────────────────────────────────────────────────────────────
SERVER_PORT = None

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # ログ抑制

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, GET, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if urlparse(self.path).path in ('/', '/index.html', ''):
            try:
                with open(HTML_PATH, 'r', encoding='utf-8') as f:
                    html = f.read()
                shim = SHIM.replace('__PORT__', str(SERVER_PORT))
                html = html.replace('<head>', '<head>' + shim, 1)
                content = html.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                self.send_error(500, str(e))
        else:
            self.send_error(404)

    def do_POST(self):
        try:
            length = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(length) or b'{}')
            path = urlparse(self.path).path

            result = None
            if path == '/api/save_data':
                result = save_data(body.get('json_str', ''))
            elif path == '/api/load_data':
                result = load_data()
            elif path == '/api/save_excel':
                result = save_excel(body.get('filename'), body.get('b64data'))
            elif path == '/api/update_app':
                result = update_app_fn(
                    body.get('html_content'),
                    body.get('old_version'),
                    body.get('new_version'),
                )

            resp = json.dumps({'result': result}).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(resp)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(resp)
        except Exception as e:
            print('POST error:', e)
            self.send_error(500)

# ── 起動ロジック ──────────────────────────────────────────────────────────────
def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

def open_edge_app(url):
    for path in [
        os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe'),
        os.path.expandvars(r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe'),
        os.path.expandvars(r'%LocalAppData%\Microsoft\Edge\Application\msedge.exe'),
    ]:
        if os.path.exists(path):
            return subprocess.Popen([
                path,
                f'--app={url}',
                '--window-size=1440,900',
                '--disable-extensions',
            ])
    # Edge が見つからない場合はデフォルトブラウザ
    import webbrowser
    webbrowser.open(url)
    return None

def main():
    global SERVER_PORT

    if not os.path.exists(HTML_PATH):
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            f'SmartSync.html が見つかりません:\n{HTML_PATH}\n\n'
            'exe と同じフォルダに SmartSync.html を置いてください。',
            'Smart Sync — エラー', 0x10
        )
        sys.exit(1)

    SERVER_PORT = find_free_port()
    server = HTTPServer(('127.0.0.1', SERVER_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()

    time.sleep(0.3)
    proc = open_edge_app(f'http://127.0.0.1:{SERVER_PORT}/')

    if proc:
        proc.wait()
    else:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass

    server.shutdown()

if __name__ == '__main__':
    main()
