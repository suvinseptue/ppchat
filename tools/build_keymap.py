#!/usr/bin/env python3
"""Verify scanned candidates and build the {db_path: key_hex} map.

Usage:
    ./.venv/bin/python tools/build_keymap.py ~/.ppchat/candidates.json
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ppchat import config
from ppchat.keys import build_key_map, load_all_candidates, save_key_map


def main() -> int:
    # default: merge memory-scan candidates + lldb kdf capture
    paths = [Path(p) for p in sys.argv[1:]] or [
        config.PPCHAT_HOME / "candidates.json",
        config.PPCHAT_HOME / "kdf_candidates.json",
    ]
    candidates = load_all_candidates(paths)
    print(f"[*] loaded: {len(candidates['literals'])} literal(s), "
          f"{len(candidates['windows'])} window(s), "
          f"{len(candidates['keys'])} raw key(s), "
          f"{len(candidates['pairs'])} pair(s)")
    all_dbs = config.db_files()
    key_map = build_key_map(candidates)
    print(f"[*] matched {len(key_map)}/{len(all_dbs)} databases:")
    for db in all_dbs:
        status = "OK " if str(db) in key_map else "-- "
        print(f"    [{status}] {Path(db).relative_to(config.CONTAINER)}")
    save_key_map(key_map)
    print(f"[*] key map saved to {config.KEYS_JSON}")
    return 0 if key_map else 1


if __name__ == "__main__":
    raise SystemExit(main())
