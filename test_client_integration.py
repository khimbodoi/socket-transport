"""Kiểm thử tích hợp toàn diện:
- Phần 1 (`transport.py`): Framing 4-byte header, giới hạn 64 KiB.
- Phần 2 (`protocol.py`): JSON control + Binary file chunks (`BINARY_MAGIC`).
- Phần 3 (`storage.py`, `chat_server.py`, `network_client.py`, `gui_client.py`):
  1. Chia phòng chat (tạo phòng mới, cô lập tin nhắn giữa các phòng).
  2. Gửi File / Hình ảnh nhị phân > 64 KiB (tự cắt mảnh 48 KiB và ráp lại nguyên vẹn từng byte).
  3. Lưu lịch sử SQLite (ngắt kết nối rồi đăng nhập lại vẫn nạp đủ cả tin nhắn cũ và file/ảnh cũ).

Chạy toàn bộ test:
    py -m unittest -v
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
    EVENT_DISCONNECTED,
    EVENT_ERROR,
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
from transport import MAX_SIZE

# Mẫu ảnh PNG 1x1 hợp lệ dùng để kiểm thử hiển thị ảnh nội tuyến trên Tkinter
TINY_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0"
    b"\x00\x00\x03\x01\x01\x00\xc9\xfe\x92\xef\x00\x00\x00\x00IEND\xaeB`\x82"
)


def collect_until(client: SocketClient, predicate, timeout: float = 4.0):
    """Đọc các sự kiện từ client cho tới khi thỏa điều kiện predicate."""
    collected = []
    while True:
        ev = client.events.get(timeout=timeout)
        collected.append(ev)
        if predicate(ev):
            return collected


class TestRoomsBinaryFilesAndSQLiteHistory(unittest.TestCase):
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
        # Chờ cho tới khi nhận xong thông báo hệ thống xác nhận vào phòng
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
            # Xóa sạch sự kiện thông báo join ban đầu
            while not khang.events.empty():
                khang.events.get_nowait()
            while not hoa.events.empty():
                hoa.events.get_nowait()

            # Minh Khang và Thái Hoà tạo & chuyển sang phòng mới '#do-an-nhom'
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

            # An ở #chung nhận thông báo hệ thống khi Thái Hoà rời #chung sang phòng khác
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

            # Thái Hoà (cùng phòng #do-an-nhom) nhận được ngay
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

            # An ở #chung hoàn toàn không nhận được tin nhắn này
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

        # Tạo dữ liệu nhị phân 130 KB (gồm đủ mọi giá trị byte 0..255, không phải chuỗi UTF-8)
        large_binary = bytes(range(256)) * 520  # 133,120 bytes (~3 mảnh 48 KiB)

        try:
            khang.send_payload(
                encode_message(make_message("chat", text="Gửi tài liệu ôn tập nhé!"))
            )
            khang.send_file_async("tai_lieu_mang.bin", large_binary)

            # Khang nhận xác nhận gửi xong EVENT_FILE_SENT
            collect_until(khang, lambda e: e.type == EVENT_FILE_SENT)

            # Thái Hoà nhận đủ cả tin chat lẫn file nhị phân 133,120 bytes
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
            self.assertEqual(file_meta["size"], len(large_binary))
            rebuilt = b"".join(assembled_chunks[i] for i in range(file_meta["total_chunks"]))
            self.assertEqual(rebuilt, large_binary)
        finally:
            khang.disconnect()
            hoa.disconnect()

        # Người dùng thứ 3 đăng nhập SAU ĐÓ vào phòng #hoc-tap -> SQLite tự phát lại cả chat & file cũ!
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
            self.assertEqual(history_chats[0]["sender"], "Minh Khang")

            self.assertIsNotNone(history_file_meta)
            self.assertEqual(history_file_meta["filename"], "tai_lieu_mang.bin")
            replayed_bytes = b"".join(
                history_file_chunks[i] for i in range(history_file_meta["total_chunks"])
            )
            self.assertEqual(replayed_bytes, large_binary)
        finally:
            late_user.disconnect()


class TestProtocolValidation(unittest.TestCase):
    def test_protocol_schema_and_binary_validation(self):
        msg = make_message("chat", text="Xin chào Việt Nam 🇻🇳")
        self.assertEqual(decode_message(encode_message(msg)), msg)

        for bad_payload in (b"\xff\xfe", b'{"type":', "not bytes"):
            with self.assertRaises(ProtocolError):
                decode_message(bad_payload)  # type: ignore[arg-type]

        for bad_dict in (
            {"version": 1, "type": "chat"},
            {"version": 1, "type": "unknown"},
            make_message("hello", username="   "),
            make_message("join_room", room="   "),
        ):
            with self.assertRaises(ProtocolError):
                encode_message(bad_dict)

        chunk_frame = encode_binary_chunk("f01", 2, b"\x00\x01\xff")
        self.assertTrue(is_binary_frame(chunk_frame))
        self.assertEqual(decode_binary_chunk(chunk_frame), ("f01", 2, b"\x00\x01\xff"))


class TestGUIWithRoomsAndInlineImage(unittest.TestCase):
    def test_gui_renders_rooms_history_and_inline_png_without_losing_draft(self):
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

            # Cập nhật danh sách phòng từ server
            gui._handle_network_event(
                EVENT_MESSAGE,
                encode_message(
                    make_message(
                        "room_list",
                        rooms=["chung", "hoc-tap", "bao-ve-do-an"],
                        current_room="bao-ve-do-an",
                    )
                ),
            )
            self.assertEqual(gui.current_room, "bao-ve-do-an")

            # Đang gõ dở tin nhắn thì nhận được 1 tin lịch sử + 1 ảnh PNG nhị phân
            gui.input_text.insert("1.0", "Đang gõ câu hỏi...")
            gui._handle_network_event(
                EVENT_MESSAGE,
                encode_message(
                    make_message(
                        "chat",
                        text="Tin nhắn cũ từ hôm qua",
                        sender="Thái Hoà",
                        room="bao-ve-do-an",
                        timestamp="09:15:00",
                        history=True,
                    )
                ),
            )

            gui._handle_network_event(
                EVENT_MESSAGE,
                encode_message(
                    make_message(
                        "file_start",
                        file_id="img01",
                        filename="so_do_mang.png",
                        size=len(TINY_PNG_BYTES),
                        total_chunks=1,
                        sender="Thái Hoà",
                        room="bao-ve-do-an",
                        timestamp="09:16:00",
                    )
                ),
            )
            gui._handle_network_event(
                EVENT_MESSAGE,
                encode_binary_chunk("img01", 0, TINY_PNG_BYTES),
            )
            gui._handle_network_event(
                EVENT_MESSAGE,
                encode_message(make_message("file_end", file_id="img01")),
            )

            # Kiểm tra ô nhập không bị mất chữ, khung chat có cả nhãn [Lịch sử] lẫn file ảnh PNG
            self.assertEqual(gui._get_input_content(), "Đang gõ câu hỏi...")
            chat_content = gui.chat_text.get("1.0", "end-1c")
            self.assertIn("[Lịch sử 09:15:00]", chat_content)
            self.assertIn("Tin nhắn cũ từ hôm qua", chat_content)
            self.assertIn("so_do_mang.png", chat_content)
            # Nút Lưu về máy + PhotoImage preview đều đã được nhúng vào khung chat
            self.assertGreaterEqual(len(gui._widget_refs), 2)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
