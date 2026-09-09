"""Keep daily WeChat officially signed; only an extract copy may be ad-hoc signed."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import config

TENCENT_TEAM_ID = config.TENCENT_TEAM_ID


class AdhocOfficialError(RuntimeError):
    """/Applications/WeChat.app is not Tencent Developer ID signed."""


class SignTargetError(ValueError):
    """Attempted to codesign something other than the extract copy."""


@dataclass(frozen=True)
class CodeSignInfo:
    identifier: str
    flags: str
    signature: str
    team_id: str
    authorities: tuple[str, ...]

    @property
    def is_adhoc(self) -> bool:
        flags = self.flags.lower()
        return (
            "adhoc" in flags
            or self.signature.lower() == "adhoc"
            or self.team_id in ("", "not set")
        )

    @property
    def is_tencent_official(self) -> bool:
        return self.team_id == TENCENT_TEAM_ID and not self.is_adhoc


def parse_codesign_info(text: str) -> CodeSignInfo:
    identifier = ""
    flags = ""
    signature = ""
    team_id = ""
    authorities: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("Identifier="):
            identifier = line.split("=", 1)[1]
        elif line.startswith("CodeDirectory"):
            if "flags=" in line:
                flags = line.split("flags=", 1)[1].split()[0]
        elif line.startswith("Signature="):
            signature = line.split("=", 1)[1]
        elif line.startswith("TeamIdentifier="):
            team_id = line.split("=", 1)[1]
            if team_id == "not set":
                team_id = ""
        elif line.startswith("Authority="):
            authorities.append(line.split("=", 1)[1])
    return CodeSignInfo(
        identifier=identifier,
        flags=flags,
        signature=signature,
        team_id=team_id,
        authorities=tuple(authorities),
    )


def inspect_app(app: Path) -> CodeSignInfo:
    proc = subprocess.run(
        ["codesign", "-dv", "--verbose=2", str(app)],
        check=False,
        capture_output=True,
        text=True,
    )
    text = (proc.stderr or "") + "\n" + (proc.stdout or "")
    if proc.returncode != 0 and "code object is not signed" not in text:
        raise RuntimeError(f"codesign inspect failed for {app}: {text.strip()}")
    return parse_codesign_info(text)


def assert_official_is_tencent(info: CodeSignInfo) -> None:
    if info.is_tencent_official:
        return
    raise AdhocOfficialError(
        "/Applications/WeChat.app is not Tencent-signed. "
        "Reinstall official WeChat from https://mac.weixin.qq.com/ "
        "before making an extract copy. Do not codesign the official app."
    )


def assert_sign_target_is_extract_copy(app: Path) -> None:
    target = app.expanduser().resolve()
    official = config.WECHAT_APP.expanduser().resolve()
    extract = config.EXTRACT_APP.expanduser().resolve()
    home = config.PPCHAT_HOME.expanduser().resolve()
    if target == official:
        raise SignTargetError("refusing to codesign /Applications/WeChat.app")
    try:
        target.relative_to(home)
    except ValueError as exc:
        raise SignTargetError(
            f"sign target must be under {home}, got {target}"
        ) from exc
    if target != extract:
        raise SignTargetError(
            f"sign target must be the extract copy {extract}, got {target}"
        )


def extract_bin_for_attach() -> Path:
    """Binary lldb/scanner must attach to. Never the official app."""
    if not config.EXTRACT_BIN.is_file():
        raise FileNotFoundError(
            f"extract copy not found at {config.EXTRACT_APP}. "
            "Run: bash tools/get_keys.sh prepare"
        )
    return config.EXTRACT_BIN


def _fmt_info(label: str, app: Path, info: CodeSignInfo | None, missing: bool) -> str:
    if missing:
        return f"{label}: MISSING ({app})"
    assert info is not None
    kind = "Tencent official" if info.is_tencent_official else (
        "ad-hoc" if info.is_adhoc else f"other team={info.team_id or '?'}"
    )
    return f"{label}: {kind}  flags={info.flags or '-'}  team={info.team_id or 'not set'}"


def status_text() -> str:
    lines = []
    official = config.WECHAT_APP
    if official.exists():
        info = inspect_app(official)
        lines.append(_fmt_info("official", official, info, False))
        if not info.is_tencent_official:
            lines.append(
                "  -> reinstall from https://mac.weixin.qq.com/ before prepare"
            )
    else:
        lines.append(_fmt_info("official", official, None, True))

    extract = config.EXTRACT_APP
    if extract.exists():
        info = inspect_app(extract)
        lines.append(_fmt_info("extract copy", extract, info, False))
        if not info.is_adhoc:
            lines.append("  -> copy exists but is not ad-hoc; re-run prepare")
    else:
        lines.append(_fmt_info("extract copy", extract, None, True))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "status"
    try:
        if cmd == "status":
            print(status_text())
            official = inspect_app(config.WECHAT_APP) if config.WECHAT_APP.exists() else None
            return 0 if official and official.is_tencent_official else 2
        if cmd == "check-prepare":
            assert_official_is_tencent(inspect_app(config.WECHAT_APP))
            print("[✓] official WeChat is Tencent-signed; safe to copy")
            return 0
        if cmd == "check-sign-target":
            if len(args) < 2:
                print("usage: python -m ppchat.wechat_app check-sign-target <app>")
                return 2
            assert_sign_target_is_extract_copy(Path(args[1]))
            print(f"[✓] sign target ok: {args[1]}")
            return 0
        if cmd == "extract-bin":
            print(extract_bin_for_attach())
            return 0
    except (AdhocOfficialError, SignTargetError, FileNotFoundError, RuntimeError) as exc:
        print(f"[!] {exc}")
        return 1
    print(
        "usage: python -m ppchat.wechat_app "
        "status|check-prepare|check-sign-target <app>|extract-bin"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
