"""Tầng lưu trữ lịch sử phòng chat & file bằng SQLite (thư viện chuẩn sqlite3).

Lưu bền vững vào file `chat_history.db`:
- Bảng `rooms`: Lưu danh sách các phòng chat theo chủ đề.
- Bảng `messages`: Lưu tin nhắn văn bản và cả dữ liệu nhị phân (BLOB) của file/hình ảnh đã gửi,
  giúp người dùng đăng nhập lại hoặc chuyển phòng vẫn xem và tải lại được tin nhắn/ảnh cũ.
"""

from datetime import datetime
import os
import sqlite3
import threading
from typing import Dict, List, Optional, Sequence

from protocol import normalize_room_name

DEFAULT_ROOMS = ("chung", "hoc-tap", "do-an-mang", "giai-tri")
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chat_history.db")


def _now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


class ChatStorage:
    """Quản lý đọc/ghi SQLite an toàn đa luồng cho Chat Server."""

    def __init__(
        self,
        db_path: str = DEFAULT_DB_PATH,
        seed_rooms: Sequence[str] = DEFAULT_ROOMS,
    ) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema(seed_rooms)

    def _init_schema(self, seed_rooms: Sequence[str]) -> None:
        with self._lock:
            if self.db_path != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS rooms (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    room TEXT NOT NULL,
                    sender TEXT NOT NULL,
                    msg_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    file_data BLOB,
                    created_at TEXT NOT NULL
                );
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_room_id
                ON messages(room, id);
                """
            )
            now = _now_str()
            for room in seed_rooms:
                norm = normalize_room_name(room)
                if norm:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO rooms(name, created_at) VALUES (?, ?);",
                        (norm, now),
                    )
            self._conn.commit()

    def ensure_room(self, room_name: str) -> str:
        """Tạo phòng mới nếu chưa tồn tại và trả về tên phòng đã chuẩn hóa."""
        norm = normalize_room_name(room_name)
        if not norm:
            raise ValueError("Tên phòng không hợp lệ")

        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO rooms(name, created_at) VALUES (?, ?);",
                (norm, _now_str()),
            )
            self._conn.commit()
        return norm

    def list_rooms(self) -> List[str]:
        """Trả về danh sách các phòng theo thứ tự tạo."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT name FROM rooms ORDER BY id ASC;"
            ).fetchall()
        return [str(r["name"]) for r in rows]

    def save_chat(
        self,
        room: str,
        sender: str,
        text: str,
        timestamp: Optional[str] = None,
    ) -> int:
        ts = timestamp or _now_str()
        norm_room = self.ensure_room(room)
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO messages(room, sender, msg_type, content, file_data, created_at)
                VALUES (?, ?, 'chat', ?, NULL, ?);
                """,
                (norm_room, sender, text, ts),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def save_file(
        self,
        room: str,
        sender: str,
        filename: str,
        file_bytes: bytes,
        timestamp: Optional[str] = None,
    ) -> int:
        ts = timestamp or _now_str()
        norm_room = self.ensure_room(room)
        with self._lock:
            cur = self._conn.execute(
                """
                INSERT INTO messages(room, sender, msg_type, content, file_data, created_at)
                VALUES (?, ?, 'file', ?, ?, ?);
                """,
                (norm_room, sender, filename, sqlite3.Binary(file_bytes), ts),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def get_recent_history(self, room: str, limit: int = 40) -> List[Dict[str, object]]:
        """Lấy `limit` tin nhắn/file gần nhất của phòng theo thứ tự thời gian cũ -> mới."""
        norm_room = normalize_room_name(room)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, room, sender, msg_type, content, file_data, created_at
                FROM messages
                WHERE room = ?
                ORDER BY id DESC
                LIMIT ?;
                """,
                (norm_room, limit),
            ).fetchall()

        history: List[Dict[str, object]] = []
        for row in reversed(rows):
            history.append(
                {
                    "id": int(row["id"]),
                    "room": str(row["room"]),
                    "sender": str(row["sender"]),
                    "msg_type": str(row["msg_type"]),
                    "content": str(row["content"]),
                    "file_data": bytes(row["file_data"]) if row["file_data"] is not None else None,
                    "timestamp": str(row["created_at"]),
                }
            )
        return history

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except Exception:
                pass
