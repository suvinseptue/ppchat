"""Pure-Python SQLCipher 4 decryption for WeChat 4.x (WCDB) databases.

WeChat 4.x stores each DB as SQLCipher 4:
  - page size      : 4096
  - cipher         : AES-256-CBC
  - HMAC           : HMAC-SHA512 (64 bytes / page)  -> page reserve = IV(16)+HMAC(64) = 80
  - key derivation : we already have the RAW 32-byte key from process memory,
                     so we skip the 256000-round PBKDF2 that maps passphrase->key.
  - hmac key       : PBKDF2-HMAC-SHA512(raw_key, salt XOR 0x3a, iters=2, dklen=32)

WCDB caches the key in memory as the literal string  x'<64hex key><32hex salt>'.
The 16-byte salt equals the first 16 bytes of the target .db file, which is how a
scanned candidate is bound to a specific database.

No native SQLCipher dependency: we decrypt page-by-page with pycryptodome and write
a plain SQLite file that stdlib sqlite3 can open.
"""
from __future__ import annotations

import hashlib
import hmac
import struct
from pathlib import Path

from Crypto.Cipher import AES

PAGE_SIZE = 4096
HMAC_SIZE = 64          # HMAC-SHA512
IV_SIZE = 16
RESERVE = IV_SIZE + HMAC_SIZE          # 80
KEY_SIZE = 32
SALT_SIZE = 16
FAST_KDF_ITER = 2
SQLITE_HEADER = b"SQLite format 3\x00"  # 16 bytes


def _pbkdf2_sha512(password: bytes, salt: bytes, iters: int, dklen: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", password, salt, iters, dklen)


def derive_hmac_key(enc_key: bytes, salt: bytes) -> bytes:
    mac_salt = bytes(b ^ 0x3A for b in salt)
    return _pbkdf2_sha512(enc_key, mac_salt, FAST_KDF_ITER, KEY_SIZE)


def _split_candidate(candidate_hex: str) -> tuple[bytes, bytes] | None:
    """candidate_hex is 96 hex chars: 64 (key) + 32 (salt). Returns (key, salt)."""
    if len(candidate_hex) != 96:
        return None
    try:
        raw = bytes.fromhex(candidate_hex)
    except ValueError:
        return None
    return raw[:KEY_SIZE], raw[KEY_SIZE:KEY_SIZE + SALT_SIZE]


def db_salt(db_path: Path) -> bytes:
    with open(db_path, "rb") as f:
        return f.read(SALT_SIZE)


def verify_key(db_path: Path, enc_key: bytes, salt: bytes) -> bool:
    """Verify enc_key/salt against page 1 of db_path via SQLCipher4 page HMAC."""
    with open(db_path, "rb") as f:
        page1 = f.read(PAGE_SIZE)
    if len(page1) < PAGE_SIZE:
        return False
    if page1[:SALT_SIZE] != salt:
        return False
    mac_key = derive_hmac_key(enc_key, salt)
    # page 1: [salt(16)][ciphertext][iv(16)][hmac(64)]
    body = page1[SALT_SIZE:]                     # everything after salt
    payload_len = PAGE_SIZE - SALT_SIZE - RESERVE
    ciphertext = body[:payload_len]
    iv = body[payload_len:payload_len + IV_SIZE]
    stored_hmac = body[payload_len + IV_SIZE:payload_len + IV_SIZE + HMAC_SIZE]
    msg = ciphertext + iv + struct.pack("<I", 1)  # page number starts at 1
    calc = hmac.new(mac_key, msg, hashlib.sha512).digest()
    return hmac.compare_digest(calc, stored_hmac)


def find_key_for_db(db_path: Path, candidates_hex: list[str]) -> str | None:
    """Return the 96-hex candidate that decrypts db_path, or None."""
    salt = db_salt(db_path)
    for cand in candidates_hex:
        split = _split_candidate(cand)
        if not split:
            continue
        enc_key, cand_salt = split
        if cand_salt != salt:
            continue
        if verify_key(db_path, enc_key, salt):
            return cand
    return None


def decrypt_db(db_path: Path, candidate_hex: str, out_path: Path) -> None:
    """Decrypt an entire SQLCipher4 db to a plain SQLite file at out_path."""
    split = _split_candidate(candidate_hex)
    if not split:
        raise ValueError("candidate must be 96 hex chars")
    enc_key, salt = split
    if db_salt(db_path) != salt:
        raise ValueError("salt mismatch: candidate does not belong to this db")
    mac_key = derive_hmac_key(enc_key, salt)

    data = db_path.read_bytes()
    n_pages = len(data) // PAGE_SIZE
    out = bytearray()

    for i in range(n_pages):
        page = data[i * PAGE_SIZE:(i + 1) * PAGE_SIZE]
        pgno = i + 1
        if i == 0:
            body = page[SALT_SIZE:]
            payload_len = PAGE_SIZE - SALT_SIZE - RESERVE
        else:
            body = page
            payload_len = PAGE_SIZE - RESERVE
        ciphertext = body[:payload_len]
        iv = body[payload_len:payload_len + IV_SIZE]
        stored_hmac = body[payload_len + IV_SIZE:payload_len + IV_SIZE + HMAC_SIZE]

        msg = ciphertext + iv + struct.pack("<I", pgno)
        calc = hmac.new(mac_key, msg, hashlib.sha512).digest()
        if not hmac.compare_digest(calc, stored_hmac):
            raise ValueError(f"HMAC verification failed on page {pgno}")

        plain = AES.new(enc_key, AES.MODE_CBC, iv).decrypt(ciphertext)
        if i == 0:
            out += SQLITE_HEADER + plain + b"\x00" * RESERVE
        else:
            out += plain + b"\x00" * RESERVE

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(bytes(out))
