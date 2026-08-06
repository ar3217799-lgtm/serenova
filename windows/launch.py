#!/usr/bin/env python3
"""
Smart Sync — Windows PyWebView ランチャー
SmartSync.html は exe と同じフォルダに置く（自動更新の対象）
"""
import webview
import os
import sys
import json
import base64
import shutil
from datetime import datetime

# PyInstaller でビルドした場合は sys.executable のフォルダ、
# 通常実行の場合はスクリプトのフォルダを使う
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

HTML_PATH = os.path.join(APP_DIR, 'SmartSync.html')
DATA_PATH = os.path.join(APP_DIR, 'data.json')

window = None  # webview.create_window の戻り値を後で代入


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
        """
        新しい SmartSync.html を適用してウィンドウをリロードする。
        失敗時はバックアップから自動ロールバック。
        """
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_path = os.path.join(
            APP_DIR, f'SmartSync_backup_{old_version}_{ts}.html'
        )
        try:
            shutil.copy2(HTML_PATH, backup_path)
            with open(HTML_PATH, 'w', encoding='utf-8') as f:
                f.write(html_content)
            print(f'update_app: v{old_version} → v{new_version} 適用完了')

            # ウィンドウをリロード
            url = 'file:///' + HTML_PATH.replace('\\', '/')
            if window:
                window.load_url(url)
            return True
        except Exception as e:
            print('update_app error:', e)
            try:
                shutil.copy2(backup_path, HTML_PATH)
                print('update_app: ロールバック完了')
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
            'Smart Sync — 起動エラー',
            0x10
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
    webview.start(debug=False)


if __name__ == '__main__':
    main()
