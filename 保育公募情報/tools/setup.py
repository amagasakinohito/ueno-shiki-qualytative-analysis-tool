#!/usr/bin/env python3
"""セットアップを最初から最後まで自動で行う。

    python setup.py

これ1つで次を全部やります。

    1. 必要なライブラリのインストール
    2. ガルーンのIDとパスワードを聞いて config.ini を作成
    3. ガルーンに接続できるか確認
    4. 投稿できる形式を自動判定してテスト投稿
    5. 各市サイトを巡回できるか確認し、現状を基準として記録
    6. 毎朝9時の自動実行を登録

途中で失敗しても、原因と次にやることを日本語で表示します。
"""

from __future__ import annotations

import configparser
import getpass
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.ini"
EXAMPLE = HERE / "config.example.ini"

# Windowsのコンソールは既定が日本語コードページのため、日本語の表示に失敗して
# 途中で落ちることがある。UTF-8に切り替えて防ぐ。
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

TASK_NAME = "保育公募情報巡回"
DEFAULT_TIME = "09:00"


# ---------------------------------------------------------------------------
# 画面表示
# ---------------------------------------------------------------------------


def step(n: int, total: int, title: str) -> None:
    print()
    print("=" * 60)
    print(f"  {n}/{total}  {title}")
    print("=" * 60)


def ok(message: str) -> None:
    print(f"  [OK] {message}")


def warn(message: str) -> None:
    print(f"  [！] {message}")


def fail(message: str) -> None:
    print()
    print("-" * 60)
    print("  中断しました")
    print("-" * 60)
    print(message)
    sys.exit(1)


def ask_yes(question: str, default: bool = True) -> bool:
    suffix = "[Y/n]" if default else "[y/N]"
    while True:
        answer = input(f"  {question} {suffix} ").strip().lower()
        if not answer:
            return default
        if answer in ("y", "yes", "はい"):
            return True
        if answer in ("n", "no", "いいえ"):
            return False


# ---------------------------------------------------------------------------
# 各ステップ
# ---------------------------------------------------------------------------


def check_python() -> None:
    if sys.version_info < (3, 10):
        fail(
            f"Python 3.10以上が必要です（今は {platform.python_version()}）。\n"
            "https://www.python.org/downloads/ から新しいものを入れてください。"
        )
    ok(f"Python {platform.python_version()}")


def install_requirements() -> None:
    print("  ライブラリを確認しています...")
    try:
        import bs4  # noqa: F401
        import requests  # noqa: F401
        import yaml  # noqa: F401
    except ImportError:
        print("  不足しているのでインストールします（1〜2分かかります）...")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(HERE / "requirements.txt")],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            fail(
                "ライブラリのインストールに失敗しました。\n"
                "社内プロキシが原因のことがあります。次を試してください。\n\n"
                f"  {sys.executable} -m pip install -r requirements.txt "
                "--proxy http://プロキシのアドレス:ポート\n\n"
                f"エラー内容:\n{result.stderr[-800:]}"
            )
    ok("必要なライブラリはそろっています")


def read_password() -> str:
    """パスワードを読み取る。

    通常は入力を伏せて受け取るが、端末によっては伏せる仕組みが働かず
    入力を受け付けられないことがある。その場合は、画面に表示される形での
    入力に切り替えられるようにしておく。
    """
    print("  パスワードを入力してください。")
    print("  ※打っても画面には何も表示されませんが、入力はされています。")
    print("    打ち終わったら Enter を押してください。")

    for attempt in range(3):
        try:
            password = getpass.getpass("  パスワード: ")
        except Exception:
            password = ""
        if password:
            return password

        if attempt == 0:
            print()
            warn("入力が受け取れませんでした。")
        if ask_yes("画面に表示される形で入力しますか？（周囲にご注意ください）", default=True):
            while True:
                password = input("  パスワード（表示されます）: ").strip()
                if password:
                    return password

    fail(
        "パスワードを読み取れませんでした。\n"
        "config.ini をテキストエディタで開き、password = の行に直接書いてから、\n"
        "もう一度 setup.py を実行して「作り直しますか？」に n と答えてください。"
    )
    return ""  # fail() で終了するのでここには来ない


def create_config() -> configparser.ConfigParser:
    if CONFIG.exists():
        print(f"  設定ファイルが既にあります: {CONFIG.name}")
        if not ask_yes("作り直しますか？（いいえ＝今の設定をそのまま使う）", default=False):
            config = configparser.ConfigParser()
            config.read(CONFIG, encoding="utf-8")
            ok("既存の設定を使います")
            return config

    print()
    print("  ガルーンのログイン情報を入力してください。")
    print("  （config.ini に保存されます。このファイルはGitに含まれません）")
    print()

    login = ""
    while not login:
        login = input("  ガルーンのログイン名: ").strip()
    password = read_password()

    shutil.copy(EXAMPLE, CONFIG)
    config = configparser.ConfigParser()
    config.read(CONFIG, encoding="utf-8")
    config["garoon"]["login"] = login
    config["garoon"]["password"] = password
    with CONFIG.open("w", encoding="utf-8") as fp:
        config.write(fp)

    # パスワードを含むので、可能な環境では本人だけが読めるようにする
    try:
        os.chmod(CONFIG, 0o600)
    except OSError:
        pass

    ok(f"設定ファイルを作成しました: {CONFIG.name}")
    return config


def check_garoon(config: configparser.ConfigParser) -> None:
    from garoon import GaroonClient, GaroonError

    client = GaroonClient.from_config(config)
    print(f"  接続先: {client.base_url}")
    try:
        print("  " + client.check(config["garoon"].get("probe_endpoint")))
    except GaroonError as exc:
        message = str(exc)
        hint = ""
        if "ログイン名かパスワード" in message:
            hint = "もう一度 python setup.py を実行して、入力し直してください。"
        elif "403" in message:
            hint = (
                "接続はできましたが拒否されました。\n"
                "  cybozu.com にIPアドレス制限がかかっている可能性があります。\n"
                "  このPCのグローバルIPを許可リストに追加してもらってください。"
            )
        elif "接続できません" in message:
            hint = (
                "ネットワークに届いていません。\n"
                "  社内プロキシ経由の場合は config.ini の https_proxy を設定してください。"
            )
        fail(f"ガルーンに接続できませんでした。\n\n{message}\n\n{hint}")


def test_post(config: configparser.ConfigParser) -> None:
    from garoon import GaroonClient, GaroonError

    print("  投稿できる形式を判定するため、テスト投稿を行います。")
    print("  スペースに1件コメントが投稿されます（あとで削除して構いません）。")
    if not ask_yes("実行しますか？"):
        warn("スキップしました。本番投稿時に失敗する可能性があります。")
        return

    client = GaroonClient.from_config(config)
    try:
        template = client.detect_body_template(
            "保育公募情報の巡回スクリプトのテスト投稿です。\n"
            "この投稿が見えていれば設定は成功しています。削除して構いません。"
        )
    except GaroonError as exc:
        fail(
            f"テスト投稿に失敗しました。\n\n{exc}\n\n"
            "スペース282への書き込み権限があるかご確認ください。"
        )

    config["garoon"]["body_template"] = template
    with CONFIG.open("w", encoding="utf-8") as fp:
        config.write(fp)
    ok("テスト投稿に成功しました。ガルーンのスペースをご確認ください。")


def crawl_baseline() -> None:
    print("  各市のサイトを巡回します（33ページ・約3分かかります）...")
    result = subprocess.run(
        [sys.executable, str(HERE / "watch.py"), "--init"],
        cwd=HERE,
        capture_output=True,
        text=True,
    )
    print(result.stdout[-2000:] or result.stderr[-2000:])
    if result.returncode != 0:
        fail("巡回に失敗しました。上のエラー内容をそのまま伝えていただければ調べます。")

    # 取得できなかったページがあれば知らせる（監視の穴になるため）
    failures = [line for line in result.stderr.splitlines() if "失敗:" in line]
    if failures:
        warn(f"{len(failures)}ページが取得できませんでした:")
        for line in failures[:5]:
            print(f"      {line.split('失敗:')[-1].strip()}")
        print("      → URLが変わった可能性があります。伝えていただければ直します。")
    ok("現在の掲載内容を基準として記録しました")


def register_schedule() -> None:
    print("  毎朝9時に自動で巡回するよう登録します。")
    if not ask_yes("登録しますか？"):
        warn("スキップしました。あとで python setup.py を実行すれば登録できます。")
        return

    if platform.system() == "Windows":
        bat = HERE / "run_daily.bat"
        result = subprocess.run(
            [
                "schtasks", "/Create",
                "/SC", "DAILY",
                "/ST", DEFAULT_TIME,
                "/TN", TASK_NAME,
                "/TR", f'"{bat}"',
                "/F",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            warn("自動登録に失敗しました。手動で登録してください。")
            print("    タスクスケジューラ →「基本タスクの作成」→ 毎日 9:00 →")
            print(f"    プログラムの開始 → {bat}")
            print(f"    （エラー: {result.stderr.strip()[:200]}）")
            return
        ok(f"タスクスケジューラに登録しました（毎日 {DEFAULT_TIME}）")
        print(f"    解除したいときは: schtasks /Delete /TN \"{TASK_NAME}\" /F")
    else:
        sh = HERE / "run_daily.sh"
        line = f"0 9 * * * {sh} >> {HERE / 'watch.log'} 2>&1"
        current = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        existing = current.stdout if current.returncode == 0 else ""
        if str(sh) in existing:
            ok("すでに登録済みです")
            return
        new = existing.rstrip("\n") + ("\n" if existing.strip() else "") + line + "\n"
        result = subprocess.run(["crontab", "-"], input=new, capture_output=True, text=True)
        if result.returncode != 0:
            warn("自動登録に失敗しました。次の1行を crontab -e で追加してください。")
            print(f"    {line}")
            return
        ok(f"cronに登録しました（毎日 {DEFAULT_TIME}）")
        print("    解除したいときは: crontab -e でその行を削除")


# ---------------------------------------------------------------------------


def main() -> int:
    print()
    print("保育公募情報 巡回スクリプト セットアップ")
    print()
    print("7市の保育所・児童発達支援・学童保育の公募情報を毎日巡回し、")
    print("新しい情報があればガルーンのスペースに自動投稿します。")

    total = 6
    step(1, total, "Pythonの確認")
    check_python()

    step(2, total, "ライブラリの準備")
    install_requirements()

    step(3, total, "ガルーンの設定")
    config = create_config()

    step(4, total, "ガルーンへの接続確認")
    check_garoon(config)

    step(5, total, "テスト投稿")
    test_post(config)

    step(6, total, "巡回の基準づくりと自動実行の登録")
    crawl_baseline()
    print()
    register_schedule()

    print()
    print("=" * 60)
    print("  完了しました")
    print("=" * 60)
    print()
    print("  明日の朝9時から、新しい公募情報があれば自動で投稿されます。")
    print("  変更がない日は投稿しません。")
    print()
    print("  今すぐ試したいとき : python watch.py --dry-run")
    print("  監視するページを見る: ../監視対象URL一覧.md")
    print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n中断しました。")
        sys.exit(130)
