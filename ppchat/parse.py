"""WeChat 4.x (macOS) ingestion: decrypt -> normalize -> load into ppchat store.

Schema knowledge about WeChat lives ONLY here. Produces rows for the generic
store; everything above the store never sees WeChat internals.

Per user decision: keep source XML in `raw`, add `sort_seq`; sender names use the
global contact nick_name/remark; appmsg is coarse-typed as 'app'.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import tempfile
from pathlib import Path

import zstandard

from . import config, store
from .decrypt import decrypt_db
from .keys import load_key_map

_ZSTD = zstandard.ZstdDecompressor()

_TYPE_MAP = {
    1: "text",
    3: "image",
    34: "voice",
    43: "video",
    47: "sticker",
    10000: "system",
    49: "app",
}

_MD5_RE = re.compile(r'\bmd5\s*=\s*"([0-9a-fA-F]{32})"')


def _decode(blob) -> str:
    """Decode message_content/source: zstd-decompress if needed, then utf-8."""
    if blob is None:
        return ""
    b = blob if isinstance(blob, (bytes, bytearray)) else str(blob).encode()
    if len(b) >= 4 and b[:4] == b"\x28\xb5\x2f\xfd":
        try:
            b = _ZSTD.decompress(b)
        except Exception:
            try:
                b = _ZSTD.decompressobj().decompress(b)
            except Exception:
                return "[zstd decode failed]"
    return bytes(b).decode("utf-8", "replace")


def _strip_sender_prefix(content: str, sender_wxid: str) -> str:
    """Group messages are stored as 'sender_wxid:\\n<content>'."""
    if sender_wxid and content.startswith(sender_wxid + ":"):
        rest = content[len(sender_wxid) + 1:]
        return rest[1:] if rest.startswith("\n") else rest
    return content


def _norm_type(local_type: int, content: str) -> str:
    if local_type in _TYPE_MAP:
        return _TYPE_MAP[local_type]
    if "<appmsg" in content:
        return "app"
    return "other"


def _decrypt_to_temp(db_name: str, key_map: dict, tmpdir: Path) -> Path:
    src = next(Path(p) for p in key_map if Path(p).name == db_name)
    out = tmpdir / db_name
    decrypt_db(src, key_map[str(src)], out)
    return out


def ingest(chat_wxid: str | None = None, account_dir: Path | None = None) -> dict:
    """Decrypt + ingest. If chat_wxid is given, only that chat; else all groups.

    Returns a small summary dict.
    """
    key_map = load_key_map()
    if not key_map:
        raise RuntimeError("no key map; run tools/build_keymap.py first")

    con = store.connect()
    store.init(con)
    summary = {"chats": 0, "messages_inserted": 0}

    with tempfile.TemporaryDirectory(prefix="ppchat_") as td:
        tmp = Path(td)
        contact_db = _decrypt_to_temp("contact.db", key_map, tmp)
        message_db = _decrypt_to_temp("message_0.db", key_map, tmp)

        cdb = sqlite3.connect(contact_db)
        mdb = sqlite3.connect(message_db)

        # global contact display map
        disp: dict[str, tuple[str, str]] = {}
        for u, nk, rk in cdb.execute("SELECT username, nick_name, remark FROM contact"):
            disp[u] = (nk or "", rk or "")

        # sender id -> username (per message db)
        n2i = {rid: u for rid, u in mdb.execute("SELECT rowid, user_name FROM Name2Id")}

        # which chats to ingest
        if chat_wxid:
            targets = [chat_wxid]
        else:
            targets = [u for u in disp if u.endswith("@chatroom")]

        existing_tables = {
            r[0] for r in mdb.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Msg_%'"
            )
        }

        for wxid in targets:
            tbl = "Msg_" + hashlib.md5(wxid.encode()).hexdigest()
            if tbl not in existing_tables:
                continue
            is_group = wxid.endswith("@chatroom")
            nk, rk = disp.get(wxid, ("", ""))
            chat_id = store.upsert_chat(con, wxid, "group" if is_group else "private",
                                        rk or nk or wxid)
            summary["chats"] += 1

            rows = mdb.execute(
                f"SELECT local_id, server_id, local_type, sort_seq, real_sender_id, "
                f"create_time, message_content, source FROM {tbl}"
            ).fetchall()
            for (lid, svr, lt, seq, sid, ct, content, source) in rows:
                sender_wxid = n2i.get(sid, "")
                content_s = _decode(content)
                source_s = _decode(source) if source is not None else ""
                mtype = _norm_type(lt, content_s)

                # register sender as a contact (global display name)
                sender_id = None
                if sender_wxid:
                    s_nk, s_rk = disp.get(sender_wxid, ("", ""))
                    sender_id = store.upsert_contact(con, sender_wxid, s_nk or None, s_rk or None)

                if mtype == "text":
                    text = _strip_sender_prefix(content_s, sender_wxid)
                else:
                    text = None

                mid = store.insert_message(
                    con,
                    chat_id=chat_id,
                    src_local_id=lid,
                    svr_id=str(svr) if svr is not None else None,
                    sender_wxid=sender_wxid or None,
                    sender_id=sender_id,
                    ts=ct,
                    sort_seq=seq,
                    type=mtype,
                    local_type=lt,
                    text=text,
                    raw=json.dumps({"content": content_s, "source": source_s},
                                   ensure_ascii=False),
                )
                if mid is not None:
                    summary["messages_inserted"] += 1
                    if mtype == "image":
                        md5 = _MD5_RE.search(content_s)
                        store.add_attachment(con, mid, "image", md5.group(1) if md5 else None)

        cdb.close()
        mdb.close()
        con.commit()
    con.close()
    return summary
