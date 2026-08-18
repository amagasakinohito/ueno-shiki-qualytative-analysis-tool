@echo off
rem 毎日の巡回（Windows タスクスケジューラ用）
rem
rem 登録手順:
rem   1. タスクスケジューラを開き「基本タスクの作成」
rem   2. トリガー: 毎日 9:00
rem   3. 操作: プログラムの開始 → このファイルを指定
rem   4. 「最上位の特権で実行する」は不要
rem
rem ログは同じフォルダの watch.log に追記されます。

setlocal
cd /d "%~dp0"

if not defined PYTHON set PYTHON=python

echo ===== %date% %time% 巡回開始 ===== >> watch.log
"%PYTHON%" watch.py %* >> watch.log 2>&1
set STATUS=%ERRORLEVEL%
echo ===== %date% %time% 終了 (exit=%STATUS%) ===== >> watch.log

exit /b %STATUS%
