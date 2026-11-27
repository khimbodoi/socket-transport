"""Giao diện người dùng (GUI Client) cho Đồ án Socket TCP - Phần của Minh Khang.

Đầy đủ 4 tính năng nâng cao trên nền `transport.py` + `protocol.py`:
1. Chia phòng chat (Channels): Tạo phòng mới & chuyển phòng trực tiếp ở thanh bên trái.
2. Gửi File / Hình ảnh nhị phân (bytes): Truyền mảnh nhị phân qua socket, xem trước ảnh trực tiếp & nút tải về.
3. Lịch sử tin nhắn SQLite: Tự động nạp lại tin nhắn, hình ảnh/file cũ & trạng thái thu hồi khi đăng nhập lại.
4. Thu hồi tin nhắn & File/Ảnh (Recall/Unsend): Nút '↩ Thu hồi' xóa nội dung/ảnh tại chỗ trên mọi màn hình theo thời gian thực.

Chạy chương trình:
    py gui_client.py --name "Minh Khang"
    py gui_client.py --name "Thái Hoà" --room hoc-tap
"""

import argparse
import ctypes
from dataclasses import dataclass, field
from datetime import datetime
import io
import json
import os
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional, Tuple

from network_client import (
    EVENT_CONNECTED,
    EVENT_DISCONNECTED,
    EVENT_ERROR,
    EVENT_FILE_SENT,
    EVENT_MESSAGE,
    SocketClient,
)
from protocol import (
    MAX_FILE_SIZE,
    ProtocolError,
    decode_binary_chunk,
    decode_message,
    encode_message,
    is_binary_frame,
    make_message,
    normalize_room_name,
)
from storage import DEFAULT_ROOMS
from transport import MAX_SIZE

try:
    from PIL import Image, ImageTk

    HAS_PIL = True
except Exception:
    HAS_PIL = False

POLL_INTERVAL_MS = 40
MAX_PREVIEW_DIM = 240


def now_hhmmss() -> str:
    return datetime.now().strftime("%H:%M:%S")


def format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f} KB"
    return f"{num_bytes / (1024 * 1024):.2f} MB"


def valid_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as err:
        raise argparse.ArgumentTypeError("Port phải là số nguyên") from err
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port phải nằm trong khoảng 1 đến 65535")
    return port


def measure_chat_bytes(text: str) -> int:
    raw = json.dumps(
        make_message("chat", text=text), ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return len(raw)


def parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TCP Socket Chat & File GUI Client")
    parser.add_argument("--host", "--h", default="127.0.0.1", help="Địa chỉ IPv4 của server")
    parser.add_argument(
        "--port", "--p", type=valid_port, default=65432, help="Port của server (1..65535)"
    )
    parser.add_argument(
        "--name", "--n", default="Minh Khang", help="Tên người dùng hiển thị"
    )
    parser.add_argument(
        "--room", "--r", default="chung", help="Phòng chat ban đầu khi kết nối"
    )
    return parser.parse_args(argv)


def enable_windows_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


@dataclass
class PendingFileDownload:
    file_id: str
    filename: str
    expected_size: int
    total_chunks: int
    sender: str
    room: str
    timestamp: str
    msg_id: Optional[int] = None
    is_history: bool = False
    chunks: Dict[int, bytes] = field(default_factory=dict)


class ChatClientGUI:
    """Cửa sổ giao diện Chat đa phòng, gửi file nhị phân, lịch sử SQLite & thu hồi tin nhắn."""

    def __init__(
        self,
        root: tk.Tk,
        initial_host: str = "127.0.0.1",
        initial_port: int = 65432,
        initial_name: str = "Minh Khang",
        initial_room: str = "chung",
    ) -> None:
        self.root = root
        self.root.title("TCP Socket Chat — Đồ án Mạng máy tính")
        self.root.configure(bg="#F1F5F9")

        self.client = SocketClient()
        self.active_username = initial_name
        self.current_room = normalize_room_name(initial_room) or "chung"
        self.rooms: List[str] = list(DEFAULT_ROOMS)
        if self.current_room not in self.rooms:
            self.rooms.append(self.current_room)

        self._updating_room_listbox = False
        self._incoming_files: Dict[str, PendingFileDownload] = {}
        self._outgoing_files: Dict[str, Tuple[str, bytes]] = {}
        self._rendered_meta: Dict[int, Dict[str, object]] = {}
        self._widget_refs: List[object] = []

        self.host_var = tk.StringVar(value=initial_host)
        self.port_var = tk.StringVar(value=str(initial_port))
        self.name_var = tk.StringVar(value=initial_name)
        self.new_room_var = tk.StringVar()
        self.status_var = tk.StringVar(value="● Chưa kết nối")
        self.room_banner_var = tk.StringVar(
            value=f"#{self.current_room} — Lịch sử SQLite & Trò chuyện trực tuyến"
        )
        self.byte_counter_var = tk.StringVar()

        self._setup_styles()
        self._build_layout()
        self._populate_room_listbox()
        self._apply_dpi_safe_geometry()
        self._bind_events()
        self._refresh_ui_state()
        self._update_byte_counter()

        self.append_system_log(
            "Sẵn sàng. Chạy 'py chat_server.py', nhập tên rồi bấm 'Kết nối' để trò chuyện."
        )
        self.root.after(POLL_INTERVAL_MS, self._poll_network_events)

    def _setup_styles(self) -> None:
        style = ttk.Style(self.root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
        elif "clam" in style.theme_names():
            style.theme_use("clam")

        style.configure("Field.TLabel", background="#FFFFFF", foreground="#334155", font=("Segoe UI", 9))
        style.configure("SidebarTitle.TLabel", background="#FFFFFF", foreground="#0F172A", font=("Segoe UI", 9, "bold"))
        style.configure("Hint.TLabel", background="#F1F5F9", foreground="#64748B", font=("Segoe UI", 9))
        style.configure("Action.TButton", font=("Segoe UI", 9, "bold"), padding=(12, 4))
        style.configure("Secondary.TButton", font=("Segoe UI", 9), padding=(8, 3))
        style.configure("Inline.TButton", font=("Segoe UI", 8), padding=(5, 1))

    def _build_layout(self) -> None:
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        # 1. Thanh kết nối trên cùng
        top_card = tk.Frame(
            self.root,
            bg="#FFFFFF",
            highlightbackground="#CBD5E1",
            highlightthickness=1,
            padx=12,
            pady=10,
        )
        top_card.grid(row=0, column=0, sticky="ew", padx=12, pady=(12, 6))
        top_card.columnconfigure(5, weight=1)

        ttk.Label(top_card, text="IP:", style="Field.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 4), pady=(0, 6)
        )
        self.host_entry = ttk.Entry(
            top_card, textvariable=self.host_var, width=13, font=("Segoe UI", 9)
        )
        self.host_entry.grid(row=0, column=1, sticky="w", padx=(0, 10), pady=(0, 6))

        ttk.Label(top_card, text="Port:", style="Field.TLabel").grid(
            row=0, column=2, sticky="w", padx=(0, 4), pady=(0, 6)
        )
        self.port_entry = ttk.Entry(
            top_card, textvariable=self.port_var, width=7, font=("Segoe UI", 9)
        )
        self.port_entry.grid(row=0, column=3, sticky="w", padx=(0, 10), pady=(0, 6))

        ttk.Label(top_card, text="Tên:", style="Field.TLabel").grid(
            row=0, column=4, sticky="w", padx=(0, 4), pady=(0, 6)
        )
        self.name_entry = ttk.Entry(
            top_card, textvariable=self.name_var, width=14, font=("Segoe UI", 9)
        )
        self.name_entry.grid(row=0, column=5, sticky="ew", padx=(0, 10), pady=(0, 6))

        self.connect_btn = ttk.Button(
            top_card,
            text="Kết nối",
            style="Action.TButton",
            command=self.toggle_connection,
        )
        self.connect_btn.grid(row=0, column=6, sticky="ew", pady=(0, 6))

        self.status_label = tk.Label(
            top_card,
            textvariable=self.status_var,
            bg="#FFFFFF",
            fg="#64748B",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        )
        self.status_label.grid(row=1, column=0, columnspan=6, sticky="ew")

        self.clear_btn = ttk.Button(
            top_card,
            text="Xóa màn hình",
            style="Secondary.TButton",
            command=self.clear_chat_history,
        )
        self.clear_btn.grid(row=1, column=6, sticky="ew")

        # 2. Thân chính: Cột trái (Danh sách Phòng) + Cột phải (Khung Chat)
        body_frame = tk.Frame(self.root, bg="#F1F5F9")
        body_frame.grid(row=1, column=0, sticky="nsew", padx=12, pady=4)
        body_frame.columnconfigure(1, weight=1)
        body_frame.rowconfigure(0, weight=1)

        sidebar_card = tk.Frame(
            body_frame,
            bg="#FFFFFF",
            highlightbackground="#CBD5E1",
            highlightthickness=1,
            padx=10,
            pady=10,
        )
        sidebar_card.grid(row=0, column=0, sticky="ns", padx=(0, 8))
        sidebar_card.rowconfigure(1, weight=1)

        ttk.Label(sidebar_card, text="PHÒNG CHAT", style="SidebarTitle.TLabel").grid(
            row=0, column=0, sticky="w", pady=(0, 6)
        )

        self.room_listbox = tk.Listbox(
            sidebar_card,
            width=18,
            exportselection=False,
            activestyle="none",
            bg="#F8FAFC",
            fg="#0F172A",
            selectbackground="#2563EB",
            selectforeground="#FFFFFF",
            font=("Segoe UI", 10),
            relief="flat",
            highlightbackground="#E2E8F0",
            highlightthickness=1,
        )
        self.room_listbox.grid(row=1, column=0, sticky="nsew", pady=(0, 8))

        ttk.Label(sidebar_card, text="Tạo / vào phòng:", style="Field.TLabel").grid(
            row=2, column=0, sticky="w", pady=(0, 4)
        )
        self.new_room_entry = ttk.Entry(
            sidebar_card, textvariable=self.new_room_var, width=18, font=("Segoe UI", 9)
        )
        self.new_room_entry.grid(row=3, column=0, sticky="ew", pady=(0, 6))

        self.join_room_btn = ttk.Button(
            sidebar_card,
            text="+ Tạo / Tham gia",
            style="Secondary.TButton",
            command=self.create_or_join_room,
        )
        self.join_room_btn.grid(row=4, column=0, sticky="ew")

        chat_card = tk.Frame(
            body_frame,
            bg="#FFFFFF",
            highlightbackground="#CBD5E1",
            highlightthickness=1,
        )
        chat_card.grid(row=0, column=1, sticky="nsew")
        chat_card.columnconfigure(0, weight=1)
        chat_card.rowconfigure(1, weight=1)

        banner = tk.Frame(chat_card, bg="#EFF6FF", padx=12, pady=6)
        banner.grid(row=0, column=0, columnspan=2, sticky="ew")
        tk.Label(
            banner,
            textvariable=self.room_banner_var,
            bg="#EFF6FF",
            fg="#1E40AF",
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill="x")

        self.chat_text = tk.Text(
            chat_card,
            wrap="word",
            state="disabled",
            bg="#FFFFFF",
            fg="#0F172A",
            font=("Segoe UI", 10),
            relief="flat",
            padx=12,
            pady=10,
            spacing1=3,
            spacing3=6,
            cursor="arrow",
        )
        self.chat_text.grid(row=1, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(chat_card, orient="vertical", command=self.chat_text.yview)
        scrollbar.grid(row=1, column=1, sticky="ns")
        self.chat_text.configure(yscrollcommand=scrollbar.set)

        self.chat_text.tag_configure("time", foreground="#64748B", font=("Consolas", 9))
        self.chat_text.tag_configure("history_badge", foreground="#7C3AED", font=("Consolas", 9, "bold"))
        self.chat_text.tag_configure("self_sender", foreground="#1D4ED8", font=("Segoe UI", 10, "bold"))
        self.chat_text.tag_configure("peer_sender", foreground="#047857", font=("Segoe UI", 10, "bold"))
        self.chat_text.tag_configure("body", foreground="#0F172A", font=("Segoe UI", 10))
        self.chat_text.tag_configure("recalled", foreground="#94A3B8", font=("Segoe UI", 10, "italic"))
        self.chat_text.tag_configure("file_meta", foreground="#0369A1", font=("Segoe UI", 9, "bold"))
        self.chat_text.tag_configure("system", foreground="#475569", font=("Segoe UI", 9, "italic"))
        self.chat_text.tag_configure("error", foreground="#DC2626", font=("Segoe UI", 9, "bold"))

        # 3. Khung nhập tin & đính kèm File/Ảnh phía dưới
        bottom_frame = tk.Frame(self.root, bg="#F1F5F9")
        bottom_frame.grid(row=2, column=0, sticky="ew", padx=12, pady=(4, 12))
        bottom_frame.columnconfigure(0, weight=1)

        composer_card = tk.Frame(
            bottom_frame,
            bg="#FFFFFF",
            highlightbackground="#CBD5E1",
            highlightthickness=1,
            padx=8,
            pady=8,
        )
        composer_card.grid(row=0, column=0, sticky="ew")
        composer_card.columnconfigure(1, weight=1)

        self.attach_btn = ttk.Button(
            composer_card,
            text="📎 File / Ảnh",
            style="Secondary.TButton",
            command=self.choose_and_send_file,
        )
        self.attach_btn.grid(row=0, column=0, sticky="ns", padx=(0, 8))

        self.input_text = tk.Text(
            composer_card,
            height=3,
            wrap="word",
            font=("Segoe UI", 10),
            bg="#FFFFFF",
            fg="#0F172A",
            relief="flat",
            padx=4,
            pady=2,
            undo=True,
        )
        self.input_text.grid(row=0, column=1, sticky="ew", padx=(0, 8))

        self.send_btn = ttk.Button(
            composer_card,
            text="Gửi tin",
            style="Action.TButton",
            command=self.send_current_message,
        )
        self.send_btn.grid(row=0, column=2, sticky="ns")

        info_bar = tk.Frame(bottom_frame, bg="#F1F5F9")
        info_bar.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        info_bar.columnconfigure(0, weight=1)

        ttk.Label(
            info_bar,
            text="Enter: Gửi  |  Shift+Enter: Xuống dòng  |  ↩ Thu hồi cạnh tin đã gửi  |  /quit: Rời phòng",
            style="Hint.TLabel",
        ).grid(row=0, column=0, sticky="w")

        self.byte_counter_label = tk.Label(
            info_bar,
            textvariable=self.byte_counter_var,
            bg="#F1F5F9",
            fg="#475569",
            font=("Consolas", 9),
        )
        self.byte_counter_label.grid(row=0, column=1, sticky="e", padx=(8, 2))

    def _apply_dpi_safe_geometry(self) -> None:
        self.root.update_idletasks()
        dpi_scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        req_w = self.root.winfo_reqwidth()
        req_h = self.root.winfo_reqheight()

        scr_w = self.root.winfo_screenwidth()
        scr_h = self.root.winfo_screenheight()

        min_w = min(req_w + int(24 * dpi_scale), int(scr_w * 0.92))
        min_h = min(max(req_h, int(460 * dpi_scale)), int(scr_h * 0.88))
        init_w = min(max(min_w, int(800 * dpi_scale)), int(scr_w * 0.92))
        init_h = min(max(min_h, int(560 * dpi_scale)), int(scr_h * 0.88))

        self.root.minsize(min_w, min_h)
        self.root.geometry(f"{init_w}x{init_h}")

    def _bind_events(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.input_text.bind("<Return>", self._on_enter_pressed)
        self.input_text.bind("<Shift-Return>", lambda _e: None)
        self.input_text.bind("<KeyRelease>", lambda _e: self._update_byte_counter())
        self.input_text.bind("<<Modified>>", self._on_text_modified)
        self.room_listbox.bind("<<ListboxSelect>>", self._on_room_selected)
        self.new_room_entry.bind("<Return>", lambda _e: self.create_or_join_room())
        self.host_entry.bind("<Return>", lambda _e: self.toggle_connection())
        self.port_entry.bind("<Return>", lambda _e: self.toggle_connection())
        self.name_entry.bind("<Return>", lambda _e: self.toggle_connection())

    def _populate_room_listbox(self) -> None:
        self._updating_room_listbox = True
        try:
            self.room_listbox.delete(0, "end")
            selected_idx = 0
            for idx, r in enumerate(self.rooms):
                self.room_listbox.insert("end", f"  # {r}")
                if r == self.current_room:
                    selected_idx = idx
            if self.rooms:
                self.room_listbox.selection_clear(0, "end")
                self.room_listbox.selection_set(selected_idx)
                self.room_listbox.see(selected_idx)
            self.room_banner_var.set(
                f"#{self.current_room} — Lịch sử SQLite & Trò chuyện trực tuyến"
            )
        finally:
            self._updating_room_listbox = False

    def _on_room_selected(self, _event: tk.Event) -> None:
        if self._updating_room_listbox:
            return
        sel = self.room_listbox.curselection()
        if not sel:
            return
        idx = int(sel[0])
        if 0 <= idx < len(self.rooms):
            chosen = self.rooms[idx]
            if chosen != self.current_room:
                self._switch_to_room(chosen)

    def create_or_join_room(self) -> None:
        raw_room = self.new_room_var.get()
        norm = normalize_room_name(raw_room)
        if not norm:
            self.append_error_log("Vui lòng nhập tên phòng hợp lệ (ví dụ: nhom-5, on-thi).")
            self.new_room_entry.focus_set()
            return
        self.new_room_var.set("")
        if norm not in self.rooms:
            self.rooms.append(norm)
        self._switch_to_room(norm)

    def _switch_to_room(self, target_room: str) -> None:
        self.current_room = target_room
        self._populate_room_listbox()
        self._refresh_ui_state()

        if not self.client.is_connected:
            self.append_system_log(
                f"Đã chọn trước phòng #{target_room}. Bấm 'Kết nối' để vào phòng."
            )
            return

        self.clear_chat_history()
        try:
            pkt = encode_message(make_message("join_room", room=target_room))
            self.client.send_payload(pkt)
        except Exception as err:
            self.append_error_log(f"Không thể chuyển sang phòng #{target_room}: {err}")

    def _on_text_modified(self, _event: tk.Event) -> None:
        if self.input_text.edit_modified():
            self._update_byte_counter()
            self.input_text.edit_modified(False)

    def _on_enter_pressed(self, event: tk.Event) -> Optional[str]:
        if getattr(event, "state", 0) & 0x1:
            return None
        self.send_current_message()
        return "break"

    def _get_input_content(self) -> str:
        return self.input_text.get("1.0", "end-1c")

    def _clear_input(self) -> None:
        self.input_text.delete("1.0", "end")
        self._update_byte_counter()

    def _update_byte_counter(self) -> None:
        byte_len = measure_chat_bytes(self._get_input_content())
        self.byte_counter_var.set(f"{byte_len:,} / {MAX_SIZE:,} byte")
        if byte_len > MAX_SIZE:
            self.byte_counter_label.configure(fg="#DC2626", font=("Consolas", 9, "bold"))
        else:
            self.byte_counter_label.configure(fg="#475569", font=("Consolas", 9))

    def _refresh_ui_state(self, endpoint: str = "") -> None:
        if self.client.is_connecting:
            self.connect_btn.configure(text="Đang nối...", state="disabled")
            self.send_btn.configure(state="disabled")
            self.attach_btn.configure(state="disabled")
            self.host_entry.configure(state="disabled")
            self.port_entry.configure(state="disabled")
            self.name_entry.configure(state="disabled")
            self.status_var.set("● Đang kết nối...")
            self.status_label.configure(fg="#D97706")
        elif self.client.is_connected:
            self.connect_btn.configure(text="Ngắt kết nối", state="normal")
            self.send_btn.configure(state="normal")
            self.attach_btn.configure(state="normal")
            self.host_entry.configure(state="disabled")
            self.port_entry.configure(state="disabled")
            self.name_entry.configure(state="disabled")
            target = f" ({endpoint})" if endpoint else ""
            self.status_var.set(
                f"● Đã kết nối{target} — {self.active_username}  |  Phòng: #{self.current_room}"
            )
            self.status_label.configure(fg="#16A34A")
        else:
            self.connect_btn.configure(text="Kết nối", state="normal")
            self.send_btn.configure(state="disabled")
            self.attach_btn.configure(state="disabled")
            self.host_entry.configure(state="normal")
            self.port_entry.configure(state="normal")
            self.name_entry.configure(state="normal")
            self.status_var.set(f"● Chưa kết nối (Phòng chờ: #{self.current_room})")
            self.status_label.configure(fg="#64748B")

    def _graceful_disconnect(self) -> None:
        self._incoming_files.clear()
        self._outgoing_files.clear()
        if self.client.is_connected:
            try:
                self.client.send_payload(encode_message(make_message("bye")))
            except Exception:
                pass
        self.client.disconnect()
        self._refresh_ui_state()

    def toggle_connection(self) -> None:
        if self.client.is_connected or self.client.is_connecting:
            self._graceful_disconnect()
            return

        host = self.host_var.get().strip()
        port_raw = self.port_var.get().strip()
        username = self.name_var.get().strip()

        if not host:
            self.append_error_log("Vui lòng nhập địa chỉ IP của server.")
            self.host_entry.focus_set()
            return

        try:
            port = valid_port(port_raw)
        except argparse.ArgumentTypeError as err:
            self.append_error_log(str(err))
            self.port_entry.focus_set()
            return

        if not username:
            self.append_error_log("Tên hiển thị không được để trống.")
            self.name_entry.focus_set()
            return

        self.active_username = username
        self.clear_chat_history()
        if self.client.connect_async(host, port, timeout=5.0):
            self.append_system_log(f"Đang kết nối tới {host}:{port}...")
            self._refresh_ui_state()

    def choose_and_send_file(self) -> None:
        if not self.client.is_connected:
            self.append_error_log("Hãy kết nối tới server trước khi gửi file/hình ảnh.")
            return

        filepath = filedialog.askopenfilename(
            parent=self.root,
            title="Chọn hình ảnh hoặc tệp cần gửi qua Socket",
            filetypes=[
                ("Tất cả tệp & hình ảnh", "*.*"),
                ("Hình ảnh (PNG, GIF, JPG)", "*.png *.gif *.jpg *.jpeg *.bmp *.webp"),
                ("Tài liệu & Mã nguồn", "*.pdf *.txt *.md *.py *.zip *.docx"),
            ],
        )
        if not filepath:
            return

        try:
            file_size = os.path.getsize(filepath)
            if file_size > MAX_FILE_SIZE:
                self.append_error_log(
                    f"Tệp quá lớn ({format_size(file_size)}). Giới hạn tối đa là {format_size(MAX_FILE_SIZE)}."
                )
                return
            with open(filepath, "rb") as f:
                raw_bytes = f.read()
        except OSError as err:
            self.append_error_log(f"Không thể đọc tệp: {err}")
            return

        filename = os.path.basename(filepath)
        try:
            file_id = self.client.send_file_async(filename, raw_bytes)
            self._outgoing_files[file_id] = (filename, raw_bytes)
            self.append_system_log(
                f"Đang truyền nhị phân '{filename}' ({format_size(len(raw_bytes))}) vào phòng #{self.current_room}..."
            )
        except Exception as err:
            self.append_error_log(str(err))

    def request_recall(self, msg_id: int) -> None:
        """Gửi yêu cầu thu hồi tin nhắn hoặc tệp theo `msg_id` lên server."""
        if not self.client.is_connected:
            self.append_error_log("Cần đang kết nối với server để thu hồi tin nhắn.")
            return
        try:
            pkt = encode_message(make_message("recall", msg_id=msg_id))
            self.client.send_payload(pkt)
        except Exception as err:
            self.append_error_log(f"Không thể gửi yêu cầu thu hồi: {err}")

    def send_current_message(self) -> None:
        content = self._get_input_content()
        stripped = content.strip()

        if stripped == "/quit":
            self._clear_input()
            if self.client.is_connected or self.client.is_connecting:
                self._graceful_disconnect()
            else:
                self.append_system_log("Hiện chưa có kết nối nào đang mở.")
            return

        if stripped.startswith("/join "):
            target = normalize_room_name(stripped[6:])
            self._clear_input()
            if target:
                if target not in self.rooms:
                    self.rooms.append(target)
                self._switch_to_room(target)
            return

        if not self.client.is_connected:
            self.append_error_log("Chưa kết nối tới server. Hãy bấm 'Kết nối' trước.")
            return

        if not stripped:
            return

        try:
            payload = encode_message(make_message("chat", text=content))
        except ProtocolError as err:
            self.append_error_log(str(err))
            return

        try:
            sent_ok = self.client.send_payload(payload)
        except (ValueError, ConnectionError) as err:
            self.append_error_log(str(err))
            self._refresh_ui_state()
            return

        if sent_ok:
            self._clear_input()
            self.input_text.focus_set()

    def _poll_network_events(self) -> None:
        try:
            while True:
                event = self.client.events.get_nowait()
                self._handle_network_event(event.type, event.data)
        except queue.Empty:
            pass
        finally:
            if self.root.winfo_exists():
                self.root.after(POLL_INTERVAL_MS, self._poll_network_events)

    def _handle_network_event(self, event_type: str, data: object) -> None:
        if event_type == EVENT_CONNECTED:
            endpoint = str(data or "")
            self._refresh_ui_state(endpoint=endpoint)
            try:
                hello_bytes = encode_message(
                    make_message(
                        "hello", username=self.active_username, room=self.current_room
                    )
                )
                self.client.send_payload(hello_bytes)
            except Exception as err:
                self.append_error_log(f"Không thể gửi gói hello: {err}")
            self.input_text.focus_set()

        elif event_type == EVENT_FILE_SENT:
            # Đợi gói file_saved kèm msg_id từ server để gắn nút Thu hồi chính xác
            return

        elif event_type == EVENT_MESSAGE:
            raw_bytes = bytes(data) if isinstance(data, (bytes, bytearray)) else b""
            if is_binary_frame(raw_bytes):
                self._handle_incoming_binary_chunk(raw_bytes)
                return

            try:
                msg = decode_message(raw_bytes)
            except ProtocolError as err:
                self.append_error_log(f"Gói tin nhận về sai chuẩn protocol.py: {err}")
                return

            self._handle_decoded_json_message(msg)

        elif event_type == EVENT_DISCONNECTED:
            self._incoming_files.clear()
            self._outgoing_files.clear()
            self.root.title("TCP Socket Chat — Đồ án Mạng máy tính")
            self._refresh_ui_state()
            self.append_system_log(str(data or "Đã ngắt kết nối."))

        elif event_type == EVENT_ERROR:
            self._incoming_files.clear()
            self._outgoing_files.clear()
            self.root.title("TCP Socket Chat — Đồ án Mạng máy tính")
            self._refresh_ui_state()
            self.append_error_log(str(data or "Lỗi kết nối."))

    def _handle_incoming_binary_chunk(self, raw_bytes: bytes) -> None:
        try:
            file_id, chunk_idx, chunk_data = decode_binary_chunk(raw_bytes)
        except ProtocolError as err:
            self.append_error_log(f"Lỗi giải mã mảnh nhị phân: {err}")
            return

        pending = self._incoming_files.get(file_id)
        if pending is not None:
            pending.chunks[chunk_idx] = chunk_data

    def _handle_decoded_json_message(self, msg: dict) -> None:
        mtype = msg["type"]

        if mtype == "hello":
            self.active_username = msg["username"]
            self.root.title(f"TCP Socket Chat — {self.active_username}")
            self._refresh_ui_state()

        elif mtype == "room_list":
            self.rooms = list(msg["rooms"])
            self.current_room = msg["current_room"]
            self._populate_room_listbox()
            self._refresh_ui_state()

        elif mtype == "chat":
            msg_room = msg.get("room")
            if msg_room and msg_room != self.current_room:
                return

            ts = msg.get("timestamp") or now_hhmmss()
            if msg.get("system"):
                self.append_system_log(msg["text"], timestamp=ts)
                return

            sender = msg.get("sender") or "Server"
            is_hist = bool(msg.get("history", False))
            is_self = sender == self.active_username
            display_sender = f"Bạn ({sender})" if is_self else sender
            mid = msg.get("msg_id")

            if msg.get("recalled"):
                self.append_recalled_placeholder(
                    sender=display_sender,
                    raw_sender=sender,
                    timestamp=ts,
                    is_self=is_self,
                    is_history=is_hist,
                    msg_id=mid,
                )
            else:
                self.append_chat_message(
                    sender=display_sender,
                    raw_sender=sender,
                    content=msg["text"],
                    timestamp=ts,
                    is_self=is_self,
                    is_history=is_hist,
                    msg_id=mid,
                )

        elif mtype == "file_saved":
            fid = msg["file_id"]
            cached = self._outgoing_files.pop(fid, None)
            if cached is None:
                return
            if msg.get("room") and msg["room"] != self.current_room:
                return
            fname, fbytes = cached
            self.append_file_message(
                sender=f"Bạn ({self.active_username})",
                raw_sender=self.active_username,
                filename=fname,
                file_bytes=fbytes,
                timestamp=msg.get("timestamp") or now_hhmmss(),
                is_self=True,
                is_history=False,
                msg_id=msg["msg_id"],
            )

        elif mtype == "file_start":
            fid = msg["file_id"]
            self._incoming_files[fid] = PendingFileDownload(
                file_id=fid,
                filename=msg["filename"],
                expected_size=msg["size"],
                total_chunks=msg["total_chunks"],
                sender=msg.get("sender") or "Server",
                room=msg.get("room") or self.current_room,
                timestamp=msg.get("timestamp") or now_hhmmss(),
                msg_id=msg.get("msg_id"),
                is_history=bool(msg.get("history", False)),
            )

        elif mtype == "file_end":
            pending = self._incoming_files.pop(msg["file_id"], None)
            if pending is None or pending.room != self.current_room:
                return
            if any(i not in pending.chunks for i in range(pending.total_chunks)):
                self.append_error_log(f"Nhận thiếu mảnh dữ liệu của tệp '{pending.filename}'.")
                return
            assembled = b"".join(pending.chunks[i] for i in range(pending.total_chunks))
            is_self = pending.sender == self.active_username
            display_sender = (
                f"Bạn ({pending.sender})" if is_self else pending.sender
            )
            self.append_file_message(
                sender=display_sender,
                raw_sender=pending.sender,
                filename=pending.filename,
                file_bytes=assembled,
                timestamp=pending.timestamp,
                is_self=is_self,
                is_history=pending.is_history,
                msg_id=pending.msg_id,
            )

        elif mtype == "recall":
            if msg.get("room") and msg["room"] != self.current_room:
                return
            self.apply_message_recall(
                msg_id=msg["msg_id"],
                sender=msg.get("sender") or "",
                timestamp=msg.get("timestamp") or "",
            )

        elif mtype == "bye":
            self.append_system_log("Đã xác nhận kết thúc phiên (bye).")

        elif mtype == "error":
            self.append_error_log(f"[{msg['code']}] {msg['message']}")

    def _try_build_image_preview(self, filename: str, file_bytes: bytes):
        if not file_bytes:
            return None

        if HAS_PIL:
            try:
                img = Image.open(io.BytesIO(file_bytes))
                img.thumbnail((MAX_PREVIEW_DIM, MAX_PREVIEW_DIM))
                return ImageTk.PhotoImage(img, master=self.root)
            except Exception:
                pass

        ext = os.path.splitext(filename.lower())[1]
        is_tk_native = (
            ext in {".png", ".gif", ".ppm", ".pgm"}
            or file_bytes.startswith(b"\x89PNG\r\n\x1a\n")
            or file_bytes.startswith(b"GIF8")
        )
        if is_tk_native:
            try:
                photo = tk.PhotoImage(master=self.root, data=file_bytes)
                w, h = photo.width(), photo.height()
                if w > MAX_PREVIEW_DIM or h > MAX_PREVIEW_DIM:
                    factor = max(
                        (w + MAX_PREVIEW_DIM - 1) // MAX_PREVIEW_DIM,
                        (h + MAX_PREVIEW_DIM - 1) // MAX_PREVIEW_DIM,
                    )
                    photo = photo.subsample(factor, factor)
                return photo
            except Exception:
                return None
        return None

    def _save_received_file(self, filename: str, file_bytes: bytes) -> None:
        ext = os.path.splitext(filename)[1]
        save_path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Lưu tệp nhận qua Socket",
            initialfile=filename,
            defaultextension=ext,
        )
        if not save_path:
            return
        try:
            with open(save_path, "wb") as f:
                f.write(file_bytes)
            self.append_system_log(f"Đã lưu '{os.path.basename(save_path)}' thành công.")
        except OSError as err:
            messagebox.showerror("Lỗi lưu tệp", f"Không thể ghi tệp:\n{err}", parent=self.root)

    def _create_recall_button(self, msg_id: int) -> ttk.Button:
        btn = ttk.Button(
            self.chat_text,
            text="↩ Thu hồi",
            style="Inline.TButton",
            command=lambda mid=msg_id: self.request_recall(mid),
        )
        self._widget_refs.append(btn)
        return btn

    def append_chat_message(
        self,
        sender: str,
        content: str,
        timestamp: str,
        is_self: bool = False,
        is_history: bool = False,
        raw_sender: str = "",
        msg_id: Optional[int] = None,
    ) -> None:
        self.chat_text.configure(state="normal")
        start_idx = self.chat_text.index("end-1c")

        if is_history:
            self.chat_text.insert("end", f"[Lịch sử {timestamp}] ", "history_badge")
        else:
            self.chat_text.insert("end", f"[{timestamp}] ", "time")

        tag = "self_sender" if is_self else "peer_sender"
        self.chat_text.insert("end", f"{sender}: ", tag)
        self.chat_text.insert("end", content, "body")

        if is_self and msg_id is not None:
            self.chat_text.insert("end", "  ")
            self.chat_text.window_create("end", window=self._create_recall_button(msg_id))

        end_idx = self.chat_text.index("end-1c")
        if msg_id is not None:
            self.chat_text.tag_add(f"msg_{msg_id}", start_idx, end_idx)
            self._rendered_meta[msg_id] = {
                "is_history": is_history,
                "timestamp": timestamp,
                "sender": raw_sender or sender,
            }

        self.chat_text.insert("end", "\n")
        self.chat_text.see("end")
        self.chat_text.configure(state="disabled")

    def append_file_message(
        self,
        sender: str,
        filename: str,
        file_bytes: bytes,
        timestamp: str,
        is_self: bool = False,
        is_history: bool = False,
        raw_sender: str = "",
        msg_id: Optional[int] = None,
    ) -> None:
        self.chat_text.configure(state="normal")
        start_idx = self.chat_text.index("end-1c")

        if is_history:
            self.chat_text.insert("end", f"[Lịch sử {timestamp}] ", "history_badge")
        else:
            self.chat_text.insert("end", f"[{timestamp}] ", "time")

        tag = "self_sender" if is_self else "peer_sender"
        self.chat_text.insert("end", f"{sender} ", tag)
        self.chat_text.insert(
            "end",
            f"đã gửi tệp đính kèm: {filename} ({format_size(len(file_bytes))})  ",
            "file_meta",
        )

        save_btn = ttk.Button(
            self.chat_text,
            text="💾 Lưu về máy",
            style="Inline.TButton",
            command=lambda fn=filename, fb=file_bytes: self._save_received_file(fn, fb),
        )
        self._widget_refs.append(save_btn)
        self.chat_text.window_create("end", window=save_btn)

        if is_self and msg_id is not None:
            self.chat_text.insert("end", " ")
            self.chat_text.window_create("end", window=self._create_recall_button(msg_id))

        preview = self._try_build_image_preview(filename, file_bytes)
        if preview is not None:
            self._widget_refs.append(preview)
            self.chat_text.insert("end", "\n    ")
            self.chat_text.image_create("end", image=preview, padx=4, pady=4)

        end_idx = self.chat_text.index("end-1c")
        if msg_id is not None:
            self.chat_text.tag_add(f"msg_{msg_id}", start_idx, end_idx)
            self._rendered_meta[msg_id] = {
                "is_history": is_history,
                "timestamp": timestamp,
                "sender": raw_sender or sender,
            }

        self.chat_text.insert("end", "\n")
        self.chat_text.see("end")
        self.chat_text.configure(state="disabled")

    def append_recalled_placeholder(
        self,
        sender: str,
        timestamp: str,
        is_self: bool = False,
        is_history: bool = False,
        raw_sender: str = "",
        msg_id: Optional[int] = None,
    ) -> None:
        self.chat_text.configure(state="normal")
        if is_history:
            self.chat_text.insert("end", f"[Lịch sử {timestamp}] ", "history_badge")
        else:
            self.chat_text.insert("end", f"[{timestamp}] ", "time")

        tag = "self_sender" if is_self else "peer_sender"
        self.chat_text.insert("end", f"{sender}: ", tag)
        self.chat_text.insert("end", "🚫 Tin nhắn đã được thu hồi\n", "recalled")
        self.chat_text.see("end")
        self.chat_text.configure(state="disabled")

    def apply_message_recall(self, msg_id: int, sender: str, timestamp: str) -> None:
        """Xóa ngay nội dung, hình ảnh & nút bấm của tin nhắn `msg_id` tại chỗ trên khung chat."""
        tag_name = f"msg_{msg_id}"
        ranges = self.chat_text.tag_ranges(tag_name)
        if not ranges:
            return

        meta = self._rendered_meta.pop(msg_id, {})
        is_hist = bool(meta.get("is_history", False))
        ts = str(meta.get("timestamp") or timestamp or now_hhmmss())
        orig_sender = sender or str(meta.get("sender") or "Người dùng")
        is_self = orig_sender == self.active_username
        display_sender = f"Bạn ({orig_sender})" if is_self else orig_sender

        start_pos = self.chat_text.index(ranges[0])
        end_pos = self.chat_text.index(ranges[-1])

        prefix = f"[Lịch sử {ts}] " if is_hist else f"[{ts}] "
        prefix_tag = "history_badge" if is_hist else "time"
        sender_tag = "self_sender" if is_self else "peer_sender"

        self.chat_text.configure(state="normal")
        self.chat_text.delete(start_pos, end_pos)
        self.chat_text.tag_delete(tag_name)
        self.chat_text.insert(
            start_pos,
            prefix,
            prefix_tag,
            f"{display_sender}: ",
            sender_tag,
            "🚫 Tin nhắn đã được thu hồi",
            "recalled",
        )
        self.chat_text.configure(state="disabled")

    def append_system_log(self, message: str, timestamp: Optional[str] = None) -> None:
        ts = timestamp or now_hhmmss()
        self.chat_text.configure(state="normal")
        self.chat_text.insert("end", f"[{ts}] [Hệ thống] {message}\n", "system")
        self.chat_text.see("end")
        self.chat_text.configure(state="disabled")

    def append_error_log(self, message: str) -> None:
        self.chat_text.configure(state="normal")
        self.chat_text.insert("end", f"[{now_hhmmss()}] [Lỗi] {message}\n", "error")
        self.chat_text.see("end")
        self.chat_text.configure(state="disabled")

    def clear_chat_history(self) -> None:
        self.chat_text.configure(state="normal")
        self.chat_text.delete("1.0", "end")
        self.chat_text.configure(state="disabled")
        self._rendered_meta.clear()
        self._widget_refs.clear()

    def on_close(self) -> None:
        try:
            self._graceful_disconnect()
        except Exception:
            pass
        self.root.destroy()


def main(argv=None) -> None:
    args = parse_args(argv)
    enable_windows_dpi_awareness()
    root = tk.Tk()
    try:
        ChatClientGUI(
            root,
            initial_host=args.host,
            initial_port=args.port,
            initial_name=args.name,
            initial_room=args.room,
        )
        root.mainloop()
    except KeyboardInterrupt:
        try:
            root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    main()
