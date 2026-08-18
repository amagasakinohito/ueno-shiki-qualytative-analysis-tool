#!/bin/sh
# このファイルをダブルクリックすればセットアップが始まります（Mac用）
#
# 「開発元が未確認のため開けません」と出た場合は、
# ファイルを右クリック →「開く」→「開く」を選んでください。

cd "$(dirname "$0")" || exit 1

echo
echo "  保育公募情報 巡回スクリプト セットアップ"
echo

if ! command -v python3 > /dev/null 2>&1; then
    echo "  Python が見つかりませんでした。"
    echo
    echo "  先に Python をインストールしてください。"
    echo "    https://www.python.org/downloads/"
    echo
    echo "  インストールが終わったら、もう一度このファイルを"
    echo "  ダブルクリックしてください。"
    echo
    exit 1
fi

python3 setup.py

echo
echo "  このウィンドウは閉じて構いません。"
