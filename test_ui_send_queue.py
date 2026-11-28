"""D07 regression checks: Tk callbacks must not wait for socket writes."""

import queue
import socket
import threading
import time
import tempfile
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch

from gui_client import ChatClientGUI
from network_client import EVENT_CONNECTED, EVENT_DISCONNECTED
from protocol import decode_message
from transport import recv_msg


class TestUISendQueue(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.gui = ChatClientGUI(self.root, initial_name="QueueTest")
        self.client = self.gui.client
        self.sock, self.peer = socket.socketpair()
        self.peer.settimeout(2)
        self.client._sock = self.sock
        self.client._connected = True
        self.client._outgoing = queue.Queue(maxsize=128)
        self.gui._session_ready = True
        self.gui.current_room = "demo"
        self.workers = []

    def tearDown(self):
        self.client.disconnect()
        if self.client._send_lock.locked():
            self.client._send_lock.release()
        for worker in self.workers:
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.peer.close()
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.root.destroy()

    def start_writer(self):
        worker = threading.Thread(
            target=self.client._send_loop,
            args=(self.sock, self.client._connection_id, self.client._outgoing),
            daemon=True,
        )
        self.workers.append(worker)
        worker.start()

    def test_chat_keeps_tk_responsive_and_delivers_after_lock_release(self):
        self.client._send_lock.acquire()
        self.start_writer()
        heartbeat = []
        self.root.after(20, lambda: heartbeat.append(time.monotonic()))
        self.gui.input_text.insert("1.0", "Xin chào D07")
        timer = threading.Timer(0.4, self.client._send_lock.release)
        started = time.monotonic()
        timer.start()
        try:
            self.gui.send_current_message()
            elapsed = time.monotonic() - started
            while time.monotonic() - started < 0.1:
                self.root.update()
                time.sleep(0.002)
            self.assertLess(elapsed, 0.15)
            self.assertTrue(heartbeat)
            self.assertLess(heartbeat[0] - started, 0.2)
        finally:
            timer.join(2)
        message = decode_message(recv_msg(self.peer))
        self.assertEqual(message["text"], "Xin chào D07")

    def test_control_callbacks_only_enqueue_under_busy_send_lock(self):
        self.client._send_lock.acquire()
        timer = threading.Timer(0.4, self.client._send_lock.release)
        timer.start()
        actions = [
            ("join_room", lambda: self.gui._request_switch_room("other")),
            ("get_members", lambda: self.gui.request_members_check(True)),
            ("approve_join", lambda: self.gui.send_approve_decision("demo", "An", True)),
            ("kick_member", lambda: self.gui.send_kick_member("demo", "An")),
            ("leave_room", self.gui.leave_current_room),
            ("delete_room", lambda: self.gui.delete_current_room(confirm=False)),
            ("recall", lambda: self.gui.request_recall(1)),
        ]
        try:
            for expected, action in actions:
                started = time.monotonic()
                action()
                self.assertLess(time.monotonic() - started, 0.15, expected)
                packet = decode_message(self.client._outgoing.get_nowait())
                self.assertEqual(packet["type"], expected)
        finally:
            timer.join(2)

    def test_full_queue_preserves_input_dialog_flag_and_approval_buttons(self):
        self.client._outgoing = queue.Queue(maxsize=1)
        self.client._outgoing.put(b"occupied")
        self.gui.input_text.insert("1.0", "keep me")
        self.gui.send_current_message()
        self.assertEqual(self.gui._get_input_content(), "keep me")
        self.gui._open_dialog_on_next_members = False
        self.gui.request_members_check(True)
        self.assertFalse(self.gui._open_dialog_on_next_members)
        buttons = (ttk.Button(self.root), ttk.Button(self.root))
        self.gui.send_approve_decision("demo", "An", True, buttons)
        self.assertTrue(all(str(button["state"]) != "disabled" for button in buttons))

    def test_disconnect_ignores_busy_send_lock_and_discards_pending_frames(self):
        self.client._send_lock.acquire()
        self.client.send_payload_async(b"pending")
        self.start_writer()
        started = time.monotonic()
        self.gui._graceful_disconnect()
        self.assertLess(time.monotonic() - started, 0.15)
        self.assertFalse(self.gui._session_ready)
        self.assertIsNone(self.client._outgoing)
        self.client._send_lock.release()
        self.workers[0].join(2)
        self.assertEqual(self.peer.recv(1), b"")

    def test_hello_is_queued_and_requests_wait_for_server_ack(self):
        self.client._send_lock.acquire()
        timer = threading.Timer(0.4, self.client._send_lock.release)
        timer.start()
        started = time.monotonic()
        try:
            self.gui._handle_network_event(EVENT_CONNECTED, "local")
            self.assertLess(time.monotonic() - started, 0.15)
        finally:
            timer.join(2)
        self.assertFalse(self.gui._session_ready)
        self.assertEqual(str(self.gui.send_btn["state"]), "disabled")
        self.assertEqual(str(self.gui.attach_btn["state"]), "disabled")
        actions = [
            lambda: self.gui._request_switch_room("other"),
            self.gui.request_members_check,
            lambda: self.gui.send_approve_decision("demo", "An", True),
            lambda: self.gui.send_kick_member("demo", "An"),
            self.gui.leave_current_room,
            lambda: self.gui.delete_current_room(confirm=False),
            lambda: self.gui.request_recall(1),
        ]
        for action in actions:
            action()
        self.gui.input_text.insert("1.0", "after hello")
        self.gui.send_current_message()
        with patch("gui_client.filedialog.askopenfilename") as dialog:
            self.gui.choose_and_send_file()
            dialog.assert_not_called()
        self.assertEqual(self.client._outgoing.qsize(), 1)
        self.start_writer()
        self.assertEqual(decode_message(recv_msg(self.peer))["type"], "hello")
        self.gui._handle_decoded_json_message({"type": "hello", "username": "QueueTest"})
        self.assertTrue(self.gui._session_ready)
        self.assertEqual(str(self.gui.send_btn["state"]), "normal")
        self.gui.send_current_message()
        self.assertEqual(decode_message(recv_msg(self.peer))["text"], "after hello")
        self.gui._handle_network_event(EVENT_DISCONNECTED, "closed")
        self.assertFalse(self.gui._session_ready)

    def test_hello_enqueue_failure_closes_unregistered_session(self):
        with patch.object(self.client, "send_payload_async", side_effect=ValueError("full")):
            self.gui._handle_network_event(EVENT_CONNECTED, "local")
        self.assertFalse(self.client.is_connected)
        self.assertFalse(self.gui._session_ready)

    def test_file_dialog_cannot_send_into_a_changed_session(self):
        with tempfile.NamedTemporaryFile(suffix=".txt") as file:
            file.write(b"temporary demo")
            file.flush()

            def select_then_disconnect(**kwargs):
                self.gui._graceful_disconnect()
                return file.name

            with patch("gui_client.filedialog.askopenfilename", side_effect=select_then_disconnect), patch.object(self.client, "send_file_async") as send:
                self.gui.choose_and_send_file()
                send.assert_not_called()


if __name__ == "__main__":
    unittest.main()
