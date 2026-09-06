"""Smart Sync ライセンス検証（公開鍵のみを含む。署名はできない）

license.key は開発元だけが持つ秘密鍵で署名されたファイル。
このモジュールは公開鍵で検証するだけなので、このファイルや
exe を解析されても新しいライセンスを偽造することはできない
（改造して検証自体を無効化する行為は別問題として windows/launch.py
 側のドキュメントに記載）。
"""
import os
import json
import base64
from datetime import date

import rsa

PUBLIC_KEY_PEM = b"""-----BEGIN RSA PUBLIC KEY-----
MIIBCgKCAQEA7DG3q/ndstTcS06fkBV5jt5fHOji3KHWQmWTlD8VpU0v2t08L1WU
qPan5O3Rb6lhO6seob62yAmDhB9Aq5GwMVpojOIaECx8lPO9uY9sWCUaJx38fds8
jbzK0dzg/ZnBNNAooBctFNakEiwd3KWeqEj6N/7aSVLatoFU1+dq6JdN5dvf6rMq
eUkoUIEHFppHxn5mq36dxjoD98Uyp3lQbZYydRvJoq19M8dlHs6dTkvG7fgjqhwZ
JUdH8KK71R0JpNq+hkejsVoaKGH71oBRbeleAoPQuxXSLtWbqZGlpgLOUigXr8j2
w0l2eXohzxvHDwbXDmEsxx1nuwJti985GwIDAQAB
-----END RSA PUBLIC KEY-----"""

_PUBKEY = rsa.PublicKey.load_pkcs1(PUBLIC_KEY_PEM)

# ライセンスファイルが無い/壊れている場合のフォールバック（お試し運用）
TRIAL_MAX_DEVICES = 1


def _canonical_payload(facility, max_devices, issued_at, expires_at):
    return f"{facility}|{max_devices}|{issued_at}|{expires_at or ''}".encode('utf-8')


def load_license(path):
    """license.key を読んで検証する。

    戻り値: {
      'present': bool,       # ファイルがあったか
      'valid': bool,         # 署名が正しいか
      'expired': bool,       # 期限切れか
      'facility': str|None,
      'maxDevices': int,     # 有効な数（無効時は TRIAL_MAX_DEVICES）
      'issuedAt': str|None,
      'expiresAt': str|None,
      'reason': str|None,    # 無効な場合の理由
    }
    """
    result = {
        'present': False, 'valid': False, 'expired': False,
        'facility': None, 'maxDevices': TRIAL_MAX_DEVICES,
        'issuedAt': None, 'expiresAt': None, 'reason': None,
    }
    if not os.path.exists(path):
        result['reason'] = 'no_license_file'
        return result

    result['present'] = True
    try:
        with open(path, 'r', encoding='utf-8') as f:
            lic = json.load(f)
        facility = lic['facility']
        max_devices = int(lic['maxDevices'])
        issued_at = lic['issuedAt']
        expires_at = lic.get('expiresAt')
        sig = base64.b64decode(lic['signature'])
    except Exception as e:
        result['reason'] = f'malformed: {e}'
        return result

    payload = _canonical_payload(facility, max_devices, issued_at, expires_at)
    try:
        rsa.verify(payload, sig, _PUBKEY)
    except rsa.pkcs1.VerificationError:
        result['reason'] = 'signature_invalid'
        return result

    result.update(facility=facility, issuedAt=issued_at, expiresAt=expires_at)

    if expires_at:
        try:
            if date.today() > date.fromisoformat(expires_at):
                result['expired'] = True
                result['reason'] = 'expired'
                return result
        except ValueError:
            result['reason'] = 'bad_expiry_format'
            return result

    result['valid'] = True
    result['maxDevices'] = max_devices
    return result
