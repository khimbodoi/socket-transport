"""D03: late connect results must not change a newer connection.

Uses two real loopback listeners; events control completion order without
depending on network delays. Does not need a running chat server or database.
"""

from contextlib import ExitStack
import queue
import socket
import threading
import time
import unittest
from unittest.mock import patch

from network_client import EVENT_CONNECTED, EVENT_ERROR, SocketClient


class TestConnectionGeneration(unittest.TestCase):
    def check_cancelled_attempt(self, failure=None):
        client = SocketClient()
        old_ready = threading.Event()
        release_old = threading.Event()
        new_ready = threading.Event()
        release_new = threading.Event()
        old_done = threading.Event()
        workers = []
        sockets = []
        original_connect = socket.create_connection
        original_worker = client._connect_worker

        def wait_for(predicate):
            deadline = time.monotonic() + 3
            while not predicate():
                if time.monotonic() >= deadline:
                    self.fail("Timed out waiting for connection state")
                time.sleep(0.005)

        with ExitStack() as stack:
            endpoints = []
            for _ in range(2):
                listener = stack.enter_context(socket.socket())
                listener.bind(("127.0.0.1", 0))
                listener.listen()
                listener.settimeout(3)
                endpoints.append(listener.getsockname())

            def controlled_connect(endpoint, timeout=None):
                if endpoint == endpoints[0] and failure is not None:
                    old_ready.set()
                    if not release_old.wait(3):
                        raise TimeoutError("Test did not release old attempt")
                    raise failure
                sock = original_connect(endpoint, timeout=timeout)
                sockets.append(sock)
                if endpoint == endpoints[0]:
                    old_ready.set()
                    if not release_old.wait(3):
                        sock.close()
                        raise TimeoutError("Test did not release old attempt")
                elif failure is not None:
                    new_ready.set()
                    if not release_new.wait(3):
                        sock.close()
                        raise TimeoutError("Test did not release new attempt")
                return sock

            def tracked_worker(host, port, timeout, *connection_id):
                workers.append(threading.current_thread())
                try:
                    original_worker(host, port, timeout, *connection_id)
                finally:
                    if port == endpoints[0][1]:
                        old_done.set()

            try:
                with patch("network_client.socket.create_connection", side_effect=controlled_connect), patch.object(client, "_connect_worker", side_effect=tracked_worker):
                    self.assertTrue(client.connect_async(*endpoints[0]))
                    self.assertTrue(old_ready.wait(3))
                    client.disconnect()
                    self.assertTrue(client.connect_async(*endpoints[1]))
                    if failure is None:
                        wait_for(lambda: client.is_connected)
                    else:
                        self.assertTrue(new_ready.wait(3))
                    release_old.set()
                    self.assertTrue(old_done.wait(3))
                    if failure is not None:
                        self.assertTrue(client.is_connecting)
                        release_new.set()
                    wait_for(lambda: client.is_connected)
                    self.assertEqual(client._sock.getpeername(), endpoints[1])
                    events = []
                    while True:
                        try:
                            events.append(client.events.get_nowait())
                        except queue.Empty:
                            break
                    self.assertEqual([e.data for e in events if e.type == EVENT_CONNECTED], [f"{endpoints[1][0]}:{endpoints[1][1]}"])
                    self.assertFalse(any(e.type == EVENT_ERROR for e in events))
                    if failure is None:
                        self.assertEqual(sockets[0].fileno(), -1)
            finally:
                release_old.set()
                release_new.set()
                for worker in workers:
                    worker.join(4)
                client.disconnect()
                if client._recv_thread is not None:
                    client._recv_thread.join(3)
                for sock in sockets:
                    sock.close()

    def test_cancelled_success_cannot_replace_new_socket(self):
        self.check_cancelled_attempt()

    def test_cancelled_timeout_cannot_clear_new_connecting_state(self):
        self.check_cancelled_attempt(TimeoutError("old attempt"))

    def test_cancelled_refusal_cannot_report_error_for_new_attempt(self):
        self.check_cancelled_attempt(ConnectionRefusedError("old attempt"))

    def test_cancelled_oserror_cannot_report_error_for_new_attempt(self):
        self.check_cancelled_attempt(OSError("old attempt"))


if __name__ == "__main__":
    unittest.main()
