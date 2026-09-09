"""Guards that keep daily WeChat officially signed."""
from __future__ import annotations

import unittest
from pathlib import Path

from ppchat.wechat_app import (
    TENCENT_TEAM_ID,
    AdhocOfficialError,
    SignTargetError,
    assert_official_is_tencent,
    assert_sign_target_is_extract_copy,
    parse_codesign_info,
)


ADHOC_DUMP = """\
Executable=/Applications/WeChat.app/Contents/MacOS/WeChat
Identifier=com.tencent.xinWeChat
Format=app bundle with Mach-O universal (x86_64 arm64)
CodeDirectory v=20400 size=13998 flags=0x2(adhoc) hashes=431+3 location=embedded
Signature=adhoc
TeamIdentifier=not set
"""

OFFICIAL_DUMP = """\
Executable=/Applications/WeChat.app/Contents/MacOS/WeChat
Identifier=com.tencent.xinWeChat
Format=app bundle with Mach-O universal (x86_64 arm64)
CodeDirectory v=20500 size=14000 flags=0x10000(runtime) hashes=431+3 location=embedded
Authority=Developer ID Application: Tencent Technology (Shenzhen) Company Limited (5A4RE8SF68)
Authority=Developer ID Certification Authority
Authority=Apple Root CA
TeamIdentifier=5A4RE8SF68
"""


class ParseCodesignTests(unittest.TestCase):
    def test_adhoc_is_not_official(self):
        info = parse_codesign_info(ADHOC_DUMP)
        self.assertTrue(info.is_adhoc)
        self.assertFalse(info.is_tencent_official)
        self.assertEqual(info.team_id, "")

    def test_tencent_developer_id_is_official(self):
        info = parse_codesign_info(OFFICIAL_DUMP)
        self.assertFalse(info.is_adhoc)
        self.assertTrue(info.is_tencent_official)
        self.assertEqual(info.team_id, TENCENT_TEAM_ID)


class PrepareGuardTests(unittest.TestCase):
    def test_refuse_prepare_when_official_is_adhoc(self):
        info = parse_codesign_info(ADHOC_DUMP)
        with self.assertRaises(AdhocOfficialError):
            assert_official_is_tencent(info)

    def test_allow_prepare_when_official_is_tencent(self):
        info = parse_codesign_info(OFFICIAL_DUMP)
        assert_official_is_tencent(info)


class SignTargetGuardTests(unittest.TestCase):
    def test_refuse_to_sign_applications_wechat(self):
        with self.assertRaises(SignTargetError):
            assert_sign_target_is_extract_copy(Path("/Applications/WeChat.app"))

    def test_refuse_to_sign_outside_ppchat_home(self):
        with self.assertRaises(SignTargetError):
            assert_sign_target_is_extract_copy(Path("/tmp/WeChat.app"))

    def test_allow_extract_copy_under_ppchat_home(self):
        from ppchat import config

        assert_sign_target_is_extract_copy(config.EXTRACT_APP)


if __name__ == "__main__":
    unittest.main()
