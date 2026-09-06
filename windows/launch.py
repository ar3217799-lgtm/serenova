#!/usr/bin/env python3
"""Smart Sync — 施設内共有サーバー

親機（このexeを起動したPC）がデータを保持し、子機はブラウザで
http://<親機のIP>:8765/ を開くだけで同じデータを共有できる。
"""
import sys
import multiprocessing
multiprocessing.freeze_support()

import os
import re
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
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

HTML_PATH    = os.path.join(APP_DIR, 'SmartSync.html')
DATA_PATH    = os.path.join(APP_DIR, 'data.json')
DEVICES_PATH = os.path.join(APP_DIR, 'devices.json')
CONFIG_PATH  = os.path.join(APP_DIR, 'config.json')
BACKUP_DIR   = os.path.join(APP_DIR, '_backups')
LOG_PATH     = os.path.join(APP_DIR, 'smartsync.log')

DEFAULT_PORT = 8765
# ライセンス未導入時に使える端末数（従来どおり1台運用を壊さないため）
DEFAULT_MAX_DEVICES = 1

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    encoding='utf-8',
)


def log(msg):
    logging.info(msg)
    print(msg)


def read_config():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


CONFIG = read_config()
SERVER_PORT = int(CONFIG.get('port', DEFAULT_PORT))
MAX_DEVICES = int(CONFIG.get('maxDevices', DEFAULT_MAX_DEVICES))

# ── 共有状態（全アクセスを1つのロックで直列化） ──────────────────────────
_lock = threading.Lock()
_rev = 0            # データの版番号。保存のたびに増える
_data_str = None    # 現在のデータ本体（JSON文字列）


def _load_from_disk():
    """data.json を読む。旧形式（rev なし）も受け付けて移行する。"""
    global _rev, _data_str
    if not os.path.exists(DATA_PATH):
        _rev, _data_str = 0, None
        return
    try:
        with open(DATA_PATH, 'r', encoding='utf-8') as f:
            raw = f.read()
        obj = json.loads(raw)
        if isinstance(obj, dict) and 'rev' in obj and 'data' in obj:
            _rev = int(obj['rev'])
            _data_str = json.dumps(obj['data'], ensure_ascii=False)
        else:
            # 旧形式：中身をそのままデータとして扱う
            _rev = 1
            _data_str = raw
            log('data.json を新形式へ移行しました')
    except Exception as e:
        log(f'data.json 読込エラー: {e}')
        _rev, _data_str = 0, None


def _write_to_disk():
    """壊れかけのファイルを残さないよう、一時ファイル経由で置き換える。"""
    tmp = DATA_PATH + '.tmp'
    payload = {'rev': _rev, 'data': json.loads(_data_str) if _data_str else None}
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, DATA_PATH)


def _daily_backup():
    """1日1回、その日の最初の保存時にデータを退避（最新30日分を保持）。"""
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        name = f"data_{datetime.now().strftime('%Y%m%d')}.json"
        dest = os.path.join(BACKUP_DIR, name)
        if os.path.exists(dest) or not os.path.exists(DATA_PATH):
            return
        shutil.copy2(DATA_PATH, dest)
        olds = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith('data_'))
        for f in olds[:-30]:
            try:
                os.remove(os.path.join(BACKUP_DIR, f))
            except Exception:
                pass
        log(f'バックアップ作成: {name}')
    except Exception as e:
        log(f'バックアップ失敗: {e}')


# ── 端末登録 ────────────────────────────────────────────────────────────
def _read_devices():
    try:
        with open(DEVICES_PATH, 'r', encoding='utf-8') as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _write_devices(d):
    tmp = DEVICES_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(d, f, ensure_ascii=False, indent=1)
    os.replace(tmp, DEVICES_PATH)


def register_device(device_id, name, remote_ip):
    """端末を登録する。上限を超える新規端末は拒否する。"""
    if not device_id:
        return {'ok': False, 'reason': 'no_id'}
    with _lock:
        devs = _read_devices()
        now = datetime.now().isoformat(timespec='seconds')
        if device_id in devs:
            devs[device_id].update(lastSeen=now, ip=remote_ip)
            if name:
                devs[device_id]['name'] = name
            _write_devices(devs)
            return {'ok': True, 'used': len(devs), 'limit': MAX_DEVICES}
        if len(devs) >= MAX_DEVICES:
            log(f'端末数の上限により拒否: {device_id} ({remote_ip})')
            return {'ok': False, 'reason': 'limit_exceeded',
                    'used': len(devs), 'limit': MAX_DEVICES}
        devs[device_id] = {'name': name or remote_ip, 'ip': remote_ip,
                           'firstSeen': now, 'lastSeen': now}
        _write_devices(devs)
        log(f'端末を登録: {device_id} ({remote_ip}) {len(devs)}/{MAX_DEVICES}')
        return {'ok': True, 'used': len(devs), 'limit': MAX_DEVICES}


def device_allowed(device_id):
    if not device_id:
        return False
    return device_id in _read_devices()


def list_devices():
    devs = _read_devices()
    return {'limit': MAX_DEVICES, 'used': len(devs),
            'devices': [dict(id=k, **v) for k, v in devs.items()]}


def revoke_device(device_id):
    with _lock:
        devs = _read_devices()
        if device_id in devs:
            del devs[device_id]
            _write_devices(devs)
            log(f'端末を解除: {device_id}')
            return {'ok': True, 'used': len(devs), 'limit': MAX_DEVICES}
    return {'ok': False, 'reason': 'not_found'}


# ── データ API ──────────────────────────────────────────────────────────
def api_load_data():
    with _lock:
        return {'rev': _rev, 'json': _data_str}


def api_get_rev():
    with _lock:
        return {'rev': _rev}


def api_save_data(json_str, base_rev):
    """版番号が食い違う保存は拒否し、最新データを返す（上書き消失を防ぐ）。"""
    global _rev, _data_str
    with _lock:
        if _data_str is not None and base_rev is not None and int(base_rev) != _rev:
            return {'ok': False, 'conflict': True, 'rev': _rev, 'json': _data_str}
        try:
            json.loads(json_str)          # 壊れたJSONは書き込まない
        except Exception as e:
            return {'ok': False, 'reason': f'invalid_json: {e}'}
        _daily_backup()
        _data_str = json_str
        _rev += 1
        try:
            _write_to_disk()
        except Exception as e:
            log(f'保存エラー: {e}')
            return {'ok': False, 'reason': str(e)}
        return {'ok': True, 'rev': _rev}


def save_excel(filename, b64data):
    try:
        desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
        out = os.path.join(desktop, os.path.basename(filename))
        with open(out, 'wb') as f:
            f.write(base64.b64decode(b64data))
        return {'ok': True, 'path': out}
    except Exception as e:
        log(f'save_excel エラー: {e}')
        return {'ok': False, 'error': str(e)}


def update_app_fn(html_content, old_version, new_version):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    ts = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup = os.path.join(BACKUP_DIR, f'SmartSync_{old_version}_{ts}.html')
    try:
        shutil.copy2(HTML_PATH, backup)
        olds = sorted(f for f in os.listdir(BACKUP_DIR) if f.startswith('SmartSync_'))
        for f in olds[:-3]:
            try:
                os.remove(os.path.join(BACKUP_DIR, f))
            except Exception:
                pass
        with open(HTML_PATH, 'w', encoding='utf-8') as f:
            f.write(html_content)
        log(f'更新: v{old_version} → v{new_version}')
        return True
    except Exception as e:
        log(f'update_app エラー: {e}')
        try:
            shutil.copy2(backup, HTML_PATH)
        except Exception:
            pass
        return False


# ── ブラウザに注入するシム ──────────────────────────────────────────────
# 子機からも同じHTMLが配信されるため、接続先は location.origin を使う。
SHIM = r"""<script>
(function(){
  var BASE = location.origin;
  var REV = 0;
  var DEV = null;
  try {
    DEV = localStorage.getItem('smartsync_device_id');
    if (!DEV) {
      DEV = (crypto.randomUUID ? crypto.randomUUID()
            : String(Date.now()) + Math.random().toString(36).slice(2));
      localStorage.setItem('smartsync_device_id', DEV);
    }
  } catch (e) { DEV = 'nostorage-' + Math.random().toString(36).slice(2); }
  window.__SMARTSYNC_DEVICE_ID = DEV;

  function call(m, a) {
    a = a || {}; a.device_id = DEV;
    return fetch(BASE + '/api/' + m, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(a)
    }).then(function(r){ return r.json(); }).then(function(d){ return d.result; });
  }
  window.__smartsyncCall = call;
  window.__smartsyncIsHost = (location.hostname === 'localhost' ||
                              location.hostname === '127.0.0.1');

  window.pywebview = { api: {
    load_data: function(){
      return call('load_data', {}).then(function(r){
        if (r && r.rev !== undefined) { REV = r.rev; return r.json; }
        return null;
      });
    },
    save_data: function(j){
      return call('save_data', {json_str: j, base_rev: REV}).then(function(r){
        if (!r) return false;
        if (r.ok) { REV = r.rev; return true; }
        if (r.conflict) {
          // 他のPCが先に保存していた。最新を取り込んで画面を更新する
          REV = r.rev;
          window.dispatchEvent(new CustomEvent('smartsync-remote-data',
            {detail: {json: r.json, conflict: true}}));
        }
        return false;
      });
    },
    save_excel: function(f, b){ return call('save_excel', {filename: f, b64data: b}); },
    update_app: function(h, o, n){
      return call('update_app', {html_content: h, old_version: o, new_version: n})
        .then(function(r){ if (r) { setTimeout(function(){ location.reload(); }, 800); } return r; });
    },
    list_devices:  function(){ return call('list_devices', {}); },
    revoke_device: function(id){ return call('revoke_device', {target_id: id}); }
  }};

  // 他のPCの変更を取り込む（3秒ごとに版番号だけ確認する軽い問い合わせ）
  setInterval(function(){
    call('get_rev', {}).then(function(r){
      if (!r || r.rev === undefined || r.rev === REV) return;
      call('load_data', {}).then(function(d){
        if (!d || d.rev === undefined) return;
        REV = d.rev;
        window.dispatchEvent(new CustomEvent('smartsync-remote-data',
          {detail: {json: d.json, conflict: false}}));
      });
    }).catch(function(){});
  }, 3000);

  call('register_device', {name: navigator.platform || ''}).then(function(r){
    if (r && r.ok === false && r.reason === 'limit_exceeded') {
      document.documentElement.innerHTML =
        '<div style="font-family:sans-serif;padding:48px;max-width:620px;margin:0 auto;'
        + 'color:#1a2233;line-height:1.9">'
        + '<h2 style="color:#b4232a">利用できる台数の上限に達しています</h2>'
        + '<p>このパソコンは登録されていません。'
        + '（登録済み ' + r.used + ' 台 ／ 上限 ' + r.limit + ' 台）</p>'
        + '<p>親機の「端末管理」から使わなくなったパソコンを解除するか、'
        + '台数の追加についてご連絡ください。</p></div>';
    }
    window.dispatchEvent(new Event('pywebviewready'));
  }).catch(function(){ window.dispatchEvent(new Event('pywebviewready')); });
})();
</script>"""


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path not in ('/', '/index.html', ''):
            self.send_error(404)
            return
        try:
            with open(HTML_PATH, 'r', encoding='utf-8') as f:
                html = f.read()
            html = html.replace('<head>', '<head>' + SHIM, 1)
            self._send(200, html.encode('utf-8'), 'text/html; charset=utf-8')
        except Exception as e:
            log(f'GET エラー: {e}')
            self.send_error(500)

    def do_POST(self):
        try:
            n = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(n) or b'{}')
            path = urlparse(self.path).path
            did = body.get('device_id')
            ip = self.client_address[0]

            if path == '/api/register_device':
                result = register_device(did, body.get('name', ''), ip)
            elif path == '/api/list_devices':
                result = list_devices()
            elif path == '/api/revoke_device':
                result = revoke_device(body.get('target_id'))
            elif not device_allowed(did):
                # 未登録端末にはデータを渡さない
                result = {'ok': False, 'reason': 'not_registered'}
            elif path == '/api/load_data':
                result = api_load_data()
            elif path == '/api/get_rev':
                result = api_get_rev()
            elif path == '/api/save_data':
                result = api_save_data(body.get('json_str', ''), body.get('base_rev'))
            elif path == '/api/save_excel':
                result = save_excel(body.get('filename'), body.get('b64data'))
            elif path == '/api/update_app':
                result = update_app_fn(body.get('html_content'),
                                       body.get('old_version'),
                                       body.get('new_version'))
            else:
                self.send_error(404)
                return
            self._send(200, json.dumps({'result': result}, ensure_ascii=False).encode('utf-8'),
                       'application/json')
        except Exception as e:
            log(f'POST エラー: {e}')
            self.send_error(500)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    """複数PCからの同時接続をさばくため、リクエストごとにスレッドで処理する。"""
    daemon_threads = True
    allow_reuse_address = True


def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


def open_edge_app(url):
    for p in [
        os.path.expandvars(r'%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe'),
        os.path.expandvars(r'%ProgramFiles%\Microsoft\Edge\Application\msedge.exe'),
        os.path.expandvars(r'%LocalAppData%\Microsoft\Edge\Application\msedge.exe'),
    ]:
        if os.path.exists(p):
            return subprocess.Popen([p, f'--app={url}', '--window-size=1440,900'])
    import webbrowser
    webbrowser.open(url)
    return None


def main():
    log('=== Smart Sync 起動 ===')
    log(f'APP_DIR: {APP_DIR}')
    if not os.path.exists(HTML_PATH):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, f'SmartSync.html が見つかりません:\n{HTML_PATH}',
                'Smart Sync — エラー', 0x10)
        except Exception:
            pass
        sys.exit(1)

    _load_from_disk()
    log(f'データ読込: rev={_rev}')
    log(f'端末上限: {MAX_DEVICES} 台（登録済み {len(_read_devices())} 台）')

    try:
        server = ThreadedHTTPServer(('0.0.0.0', SERVER_PORT), Handler)
    except OSError as e:
        msg = (f'ポート {SERVER_PORT} を使用できません。\n'
               f'Smart Sync が既に起動していないか確認してください。\n\n{e}')
        log(msg)
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, 'Smart Sync — エラー', 0x10)
        except Exception:
            pass
        sys.exit(1)

    threading.Thread(target=server.serve_forever, daemon=True).start()
    ip = local_ip()
    log(f'親機として起動しました: http://{ip}:{SERVER_PORT}/')
    log(f'子機からは上記URLをブラウザで開いてください')

    time.sleep(0.8)
    open_edge_app(f'http://localhost:{SERVER_PORT}/')

    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass
    server.shutdown()


if __name__ == '__main__':
    main()
