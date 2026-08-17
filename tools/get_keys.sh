#!/bin/bash
# Isolate key capture on a temporary WeChat copy. Never sign the official app.
#
# Official WeChat (/Applications/WeChat.app) stays Tencent-signed for daily use.
# A copy at ~/.ppchat/extract/WeChat.app is ad-hoc signed, used once to capture
# keys, then deleted.
#
# Run in your own Terminal (codesign / lldb may prompt).
#
#   bash tools/get_keys.sh status     # official vs copy signatures
#   bash tools/get_keys.sh prepare    # copy official -> ~/.ppchat/extract, sign COPY only
#   bash tools/get_keys.sh capture    # memory-scan the running COPY
#   bash tools/get_keys.sh cleanup    # kill+delete the copy, re-register official
#
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"

REAL_HOME="$HOME"
if [ -n "${SUDO_USER:-}" ]; then
  REAL_HOME="$(dscl . -read /Users/"$SUDO_USER" NFSHomeDirectory 2>/dev/null | awk '{print $2}')"
  [ -z "$REAL_HOME" ] && REAL_HOME="/Users/$SUDO_USER"
fi
export HOME="$REAL_HOME"

OFFICIAL_APP="/Applications/WeChat.app"
EXTRACT_ROOT="$REAL_HOME/.ppchat/extract"
EXTRACT_APP="$EXTRACT_ROOT/WeChat.app"
EXTRACT_BIN="$EXTRACT_APP/Contents/MacOS/WeChat"
LSREGISTER="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"

PY="$ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="python3"

ppc_py() {
  PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}" "$PY" -m ppchat.wechat_app "$@"
}

usage() {
  cat <<EOF
Usage: bash tools/get_keys.sh <status|prepare|capture|cleanup>

Key capture must never codesign $OFFICIAL_APP.

Full flow (only when keys.json is missing / new account / new DBs):

  1) Official WeChat must be Tencent-signed.
       If "status" says ad-hoc: reinstall from https://mac.weixin.qq.com/
       Chat history stays in ~/Library/Containers (app reinstall does not wipe it).

  2) bash tools/get_keys.sh prepare
       Copies official WeChat to ~/.ppchat/extract/WeChat.app (~1.3G) and
       ad-hoc signs THAT copy only.

  3) Quit official WeChat, start lldb, THEN launch the copy:

       killall WeChat 2>/dev/null || true
       sudo lldb -o "command script import $ROOT/tools/lldb_capture.py" -o "ppc_waitfor"
       # in another terminal:
       open -n "\$HOME/.ppchat/extract/WeChat.app"
       # log in, open a few chats; when captures stop growing:
       (lldb) ppc_dump
       (lldb) quit

  4) bash tools/get_keys.sh cleanup
       Deletes the copy and points Launch Services back at official WeChat.
       Then open /Applications/WeChat.app as usual.

  5) ./.venv/bin/python tools/build_keymap.py

Terminal needs Full Disk Access (System Settings > Privacy & Security).
EOF
}

cmd="${1:-}"
case "$cmd" in
  status)
    ppc_py status
    ;;

  prepare)
    if [ ! -d "$OFFICIAL_APP" ]; then
      echo "[!] $OFFICIAL_APP not found"
      exit 1
    fi
    ppc_py check-prepare
    ppc_py check-sign-target "$EXTRACT_APP"

    echo "[*] replacing $EXTRACT_APP"
    rm -rf "$EXTRACT_ROOT"
    mkdir -p "$EXTRACT_ROOT"
    echo "[*] copying official WeChat (this is ~1.3G, a minute or two)..."
    ditto "$OFFICIAL_APP" "$EXTRACT_APP"
    xattr -cr "$EXTRACT_APP" 2>/dev/null || true

    ppc_py check-sign-target "$EXTRACT_APP"
    echo "[*] ad-hoc signing extract copy only"
    if ! codesign --force --deep --sign - "$EXTRACT_APP"; then
      echo "[!] codesign failed without sudo; retrying with sudo"
      sudo codesign --force --deep --sign - "$EXTRACT_APP"
    fi

    echo "[✓] extract copy ready: $EXTRACT_APP"
    echo
    echo "    Do NOT open /Applications/WeChat.app for capture."
    echo "    Quit official WeChat, attach lldb (ppc_waitfor), then:"
    echo "      open -n \"$EXTRACT_APP\""
    echo "    When done: bash tools/get_keys.sh cleanup"
    ;;

  capture)
    SALTS="$REAL_HOME/.ppchat/salts.txt"
    OUT="$REAL_HOME/.ppchat/candidates.json"
    mkdir -p "$REAL_HOME/.ppchat"
    if [ ! -f "$SALTS" ]; then
      echo "[!] $SALTS not found. Generate it first (no sudo):"
      echo "    $PY -c 'from ppchat import config,keys; print(\"ok\")'"
      exit 1
    fi
    if [ ! -x "$EXTRACT_BIN" ]; then
      echo "[!] extract copy not found. Run: bash tools/get_keys.sh prepare"
      exit 1
    fi
    pid="$(ps -ax -o pid=,command= | awk -v want="$EXTRACT_BIN" '
      {
        cmd=$0; sub(/^[[:space:]]*[0-9]+[[:space:]]+/, "", cmd);
        split(cmd, a, " ");
        if (a[1]==want) { print $1; exit }
      }')"
    if [ -z "$pid" ]; then
      echo "[!] extract copy is not running. Launch it with:"
      echo "      open -n \"$EXTRACT_APP\""
      echo "    Official WeChat must be quit first (same data container)."
      exit 1
    fi
    echo "[*] rebuilding scanner..."
    cc -O2 -o "$HERE/find_keys_macos" "$HERE/find_keys_macos.c" -framework Foundation
    echo "[*] scanning extract-copy pid $pid (needs sudo if not already root)..."
    SCANNER="$HERE/find_keys_macos"
    if "$SCANNER" "$SALTS" "$pid" | tee "$OUT" >/dev/null; then
      if grep -q '"salt"' "$OUT" || grep -q '"literals":\["' "$OUT"; then
        echo "[✓] candidates written to $OUT"
        [ -n "${SUDO_USER:-}" ] && chown "$SUDO_USER" "$OUT" 2>/dev/null || true
        exit 0
      fi
    fi
    echo
    echo "[!] Scanner found nothing. For WeChat 4.1.x use the lldb path in:"
    echo "      bash tools/get_keys.sh"
    echo "    (prepare -> ppc_waitfor -> launch copy -> ppc_dump -> cleanup)"
    exit 1
    ;;

  cleanup)
    echo "[*] stopping extract-copy processes"
    # Match only the copy; do not kill official /Applications/WeChat.app.
    pids="$(ps -ax -o pid=,command= | awk -v root="$EXTRACT_APP" '
      index($0, root) { print $1 }')"
    if [ -n "$pids" ]; then
      # shellcheck disable=SC2086
      kill $pids 2>/dev/null || true
      sleep 1
      still="$(ps -ax -o pid=,command= | awk -v root="$EXTRACT_APP" '
        index($0, root) { print $1 }')"
      if [ -n "$still" ]; then
        # shellcheck disable=SC2086
        kill -9 $still 2>/dev/null || true
      fi
    fi
    if [ -d "$EXTRACT_ROOT" ]; then
      echo "[*] deleting $EXTRACT_ROOT"
      rm -rf "$EXTRACT_ROOT"
    else
      echo "[*] no extract copy present"
    fi
    if [ -x "$LSREGISTER" ] && [ -d "$OFFICIAL_APP" ]; then
      echo "[*] re-registering official WeChat with Launch Services"
      "$LSREGISTER" -f "$OFFICIAL_APP" >/dev/null 2>&1 || true
    fi
    echo "[✓] copy removed. Open $OFFICIAL_APP for daily use."
    echo "    If screenshot/mic permissions still fail: tccutil reset All com.tencent.xinWeChat"
    echo "    then quit and reopen official WeChat and re-grant the prompts."
    ;;

  -h|--help|help|"")
    usage
    ;;

  *)
    echo "[!] unknown command: $cmd"
    usage
    exit 2
    ;;
esac
