"""Local HTTP API exposing the generic Access API primitives.

Deliberately generic and read-only: single-message / single-chat data access, no
business logic (no summary/requirements endpoints here — those are app-layer
artifacts). Bound to 127.0.0.1 only.

Run:
    ./.venv/bin/python -m ppchat.api_http           # serves 127.0.0.1:5030
    ./.venv/bin/uvicorn ppchat.api_http:app --host 127.0.0.1 --port 5030
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query

from . import store

app = FastAPI(title="ppchat local API", version="1.0", docs_url="/docs")


def _con():
    return store.connect()


@app.get("/health")
def health():
    con = _con()
    try:
        n = con.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        g = con.execute("SELECT COUNT(*) FROM chats").fetchone()[0]
        return {"ok": True, "chats": g, "messages": n}
    finally:
        con.close()


@app.get("/groups")
def groups():
    con = _con()
    try:
        return store.get_groups(con)
    finally:
        con.close()


@app.get("/contacts")
def contacts():
    con = _con()
    try:
        return store.get_contacts(con)
    finally:
        con.close()


@app.get("/messages")
def messages(
    chat: str = Query(..., description="chat wxid, e.g. 43421369381@chatroom"),
    since: int | None = Query(None, description="unix seconds inclusive"),
    until: int | None = Query(None, description="unix seconds exclusive"),
    limit: int = Query(500, ge=1, le=100000),
    offset: int = Query(0, ge=0),
):
    con = _con()
    try:
        return store.get_messages(con, chat, since=since, until=until,
                                  limit=limit, offset=offset)
    finally:
        con.close()


@app.get("/messages/{message_id}")
def message(message_id: int):
    con = _con()
    try:
        m = store.get_message(con, message_id)
        if not m:
            raise HTTPException(status_code=404, detail="message not found")
        return m
    finally:
        con.close()


@app.get("/messages/{message_id}/images")
def message_images(message_id: int):
    con = _con()
    try:
        return store.get_images(con, message_id)
    finally:
        con.close()


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=5030, log_level="info")


if __name__ == "__main__":
    main()
