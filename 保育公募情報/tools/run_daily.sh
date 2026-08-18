#!/bin/sh
# 毎日の巡回（macOS / Linux / 社内サーバ用）
#
# cron への登録例（毎朝9時）:
#   crontab -e
#   0 9 * * * /path/to/保育公募情報/tools/run_daily.sh >> /path/to/watch.log 2>&1
#
# 仮想環境を使う場合は VENV に activate のパスを設定してください。

set -eu

DIR=$(cd "$(dirname "$0")" && pwd)
cd "$DIR"

VENV="${VENV:-}"
if [ -n "$VENV" ] && [ -f "$VENV" ]; then
    # shellcheck disable=SC1090
    . "$VENV"
fi

PYTHON="${PYTHON:-python3}"

echo "===== $(date '+%Y-%m-%d %H:%M:%S') 巡回開始 ====="
"$PYTHON" watch.py "$@"
status=$?
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 終了 (exit=$status) ====="
exit $status
