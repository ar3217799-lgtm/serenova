#!/usr/bin/env python3
import sys
import multiprocessing

# PyInstaller --onefile on Windows では必須
if hasattr(multiprocessing, 'freeze_support'):
    multiprocessing.freeze_support()

import webview
import os
import base64
import shutil
from datetime import datetime

if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

HTML_PATH = os.path.join(APP_DIR, 'SmartSync.html')
DATA_PATH = os.path.join(APP_DIR, 'data.json')

window = None


class Api:
    def save_data(self, json_str):
        try:
            with open(DATA_PATH, 'w', encoding='utf-8') as f:
                f.write(json_str)
            return True
        except Exception as e:
            print('save_data error:', e)
            return False

    def load_data(self):
        try:
            if not os.path.exists(DATA_PATH):
                return None
            with open(DATA_PATH, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            print('load_data error:', e)
            return None

    def save_excel(self, filename, b64data):
        try:
            desktop = os.path.join(os.path.expanduser('~'), 'Desktop')
            out_path = os.path.join(desktop, filename)
            data = base64.b64decode(b64data)
            with open(out_path, 'wb') as f:
                f.write(data)
            return out_path
        except Exception as e:
            print('save_excel error:', e)
            return None

    def update_app(self, html_content, old_version, new_version):
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = os.path.join(
            APP_DIR, f'SmartSync_backup_{old_version}_{ts}.html'
        )
        try:
            shutil.copy2(HTML_PATH, backup_path)
            with open(HTML_PATH, 'w', encoding='utf-8') as f:
                f.write(html_content)
            print(f'update_app: v{old_version} → v{new_version} 完了')
            url = 'file:///' + HTML_PATH.replace('\\', '/')
            if window:
                window.load_url(url)
            return True
        except Exception as e:
            print('update_app error:', e)
            try:
                if os.path.exists(backup_path):
                    shutil.copy2(backup_path, HTML_PATH)
            except Exception:
                pass
            return False


def main():
    global window

    if not os.path.exists(HTML_PATH):
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            0,
            f'SmartSync.html が見つかりません:\n{HTML_PATH}\n\n'
            'exe と同じフォルダに SmartSync.html を置いてください。',
            'Smart Sync — 起動エラー', 0x10
        )
        sys.exit(1)

    api = Api()
    url = 'file:///' + HTML_PATH.replace('\\', '/')

    window = webview.create_window(
        'Smart Sync',
        url,
        js_api=api,
        width=1440,
        height=900,
        min_size=(1024, 680),
        text_select=True,
    )

    # http_server=True で内部HTTPサーバー経由ロード（file:// の制約を回避）
    webview.start(http_server=True, debug=False)


if __name__ == '__main__':
    main()
