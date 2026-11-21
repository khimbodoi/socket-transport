#server.py
import socket
from transport import MAX_SIZE, send_msg, recv_msg
import argparse

def valid_port(value):
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("Port phải là số nguyên")

    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError(
            "Port phải nằm trong khoảng 1 đến 65535"
        )

    return port

parser = argparse.ArgumentParser(description="TCP echo server")

parser.add_argument("--host", "--h", default="127.0.0.1")
parser.add_argument("--port", "--p", type=valid_port, default=65432)


args = parser.parse_args()

HOST = args.host
PORT = args.port

try:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.bind((HOST, PORT))
        server.listen()
        print(f"Đang chờ tại {HOST}:{PORT}")

        while True:
            conn, addr = server.accept()

            with conn:
                print("Client đã kết nối:", addr)
                conn.settimeout(10)

                try:
                    while True:
                        payload = recv_msg(conn)

                        if payload is None:
                            print("Client đã ngắt kết nối")
                            break

                        print("Đã nhận:", len(payload), "byte nội dung")
                        send_msg(conn, payload)

                except TimeoutError:
                    print("Client chờ quá lâu, đóng kết nối")

                except ConnectionError as error:
                    print("Kết nối với client bị lỗi:", error)

                except ValueError as error:
                    print("Thông điệp không hợp lệ:", error)

                except OSError as error:
                    print("Lỗi socket với client:", error)

            print("Đang chờ client tiếp theo...")

except KeyboardInterrupt:
    print("\nĐã dừng server")

except OSError as error:
    if error.winerror == 10048:
        print(f"Địa chỉ {HOST}:{PORT} đang bị chiếm.")
        print("Hãy dừng server cũ hoặc chọn port khác bằng --p.")
    else:
        print("Lỗi socket của server:", error)