"""親機の SmartSync.exe を2回起動した場合に何が起きるかを Windows 上で再現する診断。
launch.main() をそのまま使い（Edge を開く部分だけ差し替え）、同じフォルダで2回起動する。"""
import subprocess, sys, time, json, os, shutil, tempfile, http.client

SRC = sys.argv[1]          # launch.py / licensing.py があるフォルダ
LABEL = sys.argv[2]        # before / after
D = tempfile.mkdtemp(prefix='ss_')
for f in ('launch.py', 'licensing.py'):
    shutil.copy(os.path.join(SRC, f), D)
open(os.path.join(D, 'SmartSync.html'), 'w', encoding='utf-8').write('<html><head></head><body>t</body></html>')
json.dump({'rev': 1, 'data': {'contactNotes': [{'id': 'n0', 'details': '初期データ'}]}},
          open(os.path.join(D, 'data.json'), 'w', encoding='utf-8'), ensure_ascii=False)
RUN = os.path.join(D, 'run_main.py')
open(RUN, 'w', encoding='utf-8').write(
    "import sys, os\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"
    "import launch\nlaunch.MAX_DEVICES = 10\nlaunch.open_edge_app = lambda url: print('OPEN_EDGE ' + url, flush=True)\n"
    "launch.main()\n")

def note(msg):
    print(f'::notice title={LABEL}::' + str(msg).replace('\n', ' | '), flush=True)

def start(name):
    p = subprocess.Popen([sys.executable, '-u', RUN], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace')
    lines = []
    t0 = time.time()
    import threading
    def rd():
        for ln in p.stdout:
            lines.append(ln.rstrip())
    threading.Thread(target=rd, daemon=True).start()
    while time.time() - t0 < 8:
        if any('OPEN_EDGE' in l or '使用できません' in l or 'エラー' in l for l in lines) or p.poll() is not None:
            break
        time.sleep(0.2)
    time.sleep(1.0)
    return p, lines

class Client:
    def __init__(self, dev):
        self.dev = dev
        self.c = http.client.HTTPConnection('127.0.0.1', 8765, timeout=10)  # keep-alive（ブラウザと同じく接続を使い回す）
    def call(self, m, a=None):
        a = dict(a or {}); a['device_id'] = self.dev
        body = json.dumps(a, ensure_ascii=False).encode('utf-8')
        self.c.request('POST', '/api/' + m, body=body, headers={'Content-Type': 'application/json'})
        r = self.c.getresponse(); data = r.read()
        return json.loads(data)['result']
    def add_note(self, nid, text):
        r = self.call('load_data')
        if 'json' not in r: return r
        d = json.loads(r['json']) if r['json'] else {'contactNotes': []}
        d.setdefault('contactNotes', []).append({'id': nid, 'details': text})
        return self.call('save_data', {'json_str': json.dumps(d, ensure_ascii=False), 'base_rev': r['rev']})
    def notes(self):
        r = self.call('load_data')
        if 'json' not in r: return r
        return r['rev'], [n['details'] for n in (json.loads(r['json'])['contactNotes'] if r['json'] else [])]

def main():
    p1, l1 = start('1回目')
    note('1回目の起動: ' + ' | '.join(l for l in l1 if '起動' in l or 'OPEN_EDGE' in l or 'エラー' in l)[:300])
    B = Client('pc-b'); B.call('register_device', {'name': 'pcB'})      # 他のPC（起動済みの親機につながっている）
    note(f'他PC 接続直後: {B.notes()}')
    p2, l2 = start('2回目')
    second_running = p2.poll() is None and any('OPEN_EDGE' in l for l in l2)
    note(f'2回目の起動: プロセス{"が動き続けている" if p2.poll() is None else "は終了"} / 出力: ' + ' | '.join(l for l in l2 if '起動' in l or 'OPEN_EDGE' in l or '使用できません' in l or '既に' in l)[:300])
    P = Client('pc-parent'); P.call('register_device', {'name': 'parent'})  # 親機で新しく開いた画面
    note(f'親機の新しい画面が保存: {P.add_note("np", "親機で入力したノート")}')
    note(f'親機の新しい画面から見えるノート: {P.notes()}')
    note(f'他PC（使い回しの接続）から見えるノート: {B.notes()}')
    note(f'他PCが保存: {B.add_note("nb", "他PCで入力したノート")}')
    note(f'親機の画面から見えるノート: {P.notes()}')
    time.sleep(1)
    for p in (p1, p2):
        if p.poll() is None:
            p.kill()
    time.sleep(1)
    on_disk = json.load(open(os.path.join(D, 'data.json'), encoding='utf-8'))
    note(f'両方を終了した後の data.json（＝再起動後に表示される内容）: rev={on_disk["rev"]} {[n["details"] for n in on_disk["data"]["contactNotes"]]}')


def scenario_locked():
    json.dump({'rev': 5, 'data': {'contactNotes': [{'id': 'n0', 'details': '施設の大事なデータ'}]}},
              open(os.path.join(D, 'data.json'), 'w', encoding='utf-8'), ensure_ascii=False)
    for f in ('devices.json',):
        if os.path.exists(os.path.join(D, f)): os.remove(os.path.join(D, f))
    locker = subprocess.Popen([sys.executable, '-c',
        "import msvcrt,os,time,sys\nf=open(sys.argv[1],'r+b')\nn=os.path.getsize(sys.argv[1])\nmsvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, n)\nprint('LOCKED',flush=True)\ntime.sleep(4)\nf.seek(0)\nmsvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, n)\nprint('UNLOCKED',flush=True)",
        os.path.join(D, 'data.json')], stdout=subprocess.PIPE, text=True)
    locker.stdout.readline()
    p, lines = start('ロック中に起動')
    t = time.time()
    while time.time() - t < 15 and not any('OPEN_EDGE' in l for l in lines):
        time.sleep(0.2)
    note('[起動時ロック] 起動ログ: ' + ' | '.join(l for l in lines if 'データ読込' in l or '読込エラー' in l or 'OPEN_EDGE' in l)[:300])
    C = Client('pc-c'); C.call('register_device', {'name': 'pcC'})
    note(f'[起動時ロック] 起動直後に見えるデータ: {C.notes()}')
    note(f'[起動時ロック] あるPCが保存: {C.add_note("nc", "起動直後に入力")}')
    time.sleep(0.5)
    if p.poll() is None: p.kill()
    locker.wait(timeout=10)
    time.sleep(1)
    on_disk = json.load(open(os.path.join(D, 'data.json'), encoding='utf-8'))
    kept = '施設の大事なデータ' in json.dumps(on_disk, ensure_ascii=False)
    note(f'[起動時ロック] 終了後の data.json: rev={on_disk.get("rev")} 元のデータが{"残っている" if kept else "消えた"} {json.dumps(on_disk.get("data"), ensure_ascii=False)[:200]}')

try:
    main()
    scenario_locked()
except Exception:
    import traceback
    print('::error title=%s::%s' % (LABEL, traceback.format_exc().replace('\n', ' | ')[:900]), flush=True)
    sys.exit(1)
