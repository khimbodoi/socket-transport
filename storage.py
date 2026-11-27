"""Tầng lưu trữ SQLite: Phòng Public/Private, Duyệt thành viên, Kick, Lịch sử & Thu hồi.

Lưu bền vững vào `chat_history.db`:
- `rooms`: Lưu tên phòng, `owner` (Admin tạo phòng) và `is_private` (0=Public, 1=Private).
- `room_members`: Lưu danh sách thành viên đã tham gia / được duyệt vào từng phòng.
- `room_bans`: Lưu thành viên bị Admin kick khỏi phòng (muốn vào lại phải gửi request để Admin duyệt).
- `join_requests`: Lưu các yêu cầu xin vào phòng Private (hoặc xin vào lại sau khi bị kick).
- `user_hidden_rooms` & `user_last_room`: Lưu trạng thái rời phòng và phòng gần nhất của từng người.
- `messages`: Lưu tin nhắn văn bản, dữ liệu nhị phân (BLOB) của file/ảnh và cờ `recalled`.
"""

from datetime import datetime
import os
import sqlite3
import threading
from typing import Dict, List, Optional, Sequence, Tuple

from protocol import normalize_room_name

LOBBY_ROOM = "chung"
DEFAULT_ROOMS = (LOBBY_ROOM, "hoc-tap", "do-an-mang", "giai-tri")
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
                    owner TEXT NOT NULL DEFAULT '',
                    is_private INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                """
            )
            for alter_sql in (
                "ALTER TABLE rooms ADD COLUMN owner TEXT NOT NULL DEFAULT '';",
                "ALTER TABLE rooms ADD COLUMN is_private INTEGER NOT NULL DEFAULT 0;",
            ):
                try:
                    self._conn.execute(alter_sql)
                except sqlite3.OperationalError:
                    pass

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS room_members (
                    room TEXT NOT NULL,
                    username TEXT NOT NULL,
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY (room, username)
                );
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS room_bans (
                    room TEXT NOT NULL,
                    username TEXT NOT NULL,
                    PRIMARY KEY (room, username)
                );
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS join_requests (
                    room TEXT NOT NULL,
                    username TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    PRIMARY KEY (room, username)
                );
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_hidden_rooms (
                    username TEXT NOT NULL,
                    room TEXT NOT NULL,
                    PRIMARY KEY (username, room)
                );
                """
            )

            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_last_room (
                    username TEXT PRIMARY KEY,
                    room TEXT NOT NULL
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
                        "INSERT OR IGNORE INTO rooms(name, owner, is_private, created_at) VALUES (?, '', 0, ?);",
                        (norm, now),
                    )
            self._conn.commit()

    def try_enter_or_create_room(
        self, room_name: str, username: str, is_private: bool = False
    ) -> Dict[str, object]:
        """Thử tạo phòng mới hoặc tham gia phòng đã có.

        Trả về dict với `status`:
        - `'entered'`: Cho phép vào phòng ngay (`room`, `is_new`, `owner`, `is_private`).
        - `'pending_approval'`: Phòng Private (hoặc từng bị kick) -> đã lưu `join_requests` chờ Admin duyệt
          (`room`, `owner`, `already_pending`).
        """
        norm = normalize_room_name(room_name)
        if not norm:
            raise ValueError("Tên phòng không hợp lệ")

        clean_user = username.strip()
        now = _now_str()

        with self._lock:
            row = self._conn.execute(
                "SELECT owner, is_private FROM rooms WHERE name = ?;", (norm,)
            ).fetchone()

            if row is None:
                # Tạo phòng mới -> người tạo là Admin (owner) và là thành viên đầu tiên
                priv_int = 1 if (is_private and norm != LOBBY_ROOM) else 0
                self._conn.execute(
                    "INSERT INTO rooms(name, owner, is_private, created_at) VALUES (?, ?, ?, ?);",
                    (norm, clean_user, priv_int, now),
                )
                if clean_user:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO room_members(room, username, joined_at) VALUES (?, ?, ?);",
                        (norm, clean_user, now),
                    )
                    self._conn.execute(
                        "DELETE FROM user_hidden_rooms WHERE username = ? AND room = ?;",
                        (clean_user, norm),
                    )
                    self._set_last_room_unlocked(clean_user, norm)
                self._conn.commit()
                return {
                    "status": "entered",
                    "room": norm,
                    "is_new": True,
                    "owner": clean_user,
                    "is_private": bool(priv_int),
                }

            owner = str(row["owner"])
            room_priv = bool(row["is_private"])

            if not clean_user:
                return {
                    "status": "entered",
                    "room": norm,
                    "is_new": False,
                    "owner": owner,
                    "is_private": room_priv,
                }

            # Admin hoặc sảnh #chung luôn được phép vào thẳng
            is_owner = bool(owner and owner == clean_user)
            is_member = (
                self._conn.execute(
                    "SELECT 1 FROM room_members WHERE room = ? AND username = ?;",
                    (norm, clean_user),
                ).fetchone()
                is not None
            )
            is_banned = (
                self._conn.execute(
                    "SELECT 1 FROM room_bans WHERE room = ? AND username = ?;",
                    (norm, clean_user),
                ).fetchone()
                is not None
            )

            # Chặn nếu: (1) Phòng Private mà chưa được duyệt làm thành viên, HOẶC (2) Đã bị Admin kick
            if norm != LOBBY_ROOM and not is_owner and ((room_priv and not is_member) or is_banned):
                existing_req = self._conn.execute(
                    "SELECT 1 FROM join_requests WHERE room = ? AND username = ?;",
                    (norm, clean_user),
                ).fetchone()
                if existing_req is None:
                    self._conn.execute(
                        "INSERT INTO join_requests(room, username, requested_at) VALUES (?, ?, ?);",
                        (norm, clean_user, now),
                    )
                self._conn.execute(
                    "DELETE FROM user_hidden_rooms WHERE username = ? AND room = ?;",
                    (clean_user, norm),
                )
                self._conn.commit()
                return {
                    "status": "pending_approval",
                    "room": norm,
                    "owner": owner,
                    "is_private": room_priv,
                    "is_banned": is_banned,
                    "already_pending": existing_req is not None,
                }

            # Được phép vào phòng -> ghi nhận thành viên & cập nhật last_room
            self._conn.execute(
                "INSERT OR IGNORE INTO room_members(room, username, joined_at) VALUES (?, ?, ?);",
                (norm, clean_user, now),
            )
            self._conn.execute(
                "DELETE FROM user_hidden_rooms WHERE username = ? AND room = ?;",
                (clean_user, norm),
            )
            self._set_last_room_unlocked(clean_user, norm)
            self._conn.commit()
            return {
                "status": "entered",
                "room": norm,
                "is_new": False,
                "owner": owner,
                "is_private": room_priv,
            }

    def _set_last_room_unlocked(self, username: str, room: str) -> None:
        self._conn.execute(
            """
            INSERT INTO user_last_room(username, room) VALUES (?, ?)
            ON CONFLICT(username) DO UPDATE SET room = excluded.room;
            """,
            (username, room),
        )

    def ensure_room(self, room_name: str, username: str = "") -> Tuple[str, bool, str]:
        """Đảm bảo phòng tồn tại (dùng nội bộ khi lưu tin nhắn)."""
        norm = normalize_room_name(room_name)
        if not norm:
            raise ValueError("Tên phòng không hợp lệ")
        with self._lock:
            row = self._conn.execute(
                "SELECT owner FROM rooms WHERE name = ?;", (norm,)
            ).fetchone()
            if row is None:
                self._conn.execute(
                    "INSERT INTO rooms(name, owner, is_private, created_at) VALUES (?, ?, 0, ?);",
                    (norm, username.strip(), _now_str()),
                )
                self._conn.commit()
                return norm, True, username.strip()
            return norm, False, str(row["owner"])

    def resolve_join_request(
        self, room_name: str, admin_user: str, target_user: str, approve: bool
    ) -> Tuple[bool, str]:
        """Admin duyệt (`approve=True`) hoặc từ chối (`approve=False`) yêu cầu vào phòng."""
        norm = normalize_room_name(room_name)
        clean_admin = admin_user.strip()
        clean_target = target_user.strip()

        with self._lock:
            row = self._conn.execute(
                "SELECT owner FROM rooms WHERE name = ?;", (norm,)
            ).fetchone()
            if row is None:
                return False, f"Phòng #{norm} không tồn tại."
            owner = str(row["owner"])
            if owner != clean_admin:
                return False, f"Chỉ Admin phòng #{norm} mới có quyền duyệt thành viên."

            self._conn.execute(
                "DELETE FROM join_requests WHERE room = ? AND username = ?;",
                (norm, clean_target),
            )
            if approve:
                self._conn.execute(
                    "DELETE FROM room_bans WHERE room = ? AND username = ?;",
                    (norm, clean_target),
                )
                self._conn.execute(
                    "DELETE FROM user_hidden_rooms WHERE username = ? AND room = ?;",
                    (clean_target, norm),
                )
                self._conn.execute(
                    "INSERT OR IGNORE INTO room_members(room, username, joined_at) VALUES (?, ?, ?);",
                    (norm, clean_target, _now_str()),
                )
            self._conn.commit()
        return True, ""

    def kick_member(
        self, room_name: str, admin_user: str, target_user: str
    ) -> Tuple[bool, str]:
        """Admin kick một thành viên ra khỏi phòng (thu hồi quyền vào phòng cho tới khi được duyệt lại)."""
        norm = normalize_room_name(room_name)
        clean_admin = admin_user.strip()
        clean_target = target_user.strip()

        if not norm or norm == LOBBY_ROOM:
            return False, "Không thể kick thành viên khỏi sảnh mặc định #chung."
        if clean_admin == clean_target:
            return False, "Admin không thể tự kick chính mình."

        with self._lock:
            row = self._conn.execute(
                "SELECT owner FROM rooms WHERE name = ?;", (norm,)
            ).fetchone()
            if row is None:
                return False, f"Phòng #{norm} không tồn tại."
            owner = str(row["owner"])
            if not owner or owner != clean_admin:
                return False, f"Chỉ Admin tạo phòng #{norm} mới có quyền kick thành viên."

            self._conn.execute(
                "DELETE FROM room_members WHERE room = ? AND username = ?;",
                (norm, clean_target),
            )
            self._conn.execute(
                "DELETE FROM join_requests WHERE room = ? AND username = ?;",
                (norm, clean_target),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO room_bans(room, username) VALUES (?, ?);",
                (norm, clean_target),
            )
            self._conn.execute(
                "UPDATE user_last_room SET room = ? WHERE username = ? AND room = ?;",
                (LOBBY_ROOM, clean_target, norm),
            )
            self._conn.commit()
        return True, ""

    def get_room_info_and_members(self, room_name: str) -> Optional[Dict[str, object]]:
        """Lấy thông tin phòng, danh sách thành viên đã tham gia & danh sách đang chờ duyệt."""
        norm = normalize_room_name(room_name)
        with self._lock:
            row = self._conn.execute(
                "SELECT name, owner, is_private FROM rooms WHERE name = ?;", (norm,)
            ).fetchone()
            if row is None:
                return None

            member_rows = self._conn.execute(
                "SELECT username FROM room_members WHERE room = ? ORDER BY rowid ASC;",
                (norm,),
            ).fetchall()
            pending_rows = self._conn.execute(
                "SELECT username FROM join_requests WHERE room = ? ORDER BY rowid ASC;",
                (norm,),
            ).fetchall()

        owner = str(row["owner"])
        members = [str(r["username"]) for r in member_rows]
        if owner and owner not in members:
            members.insert(0, owner)

        return {
            "room": norm,
            "owner": owner,
            "is_private": bool(row["is_private"]),
            "members": members,
            "pending_requests": [str(r["username"]) for r in pending_rows],
        }

    def get_pending_requests_for_admin(self, admin_user: str) -> List[Dict[str, str]]:
        """Lấy toàn bộ yêu cầu xin vào phòng đang chờ của các phòng do `admin_user` quản lý."""
        clean_admin = admin_user.strip()
        if not clean_admin:
            return []
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT j.room, j.username, j.requested_at
                FROM join_requests j
                JOIN rooms r ON r.name = j.room
                WHERE r.owner = ?
                ORDER BY j.rowid ASC;
                """,
                (clean_admin,),
            ).fetchall()
        return [
            {
                "room": str(r["room"]),
                "username": str(r["username"]),
                "timestamp": str(r["requested_at"]),
            }
            for r in rows
        ]

    def get_user_last_room(self, username: str) -> str:
        """Lấy phòng hợp lệ gần nhất người dùng đang đứng trước khi thoát."""
        clean_user = username.strip()
        if not clean_user:
            return LOBBY_ROOM
        with self._lock:
            row = self._conn.execute(
                """
                SELECT r.name, r.owner, r.is_private
                FROM user_last_room u
                JOIN rooms r ON r.name = u.room
                LEFT JOIN user_hidden_rooms h ON h.username = u.username AND h.room = u.room
                LEFT JOIN room_bans b ON b.username = u.username AND b.room = u.room
                LEFT JOIN room_members m ON m.username = u.username AND m.room = u.room
                WHERE u.username = ? AND h.room IS NULL AND b.room IS NULL;
                """,
                (clean_user,),
            ).fetchone()
            if row is None:
                return LOBBY_ROOM
            rname = str(row["name"])
            owner = str(row["owner"])
            is_priv = bool(row["is_private"])
            if is_priv and owner != clean_user:
                mem = self._conn.execute(
                    "SELECT 1 FROM room_members WHERE room = ? AND username = ?;",
                    (rname, clean_user),
                ).fetchone()
                if mem is None:
                    return LOBBY_ROOM
            return rname

    def set_user_last_room(self, username: str, room_name: str) -> None:
        clean_user = username.strip()
        norm = normalize_room_name(room_name) or LOBBY_ROOM
        if not clean_user:
            return
        with self._lock:
            self._set_last_room_unlocked(clean_user, norm)
            self._conn.commit()

    def list_rooms(self) -> List[str]:
        with self._lock:
            rows = self._conn.execute("SELECT name FROM rooms ORDER BY id ASC;").fetchall()
        return [str(r["name"]) for r in rows]

    def list_rooms_for_user(self, username: str) -> Dict[str, object]:
        """Trả về thông tin đầy đủ danh sách phòng hiển thị cho `username`."""
        clean_user = username.strip()
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, owner, is_private FROM rooms ORDER BY id ASC;"
            ).fetchall()
            hidden_rows = (
                self._conn.execute(
                    "SELECT room FROM user_hidden_rooms WHERE username = ?;",
                    (clean_user,),
                ).fetchall()
                if clean_user
                else []
            )
            member_rows = (
                self._conn.execute(
                    "SELECT room FROM room_members WHERE username = ?;",
                    (clean_user,),
                ).fetchall()
                if clean_user
                else []
            )
            ban_rows = (
                self._conn.execute(
                    "SELECT room FROM room_bans WHERE username = ?;",
                    (clean_user,),
                ).fetchall()
                if clean_user
                else []
            )

        hidden = {str(r["room"]) for r in hidden_rows}
        joined_set = {str(r["room"]) for r in member_rows}
        banned_set = {str(r["room"]) for r in ban_rows}

        visible_rooms: List[str] = []
        owners: Dict[str, str] = {}
        private_rooms: List[str] = []
        joined_rooms: List[str] = []

        for row in rows:
            rname = str(row["name"])
            row_owner = str(row["owner"])
            is_priv = bool(row["is_private"])
            if rname != LOBBY_ROOM and row_owner != clean_user and rname in hidden:
                continue
            visible_rooms.append(rname)
            owners[rname] = row_owner
            if is_priv:
                private_rooms.append(rname)
            if (
                rname == LOBBY_ROOM
                or row_owner == clean_user
                or (rname in joined_set and rname not in banned_set)
            ):
                joined_rooms.append(rname)

        if LOBBY_ROOM not in visible_rooms:
            visible_rooms.insert(0, LOBBY_ROOM)
            owners[LOBBY_ROOM] = ""
            joined_rooms.insert(0, LOBBY_ROOM)

        return {
            "rooms": visible_rooms,
            "owners": owners,
            "private_rooms": private_rooms,
            "joined_rooms": joined_rooms,
        }

    def leave_room(self, room_name: str, username: str) -> Tuple[bool, str]:
        norm = normalize_room_name(room_name)
        clean_user = username.strip()
        if not norm or norm == LOBBY_ROOM:
            return False, "Không thể rời khỏi sảnh mặc định #chung."

        with self._lock:
            row = self._conn.execute(
                "SELECT owner FROM rooms WHERE name = ?;", (norm,)
            ).fetchone()
            if row is None:
                return False, f"Phòng #{norm} không tồn tại."
            if str(row["owner"]) == clean_user:
                return (
                    False,
                    f"Bạn là Admin tạo phòng #{norm}. Hãy dùng nút 'Xóa phòng' nếu muốn giải tán phòng.",
                )

            self._conn.execute(
                "DELETE FROM room_members WHERE room = ? AND username = ?;",
                (norm, clean_user),
            )
            self._conn.execute(
                "INSERT OR IGNORE INTO user_hidden_rooms(username, room) VALUES (?, ?);",
                (clean_user, norm),
            )
            self._set_last_room_unlocked(clean_user, LOBBY_ROOM)
            self._conn.commit()
        return True, ""

    def delete_room(self, room_name: str, requester: str) -> Tuple[bool, str]:
        norm = normalize_room_name(room_name)
        clean_user = requester.strip()
        if not norm or norm == LOBBY_ROOM:
            return False, "Không thể xóa sảnh mặc định #chung."

        with self._lock:
            row = self._conn.execute(
                "SELECT owner FROM rooms WHERE name = ?;", (norm,)
            ).fetchone()
            if row is None:
                return False, f"Phòng #{norm} không tồn tại."

            owner = str(row["owner"])
            if not owner:
                return (
                    False,
                    f"Phòng #{norm} là phòng hệ thống có sẵn, không thể xóa (bạn có thể bấm 'Rời phòng').",
                )
            if owner != clean_user:
                return (
                    False,
                    f"Chỉ Admin tạo phòng ({owner}) mới có quyền xóa phòng #{norm}.",
                )

            for table in ("rooms", "messages", "room_members", "room_bans", "join_requests", "user_hidden_rooms"):
                col = "name" if table == "rooms" else "room"
                self._conn.execute(f"DELETE FROM {table} WHERE {col} = ?;", (norm,))
            self._conn.execute(
                "UPDATE user_last_room SET room = ? WHERE room = ?;",
                (LOBBY_ROOM, norm),
            )
            self._conn.commit()
        return True, ""

    def save_chat(
        self,
        room: str,
        sender: str,
        text: str,
        timestamp: Optional[str] = None,
    ) -> int:
        ts = timestamp or _now_str()
        norm_room, _, _ = self.ensure_room(room)
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
        norm_room, _, _ = self.ensure_room(room)
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
