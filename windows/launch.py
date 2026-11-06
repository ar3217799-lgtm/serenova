#!/usr/bin/env python3
"""Smart Sync — 施設内共有サーバー

親機（このexeを起動したPC）がデータを保持し、子機はブラウザで
http://<親機のIP>:8765/ を開くだけで同じデータを共有できる。

台数の上限は license.key（開発元が発行する署名付きファイル）で決まる。
license.key が無い場合はお試し運用として1台のみで動作する。
"""
import sys
import multiprocessing
multiprocessing.freeze_support()

import os
import json
import base64
import shutil
import socket
import secrets
import threading
import subprocess
import time
import logging
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse

# ── どのフォルダのデータで動くかを決める ─────────────────────────────────
# SmartSync.exe は、いつも使っているフォルダ（data.json・license.key がある所）で
# 動かす必要がある。exe だけをデスクトップ等に置いて起動すると、以前は
# 「SmartSync.html が見つかりません」で止まり、HTML を置けば今度は空のデータで
# 動き出して入力が本来のデータと分かれてしまっていた。データの無い場所で
# 起動された場合は、このPCの中からいつものフォルダを探し、確認してから
# そのデータで起動する（そのフォルダの古い exe もこの exe に置き換える）。
def _msgbox(text, title, flags):
    # 自動テスト用：確認ダイアログに「はい」と答えたことにする
    if os.environ.get('SMARTSYNC_TEST_AUTOYES') == '1':
        print('MSGBOX ' + title + ': ' + text.replace('\n', ' '), flush=True)
        return 6
    try:
        import ctypes
        return ctypes.windll.user32.MessageBoxW(0, text, title, flags)
    except Exception:
        return 0


def _looks_like_smartsync_data(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            obj = json.load(f)
        d = obj.get('data') if isinstance(obj, dict) and 'data' in obj else obj
        return isinstance(d, dict) and any(k in d for k in ('users', 'contactNotes', 'dayMasters'))
    except Exception:
        return False


_SKIP_DIRS = {'appdata', 'windows', 'program files', 'program files (x86)', 'programdata',
              '$recycle.bin', 'system volume information', 'recovery', 'perflogs',
              'node_modules', '__pycache__', 'smartsyncバックアップ'}


def _find_existing_installs(exclude):
    """このPCの中から、SmartSync のデータがあるフォルダを探す（新しい順）。"""
    home = os.path.expanduser('~')
    roots = [(home, 5)] + [(f'{d}:\\', 3) for d in 'CDEFG' if os.path.isdir(f'{d}:\\')]
    ex = os.path.normcase(os.path.abspath(exclude))
    home_n = os.path.normcase(os.path.abspath(home))
    seen, found = set(), []
    t0 = time.time()
    for root, maxdepth in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            if time.time() - t0 > 30:
                break
            rel = os.path.relpath(dirpath, root)
            depth = 0 if rel == '.' else rel.count(os.sep) + 1
            if depth >= maxdepth:
                dirnames[:] = []
            else:
                dirnames[:] = [d for d in dirnames
                               if d.lower() not in _SKIP_DIRS and not d.startswith('.')
                               and os.path.normcase(os.path.join(dirpath, d)) != home_n]
            if 'data.json' not in filenames:
                continue
            if not any(x in filenames for x in ('license.key', 'config.json', 'SmartSync.exe', 'SmartSync.html')):
                continue
            n = os.path.normcase(os.path.abspath(dirpath))
            if n == ex or n in seen:
                continue
            seen.add(n)
            if _looks_like_smartsync_data(os.path.join(dirpath, 'data.json')):
                found.append(dirpath)
    found.sort(key=lambda d: os.path.getmtime(os.path.join(d, 'data.json')), reverse=True)
    return found


def _resolve_app_dir(here):
    if not getattr(sys, 'frozen', False) and os.environ.get('SMARTSYNC_FORCE_RESOLVE') != '1':
        return here
    if os.path.exists(os.path.join(here, 'data.json')) or os.path.exists(os.path.join(here, 'license.key')):
        return here
    found = _find_existing_installs(here)
    if found:
        target = found[0]
        r = _msgbox('この場所には SmartSync のデータがありません。\n'
                    f'{here}\n\n'
                    'いつも使っているデータが見つかりました。\n'
                    f'{target}\n\n'
                    'こちらのデータで起動しますか？（通常は「はい」を押してください）',
                    'Smart Sync', 0x24)          # はい／いいえ・？アイコン
        if r != 6:
            sys.exit(0)
        if getattr(sys, 'frozen', False):
            # いつものフォルダの古い exe をこの exe に置き換える（次回からはいつもの
            # ショートカットでも新しい版が動く）。古い exe が動いていて置き換えられない
            # 場合はそのまま続ける（後のポート確認で、動いている方の画面を開く）。
            dest = os.path.join(target, 'SmartSync.exe')
            try:
                if os.path.normcase(os.path.abspath(dest)) != os.path.normcase(os.path.abspath(sys.executable)):
                    shutil.copy2(sys.executable, dest)
            except Exception:
                pass
        return target
    r = _msgbox('SmartSync のデータ（data.json）がこのパソコンに見つかりません。\n\n'
                '新しく使い始める場合は「はい」を押してください。\n\n'
                '既に使っている場合は「いいえ」を押し、この SmartSync.exe を、いつものフォルダ\n'
                '（data.json と license.key が入っているフォルダ）に置いてから起動してください。',
                'Smart Sync', 0x34)              # はい／いいえ・！アイコン
    if r != 6:
        sys.exit(0)
    return here


_HERE = os.path.dirname(sys.executable) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__))
APP_DIR = _resolve_app_dir(_HERE)
sys.path.insert(0, APP_DIR if getattr(sys, 'frozen', False) else os.path.dirname(os.path.abspath(__file__)))

import licensing  # noqa: E402  (公開鍵のみを含む検証モジュール)

HTML_PATH    = os.path.join(APP_DIR, 'SmartSync.html')
DATA_PATH    = os.path.join(APP_DIR, 'data.json')
DEVICES_PATH = os.path.join(APP_DIR, 'devices.json')
CONFIG_PATH  = os.path.join(APP_DIR, 'config.json')
LICENSE_PATH = os.path.join(APP_DIR, 'license.key')
# バックアップはデスクトップ直下に個別ファイルを増やさず、1つのフォルダに
# まとめる（デスクトップが散らからないようにするため）。
BACKUP_DIR   = os.path.join(os.path.expanduser('~'), 'Desktop', 'SmartSyncバックアップ')
LOG_PATH     = os.path.join(APP_DIR, 'smartsync.log')

DEFAULT_PORT = 8765

logging.basicConfig(
    filename=LOG_PATH,
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    encoding='utf-8',
)


def log(msg):
    logging.info(msg)
    print(msg)


# ── 設定ファイル（ポート番号・ペアリングコードを保持） ────────────────────
def _read_config():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _write_config(cfg):
    tmp = CONFIG_PATH + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, ensure_ascii=False, indent=1)
    os.replace(tmp, CONFIG_PATH)


def _new_pair_code():
    return f'{secrets.randbelow(1_000_000):06d}'


CONFIG = _read_config()
_config_changed = False
if 'port' not in CONFIG:
    CONFIG['port'] = DEFAULT_PORT
    _config_changed = True
if not CONFIG.get('pairCode'):
    # 初回起動時に自動生成。以降はこの施設固有のコードとして使い回す。
    CONFIG['pairCode'] = _new_pair_code()
    _config_changed = True
if _config_changed:
    _write_config(CONFIG)

SERVER_PORT = int(CONFIG['port'])

LICENSE = licensing.load_license(LICENSE_PATH)
MAX_DEVICES = LICENSE['maxDevices']

# ── 共有状態（全アクセスを1つのロックで直列化） ──────────────────────────
_lock = threading.Lock()
_rev = 0            # データの版番号。保存のたびに増える
_data_str = None    # 現在のデータ本体（JSON文字列）
# data.json が存在するのに読めなかった。この状態で保存を受け付けると、空の
# データ（または1台のPCの控え）で data.json を上書きしてしまうため、保存を拒否する。
_load_failed = None
HOST_IP = None       # このPC自身のLAN上のIP（起動時に確定）


def _load_from_disk():
    """data.json を読む。旧形式（rev なし）も受け付けて移行する。

    PCの起動直後はウイルス対策ソフトや同期ソフトが一時的にファイルを掴んでいて
    読めないことがあるため、何度か待って読み直す。それでも読めない場合は
    以前のように「データ無し」で動き出さない（最初の保存で data.json が
    空や1台分のデータで上書きされ、全員の入力が消えるため）。保存を拒否する。"""
    global _rev, _data_str, _load_failed
    if not os.path.exists(DATA_PATH):
        _rev, _data_str = 0, None
        return
    last_err = None
    for _ in range(20):
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
            _load_failed = None
            return
        except Exception as e:
            last_err = e
            time.sleep(0.5)
    _load_failed = str(last_err)
    log(f'data.json 読込エラー（保存を停止します）: {last_err}')
    _rev, _data_str = 0, None


def _write_to_disk():
    """壊れかけのファイルを残さないよう、一時ファイル経由で置き換える。"""
    tmp = DATA_PATH + '.tmp'
    payload = {'rev': _rev, 'data': json.loads(_data_str) if _data_str else None}
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    # Windows ではウイルス対策ソフト等が data.json を一瞬掴んでいると置き換えに
    # 失敗する（PermissionError）。少し待って何度か試す。
    for i in range(20):
        try:
            os.replace(tmp, DATA_PATH)
            return
        except PermissionError:
            if i == 19:
                raise
            time.sleep(0.1)


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
# 端末一覧はメモリに持つ。以前はリクエストのたびに devices.json を読み直して
# いたため、別のリクエストが書き換えている瞬間（Windowsでは置き換え中のファイル
# が読めないことがある）に「端末なし」と誤読し、登録済みのPCが弾かれたり、
# 端末登録の書き込みで他のPCの登録を消してしまうことがあった。
_devices_cache = None


def _load_devices_file():
    if not os.path.exists(DEVICES_PATH):
        return {}
    last_err = None
    for _ in range(20):
        try:
            with open(DEVICES_PATH, 'r', encoding='utf-8') as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
        except Exception as e:
            last_err = e
            time.sleep(0.1)
    log(f'devices.json 読込エラー: {last_err}')
    return {}


def _read_devices():
    global _devices_cache
    if _devices_cache is None:
        _devices_cache = _load_devices_file()
    return dict(_devices_cache)


def _write_devices(d):
    global _devices_cache
    _devices_cache = dict(d)
    tmp = DEVICES_PATH + '.tmp'
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
        for i in range(20):
            try:
                os.replace(tmp, DEVICES_PATH)
                break
            except PermissionError:
                if i == 19:
                    raise
                time.sleep(0.1)
    except Exception as e:
        # 書けなくても、メモリ上の一覧で動作は続ける
        log(f'devices.json 書込エラー: {e}')


def is_host_ip(ip):
    return ip in ('127.0.0.1', '::1', HOST_IP)


def register_device(device_id, name, remote_ip, pair_code):
    """端末を登録する。台数上限を超える新規端末、コード不一致の新規端末は拒否する。

    親機自身（127.0.0.1／自ホストIP）からの接続はペアリングコード不要。
    """
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

        if not is_host_ip(remote_ip):
            required = CONFIG.get('pairCode')
            if required and pair_code != required:
                reason = 'pair_code_required' if not pair_code else 'pair_code_invalid'
                log(f'ペアリングコード不一致で拒否: {device_id} ({remote_ip})')
                return {'ok': False, 'reason': reason}

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
    with _lock:
        return device_id in _read_devices()


def list_devices():
    with _lock:
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


def get_license_info():
    return {
        'present': LICENSE['present'], 'valid': LICENSE['valid'],
        'expired': LICENSE['expired'], 'reason': LICENSE['reason'],
        'facility': LICENSE['facility'], 'maxDevices': MAX_DEVICES,
        'issuedAt': LICENSE['issuedAt'], 'expiresAt': LICENSE['expiresAt'],
        'used': len(_read_devices()),
    }


def get_pair_code():
    return {'pairCode': CONFIG.get('pairCode', '')}


def regenerate_pair_code():
    with _lock:
        CONFIG['pairCode'] = _new_pair_code()
        _write_config(CONFIG)
        log('ペアリングコードを再発行しました')
        return {'pairCode': CONFIG['pairCode']}


# ── データ API ──────────────────────────────────────────────────────────
def api_load_data():
    with _lock:
        if _load_failed:
            # 空のデータを返すと、各PCの画面が空になったり、その状態から保存されて
            # data.json が上書きされたりするため、データは渡さない（画面は手元の
            # 表示のまま、入力は各PCに保存され続ける）。
            return {'ok': False, 'reason': f'data_load_failed: {_load_failed}'}
        return {'rev': _rev, 'json': _data_str}


def api_get_rev():
    with _lock:
        if _load_failed:
            return {'ok': False, 'reason': f'data_load_failed: {_load_failed}'}
        return {'rev': _rev}


def api_save_data(json_str, base_rev):
    """版番号が食い違う保存は拒否し、最新データを返す（上書き消失を防ぐ）。"""
    global _rev, _data_str
    with _lock:
        if _load_failed:
            return {'ok': False, 'reason': f'data_load_failed: {_load_failed}'}
        if _data_str is not None and base_rev is not None and int(base_rev) != _rev:
            return {'ok': False, 'conflict': True, 'rev': _rev, 'json': _data_str}
        try:
            json.loads(json_str)          # 壊れたJSONは書き込まない
        except Exception as e:
            return {'ok': False, 'reason': f'invalid_json: {e}'}
        _daily_backup()
        prev_rev, prev_str = _rev, _data_str
        _data_str = json_str
        _rev += 1
        try:
            _write_to_disk()
        except Exception as e:
            # ディスクに書けなかったのにメモリだけ進めると、他のPCには見えるのに
            # 親機の再起動で消える状態になる。メモリも元に戻して「失敗」を返す
            # （画面側は入力を手元に残して送り直す）。
            _rev, _data_str = prev_rev, prev_str
            log(f'保存エラー: {e}')
            return {'ok': False, 'reason': f'write_failed: {e}'}
        return {'ok': True, 'rev': _rev}


def save_excel(filename, b64data):
    try:
        downloads = os.path.join(os.path.expanduser('~'), 'Downloads')
        os.makedirs(downloads, exist_ok=True)
        out = os.path.join(downloads, os.path.basename(filename))
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
    list_devices:        function(){ return call('list_devices', {}); },
    revoke_device:       function(id){ return call('revoke_device', {target_id: id}); },
    get_license_info:    function(){ return call('get_license_info', {}); },
    get_pair_code:       function(){ return call('get_pair_code', {}); },
    regenerate_pair_code: function(){ return call('regenerate_pair_code', {}); }
  }};

  // 他のPCの変更を取り込む（3秒ごとに版番号だけ確認する軽い問い合わせ）。
  // 登録が認められるまでは呼んでも意味がないので、登録成功後に開始する。
  function startSync() {
    if (window.__smartsyncSyncStarted) return;
    window.__smartsyncSyncStarted = true;
    var syncing = false;
    setInterval(function(){
      if (syncing) return;  // 前回の確認が終わる前に重ねて実行しない（二重反映防止）
      call('get_rev', {}).then(function(r){
        if (!r || r.rev === undefined || r.rev === REV) return;
        syncing = true;
        return call('load_data', {}).then(function(d){
          if (!d || d.rev === undefined) return;
          REV = d.rev;
          window.dispatchEvent(new CustomEvent('smartsync-remote-data',
            {detail: {json: d.json, conflict: false}}));
        }).then(function(){ syncing = false; }, function(){ syncing = false; });
      }).catch(function(){});
    }, 3000);
  }

  // 画面全体を覆うオーバーレイとして表示する。
  // 注意: document.documentElement.innerHTML を書き換える実装は使わない —
  // このコールバックが実行される頃には React が既に #root へマウント済みのため、
  // ページ全体を上書きするとマウント先ごと壊れて復帰できなくなる。
  var __overlay = null;
  function clearOverlay() {
    if (__overlay && __overlay.parentNode) __overlay.parentNode.removeChild(__overlay);
    __overlay = null;
  }
  function showOverlay(html, onMounted) {
    clearOverlay();
    var mount = function() {
      __overlay = document.createElement('div');
      __overlay.id = 'smartsync-gate-overlay';
      __overlay.style.cssText = 'position:fixed;inset:0;z-index:999999;background:rgba(20,24,34,.55);'
        + 'display:flex;align-items:center;justify-content:center;font-family:sans-serif;'
        + 'padding:24px;box-sizing:border-box';
      __overlay.innerHTML = '<div style="background:#fff;color:#1a2233;line-height:1.8;'
        + 'max-width:440px;width:100%;padding:36px;border-radius:14px;'
        + 'box-shadow:0 12px 40px rgba(0,0,0,.25)">' + html + '</div>';
      document.body.appendChild(__overlay);
      // 巨大なHTML本体のパース中に応答が返るケースがあるため、要素の取得や
      // イベント登録は「実際にDOMへ挿入し終えた後」のこのコールバックで行う。
      if (onMounted) onMounted();
    };
    if (document.body) mount();
    else document.addEventListener('DOMContentLoaded', mount, {once: true});
  }

  function showBlocked(title, bodyHtml) {
    showOverlay('<h2 style="color:#b4232a;margin:0 0 12px">' + title + '</h2>' + bodyHtml);
  }

  function showPairPrompt() {
    showOverlay(
      '<h2 style="margin:0 0 12px">この端末を追加します</h2>'
      + '<p style="color:#555;font-size:14px;margin:0 0 16px">親機の「端末管理」画面に表示されている'
      + '6桁のコードを入力してください。</p>'
      + '<input id="__pcode" maxlength="6" inputmode="numeric" placeholder="000000" '
      + 'style="font-size:28px;letter-spacing:8px;text-align:center;width:100%;'
      + 'padding:10px;border:1px solid #ccc;border-radius:8px;box-sizing:border-box" />'
      + '<div id="__pcode_err" style="color:#b4232a;font-size:13px;min-height:20px;margin-top:8px"></div>'
      + '<button id="__pcode_go" style="margin-top:8px;width:100%;padding:12px;font-size:15px;'
      + 'font-weight:600;border:none;border-radius:8px;background:#c9a84c;color:#fff;'
      + 'cursor:pointer">接続する</button>',
      function() {
        var inp = document.getElementById('__pcode');
        var err = document.getElementById('__pcode_err');
        var go = document.getElementById('__pcode_go');
        inp.focus();
        function submit() {
          go.disabled = true;
          call('register_device', {name: navigator.platform || '', pair_code: inp.value.trim()})
            .then(handleRegisterResult)
            .catch(function(){ err.textContent = '通信エラーです。もう一度お試しください。'; go.disabled = false; });
        }
        go.addEventListener('click', submit);
        inp.addEventListener('keydown', function(e){ if (e.key === 'Enter') { e.preventDefault(); submit(); } });
        window.__pairRetry = function(msg) { err.textContent = msg; go.disabled = false; inp.focus(); inp.select(); };
      }
    );
  }

  function handleRegisterResult(r) {
    if (r && r.ok) {
      clearOverlay();
      startSync();
      window.dispatchEvent(new Event('pywebviewready'));
      return;
    }
    var reason = r && r.reason;
    if (reason === 'pair_code_required' || reason === 'pair_code_invalid') {
      if (window.__pairRetry) {
        window.__pairRetry(reason === 'pair_code_invalid'
          ? 'コードが違います。もう一度確認してください。' : 'コードを入力してください。');
      } else {
        showPairPrompt();
      }
      return;
    }
    if (reason === 'limit_exceeded') {
      showBlocked('利用できる台数の上限に達しています',
        '<p>このパソコンは登録されていません。'
        + '（登録済み ' + r.used + ' 台 ／ 上限 ' + r.limit + ' 台）</p>'
        + '<p>親機の「端末管理」から使わなくなったパソコンを解除するか、'
        + '台数追加についてご連絡ください。</p>');
      return;
    }
    // その他の失敗（通信不調など）はいったんアプリを表示する
    startSync();
    window.dispatchEvent(new Event('pywebviewready'));
  }

  call('register_device', {name: navigator.platform || ''}).then(handleRegisterResult)
    .catch(function(){ startSync(); window.dispatchEvent(new Event('pywebviewready')); });
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

    # 親機のみが呼べる操作（他PCから叩かれても情報を渡さない）
    HOST_ONLY = {'/api/list_devices', '/api/revoke_device',
                 '/api/get_pair_code', '/api/regenerate_pair_code'}

    def do_POST(self):
        try:
            n = int(self.headers.get('Content-Length', 0))
            body = json.loads(self.rfile.read(n) or b'{}')
            path = urlparse(self.path).path
            did = body.get('device_id')
            ip = self.client_address[0]

            if path in self.HOST_ONLY and not is_host_ip(ip):
                self._send(200, json.dumps({'result': {'ok': False, 'reason': 'host_only'}}).encode('utf-8'),
                           'application/json')
                return

            if path == '/api/register_device':
                result = register_device(did, body.get('name', ''), ip, body.get('pair_code'))
            elif path == '/api/list_devices':
                result = list_devices()
            elif path == '/api/revoke_device':
                result = revoke_device(body.get('target_id'))
            elif path == '/api/get_pair_code':
                result = get_pair_code()
            elif path == '/api/regenerate_pair_code':
                result = regenerate_pair_code()
            elif path == '/api/get_license_info':
                result = get_license_info()
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
    """複数PCからの同時接続をさばくため、リクエストごとにスレッドで処理する。

    Windows では SO_REUSEADDR を付けると、既に別の SmartSync が使っている
    ポートにもう1つ割り込んで待ち受けできてしまう（Linux/Mac とは意味が違う）。
    以前はこれで親機の SmartSync.exe を2回起動すると親機サーバーが2つ動き、
    それぞれが別々のデータを持って data.json を上書きし合っていた（入力が
    他のPCに共有されない・再起動すると消える）。Windows では使わず、
    逆に SO_EXCLUSIVEADDRUSE で他のプロセスと同じポートを共有しない。"""
    daemon_threads = True
    allow_reuse_address = (os.name != 'nt')

    def server_bind(self):
        if os.name == 'nt' and hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


_instance_mutex = None


def _another_instance_running():
    """このPCで SmartSync（同じポート）が既に動いていれば True（Windowsのみ判定）。"""
    global _instance_mutex
    if os.name != 'nt':
        return False
    try:
        import ctypes
        k32 = ctypes.WinDLL('kernel32', use_last_error=True)
        k32.CreateMutexW.restype = ctypes.c_void_p
        h = k32.CreateMutexW(None, False, f'Local\\SmartSync_Server_{SERVER_PORT}')
        err = ctypes.get_last_error()
        if not h:
            return False
        _instance_mutex = h      # プロセスが終わるまで保持する
        return err == 183        # ERROR_ALREADY_EXISTS
    except Exception:
        return False


def _smartsync_answering():
    """このPCの SmartSync がポートで応答しているか。"""
    try:
        import urllib.request
        with urllib.request.urlopen(f'http://127.0.0.1:{SERVER_PORT}/', timeout=3) as r:
            return b'__smartsyncCall' in r.read()
    except Exception:
        return False


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
    global HOST_IP
    log(f'=== Smart Sync 起動 === (pid {os.getpid()})')
    log(f'APP_DIR: {APP_DIR}')
    if not os.path.exists(HTML_PATH):
        # 画面のファイルが無い → exe に同梱した版を置く（以後は更新バナーで最新になる）
        bundled = os.path.join(getattr(sys, '_MEIPASS', ''), 'SmartSync.html')
        if getattr(sys, '_MEIPASS', None) and os.path.exists(bundled):
            try:
                shutil.copy2(bundled, HTML_PATH)
                log('SmartSync.html が無かったため、同梱の画面ファイルを置きました')
            except Exception as e:
                log(f'同梱の画面ファイルを置けませんでした: {e}')
    # 既に SmartSync が動いている（画面だけ閉じた後にもう一度ダブルクリックした等）
    # 場合は、親機サーバーを2つ目として起動せず、動いている方の画面を開くだけにする。
    if _another_instance_running():
        log('既に起動しています。画面だけを開きます。')
        open_edge_app(f'http://localhost:{SERVER_PORT}/')
        sys.exit(0)
    if not os.path.exists(HTML_PATH):
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(
                0, f'SmartSync.html が見つかりません:\n{HTML_PATH}',
                'Smart Sync — エラー', 0x10)
        except Exception:
            pass
        sys.exit(1)

    HOST_IP = local_ip()
    _load_from_disk()
    log(f'データ読込: rev={_rev}')
    if LICENSE['present'] and not LICENSE['valid']:
        log(f'ライセンス無効（{LICENSE["reason"]}）— お試し運用（1台）で起動します')
    elif LICENSE['valid']:
        log(f'ライセンス有効: {LICENSE["facility"]} / 上限{MAX_DEVICES}台'
            + (f' (期限 {LICENSE["expiresAt"]})' if LICENSE['expiresAt'] else ''))
    else:
        log('license.key が見つかりません — お試し運用（1台）で起動します')
    log(f'端末上限: {MAX_DEVICES} 台（登録済み {len(_read_devices())} 台）')
    log(f'ペアリングコード: {CONFIG.get("pairCode")}（他のPCを追加するときに必要）')

    try:
        server = ThreadedHTTPServer(('0.0.0.0', SERVER_PORT), Handler)
    except OSError as e:
        if _smartsync_answering():
            # 古い版の SmartSync が動いている等で、ポートは既に SmartSync が使っている
            log('既に SmartSync が応答しています。画面だけを開きます。')
            open_edge_app(f'http://localhost:{SERVER_PORT}/')
            sys.exit(0)
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
    log(f'親機として起動しました: http://{HOST_IP}:{SERVER_PORT}/')
    if _load_failed:
        def _warn_load_failed():
            try:
                import ctypes
                ctypes.windll.user32.MessageBoxW(
                    0, 'data.json を読み込めませんでした。データを守るため、保存を止めています。\n'
                       'パソコンを再起動してから、もう一度 Smart Sync を起動してください。\n\n'
                       f'{_load_failed}', 'Smart Sync — エラー', 0x30)
            except Exception:
                pass
        threading.Thread(target=_warn_load_failed, daemon=True).start()
    log('子機からは上記URLをブラウザで開いてください')

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
