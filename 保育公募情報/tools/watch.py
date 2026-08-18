#!/usr/bin/env python3
"""保育・学童・児童発達支援の公募情報を各市HPから巡回し、変更点だけを報告する。

使い方の概要（詳細は tools/README.md）:

    python watch.py --init          初回。現状を基準として記録するだけ（投稿しない）
    python watch.py --dry-run       巡回して差分を画面に出す（投稿しない）
    python watch.py                 巡回して差分があればガルーンに投稿する

設計方針:

- 変更がない日は何も投稿しない。毎日同じ投稿が積まれると読まれなくなるため。
- HTML全体を比較すると広告や更新日時で毎回「変更あり」になるので、
  本文から「募集」「公募」などのキーワードを含む行とリンクだけを抜き出して比較する。
- 市のサーバに負荷をかけないよう、1ページずつ間隔をあけて取得する。
"""

from __future__ import annotations

import argparse
import configparser
import hashlib
import json
import logging
import re
import sys
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

try:
    import requests
    import yaml
    from bs4 import BeautifulSoup
except ImportError as exc:  # pragma: no cover
    sys.exit(
        f"必要なライブラリが見つかりません: {exc}\n"
        "  pip install -r requirements.txt を実行してください。"
    )

JST = timezone(timedelta(hours=9))
HERE = Path(__file__).resolve().parent
STATE_VERSION = 1

# Windowsのコンソールは既定が日本語コードページのため、日本語の表示に失敗して
# 途中で落ちることがある。UTF-8に切り替えて防ぐ。
if sys.platform == "win32":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass

# 公募に関係する行・リンクだけを拾うためのキーワード。
# ここを広げるとノイズが増え、狭めると取りこぼす。運用しながら調整する。
KEYWORDS = (
    "募集",
    "公募",
    "プロポーザル",
    "選定",
    "指定管理",
    "民間移管",
    "民営化",
    "設置・運営",
    "設置運営",
    "整備事業者",
    "運営事業者",
    "移管先",
)

# 本文領域の推定に使うセレクタ。上から順に試し、最初に見つかったものを使う。
CONTENT_SELECTORS = (
    "main",
    "#main",
    "#content",
    ".content",
    "#contents",
    ".contents",
    "#tmp_contents",  # 自治体サイトでよく使われる（大阪市など）
    "article",
)

# 本文から除去するタグ（ナビゲーションや広告は毎回変わるためノイズになる）
DROP_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "aside", "iframe")

log = logging.getLogger("watch")


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------


@dataclass
class Source:
    city: str
    category: str
    name: str
    url: str
    priority: str = "normal"
    selector: str | None = None

    @property
    def label(self) -> str:
        return f"[{self.city}／{self.category}] {self.name}"


@dataclass
class Snapshot:
    """1ページから抜き出した「公募に関係しそうな部分」だけの状態。"""

    lines: list[str] = field(default_factory=list)
    links: dict[str, str] = field(default_factory=dict)  # URL -> リンク文字列

    def to_json(self) -> dict:
        return {"lines": self.lines, "links": self.links}

    @classmethod
    def from_json(cls, data: dict) -> "Snapshot":
        return cls(lines=list(data.get("lines", [])), links=dict(data.get("links", {})))


@dataclass
class Diff:
    source: Source
    new_lines: list[str] = field(default_factory=list)
    gone_lines: list[str] = field(default_factory=list)
    new_links: list[tuple[str, str]] = field(default_factory=list)  # (テキスト, URL)
    error: str | None = None

    @property
    def has_change(self) -> bool:
        return bool(self.new_lines or self.gone_lines or self.new_links)


# ---------------------------------------------------------------------------
# 取得と抽出
# ---------------------------------------------------------------------------


def normalize(text: str) -> str:
    """全角半角のゆらぎと空白を吸収して比較しやすくする。"""
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def has_keyword(text: str) -> bool:
    return any(kw in text for kw in KEYWORDS)


def fetch(session: requests.Session, url: str, timeout: int, retries: int) -> str:
    """1ページ取得する。一時的な失敗はリトライし、恒久的な失敗は例外を投げる。"""
    last_exc: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            res = session.get(url, timeout=timeout)
            res.raise_for_status()
            # 自治体サイトは Shift_JIS / EUC-JP のこともある。
            # requests の推測が甘いことがあるので apparent_encoding を優先する。
            if res.encoding is None or res.encoding.lower() == "iso-8859-1":
                res.encoding = res.apparent_encoding
            return res.text
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status and 400 <= status < 500 and status != 429:
                # 404 や 403 はリトライしても直らない
                raise
            last_exc = exc
        except requests.RequestException as exc:
            last_exc = exc

        if attempt < retries:
            wait = 2**attempt
            log.warning("取得失敗(%s回目) %s: %s / %s秒待って再試行", attempt, url, last_exc, wait)
            time.sleep(wait)

    raise RuntimeError(f"{retries}回試行しましたが取得できませんでした: {last_exc}")


def extract(html: str, base_url: str, selector: str | None) -> Snapshot:
    """HTMLから、公募に関係する行とリンクだけを抜き出す。"""
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(list(DROP_TAGS)):
        tag.decompose()

    root = None
    if selector:
        root = soup.select_one(selector)
        if root is None:
            log.warning("セレクタ %s が見つかりませんでした。ページ全体を対象にします。", selector)
    if root is None:
        for candidate in CONTENT_SELECTORS:
            root = soup.select_one(candidate)
            if root is not None:
                break
    if root is None:
        root = soup.body or soup

    lines = []
    for raw in root.get_text("\n").split("\n"):
        line = normalize(raw)
        # 短すぎる行は見出しの断片、長すぎる行は本文まるごとで差分が取りにくい
        if 6 <= len(line) <= 200 and has_keyword(line):
            lines.append(line)

    links: dict[str, str] = {}
    for anchor in root.find_all("a", href=True):
        text = normalize(anchor.get_text())
        if not text or not has_keyword(text):
            continue
        href = urljoin(base_url, anchor["href"])
        if urlparse(href).scheme not in ("http", "https"):
            continue
        links.setdefault(href, text)

    # 同じ文言が繰り返し出るページがあるので重複を落とす（順序は保つ）
    return Snapshot(lines=sorted(set(lines)), links=links)


# ---------------------------------------------------------------------------
# 差分
# ---------------------------------------------------------------------------


def compare(source: Source, previous: Snapshot | None, current: Snapshot) -> Diff:
    diff = Diff(source=source)
    if previous is None:
        # 初回は差分を出さない（全部を新着として報告しても意味がないため）
        return diff

    for url, text in current.links.items():
        if url not in previous.links:
            diff.new_links.append((text, url))
    diff.new_links.sort()

    # リンク文字列はそのまま本文の行としても拾われるため、
    # 新着リンクと同じ文言の行は落として二重表示を防ぐ。
    link_texts = {text for text, _ in diff.new_links}
    gone_link_texts = {
        text for url, text in previous.links.items() if url not in current.links
    }

    prev_lines = set(previous.lines)
    curr_lines = set(current.lines)
    diff.new_lines = sorted(curr_lines - prev_lines - link_texts)
    diff.gone_lines = sorted(prev_lines - curr_lines - gone_link_texts)

    return diff


# ---------------------------------------------------------------------------
# レポート
# ---------------------------------------------------------------------------


def build_report(diffs: list[Diff], now: datetime) -> str:
    changed = [d for d in diffs if d.has_change]
    errors = [d for d in diffs if d.error]

    lines: list[str] = []
    # %-m のような書式は Windows で使えないため、数値から組み立てる
    stamp = f"{now.year}年{now.month}月{now.day}日 {now.hour:02d}:{now.minute:02d}"
    lines.append(f"【保育・学童・児発 公募情報 巡回結果】{stamp}")
    lines.append("")
    lines.append(f"巡回 {len(diffs)}ページ／変更 {len(changed)}件／取得失敗 {len(errors)}件")
    lines.append("")

    if changed:
        # 優先度の高いページを先に出す
        changed.sort(key=lambda d: (d.source.priority != "high", d.source.city))
        lines.append("─" * 30)
        lines.append("■ 変更のあったページ")
        lines.append("─" * 30)
        for d in changed:
            lines.append("")
            lines.append(d.source.label)
            lines.append(f"  {d.source.url}")
            for text, url in d.new_links:
                lines.append(f"  ＋ {text}")
                lines.append(f"    {url}")
            for line in d.new_lines:
                lines.append(f"  ＋ {line}")
            for line in d.gone_lines:
                lines.append(f"  － {line}（掲載が消えました）")
        lines.append("")

    if errors:
        lines.append("─" * 30)
        lines.append("■ 取得できなかったページ")
        lines.append("─" * 30)
        lines.append("※監視が止まっている状態です。URLの変更やネットワークをご確認ください。")
        for d in errors:
            lines.append("")
            lines.append(d.source.label)
            lines.append(f"  {d.source.url}")
            lines.append(f"  {d.error}")
        lines.append("")

    lines.append("─" * 30)
    lines.append("この投稿は巡回スクリプトによる自動投稿です。")
    lines.append("掲載内容は各市HPの原文をご確認ください。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 設定・状態の読み書き
# ---------------------------------------------------------------------------


def load_sources(path: Path) -> list[Source]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    defaults = data.get("defaults") or {}
    sources = []
    for entry in data.get("sources") or []:
        merged = {**defaults, **entry}
        sources.append(
            Source(
                city=merged["city"],
                category=merged["category"],
                name=merged["name"],
                url=merged["url"],
                priority=merged.get("priority", "normal"),
                selector=merged.get("selector"),
            )
        )
    if not sources:
        raise ValueError(f"巡回対象が1件もありません: {path}")
    return sources


def load_state(path: Path) -> dict[str, Snapshot]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("version") != STATE_VERSION:
        log.warning("状態ファイルの形式が違うため、初回扱いにします: %s", path)
        return {}
    return {url: Snapshot.from_json(snap) for url, snap in data.get("sources", {}).items()}


def save_state(path: Path, snapshots: dict[str, Snapshot], now: datetime) -> None:
    payload = {
        "version": STATE_VERSION,
        "updated_at": now.isoformat(),
        "sources": {url: snap.to_json() for url, snap in snapshots.items()},
    }
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)  # 書き込み中に落ちても状態ファイルを壊さない


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------


def crawl(
    sources: list[Source],
    state: dict[str, Snapshot],
    session: requests.Session,
    delay: float,
    timeout: int,
    retries: int,
) -> tuple[list[Diff], dict[str, Snapshot]]:
    diffs: list[Diff] = []
    updated = dict(state)

    for i, source in enumerate(sources):
        if i > 0:
            time.sleep(delay)
        log.info("[%s/%s] %s", i + 1, len(sources), source.url)
        try:
            html = fetch(session, source.url, timeout=timeout, retries=retries)
            current = extract(html, source.url, source.selector)
        except Exception as exc:
            log.error("失敗: %s: %s", source.url, exc)
            diffs.append(Diff(source=source, error=str(exc)))
            continue  # 失敗したページの状態は更新しない（次回また比較できる）

        diffs.append(compare(source, state.get(source.url), current))
        updated[source.url] = current

    return diffs, updated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="各市HPの保育公募情報を巡回する")
    parser.add_argument("--config", type=Path, default=HERE / "config.ini")
    parser.add_argument("--sources", type=Path, default=HERE / "sources.yaml")
    parser.add_argument("--state", type=Path, default=HERE / "state.json")
    parser.add_argument(
        "--init",
        action="store_true",
        help="現状を基準として記録するだけ。差分は出さず投稿もしない（初回に1度実行する）",
    )
    parser.add_argument("--dry-run", action="store_true", help="投稿せず結果を画面に出す")
    parser.add_argument(
        "--post-always",
        action="store_true",
        help="変更がなくても投稿する（週次のリマインド用）",
    )
    parser.add_argument("--only", help="市名で絞り込む（例: --only 大阪市）")
    parser.add_argument("--report-file", type=Path, help="レポートをファイルにも保存する")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    config = configparser.ConfigParser()
    if args.config.exists():
        config.read(args.config, encoding="utf-8")
    elif not (args.dry_run or args.init):
        log.error(
            "設定ファイルがありません: %s\n"
            "config.example.ini をコピーして作成してください。"
            "（--dry-run / --init は設定なしでも動きます）",
            args.config,
        )
        return 2

    crawl_cfg = config["crawl"] if config.has_section("crawl") else {}
    delay = float(crawl_cfg.get("delay_seconds", 5))
    timeout = int(crawl_cfg.get("timeout_seconds", 30))
    retries = int(crawl_cfg.get("retries", 3))
    # HTTPヘッダは latin-1 しか通らないため、User-Agent に日本語は使えない
    user_agent = crawl_cfg.get(
        "user_agent",
        "hoiku-koubo-watch/1.0 (internal monitoring script)",
    )
    try:
        user_agent.encode("latin-1")
    except UnicodeEncodeError:
        log.error(
            "config.ini の user_agent に日本語などの非ASCII文字が含まれています。\n"
            "HTTPヘッダはASCIIしか扱えないため、半角英数字で書いてください。"
        )
        return 2

    sources = load_sources(args.sources)
    if args.only:
        sources = [s for s in sources if s.city == args.only]
        if not sources:
            log.error("該当する市がありません: %s", args.only)
            return 2

    state = {} if args.init else load_state(args.state)
    now = datetime.now(JST)

    session = requests.Session()
    session.headers.update({"User-Agent": user_agent})

    diffs, updated = crawl(sources, state, session, delay, timeout, retries)
    save_state(args.state, updated, now)

    if args.init:
        ok = len([d for d in diffs if not d.error])
        log.info("基準を記録しました（%s/%sページ）。次回から差分を報告します。", ok, len(diffs))
        return 0

    report = build_report(diffs, now)
    if args.report_file:
        args.report_file.write_text(report + "\n", encoding="utf-8")
        log.info("レポートを保存しました: %s", args.report_file)

    has_change = any(d.has_change for d in diffs)
    has_error = any(d.error for d in diffs)

    if args.dry_run:
        print()
        print(report)
        return 0

    if not (has_change or has_error or args.post_always):
        log.info("変更はありませんでした。投稿しません。")
        return 0

    from garoon import GaroonClient, GaroonError  # 投稿するときだけ読み込む

    try:
        client = GaroonClient.from_config(config)
        client.post(report)
    except GaroonError as exc:
        log.error("ガルーンへの投稿に失敗しました: %s", exc)
        # 投稿できなかった内容は失わないよう画面に出す
        print()
        print(report)
        return 1

    log.info("ガルーンに投稿しました。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
