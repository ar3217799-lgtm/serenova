# Smart Sync 施設内共有・ライセンス — 運用ガイド

社内向けメモ。施設に配布する際の手順をまとめる。

## 全体構成

- **親機**: `SmartSync.exe` を起動したPC。データ（`data.json`）を持ち、
  HTTPサーバーとして動作する。施設内で常時電源が入っているPCにする。
- **子機**: 親機のURL（例 `http://192.168.1.50:8765/`）をブラウザで開くだけ。
  インストール不要。子機の台数は親機側の設定（ライセンス）で制限される。

## 1. 新規に施設へ導入する

1. exe をビルドする。2通りの方法がある。
   - **Windows機がある場合**: `windows/build.bat` を実行すると
     `dist_package/` に `SmartSync.exe` と `SmartSync.html` が生成される。
   - **Windows機が無い場合（Mac等）**: GitHub Actions で自動ビルドできる。
     `windows/` 配下を変更して push するか、GitHubの Actions タブから
     「Build Windows exe」を手動実行（workflow_dispatch）する。
     完了後、そのワークフロー実行のページ下部からアーティファクト
     `SmartSync-dist_package`（zip）をダウンロードする。
2. ライセンスを発行する（下記「ライセンス発行」）。生成された `.key` ファイルを
   `license.key` にリネームし、`dist_package/` に追加する。
3. `dist_package/` フォルダを丸ごと施設の親機PCへコピーする。
4. 親機で `SmartSync.exe` を起動する。初回起動時に:
   - `config.json`（ポート番号・ペアリングコード）が自動生成される
   - `smartsync.log` に接続用URLと6桁のペアリングコードが出力される
5. 親機のアプリ内「端末管理」画面でも同じペアリングコードを確認できる。
6. 子機側で親機のURLをEdge等で開き、表示されたペアリングコードを入力すると
   その端末が使えるようになる（以後はコード不要）。

## 2. ライセンス発行

ライセンス発行ツールは `serenova-license-authority/`
（**このリポジトリの外**。秘密鍵を含むため絶対に公開リポジトリに入れない）。

```bash
cd ~/serenova-license-authority
python3 issue_license.py "施設名" 台数
# 期限付きの例:
python3 issue_license.py "施設名" 台数 --expires 2027-09-06
```

`issued/` フォルダに `.key` ファイルが生成される。これを `license.key` に
リネームして施設の `SmartSync.exe` と同じフォルダに置く。

- ライセンスが無い/壊れている/期限切れの場合、自動的に **お試し運用（1台のみ）**
  にフォールバックする（安全側に倒す設計）。
- 台数を増やしたい、期限を延長したい場合は新しい `.key` を発行して
  古い `license.key` を上書きするだけでよい（再ビルド不要）。

## 3. 既存施設をアップデートする

**HTMLは自動更新されるが、exe（launch.py）は自動更新されない。**

- `SmartSync.html` の変更のみ（画面の見た目・ロジック修正など）は、
  `version.json` を更新して push すれば施設側は自動で取り込む。
- `launch.py` / `licensing.py` / `requirements.txt` を変更した場合
  （今回の施設内共有・ライセンス機能など）は、**exeの再ビルドと
  再配布が必須**。旧exeのままでは新機能（端末管理・共有）は使えない
  （ただし旧exeでも単体PCとしての基本機能は動き続ける＝安全に共存できる）。

再配布時は `SmartSync.exe` と `SmartSync.html` を差し替えるだけでよい。
`data.json` / `config.json` / `devices.json` / `license.key` はそのまま
残せば設定・データは引き継がれる。

## 4. トラブルシュート

| 症状 | 対処 |
|---|---|
| 子機が「上限に達しています」と出る | 親機の「端末管理」で不要な端末を削除するか、上位ライセンスを発行 |
| ペアリングコードが分からない | 親機アプリの「端末管理」画面、または `config.json` の `pairCode` |
| 台数制限を増やしたい | `issue_license.py` で新しい `.key` を発行し `license.key` を上書き |
| ポートが使用中でエラー | `config.json` の `port` を別の番号に変更 |
| 子機からデータが更新されない | 親機と同じネットワーク（Wi-Fi/LAN）に接続されているか確認 |
