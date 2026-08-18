@echo off
rem このファイルをダブルクリックすればセットアップが始まります（Windows用）
chcp 65001 > nul
cd /d "%~dp0"
title 保育公募情報 セットアップ

echo.
echo   保育公募情報 巡回スクリプト セットアップ
echo.

rem Pythonを探す。Windowsには「Microsoft Storeを開くだけの偽物」の python.exe が
rem 用意されていることがあるため、where では判定せず、実際に動くかで確かめる。
rem 本物の Python Launcher (py) があればそちらを優先する。
set PY=

py -3 --version > nul 2>&1
if not errorlevel 1 (
    set PY=py -3
    goto found
)

python --version > nul 2>&1
if not errorlevel 1 (
    set PY=python
    goto found
)

goto nopython

:found
echo   使用するPython:
%PY% --version
echo.
%PY% setup.py
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
echo   インストールが終わったら、このウィンドウを閉じてから
echo   もう一度このファイルをダブルクリックしてください。
echo.
pause > nul
exit /b 1
