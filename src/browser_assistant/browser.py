"""Chrome の CDP 起動・Playwright 接続."""

from __future__ import annotations

import logging
import os
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from browser_assistant.config import Settings

logger = logging.getLogger("browser_assistant.browser")

LOGIN_GUIDE = (
    "【重要】このアプリが起動した Chrome に、必要なサイトへ先にログインしてください。"
    "事前ログインがないと、ログイン必須ページの操作・情報収集はできません。"
)


class BrowserError(Exception):
    """ブラウザ起動・接続のユーザー向けエラー."""


@dataclass
class BrowserSession:
    """CDP 経由の Playwright セッション.

    終了方針（仕様決定）:
    - デフォルトはブラウザを残す（`keep_open=True`）。ログイン状態を維持するため。
    - `keep_open=False` のときだけ Playwright 経由でブラウザを閉じる。
    """

    settings: Settings
    keep_open: bool = True
    chrome_path: Path | None = None
    _playwright: Any = field(default=None, repr=False)
    _browser: Any = field(default=None, repr=False)
    _chrome_proc: subprocess.Popen[bytes] | None = field(default=None, repr=False)
    _started_chrome: bool = field(default=False, repr=False)

    @property
    def cdp_url(self) -> str:
        return f"http://{self.settings.cdp_host}:{self.settings.cdp_port}"

    @property
    def browser(self) -> Any:
        if self._browser is None:
            raise BrowserError("ブラウザに未接続です。先に start() してください。")
        return self._browser

    def start(self) -> BrowserSession:
        """既存 CDP があれば再利用、なければ Chrome を起動して接続する."""
        if self._browser is not None:
            return self

        host = self.settings.cdp_host
        port = self.settings.cdp_port

        if is_cdp_available(host, port):
            logger.info("既存のデバッグ Chrome を再利用します: %s", self.cdp_url)
        else:
            if is_port_open(host, port):
                raise BrowserError(
                    f"ポート {port} は使用中ですが、Chrome DevTools (CDP) として応答しません。\n"
                    f"別プロセスが占有している可能性があります。"
                    f" CDP_PORT を変更するか、該当プロセスを終了してください。"
                )
            chrome = self.chrome_path or find_chrome_executable()
            if chrome is None:
                raise BrowserError(
                    "Google Chrome が見つかりませんでした。\n"
                    "Chrome をインストールするか、.env の CHROME_PATH に chrome.exe のフルパスを設定してください。"
                )
            self._chrome_proc = launch_chrome(
                chrome_path=chrome,
                user_data_dir=self.settings.user_data_dir,
                host=host,
                port=port,
            )
            self._started_chrome = True
            wait_for_cdp(host, port, timeout_sec=30)

        self._connect_playwright()
        logger.info(LOGIN_GUIDE)
        contexts = self.browser.contexts
        pages = sum(len(ctx.pages) for ctx in contexts)
        logger.info(
            "CDP 接続成功: contexts=%s pages=%s user_data_dir=%s",
            len(contexts),
            pages,
            self.settings.user_data_dir,
        )
        return self

    def _connect_playwright(self) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserError(
                "playwright がインストールされていません。`pip install -e .` を実行してください。"
            ) from exc

        try:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.connect_over_cdp(self.cdp_url)
        except Exception as exc:  # noqa: BLE001 - ユーザー向けに包む
            self._cleanup_playwright_only()
            raise BrowserError(
                f"Playwright から CDP への接続に失敗しました: {self.cdp_url}\n{exc}"
            ) from exc

    def stop(self) -> None:
        """接続を終了する. keep_open なら Chrome は残す."""
        if self._browser is None and self._playwright is None:
            return

        if self.keep_open:
            logger.info(
                "ブラウザは起動したまま残します（ログイン状態維持）。"
                "閉じる場合は `--close-browser` を付けて起動するか、Chrome を手動で閉じてください。"
            )
            self._cleanup_playwright_only()
            return

        logger.info("ブラウザを閉じます（keep_open=False）")
        try:
            if self._browser is not None:
                self._browser.close()
        except Exception:  # noqa: BLE001
            logger.exception("browser.close() に失敗しました")
        self._cleanup_playwright_only()

        if self._chrome_proc is not None and self._chrome_proc.poll() is None:
            try:
                self._chrome_proc.terminate()
                self._chrome_proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                logger.exception("起動した Chrome プロセスの終了に失敗しました")
                try:
                    self._chrome_proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        self._chrome_proc = None
        self._started_chrome = False

    def _cleanup_playwright_only(self) -> None:
        self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            except Exception:  # noqa: BLE001
                logger.exception("playwright.stop() に失敗しました")
            self._playwright = None

    def __enter__(self) -> BrowserSession:
        return self.start()

    def __exit__(self, *args: object) -> None:
        self.stop()


def find_chrome_executable(explicit: str | Path | None = None) -> Path | None:
    """Windows 中心に Chrome 実行ファイルを探す."""
    if explicit:
        path = Path(explicit)
        if path.is_file():
            return path.resolve()
        return None

    env = os.getenv("CHROME_PATH", "").strip()
    if env:
        path = Path(env)
        if path.is_file():
            return path.resolve()

    which = shutil.which("chrome") or shutil.which("google-chrome")
    if which:
        return Path(which).resolve()

    candidates = [
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        / "Google/Chrome/Application/chrome.exe",
        Path(os.environ.get("LOCALAPPDATA", ""))
        / "Google/Chrome/Application/chrome.exe",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand.resolve()
    return None


def is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def is_cdp_available(host: str, port: int, timeout: float = 1.0) -> bool:
    url = f"http://{host}:{port}/json/version"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return 200 <= getattr(resp, "status", 200) < 300
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def wait_for_cdp(host: str, port: int, timeout_sec: float = 30.0) -> None:
    deadline = time.time() + timeout_sec
    while time.time() < deadline:
        if is_cdp_available(host, port):
            return
        time.sleep(0.25)
    raise BrowserError(
        f"Chrome の CDP が時間内に応答しませんでした: http://{host}:{port}\n"
        "別の Chrome が同じ user-data-dir を使っていないか確認してください。"
    )


def launch_chrome(
    *,
    chrome_path: Path,
    user_data_dir: Path,
    host: str,
    port: int,
) -> subprocess.Popen[bytes]:
    user_data_dir.mkdir(parents=True, exist_ok=True)
    # 127.0.0.1 に限定（外部公開しない）
    debug_addr = f"{host}:{port}"
    args = [
        str(chrome_path),
        f"--remote-debugging-port={port}",
        f"--remote-debugging-address={host}",
        f"--user-data-dir={user_data_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "about:blank",
    ]
    logger.info("Chrome を起動します: %s", chrome_path)
    logger.info("user-data-dir=%s debugging=%s", user_data_dir, debug_addr)
    try:
        # Windows でコンソールに紐づけない
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        return subprocess.Popen(
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
            close_fds=True,
        )
    except OSError as exc:
        raise BrowserError(f"Chrome の起動に失敗しました: {exc}") from exc


def build_session(settings: Settings, *, keep_open: bool | None = None) -> BrowserSession:
    keep = settings.keep_browser_open if keep_open is None else keep_open
    explicit = settings.chrome_path or os.getenv("CHROME_PATH", "").strip() or None
    if explicit:
        chrome_path = find_chrome_executable(explicit)
        if chrome_path is None:
            raise BrowserError(
                f"CHROME_PATH が無効です: {explicit}\n"
                "chrome.exe のフルパスを設定してください。"
            )
    else:
        chrome_path = find_chrome_executable()
    return BrowserSession(
        settings=settings,
        keep_open=keep,
        chrome_path=chrome_path,
    )
