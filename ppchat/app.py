"""Application layer (Approach A): assemble a group's day of messages into an
analysis-ready bundle, and define/persist the two deliverables:

  - summary.json / summary.md   (daily summary: topics, participants, todos)
  - requirements.json           (needs extracted from the day, with linked images)

The *reasoning* (writing the summary/requirements) is done by the Cursor agent,
which reads the exported bundle and calls save_summary()/save_requirements().
This module owns data assembly + output contracts + file layout only; it has no
LLM dependency and no business heuristics baked in.

Output schemas
--------------
summary.json:
{
  "group": "<name>", "date": "YYYY-MM-DD",
  "overview": "<1-3 sentence gist>",
  "topics": [
    {"title": "<topic>", "detail": "<what was discussed / decided>",
     "participants": ["<name>", ...], "message_ids": [<int>, ...]}
  ],
  "participants": [{"name": "<name>", "message_count": <int>}],
  "todos": [{"text": "<action item>", "owner": "<name|null>", "message_ids": [<int>]}]
}

requirements.json:
{
  "group": "<name>", "date": "YYYY-MM-DD",
  "requirements": [
    {"id": "R1",
     "title": "<short need>",
     "detail": "<full description in context>",
     "raised_by": "<name>",
     "status": "open|answered|resolved",
     "message_ids": [<int>, ...],
     "images": [{"message_id": <int>, "src_ref": "<md5>",
                 "local_path": "<path|null>", "note": "<why linked>"}]}
  ]
}
"""
from __future__ import annotations

import datetime as _dt
import json
import re
import time
from pathlib import Path

from . import config, store

# ---- helpers to derive a human-readable line for non-text messages ----

_IMG_NAME_RE = re.compile(r"<img_file_name>(.*?)</img_file_name>", re.S)
_TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)
_SYS_CONTENT_RE = re.compile(r"<content>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</content>", re.S)


def _raw_parts(raw: str | None) -> tuple[str, str]:
    if not raw:
        return "", ""
    try:
        obj = json.loads(raw)
        return obj.get("content", "") or "", obj.get("source", "") or ""
    except Exception:
        return raw, ""


def display_text(mtype: str, text: str | None, raw: str | None) -> str:
    if mtype == "text":
        return text or ""
    content, source = _raw_parts(raw)
    if mtype == "image":
        m = _IMG_NAME_RE.search(source)
        return f"[图片] {m.group(1).strip()}" if m else "[图片]"
    if mtype == "voice":
        return "[语音]"
    if mtype == "sticker":
        return "[表情]"
    if mtype == "video":
        return "[视频]"
    if mtype == "app":
        m = _TITLE_RE.search(content)
        title = re.sub(r"\s+", " ", m.group(1)).strip() if m else ""
        return f"[引用/卡片] {title}" if title else "[引用/卡片]"
    if mtype == "system":
        m = _SYS_CONTENT_RE.search(content)
        return f"[系统] {m.group(1).strip()}" if m else "[系统消息]"
    return f"[{mtype}]"


# ---- group / date resolution ----

def resolve_group(con, query: str) -> tuple[str, str]:
    """Resolve a group by wxid or (partial) name. Returns (wxid, name)."""
    row = con.execute(
        "SELECT wxid, name FROM chats WHERE wxid=? OR name=? LIMIT 1", (query, query)
    ).fetchone()
    if not row:
        row = con.execute(
            "SELECT wxid, name FROM chats WHERE name LIKE ? LIMIT 1", (f"%{query}%",)
        ).fetchone()
    if not row:
        raise LookupError(f"no chat matching {query!r}")
    return row["wxid"], row["name"]


def _day_bounds(date_str: str) -> tuple[int, int]:
    d = _dt.datetime.strptime(date_str, "%Y-%m-%d")
    start = d.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + _dt.timedelta(days=1)
    return int(start.timestamp()), int(end.timestamp())


def bundle_dir(group_name: str, date_str: str) -> Path:
    safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", group_name).strip("_") or "group"
    return config.OUT_DIR / safe / date_str


# ---- export the analysis-ready bundle ----

def export_day(group_query: str, date_str: str) -> dict:
    con = store.connect()
    try:
        wxid, name = resolve_group(con, group_query)
        since, until = _day_bounds(date_str)
        rows = store.get_messages(con, wxid, since=since, until=until, limit=100000)

        messages = []
        pcount: dict[str, int] = {}
        for r in rows:
            imgs = store.get_images(con, r["id"]) if r["n_attachments"] else []
            messages.append({
                "id": r["id"],
                "ts": r["ts"],
                "time": _dt.datetime.fromtimestamp(r["ts"]).strftime("%H:%M"),
                "sender": r["sender_name"],
                "type": r["type"],
                "text": display_text(r["type"], r["text"], _msg_raw(con, r["id"])),
                "images": [{"message_id": r["id"], "src_ref": im["src_ref"],
                            "local_path": im["local_path"]} for im in imgs],
            })
            pcount[r["sender_name"]] = pcount.get(r["sender_name"], 0) + 1

        bundle = {
            "group": {"wxid": wxid, "name": name},
            "date": date_str,
            "message_count": len(messages),
            "participants": [{"name": k, "message_count": v}
                             for k, v in sorted(pcount.items(), key=lambda x: -x[1])],
            "messages": messages,
        }
        d = bundle_dir(name, date_str)
        d.mkdir(parents=True, exist_ok=True)
        (d / "messages.json").write_text(
            json.dumps(bundle, ensure_ascii=False, indent=2))
        bundle["_bundle_dir"] = str(d)
        return bundle
    finally:
        con.close()


def _msg_raw(con, message_id: int) -> str | None:
    r = con.execute("SELECT raw FROM messages WHERE id=?", (message_id,)).fetchone()
    return r["raw"] if r else None


# ---- persist deliverables (called by the agent after reasoning) ----

def save_summary(group_name: str, date_str: str, summary: dict) -> Path:
    d = bundle_dir(group_name, date_str)
    d.mkdir(parents=True, exist_ok=True)
    (d / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    (d / "summary.md").write_text(render_summary_md(summary))
    return d / "summary.json"


def save_requirements(group_name: str, date_str: str, reqs: dict) -> Path:
    d = bundle_dir(group_name, date_str)
    d.mkdir(parents=True, exist_ok=True)
    (d / "requirements.json").write_text(json.dumps(reqs, ensure_ascii=False, indent=2))
    return d / "requirements.json"


def _cursor_row(row, name: str) -> dict:
    return {
        "chat_wxid": row["chat_wxid"],
        "name": name,
        "kind": row["kind"],
        "kind_label": row["kind_label"],
        "last_message_id": row["last_message_id"],
        "last_sort_seq": row["last_sort_seq"],
        "last_ts": row["last_ts"],
        "updated_at": row["updated_at"],
    }


def list_cursors(group: str | None = None) -> list[dict]:
    con = store.connect()
    try:
        sql = (
            "SELECT ac.chat_wxid, ac.kind, ac.kind_label, ac.last_message_id, "
            "ac.last_sort_seq, ac.last_ts, ac.updated_at, ch.name "
            "FROM analysis_cursors ac JOIN chats ch ON ch.wxid=ac.chat_wxid"
        )
        args: list = []
        if group is not None:
            wxid, _ = resolve_group(con, group)
            sql += " WHERE ac.chat_wxid=?"
            args.append(wxid)
        sql += " ORDER BY ch.name, ac.kind"
        return [_cursor_row(r, r["name"]) for r in con.execute(sql, args)]
    finally:
        con.close()


def get_cursor(group: str, kind: str) -> dict | None:
    con = store.connect()
    try:
        wxid, name = resolve_group(con, group)
        row = con.execute(
            "SELECT chat_wxid, kind, kind_label, last_message_id, "
            "last_sort_seq, last_ts, updated_at "
            "FROM analysis_cursors WHERE chat_wxid=? AND kind=?",
            (wxid, kind),
        ).fetchone()
        if not row:
            return None
        ok = con.execute(
            "SELECT m.id FROM messages m JOIN chats c ON c.id=m.chat_id "
            "WHERE m.id=? AND c.wxid=?",
            (row["last_message_id"], wxid),
        ).fetchone()
        if not ok:
            return None
        return _cursor_row(row, name)
    finally:
        con.close()


def save_cursor(group: str, kind: str, last_message_id: int,
                kind_label: str | None = None) -> dict:
    con = store.connect()
    try:
        wxid, name = resolve_group(con, group)
        msg = con.execute(
            "SELECT m.id, m.sort_seq, m.ts, c.wxid FROM messages m "
            "JOIN chats c ON c.id=m.chat_id WHERE m.id=?",
            (last_message_id,),
        ).fetchone()
        if not msg:
            raise ValueError(f"message {last_message_id} not found")
        if msg["wxid"] != wxid:
            raise ValueError(f"message {last_message_id} is not in chat {wxid}")
        label = kind if kind_label is None else kind_label
        now = int(time.time())
        con.execute(
            "INSERT INTO analysis_cursors"
            "(chat_wxid,kind,kind_label,last_message_id,last_sort_seq,last_ts,updated_at)"
            " VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(chat_wxid,kind) DO UPDATE SET "
            "kind_label=excluded.kind_label,"
            "last_message_id=excluded.last_message_id,"
            "last_sort_seq=excluded.last_sort_seq,"
            "last_ts=excluded.last_ts,"
            "updated_at=excluded.updated_at",
            (wxid, kind, label, last_message_id, msg["sort_seq"], msg["ts"], now),
        )
        con.commit()
        return {
            "chat_wxid": wxid, "name": name, "kind": kind, "kind_label": label,
            "last_message_id": last_message_id, "last_sort_seq": msg["sort_seq"],
            "last_ts": msg["ts"], "updated_at": now,
        }
    finally:
        con.close()


def render_summary_md(s: dict) -> str:
    lines = [f"# {s.get('group','')} · {s.get('date','')} 聊天汇总", ""]
    if s.get("overview"):
        lines += [s["overview"], ""]
    if s.get("topics"):
        lines.append("## 话题")
        for t in s["topics"]:
            who = "、".join(t.get("participants", []))
            lines.append(f"- **{t['title']}**（{who}）：{t.get('detail','')}")
        lines.append("")
    if s.get("todos"):
        lines.append("## 待办 / 行动项")
        for td in s["todos"]:
            owner = f"（{td['owner']}）" if td.get("owner") else ""
            lines.append(f"- [ ] {td['text']}{owner}")
        lines.append("")
    if s.get("participants"):
        lines.append("## 参与人")
        for p in s["participants"]:
            lines.append(f"- {p['name']}：{p['message_count']} 条")
        lines.append("")
    return "\n".join(lines)
