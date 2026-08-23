"""Provider factory and macOS config profile (no live WeChat data)."""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from ppchat import config


class MacOSConfigProfileTests(unittest.TestCase):
    def test_macos_profile_constants_unchanged(self):
        self.assertEqual(config.WECHAT_APP, Path("/Applications/WeChat.app"))
        self.assertEqual(
            config.WECHAT_BIN, Path("/Applications/WeChat.app/Contents/MacOS/WeChat")
        )
        self.assertEqual(config.TENCENT_TEAM_ID, "5A4RE8SF68")
        self.assertEqual(
            config.CONTAINER,
            Path.home()
            / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files",
        )
        self.assertEqual(config.EXTRACT_ROOT, Path.home() / ".ppchat" / "extract")
        self.assertEqual(
            config.EXTRACT_APP, Path.home() / ".ppchat" / "extract" / "WeChat.app"
        )
        self.assertEqual(
            config.EXTRACT_BIN,
            Path.home() / ".ppchat" / "extract" / "WeChat.app" / "Contents/MacOS/WeChat",
        )
        self.assertEqual(config.PPCHAT_HOME, Path.home() / ".ppchat")
        self.assertEqual(config.KEYS_JSON, Path.home() / ".ppchat" / "keys.json")
        self.assertEqual(config.STORE_DB, Path.home() / ".ppchat" / "ppchat.db")


class GetProviderTests(unittest.TestCase):
    def test_get_provider_defaults_to_mac_on_darwin(self):
        from ppchat.provider import WeChat4MacProvider, get_provider

        env = {k: v for k, v in os.environ.items() if k != "PPCHAT_PROVIDER"}
        with patch.dict(os.environ, env, clear=True), patch.object(sys, "platform", "darwin"):
            p = get_provider()
        self.assertIsInstance(p, WeChat4MacProvider)
        self.assertEqual(p.name, "wechat4-mac")

    def test_get_provider_explicit_mac_name(self):
        from ppchat.provider import WeChat4MacProvider, get_provider

        p = get_provider("wechat4-mac")
        self.assertIsInstance(p, WeChat4MacProvider)
        self.assertEqual(p.name, "wechat4-mac")

    def test_get_provider_unknown_name_raises(self):
        from ppchat.provider import get_provider

        with self.assertRaises(NotImplementedError) as ctx:
            get_provider("not-a-provider")
        self.assertIn("unknown provider", str(ctx.exception))

    def test_get_provider_unknown_env_raises(self):
        from ppchat.provider import get_provider

        with patch.dict(os.environ, {"PPCHAT_PROVIDER": "wechat3-win"}):
            with self.assertRaises(NotImplementedError) as ctx:
                get_provider()
        self.assertIn("unknown provider", str(ctx.exception))

    def test_get_provider_win32_defaults_to_windows(self):
        from ppchat.provider import WeChat4WindowsProvider, get_provider

        env = {k: v for k, v in os.environ.items() if k != "PPCHAT_PROVIDER"}
        with patch.dict(os.environ, env, clear=True), patch.object(sys, "platform", "win32"):
            p = get_provider()
        self.assertIsInstance(p, WeChat4WindowsProvider)
        self.assertEqual(p.name, "wechat4-win")


class WeChat4MacProviderTests(unittest.TestCase):
    def test_acquire_keys_returns_cached_map(self):
        from ppchat.provider import WeChat4MacProvider

        with patch("ppchat.keys.load_key_map", return_value={"/tmp/a.db": "abc"}):
            self.assertEqual(WeChat4MacProvider().acquire_keys(), {"/tmp/a.db": "abc"})

    def test_acquire_keys_missing_cache_raises(self):
        from ppchat.provider import WeChat4MacProvider

        with patch("ppchat.keys.load_key_map", return_value={}):
            with self.assertRaises(RuntimeError) as ctx:
                WeChat4MacProvider().acquire_keys()
        self.assertIn("tools", str(ctx.exception))

    def test_ingest_delegates_to_parse(self):
        from ppchat.provider import WeChat4MacProvider

        def fake_ingest(chat_wxid=None, account_dir=None):
            fake_ingest.called = {"chat_wxid": chat_wxid, "account_dir": account_dir}
            return {"chats": 1}

        with patch("ppchat.parse.ingest", fake_ingest):
            result = WeChat4MacProvider().ingest("wxid_x")
        self.assertEqual(result, {"chats": 1})
        self.assertEqual(fake_ingest.called["chat_wxid"], "wxid_x")


if __name__ == "__main__":
    unittest.main()
