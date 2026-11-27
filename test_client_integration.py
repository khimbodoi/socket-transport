"""Kiểm thử tích hợp toàn diện:
- Phần 1 (`transport.py`): Framing 4-byte header, giới hạn 64 KiB.
- Phần 2 (`protocol.py`): JSON control + Binary file chunks (`BINARY_MAGIC`) + gói `recall`.
- Phần 3 (`storage.py`, `chat_server.py`, `network_client.py`, `gui_client.py`):
  1. Chia phòng chat (tạo phòng mới, cô lập tin nhắn giữa các phòng).
  2. Gửi File / Hình ảnh nhị phân > 64 KiB (tự cắt mảnh 48 KiB và ráp lại nguyên vẹn từng byte).
  3. Lưu lịch sử SQLite (ngắt kết nối rồi đăng nhập lại vẫn nạp đủ cả tin nhắn cũ và file/ảnh cũ).
  4. Thu hồi tin nhắn & File/Ảnh (chặn thu hồi tin người khác, xóa sạch trong SQLite và cập nhật tại chỗ trên GUI).

Chạy toàn bộ test:
    py test_client_integration.py
"""

import argparse
import os
import queue
import tempfile
import threading
import tkinter as tk
import unittest

from chat_server import ChatRoomServer
from gui_client import ChatClientGUI, measure_chat_bytes, parse_args, valid_port
from network_client import (
    EVENT_CONNECTED,
    EVENT_FILE_SENT,
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


class TestRoomsBinaryFilesSQLiteAndRecall(unittest.TestCase):
    def setUp(self):
        fd, self.db_file = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.server = ChatRoomServer(
            host="127.0.0.1", port=0, db_path=self.db_file, quiet=True
        )
        _, self.port = self.server.bind_and_listen()
        self.srv_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.srv_thread.start()

    def tearDown(self):
        self.server.stop()
        self.srv_thread.join(timeout=2)
        for suffix in ("", "-wal", "-shm"):
            p = self.db_file + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except OSError:
                    pass

    def _connect_and_hello(self, username: str, room: str = "chung") -> SocketClient:
        c = SocketClient()
        c.connect_async("127.0.0.1", self.port)
        ev = c.events.get(timeout=3.0)
        self.assertEqual(ev.type, EVENT_CONNECTED)
        c.send_payload(encode_message(make_message("hello", username=username, room=room)))
        collect_until(
            c,
            lambda e: e.type == EVENT_MESSAGE
            and not is_binary_frame(e.data)
            and decode_message(e.data).get("type") == "chat"
            and decode_message(e.data).get("system") is True,
        )
        return c

    def test_room_isolation_and_custom_room_creation(self):
        """Tin nhắn ở phòng #do-an-nhom không bị lọt sang người đang ở phòng #chung."""
        khang = self._connect_and_hello("Minh Khang", room="chung")
        hoa = self._connect_and_hello("Thái Hoà", room="chung")
        an = self._connect_and_hello("An", room="chung")
        try:
            khang.send_payload(encode_message(make_message("join_room", room="do-an-nhom")))
            collect_until(
                khang,
                lambda e: e.type == EVENT_MESSAGE
                and not is_binary_frame(e.data)
                and decode_message(e.data).get("system") is True
                and "#do-an-nhom" in decode_message(e.data).get("text", ""),
            )

            hoa.send_payload(encode_message(make_message("join_room", room="do-an-nhom")))
            collect_until(
                hoa,
                lambda e: e.type == EVENT_MESSAGE
                and not is_binary_frame(e.data)
                and decode_message(e.data).get("system") is True
                and "#do-an-nhom" in decode_message(e.data).get("text", ""),
            )

            collect_until(
                an,
                lambda e: e.type == EVENT_MESSAGE
                and not is_binary_frame(e.data)
                and "Thái Hoà đã chuyển sang phòng #do-an-nhom"
                in decode_message(e.data).get("text", ""),
            )
            while not an.events.empty():
                an.events.get_nowait()

            khang.send_payload(
                encode_message(make_message("chat", text="Tin mật trong phòng đồ án!"))
            )

            evs_hoa = collect_until(
                hoa,
                lambda e: e.type == EVENT_MESSAGE
                and not is_binary_frame(e.data)
                and decode_message(e.data).get("type") == "chat"
                and not decode_message(e.data).get("system"),
            )
            chat_pkt = decode_message(evs_hoa[-1].data)
            self.assertEqual(chat_pkt["sender"], "Minh Khang")
            self.assertEqual(chat_pkt["room"], "do-an-nhom")
            self.assertEqual(chat_pkt["text"], "Tin mật trong phòng đồ án!")
            self.assertIsInstance(chat_pkt["msg_id"], int)

            with self.assertRaises(queue.Empty):
                an.events.get(timeout=0.15)
        finally:
            khang.disconnect()
            hoa.disconnect()
            an.disconnect()

    def test_large_binary_file_chunking_and_sqlite_history_on_relogin(self):
        """Gửi file nhị phân 130 KB (> giới hạn 64 KiB của 1 frame TCP) + Đăng nhập lại nhận đủ lịch sử SQLite."""
        khang = self._connect_and_hello("Minh Khang", room="hoc-tap")
        hoa = self._connect_and_hello("Thái Hoà", room="hoc-tap")
        large_binary = bytes(range(256)) * 520  # 133,120 bytes (~3 mảnh 48 KiB)

        try:
            khang.send_payload(
                encode_message(make_message("chat", text="Gửi tài liệu ôn tập nhé!"))
            )
            fid = khang.send_file_async("tai_lieu_mang.bin", large_binary)
            self.assertTrue(fid)

            # Người gửi nhận được cả EVENT_FILE_SENT lẫn gói file_saved kèm msg_id
            collect_until(
                khang,
                lambda e: e.type == EVENT_MESSAGE
                and not is_binary_frame(e.data)
                and decode_message(e.data).get("type") == "file_saved",
            )

            hoa_events = collect_until(
                hoa,
                lambda e: e.type == EVENT_MESSAGE
                and not is_binary_frame(e.data)
                and decode_message(e.data).get("type") == "file_end",
            )

            assembled_chunks = {}
            file_meta = None
            for ev in hoa_events:
                if ev.type != EVENT_MESSAGE:
                    continue
                if is_binary_frame(ev.data):
                    _, idx, chunk = decode_binary_chunk(ev.data)
                    assembled_chunks[idx] = chunk
                else:
                    pkt = decode_message(ev.data)
                    if pkt["type"] == "file_start":
                        file_meta = pkt

            self.assertIsNotNone(file_meta)
            self.assertEqual(file_meta["filename"], "tai_lieu_mang.bin")
            self.assertIsInstance(file_meta["msg_id"], int)
            rebuilt = b"".join(assembled_chunks[i] for i in range(file_meta["total_chunks"]))
            self.assertEqual(rebuilt, large_binary)
        finally:
            khang.disconnect()
            hoa.disconnect()

        # Người dùng thứ 3 đăng nhập sau đó -> SQLite tự phát lại cả chat & file cũ
        late_user = SocketClient()
        try:
            late_user.connect_async("127.0.0.1", self.port)
            self.assertEqual(late_user.events.get(timeout=3.0).type, EVENT_CONNECTED)
            late_user.send_payload(
                encode_message(make_message("hello", username="Sinh Viên Mới", room="hoc-tap"))
            )

            replay_events = collect_until(
                late_user,
                lambda e: e.type == EVENT_MESSAGE
                and not is_binary_frame(e.data)
                and decode_message(e.data).get("type") == "chat"
                and decode_message(e.data).get("system") is True,
            )

            history_chats = []
            history_file_chunks = {}
            history_file_meta = None

            for ev in replay_events:
                if ev.type != EVENT_MESSAGE:
                    continue
                if is_binary_frame(ev.data):
                    _, idx, chunk = decode_binary_chunk(ev.data)
                    history_file_chunks[idx] = chunk
                else:
                    pkt = decode_message(ev.data)
                    if pkt["type"] == "chat" and pkt.get("history"):
                        history_chats.append(pkt)
                    elif pkt["type"] == "file_start" and pkt.get("history"):
                        history_file_meta = pkt

            self.assertEqual(len(history_chats), 1)
            self.assertEqual(history_chats[0]["text"], "Gửi tài liệu ôn tập nhé!")
            self.assertIsNotNone(history_file_meta)
            replayed_bytes = b"".join(
                history_file_chunks[i] for i in range(history_file_meta["total_chunks"])
            )
            self.assertEqual(replayed_bytes, large_binary)
        finally:
            late_user.disconnect()

    def test_message_recall_authorization_sync_and_sqlite_wipe(self):
        """Thu hồi tin nhắn: chặn thu hồi trái phép, đồng bộ xóa trên mọi client & xóa sạch trong SQLite."""
        khang = self._connect_and_hello("Minh Khang", room="chung")
        hoa = self._connect_and_hello("Thái Hoà", room="chung")
        try:
            khang.send_payload(
                encode_message(make_message("chat", text="Nhắn nhầm mật khẩu 123456!"))
            )

            ev_khang = collect_until(
                khang,
                lambda e: e.type == EVENT_MESSAGE
                and not is_binary_frame(e.data)
                and decode_message(e.data).get("type") == "chat"
                and not decode_message(e.data).get("system"),
            )[-1]
            ev_hoa = collect_until(
                hoa,
                lambda e: e.type == EVENT_MESSAGE
                and not is_binary_frame(e.data)
                and decode_message(e.data).get("type") == "chat"
                and not decode_message(e.data).get("system"),
            )[-1]

            msg_id = decode_message(ev_khang.data)["msg_id"]
            self.assertEqual(decode_message(ev_hoa.data)["msg_id"], msg_id)

            # 1. Thái Hoà cố tình thu hồi tin của Minh Khang -> bị Server từ chối RECALL_DENIED
            hoa.send_payload(encode_message(make_message("recall", msg_id=msg_id)))
            err_ev = collect_until(
                hoa,
                lambda e: e.type == EVENT_MESSAGE
                and decode_message(e.data).get("type") == "error",
            )[-1]
            self.assertEqual(decode_message(err_ev.data)["code"], "RECALL_DENIED")

            # 2. Chính chủ Minh Khang bấm thu hồi -> cả Khang lẫn Hoà đều nhận gói recall
            khang.send_payload(encode_message(make_message("recall", msg_id=msg_id)))
            rec_khang = collect_until(
                khang,
                lambda e: e.type == EVENT_MESSAGE
                and decode_message(e.data).get("type") == "recall",
            )[-1]
            rec_hoa = collect_until(
                hoa,
                lambda e: e.type == EVENT_MESSAGE
                and decode_message(e.data).get("type") == "recall",
            )[-1]
            self.assertEqual(decode_message(rec_khang.data)["msg_id"], msg_id)
            self.assertEqual(decode_message(rec_hoa.data)["msg_id"], msg_id)
        finally:
            khang.disconnect()
            hoa.disconnect()

        # 3. Người mới đăng nhập lại sau đó chỉ thấy trạng thái recalled=True, KHÔNG thấy mật khẩu cũ
        viewer = self._connect_and_hello("Người Xem Sau", room="chung")
        try:
            hist = self.server.storage.get_recent_history("chung")
            self.assertEqual(len(hist), 1)
            self.assertTrue(hist[0]["recalled"])
            self.assertEqual(hist[0]["content"], "")
        finally:
            viewer.disconnect()


class TestProtocolValidation(unittest.TestCase):
    def test_protocol_schema_and_binary_validation(self):
        msg = make_message("chat", text="Xin chào Việt Nam 🇻🇳")
        self.assertEqual(decode_message(encode_message(msg)), msg)

        recall_msg = make_message("recall", msg_id=10)
        self.assertEqual(decode_message(encode_message(recall_msg)), recall_msg)

        for bad_payload in (b"\xff\xfe", b'{"type":', "not bytes"):
            with self.assertRaises(ProtocolError):
                decode_message(bad_payload)  # type: ignore[arg-type]

        for bad_dict in (
            {"version": 1, "type": "chat"},
            {"version": 1, "type": "unknown"},
            make_message("hello", username="   "),
            make_message("join_room", room="   "),
            make_message("recall", msg_id=0),
            make_message("recall", msg_id=True),
        ):
            with self.assertRaises(ProtocolError):
                encode_message(bad_dict)

        chunk_frame = encode_binary_chunk("f01", 2, b"\x00\x01\xff")
        self.assertTrue(is_binary_frame(chunk_frame))
        self.assertEqual(decode_binary_chunk(chunk_frame), ("f01", 2, b"\x00\x01\xff"))


class TestGUIWithRoomsImageAndInPlaceRecall(unittest.TestCase):
    def test_gui_renders_and_recalls_message_and_image_in_place(self):
        args = parse_args(["--h", "127.0.0.1", "--p", "65432", "--n", "Khang", "--r", "hoc-tap"])
        self.assertEqual(args.room, "hoc-tap")
        for bad in ("0", "70000", "xyz"):
            with self.assertRaises(argparse.ArgumentTypeError):
                valid_port(bad)
        self.assertGreater(measure_chat_bytes("Xin chào"), 30)

        root = tk.Tk()
        root.withdraw()
        try:
            gui = ChatClientGUI(root, initial_name="Minh Khang", initial_room="chung")
            root.update_idletasks()
            min_w, _ = root.minsize()
            self.assertGreaterEqual(min_w, root.winfo_reqwidth())

            gui.input_text.insert("1.0", "Đang gõ câu hỏi...")

            # Tin nhắn số 1 (giữ lại)
            gui._handle_network_event(
                EVENT_MESSAGE,
                encode_message(
                    make_message(
                        "chat",
                        text="Tin số 1 giữ nguyên",
                        sender="Minh Khang",
                        room="chung",
                        timestamp="10:00:01",
                        msg_id=1,
                    )
                ),
            )
            # Tin nhắn số 2 (sẽ bị thu hồi)
            gui._handle_network_event(
                EVENT_MESSAGE,
                encode_message(
                    make_message(
                        "chat",
                        text="Bí mật cần thu hồi gấp!",
                        sender="Thái Hoà",
                        room="chung",
                        timestamp="10:00:02",
                        msg_id=2,
                    )
                ),
            )
            # Tin nhắn số 3 (ảnh PNG sẽ bị thu hồi)
            gui._handle_network_event(
                EVENT_MESSAGE,
                encode_message(
                    make_message(
                        "file_start",
                        file_id="img03",
                        filename="anh_nham.png",
                        size=len(TINY_PNG_BYTES),
                        total_chunks=1,
                        sender="Thái Hoà",
                        room="chung",
                        timestamp="10:00:03",
                        msg_id=3,
                    )
                ),
            )
            gui._handle_network_event(
                EVENT_MESSAGE, encode_binary_chunk("img03", 0, TINY_PNG_BYTES)
            )
            gui._handle_network_event(
                EVENT_MESSAGE, encode_message(make_message("file_end", file_id="img03"))
            )

            before_recall = gui.chat_text.get("1.0", "end-1c")
            self.assertIn("Bí mật cần thu hồi gấp!", before_recall)
            self.assertIn("anh_nham.png", before_recall)

            # Thu hồi cả tin số 2 lẫn ảnh số 3 ngay tại chỗ!
            gui._handle_network_event(
                EVENT_MESSAGE,
                encode_message(
                    make_message("recall", msg_id=2, room="chung", sender="Thái Hoà")
                ),
            )
            gui._handle_network_event(
                EVENT_MESSAGE,
                encode_message(
                    make_message("recall", msg_id=3, room="chung", sender="Thái Hoà")
                ),
            )

            after_recall = gui.chat_text.get("1.0", "end-1c")
            # Tin số 1 và nội dung đang gõ vẫn còn nguyên; tin số 2 và ảnh số 3 đã biến mất!
            self.assertEqual(gui._get_input_content(), "Đang gõ câu hỏi...")
            self.assertIn("Tin số 1 giữ nguyên", after_recall)
            self.assertNotIn("Bí mật cần thu hồi gấp!", after_recall)
            self.assertNotIn("anh_nham.png", after_recall)
            self.assertEqual(after_recall.count("🚫 Tin nhắn đã được thu hồi"), 2)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
