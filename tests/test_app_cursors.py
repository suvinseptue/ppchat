"""Application-layer analysis cursors (per group + kind watermark)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ppchat import app, config, store


class CursorTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        home = Path(self.td.name)
        self._old = (config.PPCHAT_HOME, config.STORE_DB, config.OUT_DIR)
        config.PPCHAT_HOME = home
        config.STORE_DB = home / "ppchat.db"
        config.OUT_DIR = home / "out"
        con = store.connect()
        try:
            gid = store.upsert_chat(con, "434@chatroom", "group", "富德系统支持")
            oid = store.upsert_chat(con, "999@chatroom", "group", "其他群")
            cid = store.upsert_contact(con, "wxid_a", "Alice", None)
            self.msg_a1 = store.insert_message(
                con, chat_id=gid, src_local_id=1, svr_id="s1",
                sender_wxid="wxid_a", sender_id=cid, ts=1_724_000_000,
                sort_seq=10, type="text", local_type=1, text="one", raw=None,
            )
            self.msg_a2 = store.insert_message(
                con, chat_id=gid, src_local_id=2, svr_id="s2",
                sender_wxid="wxid_a", sender_id=cid, ts=1_724_000_100,
                sort_seq=20, type="text", local_type=1, text="two", raw=None,
            )
            self.msg_a3 = store.insert_message(
                con, chat_id=gid, src_local_id=3, svr_id="s3",
                sender_wxid="wxid_a", sender_id=cid, ts=1_724_000_200,
                sort_seq=30, type="text", local_type=1, text="three", raw=None,
            )
            self.msg_b1 = store.insert_message(
                con, chat_id=oid, src_local_id=1, svr_id="b1",
                sender_wxid="wxid_a", sender_id=cid, ts=1_724_000_300,
                sort_seq=10, type="text", local_type=1, text="other", raw=None,
            )
            con.commit()
        finally:
            con.close()

    def tearDown(self):
        config.PPCHAT_HOME, config.STORE_DB, config.OUT_DIR = self._old
        self.td.cleanup()

    def test_two_kinds_do_not_overwrite(self):
        app.save_cursor("富德系统支持", "需求分析", self.msg_a1)
        app.save_cursor("富德系统支持", "事件复盘", self.msg_a2)
        req = app.get_cursor("富德系统支持", "需求分析")
        ev = app.get_cursor("富德系统支持", "事件复盘")
        self.assertEqual(req["last_message_id"], self.msg_a1)
        self.assertEqual(ev["last_message_id"], self.msg_a2)
        kinds = {c["kind"] for c in app.list_cursors("富德系统支持")}
        self.assertEqual(kinds, {"需求分析", "事件复盘"})

    def test_save_cursor_overwrites_same_kind(self):
        app.save_cursor("富德系统支持", "需求分析", self.msg_a1, kind_label="需求汇集")
        saved = app.save_cursor("富德系统支持", "需求分析", self.msg_a3, kind_label="需求统计")
        self.assertEqual(saved["last_message_id"], self.msg_a3)
        self.assertEqual(saved["kind_label"], "需求统计")
        self.assertEqual(saved["last_sort_seq"], 30)
        rows = app.list_cursors("富德系统支持")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["last_message_id"], self.msg_a3)

    def test_save_cursor_rejects_missing_message(self):
        with self.assertRaises(ValueError):
            app.save_cursor("富德系统支持", "需求分析", 999999)

    def test_save_cursor_rejects_other_group_message(self):
        with self.assertRaises(ValueError):
            app.save_cursor("富德系统支持", "需求分析", self.msg_b1)
        self.assertIsNone(app.get_cursor("富德系统支持", "需求分析"))

    def test_get_cursor_none_when_missing(self):
        self.assertIsNone(app.get_cursor("富德系统支持", "需求分析"))

    def test_get_cursor_none_when_message_deleted(self):
        app.save_cursor("富德系统支持", "需求分析", self.msg_a2)
        con = store.connect()
        try:
            con.execute("DELETE FROM messages WHERE id=?", (self.msg_a2,))
            con.commit()
        finally:
            con.close()
        self.assertIsNone(app.get_cursor("富德系统支持", "需求分析"))

    def test_list_cursors_filters_by_group(self):
        app.save_cursor("富德系统支持", "需求分析", self.msg_a1)
        app.save_cursor("其他群", "需求分析", self.msg_b1)
        only = app.list_cursors("富德系统支持")
        self.assertEqual([c["chat_wxid"] for c in only], ["434@chatroom"])
        all_rows = app.list_cursors()
        self.assertEqual(len(all_rows), 2)


if __name__ == "__main__":
    unittest.main()
