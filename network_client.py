"""Tầng quản lý kết nối TCP bất đồng bộ cho giao diện (GUI Client).

Sử dụng trực tiếp MAX_SIZE, send_msg(), recv_msg() từ transport.py:
- Tách luồng nhận (receiver thread) độc lập để nhận tin nhắn & frame nhị phân ngay cả khi đang gõ.
- Hỗ trợ truyền file/hình ảnh nhị phân (raw bytes) chia mảnh trên luồng phụ để không làm đơ giao diện.
- Khóa đồng bộ (threading.Lock) khi gọi send_msg() để các gói chat và gói file không trộn lẫn byte.
"""

from dataclasses import dataclass
import os
import queue
import socket
import threading
from typing import Optional
import uuid

from protocol import (
    FILE_CHUNK_SIZE,
    MAX_FILE_SIZE,
    encode_binary_chunk,
    encode_message,
    make_message,
)
from transport import MAX_SIZE, recv_msg, send_msg

EVENT_CONNECTED = "CONNECTED"
EVENT_MESSAGE = "MESSAGE"
EVENT_FILE_SENT = "FILE_SENT"
EVENT_DISCONNECTED = "DISCONNECTED"
EVENT_ERROR = "ERROR"


@dataclass
class NetworkEvent:
    """Sự kiện truyền từ luồng mạng sang luồng giao diện."""

    type: str
    data: object = None


class SocketClient:
    """Quản lý vòng đời kết nối TCP, nhận bất đồng bộ và truyền dữ liệu nhị phân."""

    def __init__(self) -> None:
        self.events: "queue.Queue[NetworkEvent]" = queue.Queue()
        self._sock: Optional[socket.socket] = None
        self._state_lock = threading.Lock()
        self._send_lock = threading.Lock()
        self._recv_thread: Optional[threading.Thread] = None
        self._connected = False
        self._connecting = False
        self._closing = False
        self._connection_id = 0

    @property
    def is_connected(self) -> bool:
        with self._state_lock:
            return self._connected

    @property
    def is_connecting(self) -> bool:
        with self._state_lock:
            return self._connecting

    def connect_async(self, host: str, port: int, timeout: float = 5.0) -> bool:
        """Bắt đầu kết nối TCP trên luồng phụ."""
        with self._state_lock:
            if self._connected or self._connecting:
                return False

            self._connection_id += 1
            connection_id = self._connection_id

            self._connecting = True
            self._closing = False

        worker = threading.Thread(
            target=self._connect_worker,
            args=(host, port, timeout, connection_id),
            name="tcp-connect-worker",
            daemon=True,
        )
        worker.start()
        return True

    def _connect_worker(
        self,
        host: str,
        port: int,
        timeout: float,
        connection_id: int,
    ) -> None:
        sock: Optional[socket.socket] = None

        try:
            sock = socket.create_connection((host, port), timeout=timeout)
            sock.settimeout(None)

        except OSError as error:
            if sock is not None:
                self._close_raw_socket(sock)

            if isinstance(error, TimeoutError):
                message = (
                    f"Hết thời gian chờ kết nối tới {host}:{port} "
                    f"(quá {timeout:g} giây)."
                )
            elif isinstance(error, ConnectionRefusedError):
                message = (
                    f"Kết nối tới {host}:{port} bị từ chối. "
                    "Hãy kiểm tra server và IP/port."
                )
            else:
                message = f"Không thể kết nối tới {host}:{port}: {error}"

            with self._state_lock:
                if connection_id != self._connection_id:
                    return

                self._connecting = False
                self.events.put(NetworkEvent(EVENT_ERROR, message))

            return

        with self._state_lock:
            if connection_id != self._connection_id or self._closing:
                recv_thread = None
            else:
                self._sock = sock
                self._connected = True
                self._connecting = False

                recv_thread = threading.Thread(
                    target=self._recv_loop,
                    args=(sock,),
                    name="tcp-recv-worker",
                    daemon=True,
                )
                self._recv_thread = recv_thread

                self.events.put(
                    NetworkEvent(EVENT_CONNECTED, f"{host}:{port}")
                )

        if recv_thread is None:
            self._close_raw_socket(sock)
            return

        recv_thread.start()

    def _recv_loop(self, sock: socket.socket) -> None:
        """Luồng nền đọc frame liên tục (cả JSON lẫn binary bytes) bằng recv_msg()."""
        try:
            while True:
                payload = recv_msg(sock)

                if payload is None:
                    if not self._cleanup_socket(target_sock=sock):
                        self.events.put(
                            NetworkEvent(
                                EVENT_DISCONNECTED,
                                "Server đã đóng kết nối.",
                            )
                        )
                    break

                self.events.put(NetworkEvent(EVENT_MESSAGE, payload))

        except TimeoutError:
            if not self._cleanup_socket(target_sock=sock):
                self.events.put(
                    NetworkEvent(
                        EVENT_ERROR,
                        "Hết thời gian chờ phản hồi từ server. Đã đóng phiên.",
                    )
                )
        except ValueError as err:
            if not self._cleanup_socket(target_sock=sock):
                self.events.put(
                    NetworkEvent(
                        EVENT_ERROR,
                        f"Frame nhận về không hợp lệ ({err}). Đã đóng kết nối.",
                    )
                )
        except ConnectionError as err:
            if not self._cleanup_socket(target_sock=sock):
                self.events.put(
                    NetworkEvent(EVENT_ERROR, f"Kết nối bị ngắt đột ngột: {err}")
                )
        except OSError as err:
            if not self._cleanup_socket(target_sock=sock):
                self.events.put(
                    NetworkEvent(EVENT_ERROR, f"Lỗi socket khi nhận dữ liệu: {err}")
                )

    def send_payload(self, payload: bytes) -> bool:
        """Gửi một frame kiểu bytes qua send_msg() an toàn đa luồng."""
        if not isinstance(payload, (bytes, bytearray)):
            raise TypeError("Payload truyền vào send_payload() phải là kiểu bytes")

        data = bytes(payload)
        if len(data) > MAX_SIZE:
            raise ValueError(
                f"Thông điệp quá lớn ({len(data):,} byte > giới hạn {MAX_SIZE:,} byte)"
            )

        with self._state_lock:
            sock = self._sock
            connected = self._connected

        if not connected or sock is None:
            raise ConnectionError("Chưa có kết nối tới server")

        try:
            with self._send_lock:
                send_msg(sock, data)
            return True
        except TimeoutError:
            if not self._cleanup_socket(target_sock=sock):
                self.events.put(
                    NetworkEvent(
                        EVENT_ERROR, "Hết thời gian chờ khi gửi dữ liệu. Đã đóng kết nối."
                    )
                )
            return False
        except ConnectionError as err:
            if not self._cleanup_socket(target_sock=sock):
                self.events.put(
                    NetworkEvent(EVENT_ERROR, f"Không thể gửi tin, kết nối đã ngắt: {err}")
                )
            return False
        except OSError as err:
            if not self._cleanup_socket(target_sock=sock):
                self.events.put(
                    NetworkEvent(EVENT_ERROR, f"Lỗi mạng/socket khi gửi dữ liệu: {err}")
                )
            return False

    def send_file_async(self, filename: str, file_bytes: bytes) -> str:
        """Truyền file/hình ảnh bằng các frame nhị phân (bytes) trên luồng phụ. Trả về `file_id`."""
        clean_name = os.path.basename(filename.strip()) or "tep_dinh_kem.bin"
        raw = bytes(file_bytes)
        if len(raw) > MAX_FILE_SIZE:
            raise ValueError(
                f"Kích thước tệp vượt giới hạn {MAX_FILE_SIZE // (1024 * 1024)} MB "
                f"(hiện tại: {len(raw):,} byte)"
            )
        if not self.is_connected:
            raise ConnectionError("Chưa có kết nối tới server")

        file_id = uuid.uuid4().hex[:12]
        worker = threading.Thread(
            target=self._file_send_worker,
            args=(file_id, clean_name, raw),
            name="tcp-file-send-worker",
            daemon=True,
        )
        worker.start()
        return file_id

    def _file_send_worker(self, file_id: str, filename: str, raw: bytes) -> None:
        chunks = [
            raw[idx : idx + FILE_CHUNK_SIZE]
            for idx in range(0, len(raw), FILE_CHUNK_SIZE)
        ]
        try:
            start_pkt = encode_message(
                make_message(
                    "file_start",
                    file_id=file_id,
                    filename=filename,
                    size=len(raw),
                    total_chunks=len(chunks),
                )
            )
            if not self.send_payload(start_pkt):
                return

            for idx, chunk in enumerate(chunks):
                bin_frame = encode_binary_chunk(file_id, idx, chunk)
                if not self.send_payload(bin_frame):
                    return

            end_pkt = encode_message(make_message("file_end", file_id=file_id))
            if not self.send_payload(end_pkt):
                return

            self.events.put(
                NetworkEvent(
                    EVENT_FILE_SENT,
                    {"file_id": file_id, "filename": filename, "data": raw},
                )
            )
        except Exception as err:
            self.events.put(NetworkEvent(EVENT_ERROR, f"Lỗi khi gửi tệp '{filename}': {err}"))

    def disconnect(self) -> None:
        """Ngắt kết nối chủ động từ phía người dùng."""
        with self._state_lock:
            was_active = self._connected or self._connecting

        if not was_active:
            return

        self._cleanup_socket(intentional=True)
        self.events.put(NetworkEvent(EVENT_DISCONNECTED, "Đã ngắt kết nối với server."))

    def _cleanup_socket(
        self,
        target_sock: Optional[socket.socket] = None,
        intentional: bool = False,
    ) -> bool:
        with self._state_lock:
            current_sock = self._sock
            if target_sock is not None and current_sock is not target_sock:
                return True

            self._connection_id += 1

            was_intentional = self._closing or intentional
            if intentional:
                self._closing = True

            self._sock = None
            self._connected = False
            self._connecting = False

        if current_sock is not None:
            self._close_raw_socket(current_sock)

        return was_intentional

    @staticmethod
    def _close_raw_socket(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass
