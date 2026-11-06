# ビルドした SmartSync.exe そのものを Windows 上で動かして確かめる（GitHub Actions から実行）
$ErrorActionPreference = 'Stop'
$env:SMARTSYNC_TEST_AUTOYES = '1'   # 確認ダイアログには「はい」と答えたことにする
$exe = (Resolve-Path 'windows\dist\SmartSync.exe').Path
$html = (Resolve-Path 'SmartSync.html').Path

function Stop-All {
  Get-Process SmartSync -ErrorAction SilentlyContinue | Stop-Process -Force
  Get-Process msedge -ErrorAction SilentlyContinue | Stop-Process -Force
  Start-Sleep -Seconds 2
}
function Call($m, $body) {
  Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8765/api/$m" -ContentType 'application/json' -Body ($body | ConvertTo-Json -Compress)
}
function Wait-Server {
  for ($i = 0; $i -lt 90; $i++) {
    try {
      $r = Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8765/ -TimeoutSec 2
      if ($r.Content -match '__smartsyncCall') { return }
    } catch {}
    Start-Sleep -Seconds 1
  }
  throw 'server did not start'
}

# 1. 二重起動：2回目は画面を開くだけで終了し、親機サーバーは1つだけ
$d = New-Item -ItemType Directory -Force -Path "$env:RUNNER_TEMP\smoke"
Copy-Item $exe "$d\SmartSync.exe"
Copy-Item $html "$d\SmartSync.html"
$p1 = Start-Process -FilePath "$d\SmartSync.exe" -WorkingDirectory $d -PassThru
Wait-Server
$p2 = Start-Process -FilePath "$d\SmartSync.exe" -WorkingDirectory $d -PassThru
$p2.WaitForExit(90000) | Out-Null
$log = Get-Content "$d\smartsync.log" -Raw -Encoding UTF8
if (-not $p2.HasExited -or $p1.HasExited -or $log -notmatch '既に起動しています') { Write-Host $log; Stop-All; throw 'double start not prevented' }
Stop-All
Write-Host '::notice title=smoke 1::二重起動: 2回目は画面を開いて終了し、親機サーバーは1つだけ'

# 2. exe だけをデスクトップに置いて起動：いつものフォルダのデータで動く
$inst = New-Item -ItemType Directory -Force -Path "$HOME\Documents\施設\SmartSync"
Set-Content -Encoding utf8NoBOM -Path "$inst\data.json" -Value '{"rev": 7, "data": {"users": [], "contactNotes": [{"id": "n1", "details": "施設のデータ"}]}}'
Set-Content -Path "$inst\license.key" -Value 'dummy'
Copy-Item $html "$inst\SmartSync.html"
Set-Content -Path "$inst\SmartSync.exe" -Value 'old exe'
$desk = New-Item -ItemType Directory -Force -Path "$HOME\Desktop"
Copy-Item $exe "$desk\SmartSync.exe"
Start-Process -FilePath "$desk\SmartSync.exe" -WorkingDirectory $desk | Out-Null
Wait-Server
Call 'register_device' @{ device_id = 'smoke'; name = 'smoke' } | Out-Null
$r = Call 'load_data' @{ device_id = 'smoke' }
$replaced = (Get-FileHash "$inst\SmartSync.exe").Hash -eq (Get-FileHash $exe).Hash
$deskClean = (-not (Test-Path "$desk\data.json")) -and (-not (Test-Path "$desk\config.json")) -and (-not (Test-Path "$desk\smartsync.log"))
Stop-All
if ($r.result.rev -ne 7 -or $r.result.json -notmatch '施設のデータ' -or -not $replaced -or -not $deskClean) {
  throw "wrong place: rev=$($r.result.rev) exeReplaced=$replaced deskClean=$deskClean"
}
Write-Host '::notice title=smoke 2::デスクトップに置いた exe: いつものフォルダのデータ(rev 7)で起動し、そのフォルダの古い exe を置き換え、デスクトップにはファイルを作らない'

# 3. いつものフォルダに SmartSync.html が無い：同梱の画面ファイルで起動する
Remove-Item "$inst\SmartSync.html"
Start-Process -FilePath "$inst\SmartSync.exe" -WorkingDirectory $inst | Out-Null
Wait-Server
$restored = Test-Path "$inst\SmartSync.html"
Stop-All
if (-not $restored) { throw 'SmartSync.html was not restored' }
Write-Host '::notice title=smoke 3::SmartSync.html が無くても同梱の画面ファイルを置いて起動'
