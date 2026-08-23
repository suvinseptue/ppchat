"""WeChat 4.x .dat image decode + image-key scan (synthetic bytes only)."""
from __future__ import annotations

import hashlib
import importlib.util
import sqlite3
import struct
import tempfile
import unittest
from pathlib import Path

from Crypto.Cipher import AES

from ppchat.images import (
    AES_V1_MAGIC,
    AES_V2_MAGIC,
    V1_DEFAULT_AES_KEY,
    decode_aes_v1,
    decode_aes_v2,
    decode_dat,
    decode_xor,
    detect_variant,
    extract_and_update_attachment,
    extract_image,
)
from ppchat.store import SCHEMA


def _load_find_image_key_windows():
    path = Path(__file__).resolve().parent.parent / "tools" / "find_image_key_windows.py"
    spec = importlib.util.spec_from_file_location("find_image_key_windows", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _pkcs7_pad(data: bytes, block: int = 16) -> bytes:
    n = block - (len(data) % block)
    return data + bytes([n] * n)


def _build_aes_dat(
    magic: bytes,
    aes_plain: bytes,
    raw: bytes,
    xor_plain: bytes,
    aes_key: bytes,
    xor_key: int,
) -> bytes:
    """Build a V1/V2 .dat from plaintext segments (roundtrip fixture)."""
    ct = AES.new(aes_key, AES.MODE_ECB).encrypt(_pkcs7_pad(aes_plain))
    header = magic + struct.pack("<II", len(aes_plain), len(xor_plain)) + b"\x00"
    xor_ct = bytes(b ^ xor_key for b in xor_plain)
    return header + ct + raw + xor_ct


MINI_JPEG = b"\xff\xd8\xff" + b"\x00" * 13 + b"\xff\xd9"


class DetectVariantTests(unittest.TestCase):
    def test_aes_v1_magic_header(self):
        self.assertEqual(detect_variant(AES_V1_MAGIC + b"\x00" * 20), "aes_v1")

    def test_aes_v2_magic_header(self):
        self.assertEqual(detect_variant(AES_V2_MAGIC + b"\x00" * 20), "aes_v2")

    def test_xor_jpeg_header(self):
        key = 0x5A
        blob = bytes(b ^ key for b in MINI_JPEG)
        self.assertEqual(detect_variant(blob), "xor")

    def test_unknown_garbage(self):
        self.assertEqual(detect_variant(b"\x00\x01\x02\x03not-an-image"), "unknown")


class DecodeXorTests(unittest.TestCase):
    def test_roundtrip(self):
        plain = b"hello-wechat-image"
        key = 0x7B
        cipher = bytes(b ^ key for b in plain)
        self.assertEqual(decode_xor(cipher, key), plain)


class DecodeAesV1V2Tests(unittest.TestCase):
    def test_v1_roundtrip(self):
        aes_key = V1_DEFAULT_AES_KEY
        xor_key = 0x11
        aes_plain = MINI_JPEG[:8]
        raw = b"RAWSEG"
        xor_plain = b"TAIL"
        dat = _build_aes_dat(AES_V1_MAGIC, aes_plain, raw, xor_plain, aes_key, xor_key)
        self.assertEqual(decode_aes_v1(dat, aes_key, xor_key=xor_key), aes_plain + raw + xor_plain)

    def test_v2_roundtrip(self):
        aes_key = b"0123456789abcdef"
        xor_key = 0x22
        aes_plain = MINI_JPEG
        raw = b""
        xor_plain = b"\x00\x01tail"
        dat = _build_aes_dat(AES_V2_MAGIC, aes_plain, raw, xor_plain, aes_key, xor_key)
        self.assertEqual(decode_aes_v2(dat, aes_key, xor_key=xor_key), aes_plain + raw + xor_plain)

    def test_v1_rejects_v2_magic(self):
        dat = _build_aes_dat(AES_V2_MAGIC, b"abc", b"", b"", b"0123456789abcdef", 0)
        with self.assertRaises(ValueError):
            decode_aes_v1(dat, b"0123456789abcdef")

    def test_v2_rejects_v1_magic(self):
        dat = _build_aes_dat(AES_V1_MAGIC, b"abc", b"", b"", V1_DEFAULT_AES_KEY, 0)
        with self.assertRaises(ValueError):
            decode_aes_v2(dat, V1_DEFAULT_AES_KEY)


class DecodeDatTests(unittest.TestCase):
    def test_xor_jpeg_detects_format(self):
        xor_key = 0x42
        dat = bytes(b ^ xor_key for b in MINI_JPEG)
        plain, fmt = decode_dat(dat, aes_key=None, xor_key=xor_key)
        self.assertEqual(fmt, "jpg")
        self.assertEqual(plain, MINI_JPEG)

    def test_v2_dispatches_and_detects_jpg(self):
        aes_key = b"0123456789abcdef"
        xor_key = 0x33
        dat = _build_aes_dat(AES_V2_MAGIC, MINI_JPEG, b"mid", b"zz", aes_key, xor_key)
        plain, fmt = decode_dat(dat, aes_key=aes_key, xor_key=xor_key)
        self.assertEqual(fmt, "jpg")
        self.assertEqual(plain, MINI_JPEG + b"mid" + b"zz")


class ExtractImageTests(unittest.TestCase):
    def test_writes_file_with_sha256_and_format(self):
        xor_key = 0x99
        dat_bytes = bytes(b ^ xor_key for b in MINI_JPEG)
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "shot.dat"
            out_dir = Path(td) / "out"
            src.write_bytes(dat_bytes)
            result = extract_image(src, out_dir, aes_key=None, xor_key=xor_key)
            dest = Path(result["local_path"])
            self.assertTrue(dest.is_file())
            self.assertEqual(dest.suffix, ".jpg")
            self.assertEqual(result["format"], "jpg")
            self.assertEqual(dest.read_bytes(), MINI_JPEG)
            self.assertEqual(result["sha256"], hashlib.sha256(MINI_JPEG).hexdigest())


class ExtractAttachmentTests(unittest.TestCase):
    def _mem_store(self) -> sqlite3.Connection:
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(SCHEMA)
        con.execute("INSERT INTO chats(wxid,type,name) VALUES('g@chatroom','group','g')")
        con.execute(
            "INSERT INTO messages(chat_id,src_local_id,type) VALUES(1,1,'image')"
        )
        con.execute(
            "INSERT INTO attachments(message_id,kind,src_ref,status) "
            "VALUES(1,'image','deadbeef','pending')"
        )
        con.commit()
        return con

    def test_success_updates_status_and_path(self):
        xor_key = 0x11
        dat_bytes = bytes(b ^ xor_key for b in MINI_JPEG)
        con = self._mem_store()
        self.addCleanup(con.close)
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "a.dat"
            src.write_bytes(dat_bytes)
            out = extract_and_update_attachment(
                con, 1, src, Path(td) / "out", aes_key=None, xor_key=xor_key
            )
            row = con.execute("SELECT * FROM attachments WHERE id=1").fetchone()
            self.assertEqual(row["status"], "extracted")
            self.assertEqual(row["local_path"], out["local_path"])
            self.assertEqual(row["sha256"], hashlib.sha256(MINI_JPEG).hexdigest())
            self.assertIsNone(row["error"])

    def test_failure_marks_failed(self):
        con = self._mem_store()
        self.addCleanup(con.close)
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "bad.dat"
            src.write_bytes(b"\x00\x01\x02not-decryptable")
            extract_and_update_attachment(
                con, 1, src, Path(td) / "out", aes_key=None, xor_key=None
            )
            row = con.execute("SELECT * FROM attachments WHERE id=1").fetchone()
            self.assertEqual(row["status"], "failed")
            self.assertTrue(row["error"])


class ScanImageKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = _load_find_image_key_windows()

    def test_extracts_16_and_32_byte_alnum_candidates(self):
        k16 = b"cfcd208495d565ef"
        k32 = b"0123456789abcdefABCDEF9876543210"
        buf = b"\x00noise\x01" + k16 + b"\xff" + k32 + b"\x00end"
        found = self.mod.scan_buffer_for_image_keys(buf)
        self.assertIn(k16, found)
        self.assertIn(k32, found)

    def test_skips_short_alnum_and_dedups(self):
        k16 = b"AaBbCcDdEeFf0011"
        buf = b"shortKEY!!" + k16 + b"xx" + k16
        found = self.mod.scan_buffer_for_image_keys(buf)
        self.assertEqual(found.count(k16), 1)
        self.assertNotIn(b"shortKEY", found)


if __name__ == "__main__":
    unittest.main()
