@echo off
rem このファイルをダブルクリックすればセットアップが始まります（Windows用）
chcp 65001 > nul
cd /d "%~dp0"
title 保育公募情報 セットアップ

echo.
echo   保育公募情報 巡回スクリプト セットアップ
echo.

rem Python があるか確認する
where python >nul 2>&1
if errorlevel 1 goto nopython

python setup.py
echo.
echo   このウィンドウは閉じて構いません。
pause > nul
exit /b 0

:nopython
echo   Python が見つかりませんでした。
echo.
echo   先に Python をインストールしてください。
echo     https://www.python.org/downloads/
echo.
echo   ★インストール画面の一番下にある
echo     「Add Python to PATH」に必ずチェックを入れてください。
echo     （ここにチェックを入れ忘れると、このファイルは動きません）
echo.
echo   インストールが終わったら、もう一度このファイルを
echo   ダブルクリックしてください。
echo.
pause > nul
exit /b 1
