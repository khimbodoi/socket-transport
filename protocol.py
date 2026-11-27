"""Tầng giao thức ứng dụng: Điều khiển bằng JSON UTF-8 + Truyền file nhị phân (raw bytes) trên payload TCP."""

import json
import re
from typing import Tuple

from transport import MAX_SIZE

VERSION = 1
ALLOWED_TYPES = {
    "hello",
    "chat",
    "bye",
    "error",
    "join_room",
    "leave_room",
    "delete_room",
    "room_list",
    "join_request",
    "approve_join",
    "kick_member",
    "get_members",
    "member_list",
    "file_start",
    "file_end",
    "file_saved",
    "recall",
}

BINARY_MAGIC = b"\x89BIN"
FILE_CHUNK_SIZE = 48 * 1024  # 48 KiB mỗi mảnh (< giới hạn 64 KiB của transport.py)
MAX_FILE_SIZE = 5 * 1024 * 1024  # Tối đa 5 MB mỗi file/ảnh


class ProtocolError(ValueError):
    """Payload không đúng đặc tả giao thức."""


def normalize_room_name(raw_name: str) -> str:
    """Chuẩn hóa tên phòng chat (ví dụ '#Học Tập' -> 'học-tập')."""
    cleaned = raw_name.strip().lstrip("#").strip().lower()
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"[^\w\-]", "", cleaned, flags=re.UNICODE)
    return cleaned[:32]


def _validate_str_list(val: object, field_name: str, allow_empty_items: bool = False) -> None:
    if not isinstance(val, list) or not all(
        isinstance(item, str) and (allow_empty_items or item.strip()) for item in val
    ):
        raise ProtocolError(f"{field_name} phải là danh sách chuỗi hợp lệ")


def _validate_message(message) -> None:
    if not isinstance(message, dict):
        raise ProtocolError("Thông điệp phải là JSON object")

    if message.get("version") != VERSION:
        raise ProtocolError("version không được hỗ trợ")

    msg_type = message.get("type")
    if msg_type not in ALLOWED_TYPES:
        raise ProtocolError("type không hợp lệ")

    common = {"version", "type"}
    if "message_id" in message:
        if not isinstance(message["message_id"], str) or not message["message_id"]:
            raise ProtocolError("message_id phải là chuỗi không rỗng")
        common.add("message_id")

    required_by_type = {
        "hello": {"username"},
        "chat": {"text"},
        "bye": set(),
        "error": {"code", "message"},
        "join_room": {"room"},
        "leave_room": {"room"},
        "delete_room": {"room"},
        "room_list": {"rooms", "current_room"},
        "join_request": {"room", "username"},
        "approve_join": {"room", "target", "approve"},
        "kick_member": {"room", "target"},
        "get_members": {"room"},
        "member_list": {"room", "members"},
        "file_start": {"file_id", "filename", "size", "total_chunks"},
        "file_end": {"file_id"},
        "file_saved": {"file_id", "msg_id"},
        "recall": {"msg_id"},
    }
    optional_by_type = {
        "hello": {"room"},
        "chat": {"sender", "room", "timestamp", "system", "history", "msg_id", "recalled"},
        "bye": set(),
        "error": set(),
        "join_room": {"is_private"},
        "leave_room": set(),
        "delete_room": set(),
        "room_list": {"owners", "private_rooms", "joined_rooms"},
        "join_request": {"timestamp"},
        "approve_join": set(),
        "kick_member": set(),
        "get_members": set(),
        "member_list": {"owner", "is_private", "online_members", "pending_requests"},
        "file_start": {"sender", "room", "timestamp", "history", "msg_id"},
        "file_end": set(),
        "file_saved": {"room", "timestamp"},
        "recall": {"sender", "room", "timestamp"},
    }

    allowed = common | required_by_type[msg_type] | optional_by_type[msg_type]
    missing = required_by_type[msg_type] - message.keys()
    extra = message.keys() - allowed
    if missing:
        raise ProtocolError("Thiếu trường: " + ", ".join(sorted(missing)))
    if extra:
        raise ProtocolError("Trường không được hỗ trợ: " + ", ".join(sorted(extra)))

    str_fields = {
        "username",
        "text",
        "code",
        "message",
        "room",
        "current_room",
        "file_id",
        "filename",
        "sender",
        "timestamp",
        "target",
        "owner",
    }
    for field in str_fields & message.keys():
        if not isinstance(message[field], str):
            raise ProtocolError(f"{field} phải là chuỗi")

    for bool_field in {"system", "history", "recalled", "is_private", "approve"} & message.keys():
        if not isinstance(message[bool_field], bool):
            raise ProtocolError(f"{bool_field} phải là kiểu bool")

    if "msg_id" in message:
        mid = message["msg_id"]
        if isinstance(mid, bool) or not isinstance(mid, int) or mid <= 0:
            raise ProtocolError("msg_id phải là số nguyên dương")

    if msg_type in {"hello", "join_request"} and not message["username"].strip():
        raise ProtocolError("username không được để trống")
    if msg_type == "error" and not message["code"].strip():
        raise ProtocolError("code không được để trống")
    if msg_type in {
        "join_room",
        "leave_room",
        "delete_room",
        "join_request",
        "approve_join",
        "kick_member",
        "get_members",
        "member_list",
    } and not normalize_room_name(message["room"]):
        raise ProtocolError("room không hợp lệ hoặc để trống")

    if msg_type in {"approve_join", "kick_member"} and not message["target"].strip():
        raise ProtocolError("target không được để trống")

    if msg_type == "room_list":
        _validate_str_list(message["rooms"], "rooms")
        if not message["current_room"].strip():
            raise ProtocolError("current_room không được để trống")
        if "owners" in message:
            owners = message["owners"]
            if not isinstance(owners, dict) or not all(
                isinstance(k, str) and isinstance(v, str) for k, v in owners.items()
            ):
                raise ProtocolError("owners phải là dict ánh xạ tên phòng -> tên admin")
        if "private_rooms" in message:
            _validate_str_list(message["private_rooms"], "private_rooms")
        if "joined_rooms" in message:
            _validate_str_list(message["joined_rooms"], "joined_rooms")

    if msg_type == "member_list":
        _validate_str_list(message["members"], "members")
        if "online_members" in message:
            _validate_str_list(message["online_members"], "online_members")
        if "pending_requests" in message:
            _validate_str_list(message["pending_requests"], "pending_requests")

    if msg_type == "file_start":
        if not message["file_id"].strip() or not message["filename"].strip():
            raise ProtocolError("file_id và filename không được để trống")
        size = message["size"]
        total_chunks = message["total_chunks"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise ProtocolError("size phải là số nguyên không âm")
        if size > MAX_FILE_SIZE:
            raise ProtocolError(f"Kích thước file vượt giới hạn {MAX_FILE_SIZE} byte")
        if (
            isinstance(total_chunks, bool)
            or not isinstance(total_chunks, int)
            or total_chunks < 0
        ):
            raise ProtocolError("total_chunks phải là số nguyên không âm")

    if msg_type in {"file_end", "file_saved"} and not message["file_id"].strip():
        raise ProtocolError("file_id không được để trống")


def encode_message(message: dict) -> bytes:
    """Kiểm tra cấu trúc, JSON-encode và trả về payload bytes UTF-8."""
    _validate_message(message)
    try:
        payload = json.dumps(
            message, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, UnicodeEncodeError) as exc:
        raise ProtocolError(f"Không thể mã hóa thông điệp: {exc}") from exc

    if len(payload) > MAX_SIZE:
        raise ProtocolError(f"Payload vượt giới hạn {MAX_SIZE} byte")
    return payload


def decode_message(payload: bytes) -> dict:
    """Nhận payload bytes từ transport, decode UTF-8 và kiểm tra JSON/schema."""
    if not isinstance(payload, bytes):
        raise ProtocolError("Payload đầu vào phải có kiểu bytes")
    if len(payload) > MAX_SIZE:
        raise ProtocolError(f"Payload vượt giới hạn {MAX_SIZE} byte")

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("Payload không phải UTF-8 hợp lệ") from exc

    try:
        message = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError("Payload không phải JSON hợp lệ") from exc

    _validate_message(message)
    return message


def make_message(msg_type: str, **fields) -> dict:
    """Hàm tiện ích tạo message có version thống nhất."""
    return {"version": VERSION, "type": msg_type, **fields}


def is_binary_frame(payload: bytes) -> bool:
    return isinstance(payload, (bytes, bytearray)) and bytes(payload).startswith(
        BINARY_MAGIC
    )


def encode_binary_chunk(file_id: str, chunk_index: int, data: bytes) -> bytes:
    if not isinstance(data, (bytes, bytearray)):
        raise ProtocolError("Dữ liệu chunk phải có kiểu bytes")
    fid_bytes = file_id.strip().encode("utf-8")
    if not (1 <= len(fid_bytes) <= 64):
        raise ProtocolError("file_id nhị phân phải dài từ 1 đến 64 byte")
    if isinstance(chunk_index, bool) or not isinstance(chunk_index, int) or chunk_index < 0:
        raise ProtocolError("chunk_index phải là số nguyên không âm")

    frame = (
        BINARY_MAGIC
        + len(fid_bytes).to_bytes(1, byteorder="big")
        + fid_bytes
        + chunk_index.to_bytes(4, byteorder="big")
        + bytes(data)
    )
    if len(frame) > MAX_SIZE:
        raise ProtocolError(f"Frame nhị phân vượt giới hạn {MAX_SIZE} byte")
    return frame


def decode_binary_chunk(payload: bytes) -> Tuple[str, int, bytes]:
    if not isinstance(payload, (bytes, bytearray)):
        raise ProtocolError("Frame nhị phân phải có kiểu bytes")
    raw = bytes(payload)
    if len(raw) > MAX_SIZE:
        raise ProtocolError(f"Frame nhị phân vượt giới hạn {MAX_SIZE} byte")
    if len(raw) < 9 or not raw.startswith(BINARY_MAGIC):
        raise ProtocolError("Thiếu header nhận diện frame nhị phân")

    id_len = raw[4]
    header_end = 5 + id_len + 4
    if id_len == 0 or len(raw) < header_end:
        raise ProtocolError("Frame nhị phân bị cắt cụt header")

    try:
        file_id = raw[5 : 5 + id_len].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError("file_id trong frame nhị phân không hợp lệ") from exc

    chunk_index = int.from_bytes(raw[5 + id_len : header_end], byteorder="big")
    chunk_data = raw[header_end:]
    return file_id, chunk_index, chunk_data
