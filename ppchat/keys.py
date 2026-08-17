"""Build and cache a verified {db_path: key_hex} map from scanned candidates.

The scanner (tools/find_keys_macos) emits JSON:
    {"windows": [{"salt": "<32hex>", "ctx": "<hex bytes around the salt>"}, ...],
     "literals": ["<96hex>", ...]}

- literals are tried directly (older x'<64hex><32hex>' format).
- windows are format-independent: for the db owning that salt, we slide a 32-byte
  candidate enc_key across the surrounding bytes and HMAC-verify each offset.

Verified keys are stored as 96-hex (enc_key(64) + salt(32)) so ppchat.decrypt can
consume them uniformly.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config
from .decrypt import KEY_SIZE, find_key_for_db, verify_key


def load_candidates(candidates_path: Path) -> dict:
    raw = json.loads(Path(candidates_path).read_text())
    if isinstance(raw, list):
        # legacy: plain list of 96-hex literals
        return {"windows": [], "literals": [str(x).strip() for x in raw if str(x).strip()],
                "keys": [], "pairs": []}
    if not isinstance(raw, dict):
        raise ValueError("candidates file must be a JSON object or array")
    return {
        "windows": raw.get("windows", []),
        "literals": raw.get("literals", []),
        "keys": [str(k).strip() for k in raw.get("keys", []) if str(k).strip()],
        "pairs": raw.get("pairs", []),
    }


def load_all_candidates(paths: list[Path]) -> dict:
    """Merge candidate sources (e.g. memory-scan + lldb capture)."""
    merged = {"windows": [], "literals": [], "keys": [], "pairs": []}
    for p in paths:
        p = Path(p).expanduser()
        if not p.exists():
            continue
        c = load_candidates(p)
        for k in merged:
            merged[k].extend(c.get(k, []))
    return merged


def _key_from_windows(db: Path, salt: bytes, windows: list[dict]) -> str | None:
    """Slide a 32-byte enc_key across each ctx window for this db's salt."""
    salt_hex = salt.hex()
    for w in windows:
        if w.get("salt", "").lower() != salt_hex:
            continue
        try:
            ctx = bytes.fromhex(w.get("ctx", ""))
        except ValueError:
            continue
        for off in range(0, max(0, len(ctx) - KEY_SIZE) + 1):
            enc_key = ctx[off:off + KEY_SIZE]
            if verify_key(db, enc_key, salt):
                return enc_key.hex() + salt_hex
    return None


def _key_from_raw_keys(db: Path, salt: bytes, keys_hex: list[str]) -> str | None:
    """Try each captured raw 32-byte enc key against this db's salt."""
    salt_hex = salt.hex()
    for kh in keys_hex:
        kh = kh.strip().lower()
        if len(kh) != KEY_SIZE * 2:
            continue
        try:
            enc_key = bytes.fromhex(kh)
        except ValueError:
            continue
        if verify_key(db, enc_key, salt):
            return kh + salt_hex
    return None


def build_key_map(candidates: dict, account_dir: Path | None = None) -> dict[str, str]:
    """Match each encrypted db to a verified key.

    Order: direct key+salt pairs -> raw keys -> x'..' literals -> salt windows.
    """
    literals = candidates.get("literals", [])
    windows = candidates.get("windows", [])
    keys_hex = candidates.get("keys", [])
    pairs = candidates.get("pairs", [])
    # pairs give a fast path: salt_hex -> key_hex
    pair_by_salt: dict[str, str] = {s.lower(): k.lower() for k, s in pairs}

    key_map: dict[str, str] = {}
    for db in config.db_files(account_dir):
        salt = db.read_bytes()[:16] if db.stat().st_size >= 16 else b""
        salt_hex = salt.hex()
        cand = None
        if salt_hex in pair_by_salt:
            k = pair_by_salt[salt_hex]
            if len(k) == KEY_SIZE * 2 and verify_key(db, bytes.fromhex(k), salt):
                cand = k + salt_hex
        if not cand and keys_hex:
            cand = _key_from_raw_keys(db, salt, keys_hex)
        if not cand:
            cand = find_key_for_db(db, literals)
        if not cand and salt:
            cand = _key_from_windows(db, salt, windows)
        if cand:
            key_map[str(db)] = cand
    return key_map


def save_key_map(key_map: dict[str, str]) -> Path:
    config.PPCHAT_HOME.mkdir(parents=True, exist_ok=True)
    config.KEYS_JSON.write_text(json.dumps(key_map, indent=2, ensure_ascii=False))
    config.KEYS_JSON.chmod(0o600)
    return config.KEYS_JSON


def load_key_map() -> dict[str, str]:
    if not config.KEYS_JSON.exists():
        return {}
    return json.loads(config.KEYS_JSON.read_text())
