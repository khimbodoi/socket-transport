
MAX_SIZE = 64 * 1024

def recv_exact(sock, size, allow_eof=False):
    received = bytearray()

    while len(received) < size:
        chunk = sock.recv(min(4096, size - len(received)))

        if not chunk:
            if allow_eof and not received:
                return None

            raise ConnectionError(
                "Đầu bên kia đóng kết nối khi chưa nhận đủ"
            )
            
        received.extend(chunk)
    
    return bytes(received)


def send_msg(sock, payload):
    if len(payload) > MAX_SIZE:
        raise ValueError("Thông điệp quá lớn")

    header = len(payload).to_bytes(4, byteorder="big")
    sock.sendall(header + payload)

def recv_msg(sock):
    header = recv_exact(sock, 4, allow_eof=True)

    if header is None:
        return None

    size = int.from_bytes(header, byteorder="big")

    if size > MAX_SIZE:
        raise ValueError("Thông điệp quá lớn")

    return recv_exact(sock, size)