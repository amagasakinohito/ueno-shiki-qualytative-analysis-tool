#!/usr/bin/env python3
"""ガルーンへ本文を投稿するクライアント。

■ 重要 ― エンドポイントは設定ファイルで指定してください

ガルーンの「どこに投稿するか」は環境によって異なります。

- 「電子会議室」はガルーンの標準機能名ではありません。社内での呼び名であり、
  実体は「掲示板」「スペースのディスカッション」「メッセージ」のいずれかです。
- 使えるAPIはガルーンのバージョンによって違います。
  （スペースのディスカッション追加・コメント書き込み、メッセージ作成のREST APIは
  Garoon 6.17.0 で追加されました）

そのため、このモジュールはエンドポイントのパスとリクエストボディの形を
config.ini から与える方式にしています。正確な仕様は下記でご確認ください。

    https://cybozu.dev/ja/garoon/docs/rest-api/

設定例は config.example.ini に3パターン載せています。

■ 認証

ガルーンREST APIはパスワード認証（X-Cybozu-Authorization ヘッダ）を使います。
kintone のようなAPIトークンは使えません。投稿用のアカウントと、
対象の掲示板／スペースへの書き込み権限が必要です。

cybozu.com のセキュアアクセス（クライアント証明書）やBasic認証を
有効にしている場合は、config.ini の該当項目を設定してください。
"""

from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass

import requests

log = logging.getLogger("garoon")

# 1回の投稿に入れる本文の上限。超える場合は分割して複数回投稿する。
DEFAULT_MAX_CHARS = 10000


# リクエストボディの項目名の候補。cybozu.dev の仕様書を直接確認できなかったため、
# セットアップ時にこの順で実際に投稿を試し、通ったものを config.ini に記録する。
# 失敗した候補は HTTP 400 で弾かれるだけなので、余計な書き込みは残らない。
BODY_TEMPLATE_CANDIDATES = (
    '{"text": "{text}"}',
    '{"comment": {"text": "{text}"}}',
    '{"body": "{text}"}',
    '{"content": {"body": "{text}"}}',
)


class GaroonError(RuntimeError):
    """ガルーンへの接続・投稿に失敗したことを表す。"""


@dataclass
class GaroonClient:
    base_url: str  # 例: https://example.cybozu.com
    login: str
    password: str
    endpoint: str  # 例: /g/api/v1/space/spaces/12/discussions/34/comments
    body_template: str  # {subject} と {text} を含むJSON文字列
    subject: str = "保育公募情報 巡回結果"
    method: str = "POST"
    timeout: int = 30
    max_chars: int = DEFAULT_MAX_CHARS
    basic_auth: tuple[str, str] | None = None
    client_cert: str | None = None
    proxies: dict[str, str] | None = None

    # ------------------------------------------------------------------
    # 生成
    # ------------------------------------------------------------------

    @classmethod
    def from_config(cls, config) -> "GaroonClient":
        if not config.has_section("garoon"):
            raise GaroonError(
                "config.ini に [garoon] セクションがありません。"
                "config.example.ini を参考に作成してください。"
            )
        section = config["garoon"]

        required = ("base_url", "login", "password", "endpoint", "body_template")
        missing = [key for key in required if not section.get(key)]
        if missing:
            raise GaroonError(
                f"config.ini の [garoon] に次の項目が足りません: {', '.join(missing)}"
            )

        basic_auth = None
        if section.get("basic_auth_login"):
            basic_auth = (
                section["basic_auth_login"],
                section.get("basic_auth_password", ""),
            )

        proxies = None
        if section.get("https_proxy"):
            proxies = {"https": section["https_proxy"]}

        return cls(
            base_url=section["base_url"].rstrip("/"),
            login=section["login"],
            password=section["password"],
            endpoint=section["endpoint"],
            body_template=section["body_template"],
            subject=section.get("subject", "保育公募情報 巡回結果"),
            method=section.get("method", "POST").upper(),
            timeout=section.getint("timeout_seconds", 30),
            max_chars=section.getint("max_chars", DEFAULT_MAX_CHARS),
            basic_auth=basic_auth,
            client_cert=section.get("client_cert_path") or None,
            proxies=proxies,
        )

    # ------------------------------------------------------------------
    # 投稿
    # ------------------------------------------------------------------

    def post(self, text: str) -> None:
        """本文を投稿する。長い場合は分割して順に投稿する。"""
        chunks = self._split(text)
        for i, chunk in enumerate(chunks, start=1):
            subject = self.subject
            body = chunk
            if len(chunks) > 1:
                subject = f"{self.subject}（{i}/{len(chunks)}）"
                body = f"（{i}/{len(chunks)}）\n\n{chunk}"
            self._post_once(subject, body)
            if i < len(chunks):
                time.sleep(1)

    def _split(self, text: str) -> list[str]:
        if len(text) <= self.max_chars:
            return [text]

        chunks: list[str] = []
        current: list[str] = []
        size = 0
        for line in text.split("\n"):
            # 1行が上限を超える場合は諦めてそのまま入れる（切ると読めなくなるため）
            if size + len(line) + 1 > self.max_chars and current:
                chunks.append("\n".join(current))
                current, size = [], 0
            current.append(line)
            size += len(line) + 1
        if current:
            chunks.append("\n".join(current))
        return chunks

    def _post_once(self, subject: str, body: str) -> None:
        url = f"{self.base_url}{self.endpoint}"
        payload = self._render_body(subject, body)

        headers = {
            "X-Cybozu-Authorization": base64.b64encode(
                f"{self.login}:{self.password}".encode()
            ).decode(),
            "Content-Type": "application/json",
        }

        try:
            res = requests.request(
                self.method,
                url,
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                auth=self.basic_auth,
                cert=self.client_cert,
                proxies=self.proxies,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise GaroonError(f"接続できませんでした: {exc}") from exc

        if res.status_code >= 400:
            raise GaroonError(
                f"HTTP {res.status_code} が返りました。\n"
                f"  URL: {url}\n"
                f"  応答: {res.text[:500]}\n"
                "  endpoint の指定と、投稿用アカウントの書き込み権限をご確認ください。"
            )

        log.debug("投稿成功 HTTP %s", res.status_code)

    def _render_body(self, subject: str, body: str) -> dict:
        """body_template の {subject} {text} を埋めてJSONに変換する。

        本文には改行や引用符が含まれるため、JSONとして壊れないよう
        json.dumps でエスケープしてから差し込む。
        """
        rendered = self.body_template.replace(
            "{subject}", json.dumps(subject, ensure_ascii=False)[1:-1]
        ).replace("{text}", json.dumps(body, ensure_ascii=False)[1:-1])

        try:
            return json.loads(rendered)
        except json.JSONDecodeError as exc:
            raise GaroonError(
                f"body_template がJSONとして解釈できません: {exc}\n"
                f"  組み立てた内容: {rendered[:300]}"
            ) from exc

    # ------------------------------------------------------------------
    # 疎通確認
    # ------------------------------------------------------------------

    def detect_body_template(self, text: str) -> str:
        """投稿が通るリクエストボディの形を、実際に試して突き止める。

        ガルーンのバージョンによって項目名が違う可能性があるため、候補を順に
        投稿してみて、成功したものを返す。失敗した候補はHTTP 400で弾かれるだけで
        書き込みは残らないので、成功する1件だけがコメントとして投稿される。
        """
        errors = []
        for candidate in BODY_TEMPLATE_CANDIDATES:
            self.body_template = candidate
            try:
                self._post_once(self.subject, text)
                return candidate
            except GaroonError as exc:
                message = str(exc)
                # 認証・権限の問題なら、どの候補を試しても同じなので即座に諦める
                if "HTTP 401" in message or "HTTP 403" in message or "接続できません" in message:
                    raise
                errors.append(f"  {candidate}\n    → {message.splitlines()[0]}")

        raise GaroonError(
            "どの形式でも投稿できませんでした。試した内容:\n" + "\n".join(errors)
        )

    def check(self, probe_endpoint: str = "/g/api/v1/base/users?limit=1") -> str:
        """接続と認証だけを確認する。投稿は行わない。

        probe_endpoint の既定値はユーザー一覧の取得です。環境によって
        使えない場合は、config.ini の probe_endpoint で変更してください。
        """
        url = f"{self.base_url}{probe_endpoint}"
        headers = {
            "X-Cybozu-Authorization": base64.b64encode(
                f"{self.login}:{self.password}".encode()
            ).decode()
        }
        try:
            res = requests.get(
                url,
                headers=headers,
                auth=self.basic_auth,
                cert=self.client_cert,
                proxies=self.proxies,
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise GaroonError(f"接続できませんでした: {exc}") from exc

        # ガルーンは認証失敗を401ではなく520で返すことがあるため、本文でも判定する
        if res.status_code == 401 or "SLASH_LO02" in res.text or "Invalid login name" in res.text:
            raise GaroonError(
                "ログイン名かパスワードが違います。\n"
                "  ・ログイン名は、ブラウザのガルーンのログイン画面で入力しているものと\n"
                "    同じにしてください（表示名ではなく、メールアドレスのこともあります）\n"
                "  ・パスワードを変更した場合は、新しいものを入力してください\n"
                "  ・二要素認証を有効にしている場合、この方式ではログインできません"
            )
        if res.status_code >= 400:
            raise GaroonError(f"HTTP {res.status_code}: {res.text[:300]}")
        return f"接続・認証に成功しました（HTTP {res.status_code}）"


def _cli() -> int:
    """疎通確認とテスト投稿のためのコマンド。

        python garoon.py --check          接続と認証だけ確認する
        python garoon.py --test-post      テストメッセージを実際に投稿する
    """
    import argparse
    import configparser
    from pathlib import Path

    parser = argparse.ArgumentParser(description="ガルーン接続の確認")
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parent / "config.ini")
    parser.add_argument("--check", action="store_true", help="接続と認証を確認する")
    parser.add_argument("--test-post", action="store_true", help="テスト投稿を行う")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    config = configparser.ConfigParser()
    if not args.config.exists():
        print(f"設定ファイルがありません: {args.config}")
        return 2
    config.read(args.config, encoding="utf-8")

    try:
        client = GaroonClient.from_config(config)
        if args.check or not args.test_post:
            probe = config["garoon"].get("probe_endpoint", "/g/api/v1/base/users?limit=1")
            print(client.check(probe))
        if args.test_post:
            client.post("これは保育公募情報 巡回スクリプトのテスト投稿です。")
            print("テスト投稿を送信しました。ガルーン側でご確認ください。")
    except GaroonError as exc:
        print(f"失敗: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
