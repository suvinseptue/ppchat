"""Path discovery and constants for ppchat (WeChat 4.x)."""
from __future__ import annotations

import glob
import json
import os
import sys
from pathlib import Path

# ppchat working dir (key cache, normalized store) — platform-independent
PPCHAT_HOME = Path.home() / ".ppchat"
KEYS_JSON = PPCHAT_HOME / "keys.json"
STORE_DB = PPCHAT_HOME / "ppchat.db"
CANDIDATES_WINDOWS_JSON = PPCHAT_HOME / "candidates_windows.json"

# generated application artifacts (bundles, summaries, requirements)
REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "out"


def _macos_profile() -> dict:
    wechat_app = Path("/Applications/WeChat.app")
    extract_root = PPCHAT_HOME / "extract"
    extract_app = extract_root / "WeChat.app"
    return {
        "WECHAT_APP": wechat_app,
        "WECHAT_BIN": wechat_app / "Contents/MacOS/WeChat",
        "TENCENT_TEAM_ID": "5A4RE8SF68",
        "CONTAINER": Path.home()
        / "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files",
        "EXTRACT_ROOT": extract_root,
        "EXTRACT_APP": extract_app,
        "EXTRACT_BIN": extract_app / "Contents/MacOS/WeChat",
    }


def _read_db_root_from_config() -> Path | None:
    cfg_path = PPCHAT_HOME / "config.json"
    if not cfg_path.exists():
        return None
    try:
        data = json.loads(cfg_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    raw = data.get("db_root") if isinstance(data, dict) else None
    if not raw:
        return None
    return Path(str(raw)).expanduser()


def _read_filesave_path_from_registry() -> Path | None:
    # REAL-MACHINE-VERIFY: HKCU\Software\Tencent\WeChat FileSavePath (and any
    # 4.0-specific key) — name/location may differ on WeChat 4.0 vs 3.x.
    try:
        import winreg
    except ImportError:
        return None
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Tencent\WeChat")
    except OSError:
        return None
    try:
        val, _ = winreg.QueryValueEx(key, "FileSavePath")
    except OSError:
        return None
    finally:
        winreg.CloseKey(key)
    if not val:
        return None
    return Path(str(val))


def _normalize_windows_container(path: Path) -> Path:
    # REAL-MACHINE-VERIFY: FileSavePath may already be xwechat_files, or its parent.
    if path.name.lower() == "xwechat_files":
        return path
    return path / "xwechat_files"


def _default_windows_xwechat_files() -> Path:
    root = os.environ.get("USERPROFILE")
    base = Path(root) if root else Path.home()
    return base / "Documents" / "xwechat_files"


def _discover_windows_db_root() -> Path:
    """Resolve CONTAINER: config.json db_root → registry → Documents\\xwechat_files."""
    explicit = _read_db_root_from_config()
    if explicit:
        return explicit
    reg = _read_filesave_path_from_registry()
    if reg:
        return _normalize_windows_container(reg)
    return _default_windows_xwechat_files()


def _windows_profile() -> dict:
    return {
        "CONTAINER": _discover_windows_db_root(),
        "WECHAT_APP": None,
        "WECHAT_BIN": None,
        "TENCENT_TEAM_ID": None,
        "EXTRACT_ROOT": None,
        "EXTRACT_APP": None,
        "EXTRACT_BIN": None,
    }


def _select_profile() -> dict:
    if sys.platform == "darwin":
        return _macos_profile()
    if sys.platform.startswith("win"):
        return _windows_profile()
    return _macos_profile()


_profile = _select_profile()
WECHAT_APP = _profile.get("WECHAT_APP")
WECHAT_BIN = _profile.get("WECHAT_BIN")
TENCENT_TEAM_ID = _profile.get("TENCENT_TEAM_ID")
CONTAINER = _profile["CONTAINER"]
EXTRACT_ROOT = _profile.get("EXTRACT_ROOT")
EXTRACT_APP = _profile.get("EXTRACT_APP")
EXTRACT_BIN = _profile.get("EXTRACT_BIN")


def bootstrap_home(
    home: Path | None = None, db_root: Path | str | None = None
) -> dict:
    """Create ~/.ppchat (or ``home``) and write config.json once if db_root is set.

    Does not overwrite an existing config.json. Keys / store files are only
    path handles here — they are created later by key capture and ingest.
    """
    dest = Path(home) if home is not None else PPCHAT_HOME
    dest.mkdir(parents=True, exist_ok=True)
    cfg = dest / "config.json"
    root = Path(db_root) if db_root is not None else None
    if root is not None and not cfg.exists():
        cfg.write_text(json.dumps({"db_root": str(root)}, indent=2) + "\n", encoding="utf-8")
    return {
        "home": dest,
        "config": cfg,
        "keys_json": dest / "keys.json",
        "store_db": dest / "ppchat.db",
        "candidates_windows": dest / "candidates_windows.json",
        "db_root": root,
    }


def account_dirs() -> list[Path]:
    """Return per-account data dirs (dirs that contain a db_storage folder)."""
    # REAL-MACHINE-VERIFY: Windows 4.0 is CONTAINER/<account>/db_storage (same as mac).
    out = []
    if not CONTAINER.exists():
        return out
    for child in CONTAINER.iterdir():
        if child.is_dir() and (child / "db_storage").is_dir():
            out.append(child)
    return out


def default_account_dir() -> Path:
    dirs = account_dirs()
    if not dirs:
        raise FileNotFoundError(f"No WeChat account dir with db_storage under {CONTAINER}")
    # pick the one whose message db was modified most recently
    def score(d: Path) -> float:
        # REAL-MACHINE-VERIFY: Windows 4.0 also uses db_storage/message/message_0.db
        msg = d / "db_storage" / "message" / "message_0.db"
        return msg.stat().st_mtime if msg.exists() else 0.0
    return max(dirs, key=score)


def db_files(account_dir: Path | None = None) -> list[Path]:
    account_dir = account_dir or default_account_dir()
    root = account_dir / "db_storage"
    return sorted(Path(p) for p in glob.glob(str(root / "**" / "*.db"), recursive=True))
