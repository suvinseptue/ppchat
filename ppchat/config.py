"""Path discovery and constants for ppchat (WeChat 4.x on macOS)."""
from __future__ import annotations

import glob
from pathlib import Path

WECHAT_APP = Path("/Applications/WeChat.app")
WECHAT_BIN = WECHAT_APP / "Contents/MacOS/WeChat"
TENCENT_TEAM_ID = "5A4RE8SF68"

CONTAINER = Path.home() / (
    "Library/Containers/com.tencent.xinWeChat/Data/Documents/xwechat_files"
)

# ppchat working dir (key cache, normalized store)
PPCHAT_HOME = Path.home() / ".ppchat"
KEYS_JSON = PPCHAT_HOME / "keys.json"
STORE_DB = PPCHAT_HOME / "ppchat.db"

# generated application artifacts (bundles, summaries, requirements)
REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "out"

# Temporary ad-hoc copy used only for key capture. Never sign WECHAT_APP.
EXTRACT_ROOT = PPCHAT_HOME / "extract"
EXTRACT_APP = EXTRACT_ROOT / "WeChat.app"
EXTRACT_BIN = EXTRACT_APP / "Contents/MacOS/WeChat"


def account_dirs() -> list[Path]:
    """Return per-account data dirs (dirs that contain a db_storage folder)."""
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
        msg = d / "db_storage" / "message" / "message_0.db"
        return msg.stat().st_mtime if msg.exists() else 0.0
    return max(dirs, key=score)


def db_files(account_dir: Path | None = None) -> list[Path]:
    account_dir = account_dir or default_account_dir()
    root = account_dir / "db_storage"
    return sorted(Path(p) for p in glob.glob(str(root / "**" / "*.db"), recursive=True))
