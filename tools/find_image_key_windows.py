#!/usr/bin/env python3
"""Scan Weixin.exe memory for the WeChat 4.x *image* AES key (Windows).

This is a different key from the SQLCipher DB key scanned by
``tools/find_keys_windows.py``. V2 ``.dat`` images are AES-128-ECB; the
key lives in process memory while images have been viewed.

# REAL-MACHINE-VERIFY: the in-memory *shape* of the image key is unconfirmed
on this macOS machine. Community scanners (wechat-decrypt ``find_image_key.py``)
treat it as a 16-character alphanumeric ASCII string (sometimes 32). This
module uses that feature so the scan logic can be unit-tested with synthetic
buffers. A real Windows box must confirm: length (16 vs 32), alphabet
(alnum vs raw bytes vs hex), alignment, and whether the key is only present
after the user opens an image.

Usage (Administrator, Weixin.exe logged in, view 2–3 images first)::

    python tools/find_image_key_windows.py

Output: ``~/.ppchat/image_key_windows.json`` plus hex candidates on stdout.

ctypes / kernel32 stay inside Windows-only helpers so this module imports
on macOS (``scan_buffer_for_image_keys`` is pure).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# REAL-MACHINE-VERIFY: 16/32-char [0-9A-Za-z] is the community heuristic.
# Raw 16-byte blobs (high entropy, non-ASCII) are not collected here.
_RE_KEY32 = re.compile(rb"[0-9A-Za-z]{32}")
_RE_KEY16 = re.compile(rb"[0-9A-Za-z]{16}")

PROCESS_NAME = "Weixin.exe"
OUTPUT_NAME = "image_key_windows.json"


def scan_buffer_for_image_keys(buf: bytes) -> list[bytes]:
    """Return unique 16- or 32-byte alphanumeric ASCII candidates (order kept).

    32-byte matches are recorded first so a 32-char run is kept as one key;
    its 16-byte halves are also kept (they are valid AES-128 candidates).
    """
    seen: set[bytes] = set()
    out: list[bytes] = []
    for rx in (_RE_KEY32, _RE_KEY16):
        for m in rx.finditer(buf):
            key = m.group(0)
            if key not in seen:
                seen.add(key)
                out.append(key)
    return out


def _output_path() -> Path:
    try:
        from ppchat import config

        return config.PPCHAT_HOME / OUTPUT_NAME
    except ImportError:
        return Path.home() / ".ppchat" / OUTPUT_NAME


def write_candidates(
    candidates: list[bytes],
    path: Path | None = None,
    *,
    pid: int | None = None,
) -> Path:
    dest = path or _output_path()
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "process": PROCESS_NAME,
        "pid": pid,
        "candidates": [
            {"hex": c.hex(), "ascii": _ascii_or_none(c), "n": len(c)}
            for c in candidates
        ],
    }
    dest.write_text(json.dumps(payload, indent=2) + "\n")
    return dest


def _ascii_or_none(key: bytes) -> str | None:
    try:
        return key.decode("ascii")
    except UnicodeDecodeError:
        return None


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
            f"{PROCESS_NAME} not found; start WeChat 4.0, log in, open an image"
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
    # mask, and whether PAGE_GUARD regions must be skipped on the target build.
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
                    if ok and read.value:
                        yield bytes(buf[: read.value])
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
    print(f"[*] scanning {PROCESS_NAME} pid={pid} for image AES keys", file=sys.stderr)
    seen: set[bytes] = set()
    candidates: list[bytes] = []
    for chunk in _iter_process_memory(pid):
        for key in scan_buffer_for_image_keys(chunk):
            if key not in seen:
                seen.add(key)
                candidates.append(key)
    dest = write_candidates(candidates, pid=pid)
    print(f"[*] {len(candidates)} candidate(s) -> {dest}", file=sys.stderr)
    for c in candidates:
        print(c.hex())
    if not candidates:
        print(
            "[!] no 16/32-byte alnum candidates; open a few images and retry "
            "(feature is REAL-MACHINE-VERIFY)",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    raise SystemExit(main())
