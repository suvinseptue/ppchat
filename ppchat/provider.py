"""Pluggable MessageProvider seam for chat-log ingestion."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Protocol

from . import config, keys, parse


class MessageProvider(Protocol):
    name: str

    def account_dirs(self) -> list[Path]: ...
    def db_files(self, account_dir: Path | None = None) -> list[Path]: ...
    def acquire_keys(self, account_dir: Path | None = None) -> dict[str, str]: ...
    def ingest(
        self, chat_wxid: str | None = None, account_dir: Path | None = None
    ) -> dict: ...


class WeChat4MacProvider:
    name = "wechat4-mac"

    def account_dirs(self) -> list[Path]:
        return config.account_dirs()

    def db_files(self, account_dir: Path | None = None) -> list[Path]:
        return config.db_files(account_dir)

    def acquire_keys(self, account_dir: Path | None = None) -> dict[str, str]:
        key_map = keys.load_key_map()
        if not key_map:
            raise RuntimeError(
                "no key map; run tools/get_keys.sh then tools/build_keymap.py first"
            )
        return key_map

    def ingest(
        self, chat_wxid: str | None = None, account_dir: Path | None = None
    ) -> dict:
        return parse.ingest(chat_wxid=chat_wxid, account_dir=account_dir)


class WeChat4WindowsProvider:
    name = "wechat4-win"

    def account_dirs(self) -> list[Path]:
        return config.account_dirs()

    def db_files(self, account_dir: Path | None = None) -> list[Path]:
        return config.db_files(account_dir)

    def acquire_keys(self, account_dir: Path | None = None) -> dict[str, str]:
        key_map = keys.load_key_map()
        if key_map:
            return key_map
        cand_path = config.CANDIDATES_WINDOWS_JSON
        if not cand_path.exists():
            raise RuntimeError(
                "no Windows key candidates; run as Administrator: "
                "python tools/find_keys_windows.py "
                "(Weixin.exe must be logged in), then "
                "python tools/build_keymap.py ~/.ppchat/candidates_windows.json"
            )
        candidates = keys.load_candidates(cand_path)
        key_map = keys.build_key_map(candidates, account_dir)
        keys.save_key_map(key_map)
        return key_map

    def ingest(
        self, chat_wxid: str | None = None, account_dir: Path | None = None
    ) -> dict:
        # REAL-MACHINE-VERIFY: Windows 4.0 Msg_* / contact.db schema vs macOS 4.x
        # (column names, create_time units, sender prefix, zstd). Phase 4 field-compare.
        return parse.ingest(chat_wxid=chat_wxid, account_dir=account_dir)


def _default_provider_name() -> str:
    if sys.platform == "darwin":
        return "wechat4-mac"
    if sys.platform.startswith("win"):
        return "wechat4-win"
    return sys.platform


def get_provider(name: str | None = None) -> MessageProvider:
    chosen = name or os.environ.get("PPCHAT_PROVIDER") or _default_provider_name()
    if chosen == "wechat4-mac":
        return WeChat4MacProvider()
    if chosen == "wechat4-win":
        return WeChat4WindowsProvider()
    raise NotImplementedError(f"unknown provider {chosen!r}")
