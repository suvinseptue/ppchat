"""Normalized local store (~/.ppchat/ppchat.db) + generic Access API primitives.

The Access API is deliberately dumb: single-message / single-chat data access only.
No business logic (no "requirements", no "summary") lives here.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from . import config

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    id     INTEGER PRIMARY KEY,
    wxid   TEXT UNIQUE NOT NULL,
    type   TEXT NOT NULL,          -- 'group' | 'private'
    name   TEXT
);
CREATE TABLE IF NOT EXISTS contacts (
    id           INTEGER PRIMARY KEY,
    wxid         TEXT UNIQUE NOT NULL,
    display_name TEXT,
    remark       TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY,
    chat_id      INTEGER NOT NULL REFERENCES chats(id),
    src_local_id INTEGER,          -- WeChat per-conversation local_id (dedup bookkeeping)
    svr_id       TEXT,
    sender_wxid  TEXT,
    sender_id    INTEGER REFERENCES contacts(id),
    ts           INTEGER,          -- unix seconds
    sort_seq     INTEGER,          -- WeChat authoritative ordering key
    type         TEXT,             -- text|image|voice|video|sticker|system|app|other
    local_type   INTEGER,          -- original WeChat message type
    text         TEXT,             -- plaintext body (null for non-text)
    raw          TEXT,             -- json: {"content": <str>, "source": <str|null>}
    UNIQUE(chat_id, src_local_id)
);
CREATE TABLE IF NOT EXISTS attachments (
    id         INTEGER PRIMARY KEY,
    message_id INTEGER NOT NULL REFERENCES messages(id),
    kind       TEXT,               -- 'image' | 'voice' | 'video' | 'file'
    src_ref    TEXT,               -- md5/aeskey ref parsed from message xml
    local_path TEXT,               -- decrypted file (null until extracted)
    sha256     TEXT,
    status     TEXT,               -- 'pending' | 'extracted' | 'failed'
    error      TEXT
);
CREATE TABLE IF NOT EXISTS analysis_cursors (
    chat_wxid       TEXT NOT NULL,
    kind            TEXT NOT NULL,
    kind_label      TEXT,
    last_message_id INTEGER NOT NULL,
    last_sort_seq   INTEGER,
    last_ts         INTEGER,
    updated_at      INTEGER,
    PRIMARY KEY (chat_wxid, kind)
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
CREATE INDEX IF NOT EXISTS ix_msg_chat_seq ON messages(chat_id, sort_seq);
CREATE INDEX IF NOT EXISTS ix_msg_chat_ts  ON messages(chat_id, ts);
CREATE INDEX IF NOT EXISTS ix_msg_svr      ON messages(svr_id);
"""


def connect() -> sqlite3.Connection:
    config.PPCHAT_HOME.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(config.STORE_DB)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    init(con)
    return con


def init(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)
    con.execute(
        "INSERT OR IGNORE INTO meta(key,value) VALUES('schema_version',?)",
        (str(SCHEMA_VERSION),),
    )
    con.commit()


# ---- upserts (used by providers) ----

def upsert_chat(con, wxid: str, type_: str, name: str | None) -> int:
    con.execute(
        "INSERT INTO chats(wxid,type,name) VALUES(?,?,?) "
        "ON CONFLICT(wxid) DO UPDATE SET type=excluded.type, name=excluded.name",
        (wxid, type_, name),
    )
    return con.execute("SELECT id FROM chats WHERE wxid=?", (wxid,)).fetchone()[0]


def upsert_contact(con, wxid: str, display_name: str | None, remark: str | None) -> int:
    con.execute(
        "INSERT INTO contacts(wxid,display_name,remark) VALUES(?,?,?) "
        "ON CONFLICT(wxid) DO UPDATE SET "
        "display_name=excluded.display_name, remark=excluded.remark",
        (wxid, display_name, remark),
    )
    return con.execute("SELECT id FROM contacts WHERE wxid=?", (wxid,)).fetchone()[0]


def insert_message(con, **m) -> int | None:
    """Idempotent insert keyed on (chat_id, src_local_id). Returns row id or None."""
    cur = con.execute(
        "INSERT OR IGNORE INTO messages"
        "(chat_id,src_local_id,svr_id,sender_wxid,sender_id,ts,sort_seq,type,local_type,text,raw)"
        " VALUES(:chat_id,:src_local_id,:svr_id,:sender_wxid,:sender_id,:ts,:sort_seq,"
        ":type,:local_type,:text,:raw)",
        m,
    )
    if cur.rowcount:
        return cur.lastrowid
    return None


def add_attachment(con, message_id: int, kind: str, src_ref: str | None) -> None:
    con.execute(
        "INSERT INTO attachments(message_id,kind,src_ref,status) VALUES(?,?,?, 'pending')",
        (message_id, kind, src_ref),
    )


# ---- Access API (generic, no business logic) ----

def get_groups(con) -> list[dict]:
    rows = con.execute(
        "SELECT wxid, name, "
        "(SELECT COUNT(*) FROM messages WHERE chat_id=chats.id) AS message_count "
        "FROM chats WHERE type='group' ORDER BY name"
    ).fetchall()
    return [dict(r) for r in rows]


def get_contacts(con) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT wxid, display_name, remark FROM contacts ORDER BY display_name"
    ).fetchall()]


def get_messages(con, chat_wxid: str, since: int | None = None, until: int | None = None,
                 limit: int = 500, offset: int = 0) -> list[dict]:
    sql = (
        "SELECT m.id, m.ts, m.sort_seq, m.type, m.local_type, m.sender_wxid, "
        "COALESCE(c.remark, c.display_name, m.sender_wxid) AS sender_name, "
        "m.text, "
        "(SELECT COUNT(*) FROM attachments a WHERE a.message_id=m.id) AS n_attachments "
        "FROM messages m "
        "JOIN chats ch ON ch.id=m.chat_id "
        "LEFT JOIN contacts c ON c.id=m.sender_id "
        "WHERE ch.wxid=?"
    )
    args: list = [chat_wxid]
    if since is not None:
        sql += " AND m.ts>=?"; args.append(since)
    if until is not None:
        sql += " AND m.ts<?"; args.append(until)
    sql += " ORDER BY m.sort_seq ASC, m.ts ASC LIMIT ? OFFSET ?"
    args += [limit, offset]
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def get_message(con, message_id: int) -> dict | None:
    r = con.execute("SELECT * FROM messages WHERE id=?", (message_id,)).fetchone()
    return dict(r) if r else None


def get_images(con, message_id: int) -> list[dict]:
    return [dict(r) for r in con.execute(
        "SELECT id, kind, src_ref, local_path, sha256, status "
        "FROM attachments WHERE message_id=? AND kind='image'", (message_id,)
    ).fetchall()]
