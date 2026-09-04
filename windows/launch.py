#!/usr/bin/env python3
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
import logging
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

HTML_PATH = os.path.join(APP_DIR, 'SmartSync.html')
DATA_PATH = os.path.join(APP_DIR, 'data.json')
LOG_PATH  = os.path.join(APP_DIR, 'smartsync.log')

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s %(message)s',
    encoding='utf-8',
)

def log(msg):
    logging.info(msg)
    print(msg)

SHIM_TEMPLATE = """<script>
(function(){{
  var BASE='http://localhost:{port}';
  function call(m,a){{
    return fetch(BASE+'/api/'+m,{{
      method:'POST',
      headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify(a||{{}})
    }}).then(function(r){{return r.json()}}).then(function(d){{return d.result}});
  }}
  window.pywebview={{api:{{
    save_data:  function(j){{return call('save_data',{{json_str:j}})}},
    load_data:  function(){{return call('load_data',{{}})}},
    save_excel: function(f,b){{return call('save_excel',{{filename:f,b64data:b}})}},
    update_app: function(h,o,n){{
      return call('update_app',{{html_content:h,old_version:o,new_version:n}})
        .then(function(r){{if(r){{setTimeout(function(){{location.reload()}},800);}}return r;}});
    }}
  }}}};
  window.dispatchEvent(new Event('pywebviewready'));
}})();
</script>"""

def save_data(json_str):
    try:
        with open(DATA_PATH, 'w', encoding='utf-8') as f:
            f.write(json_str)
        return True
    except Exception as e:
        log(f'save_data error: {e}')
        return False

def load_data():
    try:
        if not os.path.exists(DATA_PATH):
            return None
        with open(DATA_PATH, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception as e:
        log(f'load_data error: {e}')
        return None

def save_excel(filename, b64data):
    try:
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        out_path = os.path.join(desktop, filename)
        with open(out_path, 'wb') as f:
            f.write(base64.b64decode(b64data))
        return out_path
    except Exception as e:
        log(f'save_excel error: {e}')
        return None

def update_app_fn(html_content, old_version, new_version):
    backup_dir = os.path.join(APP_DIR, '_backups')
    os.makedirs(backup_dir, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = os.path.join(backup_dir, f'SmartSync_backup_{old_version}_{ts}.html')
    try:
        shutil.copy2(HTML_PATH, backup)
        # 古いバックアップを削除（最新3件だけ保持）
        old_backups = sorted(
            [f for f in os.listdir(backup_dir) if f.startswith('SmartSync_backup_')],
        )
        for f in old_backups[:-3]:
            try:
                os.remove(os.path.join(backup_dir, f))
            except Exception:
                pass
        with open(HTML_PATH, 'w', encoding='utf-8') as f:
            f.write(html_content)
        log(f'update: v{old_version} → v{new_version}')
        return True
    except Exception as e:
        log(f'update_app error: {e}')
        try:
            shutil.copy2(backup, HTML_PATH)
        except Exception:
            pass
        return False

SERVER_PORT = None

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST,GET,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if urlparse(self.path).path in ('/', '/index.html', ''):
            try:
                with open(HTML_PATH, 'r', encoding='utf-8') as f:
                    html = f.read()
                shim = SHIM_TEMPLATE.format(port=SERVER_PORT)
                html = html.replace('<head>', '<head>' + shim, 1)
                content = html.encode('utf-8')
                self.send_response(200)
                self.send_header('Content-Type', 'text/html; charset=utf-8')
                self.send_header('Content-Length', str(len(content)))
                self.end_headers()
                self.wfile.write(content)
            except Exception as e:
                log(f'GET error: {e}')
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
            log(f'POST error: {e}')
            self.send_error(500)

def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('', 0))
        return s.getsockname()[1]

def open_edge_app(url):
    for path in [
        os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe'),
        os.path.expandvars(r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe'),
        os.path.expandvars(r'%LocalAppData%\Microsoft\Edge\Application\msedge.exe'),
    ]:
        if os.path.exists(path):
            log(f'Edge found: {path}')
            return subprocess.Popen([path, f'--app={url}', '--window-size=1440,900'])
    import webbrowser
    log('Edge not found, using default browser')
    webbrowser.open(url)
    return None

def main():
    global SERVER_PORT
    log(f'=== Smart Sync 起動 ===')
    log(f'APP_DIR: {APP_DIR}')
    log(f'HTML_PATH: {HTML_PATH}')
    log(f'HTML exists: {os.path.exists(HTML_PATH)}')

    if not os.path.exists(HTML_PATH):
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            f'SmartSync.html が見つかりません:\n{HTML_PATH}',
            'Smart Sync — エラー', 0x10
        )
        sys.exit(1)

    try:
        SERVER_PORT = find_free_port()
        log(f'PORT: {SERVER_PORT}')
        # 0.0.0.0 でバインド（127.0.0.1 が拒否される環境への対応）
        server = HTTPServer(('0.0.0.0', SERVER_PORT), Handler)
        log('Server created OK')
        t = threading.Thread(target=server.serve_forever, daemon=True)
        t.start()
        log('Server thread started')
    except Exception as e:
        log(f'Server start FAILED: {e}')
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0, f'サーバー起動に失敗しました:\n{e}', 'Smart Sync — エラー', 0x10
        )
        sys.exit(1)

    time.sleep(1.0)  # サーバー安定待ち

    url = f'http://localhost:{SERVER_PORT}/'
    log(f'Opening Edge: {url}')
    open_edge_app(url)

    # Edge が既存インスタンスに引き渡して即終了しても
    # サーバーは動き続けてページに応答できるようにする
    log('Server running. Close this process from Task Manager to exit.')
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass

    log('App closed')
    server.shutdown()

if __name__ == '__main__':
    main()
