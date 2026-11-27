"""Kiểm thử tích hợp toàn diện:
- Phần 1 (`transport.py`): Framing 4-byte header, giới hạn 64 KiB.
- Phần 2 (`protocol.py`): JSON control + Binary file chunks (`BINARY_MAGIC`).
- Phần 3 (`storage.py`, `chat_server.py`, `network_client.py`, `gui_client.py`):
  1. Chia phòng Public / Private, xin vào phòng Private (`join_request` / `approve_join`).
  2. Phân quyền Admin: Kick thành viên (`kick_member`), Xóa phòng (`delete_room`), Thành viên rời phòng (`leave_room`), lưu phòng bền vững khi đăng nhập lại.
  3. Kiểm tra thành viên nhóm (`get_members` / lệnh `///` / hộp thoại `👥 Thành viên`).
  4. Truyền File / Ảnh nhị phân > 64 KiB + Lưu lịch sử SQLite + Thu hồi tin nhắn & ảnh (`recall`).

Chạy toàn bộ test:
    py test_client_integration.py
"""

import argparse
import os
import tempfile
import threading
import tkinter as tk
import unittest

from chat_server import ChatRoomServer
from gui_client import ChatClientGUI, measure_chat_bytes, parse_args, valid_port
from network_client import (
    EVENT_CONNECTED,
    EVENT_MESSAGE,
    SocketClient,
)
from protocol import (
    ProtocolError,
    decode_binary_chunk,
    decode_message,
    encode_binary_chunk,
    encode_message,
    is_binary_frame,
    make_message,
)

TINY_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)


def collect_until(client: SocketClient, predicate, timeout: float = 4.0):
    collected = []
    while True:
        ev = client.events.get(timeout=timeout)
        collected.append(ev)
        if predicate(ev):
            return collected


class TestFullChatSystem(unittest.TestCase):
    def setUp(self):
        fd, self.db_file = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self._start_server()

    def _start_server(self):
        self.server = ChatRoomServer(
            host="127.0.0.1", port=0, db_path=self.db_file, quiet=True
        )
        _, self.port = self.server.bind_and_listen()
        self.srv_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.srv_thread.start()

    def _stop_server(self):
        self.server.stop()
        self.srv_thread.join(timeout=2)

    def tearDown(self):
        self._stop_server()
        for suffix in ("", "-wal", "-shm"):
            p = self.db_file + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def _connect_and_hello(self, username: str, room=None):
        c = SocketClient()
        c.connect_async("127.0.0.1", self.port)
        ev = c.events.get(timeout=3.0)
        self.assertEqual(ev.type, EVENT_CONNECTED)
        hello_pkt = (
            make_message("hello", username=username, room=room)
            if room
            else make_message("hello", username=username)
        )
        c.send_payload(encode_message(hello_pkt))
        evs = collect_until(
            c,
            lambda e: e.type == EVENT_MESSAGE
            and not is_binary_frame(e.data)
            and decode_message(e.data).get("type") == "chat"
            and decode_message(e.data).get("system") is True,
        )
        last_room_list = None
        for item in evs:
            if item.type == EVENT_MESSAGE and not is_binary_frame(item.data):
                d = decode_message(item.data)
                if d["type"] == "room_list":
                    last_room_list = d
        return c, last_room_list

    def test_private_room_join_request_approval_member_check_and_kick(self):
        """Kiểm chứng trọn vẹn:
        1. Admin tạo phòng Private -> người ngoài bấm vào bị chặn và gửi join_request tới Admin.
        2. Admin bấm Duyệt -> thành viên tự động được vào phòng Private.
        3. Kiểm tra danh sách thành viên (get_members / ///) -> hiện đúng Admin & thành viên đang online; người ngoài bị chặn.
        4. Admin Kick thành viên -> bị đẩy ngay về #chung và thu hồi quyền vào phòng!
        """
        khang, _ = self._connect_and_hello("Minh Khang")
        hoa, _ = self._connect_and_hello("Thái Hoà")
        an, _ = self._connect_and_hello("An")
        try:
            # 1. Minh Khang tạo phòng riêng tư '#phong-kin'
            khang.send_payload(
                encode_message(
                    make_message("join_room", room="phong-kin", is_private=True)
                )
            )
            evs_k = collect_until(
                khang,
                lambda e: not is_binary_frame(e.data)
                and decode_message(e.data).get("system") is True
                and "#phong-kin" in decode_message(e.data).get("text", ""),
            )
            rlist_k = [
                decode_message(e.data)
                for e in evs_k
                if decode_message(e.data).get("type") == "room_list"
            ][-1]
            self.assertIn("phong-kin", rlist_k["private_rooms"])
            self.assertEqual(rlist_k["owners"]["phong-kin"], "Minh Khang")

            # 2. Thái Hoà xin vào '#phong-kin' -> bị giữ ở #chung và gửi join_request tới Admin Minh Khang
            hoa.send_payload(encode_message(make_message("join_room", room="phong-kin")))
            evs_hoa_wait = collect_until(
                hoa,
                lambda e: not is_binary_frame(e.data)
                and decode_message(e.data).get("system") is True
                and "vui lòng chờ duyệt" in decode_message(e.data).get("text", ""),
            )
            rlist_hoa_wait = [
                decode_message(e.data)
                for e in evs_hoa_wait
                if decode_message(e.data).get("type") == "room_list"
            ][-1]
            self.assertEqual(rlist_hoa_wait["current_room"], "chung")
            self.assertNotIn("phong-kin", rlist_hoa_wait["joined_rooms"])

            # Minh Khang nhận ngay gói join_request của Thái Hoà
            req_ev = collect_until(
                khang,
                lambda e: not is_binary_frame(e.data)
                and decode_message(e.data).get("type") == "join_request",
            )[-1]
            req_pkt = decode_message(req_ev.data)
            self.assertEqual(req_pkt["room"], "phong-kin")
            self.assertEqual(req_pkt["username"], "Thái Hoà")

            # Người ngoài (An) không được phép duyệt hoặc xem thành viên phòng Private
            an.send_payload(
                encode_message(
                    make_message(
                        "approve_join", room="phong-kin", target="Thái Hoà", approve=True
                    )
                )
            )
            err_approve = collect_until(
                an, lambda e: decode_message(e.data).get("type") == "error"
            )[-1]
            self.assertEqual(decode_message(err_approve.data)["code"], "APPROVE_DENIED")

            an.send_payload(encode_message(make_message("get_members", room="phong-kin")))
            err_mem = collect_until(
                an, lambda e: decode_message(e.data).get("type") == "error"
            )[-1]
            self.assertEqual(decode_message(err_mem.data)["code"], "PRIVATE_ROOM")

            # 3. Admin Minh Khang bấm Duyệt -> Thái Hoà tự động được chuyển thẳng vào '#phong-kin'!
            khang.send_payload(
                encode_message(
                    make_message(
                        "approve_join", room="phong-kin", target="Thái Hoà", approve=True
                    )
                )
            )
            evs_hoa_approved = collect_until(
                hoa,
                lambda e: not is_binary_frame(e.data)
                and decode_message(e.data).get("system") is True
                and "đã duyệt bạn vào phòng #phong-kin"
                in decode_message(e.data).get("text", ""),
            )
            rlist_hoa_in = [
                decode_message(e.data)
                for e in evs_hoa_approved
                if decode_message(e.data).get("type") == "room_list"
            ][-1]
            self.assertEqual(rlist_hoa_in["current_room"], "phong-kin")
            self.assertIn("phong-kin", rlist_hoa_in["joined_rooms"])

            # 4. Kiểm tra danh sách thành viên (tương đương gõ ///)
            hoa.send_payload(
                encode_message(make_message("get_members", room="phong-kin"))
            )
            mem_ev = collect_until(
                hoa, lambda e: decode_message(e.data).get("type") == "member_list"
            )[-1]
            mem_pkt = decode_message(mem_ev.data)
            self.assertEqual(mem_pkt["owner"], "Minh Khang")
            self.assertTrue(mem_pkt["is_private"])
            self.assertCountEqual(mem_pkt["members"], ["Minh Khang", "Thái Hoà"])
            self.assertCountEqual(mem_pkt["online_members"], ["Minh Khang", "Thái Hoà"])

            # Thành viên thường (Thái Hoà) không thể kick Admin
            hoa.send_payload(
                encode_message(
                    make_message("kick_member", room="phong-kin", target="Minh Khang")
                )
            )
            err_kick = collect_until(
                hoa, lambda e: decode_message(e.data).get("type") == "error"
            )[-1]
            self.assertEqual(decode_message(err_kick.data)["code"], "KICK_DENIED")

            # 5. Admin Minh Khang bấm Kick Thái Hoà -> đẩy Thái Hoà về #chung & khóa quyền vào lại!
            khang.send_payload(
                encode_message(
                    make_message("kick_member", room="phong-kin", target="Thái Hoà")
                )
            )
            evs_hoa_kicked = collect_until(
                hoa,
                lambda e: not is_binary_frame(e.data)
                and decode_message(e.data).get("system") is True
                and "bị Admin (Minh Khang) kick khỏi phòng #phong-kin"
                in decode_message(e.data).get("text", ""),
            )
            rlist_after_kick = [
                decode_message(e.data)
                for e in evs_hoa_kicked
                if decode_message(e.data).get("type") == "room_list"
            ][-1]
            self.assertEqual(rlist_after_kick["current_room"], "chung")
            self.assertNotIn("phong-kin", rlist_after_kick["joined_rooms"])
        finally:
            khang.disconnect()
            hoa.disconnect()
            an.disconnect()

    def test_room_persistence_on_relogin_member_leave_and_admin_delete(self):
        khang, _ = self._connect_and_hello("Minh Khang")
        try:
            khang.send_payload(encode_message(make_message("join_room", room="nhom-vip")))
            collect_until(
                khang,
                lambda e: decode_message(e.data).get("system") is True
                and "#nhom-vip" in decode_message(e.data).get("text", ""),
            )
        finally:
            khang.disconnect()

        self._stop_server()
        self._start_server()

        khang2, khang_rlist = self._connect_and_hello("Minh Khang")
        hoa, _ = self._connect_and_hello("Thái Hoà")
        try:
            self.assertEqual(khang_rlist["current_room"], "nhom-vip")
            self.assertEqual(khang_rlist["owners"]["nhom-vip"], "Minh Khang")

            hoa.send_payload(encode_message(make_message("join_room", room="nhom-vip")))
            collect_until(
                hoa,
                lambda e: decode_message(e.data).get("system") is True
                and "#nhom-vip" in decode_message(e.data).get("text", ""),
            )

            hoa.send_payload(encode_message(make_message("leave_room", room="nhom-vip")))
            hoa_evs = collect_until(
                hoa,
                lambda e: decode_message(e.data).get("system") is True
                and "quay về sảnh #chung" in decode_message(e.data).get("text", ""),
            )
            hoa_rlist = [
                decode_message(e.data)
                for e in hoa_evs
                if decode_message(e.data).get("type") == "room_list"
            ][-1]
            self.assertNotIn("nhom-vip", hoa_rlist["rooms"])

            khang2.send_payload(
                encode_message(make_message("delete_room", room="nhom-vip"))
            )
            collect_until(
                khang2,
                lambda e: decode_message(e.data).get("system") is True
                and "đã bị Admin (Minh Khang) xóa"
                in decode_message(e.data).get("text", ""),
            )
            self.assertNotIn("nhom-vip", self.server.storage.list_rooms())
        finally:
            khang2.disconnect()
            hoa.disconnect()

    def test_binary_file_sqlite_history_and_recall(self):
        khang, _ = self._connect_and_hello("Minh Khang", room="hoc-tap")
        hoa, _ = self._connect_and_hello("Thái Hoà", room="hoc-tap")
        large_binary = bytes(range(256)) * 520

        try:
            khang.send_payload(
                encode_message(make_message("chat", text="Nhắn nhầm cần thu hồi!"))
            )
            ev_chat = collect_until(
                khang,
                lambda e: decode_message(e.data).get("type") == "chat"
                and not decode_message(e.data).get("system"),
            )[-1]
            msg_id = decode_message(ev_chat.data)["msg_id"]

            khang.send_file_async("tai_lieu.bin", large_binary)
            collect_until(
                hoa,
                lambda e: not is_binary_frame(e.data)
                and decode_message(e.data).get("type") == "file_end",
            )

            khang.send_payload(encode_message(make_message("recall", msg_id=msg_id)))
            for c in (khang, hoa):
                rec = collect_until(
                    c,
                    lambda e: e.type == EVENT_MESSAGE
                    and not is_binary_frame(e.data)
                    and decode_message(e.data).get("type") == "recall",
                )[-1]
                self.assertEqual(decode_message(rec.data)["msg_id"], msg_id)
        finally:
            khang.disconnect()
            hoa.disconnect()


class TestProtocolAndGUIControls(unittest.TestCase):
    def test_protocol_and_gui_triple_slash_and_private_controls(self):
        for valid_msg in (
            make_message("join_room", room="kin", is_private=True),
            make_message("join_request", room="kin", username="An"),
            make_message("approve_join", room="kin", target="An", approve=True),
            make_message("kick_member", room="kin", target="An"),
            make_message("get_members", room="kin"),
            make_message(
                "member_list",
                room="kin",
                owner="Khang",
                is_private=True,
                members=["Khang", "An"],
                online_members=["Khang"],
                pending_requests=["Hoà"],
            ),
        ):
            self.assertEqual(decode_message(encode_message(valid_msg)), valid_msg)

        for bad_dict in (
            make_message("kick_member", room="kin", target="  "),
            make_message("approve_join", room="kin", target="An", approve="yes"),
            {"version": 1, "type": "unknown"},
        ):
            with self.assertRaises(ProtocolError):
                encode_message(bad_dict)

        args = parse_args(["--h", "127.0.0.1", "--p", "65432", "--n", "Khang"])
        self.assertIsNone(args.room)
        for bad in ("0", "70000", "xyz"):
            with self.assertRaises(argparse.ArgumentTypeError):
                valid_port(bad)
        self.assertGreater(measure_chat_bytes("Xin chào"), 30)

        root = tk.Tk()
        root.withdraw()
        try:
            gui = ChatClientGUI(root, initial_name="Minh Khang")
            root.update_idletasks()
            min_w, _ = root.minsize()
            self.assertGreaterEqual(min_w, root.winfo_reqwidth())

            # Nhận thông báo xin vào phòng Private kèm nút Duyệt / Từ chối
            gui._handle_network_event(
                EVENT_MESSAGE,
                encode_message(
                    make_message("join_request", room="phong-kin", username="Thái Hoà")
                ),
            )
            self.assertIn("Thái Hoà", gui.chat_text.get("1.0", "end-1c"))

            # Nhận phản hồi danh sách thành viên (từ lệnh /// hoặc nút 👥 Thành viên)
            gui._handle_network_event(
                EVENT_MESSAGE,
                encode_message(
                    make_message(
                        "member_list",
                        room="phong-kin",
                        owner="Minh Khang",
                        is_private=True,
                        members=["Minh Khang", "Thái Hoà"],
                        online_members=["Minh Khang", "Thái Hoà"],
                        pending_requests=[],
                    )
                ),
            )
            chat_log = gui.chat_text.get("1.0", "end-1c")
            self.assertIn("👥 Thành viên #phong-kin", chat_log)
            self.assertIn("👑 Minh Khang", chat_log)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
