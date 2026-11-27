"""Server Chat đa luồng: Chia phòng + Truyền File/Ảnh nhị phân + Lưu lịch sử SQLite + Thu hồi tin nhắn.

Tích hợp đầy đủ:
- Tầng framing TCP (`transport.py`): 4-byte header big-endian + N-byte payload (<= 64 KiB/frame).
- Tầng giao thức (`protocol.py`): Gói điều khiển JSON UTF-8 + Gói dữ liệu nhị phân (`BINARY_MAGIC`).
- Tầng lưu trữ (`storage.py`): SQLite `chat_history.db` lưu phòng, lịch sử chat/file và trạng thái thu hồi.

Chạy server:
    py chat_server.py
    py chat_server.py --host 0.0.0.0 --port 65432
"""

import argparse
from dataclasses import dataclass, field
from datetime import datetime
import socket
import threading
from typing import Dict, List, Optional, Sequence, Tuple
import uuid

from protocol import (
    FILE_CHUNK_SIZE,
    MAX_FILE_SIZE,
    ProtocolError,
    decode_binary_chunk,
    decode_message,
    encode_binary_chunk,
    encode_message,
    is_binary_frame,
    make_message,
    normalize_room_name,
)
from storage import DEFAULT_DB_PATH, ChatStorage
from transport import recv_msg, send_msg

DEFAULT_ROOM = "chung"
HISTORY_REPLAY_LIMIT = 40


def now_hhmmss() -> str:
    return datetime.now().strftime("%H:%M:%S")


def valid_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError("Port phải là số nguyên") from err
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port phải nằm trong khoảng 1 đến 65535")
    return port


@dataclass
class IncomingFileState:
    file_id: str
    filename: str
    expected_size: int
    total_chunks: int
    room: str
    chunks: Dict[int, bytes] = field(default_factory=dict)
    received_bytes: int = 0


@dataclass
class ClientSession:
    sock: socket.socket
    addr: Tuple[str, int]
    username: str
    room: str = DEFAULT_ROOM
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    welcomed: bool = False
    incoming_files: Dict[str, IncomingFileState] = field(default_factory=dict)


class ChatRoomServer:
    """TCP Chat Server hỗ trợ nhiều phòng chat, gửi file nhị phân, lưu lịch sử SQLite & thu hồi tin nhắn."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 65432,
        db_path: str = DEFAULT_DB_PATH,
        quiet: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.quiet = quiet
        self.storage = ChatStorage(db_path=db_path)
        self._server_sock: Optional[socket.socket] = None
        self._clients: Dict[socket.socket, ClientSession] = {}
        self._lock = threading.Lock()
        self._running = False

    def _log(self, text: str) -> None:
        if not self.quiet:
            print(f"[{now_hhmmss()}] {text}", flush=True)

    def bind_and_listen(self) -> Tuple[str, int]:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen()
        self._server_sock = srv
        self._running = True
        actual_addr = srv.getsockname()
        self.port = actual_addr[1]
        return actual_addr

    def serve_forever(self) -> None:
        if self._server_sock is None:
            self.bind_and_listen()

        assert self._server_sock is not None
        rooms_str = ", ".join(f"#{r}" for r in self.storage.list_rooms())
        self._log(f"[READY] Chat Server lắng nghe tại {self.host}:{self.port}")
        self._log(f"[SQLITE] Đã kết nối DB ({self.storage.db_path}) | Phòng sẵn có: {rooms_str}")

        while self._running:
            try:
                conn, addr = self._server_sock.accept()
            except OSError:
                break

            conn.settimeout(None)
            session = ClientSession(
                sock=conn,
                addr=addr,
                username=f"{addr[0]}:{addr[1]}",
                room=DEFAULT_ROOM,
            )
            with self._lock:
                self._clients[conn] = session

            self._log(f"[CONNECT] {addr[0]}:{addr[1]} đã mở kết nối TCP")
            worker = threading.Thread(
                target=self._handle_client,
                args=(session,),
                daemon=True,
            )
            worker.start()

    def stop(self) -> None:
        self._running = False
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass

        with self._lock:
            sessions = list(self._clients.values())
            self._clients.clear()

        for session in sessions:
            self._close_socket(session.sock)

        self.storage.close()

    def _send_raw_to(self, session: ClientSession, raw_frame: bytes) -> bool:
        try:
            with session.send_lock:
                send_msg(session.sock, raw_frame)
            return True
        except OSError:
            return False

    def _send_to(self, session: ClientSession, message: dict) -> bool:
        return self._send_raw_to(session, encode_message(message))

    def _room_sessions(
        self, room: str, exclude_sock: Optional[socket.socket] = None
    ) -> List[ClientSession]:
        with self._lock:
            return [
                s
                for sock, s in self._clients.items()
                if s.welcomed and s.room == room and sock is not exclude_sock
            ]

    def _broadcast_room_json(
        self, room: str, message: dict, exclude_sock: Optional[socket.socket] = None
    ) -> int:
        payload = encode_message(message)
        targets = self._room_sessions(room, exclude_sock=exclude_sock)
        sent = 0
        for target in targets:
            if self._send_raw_to(target, payload):
                sent += 1
        return sent

    def _sync_room_list_all(self) -> None:
        rooms = self.storage.list_rooms()
        with self._lock:
            welcomed_sessions = [s for s in self._clients.values() if s.welcomed]
        for target in welcomed_sessions:
            self._send_to(
                target,
                make_message("room_list", rooms=rooms, current_room=target.room),
            )

    def _stream_file_to_sessions(
        self,
        targets: Sequence[ClientSession],
        filename: str,
        file_bytes: bytes,
        sender: str,
        room: str,
        timestamp: str,
        msg_id: int,
        is_history: bool = False,
    ) -> int:
        if not targets:
            return 0

        transfer_id = uuid.uuid4().hex[:12]
        chunks = [
            file_bytes[i : i + FILE_CHUNK_SIZE]
            for i in range(0, len(file_bytes), FILE_CHUNK_SIZE)
        ]
        start_raw = encode_message(
            make_message(
                "file_start",
                file_id=transfer_id,
                filename=filename,
                size=len(file_bytes),
                total_chunks=len(chunks),
                sender=sender,
                room=room,
                timestamp=timestamp,
                history=is_history,
                msg_id=msg_id,
            )
        )
        chunk_frames = [
            encode_binary_chunk(transfer_id, idx, chunk)
            for idx, chunk in enumerate(chunks)
        ]
        end_raw = encode_message(make_message("file_end", file_id=transfer_id))

        delivered = 0
        for target in targets:
            ok = self._send_raw_to(target, start_raw)
            if ok:
                for frame in chunk_frames:
                    if not self._send_raw_to(target, frame):
                        ok = False
                        break
            if ok and self._send_raw_to(target, end_raw):
                delivered += 1
        return delivered

    def _enter_room(
        self, session: ClientSession, requested_room: str, initial: bool = False
    ) -> None:
        rooms_before = set(self.storage.list_rooms())
        target_room = self.storage.ensure_room(requested_room)
        is_new_room = target_room not in rooms_before
        old_room = session.room

        if not initial and old_room == target_room:
            self._send_to(
                session,
                make_message(
                    "room_list",
                    rooms=self.storage.list_rooms(),
                    current_room=target_room,
                ),
            )
            return

        if not initial and old_room != target_room:
            session.room = target_room
            self._broadcast_room_json(
                old_room,
                make_message(
                    "chat",
                    text=f"{session.username} đã chuyển sang phòng #{target_room}.",
                    room=old_room,
                    system=True,
                    timestamp=now_hhmmss(),
                ),
                exclude_sock=session.sock,
            )
        else:
            session.room = target_room

        if is_new_room:
            self._log(f"[NEW ROOM] '{session.username}' đã tạo phòng mới #{target_room}")
            self._sync_room_list_all()
        else:
            self._send_to(
                session,
                make_message(
                    "room_list",
                    rooms=self.storage.list_rooms(),
                    current_room=target_room,
                ),
            )

        # Nạp lịch sử tin nhắn, file & trạng thái thu hồi từ SQLite cho người vừa vào phòng
        history = self.storage.get_recent_history(target_room, limit=HISTORY_REPLAY_LIMIT)
        for item in history:
            mid = int(item["id"])
            if item.get("recalled"):
                self._send_to(
                    session,
                    make_message(
                        "chat",
                        text="",
                        sender=str(item["sender"]),
                        room=target_room,
                        timestamp=str(item["timestamp"]),
                        history=True,
                        msg_id=mid,
                        recalled=True,
                    ),
                )
            elif item["msg_type"] == "chat":
                self._send_to(
                    session,
                    make_message(
                        "chat",
                        text=str(item["content"]),
                        sender=str(item["sender"]),
                        room=target_room,
                        timestamp=str(item["timestamp"]),
                        history=True,
                        msg_id=mid,
                    ),
                )
            elif item["msg_type"] == "file" and isinstance(item["file_data"], bytes):
                self._stream_file_to_sessions(
                    targets=[session],
                    filename=str(item["content"]),
                    file_bytes=item["file_data"],
                    sender=str(item["sender"]),
                    room=target_room,
                    timestamp=str(item["timestamp"]),
                    msg_id=mid,
                    is_history=True,
                )

        room_count = len(self._room_sessions(target_room))
        hist_note = f" — Đã tải {len(history)} mục lịch sử từ SQLite" if history else ""
        self._send_to(
            session,
            make_message(
                "chat",
                text=f"Đang ở phòng #{target_room} ({room_count} người trong phòng{hist_note}).",
                room=target_room,
                system=True,
                timestamp=now_hhmmss(),
            ),
        )

        self._broadcast_room_json(
            target_room,
            make_message(
                "chat",
                text=f"{session.username} đã tham gia phòng #{target_room} ({room_count} người trong phòng).",
                room=target_room,
                system=True,
                timestamp=now_hhmmss(),
            ),
            exclude_sock=session.sock,
        )

    def _handle_binary_frame(self, session: ClientSession, raw: bytes) -> None:
        try:
            file_id, chunk_idx, chunk_data = decode_binary_chunk(raw)
        except ProtocolError as err:
            self._log(f"[BINARY ERROR] {session.username}: {err}")
            self._send_to(
                session, make_message("error", code="BAD_BINARY", message=str(err))
            )
            return

        transfer = session.incoming_files.get(file_id)
        if transfer is None:
            self._send_to(
                session,
                make_message(
                    "error",
                    code="UNKNOWN_FILE",
                    message=f"Chưa khai báo file_start cho mã {file_id}",
                ),
            )
            return

        if transfer.received_bytes + len(chunk_data) > MAX_FILE_SIZE:
            session.incoming_files.pop(file_id, None)
            self._send_to(
                session,
                make_message(
                    "error",
                    code="FILE_TOO_LARGE",
                    message="Tổng dữ liệu nhị phân vượt giới hạn cho phép",
                ),
            )
            return

        transfer.chunks[chunk_idx] = chunk_data
        transfer.received_bytes += len(chunk_data)
        self._log(
            f"[BINARY {len(chunk_data)}B] <- {session.username} (#{transfer.room}) | "
            f"file={transfer.filename} mảnh {chunk_idx + 1}/{max(1, transfer.total_chunks)}"
        )

    def _finalize_incoming_file(self, session: ClientSession, file_id: str) -> None:
        transfer = session.incoming_files.pop(file_id, None)
        if transfer is None:
            return

        missing = [i for i in range(transfer.total_chunks) if i not in transfer.chunks]
        if missing:
            self._send_to(
                session,
                make_message(
                    "error",
                    code="INCOMPLETE_FILE",
                    message=f"Thiếu {len(missing)} mảnh dữ liệu khi truyền '{transfer.filename}'",
                ),
            )
            return

        assembled = b"".join(transfer.chunks[i] for i in range(transfer.total_chunks))
        if len(assembled) != transfer.expected_size:
            self._send_to(
                session,
                make_message(
                    "error",
                    code="SIZE_MISMATCH",
                    message="Kích thước file ghép lại không khớp khai báo ban đầu",
                ),
            )
            return

        ts = now_hhmmss()
        msg_id = self.storage.save_file(
            room=transfer.room,
            sender=session.username,
            filename=transfer.filename,
            file_bytes=assembled,
            timestamp=ts,
        )
        # Xác nhận mã msg_id về cho chính người gửi (không cần tải ngược lại toàn bộ dữ liệu nhị phân)
        self._send_to(
            session,
            make_message(
                "file_saved",
                file_id=transfer.file_id,
                msg_id=msg_id,
                room=transfer.room,
                timestamp=ts,
            ),
        )
        # Chuyển tiếp các mảnh nhị phân kèm msg_id tới các thành viên khác trong cùng phòng
        peers = self._room_sessions(transfer.room, exclude_sock=session.sock)
        sent_count = self._stream_file_to_sessions(
            targets=peers,
            filename=transfer.filename,
            file_bytes=assembled,
            sender=session.username,
            room=transfer.room,
            timestamp=ts,
            msg_id=msg_id,
            is_history=False,
        )
        self._log(
            f"[FILE SAVED #{msg_id}] '{transfer.filename}' ({len(assembled):,}B) tại #{transfer.room} "
            f"-> lưu SQLite & gửi tới {sent_count} thành viên khác"
        )

    def _handle_client(self, session: ClientSession) -> None:
        conn = session.sock
        try:
            while self._running:
                raw = recv_msg(conn)
                if raw is None:
                    self._log(f"[EOF] {session.username} đã đóng kết nối")
                    break

                if is_binary_frame(raw):
                    self._handle_binary_frame(session, raw)
                    continue

                preview = raw.decode("utf-8", errors="replace")
                self._log(f"[RECV {len(raw)}B] <- {session.username} | {preview}")

                try:
                    msg = decode_message(raw)
                except ProtocolError as err:
                    self._log(f"[PROTOCOL ERROR] {session.username}: {err}")
                    self._send_to(
                        session,
                        make_message("error", code="BAD_MESSAGE", message=str(err)),
                    )
                    continue

                msg_type = msg["type"]

                if msg_type == "hello":
                    session.username = self._unique_username(
                        msg["username"].strip(), conn
                    )
                    session.welcomed = True
                    desired_room = normalize_room_name(msg.get("room", DEFAULT_ROOM)) or DEFAULT_ROOM
                    self._send_to(session, make_message("hello", username=session.username))
                    self._enter_room(session, desired_room, initial=True)
                    self._log(
                        f"[HELLO] Đăng ký '{session.username}' vào phòng #{session.room}"
                    )

                elif msg_type == "join_room":
                    if not session.welcomed:
                        self._send_to(
                            session,
                            make_message(
                                "error", code="NEED_HELLO", message="Cần gửi hello trước"
                            ),
                        )
                        continue
                    self._enter_room(session, msg["room"], initial=False)
                    self._log(
                        f"[JOIN ROOM] '{session.username}' chuyển tới phòng #{session.room}"
                    )

                elif msg_type == "chat":
                    content = msg["text"]
                    ts = now_hhmmss()
                    room = session.room
                    msg_id = self.storage.save_chat(
                        room=room, sender=session.username, text=content, timestamp=ts
                    )
                    # Gửi tới tất cả thành viên trong phòng (gồm cả người gửi để đồng bộ msg_id phục vụ thu hồi)
                    recipients = self._broadcast_room_json(
                        room,
                        make_message(
                            "chat",
                            text=content,
                            sender=session.username,
                            room=room,
                            timestamp=ts,
                            msg_id=msg_id,
                        ),
                        exclude_sock=None,
                    )
                    self._log(
                        f"[CHAT #{room} id={msg_id}] {session.username} -> lưu SQLite & đồng bộ {recipients} client"
                    )

                elif msg_type == "file_start":
                    fid = msg["file_id"]
                    session.incoming_files[fid] = IncomingFileState(
                        file_id=fid,
                        filename=msg["filename"],
                        expected_size=msg["size"],
                        total_chunks=msg["total_chunks"],
                        room=session.room,
                    )
                    self._log(
                        f"[FILE START] {session.username} (#{session.room}) bắt đầu gửi "
                        f"'{msg['filename']}' ({msg['size']:,}B, {msg['total_chunks']} mảnh)"
                    )

                elif msg_type == "file_end":
                    self._finalize_incoming_file(session, msg["file_id"])

                elif msg_type == "recall":
                    target_id = msg["msg_id"]
                    ok, err_msg, info = self.storage.recall_message(
                        msg_id=target_id, requester=session.username
                    )
                    if not ok or info is None:
                        self._send_to(
                            session,
                            make_message("error", code="RECALL_DENIED", message=err_msg),
                        )
                    else:
                        target_room = str(info["room"])
                        synced = self._broadcast_room_json(
                            target_room,
                            make_message(
                                "recall",
                                msg_id=int(info["msg_id"]),
                                room=target_room,
                                sender=str(info["sender"]),
                                timestamp=str(info["timestamp"]),
                            ),
                            exclude_sock=None,
                        )
                        self._log(
                            f"[RECALL #{target_room} id={target_id}] '{session.username}' thu hồi tin -> đồng bộ {synced} client"
                        )

                elif msg_type == "bye":
                    self._send_to(session, make_message("bye"))
                    self._log(f"[BYE] {session.username} kết thúc phiên")
                    break

                elif msg_type == "error":
                    self._log(
                        f"[CLIENT ERROR] {session.username}: [{msg['code']}] {msg['message']}"
                    )

        except ValueError as err:
            self._log(f"[FRAME ERROR] {session.username}: {err}")
        except ConnectionError as err:
            self._log(f"[DISCONNECT] {session.username}: {err}")
        except OSError as err:
            if self._running:
                self._log(f"[SOCKET ERROR] {session.username}: {err}")
        finally:
            with self._lock:
                removed = self._clients.pop(conn, None)

            self._close_socket(conn)

            if removed is not None and removed.welcomed and self._running:
                remaining = len(self._room_sessions(removed.room))
                self._broadcast_room_json(
                    removed.room,
                    make_message(
                        "chat",
                        text=f"{removed.username} đã rời phòng #{removed.room} (còn {remaining} người trong phòng).",
                        room=removed.room,
                        system=True,
                        timestamp=now_hhmmss(),
                    ),
                    exclude_sock=conn,
                )

    def _unique_username(self, desired: str, sock: socket.socket) -> str:
        with self._lock:
            taken = {
                s.username for k, s in self._clients.items() if k is not sock and s.welcomed
            }
        if desired not in taken:
            return desired
        idx = 2
        while f"{desired} ({idx})" in taken:
            idx += 1
        return f"{desired} ({idx})"

    @staticmethod
    def _close_socket(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Multi-room TCP Chat & File Server")
    parser.add_argument("--host", "--h", default="127.0.0.1", help="IP lắng nghe (0.0.0.0 cho LAN)")
    parser.add_argument("--port", "--p", type=valid_port, default=65432, help="Port (1..65535)")
    parser.add_argument("--db", default=DEFAULT_DB_PATH, help="Đường dẫn file SQLite lưu lịch sử")
    return parser.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    server = ChatRoomServer(host=args.host, port=args.port, db_path=args.db)
    try:
        server.bind_and_listen()
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐã dừng Chat Server.")
    except OSError as err:
        if getattr(err, "winerror", None) == 10048:
            print(f"Địa chỉ {args.host}:{args.port} đang bị chiếm (hãy tắt server cũ hoặc đổi port --p).")
        else:
            print("Lỗi khởi động server:", err)
    finally:
        server.stop()


if __name__ == "__main__":
    main()
