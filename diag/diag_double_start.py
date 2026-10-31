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
    "import launch\nlaunch.open_edge_app = lambda url: print('OPEN_EDGE ' + url, flush=True)\n"
    "launch.main()\n")

def note(msg):
    print(f'::notice title={LABEL}::{msg}', flush=True)

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
        r = self.call('load_data'); d = json.loads(r['json']); d['contactNotes'].append({'id': nid, 'details': text})
        return self.call('save_data', {'json_str': json.dumps(d, ensure_ascii=False), 'base_rev': r['rev']})
    def notes(self):
        r = self.call('load_data'); return r['rev'], [n['details'] for n in json.loads(r['json'])['contactNotes']]

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
