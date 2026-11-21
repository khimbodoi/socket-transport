import socket

# Send only a header declaring a payload larger than the server limit.
with socket.create_connection(("127.0.0.1", 65432), timeout=5) as client:
    fake_size = 64 * 1024 + 1
    header = fake_size.to_bytes(4, byteorder="big")

    client.sendall(header)
    print("Sent oversized payload length:", fake_size)
