"""lldb key capture for WeChat 4.x (macOS), verified path for 4.1.x.

WCDB on macOS derives the per-DB HMAC key via Apple CommonCrypto:
    CCKeyDerivationPBKDF(alg, password, passwordLen, salt, saltLen, prf,
                         rounds, derivedKey, derivedKeyLen)
For the HMAC-key derivation SQLCipher uses rounds=2 and passes the *raw 32-byte
enc key* as `password`, and mac_salt (= file_salt XOR 0x3a) as `salt`. So at the
breakpoint we read:
    x1 = password ptr, x2 = passwordLen(=32), x3 = salt ptr, x4 = saltLen(=16),
    x6 = rounds(=2)
=> enc_key = 32 bytes @ x1 ; file_salt = (16 bytes @ x3) XOR 0x3a

These derivations fire when WCDB opens/keys a database. Because DBs are lazy and
may already be open, the reliable trigger is: quit WeChat, attach with --waitfor,
then launch + log in so every DB is keyed while we watch.

Usage (in your Terminal) — attach to the extract COPY only, never official WeChat:
    bash tools/get_keys.sh prepare
    killall WeChat            # quit official WeChat first
    sudo lldb -o "command script import tools/lldb_capture.py" -o "ppc_waitfor"
    # in another terminal, launch the copy (not /Applications/WeChat.app):
    open -n "$HOME/.ppchat/extract/WeChat.app"
    # log in; watch "[+] captured ..." lines; when the count stops growing:
    (lldb) ppc_dump
    (lldb) quit
    bash tools/get_keys.sh cleanup

Writes ~/.ppchat/kdf_candidates.json : {"keys": ["<64hex>", ...],
                                        "pairs": [["<64hex key>","<32hex salt>"]]}
"""
import json
import os
import lldb

_captured_keys = set()        # 64-hex enc keys
_captured_pairs = set()       # (key_hex, salt_hex)


def _home():
    if os.environ.get("SUDO_USER"):
        return os.path.expanduser("~" + os.environ["SUDO_USER"])
    return os.path.expanduser("~")


def _out_path():
    d = os.path.join(_home(), ".ppchat")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "kdf_candidates.json")


def _extract_bin():
    return os.path.join(
        _home(), ".ppchat/extract/WeChat.app/Contents/MacOS/WeChat"
    )


def _extract_pid():
    want = _extract_bin()
    # ps: "  123 /path/to/WeChat" — compare executable path only.
    with os.popen("ps -ax -o pid=,command=") as pipe:
        for line in pipe:
            line = line.strip()
            if not line:
                continue
            pid_s, _, cmd = line.partition(" ")
            exe = cmd.strip().split(" ")[0] if cmd.strip() else ""
            if exe == want:
                try:
                    return int(pid_s)
                except ValueError:
                    return None
    return None


def on_kdf(frame, bp_loc, internal_dict):
    """Breakpoint callback; returns False to auto-continue the process."""
    try:
        def reg(name):
            return frame.FindRegister(name).GetValueAsUnsigned()

        pwd_ptr = reg("x1")
        pwd_len = reg("x2")
        salt_ptr = reg("x3")
        salt_len = reg("x4")
        rounds = reg("x6") & 0xFFFFFFFF

        process = frame.GetThread().GetProcess()
        err = lldb.SBError()

        if pwd_len == 32 and rounds == 2:
            key = process.ReadMemory(pwd_ptr, 32, err)
            if err.Success() and key:
                khex = key.hex()
                salt_hex = ""
                if salt_len == 16:
                    ms = process.ReadMemory(salt_ptr, 16, err)
                    if err.Success() and ms:
                        # file_salt = mac_salt XOR 0x3a
                        salt_hex = bytes(b ^ 0x3A for b in ms).hex()
                if khex not in _captured_keys:
                    _captured_keys.add(khex)
                    print(f"[+] captured key #{len(_captured_keys)} "
                          f"{khex[:12]}... salt={salt_hex[:12] or '?'}")
                if salt_hex:
                    _captured_pairs.add((khex, salt_hex))
    except Exception as e:  # never let a callback stop the world
        print("[!] callback error:", e)
    return False  # auto-continue


def ppc_waitfor(debugger, command, result, internal_dict):
    bin_path = _extract_bin()
    if not os.path.isfile(bin_path):
        print("[!] extract copy not found:", bin_path)
        print("[!] run: bash tools/get_keys.sh prepare")
        print("[!] refusing to attach to /Applications/WeChat.app")
        return
    debugger.SetAsync(True)
    target = debugger.CreateTarget("")
    print("[*] waiting for extract copy to launch:")
    print("      open -n \"%s\"" % os.path.dirname(os.path.dirname(os.path.dirname(bin_path))))
    err = lldb.SBError()
    attach_info = lldb.SBAttachInfo()
    attach_info.SetWaitForLaunch(True)
    attach_info.SetExecutable(bin_path)
    process = target.Attach(attach_info, err)
    if not err.Success():
        print("[!] attach failed:", err.GetCString())
        return
    print(f"[*] attached to pid {process.GetProcessID()}; setting breakpoint")
    # single pending breakpoint by name; it auto-resolves when CommonCrypto loads
    bp = target.BreakpointCreateByName("CCKeyDerivationPBKDF")
    bp.SetScriptCallbackFunction("lldb_capture.on_kdf")
    bp.SetAutoContinue(True)
    print(f"[*] breakpoint on CCKeyDerivationPBKDF, {bp.GetNumLocations()} location(s)")
    process.Continue()
    print("[*] running. Log in to WeChat and open chats. Then run: ppc_dump")


def ppc_attach(debugger, command, result, internal_dict):
    """Attach to the already-running extract copy (may capture fewer keys)."""
    pid = _extract_pid()
    if pid is None:
        print("[!] extract-copy WeChat is not running.")
        print("[!] refusing to attach by process name (that would hit official WeChat).")
        print("[!] launch ~/.ppchat/extract/WeChat.app, or use ppc_waitfor.")
        return
    debugger.SetAsync(True)
    target = debugger.CreateTarget("")
    err = lldb.SBError()
    attach_info = lldb.SBAttachInfo()
    attach_info.SetProcessID(pid)
    process = target.Attach(attach_info, err)
    if not err.Success():
        print("[!] attach failed:", err.GetCString())
        return
    print(f"[*] attached to extract-copy pid {process.GetProcessID()}")
    bp = target.BreakpointCreateByName("CCKeyDerivationPBKDF")
    bp.SetScriptCallbackFunction("lldb_capture.on_kdf")
    bp.SetAutoContinue(True)
    print(f"[*] breakpoint set, {bp.GetNumLocations()} location(s). Open new chats/sections.")
    process.Continue()


def ppc_dump(debugger, command, result, internal_dict):
    path = _out_path()
    data = {
        "keys": sorted(_captured_keys),
        "pairs": [[k, s] for (k, s) in sorted(_captured_pairs)],
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    print(f"[✓] wrote {len(_captured_keys)} key(s), "
          f"{len(_captured_pairs)} key+salt pair(s) to {path}")


def __lldb_init_module(debugger, internal_dict):
    debugger.HandleCommand("command script add -f lldb_capture.ppc_waitfor ppc_waitfor")
    debugger.HandleCommand("command script add -f lldb_capture.ppc_attach ppc_attach")
    debugger.HandleCommand("command script add -f lldb_capture.ppc_dump ppc_dump")
    print("[*] ppchat lldb loaded. Commands: ppc_waitfor | ppc_attach | ppc_dump")
