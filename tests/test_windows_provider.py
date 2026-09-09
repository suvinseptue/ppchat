"""Windows WeChat 4.0 provider, config profile, and memory-scan helpers.

All tests use mocks / synthetic bytes — no Windows API or live WeChat.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _load_find_keys_windows():
    path = Path(__file__).resolve().parent.parent / "tools" / "find_keys_windows.py"
    spec = importlib.util.spec_from_file_location("find_keys_windows", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class ScanBufferForLiteralsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_find_keys_windows()

    def test_extracts_96hex_literal_and_ignores_noise(self):
        lit = "ab" * 48  # 96 hex
        buf = b"noise\x00\x01" + b"x'" + lit.encode() + b"' trailing"
        self.assertEqual(self.mod.scan_buffer_for_literals(buf), [lit])

    def test_accepts_loose_64_to_192_hex_and_normalizes_case(self):
        hex64 = "CD" * 32
        hex192 = "ef" * 96
        buf = b"x'" + hex64.encode() + b"'xx" + b"x'" + hex192.encode() + b"'"
        self.assertEqual(
            self.mod.scan_buffer_for_literals(buf),
            [hex64.lower(), hex192],
        )

    def test_dedups_and_skips_illegal_lengths(self):
        good = "11" * 48
        too_short = "22" * 16  # 32 hex
        too_long = "33" * 100  # 200 hex
        buf = (
            b"x'" + too_short.encode() + b"'"
            + b"x'" + good.encode() + b"'"
            + b"x'" + too_long.encode() + b"'"
            + b"x'" + good.encode() + b"'"
        )
        self.assertEqual(self.mod.scan_buffer_for_literals(buf), [good])


class WindowsConfigProfileTests(unittest.TestCase):
    def test_select_profile_win32_uses_windows_container(self):
        from ppchat import config

        root = Path("C:/Users/me/Documents/xwechat_files")
        with patch.object(sys, "platform", "win32"), patch(
            "ppchat.config._discover_windows_db_root", return_value=root
        ):
            profile = config._select_profile()
        self.assertEqual(profile["CONTAINER"], root)
        self.assertIsNone(profile.get("WECHAT_APP"))
        self.assertIsNone(profile.get("EXTRACT_APP"))
        self.assertIsNone(profile.get("TENCENT_TEAM_ID"))

    def test_select_profile_darwin_unchanged(self):
        from ppchat import config

        with patch.object(sys, "platform", "darwin"):
            profile = config._select_profile()
        self.assertEqual(profile, config._macos_profile())
        self.assertEqual(config.WECHAT_APP, Path("/Applications/WeChat.app"))
        self.assertEqual(
            config.CONTAINER,
            Path.home()
            / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files",
        )

    def test_discover_prefers_config_json_db_root(self):
        from ppchat import config

        explicit = Path("D:/custom/xwechat_files")
        with patch("ppchat.config._read_db_root_from_config", return_value=explicit), patch(
            "ppchat.config._read_filesave_path_from_registry",
            return_value=Path("E:/reg"),
        ):
            self.assertEqual(config._discover_windows_db_root(), explicit)

    def test_discover_uses_registry_then_userprofile(self):
        from ppchat import config

        reg = Path("E:/WeChat Files")
        with patch("ppchat.config._read_db_root_from_config", return_value=None), patch(
            "ppchat.config._read_filesave_path_from_registry", return_value=reg
        ):
            self.assertEqual(
                config._discover_windows_db_root(),
                config._normalize_windows_container(reg),
            )

        with patch("ppchat.config._read_db_root_from_config", return_value=None), patch(
            "ppchat.config._read_filesave_path_from_registry", return_value=None
        ), patch.dict(os.environ, {"USERPROFILE": r"C:\Users\alice"}):
            self.assertEqual(
                config._discover_windows_db_root(),
                Path(r"C:\Users\alice") / "Documents" / "xwechat_files",
            )

    def test_normalize_appends_xwechat_files_unless_already(self):
        from ppchat import config

        self.assertEqual(
            config._normalize_windows_container(Path("D:/data/xwechat_files")),
            Path("D:/data/xwechat_files"),
        )
        self.assertEqual(
            config._normalize_windows_container(Path("D:/data")),
            Path("D:/data") / "xwechat_files",
        )

    def test_registry_helper_is_none_without_winreg(self):
        from ppchat import config

        self.assertIsNone(config._read_filesave_path_from_registry())


class GetProviderWindowsTests(unittest.TestCase):
    def test_get_provider_explicit_win_name(self):
        from ppchat.provider import WeChat4WindowsProvider, get_provider

        p = get_provider("wechat4-win")
        self.assertIsInstance(p, WeChat4WindowsProvider)
        self.assertEqual(p.name, "wechat4-win")

    def test_constructor_does_not_touch_disk_or_keys(self):
        from ppchat.provider import WeChat4WindowsProvider

        with patch("ppchat.config.account_dirs") as ad, patch(
            "ppchat.keys.load_key_map"
        ) as lk:
            p = WeChat4WindowsProvider()
        self.assertEqual(p.name, "wechat4-win")
        ad.assert_not_called()
        lk.assert_not_called()


class WeChat4WindowsProviderTests(unittest.TestCase):
    def test_account_and_db_files_delegate_to_config(self):
        from ppchat.provider import WeChat4WindowsProvider

        dirs = [Path("/acct")]
        dbs = [Path("/acct/db_storage/message/message_0.db")]
        with patch("ppchat.config.account_dirs", return_value=dirs), patch(
            "ppchat.config.db_files", return_value=dbs
        ) as db_files:
            p = WeChat4WindowsProvider()
            self.assertEqual(p.account_dirs(), dirs)
            self.assertEqual(p.db_files(Path("/acct")), dbs)
        db_files.assert_called_once_with(Path("/acct"))

    def test_acquire_keys_returns_cached_map(self):
        from ppchat.provider import WeChat4WindowsProvider

        cached = {"/tmp/a.db": "abc"}
        with patch("ppchat.keys.load_key_map", return_value=cached), patch(
            "ppchat.keys.build_key_map"
        ) as build:
            self.assertEqual(WeChat4WindowsProvider().acquire_keys(), cached)
        build.assert_not_called()

    def test_acquire_keys_builds_from_candidates_when_cache_empty(self):
        from ppchat.provider import WeChat4WindowsProvider

        built = {"C:/db.db": "ab" * 48}
        with tempfile.TemporaryDirectory() as td:
            cand = Path(td) / "candidates_windows.json"
            cand.write_text('{"windows":[],"literals":["aa"],"keys":[],"pairs":[]}')
            with patch("ppchat.keys.load_key_map", return_value={}), patch(
                "ppchat.provider.config.CANDIDATES_WINDOWS_JSON", cand
            ), patch("ppchat.keys.build_key_map", return_value=built) as build, patch(
                "ppchat.keys.save_key_map"
            ) as save:
                result = WeChat4WindowsProvider().acquire_keys(Path("C:/acct"))
        self.assertEqual(result, built)
        build.assert_called_once()
        self.assertEqual(build.call_args.args[1], Path("C:/acct"))
        save.assert_called_once_with(built)

    def test_acquire_keys_missing_candidates_raises(self):
        from ppchat.provider import WeChat4WindowsProvider

        missing = MagicMock()
        missing.exists.return_value = False
        with patch("ppchat.keys.load_key_map", return_value={}), patch(
            "ppchat.provider.config.CANDIDATES_WINDOWS_JSON", missing
        ):
            with self.assertRaises(RuntimeError) as ctx:
                WeChat4WindowsProvider().acquire_keys()
        self.assertIn("find_keys_windows.py", str(ctx.exception))

    def test_ingest_delegates_to_parse(self):
        from ppchat.provider import WeChat4WindowsProvider

        def fake_ingest(chat_wxid=None, account_dir=None):
            fake_ingest.called = {"chat_wxid": chat_wxid, "account_dir": account_dir}
            return {"chats": 2}

        with patch("ppchat.parse.ingest", fake_ingest):
            result = WeChat4WindowsProvider().ingest("wxid_win", Path("C:/acct"))
        self.assertEqual(result, {"chats": 2})
        self.assertEqual(fake_ingest.called["chat_wxid"], "wxid_win")
        self.assertEqual(fake_ingest.called["account_dir"], Path("C:/acct"))


class BootstrapHomeTests(unittest.TestCase):
    def test_creates_home_and_writes_config_once(self):
        from ppchat import config

        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / ".ppchat"
            root = Path(td) / "xwechat_files"
            info = config.bootstrap_home(home=home, db_root=root)
            self.assertTrue(home.is_dir())
            self.assertEqual(info["home"], home)
            self.assertEqual(info["keys_json"], home / "keys.json")
            self.assertEqual(info["store_db"], home / "ppchat.db")
            self.assertEqual(info["candidates_windows"], home / "candidates_windows.json")
            self.assertEqual(info["config"], home / "config.json")
            self.assertEqual(json.loads(info["config"].read_text())["db_root"], str(root))

            info["config"].write_text('{"db_root": "keep-me"}', encoding="utf-8")
            again = config.bootstrap_home(home=home, db_root=Path(td) / "other")
            self.assertEqual(json.loads(again["config"].read_text())["db_root"], "keep-me")

    def test_skips_config_when_db_root_missing(self):
        from ppchat import config

        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / ".ppchat"
            info = config.bootstrap_home(home=home, db_root=None)
            self.assertTrue(home.is_dir())
            self.assertFalse(info["config"].exists())


if __name__ == "__main__":
    unittest.main()
