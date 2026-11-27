"""Regression tests using local TCP sockets and temporary SQLite databases.

Run: python -m unittest -v test_client_integration test_regression
"""

import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import patch
from contextlib import contextmanager

from chat_server import ChatRoomServer
from gui_client import ChatClientGUI
from network_client import SocketClient
from protocol import (
    FILE_CHUNK_SIZE, MAX_FILE_SIZE, ProtocolError, decode_binary_chunk,
    decode_message, encode_binary_chunk, encode_message, is_binary_frame, make_message,
)
from transport import MAX_SIZE, recv_msg, send_msg
from test_client_integration import TINY_PNG_BYTES


class Peer:
    def __init__(self, port):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=3)

    def send(self, kind, **fields):
        send_msg(self.sock, encode_message(make_message(kind, **fields)))

    def read(self):
        raw = recv_msg(self.sock)
        if raw is None:
            raise AssertionError("Unexpected EOF")
        if is_binary_frame(raw):
            fid, idx, data = decode_binary_chunk(raw)
            return {"type": "binary", "file_id": fid, "index": idx, "data": data}
        return decode_message(raw)

    def wait(self, kind, **fields):
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            msg = self.read()
            if msg["type"] == kind and all(msg.get(k) == v for k, v in fields.items()):
                return msg
        raise AssertionError(f"Missing {kind}: {fields}")

    def join(self, room, private=False):
        self.send("join_room", room=room, is_private=private)
        self.wait("room_list", current_room=room)
        self.wait("chat", system=True, room=room)

    def upload(self, data, name="sample.bin", fid="test-file"):
        chunks = [data[i:i + FILE_CHUNK_SIZE] for i in range(0, len(data), FILE_CHUNK_SIZE)]
        self.send("file_start", file_id=fid, filename=name, size=len(data), total_chunks=len(chunks))
        for idx, chunk in enumerate(chunks):
            send_msg(self.sock, encode_binary_chunk(fid, idx, chunk))
        self.send("file_end", file_id=fid)
        return self.wait("file_saved", file_id=fid)

    def download(self, **fields):
        start = self.wait("file_start", **fields)
        chunks = {}
        while True:
            msg = self.read()
            if msg.get("file_id") != start["file_id"]:
                continue
            if msg["type"] == "file_end":
                break
            if msg["type"] == "binary":
                chunks[msg["index"]] = msg["data"]
        data = b"".join(chunks[i] for i in range(start["total_chunks"]))
        if len(data) != start["size"]:
            raise AssertionError("Downloaded length differs from declared size")
        return start, data

    def close(self):
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class TestChatRegression(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.peers = []
        self.start_server()

    def start_server(self):
        self.server = ChatRoomServer(port=0, db_path=str(Path(self.temp.name) / "chat.db"), quiet=True)
        _, self.port = self.server.bind_and_listen()
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()

    def stop_server(self):
        self.server.stop()
        self.worker.join(3)
        self.assertFalse(self.worker.is_alive(), "Server accept thread failed to stop")

    def tearDown(self):
        for peer in self.peers:
            peer.close()
        self.stop_server()
        self.temp.cleanup()

    def peer(self, name=None, room=None):
        peer = Peer(self.port)
        self.peers.append(peer)
        if name is not None:
            fields = {"username": name}
            if room:
                fields["room"] = room
            peer.send("hello", **fields)
            peer.wait("hello")
            peer.wait("room_list")
            peer.wait("chat", system=True)
        return peer

    def test_multi_client_unicode_isolation_and_identity(self):
        a, b, c = self.peer("An"), self.peer("Bình"), self.peer("Chi", "other")
        a.send("chat", text="Xin chào 🌏", sender="fake", room="other")
        for p in (a, b):
            msg = p.wait("chat", text="Xin chào 🌏")
            self.assertEqual((msg["sender"], msg["room"]), ("An", "chung"))
        self.assertEqual(self.server.storage.get_recent_history("other"), [])
        c.send("chat", text="separate")
        c.wait("chat", text="separate", room="other")

    def test_binary_exact_bytes_zero_max_and_restart_history(self):
        a, b = self.peer("An"), self.peer("Bình")
        for data in (b"", bytes(range(256)) * 520, b"z" * MAX_FILE_SIZE, TINY_PNG_BYTES):
            saved = a.upload(data)
            _, actual = b.download(msg_id=saved["msg_id"])
            self.assertEqual(hashlib.sha256(actual).digest(), hashlib.sha256(data).digest())
        a.send("chat", text="Persist after restart")
        a.wait("chat", text="Persist after restart")
        for p in self.peers:
            p.close()
        self.peers.clear()
        self.stop_server()
        self.start_server()
        reconnected = self.peer("Chi")
        # peer() consumed the replay; inspect durable SQLite independently too.
        rows = self.server.storage.get_recent_history("chung")
        self.assertEqual(rows[-1]["content"], "Persist after restart")
        reconnected.send("join_room", room="other")
        reconnected.wait("room_list", current_room="other")
        reconnected.send("join_room", room="chung")
        reconnected.wait("room_list", current_room="chung")
        _, empty = reconnected.download(history=True)
        self.assertEqual(empty, b"")
        _, binary = reconnected.download(history=True)
        self.assertEqual(binary, bytes(range(256)) * 520)
        _, max_file = reconnected.download(history=True)
        self.assertEqual(max_file, b"z" * MAX_FILE_SIZE)
        _, image = reconnected.download(history=True)
        self.assertEqual(image, TINY_PNG_BYTES)
        reconnected.wait("chat", history=True, text="Persist after restart")

    def test_recall_text_and_file_permissions_and_history(self):
        a, b = self.peer("An"), self.peer("Bình")
        a.send("chat", text="remove me")
        text_id = a.wait("chat", text="remove me")["msg_id"]
        file_id = a.upload(TINY_PNG_BYTES, name="tiny.png")["msg_id"]
        b.download(msg_id=file_id)
        for mid in (text_id, file_id):
            b.send("recall", msg_id=mid)
            b.wait("error", code="RECALL_DENIED")
            a.send("recall", msg_id=mid)
            for p in (a, b):
                p.wait("recall", msg_id=mid)
        for row in self.server.storage.get_recent_history("chung"):
            self.assertTrue(row["recalled"])
            self.assertEqual(row["content"], "")
            self.assertIsNone(row["file_data"])
        c = self.peer("Chi")
        c.send("chat", text="still works")
        c.wait("chat", text="still works")

    def test_private_reject_reapply_kick_and_delete_permissions(self):
        a, b = self.peer("An"), self.peer("Bình")
        a.join("private", private=True)
        for approve in (False, True):
            b.send("join_room", room="private")
            a.wait("join_request", username="Bình")
            a.send("approve_join", room="private", target="Bình", approve=approve)
            a.wait("member_list", room="private")
            if approve:
                b.wait("room_list", current_room="private")
        b.send("delete_room", room="private")
        b.wait("error", code="DELETE_DENIED")
        a.send("kick_member", room="private", target="Bình")
        b.wait("room_list", current_room="chung")
        b.send("join_room", room="private")
        a.wait("join_request", username="Bình")
        b.send("get_members", room="private")
        b.wait("error", code="PRIVATE_ROOM")
        a.send("delete_room", room="private")
        a.wait("room_list", current_room="chung")
        self.assertNotIn("private", self.server.storage.list_rooms())

    def test_bad_json_and_oversized_frame_recovery(self):
        bad = self.peer("Bad")
        send_msg(bad.sock, b"not-json")
        bad.wait("error", code="BAD_MESSAGE")
        bad.sock.sendall((MAX_SIZE + 1).to_bytes(4, "big"))
        self.assertIsNone(recv_msg(bad.sock))
        good = self.peer("Good")
        good.send("chat", text="server alive")
        good.wait("chat", text="server alive")

    def test_largest_client_chat_is_rejected_without_disconnect(self):
        a = self.peer("An")
        text = "a" * (MAX_SIZE - len(encode_message(make_message("chat", text=""))))
        a.send("chat", text=text)
        a.wait("error", code="MESSAGE_TOO_LARGE")
        self.assertEqual(self.server.storage.get_recent_history("chung"), [])
        a.send("chat", text="recovered")
        a.wait("chat", text="recovered")

    def test_hello_required_and_cannot_change_identity_mid_session(self):
        a = self.peer()
        a.send("chat", text="before hello")
        a.wait("error", code="NEED_HELLO")
        self.assertEqual(self.server.storage.get_recent_history("chung"), [])
        a.send("hello", username="An")
        a.wait("hello", username="An")
        a.send("hello", username="Other")
        a.wait("error", code="ALREADY_HELLO")
        a.send("chat", text="same identity")
        a.wait("chat", text="same identity", sender="An")

    def test_file_cannot_finish_in_room_after_kick(self):
        a, b = self.peer("An"), self.peer("Bình")
        a.join("upload-room")
        b.join("upload-room")
        b.send("file_start", file_id="pending", filename="test.bin", size=1, total_chunks=1)
        b.send("get_members", room="upload-room")
        b.wait("member_list")  # Barrier: file_start has been processed.
        a.send("kick_member", room="upload-room", target="Bình")
        b.wait("room_list", current_room="chung")
        send_msg(b.sock, encode_binary_chunk("pending", 0, b"X"))
        b.send("file_end", file_id="pending")
        b.send("get_members", room="chung")
        b.wait("member_list", room="chung")
        self.assertEqual(self.server.storage.get_recent_history("upload-room"), [])


class TestTransportAndSchema(unittest.TestCase):
    def test_split_header_and_coalesced_frames_empty_and_eof(self):
        left, right = socket.socketpair()
        with left, right:
            right.settimeout(2)
            raw = (3).to_bytes(4, "big") + b"abc" + bytes(4) + (2).to_bytes(4, "big") + b"hi"
            def write():
                for byte in raw[:5]:
                    left.sendall(bytes([byte]))
                left.sendall(raw[5:])
                left.shutdown(socket.SHUT_WR)
            worker = threading.Thread(target=write)
            worker.start()
            self.assertEqual([recv_msg(right), recv_msg(right), recv_msg(right)], [b"abc", b"", b"hi"])
            self.assertIsNone(recv_msg(right))
            worker.join(2)

    def test_truncated_header_and_body(self):
        for raw in (b"\0\0", (5).to_bytes(4, "big") + b"ab"):
            with self.subTest(raw=raw), _pair() as pair:
                left, right = pair
                left.sendall(raw)
                left.shutdown(socket.SHUT_WR)
                with self.assertRaises(ConnectionError):
                    recv_msg(right)

    def test_schema_unhashable_type_is_protocol_error(self):
        for value in ([], {}):
            with self.subTest(value=value), self.assertRaises(ProtocolError):
                decode_message(json.dumps({"version": 1, "type": value}).encode())

    def test_file_and_payload_limits(self):
        client = SocketClient()
        with self.assertRaises(ValueError):
            client.send_file_async("huge.bin", b"x" * (MAX_FILE_SIZE + 1))
        with self.assertRaises(ProtocolError):
            encode_message(make_message("file_start", file_id="x", filename="x", size=MAX_FILE_SIZE + 1, total_chunks=1))
        with self.assertRaises(ProtocolError):
            encode_message(make_message("chat", text="x" * MAX_SIZE))
        for count in (0, 2**32):
            with self.subTest(count=count), self.assertRaises(ProtocolError):
                encode_message(make_message("file_start", file_id="x", filename="x", size=1, total_chunks=count))
        with self.assertRaises(ProtocolError):
            encode_binary_chunk("x", 2**32, b"a")


@contextmanager
def _pair():
    left, right = socket.socketpair()
    try:
        right.settimeout(2)
        yield left, right
    finally:
        left.close()
        right.close()


class TestGUIRegression(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.gui = ChatClientGUI(self.root, initial_name="An")

    def tearDown(self):
        for callback in self.root.tk.call("after", "info"):
            self.root.tk.call("after", "cancel", callback)
        self.gui.on_close()

    def test_private_creation_requires_connection(self):
        self.gui._request_switch_room("secret", is_private=True)
        self.assertNotEqual(self.gui.current_room, "secret")
        self.assertIsNone(self.gui._explicit_connect_room)
        self.assertIn("kết nối", self.gui.chat_text.get("1.0", "end").lower())

    def test_preview_save_and_recall_widgets(self):
        self.assertIsNotNone(self.gui._try_build_image_preview("tiny.png", TINY_PNG_BYTES))
        self.gui.append_file_message("An", "tiny.png", TINY_PNG_BYTES, "12:00:00", is_self=True, raw_sender="An", msg_id=1)
        self.assertTrue(self.gui.chat_text.image_names())
        self.gui.apply_message_recall(1, "An", "12:00:00")
        self.assertNotIn("tiny.png", self.gui.chat_text.get("1.0", "end"))
        self.assertFalse(self.gui.chat_text.image_names())
        with tempfile.TemporaryDirectory() as td:
            dest = Path(td) / "saved.png"
            with patch("gui_client.filedialog.asksaveasfilename", return_value=str(dest)):
                self.gui._save_received_file("tiny.png", TINY_PNG_BYTES)
            self.assertEqual(dest.read_bytes(), TINY_PNG_BYTES)

    def test_two_live_guis_chat_file_recall_and_reconnect(self):
        with tempfile.TemporaryDirectory() as td:
            server = ChatRoomServer(port=0, db_path=str(Path(td) / "chat.db"), quiet=True)
            _, port = server.bind_and_listen()
            worker = threading.Thread(target=server.serve_forever, daemon=True)
            worker.start()
            second_window = tk.Toplevel(self.root)
            second_window.withdraw()
            other = ChatClientGUI(second_window, initial_port=port, initial_name="Bình")
            callback_errors = []
            self.root.report_callback_exception = lambda *err: callback_errors.append(err)
            def pump_until(predicate):
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    self.root.update()
                    if predicate():
                        return
                    time.sleep(0.01)
                self.fail("GUI did not reach the expected state")
            try:
                self.gui.port_var.set(str(port))
                for gui in (self.gui, other):
                    gui.toggle_connection()
                pump_until(lambda: all("Đang ở phòng" in g.chat_text.get("1.0", "end") for g in (self.gui, other)))
                self.gui.input_text.insert("1.0", "Gửi từ giao diện 🌏")
                self.gui.send_current_message()
                pump_until(lambda: "Gửi từ giao diện 🌏" in other.chat_text.get("1.0", "end"))
                image_path = Path(td) / "live.png"
                image_path.write_bytes(TINY_PNG_BYTES)
                with patch("gui_client.filedialog.askopenfilename", return_value=str(image_path)):
                    self.gui.choose_and_send_file()
                pump_until(lambda: "live.png" in other.chat_text.get("1.0", "end"))
                pump_until(lambda: len(self.gui._rendered_meta) == 2)
                self.assertTrue(other.chat_text.image_names())
                mid = max(self.gui._rendered_meta)
                self.gui.request_recall(mid)
                pump_until(lambda: "live.png" not in other.chat_text.get("1.0", "end"))
                self.assertIn("đã được thu hồi", other.chat_text.get("1.0", "end"))
                self.assertFalse(other.chat_text.image_names())
                other._graceful_disconnect()
                pump_until(lambda: not other.client.is_connected)
                other.toggle_connection()
                pump_until(lambda: "Gửi từ giao diện 🌏" in other.chat_text.get("1.0", "end"))
                self.assertIn("Lịch sử", other.chat_text.get("1.0", "end"))
                self.assertEqual(callback_errors, [])
            finally:
                other._graceful_disconnect()
                self.gui._graceful_disconnect()
                for callback in self.root.tk.call("after", "info"):
                    self.root.tk.call("after", "cancel", callback)
                server.stop()
                worker.join(3)
                second_window.destroy()


class TestLegacyEcho(unittest.TestCase):
    def test_cli_unicode_empty_and_quit(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        folder = Path(__file__).resolve().parent
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        server = subprocess.Popen(
            [sys.executable, str(folder / "server.py"), "--p", str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
        )
        try:
            deadline = time.monotonic() + 5
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                        break
                except OSError:
                    if server.poll() is not None or time.monotonic() > deadline:
                        self.fail("Echo server did not start")
                    time.sleep(0.02)
            result = subprocess.run(
                [sys.executable, str(folder / "client.py"), "--h", "127.0.0.1", "--p", str(port)],
                input="Xin chào\n\n/quit\n", text=True, encoding="utf-8", env=env,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=8,
            )
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("Server trả lại: Xin chào", result.stdout)
            self.assertEqual(result.stdout.count("Server trả lại:"), 2)
            self.assertNotIn("Traceback", result.stdout)
        finally:
            server.terminate()
            server.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
