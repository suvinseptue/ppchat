#!/usr/bin/env python3
"""Scan Weixin.exe memory for WeChat 4.x SQLCipher key material (Windows).

Two complementary methods in one pass (same as ``find_keys_macos.c``):

  (A) literal scan : ``x'<64hex enc_key><32hex salt>'``
  (B) salt-anchored: each DB's first 16 bytes (file salt) is searched in
      memory; a ±2 KiB window around each hit is dumped. The enc key lives
      in the same WCDB codec struct, so ``keys.build_key_map`` can slide a
      32-byte candidate across the window and HMAC-verify.

Usage (Administrator, Weixin.exe logged in)::

    python tools/find_keys_windows.py
    python tools/build_keymap.py ~/.ppchat/candidates_windows.json

Requirements:
    - Windows + WeChat 4.x (process name Weixin.exe, not WeChat.exe / 3.x)
    - Run the Python process as Administrator (PROCESS_VM_READ)
    - Weixin.exe must be running and logged in
    - ``db_root`` must point at ``xwechat_files`` so salts can be loaded

Output: ``~/.ppchat/candidates_windows.json``
    ``{"windows":[{"salt","ctx"},...], "literals":[...], "keys":[], "pairs":[]}``

No third-party deps. ctypes/kernel32 stay inside ``main()`` /
``_iter_process_memory()`` so this module imports on macOS for unit tests.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# x'<64–192 hex>' — primary form is 96 hex; loose range matches nearby variants.
# 4.1+ often drops the plaintext literal; salt-anchored windows are the fallback.
_LIT_RE = re.compile(rb"x'([0-9a-fA-F]{64,192})'")
WINDOW = 2048
SALT_LEN = 16
OVERLAP = 2 * WINDOW

PROCESS_NAME = "Weixin.exe"
CANDIDATES_NAME = "candidates_windows.json"


def scan_buffer_for_literals(buf: bytes) -> list[str]:
    """Return unique lowercase hex literals found in ``buf`` (order preserved).

    Accepts even-length hex in [64, 192]; shorter/longer ``x'...'`` wrappers
    are ignored (the regex already rejects <64; >192 fails the closing quote).
    """
    seen: set[str] = set()
    out: list[str] = []
    for m in _LIT_RE.finditer(buf):
        hex_s = m.group(1).decode("ascii").lower()
        if len(hex_s) % 2:
            continue
        if hex_s not in seen:
            seen.add(hex_s)
            out.append(hex_s)
    return out


def scan_buffer_for_windows(buf: bytes, salts: list[bytes]) -> list[dict]:
    """Return ``{salt, ctx}`` hex dicts for each salt hit in ``buf``.

    ``ctx`` is the clipped ±WINDOW bytes around the 16-byte salt, matching
    ``find_keys_macos.c``. Identical ``(salt, ctx)`` pairs are dropped.
    """
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for salt in salts:
        if len(salt) != SALT_LEN:
            continue
        start = 0
        while True:
            pos = buf.find(salt, start)
            if pos < 0:
                break
            lo = pos - WINDOW if pos > WINDOW else 0
            hi = pos + SALT_LEN + WINDOW
            if hi > len(buf):
                hi = len(buf)
            rec = {"salt": salt.hex(), "ctx": buf[lo:hi].hex()}
            key = (rec["salt"], rec["ctx"])
            if key not in seen:
                seen.add(key)
                out.append(rec)
            start = pos + SALT_LEN
    return out


def salts_from_db_files(paths: list[Path]) -> list[bytes]:
    """First 16 bytes of each ``.db``, unique, skip short/unreadable files."""
    seen: set[bytes] = set()
    out: list[bytes] = []
    for path in paths:
        try:
            blob = Path(path).read_bytes()[:SALT_LEN]
        except OSError:
            continue
        if len(blob) != SALT_LEN or blob in seen:
            continue
        seen.add(blob)
        out.append(blob)
    return out


def _load_account_salts() -> list[bytes]:
    try:
        from ppchat import config

        return salts_from_db_files(config.db_files())
    except (ImportError, FileNotFoundError, OSError):
        return []


def _candidates_output_path() -> Path:
    try:
        from ppchat import config

        return config.CANDIDATES_WINDOWS_JSON
    except ImportError:
        return Path.home() / ".ppchat" / CANDIDATES_NAME


def write_candidates(
    literals: list[str],
    path: Path | None = None,
    windows: list[dict] | None = None,
) -> Path:
    dest = path or _candidates_output_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(
        json.dumps(
            {
                "windows": windows or [],
                "literals": literals,
                "keys": [],
                "pairs": [],
            },
            indent=2,
        )
        + "\n"
    )
    return dest


def _find_weixin_pid() -> int:
    """Return the Weixin.exe pid with the largest working set (multi-instance).

    # REAL-MACHINE-VERIFY: process name is Weixin.exe on WeChat 4.0 (not WeChat.exe).
    """
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    TH32CS_SNAPPROCESS = 0x00000002
    INVALID = wintypes.HANDLE(-1).value

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    # REAL-MACHINE-VERIFY: PROCESSENTRY32W / PROCESS_MEMORY_COUNTERS layout on x64.
    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    k32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    k32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap == INVALID:
        raise RuntimeError("CreateToolhelp32Snapshot failed")

    pids: list[int] = []
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        if not k32.Process32FirstW(snap, ctypes.byref(entry)):
            raise RuntimeError("Process32FirstW failed")
        while True:
            if entry.szExeFile == PROCESS_NAME:
                pids.append(int(entry.th32ProcessID))
            if not k32.Process32NextW(snap, ctypes.byref(entry)):
                break
    finally:
        k32.CloseHandle(snap)

    if not pids:
        raise RuntimeError(
            f"{PROCESS_NAME} not found; start WeChat 4.0 and log in, then retry"
        )
    if len(pids) == 1:
        return pids[0]

    PROCESS_QUERY = 0x0400
    PROCESS_VM_READ = 0x0010
    try:
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
    except OSError:
        return pids[0]

    best_pid = pids[0]
    best_ws = -1
    for pid in pids:
        handle = k32.OpenProcess(PROCESS_QUERY | PROCESS_VM_READ, False, pid)
        if not handle:
            continue
        try:
            pmc = PROCESS_MEMORY_COUNTERS()
            pmc.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
            if psapi.GetProcessMemoryInfo(handle, ctypes.byref(pmc), pmc.cb):
                ws = int(pmc.WorkingSetSize)
                if ws > best_ws:
                    best_ws = ws
                    best_pid = pid
        finally:
            k32.CloseHandle(handle)
    return best_pid


def _iter_process_memory(pid: int):
    """Yield readable committed bytes from ``pid`` via VirtualQueryEx.

    # REAL-MACHINE-VERIFY: MEMORY_BASIC_INFORMATION layout, readable Protect
    # mask, and whether PAGE_GUARD regions must be skipped on this Windows build.
    """
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    PROCESS_VM_READ = 0x0010
    PROCESS_QUERY_INFORMATION = 0x0400
    MEM_COMMIT = 0x1000
    PAGE_GUARD = 0x100
    READABLE = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}
    CHUNK = 8 * 1024 * 1024
    ADDR_END = 0x7FFFFFFFFFFF

    class MEMORY_BASIC_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BaseAddress", ctypes.c_void_p),
            ("AllocationBase", ctypes.c_void_p),
            ("AllocationProtect", wintypes.DWORD),
            ("RegionSize", ctypes.c_size_t),
            ("State", wintypes.DWORD),
            ("Protect", wintypes.DWORD),
            ("Type", wintypes.DWORD),
        ]

    handle = k32.OpenProcess(PROCESS_VM_READ | PROCESS_QUERY_INFORMATION, False, pid)
    if not handle:
        raise RuntimeError(
            "OpenProcess failed; run as Administrator with Weixin.exe logged in"
        )

    ReadProcessMemory = k32.ReadProcessMemory
    ReadProcessMemory.argtypes = [
        wintypes.HANDLE,
        wintypes.LPCVOID,
        wintypes.LPVOID,
        ctypes.c_size_t,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    ReadProcessMemory.restype = wintypes.BOOL

    try:
        addr = 0
        mbi = MEMORY_BASIC_INFORMATION()
        while addr < ADDR_END:
            got = k32.VirtualQueryEx(
                handle, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)
            )
            if not got:
                break
            base = mbi.BaseAddress or 0
            size = mbi.RegionSize or 0
            prot = int(mbi.Protect)
            readable = (
                mbi.State == MEM_COMMIT
                and not (prot & PAGE_GUARD)
                and (prot & 0xFF) in READABLE
            )
            if readable and size:
                offset = 0
                while offset < size:
                    n = min(CHUNK, size - offset)
                    buf = (ctypes.c_char * n)()
                    read = ctypes.c_size_t(0)
                    ok = ReadProcessMemory(
                        handle,
                        ctypes.c_void_p(base + offset),
                        buf,
                        n,
                        ctypes.byref(read),
                    )
                    got = int(read.value) if ok else 0
                    if got:
                        yield bytes(buf[:got])
                    # Keep 2*WINDOW overlap so a salt on an 8 MiB boundary
                    # still has a full ctx in some chunk (same as find_keys_macos).
                    if got > OVERLAP:
                        offset += got - OVERLAP
                    elif got:
                        offset += got
                    else:
                        offset += n
            nxt = base + size
            if nxt <= addr:
                break
            addr = nxt
    finally:
        k32.CloseHandle(handle)


def main() -> int:
    if not sys.platform.startswith("win"):
        print("This scanner only runs on Windows (Weixin.exe).", file=sys.stderr)
        return 2
    pid = _find_weixin_pid()
    print(f"[*] scanning {PROCESS_NAME} pid={pid}", file=sys.stderr)
    salts = _load_account_salts()
    print(f"[*] loaded {len(salts)} salt(s) from db files", file=sys.stderr)
    seen_lit: set[str] = set()
    literals: list[str] = []
    seen_win: set[tuple[str, str]] = set()
    windows: list[dict] = []
    for chunk in _iter_process_memory(pid):
        for lit in scan_buffer_for_literals(chunk):
            if lit not in seen_lit:
                seen_lit.add(lit)
                literals.append(lit)
        for rec in scan_buffer_for_windows(chunk, salts):
            key = (rec["salt"], rec["ctx"])
            if key not in seen_win:
                seen_win.add(key)
                windows.append(rec)
    dest = write_candidates(literals, windows=windows)
    print(
        f"[*] {len(literals)} literal(s), {len(windows)} window(s) -> {dest}",
        file=sys.stderr,
    )
    if not literals and not windows:
        print(
            "[!] no x'<hex>' literals and no salt windows; "
            "check db_root, login, and that AV is not blocking ReadProcessMemory",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(main())
