@echo off
REM Smart Sync Windows exe ビルドスクリプト
REM Python 3.10 以上が必要です
REM 使い方: build.bat をダブルクリック

echo ================================
echo  Smart Sync Windows Build
echo ================================
echo.

python --version 2>NUL
if errorlevel 1 (
    echo [エラー] Python が見つかりません。
    echo https://www.python.org/downloads/ からインストールしてください。
    pause
    exit /b 1
)

echo [1/3] 依存パッケージをインストール中...
pip install -r requirements.txt
if errorlevel 1 ( echo インストール失敗 & pause & exit /b 1 )

echo.
echo [2/3] exe をビルド中...
pyinstaller SmartSync.spec --clean
if errorlevel 1 ( echo ビルド失敗 & pause & exit /b 1 )

echo.
echo [3/3] 配布フォルダを作成中...
if not exist "..\dist_package" mkdir "..\dist_package"
copy dist\SmartSync.exe ..\dist_package\SmartSync.exe
copy ..\SmartSync.html ..\dist_package\SmartSync.html
echo.
echo ================================
echo  完了！ dist_package フォルダに:
echo    SmartSync.exe
echo    SmartSync.html
echo  が生成されました。
echo  この2ファイルをユーザーに配布してください。
echo ================================
pause
