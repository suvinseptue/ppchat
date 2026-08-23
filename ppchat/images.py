"""WeChat 4.x encrypted ``.dat`` image decode (XOR / AES-V1 / AES-V2).

Three on-disk variants. Magic bytes, header offsets, and key features below
are taken from community write-ups of WeChat 4.x (notably wechat-decrypt /
ylytdeng). This machine is macOS and cannot open a live WeChat image cache,
so every concrete constant is an assumption until a Windows 4.0 box confirms
it.

1. **XOR** (legacy, ~pre-2025-07): no magic. The whole file is XOR'd with a
   single byte. The key is usually recovered by XOR-ing the first bytes
   against known image magics (JPEG ``FF D8 FF``, PNG ``89 50 4E 47``, …),
   or by comparing two known images.

2. **AES-V1**: 6-byte magic ``07 08 56 31 08 07`` (``07 08 V1 08 07``).
   AES-128-ECB + optional raw middle + XOR tail. Community reports a fixed
   key ``cfcd208495d565ef`` (md5(``"0"``)[:16]).

3. **AES-V2**: 6-byte magic ``07 08 56 32 08 07`` (``07 08 V2 08 07``).
   Same layout as V1; the AES key is a 16-byte value from ``Weixin.exe``
   process memory (not the SQLCipher DB key).

V1/V2 layout assumed:

    [6B magic] [u32le aes_size] [u32le xor_size] [1B pad]   # 15-byte header
    [aligned_aes_size AES-128-ECB] [raw unencrypted] [xor_size XOR]

``aligned_aes_size`` is the PKCS7 ciphertext length of ``aes_size`` plaintext
bytes (round up to 16; if already aligned, add a full extra 16-byte pad
block). Cipher is AES-128-ECB.

# REAL-MACHINE-VERIFY: magic, offsets 6/10/14/15, PKCS7 alignment,
# AES-128-ECB, V1 fixed key, default XOR 0x88, JPEG-EOI XOR recovery,
# and attach-dir path rules were not confirmed against a real ``.dat``.
"""
from __future__ import annotations

import hashlib
import sqlite3
import struct
from pathlib import Path

from Crypto.Cipher import AES

# REAL-MACHINE-VERIFY: 6-byte signatures from community (wechat-decrypt).
# Most likely values: 07 08 'V1' 08 07 and 07 08 'V2' 08 07.
AES_V1_MAGIC = b"\x07\x08V1\x08\x07"
AES_V2_MAGIC = b"\x07\x08V2\x08\x07"

# REAL-MACHINE-VERIFY: V1 is reported to use this hardcoded 16-byte key.
V1_DEFAULT_AES_KEY = b"cfcd208495d565ef"

# REAL-MACHINE-VERIFY: community default for the V1/V2 XOR tail when the
# JPEG EOI trick cannot recover a key.
DEFAULT_XOR_KEY = 0x88

# Longer magics first so short ones (JPEG 3B) do not win a false match.
_IMAGE_MAGICS: tuple[tuple[str, bytes], ...] = (
    ("png", b"\x89PNG"),
    ("gif", b"GIF8"),
    ("tif", b"II*\x00"),
    ("webp", b"RIFF"),  # format() also checks WEBP at [8:12]
    ("jpg", b"\xff\xd8\xff"),
)


def detect_image_format(data: bytes) -> str:
    """Return a short format id from decrypted magic bytes."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"\x89PNG":
        return "png"
    if data[:3] == b"GIF":
        return "gif"
    if data[:2] == b"BM":
        return "bmp"
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return "webp"
    if data[:4] == b"II*\x00":
        return "tif"
    if data[:4] == b"wxgf":
        # REAL-MACHINE-VERIFY: WeChat HEVC/wxgf container after decrypt.
        return "hevc"
    return "bin"


def infer_xor_key(data: bytes) -> int | None:
    """Infer a 1-byte XOR key by matching the header to a known image magic.

    # REAL-MACHINE-VERIFY: this is the usual community method; a second
    # known image can also pin the key (plain1 ^ cipher1 == plain2 ^ cipher2).
    """
    if len(data) < 3:
        return None
    if data[:6] in (AES_V1_MAGIC, AES_V2_MAGIC):
        return None
    for _name, magic in _IMAGE_MAGICS:
        if len(data) < len(magic):
            continue
        key = data[0] ^ magic[0]
        if all((data[i] ^ key) == magic[i] for i in range(len(magic))):
            if magic == b"RIFF" and len(data) >= 12:
                dec8_12 = bytes(data[i] ^ key for i in range(8, 12))
                if dec8_12 != b"WEBP":
                    continue
            return key
    return None


def detect_variant(data: bytes) -> str:
    """Classify ``.dat`` bytes as ``xor`` / ``aes_v1`` / ``aes_v2`` / ``unknown``."""
    if data.startswith(AES_V1_MAGIC):
        return "aes_v1"
    if data.startswith(AES_V2_MAGIC):
        return "aes_v2"
    if infer_xor_key(data) is not None:
        return "xor"
    return "unknown"


def decode_xor(data: bytes, xor_key: int) -> bytes:
    k = xor_key & 0xFF
    return bytes(b ^ k for b in data)


def _aligned_aes_size(aes_size: int) -> int:
    # REAL-MACHINE-VERIFY: PKCS7 extra block when aes_size is already % 16 == 0.
    if aes_size % 16:
        return aes_size + (16 - aes_size % 16)
    return aes_size + 16


def _pkcs7_unpad(data: bytes) -> bytes:
    if not data:
        raise ValueError("empty AES plaintext")
    n = data[-1]
    if n < 1 or n > 16 or len(data) < n or data[-n:] != bytes([n] * n):
        raise ValueError("invalid PKCS7 padding")
    return data[:-n]


def _infer_tail_xor_key(xor_ct: bytes) -> int:
    """Recover the XOR tail key from a JPEG EOI (``FF D9``) when possible.

    # REAL-MACHINE-VERIFY: last-two-bytes ^ FF D9 is the usual thumbnail trick.
    """
    if len(xor_ct) >= 2:
        key = xor_ct[-2] ^ 0xFF
        if (xor_ct[-1] ^ 0xD9) == key:
            return key
    return DEFAULT_XOR_KEY


def _decode_aes(
    data: bytes,
    aes_key: bytes,
    xor_key: int | None,
    expect_magic: bytes,
) -> bytes:
    if len(data) < 15:
        raise ValueError("dat too short for AES-V1/V2 header")
    if data[:6] != expect_magic:
        raise ValueError(f"expected magic {expect_magic!r}, got {data[:6]!r}")
    if len(aes_key) < 16:
        raise ValueError("aes_key must be at least 16 bytes")

    aes_size, xor_size = struct.unpack_from("<II", data, 6)
    aligned = _aligned_aes_size(aes_size)
    offset = 15
    if xor_size < 0 or aligned < 16:
        raise ValueError("invalid aes_size / xor_size")
    if offset + aligned > len(data) or xor_size > len(data):
        raise ValueError("AES/XOR region overruns file")

    ct = data[offset : offset + aligned]
    plain_aes = _pkcs7_unpad(AES.new(aes_key[:16], AES.MODE_ECB).decrypt(ct))
    offset += aligned
    raw_end = len(data) - xor_size
    raw = data[offset:raw_end] if offset < raw_end else b""
    xor_ct = data[raw_end:]
    if xor_key is None:
        xor_key = _infer_tail_xor_key(xor_ct)
    dec_xor = bytes(b ^ (xor_key & 0xFF) for b in xor_ct)
    return plain_aes + raw + dec_xor


def decode_aes_v1(data: bytes, aes_key: bytes, xor_key: int | None = None) -> bytes:
    return _decode_aes(data, aes_key, xor_key, AES_V1_MAGIC)


def decode_aes_v2(data: bytes, aes_key: bytes, xor_key: int | None = None) -> bytes:
    return _decode_aes(data, aes_key, xor_key, AES_V2_MAGIC)


def decode_dat(
    data: bytes,
    *,
    aes_key: bytes | None,
    xor_key: int | None,
) -> tuple[bytes, str]:
    """Decode any known variant. Returns ``(plaintext, format)`` e.g. ``jpg``."""
    variant = detect_variant(data)
    if variant == "xor":
        key = xor_key if xor_key is not None else infer_xor_key(data)
        if key is None:
            raise ValueError("XOR key required and could not be inferred")
        plain = decode_xor(data, key)
    elif variant == "aes_v1":
        key = aes_key if aes_key is not None else V1_DEFAULT_AES_KEY
        plain = decode_aes_v1(data, key, xor_key=xor_key)
    elif variant == "aes_v2":
        if aes_key is None:
            raise ValueError("aes_key required for AES-V2")
        plain = decode_aes_v2(data, aes_key, xor_key=xor_key)
    else:
        raise ValueError("unknown .dat variant")
    return plain, detect_image_format(plain)


def extract_image(
    src_dat: Path,
    out_dir: Path,
    *,
    aes_key: bytes | None,
    xor_key: int | None,
    sha256: bool = True,
) -> dict:
    """Decode ``src_dat`` and write ``<stem>.<fmt>`` under ``out_dir``."""
    plain, fmt = decode_dat(Path(src_dat).read_bytes(), aes_key=aes_key, xor_key=xor_key)
    stem = Path(src_dat).stem
    for suffix in ("_t", "_h"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    dest_dir = Path(out_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{stem}.{fmt}"
    dest.write_bytes(plain)
    digest = hashlib.sha256(plain).hexdigest() if sha256 else None
    return {"local_path": str(dest), "sha256": digest, "format": fmt}


def extract_and_update_attachment(
    con: sqlite3.Connection,
    attachment_id: int,
    src_dat: Path,
    out_dir: Path,
    *,
    aes_key: bytes | None = None,
    xor_key: int | None = None,
) -> dict:
    """Decrypt one attachments row and write ``local_path`` / ``status``.

    Uses the existing attachments columns only (does not change store.py).
    ``src_dat`` must already be resolved — mapping ``src_ref`` (md5) to a
    ``msg/attach/...`` path is platform-specific.

    # REAL-MACHINE-VERIFY: Windows attach path is expected to look like
    # ``xwechat_files/<account>/msg/attach/<md5(username)>/<YYYY-MM>/Img/<md5>.dat``
    # (optional ``_t`` / ``_h`` suffix). Not confirmed on this machine.
    """
    try:
        result = extract_image(src_dat, out_dir, aes_key=aes_key, xor_key=xor_key)
        con.execute(
            "UPDATE attachments SET local_path=?, sha256=?, status='extracted', error=NULL "
            "WHERE id=?",
            (result["local_path"], result["sha256"], attachment_id),
        )
        return result
    except Exception as exc:
        con.execute(
            "UPDATE attachments SET status='failed', error=? WHERE id=?",
            (str(exc), attachment_id),
        )
        return {"local_path": None, "sha256": None, "format": None, "error": str(exc)}
