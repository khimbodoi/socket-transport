#client
import socket
import argparse
from transport import MAX_SIZE, send_msg, recv_msg

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

parser = argparse.ArgumentParser(description="TCP echo client")

parser.add_argument("--host", "--h", default="127.0.0.1")
parser.add_argument("--port", "--p", type=valid_port, default=65432)
args = parser.parse_args()

try:    
    with socket.create_connection((args.host, args.port), timeout=5) as client:
        while True:
            msg = input("Bạn: ")

            if msg == "/quit":
                break
            
            payload = msg.encode("utf-8")

            if len(payload) > MAX_SIZE:
                print(f"Tin nhắn tối đa {MAX_SIZE} byte")
                continue

            send_msg(client, payload)
            received = recv_msg(client)

            if received is None:
                print("Server đã đóng kết nối")
                break

            print("Server trả lại:", received.decode("utf-8"))

except TimeoutError:
    print("Hết thời gian chờ kết nối hoặc phản hồi")

except ConnectionRefusedError:
    print("Kết nối bị từ chối. Kiểm tra server và IP/port")

except ConnectionError as error:
    print("Kết nối bị ngắt:", error)

except ValueError as error:
    print("Dữ liệu không hợp lệ:", error)

except OSError as error:
    print("Lỗi mạng hoặc socket:", error)

except (KeyboardInterrupt, EOFError):
    print("\nĐã thoát client")