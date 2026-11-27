"""Tầng lưu trữ lịch sử phòng chat, file & trạng thái thu hồi bằng SQLite (sqlite3).

Lưu bền vững vào file `chat_history.db`:
- Bảng `rooms`: Lưu danh sách các phòng chat theo chủ đề.
- Bảng `messages`: Lưu tin nhắn văn bản, dữ liệu nhị phân (BLOB) của file/hình ảnh,
  và cột `recalled` để đánh dấu xóa/thu hồi nội dung trên mọi phiên đăng nhập sau.
"""

from datetime import datetime
import os
import sqlite3
import threading
from typing import Dict, List, Optional, Sequence, Tuple

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
                    recalled INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                """
            )
            # Tự động nâng cấp nếu file chat_history.db cũ chưa có cột recalled
            try:
                self._conn.execute(
                    "ALTER TABLE messages ADD COLUMN recalled INTEGER NOT NULL DEFAULT 0;"
                )
            except sqlite3.OperationalError:
                pass

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
                INSERT INTO messages(room, sender, msg_type, content, file_data, recalled, created_at)
                VALUES (?, ?, 'chat', ?, NULL, 0, ?);
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
                INSERT INTO messages(room, sender, msg_type, content, file_data, recalled, created_at)
                VALUES (?, ?, 'file', ?, ?, 0, ?);
                """,
                (norm_room, sender, filename, sqlite3.Binary(file_bytes), ts),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    def recall_message(
        self, msg_id: int, requester: str
    ) -> Tuple[bool, str, Optional[Dict[str, object]]]:
        """Thu hồi tin nhắn hoặc tệp theo `msg_id` (chỉ chính chủ mới được phép thu hồi).

        Xóa sạch nội dung gốc và dữ liệu BLOB trong SQLite, đồng thời bật cờ `recalled = 1`.
        Trả về `(thành_công, thông_báo_lỗi_nếu_có, thông_tin_bản_ghi)`.
        """
        with self._lock:
            row = self._conn.execute(
                "SELECT id, room, sender, recalled, created_at FROM messages WHERE id = ?;",
                (msg_id,),
            ).fetchone()
            if row is None:
                return False, "Không tìm thấy tin nhắn cần thu hồi.", None
            if str(row["sender"]) != requester:
                return False, "Bạn chỉ có thể thu hồi tin nhắn do chính mình gửi.", None
            if int(row["recalled"]) == 1:
                return False, "Tin nhắn này đã được thu hồi trước đó.", None

            self._conn.execute(
                """
                UPDATE messages
                SET recalled = 1, content = '', file_data = NULL
                WHERE id = ?;
                """,
                (msg_id,),
            )
            self._conn.commit()

            info: Dict[str, object] = {
                "msg_id": int(row["id"]),
                "room": str(row["room"]),
                "sender": str(row["sender"]),
                "timestamp": str(row["created_at"]),
            }
            return True, "", info

    def get_recent_history(self, room: str, limit: int = 40) -> List[Dict[str, object]]:
        """Lấy `limit` tin nhắn/file gần nhất của phòng theo thứ tự thời gian cũ -> mới."""
        norm_room = normalize_room_name(room)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, room, sender, msg_type, content, file_data, recalled, created_at
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
                    "file_data": (
                        bytes(row["file_data"]) if row["file_data"] is not None else None
                    ),
                    "recalled": bool(row["recalled"]),
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
